"""Mouse-based placement of an ephemeral chess position using the shared board widget."""

from __future__ import annotations

import chess
from importlib.resources import files
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QResizeEvent
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QRadioButton, QToolButton,
                               QVBoxLayout, QWidget)

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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(14)
        board_panel = QWidget(self)
        board_layout = QVBoxLayout(board_panel)
        board_layout.setContentsMargins(10, 8, 10, 8)
        self.editor_title = QLabel("Редактор позиции")
        self.editor_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        board_layout.addWidget(self.editor_title)
        board_hint = QLabel("Нажмите на фигуру и установите её на доске.")
        board_hint.setObjectName("secondary")
        board_layout.addWidget(board_hint)
        self.board_view = _PositionEditorBoard(self._board)
        self.board_view.setFixedSize(320, 320)
        self.board_view.setAccessibleName("Доска расстановки временной позиции")
        self.board_view.piece_placement_requested.connect(self._place_selected_piece)
        board_layout.addWidget(self.board_view, 1, Qt.AlignmentFlag.AlignCenter)
        self.status = QLabel("Выберите фигуру и щёлкните по клетке. Позиция временная.")
        self.status.setObjectName("secondary")
        self.status.setWordWrap(True)
        board_layout.addWidget(self.status)
        layout.addWidget(board_panel, 1)

        controls_panel = QWidget(self)
        controls_layout = QVBoxLayout(controls_panel)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setSpacing(10)
        palette = QGroupBox("Палитра фигур")
        palette_layout = QVBoxLayout(palette)
        palette_hint = QLabel("Выберите фигуру или удаление")
        palette_hint.setObjectName("secondary")
        palette_layout.addWidget(palette_hint)
        self.piece_selector = QComboBox()
        self.piece_selector.setAccessibleName("Фигура для расстановки")
        for label, symbol in self._PIECES:
            self.piece_selector.addItem(label, symbol)
        self.piece_selector.hide()
        self.piece_button_group = QButtonGroup(self)
        self.piece_button_group.setExclusive(True)
        self.piece_buttons: list[QToolButton] = []
        piece_grid = QGridLayout()
        for index, (label, symbol) in enumerate(self._PIECES[:-1]):
            button = QToolButton()
            button.setObjectName("positionPiece")
            button.setCheckable(True)
            button.setAccessibleName(label)
            button.setToolTip(label)
            color_prefix = "w" if symbol.isupper() else "b"
            piece_icon = files("chess_widgets").joinpath(
                "assets", f"{color_prefix}{symbol.upper()}.svg")
            button.setIcon(QIcon(str(piece_icon)))
            button.setIconSize(QSize(28, 28))
            button.setMinimumSize(44, 42)
            self.piece_button_group.addButton(button, index)
            self.piece_buttons.append(button)
            piece_grid.addWidget(button, index // 6, index % 6)
            button.clicked.connect(lambda _checked=False, i=index: self.piece_selector.setCurrentIndex(i))
        palette_layout.addLayout(piece_grid)
        self.piece_buttons[0].setChecked(True)
        self.remove_piece_button = QPushButton("⌫  Убрать фигуру")
        self.remove_piece_button.clicked.connect(
            lambda: self.piece_selector.setCurrentIndex(len(self._PIECES) - 1))
        self.piece_selector.currentIndexChanged.connect(
            lambda index: self.piece_button_group.button(index).setChecked(True)
            if self.piece_button_group.button(index) is not None else None)
        palette_layout.addWidget(self.remove_piece_button)
        controls_layout.addWidget(palette)

        parameters = QGroupBox("Параметры позиции")
        parameters_layout = QVBoxLayout(parameters)
        turn_row = QHBoxLayout()
        turn_row.addWidget(QLabel("Чей ход"))
        self.turn_selector = QComboBox()
        self.turn_selector.setAccessibleName("Сторона, которой ходить")
        self.turn_selector.addItem("Ход белых", chess.WHITE)
        self.turn_selector.addItem("Ход чёрных", chess.BLACK)
        self.turn_selector.hide()
        self.turn_group = QButtonGroup(self)
        self.turn_group.setExclusive(True)
        self.turn_white = QRadioButton("Белые")
        self.turn_black = QRadioButton("Чёрные")
        self.turn_group.addButton(self.turn_white, 0)
        self.turn_group.addButton(self.turn_black, 1)
        self.turn_white.setChecked(True)
        self.turn_group.idClicked.connect(self.turn_selector.setCurrentIndex)
        turn_row.addWidget(self.turn_white)
        turn_row.addWidget(self.turn_black)
        parameters_layout.addLayout(turn_row)
        castling_title = QLabel("Рокировка")
        castling_title.setObjectName("secondary")
        parameters_layout.addWidget(castling_title)
        self.castling_controls: dict[str, QCheckBox] = {}
        castling_grid = QGridLayout()
        for label, right in (("Белые K", chess.BB_H1), ("Белые Q", chess.BB_A1),
                             ("Чёрные k", chess.BB_H8), ("Чёрные q", chess.BB_A8)):
            control = QCheckBox(label)
            control.setChecked(True)
            self.castling_controls[label] = control
            index = len(self.castling_controls) - 1
            castling_grid.addWidget(control, index // 2, index % 2)
        parameters_layout.addLayout(castling_grid)
        self.ep_selector = QComboBox()
        self.ep_selector.setAccessibleName("Поле взятия на проходе")
        self.ep_selector.addItem("Нет взятия на проходе", None)
        for rank in ("3", "6"):
            for file_name in "abcdefgh":
                square_name = file_name + rank
                self.ep_selector.addItem(square_name, chess.parse_square(square_name))
        ep_row = QHBoxLayout()
        ep_row.addWidget(QLabel("Взятие на проходе"))
        ep_row.addWidget(self.ep_selector, 1)
        parameters_layout.addLayout(ep_row)
        controls_layout.addWidget(parameters)

        actions = QGridLayout()
        self.reset_button = QPushButton("Начальная позиция")
        self.reset_button.clicked.connect(self._reset)
        self.clear_button = QPushButton("Очистить доску")
        self.clear_button.clicked.connect(self._clear)
        self.copy_fen_button = QPushButton("Скопировать FEN")
        self.copy_fen_button.clicked.connect(self._copy_fen)
        self.open_button = QPushButton("Открыть позицию в разборе")
        self.open_button.clicked.connect(self._open_position)
        actions.addWidget(self.reset_button, 0, 0)
        actions.addWidget(self.clear_button, 0, 1)
        actions.addWidget(self.copy_fen_button, 1, 0)
        actions.addWidget(self.open_button, 1, 1)
        controls_layout.addLayout(actions)
        controls_layout.addStretch(1)
        layout.addWidget(controls_panel, 1)
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

    def set_embedded(self) -> None:
        """Show the editor permanently when it is hosted inside another page."""
        try:
            self.toggled.disconnect(self._set_content_visible)
        except (RuntimeError, TypeError):
            pass
        self.setCheckable(False)
        self.setTitle("")
        self._set_content_visible(True)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not self.isCheckable():
            # The editor shares a tab with a fixed shell header. Give the
            # position controls room at 720p instead of letting the board
            # push the final action below the visible tab viewport.
            available = max(220, self.height() - 190)
            board_size = min(560, available)
            self.board_view.setFixedSize(board_size, board_size)

    def _copy_fen(self) -> None:
        from PySide6.QtWidgets import QApplication

        if not self._apply_position_parameters():
            self.status.setText(
                "Позиция некорректна. Проверьте королей, пешки, рокировку и поле взятия на проходе.")
            return
        QApplication.clipboard().setText(self._board.fen(en_passant="fen"))
        self.status.setText("FEN скопирована. Позиция остаётся временной.")

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

    def _apply_position_parameters(self) -> bool:
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
        return self._board.is_valid()

    def _open_position(self) -> None:
        if not self._apply_position_parameters():
            self.status.setText(
                "Позиция некорректна. Проверьте королей, пешки, рокировку и поле взятия на проходе.")
            return
        self.status.setText("Позиция проверена. Она временная и не сохраняется в архив.")
        self.position_requested.emit(self._board.fen(en_passant="fen"))
