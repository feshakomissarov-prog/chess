"""Local finished-game library view; persistence and actions are injected by the shell."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)
from ..display import format_clock_control


class LibraryPage(QWidget):
    query_requested = Signal(object, int)
    open_requested = Signal(dict)
    export_requested = Signal(dict)
    note_requested = Signal(dict, str)
    analyze_requested = Signal(dict)
    refresh_requested = Signal(dict)
    active_requested = Signal(str)

    def __init__(self, parent=None, *, accounts: list[dict] | None = None,
                 clocks: list[str] | None = None) -> None:
        super().__init__(parent)
        self._page = 0
        self._pages = 1
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        self.active_title = QLabel("Текущие партии")
        self.active_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.active_title)
        self.active_games = QListWidget()
        self.active_games.setMaximumHeight(112)
        self.active_games.itemClicked.connect(self._open_active)
        self.active_games.itemDoubleClicked.connect(self._open_active)
        layout.addWidget(self.active_games)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по сопернику, игроку, ID или статусу")
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        self.account_filter = QComboBox()
        self.account_filter.addItem("Все аккаунты", None)
        for account in accounts or []:
            self.account_filter.addItem(str(account["username"]), str(account["account_id"]))
        self.side_filter = QComboBox()
        self.side_filter.addItem("Любая сторона", "")
        self.side_filter.addItem("Белые", "white")
        self.side_filter.addItem("Чёрные", "black")
        self.result_filter = QComboBox()
        for label, value in (("Любой результат", ""), ("Победа", "win"),
                             ("Ничья", "draw"), ("Поражение", "loss"),
                             ("Неизвестный исход", "unknown")):
            self.result_filter.addItem(label, value)
        self.source_filter = QComboBox()
        for label, value in (("Любой источник", ""), ("Lichess", "lichess_archive"),
                             ("Локальные", "local")):
            self.source_filter.addItem(label, value)
        self.clock_filter = QComboBox()
        self.clock_filter.addItem("Любой контроль", "")
        for clock in sorted(set(clocks or [])):
            self.clock_filter.addItem(format_clock_control(clock), clock)
        self.analysis_filter = QComboBox()
        for label, value in (("Любой анализ", ""), ("Есть анализ", "yes"),
                             ("Без анализа", "no")):
            self.analysis_filter.addItem(label, value)
        self.analysis_source_filter = QComboBox()
        for label, value in (("Любой тип оценки", ""), ("Stockfish", "stockfish"),
                             ("Lichess", "lichess")):
            self.analysis_source_filter.addItem(label, value)
        self.period_filter = QComboBox()
        for label, value in (("Любой период", 0), ("30 дней", 30),
                             ("90 дней", 90), ("1 год", 365)):
            self.period_filter.addItem(label, value)
        for widget in (self.account_filter, self.side_filter, self.result_filter,
                       self.source_filter, self.clock_filter, self.analysis_filter,
                       self.analysis_source_filter, self.period_filter):
            filters.addWidget(widget, 1)
            widget.currentIndexChanged.connect(self._emit_filters)
        layout.addLayout(filters)
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
        # Per-game refresh is a recovery action, not a normal catalog command.
        # It is revealed only by an explicit diagnosed-recovery presentation.
        self.refresh_game.setVisible(False)
        self.close_button = QPushButton("Закрыть")
        for button in (self.open_game, self.export_game, self.analyze_game,
                       self.refresh_game, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search.textChanged.connect(lambda _value: self._emit_filters())
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

    def set_active_games(self, sessions: list[dict]) -> None:
        self.active_games.clear()
        self.active_title.setVisible(bool(sessions))
        self.active_games.setVisible(bool(sessions))
        for session in sessions:
            item = QListWidgetItem(
                f"Активна · {session.get('opponent') or 'соперник'} · "
                f"{session.get('clock') or 'без контроля'} · "
                f"{session.get('color') or 'сторона неизвестна'} · "
                f"{session.get('turn') or 'синхронизация'}")
            item.setData(Qt.ItemDataRole.UserRole, str(session["game_id"]))
            self.active_games.addItem(item)

    def _open_active(self, item) -> None:
        game_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if game_id:
            self.active_requested.emit(str(game_id))

    def _request_page(self, page: int) -> None:
        self._emit_filters(max(0, min(self._pages - 1, page)))

    def current_filters(self) -> dict:
        return {
            "query": self.search.text(),
            "account_id": self.account_filter.currentData(),
            "color": self.side_filter.currentData() or None,
            "outcome": self.result_filter.currentData() or None,
            "source": self.source_filter.currentData() or None,
            "clock": self.clock_filter.currentData() or None,
            "analysis": self.analysis_filter.currentData() or None,
            "analysis_source": self.analysis_source_filter.currentData() or None,
            "period_days": int(self.period_filter.currentData() or 0),
        }

    def _emit_filters(self, page: int = 0) -> None:
        self.query_requested.emit(self.current_filters(), max(0, int(page)))

    def set_results(self, rows: list[tuple[dict, str, str]], *, page: int, pages: int,
                    total: int) -> None:
        self._page, self._pages = max(0, page), max(1, pages)
        previous = self.selected_game()
        selected_id = str(previous.get("game_id")) if previous else None
        scroll = self.games.verticalScrollBar().value()
        self.games.clear()
        selected_item = None
        for game, row_text, source_label in rows:
            item = QListWidgetItem(row_text)
            item.setToolTip(f"Источник: {source_label}")
            item.setData(Qt.ItemDataRole.UserRole, game)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                         row_text.replace("\n", ". "))
            self.games.addItem(item)
            if selected_id and str(game.get("game_id")) == selected_id:
                selected_item = item
        if not rows:
            self.games.addItem("Завершённых партий в этой выборке нет.")
        self.page_label.setText(
            f"Страница {self._page + 1} из {self._pages}  ·  {max(0, int(total))} партий")
        if selected_item is not None:
            self.games.setCurrentItem(selected_item)
        elif rows:
            self.games.setCurrentRow(0)
        self.games.verticalScrollBar().setValue(scroll)
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
