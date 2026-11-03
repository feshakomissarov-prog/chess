"""UI formatting helpers for dashboard cards; SQL read models live in application."""

from __future__ import annotations

from typing import Any

from ..application.dashboard import dashboard_options, dashboard_snapshot, game_card_metrics
from .display import format_clock_control, format_game_date, russian_opening_name

def game_card_text(game: dict[str, Any]) -> tuple[str, str]:
    opponent = str(game.get("account_opponent") or game.get("opponent") or "Соперник неизвестен")
    color = game.get("account_color") or game.get("color")
    winner = game.get("winner")
    if game.get("status") in {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}:
        result = "Ничья"
    elif color not in {"white", "black"} or winner not in {"white", "black"}:
        result = "Результат н/д"
    else:
        result = "Победа" if winner == color else "Поражение"
    details = " · ".join((
        format_game_date(game.get("started_at"), precision=game.get("game_time_precision")),
        format_clock_control(game.get("clock")),
        russian_opening_name(game.get("eco"), game.get("opening_name")),
    ))
    return f"{result} с {opponent}", details
