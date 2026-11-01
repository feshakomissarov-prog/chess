"""SQLite M4.5 schema and migration tests use synthetic temporary databases only."""

from __future__ import annotations

import sqlite3

import pytest

from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION


def _seed_repository(path) -> tuple[GameRepository, str]:
    repository = GameRepository(path)
    repository.save_snapshot(
        "synthetic-game", "startpos", "e2e4 e7e5", "white", "Synthetic opponent",
        False, "blitz", "mate", "white", white_player="Synthetic user",
        black_player="Synthetic opponent", started_at="2026-10-01T12:00:00+00:00",
    )
    revision = repository.current_line("synthetic-game")
    assert revision is not None
    repository.save_note("synthetic-game", "synthetic note", line_revision=1, ply=1)
    repository.save_analysis(
        "synthetic-game", line_revision=1, line_hash=revision["line_hash"],
        source="stockfish", engine_name="Fake UCI", engine_version="fake-v1",
        parameters={"limit_kind": "nodes", "limit_value": 10},
        assessments=[
            {"ply": 0, "white_cp": 0, "nodes": 10, "pv": ["e2e4"]},
            {"ply": 1, "white_cp": 15, "nodes": 10, "pv": ["e7e5"]},
            {"ply": 2, "white_cp": 5, "nodes": 10, "pv": ["g1f3"]},
        ],
        classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 0, "label": "good"},
            {"ply": 2, "mover": "black", "eval_loss_cp": 10, "label": "good"},
        ],
    )
    return repository, revision["line_hash"]


def _mark_as_v9(path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("DROP VIEW IF EXISTS analysis_coverage")
        db.execute("DROP TABLE IF EXISTS analysis_jobs")
        db.execute("PRAGMA user_version=9")


def test_schema_v10_adds_queue_and_derived_coverage_without_copying_game_facts(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    repository, line_hash = _seed_repository(path)
    try:
        assert SCHEMA_VERSION == 10
        with repository._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            game = db.execute("SELECT game_id,pgn,moves FROM games WHERE game_id=?",
                              ("synthetic-game",)).fetchone()
            revision = db.execute("SELECT line_hash FROM game_revisions WHERE game_id=?",
                                  ("synthetic-game",)).fetchone()
            coverage = db.execute("SELECT * FROM analysis_coverage WHERE game_id=?",
                                  ("synthetic-game",)).fetchone()
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert version == 10
        assert game["game_id"] == "synthetic-game" and game["moves"] == "e2e4 e7e5"
        assert game["pgn"]
        assert revision["line_hash"] == line_hash
        assert coverage["total_positions"] == 3
        assert coverage["scored_positions"] == 3
        assert coverage["comparable_move_pairs"] == 2
        assert "games" in tables and "game_revisions" in tables and "analysis_jobs" in tables
    finally:
        repository.shutdown()


def test_v9_upgrade_creates_verified_backup_and_preserves_existing_records(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    repository, line_hash = _seed_repository(path)
    repository.shutdown()
    _mark_as_v9(path)

    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v10.bak")
        assert backup.exists()
        with sqlite3.connect(backup) as backup_db:
            assert backup_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup_db.execute("PRAGMA user_version").fetchone()[0] == 9
            assert backup_db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
            assert backup_db.execute("SELECT COUNT(*) FROM game_notes").fetchone()[0] == 1
            assert backup_db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 10
            assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM game_moves").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM game_positions").fetchone()[0] == 3
            assert db.execute("SELECT body FROM game_notes").fetchone()[0] == "synthetic note"
            assert db.execute("SELECT line_hash FROM game_revisions").fetchone()[0] == line_hash
            assert db.execute("SELECT COUNT(*) FROM position_assessments").fetchone()[0] == 3
        assert upgraded.latest_analysis_detail("synthetic-game", 1) is not None
    finally:
        upgraded.shutdown()


def test_v9_migration_failure_rolls_back_new_ddl_and_keeps_backup_and_facts(
        tmp_path, monkeypatch):
    path = tmp_path / "rollback.sqlite3"
    repository, _line_hash = _seed_repository(path)
    repository.shutdown()
    _mark_as_v9(path)

    original = GameRepository._migrate_m45_schema

    def fail_after_ddl(db):
        original(db)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(GameRepository, "_migrate_m45_schema", staticmethod(fail_after_ddl))
    with pytest.raises(RuntimeError, match="injected migration failure"):
        GameRepository(path)

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM game_notes").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='analysis_jobs'").fetchone() is None
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backup = path.with_name(path.name + ".pre-m3-v10.bak")
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    monkeypatch.setattr(GameRepository, "_migrate_m45_schema", staticmethod(original))
    recovered = GameRepository(path)
    try:
        assert len(recovered.list_games()) == 1
        with recovered._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 10
            assert db.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0
    finally:
        recovered.shutdown()
