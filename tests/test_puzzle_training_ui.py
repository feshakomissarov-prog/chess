import chess
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage


def test_right_annotations_do_not_move_and_lmb_click_click_solves(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    qtbot.waitUntil(lambda: page.board.width() > 160 and page.board.height() > 160)
    board = page.board

    def center(square):
        return board._square_center(square).toPoint()

    start_fen = board._board.fen()
    from_pos, to_pos = center(chess.E4), center(chess.F5)
    qtbot.mousePress(board, Qt.MouseButton.RightButton, pos=from_pos)
    qtbot.mouseMove(board, to_pos, delay=50)
    qtbot.mouseRelease(board, Qt.MouseButton.RightButton, pos=to_pos)
    qtbot.mouseClick(board, Qt.MouseButton.RightButton, pos=center(chess.C3))
    assert board._annotation_arrows == {(chess.E4, chess.F5)}
    assert board._annotation_squares == {chess.C3}
    assert board._board.fen() == start_fen

    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=center(chess.D7))
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=center(chess.D5))
    assert page.state.status.value == "success"
    assert not board._annotation_arrows and not board._annotation_squares


def test_reveal_requires_confirmation_and_exposes_line_only_after_yes(qtbot, monkeypatch):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.show()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.No)
    page._reveal()
    assert page.solution_line.isHidden()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    page._reveal()
    assert not page.solution_line.isHidden()
    assert "Правильная линия" in page.solution_line.text()
    assert page.state.status.value == "revealed"
