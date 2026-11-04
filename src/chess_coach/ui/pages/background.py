"""Read-only view of the shared background-work registry and backfill controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget,
)


class BackgroundOperationsPage(QWidget):
    pause_requested = Signal(bool)
    manual_check_requested = Signal()
    refresh_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        title = QLabel("Фоновые операции")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        self.summary = QLabel("Статус фоновой работы ещё не загружен.")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.operation_list = QListWidget()
        self.operation_list.setAccessibleName("Текущие фоновые операции")
        root.addWidget(self.operation_list, 1)
        self.source_summary = QLabel("")
        self.source_summary.setWordWrap(True)
        self.source_summary.setObjectName("secondary")
        root.addWidget(self.source_summary)
        self.retry_status = QLabel("")
        self.retry_status.setWordWrap(True)
        self.retry_status.setObjectName("secondary")
        root.addWidget(self.retry_status)
        controls = QHBoxLayout()
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
        root.addLayout(controls)
        self.close_button = QPushButton("Назад")
        self.close_button.clicked.connect(self.close_requested.emit)
        root.addWidget(self.close_button)
        self._paused = False

    def set_snapshot(self, operations, *, source_summary: str,
                     backfill_paused: bool, can_check: bool, can_pause: bool,
                     retry_status: str = "", durable_statuses=()) -> None:
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
        self.summary.setText(
            f"Активных операций: {len(operations)} · сохранённых статусов: "
            f"{len(tuple(durable_statuses))}. Онлайн-ходы и проверки безопасности "
            "используют отдельные приоритетные ресурсы.")
        self.source_summary.setText(source_summary)
        self.retry_status.setText(retry_status)
        self._paused = bool(backfill_paused)
        self.pause_button.setText("Возобновить Lichess" if self._paused
                                  else "Приостановить Lichess")
        self.pause_button.setEnabled(bool(can_pause))
        self.manual_check_button.setEnabled(bool(can_check and not self._paused))
