"""Small pure formatting helpers shared by extracted dialog views."""

from __future__ import annotations

import re
import chess
from .display import format_game_date


def _game_date_label(game: dict) -> str:
    return format_game_date(game.get("started_at"),
                            precision=game.get("game_time_precision"))


def _clock_filter_sort_key(value: object) -> tuple:
    """Sort raw controls by base time/increment, retaining raw values for SQL."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)", raw)
    if match:
        base, increment = map(int, match.groups())
        return (0, base, increment, raw.casefold())
    daily = re.fullmatch(r"(?:daily:)?(\d{4,6})", raw.casefold())
    if daily:
        return (1, int(daily.group(1)), 0, raw.casefold())
    return (2, 0, 0, raw.casefold())


def _result_label(color: chess.Color, winner: str | None, status: str) -> str:
    if winner not in {"white", "black"}:
        if status in {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}:
            return "Ничья"
        return "Партия прервана"
    won = (winner == "white") == color
    return "Победа" if won else "Поражение"
