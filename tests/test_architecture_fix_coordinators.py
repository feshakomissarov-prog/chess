from pathlib import Path

from chess_coach.application.archive_sync import ArchiveSyncCoordinator
from chess_coach.application.dashboard import DashboardQueryCoordinator
from chess_coach.application.engine_analysis import EngineAnalysisCoordinator
from chess_coach.engine_manager import StockfishManager
from chess_coach.lichess.repository import GameRepository


def test_dashboard_query_coordinator_owns_existing_snapshot_query(tmp_path):
    repository = GameRepository(tmp_path / "games.sqlite3")
    try:
        result = DashboardQueryCoordinator(repository).snapshot(account_id=None, clock=None)
        assert result["recent_games"] == []
        assert result["metrics"][0]["value"] == "0"
        first = DashboardQueryCoordinator(repository)
        old, current = first.begin_request(), first.begin_request()
        assert not first.is_current(old) and first.is_current(current)
        first.defer((old, "old-account", None))
        first.defer((current, "current-account", "blitz"))
        assert first.take_pending() == (current, "current-account", "blitz")
    finally:
        repository.shutdown()


def test_archive_coordinator_routes_intents_to_existing_service(tmp_path):
    repository = GameRepository(tmp_path / "games.sqlite3")
    calls = []

    class Service:
        def sync_archive(self, **kwargs): calls.append(("full", kwargs))
        def pilot_import_archive(self, **kwargs): calls.append(("pilot", kwargs))
        def cancel_archive_sync(self): calls.append(("cancel", {}))
        def refresh_archive_game(self, game_id): calls.append(("refresh", game_id))

    service = Service()
    coordinator = ArchiveSyncCoordinator(repository)
    coordinator.start_full_sync(service, background=True)
    coordinator.start_pilot(service, max_games=5)
    coordinator.refresh_game(service, "synthetic-id")
    coordinator.cancel(service)
    assert calls == [
        ("full", {"background": True}), ("pilot", {"max_games": 5}),
        ("refresh", "synthetic-id"), ("cancel", {}),
    ]
    repository.shutdown()


def test_application_engine_coordinator_passes_one_shared_manager():
    manager = StockfishManager(engine_factory=lambda *_args, **_kwargs: object())
    coordinator = EngineAnalysisCoordinator(manager)
    assert coordinator.manager is manager
    assert coordinator.playing_snapshot_allows_analysis([]) is True
    assert coordinator.playing_snapshot_allows_analysis([{}]) is None
    manager.close_all()


def test_window_delegates_application_queries_archive_and_engine_use_cases():
    source = Path("src/chess_coach/window.py").read_text(encoding="utf-8")
    assert "dashboard_snapshot(self.game_repository" not in source
    assert "self.lichess.sync_archive(" not in source
    assert "self.lichess.pilot_import_archive(" not in source
    assert "self.lichess.refresh_archive_game(" not in source
    assert "CompletedGameAnalyzer(" not in source
    assert "BatchAnalysisRunner(" not in source
    assert "payload = evaluate_position(" not in source
