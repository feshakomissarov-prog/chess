"""Lichess Puzzle Activity history and offline practice view."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from ...board_view import BoardView
import chess


class PuzzleHistoryPage(QWidget):
    """Reusable view. Account data and all persistence/network actions stay external."""

    def __init__(self, accounts: list[dict], account_id: str, *, refresh_enabled: bool,
                 parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("Аккаунт задач"))
        self.account_selector = QComboBox()
        for row in accounts:
            self.account_selector.addItem(
                f"{row['username']} · {row['attempts']} попыток", str(row['account_id']))
        self.account_selector.setCurrentIndex(self.account_selector.findData(account_id))
        account_row.addWidget(self.account_selector, 1)
        root.addLayout(account_row)
        top = QHBoxLayout()
        self.summary = QLabel("Загружаем локальную статистику…")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.refresh = QPushButton("Обновить с Lichess")
        self.refresh.setEnabled(refresh_enabled)
        top.addWidget(self.refresh)
        root.addLayout(top)
        body = QHBoxLayout()
        left = QVBoxLayout()
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ID, теме, рейтингу или дате")
        self.result_filter = QComboBox()
        self.result_filter.addItem("Любой результат", "")
        self.result_filter.addItem("Решено", "win")
        self.result_filter.addItem("Ошибка", "loss")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.result_filter)
        left.addLayout(filters)
        self.history = QListWidget()
        left.addWidget(self.history, 1)
        pages = QHBoxLayout()
        self.previous = QPushButton("← Назад")
        self.following = QPushButton("Вперёд →")
        self.page_label = QLabel()
        pages.addWidget(self.previous)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.following)
        left.addLayout(pages)
        left_widget = QWidget(self)
        left_widget.setLayout(left)
        body.addWidget(left_widget, 1)
        right = QVBoxLayout()
        self.board = BoardView(chess.Board(), self)
        self.board.set_interaction_enabled(False)
        self.solution = QLabel("Выберите попытку для локального просмотра.")
        self.solution.setWordWrap(True)
        self.replay = QPushButton("Решить повторно")
        self.replay.setEnabled(False)
        self.reveal = QPushButton("Показать решение…")
        self.reveal.setEnabled(False)
        self.reset_replay = QPushButton("Сбросить упражнение")
        self.reset_replay.setEnabled(False)
        right.addWidget(self.board, 1)
        right.addWidget(QLabel(
            "Практика по сохранённой попытке · ответ не отправляется на Lichess"))
        right.addWidget(self.solution)
        replay_row = QHBoxLayout()
        replay_row.addWidget(self.replay)
        replay_row.addWidget(self.reveal)
        replay_row.addWidget(self.reset_replay)
        right.addLayout(replay_row)
        right_widget = QWidget(self)
        right_widget.setLayout(right)
        body.addWidget(right_widget, 2)
        root.addLayout(body, 1)
        self.close_button = QPushButton("Закрыть")
        root.addWidget(self.close_button)
