"""Versioned, source-preserving move metrics and conservative event candidates."""

from __future__ import annotations

import json
from typing import Any

import chess
import chess.engine

from ..domain.accuracy import FORMULA_ID, move_accuracy_from_assessments


# The metric row shape is unchanged; its accuracy has an independently versioned
# formula id so existing rows remain readable and auditable.
METRIC_VERSION = "cc-move-metrics-v2"
LOCAL_ACCURACY_SOURCE = "stockfish-local-lichess-method"
SEVERITY_POLICY_VERSION = "cc-cpl-thresholds-v2"
WDL_MODEL = f"python-chess-sf-v1:{chess.__version__}"


def _typed_score(item: dict[str, Any]) -> chess.engine.PovScore | None:
    if item.get("mate_white") is not None:
        return chess.engine.PovScore(chess.engine.Mate(int(item["mate_white"])), chess.WHITE)
    if item.get("white_cp") is not None:
        return chess.engine.PovScore(chess.engine.Cp(int(item["white_cp"])), chess.WHITE)
    return None


def _white_wdl(item: dict[str, Any], ply: int) -> dict[str, int] | None:
    score = _typed_score(item)
    if score is None:
        return None
    wdl = score.wdl(model="sf", ply=max(0, int(ply))).pov(chess.WHITE)
    return {"win": int(wdl.wins), "draw": int(wdl.draws), "loss": int(wdl.losses)}


def _first_pv_move(item: dict[str, Any]) -> str | None:
    stored = item.get("pv_uci")
    if isinstance(stored, str) and stored.strip():
        return stored.split()[0]
    pv = item.get("pv")
    if isinstance(pv, (list, tuple)) and pv:
        return str(pv[0])
    return None


def classify_severity(cp_loss: int | None) -> str:
    if cp_loss is None:
        return "unknown"
    if cp_loss >= 200:
        return "blunder"
    if cp_loss >= 100:
        return "mistake"
    if cp_loss >= 50:
        return "inaccuracy"
    return "good"


def build_move_facts(assessments: list[dict[str, Any]], moves: list[dict[str, Any]], *,
                     source: str, run_id: str,
                     classifications: list[dict[str, Any]] | None = None,
                     initial_fen: str = "startpos") -> tuple[list[dict], list[dict]]:
    """Build typed per-move facts. Mate discontinuities never become fake centipawns."""
    by_ply = {int(row["ply"]): row for row in assessments}
    legacy = {int(row["ply"]): row for row in classifications or []}
    reply_positions: dict[int, chess.Board] = {}
    position_by_ply: dict[int, chess.Board] = {}
    try:
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        position_by_ply[0] = board.copy(stack=False)
        for move in sorted(moves, key=lambda item: int(item["ply"])):
            raw_uci = move.get("uci")
            if not raw_uci:
                reply_positions = {}
                break
            parsed = chess.Move.from_uci(str(raw_uci))
            expected_mover = "white" if board.turn == chess.WHITE else "black"
            if parsed not in board.legal_moves or str(move.get("mover") or expected_mover) != expected_mover:
                reply_positions = {}
                break
            board.push(parsed)
            reply_positions[int(move["ply"])] = board.copy(stack=False)
            position_by_ply[int(move["ply"])] = board.copy(stack=False)
    except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        reply_positions = {}
    facts: list[dict] = []
    for move in moves:
        ply = int(move["ply"])
        # Keep one derived row per confirmed move even when either adjacent
        # evaluation is missing. The resulting severity is unknown, never good.
        before = by_ply.get(ply - 1) or {}
        after = by_ply.get(ply) or {}
        mover = str(move.get("mover") or ("white" if (ply - 1) % 2 == 0 else "black"))
        has_mate = before.get("mate_white") is not None or after.get("mate_white") is not None
        cp_loss = None
        if (not has_mate and before.get("white_cp") is not None
                and after.get("white_cp") is not None):
            delta = int(before["white_cp"]) - int(after["white_cp"])
            cp_loss = max(0, delta if mover == "white" else -delta)
        accuracy = (move_accuracy_from_assessments(
            before, after, mover=mover,
            before_position=position_by_ply[ply - 1],
            after_position=position_by_ply[ply])
            if source == "stockfish" and ply - 1 in position_by_ply
            and ply in position_by_ply and before.get("depth") is not None
            and after.get("depth") is not None else None)
        facts.append({
            "ply": ply, "mover": mover, "source": source,
            "analysis_run_id": run_id,
            "white_cp_before": before.get("white_cp"), "mate_white_before": before.get("mate_white"),
            "white_cp_after": after.get("white_cp"), "mate_white_after": after.get("mate_white"),
            "cp_loss": cp_loss, "mate_discontinuity": int(has_mate),
            "wdl_before_json": json.dumps(_white_wdl(before, ply - 1), sort_keys=True),
            "wdl_after_json": json.dumps(_white_wdl(after, ply), sort_keys=True),
            "wdl_model": WDL_MODEL if _typed_score(before) and _typed_score(after) else None,
            "accuracy": accuracy,
            "accuracy_source": LOCAL_ACCURACY_SOURCE if accuracy is not None else None,
            "accuracy_formula_id": FORMULA_ID if accuracy is not None else None,
            "severity": classify_severity(cp_loss),
            "severity_policy_version": SEVERITY_POLICY_VERSION,
            "legacy_label": legacy.get(ply, {}).get("label"),
            "metric_version": METRIC_VERSION,
        })

    events: list[dict] = []
    if source == "stockfish" and position_by_ply:
        def proven_mating_side(row, position):
            if position.is_checkmate():
                return "white" if position.turn == chess.BLACK else "black"
            mate = row.get("mate_white")
            if mate is None:
                return None
            mate = int(mate)
            if mate == 0:
                return None  # Mate(0) without a terminal board is inconsistent.
            return "white" if mate > 0 else "black"

        for fact in facts:
            ply = fact["ply"]
            before_position = position_by_ply.get(ply - 1)
            after_position = position_by_ply.get(ply)
            if before_position is None or after_position is None:
                continue
            before = by_ply.get(ply - 1) or {}
            after = by_ply.get(ply) or {}
            before_winner = proven_mating_side(before, before_position)
            after_winner = proven_mating_side(after, after_position)
            mover = fact["mover"]
            event_type = None
            if after_position.is_checkmate():
                event_type = "checkmate_terminal"
            elif before_winner == mover and after_winner != mover:
                event_type = "missed_own_mate"
            elif before_winner and before_winner != mover and after_winner != before_winner:
                event_type = "saved_against_forced_mate"
            elif before_winner == mover and after_winner == mover:
                # A shorter M5→M3 is preserved, not a centipawn mistake.
                event_type = "mate_preserved"
            elif after_winner and after_winner != mover and before_winner != after_winner:
                event_type = "allowed_opponent_mate"
            if event_type is None:
                continue
            events.append({
                "event_key": f"{source}:{run_id}:mate:{event_type}:{ply}",
                "ply": ply, "event_type": event_type,
                "confidence_state": "verified", "source": source,
                "evidence_json": json.dumps({
                    "before_mate_white": before.get("mate_white"),
                    "after_mate_white": after.get("mate_white"),
                    "before_mating_side": before_winner,
                    "after_mating_side": after_winner,
                    "mover": mover,
                    "terminal_checkmate": after_position.is_checkmate(),
                    "white_cp_before": before.get("white_cp"),
                    "white_cp_after": after.get("white_cp"),
                    "accuracy": fact["accuracy"],
                    "accuracy_formula_id": fact["accuracy_formula_id"],
                    "line_revision_bound": True,
                    "analysis_run_id": run_id,
                }, sort_keys=True),
                "detector_version": "cc-mate-events-accuracy-a46b4e6d-v1",
                "tags": ["mate"],
            })
    # A previous opponent move can create an opportunity; a substantial loss on
    # the immediately following reply is only a candidate until selectively reviewed.
    for index, fact in enumerate(facts):
        if index == 0 or fact["cp_loss"] is None or fact["cp_loss"] < 50:
            continue
        opponent = facts[index - 1]
        if opponent["mover"] == fact["mover"] or opponent["cp_loss"] is None or opponent["cp_loss"] < 50:
            continue
        reply_position_ply = int(opponent["ply"])
        reply_position_eval = by_ply.get(reply_position_ply)
        reply_eval = by_ply.get(int(fact["ply"]))
        best_uci = _first_pv_move(reply_position_eval or {})
        board_after_opponent = reply_positions.get(reply_position_ply)
        if (not best_uci or board_after_opponent is None
                or board_after_opponent.turn != (chess.WHITE if fact["mover"] == "white" else chess.BLACK)):
            best_uci = None
        else:
            try:
                reply_move = chess.Move.from_uci(best_uci)
                if reply_move not in board_after_opponent.legal_moves:
                    best_uci = None
            except (ValueError, chess.InvalidMoveError):
                best_uci = None
        events.append({
            "event_key": f"{source}:{run_id}:missed-opportunity:{fact['ply']}",
            "ply": fact["ply"], "event_type": "missed_opportunity",
            "confidence_state": "candidate", "source": source,
            "evidence_json": json.dumps({
                "created_by_opponent_ply": opponent["ply"],
                "opponent_cp_loss": opponent["cp_loss"],
                "reply_cp_loss": fact["cp_loss"],
                "best_move_uci": best_uci,
                "best_move_available": bool(best_uci),
                "best_move_position_ply": reply_position_ply,
                "reason": "single-PV opportunity; selective confirmation required",
                "analysis_run_id": run_id,
                "reply_eval_white_cp": None if reply_eval is None else reply_eval.get("white_cp"),
            }, sort_keys=True),
            "detector_version": "cc-opportunity-candidate-v1",
        })
    return facts, events
