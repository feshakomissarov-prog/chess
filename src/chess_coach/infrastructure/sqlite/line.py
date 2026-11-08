"""Canonical PGN date, line identity and position index helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import sqlite3

import chess

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
