"""Bounded read use case for the completed-game library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class GameLibraryQueryCoordinator:
    """Build account-scoped library queries on the existing repository facade."""

    def __init__(self, repository, *, page_size: int = 50) -> None:
        self.repository = repository
        self.page_size = max(1, min(100, int(page_size)))

    def search(self, filters: dict, page: int = 0) -> dict:
        values = dict(filters or {})
        period_days = max(0, int(values.get("period_days") or 0))
        updated_after = None
        if period_days:
            updated_after = (datetime.now(timezone.utc)
                             - timedelta(days=period_days)).isoformat()
        return self.repository.search_games(
            values.get("query", ""), page=max(0, int(page)),
            page_size=self.page_size, completed_only=True,
            account_id=values.get("account_id"), source=values.get("source"),
            clock=values.get("clock"), color=values.get("color"),
            opponent=values.get("opponent"), opening=values.get("opening"),
            outcome=values.get("outcome"), analysis=values.get("analysis"),
            analysis_source=values.get("analysis_source"), updated_after=updated_after,
            order_by_game_time=True)
