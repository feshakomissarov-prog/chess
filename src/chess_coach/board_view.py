"""Chess Coach adapter around the upstream chess-widgets BoardWidget."""

from __future__ import annotations

from math import atan2, cos, sin
from typing import Optional

import chess
from chess_widgets import BoardWidget
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtGui import QColor, QFocusEvent, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QInputDialog


class BoardView(BoardWidget):
    """Render upstream board and translate gestures into intents only.

    The widget receives a disposable snapshot. Interactive moves are emitted to
    GameController and never pushed into the widget's internal board.
    """

    move_intent = Signal(object)
    premove_intent = Signal(object)
    annotations_changed = Signal()
    cancel_premove_requested = Signal()

    def __init__(self, board: chess.Board, parent=None) -> None:
        super().__init__(parent, board.copy(stack=True))
        self._show_legal_moves = True
        self._show_last_move = True
        self._annotations_enabled = True
        self._interaction_enabled = True
        self._premove_allowed = True
        self._annotation_arrows: set[tuple[int, int]] = set()
        self._annotation_squares: set[int] = set()
        self._right_start: Optional[int] = None
        self._right_end: Optional[int] = None
        self._right_dragged = False
        self._handling_input = False
        self._premove_color = chess.WHITE
        self._queued_premove: Optional[chess.Move] = None
        self._premove_selected: Optional[int] = None
        self._premove_drag_start: Optional[int] = None
        self._premove_drag_active = False
        self._premove_dragging = False
        self._premove_press_pos: Optional[QPointF] = None
        self._premove_target: Optional[int] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_position(self, board: chess.Board) -> None:
        """Replace render snapshot after the controller accepts an event."""
        # chess-widgets resets selection and renderer state on set_board(). Keep
        # the existing widget board for clock/status-only refreshes.
        unchanged = (board.fen() == self._board.fen()
                     and board.move_stack == self._board.move_stack
                     and board.chess960 == self._board.chess960)
        if unchanged:
            return
        self._cancel_premove_gesture(clear_selection=True)
        self._queued_premove = None
        super().set_board(board.copy(stack=True))

    def set_premove(self, move: Optional[chess.Move], color: chess.Color) -> None:
        if self._queued_premove == move and self._premove_color == color:
            return
        self._queued_premove = move
        self._premove_color = color
        self.update()

    def set_interaction_enabled(self, enabled: bool) -> None:
        self._interaction_enabled = enabled
        if not enabled:
            self._clear_selection()
            self._cancel_premove_gesture(clear_selection=True)

    def set_show_legal_moves(self, enabled: bool) -> None:
        if self._show_legal_moves == enabled:
            return
        self._show_legal_moves = enabled
        self.update()

    def set_show_last_move(self, enabled: bool) -> None:
        if self._show_last_move == enabled:
            return
        self._show_last_move = enabled
        self.update()

    def set_annotations_enabled(self, enabled: bool) -> None:
        if self._annotations_enabled == enabled:
            return
        self._annotations_enabled = enabled
        if not enabled:
            self.clear_annotations()
        self.update()

    def set_premove_allowed(self, enabled: bool) -> None:
        if self._premove_allowed == enabled:
            return
        self._premove_allowed = enabled
        if not enabled:
            self._cancel_premove_gesture(clear_selection=True)
            self._queued_premove = None
        self.update()

    def clear_annotations(self) -> None:
        self._annotation_arrows.clear()
        self._annotation_squares.clear()
        self.annotations_changed.emit()
        self.update()

    def _make_interactive_move(self, move: chess.Move) -> None:
        self.move_intent.emit(move)

    def _find_move(self, from_sq: int, to_sq: int) -> Optional[chess.Move]:
        candidates = [
            move
            for move in self._board.legal_moves
            if move.from_square == from_sq and move.to_square == to_sq
        ]
        if not candidates:
            return None
        promotions = [move for move in candidates if move.promotion]
        if promotions and self._handling_input:
            labels = [
                (
                    {
                        chess.QUEEN: "Ферзь",
                        chess.ROOK: "Ладья",
                        chess.BISHOP: "Слон",
                        chess.KNIGHT: "Конь",
                    }[move.promotion],
                    move,
                )
                for move in promotions
            ]
            choice, accepted = QInputDialog.getItem(
                self,
                "Превращение пешки",
                "Выберите фигуру:",
                [label for label, _ in labels],
                0,
                False,
            )
            if not accepted:
                return None
            return next(move for label, move in labels if label == choice)
        return candidates[0]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        old_highlight = self._renderer.highlight_last_move
        old_hints = self._legal_moves
        if not self._show_last_move:
            self._renderer.highlight_last_move = lambda *args: None
        if not self._show_legal_moves:
            self._legal_moves = []
        try:
            super().paintEvent(event)
        finally:
            self._renderer.highlight_last_move = old_highlight
            self._legal_moves = old_hints
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._annotations_enabled:
            for start, end in self._annotation_arrows:
                self._draw_arrow(painter, start, end, QColor(39, 119, 87, 205), 0.88)
            for square in self._annotation_squares:
                self._draw_square_mark(painter, square, QColor(39, 119, 87, 165))
        if self._queued_premove is not None:
            self._draw_arrow(
                painter,
                self._queued_premove.from_square,
                self._queued_premove.to_square,
                QColor(80, 110, 190, 185),
                0.76,
            )
        if self._premove_selected is not None:
            self._renderer.highlight_square(
                painter,
                self._get_board_rect(),
                self._premove_selected,
                QColor(75, 95, 165, 100),
                self._flipped,
            )
        if self._premove_dragging and self._premove_target is not None:
            self._renderer.highlight_square(
                painter, self._get_board_rect(), self._premove_target,
                QColor(60, 140, 220, 115), self._flipped,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.RightButton:
            if not self._annotations_enabled:
                return
            self._right_start = self._pos_to_square(event.position())
            self._right_end = self._right_start
            self._right_dragged = False
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._interaction_enabled:
            return
        if self._is_premove_turn():
            self._start_premove_gesture(event.position())
            return
        self._handling_input = True
        try:
            super().mousePressEvent(event)
        finally:
            self._handling_input = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._right_start is not None:
            self._right_end = self._pos_to_square(event.position())
            self._right_dragged = self._right_end != self._right_start
            self.update()
            return
        if self._premove_drag_active and self._premove_press_pos is not None:
            dx = event.position().x() - self._premove_press_pos.x()
            dy = event.position().y() - self._premove_press_pos.y()
            if (not self._premove_dragging
                    and dx * dx + dy * dy >= QGuiApplication.styleHints().startDragDistance() ** 2):
                self._premove_dragging = True
                self._is_dragging = True
                self._dragged_square = self._premove_drag_start
            if self._premove_dragging:
                self._drag_pos = event.position()
                self._premove_target = self._pos_to_square(event.position())
                self.update()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.RightButton and self._right_start is not None:
            end = self._pos_to_square(event.position())
            start = self._right_start
            if start is not None and not (self._right_dragged and end is None):
                if end is not None and end != start:
                    arrow = (start, end)
                    if arrow in self._annotation_arrows:
                        self._annotation_arrows.remove(arrow)
                    else:
                        self._annotation_arrows.add(arrow)
                else:
                    if start in self._annotation_squares:
                        self._annotation_squares.remove(start)
                    else:
                        self._annotation_squares.add(start)
                self.annotations_changed.emit()
            self._right_start = None
            self._right_end = None
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._interaction_enabled:
            return
        if self._premove_drag_active:
            start = self._premove_drag_start
            end = self._pos_to_square(event.position())
            dragged = self._premove_dragging
            self._cancel_premove_gesture(clear_selection=dragged)
            if start is not None and end is not None and end != start:
                self._emit_premove(start, end)
            elif dragged:
                self.update()
            elif end is not None:
                self._finish_premove_click(event.position())
            else:
                self._premove_selected = None
                self.update()
            return
        if self._is_premove_turn():
            self._finish_premove_click(event.position())
            return
        self._handling_input = True
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._handling_input = False

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self._clear_selection()
            self._queued_premove = None
            self._cancel_premove_gesture(clear_selection=True)
            self.cancel_premove_requested.emit()
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        self._clear_selection()
        self._cancel_premove_gesture(clear_selection=True)
        super().focusOutEvent(event)

    def _is_premove_turn(self) -> bool:
        return (self._premove_allowed and self._board.turn != self._premove_color
                and not self._board.is_game_over())

    def _start_premove_gesture(self, pos: QPointF) -> None:
        square = self._pos_to_square(pos)
        piece = self._board.piece_at(square) if square is not None else None
        selected = self._premove_selected
        if (piece is not None and piece.color == self._premove_color
                and square is not None and square != selected):
            self._premove_selected = square
            self._premove_drag_start = square
        elif selected is not None and square is not None and square != selected:
            self._premove_drag_start = selected
        elif piece is not None and piece.color == self._premove_color:
            self._premove_selected = square
            self._premove_drag_start = square
        else:
            self._premove_drag_start = selected
        if self._premove_drag_start is not None:
            self._premove_press_pos = pos
            self._premove_target = square
            self._premove_drag_active = True
        self.update()

    def _finish_premove_click(self, pos: QPointF) -> None:
        square = self._pos_to_square(pos)
        start = self._premove_selected
        if start is not None and square is not None and square != start:
            self._emit_premove(start, square)
        elif square is None:
            self._premove_selected = None
            self.update()

    def _emit_premove(self, start: int, end: int) -> None:
        self._premove_drag_active = False
        self._premove_dragging = False
        self._premove_drag_start = None
        self._premove_selected = None
        self._is_dragging = False
        self._dragged_square = None
        self._premove_press_pos = None
        self._premove_target = None
        piece = self._board.piece_at(start)
        promotion = None
        if piece and piece.piece_type == chess.PAWN:
            target_rank = chess.square_rank(end)
            if target_rank in (0, 7):
                labels = ["Ферзь", "Ладья", "Слон", "Конь"]
                choices = [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]
                selected, accepted = QInputDialog.getItem(
                    self, "Превращение предхода", "Выберите фигуру:", labels, 0, False
                )
                if not accepted:
                    self.update()
                    return
                promotion = choices[labels.index(selected)]
        self.premove_intent.emit(chess.Move(start, end, promotion=promotion))
        self.update()

    def _cancel_premove_gesture(self, clear_selection: bool) -> None:
        self._premove_drag_active = False
        self._premove_dragging = False
        self._premove_drag_start = None
        self._premove_press_pos = None
        self._premove_target = None
        self._is_dragging = False
        self._dragged_square = None
        self.unsetCursor()
        if clear_selection:
            self._premove_selected = None
        self.update()

    def _square_center(self, square: int) -> QPointF:
        rect = self._get_board_rect()
        side = rect.width() / 8
        row, col = self._renderer.get_visual_coordinates(square, self._flipped)
        return QPointF(rect.x() + (col + 0.5) * side, rect.y() + (row + 0.5) * side)

    def _draw_arrow(
        self, painter: QPainter, start: int, end: int, color: QColor, thickness: float
    ) -> None:
        if start == end:
            return
        a, b = self._square_center(start), self._square_center(end)
        side = self._get_board_rect().width() / 8
        dx, dy = b.x() - a.x(), b.y() - a.y()
        angle = atan2(dy, dx)
        tip = QPointF(b.x() - cos(angle) * side * 0.19, b.y() - sin(angle) * side * 0.19)
        base = QPointF(tip.x() - cos(angle) * side * 0.32, tip.y() - sin(angle) * side * 0.32)
        painter.save()
        painter.setPen(QPen(color, max(4.0, side * 0.12 * thickness), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(a, base)
        wing = side * 0.18
        left = QPointF(tip.x() - cos(angle - 0.55) * wing, tip.y() - sin(angle - 0.55) * wing)
        right = QPointF(tip.x() - cos(angle + 0.55) * wing, tip.y() - sin(angle + 0.55) * wing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, left, right]))
        painter.restore()

    def _draw_square_mark(self, painter: QPainter, square: int, color: QColor) -> None:
        rect = self._get_board_rect()
        side = rect.width() / 8
        row, col = self._renderer.get_visual_coordinates(square, self._flipped)
        painter.save()
        painter.setPen(QPen(color, max(3, side * 0.08)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            self._square_center(square), side * 0.34, side * 0.34
        )
        painter.restore()
