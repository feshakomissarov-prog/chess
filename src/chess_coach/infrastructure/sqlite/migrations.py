"""Versioned, backup-first schema initialization and migration policy."""

from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

import chess

from .constants import SCHEMA_VERSION
from .puzzles import puzzle_attempt_uid

class SQLiteMigrationMixin:

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
                CREATE TABLE IF NOT EXISTS lichess_eval_refresh_state (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL,
                    line_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                      ('checked_no_evals','partial','changed','api_failure')),
                    last_checked_at TEXT,
                    next_retry_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY(account_id,game_id,line_revision,line_hash)
                );
                CREATE TABLE IF NOT EXISTS lichess_analysis_backfill (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL,
                    line_hash TEXT NOT NULL,
                    accuracy_status TEXT NOT NULL DEFAULT 'queued'
                      CHECK(accuracy_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error','stale_line')),
                    evals_status TEXT NOT NULL DEFAULT 'queued'
                      CHECK(evals_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error','stale_line')),
                    accuracy_checked_at TEXT,
                    evals_checked_at TEXT,
                    accuracy_next_retry_at TEXT,
                    evals_next_retry_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,game_id)
                );
                CREATE INDEX IF NOT EXISTS idx_lichess_analysis_backfill_due
                  ON lichess_analysis_backfill(account_id,accuracy_next_retry_at,evals_next_retry_at,updated_at);
                CREATE TABLE IF NOT EXISTS lichess_ready_analysis_metadata (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'lichess-json',
                    schema_version TEXT NOT NULL,
                    white_analysis_json TEXT NOT NULL DEFAULT '{}',
                    black_analysis_json TEXT NOT NULL DEFAULT '{}',
                    move_annotations_json TEXT NOT NULL DEFAULT '[]',
                    validation_issues_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,game_id,line_revision),
                    FOREIGN KEY(game_id,line_revision)
                      REFERENCES game_revisions(game_id,line_revision) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS lichess_analysis_backfill_budget (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id) ON DELETE CASCADE,
                    utc_day TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0 CHECK(requests >= 0),
                    paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
                    retry_after_at TEXT,
                    updated_at TEXT NOT NULL
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
                CREATE INDEX IF NOT EXISTS idx_eval_refresh_status
                    ON lichess_eval_refresh_state(account_id,status,next_retry_at);
                CREATE INDEX IF NOT EXISTS idx_puzzle_attempt_puzzle ON puzzle_attempts(puzzle_id,attempt_date_ms);
                CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC, game_id);
                CREATE INDEX IF NOT EXISTS idx_games_started_recent
                    ON games(started_at DESC,game_id DESC);
                CREATE INDEX IF NOT EXISTS idx_games_opponent ON games(opponent);
                CREATE INDEX IF NOT EXISTS idx_game_notes_position ON game_notes(position_uid);
                CREATE INDEX IF NOT EXISTS idx_analysis_cache ON analysis_runs(game_id,line_revision,source,engine_version,status);
                CREATE INDEX IF NOT EXISTS idx_analysis_current_source ON analysis_runs(game_id,line_revision,status,source,finished_at DESC);
            """
            for statement in schema_sql.split(";"):
                if statement.strip():
                    db.execute(statement)
            queue_schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='lichess_analysis_backfill'"
            ).fetchone()
            if queue_schema and "'stale_line'" not in str(queue_schema[0]):
                # Expand the status CHECK constraint without losing the durable queue.
                db.execute("DROP INDEX IF EXISTS idx_lichess_analysis_backfill_due")
                db.execute("ALTER TABLE lichess_analysis_backfill RENAME TO lichess_analysis_backfill_v18")
                db.execute("""CREATE TABLE lichess_analysis_backfill (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
                    line_revision INTEGER NOT NULL, line_hash TEXT NOT NULL,
                    accuracy_status TEXT NOT NULL DEFAULT 'queued'
                      CHECK(accuracy_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error','stale_line')),
                    evals_status TEXT NOT NULL DEFAULT 'queued'
                      CHECK(evals_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error','stale_line')),
                    accuracy_checked_at TEXT, evals_checked_at TEXT,
                    accuracy_next_retry_at TEXT, evals_next_retry_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,game_id))""")
                db.execute("INSERT INTO lichess_analysis_backfill SELECT * FROM lichess_analysis_backfill_v18")
                db.execute("DROP TABLE lichess_analysis_backfill_v18")
                db.execute("CREATE INDEX idx_lichess_analysis_backfill_due ON "
                           "lichess_analysis_backfill(account_id,accuracy_next_retry_at,"
                           "evals_next_retry_at,updated_at)")
            budget_columns = {row[1] for row in db.execute(
                "PRAGMA table_info(lichess_analysis_backfill_budget)")}
            if "retry_after_at" not in budget_columns:
                db.execute("ALTER TABLE lichess_analysis_backfill_budget ADD COLUMN retry_after_at TEXT")
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
        # The coverage view is a schema object, not startup-time cache. Rebuild it
        # only while migrating an older schema; current v16 restarts are read-only
        # with respect to sqlite_master.
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        coverage_view_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='analysis_coverage'").fetchone()
        if schema_version < SCHEMA_VERSION:
            db.execute("DROP VIEW IF EXISTS analysis_coverage")
        if schema_version < SCHEMA_VERSION or not coverage_view_exists:
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
