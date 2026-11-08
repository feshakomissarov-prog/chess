from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest

from chess_coach.application.library import GameLibraryQueryCoordinator
from chess_coach.infrastructure.sqlite.games import GamesStoreMixin
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.service import LichessService
from chess_coach.ui.sounds import ChessSoundPlayer
from chess_coach.window import MainWindow


def _archive(repository, username: str, source_id: str) -> str:
    pgn = ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
           '1. e4 e5 2. Nf3 Nc6 1-0')
    repository.import_archive_record(username, {"id": source_id, "pgn": pgn},
                                     checkpoint_ms=None, backfill=False)
    with repository._connection() as db:
        return str(db.execute("SELECT game_id FROM games WHERE source_game_id=?",
                              (source_id,)).fetchone()[0])


def test_swg01_real_library_query_returns_unambiguous_account_scope(tmp_path):
    repository = GameRepository(tmp_path / "swg01-library.sqlite3")
    try:
        game_id = _archive(repository, "AccountA", "shared-remote-id")
        result = GameLibraryQueryCoordinator(repository).search({"account_id": "lichess:accounta"})
        row = next(item for item in result["items"] if item["game_id"] == game_id)
        assert row["account_id"] == "lichess:accounta"
        assert row["account_link_count"] == 1
        all_rows = GameLibraryQueryCoordinator(repository).search({})["items"]
        all_row = next(item for item in all_rows if item["game_id"] == game_id)
        assert all_row["account_id"] == "lichess:accounta"
        assert all_row["account_link_count"] == 1

        account_b = repository.ensure_archive_account("AccountB")
        with repository._connection() as db:
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                       "VALUES(?,?,?,?,?,?)", (account_b, game_id, "shared-remote-id",
                       "synthetic", "white", "Opponent"))
        shared = GameLibraryQueryCoordinator(repository).search({})["items"]
        ambiguous = next(item for item in shared if item["game_id"] == game_id)
        assert ambiguous["account_id"] is None
        assert ambiguous["account_link_count"] == 2
    finally:
        repository.shutdown()


def test_swg01_stale_revision_or_remote_link_cannot_enter_retry_queue(tmp_path):
    repository = GameRepository(tmp_path / "swg01-stale.sqlite3")
    try:
        game_id = _archive(repository, "AccountA", "revision-protected-remote")
        account_id = repository.ensure_archive_account("AccountA")
        old = repository.current_line(game_id)
        assert old is not None
        dto = GameLibraryQueryCoordinator(repository).search(
            {"account_id": account_id})["items"][0]
        assert dto["line_revision"] == old["line_revision"]
        repository.save_snapshot(game_id, "startpos", "e2e4 e7e5", "white", "Opponent",
                                 False, "5+0", "mate", "white")
        assert repository.current_line(game_id)["line_revision"] > old["line_revision"]
        assert repository.validate_lichess_game_link(
            account_id, game_id, line_revision=old["line_revision"],
            source_game_id="revision-protected-remote") is None
        assert repository.enqueue_lichess_analysis_backfill(
            account_id, game_id=game_id, expected_line_revision=old["line_revision"],
            expected_source_game_id="revision-protected-remote") == 0
        assert not repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=old["line_revision"],
            source_game_id="revision-protected-remote", manual=True)
        assert repository.lichess_analysis_backfill_state(account_id, game_id) is None
        assert repository.validate_lichess_game_link(
            account_id, game_id, line_revision=repository.current_line(game_id)["line_revision"],
            source_game_id="wrong-remote-id") is None
    finally:
        repository.shutdown()


def test_swg01_sqlite_to_library_page_click_revalidates_account_without_dict_injection(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "swg01-window.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False  # The isolated temporary profile remains the backing store.
    repo = window.game_repository
    game_id = _archive(repo, "AccountA", "library-click-game")
    calls = []

    class Service:
        username = "AccountA"
        auth_generation = 9
        def is_auth_generation_current(self, generation): return generation == 9
        def refresh_lichess_analysis_background(self, **kwargs):
            calls.append(kwargs)
            return "started"

    window.lichess = Service()
    window._lichess_connected = True
    window._lichess_stream_ready_generation = 9
    window._dashboard_analysis_safety_allowed = True
    window._online_actions_allowed = lambda: True
    window._current_lichess_generation = lambda generation: generation == 9
    window._sessions_for_safety = lambda: []
    window.show_saved_games()
    page = window.library_page
    window.show()
    try:
        qtbot.waitUntil(lambda: page.games.count() > 0, timeout=5000)
        row = page.games.item(0).data(Qt.ItemDataRole.UserRole)
        assert row["game_id"] == game_id
        assert row["account_id"] == "lichess:accounta"
        assert "account_id" not in repo.get_game(game_id)
        qtbot.waitUntil(lambda: page.retry_analysis_button.isEnabled(), timeout=5000)
        QTest.mouseClick(page.retry_analysis_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: len(calls) == 1, timeout=5000)
        assert calls[0]["selected_game_id"] == game_id
        assert calls[0]["expected_source_game_id"] == "library-click-game"
        with repo._connection() as db:
            row = db.execute("SELECT account_id,manual_retry_at FROM lichess_analysis_backfill "
                             "WHERE game_id=?", (game_id,)).fetchone()
        assert row["account_id"] == "lichess:accounta" and row["manual_retry_at"]
    finally:
        window.close()


def test_swg06_library_retry_click_does_not_wait_for_external_sqlite_writer(
        qtbot, tmp_path):
    import sqlite3

    window = MainWindow(QSettings(str(tmp_path / "swg06-window.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    repo = window.game_repository
    game_id = _archive(repo, "AccountA", "locked-library-game")
    calls = []

    class Service:
        username = "AccountA"
        auth_generation = 12
        def is_auth_generation_current(self, generation): return generation == 12
        def refresh_lichess_analysis_background(self, **kwargs):
            calls.append(kwargs)
            return "started"

    window.lichess = Service()
    window._lichess_connected = True
    window._lichess_stream_ready_generation = 12
    window._dashboard_analysis_safety_allowed = True
    window._online_actions_allowed = lambda: True
    window._current_lichess_generation = lambda generation: generation == 12
    window._sessions_for_safety = lambda: []
    window.show_saved_games()
    page = window.library_page
    window.show()
    try:
        qtbot.waitUntil(lambda: page.games.count() > 0, timeout=5000)
        qtbot.waitUntil(lambda: page.retry_analysis_button.isEnabled(), timeout=5000)
        repo._unit_of_work.timeout = 0.35
        lock = sqlite3.connect(repo.path, timeout=0)
        lock.execute("BEGIN IMMEDIATE")
        lock.execute("UPDATE games SET updated_at=updated_at WHERE game_id=?", (game_id,))
        timer_ran = []
        started = time.perf_counter()
        QTimer.singleShot(10, lambda: timer_ran.append(time.perf_counter()))
        QTest.mouseClick(page.retry_analysis_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: bool(timer_ran), timeout=1000)
        elapsed = timer_ran[0] - started
        lock.rollback()
        lock.close()
        qtbot.waitUntil(lambda: len(calls) == 1, timeout=5000)
        assert elapsed < 0.25
        assert calls[0]["selected_game_id"] == game_id
    finally:
        window.close()


def test_swg03_explicit_selected_retry_bypasses_only_internal_no_data_ttl(tmp_path):
    repository = GameRepository(tmp_path / "swg03-manual-ttl.sqlite3")
    try:
        game_id = _archive(repository, "Owner", "published-after-analysis")
        account_id = repository.ensure_archive_account("Owner")
        repository.enqueue_lichess_analysis_backfill(account_id)
        line = repository.current_line(game_id)
        repository.record_lichess_analysis_backfill(
            account_id, game_id, accuracy_status="no_server_data", evals_status="partial",
            line_revision=line["line_revision"], line_hash=line["line_hash"],
            retry_after_seconds=7 * 24 * 60 * 60)
        with repository._connection() as db:
            old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            db.execute("UPDATE lichess_analysis_backfill SET updated_at=?,accuracy_checked_at=?,"
                       "evals_checked_at=? "
                       "WHERE account_id=? AND game_id=?", (old, old, old, account_id, game_id))

        assert repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=line["line_revision"],
            source_game_id="published-after-analysis", manual=True)
        state = repository.lichess_analysis_backfill_state(account_id, game_id)
        assert state["accuracy_status"] == "queued"
        assert state["evals_status"] == "queued"
        assert state["accuracy_next_retry_at"] is None
        assert not repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=line["line_revision"],
            source_game_id="published-after-analysis", manual=True)

        class Client:
            rate_limit_remaining = 0
            def __init__(self): self.calls = []
            def export_game_json(self, _token, source_id):
                self.calls.append(source_id)
                return {"id": source_id, "status": "mate", "analysis": [],
                        "pgn": ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
                                '1. e4 e5 2. Nf3 Nc6 1-0'),
                        "players": {"white": {"analysis": {"accuracy": 91},
                                               "user": {"name": "Owner"}},
                                    "black": {"analysis": {"accuracy": 87},
                                              "user": {"name": "Opponent"}}}}

        client = Client()
        report = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), selected_game_id=game_id,
            expected_line_revision=line["line_revision"],
            expected_source_game_id="published-after-analysis")
        assert report["checked"] == 1
        assert client.calls == ["published-after-analysis"]
        assert repository.get_game(game_id)["lichess_accuracy_white"] == 91
    finally:
        repository.shutdown()


def test_swg04_pending_cue_obeys_shared_volume_and_mute(tmp_path, monkeypatch, qtbot):
    from PySide6.QtWidgets import QApplication

    class Effect:
        def __init__(self, _parent=None): self.volume = 0; self.play_count = 0; self.source = None
        def setSource(self, source): self.source = source
        def setLoopCount(self, _count): pass
        def setVolume(self, volume): self.volume = volume
        def play(self): self.play_count += 1

    monkeypatch.setattr(QApplication, "beep", lambda: pytest.fail("system beep bypasses app gain"))
    player = ChessSoundPlayer(tmp_path / "sounds", effect_factory=Effect, volume=0)
    assert not player.play_pending_move("volume-0")
    assert player.pending_effect.volume == 0
    for volume in (1, 45, 100):
        player.configure(moves_enabled=True, notifications_enabled=True,
                         history_enabled=True, volume=volume)
        before = player.pending_effect.play_count
        assert player.play_pending_move(f"volume-{volume}")
        assert player.pending_effect.volume == pytest.approx(volume / 100)
        assert player.pending_effect.play_count == before + 1
    player.configure(moves_enabled=False, notifications_enabled=True,
                     history_enabled=True, volume=45)
    before = player.pending_effect.play_count
    assert not player.play_pending_move("muted-toggle")
    assert player.pending_effect.play_count == before

    def no_audio_device(_parent=None):
        raise RuntimeError("no Qt audio device")
    silent = ChessSoundPlayer(tmp_path / "silent", effect_factory=no_audio_device)
    assert silent.pending_effect is None
    assert not silent.play_pending_move("silent-visual-pending")


def test_swg05_gamefull_only_human_snapshot_pauses_heavy_work(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "swg05.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    effects = []
    batch_pause = threading.Event()
    analysis_stop = threading.Event()
    window._batch_pause = batch_pause
    window._analysis_stop = analysis_stop
    monkeypatch.setattr(window, "_cancel_lichess_backfill_wakeup", lambda: effects.append("wakeup"))
    monkeypatch.setattr(window, "_cancel_legacy_metrics_batch", lambda reason: effects.append(reason))
    monkeypatch.setattr(window, "_pause_batch_analysis",
                        lambda manual=False: (effects.append("batch"), batch_pause.set()))
    window.lichess = type("Service", (), {
        "username": "Owner",
        "cancel_archive_sync": lambda self: effects.append("archive"),
        "cancel_puzzle_sync": lambda self: effects.append("puzzle"),
        "cancel_lichess_analysis_backfill": lambda self: effects.append("backfill"),
    })()
    window._lichess_game_event("reconnected-human", {
        "type": "gameFull", "id": "reconnected-human", "source": "friend",
        "initialFen": "startpos", "rated": False,
        "white": {"name": "Owner", "id": "owner"},
        "black": {"name": "Friend", "id": "friend"},
        "state": {"moves": "", "status": "started"},
    })
    assert batch_pause.is_set() and analysis_stop.is_set()
    assert {"wakeup", "live_game", "batch", "archive", "puzzle", "backfill"} <= set(effects)
    assert not window._dashboard_analysis_safety_allowed
    window.game_repository.shutdown()
    window.close()


def test_swg05_gamefull_official_ai_identity_does_not_pause_human_only_work(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "swg05-ai.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    effects = []
    monkeypatch.setattr(window, "_cancel_lichess_backfill_wakeup", lambda: effects.append("wakeup"))
    monkeypatch.setattr(window, "_cancel_legacy_metrics_batch", lambda reason: effects.append(reason))
    monkeypatch.setattr(window, "_pause_batch_analysis",
                        lambda manual=False: effects.append("batch"))
    window._position_eval_mode = True
    position_stop = threading.Event()
    window._position_eval_stop = position_stop
    window.lichess = type("Service", (), {
        "username": "Owner",
        "cancel_archive_sync": lambda self: effects.append("archive"),
        "cancel_puzzle_sync": lambda self: effects.append("puzzle"),
        "cancel_lichess_analysis_backfill": lambda self: effects.append("backfill"),
    })()
    window._lichess_game_event("reconnected-ai", {
        "type": "gameFull", "id": "reconnected-ai", "source": "ai",
        "opponent": {"id": None, "ai": 4}, "initialFen": "startpos", "rated": False,
        "white": {"name": "Owner", "id": "owner"},
        "black": {"name": "Lichess AI level 4"},
        "state": {"moves": "", "status": "started"},
    })
    assert effects == []
    assert not position_stop.is_set()
    assert window._position_eval_mode
    window.game_repository.shutdown()
    window.close()


@pytest.mark.parametrize("identity", [
    {"source": "friend", "opponent": {"id": "friend", "username": "Friend"}},
    {"source": "correspondence", "opponent": {"id": "correspondent"}},
    {"source": None, "opponent": None},
])
def test_mg01_gamefull_only_human_revokes_inflight_position_evaluation(
        qtbot, tmp_path, monkeypatch, identity):
    """A resumed human game without gameStart must invalidate a late UCI result."""
    window = MainWindow(QSettings(str(tmp_path / "mg01-position.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    service = type("Service", (), {"username": "Owner"})()
    window.lichess = service
    window._lichess_connected = True
    window._current_lichess_generation = lambda _generation: True
    window._online_actions_allowed = lambda: True
    window._sessions_for_safety = lambda: tuple(window.online_sessions.values())
    window._position_eval_mode = True
    window._position_eval_generation = 41
    stop = threading.Event()
    window._position_eval_stop = stop

    class Worker:
        joined = False
        def join(self, timeout=None): self.joined = True

    worker = Worker()
    window._position_eval_handle = {"worker_thread": worker}
    window._position_eval_thread = worker
    window.position_eval_label.show()
    score_calls = []
    monkeypatch.setattr(window.moves, "set_score", lambda *a, **kw: score_calls.append((a, kw)))
    monkeypatch.setattr(window, "_pause_heavy_work_for_live_game", lambda: None)

    window._lichess_game_event("resumed-human", {
        "type": "gameFull", "id": "resumed-human", **identity,
        "initialFen": "startpos",
        "white": {"name": "Owner", "id": "owner"},
        "black": {"name": "Friend", "id": "friend"},
        "state": {"moves": "", "status": "started"},
    }, generation=1)

    assert stop.is_set(), "gameFull-only human state must stop the running position evaluator"
    assert window._position_eval_mode is False
    assert window._position_eval_generation > 41
    window._position_eval_finished(41, {
        "score_white_cp": 325, "mate_white": None, "best_move": "e2e4", "depth": 20,
    }, worker)
    assert worker.joined
    assert score_calls == [], "late engine success must not be committed or shown"
    assert "лучший ход" not in window.position_eval_label.text()
    window.game_repository.shutdown()
    window.close()


@pytest.mark.parametrize("cancel_kind", ["account_switch", "cancelled", "stale_line"])
def test_mg02_stale_library_retry_does_not_write_after_blocked_link_read(
        qtbot, tmp_path, monkeypatch, cancel_kind):
    """Fence SQLite side effects after a link read blocked across a session change."""
    window = MainWindow(QSettings(str(tmp_path / f"mg02-{cancel_kind}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    repository = window.game_repository
    game_id = _archive(repository, "AccountA", f"blocked-link-{cancel_kind}")
    account_id = "lichess:accounta"

    class Service:
        username = "AccountA"
        auth_generation = 23
        def is_auth_generation_current(self, generation):
            return generation == self.auth_generation
        def refresh_lichess_analysis_background(self, **_kwargs):
            pytest.fail("stale selected request must never reach Lichess")

    service = Service()
    window.lichess = service
    window._lichess_connected = True
    window._lichess_stream_ready_generation = service.auth_generation
    window._online_actions_allowed = lambda: True
    window._sessions_for_safety = lambda: []
    window.show_saved_games()
    page = window.library_page
    window.show()
    entered = threading.Event()
    release = threading.Event()
    validate = repository.validate_lichess_game_link

    def blocked_validate(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test barrier was not released"
        return validate(*args, **kwargs)

    monkeypatch.setattr(repository, "validate_lichess_game_link", blocked_validate)
    try:
        qtbot.waitUntil(lambda: page.games.count() > 0, timeout=5000)
        qtbot.waitUntil(lambda: page.retry_analysis_button.isEnabled(), timeout=5000)
        selected = page.games.item(0).data(Qt.ItemDataRole.UserRole)
        assert selected["game_id"] == game_id
        assert repository.lichess_analysis_backfill_state(account_id, game_id) is None
        QTest.mouseClick(page.retry_analysis_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(entered.is_set, timeout=3000)
        if cancel_kind == "account_switch":
            # LichessService keeps object identity while replacing the session.
            service.username = "AccountB"
            service.auth_generation += 1
        elif cancel_kind == "cancelled":
            handles = tuple(window.runtime.work_coordinator._operations.values())
            handle = next(item for item in handles
                          if item.snapshot.context.kind == "selected_lichess_analysis_retry")
            assert handle.cancel("account_switched")
        else:
            repository.save_snapshot(game_id, "startpos", "e2e4 e7e5", "white",
                                     "Opponent", False, "5+0", "mate", "white")
        release.set()
        qtbot.waitUntil(
            lambda: not any(item.snapshot.context.kind == "selected_lichess_analysis_retry"
                            for item in window.runtime.work_coordinator._operations.values()),
            timeout=5000)
        assert repository.lichess_analysis_backfill_state(account_id, game_id) is None, (
            "stale/cancelled worker must not enqueue or mutate the previous account's retry state")
        assert repository.lichess_analysis_retry_after(account_id) is None
    finally:
        release.set()
        window.game_repository.shutdown()
        window.close()


def test_mg02_repository_rechecks_session_after_blocked_sqlite_write_lock(tmp_path):
    """Session invalidation while BEGIN IMMEDIATE waits causes no durable mutation."""
    import sqlite3

    repository = GameRepository(tmp_path / "mg02-write-fence.sqlite3")
    try:
        game_id = _archive(repository, "Owner", "write-fence-game")
        account_id = repository.ensure_archive_account("Owner")
        line = repository.current_line(game_id)
        assert line is not None
        owner = sqlite3.connect(repository.path, timeout=0)
        owner.execute("BEGIN IMMEDIATE")
        current = threading.Event()
        current.set()
        started = threading.Event()
        result = []

        def enqueue():
            started.set()
            result.append(repository.enqueue_lichess_analysis_backfill(
                account_id, game_id=game_id,
                expected_line_revision=line["line_revision"],
                expected_source_game_id="write-fence-game",
                generation_is_current=current.is_set))

        worker = threading.Thread(target=enqueue)
        worker.start()
        assert started.wait(2)
        time.sleep(0.05)  # writer lock is held while the operation is at BEGIN IMMEDIATE
        current.clear()
        owner.rollback()
        owner.close()
        worker.join(3)
        assert not worker.is_alive()
        assert result == [0]
        assert repository.lichess_analysis_backfill_state(account_id, game_id) is None

        assert repository.enqueue_lichess_analysis_backfill(
            account_id, game_id=game_id,
            expected_line_revision=line["line_revision"],
            expected_source_game_id="write-fence-game") == 1
        before = repository.lichess_analysis_backfill_state(account_id, game_id)
        assert before is not None and before["manual_retry_at"] is None
        owner = sqlite3.connect(repository.path, timeout=0)
        owner.execute("BEGIN IMMEDIATE")
        current.set()
        started.clear()
        result.clear()

        def retry():
            started.set()
            result.append(repository.retry_lichess_analysis_game(
                account_id, game_id, line_revision=line["line_revision"],
                source_game_id="write-fence-game", manual=True,
                generation_is_current=current.is_set))

        worker = threading.Thread(target=retry)
        worker.start()
        assert started.wait(2)
        time.sleep(0.05)
        current.clear()
        owner.rollback()
        owner.close()
        worker.join(3)
        assert not worker.is_alive()
        assert result == [False]
        assert repository.lichess_analysis_backfill_state(account_id, game_id) == before
    finally:
        repository.shutdown()


def test_swg02_selected_request_waits_for_busy_batch_then_runs_once(tmp_path, qtbot):
    repository = GameRepository(tmp_path / "swg02-busy.sqlite3")
    entered = threading.Event()
    release = threading.Event()

    class TokenStore:
        def get(self):
            return "synthetic-token"

    class Client:
        rate_limit_remaining = 0

        def __init__(self):
            self.playing_calls = 0
            self.exports = []

        def get_playing_games(self, _token):
            self.playing_calls += 1
            if self.playing_calls == 1:
                entered.set()
                assert release.wait(3)
            return []

        def export_game_json(self, _token, source_id):
            self.exports.append(source_id)
            return {"id": source_id, "status": "mate", "analysis": [],
                    "pgn": ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
                             '1. e4 e5 2. Nf3 Nc6 1-0'),
                    "players": {"white": {"user": {"name": "Owner"}},
                                "black": {"user": {"name": "Opponent"}}}}

    client = Client()
    service = LichessService(client=client, token_store=TokenStore(), repository=repository)
    service.username = "Owner"
    service._auth_generation = 4
    account_id = repository.ensure_archive_account("Owner")
    global_id = _archive(repository, "Owner", "global-batch-item")
    selected_id = _archive(repository, "Owner", "selected-while-busy")
    repository.enqueue_lichess_analysis_backfill(account_id)
    selected_line = repository.current_line(selected_id)
    assert repository.retry_lichess_analysis_game(
        account_id, selected_id, line_revision=selected_line["line_revision"],
        source_game_id="selected-while-busy")

    try:
        service.refresh_lichess_analysis_background(
            generation=4, limit=1, preferred_game_id=global_id)
        assert entered.wait(3)
        assert service.refresh_lichess_analysis_background(
            generation=4, selected_game_id=selected_id,
            expected_line_revision=selected_line["line_revision"],
            expected_source_game_id="selected-while-busy") == "queued"
        release.set()
        qtbot.waitUntil(lambda: client.exports.count("selected-while-busy") == 1,
                        timeout=8_000)
        assert client.exports.count("selected-while-busy") == 1
        assert client.exports.count("global-batch-item") == 1
    finally:
        release.set()
        service.disconnect_account()
        for thread in list(service._threads):
            if hasattr(thread, "join"):
                thread.join(timeout=2)
        repository.shutdown()


@pytest.mark.parametrize("finish_mode", ["disconnect", "account_switch", "429"])
def test_swg02_busy_selected_request_is_discarded_on_disconnect_and_blocked_by_429(
        tmp_path, qtbot, finish_mode):
    from chess_coach.lichess.api import RateLimitError

    repository = GameRepository(tmp_path / f"swg02-{finish_mode}.sqlite3")
    entered = threading.Event()
    release = threading.Event()

    class TokenStore:
        def get(self): return "synthetic-token"

    class Client:
        rate_limit_remaining = 0
        def __init__(self): self.playing_calls = 0; self.exports = []
        def get_playing_games(self, _token):
            self.playing_calls += 1
            if self.playing_calls == 1:
                entered.set()
                assert release.wait(3)
            return []
        def export_game_json(self, _token, source_id):
            self.exports.append(source_id)
            if finish_mode == "429" and source_id == "global-before-429":
                raise RateLimitError(1200)
            return {"id": source_id, "status": "mate", "analysis": [],
                    "pgn": ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
                             '1. e4 e5 2. Nf3 Nc6 1-0'),
                    "players": {"white": {"user": {"name": "Owner"}},
                                "black": {"user": {"name": "Opponent"}}}}

    client = Client()
    service = LichessService(client=client, token_store=TokenStore(), repository=repository)
    service.username = "Owner"
    service._auth_generation = 5
    account_id = repository.ensure_archive_account("Owner")
    global_id = _archive(repository, "Owner", "global-before-429")
    selected_id = _archive(repository, "Owner", "selected-after-switch")
    repository.enqueue_lichess_analysis_backfill(account_id)
    selected_line = repository.current_line(selected_id)
    assert repository.retry_lichess_analysis_game(
        account_id, selected_id, line_revision=selected_line["line_revision"],
        source_game_id="selected-after-switch")
    try:
        service.refresh_lichess_analysis_background(
            generation=5, limit=1, preferred_game_id=global_id)
        assert entered.wait(3)
        assert service.refresh_lichess_analysis_background(
            generation=5, selected_game_id=selected_id,
            expected_line_revision=selected_line["line_revision"],
            expected_source_game_id="selected-after-switch") == "queued"
        if finish_mode == "disconnect":
            service.disconnect_account()
        elif finish_mode == "account_switch":
            service._cancel_active_session()
            service.username = "AccountB"
        release.set()
        qtbot.waitUntil(lambda: all(not thread.is_alive() for thread in service._threads),
                        timeout=8000)
        qtbot.wait(50)
        assert "selected-after-switch" not in client.exports
        if finish_mode == "429":
            assert client.exports == ["global-before-429"]
            assert client.playing_calls == 1  # no selected preflight during Retry-After
            assert repository.lichess_analysis_retry_after(account_id) is not None
        else:
            assert client.exports == []
            assert not service._pending_selected_analysis
    finally:
        release.set()
        service.disconnect_account()
        for thread in list(service._threads):
            if hasattr(thread, "join"):
                thread.join(timeout=2)
        repository.shutdown()


def test_swg02_service_close_discards_pending_selected_request(tmp_path):
    repository = GameRepository(tmp_path / "swg02-close.sqlite3")
    entered = threading.Event()
    release = threading.Event()

    class TokenStore:
        def get(self): return "synthetic-token"

    class Client:
        rate_limit_remaining = 0
        def __init__(self): self.playing_calls = 0; self.exports = []
        def get_playing_games(self, _token):
            self.playing_calls += 1
            if self.playing_calls == 1:
                entered.set()
                assert release.wait(3)
            return []
        def export_game_json(self, _token, source_id):
            self.exports.append(source_id)
            raise AssertionError("closed service must not dispatch selected retry")

    client = Client()
    service = LichessService(client=client, token_store=TokenStore(), repository=repository)
    service.username = "Owner"
    service._auth_generation = 6
    account_id = repository.ensure_archive_account("Owner")
    global_id = _archive(repository, "Owner", "global-close-item")
    selected_id = _archive(repository, "Owner", "selected-close-item")
    repository.enqueue_lichess_analysis_backfill(account_id)
    line = repository.current_line(selected_id)
    assert repository.retry_lichess_analysis_game(
        account_id, selected_id, line_revision=line["line_revision"],
        source_game_id="selected-close-item")
    try:
        assert service.refresh_lichess_analysis_background(
            generation=6, limit=1, preferred_game_id=global_id) == "started"
        assert entered.wait(3)
        assert service.refresh_lichess_analysis_background(
            generation=6, selected_game_id=selected_id,
            expected_line_revision=line["line_revision"],
            expected_source_game_id="selected-close-item") == "queued"
        close_thread = threading.Thread(target=lambda: service.close(timeout=0.01))
        close_thread.start()
        close_thread.join(timeout=1)
        assert not close_thread.is_alive()
        assert not service._pending_selected_analysis
        release.set()
        for thread in list(service._threads):
            thread.join(timeout=2)
        assert all(not thread.is_alive() for thread in service._threads)
        assert client.exports == []
    finally:
        release.set()
        service.close(timeout=2)
        repository.shutdown()


def test_swg03_manual_retry_debounce_survives_sqlite_v22_migration(tmp_path):
    import sqlite3

    path = tmp_path / "swg03-v22-migration.sqlite3"
    repository = GameRepository(path)
    game_id = _archive(repository, "Owner", "migration-preserves-queue")
    account_id = repository.ensure_archive_account("Owner")
    repository.enqueue_lichess_analysis_backfill(account_id)
    repository.shutdown()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE lichess_analysis_backfill DROP COLUMN manual_retry_at")
        db.execute("PRAGMA user_version=22")
    migrated = GameRepository(path)
    try:
        state = migrated.lichess_analysis_backfill_state(account_id, game_id)
        assert state is not None
        assert state["game_id"] == game_id
        with migrated._connection() as db:
            columns = {row[1] for row in db.execute(
                "PRAGMA table_info(lichess_analysis_backfill)")}
        assert "manual_retry_at" in columns
        assert migrated.get_game(game_id)["source_game_id"] == "migration-preserves-queue"
    finally:
        migrated.shutdown()


def test_swg03_new_line_revision_does_not_inherit_manual_retry_cooldown(tmp_path):
    repository = GameRepository(tmp_path / "swg03-revision-cooldown.sqlite3")
    try:
        game_id = _archive(repository, "Owner", "revision-clears-manual-cooldown")
        account_id = repository.ensure_archive_account("Owner")
        repository.enqueue_lichess_analysis_backfill(account_id)
        line = repository.current_line(game_id)
        assert repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=line["line_revision"],
            source_game_id="revision-clears-manual-cooldown", manual=True)
        assert repository.lichess_analysis_backfill_state(
            account_id, game_id)["manual_retry_at"]

        repository.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", "white",
                                 "Opponent", False, "5+0", "mate", "white")
        current = repository.current_line(game_id)
        assert current["line_revision"] > line["line_revision"]
        repository.enqueue_lichess_analysis_backfill(
            account_id, game_id=game_id,
            expected_line_revision=current["line_revision"],
            expected_source_game_id="revision-clears-manual-cooldown")
        state = repository.lichess_analysis_backfill_state(account_id, game_id)
        assert state["line_revision"] == current["line_revision"]
        assert state["manual_retry_at"] is None
    finally:
        repository.shutdown()


def test_swg03_manual_exception_never_bypasses_429_or_request_budget(tmp_path):
    repository = GameRepository(tmp_path / "swg03-limits.sqlite3")
    try:
        game_id = _archive(repository, "Owner", "manual-limit-game")
        account_id = repository.ensure_archive_account("Owner")
        repository.enqueue_lichess_analysis_backfill(account_id)
        line = repository.current_line(game_id)
        repository.record_lichess_analysis_backfill(
            account_id, game_id, accuracy_status="no_server_data", evals_status="partial",
            line_revision=line["line_revision"], line_hash=line["line_hash"],
            retry_after_seconds=7 * 24 * 60 * 60)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with repository._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill SET accuracy_checked_at=?,evals_checked_at=? "
                       "WHERE account_id=? AND game_id=?", (old, old, account_id, game_id))
        repository.set_lichess_analysis_retry_after(account_id, 3600)
        assert not repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=line["line_revision"],
            source_game_id="manual-limit-game", manual=True)
        with repository._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill_budget SET retry_after_at=NULL,"
                       "utc_day=?,requests=25 "
                       "WHERE account_id=?", (datetime.now(timezone.utc).date().isoformat(),
                                                account_id))
        assert repository.retry_lichess_analysis_game(
            account_id, game_id, line_revision=line["line_revision"],
            source_game_id="manual-limit-game", manual=True)

        class NoCallClient:
            rate_limit_remaining = 0
            def export_game_json(self, *_args): raise AssertionError("daily cap must block export")

        report = ArchiveSync(NoCallClient(), repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), daily_limit=25,
            selected_game_id=game_id, expected_line_revision=line["line_revision"],
            expected_source_game_id="manual-limit-game")
        assert report["limited"] and report["checked"] == 0
        assert repository.lichess_analysis_retry_after(account_id) is not None
    finally:
        repository.shutdown()
