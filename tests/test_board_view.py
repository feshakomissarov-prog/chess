import chess
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

from chess_coach.board_view import BoardView


def center(board_widget: BoardView, square: int) -> tuple[int, int]:
    rect = board_widget._get_board_rect()
    side = rect.width() / 8
    row, col = board_widget._renderer.get_visual_coordinates(square, board_widget._flipped)
    return int(rect.x() + (col + 0.5) * side), int(rect.y() + (row + 0.5) * side)


def test_click_click_emits_move_intent_without_mutating_widget_board(qtbot) -> None:
    board = chess.Board()
    widget = BoardView(board)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E2)))
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E4)))
    assert emitted == [chess.Move.from_uci("e2e4")]
    assert widget._board.fen() == board.fen()


def test_unchanged_position_refresh_preserves_click_selection(qtbot) -> None:
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E2)))
    assert widget._selected_square == chess.E2

    widget.set_position(widget._board.copy(stack=True))
    assert widget._selected_square == chess.E2
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E4)))
    assert emitted == [chess.Move.from_uci("e2e4")]


def test_identical_fen_with_different_move_history_still_replaces_widget_board(qtbot) -> None:
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    cycle = chess.Board()
    for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
        cycle.push_uci(uci)
    assert cycle.board_fen() == widget._board.board_fen()
    assert cycle.turn == widget._board.turn

    widget.set_position(cycle)

    assert len(widget._board.move_stack) == 4
    assert widget._board.move_stack == cycle.move_stack


def test_illegal_click_target_does_not_emit_move(qtbot) -> None:
    board = chess.Board()
    widget = BoardView(board)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E2)))
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E5)))
    assert emitted == []
    assert widget._board.fen() == board.fen()


def test_drag_and_drop_emits_intent(qtbot) -> None:
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    start, end = QPoint(*center(widget, chess.E2)), QPoint(*center(widget, chess.E4))
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, end, delay=20)
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
    assert emitted == [chess.Move.from_uci("e2e4")]


def test_flipped_board_keeps_square_coordinates_and_move_intents(qtbot) -> None:
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.set_flipped(True)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E2)))
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.E4)))
    assert emitted == [chess.Move.from_uci("e2e4")]


def test_annotation_right_click_and_drag_toggle_without_move(qtbot) -> None:
    board = chess.Board()
    widget = BoardView(board)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    a1 = QPoint(*center(widget, chess.A1))
    c3 = QPoint(*center(widget, chess.C3))
    qtbot.mouseClick(widget, Qt.MouseButton.RightButton, pos=a1)
    assert chess.A1 in widget._annotation_squares
    qtbot.mouseClick(widget, Qt.MouseButton.RightButton, pos=a1)
    assert chess.A1 not in widget._annotation_squares
    qtbot.mousePress(widget, Qt.MouseButton.RightButton, pos=a1)
    qtbot.mouseMove(widget, c3, delay=20)
    qtbot.mouseRelease(widget, Qt.MouseButton.RightButton, pos=c3)
    assert (chess.A1, chess.C3) in widget._annotation_arrows
    assert widget._board.fen() == board.fen()


def test_annotation_action_signal_records_add_remove_and_clear_context(qtbot) -> None:
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    widget.set_annotation_context("game-a")
    actions = []
    widget.annotation_action.connect(actions.append)
    a1 = QPoint(*center(widget, chess.A1))
    c3 = QPoint(*center(widget, chess.C3))

    qtbot.mouseClick(widget, Qt.MouseButton.RightButton, pos=a1)
    qtbot.mouseClick(widget, Qt.MouseButton.RightButton, pos=a1)
    widget._annotation_arrows.add((chess.A1, chess.C3))
    widget._record_annotation_action("add", "arrow", from_square="a1", to_square="c3")
    widget.clear_annotations()

    assert [action["action"] for action in actions] == ["add", "remove", "add", "clear"]
    assert all(action["game_id"] == "game-a" and action["ply"] == 0 for action in actions)
    assert all(action["fen"] == chess.Board().fen() for action in actions)
    assert actions[0]["kind"] == "square" and actions[0]["square"] == "a1"
    assert actions[2]["kind"] == "arrow" and actions[2]["to_square"] == "c3"


def test_promotion_dialog_offers_all_four_choices(qtbot, monkeypatch) -> None:
    widget = BoardView(chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1"))
    qtbot.addWidget(widget)
    widget._handling_input = True
    seen = {}

    def choose(color, square):
        seen["args"] = (color, square)
        return chess.KNIGHT

    monkeypatch.setattr(widget, "_choose_promotion", choose)
    move = widget._find_move(chess.A7, chess.A8)
    assert seen["args"] == (chess.WHITE, chess.A8)
    assert move == chess.Move.from_uci("a7a8n")


def test_cancelled_promotion_does_not_create_move_intent(qtbot, monkeypatch) -> None:
    widget = BoardView(chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1"))
    qtbot.addWidget(widget)
    widget._handling_input = True
    monkeypatch.setattr(widget, "_choose_promotion", lambda *args: None)
    assert widget._find_move(chess.A7, chess.A8) is None
    assert widget._board.piece_at(chess.A7) == chess.Piece(chess.PAWN, chess.WHITE)


def test_premove_promotion_offers_underpromotion_choices(qtbot, monkeypatch) -> None:
    position = chess.Board("4k3/8/8/8/8/8/p7/4K3 w - - 0 1")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.set_premove(None, chess.BLACK)
    emitted = []
    widget.premove_intent.connect(emitted.append)

    widget._emit_premove(chess.A2, chess.A1)

    assert emitted == [chess.Move.from_uci("a2a1q")]


def test_disabling_premoves_clears_rendered_premove_and_blocks_queue_gesture(qtbot) -> None:
    position = chess.Board()
    position.push_uci("e2e4")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.set_premove(chess.Move.from_uci("g1f3"), chess.WHITE)

    widget.set_premove_allowed(False)

    assert widget._queued_premove is None
    assert not widget._is_premove_turn()


def test_premove_click_click_and_drag_emit_only_intents(qtbot) -> None:
    from PySide6.QtCore import QPoint

    position = chess.Board()
    position.push_uci("e2e4")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    widget.set_premove(None, chess.WHITE)
    emitted = []
    widget.premove_intent.connect(emitted.append)

    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.G1)))
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.F3)))
    assert emitted == [chess.Move.from_uci("g1f3")]
    assert widget._board.fen() == position.fen()


def test_premove_drag_keeps_cursor_visible_and_renders_piece_and_target(qtbot) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt

    position = chess.Board()
    position.push_uci("e2e4")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    widget.set_premove(None, chess.WHITE)
    emitted = []
    widget.premove_intent.connect(emitted.append)
    start = QPoint(*center(widget, chess.G1))
    end = QPoint(*center(widget, chess.F3))

    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert not widget._premove_dragging  # press alone is still a click selection
    qtbot.mouseMove(widget, end, delay=20)
    assert widget._premove_dragging
    assert widget._is_dragging
    assert widget._premove_target == chess.F3
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
    assert emitted == [chess.Move.from_uci("g1f3")]
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert not widget._is_dragging
    assert widget._board.fen() == position.fen()


def test_premove_drag_outside_or_escape_cancels_without_changing_position(qtbot) -> None:
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QFocusEvent

    position = chess.Board()
    position.push_uci("e2e4")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.premove_intent.connect(emitted.append)
    start = QPoint(*center(widget, chess.G1))

    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, QPoint(-20, -20), delay=20)
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(-20, -20))
    assert emitted == []
    assert widget._board.fen() == position.fen()
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert not widget._is_dragging

    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, QPoint(*center(widget, chess.F3)), delay=20)
    qtbot.keyClick(widget, Qt.Key.Key_Escape)
    assert emitted == []
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert not widget._premove_drag_active
    assert not widget._is_dragging

    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, QPoint(*center(widget, chess.F3)), delay=20)
    widget.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(*center(widget, chess.F3)))
    assert emitted == []
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert not widget._premove_drag_active
    assert not widget._is_dragging


def test_server_capture_of_held_piece_cancels_drag_without_move(qtbot) -> None:
    from PySide6.QtCore import QPoint, Qt

    # Black can capture the held white knight on g1.
    position = chess.Board("4k3/8/8/8/8/8/7b/4K1N1 b - - 0 1")
    widget = BoardView(position)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    emitted = []
    widget.move_intent.connect(emitted.append)
    widget.premove_intent.connect(emitted.append)
    start = QPoint(*center(widget, chess.G1))
    end = QPoint(*center(widget, chess.F3))
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, end, delay=20)
    changed = position.copy(stack=True)
    changed.push_uci("h2g1")
    widget.set_position(changed)
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
    assert emitted == []
    assert not widget._is_dragging
    assert widget.cursor().shape() != Qt.CursorShape.BlankCursor
    assert widget._board.fen() == changed.fen()


def test_held_premove_drag_survives_opponent_move_and_becomes_legal_move(qtbot):
    from PySide6.QtCore import QPoint
    board = chess.Board()
    board.push_uci("e2e4")  # Black to move while a white knight is held.
    widget = BoardView(board)
    qtbot.addWidget(widget)
    widget.resize(600, 600)
    widget.show()
    widget.set_premove(None, chess.WHITE)
    start = QPoint(*center(widget, chess.G1))
    end = QPoint(*center(widget, chess.F3))
    sent = []
    widget.move_intent.connect(sent.append)
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(widget, end, delay=20)
    assert widget._premove_dragging
    updated = board.copy(stack=True)
    updated.push_uci("e7e5")
    widget.set_position(updated)
    assert widget._premove_dragging and widget._dragged_square == chess.G1
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)
    assert sent == [chess.Move.from_uci("g1f3")]


def test_wheel_navigates_one_ply_each_direction(qtbot):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    widget = BoardView(chess.Board())
    qtbot.addWidget(widget)
    emitted = []
    widget.wheel_navigate.connect(emitted.append)
    for step in (120, -120):
        wheel = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(0, 0),
                            QPoint(0, step), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate,
                            False)
        widget.wheelEvent(wheel)
        assert wheel.isAccepted()
    assert emitted == [-1, 1]
