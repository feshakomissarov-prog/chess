"""Bounded, route-independent tracking for terminal game persistence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FinalizationPhase(StrEnum):
    SERVER_SYNC = "server_sync"
    LOCAL_WRITE = "local_write"
    COMPLETE = "complete"


@dataclass(slots=True)
class FinalizationRecord:
    game_id: str
    phase: FinalizationPhase
    storage_checks: int = 0


class GameFinalizationCoordinator:
    """Tracks terminal games independently from whichever page is visible.

    Server state remains authoritative. This coordinator never fabricates a
    final line or retries an ambiguous network command; it only observes the
    persisted sync/write flags and schedules bounded local write checks.
    """

    MAX_STORAGE_CHECKS = 8
    INITIAL_DELAY_MS = 100
    MAX_DELAY_MS = 2_000

    def __init__(self) -> None:
        self._records: dict[str, FinalizationRecord] = {}

    def track(self, game_id: str, *, server_sync_pending: bool,
              storage_write_pending: bool) -> FinalizationRecord:
        phase = (FinalizationPhase.SERVER_SYNC if server_sync_pending else
                 FinalizationPhase.LOCAL_WRITE if storage_write_pending else
                 FinalizationPhase.COMPLETE)
        record = self._records.get(str(game_id))
        if record is None:
            record = FinalizationRecord(str(game_id), phase)
            self._records[str(game_id)] = record
        else:
            record.phase = phase
        if phase == FinalizationPhase.COMPLETE:
            self._records.pop(str(game_id), None)
        return record

    def get(self, game_id: str) -> FinalizationRecord | None:
        return self._records.get(str(game_id))

    def storage_check_delay(self, game_id: str) -> int | None:
        record = self._records.get(str(game_id))
        if (record is None or record.phase != FinalizationPhase.LOCAL_WRITE
                or record.storage_checks >= self.MAX_STORAGE_CHECKS):
            return None
        delay = min(self.INITIAL_DELAY_MS * (2 ** record.storage_checks), self.MAX_DELAY_MS)
        record.storage_checks += 1
        return delay

    def pending_game_ids(self) -> tuple[str, ...]:
        return tuple(self._records)
