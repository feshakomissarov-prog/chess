"""Main M0 window and settings dialog."""

from __future__ import annotations

import chess
import time
from pathlib import Path
from PySide6.QtCore import QSettings, Qt, QStandardPaths, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
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
    QInputDialog,
    QFileDialog,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .board_view import BoardView
from .controller import GameController
from .lichess.auth import CredentialStorageError, WindowsCredentialStore
from .lichess.repository import GameRepository
from .lichess.service import LichessService
from .lichess.session import LichessGameSession


class OnlineGameDialog(QDialog):
    def __init__(self, title: str, include_level: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QVBoxLayout(self)
        self.color = QComboBox()
        self.color.addItem("Случайный", "random")
        self.color.addItem("Белыми", "white")
        self.color.addItem("Чёрными", "black")
        form.addWidget(QLabel("Ваш цвет"))
        form.addWidget(self.color)
        self.clock = QComboBox()
        self.clock.addItem("Без часов", (None, 0))
        self.clock.addItem("3 + 2", (180, 2))
        self.clock.addItem("10 + 0", (600, 0))
        self.clock.addItem("15 + 10", (900, 10))
        form.addWidget(QLabel("Контроль времени"))
        form.addWidget(self.clock)
        self.rated = QCheckBox("Рейтинговая партия (изменит рейтинг)")
        if not include_level:
            form.addWidget(self.rated)
        self.level = QComboBox()
        for level in range(1, 9):
            self.level.addItem(f"Lichess AI · уровень {level}", level)
        if include_level:
            form.addWidget(self.level)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addWidget(buttons)

    def selection(self):
        return self.color.currentData(), *self.clock.currentData(), self.level.currentData(), self.rated.isChecked()


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
        data_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        data_root.mkdir(parents=True, exist_ok=True)
        self.game_repository = GameRepository(data_root / "games.sqlite3")
        try:
            self.lichess = LichessService(token_store=WindowsCredentialStore(), parent=self)
        except CredentialStorageError:
            self.lichess = None
        self.online_sessions: dict[str, LichessGameSession] = {}
        self.online_session: LichessGameSession | None = None
        self.online_view_ply: int | None = None
        self._lichess_connected = False
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
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(250)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.activated.connect(self.cancel_premove)
        if self.lichess:
            self.lichess.status_changed.connect(self._lichess_status)
            self.lichess.authenticated.connect(self._lichess_authenticated)
            self.lichess.error.connect(self._lichess_error)
            self.lichess.move_failed.connect(self._lichess_move_failed)
            self.lichess.takeback_action_completed.connect(self._lichess_takeback_completed)
            self.lichess.event_received.connect(self._lichess_event)
            self.lichess.game_event_received.connect(self._lichess_game_event)
            self.lichess.challenge_ready.connect(self._challenge_ready)
            self.lichess.challenge_status_changed.connect(self._challenge_status)
            self.lichess.invitation_cancelled.connect(self._invitation_cancelled)
            self.lichess.restore()
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
        self.connection = QLabel("●  Не подключено")
        self.connection.setStyleSheet("color: #77766f;")
        header.addWidget(self.connection)
        self.connect_button = QPushButton("Подключить Lichess")
        self.connect_button.clicked.connect(self.connect_lichess)
        header.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Отключить")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect_lichess)
        header.addWidget(self.disconnect_button)
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
        self.opponent_label = QLabel("Соперник  ·  локальная партия")
        self.opponent_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.opponent_label)
        self.board = BoardView(self.controller.board)
        self.board.setMinimumSize(440, 440)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self.board.move_intent.connect(self.on_move_intent)
        self.board.premove_intent.connect(self.on_premove_intent)
        self.board.cancel_premove_requested.connect(self.cancel_premove)
        board_layout.addWidget(self.board, 1, Qt.AlignmentFlag.AlignCenter)
        self.you_label = QLabel("Вы  ·  офлайн")
        self.you_label.setStyleSheet("font-size: 15px; font-weight: 600; padding: 2px 4px;")
        board_layout.addWidget(self.you_label)
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
        self.clock_label = QLabel("Без часов")
        self.clock_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        side_layout.addWidget(self.clock_label)
        self.friend_link_button = QPushButton("С другом по ссылке")
        self.friend_link_button.clicked.connect(self.open_friend_game)
        self.username_button = QPushButton("Вызвать игрока Lichess")
        self.username_button.clicked.connect(self.challenge_username)
        self.ai_button = QPushButton("Играть против Lichess AI")
        self.ai_button.clicked.connect(self.play_ai)
        self.resign_button = QPushButton("Сдаться")
        self.resign_button.clicked.connect(self.resign_online)
        self.draw_button = QPushButton("Предложить ничью")
        self.draw_button.clicked.connect(self.handle_draw)
        self.decline_draw_button = QPushButton("Отклонить ничью")
        self.decline_draw_button.clicked.connect(lambda: self.lichess.draw(self.online_session.game_id, False) if self.online_session else None)
        self.decline_draw_button.setVisible(False)
        self.takeback_button = QPushButton("Предложить вернуть ход")
        self.takeback_button.clicked.connect(self.request_takeback)
        self.accept_takeback_button = QPushButton("Принять возврат хода")
        self.accept_takeback_button.clicked.connect(lambda: self.answer_takeback(True))
        self.decline_takeback_button = QPushButton("Отклонить возврат хода")
        self.decline_takeback_button.clicked.connect(lambda: self.answer_takeback(False))
        self.accept_takeback_button.setVisible(False)
        self.decline_takeback_button.setVisible(False)
        self.claim_draw_button = QPushButton("Заявить ничью по правилу")
        self.claim_draw_button.clicked.connect(lambda: self.lichess.claim_draw(self.online_session.game_id) if self.online_session else None)
        self.leave_game_button = QPushButton("Закрыть просмотр партии")
        self.leave_game_button.clicked.connect(self.leave_online_game)
        for button in (self.friend_link_button, self.username_button, self.ai_button, self.resign_button, self.draw_button, self.decline_draw_button, self.takeback_button, self.accept_takeback_button, self.decline_takeback_button, self.claim_draw_button, self.leave_game_button):
            button.setEnabled(False)
            button.setVisible(False)
            side_layout.addWidget(button)
        self.games_button = QPushButton("Завершённые партии")
        self.games_button.clicked.connect(self.show_saved_games)
        side_layout.addWidget(self.games_button)
        self.cancel_challenge_button = QPushButton("Отменить ожидающий вызов")
        self.cancel_challenge_button.clicked.connect(self.lichess.cancel_username_challenge if self.lichess else lambda: None)
        self.cancel_challenge_button.setEnabled(False)
        self.cancel_challenge_button.setVisible(False)
        side_layout.addWidget(self.cancel_challenge_button)
        self.cancel_invitation_button = QPushButton("Отменить приглашение по ссылке")
        self.cancel_invitation_button.clicked.connect(self.cancel_friend_invitation)
        self.cancel_invitation_button.setEnabled(False)
        self.cancel_invitation_button.setVisible(False)
        side_layout.addWidget(self.cancel_invitation_button)

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
        if self.online_session is not None:
            self._refresh_online(message)
            return
        for button in (self.takeback_button, self.accept_takeback_button,
                       self.decline_takeback_button):
            button.setVisible(False)
        snapshot = self.controller.snapshot()
        self.board.set_position(snapshot.board)
        self.board.set_interaction_enabled(snapshot.live)
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        self._apply_preferences()
        self.moves.clear()
        position = self.controller.board.root()
        viewed_ply = (
            self.controller.ply
            if self.controller.view_ply is None
            else self.controller.view_ply
        )
        viewed_item = None
        for ply, move in enumerate(self.controller.board.move_stack, start=1):
            number = (ply + 1) // 2
            prefix = f"{number}. " if ply % 2 else f"{number}... "
            item = QListWidgetItem(prefix + position.san(move))
            item.setData(Qt.ItemDataRole.UserRole, ply)
            self.moves.addItem(item)
            position.push(move)
            if ply == viewed_ply:
                viewed_item = item
        if viewed_item is not None:
            self.moves.setCurrentItem(viewed_item)
            self.moves.scrollToItem(viewed_item)
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
        if self.online_session is not None:
            session = self.online_session
            if self.online_view_ply is not None or not session.begin_submission(move):
                self.refresh("Ход недоступен: проверьте актуальную позицию.")
                return
            self.lichess.send_move(session.game_id, move.uci(), session.pending_request_id)
            self.refresh(f"Отправляем ход {move.uci()}; ожидаем подтверждения Lichess.")
            return
        if self.controller.apply_move_intent(move):
            self.refresh("Ход записан в локальную партию.")
        else:
            self.refresh("Ход не выполнен: позиция не изменилась.")

    def on_premove_intent(self, move: chess.Move) -> None:
        if self.online_session is not None:
            if self.online_session.queue_premove(move):
                self.board.set_premove(move, self.online_session.color)
                self.refresh("Предход записан и будет проверен после хода соперника.")
            else:
                self.refresh("Предход не принят в текущей позиции.")
            return
        if self.controller.queue_premove(move):
            self.refresh("Предход поставлен. Он будет проверен после хода соперника.")
        else:
            self.refresh("Предход не принят в текущем состоянии.")

    def cancel_premove(self) -> None:
        if self.online_session is not None:
            self.online_session.premove = None
            self.board.set_premove(None, self.online_session.color)
            self.refresh("Предход отменён.")
            return
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
        if self.online_session is not None:
            ply = int(item.data(Qt.ItemDataRole.UserRole))
            self.online_view_ply = None if ply >= len(self.online_session.board.move_stack) else ply
            self.refresh()
            return
        self.controller.go_to(int(item.data(Qt.ItemDataRole.UserRole)))
        self.refresh()

    def navigate_to(self, ply: int) -> None:
        if self.online_session is not None:
            self.online_view_ply = None if ply >= len(self.online_session.board.move_stack) else ply
            self.refresh()
            return
        self.controller.go_to(ply)
        self.refresh()

    def navigate_by(self, delta: int) -> None:
        if self.online_session is not None:
            maximum = len(self.online_session.board.move_stack)
            current = maximum if self.online_view_ply is None else self.online_view_ply
            self.online_view_ply = max(0, min(maximum, current + delta))
            if self.online_view_ply == maximum:
                self.online_view_ply = None
            self.refresh()
            return
        self.controller.step_view(delta)
        self.refresh()

    def navigate_live(self) -> None:
        if self.online_session is not None:
            self.online_view_ply = None
            self.refresh()
            return
        self.controller.go_live()
        self.refresh()

    def flip_board(self) -> None:
        flipped = not self.settings.value("board_flipped", False, type=bool)
        self.settings.setValue("board_flipped", flipped)
        self.settings.sync()
        self.board.set_flipped(flipped)

    def new_game(self) -> None:
        if self.online_session is not None:
            QMessageBox.information(
                self, "Онлайн-партия",
                "Чтобы вернуться к локальной доске, дождитесь завершения текущей онлайн-партии.",
            )
            return
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

    def connect_lichess(self) -> None:
        if not self.lichess:
            QMessageBox.warning(self, "Lichess", "Windows Credential Manager недоступен. Установите зависимости проекта.")
            return
        self.lichess.connect_account()

    def disconnect_lichess(self) -> None:
        if self.lichess:
            self.lichess.disconnect_account()

    def _lichess_status(self, status: str) -> None:
        self.connection.setText("●  " + status)
        connected = status.startswith("Подключено") or status.startswith("Переподключение")
        if status == "Не подключено":
            self._lichess_connected = False
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(False)
        self.connect_button.setEnabled(status in {"Не подключено", "Ошибка подключения"})
        self.disconnect_button.setEnabled(status.startswith("Подключено"))
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setEnabled(status.startswith("Подключено"))

    def _lichess_authenticated(self, username: str) -> None:
        self._lichess_connected = True
        self.connection.setText(f"●  Подключено: {username}")
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setEnabled(True)
            button.setVisible(self.online_session is None)

    def _lichess_error(self, message: str) -> None:
        self.cancel_challenge_button.setEnabled(False)
        self.cancel_challenge_button.setVisible(False)
        if getattr(self, "_open_challenge_id", None):
            self.cancel_invitation_button.setEnabled(True)
        self.game_status.setText(message)
        QMessageBox.warning(self, "Lichess", message)
        self.refresh(message)

    def _lichess_move_failed(self, game_id: str, uci: str, request_id: str, status: int) -> None:
        session = self.online_sessions.get(game_id)
        definitive = status in {400, 401, 403, 404, 429}
        if not session or session.pending_uci != uci or not session.fail_submission(
                request_id, definitive=definitive):
            return
        message = {
            400: "Lichess отклонил ход (400). Проверьте позицию и попробуйте снова.",
            401: "Lichess отклонил ход (401). Переподключите аккаунт.",
            403: "Lichess запретил ход (403). Проверьте состояние партии.",
            404: "Партия больше не найдена.",
            429: "Lichess временно ограничил запросы (429). Попробуйте позже.",
        }.get(status, "Не удалось отправить ход; результат запроса неизвестен.")
        if status not in {400, 401, 403, 404, 429}:
            self.game_status.setText(f"Ход {uci}: {message}")
            return
        self.refresh(message)

    def request_takeback(self) -> None:
        self._start_takeback_action("offer")

    def answer_takeback(self, accept: bool) -> None:
        self._start_takeback_action("accept" if accept else "decline")

    def _start_takeback_action(self, action: str) -> None:
        session = self.online_session
        if not session or not self.lichess:
            return
        request_id = session.begin_takeback_action(action)
        if not request_id:
            self.refresh("Возврат хода сейчас недоступен: дождитесь синхронизации партии.")
            return
        messages = {
            "offer": "Отправляем предложение вернуть ход…",
            "accept": "Принимаем предложение; ждём подтверждения позиции Lichess…",
            "decline": "Отправляем отказ от возврата хода…",
        }
        self.refresh(messages[action])
        self.lichess.takeback(session.game_id, action, request_id)

    def _lichess_takeback_completed(self, game_id: str, request_id: str, status: int) -> None:
        session = self.online_sessions.get(game_id)
        if not session:
            return
        action = session.takeback_action_pending
        if not session.finish_takeback_action(request_id, status):
            return
        if status == 0:
            messages = {
                "offer": "Предложение возврата отправлено; ждём ответа соперника.",
                "accept": "Предложение принято; ждём обновления позиции от Lichess…",
                "decline": "Отказ отправлен; ждём подтверждения от Lichess…",
            }
            message = messages.get(action, "Запрос возврата отправлен; ждём Lichess.")
        else:
            message = {
                400: "Lichess отклонил запрос возврата хода (400). Позиция не изменена.",
                403: "Lichess запретил возврат хода (403). Позиция не изменена.",
                404: "Партия не найдена; позиция не изменена.",
            }.get(status, "Не удалось отправить запрос возврата хода. Позиция не изменена.")
        self.refresh(message)

    def open_friend_game(self) -> None:
        dialog = OnlineGameDialog("Партия с другом по ссылке", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self.game_status.setText("Создаём вызов и присоединяем вас…")
        self.lichess.open_friend_game(color, limit, increment, rated)

    def challenge_username(self) -> None:
        username, ok = QInputDialog.getText(self, "Вызов игрока", "Имя пользователя Lichess:")
        if not ok or not username.strip():
            return
        dialog = OnlineGameDialog("Контроль времени", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self.game_status.setText(f"Ожидаем ответ от {username.strip()}…")
        self.lichess.challenge_username(username.strip(), color, limit, increment, rated)

    def play_ai(self) -> None:
        dialog = OnlineGameDialog("Игра против Lichess AI", include_level=True, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, level, _ = dialog.selection()
        self.game_status.setText("Создаём партию против Lichess AI…")
        self.lichess.create_ai_game(level, color, limit, increment)

    def _challenge_ready(self, result: dict) -> None:
        url = result.get("friendUrl")
        if not url:
            self.game_status.setText("Вызов отправлен; ждём ответ Lichess…")
            return
        self._open_challenge_id = result.get("id")
        active_game = self.online_session and self.online_session.status in {"started", "playing"}
        self.cancel_invitation_button.setEnabled(bool(self._open_challenge_id) and not active_game)
        self.cancel_invitation_button.setVisible(bool(self._open_challenge_id) and not active_game)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(False)
        dialog = QDialog(self)
        dialog.setWindowTitle("Приглашение готово")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Вы подключились к партии. Отправьте другу ссылку:"))
        link = QLineEdit(url)
        link.setReadOnly(True)
        layout.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = QPushButton("Скопировать ссылку")
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(url))
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        self.game_status.setText("Ожидаем, пока друг подключится через браузер…")

    def _challenge_status(self, message: str, can_cancel: bool) -> None:
        self.game_status.setText(message)
        self.cancel_challenge_button.setEnabled(can_cancel)
        self.cancel_challenge_button.setVisible(can_cancel)

    def cancel_friend_invitation(self) -> None:
        if not getattr(self, "_open_challenge_id", None):
            return
        if QMessageBox.question(self, "Отменить приглашение", "Отменить приглашение по ссылке?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.lichess.cancel_open_invitation(self._open_challenge_id)
        self.cancel_invitation_button.setEnabled(False)
        self.game_status.setText("Отменяем приглашение…")

    def _invitation_cancelled(self, challenge_id: str) -> None:
        if challenge_id == getattr(self, "_open_challenge_id", None):
            self._open_challenge_id = None
            self.cancel_invitation_button.setVisible(False)
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(self._lichess_connected and self.online_session is None)
            self.game_status.setText("Приглашение отменено.")

    def _lichess_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "gameStart":
            self._open_challenge_id = None
            self.cancel_invitation_button.setEnabled(False)
            self.cancel_invitation_button.setVisible(False)
            game = event.get("game", {})
            game_id = game.get("gameId")
            if not game_id:
                return
            session = self.online_sessions.get(game_id) or LichessGameSession(game_id, self.game_repository)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            session.status = "started"
            opponent = game.get("opponent", {})
            session.opponent = opponent.get("username", "Соперник")
            self.online_sessions[game_id] = session
            self.online_session = session
            self.online_view_ply = None
            self.refresh(f"Партия началась против {session.opponent}.")
        elif kind == "gameFinish":
            game_id = event.get("game", {}).get("gameId")
            if game_id in self.online_sessions:
                game = event.get("game", {})
                game_status = game.get("status", {})
                status = game_status.get("name", "unknownFinish") if isinstance(game_status, dict) else str(game_status)
                self.online_sessions[game_id].finish(status, game.get("winner"))
                self.refresh("Партия завершена. Результат сохранён локально.")
        elif kind == "challenge":
            self.game_status.setText("Получено или обновлено приглашение Lichess.")

    def _lichess_game_event(self, game_id: str, event: dict) -> None:
        session = self.online_sessions.get(game_id)
        if session is None:
            session = LichessGameSession(game_id, self.game_repository)
            self.online_sessions[game_id] = session
        if event.get("type") == "gameFull":
            username = (self.lichess.username or "").casefold()
            white = event.get("white", {})
            black = event.get("black", {})
            if username and white.get("name", "").casefold() == username:
                session.configure_players(white, black, "white")
            elif username and black.get("name", "").casefold() == username:
                session.configure_players(white, black, "black")
            elif event.get("color") in {"white", "black"}:
                session.configure_players(white, black, event["color"])
        if event.get("type") == "opponentGone":
            self.refresh("Соперник временно отключился; ждём переподключения.")
            return
        previous_moves = tuple(session.moves.split())
        try:
            session.apply_event(event)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError) as exc:
            self._lichess_error(f"Не удалось синхронизировать позицию Lichess: {exc}")
            return
        self.online_session = session
        self.online_view_ply = None
        had_premove = session.premove is not None
        current_moves = tuple(session.moves.split())
        takeback_confirmed = (len(current_moves) < len(previous_moves)
                              and previous_moves[:len(current_moves)] == current_moves)
        self.refresh("Возврат хода подтверждён Lichess." if takeback_confirmed else None)
        premove = session.take_ready_premove()
        if premove and session.begin_submission(premove):
            self.lichess.send_move(game_id, premove.uci(), session.pending_request_id)
            self.refresh(f"Предход {premove.uci()} отправлен; ожидаем подтверждения Lichess.")
        elif had_premove and session.premove is None:
            self.refresh("Предход не стал легальным и снят.")

    def _refresh_online(self, message: str | None = None) -> None:
        session = self.online_session
        board = session.board
        render_board = session.render_board
        ply = len(board.move_stack) if self.online_view_ply is None else min(self.online_view_ply, len(board.move_stack))
        display_source = render_board if self.online_view_ply is None else board
        shown = display_source.copy(stack=True)
        shown = shown.root()
        for move in display_source.move_stack[:(len(display_source.move_stack) if self.online_view_ply is None else ply)]:
            shown.push(move)
        live = self.online_view_ply is None or ply == len(board.move_stack)
        self.board.set_position(shown)
        active = session.status in {"started", "playing"}
        self.board.set_interaction_enabled(live and active and not session.input_locked)
        self.board.set_premove(session.premove, session.color)
        self._apply_preferences()
        self.opponent_label.setText(f"Соперник  ·  {session.opponent}")
        self.you_label.setText("Вы  ·  " + ("белыми" if session.color == chess.WHITE else "чёрными"))
        self.turn_label.setText(("Ваш ход" if session.is_my_turn else "Ход соперника") + (" · история" if not live else ""))
        if message:
            self.game_status.setText(message)
        elif session.pending_uci:
            self.game_status.setText(f"Ход {session.pending_uci} отправлен; ожидаем подтверждения Lichess…")
        elif session.takeback_requested_by_opponent:
            self.game_status.setText("Соперник предлагает вернуть ход.")
        elif session.takeback_accept_pending:
            self.game_status.setText("Возврат принят; ожидаем подтверждения позиции Lichess…")
        elif session.takeback_decline_pending:
            self.game_status.setText("Отказ отправлен; ожидаем обновления состояния партии…")
        elif session.takeback_requested_by_me:
            self.game_status.setText("Предложение возврата отправлено; ждём ответа соперника.")
        else:
            self.game_status.setText(f"Lichess · {session.status}")
        self.moves.clear()
        position = board.root()
        selected = None
        for halfmove, move in enumerate(board.move_stack, start=1):
            label = f"{(halfmove + 1) // 2}. " if halfmove % 2 else f"{(halfmove + 1) // 2}... "
            item = QListWidgetItem(label + position.san(move))
            item.setData(Qt.ItemDataRole.UserRole, halfmove)
            self.moves.addItem(item)
            position.push(move)
            if halfmove == ply:
                selected = item
        if selected:
            self.moves.setCurrentItem(selected)
        self._update_clock()
        self.history_label.setText("Актуальная позиция" if live else f"Просмотр хода {ply} из {len(board.move_stack)}")
        self.resign_button.setEnabled(session.status in {"started", "playing"})
        self.resign_button.setVisible(session.status in {"started", "playing"})
        self.draw_button.setEnabled(session.status in {"started", "playing"})
        self.draw_button.setVisible(session.status in {"started", "playing"})
        self.draw_button.setText("Принять ничью" if session.draw_offered_by_opponent else "Предложить ничью")
        self.decline_draw_button.setVisible(session.draw_offered_by_opponent)
        self.decline_draw_button.setEnabled(session.draw_offered_by_opponent)
        self.takeback_button.setVisible(active)
        self.takeback_button.setText(
            "Ожидаем ответа на возврат…" if session.takeback_requested_by_me
            else "Предложить вернуть ход"
        )
        self.takeback_button.setEnabled(
            active and bool(session.moves) and not session.input_locked
        )
        show_takeback = session.takeback_requested_by_opponent
        self.accept_takeback_button.setVisible(show_takeback)
        self.decline_takeback_button.setVisible(show_takeback)
        response_enabled = (show_takeback and not session.pending_uci
                            and not session.takeback_action_pending
                            and not session.takeback_accept_pending
                            and not session.takeback_decline_pending)
        self.accept_takeback_button.setEnabled(response_enabled)
        self.decline_takeback_button.setEnabled(response_enabled)
        self.claim_draw_button.setEnabled(session.status in {"started", "playing"} and session.board.can_claim_draw())
        self.claim_draw_button.setVisible(session.status in {"started", "playing"} and session.board.can_claim_draw())
        self.leave_game_button.setEnabled(session.status not in {"started", "playing", "created"})
        self.leave_game_button.setVisible(session.status not in {"started", "playing", "created"})
        self.new_game_button.setEnabled(False)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(False)
        self.cancel_premove_button.setEnabled(session.premove is not None)
        self.cancel_premove_button.setVisible(session.premove is not None)
        self.premove_label.setVisible(session.premove is not None)
        if session.premove:
            self.premove_label.setText(f"Предход: {session.premove.uci()} · ожидание подтверждения хода соперника.")
        for widget in (self.opponent_move, self.apply_opponent_button):
            widget.setVisible(False)

    def _update_clock(self) -> None:
        session = self.online_session
        if not session or session.white_ms is None or session.black_ms is None:
            self.clock_label.setText(
                f"Контроль {session.clock_control}" if session and session.clock_control != "-" else "Без часов"
            )
            return
        elapsed = (max(0, int((time.monotonic() - session._last_clock_at) * 1000))
                   if session.status in {"started", "playing"} else 0)
        white = max(0, session.white_ms - (elapsed if session.board.turn == chess.WHITE else 0))
        black = max(0, session.black_ms - (elapsed if session.board.turn == chess.BLACK else 0))
        self.clock_label.setText(f"Белые {white // 60000:02}:{white // 1000 % 60:02}   Чёрные {black // 60000:02}:{black // 1000 % 60:02}")

    def resign_online(self) -> None:
        session = self.online_session
        if not session:
            return
        action = "Прервать партию" if not session.board.move_stack else "Сдаться"
        if QMessageBox.question(self, "Завершить партию", f"{action} в текущей партии?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        if session.board.move_stack:
            self.lichess.resign(session.game_id)
        else:
            self.lichess.abort(session.game_id)

    def handle_draw(self) -> None:
        session = self.online_session
        if not session:
            return
        if session.draw_offered_by_opponent:
            if QMessageBox.question(self, "Ничья", "Принять предложение ничьей?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
                self.lichess.draw(session.game_id, True)
        else:
            self.lichess.draw(session.game_id, True)

    def show_saved_games(self) -> None:
        games = self.game_repository.list_games()
        if not games:
            QMessageBox.information(self, "Партии", "Сохранённых завершённых партий пока нет.")
            return
        labels = [f"{g['opponent']} · {g['status']} · {g['updated_at'][:10]}" for g in games]
        selected, ok = QInputDialog.getItem(self, "Завершённые партии", "Открыть партию:", labels, 0, False)
        if not ok:
            return
        game = games[labels.index(selected)]
        pgn = self.game_repository.export_pgn(game["game_id"])
        choice = QMessageBox(self)
        choice.setWindowTitle("Сохранённая партия")
        choice.setText(f"{game['opponent']} · {game['status']}")
        open_button = choice.addButton("Открыть", QMessageBox.ButtonRole.AcceptRole)
        export_button = choice.addButton("Экспорт PGN", QMessageBox.ButtonRole.ActionRole)
        choice.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        choice.exec()
        if choice.clickedButton() == open_button:
            session = LichessGameSession(game["game_id"], self.game_repository)
            session.initial_fen = game["initial_fen"]
            session.moves = game["moves"]
            session.color = chess.WHITE if game["color"] == "white" else chess.BLACK
            session.opponent = game["opponent"]
            session.status = game["status"]
            session.winner = game["winner"]
            session.clock_control = game["clock"]
            session._rebuild_board()
            self.online_sessions[session.game_id] = session
            self.online_session = session
            self.online_view_ply = None
            self.refresh("Открыта локально сохранённая партия (только просмотр).")
        elif choice.clickedButton() == export_button and pgn:
            path, _ = QFileDialog.getSaveFileName(self, "Экспорт PGN", f"{game['game_id']}.pgn", "PGN (*.pgn)")
            if path:
                Path(path).write_text(pgn + "\n", encoding="utf-8")

    def leave_online_game(self) -> None:
        if not self.online_session:
            return
        session = self.online_session
        if session.status in {"started", "playing"}:
            QMessageBox.information(self, "Партия активна", "Активная партия остаётся подключённой и будет восстановлена после запуска.")
            return
        self.online_session = None
        self.online_view_ply = None
        self.new_game_button.setEnabled(True)
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(self._lichess_connected)
        self.refresh("Локальная доска.")

    def closeEvent(self, event) -> None:
        if self.lichess:
            self.lichess.close()
        super().closeEvent(event)
