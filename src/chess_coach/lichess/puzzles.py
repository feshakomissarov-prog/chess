"""Explicit sync of the authenticated user's Lichess puzzle activity."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .api import LichessClient
from .repository import GameRepository


class PuzzleActivitySync:
    def __init__(self, client: LichessClient, repository: GameRepository) -> None:
        self.client = client
        self.repository = repository

    def run(self, username: str, token: str, stop_event: threading.Event, *,
            page_size: int = 1000, progress: Callable[[int], None] | None = None) -> int:
        account_id = self.repository.ensure_archive_account(username)
        state = self.repository.puzzle_sync_state(account_id)
        count = 0
        if not state.get("backfill_complete"):
            before = state.get("backfill_before") or int(time.time() * 1000)
            while not stop_event.is_set():
                page_count = 0
                oldest = before
                for record in self.client.stream_puzzle_activity(
                        token, stop_event, before=before, max_items=page_size):
                    if stop_event.is_set():
                        break
                    date = int(record.get("date", 0))
                    self.repository.import_puzzle_activity(username, record, backfill=True)
                    oldest = min(oldest, date - 1) if date else oldest
                    page_count += 1
                    count += 1
                    if progress:
                        progress(count)
                if page_count < page_size or oldest >= before:
                    break
                before = oldest
            if not stop_event.is_set():
                self.repository.finish_puzzle_backfill(account_id)
        if stop_event.is_set():
            return count
        cursor = self.repository.puzzle_sync_state(account_id).get("incremental_since")
        since = max(0, int(cursor or 0) - 86_400_000) if cursor else 0
        while not stop_event.is_set():
            page_count = 0
            max_seen = since
            for record in self.client.stream_puzzle_activity(
                    token, stop_event, since=since, max_items=page_size):
                if stop_event.is_set():
                    break
                date = int(record.get("date", 0))
                self.repository.import_puzzle_activity(username, record)
                max_seen = max(max_seen, date)
                count += 1
                page_count += 1
                if progress:
                    progress(count)
            if page_count < page_size or max_seen <= since:
                break
            since = max_seen
        return count
