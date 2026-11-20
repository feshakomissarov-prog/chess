"""PUZ-UX-04 Home proof against an isolated generated large archive."""

from time import monotonic

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
