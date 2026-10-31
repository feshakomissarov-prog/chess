"""Main M0 window and settings dialog."""

from __future__ import annotations

import chess
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .board_view import BoardView
from .controller import GameController


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.color = QComboBox()
        self.color.addItem("Белыми", chess.WHITE)
        self.color.addItem("Чёрными", chess.BLACK)
        color = settings.value("player_color", chess.WHITE, type=int)
        self.color.setCurrentIndex(self.color.findData(color))
        layout.addWidget(QLabel("Моя сторона для теста предходов"))
        layout.addWidget(self.color)
        self.flipped = QCheckBox("Перевернуть доску")
        self.flipped.setChecked(settings.value("board_flipped", False, type=bool))
        layout.addWidget(self.flipped)
        self.legal = QCheckBox("Показывать доступные ходы")
        self.legal.setChecked(settings.value("show_legal_moves", True, type=bool))
        layout.addWidget(self.legal)
        self.last = QCheckBox("Подсвечивать последний ход")
        self.last.setChecked(settings.value("show_last_move", True, type=bool))
        layout.addWidget(self.last)
        self.annotations = QCheckBox("Включить ручные стрелки и выделения")
        self.annotations.setChecked(settings.value("annotations_enabled", True, type=bool))
        layout.addWidget(self.annotations)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self, settings: QSettings) -> None:
        settings.setValue("player_color", self.color.currentData())
        settings.setValue("board_flipped", self.flipped.isChecked())
        settings.setValue("show_legal_moves", self.legal.isChecked())
        settings.setValue("show_last_move", self.last.isChecked())
        settings.setValue("annotations_enabled", self.annotations.isChecked())
        settings.sync()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = (
            settings
            if settings is not None
            else QSettings("ChessCoach", "ChessCoach")
        )
        color = self.settings.value("player_color", chess.WHITE, type=int)
        self.controller = GameController(chess.Color(color))
        self.setWindowTitle("Chess Coach")
        self.setMinimumSize(1040, 680)
        self.resize(1360, 860)
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #f4f1ea; color: #292a2b; }"
            "QPushButton { background: #ffffff; border: 1px solid #d9d5cb;"
            " border-radius: 6px; padding: 7px 11px; }"
            "QPushButton:hover { background: #ebe6db; }"
            "QPushButton:disabled { color: #9b9a96; }"
            "QFrame#card { background: #ffffff; border: 1px solid #e5e1d8;"
            " border-radius: 8px; }"
            "QListWidget { background: #ffffff; border: 1px solid #e5e1d8;"
            " border-radius: 6px; padding: 5px; }"
        )
        self._build_ui()
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.cancel_premove)
        self.refresh()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        brand = QLabel("♟  Chess Coach")
        brand.setStyleSheet("font-size: 22px; font-weight: 700;")
        header.addWidget(brand)
        header.addSpacing(24)
        for label in ("Играть", "Мои партии", "Анализ", "Тренировки"):
            button = QPushButton(label)
            button.setEnabled(label == "Играть")
            if label != "Играть":
                button.setToolTip("Будет добавлено позже")
            header.addWidget(button)
        header.addStretch(1)
        self.connection = QLabel("●  Не подключено (будет добавлено позже)")
        self.connection.setStyleSheet("color: #77766f;")
        header.addWidget(self.connection)
        self.settings_button = QPushButton("Настройки")
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        board_column = QWidget()
        board_layout = QVBoxLayout(board_column)
        board_layout.setContentsMargins(0, 0, 8, 0)
        board_layout.setSpacing(8)
        opponent = QLabel("Соперник  ·  локальная партия")
        opponent.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(opponent)
        self.board = BoardView(self.controller.board)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self.board.move_intent.connect(self.on_move_intent)
        self.board.premove_intent.connect(self.on_premove_intent)
        self.board.cancel_premove_requested.connect(self.cancel_premove)
        board_layout.addWidget(self.board, 1, Qt.AlignmentFlag.AlignCenter)
        you = QLabel("Вы  ·  офлайн")
        you.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(you)
        splitter.addWidget(board_column)

        side = QWidget()
        side.setMinimumWidth(340)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 0, 0, 0)
        side_layout.setSpacing(10)
        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        self.turn_label = QLabel()
        self.turn_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        status_layout.addWidget(self.turn_label)
        self.game_status = QLabel("Локальная тренировочная доска")
        self.game_status.setStyleSheet("color: #77766f;")
        status_layout.addWidget(self.game_status)
        side_layout.addWidget(status_card)

        moves_title = QLabel("ХОДЫ")
        moves_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #77766f;")
        side_layout.addWidget(moves_title)
        self.moves = QListWidget()
        self.moves.itemClicked.connect(self.on_move_selected)
        side_layout.addWidget(self.moves, 1)
        history_controls = QHBoxLayout()
        for label, action in (
            ("|<", lambda: self.navigate_to(0)),
            ("<", lambda: self.navigate_by(-1)),
            (">", lambda: self.navigate_by(1)),
            (">|", self.navigate_live),
        ):
            button = QPushButton(label)
            button.setToolTip(
                {"|<": "К началу", "<": "На ход назад", ">": "На ход вперёд", ">|": "К текущей позиции"}[label]
            )
            button.clicked.connect(action)
            history_controls.addWidget(button)
        side_layout.addLayout(history_controls)
        self.history_label = QLabel("Актуальная позиция")
        self.history_label.setStyleSheet("color: #77766f;")
        side_layout.addWidget(self.history_label)

        premove_title = QLabel("ПРОВЕРКА ПРЕДХОДОВ")
        premove_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #77766f;")
        side_layout.addWidget(premove_title)
        self.premove_label = QLabel("Один предход можно поставить, пока ходит соперник.")
        self.premove_label.setWordWrap(True)
        side_layout.addWidget(self.premove_label)
        self.cancel_premove_button = QPushButton("Отменить предход")
        self.cancel_premove_button.clicked.connect(self.cancel_premove)
        side_layout.addWidget(self.cancel_premove_button)
        self.opponent_move = QComboBox()
        side_layout.addWidget(self.opponent_move)
        self.apply_opponent_button = QPushButton("Сыграть тестовый ход соперника")
        self.apply_opponent_button.clicked.connect(self.apply_opponent_move)
        side_layout.addWidget(self.apply_opponent_button)

        actions = QHBoxLayout()
        self.new_game_button = QPushButton("Новая партия")
        self.new_game_button.clicked.connect(self.new_game)
        self.flip_button = QPushButton("Перевернуть")
        self.flip_button.clicked.connect(self.flip_board)
        self.clear_marks_button = QPushButton("Очистить пометки")
        self.clear_marks_button.clicked.connect(self.board.clear_annotations)
        actions.addWidget(self.new_game_button)
        actions.addWidget(self.flip_button)
        side_layout.addLayout(actions)
        side_layout.addWidget(self.clear_marks_button)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 13)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([850, 450])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def refresh(self, message: str | None = None) -> None:
        snapshot = self.controller.snapshot()
        self.board.set_position(snapshot.board)
        self.board.set_interaction_enabled(snapshot.live)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self._apply_preferences()
        self.moves.clear()
        for number, white, black in self.controller.san_moves():
            first = QListWidgetItem(f"{number}.  {white}   {black}")
            first.setData(Qt.ItemDataRole.UserRole, min(number * 2, self.controller.ply))
            self.moves.addItem(first)
        if self.controller.view_ply == 0:
            self.history_label.setText("Начальная позиция")
        elif not snapshot.live:
            self.history_label.setText(f"Просмотр хода {snapshot.ply} из {self.controller.ply}")
        else:
            self.history_label.setText("Актуальная позиция")
        side_name = "белыми" if self.controller.player_color == chess.WHITE else "чёрными"
        self.turn_label.setText(
            ("Ход белых" if snapshot.board.turn else "Ход чёрных")
            + ("  ·  просмотр истории" if not snapshot.live else "")
        )
        opponent_turn = self.controller.board.turn != self.controller.player_color
        legal = (
            list(self.controller.board.legal_moves)
            if opponent_turn and self.controller.viewing_live_position
            else []
        )
        self.opponent_move.clear()
        for move in legal:
            san = self.controller.board.san(move)
            self.opponent_move.addItem(f"{san}  ({move.uci()})", move.uci())
        enabled = opponent_turn and self.controller.viewing_live_position and bool(legal)
        self.opponent_move.setEnabled(enabled)
        self.apply_opponent_button.setEnabled(enabled)
        if self.controller.premove is not None:
            self.premove_label.setText(
                f"Предход: {self.controller.premove.uci()}  ·  Esc отменяет"
            )
        elif opponent_turn:
            self.premove_label.setText(
                f"Вы играете {side_name}. Выберите свою фигуру, затем клетку назначения."
            )
        else:
            self.premove_label.setText("Предход появится, когда будет ход соперника.")
        self.cancel_premove_button.setEnabled(self.controller.premove is not None)
        self.clear_marks_button.setEnabled(self.board._annotations_enabled)
        if message:
            self.game_status.setText(message)
        else:
            self.game_status.setText("Локальная тренировочная доска")

    def _apply_preferences(self) -> None:
        self.board.set_flipped(self.settings.value("board_flipped", False, type=bool))
        self.board.set_show_legal_moves(self.settings.value("show_legal_moves", True, type=bool))
        self.board.set_show_last_move(self.settings.value("show_last_move", True, type=bool))
        self.board.set_annotations_enabled(
            self.settings.value("annotations_enabled", True, type=bool)
        )

    def on_move_intent(self, move: chess.Move) -> None:
        if self.controller.apply_move_intent(move):
            self.refresh("Ход записан в локальную партию.")
        else:
            self.refresh("Ход не выполнен: позиция не изменилась.")

    def on_premove_intent(self, move: chess.Move) -> None:
        if self.controller.queue_premove(move):
            self.refresh("Предход поставлен. Он будет проверен после хода соперника.")
        else:
            self.refresh("Предход не принят в текущем состоянии.")

    def cancel_premove(self) -> None:
        self.controller.cancel_premove()
        self.refresh("Предход отменён.")

    def apply_opponent_move(self) -> None:
        raw = self.opponent_move.currentData()
        if not raw:
            return
        move = chess.Move.from_uci(raw)
        _, message = self.controller.apply_test_opponent_move(move)
        self.refresh(message)

    def on_move_selected(self, item: QListWidgetItem) -> None:
        self.controller.go_to(int(item.data(Qt.ItemDataRole.UserRole)))
        self.refresh()

    def navigate_to(self, ply: int) -> None:
        self.controller.go_to(ply)
        self.refresh()

    def navigate_by(self, delta: int) -> None:
        self.controller.step_view(delta)
        self.refresh()

    def navigate_live(self) -> None:
        self.controller.go_live()
        self.refresh()

    def flip_board(self) -> None:
        flipped = not self.settings.value("board_flipped", False, type=bool)
        self.settings.setValue("board_flipped", flipped)
        self.settings.sync()
        self.board.set_flipped(flipped)

    def new_game(self) -> None:
        if self.controller.ply:
            answer = QMessageBox.question(
                self,
                "Новая партия",
                "Начать новую локальную партию? Текущая партия не будет сохранена.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.controller.new_game()
        self.board.clear_annotations()
        self.refresh("Новая локальная партия.")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.save(self.settings)
            new_color = chess.Color(int(self.settings.value("player_color", chess.WHITE)))
            self.controller.player_color = new_color
            self.refresh("Настройки сохранены.")
