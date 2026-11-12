"""Analysis landing and its nested party breakdown workspace."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStackedWidget,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .position_setup import PositionSetupWidget
from ..icons import icon_pixmap


class AnalysisLandingPage(QWidget):
    """Four analysis destinations and the single nested review/import workspace."""

    back_requested = Signal()
    archive_requested = Signal()
    nested_tool_changed = Signal(int)

    _CARDS = (
        ("games", "Разбор партии", "Архив, PGN/FEN, редактор позиции и анализ ходов",
         "Доступно", "Открыть разбор", "games", True),
        ("analysis", "Исследование дебютов", "Сохранённая статистика дебютов и фаз",
         "Частично доступно", "Открыть статистику", "openings", True),
        ("target", "Диагностика слабостей", "Повторяющиеся ошибки и приоритеты развития",
         "В разработке", "В разработке", "weakness", False),
        ("pawn", "Подготовка к сопернику", "Анализ открытых партий Lichess и дебютов",
         "В разработке", "В разработке", "opponent", False),
    )

    def __init__(self, actions: dict[str, Callable], parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.workspace = QStackedWidget(self)
        root.addWidget(self.workspace, 1)

        landing = QWidget(self.workspace)
        landing_layout = QVBoxLayout(landing)
        landing_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Анализ")
        title.setObjectName("pageTitle")
        landing_layout.addWidget(title)
        intro = QLabel("Выберите направление исследования вашей шахматной игры")
        intro.setObjectName("secondary")
        intro.setWordWrap(True)
        landing_layout.addWidget(intro)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.cards: list[QFrame] = []
        self.buttons: dict[str, QPushButton] = {}
        for index, (icon_name, heading, description, status, label, key, enabled) in enumerate(self._CARDS):
            card = QFrame()
            card.setObjectName("metricCard" if enabled else "disabledAnalysisCard")
            card.setMinimumHeight(206)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 14, 18, 14)
            heading_row = QHBoxLayout()
            icon = QLabel()
            icon.setObjectName("analysisCardIcon")
            icon.setFixedSize(48, 48)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setPixmap(icon_pixmap(icon_name, 25, "#C8BBFF",
                                       icon.devicePixelRatioF()))
            heading_row.addWidget(icon)
            name = QLabel(heading)
            name.setStyleSheet("font-size: 20px; font-weight: 700;")
            name.setWordWrap(True)
            heading_row.addWidget(name, 1)
            card_layout.addLayout(heading_row)
            note = QLabel(description)
            note.setObjectName("secondary")
            note.setWordWrap(True)
            card_layout.addWidget(note, 1)
            badge = QLabel(status)
            badge.setObjectName("analysisStatus")
            card_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
            button = QPushButton(label)
            button.setEnabled(enabled)
            if enabled:
                button.setObjectName("primaryAction")
                if key == "games":
                    button.clicked.connect(actions["games"])
                else:
                    button.clicked.connect(actions["openings"])
            else:
                button.setToolTip("Эта функция пока не реализована.")
            card_layout.addWidget(button)
            self.buttons[key] = button
            self.cards.append(card)
            self.grid.addWidget(card, index // 2, index % 2)
        landing_layout.addLayout(self.grid, 1)
        self.workspace.addWidget(landing)

        self.breakdown_page = QWidget(self.workspace)
        breakdown_layout = QVBoxLayout(self.breakdown_page)
        breakdown_layout.setContentsMargins(0, 0, 0, 0)
        breadcrumb = QHBoxLayout()
        self.back_button = QPushButton("‹  Анализ")
        self.back_button.setObjectName("linkAction")
        self.back_button.clicked.connect(self.show_landing)
        breadcrumb.addWidget(self.back_button)
        crumb = QLabel("/  Разбор партии")
        crumb.setObjectName("breadcrumb")
        breadcrumb.addWidget(crumb)
        breadcrumb.addStretch(1)
        breakdown_layout.addLayout(breadcrumb)
        heading = QLabel("Разбор партии")
        heading.setObjectName("pageTitle")
        breakdown_layout.addWidget(heading)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("analysisWorkspaceTabs")
        self.archive_tab = QWidget()
        self.archive_layout = QVBoxLayout(self.archive_tab)
        self.archive_layout.setContentsMargins(0, 6, 0, 0)
        archive_hint = QLabel("Ваши сохранённые партии")
        archive_hint.setObjectName("secondary")
        self.archive_layout.addWidget(archive_hint)
        self.tabs.addTab(self.archive_tab, "Мои партии")
        self.import_tab = QWidget()
        self._build_import_tab(self.import_tab, actions)
        self.tabs.addTab(self.import_tab, "Вставить ходы")
        self.editor_tab = QWidget()
        self._build_editor_tab(self.editor_tab, actions)
        self.tabs.addTab(self.editor_tab, "Расставить позицию")
        self.tabs.currentChanged.connect(self._tab_changed)
        self._switching_tab = False
        breakdown_layout.addWidget(self.tabs, 1)
        self.workspace.addWidget(self.breakdown_page)
        self.show_landing()

    def _build_import_tab(self, page: QWidget, actions: dict[str, Callable]) -> None:
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        title = QLabel("Вставьте PGN, последовательность ходов или FEN")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        card_layout.addWidget(title)
        explanation = QLabel("Поддерживаются полные PGN, обычная запись ходов SAN и одна позиция FEN.")
        explanation.setObjectName("secondary")
        card_layout.addWidget(explanation)
        self.external_text = QTextEdit()
        self.external_text.setObjectName("externalStudyText")
        self.external_text.setPlaceholderText("Например: 1. e4 e5 2. Nf3 Nc6")
        self.external_text.setMinimumHeight(160)
        card_layout.addWidget(self.external_text, 1)
        tools = QHBoxLayout()
        self.open_file_button = QPushButton("Выбрать файл PGN…")
        self.open_file_button.clicked.connect(actions["open_file"])
        tools.addWidget(self.open_file_button)
        tools.addStretch(1)
        card_layout.addLayout(tools)
        self.external_status = QLabel("Внешний материал откроется временно и не попадёт в архив.")
        self.external_status.setObjectName("secondary")
        self.external_status.setWordWrap(True)
        card_layout.addWidget(self.external_status)
        self.candidate_heading = QLabel()
        self.candidate_heading.setObjectName("secondary")
        self.candidate_heading.setVisible(False)
        card_layout.addWidget(self.candidate_heading)
        self.candidate_list = QListWidget()
        self.candidate_list.setObjectName("externalStudyCandidates")
        self.candidate_list.setMaximumHeight(190)
        self.candidate_list.setVisible(False)
        card_layout.addWidget(self.candidate_list)
        self.candidates: list[dict] = []
        self.back_to_text_button = QPushButton("Назад к тексту")
        self.back_to_text_button.setVisible(False)
        self.back_to_text_button.clicked.connect(self.clear_candidates)
        card_layout.addWidget(self.back_to_text_button, 0, Qt.AlignmentFlag.AlignLeft)
        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        self.open_text_button = QPushButton("Открыть на доске")
        self.open_text_button.setObjectName("primaryAction")
        self._open_text_action = actions["open_text"]
        self.open_text_button.clicked.connect(self._open_selected_or_text)
        actions_row.addWidget(self.open_text_button)
        card_layout.addLayout(actions_row)
        layout.addWidget(card, 1)

    def _open_selected_or_text(self) -> None:
        if self.candidates:
            row = self.candidate_list.currentRow()
            if row < 0 or row >= len(self.candidates):
                self.set_external_status("Выберите партию в списке.")
                return
            self._open_text_action(dict(self.candidates[row]))
            return
        self._open_text_action(self.external_text.toPlainText())

    def set_candidates(self, games: list[dict]) -> None:
        self.candidates = [dict(game) for game in games]
        self.candidate_list.clear()
        for index, game in enumerate(self.candidates, 1):
            headers = game.get("headers") or {}
            white = str(headers.get("White") or "Белые")
            black = str(headers.get("Black") or "Чёрные")
            if white.strip().casefold() in {"?", "unknown"}:
                white = "Белые"
            if black.strip().casefold() in {"?", "unknown"}:
                black = "Чёрные"
            date = str(headers.get("Date") or "дата не указана")
            result = str(headers.get("Result") or "результат не указан")
            if result == "*":
                result = "результат не указан"
            eco = str(headers.get("ECO") or "")
            label = f"{index}. {white} — {black} · {date} · {result}"
            if eco:
                label += f" · {eco}"
            preview = str(game.get("preview") or "")
            item = QListWidgetItem(label + (f"\n{preview}" if preview else ""))
            self.candidate_list.addItem(item)
        self.candidate_heading.setText(f"Найдено {len(self.candidates)} партий. Выберите одну.")
        self.candidate_heading.setVisible(bool(self.candidates))
        self.candidate_list.setVisible(bool(self.candidates))
        self.back_to_text_button.setVisible(bool(self.candidates))
        self.set_external_status("Выберите партию, затем нажмите «Открыть на доске».")

    def clear_candidates(self) -> None:
        self.candidates.clear()
        self.candidate_list.clear()
        self.candidate_heading.hide()
        self.candidate_list.hide()
        self.back_to_text_button.hide()
        self.set_external_status("Черновик сохранён. Можно изменить текст или открыть другую партию.")

    def _build_editor_tab(self, page: QWidget, actions: dict[str, Callable]) -> None:
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        self.position_setup = PositionSetupWidget(page)
        self.position_setup.set_embedded()
        self.position_setup.position_requested.connect(lambda fen: actions["open_text"](fen))
        layout.addWidget(self.position_setup, 1)

    def _tab_changed(self, index: int) -> None:
        self.nested_tool_changed.emit(index)
        if index == 0 and not self._switching_tab:
            self.archive_requested.emit()

    def set_archive_widget(self, widget: QWidget) -> None:
        if self.archive_layout.indexOf(widget) >= 0:
            return
        for index in reversed(range(self.archive_layout.count())):
            item = self.archive_layout.takeAt(index)
            child = item.widget()
            if child is not None:
                child.setParent(None)
        widget.setParent(self.archive_tab)
        self.archive_layout.addWidget(widget, 1)

    def show_breakdown(self, tab: int = 0) -> None:
        self.workspace.setCurrentWidget(self.breakdown_page)
        self._switching_tab = True
        try:
            self.tabs.setCurrentIndex(max(0, min(int(tab), self.tabs.count() - 1)))
        finally:
            self._switching_tab = False

    def show_landing(self) -> None:
        self.nested_tool_changed.emit(-1)
        self.workspace.setCurrentIndex(0)

    def set_external_status(self, text: str) -> None:
        self.external_status.setText(str(text))
