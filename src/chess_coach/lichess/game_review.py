"""Read-only review model and evidence policy for completed game review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chess
from ..domain.accuracy import calculate_saved_game_accuracy
from .phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries, phase_at_ply

ACCEPTANCE_POLICY_VERSION = "saved-stockfish-pv-improvement-v1"
MIN_VERIFIED_DEPTH = 15
MIN_VERIFIED_NODES = 50_000
MIN_SCORE_IMPROVEMENT_CP = 30
ACTIVE_STATUSES = {"created", "started", "started_soon", "playing"}
DRAW_STATUSES = {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}
ERROR_SEVERITIES = {"mistake", "blunder"}


@dataclass(frozen=True)
class ReviewMove:
    ply: int
    uci: str
    san: str
    mover: str
    before_fen: str
    after_fen: str
    severity: str
    cp_loss: int | None
    source: str
    white_cp: int | None
    mate_white: int | None
    depth: int | None
    nodes: int | None
    local_accuracy: float | None = None
    local_accuracy_formula_id: str | None = None


def _score(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    cp = row.get("white_cp")
    if cp is not None:
        return int(cp)
    mate = row.get("mate_white")
    if mate is None:
        return None
    mate = int(mate)
    return 100_000 - abs(mate) if mate > 0 else -100_000 + abs(mate) if mate < 0 else 0


def _quality(row: dict[str, Any]) -> bool:
    return int(row.get("depth") or 0) >= MIN_VERIFIED_DEPTH or int(
        row.get("nodes") or 0) >= MIN_VERIFIED_NODES


def _pv(row: dict[str, Any] | None) -> list[str]:
    if not row:
        return []
    value = row.get("pv") or row.get("pv_uci") or ""
    return list(value) if isinstance(value, (list, tuple)) else str(value).split()


def load_game_review(repository, game: dict[str, Any], *,
                     analysis_source: str | None = None) -> dict[str, Any]:
    """Read current line, saved analysis, source-aware metrics and notes, without UCI."""
    game_id = str(game["game_id"])
    line = repository.current_line(game_id)
    if not line:
        raise ValueError("У партии нет текущей сохранённой линии.")
    revision = int(line["line_revision"])
    account_id = game.get("account_id")
    owner_color = game.get("account_color") or game.get("color")
    analysis = repository.latest_analysis_detail(
        game_id, revision, account_id=account_id, user_color=owner_color,
        analysis_source=analysis_source)

    fen = chess.STARTING_FEN if line["initial_fen"] == "startpos" else line["initial_fen"]
    board = chess.Board(fen)
    raw_moves = str(line.get("moves") or "").split()
    metrics_by_ply = {int(row["ply"]): row for row in (analysis or {}).get("move_metrics", [])}
    assessments_by_ply = {int(row["ply"]): row for row in (analysis or {}).get("assessments", [])}
    moves: list[ReviewMove] = []
    positions = [board.copy(stack=True)]
    for ply, uci in enumerate(raw_moves, start=1):
        before = board.fen()
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"Сохранённая линия содержит нелегальный ход на ply {ply}.")
        san = board.san(move)
        mover = "white" if board.turn == chess.WHITE else "black"
        board.push(move)
        positions.append(board.copy(stack=True))
        metric = metrics_by_ply.get(ply, {})
        assessment = assessments_by_ply.get(ply, {})
        moves.append(ReviewMove(
            ply=ply, uci=uci, san=san, mover=mover, before_fen=before,
            after_fen=board.fen(), severity=str(metric.get("severity") or "unknown"),
            cp_loss=(int(metric["cp_loss"]) if metric.get("cp_loss") is not None else None),
            source=str(metric.get("source") or (analysis or {}).get("source") or "unknown"),
            white_cp=assessment.get("white_cp"), mate_white=assessment.get("mate_white"),
            depth=assessment.get("depth"), nodes=assessment.get("nodes"),
            local_accuracy=(float(metric["accuracy"]) if metric.get("accuracy") is not None
                            and str(metric.get("accuracy_source") or "").startswith(
                                "stockfish-local-lichess-method")
                            else None),
            local_accuracy_formula_id=metric.get("accuracy_formula_id")))

    source_is_lichess = bool(game.get("source") in {"lichess", "lichess_archive"}
                             or game.get("source_game_id"))
    per_side = {}
    best_lines = {int(item.get("ply", -1)): item for item in
                  ((analysis or {}).get("alternatives") or [])
                  if int(item.get("multipv_rank") or 0) == 1}
    for color in ("white", "black"):
        side_moves = [move for move in moves if move.mover == color]
        assessed = [move for move in side_moves if move.cp_loss is not None]
        best_known = best_matches = 0
        if source_is_lichess or (analysis or {}).get("source") == "stockfish":
            for move in side_moves:
                row = best_lines.get(move.ply - 1)
                raw_pv = str((row or {}).get("pv_uci") or "").split()
                if not raw_pv:
                    continue
                try:
                    best = chess.Move.from_uci(raw_pv[0])
                    before = positions[move.ply - 1]
                    if best not in before.legal_moves or len(list(before.legal_moves)) <= 1:
                        continue
                except (ValueError, IndexError):
                    continue
                best_known += 1
                best_matches += int(best == chess.Move.from_uci(move.uci))
        per_side[color] = {
            "total": len(side_moves), "assessed": len(assessed),
            "best_known": best_known, "best_matches": best_matches,
            "good": sum(move.severity == "good" for move in assessed),
            "inaccuracy": sum(move.severity == "inaccuracy" for move in assessed),
            "mistake": sum(move.severity == "mistake" for move in assessed),
            "blunder": sum(move.severity == "blunder" for move in assessed),
        }
    official_accuracy = {
        "white": game.get("lichess_accuracy_white") if source_is_lichess else None,
        "black": game.get("lichess_accuracy_black") if source_is_lichess else None,
        "source": "Lichess" if source_is_lichess else None,
    }
    server_backfill = (repository.lichess_analysis_backfill_state(
        str(account_id), game_id) if account_id and source_is_lichess else None)
    read_phases = getattr(repository, "phase_boundaries", None)
    boundaries = read_phases(game_id, revision) if analysis and read_phases else None
    if analysis and boundaries is None:
        middle, end = infer_phase_boundaries(positions)
        boundaries = {"middle_start_ply": middle, "end_start_ply": end,
                      "source": PHASE_FALLBACK_VERSION}
    phase_by_ply = {
        move.ply: phase_at_ply(boundaries.get("middle_start_ply"),
                               boundaries.get("end_start_ply"), move.ply)
        for move in moves
    } if boundaries else {}
    local_accuracy = calculate_saved_game_accuracy(
        game, line, analysis, positions, phase_by_ply=phase_by_ply)
    if analysis is not None:
        analysis["local_game_accuracy"] = dict(local_accuracy.values)
        analysis["local_game_accuracy_formula_id"] = local_accuracy.formula_id
        analysis["local_accuracy_status"] = {
            "status": local_accuracy.status, "reason": local_accuracy.reason,
            "source": local_accuracy.source, "formula_id": local_accuracy.formula_id,
            "coverage": {"scored": local_accuracy.coverage[0],
                         "total": local_accuracy.coverage[1]},
            "depth_range": local_accuracy.depth_range,
            "terminal_positions_derived": local_accuracy.terminal_positions_derived,
            "official": False,
        }
    coverage = (analysis or {}).get("coverage") or {}
    sources = getattr(repository, "game_analysis_sources", None)
    return {
        "available_sources": sources(game_id, revision, line["line_hash"]) if sources else set(),
        "game": dict(game), "line": dict(line), "revision": revision, "moves": moves,
        "positions": positions, "analysis": analysis, "analysis_source":
            (analysis or {}).get("source"), "analysis_status": (analysis or {}).get("status"),
        "coverage": {"scored": int(coverage.get("scored_positions") or 0),
                     "total": int(coverage.get("total_positions") or (len(moves) + 1))},
        "owner_color": owner_color if owner_color in {"white", "black"} else None,
        "per_side": per_side, "official_accuracy": official_accuracy,
        "local_game_accuracy": dict(local_accuracy.values),
        "local_phase_accuracy": local_accuracy.phase_values,
        "phase_by_ply": phase_by_ply,
        "phase_source": boundaries.get("source") if boundaries else None,
        "local_accuracy_status": analysis.get("local_accuracy_status") if analysis else {
            "status": local_accuracy.status, "reason": local_accuracy.reason,
            "coverage": {"scored": local_accuracy.coverage[0],
                         "total": local_accuracy.coverage[1]},
            "formula_id": local_accuracy.formula_id, "official": False},
        "lichess_backfill_state": server_backfill,
        "notes": repository.list_notes(game_id, line_revision=revision),
    }


def verified_improved_moves(review: dict[str, Any], move: ReviewMove) -> dict[str, dict[str, Any]]:
    """Return saved, legal alternatives proven better than the played move.

    A saved same-run Stockfish PV must meet a quality floor and beat the played
    post-move evaluation by >=30 centipawns from the mover's point of view.
    Unknown alternatives remain unknown; this function never calls an engine.
    """
    analysis = review.get("analysis") or {}
    if (analysis.get("source") != "stockfish" or analysis.get("status") != "complete"
            or review["game"].get("status") in ACTIVE_STATUSES):
        return {}
    pre_ply = move.ply - 1
    assessments = {int(row["ply"]): row for row in analysis.get("assessments", [])}
    played_score = _score(assessments.get(move.ply))
    if played_score is None or not _quality(assessments[move.ply]):
        return {}
    board = review["positions"][pre_ply].copy(stack=True)
    rows: list[dict[str, Any]] = []
    root = assessments.get(pre_ply)
    if root:
        rows.append({"pv_uci": " ".join(_pv(root)), "white_cp": root.get("white_cp"),
                     "mate_white": root.get("mate_white"), "depth": root.get("depth"),
                     "nodes": root.get("nodes"), "provenance": "position_assessments"})
    rows.extend({**row, "provenance": "analysis_alternatives"}
                for row in analysis.get("alternatives", []) if int(row.get("ply", -1)) == pre_ply)
    proven: dict[str, dict[str, Any]] = {}
    for row in rows:
        pv = _pv(row)
        if not pv or not _quality(row):
            continue
        try:
            candidate = chess.Move.from_uci(pv[0])
        except ValueError:
            continue
        if candidate not in board.legal_moves:
            continue
        score = _score(row)
        if score is None:
            continue
        improvement = score - played_score if move.mover == "white" else played_score - score
        if improvement >= MIN_SCORE_IMPROVEMENT_CP:
            proven[candidate.uci()] = {
                "score_white": score, "improvement_cp": improvement,
                "provenance": row["provenance"], "depth": int(row.get("depth") or 0),
                "nodes": int(row.get("nodes") or 0), "run_id": analysis.get("run_id"),
                "policy_version": ACCEPTANCE_POLICY_VERSION,
            }
    return proven


def eligible_error_moves(review: dict[str, Any], *, include_inaccuracies: bool = False) -> list[ReviewMove]:
    color = review.get("owner_color")
    analysis = review.get("analysis") or {}
    if (not color or review["game"].get("status") in ACTIVE_STATUSES
            or analysis.get("source") != "stockfish" or analysis.get("status") != "complete"):
        return []
    severities = set(ERROR_SEVERITIES)
    if include_inaccuracies:
        severities.add("inaccuracy")
    return [move for move in review["moves"]
            if move.mover == color and move.severity in severities
            and verified_improved_moves(review, move)]
