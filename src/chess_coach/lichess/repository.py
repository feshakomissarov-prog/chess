"""Small local SQLite store for confirmed Lichess games only."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.pgn


class GameRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, moves TEXT NOT NULL,
                color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER NOT NULL,
                clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT,
                updated_at TEXT NOT NULL, pgn TEXT NOT NULL,
                white_player TEXT NOT NULL DEFAULT '?', black_player TEXT NOT NULL DEFAULT '?',
                started_at TEXT, final_sync_pending INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
            if "white_player" not in columns:
                db.execute("ALTER TABLE games ADD COLUMN white_player TEXT NOT NULL DEFAULT '?'")
            if "black_player" not in columns:
                db.execute("ALTER TABLE games ADD COLUMN black_player TEXT NOT NULL DEFAULT '?'")
            if "started_at" not in columns:
                db.execute("ALTER TABLE games ADD COLUMN started_at TEXT")
            if "final_sync_pending" not in columns:
                db.execute(
                    "ALTER TABLE games ADD COLUMN final_sync_pending INTEGER NOT NULL DEFAULT 0"
                )

    def save_snapshot(self, game_id: str, initial_fen: str, moves: str, color: str,
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
        with self._connect() as db:
            previous = db.execute(
                "SELECT white_player, black_player, started_at FROM games WHERE game_id=?",
                (game_id,),
            ).fetchone()
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
        with self._connect() as db:
            db.execute("""INSERT INTO games (
                game_id, initial_fen, moves, color, opponent, rated, clock, status,
                winner, updated_at, pgn, white_player, black_player, started_at,
                final_sync_pending
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET initial_fen=excluded.initial_fen,
                moves=excluded.moves, color=excluded.color, opponent=excluded.opponent,
                rated=excluded.rated, clock=excluded.clock, status=excluded.status,
                winner=excluded.winner, updated_at=excluded.updated_at, pgn=excluded.pgn,
                white_player=excluded.white_player, black_player=excluded.black_player,
                started_at=excluded.started_at,
                final_sync_pending=excluded.final_sync_pending""",
                (game_id, initial_fen, moves, color, opponent, int(rated), clock, status,
                 winner, datetime.now(timezone.utc).isoformat(), pgn, white_player,
                 black_player, started_at, int(final_sync_pending)))

    def list_games(self, completed_only: bool = True) -> list[dict]:
        with self._connect() as db:
            query = "SELECT * FROM games"
            if completed_only:
                query += " WHERE status NOT IN ('created', 'started', 'started_soon', 'playing')"
            query += " ORDER BY updated_at DESC"
            return [dict(row) for row in db.execute(query)]

    def get_game(self, game_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            return dict(row) if row else None

    def list_pending_final_sync(self) -> list[dict]:
        with self._connect() as db:
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
