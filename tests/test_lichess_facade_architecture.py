from pathlib import Path

from PySide6.QtCore import QObject

from chess_coach.lichess.service import LichessService


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "chess_coach" / "lichess"


def test_lichess_service_keeps_qobject_signal_and_public_method_facade():
    assert issubclass(LichessService, QObject)
    expected_methods = (
        "connect_account", "connect_personal_token", "restore", "disconnect_account",
        "sync_archive", "sync_puzzle_activity_background", "refresh_lichess_analysis_background",
        "start_game", "send_move", "takeback", "close",
    )
    assert all(callable(getattr(LichessService, name, None)) for name in expected_methods)
    expected_signals = (
        "session_status_changed", "session_event_stream_ready", "session_game_event_received",
        "analysis_safety_games_received", "archive_progress", "puzzles_finished",
        "move_failed", "takeback_action_completed",
    )
    assert all(hasattr(LichessService, name) for name in expected_signals)


def test_lichess_service_mixins_have_no_qt_widget_dependencies():
    for name in ("service_auth.py", "service_sync.py", "service_games.py"):
        source = (PACKAGE / name).read_text(encoding="utf-8")
        assert "PySide6.QtWidgets" not in source
        assert "chess_coach.ui" not in source
