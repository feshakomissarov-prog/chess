"""Main M0 window and settings dialog."""

from __future__ import annotations

import chess
import time
import threading
from datetime import datetime
from pathlib import Path
import tempfile
from PySide6.QtCore import QSettings, Qt, QStandardPaths, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QColor, QPainter, QPen
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
    QProgressDialog,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .board_view import BoardView
from .controller import GameController
from .lichess.auth import CredentialStorageError, WindowsCredentialStore
from .lichess.analysis import AnalysisOptions, CompletedGameAnalyzer
from .lichess.repository import GameRepository
from .lichess.service import LichessService
from .lichess.session import LichessGameSession
from .lichess.statistics import StatisticsService


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


class EvaluationGraph(QWidget):
    """Small evaluation-over-ply chart; score is always from White's point of view."""
    selected_ply = Signal(int)

    def __init__(self, assessments: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.assessments = assessments
        self.setMinimumHeight(210)
        self.setMinimumWidth(420)

    def _points(self):
        values = []
        for item in self.assessments:
            score = item.get("white_cp")
            if score is None and item.get("mate_white") is not None:
                score = 1000 if item["mate_white"] > 0 else -1000 if item["mate_white"] < 0 else 0
            if score is not None:
                values.append((int(item["ply"]), max(-1000, min(1000, int(score)))))
        return values

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(32, 12, -12, -26)
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        points = self._points()
        if not points:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Нет оценок для графика")
            return
        max_ply = max(1, max(ply for ply, _ in points))
        coords = []
        for ply, score in points:
            x = rect.left() + int((rect.width() * ply) / max_ply)
            y = rect.center().y() - int((rect.height() * score) / 2000)
            coords.append((x, y))
        painter.setPen(QPen(QColor("#315f9b"), 2))
        for first, second in zip(coords, coords[1:]):
            painter.drawLine(first[0], first[1], second[0], second[1])
        painter.setBrush(QColor("#315f9b"))
        for x, y in coords:
            painter.drawEllipse(x - 3, y - 3, 6, 6)
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawText(2, rect.top() + 8, "+10 pawns")
        painter.drawText(2, rect.bottom(), "−10")
        painter.drawText(rect.left(), self.height() - 4, "ply 0")
        painter.drawText(rect.right() - 35, self.height() - 4, f"ply {max_ply}")

    def mousePressEvent(self, event) -> None:
        points = self._points()
        if points:
            rect = self.rect().adjusted(32, 12, -12, -26)
            max_ply = max(1, max(ply for ply, _ in points))
            ply = round((event.position().x() - rect.left()) * max_ply / max(1, rect.width()))
            self.selected_ply.emit(max(0, min(max_ply, ply)))


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
        self.allow_premoves = QCheckBox("Разрешить предходы")
        self.allow_premoves.setChecked(settings.value("allow_premoves", True, type=bool))
        layout.addWidget(self.allow_premoves)
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
        settings.setValue("allow_premoves", self.allow_premoves.isChecked())
        settings.sync()


class ActiveGamesDialog(QDialog):
    refresh_requested = Signal()
    open_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Активные партии Lichess")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.status = QLabel("Нажмите «Обновить», чтобы запросить список у Lichess.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.games = QListWidget()
        self.games.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.games)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить список")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        buttons.addWidget(self.refresh_button)
        self.open_button = QPushButton("Открыть выбранную партию")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.games.currentItemChanged.connect(
            lambda current, _previous: self.open_button.setEnabled(current is not None)
        )

    def set_loading(self) -> None:
        self.status.setText("Запрашиваем активные партии…")
        self.refresh_button.setEnabled(False)

    def set_error(self, message: str) -> None:
        self.status.setText(message)
        self.refresh_button.setEnabled(True)

    def set_games(self, games: list[dict]) -> None:
        self.games.clear()
        for game in games:
            opponent = game.get("opponent") or {}
            opponent_name = opponent.get("username") or opponent.get("id") or "Соперник"
            color = {"white": "белыми", "black": "чёрными"}.get(
                game.get("color"), "цвет не указан"
            )
            speed = str(game.get("speed") or "контроль не указан")
            kind = "заочная" if speed.casefold() == "correspondence" else "реальное время"
            is_my_turn = game.get("isMyTurn")
            turn = ("ваш ход" if is_my_turn else "ход соперника"
                    if isinstance(is_my_turn, bool) else "чей ход не указан")
            seconds = game.get("secondsLeft")
            remaining = f" · осталось {int(seconds)} с" if isinstance(seconds, (int, float)) else ""
            item = QListWidgetItem(
                f"{opponent_name} · вы {color} · {kind} ({speed}) · {turn}{remaining}"
            )
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games.addItem(item)
        self.status.setText(
            f"Активных партий: {len(games)}." if games else "Lichess не вернул активных партий."
        )
        self.refresh_button.setEnabled(True)
        self.open_button.setEnabled(self.games.currentItem() is not None)

    def open_selected(self) -> None:
        item = self.games.currentItem()
        if item is not None:
            self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()


class MainWindow(QMainWindow):
    analysis_progress = Signal(str, int, int)
    analysis_finished = Signal(str, object)

    def __init__(
        self,
        settings: QSettings | None = None,
        *,
        asynchronous_storage: bool = False,
        offline_mode: bool = False,
    ) -> None:
        super().__init__()
        self.offline_mode = offline_mode
        self._offline_data_dir: tempfile.TemporaryDirectory | None = None
        if offline_mode:
            # Smoke runs must not read or write the owner's settings or game DB.
            self._offline_data_dir = tempfile.TemporaryDirectory(prefix="chess-coach-offline-")
            data_root = Path(self._offline_data_dir.name)
            self.settings = QSettings(
                str(data_root / "settings.ini"), QSettings.Format.IniFormat
            )
        else:
            self.settings = settings if settings is not None else QSettings("ChessCoach", "ChessCoach")
            data_root = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            ))
            data_root.mkdir(parents=True, exist_ok=True)
        color = self.settings.value("player_color", chess.WHITE, type=int)
        self.controller = GameController(chess.Color(color))
        self.game_repository = GameRepository(data_root / "games.sqlite3", async_writes=asynchronous_storage)
        self._online_history_game_id: str | None = None
        self._online_history_moves: tuple[chess.Move, ...] = ()
        self._online_history_position: chess.Board | None = None
        if offline_mode:
            # The offline smoke must not even construct a credential store. This
            # is stronger than passing an environment variable through a shell
            # that may launch the app via Explorer/Start-Process.
            self.lichess = None
        else:
            try:
                self.lichess = LichessService(token_store=WindowsCredentialStore(),
                                              repository=self.game_repository, parent=self)
            except CredentialStorageError:
                self.lichess = None
        self.online_sessions: dict[str, LichessGameSession] = {}
        self.online_session: LichessGameSession | None = None
        self._local_board_mode = "free"
        self.online_view_ply: int | None = None
        self._screen_mode = "home"
        self._awaiting_game_start = False
        self._selected_live_game_id: str | None = None
        self._review_session: LichessGameSession | None = None
        self._lichess_connected = False
        self._archive_progress_dialog: QProgressDialog | None = None
        self._puzzle_progress_dialog: QProgressDialog | None = None
        self._close_retry_scheduled = False
        self._analysis_stop: threading.Event | None = None
        self._analysis_thread: threading.Thread | None = None
        self._analysis_dialog: QProgressDialog | None = None
        self._shutdown_requested = False
        self._shutdown_read_only = False
        self._analysis_shutdown_deadline: float | None = None
        self.analysis_progress.connect(self._analysis_progress)
        self.analysis_finished.connect(self._analysis_finished)
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
        self.show_home()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(250)
        self._close_retry_timer = QTimer(self)
        self._close_retry_timer.setSingleShot(True)
        self._close_retry_timer.setInterval(200)
        self._close_retry_timer.timeout.connect(self._retry_close_after_workers)
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
            self.lichess.active_games_received.connect(self._active_games_received)
            self.lichess.active_games_error.connect(self._active_games_error)
            self.lichess.archive_progress.connect(self._archive_progress)
            self.lichess.archive_finished.connect(self._archive_finished)
            self.lichess.archive_game_refresh_finished.connect(self._archive_game_refresh_finished)
            self.lichess.puzzles_progress.connect(self._puzzles_progress)
            self.lichess.puzzles_finished.connect(self._puzzles_finished)
            self.lichess.restore()
        elif offline_mode:
            self._disable_online_controls()
        self.refresh()

    def _disable_online_controls(self) -> None:
        self.connection.setText("●  Офлайн-режим · Lichess отключён")
        self.connect_button.setEnabled(False)
        self.connect_button.setToolTip("В офлайн-smoke авторизация и сетевые запросы отключены.")
        self.disconnect_button.setVisible(False)
        for button in (
            self.friend_link_button, self.username_button, self.ai_button,
            self.home_ai_button, self.home_friend_button, self.home_username_button,
            self.cancel_challenge_button, self.active_games_button,
        ):
            button.setEnabled(False)
            button.setVisible(False)

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
            if label == "Играть":
                button.clicked.connect(self.show_home)
            elif label == "Мои партии":
                button.clicked.connect(self.show_saved_games)
            else:
                button.setEnabled(False)
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

        self.screen_title = QLabel("Главная")
        self.screen_title.setStyleSheet("font-size: 18px; font-weight: 700; padding: 2px 4px;")
        outer.addWidget(self.screen_title)
        self.screen_stack = QStackedWidget()
        self.home_page = self._build_home_page()
        self.screen_stack.addWidget(self.home_page)

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
        self.local_mode_label = QLabel("Режим локальной доски")
        side_layout.addWidget(self.local_mode_label)
        self.local_mode_combo = QComboBox()
        self.local_mode_combo.addItem("Свободная доска (за обе стороны)", "free")
        self.local_mode_combo.addItem("Тренировка предходов (мой цвет)", "premove_training")
        self.local_mode_combo.currentIndexChanged.connect(self._local_board_mode_changed)
        side_layout.addWidget(self.local_mode_combo)
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
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
        self.takeback_button.setToolTip("Запросить возврат через Lichess и дождаться подтверждённой сервером позиции.")
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
        self.undo_local_button = QPushButton("Отменить последний ход")
        self.undo_local_button.setToolTip("Отменяет последний подтверждённый ход локальной партии. Для Lichess используется «Вернуть ход».")
        self.undo_local_button.clicked.connect(self.undo_local_last_move)
        self.undo_local_button.setVisible(False)
        side_layout.addWidget(self.undo_local_button)
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

        self.premove_title = QLabel("ПРОВЕРКА ПРЕДХОДОВ")
        self.premove_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #77766f;")
        side_layout.addWidget(self.premove_title)
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
        self.game_page = splitter
        self.screen_stack.addWidget(self.game_page)
        self.result_page = self._build_result_page()
        self.screen_stack.addWidget(self.result_page)
        self.screen_stack.setCurrentWidget(self.home_page)
        outer.addWidget(self.screen_stack, 1)
        self.setCentralWidget(root)

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Создать или открыть партию"))
        self.home_status = QLabel("Выберите режим игры. Анализ и тренировки появятся позже.")
        self.home_status.setWordWrap(True)
        layout.addWidget(self.home_status)
        self.home_ai_button = QPushButton("Играть против Lichess AI")
        self.home_ai_button.clicked.connect(self.play_ai)
        self.home_friend_button = QPushButton("Играть с другом по ссылке")
        self.home_friend_button.clicked.connect(self.open_friend_game)
        self.home_username_button = QPushButton("Вызвать игрока Lichess")
        self.home_username_button.clicked.connect(self.challenge_username)
        self.active_games_button = QPushButton("Активные партии Lichess")
        self.active_games_button.clicked.connect(self.show_active_games)
        self.active_games_button.setEnabled(False)
        self.home_local_button = QPushButton("Локальная доска")
        self.home_local_button.clicked.connect(self.start_local_game)
        self.home_games_button = QPushButton("Завершённые партии")
        self.home_games_button.clicked.connect(self.show_saved_games)
        self.home_archive_button = QPushButton("Синхронизировать архив Lichess")
        self.home_archive_button.clicked.connect(self.start_archive_sync)
        self.home_archive_button.setEnabled(False)
        self.home_stats_button = QPushButton("Статистика партий")
        self.home_stats_button.clicked.connect(self.show_statistics)
        self.home_puzzles_button = QPushButton("Моя история задач Lichess")
        self.home_puzzles_button.clicked.connect(self.start_puzzle_sync)
        self.home_puzzles_button.setEnabled(False)
        self.home_settings_button = QPushButton("Настройки")
        self.home_settings_button.clicked.connect(self.open_settings)
        self.resume_game_button = QPushButton("Вернуться в текущую партию")
        self.resume_game_button.clicked.connect(self.resume_current_game)
        for button in (self.home_ai_button, self.home_friend_button,
                       self.home_username_button, self.active_games_button,
                       self.home_local_button,
                       self.home_games_button, self.home_archive_button, self.home_stats_button,
                       self.home_puzzles_button,
                       self.home_settings_button,
                       self.resume_game_button):
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.result_summary = QLabel("Партия завершена")
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)
        self.result_saved_label = QLabel()
        layout.addWidget(self.result_saved_label)
        self.result_review_button = QPushButton("Просмотреть партию")
        self.result_review_button.clicked.connect(self.review_finished_game)
        self.result_export_button = QPushButton("Экспорт PGN")
        self.result_export_button.clicked.connect(self.export_current_pgn)
        self.result_new_button = QPushButton("Новая партия")
        self.result_new_button.clicked.connect(self.show_home)
        self.result_home_button = QPushButton("Главная")
        self.result_home_button.clicked.connect(self.show_home)
        for button in (self.result_review_button, self.result_export_button,
                       self.result_new_button, self.result_home_button):
            layout.addWidget(button)
        layout.addStretch(1)
        return page

    def refresh(self, message: str | None = None) -> None:
        if self.online_session is not None:
            self.undo_local_button.setVisible(False)
            self._refresh_online(message)
            return
        for button in (self.takeback_button, self.accept_takeback_button,
                       self.decline_takeback_button):
            button.setVisible(False)
        snapshot = self.controller.snapshot()
        local_game_visible = self.screen_stack.currentWidget() is self.game_page
        self.undo_local_button.setVisible(local_game_visible and self.controller.ply > 0)
        self.undo_local_button.setEnabled(local_game_visible and self.controller.ply > 0)
        self.board.set_position(snapshot.board)
        self.board.set_annotation_context("__local__", snapshot.board)
        self.board.set_interaction_enabled(snapshot.live)
        is_local_mode = self._screen_mode == "local" and local_game_visible
        is_training = is_local_mode and self._local_board_mode == "premove_training"
        self.local_mode_label.setVisible(is_local_mode)
        self.local_mode_combo.setVisible(is_local_mode)
        for widget in (self.premove_title, self.premove_label, self.cancel_premove_button,
                       self.opponent_move, self.apply_opponent_button):
            widget.setVisible(is_training)
        self._apply_preferences()
        if is_local_mode:
            self.board.set_premove_allowed(
                self.settings.value("allow_premoves", True, type=bool) and is_training
            )
        self.board.set_premove(self.controller.premove, self.controller.player_color)
        viewed_ply = (
            self.controller.ply
            if self.controller.view_ply is None
            else self.controller.view_ply
        )
        self._sync_online_history(self.controller.board, "__local__", viewed_ply)
        if self.controller.view_ply == 0:
            self.history_label.setText("Начальная позиция")
        elif not snapshot.live:
            self.history_label.setText(f"Просмотр хода {snapshot.ply} из {self.controller.ply}")
        else:
            self.history_label.setText("Актуальная позиция")
        turn_name = "белых" if snapshot.board.turn == chess.WHITE else "чёрных"
        if is_local_mode and self._local_board_mode == "free":
            turn_text = f"Сейчас ход {turn_name}"
        else:
            turn_text = "Ход белых" if snapshot.board.turn else "Ход чёрных"
        self.turn_label.setText(turn_text + ("  ·  просмотр истории" if not snapshot.live else ""))
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
        enabled = (is_training and opponent_turn and self.controller.viewing_live_position
                   and bool(legal))
        self.opponent_move.setEnabled(enabled)
        self.apply_opponent_button.setEnabled(enabled)
        if self.controller.premove is not None:
            self.premove_label.setText(
                f"Предход: {self.controller.premove.uci()}  ·  Esc отменяет"
            )
        elif is_training and opponent_turn:
            side_name = "белыми" if self.controller.player_color == chess.WHITE else "чёрными"
            self.premove_label.setText(f"Вы играете {side_name}. Выберите свою фигуру, затем клетку назначения.")
        elif is_training:
            self.premove_label.setText("Предход появится, когда будет ход соперника.")
        self.cancel_premove_button.setEnabled(self.controller.premove is not None)
        self.clear_marks_button.setEnabled(self.board._annotations_enabled)
        if message:
            self.game_status.setText(message)
        elif is_local_mode and self._local_board_mode == "free":
            self.game_status.setText("Свободная доска: делайте ходы за белых и чёрных.")
        elif is_training:
            self.game_status.setText("Локальная тренировка предходов.")
        else:
            self.game_status.setText("Локальная тренировочная доска")

    def show_home(self) -> None:
        self._screen_mode = "home"
        self.screen_title.setText("Главная")
        self.screen_stack.setCurrentWidget(self.home_page)
        active_id = self._selected_live_game_id
        active = self.online_sessions.get(active_id) if active_id else None
        if active and active.status in {"started", "playing"}:
            self.resume_game_button.setVisible(True)
            self.home_status.setText(f"Идёт партия против {active.opponent}. Её можно продолжить позже.")
        else:
            self.resume_game_button.setVisible(False)
            self.home_status.setText("Выберите режим игры. Анализ и тренировки появятся позже.")
        for button in (self.home_ai_button, self.home_friend_button, self.home_username_button):
            button.setEnabled(self._online_actions_allowed())
        self.active_games_button.setEnabled(self._online_actions_allowed())
        self.home_archive_button.setEnabled(self._online_actions_allowed())
        self.home_puzzles_button.setEnabled(self._online_actions_allowed())

    def start_archive_sync(self) -> None:
        if not self._online_actions_allowed():
            return
        answer = QMessageBox.question(
            self, "Синхронизация архива",
            "Chess Coach запросит PGN партий вашего аккаунта и сохранит их локально. "
            "Первый запуск загрузит архив, следующие — только новые/изменившиеся записи. "
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Подготовка синхронизации…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("Архив Lichess")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_archive_sync)
        self._archive_progress_dialog = dialog
        dialog.show()
        self.lichess.sync_archive()

    def _archive_progress(self, count: int, game_id: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.setLabelText(
                f"Сохранено партий: {count}\nТекущая запись: {game_id}")

    def _archive_finished(self, count: int, message: str) -> None:
        if self._archive_progress_dialog:
            self._archive_progress_dialog.close()
            self._archive_progress_dialog = None
        state = self._current_archive_sync_state()
        successful = state.get("last_success_at")
        last_success = self._format_sync_time(successful) if successful else "ещё не было"
        resumable = ("Да — продолжение доступно" if state.get("last_error")
                     or not state.get("backfill_complete") else "Нет")
        diagnostics = (f"Новых партий в этом запуске: {count}\n"
                       f"Последняя успешная синхронизация: {last_success}\n"
                       f"Можно возобновить: {resumable}")
        if state.get("last_error"):
            diagnostics += f"\nОшибка/прерывание: {state['last_error']}"
        QMessageBox.information(self, "Архив Lichess", f"{diagnostics}\n\n{message}")

    def _current_archive_sync_state(self) -> dict:
        if not self.lichess or not self.lichess.username:
            return {}
        account_id = f"lichess:{self.lichess.username.strip().lower()}"
        return self.game_repository.archive_sync_state(account_id)

    @staticmethod
    def _format_sync_time(value: str) -> str:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
        except (AttributeError, ValueError):
            return str(value)

    def _archive_game_refresh_finished(self, game_id: str, message: str) -> None:
        if message.startswith("История этой партии обновлена"):
            self.home_status.setText(f"Партия {game_id}: {message}")
        else:
            self.home_status.setText(f"Не удалось обновить партию {game_id}: {message}")

    def show_statistics(self) -> None:
        data = StatisticsService(self.game_repository).summary()
        coverage = ("нет оценённых ходов" if data["move_coverage_percent"] is None else
                    f"{data['move_coverage_percent']}% ({data['evaluated_moves']} из "
                    f"{data['confirmed_moves']} ходов)")
        error_rate = ("н/д" if data["own_error_rate_per_100"] is None else
                      f"{data['own_error_rate_per_100']} на 100 оценённых ходов")
        accuracy = data["lichess_accuracy"]
        text = (f"Срез: {data['account_scope']}\n"
                f"Завершённые партии: {data['games']}\n"
                f"Победы / ничьи / поражения: {data['wins']} / {data['draws']} / {data['losses']}\n"
                f"Неизвестный результат / прерваны: {data['unknown_or_aborted']}\n"
                f"Покрытие локальной оценки: {coverage}\n"
                f"Ошибки своих ходов: {error_rate}\n"
                f"Средняя потеря CP по оценённым своим ходам: {data['avg_cpl_cp'] if data['avg_cpl_cp'] is not None else 'н/д'}\n"
                f"Lichess accuracy (официальная, отдельно): белые {accuracy['white'] if accuracy['white'] is not None else 'н/д'}; "
                f"чёрные {accuracy['black'] if accuracy['black'] is not None else 'н/д'}\n"
                "Фазы/классификации доступны в локальном отчёте по оценённым ходам.")
        QMessageBox.information(self, "Статистика партий", text)

    def start_puzzle_sync(self) -> None:
        if not self._online_actions_allowed():
            return
        answer = QMessageBox.question(
            self, "История шахматных задач",
            "Откроется Lichess OAuth для отдельного разрешения puzzle:read. "
            "Chess Coach сохранит только вашу личную историю задач в локальной SQLite. "
            "Текущий токен для партий не будет заменён. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        dialog = QProgressDialog("Ожидание OAuth и загрузка истории…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("История задач Lichess")
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self.lichess.cancel_puzzle_sync)
        self._puzzle_progress_dialog = dialog
        dialog.show()
        self.lichess.connect_and_sync_puzzle_activity(self.game_repository)

    def _puzzles_progress(self, count: int) -> None:
        if self._puzzle_progress_dialog:
            self._puzzle_progress_dialog.setLabelText(f"Обработано попыток: {count}")

    def _puzzles_finished(self, count: int, message: str) -> None:
        if self._puzzle_progress_dialog:
            self._puzzle_progress_dialog.close()
            self._puzzle_progress_dialog = None
        if self.lichess and (self.lichess.puzzle_username or self.lichess.username):
            puzzle_username = self.lichess.puzzle_username or self.lichess.username
            account_id = f"lichess:{puzzle_username.strip().lower()}"
            stats = self.game_repository.puzzle_statistics(account_id)
            sync_state = self.game_repository.puzzle_sync_state(account_id)
            rate = stats["success_percent"]
            successful = sync_state.get("last_success_at")
            last_success = self._format_sync_time(successful) if successful else "ещё не было"
            resumable = ("Да" if sync_state.get("last_error")
                         or not sync_state.get("backfill_complete")
                         or sync_state.get("incremental_before") else "Нет")
            result = (f"Всего попыток: {stats['attempts']}\n"
                      f"Новых попыток в этом запуске: {count}\n"
                      f"Решено: {stats['wins']} · Ошибок: {stats['failures']}\n"
                      f"Доля успеха: {rate if rate is not None else 'н/д'}%\n"
                      f"Средний рейтинг задач: {stats['average_puzzle_rating'] if stats['average_puzzle_rating'] is not None else 'н/д'}\n"
                      f"Последняя успешная синхронизация: {last_success}\n"
                      f"Можно возобновить: {resumable}"
                      +(f"\nОшибка/прерывание: {sync_state['last_error']}" if sync_state.get("last_error") else "")
                      +"\n"
                      f"{message}")
        else:
            result = f"Обработано: {count}. {message}"
        QMessageBox.information(self, "История задач Lichess", result)
        if self.lichess and (self.lichess.puzzle_username or self.lichess.username):
            puzzle_username = self.lichess.puzzle_username or self.lichess.username
            self.show_puzzle_history(f"lichess:{puzzle_username.strip().lower()}")

    def show_puzzle_history(self, account_id: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Моя история задач Lichess · локально")
        dialog.resize(850, 540)
        layout = QHBoxLayout(dialog)
        history = QListWidget()
        detail = QVBoxLayout()
        board = BoardView(chess.Board(), dialog)
        board.set_interaction_enabled(False)
        solution = QLabel("Выберите попытку для офлайн-просмотра.")
        solution.setWordWrap(True)
        detail.addWidget(board, 1)
        detail.addWidget(solution)
        layout.addWidget(history, 1)
        detail_widget = QWidget(dialog)
        detail_widget.setLayout(detail)
        layout.addWidget(detail_widget, 2)
        attempts = self.game_repository.puzzle_history(account_id, limit=500)
        import json
        for attempt in attempts:
            date = datetime.fromtimestamp(attempt["attempt_date_ms"] / 1000).strftime("%Y-%m-%d %H:%M")
            themes = ", ".join(json.loads(attempt["themes_json"]))
            outcome = "Решено" if attempt["win"] else "Не решено"
            item = QListWidgetItem(f"{date} · {outcome} · {attempt['puzzle_rating'] or 'без рейтинга'} · {themes}")
            item.setData(Qt.ItemDataRole.UserRole, attempt)
            history.addItem(item)
        def select_attempt(row: int) -> None:
            if row < 0:
                return
            attempt = history.item(row).data(Qt.ItemDataRole.UserRole)
            try:
                position = chess.Board(attempt["fen"])
                board.set_position(position)
                moves = json.loads(attempt["solution_json"])
                san = []
                for uci in moves:
                    move = chess.Move.from_uci(uci)
                    san.append(position.san(move))
                    position.push(move)
                solution.setText(f"Позиция FEN: {attempt['fen']}\n"+
                                 "Решение (офлайн, без отправки результата): " + " ".join(san))
            except (ValueError, chess.InvalidMoveError):
                solution.setText("Не удалось разобрать позицию или решение этой задачи.")
        history.currentRowChanged.connect(select_attempt)
        if attempts:
            history.setCurrentRow(0)
        else:
            solution.setText("История задач пуста.")
        dialog.exec()

    def start_local_game(self) -> None:
        self.online_session = None
        self._review_session = None
        self.board.clear_annotations()
        self.controller.new_game()
        self._local_board_mode = "free"
        self.controller.set_premove_training(False)
        self.local_mode_combo.blockSignals(True)
        self.local_mode_combo.setCurrentIndex(self.local_mode_combo.findData("free"))
        self.local_mode_combo.blockSignals(False)
        self._screen_mode = "local"
        self.screen_title.setText("Локальная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Новая свободная партия. Можно ходить за обе стороны.")

    def _local_board_mode_changed(self, _index: int) -> None:
        if self._screen_mode != "local":
            return
        mode = self.local_mode_combo.currentData()
        if mode not in {"free", "premove_training"} or mode == self._local_board_mode:
            return
        self._local_board_mode = mode
        discarded = self.controller.set_premove_training(mode == "premove_training")
        self.board.set_premove(None, self.controller.player_color)
        if discarded:
            message = "Режим изменён; ожидавшийся предход снят, история ходов сохранена."
        elif mode == "free":
            message = "Свободная доска включена. История ходов сохранена."
        else:
            message = "Тренировка предходов включена. История ходов сохранена."
        self.refresh(message)

    def resume_current_game(self) -> None:
        game_id = self._selected_live_game_id
        session = self.online_sessions.get(game_id) if game_id else None
        if not session:
            return
        if session is not self.online_session:
            self.board.clear_annotations()
        self.online_session = session
        self._review_session = None
        self.online_view_ply = None
        self._screen_mode = "online"
        self.screen_title.setText("Активная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Возврат к текущей партии.")

    def _show_result(self, session: LichessGameSession, saved: bool, detail: str | None = None) -> None:
        self._screen_mode = "result"
        self.screen_title.setText("Партия завершена")
        outcome = _result_label(session.color, session.winner, session.status)
        self.result_summary.setText(
            f"{outcome}\nСоперник: {session.opponent}\nСтатус Lichess: {session.status}"
        )
        write_pending = self.game_repository.has_pending_write(session.game_id)
        stored = (None if write_pending or session.final_sync_pending
                  else self.game_repository.get_game(session.game_id))
        if session.final_sync_pending:
            saved = False
            saved_label = "Результат получен; ожидаем подтверждённую финальную историю от Lichess."
        elif write_pending:
            saved = False
            saved_label = "Финальная история подтверждена; сохраняем партию локально…"
            QTimer.singleShot(80, lambda game_id=session.game_id: self._poll_result_write(game_id))
        else:
            saved = bool(saved and stored and stored["moves"] == session.moves
                         and stored["status"] == session.status
                         and not stored.get("final_sync_pending", 0))
            saved_label = ("Партия сохранена локально." if saved else
                           f"Не удалось подтвердить сохранение локальной партии: {detail or self.game_repository.write_error(session.game_id) or 'ошибка записи'}")
        self.result_saved_label.setText(
            saved_label
        )
        self.result_saved_label.setStyleSheet("color: #39704a;" if saved else "color: #9b3434;")
        self.result_review_button.setEnabled(bool(session.moves))
        self.result_export_button.setEnabled(
            not session.final_sync_pending and not write_pending
            and bool(self.game_repository.export_pgn(session.game_id))
        )
        self.screen_stack.setCurrentWidget(self.result_page)

    def _poll_result_write(self, game_id: str) -> None:
        session = self.online_sessions.get(game_id)
        if (not session or session.final_sync_pending or self._screen_mode != "result"
                or game_id != self._selected_live_game_id):
            return
        if self.game_repository.has_pending_write(game_id):
            QTimer.singleShot(80, lambda: self._poll_result_write(game_id))
            return
        self._show_result(session, True)

    def review_finished_game(self) -> None:
        session = self.online_session
        if not session or session.status in {"started", "playing", "created"}:
            return
        self._review_session = session
        self._screen_mode = "review"
        self.screen_title.setText("Просмотр завершённой партии")
        self.online_view_ply = None
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Просмотр сохранённой партии (только чтение).")

    def export_current_pgn(self) -> None:
        session = self.online_session
        if not session:
            return
        pgn = self.game_repository.export_pgn(session.game_id)
        if not pgn:
            QMessageBox.information(self, "Экспорт PGN", "Для этой партии пока нет сохранённого PGN.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт PGN", f"{session.game_id}.pgn", "PGN (*.pgn)")
        if path:
            Path(path).write_text(pgn + "\n", encoding="utf-8")

    def _apply_preferences(self) -> None:
        self.board.set_flipped(self.settings.value("board_flipped", False, type=bool))
        self.board.set_show_legal_moves(self.settings.value("show_legal_moves", True, type=bool))
        self.board.set_show_last_move(self.settings.value("show_last_move", True, type=bool))
        self.board.set_annotations_enabled(
            self.settings.value("annotations_enabled", True, type=bool)
        )
        allow_premoves = self.settings.value("allow_premoves", True, type=bool)
        self.board.set_premove_allowed(allow_premoves)
        if not allow_premoves:
            self.controller.cancel_premove()
            if self.online_session:
                self.online_session.premove = None

    def on_move_intent(self, move: chess.Move) -> None:
        if self.online_session is not None:
            session = self.online_session
            if self.online_view_ply is not None or not session.begin_submission(move):
                self.refresh("Ход недоступен: проверьте актуальную позицию.")
                return
            self.lichess.send_move(session.game_id, move.uci(), session.pending_request_id)
            self.refresh(f"Отправляем ход {move.uci()}; ожидаем подтверждения Lichess.")
            return
        previous = tuple(self.controller.board.move_stack)
        if self.controller.apply_move_intent(move):
            if tuple(self.controller.board.move_stack) != previous:
                self.board.clear_annotations()
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

    def undo_local_last_move(self) -> None:
        if self.online_session is not None:
            return
        if self.controller.undo_last_move():
            self.board.clear_annotations()
            self.refresh("Последний ход локальной партии отменён.")
        else:
            self.refresh("В локальной партии пока нет хода для отмены.")

    def apply_opponent_move(self) -> None:
        if self._local_board_mode != "premove_training" or not self.controller.premove_training:
            return
        raw = self.opponent_move.currentData()
        if not raw:
            return
        move = chess.Move.from_uci(raw)
        previous = tuple(self.controller.board.move_stack)
        _, message = self.controller.apply_test_opponent_move(move)
        if tuple(self.controller.board.move_stack) != previous:
            self.board.clear_annotations()
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
            if not self.settings.value("allow_premoves", True, type=bool):
                self.cancel_premove()
            self.refresh("Настройки сохранены.")

    def connect_lichess(self) -> None:
        if not self.lichess:
            QMessageBox.warning(self, "Lichess", "Windows Credential Manager недоступен. Установите зависимости проекта.")
            return
        if getattr(self, "_restore_retry_ready", False) and self.lichess.token:
            self._restore_retry_ready = False
            self.connect_button.setEnabled(False)
            self.lichess.restore()
            return
        self.lichess.connect_account()

    def disconnect_lichess(self) -> None:
        if self.lichess:
            self.lichess.disconnect_account()

    def show_active_games(self) -> None:
        if not self._online_actions_allowed():
            self.home_status.setText("Подключите Lichess, чтобы запросить активные партии.")
            return
        dialog = ActiveGamesDialog(self)
        self._active_games_dialog = dialog
        dialog.refresh_requested.connect(self._request_active_games)
        dialog.open_requested.connect(self.open_active_game)
        dialog.refresh_requested.emit()
        dialog.exec()
        self._active_games_dialog = None

    def _request_active_games(self) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if not dialog or not self.lichess:
            return
        dialog.set_loading()
        self.lichess.fetch_active_games()

    def _active_games_received(self, games: list[dict]) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_games(games)

    def _active_games_error(self, message: str) -> None:
        dialog = getattr(self, "_active_games_dialog", None)
        if dialog and dialog.isVisible():
            dialog.set_error(message)

    def open_active_game(self, game: dict) -> None:
        game_id = game.get("gameId")
        if not game_id or not self._online_actions_allowed():
            return
        session = self.online_sessions.get(str(game_id))
        if session is not None and session.status not in {"created", "started", "playing"}:
            QMessageBox.information(self, "Активная партия", "Lichess сообщает, что эта партия уже завершена.")
            return
        if session is None:
            session = LichessGameSession(str(game_id), self.game_repository)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            opponent = game.get("opponent") or {}
            session.opponent = opponent.get("username") or opponent.get("id") or "Соперник"
            session.status = "started"
            session.clock_control = str(game.get("speed") or "-")
            self.online_sessions[session.game_id] = session
        else:
            # A queued premove belongs to an earlier visible session. Viewing a
            # game from the account list must never submit that stale intent.
            session.premove = None
        if session is not self.online_session:
            self.board.clear_annotations()
        self.online_session = session
        self._selected_live_game_id = session.game_id
        self._review_session = None
        self.online_view_ply = None
        self._screen_mode = "online"
        self.screen_title.setText("Активная партия")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Открываем выбранную активную партию; позицию подтвердит поток Lichess.")
        self.lichess.start_game(session.game_id)

    def _lichess_status(self, status: str) -> None:
        self.connection.setText("●  " + status)
        connected = bool(self._lichess_connected and not self.offline_mode)
        if status == "Не подключено":
            self._lichess_connected = False
            for button in (self.friend_link_button, self.username_button, self.ai_button):
                button.setVisible(False)
            self.active_games_button.setEnabled(False)
        retry_status = status.startswith("Ограничение Lichess (429)")
        if status == "Проверяем подключение…":
            self.connect_button.setEnabled(False)
        elif retry_status:
            self.connect_button.setEnabled(False)
            self._restore_retry_ready = False
            import re
            match = re.search(r"через (\d+) с", status)
            delay = max(1, int(match.group(1))) if match else 60
            QTimer.singleShot(delay * 1000, self._enable_restore_retry)
            QTimer.singleShot(delay * 1000, self._refresh_online_controls)
        elif status in {"Не подключено", "Ошибка подключения"}:
            self._restore_retry_ready = False
            self.connect_button.setText("Подключить Lichess")
            self.connect_button.setEnabled(True)
        elif status.startswith("Подключено"):
            self._restore_retry_ready = False
            self.connect_button.setText("Подключить Lichess")
            self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(connected)
        online_allowed = connected and self._lichess_rate_limit_remaining() == 0
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setEnabled(online_allowed)
        for button in (self.home_ai_button, self.home_friend_button, self.home_username_button):
            button.setEnabled(online_allowed)
        self.active_games_button.setEnabled(online_allowed)
        self.home_archive_button.setEnabled(online_allowed)
        self.home_puzzles_button.setEnabled(online_allowed)
        if status.startswith("Ограничение Lichess"):
            self.home_status.setText(status + ". Автоматические запросы приостановлены.")

    def _enable_restore_retry(self) -> None:
        if (self.lichess and self.lichess.token and not self._lichess_connected
                and not self.offline_mode):
            self._restore_retry_ready = True
            self.connect_button.setText("Повторить подключение")
            self.connect_button.setEnabled(True)

    def _lichess_rate_limit_remaining(self) -> int:
        if not self.lichess:
            return 0
        return max(0, int(getattr(self.lichess.client, "rate_limit_remaining", 0)))

    def _online_actions_allowed(self) -> bool:
        return bool(self.lichess and not self.offline_mode and self._lichess_connected
                    and self._lichess_rate_limit_remaining() == 0)

    def _refresh_online_controls(self) -> None:
        if (not self.lichess or self.offline_mode or not self._lichess_connected):
            return
        remaining = self._lichess_rate_limit_remaining()
        if remaining > 0:
            QTimer.singleShot(remaining * 1000, self._refresh_online_controls)
            return
        for button in (self.friend_link_button, self.username_button, self.ai_button,
                       self.home_ai_button, self.home_friend_button, self.home_username_button,
                       self.active_games_button, self.home_archive_button, self.home_puzzles_button):
            button.setEnabled(True)

    def _lichess_authenticated(self, username: str) -> None:
        self._lichess_connected = True
        self.connection.setText(f"●  Подключено: {username}")
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self._refresh_online_controls()
        for button in (self.friend_link_button, self.username_button, self.ai_button):
            button.setVisible(self.online_session is None)
        self._resume_pending_final_syncs()

    def _resume_pending_final_syncs(self) -> None:
        """Resume persisted terminal games without taking over the visible screen."""
        for row in self.game_repository.list_pending_final_sync():
            game_id = row["game_id"]
            session = self.online_sessions.get(game_id)
            if session is None:
                session = LichessGameSession(game_id, self.game_repository)
                session.initial_fen = row["initial_fen"]
                session.moves = row["moves"]
                session.color = chess.WHITE if row["color"] == "white" else chess.BLACK
                session.opponent = row["opponent"]
                session.rated = bool(row["rated"])
                session.clock_control = row["clock"]
                session.status = row["status"]
                session.winner = row["winner"]
                session.white_player = row["white_player"]
                session.black_player = row["black_player"]
                session.started_at = row["started_at"]
                session.final_sync_pending = True
                session._rebuild_board()
                self.online_sessions[game_id] = session
            self.lichess.mark_game_finished(game_id)
            self.lichess.start_game(game_id)

    def _lichess_error(self, message: str) -> None:
        self._awaiting_game_start = False
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
            "offer": ("Запрашиваем возврат хода у Lichess AI…" if session.is_ai_opponent
                      else "Отправляем предложение вернуть ход…"),
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
                "offer": ("Запрос отправлен; ожидаем подтверждённую позицию Lichess AI…"
                          if session.is_ai_opponent else
                          "Предложение возврата отправлено; ждём ответа соперника."),
                "accept": "Предложение принято; ждём обновления позиции от Lichess…",
                "decline": "Отказ отправлен; ждём подтверждения от Lichess…",
            }
            message = messages.get(action, "Запрос возврата отправлен; ждём Lichess.")
        else:
            message = {
                400: "Lichess отклонил запрос возврата хода (400). Позиция не изменена.",
                403: "Lichess запретил возврат хода (403). Позиция не изменена.",
                404: "Партия не найдена; позиция не изменена.",
                429: "Lichess временно ограничил запросы (429). Позиция не изменена; дождитесь окончания паузы перед новым действием.",
            }.get(status, "Не удалось отправить запрос возврата хода. Позиция не изменена.")
        self.refresh(message)

    def open_friend_game(self) -> None:
        if not self._online_actions_allowed():
            return
        dialog = OnlineGameDialog("Партия с другом по ссылке", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText("Создаём вызов и присоединяем вас…")
        self.home_status.setText("Создаём вызов и присоединяем вас…")
        self.lichess.open_friend_game(color, limit, increment, rated)

    def challenge_username(self) -> None:
        if not self._online_actions_allowed():
            return
        username, ok = QInputDialog.getText(self, "Вызов игрока", "Имя пользователя Lichess:")
        if not ok or not username.strip():
            return
        dialog = OnlineGameDialog("Контроль времени", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, _, rated = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText(f"Ожидаем ответ от {username.strip()}…")
        self.home_status.setText(f"Ожидаем ответ от {username.strip()}…")
        self.lichess.challenge_username(username.strip(), color, limit, increment, rated)

    def play_ai(self) -> None:
        if not self._online_actions_allowed():
            return
        dialog = OnlineGameDialog("Игра против Lichess AI", include_level=True, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        color, limit, increment, level, _ = dialog.selection()
        self._awaiting_game_start = True
        self.game_status.setText("Создаём партию против Lichess AI…")
        self.home_status.setText("Создаём партию против Lichess AI…")
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
        if not can_cancel and any(word in message.casefold() for word in ("отклонил", "отменён", "отменено", "завершено")):
            self._awaiting_game_start = False
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
            self._awaiting_game_start = False
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
            previous = self.online_sessions.get(game_id)
            session = previous or LichessGameSession(game_id, self.game_repository)
            color = game.get("color")
            if color in {"white", "black"}:
                session.color = chess.WHITE if color == "white" else chess.BLACK
            session.status = "started"
            opponent = game.get("opponent", {})
            session.opponent = opponent.get("username", "Соперник")
            self.online_sessions[game_id] = session
            if (not session.is_ai_opponent
                    and getattr(self, "_analysis_stop", None) is not None):
                self._analysis_stop.set()
            if self._awaiting_game_start and previous is None:
                self._awaiting_game_start = False
                self.board.clear_annotations()
                self._selected_live_game_id = game_id
                self.online_session = session
                self._review_session = None
                self.online_view_ply = None
                self._screen_mode = "online"
                self.screen_title.setText("Активная партия")
                self.screen_stack.setCurrentWidget(self.game_page)
                self.refresh(f"Партия началась против {session.opponent}.")
        elif kind == "gameFinish":
            game_id = event.get("game", {}).get("gameId")
            if game_id in self.online_sessions:
                game = event.get("game", {})
                game_status = game.get("status", {})
                status = game_status.get("name", "unknownFinish") if isinstance(game_status, dict) else str(game_status)
                session = self.online_sessions[game_id]
                error = None
                if session.status in {"started", "playing", "created"}:
                    try:
                        session.finish(status, game.get("winner"))
                    except Exception as exc:
                        error = str(exc)
                        session.status = status
                        session.winner = game.get("winner")
                if session.final_sync_pending:
                    self.lichess.mark_game_finished(game_id)
                    self.lichess.start_game(game_id)
                if (game_id == self._selected_live_game_id
                        and self.online_session is session
                        and self._screen_mode == "online"):
                    self._show_result(session, error is None, error)
                elif game_id == self._selected_live_game_id and self._screen_mode == "home":
                    self.show_home()
                    if error:
                        self.home_status.setText(
                            f"Партия завершена, локальная запись не подтверждена: {error}"
                        )
        elif kind == "challenge":
            if self._screen_mode == "home":
                self.home_status.setText("Получено или обновлено приглашение Lichess.")

    def _lichess_game_event(self, game_id: str, event: dict) -> None:
        session = self.online_sessions.get(game_id)
        if session is None:
            session = LichessGameSession(game_id, self.game_repository)
            self.online_sessions[game_id] = session
        # Finish is terminal. A buffered or reordered active state must never
        # reopen the session or overwrite the saved result.
        if (session.status not in {"created", "started", "playing"}
                and not session.final_sync_pending
                and event.get("type") in {"gameState", "gameFull"}):
            return
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
            if (game_id == self._selected_live_game_id and self.online_session is session
                    and self._screen_mode == "online"):
                self.refresh("Соперник временно отключился; ждём переподключения.")
            return
        previous_moves = tuple(session.moves.split())
        previous_view_ply = self.online_view_ply if (
            game_id == self._selected_live_game_id and self.online_session is session
        ) else None
        try:
            session.apply_event(event)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError) as exc:
            self._lichess_error(f"Не удалось синхронизировать позицию Lichess: {exc}")
            return
        except Exception as exc:
            if (game_id == self._selected_live_game_id and self.online_session is session
                    and self._screen_mode == "online"):
                if session.status not in {"started", "playing", "created"}:
                    self._show_result(session, False, str(exc))
                else:
                    self.refresh(f"Позиция обновлена с Lichess, но локальная запись не удалась: {exc}")
            return
        if (session.status in {"started", "playing"} and not session.is_ai_opponent
                and getattr(self, "_analysis_stop", None) is not None):
            self._analysis_stop.set()
        had_premove = session.premove is not None
        current_moves = tuple(session.moves.split())
        takeback_confirmed = (len(current_moves) < len(previous_moves)
                              and previous_moves[:len(current_moves)] == current_moves)
        is_selected_live = (
            game_id == self._selected_live_game_id
            and self.online_session is session
            and self._screen_mode == "online"
        )
        if is_selected_live and current_moves != previous_moves:
            # Only the selected game's authoritative line can clear current
            # marks. A duplicate/clock event or background game must not.
            self.board.clear_annotations()
        if is_selected_live:
            if previous_view_ply is not None:
                self.online_view_ply = min(previous_view_ply, len(session.board.move_stack))
                if self.online_view_ply == len(session.board.move_stack):
                    self.online_view_ply = None
            if session.status not in {"started", "playing", "created"}:
                self._show_result(session, True)
            else:
                message = "Возврат хода подтверждён Lichess." if takeback_confirmed else None
                if previous_view_ply is not None and takeback_confirmed:
                    message = "Возврат подтверждён; просмотр ограничен новой историей."
                self.refresh(message)
        elif (game_id == self._selected_live_game_id and self.online_session is session
              and self._screen_mode == "result" and not session.final_sync_pending):
            # The terminal result screen stays open while its saved history is reconciled.
            self._show_result(session, True)
        elif (game_id == self._selected_live_game_id and self._screen_mode == "home"
              and session.status not in {"started", "playing", "created"}):
            self.show_home()
        premove = session.take_ready_premove() if is_selected_live else None
        if premove and session.begin_submission(premove):
            self.lichess.send_move(game_id, premove.uci(), session.pending_request_id)
            if is_selected_live:
                self.refresh(f"Предход {premove.uci()} отправлен; ожидаем подтверждения Lichess.")
        elif had_premove and session.premove is None:
            if is_selected_live:
                self.refresh("Предход не стал легальным и снят.")

    def _refresh_online(self, message: str | None = None) -> None:
        self.local_mode_label.setVisible(False)
        self.local_mode_combo.setVisible(False)
        self.premove_title.setVisible(True)
        session = self.online_session
        board = session.board
        render_board = session.render_board
        ply = len(board.move_stack) if self.online_view_ply is None else min(self.online_view_ply, len(board.move_stack))
        display_source = render_board if self.online_view_ply is None else board
        live = self.online_view_ply is None or ply == len(board.move_stack)
        if live:
            # render_board is disposable; BoardView copies it when installing.
            shown = display_source
        else:
            shown = board.root()
            for move in board.move_stack[:ply]:
                shown.push(move)
        self.board.set_position(shown)
        self.board.set_annotation_context(session.game_id, shown)
        active = (session.status in {"started", "playing"}
                  and self._screen_mode != "review"
                  and not session.awaiting_authoritative_snapshot)
        self.board.set_interaction_enabled(live and active and not session.input_locked)
        self._apply_preferences()
        self.board.set_premove(session.premove, session.color)
        self.opponent_label.setText(f"Соперник  ·  {session.opponent}")
        self.you_label.setText("Вы  ·  " + ("белыми" if session.color == chess.WHITE else "чёрными"))
        turn = ("Синхронизация позиции…" if session.awaiting_authoritative_snapshot
                else "Ваш ход" if session.is_my_turn else "Ход соперника")
        self.turn_label.setText(turn + (" · история" if not live else ""))
        if message:
            self.game_status.setText(message)
        elif session.awaiting_authoritative_snapshot:
            self.game_status.setText(
                "Ожидаем первичную подтверждённую позицию Lichess; действия партии заблокированы."
            )
        elif session.pending_uci:
            self.game_status.setText(f"Ход {session.pending_uci} отправлен; ожидаем подтверждения Lichess…")
        elif session.takeback_requested_by_opponent:
            self.game_status.setText("Соперник предлагает вернуть ход.")
        elif session.takeback_accept_pending:
            self.game_status.setText("Возврат принят; ожидаем подтверждения позиции Lichess…")
        elif session.takeback_decline_pending:
            self.game_status.setText("Отказ отправлен; ожидаем обновления состояния партии…")
        elif session.takeback_requested_by_me:
            self.game_status.setText(
                "Запрос возврата отправлен; ждём обновления позиции Lichess AI."
                if session.is_ai_opponent else
                "Предложение возврата отправлено; ждём ответа соперника."
            )
        else:
            self.game_status.setText(f"Lichess · {session.status}")
        self._sync_online_history(board, session.game_id, ply)
        self._update_clock()
        self.history_label.setText("Актуальная позиция" if live else f"Просмотр хода {ply} из {len(board.move_stack)}")
        self.resign_button.setEnabled(active)
        self.resign_button.setVisible(active)
        self.draw_button.setEnabled(active)
        self.draw_button.setVisible(active)
        self.draw_button.setText("Принять ничью" if session.draw_offered_by_opponent else "Предложить ничью")
        self.decline_draw_button.setVisible(session.draw_offered_by_opponent)
        self.decline_draw_button.setEnabled(session.draw_offered_by_opponent)
        self.takeback_button.setVisible(active)
        self.takeback_button.setText(
            "Ожидаем ответа на возврат…" if session.takeback_requested_by_me
            else "Вернуть ход" if session.is_ai_opponent else "Предложить вернуть ход"
        )
        self.takeback_button.setToolTip(
            "Запросить серверный возврат у Lichess AI. После ответа доска изменится только по подтверждённому состоянию."
            if session.is_ai_opponent else
            "Предложить сопернику возврат хода через Lichess. Это не отмена ожидающего предхода."
        )
        self.undo_local_button.setVisible(False)
        self.takeback_button.setEnabled(
            active and bool(session.moves) and not session.input_locked
        )
        show_takeback = (session.takeback_requested_by_opponent
                         and not session.awaiting_authoritative_snapshot)
        self.accept_takeback_button.setVisible(show_takeback)
        self.decline_takeback_button.setVisible(show_takeback)
        response_enabled = (show_takeback and not session.pending_uci
                            and not session.takeback_action_pending
                            and not session.takeback_accept_pending
                            and not session.takeback_decline_pending)
        self.accept_takeback_button.setEnabled(response_enabled)
        self.decline_takeback_button.setEnabled(response_enabled)
        self.claim_draw_button.setEnabled(active and session.board.can_claim_draw())
        self.claim_draw_button.setVisible(active and session.board.can_claim_draw())
        self.leave_game_button.setEnabled(session.status not in {"started", "playing", "created"})
        self.leave_game_button.setVisible(session.status not in {"started", "playing", "created"})
        self.leave_game_button.setText("Главная")
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

    def _sync_online_history(self, board: chess.Board, game_id: str, selected_ply: int) -> None:
        """Append SAN rows for new plies; rebuild only on a new/divergent line."""
        moves = tuple(board.move_stack)
        previous = self._online_history_moves
        incremental = (
            self._online_history_game_id == game_id
            and len(moves) >= len(previous)
            and moves[:len(previous)] == previous
            and self._online_history_position is not None
        )
        unchanged = incremental and moves == previous
        scroll = self.moves.verticalScrollBar().value()
        self.moves.blockSignals(True)
        try:
            if not incremental:
                self.moves.clear()
                position = board.root()
                start_ply = 0
            elif not unchanged:
                position = self._online_history_position.copy(stack=True)
                start_ply = len(previous)
            else:
                position = None
                start_ply = len(moves)
            if position is not None:
                for index in range(start_ply, len(moves)):
                    move = moves[index]
                    ply = index + 1
                    prefix = f"{(ply + 1) // 2}. " if ply % 2 else f"{(ply + 1) // 2}... "
                    item = QListWidgetItem(prefix + position.san(move))
                    item.setData(Qt.ItemDataRole.UserRole, ply)
                    self.moves.addItem(item)
                    position.push(move)
            desired_ply = min(selected_ply, len(moves))
            current = self.moves.currentItem()
            if current is None or current.data(Qt.ItemDataRole.UserRole) != desired_ply:
                selected = next((self.moves.item(i) for i in range(self.moves.count())
                                 if self.moves.item(i).data(Qt.ItemDataRole.UserRole) == desired_ply), None)
                if selected:
                    self.moves.setCurrentItem(selected)
                elif desired_ply == 0:
                    self.moves.setCurrentRow(-1)
            self._online_history_game_id = game_id
            self._online_history_moves = moves
            if not unchanged:
                self._online_history_position = board.copy(stack=True)
        finally:
            self.moves.blockSignals(False)
        self.moves.verticalScrollBar().setValue(scroll)

    def _update_clock(self) -> None:
        session = self.online_session
        if session and self._screen_mode == "online":
            write_error = self.game_repository.write_error(session.game_id)
            if write_error:
                self.game_status.setText(f"Ошибка сохранения локальной партии: {write_error}")
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
        dialog = QDialog(self)
        dialog.setWindowTitle("Библиотека партий")
        dialog.resize(680, 520)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Поиск по сопернику, игроку, ID или статусу")
        layout.addWidget(search)
        games_list = QListWidget()
        layout.addWidget(games_list, 1)
        page_row = QHBoxLayout()
        previous = QPushButton("← Назад")
        page_label = QLabel()
        following = QPushButton("Вперёд →")
        page_row.addWidget(previous)
        page_row.addWidget(page_label, 1)
        page_row.addWidget(following)
        layout.addLayout(page_row)
        note_row = QHBoxLayout()
        note = QLineEdit()
        note.setPlaceholderText("Заметка к начальной позиции выбранной партии")
        save_note = QPushButton("Сохранить заметку")
        note_row.addWidget(note, 1)
        note_row.addWidget(save_note)
        layout.addLayout(note_row)
        actions = QHBoxLayout()
        open_game = QPushButton("Открыть для просмотра")
        export_game = QPushButton("Экспорт PGN")
        analyze_game = QPushButton("Анализ Stockfish")
        refresh_game = QPushButton("Обновить эту партию из Lichess")
        refresh_game.setEnabled(bool(self._lichess_connected and not self.offline_mode))
        close = QPushButton("Закрыть")
        actions.addWidget(open_game)
        actions.addWidget(export_game)
        actions.addWidget(analyze_game)
        actions.addWidget(refresh_game)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        state = {"page": 0, "items": []}

        def selected_game():
            index = games_list.currentRow()
            return state["items"][index] if 0 <= index < len(state["items"]) else None

        def load_page():
            account_id = (f"lichess:{self.lichess.username.strip().lower()}"
                          if self.lichess and self.lichess.username else None)
            result = self.game_repository.search_games(
                search.text(), page=state["page"], page_size=50, completed_only=True,
                account_id=account_id)
            state["items"] = result["items"]
            games_list.clear()
            for game in result["items"]:
                view_state = (f" · просмотр/анализ недоступны ({game.get('variant') or 'variant'})"
                              if game.get("import_error") or game.get("variant", "standard") != "standard"
                              else "")
                opponent_label = game.get("account_opponent") or game["opponent"]
                games_list.addItem(f"{game['white_player']} – {game['black_player']}  ·  "
                                   f"для вас: {opponent_label}  ·  "
                                   f"{game['status']}  ·  {game['updated_at'][:10]}{view_state}")
            page_label.setText(f"Страница {state['page'] + 1} из {max(1, result['pages'])}"
                               f"  ·  {result['total']} партий")
            previous.setEnabled(state["page"] > 0)
            following.setEnabled(state["page"] + 1 < result["pages"])
        def open_selected():
            game = selected_game()
            if game:
                if self._open_saved_game(game):
                    dialog.accept()
                else:
                    QMessageBox.information(dialog, "Партия сохранена как PGN",
                        self._raw_only_game_message(game))
        def export_selected():
            game = selected_game()
            if not game:
                return
            pgn = self.game_repository.export_pgn(game["game_id"])
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Экспорт PGN", f"{game['game_id']}.pgn", "PGN (*.pgn)")
            if path and pgn is not None:
                Path(path).write_text(pgn.rstrip() + "\n", encoding="utf-8")
        def save_selected_note():
            game = selected_game()
            if not game:
                return
            try:
                current = self.game_repository.current_line(game["game_id"])
                self.game_repository.save_note(
                    game["game_id"], note.text(),
                    line_revision=current["line_revision"] if current else None,
                    ply=0 if current else None)
                note.clear()
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(dialog, "Заметка", str(exc))
        def analyze_selected():
            game = selected_game()
            if not game:
                return
            if not self._is_standard_board_game(game):
                QMessageBox.information(dialog, "Stockfish",
                    "Для этой записи доска не поддерживается. Оригинальный PGN можно экспортировать.")
                return
            if game["status"] in {"created", "started", "started_soon", "playing"}:
                QMessageBox.information(dialog, "Stockfish",
                                        "Движок доступен только после завершения партии.")
                return
            engine_path, _ = QFileDialog.getOpenFileName(
                dialog, "Выберите локальный Stockfish UCI executable", "",
                "Программы (*.exe);;Все файлы (*)")
            if engine_path:
                dialog.accept()
                self._start_game_analysis(game["game_id"], engine_path)
        def refresh_selected():
            game = selected_game()
            if not game or not self.lichess or not self._lichess_connected:
                return
            if game.get("source") != "lichess_archive":
                QMessageBox.information(dialog, "Обновить партию",
                    "Обновление доступно для партий, импортированных из текущего аккаунта Lichess.")
                return
            self.lichess.refresh_archive_game(game["game_id"])
        search.textChanged.connect(lambda _text: (state.update(page=0), load_page()))
        previous.clicked.connect(lambda: (state.update(page=max(0, state["page"] - 1)), load_page()))
        following.clicked.connect(lambda: (state.update(page=state["page"] + 1), load_page()))
        games_list.itemDoubleClicked.connect(lambda _item: open_selected())
        open_game.clicked.connect(open_selected)
        export_game.clicked.connect(export_selected)
        save_note.clicked.connect(save_selected_note)
        analyze_game.clicked.connect(analyze_selected)
        refresh_game.clicked.connect(refresh_selected)
        close.clicked.connect(dialog.reject)
        load_page()
        dialog.exec()

    def _start_game_analysis(self, game_id: str, engine_path: str) -> None:
        active_thread = self._analysis_thread
        if self._shutdown_requested:
            return
        if active_thread is not None and active_thread.is_alive():
            self.home_status.setText("Анализ уже выполняется. Дождитесь его завершения или отмените его.")
            return
        dialog = QProgressDialog("Запуск Stockfish…", "Отменить", 0, 0, self)
        dialog.setWindowTitle("Послепартийный анализ")
        dialog.setMinimumDuration(0)
        dialog.show()
        self._analysis_dialog = dialog
        stop = threading.Event()
        self._analysis_stop = stop
        dialog.canceled.connect(stop.set)
        active_human_ids = {session.game_id for session in self.online_sessions.values()
                            if session.status in {"started", "playing"}
                            and not session.is_ai_opponent}
        def work():
            try:
                result = CompletedGameAnalyzer(self.game_repository).analyze(
                    game_id, engine_path, AnalysisOptions(),
                    active_human_game_ids=active_human_ids,
                    progress=lambda done, total: self.analysis_progress.emit(game_id, done, total),
                    stop_event=stop)
                self.analysis_finished.emit(game_id, result)
            except Exception as exc:
                self.analysis_finished.emit(game_id, {"error": str(exc)})
        thread = threading.Thread(target=work, name="chess-coach-analysis", daemon=True)
        self._analysis_thread = thread
        thread.start()

    def _analysis_progress(self, game_id: str, done: int, total: int) -> None:
        if self._shutdown_requested:
            return
        if getattr(self, "_analysis_dialog", None):
            self._analysis_dialog.setLabelText(f"Анализ позиции {done} из {total}")
            self._analysis_dialog.setRange(0, total)
            self._analysis_dialog.setValue(done)

    def _analysis_finished(self, game_id: str, result: object) -> None:
        self._analysis_stop = None
        if getattr(self, "_analysis_dialog", None):
            self._analysis_dialog.close()
            self._analysis_dialog = None
        if self._shutdown_requested:
            return
        if isinstance(result, dict) and result.get("error"):
            QMessageBox.warning(self, "Stockfish", result["error"])
        else:
            count = len(result.get("assessments", [])) if isinstance(result, dict) else 0
            if count:
                dialog = QDialog(self)
                dialog.setWindowTitle("Оценка партии · белые сверху положительны")
                layout = QVBoxLayout(dialog)
                layout.addWidget(QLabel(f"Оценено позиций: {count}. Источник: "
                                        f"{result.get('source', 'Stockfish')}. Клик по графику открывает позицию."))
                graph = EvaluationGraph(result["assessments"], dialog)
                graph.selected_ply.connect(lambda ply: self._open_analysis_position(game_id, ply))
                layout.addWidget(graph)
                close = QPushButton("Закрыть")
                close.clicked.connect(dialog.accept)
                layout.addWidget(close)
                dialog.resize(720, 320)
                dialog.exec()
            else:
                QMessageBox.information(self, "Stockfish", "Оценок для этой партии нет.")

    def _open_analysis_position(self, game_id: str, ply: int) -> None:
        game = self.game_repository.get_game(game_id)
        if not game:
            return
        if not self._open_saved_game(game):
            self.home_status.setText(self._raw_only_game_message(game))
            return
        self.navigate_to(ply)

    @staticmethod
    def _is_standard_board_game(game: dict) -> bool:
        return (game.get("variant", "standard") == "standard"
                and not game.get("import_error")
                and bool(game.get("initial_fen")))

    @staticmethod
    def _raw_only_game_message(game: dict) -> str:
        variant = (game.get("variant") or "неизвестный").capitalize()
        reason = game.get("import_error") or "для этого варианта нет стандартной шахматной доски"
        return (f"Запись сохранена как исходный PGN. Вариант: {variant}. "
                f"Просмотр доски и анализ недоступны: {reason}. Экспорт PGN доступен.")

    def _open_saved_game(self, game: dict) -> bool:
        if not self._is_standard_board_game(game):
            self.home_status.setText(self._raw_only_game_message(game))
            return False
        self.board.clear_annotations()
        session = LichessGameSession(game["game_id"], self.game_repository)
        session.initial_fen = game["initial_fen"]
        session.moves = game["moves"]
        session.color = chess.WHITE if game["color"] == "white" else chess.BLACK
        session.opponent = game["opponent"]
        session.status = game["status"]
        session.winner = game["winner"]
        session.clock_control = game["clock"]
        session._rebuild_board()
        self.online_session = session
        self._review_session = session
        self.online_view_ply = None
        self._screen_mode = "review"
        self.screen_title.setText("Просмотр сохранённой партии")
        self.screen_stack.setCurrentWidget(self.game_page)
        self.refresh("Открыта локально сохранённая партия (только просмотр).")
        return True

    def leave_online_game(self) -> None:
        if self._screen_mode == "review":
            self.online_session = None
            self._review_session = None
            self.online_view_ply = None
            self.show_home()
            return
        if not self.online_session:
            return
        session = self.online_session
        if session.status in {"started", "playing"}:
            self.show_home()
            return
        self.show_home()

    def closeEvent(self, event) -> None:
        self._shutdown_requested = True
        analysis_thread = self._analysis_thread
        analysis_running = bool(analysis_thread and analysis_thread.is_alive())
        if analysis_running:
            if self._analysis_shutdown_deadline is None:
                self._analysis_shutdown_deadline = time.monotonic() + 10.0
            if self._analysis_stop is not None:
                self._analysis_stop.set()
            if self._analysis_dialog is not None:
                self._analysis_dialog.setLabelText("Останавливаем Stockfish перед закрытием приложения…")
        else:
            self._analysis_shutdown_deadline = None

        workers_stopped = True
        if self.lichess:
            close_method = self.lichess.close
            try:
                close_result = close_method(timeout=0.15)
            except TypeError:
                close_result = close_method()
            workers_stopped = close_result is not False
        analysis_stopped = not (analysis_thread and analysis_thread.is_alive())
        if not workers_stopped or not analysis_stopped or self.game_repository.has_pending_writes():
            event.ignore()
            self.home_status.setText("Завершаем фоновые операции и сохраняем подтверждённые данные. "
                                     "Окно закроется после безопасной остановки.")
            if (analysis_running and self._analysis_shutdown_deadline is not None
                    and time.monotonic() >= self._analysis_shutdown_deadline):
                self._analysis_shutdown_deadline = None
                self._close_retry_scheduled = False
                self._close_retry_timer.stop()
                self.setEnabled(True)
                self._shutdown_read_only = True
                self.setWindowTitle("Chess Coach — ожидание завершения Stockfish")
                self.home_status.setText(
                    "Stockfish не остановился за 10 секунд. Приложение ожидает завершения анализа; "
                    "действия отключены. После завершения нажмите X, чтобы закрыть окно.")
                # The service has already entered its close path. Keep the shell
                # available for the window's close control, but make the app's
                # controls clearly unavailable until the user retries closing.
                if self.centralWidget() is not None:
                    self.centralWidget().setEnabled(False)
                return
            if not self._close_retry_scheduled:
                self._close_retry_scheduled = True
                self.setEnabled(False)
                self._close_retry_timer.start()
            return
        self.game_repository.shutdown()
        if self._offline_data_dir is not None:
            self._offline_data_dir.cleanup()
            self._offline_data_dir = None
        event.accept()
        super().closeEvent(event)

    def _retry_close_after_workers(self) -> None:
        self._close_retry_scheduled = False
        self.close()


def _result_label(color: chess.Color, winner: str | None, status: str) -> str:
    if winner not in {"white", "black"}:
        if status in {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}:
            return "Ничья"
        return "Партия прервана"
    won = (winner == "white") == color
    return "Победа" if won else "Поражение"
