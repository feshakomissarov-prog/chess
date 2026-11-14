from datetime import datetime, timezone
import sqlite3

import pytest

from chess_coach.application.lichess_coverage_audit import audit_lichess_coverage
from chess_coach.domain.accuracy import FORMULA_ID
from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION
from scripts.audit_lichess_coverage import main as audit_cli_main


def _save_game(repo, game_id, *, color="white", source="lichess_archive",
               remote_id=None, white_accuracy=None, black_accuracy=None,
               started_at="2026-11-14T12:00:00Z"):
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", color, "Opponent",
                       False, "5+0", "mate", "white", started_at=started_at)
    with repo._connection() as db:
        db.execute("UPDATE games SET source=?,source_game_id=?,"
                   "lichess_accuracy_white=?,lichess_accuracy_black=? WHERE game_id=?",
                   (source, remote_id, white_accuracy, black_accuracy, game_id))


def _link(repo, account_id, game_id, *, remote_id=None, color="white"):
    with repo._connection() as db:
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color) "
                   "VALUES (?,?,?,'2026-11-14T12:00:00Z',?)",
                   (account_id, game_id, remote_id or game_id, color))


def _save_run(repo, game_id, source, *, status="complete", plies=(0, 1, 2, 3),
              accuracy=None):
    line = repo.current_line(game_id)
    run_id = repo.save_analysis(
        game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="Synthetic", engine_version="fixture", parameters={},
        status=status,
        assessments=[{"ply": ply, "white_cp": ply * 10} for ply in plies],
        classifications=[])
    if accuracy is not None:
        with repo._connection() as db:
            db.execute("INSERT INTO analysis_game_accuracy(run_id,formula_id,white_accuracy,"
                       "black_accuracy,run_finished_at,computed_at) VALUES (?,?,?,?,?,?)",
                       (run_id, FORMULA_ID, accuracy, accuracy,
                        db.execute("SELECT finished_at FROM analysis_runs WHERE run_id=?",
                                   (run_id,)).fetchone()[0], "2026-11-14T12:01:00Z"))


def test_coverage_audit_distinguishes_sources_accuracy_scope_and_backfill_read_only(
        tmp_path, capsys):
    path = tmp_path / "coverage-fixture.sqlite3"
    repo = GameRepository(path)
    try:
        account_id = repo.ensure_archive_account("synthetic-owner")
        _save_game(repo, "lichess-zero", white_accuracy=0)
        _save_game(repo, "opposite-only", white_accuracy=120, black_accuracy=85)
        _save_game(repo, "unknown-side", color="white", white_accuracy=80)
        _save_game(repo, "stockfish-only", source="local", remote_id=None)
        _save_game(repo, "overlap", white_accuracy=91)
        _save_game(repo, "partial", white_accuracy=None)
        _save_game(repo, "stale-only", white_accuracy=None)
        _save_game(repo, "without-scores", source="local", remote_id=None)
        for game_id in ("lichess-zero", "opposite-only", "unknown-side", "stockfish-only",
                        "overlap", "partial", "stale-only", "without-scores"):
            color = None if game_id == "unknown-side" else "white"
            _link(repo, account_id, game_id, color=color)

        _save_run(repo, "lichess-zero", "lichess-pgn")
        _save_run(repo, "opposite-only", "lichess-json")
        _save_run(repo, "stockfish-only", "stockfish", accuracy=87)
        _save_run(repo, "overlap", "lichess-json", accuracy=92)
        _save_run(repo, "overlap", "stockfish", accuracy=90)
        _save_run(repo, "partial", "lichess-pgn", status="partial", plies=(0, 1))
        _save_run(repo, "stale-only", "stockfish")
        repo.save_snapshot("stale-only", "startpos", "d2d4 d7d5 c2c4", "white", "Opponent",
                           False, "5+0", "mate", "black", started_at="2026-11-14T12:00:00Z")
        with repo._connection() as db:
            # Current-line scores and accuracy must ignore this old-line run.
            for game_id, accuracy_status, evals_status, checked, retry in (
                ("lichess-zero", "has_data", "has_data", "2026-11-14T10:00:00Z", None),
                ("opposite-only", "partial", "partial", "2026-11-14T10:05:00Z", None),
                ("partial", "no_server_data", "no_server_data", "2026-11-14T10:10:00Z",
                 "2026-11-15T10:00:00Z"),
                ("without-scores", "queued", "queued", None, None),
            ):
                current = repo.current_line(game_id)
                db.execute("INSERT INTO lichess_analysis_backfill(account_id,game_id,line_revision,"
                           "line_hash,accuracy_status,evals_status,accuracy_checked_at,"
                           "evals_checked_at,accuracy_next_retry_at,evals_next_retry_at,updated_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (account_id, game_id, current["line_revision"], current["line_hash"],
                            accuracy_status, evals_status, checked, checked, retry, retry, checked or "queued"))
            db.execute("INSERT INTO lichess_analysis_backfill_budget(account_id,utc_day,requests,"
                       "paused,updated_at) VALUES (?, '2026-11-14', 4, 0, '2026-11-14T12:00:00Z')",
                       (account_id,))
        snapshot_path = tmp_path / "coverage-stable-snapshot.sqlite3"
        source = sqlite3.connect(path)
        destination = sqlite3.connect(snapshot_path)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        connection = sqlite3.connect(f"file:{snapshot_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            result = audit_lichess_coverage(
                connection, account_id,
                snapshot_time=datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc))
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("UPDATE games SET opponent='changed'")
        finally:
            connection.close()

        scope = result["all_time"]
        assert scope["completed_games"] == 8
        assert scope["valid_lichess_game_ids"] == 6
        assert scope["position_scores"] == {
            "games_with_any": 5, "games_with_lichess": 4, "games_with_stockfish": 2,
            "source_overlap": 1, "no_scores": 3, "partial": 1, "complete": 4,
        }
        assert scope["accuracy"] == {
            "official_lichess_player_side": 2, "official_only_opposite_side": 1,
            "official_invalid_or_out_of_range": 1, "official_unknown_player_side": 1,
            "local_stockfish": 2, "local_server_based": 1, "combined_distinct": 3,
        }
        ready = result["ready_backfill"]
        assert (ready["total"], ready["checked"], ready["accuracy"],
                ready["evals"], ready["no_server_data"], ready["pending"],
                ready["requests_today"], ready["retry_waiting"]) == (4, 3, 1, 2, 1, 1, 4, 1)
        serialized = repr(result)
        assert account_id not in serialized
        assert all(game_id not in serialized for game_id in (
            "lichess-zero", "opposite-only", "stockfish-only", "synthetic-owner"))
        assert result["schema_user_version"] == SCHEMA_VERSION

        assert audit_cli_main([
            str(snapshot_path), account_id,
            "--snapshot-time", "2026-11-14T12:30:00Z",
        ]) == 0
        output = capsys.readouterr().out
        assert "synthetic-owner" not in output and account_id not in output
        assert all(game_id not in output for game_id in (
            "lichess-zero", "opposite-only", "stockfish-only", "without-scores"))
        assert str(tmp_path) not in output
        assert '"games_with_lichess": 4' in output
    finally:
        repo.shutdown()
