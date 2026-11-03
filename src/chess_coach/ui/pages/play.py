"""Mode selection view; actions are supplied by the application shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class PlayPage(QWidget):
    """Responsive local/online mode cards with no repository or network access."""

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
        layout.addStretch(1)

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
