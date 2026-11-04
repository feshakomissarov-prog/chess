"""Mode selection view; actions are supplied by the application shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)


class PlayPage(QWidget):
    """Embedded game setup form; networking and game-state ownership stay outside UI."""

    create_requested = Signal(object)

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
                             ("15 + 10", (900, 10))):
            self.clock.addItem(label, value)
        self.level = QComboBox()
        for value in range(1, 9):
            self.level.addItem(f"Уровень {value}", value)
        self.rated = QCheckBox("Рейтинговая партия")
        self.start_button = QPushButton("Начать свободную доску")
        self.start_button.setObjectName("primaryAction")
        self.form_status = QLabel("Выберите режим и параметры партии.")
        self.form_status.setObjectName("secondary")
        self.form_status.setWordWrap(True)
        form = QFrame()
        form.setObjectName("metricCard")
        form_layout = QVBoxLayout(form)
        for label, widget in (("Режим", self.mode_selector), ("Ваш цвет", self.color),
                              ("Контроль времени", self.clock), ("Уровень Lichess AI", self.level),
                              ("Соперник", self.username)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(widget, 1)
            form_layout.addLayout(row)
        form_layout.addWidget(self.rated)
        form_layout.addWidget(self.form_status)
        form_layout.addWidget(self.start_button)
        layout.addWidget(form)
        self.mode_selector.currentIndexChanged.connect(self._update_form)
        self.start_button.clicked.connect(self._emit_create)
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
        self.level.setVisible(mode == "ai")
        self.rated.setVisible(mode in {"friend", "challenge"})
        self.username.setVisible(mode == "challenge")
        labels = {
            "local": "Начать свободную доску", "ai": "Играть против Lichess AI",
            "friend": "Создать приглашение", "challenge": "Вызвать соперника",
            "active": "Показать активные партии",
        }
        self.start_button.setText(labels.get(mode, "Начать"))

    def _emit_create(self) -> None:
        limit, increment = self.clock.currentData()
        self.create_requested.emit({
            "mode": self.mode_selector.currentData(),
            "color": self.color.currentData(), "clock_limit": limit,
            "clock_increment": increment, "level": int(self.level.currentData()),
            "rated": self.rated.isChecked(), "username": self.username.text().strip(),
        })

    def set_status(self, text: str) -> None:
        self.form_status.setText(str(text))

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
