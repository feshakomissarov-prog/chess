"""Small local SQLite store for confirmed Lichess games only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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


SCHEMA_VERSION = 16
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


def puzzle_attempt_uid(account_id: str, puzzle_id: str, attempt_date_ms: int,
                       win: bool | int) -> str:
    """Identity of a Lichess PuzzleActivity event, excluding mutable puzzle metadata."""
    payload = json.dumps([str(account_id), str(puzzle_id), int(attempt_date_ms),
                          int(bool(win))], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(("lichess-puzzle-attempt:v1:" + payload).encode("utf-8")).hexdigest()


class GameRepository:
    def __init__(self, path: str | Path, *, async_writes: bool = False) -> None:
        self.path = str(path)
        self.async_writes = async_writes
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

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback the transaction and always release its file handle."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        # Preserve an existing pre-migration database before applying schema changes.
        # sqlite's online backup API gives a consistent copy even if another reader exists.
        path = Path(self.path)
        existed = path.exists() and path.stat().st_size > 0
        if existed:
            with self._connection() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                has_games = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='games'"
                ).fetchone()
            if has_games and version < SCHEMA_VERSION:
                backup = path.with_name(path.name + f".pre-m3-v{SCHEMA_VERSION}.bak")
                if not backup.exists():
                    source = self._connect()
                    try:
                        destination = sqlite3.connect(str(backup))
                        try:
                            source.backup(destination)
                        finally:
                            destination.close()
                    finally:
                        source.close()
                backup_db = sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)
                try:
                    integrity = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"SQLite migration backup failed integrity_check: {integrity}")
                finally:
                    backup_db.close()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported database schema version: {version}")
            db.execute("""CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, moves TEXT NOT NULL,
                color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER NOT NULL,
                clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT,
                updated_at TEXT NOT NULL, pgn TEXT NOT NULL,
                white_player TEXT NOT NULL DEFAULT '?', black_player TEXT NOT NULL DEFAULT '?',
                started_at TEXT, final_sync_pending INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
            additions = {
                "white_player": "TEXT NOT NULL DEFAULT '?'",
                "black_player": "TEXT NOT NULL DEFAULT '?'",
                "started_at": "TEXT",
                "game_time_precision": "TEXT NOT NULL DEFAULT 'unknown'",
                "final_sync_pending": "INTEGER NOT NULL DEFAULT 0",
                "source": "TEXT NOT NULL DEFAULT 'lichess'",
                "source_game_id": "TEXT",
                "variant": "TEXT NOT NULL DEFAULT 'standard'",
                "line_revision": "INTEGER NOT NULL DEFAULT 1",
                "import_error": "TEXT",
                "raw_checksum": "TEXT",
                "raw_pgn": "TEXT",
                "last_move_at": "INTEGER",
                "lichess_accuracy_white": "REAL",
                "lichess_accuracy_black": "REAL",
                "white_rating": "INTEGER",
                "black_rating": "INTEGER",
                "opening_eco": "TEXT",
                "opening_name": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
            schema_sql = """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    username TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(provider, username)
                );
                CREATE TABLE IF NOT EXISTS account_games (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    remote_id TEXT NOT NULL, imported_at TEXT NOT NULL,
                    opponent TEXT, player_name TEXT,
                    PRIMARY KEY(account_id, game_id), UNIQUE(account_id, remote_id)
                );
                CREATE TABLE IF NOT EXISTS archive_sync_state (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    backfill_before INTEGER, backfill_complete INTEGER NOT NULL DEFAULT 0,
                    backfill_started_at INTEGER,
                    incremental_since INTEGER, last_success_at TEXT, imported_count INTEGER NOT NULL DEFAULT 0,
                    last_sync_new_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive_pilot_runs (
                    run_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    requested_limit INTEGER NOT NULL,
                    received_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS game_revisions (
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL, initial_fen TEXT NOT NULL,
                    moves TEXT NOT NULL, pgn TEXT NOT NULL, line_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(game_id, line_revision)
                );
                CREATE TABLE IF NOT EXISTS game_moves (
                    game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
                    uci TEXT NOT NULL, san TEXT NOT NULL, mover TEXT NOT NULL,
                    clock_ms INTEGER, elapsed_ms INTEGER,
                    PRIMARY KEY(game_id, line_revision, ply),
                    FOREIGN KEY(game_id, line_revision)
                      REFERENCES game_revisions(game_id, line_revision) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS game_positions (
                    game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
                    position_uid TEXT NOT NULL UNIQUE, fen TEXT NOT NULL, position_hash TEXT NOT NULL,
                    PRIMARY KEY(game_id, line_revision, ply),
                    FOREIGN KEY(game_id, line_revision)
                      REFERENCES game_revisions(game_id, line_revision) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS game_phase_boundaries (
                    game_id TEXT NOT NULL, line_revision INTEGER NOT NULL,
                    middle_start_ply INTEGER, end_start_ply INTEGER,
                    source TEXT NOT NULL, method_version TEXT NOT NULL,
                    PRIMARY KEY(game_id,line_revision),
                    FOREIGN KEY(game_id,line_revision)
                      REFERENCES game_revisions(game_id,line_revision) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS opening_matches (
                    game_id TEXT NOT NULL,
                    line_revision INTEGER NOT NULL,
                    dataset_revision TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    eco TEXT NOT NULL,
                    name TEXT NOT NULL,
                    family TEXT NOT NULL,
                    variation TEXT,
                    matched_ply INTEGER NOT NULL,
                    lichess_conflict INTEGER NOT NULL DEFAULT 0,
                    matched_at TEXT NOT NULL,
                    PRIMARY KEY(game_id,line_revision),
                    FOREIGN KEY(game_id,line_revision)
                      REFERENCES game_revisions(game_id,line_revision) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_opening_matches_eco
                    ON opening_matches(eco,game_id,line_revision);
                CREATE TABLE IF NOT EXISTS game_notes (
                    note_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                    line_revision INTEGER, ply INTEGER, position_uid TEXT,
                    body TEXT NOT NULL, author TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(game_id) REFERENCES games(game_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL, line_hash TEXT NOT NULL,
                    source TEXT NOT NULL, engine_name TEXT, engine_version TEXT,
                    parameters_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT,
                    FOREIGN KEY(game_id,line_revision) REFERENCES game_revisions(game_id,line_revision)
                );
                CREATE TABLE IF NOT EXISTS position_assessments (
                    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
                    white_cp INTEGER, mate_white INTEGER, depth INTEGER, nodes INTEGER,
                    pv_uci TEXT, PRIMARY KEY(run_id,ply),
                    FOREIGN KEY(game_id,line_revision,ply)
                      REFERENCES game_positions(game_id,line_revision,ply)
                );
                CREATE TABLE IF NOT EXISTS move_classifications (
                    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
                    mover TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'unknown',
                    eval_loss_cp INTEGER, label TEXT NOT NULL, algorithm_version TEXT NOT NULL,
                    PRIMARY KEY(run_id,ply),
                    FOREIGN KEY(game_id,line_revision,ply)
                      REFERENCES game_moves(game_id,line_revision,ply)
                );
                CREATE TABLE IF NOT EXISTS puzzles (
                    puzzle_id TEXT PRIMARY KEY, fen TEXT NOT NULL, solution_json TEXT NOT NULL,
                    themes_json TEXT NOT NULL, puzzle_rating INTEGER, plays INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS puzzle_attempts (
                    attempt_uid TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    puzzle_id TEXT NOT NULL REFERENCES puzzles(puzzle_id), attempt_date_ms INTEGER NOT NULL,
                    win INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'lichess', metadata_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS puzzle_sync_state (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    backfill_before INTEGER, backfill_complete INTEGER NOT NULL DEFAULT 0,
                    incremental_since INTEGER, incremental_before INTEGER, incremental_target INTEGER,
                    last_success_at TEXT, imported_count INTEGER NOT NULL DEFAULT 0,
                    last_sync_new_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS puzzle_dashboard_snapshots (
                    snapshot_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    days INTEGER NOT NULL, fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS puzzle_pilot_runs (
                    run_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    requested_limit INTEGER NOT NULL CHECK(requested_limit IN (5,10)),
                    received_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS puzzle_pilot_run_attempts (
                    run_id TEXT NOT NULL REFERENCES puzzle_pilot_runs(run_id) ON DELETE CASCADE,
                    attempt_uid TEXT NOT NULL REFERENCES puzzle_attempts(attempt_uid) ON DELETE CASCADE,
                    PRIMARY KEY(run_id,attempt_uid)
                );
                CREATE INDEX IF NOT EXISTS idx_puzzle_attempt_date ON puzzle_attempts(account_id,attempt_date_ms);
                CREATE INDEX IF NOT EXISTS idx_puzzle_attempt_puzzle ON puzzle_attempts(puzzle_id,attempt_date_ms);
                CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC, game_id);
                CREATE INDEX IF NOT EXISTS idx_games_opponent ON games(opponent);
                CREATE INDEX IF NOT EXISTS idx_game_notes_position ON game_notes(position_uid);
                CREATE INDEX IF NOT EXISTS idx_analysis_cache ON analysis_runs(game_id,line_revision,source,engine_version,status);
                CREATE INDEX IF NOT EXISTS idx_analysis_current_source ON analysis_runs(game_id,line_revision,status,source,finished_at DESC);
            """
            for statement in schema_sql.split(";"):
                if statement.strip():
                    db.execute(statement)
            account_game_columns = {row[1] for row in db.execute("PRAGMA table_info(account_games)")}
            for name, definition in (("color", "TEXT"), ("player_rating", "INTEGER"),
                                     ("opponent_rating", "INTEGER"), ("opponent", "TEXT"),
                                     ("player_name", "TEXT")):
                if name not in account_game_columns:
                    db.execute(f"ALTER TABLE account_games ADD COLUMN {name} {definition}")
            archive_state_columns = {row[1] for row in db.execute("PRAGMA table_info(archive_sync_state)")}
            for name, definition in (("backfill_started_at", "INTEGER"),
                                     ("last_sync_new_count", "INTEGER NOT NULL DEFAULT 0")):
                if name not in archive_state_columns:
                    db.execute(f"ALTER TABLE archive_sync_state ADD COLUMN {name} {definition}")
            puzzle_state_columns = {row[1] for row in db.execute("PRAGMA table_info(puzzle_sync_state)")}
            for name, definition in (("incremental_before", "INTEGER"),
                                     ("incremental_target", "INTEGER"),
                                     ("last_sync_new_count", "INTEGER NOT NULL DEFAULT 0")):
                if name not in puzzle_state_columns:
                    db.execute(f"ALTER TABLE puzzle_sync_state ADD COLUMN {name} {definition}")
            # Preserve account-specific identity when upgrading databases written
            # before the owner-relative opponent columns existed.
            db.execute("UPDATE account_games SET opponent=CASE color "
                       "WHEN 'white' THEN (SELECT black_player FROM games WHERE games.game_id=account_games.game_id) "
                       "WHEN 'black' THEN (SELECT white_player FROM games WHERE games.game_id=account_games.game_id) "
                       "END WHERE opponent IS NULL")
            db.execute("UPDATE account_games SET player_name=CASE color "
                       "WHEN 'white' THEN (SELECT white_player FROM games WHERE games.game_id=account_games.game_id) "
                       "WHEN 'black' THEN (SELECT black_player FROM games WHERE games.game_id=account_games.game_id) "
                       "END WHERE player_name IS NULL")
            db.execute("UPDATE archive_sync_state SET backfill_started_at="
                       "COALESCE(backfill_started_at,incremental_since,backfill_before) "
                       "WHERE backfill_complete=0")
            if version < 9:
                # Older versions hashed the entire API object. Puzzle metadata may
                # change, so collapse those legacy rows to the stable event identity.
                db.execute("CREATE TEMP TABLE old_puzzle_attempts AS SELECT * FROM puzzle_attempts")
                db.execute("CREATE TEMP TABLE puzzle_attempt_uid_map "
                           "(old_uid TEXT PRIMARY KEY,new_uid TEXT NOT NULL)")
                source = db.execute("SELECT attempt_uid,account_id,puzzle_id,attempt_date_ms,win "
                                    "FROM old_puzzle_attempts")
                batch: list[tuple[str, str]] = []
                while True:
                    rows = source.fetchmany(1000)
                    if not rows:
                        break
                    batch.extend((row["attempt_uid"], puzzle_attempt_uid(
                        row["account_id"], row["puzzle_id"], row["attempt_date_ms"], row["win"]))
                                 for row in rows)
                    if len(batch) >= 1000:
                        db.executemany("INSERT INTO puzzle_attempt_uid_map VALUES (?,?)", batch)
                        batch.clear()
                if batch:
                    db.executemany("INSERT INTO puzzle_attempt_uid_map VALUES (?,?)", batch)
                db.execute("CREATE TEMP TABLE migrated_puzzle_links AS "
                           "SELECT DISTINCT links.run_id,m.new_uid AS attempt_uid "
                           "FROM puzzle_pilot_run_attempts links "
                           "JOIN puzzle_attempt_uid_map m ON m.old_uid=links.attempt_uid")
                db.execute("CREATE TEMP TABLE migrated_puzzle_attempts AS "
                           "SELECT m.new_uid AS attempt_uid,a.account_id,a.puzzle_id,"
                           "a.attempt_date_ms,a.win,a.source,a.metadata_json,a.imported_at,"
                           "ROW_NUMBER() OVER (PARTITION BY m.new_uid "
                           "ORDER BY a.imported_at DESC,a.attempt_uid DESC) AS rank "
                           "FROM old_puzzle_attempts a "
                           "JOIN puzzle_attempt_uid_map m ON m.old_uid=a.attempt_uid")
                db.execute("DELETE FROM puzzle_pilot_run_attempts")
                db.execute("DELETE FROM puzzle_attempts")
                db.execute("INSERT INTO puzzle_attempts "
                           "SELECT attempt_uid,account_id,puzzle_id,attempt_date_ms,win,source,"
                           "metadata_json,imported_at FROM migrated_puzzle_attempts WHERE rank=1")
                db.execute("INSERT OR IGNORE INTO puzzle_pilot_run_attempts(run_id,attempt_uid) "
                           "SELECT run_id,attempt_uid FROM migrated_puzzle_links")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_puzzle_attempt_event "
                           "ON puzzle_attempts(account_id,puzzle_id,attempt_date_ms,win)")
            # Backfill old rows exactly once, retaining bad raw PGN/moves for repair.
            for row in db.execute("SELECT * FROM games WHERE game_id NOT IN "
                                  "(SELECT game_id FROM game_revisions)").fetchall():
                savepoint = "migrate_" + hashlib.sha1(str(row["game_id"]).encode()).hexdigest()[:12]
                db.execute(f"SAVEPOINT {savepoint}")
                try:
                    self._write_revision(db, dict(row), 1, current=True)
                    db.execute("UPDATE games SET line_revision=1, import_error=NULL WHERE game_id=?",
                               (row["game_id"],))
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                except (ValueError, chess.IllegalMoveError) as exc:
                    db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                    db.execute("UPDATE games SET import_error=? WHERE game_id=?",
                               (str(exc), row["game_id"]))
            self._migrate_m45_schema(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _migrate_m45_schema(db: sqlite3.Connection) -> None:
        """Add resumable analysis, derived coverage, and versioned move/event facts.

        The queue describes work; metrics/events reference existing run and move facts.
        Existing raw games, PGN, moves, positions, and v1 classifications remain untouched.
        """
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_jobs (
            job_id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
            line_revision INTEGER NOT NULL,
            line_hash TEXT NOT NULL,
            analysis_pass TEXT NOT NULL CHECK(analysis_pass IN ('fast','review')),
            engine_version TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('queued','running','paused','complete','failed','cancelled','stale')),
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_until TEXT,
            result_run_id TEXT REFERENCES analysis_runs(run_id) ON DELETE SET NULL,
            priority_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            UNIQUE(game_id,line_revision,line_hash,analysis_pass,engine_version,parameters_json),
            FOREIGN KEY(game_id,line_revision)
                REFERENCES game_revisions(game_id,line_revision) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_jobs_dispatch "
                   "ON analysis_jobs(status,priority_at DESC,created_at,job_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_jobs_line "
                   "ON analysis_jobs(game_id,line_revision,line_hash,status)")
        job_columns = {row[1] for row in db.execute("PRAGMA table_info(analysis_jobs)")}
        for name, definition in (("lease_owner", "TEXT"), ("lease_until", "TEXT"),
                                 ("result_run_id", "TEXT REFERENCES analysis_runs(run_id) ON DELETE SET NULL")):
            if name not in job_columns:
                db.execute(f"ALTER TABLE analysis_jobs ADD COLUMN {name} {definition}")
        # Versioned derived facts are additive: v1 classifications and all raw
        # games/positions remain intact and can be reclassified without UCI.
        db.execute("""CREATE TABLE IF NOT EXISTS move_metrics (
            run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
            game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
            metric_version TEXT NOT NULL, source TEXT NOT NULL,
            white_cp_before INTEGER, mate_white_before INTEGER,
            white_cp_after INTEGER, mate_white_after INTEGER,
            cp_loss INTEGER, mate_discontinuity INTEGER NOT NULL DEFAULT 0,
            wdl_before_json TEXT, wdl_after_json TEXT, wdl_model TEXT,
            accuracy REAL, accuracy_source TEXT, accuracy_formula_id TEXT,
            severity TEXT NOT NULL, severity_policy_version TEXT NOT NULL,
            legacy_label TEXT,
            PRIMARY KEY(run_id,ply,metric_version),
            FOREIGN KEY(game_id,line_revision,ply)
                REFERENCES game_moves(game_id,line_revision,ply) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_move_metrics_scope "
                   "ON move_metrics(game_id,line_revision,source,metric_version,ply)")
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_events (
            event_key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
            game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
            event_type TEXT NOT NULL, confidence_state TEXT NOT NULL
                CHECK(confidence_state IN ('verified','candidate','unknown','rejected')),
            source TEXT NOT NULL, detector_version TEXT NOT NULL, evidence_json TEXT NOT NULL,
            FOREIGN KEY(game_id,line_revision,ply)
                REFERENCES game_moves(game_id,line_revision,ply) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_events_scope "
                   "ON analysis_events(game_id,line_revision,event_type,confidence_state,ply)")
        db.execute("""CREATE TABLE IF NOT EXISTS event_tags (
            event_key TEXT NOT NULL REFERENCES analysis_events(event_key) ON DELETE CASCADE,
            tag TEXT NOT NULL, detector_version TEXT NOT NULL,
            confidence_state TEXT NOT NULL
                CHECK(confidence_state IN ('verified','candidate','unknown','rejected')),
            evidence_json TEXT NOT NULL,
            PRIMARY KEY(event_key,tag,detector_version)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_event_tags_tag "
                   "ON event_tags(tag,confidence_state,event_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS analysis_alternatives (
            run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
            game_id TEXT NOT NULL, line_revision INTEGER NOT NULL, ply INTEGER NOT NULL,
            multipv_rank INTEGER NOT NULL CHECK(multipv_rank >= 1),
            white_cp INTEGER, mate_white INTEGER, depth INTEGER, nodes INTEGER, pv_uci TEXT,
            PRIMARY KEY(run_id,ply,multipv_rank),
            FOREIGN KEY(game_id,line_revision,ply)
                REFERENCES game_positions(game_id,line_revision,ply) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_analysis_alternatives_game "
                   "ON analysis_alternatives(game_id,line_revision,ply,run_id)")
        db.execute("DROP VIEW IF EXISTS analysis_coverage")
        db.execute("""CREATE VIEW IF NOT EXISTS analysis_coverage AS
            SELECT ar.run_id,ar.game_id,ar.line_revision,ar.line_hash,ar.source,
                   ar.engine_name,ar.engine_version,ar.parameters_json,ar.status,
                   ar.started_at,ar.finished_at,
                   (SELECT COUNT(*) FROM game_positions gp
                    WHERE gp.game_id=ar.game_id AND gp.line_revision=ar.line_revision)
                       AS total_positions,
                   (SELECT COUNT(*) FROM position_assessments pa
                    WHERE pa.run_id=ar.run_id
                      AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL))
                       AS scored_positions,
                   (SELECT COUNT(*) FROM move_metrics mm
                    WHERE mm.run_id=ar.run_id AND mm.metric_version='cc-move-metrics-v2')
                       AS measured_moves,
                   (SELECT COUNT(*) FROM analysis_events ae
                    WHERE ae.run_id=ar.run_id AND ae.confidence_state='candidate')
                       AS candidate_events,
                   (SELECT COUNT(*) FROM game_moves gm
                    WHERE gm.game_id=ar.game_id AND gm.line_revision=ar.line_revision
                      AND EXISTS (SELECT 1 FROM position_assessments pre
                                  WHERE pre.run_id=ar.run_id AND pre.ply=gm.ply-1
                                    AND (pre.white_cp IS NOT NULL OR pre.mate_white IS NOT NULL))
                      AND EXISTS (SELECT 1 FROM position_assessments post
                                  WHERE post.run_id=ar.run_id AND post.ply=gm.ply
                                    AND (post.white_cp IS NOT NULL OR post.mate_white IS NOT NULL)))
                       AS comparable_move_pairs
            FROM analysis_runs ar
            JOIN game_revisions gr ON gr.game_id=ar.game_id
                AND gr.line_revision=ar.line_revision AND gr.is_current=1""")

    def save_snapshot(self, game_id: str, initial_fen: str, moves: str, color: str,
                      opponent: str, rated: bool, clock: str, status: str,
                      winner: str | None = None, *, white_player: str | None = None,
                      black_player: str | None = None, started_at: str | None = None,
                      final_sync_pending: bool = False) -> None:
        if self._writer is None:
            self._save_snapshot_sync(game_id, initial_fen, moves, color, opponent,
                                     rated, clock, status, winner,
                                     white_player=white_player, black_player=black_player,
                                     started_at=started_at, final_sync_pending=final_sync_pending)
            return
        with self._write_lock:
            if not self._accepting_writes:
                raise RuntimeError("SQLite writer is shutting down; new writes are rejected.")
            future = self._writer.submit(
                self._save_snapshot_sync, game_id, initial_fen, moves, color, opponent,
                rated, clock, status, winner, white_player=white_player,
                black_player=black_player, started_at=started_at,
                final_sync_pending=final_sync_pending,
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

    def _save_snapshot_sync(self, game_id: str, initial_fen: str, moves: str, color: str,
                            opponent: str, rated: bool, clock: str, status: str,
                            winner: str | None = None, *, white_player: str | None = None,
                            black_player: str | None = None, started_at: str | None = None,
                            final_sync_pending: bool = False,
                            transaction_hook: Callable[[sqlite3.Connection, int, str], None] | None = None,
                            preserve_existing_color: bool = False) -> None:
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        for uci in moves.split():
            board.push_uci(uci)
        game = chess.pgn.Game.from_board(board)
        game.headers["Event"] = "Lichess rated" if rated else "Lichess casual"
        game.headers["Site"] = f"https://lichess.org/{game_id}"
        previous = None
        if not (white_player and black_player and started_at):
            with self._connection() as db:
                previous = db.execute("SELECT white_player, black_player, started_at,game_time_precision FROM games "
                                      "WHERE game_id=?", (game_id,)).fetchone()
        if previous:
            white_player = white_player or previous["white_player"]
            black_player = black_player or previous["black_player"]
            started_at = started_at or previous["started_at"]
        precision = _time_precision(started_at)
        if precision == "unknown" and previous:
            precision = previous["game_time_precision"] or "unknown"
        if color == "white":
            white_player = white_player or "Вы"
            black_player = black_player or opponent or "?"
        else:
            white_player = white_player or opponent or "?"
            black_player = black_player or "Вы"
        date = _pgn_date(started_at)
        game.headers["Date"] = date
        game.headers["White"] = white_player
        game.headers["Black"] = black_player
        game.headers["TimeControl"] = clock or "-"
        game.headers["Result"] = ("*" if final_sync_pending else
                                   "1-0" if winner == "white" else "0-1" if winner == "black" else
                                   "1/2-1/2" if status in {
                                       "draw", "stalemate", "repetition", "50moves",
                                       "insufficientMaterial",
                                   } else "*")
        pgn = str(game)
        with self._connection() as db:
            db.execute("""INSERT INTO games (
                game_id, initial_fen, moves, color, opponent, rated, clock, status,
                winner, updated_at, pgn, white_player, black_player, started_at,game_time_precision,
                final_sync_pending, source, source_game_id, variant
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lichess', ?, 'standard')
                ON CONFLICT(game_id) DO UPDATE SET initial_fen=excluded.initial_fen,
                moves=excluded.moves, opponent=CASE WHEN ? THEN games.opponent ELSE excluded.opponent END,
                rated=excluded.rated, clock=excluded.clock, status=excluded.status,
                winner=excluded.winner, updated_at=excluded.updated_at, pgn=excluded.pgn,
                color=CASE WHEN ? THEN games.color ELSE excluded.color END,
                white_player=excluded.white_player, black_player=excluded.black_player,
                started_at=excluded.started_at,
                game_time_precision=excluded.game_time_precision,
                final_sync_pending=excluded.final_sync_pending,
                source=excluded.source, source_game_id=excluded.source_game_id,
                variant=excluded.variant""",
                (game_id, initial_fen, moves, color, opponent, int(rated), clock, status,
                 winner, datetime.now(timezone.utc).isoformat(), pgn, white_player,
                 black_player, started_at, precision, int(final_sync_pending), game_id,
                 int(preserve_existing_color),
                 int(preserve_existing_color)))
            current = db.execute("SELECT line_revision, line_hash FROM game_revisions "
                                 "WHERE game_id=? AND is_current=1", (game_id,)).fetchone()
            line_hash = _line_hash(initial_fen, moves)
            revision = (int(current["line_revision"]) if current and current["line_hash"] == line_hash
                        else int(current["line_revision"]) + 1 if current else 1)
            if not current or current["line_hash"] != line_hash:
                self._write_revision(db, {
                    "game_id": game_id, "initial_fen": initial_fen, "moves": moves, "pgn": pgn,
                }, revision, current=True)
            else:
                db.execute("UPDATE game_revisions SET pgn=? WHERE game_id=? AND line_revision=?",
                           (pgn, game_id, revision))
            db.execute("UPDATE games SET line_revision=?, import_error=NULL WHERE game_id=?",
                       (revision, game_id))
            if transaction_hook is not None:
                transaction_hook(db, revision, line_hash)
        self.invalidate_statistics_cache()

    @staticmethod
    def _write_revision(db: sqlite3.Connection, row: dict, revision: int,
                        *, current: bool) -> None:
        game_id = str(row["game_id"])
        initial_fen = str(row.get("initial_fen") or "startpos")
        moves = str(row.get("moves") or "")
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        if not board.is_valid():
            raise ValueError("Invalid initial FEN")
        pgn = str(row.get("pgn") or "")
        db.execute("UPDATE game_revisions SET is_current=0 WHERE game_id=?", (game_id,))
        db.execute("INSERT OR REPLACE INTO game_revisions "
                   "(game_id,line_revision,initial_fen,moves,pgn,line_hash,created_at,is_current) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (game_id, revision, initial_fen, moves, pgn, _line_hash(initial_fen, moves),
                    datetime.now(timezone.utc).isoformat(), int(current)))
        db.execute("DELETE FROM game_moves WHERE game_id=? AND line_revision=?",
                   (game_id, revision))
        db.execute("DELETE FROM game_positions WHERE game_id=? AND line_revision=?",
                   (game_id, revision))
        _insert_position(db, game_id, revision, 0, board)
        for ply, uci in enumerate(moves.split(), start=1):
            move = chess.Move.from_uci(uci)
            san = board.san(move)
            mover = "white" if board.turn == chess.WHITE else "black"
            board.push(move)
            db.execute("INSERT INTO game_moves "
                       "(game_id,line_revision,ply,uci,san,mover) VALUES (?,?,?,?,?,?)",
                       (game_id, revision, ply, uci, san, mover))
            _insert_position(db, game_id, revision, ply, board)

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

    def list_games(self, completed_only: bool = True) -> list[dict]:
        self._wait_before_read()
        with self._connection() as db:
            query = "SELECT * FROM games"
            if completed_only:
                query += " WHERE status NOT IN ('created', 'started', 'started_soon', 'playing')"
            query += " ORDER BY updated_at DESC"
            return [dict(row) for row in db.execute(query)]

    def list_games_missing_lichess_evaluations(self, account_id: str, *,
                                                limit: int = 25) -> list[dict]:
        """Return a bounded recent-first list whose current Lichess PGN evals are incomplete."""
        limit = max(1, min(100, int(limit)))
        with self._connection() as db:
            rows = db.execute(
                "SELECT g.game_id,gr.line_revision,gr.line_hash,g.started_at,g.variant "
                "FROM games g JOIN account_games ag ON ag.game_id=g.game_id "
                "JOIN game_revisions gr ON gr.game_id=g.game_id AND gr.is_current=1 "
                "WHERE ag.account_id=? AND g.status NOT IN "
                "('created','started','started_soon','playing') AND g.variant='standard' "
                "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
                "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
                "AND ar.line_hash=gr.line_hash AND ar.source='lichess-pgn' "
                "AND ar.status='complete' AND "
                "(SELECT COUNT(*) FROM position_assessments pa WHERE pa.run_id=ar.run_id "
                "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) >= "
                "(SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=g.game_id "
                "AND gm.line_revision=gr.line_revision)) "
                "ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,"
                "g.started_at DESC,g.game_id DESC LIMIT ?", (account_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def list_games_needing_fast_analysis(self, *, account_id: str | None = None,
                                         limit: int = 1000,
                                         engine_version: str | None = None,
                                         parameters: dict | None = None,
                                         retry_failed: bool = False) -> list[dict]:
        """Page recent eligible lines, skipping work already represented for this config."""
        limit = max(1, min(10_000, int(limit)))
        join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
        account_filter = "AND ag.account_id=? " if account_id else ""
        params: list[object] = [account_id] if account_id else []
        represented = ""
        if engine_version is not None and parameters is not None:
            params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
            represented_job_statuses = ("'queued','running','paused','complete'" if retry_failed
                                        else "'queued','running','paused','complete','failed','cancelled','stale'")
            represented = (
                "AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj "
                "WHERE aj.game_id=g.game_id AND aj.line_revision=gr.line_revision "
                "AND aj.line_hash=gr.line_hash AND aj.analysis_pass='fast' "
                "AND aj.engine_version=? AND aj.parameters_json=? "
                f"AND aj.status IN ({represented_job_statuses})) "
                "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
                "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
                "AND ar.line_hash=gr.line_hash AND ar.source='stockfish' "
                "AND ar.engine_version=? AND ar.parameters_json=? AND ar.status='complete') "
            )
            params.extend([engine_version, params_json, engine_version, params_json])
        params.append(limit)
        sql = (
            "SELECT DISTINCT g.game_id,gr.line_revision,gr.line_hash,g.started_at,g.variant "
            "FROM games g " + join +
            "JOIN game_revisions gr ON gr.game_id=g.game_id AND gr.is_current=1 "
            "WHERE g.status IN ('mate','resign','stalemate','timeout','draw','outoftime',"
            "'cheat','noStart','variantEnd','finished') AND COALESCE(g.final_sync_pending,0)=0 "
            "AND g.import_error IS NULL AND g.pgn IS NOT NULL "
            "AND (g.pgn LIKE '%[Result \"1-0\"]%' OR g.pgn LIKE '%[Result \"0-1\"]%' "
            "OR g.pgn LIKE '%[Result \"1/2-1/2\"]%') AND g.variant='standard' "
            + account_filter +
            "AND NOT EXISTS (SELECT 1 FROM analysis_runs ar "
            "WHERE ar.game_id=g.game_id AND ar.line_revision=gr.line_revision "
            "AND ar.line_hash=gr.line_hash AND ar.source='lichess-pgn' "
            "AND ar.status='complete' AND "
            "(SELECT COUNT(*) FROM position_assessments pa WHERE pa.run_id=ar.run_id "
            "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) >= "
            "(SELECT COUNT(*) FROM game_moves gm WHERE gm.game_id=g.game_id "
            "AND gm.line_revision=gr.line_revision)) "
            + represented +
            "ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,"
            "g.started_at DESC,g.game_id DESC LIMIT ?")
        with self._connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def enqueue_analysis_jobs(self, candidates: list[dict], *, analysis_pass: str,
                              engine_version: str, parameters: dict,
                              retry_failed: bool = False) -> dict[str, int]:
        if analysis_pass not in {"fast", "review"}:
            raise ValueError("Unknown analysis pass")
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        queued = skipped = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                game_id = str(candidate["game_id"])
                revision = int(candidate["line_revision"])
                line_hash = str(candidate["line_hash"])
                job_id = uuid.uuid4().hex
                if retry_failed:
                    requeued = db.execute(
                        "UPDATE analysis_jobs SET status='queued',attempts=0,lease_owner=NULL,"
                        "lease_until=NULL,result_run_id=NULL,error=NULL,started_at=NULL,finished_at=NULL,"
                        "updated_at=? WHERE game_id=? AND line_revision=? AND line_hash=? "
                        "AND analysis_pass=? AND engine_version=? AND parameters_json=? "
                        "AND status IN ('failed','cancelled','stale')",
                        (now, game_id, revision, line_hash, analysis_pass, engine_version, params_json))
                    if requeued.rowcount:
                        queued += 1
                        continue
                cursor = db.execute(
                    "INSERT OR IGNORE INTO analysis_jobs(job_id,game_id,line_revision,line_hash,"
                    "analysis_pass,engine_version,parameters_json,status,attempts,priority_at,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',0,?,?,?)",
                    (job_id, game_id, revision, line_hash, analysis_pass, engine_version,
                     params_json, candidate.get("started_at"), now, now))
                if cursor.rowcount:
                    queued += 1
                else:
                    skipped += 1
        return {"queued": queued, "already_present": skipped}

    def claim_next_analysis_job(self, worker_id: str, *, engine_version: str,
                                analysis_pass: str, parameters: dict | None = None,
                                lease_seconds: int = 300) -> dict | None:
        if not worker_id:
            raise ValueError("worker_id is required")
        lease_seconds = max(30, min(3600, int(lease_seconds)))
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":")) if parameters is not None else None
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            while True:
                params_clause = " AND parameters_json=?" if params_json is not None else ""
                values = [analysis_pass, engine_version]
                if params_json is not None:
                    values.append(params_json)
                values.append(now)
                row = db.execute(
                    "SELECT * FROM analysis_jobs WHERE analysis_pass=? AND engine_version=? "
                    + params_clause + " AND (status='queued' OR "
                    "(status='running' AND (lease_until IS NULL OR lease_until<?))) "
                    "ORDER BY CASE WHEN priority_at IS NULL THEN 1 ELSE 0 END,"
                    "priority_at DESC,created_at,job_id LIMIT 1", values).fetchone()
                if not row:
                    return None
                current = db.execute(
                    "SELECT line_revision,line_hash FROM game_revisions "
                    "WHERE game_id=? AND is_current=1", (row["game_id"],)).fetchone()
                if (not current or int(current["line_revision"]) != int(row["line_revision"])
                        or current["line_hash"] != row["line_hash"]):
                    db.execute("UPDATE analysis_jobs SET status='stale',updated_at=?,"
                                "lease_owner=NULL,lease_until=NULL WHERE job_id=?",
                                (now, row["job_id"]))
                    continue
                db.execute("UPDATE analysis_jobs SET status='running',attempts=attempts+1,"
                            "lease_owner=?,lease_until=?,started_at=?,updated_at=?,error=NULL "
                            "WHERE job_id=?",
                            (worker_id, until, now, now, row["job_id"]))
                result = dict(row)
                result.update(status="running", attempts=int(row["attempts"]) + 1,
                              lease_owner=worker_id, lease_until=until, started_at=now,
                              updated_at=now, error=None)
                return result

    def list_completed_fast_analysis_jobs(self, *, engine_version: str,
                                          parameters: dict,
                                          account_id: str | None = None) -> list[dict]:
        """List exact-config completed fast jobs with their persisted result runs."""
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        join = " JOIN account_games ag ON ag.game_id=aj.game_id " if account_id else ""
        account_clause = " AND ag.account_id=?" if account_id else ""
        values: list[object] = [engine_version, params_json]
        if account_id:
            values.append(account_id)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT DISTINCT aj.game_id,aj.line_revision,aj.line_hash,aj.result_run_id "
                "FROM analysis_jobs aj " + join + "WHERE aj.analysis_pass='fast' "
                "AND aj.engine_version=? AND aj.parameters_json=? AND aj.status='complete' "
                "AND aj.result_run_id IS NOT NULL" + account_clause +
                " ORDER BY aj.game_id", values)]

    def list_reviewable_analysis_runs(self, *, engine_version: str,
                                      parameters: dict,
                                      account_id: str | None = None) -> list[dict]:
        """Select one deterministic current source run per game for selective review."""
        params_json = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
        account_join = " JOIN account_games ag ON ag.game_id=ar.game_id " if account_id else ""
        account_clause = " AND ag.account_id=?" if account_id else ""
        values: list[object] = [engine_version, params_json]
        if account_id:
            values.append(account_id)
        sql = (
            "WITH eligible AS (SELECT ar.run_id,ar.game_id,ar.line_revision,ar.line_hash,ar.source,"
            "ROW_NUMBER() OVER(PARTITION BY ar.game_id ORDER BY "
            "CASE WHEN ar.source='stockfish' THEN 0 ELSE 1 END,ar.finished_at DESC,ar.run_id DESC) rank "
            "FROM analysis_runs ar JOIN game_revisions gr ON gr.game_id=ar.game_id "
            "AND gr.line_revision=ar.line_revision AND gr.is_current=1 "
            "LEFT JOIN analysis_jobs aj ON aj.result_run_id=ar.run_id " + account_join +
            "WHERE ((ar.source='stockfish' AND ar.status='complete' AND aj.analysis_pass='fast' "
            "AND aj.status='complete' AND aj.engine_version=? AND aj.parameters_json=?) "
            "OR (ar.source='lichess-pgn' AND ar.status IN ('complete','partial')))" +
            account_clause + ") SELECT run_id,game_id,line_revision,line_hash,source "
            "FROM eligible WHERE rank=1 ORDER BY game_id")
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, values)]

    def renew_analysis_job_lease(self, job_id: str, worker_id: str, *,
                                 lease_seconds: int = 300) -> bool:
        lease_seconds = max(30, min(3600, int(lease_seconds)))
        now_dt = datetime.now(timezone.utc)
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE analysis_jobs SET lease_until=?,updated_at=? "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                ((now_dt + timedelta(seconds=lease_seconds)).isoformat(),
                 now_dt.isoformat(), job_id, worker_id))
        return cursor.rowcount == 1

    def finish_analysis_job(self, job_id: str, worker_id: str, *, status: str,
                            result_run_id: str | None = None,
                            error: str | None = None) -> None:
        if status not in {"complete", "paused", "cancelled", "failed", "stale"}:
            raise ValueError("Invalid terminal analysis job status")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE analysis_jobs SET status=?,result_run_id=?,error=?,updated_at=?,"
                "finished_at=?,lease_owner=NULL,lease_until=NULL "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                (status, result_run_id, error[:500] if error else None, now,
                 now if status in {"complete", "cancelled", "failed", "stale"} else None,
                 job_id, worker_id))
            if cursor.rowcount != 1:
                raise RuntimeError("Analysis job lease was lost before it could be finalized.")

    def resume_paused_analysis_jobs(self, *, engine_version: str | None = None,
                                    parameters: dict | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        params_json = (json.dumps(parameters, sort_keys=True, separators=(",", ":"))
                       if parameters is not None else None)
        with self._connection() as db:
            if engine_version is None and params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused'", (now,))
            elif params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND engine_version=?",
                                    (now, engine_version))
            elif engine_version is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND parameters_json=?",
                                    (now, params_json))
            else:
                cursor = db.execute("UPDATE analysis_jobs SET status='queued',updated_at=? "
                                    "WHERE status='paused' AND engine_version=? "
                                    "AND parameters_json=?",
                                    (now, engine_version, params_json))
        return cursor.rowcount

    def cancel_queued_analysis_jobs(self, *, engine_version: str | None = None,
                                    parameters: dict | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        params_json = (json.dumps(parameters, sort_keys=True, separators=(",", ":"))
                       if parameters is not None else None)
        with self._connection() as db:
            if engine_version is None and params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued'", (now, now))
            elif params_json is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND engine_version=?",
                                    (now, now, engine_version))
            elif engine_version is None:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND parameters_json=?",
                                    (now, now, params_json))
            else:
                cursor = db.execute("UPDATE analysis_jobs SET status='cancelled',updated_at=?,"
                                    "finished_at=? WHERE status='queued' AND engine_version=? "
                                    "AND parameters_json=?",
                                    (now, now, engine_version, params_json))
        return cursor.rowcount

    def analysis_job_counts(self, *, engine_version: str | None = None,
                            parameters: dict | None = None) -> dict[str, int]:
        clauses: list[str] = []
        values: list[str] = []
        if engine_version is not None:
            clauses.append("engine_version=?")
            values.append(engine_version)
        if parameters is not None:
            clauses.append("parameters_json=?")
            values.append(json.dumps(parameters, sort_keys=True, separators=(",", ":")))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as db:
            rows = db.execute("SELECT status,COUNT(*) AS count FROM analysis_jobs" + where +
                              " GROUP BY status", tuple(values)).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def search_games(self, query: str = "", *, page: int = 0,
                     page_size: int = DEFAULT_PAGE_SIZE, completed_only: bool = False,
                     source: str | None = None, opponent: str | None = None,
                     account_id: str | None = None) -> dict:
        """Search the local library without loading every PGN into a widget."""
        self._wait_before_read()
        page = max(0, int(page))
        page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        clauses: list[str] = []
        params: list[object] = []
        join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
        if account_id:
            clauses.append("ag.account_id=?")
            params.append(account_id)
        opponent_expr = "COALESCE(ag.opponent,g.opponent)" if account_id else "g.opponent"
        term = query.strip()
        if term:
            clauses.append(f"(g.game_id LIKE ? OR {opponent_expr} LIKE ? OR g.white_player LIKE ? "
                           "OR g.black_player LIKE ? OR g.status LIKE ?)")
            params.extend([f"%{term}%"] * 5)
        if completed_only:
            clauses.append("status NOT IN ('created','started','started_soon','playing')")
        if source:
            clauses.append("source=?")
            params.append(source)
        if opponent:
            clauses.append(f"{opponent_expr} LIKE ?")
            params.append(f"%{opponent}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as db:
            total = db.execute("SELECT COUNT(DISTINCT g.game_id) FROM games g" + join + where,
                               params).fetchone()[0]
            selected = "g.*"
            if account_id:
                selected += ",ag.opponent AS account_opponent,ag.player_name AS account_player_name,ag.color AS account_color"
            rows = db.execute(f"SELECT {selected} FROM games g" + join + where +
                              " ORDER BY g.updated_at DESC, g.game_id LIMIT ? OFFSET ?",
                              [*params, page_size, page * page_size]).fetchall()
        return {"items": [dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size}

    def save_note(self, game_id: str, body: str, *, line_revision: int | None = None,
                  ply: int | None = None, position_uid: str | None = None,
                  author: str | None = None) -> str:
        text = body.strip()
        if not text:
            raise ValueError("Note cannot be empty")
        self._wait_before_read(game_id)
        with self._connection() as db:
            if not db.execute("SELECT 1 FROM games WHERE game_id=?", (game_id,)).fetchone():
                raise KeyError(game_id)
            if position_uid:
                pos = db.execute("SELECT line_revision,ply FROM game_positions "
                                 "WHERE position_uid=? AND game_id=?",
                                 (position_uid, game_id)).fetchone()
                if not pos:
                    raise ValueError("Unknown position_uid")
                line_revision, ply = pos["line_revision"], pos["ply"]
            elif line_revision is not None or ply is not None:
                pos = db.execute("SELECT position_uid FROM game_positions WHERE game_id=? "
                                 "AND line_revision=? AND ply=?",
                                 (game_id, line_revision, ply)).fetchone()
                if not pos:
                    raise ValueError("Unknown game position")
                position_uid = pos["position_uid"]
            now = datetime.now(timezone.utc).isoformat()
            note_id = uuid.uuid4().hex
            db.execute("INSERT INTO game_notes VALUES (?,?,?,?,?,?,?,?,?)",
                       (note_id, game_id, line_revision, ply, position_uid, text,
                        author, now, now))
        return note_id

    def update_note(self, note_id: str, body: str) -> None:
        text = body.strip()
        if not text:
            raise ValueError("Note cannot be empty")
        with self._connection() as db:
            cursor = db.execute("UPDATE game_notes SET body=?,updated_at=? WHERE note_id=?",
                                (text, datetime.now(timezone.utc).isoformat(), note_id))
            if not cursor.rowcount:
                raise KeyError(note_id)

    def delete_note(self, note_id: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM game_notes WHERE note_id=?", (note_id,))

    def list_notes(self, game_id: str, *, line_revision: int | None = None,
                   ply: int | None = None) -> list[dict]:
        self._wait_before_read(game_id)
        clauses = ["game_id=?"]
        params: list[object] = [game_id]
        if line_revision is not None:
            clauses.append("line_revision=?")
            params.append(line_revision)
        if ply is not None:
            clauses.append("ply=?")
            params.append(ply)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM game_notes WHERE " + " AND ".join(clauses) +
                " ORDER BY created_at,note_id", params)]

    def current_line(self, game_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM game_revisions WHERE game_id=? AND is_current=1",
                             (game_id,)).fetchone()
            return dict(row) if row else None

    def ensure_archive_account(self, username: str) -> str:
        normalized = username.strip().lower()
        if not normalized:
            raise ValueError("Lichess username is required")
        account_id = f"lichess:{normalized}"
        if normalized in self._archive_account_cache:
            return account_id
        with self._connection() as db:
            db.execute("INSERT INTO accounts(account_id,provider,username,created_at) "
                       "VALUES (?,'lichess',?,?) ON CONFLICT(account_id) DO UPDATE SET "
                       "username=excluded.username", (account_id, username.strip(),
                       datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR IGNORE INTO archive_sync_state(account_id,updated_at) "
                       "VALUES (?,?)", (account_id, datetime.now(timezone.utc).isoformat()))
        self._archive_account_cache[normalized] = account_id
        return account_id

    def archive_sync_state(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM archive_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return dict(row) if row else {}

    def begin_archive_pilot_run(self, account_id: str, requested_limit: int) -> str:
        if not 5 <= int(requested_limit) <= 10:
            raise ValueError("Пробный импорт должен быть ограничен 5–10 партиями.")
        run_id = uuid.uuid4().hex
        with self._connection() as db:
            db.execute("INSERT INTO archive_pilot_runs(run_id,account_id,requested_limit,status,started_at) "
                       "VALUES (?,?,?,'running',?)",
                       (run_id, account_id, int(requested_limit), datetime.now(timezone.utc).isoformat()))
        return run_id

    def record_archive_pilot_received(self, run_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_pilot_runs SET received_count=received_count+1 WHERE run_id=?",
                       (run_id,))

    def finish_archive_pilot_run(self, run_id: str, status: str, error: str | None = None) -> None:
        if status not in {"completed", "interrupted", "error"}:
            raise ValueError("Unknown pilot run status")
        with self._connection() as db:
            db.execute("UPDATE archive_pilot_runs SET status=?,finished_at=?,last_error=? WHERE run_id=?",
                       (status, datetime.now(timezone.utc).isoformat(),
                        error[:500] if error else None, run_id))

    def list_archive_pilot_runs(self, account_id: str, limit: int = 10) -> list[dict]:
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM archive_pilot_runs WHERE account_id=? ORDER BY started_at DESC LIMIT ?",
                (account_id, max(1, min(int(limit), 100))))]

    def import_archive_record(self, username: str, record: dict, *, checkpoint_ms: int | None,
                              backfill: bool, pilot_run_id: str | None = None,
                              _return_inserted: bool = False) -> str | tuple[str, bool]:
        """Upsert one exported PGN, link it to its account, then advance the durable cursor."""
        import io

        game_id = str(record.get("id") or "").strip()
        raw_pgn = str(record.get("pgn") or "")
        if not game_id or not raw_pgn:
            raise ValueError("Archive record requires id and pgn")
        parsed = chess.pgn.read_game(io.StringIO(raw_pgn))
        variant = str(record.get("variant") or
                      (parsed.headers.get("Variant") if parsed else None) or "standard").lower()
        if parsed is None or parsed.errors:
            details = "; ".join(str(error) for error in (parsed.errors if parsed else []))
            error = "Malformed PGN quarantined" + (f": {details[:300]}" if details else "")
            return self._import_raw_only_record(username, record, parsed, variant, raw_pgn,
                                                error, checkpoint_ms, backfill,
                                                pilot_run_id=pilot_run_id,
                                                _return_inserted=_return_inserted)
        if variant != "standard":
            # Keep non-standard games searchable/exportable without pretending their
            # move rules or initial position are standard chess.
            return self._import_raw_only_record(
                username, record, parsed, variant, raw_pgn,
                f"Unsupported variant: {variant}; board viewing and analysis disabled",
                checkpoint_ms, backfill, pilot_run_id=pilot_run_id,
                _return_inserted=_return_inserted)
        initial_fen = parsed.headers.get("FEN", "startpos")
        uci_moves = []
        for move in parsed.mainline_moves():
            uci_moves.append(move.uci())
        moves = " ".join(uci_moves)
        white = parsed.headers.get("White", "?")
        black = parsed.headers.get("Black", "?")
        is_white = white.casefold() == username.casefold()
        player_color = "white" if is_white else "black"
        opponent = black if is_white else white
        result = parsed.headers.get("Result", "*")
        winner = "white" if result == "1-0" else "black" if result == "0-1" else None
        status = ("draw" if result == "1/2-1/2" else "finished"
                  if result in {"1-0", "0-1"} else "unknownFinish")
        clock = parsed.headers.get("TimeControl", "-")
        header_date = parsed.headers.get("UTCDate") or parsed.headers.get("Date")
        started_at = None
        time_precision = "unknown"
        if header_date and len(header_date) == 10 and header_date[4] in {".", "-"}:
            try:
                parsed_date = datetime.strptime(header_date, "%Y.%m.%d" if header_date[4] == "."
                                                else "%Y-%m-%d").date()
                started_at = parsed_date.isoformat() + "T00:00:00+00:00"
                time_precision = "date_only"
            except ValueError:
                pass
        if started_at is None and record.get("createdAt") is not None:
            try:
                started_at = datetime.fromtimestamp(int(record["createdAt"]) / 1000,
                    timezone.utc).isoformat()
                time_precision = "timestamp"
            except (TypeError, ValueError, OverflowError, OSError):
                started_at = None
                time_precision = "unknown"
        lichess_assessments = []
        for ply, node in enumerate(parsed.mainline(), start=1):
            evaluation = node.eval()
            if evaluation is None:
                continue
            score = evaluation.pov(chess.WHITE)
            lichess_assessments.append({
                "ply": ply, "white_cp": score.score(mate_score=None),
                "mate_white": score.mate(), "depth": node.eval_depth(),
            })
        raw_hash = hashlib.sha256(raw_pgn.encode("utf-8")).hexdigest()
        players = record.get("players") if isinstance(record.get("players"), dict) else {}
        white_data = players.get("white") if isinstance(players.get("white"), dict) else {}
        black_data = players.get("black") if isinstance(players.get("black"), dict) else {}
        opening = record.get("opening") if isinstance(record.get("opening"), dict) else {}
        account_id = self.ensure_archive_account(username)
        imported_at = datetime.now(timezone.utc).isoformat()
        phase = record.get("division")
        middle_start = end_start = None
        from .phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries
        if isinstance(phase, dict):
            middle_start = _phase_ply(phase.get("middle"))
            end_start = _phase_ply(phase.get("end"))
        fallback_middle = fallback_end = None
        if middle_start is None and end_start is None:
            phase_board = (chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen))
            phase_boards = [phase_board.copy(stack=False)]
            for raw_move in moves.split():
                move = chess.Move.from_uci(raw_move)
                phase_board.push(move)
                phase_boards.append(phase_board.copy(stack=False))
            fallback_middle, fallback_end = infer_phase_boundaries(phase_boards)

        inserted_link = {"value": False}

        def commit_archive(db: sqlite3.Connection, revision: int, line_hash: str) -> None:
            db.execute("UPDATE games SET source='lichess_archive', source_game_id=?, variant=?, "
                       "started_at=COALESCE(started_at,?), "
                       "game_time_precision=CASE WHEN ?='unknown' THEN game_time_precision "
                       "ELSE ? END, "
                       "raw_checksum=?, raw_pgn=?, last_move_at=COALESCE(?,last_move_at), "
                       "lichess_accuracy_white=COALESCE(?,lichess_accuracy_white), "
                       "lichess_accuracy_black=COALESCE(?,lichess_accuracy_black), "
                       "white_rating=COALESCE(?,white_rating),black_rating=COALESCE(?,black_rating), "
                       "opening_eco=COALESCE(?,opening_eco),opening_name=COALESCE(?,opening_name) "
                       "WHERE game_id=?",
                       (game_id, variant, started_at, time_precision, time_precision,
                        raw_hash, raw_pgn, record.get("lastMoveAt"),
                        (record.get("accuracy") or {}).get("white"),
                        (record.get("accuracy") or {}).get("black"), white_data.get("rating"),
                        black_data.get("rating"), opening.get("eco"), opening.get("name"), game_id))
            player_rating = white_data.get("rating") if player_color == "white" else black_data.get("rating")
            opponent_rating = black_data.get("rating") if player_color == "white" else white_data.get("rating")
            existing_account_link = db.execute(
                "SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                (account_id, game_id)).fetchone()
            inserted_link["value"] = not bool(existing_account_link)
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,player_rating,opponent_rating,opponent,player_name) "
                       "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,game_id) DO UPDATE SET "
                       "remote_id=excluded.remote_id, imported_at=excluded.imported_at,color=excluded.color,"
                       "player_rating=COALESCE(excluded.player_rating,account_games.player_rating),"
                       "opponent_rating=COALESCE(excluded.opponent_rating,account_games.opponent_rating),"
                       "opponent=excluded.opponent,player_name=excluded.player_name",
                       (account_id, game_id, game_id, imported_at, player_color, player_rating,
                        opponent_rating, opponent, white if player_color == "white" else black))
            eval_parameters = json.dumps({"format": "pgn-eval-v1"}, sort_keys=True)
            existing_eval_run = db.execute(
                "SELECT * FROM analysis_runs WHERE game_id=? AND line_revision=? AND line_hash=? "
                "AND source='lichess-pgn' AND parameters_json=? "
                "ORDER BY finished_at DESC,run_id DESC LIMIT 1",
                (game_id, revision, line_hash, eval_parameters)).fetchone()
            if lichess_assessments or existing_eval_run:
                now = datetime.now(timezone.utc).isoformat()
                if existing_eval_run:
                    run_id = existing_eval_run["run_id"]
                    changed = False
                else:
                    run_id = uuid.uuid4().hex
                    db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (run_id, game_id, revision, line_hash, "lichess-pgn", "Lichess", None,
                                eval_parameters, now, now, "partial", None))
                    changed = True
                for item in lichess_assessments:
                    old = db.execute("SELECT white_cp,mate_white,depth,nodes,pv_uci "
                                     "FROM position_assessments WHERE run_id=? AND ply=?",
                                     (run_id, item["ply"])).fetchone()
                    incoming_has_score = (item["white_cp"] is not None or
                                          item["mate_white"] is not None)
                    if old:
                        old_has_score = old["white_cp"] is not None or old["mate_white"] is not None
                        incoming_depth = item["depth"]
                        old_depth = old["depth"]
                        # Preserve an existing higher-quality assessment when a later
                        # export is shallower or omits depth; equal/newer depth replaces it.
                        if not incoming_has_score or (old_has_score and old_depth is not None and
                                (incoming_depth is None or incoming_depth < old_depth)):
                            continue
                        values = (item["white_cp"], item["mate_white"], incoming_depth)
                        if tuple(old[:3]) != values:
                            db.execute("UPDATE position_assessments SET white_cp=?,mate_white=?,depth=? "
                                       "WHERE run_id=? AND ply=?",
                                       (*values, run_id, item["ply"]))
                            changed = True
                    else:
                        db.execute("INSERT INTO position_assessments VALUES (?,?,?,?,?,?,?,?,?)",
                                   (run_id, game_id, revision, item["ply"], item["white_cp"],
                                    item["mate_white"], item["depth"], None, ""))
                        changed = True
                expected_positions = db.execute(
                    "SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
                    (game_id, revision)).fetchone()[0]
                scored_positions = db.execute(
                    "SELECT COUNT(*) FROM position_assessments WHERE run_id=? "
                    "AND (white_cp IS NOT NULL OR mate_white IS NOT NULL)", (run_id,)).fetchone()[0]
                eval_status = "complete" if expected_positions and scored_positions >= expected_positions else "partial"
                if existing_eval_run and existing_eval_run["status"] != eval_status:
                    changed = True
                if changed:
                    db.execute("UPDATE analysis_runs SET status=?,finished_at=?,error=NULL WHERE run_id=?",
                               (eval_status, now, run_id))
                existing_metrics = db.execute(
                    "SELECT 1 FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' LIMIT 1",
                    (run_id,)).fetchone()
                if changed or not existing_metrics:
                    self._insert_derived_metrics_for_run(
                        db, run_id, user_color=player_color, replace=bool(changed))
            if middle_start is not None or end_start is not None:
                db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?) "
                           "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                           "middle_start_ply=excluded.middle_start_ply,end_start_ply=excluded.end_start_ply,"
                           "source=excluded.source,method_version=excluded.method_version",
                           (game_id, revision, middle_start, end_start, "lichess", "lichess-division-v1"))
                self._apply_phase_labels(db, game_id, revision, middle_start, end_start)
            elif fallback_middle is not None or fallback_end is not None:
                existing_phase = db.execute(
                    "SELECT source FROM game_phase_boundaries WHERE game_id=? AND line_revision=?",
                    (game_id, revision)).fetchone()
                if not existing_phase or existing_phase["source"] != "lichess":
                    db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?) "
                               "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                               "middle_start_ply=excluded.middle_start_ply,end_start_ply=excluded.end_start_ply,"
                               "source=excluded.source,method_version=excluded.method_version",
                               (game_id, revision, fallback_middle, fallback_end,
                                "material-fallback", PHASE_FALLBACK_VERSION))
                    self._apply_phase_labels(db, game_id, revision, fallback_middle, fallback_end)
            if pilot_run_id:
                db.execute("UPDATE archive_pilot_runs SET new_count=new_count+? WHERE run_id=?",
                           (int(not existing_account_link), pilot_run_id))
            elif backfill and checkpoint_ms is not None:
                db.execute("UPDATE archive_sync_state SET backfill_before=?,"
                           "imported_count=imported_count+?,updated_at=? WHERE account_id=?",
                           (checkpoint_ms + 1, int(not existing_account_link), imported_at, account_id))
            else:
                db.execute("UPDATE archive_sync_state SET imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (int(not existing_account_link), imported_at, account_id))

        args = (game_id, initial_fen, moves, player_color, opponent,
                bool(record.get("rated", False)), clock, status, winner)
        kwargs = {"white_player": white, "black_player": black, "started_at": started_at,
                  "transaction_hook": commit_archive, "preserve_existing_color": True}
        if self._writer is not None:
            # Archive workers wait for the durable single-writer transaction before
            # requesting the next NDJSON record/checkpoint.
            self._submit_writer(self._save_snapshot_sync, *args, wait=True, **kwargs)
        else:
            self._save_snapshot_sync(*args, **kwargs)
        return (game_id, inserted_link["value"]) if _return_inserted else game_id

    def _import_raw_only_record(self, username: str, record: dict, parsed,
                                variant: str, raw_pgn: str, import_error: str,
                                checkpoint_ms: int | None, backfill: bool, *,
                                pilot_run_id: str | None = None,
                                _return_inserted: bool = False) -> str | tuple[str, bool]:
        args = (username, record, parsed, variant, raw_pgn, import_error,
                checkpoint_ms, backfill, pilot_run_id)
        if self._writer is not None:
            return self._submit_writer(self._import_raw_only_record_sync, *args,
                                       _return_inserted=_return_inserted, wait=True)
        return self._import_raw_only_record_sync(*args, _return_inserted=_return_inserted)

    def _import_raw_only_record_sync(self, username: str, record: dict, parsed,
                                     variant: str, raw_pgn: str, import_error: str,
                                     checkpoint_ms: int | None, backfill: bool,
                                     pilot_run_id: str | None = None, *,
                                     _return_inserted: bool = False) -> str | tuple[str, bool]:
        game_id = str(record.get("id") or "").strip()
        if not game_id or not raw_pgn:
            raise ValueError("Raw-only archive record requires id and PGN")
        account_id = self.ensure_archive_account(username)
        headers = parsed.headers if parsed is not None else {}
        white, black = headers.get("White", "?"), headers.get("Black", "?")
        color = "white" if white.casefold() == username.casefold() else "black"
        opponent = black if color == "white" else white
        result = headers.get("Result", "*")
        winner = "white" if result == "1-0" else "black" if result == "0-1" else None
        now = datetime.now(timezone.utc).isoformat()
        raw_hash = hashlib.sha256(raw_pgn.encode("utf-8")).hexdigest()
        players = record.get("players") if isinstance(record.get("players"), dict) else {}
        white_user = players.get("white") if isinstance(players.get("white"), dict) else {}
        black_user = players.get("black") if isinstance(players.get("black"), dict) else {}
        white_rating = white_user.get("rating")
        black_rating = black_user.get("rating")
        with self._connection() as db:
            existing_account_link = db.execute(
                "SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                (account_id, game_id)).fetchone()
            db.execute("INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,winner,"
                       "updated_at,pgn,white_player,black_player,source,source_game_id,variant,import_error,"
                       "raw_checksum,raw_pgn,last_move_at,white_rating,black_rating) "
                       "VALUES(?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lichess_archive', ?, ?, ?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(game_id) DO UPDATE SET source='lichess_archive',source_game_id=excluded.source_game_id,"
                       "variant=excluded.variant,import_error=excluded.import_error,raw_checksum=excluded.raw_checksum,"
                       "raw_pgn=excluded.raw_pgn,last_move_at=excluded.last_move_at",
                       (game_id, color, opponent, int(bool(record.get("rated", False))),
                        headers.get("TimeControl", "-"),
                        "draw" if result == "1/2-1/2" else "finished" if winner else "unknownFinish",
                        winner, now, raw_pgn, white, black,
                        game_id, variant, import_error, raw_hash,
                        raw_pgn, record.get("lastMoveAt"), white_rating, black_rating))
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,player_rating,opponent_rating,opponent,player_name) "
                       "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,game_id) DO UPDATE SET color=excluded.color,"
                       "player_rating=COALESCE(excluded.player_rating,account_games.player_rating),"
                       "opponent_rating=COALESCE(excluded.opponent_rating,account_games.opponent_rating),"
                       "opponent=excluded.opponent,player_name=excluded.player_name,imported_at=excluded.imported_at",
                       (account_id, game_id, game_id, now, color,
                        white_rating if color == "white" else black_rating,
                        black_rating if color == "white" else white_rating, opponent,
                        white if color == "white" else black))
            if pilot_run_id:
                db.execute("UPDATE archive_pilot_runs SET new_count=new_count+? WHERE run_id=?",
                           (int(not existing_account_link), pilot_run_id))
            elif backfill and checkpoint_ms is not None:
                db.execute("UPDATE archive_sync_state SET backfill_before=?,imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (checkpoint_ms + 1, int(not existing_account_link), now, account_id))
            else:
                db.execute("UPDATE archive_sync_state SET imported_count=imported_count+?,"
                           "updated_at=? WHERE account_id=?",
                           (int(not existing_account_link), now, account_id))
        self.invalidate_statistics_cache()
        return (game_id, not bool(existing_account_link)) if _return_inserted else game_id

    def begin_archive_backfill(self, account_id: str, started_at_ms: int) -> int:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET backfill_started_at=COALESCE(backfill_started_at,?),"
                       "backfill_before=COALESCE(backfill_before,?),updated_at=? WHERE account_id=?",
                       (int(started_at_ms), int(started_at_ms), datetime.now(timezone.utc).isoformat(), account_id))
            row = db.execute("SELECT backfill_started_at FROM archive_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return int(row[0])

    def finish_archive_backfill(self, account_id: str, last_sync_new_count: int = 0) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET backfill_complete=1,incremental_since=MAX("
                       "COALESCE(incremental_since,0),COALESCE(backfill_started_at,0)),"
                       "updated_at=? WHERE account_id=?",
                       (datetime.now(timezone.utc).isoformat(), account_id))

    def finish_archive_incremental(self, account_id: str, cursor_ms: int,
                                   last_sync_new_count: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET incremental_since=MAX(COALESCE(incremental_since,0),?), "
                       "last_sync_new_count=?,last_success_at=?,last_error=NULL,updated_at=? WHERE account_id=?",
                       (int(cursor_ms), int(last_sync_new_count), now, now, account_id))

    def record_archive_interrupted(self, account_id: str) -> None:
        self.record_archive_error(account_id, "Синхронизация остановлена; сохранённый курсор можно возобновить.")

    def archive_game_linked(self, account_id: str, game_id: str) -> bool:
        with self._connection() as db:
            return bool(db.execute("SELECT 1 FROM account_games WHERE account_id=? AND game_id=?",
                                   (account_id, game_id)).fetchone())

    def record_archive_error(self, account_id: str, error: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE archive_sync_state SET last_error=?,updated_at=? WHERE account_id=?",
                       (error[:500], datetime.now(timezone.utc).isoformat(), account_id))

    def save_analysis(self, game_id: str, *, line_revision: int, line_hash: str,
                      source: str, engine_name: str | None, engine_version: str | None,
                      parameters: dict, assessments: list[dict],
                      classifications: list[dict], status: str = "complete",
                      error: str | None = None,
                      alternatives: list[dict] | None = None) -> str:
        from .move_metrics import build_move_facts

        self._wait_before_read(game_id)
        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, game_id, line_revision, line_hash, source, engine_name,
                        engine_version, json.dumps(parameters, sort_keys=True), now, now,
                        status, error))
            db.executemany("INSERT INTO position_assessments VALUES (?,?,?,?,?,?,?,?,?)",
                           [(run_id, game_id, line_revision, item["ply"], item.get("white_cp"),
                             item.get("mate_white"), item.get("depth"), item.get("nodes"),
                             " ".join(item.get("pv", []))) for item in assessments])
            db.executemany("""INSERT INTO analysis_alternatives (
                run_id,game_id,line_revision,ply,multipv_rank,white_cp,mate_white,
                depth,nodes,pv_uci
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(run_id, game_id, line_revision, item["ply"], item["multipv_rank"],
                  item.get("white_cp"), item.get("mate_white"), item.get("depth"),
                  item.get("nodes"), " ".join(item.get("pv", [])))
                 for item in alternatives or []])
            db.executemany("INSERT INTO move_classifications VALUES (?,?,?,?,?,?,?,?,?)",
                           [(run_id, game_id, line_revision, item["ply"], item["mover"],
                             item.get("phase", "unknown"), item.get("eval_loss_cp"),
                             item.get("label", "unknown"), item.get("algorithm_version", ""))
                            for item in classifications])
            moves = [dict(row) for row in db.execute(
                "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
                (game_id, line_revision))]
            revision_row = db.execute("SELECT initial_fen FROM game_revisions WHERE game_id=? "
                                      "AND line_revision=?", (game_id, line_revision)).fetchone()
            metrics, events = build_move_facts(
                assessments, moves, source=source, run_id=run_id,
                classifications=classifications,
                initial_fen=revision_row["initial_fen"] if revision_row else "startpos")
            for event in events:
                event["tags"] = ["opportunity"]
            from .event_detectors import detect_registered_events
            game = db.execute("SELECT color FROM games WHERE game_id=?", (game_id,)).fetchone()
            events.extend(detect_registered_events(
                assessments, moves, metrics, run_id=run_id, source=source,
                user_color=game["color"] if game else None))
            db.executemany("""INSERT INTO move_metrics (
                run_id,game_id,line_revision,ply,metric_version,source,
                white_cp_before,mate_white_before,white_cp_after,mate_white_after,
                cp_loss,mate_discontinuity,wdl_before_json,wdl_after_json,wdl_model,
                accuracy,accuracy_source,accuracy_formula_id,severity,
                severity_policy_version,legacy_label
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(run_id, game_id, line_revision, row["ply"], row["metric_version"], row["source"],
                  row["white_cp_before"], row["mate_white_before"], row["white_cp_after"],
                  row["mate_white_after"], row["cp_loss"], row["mate_discontinuity"],
                  row["wdl_before_json"], row["wdl_after_json"], row["wdl_model"],
                  row["accuracy"], row["accuracy_source"], row["accuracy_formula_id"],
                  row["severity"], row["severity_policy_version"], row["legacy_label"])
                 for row in metrics])
            self._insert_event_rows(db, events, run_id, game_id, line_revision)
        self.invalidate_statistics_cache()
        return run_id

    @staticmethod
    def _insert_event_rows(db: sqlite3.Connection, events: list[dict], run_id: str,
                           game_id: str, line_revision: int, *, ignore: bool = False) -> None:
        insert = "INSERT OR IGNORE" if ignore else "INSERT"
        db.executemany(f"""{insert} INTO analysis_events (
            event_key,run_id,game_id,line_revision,ply,event_type,confidence_state,
            source,detector_version,evidence_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(row["event_key"], run_id, game_id, line_revision, row["ply"],
              row["event_type"], row["confidence_state"], row["source"],
              row["detector_version"], row["evidence_json"]) for row in events])
        db.executemany("""INSERT OR IGNORE INTO event_tags (
            event_key,tag,detector_version,confidence_state,evidence_json
        ) VALUES (?,?,?,?,?)""",
            [(row["event_key"], tag, row["detector_version"], row["confidence_state"],
              row["evidence_json"]) for row in events for tag in row.get("tags", [])])

    @classmethod
    def _insert_derived_metrics_for_run(cls, db: sqlite3.Connection, run_id: str,
                                        *, user_color: str | None = None,
                                        replace: bool = False) -> int:
        """Materialize canonical v2 move facts from one persisted source run, without UCI."""
        from .move_metrics import build_move_facts
        from .event_detectors import detect_registered_events

        run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            return 0
        assessments = [dict(row) for row in db.execute(
            "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run_id,))]
        for item in assessments:
            item["pv"] = str(item.get("pv_uci") or "").split()
        moves = [dict(row) for row in db.execute(
            "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
            (run["game_id"], run["line_revision"]))]
        revision = db.execute("SELECT initial_fen FROM game_revisions WHERE game_id=? "
                              "AND line_revision=?", (run["game_id"], run["line_revision"])).fetchone()
        classifications = [dict(row) for row in db.execute(
            "SELECT * FROM move_classifications WHERE run_id=? ORDER BY ply", (run_id,))]
        metrics, events = build_move_facts(
            assessments, moves, source=run["source"], run_id=run_id,
            classifications=classifications,
            initial_fen=revision["initial_fen"] if revision else "startpos")
        for event in events:
            event["tags"] = ["opportunity"]
        game = db.execute("SELECT color FROM games WHERE game_id=?", (run["game_id"],)).fetchone()
        events.extend(detect_registered_events(
            assessments, moves, metrics, run_id=run_id, source=run["source"],
            user_color=user_color or (game["color"] if game else None)))
        if replace:
            from .move_metrics import METRIC_VERSION
            db.execute("DELETE FROM move_metrics WHERE run_id=? AND metric_version=?",
                       (run_id, METRIC_VERSION))
            db.execute("DELETE FROM analysis_events WHERE run_id=? AND "
                       "(detector_version='cc-opportunity-candidate-v1' "
                       "OR detector_version LIKE 'cc-base-events-v1:%')", (run_id,))
        db.executemany("""INSERT OR IGNORE INTO move_metrics (
            run_id,game_id,line_revision,ply,metric_version,source,
            white_cp_before,mate_white_before,white_cp_after,mate_white_after,
            cp_loss,mate_discontinuity,wdl_before_json,wdl_after_json,wdl_model,
            accuracy,accuracy_source,accuracy_formula_id,severity,
            severity_policy_version,legacy_label
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, run["game_id"], run["line_revision"], row["ply"],
              row["metric_version"], row["source"], row["white_cp_before"],
              row["mate_white_before"], row["white_cp_after"], row["mate_white_after"],
              row["cp_loss"], row["mate_discontinuity"], row["wdl_before_json"],
              row["wdl_after_json"], row["wdl_model"], row["accuracy"],
              row["accuracy_source"], row["accuracy_formula_id"], row["severity"],
              row["severity_policy_version"], row["legacy_label"]) for row in metrics])
        cls._insert_event_rows(db, events, run_id, run["game_id"], run["line_revision"], ignore=True)
        return len(metrics)

    @staticmethod
    def _load_analysis_events(db: sqlite3.Connection, run_id: str) -> list[dict]:
        events = [dict(row) for row in db.execute(
            "SELECT * FROM analysis_events WHERE run_id=? ORDER BY ply,event_type", (run_id,))]
        for event in events:
            event["tags"] = [row[0] for row in db.execute(
                "SELECT tag FROM event_tags WHERE event_key=? ORDER BY tag",
                (event["event_key"],))]
        return events

    @staticmethod
    def _load_move_metrics(db: sqlite3.Connection, run_id: str) -> list[dict]:
        rows = db.execute(
            "SELECT mm.*,gm.mover,CASE WHEN pb.middle_start_ply IS NOT NULL "
            "AND mm.ply<pb.middle_start_ply THEN 'opening' WHEN pb.end_start_ply IS NOT NULL "
            "AND mm.ply>=pb.end_start_ply THEN 'endgame' WHEN pb.middle_start_ply IS NOT NULL "
            "AND mm.ply>=pb.middle_start_ply THEN 'middlegame' ELSE 'unknown' END phase "
            "FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
            "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
            "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id "
            "AND pb.line_revision=mm.line_revision WHERE mm.run_id=? ORDER BY mm.ply",
            (run_id,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _load_review_coverage(db: sqlite3.Connection, game_id: str,
                              line_revision: int, line_hash: str) -> dict | None:
        row = db.execute(
            "SELECT ar.run_id,aj.parameters_json,aj.status FROM analysis_jobs aj "
            "JOIN analysis_runs ar ON ar.run_id=aj.result_run_id WHERE aj.game_id=? "
            "AND aj.line_revision=? AND aj.line_hash=? AND aj.analysis_pass='review' "
            "AND aj.status='complete' ORDER BY aj.updated_at DESC,aj.job_id DESC LIMIT 1",
            (game_id, line_revision, line_hash)).fetchone()
        if not row:
            return None
        params = json.loads(row["parameters_json"])
        reviewed = db.execute(
            "SELECT COUNT(*) FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
            "AND cp_loss IS NOT NULL", (row["run_id"],)).fetchone()[0]
        total_moves = db.execute(
            "SELECT COUNT(*) FROM game_moves WHERE game_id=? AND line_revision=?",
            (game_id, line_revision)).fetchone()[0]
        return {"status": row["status"], "reviewed_moves": int(reviewed),
                "selected_moves": len(params.get("review_move_plies", [])),
                "total_moves": int(total_moves), "source": "stockfish-review"}

    def find_analysis_cache(self, game_id: str, line_revision: int, line_hash: str,
                            source: str, engine_version: str | None,
                            parameters: dict, *, account_id: str | None = None,
                            user_color: str | None = None) -> dict | None:
        params_json = json.dumps(parameters, sort_keys=True)
        usable_statuses = ("('complete','partial')" if source == "lichess-pgn" or
                           parameters.get("analysis_pass") == "review" else "('complete')")
        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE game_id=? AND line_revision=? "
                             "AND line_hash=? AND source=? AND engine_version IS ? "
                             f"AND parameters_json=? AND status IN {usable_statuses} "
                             "ORDER BY finished_at DESC LIMIT 1",
                             (game_id, line_revision, line_hash, source, engine_version,
                              params_json)).fetchone()
            if not run:
                return None
            result = dict(run)
            result["assessments"] = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["classifications"] = [dict(row) for row in db.execute(
                "SELECT * FROM move_classifications WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["move_metrics"] = self._load_move_metrics(db, run["run_id"])
            result["alternatives"] = [dict(row) for row in db.execute(
                "SELECT * FROM analysis_alternatives WHERE run_id=? ORDER BY ply,multipv_rank",
                (run["run_id"],))]
            result["events"] = self._load_analysis_events(db, run["run_id"])
            coverage = db.execute("SELECT * FROM analysis_coverage WHERE run_id=?",
                                  (run["run_id"],)).fetchone()
            result["coverage"] = dict(coverage) if coverage else None
            result["review_coverage"] = self._load_review_coverage(
                db, game_id, int(run["line_revision"]), str(run["line_hash"]))
            self._attach_official_accuracy(db, result, game_id, account_id=account_id,
                                           user_color=user_color)
            result["local_game_accuracy"] = None
            result["local_game_accuracy_formula_id"] = None
            return result

    def list_analyses(self, game_id: str, line_revision: int | None = None) -> list[dict]:
        sql = "SELECT * FROM analysis_runs WHERE game_id=?"
        params: list[object] = [game_id]
        if line_revision is not None:
            sql += " AND line_revision=?"
            params.append(line_revision)
        sql += " ORDER BY finished_at DESC"
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def latest_analysis_detail(self, game_id: str, line_revision: int, *,
                               account_id: str | None = None,
                               user_color: str | None = None) -> dict | None:
        """Load one current analysis and its plotted points/classifications for a detail view."""
        with self._connection() as db:
            materialize = db.execute(
                "SELECT ar.run_id FROM analysis_runs ar JOIN game_revisions gr "
                "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                "WHERE ar.game_id=? AND ar.line_revision=? AND gr.is_current=1 "
                "AND ar.status IN ('complete','partial') ORDER BY "
                "CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,ar.finished_at DESC,"
                "ar.run_id DESC LIMIT 1", (game_id, line_revision)).fetchone()
            if materialize:
                has_metrics = db.execute("SELECT 1 FROM move_metrics WHERE run_id=? LIMIT 1",
                                         (materialize["run_id"],)).fetchone()
            else:
                has_metrics = True
        if materialize and not has_metrics:
            # Legacy reports are hydrated from saved assessments only; this never opens UCI.
            self.materialize_analysis_metrics(materialize["run_id"])
        with self._connection() as db:
            run = db.execute(
                "SELECT ar.* FROM analysis_runs ar JOIN game_revisions gr "
                "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                "WHERE ar.game_id=? AND ar.line_revision=? AND gr.is_current=1 "
                "AND ar.status IN ('complete','partial') "
                "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,"
                "ar.finished_at DESC,ar.run_id DESC LIMIT 1",
                (game_id, line_revision)).fetchone()
            if not run:
                return None
            result = dict(run)
            result["assessments"] = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run["run_id"],))]
            result["classifications"] = [dict(row) for row in db.execute(
                "SELECT * FROM move_classifications WHERE run_id=? AND ply>0 ORDER BY ply",
                (run["run_id"],))]
            result["move_metrics"] = self._load_move_metrics(db, run["run_id"])
            result["alternatives"] = [dict(row) for row in db.execute(
                "SELECT * FROM analysis_alternatives WHERE run_id=? ORDER BY ply,multipv_rank",
                (run["run_id"],))]
            result["events"] = self._load_analysis_events(db, run["run_id"])
            coverage = db.execute("SELECT * FROM analysis_coverage WHERE run_id=?",
                                  (run["run_id"],)).fetchone()
            result["coverage"] = dict(coverage) if coverage else None
            result["review_coverage"] = self._load_review_coverage(
                db, game_id, int(run["line_revision"]), str(run["line_hash"]))
            self._attach_official_accuracy(db, result, game_id, account_id=account_id,
                                           user_color=user_color)
            result["local_game_accuracy"] = None
            result["local_game_accuracy_formula_id"] = None
            return result

    @staticmethod
    def _attach_official_accuracy(db: sqlite3.Connection, result: dict, game_id: str, *,
                                  account_id: str | None, user_color: str | None) -> None:
        game = db.execute("SELECT source,source_game_id,color,lichess_accuracy_white,"
                          "lichess_accuracy_black FROM games WHERE game_id=?", (game_id,)).fetchone()
        linked_color = None
        if account_id:
            link = db.execute("SELECT color FROM account_games WHERE account_id=? AND game_id=?",
                              (account_id, game_id)).fetchone()
            linked_color = link["color"] if link else None
        color = (linked_color if account_id else
                 user_color if user_color in {"white", "black"} else
                 game["color"] if game else None)
        is_lichess = bool(game and (game["source"] in {"lichess", "lichess_archive"}
                                    or game["source_game_id"]
                                    or db.execute("SELECT 1 FROM account_games WHERE game_id=? LIMIT 1",
                                                  (game_id,)).fetchone()))
        value = None
        if is_lichess and (not account_id or linked_color):
            column = "lichess_accuracy_white" if color == "white" else (
                "lichess_accuracy_black" if color == "black" else None)
            if column:
                value = game[column]
        result["official_game_accuracy"] = value
        result["official_game_accuracy_source"] = "lichess" if is_lichess else None
        result["official_game_accuracy_color"] = color
        result["official_game_accuracy_n"] = int(value is not None)

    def materialize_analysis_metrics(self, run_id: str) -> int:
        """Atomically replace v2 facts from persisted assessments; no engine is started."""
        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return 0
            db.execute("BEGIN IMMEDIATE")
            count = self._insert_derived_metrics_for_run(db, run_id, replace=True)
        self.invalidate_statistics_cache()
        return count

    def reclassify_analysis_events(self, run_id: str) -> int:
        """Run registered pure detectors over one saved run, with no Stockfish access."""
        from .event_detectors import detect_registered_events

        with self._connection() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return 0
            assessments = [dict(row) for row in db.execute(
                "SELECT * FROM position_assessments WHERE run_id=? ORDER BY ply", (run_id,))]
            for row in assessments:
                row["pv"] = row.get("pv_uci", "").split()
            moves = [dict(row) for row in db.execute(
                "SELECT ply,mover,uci FROM game_moves WHERE game_id=? AND line_revision=? ORDER BY ply",
                (run["game_id"], run["line_revision"]))]
            metrics = [dict(row) for row in db.execute(
                "SELECT * FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
                "ORDER BY ply", (run_id,))]
            game = db.execute("SELECT color FROM games WHERE game_id=?",
                              (run["game_id"],)).fetchone()
            before = int(db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                                    (run_id,)).fetchone()[0])
        events = detect_registered_events(
            assessments, moves, metrics, run_id=run_id, source=run["source"],
            user_color=game["color"] if game else None)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_event_rows(db, events, run_id, run["game_id"],
                                    run["line_revision"], ignore=True)
            after = int(db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                                   (run_id,)).fetchone()[0])
        return max(0, after - before)

    def plan_review_positions(self, run_id: str, *, budget_per_game: int = 12,
                              budget_total: int = 100) -> list[dict]:
        """Return a capped review plan from stored metrics; this method never starts UCI."""
        from .event_detectors import select_review_positions

        with self._connection() as db:
            metrics = [dict(row) for row in db.execute(
                "SELECT * FROM move_metrics WHERE run_id=? AND metric_version='cc-move-metrics-v2' "
                "ORDER BY ply", (run_id,))]
            events = self._load_analysis_events(db, run_id)
        return select_review_positions(metrics, events, budget_per_game=budget_per_game,
                                       budget_total=budget_total)

    def phase_for_ply(self, game_id: str, line_revision: int, ply: int) -> str:
        from .phases import phase_at_ply

        with self._connection() as db:
            row = db.execute("SELECT middle_start_ply,end_start_ply FROM game_phase_boundaries "
                             "WHERE game_id=? AND line_revision=?", (game_id,line_revision)).fetchone()
        return phase_at_ply(row["middle_start_ply"], row["end_start_ply"], int(ply)) if row else "unknown"

    def phase_boundaries(self, game_id: str, line_revision: int) -> dict | None:
        """Return phase boundaries together with their source and method version."""
        with self._connection() as db:
            row = db.execute("SELECT * FROM game_phase_boundaries WHERE game_id=? AND line_revision=?",
                             (game_id, line_revision)).fetchone()
        return dict(row) if row else None

    def infer_missing_phase_boundaries(self, limit: int = 25) -> int:
        """Explicit bounded phase-fallback backfill for current standard lines only."""
        from .phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries

        limit = max(1, min(int(limit), 100))
        with self._connection() as db:
            ids = [str(row[0]) for row in db.execute(
                "SELECT g.game_id FROM games g LEFT JOIN game_phase_boundaries p "
                "ON p.game_id=g.game_id AND p.line_revision=g.line_revision "
                "WHERE g.variant='standard' AND p.game_id IS NULL "
                "AND g.status NOT IN ('created','started','started_soon','playing') "
                "AND g.import_error IS NULL ORDER BY COALESCE(g.started_at,g.updated_at) DESC,"
                "g.game_id LIMIT ?", (limit,))]
        saved = 0
        for game_id in ids:
            line = self.current_line(game_id)
            if not line:
                continue
            try:
                board = chess.Board() if line["initial_fen"] == "startpos" else chess.Board(line["initial_fen"])
                if not board.is_valid():
                    continue
                boards = [board.copy(stack=False)]
                for raw in str(line["moves"]).split():
                    move = chess.Move.from_uci(raw)
                    if move not in board.legal_moves:
                        boards = []
                        break
                    board.push(move)
                    boards.append(board.copy(stack=False))
            except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
                continue
            if not boards:
                continue
            middle, end = infer_phase_boundaries(boards)
            if middle is None and end is None:
                continue
            with self._connection() as db:
                cur = db.execute("INSERT OR IGNORE INTO game_phase_boundaries VALUES (?,?,?,?,?,?)",
                    (game_id, int(line["line_revision"]), middle, end,
                     "material-fallback", PHASE_FALLBACK_VERSION))
                saved += int(cur.rowcount == 1)
                if cur.rowcount == 1:
                    self._apply_phase_labels(db, game_id, int(line["line_revision"]), middle, end)
        return saved

    @staticmethod
    def _apply_phase_labels(db: sqlite3.Connection, game_id: str, line_revision: int,
                            middle: int | None, end: int | None) -> None:
        from .phases import phase_at_ply

        rows = db.execute("SELECT mc.run_id,mc.ply FROM move_classifications mc "
                          "JOIN analysis_runs ar ON ar.run_id=mc.run_id "
                          "WHERE mc.game_id=? AND mc.line_revision=?",
                          (game_id, line_revision)).fetchall()
        db.executemany("UPDATE move_classifications SET phase=? WHERE run_id=? AND ply=?",
                       [(phase_at_ply(middle, end, int(row["ply"])), row["run_id"], row["ply"])
                        for row in rows])

    def save_opening_match(self, game_id: str, line_revision: int, match: dict | None) -> None:
        """Store a versioned opening reference without replacing Lichess metadata."""
        self._wait_before_read(game_id)
        with self._connection() as db:
            if match is None:
                db.execute("DELETE FROM opening_matches WHERE game_id=? AND line_revision=?",
                           (game_id, line_revision))
                return
            game = db.execute("SELECT opening_eco,opening_name FROM games WHERE game_id=?",
                              (game_id,)).fetchone()
            if not game:
                raise KeyError(game_id)
            conflict = bool((game["opening_eco"] and game["opening_eco"] != match["eco"]) or
                            (game["opening_name"] and game["opening_name"] != match["name"]))
            db.execute("INSERT INTO opening_matches VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(game_id,line_revision) DO UPDATE SET "
                       "dataset_revision=excluded.dataset_revision,algorithm_version=excluded.algorithm_version,"
                       "eco=excluded.eco,name=excluded.name,family=excluded.family,variation=excluded.variation,"
                       "matched_ply=excluded.matched_ply,lichess_conflict=excluded.lichess_conflict,"
                       "matched_at=excluded.matched_at",
                       (game_id, line_revision, match["dataset_revision"], match["algorithm_version"],
                        match["eco"], match["name"], match["family"], match.get("variation"),
                        match["matched_ply"], int(conflict), datetime.now(timezone.utc).isoformat()))
        self.invalidate_statistics_cache()

    def opening_match(self, game_id: str, line_revision: int | None = None) -> dict | None:
        with self._connection() as db:
            if line_revision is None:
                row = db.execute("SELECT om.* FROM opening_matches om JOIN games g "
                                 "ON g.game_id=om.game_id AND g.line_revision=om.line_revision "
                                 "WHERE om.game_id=?", (game_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM opening_matches WHERE game_id=? AND line_revision=?",
                                 (game_id, line_revision)).fetchone()
        return dict(row) if row else None

    def import_puzzle_activity(self, username: str, record: dict, *,
                               backfill: bool = False,
                               _return_inserted: bool = False) -> str | tuple[str, bool]:
        result = self.import_puzzle_activities(username, [record], backfill=backfill)[0]
        return result if _return_inserted else result[0]

    def import_puzzle_activities(self, username: str, records: list[dict], *,
                                 backfill: bool = False,
                                 pilot_run_id: str | None = None) -> list[tuple[str, bool]]:
        """Persist one API page of puzzle attempts in a single SQLite transaction."""
        account_id = self.ensure_archive_account(username)
        prepared: list[tuple[str, str, int, dict, str]] = []
        for record in records:
            puzzle = record.get("puzzle")
            if not isinstance(puzzle, dict):
                raise ValueError("Puzzle activity entry has no puzzle object")
            puzzle_id = str(puzzle.get("id") or "").strip()
            try:
                attempt_date = int(record["date"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Puzzle activity entry has no valid date") from exc
            if not puzzle_id or not str(puzzle.get("fen") or ""):
                raise ValueError("Puzzle activity entry requires puzzle id and FEN")
            stable = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            attempt_uid = puzzle_attempt_uid(account_id, puzzle_id, attempt_date,
                                             bool(record.get("win")))
            prepared.append((attempt_uid, puzzle_id, attempt_date, record, stable))

        results: list[tuple[str, bool]] = []
        with self._connection() as db:
            for attempt_uid, puzzle_id, attempt_date, record, stable in prepared:
                puzzle = record["puzzle"]
                now = datetime.now(timezone.utc).isoformat()
                solution = puzzle.get("solution") if isinstance(puzzle.get("solution"), list) else []
                themes = puzzle.get("themes") if isinstance(puzzle.get("themes"), list) else []
                db.execute("INSERT INTO puzzles VALUES (?,?,?,?,?,?,?) ON CONFLICT(puzzle_id) "
                           "DO UPDATE SET fen=excluded.fen,solution_json=excluded.solution_json, "
                           "themes_json=excluded.themes_json,puzzle_rating=excluded.puzzle_rating, "
                           "plays=excluded.plays,updated_at=excluded.updated_at",
                           (puzzle_id, puzzle["fen"], json.dumps(solution), json.dumps(themes),
                            puzzle.get("rating"), puzzle.get("plays"), now))
                inserted = db.execute("INSERT OR IGNORE INTO puzzle_attempts VALUES (?,?,?,?,?,?,?,?)",
                                      (attempt_uid, account_id, puzzle_id, attempt_date,
                                       int(bool(record.get("win"))), "lichess", stable, now)).rowcount
                if pilot_run_id is not None:
                    run = db.execute("SELECT account_id,status FROM puzzle_pilot_runs WHERE run_id=?",
                                     (pilot_run_id,)).fetchone()
                    if run is None or run["account_id"] != account_id or run["status"] != "running":
                        raise ValueError("Пробная загрузка задач не активна для этого аккаунта.")
                    # Associate even an already-known attempt with this pilot run, but do
                    # not touch the full-sync cursor or imported_count.
                    db.execute("INSERT OR IGNORE INTO puzzle_pilot_run_attempts VALUES (?,?)",
                               (pilot_run_id, attempt_uid))
                    db.execute("UPDATE puzzle_pilot_runs SET received_count=received_count+1, "
                               "new_count=new_count+?,finished_at=NULL WHERE run_id=?",
                               (int(bool(inserted)), pilot_run_id))
                else:
                    db.execute("INSERT OR IGNORE INTO puzzle_sync_state(account_id,updated_at) VALUES (?,?)",
                               (account_id, now))
                    if backfill:
                        db.execute("UPDATE puzzle_sync_state SET backfill_before=?, "
                                   "imported_count=imported_count+?,updated_at=? WHERE account_id=?",
                                   (attempt_date + 1, int(bool(inserted)), now, account_id))
                    else:
                        db.execute("UPDATE puzzle_sync_state SET imported_count=imported_count+?, "
                                   "last_error=NULL,updated_at=? WHERE account_id=?",
                                   (int(bool(inserted)), now, account_id))
                results.append((attempt_uid, bool(inserted)))
        return results

    def start_puzzle_pilot(self, account_id: str, requested_limit: int) -> str:
        if requested_limit not in (5, 10):
            raise ValueError("Пробная загрузка допускает только 5 или 10 попыток.")
        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO puzzle_pilot_runs(run_id,account_id,requested_limit,status,started_at) "
                       "VALUES (?,?,?,'running',?)", (run_id, account_id, requested_limit, now))
        return run_id

    def finish_puzzle_pilot(self, run_id: str, status: str, error: str | None = None) -> dict:
        if status not in {"complete", "cancelled", "error"}:
            raise ValueError("Недопустимый итог пробной загрузки задач.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE puzzle_pilot_runs SET status=?,finished_at=?,last_error=? WHERE run_id=?",
                       (status, now, error[:500] if error else None, run_id))
            row = db.execute("SELECT * FROM puzzle_pilot_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            return dict(row)

    def puzzle_pilot_history(self, account_id: str, *, run_id: str | None = None,
                             limit: int = 100) -> list[dict]:
        limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
        with self._connection() as db:
            query = ("SELECT r.run_id,r.status,r.requested_limit,r.received_count,"
                     "r.new_count,r.started_at,r.finished_at,r.last_error,"
                     "a.attempt_uid,a.puzzle_id,a.attempt_date_ms,a.win,"
                     "p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                     "FROM puzzle_pilot_runs r LEFT JOIN puzzle_pilot_run_attempts x USING(run_id) "
                     "LEFT JOIN puzzle_attempts a USING(attempt_uid) "
                     "LEFT JOIN puzzles p USING(puzzle_id) WHERE r.account_id=? ")
            params: list[object] = [account_id]
            if run_id is not None:
                query += "AND r.run_id=? "
                params.append(run_id)
            query += "ORDER BY r.started_at DESC,a.attempt_date_ms DESC,a.attempt_uid LIMIT ?"
            params.append(limit)
            rows = db.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def has_puzzle_attempt(self, username: str, record: dict) -> bool:
        puzzle = record.get("puzzle") or {}
        account_id = self.ensure_archive_account(username)
        attempt_uid = puzzle_attempt_uid(account_id, str(puzzle.get("id") or ""),
                                         int(record.get("date", 0)), bool(record.get("win")))
        with self._connection() as db:
            return bool(db.execute("SELECT 1 FROM puzzle_attempts WHERE attempt_uid=?",
                                   (attempt_uid,)).fetchone())

    def puzzle_sync_state(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM puzzle_sync_state WHERE account_id=?",
                             (account_id,)).fetchone()
            return dict(row) if row else {}

    def finish_puzzle_backfill(self, account_id: str) -> None:
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET backfill_complete=1,incremental_since=MAX("
                       "COALESCE(incremental_since,0),COALESCE((SELECT MAX(attempt_date_ms) FROM "
                       "puzzle_attempts WHERE account_id=?),0)),updated_at=? WHERE account_id=?",
                       (account_id, datetime.now(timezone.utc).isoformat(), account_id))

    def set_puzzle_incremental_progress(self, account_id: str, before_ms: int, target_ms: int) -> None:
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET incremental_before=?,incremental_target=?,updated_at=? "
                       "WHERE account_id=?", (int(before_ms), int(target_ms),
                       datetime.now(timezone.utc).isoformat(), account_id))

    def finish_puzzle_incremental(self, account_id: str, cursor_ms: int,
                                  last_sync_new_count: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("UPDATE puzzle_sync_state SET incremental_since=MAX(COALESCE(incremental_since,0),?),"
                       "incremental_before=NULL,incremental_target=NULL,last_sync_new_count=?,"
                       "last_success_at=?,last_error=NULL,updated_at=? WHERE account_id=?",
                       (int(cursor_ms), int(last_sync_new_count), now, now, account_id))

    def record_puzzle_error(self, account_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO puzzle_sync_state(account_id,updated_at) VALUES (?,?)",
                       (account_id, now))
            db.execute("UPDATE puzzle_sync_state SET last_error=?,updated_at=? WHERE account_id=?",
                       (error[:500], now, account_id))

    def puzzle_history(self, account_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        limit = min(MAX_PAGE_SIZE, max(1, int(limit)))
        offset = max(0, int(offset))
        with self._connection() as db:
            rows = db.execute("SELECT a.*,p.fen,p.solution_json,p.themes_json,p.puzzle_rating,p.plays "
                               "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                               "WHERE account_id=? ORDER BY attempt_date_ms DESC,attempt_uid "
                               "LIMIT ? OFFSET ?", (account_id, limit, offset)).fetchall()
            return [dict(row) for row in rows]

    def puzzle_statistics(self, account_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT COUNT(*) total,COALESCE(SUM(win),0) wins, "
                              "AVG(p.puzzle_rating) avg_puzzle_rating "
                              "FROM puzzle_attempts a JOIN puzzles p USING(puzzle_id) "
                              "WHERE account_id=?", (account_id,)).fetchone()
            total = int(row["total"])
            wins = int(row["wins"])
            themes = db.execute("SELECT p.themes_json,a.win FROM puzzle_attempts a "
                                "JOIN puzzles p USING(puzzle_id) WHERE account_id=?", (account_id,)).fetchall()
        counts: dict[str, list[int]] = {}
        for record in themes:
            try:
                values = json.loads(record["themes_json"])
            except (TypeError, json.JSONDecodeError):
                values = []
            for theme in values:
                pair = counts.setdefault(str(theme), [0, 0])
                pair[0] += 1
                pair[1] += int(bool(record["win"]))
        return {"attempts": total, "wins": wins, "failures": total - wins,
                "success_percent": round(100 * wins / total, 1) if total else None,
                "average_puzzle_rating": round(row["avg_puzzle_rating"], 1)
                    if row["avg_puzzle_rating"] is not None else None,
                "themes": {theme: {"attempts": values[0], "wins": values[1],
                                   "success_percent": round(100 * values[1] / values[0], 1)}
                           for theme, values in counts.items()}}

    def save_puzzle_dashboard(self, account_id: str, days: int, payload: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("INSERT INTO puzzle_dashboard_snapshots VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, account_id, int(days), now,
                        json.dumps(payload, sort_keys=True)))

    def get_game(self, game_id: str) -> dict | None:
        self._wait_before_read(game_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            return dict(row) if row else None

    def list_pending_final_sync(self) -> list[dict]:
        self._wait_before_read()
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM games WHERE final_sync_pending=1 ORDER BY updated_at"
            )]

    def export_pgn(self, game_id: str) -> str | None:
        game = self.get_game(game_id)
        return (game.get("raw_pgn") or game["pgn"]) if game else None


def _pgn_date(started_at: str | None) -> str:
    if not started_at:
        return "????.??.??"
    value = str(started_at)
    if len(value) == 10 and value[4] in {".", "-"}:
        return value.replace("-", ".")
    try:
        if value.isdigit():
            instant = datetime.fromtimestamp(int(value) / 1000, timezone.utc)
        else:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.strftime("%Y.%m.%d")
    except (ValueError, OverflowError, OSError):
        return "????.??.??"


def _time_precision(value: str | None) -> str:
    if not value:
        return "unknown"
    text = str(value).strip()
    if len(text) == 10:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            return "date_only"
        except ValueError:
            return "unknown"
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return "timestamp"
    except ValueError:
        return "unknown"


def _line_hash(initial_fen: str, moves: str) -> str:
    return hashlib.sha256(f"{initial_fen}\n{moves.strip()}".encode("utf-8")).hexdigest()


def _insert_position(db: sqlite3.Connection, game_id: str, revision: int,
                     ply: int, board: chess.Board) -> None:
    fen = board.fen()
    position_hash = hashlib.sha256(fen.encode("utf-8")).hexdigest()
    uid = f"{game_id}:r{revision}:p{ply}"
    db.execute("INSERT INTO game_positions "
               "(game_id,line_revision,ply,position_uid,fen,position_hash) "
               "VALUES (?,?,?,?,?,?)", (game_id, revision, ply, uid, fen, position_hash))


def _phase_ply(value: object) -> int | None:
    if isinstance(value, dict):
        value = value.get("begin")
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
