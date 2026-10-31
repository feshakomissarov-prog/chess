import chess
from PySide6.QtCore import QPoint, QSettings, Qt

from chess_coach.board_view import BoardView
from chess_coach.window import MainWindow, SettingsDialog


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
