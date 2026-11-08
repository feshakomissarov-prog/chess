"""Analysis landing and its nested party breakdown workspace."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .position_setup import PositionSetupWidget


class AnalysisLandingPage(QWidget):
    """Four analysis destinations and the single nested review/import workspace."""

    back_requested = Signal()
    archive_requested = Signal()

    _CARDS = (
        ("♜", "Разбор партии", "Архив, PGN/FEN, редактор позиции и анализ ходов",
         "Доступно", "Открыть разбор", "games", True),
        ("♞", "Исследование дебютов", "Сохранённая статистика дебютов и фаз",
         "Частично доступно", "Открыть статистику", "openings", True),
        ("◎", "Диагностика слабостей", "Повторяющиеся ошибки и приоритеты развития",
         "В разработке", "В разработке", "weakness", False),
        ("♟", "Подготовка к сопернику", "Анализ открытых партий Lichess и дебютов",
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
        for index, (glyph, heading, description, status, label, key, enabled) in enumerate(self._CARDS):
            card = QFrame()
            card.setObjectName("metricCard" if enabled else "disabledAnalysisCard")
            card.setMinimumHeight(206)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 14, 18, 14)
            heading_row = QHBoxLayout()
            icon = QLabel(glyph)
            icon.setObjectName("analysisCardIcon")
            icon.setFixedSize(48, 48)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        self.open_text_button = QPushButton("Открыть на доске")
        self.open_text_button.setObjectName("primaryAction")
        self.open_text_button.clicked.connect(lambda: actions["open_text"](
            self.external_text.toPlainText()))
        actions_row.addWidget(self.open_text_button)
        card_layout.addLayout(actions_row)
        layout.addWidget(card, 1)

    def _build_editor_tab(self, page: QWidget, actions: dict[str, Callable]) -> None:
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        self.position_setup = PositionSetupWidget(page)
        self.position_setup.setCheckable(False)
        self.position_setup.setTitle("")
        self.position_setup.position_requested.connect(lambda fen: actions["open_text"](fen))
        layout.addWidget(self.position_setup, 1)

    def _tab_changed(self, index: int) -> None:
        if index == 0 and not self._switching_tab:
            self.archive_requested.emit()

    def set_archive_widget(self, widget: QWidget) -> None:
        if self.archive_layout.indexOf(widget) >= 0:
            return
        for index in reversed(range(self.archive_layout.count())):
            item = self.archive_layout.itemAt(index)
            child = item.widget()
            if child is not None:
                child.setParent(None)
            self.archive_layout.removeItem(item)
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
        self.workspace.setCurrentIndex(0)

    def set_external_status(self, text: str) -> None:
        self.external_status.setText(str(text))
