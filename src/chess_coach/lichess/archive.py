"""Explicit, resumable archive synchronization; never invoked at application startup."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Callable

from .api import LichessClient
from .repository import GameRepository


class ArchiveSync:
    def __init__(self, client: LichessClient, repository: GameRepository) -> None:
        self.client = client
        self.repository = repository

    def run(self, username: str, token: str, stop_event: threading.Event, *,
            full: bool = False, page_size: int = 1000,
            progress: Callable[[int, str], None] | None = None) -> int:
        """Import/resume a backfill, then incrementally update only newer/changed games."""
        account_id = self.repository.ensure_archive_account(username)
        state = self.repository.archive_sync_state(account_id)
        if full or not state.get("backfill_complete"):
            before = state.get("backfill_before") or int(time.time() * 1000)
            imported = self._backfill(username, token, account_id, before, stop_event,
                                      page_size, progress)
            if not stop_event.is_set():
                self.repository.finish_archive_backfill(account_id)
                state = self.repository.archive_sync_state(account_id)
        else:
            imported = 0
        if stop_event.is_set() or not self.repository.archive_sync_state(account_id).get("backfill_complete"):
            return imported
        cursor = self.repository.archive_sync_state(account_id).get("incremental_since")
        # Overlap catches late final PGN/analysis metadata; upserts are idempotent.
        since = max(0, int(cursor or 0) - 24 * 60 * 60 * 1000) if cursor else 0
        while not stop_event.is_set():
            count = 0
            max_seen = since
            for record in self.client.stream_user_games(
                    token, username, stop_event, since=since, max_games=page_size):
                if stop_event.is_set():
                    break
                count += 1
                stamp = _timestamp(record)
                self.repository.import_archive_record(
                    username, record, checkpoint_ms=stamp, backfill=False)
                max_seen = max(max_seen, stamp or since)
                imported += 1
                if progress:
                    progress(imported, str(record.get("id", "")))
            if count < page_size or max_seen <= since:
                break
            since = max_seen
        return imported

    def _backfill(self, username: str, token: str, account_id: str, before: int,
                  stop_event: threading.Event, page_size: int,
                  progress: Callable[[int, str], None] | None) -> int:
        imported = 0
        cursor = before
        while not stop_event.is_set():
            count = 0
            oldest = cursor
            for record in self.client.stream_user_games(
                    token, username, stop_event, before=cursor, max_games=page_size):
                if stop_event.is_set():
                    break
                count += 1
                stamp = _timestamp(record)
                self.repository.import_archive_record(
                    username, record, checkpoint_ms=max(0, stamp - 1) if stamp else None,
                    backfill=True)
                if stamp:
                    oldest = min(oldest, stamp - 1)
                imported += 1
                if progress:
                    progress(imported, str(record.get("id", "")))
            if count < page_size or oldest >= cursor:
                break
            cursor = oldest
        return imported


def _timestamp(record: dict) -> int | None:
    value = record.get("lastMoveAt") or record.get("createdAt")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
