"""Read-only view of the shared background-work registry and backfill controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget,
)


class BackgroundOperationsPage(QWidget):
    pause_requested = Signal(bool)
    manual_check_requested = Signal()
    refresh_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setSpacing(12)
        banner = QFrame()
        banner.setObjectName("metricCard")
        controls = QHBoxLayout(banner)
        self.summary = QLabel("Загружаю состояние фоновых операций…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 19px; font-weight: 700;")
        controls.addWidget(self.summary, 1)
        root.addWidget(banner)
        self.workflow_status = {}
        for key, caption in (
                ("archive", "Синхронизация партий Lichess"),
                ("ready", "Готовая аналитика Lichess"),
                ("puzzles", "Задачи Lichess"),
                ("stockfish", "Фоновый Stockfish")):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setMinimumHeight(62)
            row = QHBoxLayout(card)
            row.setContentsMargins(18, 11, 18, 11)
            title = QLabel(caption)
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            title.setWordWrap(True)
            detail = QLabel("Загружаю состояние…")
            detail.setObjectName("secondary")
            detail.setWordWrap(True)
            detail.setAccessibleName(caption)
            row.addWidget(title, 4)
            row.addWidget(detail, 6)
            root.addWidget(card)
            self.workflow_status[key] = detail
        self.details_toggle = QPushButton("Диагностика операций")
        self.details_toggle.setCheckable(True)
        root.addWidget(self.details_toggle)
        self.operation_list = QListWidget()
        self.operation_list.setAccessibleName("Текущие фоновые операции")
        root.addWidget(self.operation_list)
        self.operation_list.hide()
        self.details_toggle.toggled.connect(self.operation_list.setVisible)
        self.source_summary = QLabel("")
        self.source_summary.setWordWrap(True)
        self.source_summary.setObjectName("secondary")
        self.source_summary.hide()
        self.local_metrics_status = QLabel("Локальные метрики v3: состояние не загружено.")
        self.local_metrics_status.setWordWrap(True)
        self.local_metrics_status.setObjectName("secondary")
        self.local_metrics_status.setAccessibleName("Фоновое обновление метрик сохранённой аналитики")
        root.addWidget(self.local_metrics_status)
        self.retry_status = QLabel("")
        self.retry_status.setWordWrap(True)
        self.retry_status.setObjectName("secondary")
        root.addWidget(self.retry_status)
        self.pause_button = QPushButton("Приостановить Lichess")
        self.pause_button.clicked.connect(
            lambda: self.pause_requested.emit(not self._paused))
        self.manual_check_button = QPushButton("Проверить готовые данные")
        self.manual_check_button.clicked.connect(self.manual_check_requested.emit)
        self.refresh_button = QPushButton("Обновить статус")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.manual_check_button)
        controls.addWidget(self.refresh_button)
        root.addStretch(1)
        self.close_button = QPushButton("Назад")
        self.close_button.clicked.connect(self.close_requested.emit)
        root.addWidget(self.close_button)
        self._paused = False

    def set_snapshot(self, operations, *, source_summary: str,
                     backfill_paused: bool, can_check: bool, can_pause: bool,
                     retry_status: str = "", durable_statuses=(),
                     local_metrics_status: str | None = None, workflows=None) -> None:
        durable_statuses = tuple(durable_statuses)
        self.operation_list.clear()
        for operation in operations:
            context = operation.context
            progress = ""
            if context and operation.progress_total is not None:
                progress = f" · {operation.progress_done or 0}/{operation.progress_total}"
            self.operation_list.addItem(
                f"{context.kind if context else operation.lane.value} · "
                f"{operation.status.value}{progress}")
        for status in durable_statuses:
            self.operation_list.addItem(str(status))
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
        self.source_summary.setText(source_summary)
        if local_metrics_status is not None:
            self.local_metrics_status.setText(local_metrics_status)
        self.retry_status.setText(retry_status)
        self._paused = bool(backfill_paused)
        self.pause_button.setText("Возобновить Lichess" if self._paused
                                  else "Приостановить Lichess")
        self.pause_button.setEnabled(bool(can_pause))
        self.manual_check_button.setEnabled(bool(can_check and not self._paused))
