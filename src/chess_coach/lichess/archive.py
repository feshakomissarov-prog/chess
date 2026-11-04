"""Explicit, resumable archive synchronization; never invoked at application startup."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import io
import threading
import time
from typing import Callable

from .api import LichessClient, RateLimitError
from .repository import GameRepository, StaleGameLineError
from .analysis_json import normalize_game_analytics

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
        # Keep a finite NDJSON response short enough that other finite work can
        # receive the next slot. The caller still resumes at durable page cursors.
        page_size = min(max(1, int(page_size)), LichessClient.MAX_FINITE_STREAM_PAGE)
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
                checkpoint = self.repository.finish_archive_incremental
                if len(inspect.signature(checkpoint).parameters) >= 3:
                    checkpoint(account_id, since, imported)
                else:  # Compatibility with repository test doubles and older callers.
                    checkpoint(account_id, since)
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
                before_pairs = self.repository.lichess_eval_pair_count(
                    game["game_id"], int(game["line_revision"]))
                pgn = self.client.export_game_pgn(token, game["game_id"])
                # This import has no archive checkpoint; successful game-specific state
                # is recorded only after the PGN and its derived metrics are persisted.
                self.repository.import_archive_record(
                    username, {"id": game["game_id"], "pgn": pgn},
                    checkpoint_ms=None, backfill=False)
                after = self.repository.lichess_eval_score_count(
                    game["game_id"], int(game["line_revision"]))
                after_pairs = self.repository.lichess_eval_pair_count(
                    game["game_id"], int(game["line_revision"]))
                gained_now = max(0, after - before)
                gained_pairs_now = max(0, after_pairs - before_pairs)
                gained += gained_pairs_now
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

    def refresh_ready_analytics(self, username: str, token: str,
                                stop_event: threading.Event, *, limit: int = 10,
                                daily_limit: int = 25,
                                force_retry: bool = False,
                                generation_is_current: Callable[[], bool] | None = None,
                                progress: Callable[[int, int, dict], None] | None = None
                                ) -> dict[str, int | bool | str | None]:
        """Fetch only existing documented per-game JSON analytics, in resumable pages."""
        if not 1 <= int(limit) <= 10:
            raise ValueError("Одна фоновая порция ограничена десятью партиями.")
        account_id = self.repository.ensure_archive_account(username)
        self.repository.enqueue_lichess_analysis_backfill(account_id)
        if self.repository.lichess_analysis_backfill_paused(account_id):
            return {"selected": 0, "checked": 0, "accuracy": 0, "evals": 0,
                    "no_data": 0, "errors": 0, "paused": True, "limited": False}
        retry_after_at = self.repository.lichess_analysis_retry_after(account_id)
        if retry_after_at and retry_after_at > datetime.now(timezone.utc).isoformat():
            return {"selected": 0, "checked": 0, "accuracy": 0, "evals": 0,
                    "no_data": 0, "errors": 0, "paused": False, "limited": True,
                    "retry_after_at": retry_after_at}
        candidates = self.repository.list_lichess_analysis_backfill(
            account_id, limit=limit, force_retry=force_retry)
        checked = accuracy_count = eval_count = no_data = errors = 0
        limited = False
        fatal_status: int | None = None

        def current() -> bool:
            return (not stop_event.is_set() and
                    (generation_is_current is None or generation_is_current()))

        for item in candidates:
            if not current():
                break
            if getattr(self.client, "rate_limit_remaining", 0) > 0:
                limited = True
                break
            game_id = str(item["game_id"])
            check_accuracy = bool(item.get("check_accuracy", 1))
            check_evals = bool(item.get("check_evals", 1))
            if not self.repository.reserve_lichess_analysis_request(
                    account_id, daily_limit=daily_limit):
                limited = True
                break
            self.repository.mark_lichess_analysis_backfill_running(
                account_id, game_id, line_revision=int(item["line_revision"]),
                line_hash=str(item["line_hash"]), check_accuracy=check_accuracy,
                check_evals=check_evals)
            try:
                payload = self.client.export_game_json(token, str(item["source_game_id"]))
                if not current():
                    # The response belongs to a previous auth/session generation.
                    break
                self._validate_backfill_export(username, item, payload)
                previous_revision = int(item["line_revision"])
                previous_hash = str(item["line_hash"])
                expected_moves = str(item["moves"])
                normalized = normalize_game_analytics(payload, len(expected_moves.split()))
                pgn_assessments = self._pgn_eval_assessments(str(payload.get("pgn") or ""))
                assessments_by_ply = {int(row["ply"]): row for row in pgn_assessments}
                # JSON slots are authoritative for their exact ply. If both forms
                # supply a score, repository depth preference preserves deeper PGN data.
                for row in normalized.assessments:
                    previous = assessments_by_ply.get(int(row["ply"]))
                    if previous and previous.get("depth") is not None:
                        continue
                    assessments_by_ply[int(row["ply"])] = dict(row)
                players = payload.get("players") if isinstance(payload.get("players"), dict) else {}
                white = players.get("white") if isinstance(players.get("white"), dict) else {}
                black = players.get("black") if isinstance(players.get("black"), dict) else {}
                opening = payload.get("opening") if isinstance(payload.get("opening"), dict) else {}
                opening_metadata = {
                    "eco": opening.get("eco"), "name": opening.get("name"),
                    "white_rating": white.get("rating"), "black_rating": black.get("rating"),
                    "lastMoveAt": payload.get("lastMoveAt"),
                }
                self.repository.merge_lichess_ready_analysis(
                    username, game_id, source_game_id=str(item["source_game_id"]),
                    account_id=account_id, expected_line_revision=previous_revision,
                    expected_line_hash=previous_hash, expected_moves=expected_moves,
                    accuracy=normalized.accuracy, player_analysis=normalized.player_analysis,
                    assessments=list(assessments_by_ply.values()),
                    move_annotations=list(normalized.move_annotations),
                    validation_issues=list(normalized.issues), opening=opening_metadata,
                    division=payload.get("division"),
                    generation_is_current=current)
                eval_positions = self.repository.lichess_eval_score_count(game_id, previous_revision)
                # Every completed decision has one post-move position. A position
                # zero baseline improves comparability but is optional in Lichess PGN.
                position_count = max(1, self.repository.game_move_count(game_id, previous_revision))
                accuracy_values = normalized.accuracy
                has_accuracy = any(value is not None for value in accuracy_values.values())
                complete_accuracy = all(value is not None for value in accuracy_values.values())
                has_evals = eval_positions > 0
                invalid_accuracy = any(issue in {"invalid_white_accuracy", "invalid_black_accuracy"}
                                       for issue in normalized.issues)
                fetched_accuracy_status = ("has_data" if complete_accuracy else
                                           "partial" if has_accuracy or invalid_accuracy
                                           else "no_server_data")
                malformed_array = any(issue.startswith(("analysis_", "invalid_analysis_",
                                                        "conflicting_score_", "invalid_eval_",
                                                        "invalid_mate_")) for issue in normalized.issues)
                fetched_evals_status = (
                    "has_data" if eval_positions >= position_count and not malformed_array else
                    "partial" if has_evals or (normalized.array_present and
                                               not normalized.array_complete)
                    else "no_server_data")
                accuracy_status = fetched_accuracy_status if check_accuracy else str(
                    item["accuracy_status"])
                evals_status = fetched_evals_status if check_evals else str(item["evals_status"])
                considered_statuses = set()
                if check_accuracy:
                    considered_statuses.add(fetched_accuracy_status)
                if check_evals:
                    considered_statuses.add(fetched_evals_status)
                retry_seconds = (7 * 24 * 60 * 60 if considered_statuses.intersection(
                    {"no_server_data", "partial"}) else 0)
                self.repository.record_lichess_analysis_backfill(
                    account_id, game_id, accuracy_status=accuracy_status,
                    evals_status=evals_status, retry_after_seconds=retry_seconds,
                    check_accuracy=check_accuracy, check_evals=check_evals)
                checked += 1
                accuracy_count += int(has_accuracy)
                eval_count += int(has_evals)
                no_data += int(not has_accuracy and not has_evals)
            except Exception as exc:
                errors += 1
                retry_after = max(60, int(getattr(exc, "retry_after", 60)))
                status = ("stale_line" if isinstance(exc, StaleGameLineError) else
                          "rate_limited" if getattr(exc, "status", None) == 429 else "error")
                http_status = getattr(exc, "status", None)
                backoff = (max(retry_after, 86_400) if http_status in {401, 403}
                           else max(retry_after, 3_600) if status == "error" else retry_after)
                self.repository.record_lichess_analysis_backfill(
                    account_id, game_id, accuracy_status=status, evals_status=status,
                    retry_after_seconds=(max(60, retry_after) if status == "stale_line" else backoff),
                    error=f"{type(exc).__name__}" + (f" HTTP {http_status}" if http_status else ""),
                    line_revision=int(item["line_revision"]), line_hash=str(item["line_hash"]),
                    check_accuracy=check_accuracy, check_evals=check_evals)
                if http_status in {401, 403, 429}:
                    fatal_status = int(http_status)
                    if http_status == 429:
                        self.repository.set_lichess_analysis_retry_after(account_id, backoff)
                    break
            if progress:
                progress(checked + errors, len(candidates), item)
        return {"selected": len(candidates), "checked": checked,
                "accuracy": accuracy_count, "evals": eval_count,
                "no_data": no_data, "errors": errors,
                "paused": stop_event.is_set(), "limited": limited,
                "fatal_status": fatal_status}

    @staticmethod
    def _official_accuracy_values(payload: dict) -> dict[str, float | None]:
        return normalize_game_analytics(payload, 0).accuracy

    @staticmethod
    def _pgn_eval_assessments(raw_pgn: str) -> list[dict]:
        import chess
        import chess.pgn

        parsed = chess.pgn.read_game(io.StringIO(raw_pgn))
        if parsed is None or parsed.errors:
            return []
        values = []
        initial = parsed.eval()
        if initial is not None:
            score = initial.pov(chess.WHITE)
            values.append({"ply": 0, "white_cp": score.score(mate_score=None),
                           "mate_white": score.mate(), "depth": parsed.eval_depth()})
        for ply, node in enumerate(parsed.mainline(), start=1):
            evaluation = node.eval()
            if evaluation is not None:
                score = evaluation.pov(chess.WHITE)
                values.append({"ply": ply, "white_cp": score.score(mate_score=None),
                               "mate_white": score.mate(), "depth": node.eval_depth()})
        return values

    @staticmethod
    def _validate_backfill_export(username: str, item: dict, payload: dict) -> None:
        import chess
        import chess.pgn

        game_id = str(item["game_id"])
        if payload.get("id") != str(item.get("source_game_id") or game_id):
            raise ValueError("Lichess вернул другую партию; экспорт отклонён.")
        status = str(payload.get("status") or "").casefold()
        if status not in {"mate", "resign", "stalemate", "draw", "outoftime", "timeout",
                          "cheat", "nostart", "variantend", "unknownfinish", "finished"}:
            raise ValueError("Lichess export не подтверждает известный завершённый статус партии.")
        raw_pgn = payload.get("pgn")
        if not isinstance(raw_pgn, str) or not raw_pgn.strip():
            raise ValueError("В JSON-экспорте нет PGN для проверки линии.")
        parsed = chess.pgn.read_game(io.StringIO(raw_pgn))
        if parsed is None or parsed.errors:
            raise ValueError("Некорректный PGN в JSON-экспорте Lichess.")
        if parsed.headers.get("Result", "*") == "*":
            raise ValueError("PGN Lichess не содержит подтверждённого результата партии.")
        exported_moves = " ".join(move.uci() for move in parsed.mainline_moves())
        if exported_moves != str(item["moves"]):
            raise ValueError("Линия JSON-экспорта отличается от сохранённой партии.")
        link_color = item.get("account_color")
        expected_header = parsed.headers.get("White" if link_color == "white" else "Black", "")
        if not expected_header or expected_header.casefold() != username.casefold():
            raise ValueError("В JSON-экспорте не подтверждается владелец связанной партии.")
        players = payload.get("players")
        if players is not None:
            if not isinstance(players, dict):
                raise ValueError("Некорректные данные игроков в JSON-экспорте.")
            own = players.get(str(link_color))
            if own is not None:
                if not isinstance(own, dict):
                    raise ValueError("Некорректная запись игрока владельца.")
                user_data = own.get("user") if isinstance(own.get("user"), dict) else own
                remote_name = user_data.get("name") or user_data.get("username")
                if remote_name and str(remote_name).casefold() != username.casefold():
                    raise ValueError("Идентификатор аккаунта не совпадает; ответ отклонён.")

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
