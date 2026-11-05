from __future__ import annotations

import json
import threading

import chess
import pytest
from PySide6.QtCore import QSettings

from chess_coach.lichess.api import LichessError
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.service import LichessService
from chess_coach.ui.dialogs import SettingsDialog
from chess_coach.window import MainWindow


class _Client:
    rate_limit_remaining = 0

    def get_playing_games(self, _token):
        return []


class _Store:
    def get(self):
        return "synthetic-token"


class _ReadOnlyService:
    username = "SyntheticB"
    auth_generation = 8
    client = _Client()

    def __init__(self):
        self.client = _Client()
        self.safety_requests = []
        self.archive_calls = []
        self.puzzle_calls = []
        self.analytics_calls = []
        self.disconnect_calls = 0

    def is_auth_generation_current(self, generation):
        return generation == self.auth_generation

    def check_active_games_for_analysis(self, request_id):
        self.safety_requests.append(request_id)

    def sync_archive(self, **kwargs):
        self.archive_calls.append(kwargs)

    def sync_puzzle_activity_background(self, repository):
        self.puzzle_calls.append(repository)

    def refresh_lichess_analysis_background(self, **kwargs):
        self.analytics_calls.append(kwargs)

    def disconnect_account(self):
        self.disconnect_calls += 1
        self.auth_generation += 1
        self.username = None

    def close(self, timeout=1.0):
        return True


def _window(qtbot, tmp_path, *, name="rf6", settings=None):
    window = MainWindow(
        settings or QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat),
        offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=5000)
    return window


class _BackfillRepo:
    def __init__(self):
        self.paused = False

    def ensure_archive_account(self, username):
        return f"lichess:{username.casefold()}"

    def lichess_analysis_backfill_has_due_work(self, *_args, **_kwargs):
        return True

    def set_lichess_analysis_backfill_paused(self, _account, paused):
        self.paused = bool(paused)

    def lichess_analysis_backfill_paused(self, _account):
        return self.paused

    def lichess_analysis_backfill_summary(self, _account):
        return {"checked": 1, "total": 3, "accuracy": 0, "evals": 0,
                "no_data": 1, "errors": 1}

    def lichess_analysis_backfill_next_eligible_at(self, *_args, **_kwargs):
        return ""

    def set_lichess_analysis_retry_after(self, *_args):
        pass


@pytest.mark.parametrize("code", (401, 403))
def test_ready_analytics_http_errors_are_typed_and_do_not_logout(monkeypatch, code):
    repo = _BackfillRepo()
    service = LichessService(client=_Client(), token_store=_Store(), repository=repo)
    service.username = "SyntheticB"
    service._auth_generation = 8
    monkeypatch.setattr(service, "_thread", lambda target, _name: target())
    monkeypatch.setattr(ArchiveSync, "refresh_ready_analytics",
                        lambda *args, **kwargs: {"fatal_status": code})
    statuses, operation_events, finished = [], [], []
    service.session_status_changed.connect(lambda *args: statuses.append(args))
    service.background_operation_status.connect(lambda *args: operation_events.append(args))
    service.lichess_analysis_backfill_finished.connect(lambda *args: finished.append(args))

    service.refresh_lichess_analysis_background(generation=8)

    assert repo.paused is True
    assert operation_events == [(8, "ready_analytics", code,
                                 f"Очередь готовой аналитики приостановлена (HTTP {code}).")]
    assert not any("Ошибка" in status for _generation, status in statuses)
    assert service.username == "SyntheticB"
    assert service.is_auth_generation_current(8)
    assert finished and finished[0][-2] == f"пауза · HTTP {code}"


def test_background_analytics_denial_masks_dashboard_without_closing_game_lane(
        qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="analytics-403-mask")
    service = _ReadOnlyService()
    window.offline_mode = False
    window.lichess = service
    window.show()
    window._lichess_authenticated(service.username)
    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_event_stream_ready(service.auth_generation, service.username)
    window._dashboard_display_safety_reply(service.safety_requests[-1], [])
    window._render_dashboard_snapshot({"metrics": [
        {"key": "games", "value": "3", "detail": "B scope", "source": "SQLite"},
        {"key": "analysis", "value": "2/3", "detail": "analysis", "source": "Stockfish"},
        {"key": "accuracy", "value": "91%", "detail": "accuracy", "source": "Lichess"},
        {"key": "errors", "value": "1", "detail": "errors", "source": "Stockfish"},
    ], "recent_games": []})
    assert window._dashboard_metric_values["analysis"].text() == "2/3"

    window._lichess_background_operation_status(
        service.auth_generation, "ready_analytics", 403,
        "Очередь готовой аналитики приостановлена (HTTP 403).")

    assert window._lichess_account_authenticated is True
    assert window._lichess_connected is True
    assert window._online_actions_allowed() is True
    assert window._dashboard_analysis_safety_allowed is False
    assert window._dashboard_metric_values["analysis"].text() == "скрыто"
    assert window._dashboard_metric_values["accuracy"].text() == "скрыто"
    assert window.safety_retry_button.isVisible()
    assert "HTTP 403" in window.lichess_analysis_status.text()
    request_count = len(service.safety_requests)
    window.safety_retry_button.click()
    assert len(service.safety_requests) == request_count + 1
    assert not window._dashboard_analysis_safety_allowed
    window.close()


def test_disconnect_is_clickable_during_restore_reconnect_and_rate_limit(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="disconnect-pending")
    service = _ReadOnlyService()
    window.offline_mode = False
    window.lichess = service
    window.show()
    window._lichess_authenticated(service.username)
    for status in ("Проверяем подключение…", "Переподключение…",
                   "Ограничение Lichess (429): повторите через 60 с"):
        window._lichess_status(status)
        assert window.disconnect_button.isVisible(), status
        assert window.disconnect_button.isEnabled(), status
        window.disconnect_button.click()
        assert service.disconnect_calls >= 1
        assert window._lichess_connected is False
        window._lichess_status("Не подключено")
        service.username = "SyntheticB"
        service.auth_generation += 1
        window._lichess_session_selected = True
        window._lichess_authenticated(service.username)
        # A late previous-generation readiness callback is ignored.
        stale = service.auth_generation - 1
        window._lichess_event_stream_ready(stale, service.username)
        assert window._lichess_connected is False
    window.close()


def test_safety_preflight_429_masks_until_due_retry_has_complete_reply(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="safety-retry-after")
    service = _ReadOnlyService()
    window.offline_mode = False
    window.lichess = service
    window.show()
    window._lichess_authenticated(service.username)
    window._lichess_event_stream_ready(service.auth_generation, service.username)
    request_id = service.safety_requests[-1]
    service.client.rate_limit_remaining = 60
    window._dashboard_display_safety_error(request_id, "HTTP 429 Retry-After: 60")
    assert window._dashboard_analysis_safety_allowed is False
    assert window.safety_retry_button.isVisible()
    assert window._dashboard_safety_retry_timer.isActive()
    assert window._dashboard_safety_retry_timer.remainingTime() >= 59_000

    count = len(service.safety_requests)
    window.safety_retry_button.click()
    assert len(service.safety_requests) == count  # API cooldown is still authoritative.
    service.client.rate_limit_remaining = 0
    window._dashboard_safety_retry_timer.start(1)  # advance only the synthetic clock
    qtbot.waitUntil(lambda: len(service.safety_requests) == count + 1, timeout=1000)
    assert window._dashboard_analysis_safety_allowed is False
    new_request = service.safety_requests[-1]
    window._dashboard_display_safety_reply(new_request, [])
    assert window._dashboard_analysis_safety_allowed is True
    window.close()


def test_new_account_home_scope_never_falls_back_to_all_accounts(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="home-account-scope")
    repo = window.game_repository
    repo.ensure_archive_account("SyntheticA")
    for number in range(3):
        game_id = f"account-a-{number}"
        pgn = (f'[Event "Synthetic A {number}"]\n[White "SyntheticA"]\n'
               f'[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n')
        repo.import_archive_record("SyntheticA", {"id": game_id, "pgn": pgn},
                                   checkpoint_ms=None, backfill=False)
    service = _ReadOnlyService()
    service.username = "SyntheticB"
    service.auth_generation = 80
    window.offline_mode = False
    window.lichess = service

    window._lichess_authenticated(service.username)

    assert window.dashboard_account.currentData() == "lichess:syntheticb"
    assert window._latest_dashboard_snapshot is None
    assert window._dashboard_metric_values["games"].text() in {"0", "н/д"}
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=5000)
    qtbot.wait(30)
    assert window._latest_dashboard_snapshot is not None
    assert window._latest_dashboard_snapshot["filter"]["account_id"] == "lichess:syntheticb"
    assert window._dashboard_metric_values["games"].text() == "0"
    assert window.dashboard_account.currentData() == "lichess:syntheticb"
    stale_generation = window._analysis_session_generation - 1
    window._dashboard_result_ready(stale_generation, {
        "filter": {"account_id": "lichess:synthetica"},
        "metrics": [{"key": "games", "value": "3", "detail": "A data", "source": "SQLite"}],
        "recent_games": [],
    }, None, None)
    assert window.dashboard_account.currentData() == "lichess:syntheticb"
    assert window._dashboard_metric_values["games"].text() == "0"
    window.close()


def test_home_game_target_opens_by_id_while_saved_filters_exclude_it(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "home-target.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/library_page", 1)
    settings.setValue("workspace/library_filters", json.dumps({
        "query": "", "outcome": "win", "account_id": None,
        "period_days": 0,
    }))
    window = _window(qtbot, tmp_path, name="home-target", settings=settings)
    repo = window.game_repository
    target = "target-lost-game"
    for number in range(86):
        game_id = target if number == 4 else f"synthetic-home-{number:03d}"
        winner = "black" if game_id == target else "white"
        repo.save_snapshot(game_id, chess.STARTING_FEN, "e2e4 e7e5", "white",
                           "Synthetic Opponent", False, "300+0", "mate", winner,
                           started_at=f"2026-10-{(number % 28) + 1:02d}T10:00:00Z")
    game = repo.get_game(target)
    game["review_metrics"] = {"status": "без анализа", "coverage": "0/2",
                              "source": "нет данных"}
    window._render_dashboard_snapshot({"metrics": [], "recent_games": [game]})
    window.show_saved_games()
    qtbot.waitUntil(lambda: window.library_page.games.count() > 0, timeout=5000)
    assert window._library_detail_target_id is None

    window._open_home_game_details(game)
    qtbot.waitUntil(lambda: window.library_page._detail_game is not None
                    and window.library_page._detail_game.get("game_id") == target,
                    timeout=5000)
    assert window.library_page.selected_game()["game_id"] != target
    assert window.library_page._detail_game["game_id"] == target
    assert window.library_page.detail_title.text() == "Synthetic Opponent"
    assert json.loads(settings.value("workspace/library_filters"))["outcome"] == "win"
    assert settings.value("workspace/library_page", type=int) == 1
    assert window._screen_mode != "review"
    window.close()


def test_background_sync_restores_incremental_archive_and_saved_puzzle_history(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="background-sync-restore")
    service = _ReadOnlyService()
    window.offline_mode = False
    window.lichess = service
    service.username = "SyntheticB"
    window.settings.setValue("lichess/auto_archive_sync", True)
    window.settings.setValue("lichess/auto_puzzle_history_sync", True)
    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_authenticated(service.username)
    window._lichess_event_stream_ready(service.auth_generation, service.username)
    qtbot.waitUntil(
        lambda: window._archive_account_ready_generation == service.auth_generation,
        timeout=3000)

    window._run_background_sync()

    assert service.archive_calls == [{"background": True}]
    assert service.puzzle_calls == [window.game_repository]
    assert service.analytics_calls == []
    assert window._auto_sync_timer.isActive()
    service.client.rate_limit_remaining = 17
    window._run_background_sync()
    assert len(service.archive_calls) == 1  # Shared cooldown gates every automatic lane.
    assert len(service.puzzle_calls) == 1
    window.close()


def test_background_sync_yields_to_human_game_and_shared_cooldown(qtbot, tmp_path):
    from types import SimpleNamespace

    window = _window(qtbot, tmp_path, name="background-sync-priority")
    service = _ReadOnlyService()
    service.username = "SyntheticB"
    window.offline_mode = False
    window.lichess = service
    window._lichess_account_authenticated = True
    window._lichess_session_selected = True
    window._lichess_connected = True
    window._lichess_stream_ready_generation = service.auth_generation
    window.online_sessions["human-game"] = SimpleNamespace(
        status="playing", is_ai_opponent=False)
    window._run_background_sync()
    assert not service.archive_calls and not service.puzzle_calls
    window.online_sessions.clear()
    service.client.rate_limit_remaining = 60
    window._run_background_sync()
    assert not service.archive_calls and not service.puzzle_calls
    service.client.rate_limit_remaining = 0
    window._run_background_sync()
    assert len(service.archive_calls) == 1 and len(service.puzzle_calls) == 1
    window.close()


def test_analytics_failure_status_is_scoped_to_auth_generation_and_account(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="analytics-status-account-scope")
    service = _ReadOnlyService()
    window.offline_mode = False
    window.lichess = service
    window._lichess_authenticated(service.username)
    window._lichess_background_operation_status(
        service.auth_generation, "ready_analytics", 403, "synthetic denial")
    assert window._ready_analytics_failure_status == "synthetic denial"
    service.username = "SyntheticC"
    service.auth_generation += 1
    window._lichess_authenticated(service.username)
    assert window._ready_analytics_failure_status == ""
    assert window._ready_analytics_failure_session is None
    window.close()


def test_auto_puzzle_history_sync_is_opt_in_to_existing_credential_only():
    service = LichessService(client=object(), token_store=_Store())
    assert callable(service.sync_puzzle_activity_background)
    assert service.puzzle_username is None


def test_safe_tooltip_has_source_only_after_preflight(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="provenance-tooltip")
    window.offline_mode = False
    window._dashboard_analysis_safety_allowed = False
    game = {"game_id": "synthetic", "status": "mate", "winner": "white",
            "color": "white", "opponent": "Synthetic", "started_at": "2026-11-05",
            "clock": "300+0", "review_metrics": {"status": "partial", "coverage": "2/4",
            "source": "Stockfish"}}
    window._render_dashboard_snapshot({"metrics": [], "recent_games": [game]})
    coverage = window._recent_games_layout.itemAt(0).widget().findChild(
        __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel,
        "dashboardAnalysisCoverage")
    assert "Stockfish" not in coverage.toolTip()
    assert "2/4" not in coverage.toolTip()
    window._dashboard_analysis_safety_allowed = True
    window._render_dashboard_snapshot({"metrics": [], "recent_games": [game]})
    coverage = window._recent_games_layout.itemAt(0).widget().findChild(
        __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel,
        "dashboardAnalysisCoverage")
    assert "Stockfish" in coverage.toolTip()
    assert "2/4" in coverage.toolTip()
    window.close()


def test_settings_expose_restored_automatic_archive_and_puzzle_sync(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.auto_lichess_archive.isChecked()
    assert dialog.auto_lichess_puzzles.isChecked()
    dialog.auto_lichess_archive.setChecked(False)
    dialog.auto_lichess_puzzles.setChecked(False)
    dialog.save(settings)
    assert settings.value("lichess/auto_archive_sync", True, type=bool) is False
    assert settings.value("lichess/auto_puzzle_history_sync", True, type=bool) is False


@pytest.mark.parametrize("mate_white, expected", [
    (3, "мат за белых через 3"),
    (-3, "мат за чёрных через 3"),
])
def test_live_position_eval_keeps_mate_typed_without_fake_cp(
        qtbot, tmp_path, monkeypatch, mate_white, expected):
    from types import SimpleNamespace

    window = _window(qtbot, tmp_path, name=f"quick-mate-{mate_white}")
    window._position_eval_generation = 7
    window._position_eval_stop = threading.Event()
    score = chess.engine.PovScore(chess.engine.Mate(mate_white), chess.WHITE)
    monkeypatch.setattr(window, "refresh", lambda *_args, **_kwargs: None)

    window._position_eval_update(7, {"score": score, "depth": 18})

    assert window.position_eval_label.text().find(expected) >= 0
    assert "1000.00" not in window.position_eval_label.text()
    assert "100000" not in window.position_eval_label.text()
    window.close()


@pytest.mark.parametrize("is_checkmate, expected", [
    (False, "мат в текущей позиции не подтверждён доской"),
    (True, "мат уже поставлен; победили чёрные"),
])
def test_live_position_eval_accepts_mate_zero_only_from_terminal_board(
        qtbot, tmp_path, monkeypatch, is_checkmate, expected):
    from types import SimpleNamespace

    window = _window(qtbot, tmp_path, name=f"quick-mate-zero-{is_checkmate}")
    board = chess.Board()
    if is_checkmate:
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            board.push_uci(uci)
    monkeypatch.setattr(window.controller, "snapshot",
                        lambda: SimpleNamespace(board=board))
    window._position_eval_generation = 9
    window._position_eval_stop = threading.Event()
    score = chess.engine.PovScore(chess.engine.Mate(0), chess.WHITE)
    monkeypatch.setattr(window, "refresh", lambda *_args, **_kwargs: None)

    window._position_eval_update(9, {"score": score, "depth": 22})

    assert expected in window.position_eval_label.text()
    assert "100000" not in window.position_eval_label.text()
    window.close()


def test_collapsed_sidebar_keeps_connection_actions_accessible_at_board_width(qtbot, tmp_path):
    window = _window(qtbot, tmp_path, name="collapsed-sidebar")
    window.resize(1280, 720)
    window.offline_mode = False
    window.lichess = _ReadOnlyService()
    window._lichess_status("Не подключено")
    window.start_local_game()
    window._set_sidebar_collapsed(True, explicit=True)
    window.show()
    qtbot.wait(20)
    assert window.sidebar.width() == 60
    assert window.connect_button.isVisible()
    assert window.connect_button.width() == 34
    assert window.connect_button.text() == "↗"
    assert window.connect_button.accessibleName() == "Подключить Lichess"
    assert window.connect_button.toolTip() == "Подключить Lichess"
    assert window.disconnect_button.width() == 34
    assert window.disconnect_button.accessibleName() == "Отключить Lichess"
    assert window.sidebar.geometry().right() < window.width()
    window.close()
