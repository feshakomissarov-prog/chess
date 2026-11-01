"""Post-game local UCI analysis. Nothing in this module is attached to live play."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import threading
import time
from typing import Callable

import chess
import chess.engine

from ..diagnostics import log_event
from .repository import GameRepository


ACTIVE_STATUSES = {"created", "started", "started_soon", "playing"}
CLASSIFIER_VERSION = "cc-cpl-thresholds-v1"
_ENGINE_FINGERPRINTS: dict[str, tuple[int, int, str]] = {}
_ENGINE_FINGERPRINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class AnalysisOptions:
    nodes: int = 100_000
    multipv: int = 1
    threads: int = 1
    hash_mb: int = 128
    limit_kind: str = "nodes"
    limit_value: int | None = None

    def normalized(self) -> "AnalysisOptions":
        kind = self.limit_kind if self.limit_kind in {"nodes", "depth", "time"} else "nodes"
        requested = self.nodes if kind == "nodes" and self.limit_value is None else self.limit_value
        value = max(1, min(int(requested or 1), 3_600_000 if kind == "time" else 100_000_000))
        return AnalysisOptions(
            nodes=value if kind == "nodes" else max(1, int(self.nodes)),
            multipv=1,
            threads=max(1, min(8, int(self.threads))),
            hash_mb=max(16, min(512, int(self.hash_mb))),
            limit_kind=kind,
            limit_value=value,
        )

    def engine_limit(self) -> chess.engine.Limit:
        options = self.normalized()
        return {
            "nodes": chess.engine.Limit(nodes=options.limit_value),
            "depth": chess.engine.Limit(depth=options.limit_value),
            "time": chess.engine.Limit(time=options.limit_value / 1000.0),
        }[options.limit_kind]


class CompletedGameAnalyzer:
    def __init__(self, repository: GameRepository,
                 engine_factory: Callable[[str], chess.engine.SimpleEngine] | None = None) -> None:
        self.repository = repository
        self.engine_factory = engine_factory or chess.engine.SimpleEngine.popen_uci

    def analyze(self, game_id: str, engine_path: str | Path,
                options: AnalysisOptions = AnalysisOptions(), *,
                active_human_game_ids: set[str] | None = None,
                progress: Callable[[int, int], None] | None = None,
                stop_event: threading.Event | None = None,
                analysis_pass: str = "standard",
                engine: chess.engine.SimpleEngine | None = None) -> dict:
        if analysis_pass not in {"standard", "fast", "review"}:
            raise ValueError("Unknown analysis pass")
        if active_human_game_ids:
            raise ValueError("Нельзя запускать движок во время онлайн-партии с человеком.")
        game = self.repository.get_game(game_id)
        if not game:
            raise KeyError(game_id)
        if game["status"] in ACTIVE_STATUSES:
            raise ValueError("Анализ доступен только после завершения партии.")
        path = Path(engine_path)
        if not path.is_file():
            raise FileNotFoundError("Укажите существующий локальный Stockfish UCI executable.")
        revision = self.repository.current_line(game_id)
        if not revision:
            raise ValueError("У партии нет проверенной нормализованной линии.")
        options = options.normalized()
        parameters = {"limit_kind": options.limit_kind,
                      "limit_value": options.limit_value,
                      "threads": options.threads, "hash_mb": options.hash_mb,
                      "multipv": 1, "classifier_version": CLASSIFIER_VERSION,
                      **({"analysis_pass": analysis_pass} if analysis_pass != "standard" else {})}
        initial_fen = revision["initial_fen"]
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        boards = [board.copy()]
        moves = revision["moves"].split()
        for uci in moves:
            board.push_uci(uci)
            boards.append(board.copy())
        engine_instance = engine
        owns_engine = engine_instance is None
        if engine_instance is None:
            engine_instance = self.engine_factory(str(path))
        try:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Анализ отменён.")
            identity = dict(engine_instance.id)
            engine_name = str(identity.get("name") or "UCI engine")
            engine_version = json.dumps({
                "identity_version": 2,
                "uci_name": engine_name,
                "uci_author": str(identity.get("author") or ""),
                "binary_sha256": _binary_fingerprint(path),
            }, sort_keys=True, separators=(",", ":"))
            cache = self.repository.find_analysis_cache(
                game_id, int(revision["line_revision"]), revision["line_hash"],
                "stockfish", engine_version, parameters)
            if cache:
                cache["cached"] = True
                return cache
            # MultiPV is marked as a managed UCI option by python-chess. For a
            # single line, leave it at the engine default and do not pass it to
            # SimpleEngine.configure().
            engine_instance.configure({"Threads": options.threads,
                                       "Hash": options.hash_mb})
            log_event("uci_engine_configured", "analysis")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Анализ отменён.")
            assessments = []
            for ply, position in enumerate(boards):
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Анализ отменён.")
                position_started = time.monotonic()
                log_event("uci_analysis_started", "analysis",
                          mode="selective" if analysis_pass == "review" else "fast")
                info = _analyze_interruptibly(
                    engine_instance, position, options.engine_limit(), stop_event)
                log_event("uci_analysis_position_completed", "analysis",
                          duration_ms=(time.monotonic() - position_started) * 1000,
                          count=ply + 1,
                          mode="selective" if analysis_pass == "review" else "fast")
                score = info["score"].pov(chess.WHITE)
                cp = score.score(mate_score=None)
                assessments.append({
                    "ply": ply, "white_cp": cp, "mate_white": score.mate(),
                    "depth": info.get("depth"), "nodes": info.get("nodes"),
                    "pv": [move.uci() for move in info.get("pv", [])],
                })
                if progress:
                    progress(ply + 1, len(boards))
            classifications = []
            for ply, uci in enumerate(moves, start=1):
                before, after = assessments[ply - 1], assessments[ply]
                position = boards[ply - 1]
                mover = "white" if position.turn == chess.WHITE else "black"
                bcp, acp = before["white_cp"], after["white_cp"]
                loss = None if bcp is None or acp is None else max(
                    0, bcp - acp if mover == "white" else acp - bcp)
                label = ("unknown" if loss is None else "blunder" if loss >= 200 else
                         "mistake" if loss >= 100 else "inaccuracy" if loss >= 50 else "good")
                classifications.append({"ply": ply, "mover": mover, "eval_loss_cp": loss,
                                        "label": label,
                                        "phase": self.repository.phase_for_ply(
                                            game_id, int(revision["line_revision"]), ply),
                                        "algorithm_version": CLASSIFIER_VERSION})
            run_id = self.repository.save_analysis(
                game_id, line_revision=int(revision["line_revision"]),
                line_hash=revision["line_hash"], source="stockfish",
                engine_name=engine_name,
                engine_version=engine_version, parameters=parameters,
                assessments=assessments, classifications=classifications)
            return {"run_id": run_id, "assessments": assessments,
                    "classifications": classifications, "cached": False}
        finally:
            if owns_engine:
                engine_instance.quit()

    def review_selected_positions(self, game_id: str, engine_path: str | Path,
                                  move_plies: list[int], *,
                                  safety_check: Callable[[], bool | None],
                                  nodes: int = 150_000, multipv: int = 2,
                                  stop_event: threading.Event | None = None,
                                  progress: Callable[[int, int], None] | None = None,
                                  engine: chess.engine.SimpleEngine | None = None) -> dict:
        """Spend a bounded higher-depth budget on explicit moves from a completed game.

        Each requested move evaluates the position before and after that move. The
        caller must prove current Lichess fair-play safety before every position.
        """
        if safety_check is None or safety_check() is not True:
            raise ValueError("Selective review requires a positive current fair-play safety check.")
        game = self.repository.get_game(game_id)
        if not game:
            raise KeyError(game_id)
        if game["status"] in ACTIVE_STATUSES:
            raise ValueError("Selective review is available only after the game ends.")
        path = Path(engine_path)
        if not path.is_file():
            raise FileNotFoundError("Укажите существующий локальный Stockfish UCI executable.")
        revision = self.repository.current_line(game_id)
        if not revision:
            raise ValueError("У партии нет проверенной нормализованной линии.")
        moves = revision["moves"].split()
        selected = sorted(set(int(ply) for ply in move_plies))[:12]
        if any(ply < 1 or ply > len(moves) for ply in selected):
            raise ValueError("Review ply must identify a move in the current game line.")
        if not selected:
            return {"run_id": None, "assessments": [], "alternatives": [], "cached": False}
        node_budget = max(10_000, min(300_000, int(nodes)))
        multipv = max(1, min(3, int(multipv)))
        board = chess.Board() if revision["initial_fen"] == "startpos" else chess.Board(
            revision["initial_fen"])
        boards = [board.copy()]
        for uci in moves:
            board.push_uci(uci)
            boards.append(board.copy())
        position_plies = sorted({ply for move_ply in selected for ply in (move_ply - 1, move_ply)})
        options = AnalysisOptions(limit_kind="nodes", limit_value=node_budget,
                                  threads=1, hash_mb=128).normalized()
        engine_version = "stockfish-review-sha256:" + _binary_fingerprint(path)
        parameters = {
            "analysis_pass": "review", "parent_line_revision": int(revision["line_revision"]),
            "parent_line_hash": revision["line_hash"], "review_move_plies": selected,
            "review_position_plies": position_plies, "limit_kind": "nodes",
            "limit_value": node_budget, "multipv": multipv, "threads": 1,
            "hash_mb": 128, "review_policy_version": "cc-selective-review-v1",
        }
        cached = self.repository.find_analysis_cache(
            game_id, int(revision["line_revision"]), revision["line_hash"],
            "stockfish", engine_version, parameters)
        if cached:
            cached["cached"] = True
            cached["review_move_plies"] = selected
            cached["review_position_plies"] = position_plies
            return cached
        engine_instance = engine
        owns_engine = engine_instance is None
        if engine_instance is None:
            engine_instance = self.engine_factory(str(path))
        try:
            identity = dict(engine_instance.id)
            engine_name = str(identity.get("name") or "UCI engine")
            # MultiPV is configured per search via engine.analysis(...,
            # multipv=N). python-chess owns the UCI option while that analysis
            # is running and rejects changing it through configure().
            engine_instance.configure({"Threads": 1, "Hash": 128})
            log_event("uci_engine_configured", "analysis", mode="selective")
            assessments: list[dict] = []
            alternatives: list[dict] = []
            for index, ply in enumerate(position_plies):
                if safety_check() is not True:
                    raise InterruptedError("Fair-play safety state changed; review stopped.")
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Selective review cancelled.")
                position_started = time.monotonic()
                log_event("uci_analysis_started", "analysis", mode="selective")
                result = _analyze_interruptibly(
                    engine_instance, boards[ply], options.engine_limit(), stop_event,
                    multipv=multipv)
                log_event("uci_analysis_position_completed", "analysis",
                          duration_ms=(time.monotonic() - position_started) * 1000,
                          count=index + 1, mode="selective")
                lines = result if isinstance(result, list) else [result]
                lines = lines[:multipv]
                if not lines:
                    raise RuntimeError("UCI review returned no principal variation.")
                first = lines[0]
                score = first["score"].pov(chess.WHITE)
                assessments.append({
                    "ply": ply, "white_cp": score.score(mate_score=None),
                    "mate_white": score.mate(), "depth": first.get("depth"),
                    "nodes": first.get("nodes"), "pv": [move.uci() for move in first.get("pv", [])],
                })
                for rank, info in enumerate(lines, start=1):
                    alt_score = info["score"].pov(chess.WHITE)
                    alternatives.append({
                        "ply": ply, "multipv_rank": rank,
                        "white_cp": alt_score.score(mate_score=None),
                        "mate_white": alt_score.mate(), "depth": info.get("depth"),
                        "nodes": info.get("nodes"),
                        "pv": [move.uci() for move in info.get("pv", [])],
                    })
                if progress:
                    progress(index + 1, len(position_plies))
            run_id = self.repository.save_analysis(
                game_id, line_revision=int(revision["line_revision"]),
                line_hash=revision["line_hash"], source="stockfish",
                engine_name=engine_name, engine_version=engine_version,
                parameters=parameters, assessments=assessments, classifications=[],
                alternatives=alternatives, status="partial")
            return {"run_id": run_id, "assessments": assessments,
                    "alternatives": alternatives, "review_move_plies": selected,
                    "review_position_plies": position_plies, "cached": False}
        finally:
            if owns_engine:
                engine_instance.quit()


def _binary_fingerprint(path: Path) -> str:
    """Hash a selected executable once per file version, not on every cache lookup."""
    resolved = str(path.resolve())
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    with _ENGINE_FINGERPRINT_LOCK:
        cached = _ENGINE_FINGERPRINTS.get(resolved)
        if cached and cached[:2] == signature:
            return cached[2]
        digest = hashlib.sha256()
        with path.open("rb") as executable:
            for block in iter(lambda: executable.read(1024 * 1024), b""):
                digest.update(block)
        fingerprint = digest.hexdigest()
        _ENGINE_FINGERPRINTS[resolved] = (*signature, fingerprint)
        return fingerprint


def _analyze_interruptibly(engine, board: chess.Board, limit, stop_event: threading.Event | None,
                           *, multipv: int = 1):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Анализ отменён.")
    try:
        analysis = (engine.analysis(board, limit, multipv=multipv) if multipv > 1
                    else engine.analysis(board, limit))
    except Exception as exc:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Анализ отменён.") from exc
        raise

    finished = threading.Event()
    stop_errors: list[Exception] = []

    def stop_analysis_when_requested() -> None:
        assert stop_event is not None
        while not finished.is_set():
            if stop_event.wait(0.05):
                if not finished.is_set():
                    try:
                        analysis.stop()
                    except Exception as exc:
                        stop_errors.append(exc)
                return

    with analysis:
        stopper = None
        if stop_event is not None:
            stopper = threading.Thread(target=stop_analysis_when_requested,
                                       name="chess-coach-analysis-cancel", daemon=True)
            stopper.start()
        try:
            for _info in analysis:
                pass  # Drain streamed updates until the finite search completes or is stopped.
            if stop_event is not None and stop_event.is_set():
                error = stop_errors[0] if stop_errors else None
                raise InterruptedError("Анализ отменён.") from error
            if multipv > 1:
                lines = getattr(analysis, "multipv", None)
                if isinstance(lines, list) and lines:
                    return lines
            return analysis.info
        except Exception as exc:
            if stop_event is not None and stop_event.is_set() and not isinstance(exc, InterruptedError):
                error = stop_errors[0] if stop_errors else exc
                raise InterruptedError("Анализ отменён.") from error
            raise
        finally:
            finished.set()
            if stopper is not None:
                # Do not let engine.quit() race a still-running analysis.stop().
                # This join runs on the analysis worker, never in the GUI thread.
                stopper.join()
