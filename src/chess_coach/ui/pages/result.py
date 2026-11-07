"""Completed-game result view with application actions passed in at creation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ResultPage(QWidget):
    def __init__(self, actions: dict[str, Callable], parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.summary = QLabel("Партия завершена")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.saved_label = QLabel()
        layout.addWidget(self.saved_label)
        self.review_button = QPushButton("Просмотреть партию")
        self.review_button.clicked.connect(lambda _checked=False: actions["review"]())
        self.export_button = QPushButton("Экспорт PGN")
        self.export_button.clicked.connect(lambda _checked=False: actions["export"]())
        self.new_button = QPushButton("Новая партия")
        self.new_button.clicked.connect(lambda _checked=False: actions["new"]())
        self.home_button = QPushButton("Главная")
        self.home_button.clicked.connect(lambda _checked=False: actions["home"]())
        for button in (self.review_button, self.export_button,
                       self.new_button, self.home_button):
            layout.addWidget(button)
        layout.addStretch(1)
