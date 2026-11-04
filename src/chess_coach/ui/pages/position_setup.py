"""Mouse-based placement of an ephemeral chess position using the shared board widget."""

from __future__ import annotations

import chess
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout)

from ...board_view import BoardView


class _PositionEditorBoard(BoardView):
    piece_placement_requested = Signal(int)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            square = self._pos_to_square(event.position())
            if square is not None:
                self.piece_placement_requested.emit(square)
                event.accept()
                return
        super().mousePressEvent(event)


class PositionSetupWidget(QGroupBox):
    """Build a temporary FEN; no repository, network or engine is touched here."""

    position_requested = Signal(str)

    _PIECES = (
        ("Белый король", "K"), ("Белый ферзь", "Q"), ("Белая ладья", "R"),
        ("Белый слон", "B"), ("Белый конь", "N"), ("Белая пешка", "P"),
        ("Чёрный король", "k"), ("Чёрный ферзь", "q"), ("Чёрная ладья", "r"),
        ("Чёрный слон", "b"), ("Чёрный конь", "n"), ("Чёрная пешка", "p"),
        ("Убрать фигуру", ""),
    )

    def __init__(self, parent=None) -> None:
        super().__init__("Расставить позицию мышью", parent)
        self.setCheckable(True)
        self.setChecked(False)
        self._board = chess.Board()
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.piece_selector = QComboBox()
        self.piece_selector.setAccessibleName("Фигура для расстановки")
        for label, symbol in self._PIECES:
            self.piece_selector.addItem(label, symbol)
        controls.addWidget(QLabel("Фигура"))
        controls.addWidget(self.piece_selector, 1)
        self.turn_selector = QComboBox()
        self.turn_selector.setAccessibleName("Сторона, которой ходить")
        self.turn_selector.addItem("Ход белых", chess.WHITE)
        self.turn_selector.addItem("Ход чёрных", chess.BLACK)
        controls.addWidget(self.turn_selector)
        layout.addLayout(controls)

        fen_rights = QHBoxLayout()
        fen_rights.addWidget(QLabel("Рокировка"))
        self.castling_controls: dict[str, QCheckBox] = {}
        for label, right in (("Белые K", chess.BB_H1), ("Белые Q", chess.BB_A1),
                             ("Чёрные k", chess.BB_H8), ("Чёрные q", chess.BB_A8)):
            control = QCheckBox(label)
            control.setChecked(True)
            self.castling_controls[label] = control
            fen_rights.addWidget(control)
        self.ep_selector = QComboBox()
        self.ep_selector.setAccessibleName("Поле взятия на проходе")
        self.ep_selector.addItem("Нет взятия на проходе", None)
        for rank in ("3", "6"):
            for file_name in "abcdefgh":
                square_name = file_name + rank
                self.ep_selector.addItem(square_name, chess.parse_square(square_name))
        fen_rights.addWidget(self.ep_selector)
        layout.addLayout(fen_rights)

        self.board_view = _PositionEditorBoard(self._board)
        self.board_view.setFixedSize(320, 320)
        self.board_view.setAccessibleName("Доска расстановки временной позиции")
        self.board_view.piece_placement_requested.connect(self._place_selected_piece)
        layout.addWidget(self.board_view, 0, Qt.AlignmentFlag.AlignHCenter)

        actions = QGridLayout()
        self.reset_button = QPushButton("Начальная позиция")
        self.reset_button.clicked.connect(self._reset)
        self.clear_button = QPushButton("Очистить доску")
        self.clear_button.clicked.connect(self._clear)
        self.open_button = QPushButton("Открыть позицию в разборе")
        self.open_button.clicked.connect(self._open_position)
        actions.addWidget(self.reset_button, 0, 0)
        actions.addWidget(self.clear_button, 0, 1)
        actions.addWidget(self.open_button, 1, 0, 1, 2)
        layout.addLayout(actions)
        self.status = QLabel("Выберите фигуру и щёлкните по клетке. Позиция временная.")
        self.status.setObjectName("secondary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.toggled.connect(self._set_content_visible)
        self._set_content_visible(False)

    def _set_content_visible(self, expanded: bool) -> None:
        def set_layout_visible(layout) -> None:
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.widget() is not None:
                    item.widget().setVisible(bool(expanded))
                elif item.layout() is not None:
                    set_layout_visible(item.layout())

        set_layout_visible(self.layout())

    def _place_selected_piece(self, square: int) -> None:
        symbol = str(self.piece_selector.currentData() or "")
        piece = chess.Piece.from_symbol(symbol) if symbol else None
        self._board.set_piece_at(square, piece)
        self._clear_inconsistent_castling_rights()
        self.board_view.set_position(self._board)
        self.status.setText(f"Изменена клетка {chess.square_name(square)}. Позиция не сохранена.")

    def _clear_inconsistent_castling_rights(self) -> None:
        required = {
            "Белые K": ((chess.E1, chess.KING, chess.WHITE),
                        (chess.H1, chess.ROOK, chess.WHITE)),
            "Белые Q": ((chess.E1, chess.KING, chess.WHITE),
                        (chess.A1, chess.ROOK, chess.WHITE)),
            "Чёрные k": ((chess.E8, chess.KING, chess.BLACK),
                         (chess.H8, chess.ROOK, chess.BLACK)),
            "Чёрные q": ((chess.E8, chess.KING, chess.BLACK),
                         (chess.A8, chess.ROOK, chess.BLACK)),
        }
        for label, squares in required.items():
            if self.castling_controls[label].isChecked() and any(
                    self._board.piece_at(square) != chess.Piece(piece_type, color)
                    for square, piece_type, color in squares):
                self.castling_controls[label].setChecked(False)

    def _reset(self) -> None:
        self._board = chess.Board()
        self.turn_selector.setCurrentIndex(0)
        for control in self.castling_controls.values():
            control.setChecked(True)
        self.ep_selector.setCurrentIndex(0)
        self.board_view.set_position(self._board)
        self.status.setText("Начальная позиция восстановлена; запись в архив не выполнялась.")

    def _clear(self) -> None:
        self._board = chess.Board(None)
        for control in self.castling_controls.values():
            control.setChecked(False)
        self.ep_selector.setCurrentIndex(0)
        self.board_view.set_position(self._board)
        self.status.setText("Доска очищена. Расставьте фигуры и выберите сторону хода.")

    def _open_position(self) -> None:
        self._board.turn = bool(self.turn_selector.currentData())
        rights_by_label = {
            "Белые K": chess.BB_H1, "Белые Q": chess.BB_A1,
            "Чёрные k": chess.BB_H8, "Чёрные q": chess.BB_A8,
        }
        self._board.castling_rights = chess.BB_EMPTY
        for label, right in rights_by_label.items():
            if self.castling_controls[label].isChecked():
                self._board.castling_rights |= right
        self._board.ep_square = self.ep_selector.currentData()
        self._board.halfmove_clock = 0
        self._board.fullmove_number = 1
        if not self._board.is_valid():
            self.status.setText(
                "Позиция некорректна. Проверьте королей, пешки, рокировку и поле взятия на проходе.")
            return
        self.status.setText("Позиция проверена. Она временная и не сохраняется в архив.")
        self.position_requested.emit(self._board.fen(en_passant="fen"))
