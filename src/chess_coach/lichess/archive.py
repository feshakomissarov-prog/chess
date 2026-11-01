"""Explicit, resumable archive synchronization; never invoked at application startup."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import threading
import time
from typing import Callable

from .api import LichessClient, RateLimitError
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
            imported = self._run(username, token, stop_event, full=full,
                                 page_size=page_size, progress=progress)
            if stop_event.is_set() and hasattr(self.repository, "record_archive_interrupted"):
                self.repository.record_archive_interrupted(account_id)
            return imported
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
            started_ms = int(time.time() * 1000)
            if hasattr(self.repository, "begin_archive_backfill"):
                anchor_ms = self.repository.begin_archive_backfill(account_id, started_ms)
            else:
                anchor_ms = int(state.get("backfill_started_at") or
                                state.get("backfill_before") or started_ms)
            until_ms = state.get("backfill_before") or anchor_ms
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
                result = self.repository.import_archive_record(
                    username, record, checkpoint_ms=None, backfill=False, _return_inserted=True)
                inserted = result[1] if isinstance(result, tuple) else True
                max_seen = max(max_seen, stamp or since)
                imported += int(inserted)
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
                result = self.repository.import_archive_record(
                    username, record, checkpoint_ms=None, backfill=False, _return_inserted=True)
                inserted = result[1] if isinstance(result, tuple) else True
                imported += int(inserted)
                if progress:
                    progress(imported, game_id)
            if count == 0 and boundary_count == 0:
                raise RuntimeError("Lichess archive incremental page made no progress; cursor unchanged.")
            since = max_seen + 1
        if not stop_event.is_set():
            finish_incremental = self.repository.finish_archive_incremental
            if len(inspect.signature(finish_incremental).parameters) >= 3:
                finish_incremental(account_id, target_ms, imported)
            else:  # Compatibility with small legacy test doubles.
                finish_incremental(account_id, target_ms)
        return imported

    def run_pilot(self, username: str, token: str, stop_event: threading.Event, *,
                  max_games: int = 5,
                  progress: Callable[[int, int, str], None] | None = None) -> dict[str, int | str]:
        """Import a bounded newest-games sample without moving full-sync cursors."""
        if not 5 <= int(max_games) <= 10:
            raise ValueError("Пробный импорт ограничен 5–10 партиями.")
        account_id = self.repository.ensure_archive_account(username)
        run_id = self.repository.begin_archive_pilot_run(account_id, max_games)
        received = new = 0
        try:
            for record in self.client.stream_user_games(
                    token, username, stop_event, max_games=max_games):
                if stop_event.is_set() or received >= max_games:
                    break
                received += 1
                self.repository.record_archive_pilot_received(run_id)
                result = self.repository.import_archive_record(
                    username, record, checkpoint_ms=None, backfill=False,
                    pilot_run_id=run_id, _return_inserted=True)
                inserted = result[1] if isinstance(result, tuple) else True
                new += int(inserted)
                if progress:
                    progress(received, new, str(record.get("id", "")))
            status = "interrupted" if stop_event.is_set() else "completed"
            self.repository.finish_archive_pilot_run(run_id, status)
            return {"received": received, "new": new, "status": status}
        except Exception as exc:
            self.repository.finish_archive_pilot_run(run_id, "error", str(exc))
            raise

    def refresh_game(self, username: str, token: str, game_id: str) -> bool:
        """Refresh one previously imported completed game by its Lichess ID."""
        account_id = self.repository.ensure_archive_account(username)
        game = self.repository.get_game(game_id)
        if not game or not self.repository.archive_game_linked(account_id, game_id):
            raise ValueError("Эта партия не связана с текущим аккаунтом Lichess.")
        if game.get("status") in {"created", "started", "started_soon", "playing"}:
            raise ValueError("Обновлять можно только завершённую партию.")
        pgn = self.client.export_game_pgn(token, game_id)
        _game_id, inserted = self.repository.import_archive_record(
            username, {"id": game_id, "pgn": pgn}, checkpoint_ms=None,
            backfill=False, _return_inserted=True)
        return bool(inserted)

    def refresh_missing_evaluations(self, username: str, token: str,
                                    stop_event: threading.Event, *, limit: int = 25,
                                    recheck_checked: bool = False,
                                    progress: Callable[[int, int, str], None] | None = None
                                    ) -> dict[str, int | bool]:
        """Explicitly check bounded unchecked lines; never moves archive cursors."""
        if not 1 <= int(limit) <= 100:
            raise ValueError("Targeted eval refresh is limited to 1–100 games per request.")
        account_id = self.repository.ensure_archive_account(username)
        candidates = self.repository.list_games_missing_lichess_evaluations(
            account_id, limit=int(limit), recheck_checked=recheck_checked)
        refreshed = 0
        checked = gained = unchanged = failed = 0
        for index, game in enumerate(candidates, start=1):
            if stop_event.is_set():
                break
            try:
                before = self.repository.lichess_eval_score_count(
                    game["game_id"], int(game["line_revision"]))
                pgn = self.client.export_game_pgn(token, game["game_id"])
                # This import has no archive checkpoint; successful game-specific state
                # is recorded only after the PGN and its derived metrics are persisted.
                self.repository.import_archive_record(
                    username, {"id": game["game_id"], "pgn": pgn},
                    checkpoint_ms=None, backfill=False)
                after = self.repository.lichess_eval_score_count(
                    game["game_id"], int(game["line_revision"]))
                gained_now = max(0, after - before)
                gained += gained_now
                refreshed += 1
                checked += 1
                unchanged += int(gained_now == 0)
                status = "changed" if gained_now and after >= self.repository.game_move_count(
                    game["game_id"], int(game["line_revision"])) else (
                    "partial" if gained_now else "checked_no_evals")
                self.repository.record_lichess_eval_refresh(
                    account_id, game["game_id"], line_revision=int(game["line_revision"]),
                    line_hash=game["line_hash"], status=status)
            except Exception as exc:
                failed += 1
                retry_after = max(1, int(getattr(exc, "retry_after", 60)))
                self.repository.record_lichess_eval_refresh(
                    account_id, game["game_id"], line_revision=int(game["line_revision"]),
                    line_hash=game["line_hash"], status="api_failure",
                    error=f"{type(exc).__name__}: {exc}", retry_after_seconds=retry_after)
                if isinstance(exc, RateLimitError):
                    break
            if progress:
                progress(checked + failed, len(candidates), game["game_id"])
        return {"selected": len(candidates), "checked": checked,
                "gained_scored_pairs": gained, "unchanged": unchanged, "failed": failed,
                "refreshed": refreshed, "cancelled": stop_event.is_set()}

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
                result = self.repository.import_archive_record(
                    username, record, checkpoint_ms=stamp,
                    backfill=True, _return_inserted=True)
                inserted = result[1] if isinstance(result, tuple) else True
                if stamp:
                    stamps.append(stamp)
                imported += int(inserted)
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
                result = self.repository.import_archive_record(
                    username, record, checkpoint_ms=stamp, backfill=True,
                    _return_inserted=True)
                inserted = result[1] if isinstance(result, tuple) else True
                imported += int(inserted)
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
