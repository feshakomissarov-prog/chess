"""Mode selection view; actions are supplied by the application shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QApplication, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget)


class PlayPage(QWidget):
    """Embedded game setup form; networking and game-state ownership stay outside UI."""

    create_requested = Signal(object)
    cancel_requested = Signal()
    cancel_invitation_requested = Signal()
    active_game_requested = Signal(dict)

    _MODES = (
        ("Локально", "Свободная доска для двух сторон.", "Свободная доска", "local", True),
        ("Lichess AI", "Нерейтинговая партия против встроенного AI.", "Играть против AI", "ai", False),
        ("Друг", "Создать ссылку-приглашение или открыть игру.", "Играть с другом", "friend", False),
        ("Соперник Lichess", "Вызвать игрока по имени аккаунта.", "Вызвать игрока", "challenge", False),
        ("Активные партии", "Открыть и продолжить текущую сетевую игру.", "Показать партии", "active", False),
    )

    def __init__(self, actions: dict[str, Callable], parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        title = QLabel("Выберите режим игры")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        note = QLabel("Выберите локальную доску или сетевую партию. Текущая партия не сбрасывается.")
        note.setObjectName("secondary")
        layout.addWidget(note)
        self.mode_selector = QComboBox()
        for label, value in (("Свободная доска", "local"),
                             ("Lichess AI", "ai"), ("Друг по ссылке", "friend"),
                             ("Вызов по имени", "challenge"),
                             ("Продолжить активную партию", "active")):
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
        for label, widget in (("Режим", self.mode_selector), ("Ваш цвет", self.color),
                              ("Контроль времени", self.clock), ("Уровень Lichess AI", self.level),
                              ("Соперник", self.username)):
            row_widget = QWidget()
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
            row_widget.setLayout(row)
            self.form_rows[label] = row_widget
            form_layout.addWidget(row_widget)
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
        self.copy_invitation_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.invitation_link.text()))
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
        self.active_games.setMaximumHeight(130)
        self.active_open = QPushButton("Продолжить выбранную партию")
        self.active_open.setEnabled(False)
        self.active_open.clicked.connect(self._open_selected_active_game)
        self.active_games.currentItemChanged.connect(
            lambda item, _old: self.active_open.setEnabled(item is not None))
        active_layout.addWidget(self.active_status)
        active_layout.addWidget(self.active_games)
        active_layout.addWidget(self.active_open)
        self.active_panel.hide()
        layout.addWidget(self.active_panel)
        self.mode_selector.currentIndexChanged.connect(self._update_form)
        self.clock.currentIndexChanged.connect(self._update_form)
        self.start_button.clicked.connect(self._emit_create)
        self._request_pending = False
        self._update_form()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.buttons: dict[str, QPushButton] = {}
        self.cards: list[QFrame] = []
        for index, (heading, description, button_text, key, primary) in enumerate(self._MODES):
            card = QFrame()
            card.setObjectName("metricCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            title_label = QLabel(heading)
            title_label.setStyleSheet("font-size: 16px; font-weight: 700;")
            card_layout.addWidget(title_label)
            description_label = QLabel(description)
            description_label.setObjectName("secondary")
            description_label.setWordWrap(True)
            card_layout.addWidget(description_label)
            button = QPushButton(button_text)
            if primary:
                button.setObjectName("primaryAction")
            button.clicked.connect(lambda _checked=False, key=key: actions[key]())
            self.buttons[key] = button
            card_layout.addWidget(button)
            self.cards.append(card)
        self._columns = 2
        self._place_cards()
        layout.addLayout(self.grid)
        for card in self.cards:
            card.setVisible(False)
        layout.addStretch(1)

    def _update_form(self, *_args) -> None:
        mode = str(self.mode_selector.currentData() or "local")
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
        self.active_games.clear()
        for game in games:
            if not game.get("gameId"):
                continue
            opponent = game.get("opponent") or {}
            name = opponent.get("username") or opponent.get("id") or "Соперник"
            item = QListWidgetItem(str(name))
            item.setData(Qt.ItemDataRole.UserRole, dict(game))
            self.active_games.addItem(item)
        self.active_status.setText(status or (
            "Выберите партию для продолжения." if self.active_games.count()
            else "Активных партий нет."))
        self.active_panel.show()

    def _open_selected_active_game(self) -> None:
        item = self.active_games.currentItem()
        if item is not None:
            self.active_game_requested.emit(item.data(Qt.ItemDataRole.UserRole))

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
