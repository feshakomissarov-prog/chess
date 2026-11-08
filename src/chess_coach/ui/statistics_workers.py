"""Application-scoped dispatcher for statistics jobs that outlive their page."""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication


class StatisticsWorkerHub(QObject):
    """Own worker handles and deliver results only from a live application QObject.

    Worker callables capture the thread-safe completion queue and plain Python
    service/repository objects. They never capture a dialog or emit a Qt signal.
    The application-owned timer drains completed results on the GUI thread;
    Qt then automatically drops a result connection when its page is destroyed.
    """

    query_completed = Signal(str, int, object, object, object, object)
    opening_completed = Signal(str, int, object, object)
    worker_started = Signal(object)
    worker_finished = Signal(object)

    _APP_ATTRIBUTE = "_chess_coach_statistics_worker_hub"

    @classmethod
    def for_application(cls) -> "StatisticsWorkerHub":
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Statistics workers require an active QApplication")
        hub = getattr(app, cls._APP_ATTRIBUTE, None)
        if hub is None:
            hub = cls(app)
            setattr(app, cls._APP_ATTRIBUTE, hub)
        return hub

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._completed: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._threads: set[threading.Thread] = set()
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(10)
        self._drain_timer.timeout.connect(self._drain_completed)
        self._drain_timer.start()

    @property
    def active_threads(self) -> tuple[threading.Thread, ...]:
        return tuple(self._threads)

    def submit_query(self, dialog_key: str, generation: int, service,
                     filters: dict, page: int) -> threading.Thread:
        completed = self._completed

        def run() -> None:
            summary = parties = error = None
            try:
                summary = service.summary(**filters)
                parties = service.game_page(**filters, page=page, page_size=100)
                if filters["account_id"]:
                    summary["personal_profile"] = service.personal_profile(
                        account_id=filters["account_id"], clock=filters["clock"],
                        side=filters["side"], analysis_source=filters["analysis_source"])
            except Exception as exc:
                error = str(exc)
            completed.put(("query", dialog_key, generation, summary, parties,
                           error, threading.current_thread()))

        return self._start(run, "chess-coach-statistics")

    def submit_opening_refresh(self, dialog_key: str, generation: int,
                               repository, filters: dict) -> threading.Thread:
        completed = self._completed

        def run() -> None:
            try:
                from ..lichess.opening_matcher import OpeningMatcher
                matched = OpeningMatcher().match_recent(repository, limit=25)
                phases = repository.infer_missing_phase_boundaries(limit=25)
                from ..lichess.statistics import StatisticsService
                groups = StatisticsService(repository).opening_statistics(
                    account_id=filters["account_id"], analysis_source=filters["analysis_source"],
                    since=filters["since"], until=filters["until"], min_games=1)
                payload = {"matched": matched, "phases": phases, "groups": groups}
            except Exception as exc:
                payload = {"error": str(exc)}
            completed.put(("opening", dialog_key, generation, payload,
                           threading.current_thread()))

        return self._start(run, "chess-coach-opening-index")

    def _start(self, target, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.add(thread)
        self.worker_started.emit(thread)
        thread.start()
        return thread

    def _drain_completed(self) -> None:
        # The final queue put occurs immediately before the worker returns. If
        # the OS still reports it alive, retry next tick instead of publishing a
        # finished handle while it can still touch the repository.
        for _ in range(64):
            try:
                event = self._completed.get_nowait()
            except queue.Empty:
                return
            thread = event[-1]
            if thread.is_alive():
                self._completed.put(event)
                return
            self._threads.discard(thread)
            if event[0] == "query":
                _, dialog_key, generation, summary, parties, error, _thread = event
                self.query_completed.emit(dialog_key, generation, summary, parties, error, thread)
            else:
                _, dialog_key, generation, payload, _thread = event
                self.opening_completed.emit(dialog_key, generation, payload, thread)
            self.worker_finished.emit(thread)
