from __future__ import annotations

import chess
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel

from chess_coach.application.routes import WorkspaceNavigator, WorkspaceRoute
from chess_coach.window import MainWindow


class _Client:
    rate_limit_remaining = 0


class _ReadOnlyService:
    username = "SyntheticAccount"
    auth_generation = 41
    client = _Client()

    def __init__(self):
        self.safety_requests: list[str] = []
        self.analytics_calls: list[dict] = []
        self.archive_calls = 0
        self.puzzle_calls = 0

    def is_auth_generation_current(self, generation):
        return generation == self.auth_generation

    def check_active_games_for_analysis(self, request_id):
        self.safety_requests.append(request_id)

    def refresh_lichess_analysis_background(self, **kwargs):
        self.analytics_calls.append(kwargs)

    def sync_archive(self, **kwargs):
        self.archive_calls += 1

    def sync_puzzle_activity_background(self, _repository):
        self.puzzle_calls += 1

    def close(self, timeout=1.0):
        return True


def _offline_window(qtbot, tmp_path, name="rf5"):
    window = MainWindow(QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat),
                        offline_mode=True)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=4000)
    return window


def test_root_home_never_keeps_back_history_across_twenty_transitions():
    navigator = WorkspaceNavigator()
    routes = [WorkspaceRoute.PLAY, WorkspaceRoute.GAMES, WorkspaceRoute.ANALYSIS,
              WorkspaceRoute.TRAINING, WorkspaceRoute.PROGRESS, WorkspaceRoute.SETTINGS]
    for index in range(40):
        navigator.navigate(routes[index % len(routes)], context=f"context-{index}")
        navigator.navigate(WorkspaceRoute.HOME)
        assert navigator.current.route is WorkspaceRoute.HOME
        assert not navigator.can_go_back
        assert navigator.back().route is WorkspaceRoute.HOME


def test_home_geometry_has_only_approved_visible_controls_at_common_sizes(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "home-geometry")
    for width, height in ((1280, 720), (1366, 768), (1600, 900), (1920, 1080)):
        window.resize(width, height)
        window.show()
        qtbot.wait(15)
        window.show_home()
        assert window.back_button.isHidden()
        assert window.dashboard_clock.isHidden()
        assert window.settings_button.isHidden()
        assert not window.home_games_button.isHidden()
        assert not window.home_all_progress_button.isHidden()
        assert window.home_recent_card.isVisible()
        header = window.header_widget
        visible = [child for child in (window.back_button, window.return_to_game_button,
                                       window.header_brand, window.connection,
                                       window.background_operations_button)
                   if child.isVisible()]
        for index, left in enumerate(visible):
            assert header.rect().contains(left.geometry()), (width, height, left.objectName())
            for right in visible[index + 1:]:
                assert not left.geometry().intersects(right.geometry()), (
                    width, height, left.objectName(), right.objectName())
        assert window.home_page.findChild(QLabel, "dashboardClock") is None
        assert not window.sidebar_settings.isHidden()
        assert window.settings_button.isHidden()
    window.close()


def test_library_inspector_scrolls_instead_of_clipping_filters_on_compact_window(
        qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "library-compact")
    window.resize(1280, 720)
    window.show()
    window.show_saved_games()
    qtbot.wait(40)
    page = window.library_page

    assert page.filter_panel.isHidden()
    page.filters_toggle.click()
    qtbot.waitUntil(page.filter_panel.isVisible)
    viewport = page.aside_scroll.viewport()
    bottom = page.filter_panel.mapTo(viewport, page.filter_panel.rect().bottomLeft()).y()
    scroll = page.aside_scroll.verticalScrollBar()
    assert bottom - (scroll.maximum() - scroll.value()) < viewport.height()
    assert page.search.height() >= 28
    assert page.account_filter.height() >= 28
    assert page.detail_open.height() > 0
    assert page.sync_button.geometry().intersects(page.search.geometry()) is False
    assert page.background_button.geometry().intersects(page.settings_button.geometry()) is False
    window.close()


def test_review_board_fits_visible_board_column_at_compact_window_size(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "review-board-compact")
    window.resize(1280, 720)
    window.show()
    window.game_repository.save_snapshot(
        "synthetic-review-compact", chess.STARTING_FEN, "e2e4 e7e5 g1f3 b8c6",
        "white", "Synthetic Opponent", False, "3+2", "mate", "white",
        white_player="Synthetic Player", black_player="Synthetic Opponent",
        started_at="2026-11-05T11:00:00Z")
    game = window.game_repository.get_game("synthetic-review-compact")
    assert window._open_saved_game(game)
    qtbot.wait(100)
    board = window.review_workspace.review_board
    column = window.review_workspace.board_column
    assert board.width() <= column.contentsRect().width()
    assert board.height() <= column.contentsRect().height()
    window.close()


def test_home_metric_details_wrap_at_compact_width(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "home-metric-wrap")
    window.resize(1280, 720)
    window.show()
    window.show_home()
    qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=4000)
    window._render_dashboard_snapshot({"metrics": [
        {"key": "games", "value": "12", "detail": "Все локальные партии · смешанная перспектива", "source": "SQLite"},
        {"key": "analysis", "value": "8/12", "detail": "Полный охват: 5 · частичный: 3 · Stockfish: 6 · Lichess с оценками: 2 · оценено моих ходов: 21/40", "source": "synthetic"},
        {"key": "accuracy", "value": "н/д", "detail": "Официальная точность: 0/12 партий с данными", "source": "Lichess"},
        {"key": "errors", "value": "н/д", "detail": "нет данных", "source": "synthetic"},
    ], "recent_games": []})
    qtbot.wait(20)
    detail = window._dashboard_metric_details["analysis"]
    assert detail.wordWrap()
    assert detail.text() == "С оценками: 8\nStockfish: 6 · Lichess с оценками: 2"
    assert "Stockfish: 6" in detail.toolTip()
    assert "Lichess с оценками: 2" in detail.toolTip()
    assert detail.geometry().bottom() < window.dashboard_metrics["games"].rect().height()
    window.close()


def test_authenticated_account_is_not_ready_until_event_stream_and_safety_snapshot(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "connection-state")
    service = _ReadOnlyService()
    window.lichess = service
    window.offline_mode = False
    window._lichess_authenticated(service.username)
    window._render_dashboard_snapshot({"metrics": [
        {"key": "games", "value": "1", "detail": "synthetic", "source": "SQLite"},
        {"key": "analysis", "value": "1/1", "detail": "Stockfish", "source": "Stockfish"},
        {"key": "accuracy", "value": "92%", "detail": "Lichess", "source": "Lichess"},
        {"key": "errors", "value": "1", "detail": "synthetic", "source": "Stockfish"},
    ], "recent_games": []})
    assert window._dashboard_metric_values["analysis"].text() == "скрыто"
    assert window._dashboard_metric_values["accuracy"].text() == "скрыто"
    assert not window._lichess_connected
    assert not window._online_actions_allowed()
    assert window.connection.text() == "Вход"

    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_event_stream_ready(service.auth_generation, service.username)
    assert window._lichess_connected
    assert window._online_actions_allowed()
    assert len(service.safety_requests) == 1
    request_id = service.safety_requests[0]
    window._request_dashboard_display_safety()  # coalesced with the pending request
    assert service.safety_requests == [request_id]
    assert not window._dashboard_analysis_safety_allowed
    window._dashboard_display_safety_reply(request_id, [])
    assert window._dashboard_analysis_safety_allowed
    assert window._dashboard_metric_values["analysis"].text() == "1/1"
    assert window._dashboard_metric_values["accuracy"].text() == "92%"

    window._run_background_sync()
    # RF6 restores historical incremental archive/puzzle sync independently of
    # the optional ready-analysis queue.
    assert service.archive_calls == 1
    assert service.puzzle_calls == 1
    assert service.analytics_calls == []
    window.close()


def test_dashboard_safety_denies_local_human_and_late_account_reply(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "safety-stale")
    first = _ReadOnlyService()
    window.lichess = first
    window.offline_mode = False
    window._lichess_authenticated(first.username)
    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_event_stream_ready(first.auth_generation, first.username)
    stale_id = first.safety_requests[0]

    second = _ReadOnlyService()
    second.username = "DifferentSyntheticAccount"
    second.auth_generation = 42
    window.lichess = second
    window._lichess_authenticated(second.username)
    assert window._dashboard_display_safety_request_id is None
    assert window._dashboard_display_safety_reply(stale_id, []) is False
    assert not window._dashboard_analysis_safety_allowed

    window._lichess_stream_ready_generation = second.auth_generation
    window._lichess_connected = True
    window.game_sessions.sessions["synthetic-human"] = type("HumanSession", (), {
        "status": "playing", "is_ai_opponent": False,
    })()
    window._request_dashboard_display_safety()
    assert not window._dashboard_analysis_safety_allowed
    assert second.safety_requests == []
    window.close()


def test_home_recent_game_click_routes_to_library_details_without_opening_review(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "home-card-route")
    game = {
        "game_id": "synthetic-home-route", "status": "mate", "winner": "white",
        "color": "white", "opponent": "Synthetic Opponent", "started_at": "2026-11-01",
        "clock": "300+0", "eco": "B20", "opening_name": "Sicilian Defense",
        "review_metrics": {"status": "без анализа", "coverage": "0/2",
                            "opponent_coverage": "0/2", "source": "нет данных"},
    }
    window._render_dashboard_snapshot({"metrics": [], "recent_games": [game]})
    card = window._recent_games_layout.itemAt(0).widget()
    card.activated.emit()
    assert window.screen_stack.currentWidget() is window.library_page
    assert window._screen_mode != "review"
    window.close()


def test_analysis_landing_describes_existing_opening_statistics_honestly(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "analysis-label")
    window.show_analysis()
    text = "\n".join(label.text() for label in window.analysis_page.findChildren(QLabel))
    assert "Исследование дебютов" in text
    assert "полноценного explorer ещё нет" in text
    assert "Дебюты и фазы" not in text
    window.close()


def test_header_and_sidebar_show_compact_connection_and_safety_states(qtbot, tmp_path):
    window = _offline_window(qtbot, tmp_path, "connection-indicators")
    window.resize(1280, 720)
    window.show()
    qtbot.wait(30)
    window.offline_mode = False
    cases = (
        ("Ограничение Lichess (429): повторите через 60 с", "429", "Пауза API · 60 с"),
        ("Проверка аккаунта отклонена Lichess (HTTP 403)", "403", "Недостаточно прав"),
        ("Требуется повторное подключение Lichess (HTTP 401)", "401", "Нужна авторизация"),
    )
    for status, header, sidebar in cases:
        window._lichess_status(status)
        assert header in window.connection.text()
        assert window.sidebar_connection_label.text() == sidebar
        assert QFontMetrics(window.connection.font()).horizontalAdvance(
            window.connection.text()) <= window.connection.contentsRect().width()

    service = _ReadOnlyService()
    window.lichess = service
    window._lichess_authenticated(service.username)
    assert window.connection.text() == "Вход"
    assert window.sidebar_connection_label.text() == "Подключаем поток…"
    assert window.sidebar_safety_label.text() == "Ожидание проверки активных партий"
    window.settings.setValue("lichess/auto_ready_analytics", False)
    window._lichess_event_stream_ready(service.auth_generation, service.username)
    assert window.connection.text() == "Готов"
    assert window.sidebar_connection_label.text() == "Поток готов"
    assert window.sidebar_safety_label.text() == "Проверяем активные партии Lichess…"
    window._dashboard_display_safety_reply(service.safety_requests[0], [])
    assert window.sidebar_safety_label.text() == "Проверка пройдена · аналитика доступна"
    window.close()
