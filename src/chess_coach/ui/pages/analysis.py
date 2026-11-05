"""Analysis landing page using only existing analysis capabilities."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from .position_setup import PositionSetupWidget


class AnalysisLandingPage(QWidget):
    """Small in-shell entry point; it does not start imports or engines itself."""

    _CARDS = (
        ("Разбор партии", "Откройте сохранённую завершённую партию и её позиции.",
         "Выбрать партию", "games"),
        ("Массовый анализ", "Продолжить работу с существующей очередью Stockfish.",
         "Открыть очередь", "batch"),
        ("Исследование дебютов", "Пока доступна только статистика дебютов из локальных партий; дерева и полноценного explorer ещё нет.",
         "Открыть статистику", "progress"),
    )

    def __init__(self, actions: dict[str, Callable], parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        title = QLabel("Анализ")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        intro = QLabel("Выберите сохранённую партию или откройте существующую очередь анализа. "
                       "Открытие раздела само по себе не запускает Stockfish.")
        intro.setObjectName("secondary")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.grid = QGridLayout()
        self.cards: list[QFrame] = []
        self.buttons: dict[str, QPushButton] = {}
        for index, (heading, description, label, key) in enumerate(self._CARDS):
            card = QFrame()
            card.setObjectName("metricCard")
            layout = QVBoxLayout(card)
            name = QLabel(heading)
            name.setStyleSheet("font-size: 17px; font-weight: 700;")
            layout.addWidget(name)
            note = QLabel(description)
            note.setObjectName("secondary")
            note.setWordWrap(True)
            layout.addWidget(note, 1)
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, key=key: actions[key]())
            layout.addWidget(button)
            self.buttons[key] = button
            self.cards.append(card)
            self.grid.addWidget(card, 0, index)
        root.addLayout(self.grid)
        self.external_group = QGroupBox("Внешняя партия или позиция")
        external_layout = QVBoxLayout(self.external_group)
        self.external_text = QTextEdit()
        self.external_text.setPlaceholderText(
            "Вставьте одну PGN-партию или FEN. Данные будут открыты временно и не попадут в архив.")
        self.external_text.setMaximumHeight(150)
        external_layout.addWidget(self.external_text)
        external_actions = QHBoxLayout()
        self.open_text_button = QPushButton("Открыть вставленную PGN/FEN")
        self.open_text_button.clicked.connect(lambda: actions["open_text"](
            self.external_text.toPlainText()))
        self.open_file_button = QPushButton("Выбрать файл PGN…")
        self.open_file_button.clicked.connect(actions["open_file"])
        external_actions.addWidget(self.open_text_button)
        external_actions.addWidget(self.open_file_button)
        external_layout.addLayout(external_actions)
        self.external_status = QLabel("Внешние PGN/FEN открываются временно и не добавляются в архив.")
        self.external_status.setObjectName("secondary")
        self.external_status.setWordWrap(True)
        external_layout.addWidget(self.external_status)
        root.addWidget(self.external_group)
        self.position_setup = PositionSetupWidget(self)
        self.position_setup.position_requested.connect(lambda fen: actions["open_text"](fen))
        root.addWidget(self.position_setup)
        root.addStretch(1)

    def set_external_status(self, text: str) -> None:
        self.external_status.setText(str(text))
