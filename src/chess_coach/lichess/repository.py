"""Small local SQLite store for confirmed Lichess games only."""

from __future__ import annotations

from typing import Callable
import hashlib
import json
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import uuid

import chess
import chess.pgn
from ..infrastructure.sqlite.transactions import SQLiteUnitOfWork
from ..infrastructure.sqlite.constants import (
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SCHEMA_VERSION,
)
from ..infrastructure.sqlite.puzzles import PuzzleActivityStoreMixin, puzzle_attempt_uid
from ..infrastructure.sqlite.jobs import AnalysisJobsStoreMixin
from ..infrastructure.sqlite.analysis import AnalysisStoreMixin
from ..infrastructure.sqlite.games import GamesStoreMixin
from ..infrastructure.sqlite.line import (
    _insert_position, _line_hash, _pgn_date, _time_precision,
)
from ..infrastructure.sqlite.archive_store import ArchiveStoreMixin
from ..infrastructure.sqlite.migrations import SQLiteMigrationMixin






class StaleGameLineError(RuntimeError):
    """A delayed server metadata response no longer matches the linked game line."""


class GameRepository(
    PuzzleActivityStoreMixin,
    AnalysisJobsStoreMixin,
    AnalysisStoreMixin,
    GamesStoreMixin,
    ArchiveStoreMixin,
    SQLiteMigrationMixin,
):
    def __init__(self, path: str | Path, *, async_writes: bool = False) -> None:
        self.path = str(path)
        self.async_writes = async_writes
        self._unit_of_work = SQLiteUnitOfWork(self.path)
        self._writer = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="chess-coach-sqlite")
                        if async_writes else None)
        self._write_lock = threading.Lock()
        self._accepting_writes = True
        self._writer_shutdown = False
        self._pending_writes: dict[str, Future] = {}
        self._write_errors: dict[str, str] = {}
        self._archive_account_cache: dict[str, str] = {}
        self._statistics_revision = 0
        self._initialize()

    @property
    def statistics_revision(self) -> int:
        return self._statistics_revision

    def invalidate_statistics_cache(self) -> None:
        self._statistics_revision += 1

    def merge_lichess_ready_analysis(self, username: str, game_id: str, *,
                                     source_game_id: str, account_id: str,
                                     expected_line_revision: int, expected_line_hash: str,
                                     expected_moves: str, accuracy: dict,
                                     player_analysis: dict, assessments: list[dict],
                                     move_annotations: list[dict], validation_issues: list[str],
                                     opening: dict | None = None,
                                     division: dict | None = None,
                                     lease_owner: str | None = None,
                                     generation_is_current: Callable[[], bool] | None = None) -> int:
        """CAS-merge server analytics without replacing PGN or the current line.

        The account link and immutable line identity are checked after acquiring a
        SQLite write transaction. Any stale/cancelled response rolls back every field.
        """
        from .analysis_json import ANALYTICS_SCHEMA_VERSION

        def current() -> bool:
            return generation_is_current is None or generation_is_current()

        def merge() -> int:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if not current():
                    raise StaleGameLineError("stale_session: аккаунт уже сменился")
                if lease_owner is not None:
                    lease = db.execute(
                        "SELECT 1 FROM lichess_analysis_backfill WHERE account_id=? AND game_id=? "
                        "AND line_revision=? AND lease_owner=? "
                        "AND (accuracy_status='running' OR evals_status='running')",
                        (account_id, game_id, int(expected_line_revision),
                         str(lease_owner))).fetchone()
                    if not lease:
                        raise StaleGameLineError(
                            "stale_lease: фоновая проверка больше не владеет записью очереди")
                linked = db.execute("""
                    SELECT g.line_revision,g.source_game_id,g.moves,gr.line_hash,
                           gr.is_current,a.username
                    FROM games g JOIN game_revisions gr ON gr.game_id=g.game_id
                      AND gr.line_revision=?
                    JOIN accounts a ON a.account_id=? AND a.provider='lichess'
                    JOIN account_games ag ON ag.account_id=a.account_id AND ag.game_id=g.game_id
                    WHERE g.game_id=?
                """, (expected_line_revision, account_id, game_id)).fetchone()
                if (not linked or not linked["is_current"]
                        or int(linked["line_revision"]) != int(expected_line_revision)
                        or str(linked["line_hash"]) != str(expected_line_hash)
                        or str(linked["moves"]) != str(expected_moves)
                        or str(linked["source_game_id"] or "") != str(source_game_id)
                        or str(linked["username"]).casefold() != username.casefold()):
                    raise StaleGameLineError("stale_line: партия, линия или account link изменились")

                white = player_analysis.get("white", {}) if isinstance(player_analysis, dict) else {}
                black = player_analysis.get("black", {}) if isinstance(player_analysis, dict) else {}
                players_opening = opening if isinstance(opening, dict) else {}
                db.execute("UPDATE games SET "
                           "lichess_accuracy_white=COALESCE(?,lichess_accuracy_white),"
                           "lichess_accuracy_black=COALESCE(?,lichess_accuracy_black),"
                           "white_rating=COALESCE(?,white_rating),black_rating=COALESCE(?,black_rating),"
                           "opening_eco=COALESCE(?,opening_eco),opening_name=COALESCE(?,opening_name),"
                           "last_move_at=COALESCE(?,last_move_at),updated_at=? WHERE game_id=?",
                           (accuracy.get("white"), accuracy.get("black"),
                            (players_opening.get("white_rating")),
                            (players_opening.get("black_rating")),
                            players_opening.get("eco"), players_opening.get("name"),
                            players_opening.get("lastMoveAt"), datetime.now(timezone.utc).isoformat(),
                            game_id))
                now = datetime.now(timezone.utc).isoformat()
                db.execute("""INSERT INTO lichess_ready_analysis_metadata(
                    account_id,game_id,line_revision,source,schema_version,
                    white_analysis_json,black_analysis_json,move_annotations_json,
                    validation_issues_json,updated_at) VALUES (?,?,?,'lichess-json',?,?,?,?,?,?)
                    ON CONFLICT(account_id,game_id,line_revision) DO UPDATE SET
                    source=excluded.source,schema_version=excluded.schema_version,
                    white_analysis_json=excluded.white_analysis_json,
                    black_analysis_json=excluded.black_analysis_json,
                    move_annotations_json=excluded.move_annotations_json,
                    validation_issues_json=excluded.validation_issues_json,
                    updated_at=excluded.updated_at""",
                           (account_id, game_id, expected_line_revision, ANALYTICS_SCHEMA_VERSION,
                            json.dumps(white, sort_keys=True, separators=(",", ":")),
                            json.dumps(black, sort_keys=True, separators=(",", ":")),
                            json.dumps(move_annotations, sort_keys=True, separators=(",", ":")),
                            json.dumps(validation_issues, sort_keys=True, separators=(",", ":")), now))

                params_json = json.dumps({"format": "pgn-eval-v1"}, sort_keys=True)
                run = db.execute("SELECT * FROM analysis_runs WHERE game_id=? AND line_revision=? "
                                 "AND line_hash=? AND source='lichess-pgn' AND parameters_json=? "
                                 "ORDER BY finished_at DESC,run_id DESC LIMIT 1",
                                 (game_id, expected_line_revision, expected_line_hash,
                                  params_json)).fetchone()
                if assessments or run:
                    if run:
                        run_id = str(run["run_id"])
                        changed = False
                    else:
                        run_id = uuid.uuid4().hex
                        db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (run_id, game_id, expected_line_revision, expected_line_hash,
                                    "lichess-pgn", "Lichess", None, params_json, now, now,
                                    "partial", None))
                        changed = True
                    for item in assessments:
                        ply = int(item["ply"])
                        old = db.execute("SELECT white_cp,mate_white,depth FROM position_assessments "
                                         "WHERE run_id=? AND ply=?", (run_id, ply)).fetchone()
                        incoming_score = item.get("white_cp") is not None or item.get("mate_white") is not None
                        if not incoming_score:
                            continue
                        if old:
                            # PGN scores with a known depth are preferred over JSON scores
                            # whose documented schema carries no depth field.
                            if old["depth"] is not None:
                                continue
                            values = (item.get("white_cp"), item.get("mate_white"), None)
                            if tuple(old[:3]) != values:
                                db.execute("UPDATE position_assessments SET white_cp=?,mate_white=?,depth=? "
                                           "WHERE run_id=? AND ply=?", (*values, run_id, ply))
                                changed = True
                        else:
                            db.execute("INSERT INTO position_assessments VALUES (?,?,?,?,?,?,?,?,?)",
                                       (run_id, game_id, expected_line_revision, ply,
                                        item.get("white_cp"), item.get("mate_white"), None, None, ""))
                            changed = True
                    expected = int(db.execute("SELECT COUNT(*) FROM game_moves WHERE game_id=? "
                                              "AND line_revision=?", (game_id, expected_line_revision)).fetchone()[0])
                    scored = int(db.execute("SELECT COUNT(*) FROM position_assessments WHERE run_id=? "
                                            "AND ply>0 AND (white_cp IS NOT NULL OR mate_white IS NOT NULL)",
                                            (run_id,)).fetchone()[0])
                    run_status = "complete" if expected and scored >= expected else "partial"
                    if run and run["status"] != run_status:
                        changed = True
                    if changed:
                        db.execute("UPDATE analysis_runs SET status=?,finished_at=?,error=NULL WHERE run_id=?",
                                   (run_status, now, run_id))
                        owner_color = db.execute(
                            "SELECT color FROM account_games WHERE account_id=? AND game_id=?",
                            (account_id, game_id)).fetchone()[0]
                        self._insert_derived_metrics_for_run(
                            db, run_id, user_color=str(owner_color), replace=True)
                division_data = division if isinstance(division, dict) else {}
                middle = _phase_ply(division_data.get("middle"))
                end = _phase_ply(division_data.get("end"))
                if ((middle is not None or end is not None)
                        and (middle is None or middle <= len(expected_moves.split()))
                        and (end is None or end <= len(expected_moves.split()))):
                    db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?) "
                               "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                               "middle_start_ply=excluded.middle_start_ply,end_start_ply=excluded.end_start_ply,"
                               "source='lichess',method_version='lichess-division-v1'",
                               (game_id, expected_line_revision, middle, end,
                                "lichess", "lichess-division-v1"))
                    self._apply_phase_labels(db, game_id, expected_line_revision, middle, end)
                if not current():
                    raise StaleGameLineError("stale_session: ответ Lichess устарел до commit")
            self.invalidate_statistics_cache()
            return len(assessments)

        if self._writer is not None:
            return int(self._submit_writer(merge, wait=True))
        return merge()

    def _connect(self) -> sqlite3.Connection:
        return self._unit_of_work.connect()

    def _connection(self):
        """Compatibility façade to the shared unit-of-work connection policy."""
        return self._unit_of_work.connection()



    def save_snapshot(self, game_id: str, initial_fen: str, moves: str, color: str,
                      opponent: str, rated: bool, clock: str, status: str,
                      winner: str | None = None, *, white_player: str | None = None,
                      black_player: str | None = None, started_at: str | None = None,
                      final_sync_pending: bool = False,
                      account_username: str | None = None) -> None:
        if self._writer is None:
            self._save_snapshot_sync(game_id, initial_fen, moves, color, opponent,
                                     rated, clock, status, winner,
                                     white_player=white_player, black_player=black_player,
                                     started_at=started_at, final_sync_pending=final_sync_pending,
                                     account_username=account_username)
            return
        with self._write_lock:
            if not self._accepting_writes:
                raise RuntimeError("SQLite writer is shutting down; new writes are rejected.")
            future = self._writer.submit(
                self._save_snapshot_sync, game_id, initial_fen, moves, color, opponent,
                rated, clock, status, winner, white_player=white_player,
                black_player=black_player, started_at=started_at,
                final_sync_pending=final_sync_pending, account_username=account_username,
            )
            self._pending_writes[game_id] = future
        future.add_done_callback(lambda completed, key=game_id: self._record_write_result(key, completed))

    def _submit_writer(self, fn, *args, wait: bool = False, **kwargs):
        if self._writer is None:
            return fn(*args, **kwargs)
        with self._write_lock:
            if not self._accepting_writes:
                raise RuntimeError("SQLite writer is shutting down; new writes are rejected.")
            future = self._writer.submit(fn, *args, **kwargs)
        return future.result() if wait else future



    def _record_write_result(self, game_id: str, future: Future) -> None:
        with self._write_lock:
            if self._pending_writes.get(game_id) is future:
                self._pending_writes.pop(game_id, None)
            try:
                future.result()
            except Exception as exc:
                self._write_errors[game_id] = str(exc)
            else:
                self._write_errors.pop(game_id, None)

    def has_pending_write(self, game_id: str) -> bool:
        with self._write_lock:
            future = self._pending_writes.get(game_id)
            return bool(future and not future.done())

    def has_write_error(self, game_id: str) -> bool:
        with self._write_lock:
            return game_id in self._write_errors

    def write_error(self, game_id: str) -> str | None:
        with self._write_lock:
            return self._write_errors.get(game_id)

    def wait_for_writes(self, game_id: str | None = None) -> None:
        with self._write_lock:
            futures = ([self._pending_writes[game_id]] if game_id in self._pending_writes
                       else [] if game_id else list(self._pending_writes.values()))
        for future in futures:
            try:
                future.result()
            except Exception:
                pass

    def shutdown(self) -> None:
        with self._write_lock:
            if self._writer is None or self._writer_shutdown:
                return
            self._accepting_writes = False
            self._writer_shutdown = True
            writer = self._writer
        writer.shutdown(wait=True)

    def has_pending_writes(self) -> bool:
        with self._write_lock:
            return any(not future.done() for future in self._pending_writes.values())

    def _wait_before_read(self, game_id: str | None = None) -> None:
        self.wait_for_writes(game_id)




































































































def _phase_ply(value: object) -> int | None:
    if isinstance(value, dict):
        value = value.get("begin")
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
