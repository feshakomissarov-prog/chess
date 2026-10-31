"""Small local SQLite store for confirmed Lichess games only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading
import uuid

import chess
import chess.pgn


SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class GameRepository:
    def __init__(self, path: str | Path, *, async_writes: bool = False) -> None:
        self.path = str(path)
        self.async_writes = async_writes
        self._writer = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="chess-coach-sqlite")
                        if async_writes else None)
        self._write_lock = threading.Lock()
        self._pending_writes: dict[str, Future] = {}
        self._write_errors: dict[str, str] = {}
        self._initialize()

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
                backup = path.with_name(path.name + ".pre-m3-v1.bak")
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
                "final_sync_pending": "INTEGER NOT NULL DEFAULT 0",
                "source": "TEXT NOT NULL DEFAULT 'lichess'",
                "source_game_id": "TEXT",
                "variant": "TEXT NOT NULL DEFAULT 'standard'",
                "line_revision": "INTEGER NOT NULL DEFAULT 1",
                "import_error": "TEXT",
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
                    PRIMARY KEY(account_id, game_id), UNIQUE(account_id, remote_id)
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
                CREATE TABLE IF NOT EXISTS game_notes (
                    note_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                    line_revision INTEGER, ply INTEGER, position_uid TEXT,
                    body TEXT NOT NULL, author TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(game_id) REFERENCES games(game_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC, game_id);
                CREATE INDEX IF NOT EXISTS idx_games_opponent ON games(opponent);
                CREATE INDEX IF NOT EXISTS idx_game_notes_position ON game_notes(position_uid);
            """
            for statement in schema_sql.split(";"):
                if statement.strip():
                    db.execute(statement)
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
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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
        future = self._writer.submit(
            self._save_snapshot_sync, game_id, initial_fen, moves, color, opponent,
            rated, clock, status, winner, white_player=white_player,
            black_player=black_player, started_at=started_at,
            final_sync_pending=final_sync_pending,
        )
        with self._write_lock:
            self._pending_writes[game_id] = future
        future.add_done_callback(lambda completed, key=game_id: self._record_write_result(key, completed))

    def _save_snapshot_sync(self, game_id: str, initial_fen: str, moves: str, color: str,
                            opponent: str, rated: bool, clock: str, status: str,
                            winner: str | None = None, *, white_player: str | None = None,
                            black_player: str | None = None, started_at: str | None = None,
                            final_sync_pending: bool = False) -> None:
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        for uci in moves.split():
            board.push_uci(uci)
        game = chess.pgn.Game.from_board(board)
        game.headers["Event"] = "Lichess rated" if rated else "Lichess casual"
        game.headers["Site"] = f"https://lichess.org/{game_id}"
        with self._connection() as db:
            previous = db.execute("SELECT white_player, black_player, started_at FROM games "
                                  "WHERE game_id=?", (game_id,)).fetchone()
        if previous:
            white_player = white_player or previous["white_player"]
            black_player = black_player or previous["black_player"]
            started_at = started_at or previous["started_at"]
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
                winner, updated_at, pgn, white_player, black_player, started_at,
                final_sync_pending, source, source_game_id, variant
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'lichess', ?, 'standard')
                ON CONFLICT(game_id) DO UPDATE SET initial_fen=excluded.initial_fen,
                moves=excluded.moves, color=excluded.color, opponent=excluded.opponent,
                rated=excluded.rated, clock=excluded.clock, status=excluded.status,
                winner=excluded.winner, updated_at=excluded.updated_at, pgn=excluded.pgn,
                white_player=excluded.white_player, black_player=excluded.black_player,
                started_at=excluded.started_at,
                final_sync_pending=excluded.final_sync_pending,
                source=excluded.source, source_game_id=excluded.source_game_id,
                variant=excluded.variant""",
                (game_id, initial_fen, moves, color, opponent, int(rated), clock, status,
                 winner, datetime.now(timezone.utc).isoformat(), pgn, white_player,
                 black_player, started_at, int(final_sync_pending), game_id))
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
        if self._writer:
            self._writer.shutdown(wait=True)

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

    def search_games(self, query: str = "", *, page: int = 0,
                     page_size: int = DEFAULT_PAGE_SIZE, completed_only: bool = False,
                     source: str | None = None, opponent: str | None = None) -> dict:
        """Search the local library without loading every PGN into a widget."""
        self._wait_before_read()
        page = max(0, int(page))
        page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        clauses: list[str] = []
        params: list[object] = []
        term = query.strip()
        if term:
            clauses.append("(game_id LIKE ? OR opponent LIKE ? OR white_player LIKE ? "
                           "OR black_player LIKE ? OR status LIKE ?)")
            params.extend([f"%{term}%"] * 5)
        if completed_only:
            clauses.append("status NOT IN ('created','started','started_soon','playing')")
        if source:
            clauses.append("source=?")
            params.append(source)
        if opponent:
            clauses.append("opponent LIKE ?")
            params.append(f"%{opponent}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as db:
            total = db.execute("SELECT COUNT(*) FROM games" + where, params).fetchone()[0]
            rows = db.execute("SELECT * FROM games" + where +
                              " ORDER BY updated_at DESC, game_id LIMIT ? OFFSET ?",
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
        return game["pgn"] if game else None


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
