"""Single fail-closed policy for deciding whether local engine analysis is allowed."""

from __future__ import annotations

from collections.abc import Callable

from ..lichess.session import classify_lichess_opponent


def playing_snapshot_allows_analysis(games: object, *, active_human_session: bool = False) -> bool | None:
    """Return True only for a complete, valid snapshot with no human opponent."""
    if active_human_session or not isinstance(games, list) or len(games) >= 50:
        return None
    if any(not isinstance(game, dict) or not game.get("gameId") for game in games):
        return None
    return not any(classify_lichess_opponent(game) != "official_ai" for game in games)


def refresh_playing_snapshot(service, token: str | None, username: str,
                             is_current: Callable[[], bool], *,
                             active_human_session: bool = False) -> bool:
    """Fetch and validate a fresh server snapshot after engine admission.

    Any exception, incomplete response, changed credential/session, or uncertain
    opponent identity fails closed. This helper never logs the token or payload.
    """
    if not token or not username or not is_current():
        return False
    if str(getattr(service, "token", "") or "") != token:
        return False
    if str(getattr(service, "username", "") or "").strip().casefold() != username.casefold():
        return False
    try:
        games = service.client.get_playing_games(token)
    except Exception:
        return False
    if not is_current() or str(getattr(service, "token", "") or "") != token:
        return False
    if str(getattr(service, "username", "") or "").strip().casefold() != username.casefold():
        return False
    return playing_snapshot_allows_analysis(
        games, active_human_session=active_human_session) is True
