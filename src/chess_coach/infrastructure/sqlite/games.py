"""Game records, notes and line read models backed by the shared unit of work."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
import json
import sqlite3
import uuid

import chess
import chess.pgn

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from .line import _insert_position, _line_hash, _pgn_date, _time_precision

class GamesStoreMixin:

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
                     source: str | None = None, opponent: str | None = None,
                     account_id: str | None = None, clock: str | None = None) -> dict:
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
        if clock:
            clauses.append("g.clock=?")
            params.append(clock)
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

    def get_game(self, game_id: str) -> dict | None:
        self._wait_before_read(game_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            metadata = db.execute(
                "SELECT source,schema_version,white_analysis_json,black_analysis_json,"
                "move_annotations_json,validation_issues_json FROM lichess_ready_analysis_metadata "
                "WHERE game_id=? AND line_revision=? ORDER BY updated_at DESC LIMIT 1",
                (game_id, result.get("line_revision"))).fetchone()
            if metadata:
                result["lichess_analysis_source"] = metadata["source"]
                result["lichess_analysis_schema_version"] = metadata["schema_version"]
                for key in ("white_analysis_json", "black_analysis_json",
                            "move_annotations_json", "validation_issues_json"):
                    try:
                        result[key.removesuffix("_json")] = json.loads(metadata[key])
                    except (TypeError, json.JSONDecodeError):
                        result[key.removesuffix("_json")] = {} if key.endswith("analysis_json") else []
            else:
                result["lichess_analysis_source"] = None
                result["lichess_analysis_schema_version"] = None
                result["white_analysis"] = result["black_analysis"] = {}
                result["move_annotations"] = result["validation_issues"] = []
            return result

    def list_pending_final_sync(self) -> list[dict]:
        self._wait_before_read()
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM games WHERE final_sync_pending=1 ORDER BY updated_at"
            )]

    def export_pgn(self, game_id: str) -> str | None:
        game = self.get_game(game_id)
        return (game.get("raw_pgn") or game["pgn"]) if game else None

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
