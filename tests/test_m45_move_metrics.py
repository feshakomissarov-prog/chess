"""Synthetic regression coverage for typed, source-specific M4.5-D facts."""

from __future__ import annotations

import json

from chess_coach.lichess.move_metrics import build_move_facts
from chess_coach.lichess.repository import GameRepository


def test_metrics_keep_white_pov_but_compute_loss_from_each_movers_perspective():
    facts, _events = build_move_facts(
        [
            {"ply": 0, "white_cp": 150, "pv_uci": "e2e4"},
            {"ply": 1, "white_cp": 20, "pv_uci": "e7e5"},
            {"ply": 2, "white_cp": 180},
        ],
        [{"ply": 1, "mover": "white"}, {"ply": 2, "mover": "black"}],
        source="stockfish", run_id="synthetic-run",
        classifications=[{"ply": 1, "label": "mistake"}],
    )
    assert [row["cp_loss"] for row in facts] == [130, 160]
    assert [row["severity"] for row in facts] == ["mistake", "mistake"]
    assert all(row["source"] == "stockfish" for row in facts)
    assert facts[0]["legacy_label"] == "mistake"
    assert facts[0]["severity_policy_version"] == "cc-cpl-thresholds-v2"
    assert facts[0]["wdl_model"].startswith("python-chess-sf-v1:")
    assert json.loads(facts[0]["wdl_before_json"]) is not None


def test_mate_discontinuity_sparse_scores_and_unconfirmed_opportunity_are_explicit():
    facts, events = build_move_facts(
        [
            {"ply": 0, "white_cp": 0},
            {"ply": 1, "white_cp": -120},
            {"ply": 2, "mate_white": 4},
            {"ply": 3, "white_cp": None},
        ],
        [{"ply": 1, "mover": "white"}, {"ply": 2, "mover": "black"},
         {"ply": 3, "mover": "white"}],
        source="lichess-pgn", run_id="synthetic-lichess-run",
    )
    assert facts[0]["cp_loss"] == 120
    assert facts[1]["cp_loss"] is None
    assert facts[1]["mate_discontinuity"] == 1
    assert facts[1]["severity"] == "unknown"
    assert facts[2]["cp_loss"] is None
    assert facts[2]["wdl_model"] is None
    assert len(events) == 0  # no comparable reply loss, so no invented opportunity


def test_opportunity_is_candidate_only_and_not_claimed_as_verified():
    facts, events = build_move_facts(
        [
            {"ply": 0, "white_cp": 0, "pv": ["e2e4", "e7e5"]},
            {"ply": 1, "white_cp": -120},
            {"ply": 2, "white_cp": 5, "pv_uci": "g1f3"},
            {"ply": 3, "white_cp": 135},
        ],
        [{"ply": 1, "mover": "white"}, {"ply": 2, "mover": "black"},
         {"ply": 3, "mover": "white"}],
        source="stockfish", run_id="synthetic-opportunity",
    )
    assert facts[0]["cp_loss"] == 120
    assert len(events) == 1
    assert events[0]["event_type"] == "missed_opportunity"
    assert events[0]["confidence_state"] == "candidate"
    evidence = json.loads(events[0]["evidence_json"])
    assert evidence["created_by_opponent_ply"] == 1
    assert evidence["best_move_position_ply"] == 0
    assert evidence["best_move_uci"] == "e2e4"
    assert evidence["best_move_available"] is True
    assert "single-PV" in evidence["reason"]


def test_save_analysis_persists_versioned_facts_and_report_detail_without_mixing_sources(tmp_path):
    repository = GameRepository(tmp_path / "metrics.sqlite3")
    try:
        repository.save_snapshot(
            "metric-game", "startpos", "e2e4 e7e5", "white", "Synthetic",
            False, "blitz", "mate", "white", white_player="Me", black_player="Synthetic",
            started_at="2026-10-01T12:00:00+00:00")
        revision = repository.current_line("metric-game")
        assert revision
        repository.save_analysis(
            "metric-game", line_revision=1, line_hash=revision["line_hash"],
            source="lichess-pgn", engine_name=None, engine_version=None,
            parameters={"format": "pgn-eval-v1"},
            assessments=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 100},
                         {"ply": 2, "white_cp": 20}],
            classifications=[{"ply": 1, "mover": "white", "label": "good",
                              "algorithm_version": "cc-cpl-thresholds-v1"},
                             {"ply": 2, "mover": "black", "label": "good",
                              "algorithm_version": "cc-cpl-thresholds-v1"}],
        )
        with repository._connection() as db:
            db.execute("UPDATE games SET source='lichess',lichess_accuracy_white=74.5,"
                       "lichess_accuracy_black=69.0 WHERE game_id='metric-game'")
            run_id = db.execute("SELECT run_id FROM analysis_runs WHERE game_id='metric-game'").fetchone()[0]
            db.execute("DELETE FROM move_metrics WHERE run_id=?", (run_id,))
        # Opening a legacy report derives metrics from persisted evals without UCI.
        detail = repository.latest_analysis_detail("metric-game", 1)
        assert detail
        assert detail["source"] == "lichess-pgn"
        assert len(detail["move_metrics"]) == 2
        assert {row["source"] for row in detail["move_metrics"]} == {"lichess-pgn"}
        assert {row["metric_version"] for row in detail["move_metrics"]} == {"cc-move-metrics-v2"}
        assert all(row["accuracy"] is None and row["accuracy_source"] is None
                   for row in detail["move_metrics"])
        assert all(row["legacy_label"] == "good" for row in detail["move_metrics"])
        assert detail["official_game_accuracy"] == 74.5
        assert detail["official_game_accuracy_source"] == "lichess"
        assert detail["official_game_accuracy_color"] == "white"
        assert detail["local_game_accuracy"] is None
        with repository._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=?",
                              (detail["run_id"],)).fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                              (detail["run_id"],)).fetchone()[0] == 0
    finally:
        repository.shutdown()
