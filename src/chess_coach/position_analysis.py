"""Small cancellable, one-position Stockfish evaluation adapter."""

from __future__ import annotations

import threading
from pathlib import Path
import atexit

import chess
import chess.engine
from .uci import popen_uci_hidden


_ENGINE_LOCK = threading.RLock()
_ENGINES: dict[str, chess.engine.SimpleEngine] = {}


def _engine_for_path(engine_path: str) -> chess.engine.SimpleEngine:
    key = str(Path(engine_path).resolve())
    with _ENGINE_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = popen_uci_hidden(key, timeout=5, cwd=str(Path(key).parent))
            _ENGINES[key] = engine
        return engine


def close_position_engines() -> None:
    """Gracefully close cached local-position UCI processes at application exit."""
    with _ENGINE_LOCK:
        engines = list(_ENGINES.values())
        _ENGINES.clear()
    for engine in engines:
        try:
            engine.quit()
        except (OSError, RuntimeError, chess.engine.EngineError):
            pass


atexit.register(close_position_engines)


def evaluate_position(engine_path: str, board: chess.Board, stop_event: threading.Event,
                      active_result: dict, on_update=None, *, engine_manager=None,
                      safety_check=None) -> dict:
    """Evaluate one copied position; ``active_result`` lets the UI stop stale UCI work."""
    if stop_event.is_set():
        return {"cancelled": True}
    engine = (engine_manager.position_engine(
        engine_path, stop_event=stop_event, safety_check=safety_check)
        if engine_manager is not None else _engine_for_path(engine_path))
    on_update = on_update or active_result.get("on_update")
    try:
        if stop_event.is_set():
            return {"cancelled": True}
        analysis = engine.analysis(board.copy(stack=False), chess.engine.Limit(time=2.0),
                                   info=chess.engine.INFO_ALL)
        active_result["analysis"] = analysis
        if stop_event.is_set():
            analysis.stop()
        info = {}
        for update in analysis:
            info = update
            active_result["latest_info"] = update
            if on_update is not None and not stop_event.is_set():
                on_update(update)
            if stop_event.is_set():
                analysis.stop()
                break
        best = analysis.wait()
        info = analysis.info or info
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
        active_result.pop("analysis", None)
        if engine_manager is not None:
            engine.quit()
