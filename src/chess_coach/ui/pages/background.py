"""Read-only view of the shared background-work registry and backfill controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)


class BackgroundOperationsPage(QWidget):
    pause_requested = Signal(bool)
    manual_check_requested = Signal()
    refresh_requested = Signal()
    close_requested = Signal()
    batch_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
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
        self._root_layout.setSpacing(6 if compact else 8)
        self._banner.setMinimumHeight(58 if compact else 76)
        self.summary.setStyleSheet(
            f"font-size: {16 if compact else 19}px; font-weight: 700;")
        for card, icon, title in self._workflow_cards:
            card.setMinimumHeight(70 if compact else 100)
            card.layout().setContentsMargins(10 if compact else 14,
                                             4 if compact else 6,
                                             10 if compact else 14,
                                             4 if compact else 6)
            icon.setFixedSize(32 if compact else 43, 32 if compact else 43)
            icon.setStyleSheet(
                "background: #39365E; color: #C8BBFF; border-radius: 10px; "
                f"font-size: {20 if compact else 26}px;")
            title.setStyleSheet(
                f"font-size: {16 if compact else 18}px; font-weight: 700;")
        # Keep all four approved desktop event rows inside the list viewport.
        self.recent_events.setMinimumHeight(84 if compact else 162)
        self.recent_events.setMaximumHeight(104 if compact else 170)
        self.recent_events_title.setStyleSheet(
            f"font-size: {19 if compact else 23}px; font-weight: 700;")
        self.details_toggle.setMaximumHeight(25 if compact else 30)

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
            label.setText((workflows or {}).get(key) or "Состояние не подтверждено.")
            label.setToolTip((workflow_tooltips or {}).get(key, ""))
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
