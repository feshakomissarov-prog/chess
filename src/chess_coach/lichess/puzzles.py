"""Explicit sync of the authenticated user's Lichess puzzle activity."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .api import LichessClient
from .repository import GameRepository, puzzle_attempt_uid

_LICHESS_EPOCH_MS = 1_356_998_400_070


class PuzzleActivitySync:
    def __init__(self, client: LichessClient, repository: GameRepository) -> None:
        self.client = client
        self.repository = repository

    def run(self, username: str, token: str, stop_event: threading.Event, *,
            page_size: int = 1000, progress: Callable[[int], None] | None = None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        try:
            count = self._run(username, token, stop_event, page_size=page_size, progress=progress)
            if stop_event.is_set():
                self.repository.record_puzzle_error(
                    account_id, "Синхронизация остановлена; сохранённый курсор можно возобновить.")
            return count
        except Exception as exc:
            self.repository.record_puzzle_error(account_id, str(exc))
            raise

    def run_pilot(self, username: str, token: str, stop_event: threading.Event, *,
                  limit: int, progress: Callable[[int, int], None] | None = None) -> dict:
        """Import one bounded latest-attempt sample without touching full-sync state."""
        if limit not in (5, 10):
            raise ValueError("Пробный импорт задач поддерживает только 5 или 10 попыток.")
        account_id = self.repository.ensure_archive_account(username)
        run_id = self.repository.start_puzzle_pilot(account_id, limit)
        received = new_count = 0
        try:
            records = list(self.client.stream_puzzle_activity(
                token, stop_event, before=int(time.time() * 1000) + 1,
                max_items=limit))
            # The endpoint is descending; sorting the bounded response also makes the
            # stored sample deterministic with test transports and timestamp ties.
            records.sort(key=lambda record: int(record.get("date", 0)), reverse=True)
            records = records[:limit]
            received = len(records)
            if records:
                results = self.repository.import_puzzle_activities(
                    username, records, pilot_run_id=run_id)
                new_count = sum(int(inserted) for _uid, inserted in results)
            if progress:
                progress(received, new_count)
            status = "cancelled" if stop_event.is_set() else "complete"
            return self.repository.finish_puzzle_pilot(run_id, status)
        except Exception as exc:
            result = self.repository.finish_puzzle_pilot(run_id, "error", str(exc))
            result["error"] = str(exc)
            return result

    def _run(self, username: str, token: str, stop_event: threading.Event, *,
             page_size: int, progress: Callable[[int], None] | None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        state = self.repository.puzzle_sync_state(account_id)
        count = 0

        def import_page(records: list[dict], *, backfill: bool = False) -> None:
            nonlocal count
            bulk_import = getattr(self.repository, "import_puzzle_activities", None)
            if bulk_import is not None:
                imported = bulk_import(username, records, backfill=backfill)
            else:  # Small repository fakes used by sync tests may implement only the row API.
                imported = [self.repository.import_puzzle_activity(
                    username, record, backfill=backfill, _return_inserted=True)
                    for record in records]
            for _attempt_uid, inserted in imported:
                if inserted:
                    count += 1
                    if progress:
                        progress(count)

        if not state.get("backfill_complete"):
            cursor = int(state.get("backfill_before") or time.time() * 1000)
            seen: set[str] = set()
            while not stop_event.is_set():
                records = list(self.client.stream_puzzle_activity(
                    token, stop_event, before=cursor, max_items=page_size))
                if stop_event.is_set():
                    break
                records = self._expand_boundary(token, stop_event, records, page_size, account_id)
                dates = [int(record.get("date", 0)) for record in records if record.get("date") is not None]
                fresh = [record for record in records if _key(record, account_id) not in seen]
                for record in fresh:
                    seen.add(_key(record, account_id))
                import_page(fresh, backfill=True)
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
            records = self._expand_boundary(token, stop_event, records, page_size, account_id)
            dates = [int(record.get("date", 0)) for record in records if record.get("date") is not None]
            older_than_window = any(date <= lower_bound for date in dates)
            relevant = [record for record in records if lower_bound < int(record.get("date", 0)) <= target]
            fresh = [record for record in relevant if _key(record, account_id) not in seen]
            for record in fresh:
                seen.add(_key(record, account_id))
            import_page(fresh)
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
            self.repository.finish_puzzle_incremental(account_id, target, count)
        return count

    def _expand_boundary(self, token: str, stop_event: threading.Event,
                         records: list[dict], page_size: int,
                         account_id: str = "") -> list[dict]:
        """Fetch a bounded wider window when a page may split same-ms attempts."""
        if len(records) < page_size or not records:
            return records
        oldest = min(int(record.get("date", 0)) for record in records)
        wide = list(self.client.stream_puzzle_activity(
            token, stop_event, since=oldest, before=oldest + 1, max_items=10_000))
        if len(wide) >= 10_000:
            raise RuntimeError("More than 10000 puzzle attempts share a timestamp; pagination cannot prove completeness.")
        combined = {_key(record, account_id): record for record in records}
        combined.update({_key(record, account_id): record for record in wide})
        return sorted(combined.values(), key=lambda item: int(item.get("date", 0)), reverse=True)


def _key(record: dict, account_id: str = "") -> str:
    puzzle = record.get("puzzle") or {}
    try:
        date = int(record["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Puzzle activity entry has no valid date") from exc
    puzzle_id = str(puzzle.get("id") or "").strip()
    if not puzzle_id:
        raise ValueError("Puzzle activity entry has no puzzle id")
    return puzzle_attempt_uid(account_id, puzzle_id, date, bool(record.get("win")))
