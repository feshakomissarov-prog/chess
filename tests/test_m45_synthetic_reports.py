"""Twenty hand-authored synthetic report cases for M4.5 metric boundaries."""

from __future__ import annotations

import pytest

from chess_coach.lichess.move_metrics import build_move_facts
from chess_coach.lichess.event_detectors import detect_registered_events


# Each row is a small, source-labelled game report. Scores are White POV.
_REPORTS = [
    {"id": "white-loss-0", "mover": "white", "before": 100, "after": 100, "loss": 0, "severity": "good", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-49", "mover": "white", "before": 100, "after": 51, "loss": 49, "severity": "good", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-50", "mover": "white", "before": 100, "after": 50, "loss": 50, "severity": "inaccuracy", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-99", "mover": "white", "before": 100, "after": 1, "loss": 99, "severity": "inaccuracy", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-100", "mover": "white", "before": 100, "after": 0, "loss": 100, "severity": "mistake", "source": "lichess-pgn", "coverage": "complete", "official_accuracy": 74.5},
    {"id": "white-loss-199", "mover": "white", "before": 100, "after": -99, "loss": 199, "severity": "mistake", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-200", "mover": "white", "before": 100, "after": -100, "loss": 200, "severity": "blunder", "source": "stockfish", "coverage": "complete"},
    {"id": "white-loss-310", "mover": "white", "before": 100, "after": -210, "loss": 310, "severity": "blunder", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-0", "mover": "black", "before": -100, "after": -100, "loss": 0, "severity": "good", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-49", "mover": "black", "before": -100, "after": -51, "loss": 49, "severity": "good", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-50", "mover": "black", "before": -100, "after": -50, "loss": 50, "severity": "inaccuracy", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-99", "mover": "black", "before": -100, "after": -1, "loss": 99, "severity": "inaccuracy", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-100", "mover": "black", "before": -100, "after": 0, "loss": 100, "severity": "mistake", "source": "lichess-pgn", "coverage": "complete", "official_accuracy": 69.0},
    {"id": "black-loss-199", "mover": "black", "before": -100, "after": 99, "loss": 199, "severity": "mistake", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-200", "mover": "black", "before": -100, "after": 100, "loss": 200, "severity": "blunder", "source": "stockfish", "coverage": "complete"},
    {"id": "black-loss-310", "mover": "black", "before": -100, "after": 210, "loss": 310, "severity": "blunder", "source": "stockfish", "coverage": "complete"},
    {"id": "mate-transition", "mover": "white", "before": 0, "mate_after": 3, "loss": None, "severity": "unknown", "source": "stockfish", "coverage": "partial"},
    {"id": "missing-score", "mover": "white", "before": 0, "after": None, "loss": None, "severity": "unknown", "source": "lichess-pgn", "coverage": "partial", "official_accuracy": 55.0},
    {"id": "missing-position", "mover": "white", "before": 0, "after": 0, "loss": None, "severity": None, "source": "lichess-pgn", "coverage": "partial", "official_accuracy": None},
    {"id": "candidate-opportunity", "mover": "white", "before": 0, "after": -120, "loss": 120, "severity": "mistake", "source": "stockfish", "coverage": "complete", "event": "candidate"},
]


@pytest.mark.parametrize("report", _REPORTS, ids=lambda report: report["id"])
def test_synthetic_game_report_has_expected_source_coverage_and_metrics(report):
    assessments = [{"ply": 0, "white_cp": report["before"]}]
    moves = [{"ply": 1, "mover": report["mover"]}]
    if "mate_after" in report:
        assessments.append({"ply": 1, "mate_white": report["mate_after"]})
    elif report.get("id") == "missing-position":
        # The before-position exists but the after-position was not assessed.
        pass
    else:
        assessments.append({"ply": 1, "white_cp": report.get("after")})

    if report.get("event") == "candidate":
        assessments[0]["pv_uci"] = "e2e4"
        assessments.append({"ply": 2, "white_cp": 5})
        moves.append({"ply": 2, "mover": "black"})

    facts, events = build_move_facts(assessments, moves, source=report["source"],
                                     run_id=f"report:{report['id']}")
    if report["id"] == "missing-position":
        assert len(facts) == 1
        assert facts[0]["cp_loss"] is None
        assert facts[0]["severity"] == "unknown"
        unknown_events = detect_registered_events(assessments, moves, facts,
            run_id="report:missing-position", source=report["source"], user_color=None)
        assert len(unknown_events) == 1
        assert unknown_events[0]["event_type"] == "outcome_unknown"
        assert unknown_events[0]["confidence_state"] == "unknown"
    else:
        assert len(facts) == (2 if report.get("event") == "candidate" else 1)
        fact = facts[0]
        assert fact["source"] == report["source"]
        assert fact["cp_loss"] == report["loss"]
        assert fact["severity"] == report["severity"]
        assert fact["accuracy"] is None
        assert fact["accuracy_source"] is None
    assert report["coverage"] in {"complete", "partial"}
    assert report.get("official_accuracy") is None or report["source"] == "lichess-pgn"
    if report.get("event") == "candidate":
        assert len(events) == 1
    elif report["id"] != "missing-position":
        assert events == []
    if events:
        assert facts[1]["mover"] == "black"
        assert facts[1]["cp_loss"] == 125
        assert events[0]["confidence_state"] == "candidate"
        assert events[0]["source"] == report["source"]
