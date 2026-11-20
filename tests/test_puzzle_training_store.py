import json
import sqlite3
import chess
import pytest

from chess_coach.lichess.repository import GameRepository


def test_local_attempts_are_additive_and_account_scoped(tmp_path):
    repository = GameRepository(tmp_path / "puzzles.sqlite3")
    account_a = repository.ensure_archive_account("local-a")
    account_b = repository.ensure_archive_account("local-b")
    payload = json.dumps({"puzzle": {"id": "p1", "solution": ["e2e4"],
                                     "themes": ["fork"]}})
    repository.cache_training_puzzle(account_a, "p1", payload)
    repository.save_local_puzzle_attempt(account_a, "p1", result="mistake", solved=False, mistakes=1,
                                         hints=0, duration_ms=1000, puzzle_json=payload)
    assert repository.local_puzzle_attempt_count(account_a) == 1
    assert repository.local_puzzle_attempt_count(account_b) == 0
    assert repository.local_puzzle_attempts(account_b) == []
    assert repository.cached_training_puzzles(account_a)[0]["puzzle_id"] == "p1"
    assert repository.local_puzzle_statistics(account_a, "fork") == {
        "attempts": 1, "solved": 0, "success_percent": 0.0}
    assert repository.puzzle_history(account_a) == []
    repository.shutdown()


def test_cached_training_theme_filter_applies_before_bounded_page(tmp_path):
    repository = GameRepository(tmp_path / "theme-page.sqlite3")
    account = repository.ensure_archive_account("theme-user")
    try:
        for index in range(150):
            theme = "fork" if index == 0 else "pin"
            puzzle_id = f"cache-{index:03d}"
            payload = json.dumps({"puzzle": {"id": puzzle_id, "themes": [theme]}})
            repository.cache_training_puzzle(account, puzzle_id, payload)
            with repository._connection() as db:
                db.execute("UPDATE local_puzzle_cache SET cached_at_ms=? WHERE account_id=? AND puzzle_id=?",
                           (index, account, puzzle_id))
        rows = repository.cached_training_puzzles(account, limit=5, theme="fork")
        assert [row["puzzle_id"] for row in rows] == ["cache-000"]
        assert repository.cached_training_puzzles("other-account", limit=5, theme="fork") == []
    finally:
        repository.shutdown()


def test_exact_puzzle_lookup_finds_cache_and_imported_attempt_outside_recent_window(tmp_path):
    repository = GameRepository(tmp_path / "old-puzzle.sqlite3")
    account = repository.ensure_archive_account("old-history")
    try:
        for index in range(1501):
            puzzle_id = f"old-{index:04d}"
            payload = json.dumps({"game": {"pgn": "1. e4 e5 2. Nf3"},
                                  "puzzle": {"id": puzzle_id, "initialPly": 2,
                                             "solution": ["d7d5"], "themes": ["fork"]}})
            repository.cache_training_puzzle(account, puzzle_id, payload)
        target = repository.get_cached_training_puzzle(account, "old-0100")
        assert target["puzzle_id"] == "old-0100"
        assert repository.get_cached_training_puzzle("another-account", "old-0100") is None

        records = [{"date": 1_700_000_000_000 + index, "win": bool(index % 2),
                    "puzzle": {"id": f"imported-{index:04d}", "fen": chess.STARTING_FEN,
                               "solution": ["e2e4"], "themes": ["fork"],
                               "rating": 1500, "plays": 10}}
                   for index in range(1501)]
        repository.import_puzzle_activities("old-history", records)
        imported = repository.get_puzzle_attempt_by_id(account, "imported-1500")
        assert imported["puzzle_id"] == "imported-1500"
        assert repository.get_puzzle_attempt_by_id("another-account", "imported-1500") is None
    finally:
        repository.shutdown()


def test_exact_imported_attempt_lookup_uses_account_and_id_without_recent_limit(tmp_path):
    repository = GameRepository(tmp_path / "old-imported-puzzle.sqlite3")
    account = repository.ensure_archive_account("old-imported-user")
    try:
        records = [{"date": 1_700_000_000_000 + index, "win": bool(index % 2),
                    "puzzle": {"id": f"activity-{index:04d}", "fen": chess.STARTING_FEN,
                               "solution": ["e2e4"], "themes": ["endgame" if index == 0 else "fork"],
                               "rating": 1500, "plays": 10}}
                   for index in range(1501)]
        repository.import_puzzle_activities("old-imported-user", records)
        target = repository.get_puzzle_attempt_by_id(account, "activity-0000")
        assert target["puzzle_id"] == "activity-0000"
        assert repository.get_puzzle_attempt_by_id("other-account", "activity-0000") is None
    finally:
        repository.shutdown()


def test_stable_local_attempt_id_makes_retry_idempotent(tmp_path):
    repository = GameRepository(tmp_path / "idempotent.sqlite3")
    account = repository.ensure_archive_account("retry-user")
    payload = json.dumps({"puzzle": {"id": "retry-p1", "themes": ["pin"]}})
    try:
        first = repository.save_local_puzzle_attempt(
            account, "retry-p1", attempt_id="stable-session-attempt",
            result="success", solved=True, mistakes=0, hints=0, duration_ms=300,
            puzzle_json=payload)
        second = repository.save_local_puzzle_attempt(
            account, "retry-p1", attempt_id="stable-session-attempt",
            result="success", solved=True, mistakes=0, hints=0, duration_ms=300,
            puzzle_json=payload)
        assert first == ("stable-session-attempt", True)
        assert second == ("stable-session-attempt", False)
        assert repository.local_puzzle_attempt_count(account) == 1
    finally:
        repository.shutdown()


@pytest.mark.parametrize("puzzle_json", ["{broken", "[]", "null"])
def test_malformed_legacy_payload_saves_attempt_without_rating(tmp_path, puzzle_json):
    repository = GameRepository(tmp_path / "malformed-puzzle-payload.sqlite3")
    account = repository.ensure_archive_account("legacy-puzzle-json")
    try:
        attempt_id, inserted = repository.save_local_puzzle_attempt(
            account, "legacy-puzzle", result="success", solved=True, mistakes=0,
            hints=0, duration_ms=100, puzzle_json=puzzle_json,
            attempt_id="legacy-attempt", rating_eligible=True)
        assert (attempt_id, inserted) == ("legacy-attempt", True)
        assert repository.local_puzzle_attempt_count(account) == 1
        assert repository.local_puzzle_rating(account)["rated_attempts"] == 0
    finally:
        repository.shutdown()


def test_valid_legacy_payload_still_contributes_rating(tmp_path):
    repository = GameRepository(tmp_path / "valid-puzzle-payload.sqlite3")
    account = repository.ensure_archive_account("valid-puzzle-json")
    try:
        payload = json.dumps({"puzzle": {"rating": 1600, "themes": ["fork"]}})
        repository.save_local_puzzle_attempt(
            account, "valid-puzzle", result="success", solved=True, mistakes=0,
            hints=0, duration_ms=100, puzzle_json=payload,
            attempt_id="valid-attempt", rating_eligible=True)
        assert repository.local_puzzle_rating(account)["rated_attempts"] == 1
    finally:
        repository.shutdown()


def test_failed_attempt_outbox_survives_reopen_and_is_account_scoped(tmp_path):
    path = tmp_path / "attempt-outbox.sqlite3"
    repository = GameRepository(path)
    account_a = repository.ensure_archive_account("outbox-a")
    account_b = repository.ensure_archive_account("outbox-b")
    attempt = {"attempt_id": "durable-attempt", "account_id": account_a,
               "puzzle_id": "p-outbox", "result": "mistake", "solved": False,
               "mistakes": 1, "hints": 0, "duration_ms": 120,
               "puzzle_json": "{broken"}
    try:
        repository.enqueue_puzzle_attempt(attempt, error="disk busy")
        repository.shutdown()
        repository = GameRepository(path)
        recovered = repository.pending_puzzle_attempts(account_a)
        assert len(recovered) == 1
        assert recovered[0]["attempt_id"] == "durable-attempt"
        assert recovered[0]["last_error"] == "disk busy"
        assert repository.pending_puzzle_attempts(account_b) == []
        repository.acknowledge_puzzle_attempt("durable-attempt")
        repository.acknowledge_puzzle_attempt("durable-attempt")
        assert repository.pending_puzzle_attempts(account_a) == []
    finally:
        repository.shutdown()


def test_v27_upgrade_adds_rating_tables_without_losing_attempt_history(tmp_path):
    path = tmp_path / "puzzles-v27.sqlite3"
    repository = GameRepository(path)
    account = repository.ensure_archive_account("migration-user")
    payload = json.dumps({"puzzle": {"id": "old-attempt", "rating": 1550,
                                     "solution": ["e2e4"], "themes": ["fork"]}})
    repository.save_local_puzzle_attempt(
        account, "old-attempt", result="mistake", solved=False, mistakes=1,
        hints=0, duration_ms=900, puzzle_json=payload, attempt_id="preserved-attempt",
    )
    repository.shutdown()

    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE IF EXISTS local_puzzle_rating_events")
        db.execute("DROP TABLE IF EXISTS local_puzzle_practice_ratings")
        db.execute("PRAGMA user_version=27")

    migrated = GameRepository(path)
    try:
        attempts = migrated.local_puzzle_attempts(account, limit=8)
        assert len(attempts) == 1
        assert attempts[0]["local_attempt_id"] == "preserved-attempt"
        assert migrated.local_puzzle_rating(account)["rating"] == 1500
        with migrated._connection() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"local_puzzle_practice_ratings", "local_puzzle_rating_events"} <= tables
            assert {"local_puzzle_attempt_outbox"} <= tables
            assert db.execute("PRAGMA user_version").fetchone()[0] == 29
    finally:
        migrated.shutdown()
