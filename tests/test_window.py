import chess
import pytest
from PySide6.QtCore import QPoint, QSettings, Qt

from chess_coach.board_view import BoardView
from chess_coach.window import MainWindow, SettingsDialog
from chess_coach.lichess.session import LichessGameSession
from chess_coach.lichess.repository import GameRepository


@pytest.fixture(autouse=True)
def avoid_background_auth_restore(monkeypatch):
    # Window tests exercise presentation and event handling, not stored credentials.
    monkeypatch.setattr("chess_coach.lichess.service.LichessService.restore", lambda self: None)


def test_main_window_smoke_and_game_controller_integration(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.show()
    assert isinstance(window.board, BoardView)
    assert "Не подключено" in window.connection.text()
    assert window.controller.ply == 0
    e2 = window.board._get_board_rect()
    side = e2.width() / 8
    center_e2 = QPoint(int(e2.x() + 4.5 * side), int(e2.y() + 6.5 * side))
    center_e4 = QPoint(int(e2.x() + 4.5 * side), int(e2.y() + 4.5 * side))
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=center_e2)
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=center_e4)
    assert window.controller.ply == 1
    assert window.controller.san_moves() == [(1, "e4", "")]
    assert window.board._board.fen() == window.controller.board.fen()
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    assert window.board._queued_premove == chess.Move.from_uci("g1f3")
    index = window.opponent_move.findData("e7e5")
    assert index >= 0
    window.opponent_move.setCurrentIndex(index)
    qtbot.mouseClick(window.apply_opponent_button, Qt.MouseButton.LeftButton)
    assert window.controller.san_moves() == [(1, "e4", "e5"), (2, "Nf3", "")]
    assert window.controller.premove is None
    window.navigate_to(1)
    assert window.controller.ply == 3
    assert window.controller.snapshot().board.piece_at(chess.E4) == chess.Piece(
        chess.PAWN, chess.WHITE
    )
    rect = window.board._get_board_rect()
    side = rect.width() / 8
    e7 = QPoint(int(rect.x() + 4.5 * side), int(rect.y() + 1.5 * side))
    e5 = QPoint(int(rect.x() + 4.5 * side), int(rect.y() + 3.5 * side))
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=e7)
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=e5)
    assert window.controller.ply == 3
    window.navigate_live()
    assert window.controller.viewing_live_position


def test_completed_pair_allows_selecting_white_halfmove(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    for uci in ("e2e4", "e7e5"):
        assert window.controller.apply_move_intent(chess.Move.from_uci(uci))
    live_fen = window.controller.board.fen()
    window.refresh()
    assert window.moves.count() == 2
    white_item = next(
        window.moves.item(index)
        for index in range(window.moves.count())
        if window.moves.item(index).data(Qt.ItemDataRole.UserRole) == 1
    )
    assert white_item.text() == "1. e4"
    qtbot.mouseClick(
        window.moves.viewport(),
        Qt.MouseButton.LeftButton,
        pos=window.moves.visualItemRect(white_item).center(),
    )
    assert window.controller.view_ply == 1
    assert window.controller.snapshot().board.fen().split()[1] == "b"
    assert window.controller.board.fen() == live_fen
    assert window.controller.ply == 2


def test_visual_preferences_persist_in_qsettings(tmp_path) -> None:
    path = str(tmp_path / "settings.ini")
    settings = QSettings(path, QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    dialog.color.setCurrentIndex(1)
    dialog.flipped.setChecked(True)
    dialog.legal.setChecked(False)
    dialog.last.setChecked(False)
    dialog.annotations.setChecked(True)
    dialog.save(settings)

    loaded = QSettings(path, QSettings.Format.IniFormat)
    assert loaded.value("player_color", type=int) == 0
    assert loaded.value("board_flipped", type=bool) is True
    assert loaded.value("show_legal_moves", type=bool) is False
    assert loaded.value("show_last_move", type=bool) is False
    assert loaded.value("annotations_enabled", type=bool) is True


def test_cancel_premove_button_clears_queued_intent(qtbot, tmp_path) -> None:
    import chess

    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    assert window.cancel_premove_button.isEnabled()
    qtbot.mouseClick(window.cancel_premove_button, Qt.MouseButton.LeftButton)
    assert window.controller.premove is None
    assert not window.cancel_premove_button.isEnabled()


def test_new_game_clears_premove_after_confirmation(qtbot, tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.controller.apply_move_intent(chess.Move.from_uci("e2e4"))
    assert window.controller.queue_premove(chess.Move.from_uci("g1f3"))
    window.refresh()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    window.new_game()
    assert window.controller.ply == 0
    assert window.controller.premove is None
    assert window.board._queued_premove is None


@pytest.mark.parametrize("gesture", ["click", "drag"])
def test_online_premove_is_available_on_opponent_turn_and_sent_once_after_state(
    qtbot, tmp_path, monkeypatch, gesture
) -> None:
    from PySide6.QtCore import QPoint

    window = MainWindow(QSettings(str(tmp_path / f"{gesture}.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda game_id, uci, request_id: calls.append(
        (game_id, uci, request_id)
    ))
    session = LichessGameSession(gesture, GameRepository(tmp_path / f"{gesture}.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    window.online_sessions[gesture] = session
    window.online_session = session
    window.online_view_ply = None
    window.show()
    window._refresh_online()
    assert window.board._interaction_enabled  # permits premove gestures during opponent turn
    assert not session.is_my_turn

    rect = window.board._get_board_rect()
    side = rect.width() / 8
    def center(square):
        row, col = window.board._renderer.get_visual_coordinates(square, window.board._flipped)
        return QPoint(int(rect.x() + (col + .5) * side), int(rect.y() + (row + .5) * side))
    start, end = center(chess.G1), center(chess.F3)
    window.board.show()
    if gesture == "click":
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=end)
    else:
        qtbot.mousePress(window.board, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(window.board, end, delay=20)
        qtbot.mouseRelease(window.board, Qt.MouseButton.LeftButton, pos=end)
    assert session.premove == chess.Move.from_uci("g1f3")
    assert session.moves == "e2e4"  # queue does not mutate confirmed history
    assert window.cancel_premove_button.isVisible()
    qtbot.mouseClick(window.cancel_premove_button, Qt.MouseButton.LeftButton)
    # Restore the intent after also verifying explicit UI cancellation.
    assert session.premove is None
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    window._refresh_online()
    window._lichess_game_event(gesture, {"type": "gameState", "moves": "e2e4 e7e5",
                                         "status": "started"})
    assert calls == [(gesture, "g1f3", session.pending_request_id)]
    window._lichess_game_event(gesture, {"type": "gameState", "moves": "e2e4 e7e5",
                                         "status": "started"})
    assert len(calls) == 1  # repeated state must not submit the same premove again


def test_online_premove_is_discarded_when_server_position_makes_it_illegal(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "illegal.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: calls.append(args))
    session = LichessGameSession("illegal", GameRepository(tmp_path / "illegal.sqlite3"))
    session.color = chess.WHITE
    fen = "4k3/8/8/2b5/8/8/8/4K1N1 b - - 0 1"
    session.apply_event({"type": "gameFull", "initialFen": fen,
                         "state": {"moves": "", "status": "started"}})
    window.online_sessions["illegal"] = session
    window.online_session = session
    window.show()
    window._refresh_online()
    assert session.queue_premove(chess.Move.from_uci("g1f3"))
    window._lichess_game_event("illegal", {"type": "gameState", "moves": "c5f2",
                                             "status": "started"})
    assert session.premove is None
    assert not calls


def test_online_pending_move_is_rendered_optimistically_without_persisting_until_confirmation(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(QSettings(str(tmp_path / "optimistic-ui.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.lichess, "send_move", lambda *args: calls.append(args))
    session = LichessGameSession("optimistic-ui", GameRepository(tmp_path / "optimistic-ui.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    window.online_sessions[session.game_id] = session
    window.online_session = session
    window.online_view_ply = None
    window.show()

    window.on_move_intent(chess.Move.from_uci("e2e4"))
    assert window.board._board.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert window.board._board.piece_at(chess.E2) is None
    assert session.board.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert session.moves == ""
    assert session.repository.get_game(session.game_id)["moves"] == ""
    assert window.moves.count() == 0  # the move is not yet confirmed SAN history
    assert "подтвержд" in window.game_status.text()

    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4",
                                                  "status": "started"})
    assert session.pending_uci is None
    assert window.board._board.fen() == session.board.fen()
    assert session.moves == "e2e4"
    assert session.repository.get_game(session.game_id)["moves"] == "e2e4"
    assert window.moves.count() == 1
    assert len(calls) == 1


def test_takeback_offer_ui_accept_and_server_rewind_refresh_history_and_pgn(qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "takeback-ui.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    requests = []
    monkeypatch.setattr(window.lichess, "takeback", lambda game_id, action, request_id:
                        requests.append((game_id, action, request_id)))
    session = LichessGameSession("takeback-ui", GameRepository(tmp_path / "takeback-ui.sqlite3"))
    session.color = chess.WHITE
    line = "e2e4 e7e5 g1f3 b8c6"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    window.online_sessions[session.game_id] = session
    window.online_session = session
    window.online_view_ply = None
    window.show()
    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": line,
                                                  "btakeback": True, "status": "started"})
    assert session.takeback_requested_by_opponent
    assert window.accept_takeback_button.isVisible()
    assert "Соперник предлагает вернуть ход" in window.game_status.text()

    confirmed_before = session.board.fen()
    qtbot.mouseClick(window.accept_takeback_button, Qt.MouseButton.LeftButton)
    assert requests[0][1] == "accept"
    assert session.board.fen() == confirmed_before
    assert session.moves == line
    assert session.takeback_accept_pending
    window._lichess_takeback_completed(session.game_id, requests[0][2], 0)
    window._lichess_game_event(session.game_id, {"type": "gameState", "moves": "e2e4 e7e5",
                                                  "status": "started"})
    assert session.moves == "e2e4 e7e5"
    assert session.board.ply() == 2
    assert window.moves.count() == 2
    assert window.board._board.fen() == session.board.fen()
    assert not window.accept_takeback_button.isVisible()
    assert session.repository.get_game(session.game_id)["moves"] == "e2e4 e7e5"
    pgn = session.repository.export_pgn(session.game_id)
    assert "Nf3" not in pgn and "Nc6" not in pgn
