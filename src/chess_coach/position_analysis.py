"""Small cancellable, one-position Stockfish evaluation adapter."""

from __future__ import annotations

import threading
from pathlib import Path

import chess
import chess.engine


def evaluate_position(engine_path: str, board: chess.Board, stop_event: threading.Event,
                      active_result: dict) -> dict:
    """Evaluate one copied position; ``active_result`` lets the UI stop stale UCI work."""
    engine = chess.engine.SimpleEngine.popen_uci(engine_path, timeout=5,
                                                 cwd=str(Path(engine_path).parent))
    try:
        if stop_event.is_set():
            return {"cancelled": True}
        analysis = engine.analysis(board.copy(stack=False), chess.engine.Limit(time=2.0))
        active_result["analysis"] = analysis
        if stop_event.is_set():
            analysis.stop()
        best = analysis.wait()
        info = analysis.info
        if stop_event.is_set():
            return {"cancelled": True}
        score = info.get("score")
        score_cp = score.pov(chess.WHITE).score(mate_score=100000) if score else None
        pv = info.get("pv") or []
        return {"cancelled": False, "score_white_cp": score_cp,
                "mate_white": score.pov(chess.WHITE).mate() if score else None,
                "best_move": pv[0].uci() if pv else (best.move.uci() if best.move else None),
                "pv": [move.uci() for move in pv], "depth": info.get("depth"),
                "nodes": info.get("nodes")}
    finally:
        engine.quit()
