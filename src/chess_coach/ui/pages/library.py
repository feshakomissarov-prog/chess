"""Local finished-game library view; persistence and actions are injected by the shell."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)


class LibraryPage(QWidget):
    query_requested = Signal(str, int)
    open_requested = Signal(dict)
    export_requested = Signal(dict)
    note_requested = Signal(dict, str)
    analyze_requested = Signal(dict)
    refresh_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = 0
        self._pages = 1
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по сопернику, игроку, ID или статусу")
        layout.addWidget(self.search)
        self.games = QListWidget()
        layout.addWidget(self.games, 1)
        page_row = QHBoxLayout()
        self.previous = QPushButton("← Назад")
        self.page_label = QLabel()
        self.following = QPushButton("Вперёд →")
        page_row.addWidget(self.previous)
        page_row.addWidget(self.page_label, 1)
        page_row.addWidget(self.following)
        layout.addLayout(page_row)
        note_row = QHBoxLayout()
        self.note = QLineEdit()
        self.note.setPlaceholderText("Заметка к начальной позиции выбранной партии")
        self.save_note = QPushButton("Сохранить заметку")
        note_row.addWidget(self.note, 1)
        note_row.addWidget(self.save_note)
        layout.addLayout(note_row)
        actions = QHBoxLayout()
        self.open_game = QPushButton("Открыть для просмотра")
        self.export_game = QPushButton("Экспорт PGN")
        self.analyze_game = QPushButton("Анализ Stockfish")
        self.refresh_game = QPushButton("Обновить эту партию из Lichess")
        self.close_button = QPushButton("Закрыть")
        for button in (self.open_game, self.export_game, self.analyze_game,
                       self.refresh_game, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search.textChanged.connect(lambda value: self.query_requested.emit(value, 0))
        self.previous.clicked.connect(lambda _checked=False: self._request_page(self._page - 1))
        self.following.clicked.connect(lambda _checked=False: self._request_page(self._page + 1))
        self.games.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.open_game.clicked.connect(lambda _checked=False: self._open_selected())
        self.export_game.clicked.connect(lambda _checked=False: self._emit_for_selected(
            self.export_requested))
        self.save_note.clicked.connect(lambda _checked=False: self._emit_note())
        self.analyze_game.clicked.connect(lambda _checked=False: self._emit_for_selected(
            self.analyze_requested))
        self.refresh_game.clicked.connect(lambda _checked=False: self._emit_for_selected(
            self.refresh_requested))
        self.games.currentItemChanged.connect(self._selection_changed)

    def _request_page(self, page: int) -> None:
        self.query_requested.emit(self.search.text(), max(0, min(self._pages - 1, page)))

    def set_results(self, rows: list[tuple[dict, str, str]], *, page: int, pages: int,
                    total: int) -> None:
        self._page, self._pages = max(0, page), max(1, pages)
        self.games.clear()
        for game, row_text, source_label in rows:
            item = QListWidgetItem(row_text)
            item.setToolTip(f"Источник: {source_label}")
            item.setData(Qt.ItemDataRole.UserRole, game)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                         row_text.replace("\n", ". "))
            self.games.addItem(item)
        if not rows:
            self.games.addItem("Завершённых партий в этой выборке нет.")
        self.page_label.setText(
            f"Страница {self._page + 1} из {self._pages}  ·  {max(0, int(total))} партий")
        self.previous.setEnabled(self._page > 0)
        self.following.setEnabled(self._page + 1 < self._pages)
        self._selection_changed(self.games.currentItem(), None)

    def selected_game(self) -> dict | None:
        item = self.games.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, dict) else None

    def _emit_for_selected(self, signal) -> None:
        game = self.selected_game()
        if game is not None:
            signal.emit(game)

    def _open_selected(self) -> None:
        self._emit_for_selected(self.open_requested)

    def _emit_note(self) -> None:
        game = self.selected_game()
        if game is not None:
            self.note_requested.emit(game, self.note.text())

    def _selection_changed(self, current, _previous) -> None:
        enabled = current is not None and self.selected_game() is not None
        for button in (self.open_game, self.export_game, self.analyze_game):
            button.setEnabled(enabled)
