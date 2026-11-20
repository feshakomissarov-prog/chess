"""PUZ-UX-04 Home proof against an isolated generated large archive."""

from time import monotonic

from PySide6.QtWidgets import QFrame

from chess_coach.bootstrap import create_runtime
from chess_coach.window import MainWindow


def test_large_archive_home_shows_cached_or_informative_status(qtbot):
    runtime = create_runtime(offline_mode=True)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    try:
        qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=10_000)
        repo = runtime.repository
        with repo._connection() as db:
            db.executemany(
                "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                "winner,updated_at,pgn,started_at,game_time_precision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(f"puz04-home-{index}", "startpos", "", "white", "Fixture", 0,
                  "5+0", "resign", "white", "2026-11-20T12:00:00Z", "",
                  "2026-11-20T12:00:00Z", "timestamp") for index in range(3300)])
        started = monotonic()
        window._refresh_home_dashboard()
        qtbot.waitUntil(lambda: window._dashboard_metric_values["games"].text() == "3300",
                        timeout=5000)
        first_paint_ms = (monotonic() - started) * 1000
        assert first_paint_ms < 1000
        assert window._dashboard_metric_details["analysis"].text() != "Загружаем…"
        qtbot.waitUntil(lambda: window._dashboard_thread is None, timeout=15_000)
        assert "н/д" in window._dashboard_metric_values["accuracy"].text()
        assert "3300" in window._dashboard_metric_values["games"].text()
    finally:
        window.close()
        assert runtime.close()


def test_home_recent_game_scroll_content_tracks_native_viewport(qtbot):
    runtime = create_runtime(offline_mode=True)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    try:
        window.resize(1360, 860)
        window.show()
        page = window._home_view
        card = QFrame(page._recent_rows)
        card.setObjectName("metricCard")
        page._recent_games_layout.insertWidget(0, card)
        page._sync_recent_rows_width()
        for size in ((1360, 860), (1280, 720), (1120, 640)):
            window.resize(*size)
            qtbot.waitUntil(
                lambda: (
                    page._recent_rows.width() == page.recent_scroll.viewport().width()
                    and card.maximumWidth() == page.recent_scroll.viewport().width()
                ),
                timeout=3000)
            assert page._recent_rows.width() <= page.recent_scroll.viewport().width()
            assert card.maximumWidth() == page.recent_scroll.viewport().width()
    finally:
        window.close()
        assert runtime.close()
