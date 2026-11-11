"""Read-only view of the shared background-work registry and backfill controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)


class BackgroundOperationsPage(QWidget):
    pause_requested = Signal(bool)
    manual_check_requested = Signal()
    refresh_requested = Signal()
    close_requested = Signal()
    batch_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("backgroundContentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget(self.content_scroll)
        self.content.setObjectName("backgroundContent")
        self.content_scroll.setWidget(self.content)
        outer.addWidget(self.content_scroll)
        root = QVBoxLayout(self.content)
        self._root_layout = root
        root.setSpacing(12)
        banner = QFrame()
        self._banner = banner
        banner.setObjectName("metricCard")
        banner.setMinimumHeight(76)
        controls = QHBoxLayout(banner)
        self.summary = QLabel("Загружаю состояние фоновых операций…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 19px; font-weight: 700;")
        controls.addWidget(self.summary, 1)
        self.pause_button = QPushButton("Приостановить Lichess")
        self.pause_button.clicked.connect(
            lambda: self.pause_requested.emit(not self._paused))
        self.refresh_button = QPushButton("Обновить статус")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.refresh_button)
        root.addWidget(banner)
        self.workflow_status = {}
        self._workflow_status_source = {}
        self._compact_status = False
        self._tight_status = False
        self.workflow_badges = {}
        self.workflow_progress = {}
        self._workflow_rows = {}
        self._workflow_cards = []
        for key, glyph, caption in (
                ("archive", "⟳", "Синхронизация партий Lichess"),
                ("ready", "▱", "Готовая аналитика Lichess"),
                ("puzzles", "✓", "Задачи Lichess"),
                ("stockfish", "♞", "Фоновый Stockfish")):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setMinimumHeight(112)
            row = QHBoxLayout(card)
            row.setContentsMargins(18, 11, 18, 11)
            icon = QLabel(glyph)
            icon.setFixedSize(43, 43)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setStyleSheet("background: #39365E; color: #C8BBFF;"
                               "border-radius: 10px; font-size: 26px;")
            row.addWidget(icon)
            title = QLabel(caption)
            self._workflow_cards.append((card, icon, title))
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            title.setWordWrap(True)
            detail = QLabel("Загружаю состояние…")
            detail.setObjectName("secondary")
            detail.setWordWrap(True)
            detail.setAccessibleName(caption)
            row.addWidget(title, 4)
            badge = QLabel("Ожидает")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(95)
            badge.setMaximumHeight(34)
            row.addWidget(badge)
            if key == "ready":
                self.manual_check_button = QPushButton("Проверить сейчас")
                self.manual_check_button.setObjectName("secondaryAction")
                self.manual_check_button.setToolTip(
                    "Запросить уже опубликованные оценки Lichess, если партия доступна для проверки.")
                self.manual_check_button.clicked.connect(self.manual_check_requested.emit)
                row.addWidget(self.manual_check_button)
            elif key == "stockfish":
                self.batch_button = QPushButton("Очередь Stockfish")
                self.batch_button.clicked.connect(self.batch_requested.emit)
                row.addWidget(self.batch_button)
            detail_column = QVBoxLayout()
            detail_column.setSpacing(6)
            detail_column.addWidget(detail, 1)
            row.addLayout(detail_column, 6)
            bar = QProgressBar()
            bar.setAccessibleName(f"{caption} · прогресс")
            bar.setMaximumWidth(220)
            bar.setStyleSheet(
                "QProgressBar { background: #303950; border-radius: 7px;"
                "color: #F5F6FD; min-height: 20px; text-align: center; }"
                "QProgressBar::chunk { background: #43795B; border-radius: 7px; }")
            bar.hide()
            row.addWidget(bar, 2)
            root.addWidget(card)
            self.workflow_status[key] = detail
            self.workflow_badges[key] = badge
            self.workflow_progress[key] = bar
            self._workflow_rows[key] = row
        self.details_toggle = QPushButton("Диагностика операций")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setObjectName("backgroundDiagnosticsToggle")
        self.details_toggle.setMaximumHeight(30)
        self.operation_list = QListWidget()
        self.operation_list.setAccessibleName("Текущие фоновые операции")
        self.operation_list.hide()
        self.details_toggle.toggled.connect(self.operation_list.setVisible)
        self.recent_events_title = QLabel("Последние события")
        self.recent_events_title.setStyleSheet("font-size: 23px; font-weight: 700;")
        events_header = QHBoxLayout()
        events_header.setObjectName("backgroundEventsHeader")
        events_header.addWidget(self.recent_events_title, 1)
        events_header.addWidget(self.details_toggle)
        root.addLayout(events_header)
        root.addWidget(self.operation_list)
        self.recent_events = QListWidget()
        self.recent_events.setAccessibleName("Последние сохранённые события")
        self.recent_events.setMinimumHeight(162)
        self.recent_events.setMaximumHeight(170)
        # The shared visual theme gives list rows generous 5px padding. Recent
        # events need a denser, still readable 1px vertical rhythm so four
        # saved rows fit in the approved desktop card without scrolling.
        self.recent_events.setStyleSheet("QListWidget::item { padding: 1px 8px; }")
        root.addWidget(self.recent_events)
        self.source_summary = QLabel("")
        self.source_summary.setWordWrap(True)
        self.source_summary.setObjectName("secondary")
        self.source_summary.hide()
        self.local_metrics_status = QLabel("Локальные метрики v3: состояние не загружено.")
        self.local_metrics_status.setWordWrap(True)
        self.local_metrics_status.setObjectName("secondary")
        self.local_metrics_status.setAccessibleName("Фоновое обновление метрик сохранённой аналитики")
        self.local_metrics_status.hide()
        root.addWidget(self.local_metrics_status)
        self.retry_status = QLabel("")
        self.retry_status.setWordWrap(True)
        self.retry_status.setObjectName("secondary")
        self.retry_status.hide()
        root.addWidget(self.retry_status)
        root.addStretch(1)
        self.close_button = QPushButton("Назад")
        self.close_button.clicked.connect(self.close_requested.emit)
        root.addWidget(self.close_button)
        self._paused = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        compact = self.height() < 740
        tight = self.height() < 540
        self._compact_status = compact
        self._tight_status = tight
        self._root_layout.setSpacing(2 if tight else 6 if compact else 8)
        self._banner.setMinimumHeight(52 if tight else 58 if compact else 76)
        self.summary.setStyleSheet(
            f"font-size: {14 if tight else 16 if compact else 19}px; font-weight: 700;")
        for card, icon, title in self._workflow_cards:
            card.setMinimumHeight(60 if tight else 70 if compact else 100)
            card.layout().setContentsMargins(8 if tight else 10 if compact else 14,
                                             2 if tight else 4 if compact else 6,
                                             8 if tight else 10 if compact else 14,
                                             2 if tight else 4 if compact else 6)
            icon.setFixedSize(28 if tight else 32 if compact else 43,
                              28 if tight else 32 if compact else 43)
            icon.setStyleSheet(
                "background: #39365E; color: #C8BBFF; border-radius: 10px; "
                f"font-size: {18 if tight else 20 if compact else 26}px;")
            title.setStyleSheet(
                f"font-size: {14 if tight else 16 if compact else 18}px; font-weight: 700;")
        # Keep all four approved desktop event rows inside the list viewport.
        self.recent_events.setMinimumHeight(64 if tight else 84 if compact else 162)
        self.recent_events.setMaximumHeight(76 if tight else 104 if compact else 170)
        self.recent_events_title.setStyleSheet(
            f"font-size: {17 if tight else 19 if compact else 23}px; font-weight: 700;")
        self.details_toggle.setMaximumHeight(22 if tight else 25 if compact else 30)
        self._render_workflow_statuses()
        # Updating row minimum heights here can happen while the page is being
        # resized inside the stacked workspace. Re-activate the vertical layout
        # so Qt does not retain the prior compact geometry and overlap cards.
        self._root_layout.activate()

    @staticmethod
    def _compact_ready_status(text: str) -> str:
        """Keep the important queue counts and next check visible in short cards.

        The full schedule/retry explanation remains available in the tooltip. A
        three-line server status otherwise clips on 720p Windows at 125% scale.
        """
        lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        if len(lines) < 3:
            return str(text)
        counts = lines[0].replace("точность", "точн.").replace("оценки", "оценок")
        detail = (lines[1].replace("Без анализа", "Без данных")
                  .replace("без анализа", "без данных")
                  .replace("повторы", "повтор"))
        schedule = lines[2]
        import re
        match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}:\d{2})", schedule)
        if match:
            date = f"{match.group(1)}.{match.group(2)}"
            schedule = f"Далее: {date} {match.group(4)}"
        else:
            replacements = (
                ("Следующая проверка:", "Далее:"),
                ("после восстановления игрового потока Lichess", "после связи"),
                ("после завершения активной партии", "после партии"),
                ("автоматическая проверка выключена", "авто-проверка выключена"),
                ("очередь пуста или серверных данных больше не ожидается", "очередь пуста"),
                ("готова к запуску", "готова"),
            )
            for old, new in replacements:
                schedule = schedule.replace(old, new)
        # Shorten count labels while retaining every count; full text is in tooltip.
        detail = detail.replace("ошибки", "ошибок")
        return f"{counts}\n{detail} · {schedule}"

    def _render_workflow_statuses(self) -> None:
        for key, label in self.workflow_status.items():
            source = self._workflow_status_source.get(key, label.text())
            shown = self._compact_ready_status(source) if (
                self._compact_status and key == "ready") else source
            label.setText(shown)
            label.setWordWrap(True)
            label.setStyleSheet(
                ("font-size: 14px;" if self._tight_status else "font-size: 15px;")
                if self._compact_status else "")

    def set_snapshot(self, operations, *, source_summary: str,
                     backfill_paused: bool, can_check: bool, can_pause: bool,
                     retry_status: str = "", durable_statuses=(),
                     local_metrics_status: str | None = None, workflows=None, progress=None,
                     recent_events=(), states=None, workflow_tooltips=None) -> None:
        self.recent_events.clear()
        self.recent_events.addItems(list(recent_events) or ["Нет сохранённых событий."])
        durable_statuses = tuple(durable_statuses)
        self.operation_list.clear()
        for operation in operations:
            context = operation.context
            progress_text = ""
            if context and operation.progress_total is not None:
                progress_text = f" · {operation.progress_done or 0}/{operation.progress_total}"
            self.operation_list.addItem(
                f"{context.kind if context else operation.lane.value} · "
                f"{operation.status.value}{progress_text}")
        for status in durable_statuses:
            self.operation_list.addItem(str(status))
        if local_metrics_status is not None:
            self.local_metrics_status.setText(local_metrics_status)
            if local_metrics_status.strip():
                self.operation_list.addItem(f"Метрики: {local_metrics_status}")
        if retry_status.strip():
            self.operation_list.addItem(f"Ограничение/повтор: {retry_status}")
        if self.operation_list.count() == 0:
            self.operation_list.addItem("Активных фоновых операций нет.")
        active = [operation for operation in operations
                  if operation.status.value == "running"
                  and operation.lane.value not in {"long_lived_stream", "safety_control"}
                  and operation.context is not None
                  and operation.context.kind != "background_status_read"]
        names = {"stockfish_batch_analysis": "массовый анализ Stockfish",
                 "lichess_archive_sync": "синхронизация архива",
                 "lichess_puzzle_sync": "синхронизация задач",
                 "lichess_analysis_backfill": "проверка готовых оценок"}
        recognized = [names[op.context.kind] for op in active if op.context.kind in names]
        self.summary.setText(
            "Сейчас: " + ", ".join(dict.fromkeys(recognized)) if recognized
            else f"Сейчас выполняется задач: {len(active)}" if active
            else "Сейчас активных задач нет")
        for key, label in self.workflow_status.items():
            source = (workflows or {}).get(key) or "Состояние не подтверждено."
            self._workflow_status_source[key] = source
            label.setText(source)
            label.setToolTip((workflow_tooltips or {}).get(key, source))
            state = (states or {}).get(key, "Ожидает")
            self.set_workflow_state(key, state)
            bar = self.workflow_progress[key]
            done, total = (progress or {}).get(key, (0, 0))
            # Ready analytics is a deferred API worklist: retries, Retry-After
            # and not-yet-due games make checked/total a misleading completion
            # percentage. Its truthful counters and next attempt live in the
            # explanatory status text instead.
            bar.setVisible(total > 0 and key != "ready")
            if total > 0:
                bar.setRange(0, total)
                bar.setValue(max(0, min(done, total)))
        self.source_summary.setText(source_summary)
        self.retry_status.setText(retry_status)
        self.retry_status.setVisible(bool(retry_status.strip()))
        self.retry_status.setStyleSheet(
            "background: #232B3E; border: 1px solid #303950; border-radius: 10px; "
            "padding: 10px 14px; color: #D5D9E5;")
        self._paused = bool(backfill_paused)
        self.pause_button.setText("Возобновить Lichess" if self._paused
                                  else "Приостановить Lichess")
        self.pause_button.setEnabled(bool(can_pause))
        self.manual_check_button.setEnabled(bool(can_check and not self._paused))
        self._render_workflow_statuses()

    def set_workflow_state(self, key: str, state: str) -> None:
        colors = {"Идёт": ("#234B38", "#79E599"), "Готово": ("#234B38", "#79E599"),
                  "Ошибка": ("#4B2936", "#FFB1B7")}
        background, foreground = colors.get(state, ("#383349", "#E3D8FF"))
        badge = self.workflow_badges[key]
        badge.setText(state)
        if key == "stockfish":
            self.batch_button.setText("Пауза Stockfish" if state == "Идёт" else "Очередь Stockfish")
        badge.setStyleSheet(f"background: {background}; color: {foreground}; "
                            "border-radius: 8px; padding: 6px; font-weight: 600;")
