"""Application use-case boundary for archive import and refresh commands."""

from __future__ import annotations


class ArchiveSyncCoordinator:
    """Route user intents to the existing service/repository owners."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def start_full_sync(self, service, *, background: bool = False) -> None:
        service.sync_archive(background=background)

    def start_pilot(self, service, *, max_games: int) -> None:
        service.pilot_import_archive(max_games=max_games)

    def cancel(self, service) -> None:
        service.cancel_archive_sync()

    def refresh_game(self, service, game_id: str) -> None:
        service.refresh_archive_game(game_id)

    def account_state(self, account_id: str) -> dict:
        return self.repository.archive_sync_state(account_id)

    def recent_pilot_runs(self, account_id: str, *, limit: int = 1) -> list[dict]:
        return self.repository.list_archive_pilot_runs(account_id, limit=limit)
