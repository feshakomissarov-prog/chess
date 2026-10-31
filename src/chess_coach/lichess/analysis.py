"""Post-game local UCI analysis. Nothing in this module is attached to live play."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable

import chess
import chess.engine

from .repository import GameRepository


ACTIVE_STATUSES = {"created", "started", "started_soon", "playing"}
CLASSIFIER_VERSION = "cc-cpl-thresholds-v1"


@dataclass(frozen=True)
class AnalysisOptions:
    nodes: int = 100_000
    multipv: int = 1


class CompletedGameAnalyzer:
    def __init__(self, repository: GameRepository,
                 engine_factory: Callable[[str], chess.engine.SimpleEngine] | None = None) -> None:
        self.repository = repository
        self.engine_factory = engine_factory or chess.engine.SimpleEngine.popen_uci

    def analyze(self, game_id: str, engine_path: str | Path,
                options: AnalysisOptions = AnalysisOptions(), *,
                active_human_game_ids: set[str] | None = None,
                progress: Callable[[int, int], None] | None = None,
                stop_event: threading.Event | None = None) -> dict:
        if active_human_game_ids and game_id in active_human_game_ids:
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
        parameters = {"nodes": max(1, int(options.nodes)), "multipv": 1}
        initial_fen = revision["initial_fen"]
        board = chess.Board() if initial_fen == "startpos" else chess.Board(initial_fen)
        boards = [board.copy()]
        moves = revision["moves"].split()
        for uci in moves:
            board.push_uci(uci)
            boards.append(board.copy())
        engine = self.engine_factory(str(path))
        try:
            identity = dict(engine.id)
            engine_version = str(identity.get("version", "unknown"))
            cache = self.repository.find_analysis_cache(
                game_id, int(revision["line_revision"]), revision["line_hash"],
                "stockfish", engine_version, parameters)
            if cache:
                cache["cached"] = True
                return cache
            engine.configure({"MultiPV": 1})
            assessments = []
            for ply, position in enumerate(boards):
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Анализ отменён.")
                info = engine.analyse(position, chess.engine.Limit(nodes=parameters["nodes"]))
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
                                        "label": label, "phase": "unknown",
                                        "algorithm_version": CLASSIFIER_VERSION})
            run_id = self.repository.save_analysis(
                game_id, line_revision=int(revision["line_revision"]),
                line_hash=revision["line_hash"], source="stockfish",
                engine_name=identity.get("name", "Stockfish"),
                engine_version=engine_version, parameters=parameters,
                assessments=assessments, classifications=classifications)
            return {"run_id": run_id, "assessments": assessments,
                    "classifications": classifications, "cached": False}
        finally:
            engine.quit()
