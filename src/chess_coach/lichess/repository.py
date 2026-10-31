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
                updated_at TEXT NOT NULL, pgn TEXT NOT NULL
            )""")

    def save_snapshot(self, game_id: str, initial_fen: str, moves: str, color: str,
                      opponent: str, rated: bool, clock: str, status: str,
                      winner: str | None = None) -> None:
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        for uci in moves.split():
            board.push_uci(uci)
        game = chess.pgn.Game.from_board(board)
        game.headers["Event"] = "Lichess rated" if rated else "Lichess casual"
        game.headers["Site"] = f"https://lichess.org/{game_id}"
        game.headers["Date"] = datetime.now(timezone.utc).strftime("%Y.%m.%d")
        game.headers["White"] = "Chess Coach" if color == "white" else opponent
        game.headers["Black"] = opponent if color == "white" else "Chess Coach"
        game.headers["TimeControl"] = clock or "-"
        game.headers["Result"] = ("1-0" if winner == "white" else "0-1" if winner == "black" else
                                   "1/2-1/2" if status in {"draw", "stalemate"} else "*")
        pgn = str(game)
        with self._connect() as db:
            db.execute("""INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET initial_fen=excluded.initial_fen,
                moves=excluded.moves, color=excluded.color, opponent=excluded.opponent,
                rated=excluded.rated, clock=excluded.clock, status=excluded.status,
                winner=excluded.winner, updated_at=excluded.updated_at, pgn=excluded.pgn""",
                (game_id, initial_fen, moves, color, opponent, int(rated), clock, status,
                 winner, datetime.now(timezone.utc).isoformat(), pgn))

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

    def export_pgn(self, game_id: str) -> str | None:
        game = self.get_game(game_id)
        return game["pgn"] if game else None
