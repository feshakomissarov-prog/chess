"""Synthetic regression coverage for typed, source-specific M4.5-D facts."""

from __future__ import annotations

import json
import pytest

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
    assert facts[0]["wdl_model"].startswith("python-chess-sf-v2-terminal-aware:")
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
            {"ply": 0, "white_cp": 0, "pv": ["e2e4"]},
            {"ply": 1, "white_cp": -120, "pv_uci": "e7e5"},
            {"ply": 2, "white_cp": 5, "pv_uci": "g1f3"},
            {"ply": 3, "white_cp": 135},
        ],
        [{"ply": 1, "mover": "white", "uci": "e2e4"},
         {"ply": 2, "mover": "black", "uci": "e7e5"},
         {"ply": 3, "mover": "white", "uci": "g1f3"}],
        source="stockfish", run_id="synthetic-opportunity",
    )
    assert facts[0]["cp_loss"] == 120
    assert len(events) == 1
    assert events[0]["event_type"] == "missed_opportunity"
    assert events[0]["confidence_state"] == "candidate"
    evidence = json.loads(events[0]["evidence_json"])
    assert evidence["created_by_opponent_ply"] == 1
    assert evidence["best_move_position_ply"] == 1
    assert evidence["best_move_uci"] == "e7e5"
    assert evidence["best_move_available"] is True
    assert "single-PV" in evidence["reason"]


def test_local_move_accuracy_is_pov_correct_and_keeps_cpl_when_cp_comparable():
    facts, _events = build_move_facts(
        [{"ply": 0, "white_cp": 0, "depth": 20},
         {"ply": 1, "white_cp": -100, "depth": 20},
         {"ply": 2, "white_cp": 0, "depth": 20}],
        [{"ply": 1, "mover": "white", "uci": "e2e4"},
         {"ply": 2, "mover": "black", "uci": "e7e5"}],
        source="stockfish", run_id="accuracy-cp")
    assert [row["accuracy"] for row in facts] == [
        pytest.approx(67.2397212280), pytest.approx(67.2397212280)]
    assert [row["accuracy_source"] for row in facts] == [
        "stockfish-local-lichess-method"] * 2
    assert all(row["accuracy_formula_id"].startswith("lichess-accuracy-a46b4e6d")
               for row in facts)
    assert [row["cp_loss"] for row in facts] == [100, 100]


def test_mate_accuracy_preserves_typed_mate_and_emits_evidence_not_fake_cpl():
    facts, events = build_move_facts(
        [{"ply": 0, "mate_white": 5, "depth": 20},
         {"ply": 1, "mate_white": 3, "depth": 20},
         {"ply": 2, "mate_white": -3, "depth": 20}],
        [{"ply": 1, "mover": "white", "uci": "e2e4"},
         {"ply": 2, "mover": "black", "uci": "e7e5"}],
        source="stockfish", run_id="accuracy-mate")
    assert facts[0]["accuracy"] == pytest.approx(100.0)  # M5→M3 preserved.
    assert facts[0]["cp_loss"] is None
    assert facts[1]["accuracy"] == pytest.approx(100.0)  # Black POV: +M3→-M3.
    assert facts[1]["cp_loss"] is None
    assert {event["event_type"] for event in events} == {
        "mate_preserved", "saved_against_forced_mate"}
    assert all(event["confidence_state"] == "verified" for event in events)
    assert all(json.loads(event["evidence_json"])["white_cp_after"] is None
               for event in events)


def test_mate_zero_only_uses_terminal_board_and_checkmate_event_is_verified():
    facts, events = build_move_facts(
        [{"ply": 0, "white_cp": 0, "depth": 20},
         {"ply": 1, "white_cp": 0, "depth": 20},
         {"ply": 2, "white_cp": 0, "depth": 20},
         {"ply": 3, "white_cp": 0, "depth": 20},
         {"ply": 4, "mate_white": 0, "depth": 20}],
        [{"ply": 1, "mover": "white", "uci": "f2f3"},
         {"ply": 2, "mover": "black", "uci": "e7e5"},
         {"ply": 3, "mover": "white", "uci": "g2g4"},
         {"ply": 4, "mover": "black", "uci": "d8h4"}],
        source="stockfish", run_id="accuracy-terminal-mate")
    assert facts[-1]["accuracy"] == pytest.approx(100.0)
    assert facts[-1]["cp_loss"] is None
    event = next(row for row in events if row["event_type"] == "checkmate_terminal")
    assert event["confidence_state"] == "verified"
    assert json.loads(event["evidence_json"])["terminal_checkmate"] is True


def test_mate_zero_on_nonterminal_board_does_not_create_accuracy_or_event():
    facts, events = build_move_facts(
        [{"ply": 0, "white_cp": 0}, {"ply": 1, "mate_white": 0}],
        [{"ply": 1, "mover": "white", "uci": "e2e4"}],
        source="stockfish", run_id="accuracy-invalid-mate-zero")
    assert facts[0]["accuracy"] is None
    assert facts[0]["accuracy_formula_id"] is None
    assert not events


def test_stockfish_move_accuracy_is_persisted_separately_from_official_lichess(tmp_path):
    repo = GameRepository(tmp_path / "local-accuracy-persist.sqlite3")
    try:
        repo.save_snapshot("accuracy-store", "startpos", "e2e4 e7e5", "white",
                           "Synthetic", False, "300+0", "mate", "white")
        with repo._connection() as db:
            db.execute("UPDATE games SET source='lichess',source_game_id='synthetic-linked',"
                       "lichess_accuracy_white=81.2,lichess_accuracy_black=77.4 "
                       "WHERE game_id='accuracy-store'")
        line = repo.current_line("accuracy-store")
        repo.save_analysis(
            "accuracy-store", line_revision=line["line_revision"], line_hash=line["line_hash"],
            source="stockfish", engine_name="SyntheticFish", engine_version="test",
            parameters={"depth": 20}, assessments=[
                {"ply": 0, "white_cp": 0, "depth": 20},
                {"ply": 1, "white_cp": -100, "depth": 20},
                {"ply": 2, "white_cp": 0, "depth": 20}], classifications=[])
        detail = repo.latest_analysis_detail("accuracy-store", 1)
        assert detail["source"] == "stockfish"
        assert [row["accuracy"] for row in detail["move_metrics"]] == [
            pytest.approx(67.2397212280), pytest.approx(67.2397212280)]
        assert all(row["accuracy_source"] == "stockfish-local-lichess-method"
                   for row in detail["move_metrics"])
        assert all(row["accuracy_formula_id"].startswith("lichess-accuracy-a46b4e6d")
                   for row in detail["move_metrics"])
        game = repo.get_game("accuracy-store")
        assert game["lichess_accuracy_white"] == 81.2
        assert game["lichess_accuracy_black"] == 77.4
    finally:
        repo.shutdown()


def test_missed_opportunity_never_emits_illegal_or_wrong_side_reply_move():
    _facts, events = build_move_facts(
        [{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": -120, "pv_uci": "e2e4"},
         {"ply": 2, "white_cp": 5, "pv_uci": "a1a8"}, {"ply": 3, "white_cp": 135}],
        [{"ply": 1, "mover": "white", "uci": "e2e4"},
         {"ply": 2, "mover": "black", "uci": "e7e5"},
         {"ply": 3, "mover": "white", "uci": "g1f3"}],
        source="stockfish", run_id="illegal-reply-candidate")
    event = next(item for item in events if item["event_type"] == "missed_opportunity")
    evidence = json.loads(event["evidence_json"])
    assert event["confidence_state"] == "candidate"
    assert evidence["best_move_uci"] is None
    assert evidence["best_move_available"] is False
    assert evidence["best_move_position_ply"] == 1


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
        # A synchronous legacy report read derives metrics in memory without writes or UCI.
        detail = repository.latest_analysis_detail("metric-game", 1)
        assert detail
        assert detail["source"] == "lichess-pgn"
        assert len(detail["move_metrics"]) == 2
        assert {row["source"] for row in detail["move_metrics"]} == {"lichess-pgn"}
        assert {row["metric_version"] for row in detail["move_metrics"]} == {"cc-move-metrics-v3"}
        assert all(row["accuracy"] is None and row["accuracy_source"] is None
                   for row in detail["move_metrics"])
        assert all(row["legacy_label"] == "good" for row in detail["move_metrics"])
        assert detail["official_game_accuracy"] == 74.5
        assert detail["official_game_accuracy_source"] == "lichess"
        assert detail["official_game_accuracy_color"] == "white"
        assert detail["local_game_accuracy"] is None
        assert detail["metrics_materialization_pending"] is False  # non-Stockfish source
        with repository._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=?",
                              (detail["run_id"],)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM analysis_events WHERE run_id=?",
                              (detail["run_id"],)).fetchone()[0] == 0
    finally:
        repository.shutdown()
