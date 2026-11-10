"""Shared Lichess game-status classification for UI, service, and sessions."""

TERMINAL_GAME_STATUSES = frozenset({
    "aborted", "mate", "resign", "stalemate", "timeout", "draw", "outoftime",
    "cheat", "noStart", "unknownFinish", "variantEnd", "repetition", "50moves",
    "insufficientMaterial", "finished",
})

ACTIONABLE_GAME_STATUSES = frozenset({"started", "playing"})


def is_terminal_game_status(status: object) -> bool:
    return isinstance(status, str) and status in TERMINAL_GAME_STATUSES
