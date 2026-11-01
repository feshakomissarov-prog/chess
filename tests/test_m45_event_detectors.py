"""Pure detector catalogue and saved-analysis review-planning tests."""

from __future__ import annotations

import json

import chess
import chess.engine

from chess_coach.lichess.event_detectors import (
    DETECTOR_VERSION, EVENT_CATALOG, detect_registered_events, select_review_positions,
)
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.analysis import CompletedGameAnalyzer
from chess_coach.lichess.statistics import StatisticsService


def test_catalog_registers_deferred_motifs_without_claiming_them_detected():
    assert EVENT_CATALOG["fork"].detection == "deferred"
    assert EVENT_CATALOG["pin"].detection == "deferred"
    assert EVENT_CATALOG["move_quality_decrease"].detection == "implemented"
    assert EVENT_CATALOG["missed_opportunity"].detection == "candidate_only"
    assessments = [{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 0}]
    moves = [{"ply": 1, "mover": "white", "uci": "e2e4"}]
    metrics = [{"ply": 1, "cp_loss": 0, "severity": "good"}]
    events = detect_registered_events(assessments, moves, metrics, run_id="r", source="x",
                                     user_color="white")
    assert not any(event["event_type"] in {"fork", "pin", "skewer"} for event in events)


def test_pure_detector_records_best_move_and_cp_transition_with_explicit_confidence():
    assessments = [
        {"ply": 0, "white_cp": 250, "pv": ["e2e4"]},
        {"ply": 1, "white_cp": 0},
    ]
    moves = [{"ply": 1, "mover": "white", "uci": "e2e4"}]
    metrics = [{"ply": 1, "cp_loss": 250, "severity": "blunder", "metric_version": "v2"}]
    events = detect_registered_events(assessments, moves, metrics, run_id="synthetic",
                                     source="stockfish", user_color="white")
    by_code = {event["event_type"]: event for event in events}
    assert {"move_quality_decrease", "large_eval_swing", "best_move_confirmed",
            "winning_advantage_lost", "winning_to_equal"} <= set(by_code)
    assert by_code["move_quality_decrease"]["confidence_state"] == "verified"
    assert by_code["best_move_confirmed"]["confidence_state"] == "candidate"
    assert by_code["best_move_confirmed"]["tags"] == ["base", "best-move", "positive"]
    assert json.loads(by_code["best_move_confirmed"]["evidence_json"])["played_uci"] == "e2e4"
    assert all(event["detector_version"].startswith(DETECTOR_VERSION) for event in events)


def test_selective_review_plan_has_priority_and_enforces_caps():
    metrics = [
        {"ply": 1, "cp_loss": 50, "severity": "inaccuracy", "source": "stockfish",
         "metric_version": "v2", "mate_discontinuity": 0},
        {"ply": 3, "cp_loss": 240, "severity": "blunder", "source": "stockfish",
         "metric_version": "v2", "mate_discontinuity": 0},
        {"ply": 5, "cp_loss": None, "severity": "unknown", "source": "stockfish",
         "metric_version": "v2", "mate_discontinuity": 1},
    ]
    events = [{"ply": 1, "confidence_state": "candidate"},
              {"ply": 3, "confidence_state": "candidate"},
              {"ply": 5, "confidence_state": "unknown"}]
    planned = select_review_positions(metrics, events, budget_per_game=2, budget_total=20)
    assert [row["ply"] for row in planned] == [5, 3]
    assert len(select_review_positions(metrics, events, budget_per_game=50,
                                       budget_total=1)) == 1
    assert select_review_positions(metrics, events, budget_per_game=0,
                                   budget_total=20) == []


def test_repository_persists_multiple_tags_and_reclassification_is_idempotent(tmp_path):
    repository = GameRepository(tmp_path / "events.sqlite3")
    try:
        repository.save_snapshot(
            "event-game", "startpos", "e2e4 e7e5", "white", "Synthetic", False,
            "blitz", "mate", "black", white_player="Me", black_player="Synthetic",
            started_at="2026-10-01T12:00:00+00:00")
        revision = repository.current_line("event-game")
        assert revision
        run_id = repository.save_analysis(
            "event-game", line_revision=1, line_hash=revision["line_hash"],
            source="stockfish", engine_name="Fake UCI", engine_version="synthetic-v1",
            parameters={"pass": "fast"},
            assessments=[{"ply": 0, "white_cp": 0, "pv": ["e2e4"]},
                         {"ply": 1, "white_cp": 10, "pv": ["d7d5"]},
                         {"ply": 2, "white_cp": 120}],
            classifications=[{"ply": 1, "mover": "white", "label": "good"},
                             {"ply": 2, "mover": "black", "label": "mistake"}],
        )
        with repository._connection() as db:
            opponent = db.execute("SELECT * FROM analysis_events WHERE run_id=? "
                                  "AND event_type='opponent_blunder'", (run_id,)).fetchone()
            assert opponent is not None and opponent["confidence_state"] == "candidate"
            tags = {row[0] for row in db.execute(
                "SELECT tag FROM event_tags WHERE event_key=?", (opponent["event_key"],))}
            assert {"base", "opponent", "opportunity"} <= tags
            event_count = db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                                     (run_id,)).fetchone()[0]
        assert repository.reclassify_analysis_events(run_id) == 0
        with repository._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                              (run_id,)).fetchone()[0] == event_count
        plan = repository.plan_review_positions(run_id, budget_per_game=1, budget_total=10)
        assert len(plan) == 1 and plan[0]["ply"] in {1, 2}
    finally:
        repository.shutdown()


def test_selective_review_runs_only_capped_positions_and_persists_multipv_alternatives(tmp_path):
    class FakeReviewResult:
        def __init__(self, board, multipv):
            first_move = next(iter(board.legal_moves))
            second_move = list(board.legal_moves)[1]
            self.info = {"score": chess.engine.PovScore(chess.engine.Cp(30), chess.WHITE),
                         "depth": 4, "nodes": 100, "pv": [first_move]}
            self.multipv = [self.info,
                            {"score": chess.engine.PovScore(chess.engine.Cp(10), chess.WHITE),
                             "depth": 4, "nodes": 100, "pv": [second_move]}][:multipv]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            yield self.info

        def stop(self):
            return None

    class FakeReviewEngine:
        id = {"name": "Synthetic Review", "author": "tests"}

        def __init__(self):
            self.positions = []
            self.quit_calls = 0

        def configure(self, _options):
            return None

        def analysis(self, board, _limit, *, multipv=1):
            self.positions.append(board.fen())
            return FakeReviewResult(board, multipv)

        def quit(self):
            self.quit_calls += 1

    repository = GameRepository(tmp_path / "selective-review.sqlite3")
    engine_file = tmp_path / "fake-stockfish.exe"
    engine_file.write_bytes(b"fake binary for injected engine only")
    engine = FakeReviewEngine()
    checks = []
    try:
        repository.save_snapshot(
            "review-game", "startpos", "e2e4 e7e5", "white", "Synthetic", False,
            "blitz", "finished", "white", white_player="Me", black_player="Synthetic",
            started_at="2026-10-01T12:00:00+00:00")
        analyzer = CompletedGameAnalyzer(repository, lambda _path: engine)
        result = analyzer.review_selected_positions(
            "review-game", engine_file, [1], safety_check=lambda: checks.append(True) or True,
            nodes=999_999, multipv=8)
        assert result["review_position_plies"] == [0, 1]
        assert len(engine.positions) == 2
        assert len(result["alternatives"]) == 4
        assert all(item["multipv_rank"] in {1, 2} for item in result["alternatives"])
        assert len(checks) == 3  # before process start and before each search
        detail = repository.latest_analysis_detail("review-game", 1)
        assert detail and detail["status"] == "partial"
        assert len(detail["alternatives"]) == 4
        with repository._connection() as db:
            params = json.loads(db.execute("SELECT parameters_json FROM analysis_runs WHERE run_id=?",
                                           (result["run_id"],)).fetchone()[0])
            assert params["multipv"] == 3 and params["limit_value"] == 300_000
            assert db.execute("SELECT COUNT(*) FROM analysis_alternatives WHERE run_id=?",
                              (result["run_id"],)).fetchone()[0] == 4
        calls_before_cache = len(engine.positions)
        cached = analyzer.review_selected_positions(
            "review-game", engine_file, [1], safety_check=lambda: True,
            nodes=999_999, multipv=8)
        assert cached["cached"] is True
        assert cached["run_id"] == result["run_id"]
        assert len(engine.positions) == calls_before_cache
        assert engine.quit_calls == 1  # the cache hit created no second process
    finally:
        repository.shutdown()


def test_selective_review_fails_closed_before_starting_fake_engine(tmp_path):
    repository = GameRepository(tmp_path / "unsafe-review.sqlite3")
    engine_file = tmp_path / "fake-stockfish.exe"
    engine_file.write_bytes(b"fake binary")
    created = []
    try:
        repository.save_snapshot(
            "unsafe-review", "startpos", "e2e4", "white", "Human", False,
            "blitz", "finished", None)
        analyzer = CompletedGameAnalyzer(repository,
                                         lambda path: created.append(path) or object())
        import pytest
        with pytest.raises(ValueError, match="fair-play"):
            analyzer.review_selected_positions(
                "unsafe-review", engine_file, [1], safety_check=lambda: None)
        assert created == []
    finally:
        repository.shutdown()


def test_selective_review_stops_on_cancellation_or_changed_safety_without_saving_run(tmp_path):
    class IdleEngine:
        id = {"name": "Synthetic", "author": "tests"}

        def __init__(self):
            self.searches = 0
            self.quit_calls = 0

        def configure(self, _options):
            pass

        def analysis(self, *_args, **_kwargs):
            self.searches += 1
            raise AssertionError("cancelled/unsafe review must not start a search")

        def quit(self):
            self.quit_calls += 1

    repository = GameRepository(tmp_path / "cancel-review.sqlite3")
    engine_file = tmp_path / "fake-stockfish.exe"
    engine_file.write_bytes(b"fake binary")
    engine = IdleEngine()
    try:
        repository.save_snapshot("cancel-review", "startpos", "e2e4", "white", "Friend",
                                 False, "blitz", "finished", None)
        analyzer = CompletedGameAnalyzer(repository, lambda _path: engine)
        import pytest
        stop = __import__("threading").Event()
        stop.set()
        with pytest.raises(InterruptedError, match="cancelled"):
            analyzer.review_selected_positions("cancel-review", engine_file, [1],
                                                safety_check=lambda: True, stop_event=stop)
        assert engine.searches == 0
        assert engine.quit_calls == 1
        assert repository.list_analyses("cancel-review") == []
        checks = iter([True, False])
        with pytest.raises(InterruptedError, match="safety state changed"):
            analyzer.review_selected_positions("cancel-review", engine_file, [1],
                                                safety_check=lambda: next(checks))
        assert engine.searches == 0
        assert engine.quit_calls == 2
        assert repository.list_analyses("cancel-review") == []
    finally:
        repository.shutdown()


def test_partial_review_run_does_not_replace_complete_analysis_for_statistics(tmp_path):
    repository = GameRepository(tmp_path / "review-stat-priority.sqlite3")
    try:
        repository.save_snapshot(
            "review-priority", "startpos", "e2e4 e7e5", "white", "Synthetic", False,
            "blitz", "finished", "white", started_at="2026-10-01T12:00:00+00:00")
        revision = repository.current_line("review-priority")
        assert revision
        common = {"game_id": "review-priority", "line_revision": 1,
                  "line_hash": revision["line_hash"], "source": "stockfish",
                  "engine_name": "fake", "engine_version": "fake-v1"}
        repository.save_analysis(
            **common, parameters={"analysis_pass": "fast"},
            assessments=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 10},
                         {"ply": 2, "white_cp": 5}],
            classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 0, "label": "good"},
                             {"ply": 2, "mover": "black", "eval_loss_cp": 5, "label": "good"}],
            status="complete")
        repository.save_analysis(
            **common, parameters={"analysis_pass": "review"},
            assessments=[{"ply": 1, "white_cp": 10}], classifications=[], status="partial")
        detail = repository.latest_analysis_detail("review-priority", 1)
        assert detail and detail["status"] == "complete"
        summary = StatisticsService(repository).summary(analysis_source="stockfish")
        assert summary["evaluated_moves"] == 2
    finally:
        repository.shutdown()
