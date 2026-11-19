import json
import chess

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
