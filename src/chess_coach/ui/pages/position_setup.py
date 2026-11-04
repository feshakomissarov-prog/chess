"""Mouse-based placement of an ephemeral chess position using the shared board widget."""

from __future__ import annotations

import chess
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
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
        self.board_view.set_position(self._board)
        self.status.setText(f"Изменена клетка {chess.square_name(square)}. Позиция не сохранена.")

    def _reset(self) -> None:
        self._board = chess.Board()
        self.turn_selector.setCurrentIndex(0)
        self.board_view.set_position(self._board)
        self.status.setText("Начальная позиция восстановлена; запись в архив не выполнялась.")

    def _clear(self) -> None:
        self._board = chess.Board(None)
        self.board_view.set_position(self._board)
        self.status.setText("Доска очищена. Расставьте фигуры и выберите сторону хода.")

    def _open_position(self) -> None:
        self._board.turn = bool(self.turn_selector.currentData())
        self._board.castling_rights = chess.BB_EMPTY
        self._board.ep_square = None
        self._board.halfmove_clock = 0
        self._board.fullmove_number = 1
        self.position_requested.emit(self._board.fen())
