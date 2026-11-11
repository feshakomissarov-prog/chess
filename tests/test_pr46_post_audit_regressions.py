from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QLabel, QScrollArea

from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.dashboard_cache import DashboardSnapshotCache
from chess_coach.application.app_state import ReviewResumeTarget, WorkspaceSnapshot
from chess_coach.lichess.auth import CredentialStorageError
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.api import LichessClient, authenticated_request_admission
from chess_coach.ui.game_review import GameReviewWorkspace
from chess_coach.window import MainWindow
from test_pr46_independent_source_review import (
    FakeGameClient, MemoryStore, _accept_snapshot, _event, _service,
)


def test_dashboard_revision_covers_job_and_position_dependencies_but_ignores_metadata(tmp_path):
    repository = GameRepository(tmp_path / "dashboard-revision.sqlite3")
    base = repository.dashboard_data_revision()
    repository.save_snapshot("g", "startpos", "", "white", "Opponent", False,
                             "5+0", "mate", "white")
    after_game = repository.dashboard_data_revision()
    assert after_game > base

    with repository._connection() as db:
        line_revision, line_hash = db.execute(
            "SELECT line_revision,line_hash FROM game_revisions WHERE game_id='g'").fetchone()
        db.execute("INSERT INTO game_positions(game_id,line_revision,ply,position_uid,fen,position_hash) "
                   "VALUES('g',?,99,'pos-99','startpos','hash-99')", (line_revision,))
    after_position = repository.dashboard_data_revision()
    assert after_position > after_game

    with repository._connection() as db:
        db.execute(
            "INSERT INTO analysis_jobs(job_id,game_id,line_revision,line_hash,analysis_pass,"
            "engine_version,parameters_json,status,created_at,updated_at) "
            "VALUES('job','g',? ,?,'fast','engine','{}','queued','now','now')",
            (line_revision, line_hash))
    after_job = repository.dashboard_data_revision()
    assert after_job > after_position
    with repository._connection() as db:
        db.execute("UPDATE games SET updated_at='metadata-only' WHERE game_id='g'")
    assert repository.dashboard_data_revision() == after_job
    with repository._connection() as db:
        db.execute("UPDATE analysis_jobs SET status='complete' WHERE job_id='job'")
    assert repository.dashboard_data_revision() > after_job
    repository.shutdown()


def test_dashboard_profile_revision_ignores_other_account_and_noop_writes(tmp_path):
    repository = GameRepository(tmp_path / "profile-revisions.sqlite3")
    repository.save_snapshot(
        "profile-a-game", "startpos", "e2e4", "white", "A opponent", False,
        "5+0", "mate", "white", account_username="profile-a")
    before_a = repository.dashboard_data_revision("lichess:profile-a")
    before_b = repository.dashboard_data_revision("lichess:profile-b")
    before_global = repository.dashboard_data_revision()

    repository.save_snapshot(
        "profile-b-game", "startpos", "d2d4", "white", "B opponent", False,
        "5+0", "mate", "white", account_username="profile-b")
    after_b_write = repository.dashboard_data_revision("lichess:profile-a")
    assert after_b_write == before_a
    assert repository.dashboard_data_revision("lichess:profile-b") > before_b
    assert repository.dashboard_data_revision() > before_global

    with repository._connection() as db:
        db.execute("UPDATE account_games SET opponent=opponent "
                   "WHERE account_id='lichess:profile-b'")
    assert repository.dashboard_data_revision("lichess:profile-a") == after_b_write
    repository.shutdown()


def test_dashboard_cache_rejects_schema_valid_json_with_wrong_tier_shapes(tmp_path):
    repository = GameRepository(tmp_path / "dashboard-malformed.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    snapshot = dashboard_snapshot(repository, account_id=None, clock=None)
    assert cache.save(None, None, revision, snapshot)
    value = json.loads(cache.path.read_text(encoding="utf-8"))
    next(iter(value["scopes"].values()))["tier_b"] = {
        "metrics": "not-a-list", "recent_metrics": []}
    cache.path.write_text(json.dumps(value), encoding="utf-8")
    assert cache.load(None, None, expected_revision=revision) is None
    repository.shutdown()


@pytest.mark.parametrize("game_count", [0, 3319, 10000])
def test_two_dashboard_tiers_survive_a_real_process_restart(tmp_path, game_count):
    repository = GameRepository(tmp_path / "dashboard-process-restart.sqlite3")
    if game_count:
        with repository._connection() as db:
            db.executemany(
                "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                "winner,updated_at,pgn) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ((f"synthetic-{index:05d}", "startpos", "", "white", "Synthetic", 0,
                  "5+0", "mate", "white", "2026-11-10T00:00:00Z", "")
                 for index in range(game_count)))
            db.executemany(
                "INSERT INTO game_revisions(game_id,line_revision,initial_fen,moves,pgn,"
                "line_hash,created_at,is_current) VALUES(?,1,'startpos','','',?,'2026-11-10T00:00:00Z',1)",
                ((f"synthetic-{index:05d}", f"hash-{index:05d}")
                 for index in range(game_count)))
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    from chess_coach.application.dashboard import dashboard_preview_snapshot
    tier_a = dashboard_preview_snapshot(repository)
    assert cache.save_tier_a(None, None, revision, tier_a)
    tier_b = dashboard_snapshot(repository, account_id=None, clock=None)
    assert cache.save_tier_b(None, None, revision, tier_b)
    assert cache.load(None, None, expected_revision=revision) is not None
    db_path = repository.path
    repository.shutdown()

    script = (
        "import json,sys; from chess_coach.lichess.repository import GameRepository; "
        "from chess_coach.application.dashboard_cache import DashboardSnapshotCache; "
        "r=GameRepository(sys.argv[1]); c=DashboardSnapshotCache(r); rev=r.dashboard_data_revision(); "
        "s=c.load(None,None); print(json.dumps({'hit':s is not None,'games':next((m['value'] "
        "for m in (s or {}).get('metrics',[]) if m['key']=='games'),None),'preview':"
        "bool((s or {}).get('is_preview')),'revision':rev,'cache_revision':"
        "json.load(open(c.path,encoding='utf8')).get('revision') if c.path.exists() else None})); r.shutdown()")
    completed = subprocess.run([sys.executable, "-c", script, db_path],
                               cwd=str(Path.cwd()), capture_output=True,
                               text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {"hit": True, "games": str(game_count), "preview": False,
                      "revision": result["cache_revision"],
                      "cache_revision": result["cache_revision"]}


def test_warm_home_navigation_performs_no_disk_or_sqlite_reads(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "warm-home-no-io.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window._dashboard_preview_thread is None
                    and window._dashboard_thread is None, timeout=5000)
    window._render_dashboard_snapshot(dashboard_snapshot(
        window.game_repository, account_id=None, clock=None))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("warm page switch must not read SQLite or the disk cache")

    monkeypatch.setattr(window.game_repository, "dashboard_data_revision", forbidden)
    monkeypatch.setattr(window._dashboard_snapshot_cache, "load", forbidden)
    elapsed = []
    for _ in range(10):
        started = time.perf_counter()
        window.show_home()
        elapsed.append((time.perf_counter() - started) * 1000)
    assert max(elapsed) < 100
    window.close()


def test_archive_status_slow_sqlite_read_does_not_block_qt_heartbeat(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "archive-status-heartbeat.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    entered, release = threading.Event(), threading.Event()
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()

    def locked_read(**_kwargs):
        entered.set()
        assert release.wait(2.0)
        return {"total": 0}

    monkeypatch.setattr(window.game_repository, "search_games", locked_read)
    started = time.perf_counter()
    window.show_archive_status()
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 100
    try:
        assert entered.wait(1.0)
        # Allow a generously scheduled Qt wait on slower Windows runners while
        # still requiring the event loop to service several heartbeats during
        # the deliberately blocked repository read.
        qtbot.waitUntil(lambda: len(ticks) >= 3, timeout=750)
    finally:
        release.set()
    dialog = window._archive_status_dialog
    qtbot.waitUntil(lambda: "Локальных партий: 0" in dialog.findChild(
        QLabel, "archiveStatusDetails").text(), timeout=1500)
    dialog.close()
    window.close()


def test_resume_review_loads_sqlite_read_model_off_the_qt_thread(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "resume-review-background.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.game_repository.save_snapshot(
        "resume-review", "startpos", "e2e4 e7e5", "white", "Synthetic", False,
        "5+0", "mate", "white")
    game = window.game_repository.get_game("resume-review")
    line = window.game_repository.current_line("resume-review")
    target = ReviewResumeTarget("resume-review", line["line_revision"],
                                line["line_hash"], 1, 0)
    window.workspace_state.save(WorkspaceSnapshot(route="home", review=target))

    entered, release = threading.Event(), threading.Event()
    original_get_game = window.game_repository.get_game
    def slow_get_game(game_id, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            entered.set()
            assert release.wait(2.0)
        return original_get_game(game_id, *args, **kwargs)
    monkeypatch.setattr(window.game_repository, "get_game", slow_get_game)
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    started = time.perf_counter()
    window.request_resume_saved_review()
    assert (time.perf_counter() - started) * 1000 < 100
    try:
        assert entered.wait(1.0)
        qtbot.waitUntil(lambda: len(ticks) >= 3, timeout=750)
    finally:
        release.set()
    qtbot.waitUntil(lambda: window.screen_stack.currentWidget() is window.review_workspace,
                    timeout=3000)
    assert window.review_workspace.review["game"]["game_id"] == "resume-review"
    window.close()


@pytest.mark.parametrize("command", ["move", "resign", "offer_draw", "takeback"])
def test_credential_store_failure_after_reservation_is_definitive_not_sent(
        qtbot, monkeypatch, command):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "credential-fail", generation=generation)
    completed, failed = [], []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    service.session_move_failed.connect(lambda *args: failed.append(args))

    def fail_get(_context):
        raise CredentialStorageError("store unavailable")

    monkeypatch.setattr(service, "operation_token", fail_get)
    if command == "move":
        service.send_move("credential-fail", "e2e4", "request")
        qtbot.waitUntil(lambda: bool(failed), timeout=1000)
        assert failed[-1][-1] == -2
        assert not service._pending_move_by_game
    elif command == "takeback":
        takeback = []
        service.session_takeback_action_completed.connect(lambda *args: takeback.append(args))
        service.takeback("credential-fail", "offer", "request")
        qtbot.waitUntil(lambda: bool(takeback), timeout=1000)
        assert takeback[-1][-1] == -2
        assert not service.has_pending_action("credential-fail", "takeback")
    elif command == "offer_draw":
        service.draw("credential-fail", True)
        qtbot.waitUntil(lambda: bool(completed), timeout=1000)
        assert completed[-1][-1] == -2
        assert not service.has_pending_action("credential-fail", "offer_draw")
    else:
        service.resign("credential-fail")
        qtbot.waitUntil(lambda: bool(completed), timeout=1000)
        assert completed[-1][-1] == -2
        assert not service.has_pending_action("credential-fail", "resign")
    assert client.posts == []
    service.close(timeout=0.2)


def test_offer_draw_cannot_become_accept_when_incoming_offer_arrives_while_queued(qtbot, monkeypatch):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "draw-race", generation=generation)
    entered, release = threading.Event(), threading.Event()
    original = service.operation_token

    def wait_for_credentials(context):
        entered.set()
        assert release.wait(1.0)
        return original(context)

    monkeypatch.setattr(service, "operation_token", wait_for_credentials)
    service.draw("draw-race", True)
    assert entered.wait(1.0)
    service.reconcile_game_actions(
        "draw-race", _event("gameState", bdraw=True, wdraw=False), generation)
    release.set()
    qtbot.waitUntil(lambda: not service.has_pending_action("draw-race", "offer_draw"), timeout=1000)
    assert client.posts == []
    service.close(timeout=0.2)


def test_authenticated_post_rechecks_session_after_waiting_for_request_gate():
    first_entered, release_first = threading.Event(), threading.Event()
    sent = []

    class Transport:
        def request(self, method, url, *_args):
            sent.append((method, url))
            if url.endswith("/first"):
                first_entered.set()
                assert release_first.wait(1.0)
            return type("Response", (), {"status": 200, "headers": {}, "body": b"{}"})()

    client = LichessClient(Transport(), base_url="https://unit.test")
    first = threading.Thread(target=lambda: client._call("GET", "/first"))
    first.start()
    assert first_entered.wait(1.0)
    errors = []
    guard = {"current": True}

    def queued_post():
        try:
            with authenticated_request_admission(lambda: guard["current"]):
                client._call("POST", "/move/queued", token="captured-token", form={"move": "e2e4"})
        except Exception as exc:
            errors.append(exc)

    queued = threading.Thread(target=queued_post)
    queued.start()
    with client._request_gate._condition:
        assert client._request_gate._condition.wait_for(
            lambda: client._request_gate.waiting_count == 1, timeout=1.0)
    guard["current"] = False
    release_first.set()
    first.join(1.0)
    queued.join(1.0)
    assert not first.is_alive() and not queued.is_alive()
    assert len(sent) == 1 and sent[0][1].endswith("/first")
    assert errors and getattr(errors[0], "status", None) == -2


def test_username_challenge_post_is_suppressed_after_account_switch_while_queued(qtbot):
    entered, release = threading.Event(), threading.Event()
    sent = []

    class Transport:
        def request(self, method, url, *_args):
            sent.append((method, url))
            if url.endswith("/gate-holder"):
                entered.set()
                assert release.wait(2.0)
            return type("Response", (), {"status": 200, "headers": {}, "body": b"{}"})()

        def stream(self, method, url, *_args, **_kwargs):
            sent.append((method, url))
            return iter([b'{"type":"done","done":"declined"}\n'])

    client = LichessClient(Transport(), base_url="https://unit.test")
    service = _service(client)
    holder = threading.Thread(target=lambda: client._call("GET", "/gate-holder"))
    holder.start()
    assert entered.wait(1.0)
    service.challenge_username("opponent", "white", None)
    with client._request_gate._condition:
        assert client._request_gate._condition.wait_for(
            lambda: client._request_gate.waiting_count == 1, timeout=1.0)
    service._cancel_active_session()
    release.set()
    holder.join(1.0)
    qtbot.waitUntil(lambda: not any(thread.is_alive() for thread in service._threads),
                    timeout=1500)
    assert len(sent) == 1 and sent[0][1].endswith("/gate-holder")
    service.close(timeout=0.2)


def test_move_request_ledger_has_a_hard_bound_for_unresolved_intents(qtbot):
    service = _service()
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    service._game_action_admissions[(generation, "seed")] = {"snapshot_sequence": 1,
                                                               "color": "white"}
    service._game_authoritative_state[(generation, "seed")] = {
        "status": "playing", "moves": "", "sequence": 1}
    with service._pending_action_lock:
        service._move_request_ledger.update({
            (generation, f"old-{i}", f"request-{i}"): {"status": "ambiguous"}
            for i in range(512)})
    failures = []
    service.session_move_failed.connect(lambda *args: failures.append(args))
    service.send_move("seed", "e2e4", "last")
    assert len(service._move_request_ledger) == 512
    assert not service._pending_move_by_game
    assert failures and failures[-1][-1] == -2
    service.close(timeout=0.2)


@pytest.mark.parametrize("size", [(1120, 640), (1280, 720), (1366, 768), (1672, 941)])
def test_review_metrics_remain_reachable_at_compact_window_sizes(qtbot, tmp_path, size):
    repository = GameRepository(tmp_path / "review-layout.sqlite3")
    workspace = GameReviewWorkspace(repository)
    qtbot.addWidget(workspace)
    workspace.resize(*size)
    workspace.show()
    qtbot.wait(20)
    scroll = workspace.findChild(QScrollArea, "reviewMetricsScroll")
    assert scroll is not None
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.viewport().width() > 0 and scroll.viewport().height() > 0
    scroll.widget().setMinimumHeight(1000)
    qtbot.wait(10)
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum()
    workspace.deleteLater()
    repository.shutdown()
