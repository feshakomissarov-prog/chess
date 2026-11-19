import json

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
