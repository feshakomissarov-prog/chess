from __future__ import annotations

from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.dashboard_cache import DashboardSnapshotCache
from chess_coach.application.dashboard import dashboard_preview_snapshot
from chess_coach.lichess.repository import GameRepository


def test_dashboard_cache_keeps_scoped_last_good_tier_b_across_sqlite_revision(tmp_path):
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
    loaded = restarted.load(None, None)
    assert loaded is not None and loaded["_dashboard_stale"] is False
    assert {key: value for key, value in loaded.items() if not key.startswith("_dashboard_")} == all_accounts
    loaded_a = restarted.load("lichess:a", None)
    assert loaded_a is not None
    assert {key: value for key, value in loaded_a.items() if not key.startswith("_dashboard_")} == account_a
    loaded_b = restarted.load("lichess:b", None)
    assert loaded_b is not None
    assert {key: value for key, value in loaded_b.items() if not key.startswith("_dashboard_")} == account_b
    assert restarted.load("lichess:other", None) is None

    repository.save_snapshot("new-game", "startpos", "e2e4", "white", "Opponent",
                             False, "5+0", "mate", "white",
                             white_player="Owner", black_player="Opponent")
    assert repository.dashboard_data_revision() > revision
    stale = restarted.load(None, None)
    assert stale is not None and stale["_dashboard_stale"] is True
    assert stale["puzzle_player_rating"] == all_accounts["puzzle_player_rating"]
    current_revision = repository.dashboard_data_revision()
    preview = dashboard_preview_snapshot(repository)
    assert restarted.save_tier_a(None, None, current_revision, preview)
    tier_a_updated = restarted.load(None, None)
    assert tier_a_updated is not None and tier_a_updated["_dashboard_stale"] is True
    assert next(metric["value"] for metric in tier_a_updated["metrics"]
                if metric["key"] == "games") == "1"
    assert tier_a_updated["puzzle_player_rating"] == all_accounts["puzzle_player_rating"]
    stale_a = restarted.load("lichess:a", None)
    assert stale_a is not None and stale_a["_dashboard_stale"] is True
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
