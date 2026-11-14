"""Lichess Puzzle Activity history and offline practice view."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QLineEdit, QPushButton, QVBoxLayout,
    QWidget, QSizePolicy,
)
from PySide6.QtCore import Qt

from ...board_view import BoardView
import chess


class PuzzleSolutionLabel(QLabel):
    """Keep full local attempt details accessible while fitting compact screens."""

    def __init__(self, text: str, parent=None) -> None:
        self._full_text = str(text)
        self._compact = False
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setToolTip(self._full_text)
        self.setAccessibleDescription(self._full_text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API override
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self.setAccessibleDescription(self._full_text)
        super().setText(self._display_text())

    def set_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setMinimumHeight(60 if compact else 0)
        self.setStyleSheet("font-size: 13px;" if compact else "")
        super().setText(self._display_text())

    def _display_text(self) -> str:
        if not self._compact or "Исходная попытка:" not in self._full_text:
            return self._full_text
        lines = self._full_text.splitlines()
        title = lines[0].split(" · темы:", 1)[0]
        attempt = lines[1].split(".", 1)[0].replace(
            "Исходная попытка:", "Попытка:") if len(lines) > 1 else "Попытка"
        return f"{title}\n{attempt} · правильный вариант скрыт"


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
        self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self.board.setMinimumSize(0, 0)
        self.board.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.solution = PuzzleSolutionLabel("Выберите попытку для локального просмотра.")
        self.solution.setWordWrap(True)
        self.replay = QPushButton("Решить повторно")
        self.replay.setMinimumHeight(32)
        self.replay.setEnabled(False)
        self.reveal = QPushButton("Показать решение…")
        self.reveal.setMinimumHeight(32)
        self.reveal.setEnabled(False)
        self.reset_replay = QPushButton("Сбросить")
        self.reset_replay.setObjectName("resetPuzzleReplay")
        self.reset_replay.setToolTip("Сбросить упражнение и начать его заново")
        self.reset_replay.setMinimumHeight(32)
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
        self.close_button.setMinimumHeight(32)
        root.addWidget(self.close_button)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.solution.set_compact(self.height() < 540 or self.width() < 920)
        right = self.board.parentWidget()
        if right is None or right.width() < 1 or right.height() < 1:
            return
        # The board must yield to the text and exercise controls instead of
        # painting/laying out over them at elevated DPI.
        right_layout = right.layout()
        margins = right_layout.contentsMargins()
        controls = [item.widget() for index in range(right_layout.count())
                    if (item := right_layout.itemAt(index)).widget() is not None
                    and item.widget() is not self.board and not item.widget().isHidden()]
        controls_height = 0
        for widget in controls:
            hint = widget.sizeHint().height()
            if widget.hasHeightForWidth():
                hint = max(hint, widget.heightForWidth(
                    max(1, right.width() - margins.left() - margins.right())))
            controls_height += max(widget.minimumSizeHint().height(), hint)
        spacing = max(0, len(controls)) * right_layout.spacing()
        available_height = (right.height() - margins.top() - margins.bottom()
                            - controls_height - spacing)
        available_width = right.width() - margins.left() - margins.right()
        # A fixed-size board can overflow its assigned vertical-layout slot
        # when a wrapped description grows at high DPI. Keep it square via a
        # maximum bound and let QVBoxLayout give actions/text their required
        # height; the board may shrink below 120 logical px rather than paint
        # over the solution or buttons.
        side = max(0, min(available_width, available_height))
        self.board.setMinimumSize(0, 0)
        self.board.setMaximumSize(side, side)
