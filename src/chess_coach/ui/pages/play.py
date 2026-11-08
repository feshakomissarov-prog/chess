"""Mode selection view; actions are supplied by the application shell."""

from __future__ import annotations

from collections.abc import Callable

import chess
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QSettings
from PySide6.QtWidgets import (QCheckBox, QComboBox, QApplication, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)
from ...board_view import BoardView


class PlayPage(QWidget):
    """Embedded game setup form; networking and game-state ownership stay outside UI."""

    create_requested = Signal(object)
    cancel_requested = Signal()
    cancel_invitation_requested = Signal()
    active_game_requested = Signal(dict)

    _MODES = (
        ("Lichess AI", "Сыграйте против искусственного интеллекта Lichess.", "ai"),
        ("Друг по ссылке", "Создайте ссылку и сыграйте с другом.", "friend"),
        ("Вызвать игрока", "Найдите соперника на Lichess и сыграйте с ним.", "challenge"),
        ("Свободная доска", "Откройте локальную доску для игры за обе стороны.", "local"),
    )

    def __init__(self, actions: dict[str, Callable], parent=None,
                 settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        root_layout = QVBoxLayout(self)
        title = QLabel("Выберите режим игры")
        title.setObjectName("pageTitle")
        root_layout.addWidget(title)
        note = QLabel("Выберите локальную доску или сетевую партию. Текущая партия не сбрасывается.")
        note.setObjectName("secondary")
        note.setWordWrap(True)
        root_layout.addWidget(note)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("playContentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.content_scroll.setWidget(self.content)
        root_layout.addWidget(self.content_scroll, 1)
        self.mode_selector = QComboBox()
        for label, value in (("Lichess AI", "ai"), ("Друг по ссылке", "friend"),
                             ("Вызвать игрока", "challenge"),
                             ("Свободная доска", "local"),
                             # Retained only for old callers; active games are above the cards.
                             ("Активные партии", "active")):
            self.mode_selector.addItem(label, value)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Имя пользователя Lichess")
        self.color = QComboBox()
        for label, value in (("Случайный цвет", "random"), ("Белыми", "white"),
                             ("Чёрными", "black")):
            self.color.addItem(label, value)
        self.clock = QComboBox()
        for label, value in (("Без часов", (None, 0)), ("3 + 2", (180, 2)),
                             ("5 + 0", (300, 0)), ("10 + 0", (600, 0)),
                             ("15 + 10", (900, 10)), ("Свой…", ("custom", None))):
            self.clock.addItem(label, value)
        self.clock.setCurrentIndex(self.clock.findData((600, 0)))
        self.custom_minutes = QSpinBox()
        self.custom_minutes.setRange(1, 180)
        self.custom_minutes.setValue(5)
        self.custom_minutes.setSuffix(" мин")
        self.custom_minutes.setAccessibleName("Свои минуты на партию")
        self.custom_increment = QSpinBox()
        self.custom_increment.setRange(0, 180)
        self.custom_increment.setValue(0)
        self.custom_increment.setSuffix(" сек инкремент")
        self.custom_increment.setAccessibleName("Свой инкремент в секундах")
        self.custom_clock_row = QWidget()
        custom_layout = QHBoxLayout(self.custom_clock_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(self.custom_minutes)
        custom_layout.addWidget(self.custom_increment)
        self.custom_clock_row.setVisible(False)
        self.level = QComboBox()
        for value in range(1, 9):
            self.level.addItem(f"Уровень {value}", value)
        self.level.setCurrentIndex(self.level.findData(4))
        self.rated = QCheckBox("Рейтинговая партия")
        self.start_button = QPushButton("Начать свободную доску")
        self.start_button.setObjectName("primaryAction")
        self.cancel_pending_button = QPushButton("Отменить ожидание вызова")
        self.cancel_pending_button.setAccessibleName("Отменить ожидающий вызов Lichess")
        self.cancel_pending_button.setToolTip(
            "Отменяет ожидающий вызов игрока Lichess. Для других типов создания партии отмена протоколом недоступна.")
        self.cancel_pending_button.setVisible(False)
        self.cancel_pending_button.clicked.connect(self.cancel_requested.emit)
        self.form_status = QLabel("Выберите режим и параметры партии.")
        self.form_status.setObjectName("secondary")
        self.form_status.setWordWrap(True)
        form = QFrame()
        form.setObjectName("metricCard")
        form_layout = QVBoxLayout(form)
        self.form_rows: dict[str, QWidget] = {}
        self.form_rows["Режим"] = self.mode_selector
        self.mode_selector.hide()
        form_grid = QGridLayout()
        form_grid.setHorizontalSpacing(16)
        for index, (label, widget) in enumerate((
                ("Ваш цвет", self.color), ("Уровень Lichess AI", self.level),
                ("Контроль времени", self.clock), ("Соперник", self.username))):
            row_widget = QWidget()
            row = QVBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
            self.form_rows[label] = row_widget
            form_grid.addWidget(row_widget, index // 3, index % 3)
        form_layout.addLayout(form_grid)
        form_layout.addWidget(self.custom_clock_row)
        form_layout.addWidget(self.rated)
        form_layout.addWidget(self.form_status)
        self.invitation_row = QWidget()
        invitation_layout = QHBoxLayout(self.invitation_row)
        invitation_layout.setContentsMargins(0, 0, 0, 0)
        self.invitation_link = QLineEdit()
        self.invitation_link.setReadOnly(True)
        self.invitation_link.setAccessibleName("Ссылка приглашения Lichess")
        invitation_layout.addWidget(self.invitation_link, 1)
        self.copy_invitation_button = QPushButton("Копировать ссылку")
        self.copy_invitation_button.clicked.connect(self._copy_invitation)
        invitation_layout.addWidget(self.copy_invitation_button)
        self.cancel_invitation_button = QPushButton("Отменить приглашение")
        self.cancel_invitation_button.clicked.connect(self.cancel_invitation_requested.emit)
        invitation_layout.addWidget(self.cancel_invitation_button)
        self.invitation_row.setVisible(False)
        form_layout.addWidget(self.invitation_row)
        form_layout.addWidget(self.start_button)
        form_layout.addWidget(self.cancel_pending_button)
        layout.addWidget(form)
        self.active_panel = QFrame()
        self.active_panel.setObjectName("activeGamesPanel")
        active_layout = QVBoxLayout(self.active_panel)
        self.active_status = QLabel()
        self.active_status.setWordWrap(True)
        self.active_games = QListWidget()
        self.active_games.setAccessibleName("Активные партии Lichess")
        self.active_games.setMinimumHeight(112)
        self.active_games.currentItemChanged.connect(self._show_active_preview)
        preview = QWidget(self.active_panel)
        preview_row = QHBoxLayout(preview)
        preview_row.setContentsMargins(0, 0, 0, 0)
        preview_row.setSpacing(10)
        preview_row.addWidget(self.active_games, 1)
        preview_column = QVBoxLayout()
        self.active_preview_status = QLabel("Выберите партию, чтобы увидеть подтверждённую позицию.")
        self.active_preview_status.setWordWrap(True)
        self.active_preview_status.setObjectName("secondary")
        self.active_preview_board = BoardView(chess.Board(), self.active_panel)
        self.active_preview_board.setMinimumSize(0, 0)
        self.active_preview_board.setFixedSize(176, 176)
        self.active_preview_board.set_interaction_enabled(False)
        self.active_preview_board.set_annotations_enabled(False)
        self.active_preview_board.set_premove_allowed(False)
        self.active_preview_board.set_show_legal_moves(False)
        self.active_preview_board.hide()
        preview_column.addWidget(self.active_preview_status)
        preview_column.addWidget(self.active_preview_board, 0, Qt.AlignmentFlag.AlignHCenter)
        preview_row.addLayout(preview_column)
        self.active_open = QPushButton("Продолжить выбранную партию")
        self.active_open.setEnabled(False)
        self.active_open.clicked.connect(self._open_selected_active_game)
        self.active_games.currentItemChanged.connect(
            lambda item, _old: self._set_active_open_enabled(item))
        active_layout.addWidget(self.active_status)
        active_layout.addWidget(preview)
        active_layout.addWidget(self.active_open)
        self.active_panel.hide()
        layout.addWidget(self.active_panel)
        stored_mode = str(settings.value("play/last_selected_mode", "ai", type=str)) if settings else "ai"
        mode_index = self.mode_selector.findData(stored_mode)
        if mode_index < 0 or stored_mode == "active":
            mode_index = self.mode_selector.findData("ai")
        self.mode_selector.setCurrentIndex(mode_index)
        self._last_mode = str(self.mode_selector.currentData() or "ai")
        self._restore_mode_preferences(self._last_mode)
        self.mode_selector.currentIndexChanged.connect(self._mode_changed)
        self.clock.currentIndexChanged.connect(self._clock_changed)
        self.color.currentIndexChanged.connect(lambda *_: self._save_mode_preferences())
        self.level.currentIndexChanged.connect(lambda *_: self._save_mode_preferences())
        self.rated.toggled.connect(lambda *_: self._save_mode_preferences())
        self.username.textChanged.connect(lambda *_: self._save_mode_preferences())
        self.clock.currentIndexChanged.connect(self._update_form)
        self.start_button.clicked.connect(self._emit_create)
        self._request_pending = False
        self._update_form()
        self._set_mode_description()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.buttons: dict[str, QPushButton] = {}
        self.cards: list[QFrame] = []
        for index, (heading, description, key) in enumerate(self._MODES):
            card = QPushButton()
            card.setObjectName("playModeCard")
            card.setProperty("playMode", key)
            card.setCheckable(True)
            card.setAccessibleName(heading)
            card.setToolTip(description)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 12, 18, 12)
            title_label = QLabel(heading)
            title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
            card_layout.addWidget(title_label)
            description_label = QLabel(description)
            description_label.setObjectName("secondary")
            description_label.setWordWrap(True)
            card_layout.addWidget(description_label)
            card.clicked.connect(lambda _checked=False, key=key: self.select_mode(key))
            self.buttons[key] = card
            self.cards.append(card)
        self._columns = 2
        self._place_cards()
        layout.addLayout(self.grid)
        for card in self.cards:
            card.setVisible(True)
        self._update_form()
        layout.removeWidget(form)
        layout.removeWidget(self.active_panel)
        layout.removeItem(self.grid)
        layout.insertWidget(0, self.active_panel)
        layout.insertLayout(1, self.grid)
        layout.insertWidget(2, form)
        for card in self.cards:
            card.setMinimumHeight(132)
        layout.addStretch(1)

    def select_mode(self, mode: str) -> None:
        """Select a mode only; network/local actions require the explicit form button."""
        index = self.mode_selector.findData(str(mode))
        if index >= 0 and self.mode_selector.currentIndex() != index:
            self.mode_selector.setCurrentIndex(index)
        for key, button in getattr(self, "buttons", {}).items():
            button.setChecked(key == mode)

    def _mode_changed(self, *_args) -> None:
        previous = self._last_mode
        mode = str(self.mode_selector.currentData() or "ai")
        if previous != "active":
            self._save_mode_preferences(previous)
        if mode != "active":
            self._last_mode = mode
            if self.settings is not None:
                self.settings.setValue("play/last_selected_mode", mode)
            self._restore_mode_preferences(mode)
        else:
            self._last_mode = mode
        self._update_form()
        self._set_mode_description()

    def _set_mode_description(self) -> None:
        if self._request_pending or self.invitation_row.isVisible():
            return
        mode = str(self.mode_selector.currentData() or "ai")
        self.form_status.setText({
            "ai": "Выберите цвет, контроль времени и уровень Lichess AI.",
            "friend": "Настройте ссылку-приглашение и подтвердите создание.",
            "challenge": "Введите имя игрока Lichess и параметры вызова.",
            "local": "Локальная партия работает без сети и часов.",
            "active": "Выберите подтверждённую активную партию выше.",
        }.get(mode, "Выберите режим и параметры партии."))

    def _copy_invitation(self) -> None:
        url = self.invitation_link.text().strip()
        if not url:
            return
        QApplication.clipboard().setText(url)
        self.set_status("Ссылка скопирована в буфер обмена.")

    def _save_mode_preferences(self, mode: str | None = None) -> None:
        if self.settings is None:
            return
        mode = str(mode or self.mode_selector.currentData() or self._last_mode)
        if mode not in {"ai", "friend", "challenge"}:
            return
        prefix = f"play/modes/{mode}/"
        self.settings.setValue(prefix + "color", str(self.color.currentData() or "random"))
        self.settings.setValue(prefix + "clock_index", self.clock.currentIndex())
        self.settings.setValue(prefix + "custom_minutes", self.custom_minutes.value())
        self.settings.setValue(prefix + "custom_increment", self.custom_increment.value())
        self.settings.setValue(prefix + "rated", self.rated.isChecked())
        if mode == "ai":
            self.settings.setValue(prefix + "level", int(self.level.currentData() or 1))
        if mode == "challenge":
            self.settings.setValue(prefix + "username", self.username.text())

    def _restore_mode_preferences(self, mode: str) -> None:
        if self.settings is None or mode not in {"ai", "friend", "challenge"}:
            return
        prefix = f"play/modes/{mode}/"
        blockers = [QSignalBlocker(widget) for widget in (
            self.color, self.clock, self.custom_minutes, self.custom_increment,
            self.rated, self.level, self.username)]
        color_index = self.color.findData(str(self.settings.value(prefix + "color", "random")))
        self.color.setCurrentIndex(color_index if color_index >= 0 else 0)
        clock_index = self.settings.value(prefix + "clock_index", self.clock.currentIndex(), type=int)
        if 0 <= clock_index < self.clock.count():
            self.clock.setCurrentIndex(clock_index)
        self.custom_minutes.setValue(self.settings.value(prefix + "custom_minutes", 5, type=int))
        self.custom_increment.setValue(self.settings.value(prefix + "custom_increment", 0, type=int))
        self.rated.setChecked(self.settings.value(prefix + "rated", False, type=bool))
        level_index = self.level.findData(self.settings.value(prefix + "level", 4, type=int))
        if level_index >= 0:
            self.level.setCurrentIndex(level_index)
        if mode == "challenge":
            self.username.setText(str(self.settings.value(prefix + "username", "", type=str)))
        del blockers

    def _clock_changed(self, *_args) -> None:
        mode = str(self.mode_selector.currentData() or "ai")
        if mode != "active":
            self._save_mode_preferences(mode)

    def _update_form(self, *_args) -> None:
        mode = str(self.mode_selector.currentData() or "local")
        for key, button in getattr(self, "buttons", {}).items():
            button.setChecked(key == mode)
        for card in getattr(self, "cards", ()):
            card.setVisible(True)
        online = mode in {"ai", "friend", "challenge"}
        self.color.setVisible(online)
        self.clock.setVisible(online)
        self.form_rows["Ваш цвет"].setVisible(online)
        self.form_rows["Контроль времени"].setVisible(online)
        self.form_rows["Уровень Lichess AI"].setVisible(mode == "ai")
        self.form_rows["Соперник"].setVisible(mode == "challenge")
        self.level.setVisible(mode == "ai")
        self.rated.setVisible(mode in {"friend", "challenge"})
        self.username.setVisible(mode == "challenge")
        self.custom_clock_row.setVisible(
            online and isinstance(self.clock.currentData(), tuple)
            and self.clock.currentData()[0] == "custom")
        labels = {
            "local": "Начать свободную доску", "ai": "Играть против Lichess AI",
            "friend": "Создать приглашение", "challenge": "Вызвать соперника",
            "active": "Показать активные партии",
        }
        self.start_button.setText(labels.get(mode, "Начать"))

    def _emit_create(self) -> None:
        mode = str(self.mode_selector.currentData() or "local")
        if self._request_pending:
            return
        clock = self.clock.currentData()
        if isinstance(clock, tuple) and clock[0] == "custom":
            limit = int(self.custom_minutes.value()) * 60
            increment = int(self.custom_increment.value())
        else:
            limit, increment = clock
        online = mode in {"ai", "friend", "challenge"}
        if online and limit is not None and (int(limit) <= 0 or int(limit) > 10_800):
            self.set_status("Проверьте контроль: начальное время должно быть от 1 секунды до 3 часов.")
            return
        if online and not 0 <= int(increment or 0) <= 180:
            self.set_status("Инкремент должен быть от 0 до 180 секунд.")
            return
        if online:
            self.set_request_pending(True, cancellable=mode in {"friend", "challenge"})
        self.create_requested.emit({
            "mode": mode,
            "color": self.color.currentData(), "clock_limit": limit,
            "clock_increment": increment, "level": int(self.level.currentData()),
            "rated": self.rated.isChecked(), "username": self.username.text().strip(),
        })

    def set_active_games(self, games=(), *, status: str = "") -> None:
        selected_game_id = ""
        current = self.active_games.currentItem()
        if current is not None:
            selected_game_id = str((current.data(Qt.ItemDataRole.UserRole) or {}).get("gameId") or "")
        self.active_games.clear()
        selected_index = -1
        for game in games:
            if not game.get("gameId"):
                continue
            opponent = game.get("opponent") or {}
            name = (opponent.get("username") or opponent.get("id")
                    or (f"Lichess AI · уровень {opponent.get('ai')}"
                        if game.get("source") == "ai" and opponent.get("ai") else None)
                    or "Неизвестный соперник")
            color = {"white": "белые", "black": "чёрные"}.get(game.get("color"), "цвет неизвестен")
            clock = game.get("clock") or game.get("speed") or "контроль не указан"
            item = QListWidgetItem(f"{name} · {color} · {clock}")
            item.setData(Qt.ItemDataRole.UserRole, dict(game))
            self.active_games.addItem(item)
            if str(game.get("gameId")) == selected_game_id:
                selected_index = self.active_games.count() - 1
        if selected_index >= 0:
            self.active_games.setCurrentRow(selected_index)
        elif self.active_games.count():
            self.active_games.setCurrentRow(0)
        else:
            self._clear_active_preview("Нет подтверждённой позиции активной партии.")
        self.active_status.setText(status or (
            "Выберите партию для продолжения." if self.active_games.count()
            else "Активных партий нет."))
        self.active_panel.setVisible(self.active_games.count() > 0)
        self._update_form()

    def _show_active_preview(self, item, _previous=None) -> None:
        game = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(game, dict):
            self._clear_active_preview("Выберите партию, чтобы увидеть подтверждённую позицию.")
            return
        fen = game.get("fen")
        try:
            board = chess.Board(str(fen)) if fen else None
            if board is not None and not board.is_valid():
                board = None
        except (TypeError, ValueError):
            board = None
        if board is None:
            self._clear_active_preview("Позиция ещё не подтверждена сервером.")
            return
        color = game.get("color")
        self.active_preview_board.set_position(board)
        self.active_preview_board.set_flipped(color == "black")
        self.active_preview_board.show()
        self.active_open.setEnabled(True)
        self.active_preview_status.setText(
            "Подтверждённая позиция · " + {"white": "вы играете белыми",
                                            "black": "вы играете чёрными"}.get(
                                                color, "цвет неизвестен"))

    def _clear_active_preview(self, message: str) -> None:
        self.active_preview_board.hide()
        self.active_preview_status.setText(str(message))
        self.active_open.setEnabled(False)

    def _set_active_open_enabled(self, item) -> None:
        if item is None:
            self.active_open.setEnabled(False)
            return
        game = item.data(Qt.ItemDataRole.UserRole)
        self.active_open.setEnabled(bool(isinstance(game, dict) and self._valid_fen(game.get("fen"))))

    @staticmethod
    def _valid_fen(fen) -> bool:
        try:
            board = chess.Board(str(fen)) if fen else None
            return bool(board is not None and board.is_valid())
        except (TypeError, ValueError):
            return False

    def _open_selected_active_game(self) -> None:
        item = self.active_games.currentItem()
        game = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(game, dict) and self._valid_fen(game.get("fen")):
            self.active_game_requested.emit(game)

    def set_status(self, text: str) -> None:
        self.form_status.setText(str(text))

    def set_request_pending(self, pending: bool, *, cancellable: bool = False) -> None:
        self._request_pending = bool(pending)
        self.start_button.setEnabled(not self._request_pending)
        self.cancel_pending_button.setVisible(self._request_pending and bool(cancellable))
        self.cancel_pending_button.setEnabled(self._request_pending and bool(cancellable))

    def show_invitation(self, url: str) -> None:
        self.invitation_link.setText(str(url))
        self.invitation_row.setVisible(bool(url))
        self.cancel_invitation_button.setEnabled(bool(url))

    def clear_invitation(self) -> None:
        self.invitation_link.clear()
        self.invitation_row.setVisible(False)

    def layout_modes(self, available_width: int) -> None:
        columns = 1 if int(available_width) < 1100 else 2
        if columns == self._columns:
            return
        self._columns = columns
        self._place_cards()

    def _place_cards(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // self._columns, index % self._columns)
