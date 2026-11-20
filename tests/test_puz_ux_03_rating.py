import json

import chess
from chess_coach.lichess.puzzle_training import PuzzlePlayablePosition
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage


def payload(puzzle_id, rating=1500):
    return json.dumps({"game": {"id": "g"}, "puzzle": {
        "id": puzzle_id, "rating": rating, "themes": ["fork"], "solution": ["e2e4"]
    }})


def save(repository, account, puzzle_id, attempt_id, *, result="success",
         solved=True, mistakes=0, hints=0, rating=1500, rating_eligible=True):
    return repository.save_local_puzzle_attempt(
        account, puzzle_id, result=result, solved=solved, mistakes=mistakes,
        hints=hints, duration_ms=250, puzzle_json=payload(puzzle_id, rating),
        attempt_id=attempt_id, rating_eligible=rating_eligible,
    )


def test_practice_rating_is_separate_persisted_and_account_scoped(tmp_path):
    repository = GameRepository(tmp_path / "practice-rating.sqlite3")
    account_a = repository.ensure_archive_account("rating-a")
    account_b = repository.ensure_archive_account("rating-b")
    try:
        before = repository.local_puzzle_rating(account_a)
        assert {key: before[key] for key in ("rating", "delta", "rated_attempts")} == {
            "rating": 1500, "delta": 0, "rated_attempts": 0}
        save(repository, account_a, "p1", "attempt-a", rating=1600)
        after_a = repository.local_puzzle_rating(account_a)
        assert after_a["rating"] == 1510
        assert after_a["delta"] == 10
        assert after_a["rated_attempts"] == 1
        assert repository.local_puzzle_rating(account_b)["rating"] == 1500
    finally:
        repository.shutdown()

    reopened = GameRepository(tmp_path / "practice-rating.sqlite3")
    try:
        assert reopened.local_puzzle_rating(account_a)["rating"] == 1510
    finally:
        reopened.shutdown()


def test_rating_changes_once_per_account_and_puzzle_even_when_attempt_retries(tmp_path):
    repository = GameRepository(tmp_path / "rating-dedupe.sqlite3")
    account = repository.ensure_archive_account("dedupe")
    try:
        save(repository, account, "p1", "attempt-1", rating=1500)
        save(repository, account, "p1", "attempt-2", rating=1500)
        rating = repository.local_puzzle_rating(account)
        assert {key: rating[key] for key in ("rating", "delta", "rated_attempts")} == {
            "rating": 1508, "delta": 8, "rated_attempts": 1}
        with repository._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM local_puzzle_rating_events").fetchone()[0] == 1
    finally:
        repository.shutdown()


def test_failed_first_try_or_hint_is_not_a_clean_rating_win(tmp_path):
    repository = GameRepository(tmp_path / "rating-error.sqlite3")
    account = repository.ensure_archive_account("error")
    try:
        save(repository, account, "p1", "wrong", result="mistake",
             solved=True, mistakes=1, rating=1500)
        rating = repository.local_puzzle_rating(account)
        assert rating["rating"] == 1492
        assert rating["delta"] == -8
    finally:
        repository.shutdown()


def test_unrated_replay_and_missing_puzzle_rating_do_not_change_cc_rating(tmp_path):
    repository = GameRepository(tmp_path / "rating-unrated.sqlite3")
    account = repository.ensure_archive_account("unrated")
    try:
        save(repository, account, "replay", "replay-1", rating_eligible=False)
        save(repository, account, "unknown", "unknown-1", rating=None)
        rating = repository.local_puzzle_rating(account)
        assert {key: rating[key] for key in ("rating", "delta", "rated_attempts")} == {
            "rating": 1500, "delta": 0, "rated_attempts": 0,
        }
    finally:
        repository.shutdown()


def test_prefetched_rating_metadata_cannot_roll_back_fresh_save_ack(qtbot):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.account_id = "lichess:rating-user"
    page.set_recent_history([], local_count=2, practice_rating={
        "rating": 1508, "delta": 8, "rated_attempts": 1,
    })
    prepared = PuzzlePlayablePosition("prepared", chess.STARTING_FEN, ("d2d4",), ("fork",))

    page._apply_prepared_puzzle({
        "account_id": page.account_id, "theme": "mix", "difficulty": "normal",
        "puzzle": prepared,
        "payload": {"puzzle": {"id": "prepared", "rating": 1500,
                               "solution": ["d2d4"], "themes": ["fork"]}},
        "details": {
            "recent": [], "local_count": 1,
            "practice_rating": {"rating": 1500, "delta": 0, "rated_attempts": 0},
        },
    })

    assert page._current_cc_rating == 1508
    assert "1508" in page.practice_rating.text()
    assert page._local_count == 2

    # A later callback carrying an older per-account snapshot is stale too.
    page.set_recent_history([], local_count=1, practice_rating={
        "rating": 1500, "delta": 0, "rated_attempts": 0,
    })
    assert page._current_cc_rating == 1508
    assert "1508" in page.practice_rating.text()
