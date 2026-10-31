"""Explicit, resumable archive synchronization; never invoked at application startup."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Callable

from .api import LichessClient
from .repository import GameRepository

_LICHESS_EPOCH_MS = 1_356_998_400_070


class ArchiveSync:
    def __init__(self, client: LichessClient, repository: GameRepository) -> None:
        self.client = client
        self.repository = repository

    def run(self, username: str, token: str, stop_event: threading.Event, *,
            full: bool = False, page_size: int = 1000,
            progress: Callable[[int, str], None] | None = None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        try:
            return self._run(username, token, stop_event, full=full,
                             page_size=page_size, progress=progress)
        except Exception as exc:
            self.repository.record_archive_error(account_id, str(exc))
            raise

    def _run(self, username: str, token: str, stop_event: threading.Event, *,
             full: bool, page_size: int,
             progress: Callable[[int, str], None] | None) -> int:
        """Import/resume a backfill, then incrementally update only newer/changed games."""
        account_id = self.repository.ensure_archive_account(username)
        state = self.repository.archive_sync_state(account_id)
        if full or not state.get("backfill_complete"):
            until_ms = state.get("backfill_before") or int(time.time() * 1000)
            imported = self._backfill(username, token, account_id, until_ms, stop_event,
                                      page_size, progress)
            if not stop_event.is_set():
                self.repository.finish_archive_backfill(account_id)
                state = self.repository.archive_sync_state(account_id)
        else:
            imported = 0
        if stop_event.is_set() or not self.repository.archive_sync_state(account_id).get("backfill_complete"):
            return imported
        cursor = self.repository.archive_sync_state(account_id).get("incremental_since")
        # Date ascending plus an inclusive timestamp overlap avoids timestamp-boundary loss.
        since = max(_LICHESS_EPOCH_MS,
                    int(cursor or _LICHESS_EPOCH_MS) - 24 * 60 * 60 * 1000)
        target_ms = int(time.time() * 1000)
        seen: set[str] = set()
        while not stop_event.is_set():
            count = 0
            page_items = 0
            max_seen = since
            page_stamps: list[int] = []
            for record in self.client.stream_user_games(
                    token, username, stop_event, since=since, max_games=page_size):
                if stop_event.is_set():
                    break
                page_items += 1
                game_id = str(record.get("id", ""))
                if not game_id or game_id in seen:
                    continue
                seen.add(game_id)
                count += 1
                stamp = _timestamp(record)
                if stamp is not None:
                    page_stamps.append(stamp)
                self.repository.import_archive_record(
                    username, record, checkpoint_ms=None, backfill=False)
                max_seen = max(max_seen, stamp or since)
                imported += 1
                if progress:
                    progress(imported, str(record.get("id", "")))
            if page_items < page_size:
                break
            if page_stamps:
                max_seen = max(page_stamps)
            boundary = list(self.client.stream_user_games(
                token, username, stop_event, since=max_seen, until_ms=max_seen, max_games=10_000))
            if len(boundary) >= 10_000:
                raise RuntimeError("More than 10000 archive games share a timestamp; pagination cannot prove completeness.")
            boundary_count = 0
            for record in boundary:
                game_id = str(record.get("id", ""))
                if not game_id or game_id in seen:
                    continue
                seen.add(game_id)
                boundary_count += 1
                self.repository.import_archive_record(username, record, checkpoint_ms=None, backfill=False)
                imported += 1
                if progress:
                    progress(imported, game_id)
            if count == 0 and boundary_count == 0:
                raise RuntimeError("Lichess archive incremental page made no progress; cursor unchanged.")
            since = max_seen + 1
        if not stop_event.is_set():
            self.repository.finish_archive_incremental(account_id,
                max(target_ms, max_seen if 'max_seen' in locals() else since))
        return imported

    def _backfill(self, username: str, token: str, account_id: str, until_ms: int,
                  stop_event: threading.Event, page_size: int,
                  progress: Callable[[int, str], None] | None) -> int:
        imported = 0
        cursor = until_ms
        seen: set[str] = set()
        while not stop_event.is_set():
            count = 0
            page_items = 0
            stamps: list[int] = []
            for record in self.client.stream_user_games(
                    token, username, stop_event, until_ms=cursor, max_games=page_size):
                if stop_event.is_set():
                    break
                page_items += 1
                game_id = str(record.get("id", ""))
                if not game_id or game_id in seen:
                    continue
                seen.add(game_id)
                count += 1
                stamp = _timestamp(record)
                self.repository.import_archive_record(
                    username, record, checkpoint_ms=stamp,
                    backfill=True)
                if stamp:
                    stamps.append(stamp)
                imported += 1
                if progress:
                    progress(imported, str(record.get("id", "")))
            if page_items < page_size:
                break
            if count == 0:
                raise RuntimeError("Lichess archive backfill page contained no unseen games; checkpoint unchanged.")
            oldest = min(stamps) if stamps else cursor
            if oldest >= cursor:
                raise RuntimeError("Lichess archive backfill pagination made no progress; checkpoint unchanged.")
            # Drain the entire boundary millisecond before moving to older timestamps;
            # this protects pages that split many games created in the same millisecond.
            boundary = list(self.client.stream_user_games(
                token, username, stop_event, since=oldest, until_ms=oldest, max_games=10_000))
            if len(boundary) >= 10_000:
                raise RuntimeError("More than 10000 archive games share a timestamp; pagination cannot prove completeness.")
            for record in boundary:
                game_id = str(record.get("id", ""))
                if not game_id or game_id in seen:
                    continue
                seen.add(game_id)
                stamp = _timestamp(record)
                self.repository.import_archive_record(username, record,
                    checkpoint_ms=stamp, backfill=True)
                imported += 1
                if progress:
                    progress(imported, game_id)
            if oldest <= _LICHESS_EPOCH_MS:
                break
            cursor = oldest - 1
        return imported


def _timestamp(record: dict) -> int | None:
    # The export endpoint filters by game creation time; lastMoveAt can be much later.
    value = record.get("createdAt") or record.get("lastMoveAt")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
