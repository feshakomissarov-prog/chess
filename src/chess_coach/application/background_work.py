"""Operation registry with independent execution lanes, not a global work queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import threading
import time
import uuid

from ..domain.models import JobContext
from ..domain.work_context import reset_work_priority, set_work_priority
from ..diagnostics import log_event, reset_context, set_context


_CANCELLATION_CODES = {
    "user_cancelled", "shutdown", "account_switched", "session_replaced",
    "safety_revoked", "rate_limited", "paused", "worker_stop_event",
}


def _safe_cancellation_code(reason: object) -> str:
    value = str(reason)
    return value if value in _CANCELLATION_CODES else "cancelled"


class OperationLane(StrEnum):
    INTERACTIVE = "interactive"
    SAFETY = "safety_control"
    STREAM = "long_lived_stream"
    NETWORK_BACKGROUND = "network_background"
    ENGINE = "engine"
    SQLITE_WRITER = "sqlite_writer"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    STALE = "stale"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class OperationSnapshot:
    context: JobContext
    lane: OperationLane
    status: OperationStatus
    started_at: float | None
    finished_at: float | None
    progress_done: int | None
    progress_total: int | None
    cancellation_reason: str | None
    failure_code: str | None


class OperationHandle:
    """Thread-shaped compatibility handle for existing orderly shutdown loops."""

    def __init__(self, snapshot: OperationSnapshot, thread: threading.Thread,
                 stop_event: threading.Event | None) -> None:
        self.operation_id = snapshot.context.operation_id
        self._snapshot = snapshot
        self._thread = thread
        self._stop_event = stop_event
        self._lock = threading.Lock()

    @property
    def snapshot(self) -> OperationSnapshot:
        with self._lock:
            return self._snapshot

    def _replace(self, **changes) -> None:
        with self._lock:
            fields = {
                "context": self._snapshot.context,
                "lane": self._snapshot.lane,
                "status": self._snapshot.status,
                "started_at": self._snapshot.started_at,
                "finished_at": self._snapshot.finished_at,
                "progress_done": self._snapshot.progress_done,
                "progress_total": self._snapshot.progress_total,
                "cancellation_reason": self._snapshot.cancellation_reason,
                "failure_code": self._snapshot.failure_code,
            }
            fields.update(changes)
            self._snapshot = OperationSnapshot(**fields)

    def cancel(self, reason: str = "user_cancelled") -> bool:
        if self._thread.is_alive() and self._stop_event is not None:
            self._stop_event.set()
            self._replace(status=OperationStatus.CANCELLED,
                          cancellation_reason=_safe_cancellation_code(reason))
            return True
        return False

    def update_progress(self, done: int, total: int) -> None:
        self._replace(progress_done=max(0, int(done)), progress_total=max(0, int(total)))

    def transition(self, status: OperationStatus, *, reason: str | None = None,
                   failure_code: str | None = None) -> None:
        self._replace(status=status,
                      cancellation_reason=(_safe_cancellation_code(reason)
                                           if reason and status is OperationStatus.CANCELLED
                                           else str(reason)[:80] if reason else None),
                      failure_code=str(failure_code)[:80] if failure_code else None)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


class BackgroundWorkCoordinator:
    """Track operation identity/state while every operation keeps its own thread.

    Lanes classify resource and priority ownership. They deliberately do not share
    an executor/queue, so a long archive or event stream cannot occupy the worker
    needed by a move, safety preflight, Stockfish, or SQLite writer.
    """

    _PRIORITY = {
        OperationLane.INTERACTIVE: 100,
        OperationLane.SAFETY: 90,
        OperationLane.STREAM: 70,
        OperationLane.NETWORK_BACKGROUND: 20,
        OperationLane.ENGINE: 40,
        OperationLane.SQLITE_WRITER: 60,
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._operations: dict[str, OperationHandle] = {}
        self._closing = False

    @classmethod
    def lane_for_legacy_name(cls, name: str) -> OperationLane:
        lowered = name.casefold()
        if any(key in lowered for key in ("send-move", "lichess-action", "takeback",
                                          "cancel-invitation", "challenge")):
            return OperationLane.INTERACTIVE
        if any(key in lowered for key in ("analysis-safety", "active-games", "restore-auth",
                                          "personal-token-check", "oauth-revoke", "oauth")):
            return OperationLane.SAFETY
        if "events" in lowered or "game-" in lowered:
            return OperationLane.STREAM
        return OperationLane.NETWORK_BACKGROUND

    @staticmethod
    def kind_for_legacy_name(name: str) -> str:
        """Return a stable category; never copy dynamic game ids into diagnostics."""
        lowered = name.casefold()
        for marker, kind in (
            ("lichess-events", "lichess_events"),
            ("game-", "lichess_game_stream"),
            ("send-move", "lichess_move"),
            ("takeback", "lichess_takeback"),
            ("active-games", "active_games_check"),
            ("analysis-safety", "analysis_safety_check"),
            ("archive", "archive_sync"),
            ("puzzle", "puzzle_sync"),
            ("oauth", "oauth"),
            ("challenge", "challenge"),
        ):
            if marker in lowered:
                return kind
        return "lichess_background"

    def start(self, target, *, kind: str, lane: OperationLane,
              account_id: str | None = None, auth_generation: int | None = None,
              game=None, stop_event: threading.Event | None = None,
              thread_name: str | None = None) -> OperationHandle:
        with self._lock:
            if self._closing:
                raise RuntimeError("background work coordinator is shutting down")
            context = JobContext(uuid.uuid4().hex, kind, self._PRIORITY[lane],
                                 account_id=account_id, auth_generation=auth_generation,
                                 game=game)
            initial = OperationSnapshot(context, lane, OperationStatus.QUEUED,
                                        None, None, None, None, None, None)
            holder: dict[str, OperationHandle] = {}

            def run() -> None:
                handle = holder["handle"]
                handle._replace(status=OperationStatus.RUNNING, started_at=time.monotonic())
                diagnostic_context = set_context(operation_id=context.operation_id)
                priority_token = set_work_priority(context.priority)
                try:
                    log_event("operation_started", "background_work",
                              operation_id=context.operation_id, stage=kind, mode=lane.value)
                    target()
                except Exception as exc:
                    handle._replace(status=OperationStatus.FAILED,
                                    failure_code=type(exc).__name__,
                                    finished_at=time.monotonic())
                    log_event("operation_failed", "background_work",
                              operation_id=context.operation_id, stage=kind,
                              mode=lane.value, error_code=type(exc).__name__)
                else:
                    current = handle.snapshot
                    if stop_event is not None and stop_event.is_set():
                        handle._replace(status=OperationStatus.CANCELLED,
                                        cancellation_reason=current.cancellation_reason or
                                        "worker_stop_event", finished_at=time.monotonic())
                    elif current.status is OperationStatus.RUNNING:
                        handle._replace(status=OperationStatus.COMPLETED,
                                        finished_at=time.monotonic())
                    elif current.finished_at is None:
                        handle._replace(finished_at=time.monotonic())
                    log_event("operation_finished", "background_work",
                              operation_id=context.operation_id, stage=kind,
                              mode=lane.value, status=handle.snapshot.status.value,
                              reason_code=(handle.snapshot.cancellation_reason
                                           or handle.snapshot.failure_code))
                finally:
                    reset_work_priority(priority_token)
                    reset_context(diagnostic_context)
                    with self._lock:
                        self._operations.pop(context.operation_id, None)

            thread = threading.Thread(target=run, name=thread_name or f"cc-{kind}", daemon=True)
            handle = OperationHandle(initial, thread, stop_event)
            holder["handle"] = handle
            self._operations[context.operation_id] = handle
            thread.start()
            return handle

    def snapshots(self) -> tuple[OperationSnapshot, ...]:
        with self._lock:
            return tuple(handle.snapshot for handle in self._operations.values())

    def close(self, timeout: float = 1.0) -> bool:
        with self._lock:
            self._closing = True
            handles = tuple(self._operations.values())
        deadline = time.monotonic() + max(0.0, timeout)
        for handle in handles:
            handle.join(max(0.0, deadline - time.monotonic()))
        return not any(handle.is_alive() for handle in handles)
