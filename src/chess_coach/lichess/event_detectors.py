"""Versioned event catalogue and conservative pure detectors.

The catalogue is intentionally broader than the implemented detector set. A
registered deferred code is discoverable but never emitted as though detected.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import chess


DETECTOR_VERSION = "cc-base-events-v1"


@dataclass(frozen=True)
class EventDefinition:
    code: str
    group: str
    detection: str


_CODES = {
    "base": "move_quality_decrease large_eval_swing winning_to_equal equal_to_losing "
            "winning_to_losing allowed_mate missed_mate missed_opportunity opportunity_used "
            "winning_advantage_lost defensive_resource_missed strong_defense_confirmed "
            "opponent_blunder best_move_confirmed engine_disagreement uncertain outcome_unknown",
    "material_tactics": "hanging_piece hanging_pawn lost_piece unfavorable_exchange "
            "overlooked_capture fork knight_fork pin skewer discovered_attack double_attack "
            "overloaded_defender back_rank_threat lost_material_via_tactic missed_tactical_gain",
    "king": "missed_forced_mate allowed_forced_mate back_rank_mate king_exposure_candidate",
    "opening": "named_position repertoire_or_book_departure early_material_error early_eval_drop "
              "recurring_opening_position_error opening_trap_candidate",
    "endgame_defense": "lost_draw failed_to_convert_winning_position promotion_miss "
                       "endgame_exchange_error missed_saving_resource defense_success "
                       "material_conversion_success",
    "strategic": "pawn_structure_change isolated_pawn doubled_pawns weak_square_candidate "
                 "piece_activity_candidate king_safety_candidate",
    "positive": "opponent_error_punished only_good_move_found stable_advantage_converted "
                "critical_defense_found",
    "unknown": "no_verified_motif",
}

_IMPLEMENTED = {
    "move_quality_decrease", "large_eval_swing", "outcome_unknown",
}
_CANDIDATE_ONLY = {"allowed_mate", "missed_mate", "missed_opportunity",
                   "winning_to_equal", "equal_to_losing", "winning_to_losing",
                   "winning_advantage_lost", "opponent_blunder", "best_move_confirmed",
                   "uncertain"}
EVENT_CATALOG = {
    code: EventDefinition(
        code, group,
        "candidate_only" if code in _CANDIDATE_ONLY else
        "implemented" if code in _IMPLEMENTED else "deferred")
    for group, codes in _CODES.items() for code in codes.split()
}


def _pv_move(assessment: dict[str, Any] | None) -> str | None:
    if not assessment:
        return None
    raw = assessment.get("pv_uci")
    if isinstance(raw, str) and raw.strip():
        return raw.split()[0]
    pv = assessment.get("pv")
    if isinstance(pv, (list, tuple)) and pv:
        return str(pv[0])
    return None


def _own_cp(assessment: dict[str, Any] | None, color: str) -> int | None:
    if not assessment or assessment.get("white_cp") is None or assessment.get("mate_white") is not None:
        return None
    score = int(assessment["white_cp"])
    return score if color == "white" else -score


def detect_registered_events(assessments: list[dict[str, Any]], moves: list[dict[str, Any]],
                            metrics: list[dict[str, Any]], *, run_id: str,
                            source: str, user_color: str | None = None) -> list[dict[str, Any]]:
    """Emit only deterministic CP/PV facts; tactical motifs remain deferred."""
    by_ply = {int(row["ply"]): row for row in assessments}
    metric_by_ply = {int(row["ply"]): row for row in metrics}
    output: list[dict[str, Any]] = []

    def emit(ply: int, code: str, confidence: str, evidence: dict[str, Any], tags: list[str]) -> None:
        definition = EVENT_CATALOG[code]
        detector = DETECTOR_VERSION + ":" + definition.group
        key = f"{run_id}:{detector}:{code}:{ply}"
        output.append({
            "event_key": key, "ply": ply, "event_type": code,
            "confidence_state": confidence, "source": source,
            "detector_version": detector,
            "evidence_json": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            "tags": sorted(set([definition.group, *tags])),
        })

    for move in moves:
        ply = int(move["ply"])
        metric = metric_by_ply.get(ply)
        before, after = by_ply.get(ply - 1), by_ply.get(ply)
        if not metric:
            continue
        before = before or {}
        after = after or {}
        played = move.get("uci")
        mover = str(move.get("mover") or "unknown")
        evidence = {
            "analysis_run_id": run_id, "source": source, "mover": mover,
            "played_uci": played, "before_ply": ply - 1, "after_ply": ply,
            "white_cp_before": before.get("white_cp"), "mate_white_before": before.get("mate_white"),
            "white_cp_after": after.get("white_cp"), "mate_white_after": after.get("mate_white"),
            "cp_loss": metric.get("cp_loss"), "severity": metric.get("severity"),
            "best_move_uci": _pv_move(before),
        }
        severity = metric.get("severity")
        if severity in {"inaccuracy", "mistake", "blunder"}:
            emit(ply, "move_quality_decrease", "verified", evidence, ["quality", severity])
        if metric.get("cp_loss") is not None and metric["cp_loss"] >= 200:
            emit(ply, "large_eval_swing", "candidate", evidence, ["evaluation", "blunder"])
        if metric.get("cp_loss") is None:
            emit(ply, "outcome_unknown", "unknown", evidence, ["coverage"])
        if not before or not after:
            continue
        best = evidence["best_move_uci"]
        if played and best and played == best:
            emit(ply, "best_move_confirmed", "candidate", evidence, ["positive", "best-move"])
        # Opponent-relative labels cannot be persisted on a shared run: one game
        # may be linked to both white and black accounts. Derive that perspective
        # only in an account-scoped read model.

        before_own = _own_cp(before, mover)
        after_own = _own_cp(after, mover)
        if before_own is not None and after_own is not None:
            if before_own >= 200 and after_own <= 50:
                emit(ply, "winning_advantage_lost", "candidate", evidence, ["conversion"])
            if before_own >= 200 and -50 <= after_own <= 50:
                emit(ply, "winning_to_equal", "candidate", evidence, ["outcome-transition"])
            elif -50 <= before_own <= 50 and after_own <= -100:
                emit(ply, "equal_to_losing", "candidate", evidence, ["outcome-transition"])
            elif before_own >= 200 and after_own <= -100:
                emit(ply, "winning_to_losing", "candidate", evidence, ["outcome-transition"])

        if before.get("mate_white") is not None or after.get("mate_white") is not None:
            if after.get("mate_white") is not None:
                # Mate for the opponent is presented as allowed-mate candidate.
                opponent_mate = ((mover == "white" and int(after["mate_white"]) < 0)
                                 or (mover == "black" and int(after["mate_white"]) > 0))
                if opponent_mate:
                    emit(ply, "allowed_mate", "candidate", evidence, ["mate", "tactical"])
            if before.get("mate_white") is not None and after.get("mate_white") is None:
                emit(ply, "missed_mate", "candidate", evidence, ["mate", "tactical"])
    return output


def select_review_positions(metrics: list[dict[str, Any]], events: list[dict[str, Any]], *,
                            budget_per_game: int = 12, budget_total: int = 100) -> list[dict[str, Any]]:
    """Rank stored candidates without invoking an engine; enforce explicit caps."""
    per_game = max(0, min(50, int(budget_per_game)))
    total = max(0, min(500, int(budget_total)))
    by_ply = {int(row["ply"]): row for row in metrics}
    event_ply = {int(row["ply"]) for row in events
                 if row.get("confidence_state") in {"candidate", "unknown"}}
    candidates = []
    for ply, row in by_ply.items():
        loss = row.get("cp_loss")
        mate = bool(row.get("mate_discontinuity"))
        if not mate and (loss is None or int(loss) < 50) and ply not in event_ply:
            continue
        severity_rank = {"blunder": 4, "mistake": 3, "inaccuracy": 2}.get(row.get("severity"), 0)
        score = (1000 if mate else 0) + (severity_rank * 100) + min(int(loss or 0), 999)
        candidates.append({"ply": ply, "reason": "mate" if mate else "evaluation_or_event",
                           "priority": score, "source": row.get("source"),
                           "metric_version": row.get("metric_version")})
    selected = []
    for item in sorted(candidates, key=lambda row: (-row["priority"], row["ply"])):
        if len(selected) >= min(total, per_game):
            break
        selected.append(item)
    return selected
