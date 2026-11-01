"""SQLite M4.5 schema and migration tests use synthetic temporary databases only."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.api import RateLimitError
from chess_coach.lichess.statistics import StatisticsService


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
        db.execute("DROP TABLE IF EXISTS event_tags")
        db.execute("DROP TABLE IF EXISTS analysis_alternatives")
        db.execute("DROP TABLE IF EXISTS analysis_events")
        db.execute("DROP TABLE IF EXISTS move_metrics")
        db.execute("DROP TABLE IF EXISTS analysis_jobs")
        db.execute("PRAGMA user_version=9")


def test_schema_v16_includes_openings_and_date_precision_without_copying_game_facts(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    repository, line_hash = _seed_repository(path)
    try:
        assert SCHEMA_VERSION == 16
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
            columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
        assert version == 16
        assert game["game_id"] == "synthetic-game" and game["moves"] == "e2e4 e7e5"
        assert game["pgn"]
        assert revision["line_hash"] == line_hash
        assert coverage["total_positions"] == 3
        assert coverage["scored_positions"] == 3
        assert coverage["comparable_move_pairs"] == 2
        assert {"games", "game_revisions", "analysis_jobs", "move_metrics",
                "analysis_events", "event_tags", "analysis_alternatives", "opening_matches"} <= tables
        assert "game_time_precision" in columns
        assert coverage["measured_moves"] == 2
    finally:
        repository.shutdown()


def test_v9_upgrade_creates_verified_backup_and_preserves_existing_records(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    repository, line_hash = _seed_repository(path)
    repository.shutdown()
    _mark_as_v9(path)

    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v16.bak")
        assert backup.exists()
        with sqlite3.connect(backup) as backup_db:
            assert backup_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup_db.execute("PRAGMA user_version").fetchone()[0] == 9
            assert backup_db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
            assert backup_db.execute("SELECT COUNT(*) FROM game_notes").fetchone()[0] == 1
            assert backup_db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM game_moves").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM game_positions").fetchone()[0] == 3
            assert db.execute("SELECT body FROM game_notes").fetchone()[0] == "synthetic note"
            assert db.execute("SELECT line_hash FROM game_revisions").fetchone()[0] == line_hash
            assert db.execute("SELECT COUNT(*) FROM position_assessments").fetchone()[0] == 3
        assert upgraded.latest_analysis_detail("synthetic-game", 1) is not None
    finally:
        upgraded.shutdown()


def test_v10_database_with_preexisting_analysis_jobs_is_upgraded_additively(tmp_path):
    path = tmp_path / "preexisting-jobs-v10.sqlite3"
    repository, _line_hash = _seed_repository(path)
    candidate = repository.list_games_needing_fast_analysis()[0]
    repository.enqueue_analysis_jobs(
        [candidate], analysis_pass="fast", engine_version="synthetic-engine",
        parameters={"limit_kind": "nodes", "limit_value": 20_000})
    repository.shutdown()

    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE analysis_jobs DROP COLUMN result_run_id")
        db.execute("ALTER TABLE analysis_jobs DROP COLUMN lease_until")
        db.execute("ALTER TABLE analysis_jobs DROP COLUMN lease_owner")
        db.execute("DROP TABLE IF EXISTS analysis_events")
        db.execute("DROP TABLE IF EXISTS event_tags")
        db.execute("DROP TABLE IF EXISTS analysis_alternatives")
        db.execute("DROP TABLE IF EXISTS move_metrics")
        db.execute("PRAGMA user_version=10")

    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v16.bak")
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA user_version").fetchone()[0] == 10
            assert db.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 1
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            columns = {row[1] for row in db.execute("PRAGMA table_info(analysis_jobs)")}
            assert {"lease_owner", "lease_until", "result_run_id"} <= columns
            job = db.execute("SELECT status,game_id FROM analysis_jobs").fetchone()
        assert tuple(job) == ("queued", "synthetic-game")
    finally:
        upgraded.shutdown()


def test_v11_database_upgrades_additively_to_move_metrics_and_events(tmp_path):
    path = tmp_path / "upgrade-v11.sqlite3"
    repository, line_hash = _seed_repository(path)
    repository.shutdown()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE analysis_events")
        db.execute("DROP TABLE event_tags")
        db.execute("DROP TABLE analysis_alternatives")
        db.execute("DROP TABLE move_metrics")
        db.execute("DROP VIEW analysis_coverage")
        db.execute("PRAGMA user_version=11")
    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v16.bak")
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 11
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("SELECT line_hash FROM game_revisions").fetchone()[0] == line_hash
            assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM move_metrics").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM analysis_events").fetchone()[0] == 0
    finally:
        upgraded.shutdown()


def test_v12_database_adds_many_to_many_event_tags_without_touching_analysis(tmp_path):
    path = tmp_path / "upgrade-v12.sqlite3"
    repository, line_hash = _seed_repository(path)
    repository.shutdown()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE event_tags")
        db.execute("DROP TABLE analysis_alternatives")
        db.execute("PRAGMA user_version=12")
    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v16.bak")
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 12
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("SELECT line_hash FROM game_revisions").fetchone()[0] == line_hash
            assert db.execute("SELECT COUNT(*) FROM position_assessments").fetchone()[0] == 3
            assert db.execute("SELECT COUNT(*) FROM move_metrics").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM event_tags").fetchone()[0] == 0
    finally:
        upgraded.shutdown()


def test_v13_database_adds_review_alternatives_and_preserves_event_tags(tmp_path):
    path = tmp_path / "upgrade-v13.sqlite3"
    repository, _line_hash = _seed_repository(path)
    repository.shutdown()
    with sqlite3.connect(path) as db:
        tagged = db.execute("SELECT COUNT(*) FROM event_tags").fetchone()[0]
        assert tagged > 0
        db.execute("DROP TABLE analysis_alternatives")
        db.execute("PRAGMA user_version=13")
    upgraded = GameRepository(path)
    try:
        backup = path.with_name(path.name + ".pre-m3-v16.bak")
        with sqlite3.connect(backup) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 13
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("SELECT COUNT(*) FROM event_tags").fetchone()[0] == tagged
            assert db.execute("SELECT COUNT(*) FROM position_assessments").fetchone()[0] == 3
            assert db.execute("SELECT COUNT(*) FROM analysis_alternatives").fetchone()[0] == 0
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
        assert db.execute("SELECT name FROM sqlite_master WHERE name='move_metrics'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='analysis_events'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='event_tags'").fetchone() is None
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backup = path.with_name(path.name + ".pre-m3-v16.bak")
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    monkeypatch.setattr(GameRepository, "_migrate_m45_schema", staticmethod(original))
    recovered = GameRepository(path)
    try:
        assert len(recovered.list_games()) == 1
        with recovered._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0
    finally:
        recovered.shutdown()


def test_late_lichess_pgn_evaluations_merge_into_one_partial_run_without_erasing_scores(tmp_path):
    repository = GameRepository(tmp_path / "late-evals.sqlite3")
    try:
        partial = {
            "id": "late-eval-game",
            "pgn": ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                    '1. e4 { [%eval 0.25,20] } e5 *\n'),
        }
        repository.import_archive_record("Me", partial, checkpoint_ms=10, backfill=True)
        first_runs = repository.list_analyses("late-eval-game")
        assert len(first_runs) == 1
        assert first_runs[0]["status"] == "partial"
        from chess_coach.lichess.statistics import StatisticsService
        partial_summary = StatisticsService(repository).summary(analysis_source="lichess-pgn")
        assert partial_summary["analysis_games"] == 1
        assert partial_summary["evaluated_moves"] == 1
        first_run_id = first_runs[0]["run_id"]
        first = repository.find_analysis_cache(
            "late-eval-game", 1, first_runs[0]["line_hash"], "lichess-pgn", None,
            {"format": "pgn-eval-v1"})
        assert len(first["assessments"]) == 1
        assert first["assessments"][0]["white_cp"] == 25

        refreshed = {
            **partial,
            "pgn": ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                    '1. e4 { [%eval 0.10,10] } e5 { [%eval 0.10,10] } *\n'),
        }
        repository.import_archive_record("Me", refreshed, checkpoint_ms=11, backfill=True)
        repository.import_archive_record("Me", refreshed, checkpoint_ms=11, backfill=True)

        runs = repository.list_analyses("late-eval-game")
        assert len(runs) == 1
        assert runs[0]["run_id"] == first_run_id
        assert runs[0]["status"] == "complete"
        detail = repository.find_analysis_cache(
            "late-eval-game", 1, runs[0]["line_hash"], "lichess-pgn", None,
            {"format": "pgn-eval-v1"})
        assert [(row["ply"], row["white_cp"]) for row in detail["assessments"]] == [
            (1, 25), (2, 10)]
        with repository._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM position_assessments WHERE run_id=?",
                              (first_run_id,)).fetchone()[0] == 2
            coverage = db.execute("SELECT scored_positions,comparable_move_pairs "
                                  "FROM analysis_coverage WHERE run_id=?",
                                  (first_run_id,)).fetchone()
        assert tuple(coverage) == (2, 1)
    finally:
        repository.shutdown()


def test_lichess_archive_move_metrics_drive_summary_without_legacy_classifications(tmp_path):
    repository = GameRepository(tmp_path / "canonical-lichess-metrics.sqlite3")
    try:
        pgn = ('[Event "Synthetic"]\n[Site "Lichess"]\n[Date "2026.10.01"]\n'
               '[White "Me"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
               '1. e4 { [%eval 0.50,12] } e5 { [%eval 0.50,12] } '
               '2. Nf3 { [%eval -1.00,12] } Nc6 { [%eval -1.00,12] } 1-0\n')
        repository.import_archive_record("Me", {"id": "lichess-metrics", "pgn": pgn,
                "accuracy": {"white": 76, "black": 81}}, checkpoint_ms=1, backfill=True)
        account_id = repository.ensure_archive_account("Me")
        run = repository.list_analyses("lichess-metrics")[0]
        with repository._connection() as db:
            legacy_count = db.execute(
                "SELECT COUNT(*) FROM move_classifications WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
        detail = repository.latest_analysis_detail("lichess-metrics", 1)
        by_ply = {item["ply"]: item for item in detail["move_metrics"]}
        assert legacy_count == 0
        assert by_ply[3]["severity"] in {"mistake", "blunder"}
        assert by_ply[3]["source"] == "lichess-pgn"
        assert detail["official_game_accuracy"] == 76
        assert detail["official_game_accuracy_color"] == "white"
        assert detail["official_game_accuracy_source"] == "lichess"
        assert detail["official_game_accuracy_n"] == 1
        summary = StatisticsService(repository).summary(
            account_id=account_id, analysis_source="lichess-pgn")
        assert summary["own_assessed_moves"] == 1
        assert summary["own_error_count"] == 1
        assert summary["lichess_accuracy"]["white"] == 76
        page = StatisticsService(repository).game_page(
            account_id=account_id, analysis_source="lichess-pgn")
        assert page["items"][0]["own_mistakes"] + page["items"][0]["own_blunders"] == 1
        second_account = repository.ensure_archive_account("SecondOwner")
        with repository._connection() as db:
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color) "
                       "VALUES(?,?,?,?,?)", (second_account, "lichess-metrics", "same-game",
                       "2026-11-01T00:00:00+00:00", "black"))
        second_detail = repository.latest_analysis_detail(
            "lichess-metrics", 1, account_id=second_account)
        assert second_detail["official_game_accuracy"] == 81
        assert second_detail["official_game_accuracy_color"] == "black"
        assert second_detail["official_game_accuracy_n"] == 1
    finally:
        repository.shutdown()


def test_bounded_eval_refresh_only_requests_incomplete_recent_lines_and_keeps_cursors(tmp_path):
    repository = GameRepository(tmp_path / "targeted-refresh.sqlite3")
    try:
        repository.import_archive_record("Me", {
            "id": "needs-eval",
            "pgn": ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                    '1. e4 { [%eval 0.20] } e5 *\n'),
        }, checkpoint_ms=None, backfill=False)
        repository.import_archive_record("Me", {
            "id": "already-covered",
            "pgn": ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                    '1. d4 { [%eval 0.30] } d5 { [%eval 0.10] } *\n'),
        }, checkpoint_ms=None, backfill=False)
        account_id = "lichess:me"
        before = repository.archive_sync_state(account_id)

        class FakeExportClient:
            def __init__(self):
                self.calls = []

            def export_game_pgn(self, token, game_id):
                self.calls.append((token, game_id))
                assert token == "fake-token"
                assert game_id == "needs-eval"
                return ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                        '1. e4 { [%eval 0.20] } e5 { [%eval 0.10] } *\n')

        client = FakeExportClient()
        result = ArchiveSync(client, repository).refresh_missing_evaluations(
            "Me", "fake-token", threading.Event(), limit=10)
        after = repository.archive_sync_state(account_id)
        assert result == {"selected": 1, "refreshed": 1, "cancelled": False}
        assert client.calls == [("fake-token", "needs-eval")]
        for key in ("backfill_before", "backfill_complete", "backfill_started_at",
                    "incremental_since"):
            assert after.get(key) == before.get(key)
        run = repository.list_analyses("needs-eval")[0]
        assert run["status"] == "complete"
        assert repository.list_games_missing_lichess_evaluations(account_id, limit=10) == []
        with pytest.raises(ValueError, match="1–100"):
            ArchiveSync(client, repository).refresh_missing_evaluations(
                "Me", "fake-token", threading.Event(), limit=101)
    finally:
        repository.shutdown()


def test_targeted_eval_refresh_stops_on_429_without_retrying_or_masking_error(tmp_path):
    repository = GameRepository(tmp_path / "targeted-refresh-429.sqlite3")
    try:
        repository.import_archive_record("Me", {
            "id": "needs-eval",
            "pgn": ('[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
                    '1. e4 *\n'),
        }, checkpoint_ms=None, backfill=False)

        class LimitedClient:
            calls = 0

            def export_game_pgn(self, _token, _game_id):
                self.calls += 1
                raise RateLimitError(75)

        client = LimitedClient()
        with pytest.raises(RateLimitError) as error:
            ArchiveSync(client, repository).refresh_missing_evaluations(
                "Me", "fake-token", threading.Event(), limit=10)
        assert error.value.retry_after == 75
        assert client.calls == 1
        assert len(repository.list_games_missing_lichess_evaluations("lichess:me")) == 1
    finally:
        repository.shutdown()
