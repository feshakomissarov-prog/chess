from __future__ import annotations

from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.dashboard_cache import DashboardSnapshotCache
from chess_coach.lichess.repository import GameRepository


def test_dashboard_cache_is_persistent_scoped_and_invalidated_by_sqlite_revision(tmp_path):
    repository = GameRepository(tmp_path / "dashboard.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    all_accounts = dashboard_snapshot(repository, account_id=None, clock=None)
    account_a = {**all_accounts, "filter": {"account_id": "lichess:a", "clock": None}}
    account_b = {**all_accounts, "filter": {"account_id": "lichess:b", "clock": None}}

    assert cache.save(None, None, revision, all_accounts)
    assert cache.save("lichess:a", None, revision, account_a)
    assert cache.save("lichess:b", None, revision, account_b)
    # A fresh cache object models the next process; account scopes cannot cross.
    restarted = DashboardSnapshotCache(repository)
    assert restarted.load(None, None) == all_accounts
    assert restarted.load("lichess:a", None) == account_a
    assert restarted.load("lichess:b", None) == account_b
    assert restarted.load("lichess:other", None) is None

    repository.save_snapshot("new-game", "startpos", "e2e4", "white", "Opponent",
                             False, "5+0", "mate", "white",
                             white_player="Owner", black_player="Opponent")
    assert repository.dashboard_data_revision() > revision
    assert restarted.load(None, None) is None
    assert restarted.load("lichess:a", None) is None
    repository.shutdown()


def test_dashboard_cache_refuses_stale_revision_and_oversized_or_corrupt_payload(tmp_path):
    repository = GameRepository(tmp_path / "dashboard-corrupt.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    snapshot = dashboard_snapshot(repository, account_id=None, clock=None)
    assert not cache.save(None, None, revision - 1, snapshot)
    assert cache.save(None, None, revision, snapshot)
    cache.path.write_text("{broken", encoding="utf-8")
    assert cache.load(None, None) is None
    repository.shutdown()
