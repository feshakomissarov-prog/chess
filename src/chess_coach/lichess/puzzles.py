"""Explicit sync of the authenticated user's Lichess puzzle activity."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .api import LichessClient
from .repository import GameRepository

_LICHESS_EPOCH_MS = 1_356_998_400_070


class PuzzleActivitySync:
    def __init__(self, client: LichessClient, repository: GameRepository) -> None:
        self.client = client
        self.repository = repository

    def run(self, username: str, token: str, stop_event: threading.Event, *,
            page_size: int = 1000, progress: Callable[[int], None] | None = None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        try:
            return self._run(username, token, stop_event, page_size=page_size, progress=progress)
        except Exception as exc:
            self.repository.record_puzzle_error(account_id, str(exc))
            raise

    def _run(self, username: str, token: str, stop_event: threading.Event, *,
             page_size: int, progress: Callable[[int], None] | None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        state = self.repository.puzzle_sync_state(account_id)
        count = 0
        if not state.get("backfill_complete"):
            cursor = int(state.get("backfill_before") or time.time() * 1000)
            seen: set[str] = set()
            while not stop_event.is_set():
                records = list(self.client.stream_puzzle_activity(
                    token, stop_event, before=cursor, max_items=page_size))
                if stop_event.is_set():
                    break
                records = self._expand_boundary(token, stop_event, records, page_size)
                dates = [int(record.get("date", 0)) for record in records if record.get("date") is not None]
                fresh = [record for record in records if _key(record) not in seen]
                for record in fresh:
                    seen.add(_key(record))
                    _uid, inserted = self.repository.import_puzzle_activity(
                        username, record, backfill=True, _return_inserted=True)
                    if inserted:
                        count += 1
                        if progress:
                            progress(count)
                if len(records) < page_size:
                    break
                oldest = min(dates) if dates else cursor
                next_cursor = oldest
                if next_cursor >= cursor or not fresh:
                    raise RuntimeError("Lichess puzzle pagination made no progress; high-watermark unchanged.")
                if oldest <= _LICHESS_EPOCH_MS:
                    break
                cursor = next_cursor
            if not stop_event.is_set():
                self.repository.finish_puzzle_backfill(account_id)
        if stop_event.is_set():
            return count

        state = self.repository.puzzle_sync_state(account_id)
        watermark = int(state.get("incremental_since") or 0)
        lower_bound = max(_LICHESS_EPOCH_MS - 1,
                          watermark - 86_400_000 if watermark else _LICHESS_EPOCH_MS - 1)
        target = int(state.get("incremental_target") or time.time() * 1000)
        cursor = int(state.get("incremental_before") or target)
        seen = set()
        while not stop_event.is_set():
            records = list(self.client.stream_puzzle_activity(
                token, stop_event, before=cursor, max_items=page_size))
            if stop_event.is_set():
                break
            records = self._expand_boundary(token, stop_event, records, page_size)
            dates = [int(record.get("date", 0)) for record in records if record.get("date") is not None]
            older_than_window = any(date <= lower_bound for date in dates)
            relevant = [record for record in records if lower_bound < int(record.get("date", 0)) <= target]
            fresh = [record for record in relevant if _key(record) not in seen]
            for record in fresh:
                seen.add(_key(record))
                _uid, inserted = self.repository.import_puzzle_activity(
                    username, record, _return_inserted=True)
                if inserted:
                    count += 1
                    if progress:
                        progress(count)
            if len(records) < page_size or older_than_window:
                break
            oldest = min(dates) if dates else cursor
            next_cursor = oldest
            if next_cursor >= cursor or not fresh:
                raise RuntimeError("Lichess puzzle incremental pagination made no progress; high-watermark unchanged.")
            if oldest <= _LICHESS_EPOCH_MS:
                break
            cursor = next_cursor
            # Attempts are durable before the resumable page cursor is advanced.
            self.repository.set_puzzle_incremental_progress(account_id, cursor, target)
        if not stop_event.is_set():
            self.repository.finish_puzzle_incremental(account_id, target)
        return count

    def _expand_boundary(self, token: str, stop_event: threading.Event,
                         records: list[dict], page_size: int) -> list[dict]:
        """Fetch a bounded wider window when a page may split same-ms attempts."""
        if len(records) < page_size or not records:
            return records
        oldest = min(int(record.get("date", 0)) for record in records)
        wide = list(self.client.stream_puzzle_activity(
            token, stop_event, since=oldest, before=oldest + 1, max_items=10_000))
        if len(wide) >= 10_000:
            raise RuntimeError("More than 10000 puzzle attempts share a timestamp; pagination cannot prove completeness.")
        combined = {_key(record): record for record in records}
        combined.update({_key(record): record for record in wide})
        return sorted(combined.values(), key=lambda item: int(item.get("date", 0)), reverse=True)


def _key(record: dict) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8")).hexdigest()
