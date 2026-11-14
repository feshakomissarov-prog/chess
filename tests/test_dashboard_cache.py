from __future__ import annotations

import json

from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.dashboard_cache import DashboardSnapshotCache
from chess_coach.application.dashboard import dashboard_preview_snapshot
from chess_coach.application.dashboard_cache import (
    _compact_review_metrics, _split_snapshot, _valid_game_shell,
)
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
    assert stale_a is not None and stale_a["_dashboard_stale"] is False
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


def test_dashboard_cache_round_trips_puzzle_attempt_activity_tuple(tmp_path):
    repository = GameRepository(tmp_path / "puzzle-rating-cache.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    snapshot = {
        "scope": "Все локальные партии · смешанная перспектива",
        "filter": {"account_id": None, "clock": None},
        "metrics": [],
        "recent_games": [],
        "puzzle_player_rating": {
            "latest": 1917,
            "date": "2026-11-10",
            "points": [{"day": "2026-11-10", "rating": 1917,
                        "attempts": (5, 3)}],
        },
        "activity_trend": [],
        "activity_trend_source": None,
    }

    assert cache.save(None, None, revision, snapshot)
    loaded = DashboardSnapshotCache(repository).load(None, None)

    assert loaded is not None
    assert loaded["puzzle_player_rating"]["points"] == [
        {"day": "2026-11-10", "rating": 1917, "attempts": [5, 3]}]
    repository.shutdown()


def test_dashboard_cache_preserves_gap_and_aggregated_mate_chart_metadata():
    graph_points = [
        {"ply": 300, "white_cp": 80, "mate_white": None,
         "connect_from_previous": False},
        {"ply": 301, "white_cp": 120, "mate_white": None,
         "connect_from_previous": True},
        {"ply": 369, "white_cp": None, "mate_white": 6,
         "connect_from_previous": False, "mate_start_ply": 350,
         "mate_sample_count": 20},
    ]
    compact = _compact_review_metrics({"graph_points": graph_points})

    assert compact["graph_points"] == graph_points
    assert _valid_game_shell({
        "game_id": "cached-long-game", "status": "mate",
        "review_metrics": compact,
    })
    assert not _valid_game_shell({
        "game_id": "bad-connect-flag", "status": "mate",
        "review_metrics": {"graph_points": [
            {"ply": 1, "white_cp": 0, "connect_from_previous": 1}],
        },
    })


def test_last_good_loader_skips_sqlite_and_recovers_structurally_corrupt_primary(tmp_path,
                                                                                monkeypatch):
    repository = GameRepository(tmp_path / "dashboard-fast.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    snapshot = dashboard_snapshot(repository, account_id=None, clock=None)
    assert cache.save(None, None, revision, snapshot)
    assert cache.save(None, None, revision, snapshot)

    document = json.loads(cache.path.read_text(encoding="utf-8"))
    scope = next(iter(document["scopes"].values()))
    scope["tier_b"]["metrics"] = "invalid-tier-shape"
    cache.path.write_text(json.dumps(document), encoding="utf-8")

    monkeypatch.setattr(repository, "dashboard_data_revision",
                        lambda: (_ for _ in ()).throw(AssertionError("SQLite was queried")))
    recovered = cache.load_last_good(None, None)
    assert recovered is not None
    assert recovered["_dashboard_tier_b_saved_at"]
    assert cache.load_last_good("lichess:other", None) is None
    repository.shutdown()


def test_tier_a_retains_last_move_time_and_precision():
    snapshot = {
        "scope": "Synthetic", "filter": {"account_id": "lichess:a", "clock": None},
        "metrics": [{"key": "games", "label": "Партии", "value": "1",
                     "detail": "SQLite", "source": "games"}],
        "recent_games": [{"game_id": "dated", "status": "mate",
                          "started_at": "2026-11-10", "last_move_at": 1794268800000,
                          "game_time_precision": "date_only"}],
    }
    tier_a, _tier_b = _split_snapshot(snapshot)
    assert tier_a["recent_games"][0]["last_move_at"] == 1794268800000
    assert tier_a["recent_games"][0]["game_time_precision"] == "date_only"


def test_last_good_cache_migrates_one_compatible_sqlite_schema_version(tmp_path):
    repository = GameRepository(tmp_path / "dashboard-cache-migration.sqlite3")
    cache = DashboardSnapshotCache(repository)
    revision = repository.dashboard_data_revision()
    snapshot = dashboard_snapshot(repository, account_id=None, clock=None)
    assert cache.save(None, None, revision, snapshot)
    document = json.loads(cache.path.read_text(encoding="utf-8"))
    document["schema_version"] -= 1
    key, entry = next(iter(document["scopes"].items()))
    scope = json.loads(key)
    scope["schema"] -= 1
    document["scopes"] = {json.dumps(scope, sort_keys=True, separators=(",", ":")): entry}
    cache.path.write_text(json.dumps(document), encoding="utf-8")

    migrated = cache.load_last_good(None, None)
    assert migrated is not None
    assert migrated["filter"] == snapshot["filter"]
    repository.shutdown()
