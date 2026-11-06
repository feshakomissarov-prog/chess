"""Local finished-game library view; persistence and actions are injected by the shell."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QLayout, QLineEdit, QPushButton, QScrollArea, QSplitter, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QWidget,
)
from ..dashboard import game_card_text
from ..display import (
    format_clock_control, format_game_activity_time, format_game_timeline, russian_opening_name,
)
from ..game_review import ReviewGraph


class LibraryRowDelegate(QStyledItemDelegate):
    """Keep native selection, keyboard navigation and accessibility for card rows."""

    def paint(self, painter, option, index):
        game = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(game, dict) or option.rect.width() < 430:
            super().paint(painter, option, index)
            return
        shell = QStyleOptionViewItem(option)
        self.initStyleOption(shell, index)
        shell.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, shell,
                                          painter, option.widget)
        rect = option.rect.adjusted(18, 8, -15, -8)
        left = max(145, rect.width() * 29 // 100)
        middle = max(135, rect.width() * 34 // 100)
        first, second = rect.left() + left, rect.left() + left + middle
        heading, _ = game_card_text(game)
        outcome = heading.partition(" с ")[0]
        color = {"Победа": "#79E599", "Поражение": "#FF737C", "Ничья": "#E2E5F0"}.get(
            outcome, "#AEB8CF")
        glyph = {"Победа": "♜", "Поражение": "♛", "Ничья": "="}.get(outcome, "·")
        painter.save()
        painter.setClipRect(rect)

        def text(value, x, y, width, height, *, size=14, color="#F5F6FD", bold=False):
            font = painter.font()
            font.setPixelSize(size)
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(QColor(color))
            content = painter.fontMetrics().elidedText(str(value), Qt.TextElideMode.ElideRight, width)
            painter.drawText(QRect(x, y, width, height),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, content)

        text(f"{glyph}  {outcome}", rect.left(), rect.top(), left - 10, rect.height(),
             size=18, color=color, bold=True)
        half = rect.height() // 2
        text(game.get("account_opponent") or game.get("opponent") or "Соперник",
             first, rect.top(), middle - 12, half, size=16, bold=True)
        activity = format_game_activity_time(game)
        text(activity.partition(": ")[2] or activity, first, rect.top() + half, middle - 12, half,
             color="#AEB8CF")
        painter.setPen(QColor("#303950"))
        painter.drawLine(second, rect.top() + 4, second, rect.bottom() - 4)
        width = rect.right() - second - 10
        text(format_clock_control(game.get("clock")), second + 10, rect.top(), width, half)
        text(russian_opening_name(game.get("opening_eco"), game.get("opening_name")),
             second + 10, rect.top() + half, width, half, color="#AEB8CF")
        painter.restore()


class LibraryPage(QWidget):
    query_requested = Signal(object, int)
    open_requested = Signal(dict)
    export_requested = Signal(dict)
    note_requested = Signal(dict, str)
    analyze_requested = Signal(dict)
    refresh_requested = Signal(dict)
    active_requested = Signal(object)
    detail_requested = Signal(dict)
    detail_position_requested = Signal(dict, int)
    sync_requested = Signal()
    background_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None, *, accounts: list[dict] | None = None,
                 clocks: list[str] | None = None) -> None:
        super().__init__(parent)
        self._page = 0
        self._pages = 1
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        layout.setSpacing(7)
        self.active_title = QLabel("Текущие партии")
        self.active_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.active_title)
        self.active_games = QListWidget()
        self.active_games.setMaximumHeight(112)
        self.active_games.itemClicked.connect(self._open_active)
        self.active_games.itemDoubleClicked.connect(self._open_active)
        layout.addWidget(self.active_games)
        self.summary = QLabel("Завершённые партии · загрузка выборки…")
        self.summary.setObjectName("secondary")
        self.sort_description = QLabel("Сортировка: последняя дата хода, затем дата начала")
        self.sort_description.setObjectName("muted")
        self.sort_description.setToolTip(
            "Порядок использует lastMoveAt, затем подтверждённое время/дату начала; "
            "обновление аналитики его не меняет.")
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(self.sort_description, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(summary_row)
        action_row = QHBoxLayout()
        self.sync_button = QPushButton("Синхронизировать")
        self.sync_button.clicked.connect(self.sync_requested.emit)
        self.background_button = QPushButton("Фоновые операции")
        self.background_button.clicked.connect(self.background_requested.emit)
        self.settings_button = QPushButton("Настройки загрузки")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        for button in (self.sync_button, self.background_button, self.settings_button):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self.sync_status = QLabel("")
        self.sync_status.setWordWrap(True)
        self.sync_status.setObjectName("secondary")
        layout.addWidget(self.sync_status)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по сопернику, дебюту или дате")
        self.search.setAccessibleName("Поиск по партиям")
        self.filters_toggle = QPushButton("Фильтры")
        self.filters_toggle.setCheckable(True)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.filters_toggle)
        layout.addLayout(search_row)
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
                             ("Lichess · оценки ходов", "lichess"),
                             ("Есть официальная точность", "official_accuracy")):
            self.analysis_source_filter.addItem(label, value)
        self.period_filter = QComboBox()
        for label, value in (("Любой период", 0), ("30 дней", 30),
                             ("90 дней", 90), ("1 год", 365)):
            self.period_filter.addItem(label, value)
        filter_panel = QFrame()
        self.filter_panel = filter_panel
        filter_panel.setObjectName("metricCard")
        filter_layout = QVBoxLayout(filter_panel)
        filter_layout.setContentsMargins(8, 8, 8, 8)
        filter_layout.setSpacing(6)
        filter_title = QLabel("Фильтры")
        filter_title.setStyleSheet("font-weight: 700;")
        filter_layout.addWidget(filter_title)
        self.opponent_filter = QLineEdit()
        self.opponent_filter.setPlaceholderText("Соперник")
        filter_layout.addWidget(self.opponent_filter)
        self.opening_filter = QLineEdit()
        self.opening_filter.setPlaceholderText("Дебют / ECO")
        filter_layout.addWidget(self.opening_filter)
        for control in (self.search, self.opponent_filter, self.opening_filter,
                        self.account_filter, self.side_filter, self.result_filter,
                        self.source_filter, self.clock_filter, self.analysis_filter,
                        self.analysis_source_filter, self.period_filter):
            control.setMinimumHeight(28)
        filter_grid = QGridLayout()
        filter_grid.setContentsMargins(0, 0, 0, 0)
        filter_grid.setHorizontalSpacing(6)
        filter_grid.setVerticalSpacing(5)
        filter_widgets = (self.account_filter, self.side_filter, self.result_filter,
                          self.source_filter, self.clock_filter, self.analysis_filter,
                          self.analysis_source_filter, self.period_filter)
        for index, widget in enumerate(filter_widgets):
            filter_grid.addWidget(widget, index // 2, index % 2)
            widget.currentIndexChanged.connect(self._emit_filters)
        filter_layout.addLayout(filter_grid)
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        self.body_splitter = body
        self.games = QListWidget()
        self.games.setItemDelegate(LibraryRowDelegate(self.games))
        self.games.setAccessibleName("Завершённые партии")
        self.games.setStyleSheet("""
            QListWidget { background: transparent; border: none; }
            QListWidget::item {
                background: #191f30; border: 1px solid #303950;
                border-radius: 12px; margin: 3px; padding: 8px 12px;
            }
            QListWidget::item:selected {
                background: #343153; color: #f5f6fd; border: 2px solid #aa9bef;
            }
        """)
        body.addWidget(self.games)
        aside = QWidget()
        aside_layout = QVBoxLayout(aside)
        aside_layout.setContentsMargins(0, 0, 0, 0)
        aside_layout.setSpacing(8)
        aside_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        # Keep the filter grid and selected-game actions at readable heights;
        # compact windows scroll this inspector instead of compressing controls.
        aside.setMinimumHeight(520)
        aside_layout.addWidget(filter_panel)
        filter_panel.hide()
        self.filters_toggle.toggled.connect(filter_panel.setVisible)
        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("metricCard")
        detail_layout = QVBoxLayout(self.detail_panel)
        self.detail_title = QLabel("Выберите партию")
        self.detail_title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.detail_title.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)
        self.detail_metadata = QLabel("Детали и оценки отображаются здесь.")
        self.detail_metadata.setObjectName("secondary")
        self.detail_metadata.setWordWrap(True)
        detail_layout.addWidget(self.detail_metadata)
        self.detail_players = QLabel()
        self.detail_players.setWordWrap(True)
        self.detail_players.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_players.setStyleSheet(
            "background: #232B3E; border: 1px solid #303950; border-radius: 10px;"
            "padding: 10px; font-size: 16px; font-weight: 600;")
        self.detail_players.hide()
        detail_layout.addWidget(self.detail_players)
        self.detail_status = QLabel("Оценки требуют актуальной проверки безопасности.")
        self.detail_status.setObjectName("secondary")
        self.detail_status.setWordWrap(True)
        detail_layout.addWidget(self.detail_status)
        self.detail_metrics = QLabel("")
        self.detail_metrics.setWordWrap(True)
        detail_layout.addWidget(self.detail_metrics)
        self.phase_button = QPushButton("Точность по фазам")
        self.phase_button.setCheckable(True)
        self.phase_button.hide()
        self.phase_detail = QLabel()
        self.phase_detail.setWordWrap(True)
        self.phase_detail.hide()
        self.phase_button.toggled.connect(self.phase_detail.setVisible)
        detail_layout.addWidget(self.phase_button)
        detail_layout.addWidget(self.phase_detail)
        self.detail_graph = ReviewGraph([], [])
        self.detail_graph.compact = True
        self.detail_graph.setMinimumWidth(0)
        self.detail_graph.setMaximumHeight(190)
        self.detail_graph.setMinimumHeight(120)
        self.detail_graph.setVisible(False)
        detail_layout.addWidget(self.detail_graph)
        self.detail_open = QPushButton("Открыть разбор")
        self.detail_open.clicked.connect(lambda: self._emit_for_detail(self.open_requested))
        detail_actions = QHBoxLayout()
        detail_actions.addWidget(self.detail_open)
        self.detail_open.setObjectName("primaryAction")
        aside_layout.addWidget(self.detail_panel, 1)
        self.aside_scroll = QScrollArea()
        self.aside_scroll.setObjectName("libraryAsideScroll")
        self.aside_scroll.setWidgetResizable(True)
        self.aside_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.aside_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.aside_scroll.setWidget(aside)
        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(6)
        inspector_layout.addWidget(self.aside_scroll, 1)
        inspector_layout.addLayout(detail_actions)
        body.addWidget(inspector)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 1)
        body.setSizes([600, 600])
        layout.addWidget(body, 1)
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
        self._note_draft_game_id: str | None = None
        self._note_draft_line_revision: int | None = None
        self._note_drafts: dict[tuple[str, int | None], str] = {}
        self.save_note = QPushButton("Сохранить заметку")
        self.save_note.setEnabled(False)
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
        self.open_game.hide()
        for button in (self.export_game, self.analyze_game):
            detail_actions.addWidget(button)
        for button in (self.refresh_game, self.close_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._typing_filter_timer = QTimer(self)
        self._typing_filter_timer.setSingleShot(True)
        self._typing_filter_timer.setInterval(275)
        self._typing_filter_timer.timeout.connect(self._emit_filters)
        self.search.textChanged.connect(lambda _value: self._typing_filter_timer.start())
        self.opponent_filter.textChanged.connect(lambda _value: self._typing_filter_timer.start())
        self.opening_filter.textChanged.connect(lambda _value: self._typing_filter_timer.start())
        self.previous.clicked.connect(lambda _checked=False: self._request_page(self._page - 1))
        self.following.clicked.connect(lambda _checked=False: self._request_page(self._page + 1))
        self.games.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.open_game.clicked.connect(lambda _checked=False: self._open_selected())
        self.export_game.clicked.connect(lambda _checked=False: self._emit_for_detail(
            self.export_requested))
        self.save_note.clicked.connect(lambda _checked=False: self._emit_note())
        self.analyze_game.clicked.connect(lambda _checked=False: self._emit_for_detail(
            self.analyze_requested))
        self.refresh_game.clicked.connect(lambda _checked=False: self._emit_for_selected(
            self.refresh_requested))
        self.games.currentItemChanged.connect(self._selection_changed)
        self.note.textChanged.connect(self._note_draft_changed)
        self.detail_graph.selected_ply.connect(self._detail_graph_selected)
        self._detail_game: dict | None = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep ordering help available as a tooltip while giving the game count
        # enough room on compact windows. The action buttons have their own row.
        self.sort_description.setVisible(self.width() >= 760)

    def show_game_detail(self, game: dict | None, review: dict | None = None, *,
                         safety_allowed: bool = False) -> None:
        previous = self._detail_game
        self._detail_game = dict(game) if game else None
        if not safety_allowed or not previous or not game or any(
                previous.get(key) != game.get(key) for key in ("game_id", "line_revision")):
            self.phase_button.setChecked(False)
        self.phase_button.hide()
        self.phase_detail.clear()
        self.phase_detail.hide()
        if not game:
            self.detail_title.setText("Выберите партию")
            self.detail_title.setStyleSheet("font-size: 24px; font-weight: 700;")
            self.detail_players.clear()
            self.detail_players.hide()
            self.detail_metadata.setText("Детали партии появятся здесь.")
            self.detail_status.setText("Оценки требуют актуальной проверки безопасности.")
            self.detail_metrics.clear()
            self.detail_graph.setVisible(False)
            self.detail_graph.set_safety_gate(False, "Выберите партию")
            self.detail_open.setEnabled(False)
            return
        title = str(game.get("account_opponent") or game.get("opponent") or "Соперник")
        heading, _ = game_card_text(game)
        outcome = heading.partition(" с ")[0]
        self.detail_title.setText(f"{outcome} · {title}")
        outcome_color = {"Победа": "#79E599", "Поражение": "#FF737C"}.get(outcome, "#F5F6FD")
        self.detail_title.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {outcome_color};")
        white, black = game.get("white_player"), game.get("black_player")
        score = {"white": "1 – 0", "black": "0 – 1"}.get(game.get("winner"))
        if outcome == "Ничья":
            score = "½ – ½"
        self.detail_players.setVisible(bool(white and black))
        self.detail_players.setText(f"{white}   {score or '—'}   {black}" if white and black else "")
        date = format_game_timeline(game)
        clock = format_clock_control(game.get("clock"))
        opening = russian_opening_name(game.get("opening_eco"), game.get("opening_name"))
        self.detail_metadata.setText(f"{date} · {clock}\n{opening}")
        self.detail_open.setEnabled(True)
        if not safety_allowed:
            self.detail_status.setText("Оценки и график скрыты до подтверждения безопасности Lichess.")
            self.detail_metrics.clear()
            self.detail_graph.assessments = []
            self.detail_graph.markers = {}
            self.detail_graph.set_safety_gate(False, "Оценки скрыты правилами Fair Play")
            self.detail_graph.setVisible(True)
            self.detail_graph.update()
            return
        if review is None:
            self.detail_status.setText("Загружаю сохранённые данные партии…")
            self.detail_metrics.clear()
            self.detail_graph.setVisible(False)
            return
        analysis = review.get("analysis") or {}
        assessments = list(analysis.get("assessments") or [])
        markers = list(analysis.get("move_metrics") or [])
        source = analysis.get("source") or ""
        source_name = ("Stockfish" if source == "stockfish" else
                       "Lichess" if source.startswith("lichess") else "Сохранённые оценки")
        self.detail_status.setText(
            f"{source_name} · "
            f"охват {review.get('coverage', {}).get('scored', 0)}/"
            f"{review.get('coverage', {}).get('total', 0)} позиций")
        sides = review.get("per_side") or {}
        official = review.get("official_accuracy") or {}
        local = review.get("local_game_accuracy") or {}

        def accuracy_for(side):
            if official.get(side) is not None:
                return f"{official[side]:.1f}% · Lichess"
            if local.get(side) is not None:
                return f"{local[side]:.1f}% · локально"
            return "—"

        def classified(side, key):
            counts = sides.get(side) or {}
            return str(counts.get(key, 0)) if counts.get("assessed") else "—"

        lines = [f"Точность · белые / чёрные: {accuracy_for('white')} / {accuracy_for('black')}"]
        for label, key in (("Неточности", "inaccuracy"), ("Ошибки", "mistake"), ("Зевки", "blunder")):
            lines.append(f"{label} · {classified('white', key)} / {classified('black', key)}")
        self.detail_metrics.setText("\n".join(lines))
        phase_values = review.get("local_phase_accuracy") or {}
        if phase_values:
            phase_lines = ["Локальная точность · белые / чёрные"]
            for phase, label in (("opening", "Дебют"), ("middlegame", "Миттельшпиль"),
                                 ("endgame", "Эндшпиль")):
                values = phase_values.get(phase, {})
                sides_text = ["—" if values.get(side) is None else f"{values[side]:.1f}%"
                              for side in ("white", "black")]
                phase_lines.append(f"{label}: {' / '.join(sides_text)}")
            self.phase_detail.setText("\n".join(phase_lines))
            self.phase_button.show()
            self.phase_detail.setVisible(self.phase_button.isChecked())
        self.detail_graph.assessments = assessments
        self.detail_graph.markers = {int(row["ply"]): row for row in markers}
        self.detail_graph.full_ply_count = len(review.get("moves") or [])
        self.detail_graph.analysis_source = str(analysis.get("source") or "")
        self.detail_graph.analysis_status = str(analysis.get("status") or "")
        self.detail_graph.coverage_text = self.detail_status.text()
        self.detail_graph.move_san = {int(move.ply): str(move.san)
                                      for move in review.get("moves") or []}
        self.detail_graph.set_markers_visible(True)
        self.detail_graph.set_safety_gate(True)
        self.detail_graph.setVisible(True)
        self.detail_graph.update()

    def _detail_graph_selected(self, ply: int) -> None:
        game = self._detail_game
        if game is not None:
            self.detail_position_requested.emit(dict(game), int(ply))

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
            payload = session.get("server_payload")
            if isinstance(payload, dict):
                item.setData(Qt.ItemDataRole.UserRole + 1, payload)
            self.active_games.addItem(item)

    def _open_active(self, item) -> None:
        game_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if game_id:
            payload = item.data(Qt.ItemDataRole.UserRole + 1)
            self.active_requested.emit(payload if isinstance(payload, dict) else str(game_id))

    def _request_page(self, page: int) -> None:
        self._emit_filters(max(0, min(self._pages - 1, page)))

    def current_filters(self) -> dict:
        return {
            "query": self.search.text(),
            "opponent": self.opponent_filter.text().strip(),
            "opening": self.opening_filter.text().strip(),
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
            item.setSizeHint(QSize(0, 74))
            if row_text.startswith("Победа"):
                item.setForeground(QColor("#79E599"))
            elif row_text.startswith("Поражение"):
                item.setForeground(QColor("#FF737C"))
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
        self.summary.setText(f"Завершённые партии · {max(0, int(total))} в текущей выборке")
        self.previous.setEnabled(self._page > 0)
        self.following.setEnabled(self._page + 1 < self._pages)
        self._selection_changed(self.games.currentItem(), None)

    def selected_game(self) -> dict | None:
        item = self.games.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, dict) else None

    def set_sync_status(self, text: str) -> None:
        self.sync_status.setText(str(text))

    def set_filter_options(self, options: dict) -> None:
        pending = getattr(self, "_pending_filter_values", {})
        account = pending.get("account_id", self.account_filter.currentData())
        clock = pending.get("clock", self.clock_filter.currentData())
        self.account_filter.blockSignals(True)
        self.clock_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("Все аккаунты", None)
        for item in options.get("accounts", []):
            self.account_filter.addItem(str(item["username"]), str(item["account_id"]))
        self.clock_filter.clear()
        self.clock_filter.addItem("Любой контроль", "")
        for value in sorted(set(options.get("clocks", []))):
            self.clock_filter.addItem(format_clock_control(value), value)
        account_index = self.account_filter.findData(account)
        clock_index = self.clock_filter.findData(clock)
        if account_index >= 0:
            self.account_filter.setCurrentIndex(account_index)
        if clock_index >= 0:
            self.clock_filter.setCurrentIndex(clock_index)
        self._pending_filter_values = {}
        self.account_filter.blockSignals(False)
        self.clock_filter.blockSignals(False)

    def _emit_for_selected(self, signal) -> None:
        game = self.selected_game()
        if game is not None:
            signal.emit(game)

    def _emit_for_detail(self, signal) -> None:
        if self._detail_game is not None:
            signal.emit(dict(self._detail_game))

    def _open_selected(self) -> None:
        self._emit_for_selected(self.open_requested)

    def _emit_note(self) -> None:
        game = self.selected_game()
        if game is None:
            return
        if (self._note_draft_game_id != str(game.get("game_id") or "")
                or self._note_draft_line_revision != self._line_revision(game)):
            self.save_note.setToolTip(
                "Черновик заметки привязан к другой партии. Выберите исходную партию, чтобы сохранить его.")
            return
        self.note_requested.emit(game, self.note.text())

    def _note_draft_changed(self, text: str) -> None:
        game = self.selected_game()
        current_id = str(game.get("game_id") or "") if game else None
        current_revision = self._line_revision(game) if game else None
        if self._note_draft_game_id is None:
            self._note_draft_game_id = current_id
            self._note_draft_line_revision = current_revision
        if current_id and current_id == self._note_draft_game_id:
            self._note_drafts[(current_id, self._note_draft_line_revision)] = str(text)
        enabled = bool(text.strip() and current_id
                       and current_id == self._note_draft_game_id
                       and current_revision == self._note_draft_line_revision)
        self.save_note.setEnabled(enabled)
        self.save_note.setToolTip("" if enabled else
            "Черновик заметки привязан к другой партии; выберите её для сохранения.")

    def clear_saved_note_draft(self, game_id: str, line_revision: int | None,
                               saved_text: str) -> None:
        key = (str(game_id), line_revision)
        if self._note_drafts.get(key) == str(saved_text):
            self._note_drafts.pop(key, None)

    @staticmethod
    def _line_revision(game: dict | None) -> int | None:
        if not isinstance(game, dict) or game.get("line_revision") is None:
            return None
        try:
            return int(game["line_revision"])
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _item_game(item) -> dict | None:
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, dict) else None

    def _selection_changed(self, current, previous) -> None:
        old_game = self._item_game(previous)
        if old_game:
            old_id = str(old_game.get("game_id") or "")
            old_revision = self._line_revision(old_game)
            if old_id:
                self._note_drafts[(old_id, old_revision)] = self.note.text()
        enabled = current is not None and self.selected_game() is not None
        for button in (self.open_game, self.export_game, self.analyze_game):
            button.setEnabled(enabled)
        game = self.selected_game()
        current_id = str(game.get("game_id") or "") if game else None
        current_revision = self._line_revision(game) if game else None
        self._note_draft_game_id = current_id
        self._note_draft_line_revision = current_revision
        restored = self._note_drafts.get((current_id, current_revision), "") if current_id else ""
        self.note.blockSignals(True)
        self.note.setText(restored)
        self.note.blockSignals(False)
        self._note_draft_changed(restored)
        if game is None:
            self.show_game_detail(None)
        else:
            self.show_game_detail(game)
            self.detail_requested.emit(game)
