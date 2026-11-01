"""Acceptance regressions for PR #14 review fixes using file-backed SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading

import chess
import pytest

from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.puzzles import PuzzleActivitySync
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.statistics import StatisticsService


LICHESS_EPOCH_MS = 1_356_998_400_070


def pgn(white: str = "Alice", black: str = "Bob", result: str = "1-0",
        moves: str = "1. e4 e5 2. Nf3 Nc6") -> str:
    return (f'[Event "Integration"]\n[White "{white}"]\n[Black "{black}"]\n'
            f'[Result "{result}"]\n[TimeControl "600+0"]\n\n{moves} {result}\n')


def archive_record(game_id: str, created_at: int, *, white: str = "Alice",
                   black: str = "Bob", result: str = "1-0", moves: str | None = None) -> dict:
    return {
        "id": game_id,
        "createdAt": created_at,
        "pgn": pgn(white, black, result, moves or "1. e4 e5 2. Nf3 Nc6"),
        "players": {"white": {"rating": 1610}, "black": {"rating": 1720}},
    }


class FileArchiveClient:
    def __init__(self, records: list[dict]):
        self.records = records

    def stream_user_games(self, _token, _username, stop_event, *, since=None,
                          until_ms=None, max_games=1000):
        selected = [record for record in self.records
                    if (since is None or int(record["createdAt"]) >= since)
                    and (until_ms is None or int(record["createdAt"]) <= until_ms)]
        selected.sort(key=lambda record: int(record["createdAt"]), reverse=since is None)
        for record in selected[:max_games]:
            if stop_event.is_set():
                break
            yield record


def test_archive_file_sqlite_resume_preserves_fixed_backfill_anchor_and_late_games(
        tmp_path, monkeypatch):
    from chess_coach.lichess import archive as archive_module

    anchor_ms = 1_735_689_600_000  # 2025-01-01T00:00:00Z
    clock_ms = [anchor_ms]
    monkeypatch.setattr(archive_module.time, "time", lambda: clock_ms[0] / 1000)
    records = [archive_record(f"old-{index}", anchor_ms - index * 1000)
               for index in range(1, 201)]
    late = archive_record("created-during-backfill", anchor_ms + 86_400_000)
    records.append(late)
    client = FileArchiveClient(records)
    path = tmp_path / "archive-restart.sqlite3"
    repo = GameRepository(path, async_writes=True)
    account = repo.ensure_archive_account("Alice")
    stop = threading.Event()

    def cancel_after_first_page(new_count, _game_id):
        if new_count == 50:
            stop.set()

    assert ArchiveSync(client, repo).run(
        "Alice", "fake", stop, full=True, page_size=50,
        progress=cancel_after_first_page) == 50
    interrupted = repo.archive_sync_state(account)
    assert interrupted["backfill_complete"] == 0
    assert interrupted["backfill_started_at"] == anchor_ms
    assert interrupted["backfill_before"] is not None
    assert interrupted["last_success_at"] is None
    repo.shutdown()

    # Simulate a new process and a backfill lasting three days.
    clock_ms[0] = anchor_ms + 3 * 86_400_000
    repo = GameRepository(path, async_writes=True)
    resumed_account = repo.ensure_archive_account("Alice")
    assert repo.archive_sync_state(resumed_account)["backfill_started_at"] == anchor_ms
    imported = ArchiveSync(client, repo).run(
        "Alice", "fake", threading.Event(), full=False, page_size=50)
    assert imported == 151  # 150 older rows plus the one created after the frozen anchor.
    state = repo.archive_sync_state(resumed_account)
    assert state["backfill_complete"] == 1
    assert state["incremental_since"] == anchor_ms + 3 * 86_400_000
    assert state["last_sync_new_count"] == 151
    assert state["last_success_at"]
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 201
        assert db.execute("SELECT COUNT(*) FROM account_games WHERE account_id=?",
                          (resumed_account,)).fetchone()[0] == 201
        assert db.execute("SELECT COUNT(*) FROM game_moves").fetchone()[0] == 4 * 201
    row = repo.get_game("created-during-backfill")
    assert row["raw_pgn"] == late["pgn"]
    assert repo.export_pgn("created-during-backfill") == late["pgn"]
    repo.shutdown()


def test_archive_pilot_import_is_bounded_repeatable_and_does_not_move_full_cursor(tmp_path):
    records = [archive_record(f"pilot-{index}", 1_735_689_600_000 + index)
               for index in range(5)]

    class PilotClient:
        calls = []
        def stream_user_games(self, token, username, stop_event, *, since=None,
                              until_ms=None, max_games=1000):
            self.calls.append((token, username, since, until_ms, max_games))
            selected = [record for record in records
                        if (since is None or record["createdAt"] >= since)
                        and (until_ms is None or record["createdAt"] <= until_ms)]
            selected.sort(key=lambda record: record["createdAt"], reverse=since is None)
            yield from selected[:max_games]

    client = PilotClient()
    repo = GameRepository(tmp_path / "pilot.sqlite3", async_writes=True)
    account = repo.ensure_archive_account("Alice")
    before = repo.archive_sync_state(account)

    first = ArchiveSync(client, repo).run_pilot(
        "Alice", "fake", threading.Event(), max_games=5)

    assert first == {"received": 5, "new": 5, "status": "completed"}
    repo.save_note("pilot-0", "Keep this note across archive pilot upserts")
    second = ArchiveSync(client, repo).run_pilot(
        "Alice", "fake", threading.Event(), max_games=5)

    assert second == {"received": 5, "new": 0, "status": "completed"}
    assert client.calls == [("fake", "Alice", None, None, 5)] * 2
    assert repo.archive_sync_state(account) == before
    assert repo.list_notes("pilot-0")[0]["body"] == "Keep this note across archive pilot upserts"
    assert repo.export_pgn("pilot-0") == records[0]["pgn"]
    assert len(repo.list_games(completed_only=False)) == 5
    runs = repo.list_archive_pilot_runs(account)
    assert [(row["received_count"], row["new_count"], row["status"]) for row in runs] == [
        (5, 0, "completed"), (5, 5, "completed")]
    assert ArchiveSync(client, repo).run(
        "Alice", "fake", threading.Event(), full=True, page_size=5) == 0
    assert repo.archive_sync_state(account)["backfill_complete"] == 1
    assert len(repo.list_games(completed_only=False)) == 5
    assert repo.list_notes("pilot-0")[0]["body"] == "Keep this note across archive pilot upserts"
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM account_games WHERE account_id=?",
                          (account,)).fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM archive_pilot_runs WHERE account_id=?",
                          (account,)).fetchone()[0] == 2
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    repo.shutdown()


def test_archive_pilot_import_rejects_unbounded_sample_and_records_empty_archive(tmp_path):
    repo = GameRepository(tmp_path / "pilot-empty.sqlite3")
    account = repo.ensure_archive_account("Alice")

    class EmptyClient:
        def stream_user_games(self, _token, _username, _stop, *, max_games=1000, **_kwargs):
            assert max_games == 10
            return iter(())

    sync = ArchiveSync(EmptyClient(), repo)
    with pytest.raises(ValueError):
        sync.run_pilot("Alice", "fake", threading.Event(), max_games=100)
    assert sync.run_pilot("Alice", "fake", threading.Event(), max_games=10) == {
        "received": 0, "new": 0, "status": "completed"}
    assert repo.archive_sync_state(account)["backfill_complete"] == 0
    assert repo.list_archive_pilot_runs(account)[0]["received_count"] == 0
    repo.shutdown()


def test_archive_pilot_interruption_and_sqlite_failure_keep_full_cursor_and_atomic_games(
        tmp_path, monkeypatch):
    records = [archive_record(f"pilot-stop-{index}", 1_735_689_600_000 + index)
               for index in range(5)]
    client = FileArchiveClient(records)
    repo = GameRepository(tmp_path / "pilot-interruption.sqlite3", async_writes=True)
    account = repo.ensure_archive_account("Alice")
    before = repo.archive_sync_state(account)
    stop = threading.Event()

    def cancel_after_first(_received, _new, _game_id):
        stop.set()

    interrupted = ArchiveSync(client, repo).run_pilot(
        "Alice", "fake", stop, max_games=5, progress=cancel_after_first)
    assert interrupted == {"received": 1, "new": 1, "status": "interrupted"}
    assert repo.archive_sync_state(account) == before
    assert repo.get_game("pilot-stop-4") is not None
    client.records = [archive_record("pilot-failed", 1_735_689_600_100)]

    def fail_before_commit(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated pilot write failure")

    monkeypatch.setattr(repo, "_save_snapshot_sync", fail_before_commit)
    with pytest.raises(sqlite3.OperationalError, match="simulated pilot write failure"):
        ArchiveSync(client, repo).run_pilot("Alice", "fake", threading.Event(), max_games=5)
    assert repo.archive_sync_state(account) == before
    assert repo.get_game("pilot-failed") is None
    failed_run = repo.list_archive_pilot_runs(account)[0]
    assert failed_run["status"] == "error"
    assert failed_run["received_count"] == 1 and failed_run["new_count"] == 0
    repo.shutdown()


@pytest.mark.parametrize("order", [("Alice", "Bob"), ("Bob", "Alice")])
def test_account_relative_opponent_result_rating_search_and_notes_are_order_independent(
        tmp_path, order):
    repo = GameRepository(tmp_path / f"accounts-{'-'.join(order)}.sqlite3")
    record = archive_record("shared-game", 1_735_689_600_000)
    for username in order:
        repo.import_archive_record(username, record, checkpoint_ms=None, backfill=False)
    alice = repo.ensure_archive_account("Alice")
    bob = repo.ensure_archive_account("Bob")
    note_id = repo.save_note("shared-game", "account import order regression")

    for account, opponent, color, rating in (
        (alice, "Bob", "white", 1610), (bob, "Alice", "black", 1720),
    ):
        with repo._connection() as db:
            row = db.execute("SELECT opponent,player_name,color,player_rating,opponent_rating "
                             "FROM account_games WHERE account_id=? AND game_id='shared-game'",
                             (account,)).fetchone()
        assert tuple(row) == (opponent, "Alice" if color == "white" else "Bob", color,
                              rating, 1720 if color == "white" else 1610)
        assert StatisticsService(repo).summary(account_id=account, opponent=opponent)["wins"] == (
            1 if color == "white" else 0)
        summary = StatisticsService(repo).summary(account_id=account, opponent=opponent)
        assert summary["losses"] == (1 if color == "black" else 0)
        assert StatisticsService(repo).summary(
            account_id=account, opponent="unrelated")["games"] == 0
        assert StatisticsService(repo).summary(account_id=account, rating_min=rating)["games"] == 1
        assert repo.search_games(opponent=opponent, account_id=account)["total"] == 1
        assert repo.search_games(opponent="unrelated", account_id=account)["total"] == 0
    assert repo.list_notes("shared-game")[0]["note_id"] == note_id
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM account_games WHERE game_id='shared-game'").fetchone()[0] == 2
    repo.shutdown()


def test_schema_v5_migrates_account_identity_and_keeps_backup_and_existing_rows(tmp_path):
    path = tmp_path / "legacy-v5.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE games (
            game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, moves TEXT NOT NULL,
            color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER NOT NULL,
            clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT,
            updated_at TEXT NOT NULL, pgn TEXT NOT NULL,
            white_player TEXT NOT NULL DEFAULT '?', black_player TEXT NOT NULL DEFAULT '?',
            started_at TEXT, final_sync_pending INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE accounts(account_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
            username TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(provider,username));
        CREATE TABLE account_games(account_id TEXT NOT NULL,game_id TEXT NOT NULL,remote_id TEXT NOT NULL,
            imported_at TEXT NOT NULL,color TEXT,player_rating INTEGER,opponent_rating INTEGER,
            PRIMARY KEY(account_id,game_id));
        CREATE TABLE archive_sync_state(account_id TEXT PRIMARY KEY,backfill_before INTEGER,
            backfill_complete INTEGER NOT NULL DEFAULT 0,incremental_since INTEGER,last_success_at TEXT,
            imported_count INTEGER NOT NULL DEFAULT 0,last_error TEXT,updated_at TEXT NOT NULL);
        CREATE TABLE puzzle_sync_state(account_id TEXT PRIMARY KEY,backfill_before INTEGER,
            backfill_complete INTEGER NOT NULL DEFAULT 0,incremental_since INTEGER,last_success_at TEXT,
            imported_count INTEGER NOT NULL DEFAULT 0,last_error TEXT,updated_at TEXT NOT NULL);
    """)
    db.execute("INSERT INTO accounts VALUES ('lichess:alice','lichess','Alice','2025-01-01')")
    db.execute("INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,winner,"
                "updated_at,pgn,white_player,black_player) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old-game", "startpos", "e2e4", "white", "Bob", 0, "600+0", "finished",
                 "white", "2025-01-01", pgn("Alice", "Bob", "1-0", "1. e4"), "Alice", "Bob"))
    db.execute("INSERT INTO account_games VALUES (?,?,?,?,?,?,?)",
                ("lichess:alice", "old-game", "old-game", "2025-01-01", "white", 1600, 1500))
    db.execute("INSERT INTO archive_sync_state VALUES (?,?,?,?,?,?,?,?)",
                ("lichess:alice", 100, 0, 50, None, 1, None, "2025-01-01"))
    db.execute("PRAGMA user_version=5")
    db.commit()
    db.close()

    repo = GameRepository(path)
    account = repo.ensure_archive_account("Alice")
    assert repo.get_game("old-game")["moves"] == "e2e4"
    assert repo.current_line("old-game")["moves"] == "e2e4"
    with repo._connection() as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 11
        identity = migrated.execute("SELECT opponent,player_name FROM account_games "
                                    "WHERE account_id=?", (account,)).fetchone()
        assert tuple(identity) == ("Bob", "Alice")
        state = migrated.execute("SELECT backfill_started_at FROM archive_sync_state "
                                 "WHERE account_id=?", (account,)).fetchone()
        assert state[0] == 50
    backup = sqlite3.connect(str(path) + ".pre-m3-v11.bak")
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5
        assert backup.execute("SELECT game_id FROM games").fetchone()[0] == "old-game"
        assert {row[1] for row in backup.execute("PRAGMA table_info(account_games)")} == {
            "account_id", "game_id", "remote_id", "imported_at", "color",
            "player_rating", "opponent_rating"}
    finally:
        backup.close()
    repo.shutdown()


def test_sqlite_archive_transaction_failure_rolls_back_game_mapping_and_checkpoint(
        tmp_path, monkeypatch):
    repo = GameRepository(tmp_path / "archive-rollback.sqlite3", async_writes=True)
    account = repo.ensure_archive_account("Alice")
    repo.begin_archive_backfill(account, 1_735_689_600_000)
    original = repo._write_revision

    def fail_after_normalized_rows(db, row, revision, *, current):
        original(db, row, revision, current=current)
        raise sqlite3.OperationalError("simulated process interruption before transaction commit")

    monkeypatch.setattr(repo, "_write_revision", fail_after_normalized_rows)
    with pytest.raises(sqlite3.OperationalError, match="simulated process interruption"):
        repo.import_archive_record("Alice", archive_record("rollback", 1_735_689_500_000),
                                   checkpoint_ms=1_735_689_500_000, backfill=True)
    assert repo.get_game("rollback") is None
    assert repo.archive_sync_state(account)["backfill_before"] == 1_735_689_600_000
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM account_games WHERE game_id='rollback'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM game_moves WHERE game_id='rollback'").fetchone()[0] == 0
    monkeypatch.setattr(repo, "_write_revision", original)
    repo.shutdown()
    reopened = GameRepository(tmp_path / "archive-rollback.sqlite3")
    account = reopened.ensure_archive_account("Alice")
    assert reopened.archive_sync_state(account)["backfill_before"] == 1_735_689_600_000
    assert reopened.get_game("rollback") is None
    reopened.shutdown()


def test_puzzle_page_validation_failure_commits_no_partial_attempts_or_cursor(tmp_path):
    repo = GameRepository(tmp_path / "puzzle-page-rollback.sqlite3")
    account = repo.ensure_archive_account("Alice")
    valid = {"date": LICHESS_EPOCH_MS + 1, "win": True,
             "puzzle": {"id": "p1", "fen": chess.STARTING_FEN}}
    invalid = {"date": LICHESS_EPOCH_MS + 2, "win": False,
               "puzzle": {"id": "p2", "fen": ""}}
    with pytest.raises(ValueError, match="requires puzzle id and FEN"):
        repo.import_puzzle_activities("Alice", [valid, invalid], backfill=True)
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM puzzle_attempts WHERE account_id=?",
                          (account,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0] == 0
    assert repo.puzzle_sync_state(account).get("backfill_before") is None
    repo.shutdown()


def test_puzzle_activity_2501_file_sqlite_attempts_resume_without_loss(tmp_path):
    records = [{"date": LICHESS_EPOCH_MS + index, "win": index % 2 == 0,
                "puzzle": {"id": f"p{index % 23}", "fen": chess.STARTING_FEN,
                           "solution": ["e2e4"], "themes": ["fork"],
                           "rating": 1200 + index % 50, "plays": index}}
               for index in range(1, 2502)]

    class Client:
        def __init__(self):
            self.page_calls = 0
            self.cancel_second_page = True

        def stream_puzzle_activity(self, _token, stop_event, *, since=None, before=None, max_items=1000):
            if max_items == 1000:
                self.page_calls += 1
                if self.cancel_second_page and self.page_calls == 2:
                    stop_event.set()
                    return iter(())
            selected = [record for record in records
                        if (before is None or int(record["date"]) < before)
                        and (since is None or int(record["date"]) >= since)]
            selected.sort(key=lambda record: int(record["date"]), reverse=True)
            return iter(selected[:max_items])

    path = tmp_path / "puzzles-restart.sqlite3"
    client = Client()
    stop = threading.Event()
    repo = GameRepository(path)
    assert PuzzleActivitySync(client, repo).run("Alice", "fake", stop, page_size=1000) == 1000
    account = repo.ensure_archive_account("Alice")
    state = repo.puzzle_sync_state(account)
    assert state["backfill_complete"] == 0
    assert state["last_error"]
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM puzzle_attempts WHERE account_id=?",
                          (account,)).fetchone()[0] == 1000
    repo.shutdown()

    repo = GameRepository(path)
    client.page_calls = 0
    client.cancel_second_page = False
    imported = PuzzleActivitySync(client, repo).run("Alice", "fake", threading.Event(), page_size=1000)
    assert imported == 1501
    account = repo.ensure_archive_account("Alice")
    state = repo.puzzle_sync_state(account)
    assert state["backfill_complete"] == 1
    assert state["last_sync_new_count"] == 1501
    assert state["last_success_at"]
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM puzzle_attempts WHERE account_id=?",
                          (account,)).fetchone()[0] == 2501
        assert db.execute("SELECT COUNT(DISTINCT attempt_date_ms) FROM puzzle_attempts WHERE account_id=?",
                          (account,)).fetchone()[0] == 2501
        assert db.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0] == 23
    assert repo.puzzle_statistics(account)["attempts"] == 2501
    repo.shutdown()


def test_targeted_refresh_updates_old_correspondence_game_without_archive_fetch(tmp_path):
    created = 1_700_000_000_000
    original = archive_record("correspondence", created, moves="1. e4 e5")
    updated_pgn = pgn("Alice", "Bob", "0-1", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")

    class Client:
        def __init__(self): self.export_calls = []
        def export_game_pgn(self, token, game_id):
            self.export_calls.append((token, game_id))
            return updated_pgn
        def stream_user_games(self, *_args, **_kwargs):
            pytest.fail("targeted refresh must not fetch the user archive")

    repo = GameRepository(tmp_path / "targeted-refresh.sqlite3")
    repo.import_archive_record("Alice", original, checkpoint_ms=None, backfill=False)
    account = repo.ensure_archive_account("Alice")
    first_revision = repo.get_game("correspondence")["line_revision"]
    client = Client()
    assert ArchiveSync(client, repo).refresh_game("Alice", "fake", "correspondence") is False
    assert repo.get_game("correspondence")["line_revision"] == first_revision + 1
    assert repo.get_game("correspondence")["moves"].endswith("a7a6")
    assert repo.get_game("correspondence")["winner"] == "black"
    assert repo.export_pgn("correspondence") == updated_pgn
    assert client.export_calls == [("fake", "correspondence")]
    assert repo.archive_game_linked(account, "correspondence")
    assert repo.search_games(account_id=account)["total"] == 1
    repo.shutdown()
