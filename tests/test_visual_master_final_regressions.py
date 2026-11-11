from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QSettings

from chess_coach.application import dashboard as dashboard_module
from chess_coach.lichess import service_sync
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.service import LichessService
from chess_coach.window import MainWindow


class _TokenStore:
    def __init__(self, value: str | None):
        self.value = value
        self._lock = threading.RLock()
        self._read_count = 0
        self.compare_read = threading.Event()
        self.release_compare = threading.Event()
        self.atomic_delete_entered = threading.Event()

    def get(self):
        with self._lock:
            self._read_count += 1
            value = self.value
        if self._read_count == 2:
            # Deterministically pause in the old get-then-delete window.
            self.compare_read.set()
            assert self.release_compare.wait(2)
        return value

    def set(self, value):
        with self._lock:
            self.value = value

    def delete(self):
        with self._lock:
            self.value = None

    def delete_if_value(self, value: str) -> bool:
        # The production store implements this as one credential-slot operation.
        with self._lock:
            self.atomic_delete_entered.set()
            assert self.release_compare.wait(2)
            if self.value != value:
                return False
            self.value = None
            return True


class _RevokeClient:
    def __init__(self):
        self.revoked: list[str] = []
        self.done = threading.Event()

    def revoke_token(self, token):
        self.revoked.append(token)
        self.done.set()


def test_oauth_revoke_cannot_delete_replacement_written_at_compare_delete_boundary():
    oauth = _TokenStore("old-oauth")
    client = _RevokeClient()
    service = LichessService(client=client, token_store=oauth,
                             personal_token_store=_TokenStore("pat"), auth_method="oauth")
    service.revoke_oauth_credential()
    assert oauth.compare_read.wait(1) or oauth.atomic_delete_entered.wait(1)

    replacement_done = threading.Event()
    replacement = threading.Thread(target=lambda: (oauth.set("new-oauth"), replacement_done.set()))
    replacement.start()
    # If the conditional-delete operation is atomic, the setter waits until it
    # finishes and then writes the replacement. The legacy two-call sequence
    # pauses after its compare read, allowing the replacement to be deleted.
    oauth.release_compare.set()
    replacement.join(timeout=1)
    assert replacement_done.is_set()
    assert client.done.wait(1)
    for thread in list(service._threads):
        thread.join(timeout=1)
    assert client.revoked == ["old-oauth"]
    assert oauth.value == "new-oauth"
    service.close(timeout=1)


def test_service_close_stops_ready_analytics_backfill_event():
    service = LichessService(client=_RevokeClient(), token_store=_TokenStore(None),
                             personal_token_store=_TokenStore(None))
    backfill_stop = threading.Event()
    service._analysis_backfill_stop = backfill_stop
    assert service.close(timeout=0)
    assert backfill_stop.is_set()


def test_enqueue_does_not_requeue_a_live_ready_analytics_lease(tmp_path):
    repository = GameRepository(tmp_path / "ready-live-lease.sqlite3")
    account_id = repository.ensure_archive_account("Me")
    repository.import_archive_record(
        "Me", {"id": "lease-game", "pgn": '[White "Me"]\n[Black "Them"]\n'
         '[Result "1-0"]\n\n1. e4 1-0'}, checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill(account_id)
    row = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    repository.mark_lichess_analysis_backfill_running(
        account_id, row["game_id"], line_revision=row["line_revision"],
        line_hash=row["line_hash"])
    try:
        repository.enqueue_lichess_analysis_backfill(account_id)
        with repository._connection() as db:
            status = db.execute("SELECT accuracy_status FROM lichess_analysis_backfill "
                                 "WHERE account_id=? AND game_id=?",
                                 (account_id, row["game_id"])).fetchone()[0]
        assert status == "running"
        with repository._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill SET updated_at='2020-01-01T00:00:00+00:00' "
                       "WHERE account_id=? AND game_id=?",
                       (account_id, row["game_id"]))
        repository.enqueue_lichess_analysis_backfill(account_id)
        with repository._connection() as db:
            recovered = db.execute("SELECT accuracy_status FROM lichess_analysis_backfill "
                                   "WHERE account_id=? AND game_id=?",
                                   (account_id, row["game_id"])).fetchone()[0]
        assert recovered == "queued"
    finally:
        repository.shutdown()


def test_dashboard_preview_does_not_wait_for_source_accuracy_aggregate(tmp_path, monkeypatch):
    repository = GameRepository(tmp_path / "preview-no-aggregate.sqlite3")
    account_id = repository.ensure_archive_account("synthetic")
    repository.import_archive_record(
        "synthetic", {"id": "one", "pgn": '[Result "*"]\n\n*'},
        checkpoint_ms=None, backfill=False)

    def expensive_aggregate(*_args, **_kwargs):
        raise AssertionError("preview must not run source-wide accuracy aggregation")

    monkeypatch.setattr(dashboard_module, "_dashboard_accuracy_stats", expensive_aggregate)
    try:
        preview = dashboard_module.dashboard_preview_snapshot(repository, account_id=account_id)
        assert preview["recent_games"]
        assert preview["metrics"][0]["value"] == "1"
    finally:
        repository.shutdown()


def test_dashboard_preview_fast_worker_does_not_need_post_start_handle(
        qtbot, tmp_path, monkeypatch):
    from PySide6.QtTest import QSignalSpy

    window = MainWindow(QSettings(str(tmp_path / "preview-fast-worker.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window._dashboard_preview_thread is None, timeout=3000)
    window._dashboard_options_loaded = True
    monkeypatch.setattr(dashboard_module, "dashboard_preview_snapshot",
                        lambda *_args, **_kwargs: {"recent_games": [], "filter": {"account_id": None}})
    window.dashboard_preview_ready.disconnect(window._dashboard_preview_result_ready)
    ready = QSignalSpy(window.dashboard_preview_ready)

    class Handle:
        def is_alive(self):
            return False

    def run_before_return(target, **_kwargs):
        # Deterministically reproduce a worker that completes before the
        # coordinator's start() call returns its OperationHandle.
        target()
        return Handle()

    monkeypatch.setattr(window, "_start_registered_work", run_before_return)
    window._request_dashboard_preview(17, None, None)

    assert ready.count() == 1
    args = ready.at(0)
    assert args[0] == 17
    assert args[3]
    window._dashboard_preview_result_ready(*args)
    assert window._dashboard_preview_thread is None
    # The startup aggregate is independent of this preview-handle regression.
    # Let it release before asserting that a normal close is accepted.
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=5000)
    assert window.close() is True


def test_ready_analytics_preview_uses_no_post_start_mutable_handle_slot(qtbot, tmp_path, monkeypatch):
    import chess_coach.window as window_module

    window = MainWindow(
        QSettings(str(tmp_path / "preview-handle.ini"), QSettings.Format.IniFormat),
        offline_mode=True,
    )
    monkeypatch.setattr(window_module, "dashboard_preview_snapshot", lambda *_a, **_k: {})
    monkeypatch.setattr(window_module, "dashboard_options", lambda *_a, **_k: {})

    class _Handle:
        pass

    def run_before_return(target, **_kwargs):
        target()
        return _Handle()

    monkeypatch.setattr(window, "_start_registered_work", run_before_return)
    try:
        window._request_dashboard_preview(1, None, None)
    finally:
        window.close()


def test_home_accuracy_tooltip_is_compact_but_accessible_description_keeps_provenance(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "accuracy-tooltip.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window._dashboard_analysis_safety_allowed = True
    window._render_dashboard_snapshot({
        "metrics": [
            {"key": "games", "value": "12", "detail": "12 сохранено", "source": "SQLite"},
            {"key": "accuracy", "value": "72.4%", "detail": "Lichess 70%; Stockfish 74%",
             "source": "Официальная точность Lichess 70% (8 партий); локальная формула "
                       "chess-coach-accuracy-v3 Stockfish 74% (9 партий); пересечение 5"},
            {"key": "analysis", "value": "9/12", "detail": "9 партий", "source": "Stockfish"},
        ],
        "recent_games": [],
    })
    tooltip = window._dashboard_metric_values["accuracy"].toolTip()
    accessible = window._dashboard_metric_values["accuracy"].accessibleDescription()
    assert len(tooltip) < 200
    assert "chess-coach-accuracy-v3" not in tooltip
    assert "chess-coach-accuracy-v3" in accessible
    assert "пересечение 5" in accessible
    assert "Lichess" in tooltip and "Stockfish" in tooltip
    window.close()


def test_live_game_defers_only_fairness_sensitive_background_workflows(qtbot, tmp_path,
                                                                       monkeypatch):
    from chess_coach.lichess.session import LichessGameSession
    from chess_coach.ui.pages.background import BackgroundOperationsPage

    window = MainWindow(QSettings(str(tmp_path / "background-live-game.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    session = LichessGameSession("synthetic-live-game", window.game_repository)
    session.status = "playing"
    window.game_sessions.register(session)
    class FakeService:
        username = "Synthetic"
        auth_generation = 2
    window.lichess = FakeService()
    window._background_status_generation = 4
    monkeypatch.setattr(window, "_library_detail_safety_allowed", lambda: False)
    page = BackgroundOperationsPage(window)
    window._background_operations_page = page
    captured = {}
    original = page.set_snapshot
    def capture(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(page, "set_snapshot", capture)
    window._background_operations_result_ready(4, {
        "account_id": "lichess:synthetic", "username": "Synthetic", "available": True,
        "archive": {"imported_count": 1, "backfill_complete": False},
        "puzzles": {"imported_count": 2, "backfill_complete": False},
        "summary": {"total": 4, "checked": 1, "accuracy": 1, "evals": 1,
                    "no_data": 0, "errors": 0, "pending": 3, "retry_waiting": 0,
                    "requests_today": 1},
        "paused": False, "operations": (), "jobs": {}, "last_job": {},
        "metric_scope": None, "metric_status": {}, "unqueued": None,
        "next_eligible_at": None, "daily_limit": 25,
    })
    states = captured["states"]
    assert states["archive"] == "Ожидает"
    assert states["puzzles"] == "Ожидает"
    assert states["ready"] == "Отложено до конца игры"
    assert states["stockfish"] == "Отложено до конца игры"

    requested = []
    monkeypatch.setattr(window, "_start_lichess_analysis_backfill",
                        lambda *args, **kwargs: requested.append((args, kwargs)))
    monkeypatch.setattr(window, "_refresh_background_operations_page", lambda: None)
    window.offline_mode = False
    window._lichess_connected = True
    window._lichess_stream_ready_generation = 2
    window._manual_lichess_analytics_check()
    assert requested == []
    assert "отложена до завершения активной партии" in window.lichess_analysis_status.text()
    window.close()


def test_archive_sync_abandoned_at_auth_switch_does_not_use_new_account_token(monkeypatch):
    calls = []
    scheduled = []

    class _ArchiveSync:
        def __init__(self, _client, _repository):
            pass

        def run(self, username, token, *_args, **_kwargs):
            calls.append((username, token))
            return 1

    monkeypatch.setattr(service_sync, "ArchiveSync", _ArchiveSync)
    old = _TokenStore("old-oauth")
    new = _TokenStore("new-pat")
    service = LichessService(client=object(), token_store=old, repository=object(),
                             personal_token_store=new, auth_method="oauth")
    service.username = "account-a"
    service._thread = lambda target, _name: scheduled.append(target)
    service.sync_archive(background=True)
    service.set_auth_method("personal_token")
    service.username = "account-b"
    scheduled.pop()()
    assert calls == []
    service.close(timeout=0)


def test_cancelled_archive_sync_allows_a_new_explicit_run(monkeypatch):
    scheduled = []
    service = LichessService(client=object(), token_store=_TokenStore("synthetic"),
                             repository=object())
    service.username = "synthetic-account"
    service._thread = lambda target, _name: scheduled.append(target)

    service.sync_archive(background=True)
    first_stop = service._archive_stop
    service.cancel_archive_sync()
    service.sync_archive(background=True)

    assert first_stop is not None and first_stop.is_set()
    assert len(scheduled) == 2, "an already-cancelled lease must not block a retry"
    assert service._archive_stop is not first_stop
    service.close(timeout=0)


def test_session_switch_cancels_puzzle_oauth_callback(monkeypatch):
    class _Callback:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    service = LichessService(client=object(), token_store=_TokenStore("synthetic"))
    callback = _Callback()
    service._puzzle_callback = callback

    service.disconnect_account()

    assert callback.cancelled
    assert service._puzzle_callback is None
    service.close(timeout=0)


def test_send_move_ack_emits_sound_eligibility_only_after_success(monkeypatch):
    from chess_coach.lichess.api import LichessError

    class _Client:
        fail = False

        def send_move(self, _token, _game_id, _uci):
            if self.fail:
                raise LichessError("rejected", status=400)

    class _Store:
        def get(self):
            return "synthetic-token"

        def delete(self):
            pass

    store = _Store()
    client = _Client()
    service = LichessService(client=client, token_store=store)
    service.username = "synthetic-account"
    generation = service.auth_generation
    assert service.mark_event_stream_ready(generation)
    service.reconcile_game_actions(
        "synthetic-game", {"type": "gameFull", "state": {
            "status": "started", "moves": "", "wdraw": False, "bdraw": False}},
        generation, snapshot_sequence=1)
    sequence = service._game_authoritative_sequence[(generation, "synthetic-game")]
    assert service.authorize_game_actions(
        "synthetic-game", generation, sequence, color="white")
    service._thread = lambda target, _name: target()
    accepted, failed = [], []
    service.session_move_accepted.connect(lambda *args: accepted.append(args))
    service.session_move_failed.connect(lambda *args: failed.append(args))

    service.send_move("synthetic-game", "e2e4", "request-ok")
    assert accepted == [(service.auth_generation, "synthetic-game", "e2e4", "request-ok")]
    # A second intent is admitted only after authoritative server progress
    # settles the first one-shot request.
    service.reconcile_game_actions(
        "synthetic-game", {"type": "gameState", "state": {
            "status": "started", "moves": "e2e4", "wdraw": False, "bdraw": False}},
        generation)
    client.fail = True
    service.send_move("synthetic-game", "d2d4", "request-rejected")
    assert len(accepted) == 1
    assert failed == [(service.auth_generation, "synthetic-game", "d2d4", "request-rejected", 400)]
    service.close(timeout=0)


def test_invalid_partial_lichess_payload_is_not_counted_as_valid_accuracy(tmp_path):
    repository = GameRepository(tmp_path / "invalid-accuracy.sqlite3")
    repository.import_archive_record(
        "Me", {"id": "synthetic-invalid", "pgn": '[Result "1-0"]\n\n1. e4 1-0'},
        checkpoint_ms=None, backfill=False)
    account_id = "lichess:me"
    repository.enqueue_lichess_analysis_backfill(account_id)
    repository.record_lichess_analysis_backfill(
        account_id, "synthetic-invalid", accuracy_status="partial",
        evals_status="no_server_data")
    try:
        summary = repository.lichess_analysis_backfill_summary(account_id)
        assert summary["accuracy"] == 0
    finally:
        repository.shutdown()


def test_partial_status_with_persisted_official_accuracy_is_counted(tmp_path):
    repository = GameRepository(tmp_path / "partial-valid-accuracy.sqlite3")
    account_id = repository.ensure_archive_account("Me")
    repository.import_archive_record(
        "Me", {"id": "synthetic-partial", "pgn": '[Result "1-0"]\n\n1. e4 1-0'},
        checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill(account_id)
    with repository._connection() as db:
        db.execute("UPDATE games SET lichess_accuracy_black=87.5 "
                   "WHERE source_game_id='synthetic-partial'")
    repository.record_lichess_analysis_backfill(
        account_id, "synthetic-partial", accuracy_status="partial",
        evals_status="no_server_data")
    try:
        assert repository.lichess_analysis_backfill_summary(account_id)["accuracy"] == 1
    finally:
        repository.shutdown()


def test_no_server_analysis_is_not_reported_as_last_data_received(tmp_path):
    repository = GameRepository(tmp_path / "no-analysis-last-data.sqlite3")
    repository.import_archive_record(
        "Me", {"id": "synthetic-empty", "pgn": '[Result "1-0"]\n\n1. e4 1-0'},
        checkpoint_ms=None, backfill=False)
    account_id = "lichess:me"
    repository.enqueue_lichess_analysis_backfill(account_id)
    repository.record_lichess_analysis_backfill(
        account_id, "synthetic-empty", accuracy_status="no_server_data",
        evals_status="no_server_data")
    try:
        summary = repository.lichess_analysis_backfill_summary(account_id)
        assert summary["last_success_at"] is None
    finally:
        repository.shutdown()


@pytest.mark.parametrize("session_is_current, expected_delays", [(True, [500]), (False, [])])
def test_cancelled_manual_analysis_only_resumes_batch_for_current_session(
        qtbot, tmp_path, monkeypatch, session_is_current, expected_delays):
    window = MainWindow(QSettings(str(tmp_path / f"analysis-cancel-{session_is_current}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    original_service = type("Service", (), {"username": "AccountA"})()
    current_service = original_service if session_is_current else type(
        "Service", (), {"username": "AccountB"})()
    window.lichess = current_service
    window.offline_mode = False
    window._lichess_connected = True
    window._analysis_session_generation = 4 if session_is_current else 5
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)

    class Timer:
        def __init__(self):
            self.delays = []

        def start(self, delay):
            self.delays.append(delay)

    timer = Timer()
    window._batch_resume_timer = timer
    request_id = "cancelled-analysis-request"
    stop = threading.Event()
    stop.set()
    window._analysis_safety_request_id = request_id
    window._analysis_safety_phase = "running"
    window._analysis_worker_context = (
        request_id, "finished-game", 4, original_service, False, stop)
    window._analysis_user_cancelled_request_id = request_id

    window._analysis_finished("finished-game", {"cancelled": True})

    assert timer.delays == expected_delays
    assert window._analysis_user_cancelled_request_id is None
    assert window.close() is True
