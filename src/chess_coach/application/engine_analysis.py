"""Application use cases for all local Stockfish work.

The coordinator has one StockfishManager dependency and delegates permission
decisions to the existing Fair Play gate callback supplied by the caller.
"""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable

import chess

from ..engine_manager import StockfishManager
from ..lichess.analysis import AnalysisOptions, CompletedGameAnalyzer
from ..lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from ..position_analysis import evaluate_position
from .fair_play import playing_snapshot_allows_analysis, refresh_playing_snapshot


class EngineAnalysisCoordinator:
    def __init__(self, manager: StockfishManager) -> None:
        self.manager = manager

    def create_batch_runner(self, repository, engine_path: str | Path, *,
                            options: FastPassOptions = FastPassOptions()) -> BatchAnalysisRunner:
        return BatchAnalysisRunner(repository, engine_path, options=options,
                                   engine_manager=self.manager)

    def analyze_completed_game(self, repository, game_id: str, engine_path: str | Path,
                               options: AnalysisOptions, *, stop_event: Event,
                               safety_check: Callable[[], bool],
                               active_human_game_ids: set[str] | None = None,
                               progress=None, analyzer_factory=CompletedGameAnalyzer) -> dict:
        analyzer = analyzer_factory(repository, engine_manager=self.manager)
        return analyzer.analyze(
            game_id, engine_path, options, active_human_game_ids=active_human_game_ids,
            progress=progress, stop_event=stop_event, safety_check=safety_check)

    def evaluate_position(self, engine_path: str | Path, board: chess.Board,
                          stop_event: Event, active_result: dict, *,
                          safety_check: Callable[[], bool], on_update=None,
                          evaluator=evaluate_position) -> dict:
        kwargs = {"engine_manager": self.manager, "safety_check": safety_check}
        if on_update is not None:
            kwargs["on_update"] = on_update
        return evaluator(str(engine_path), board, stop_event, active_result, **kwargs)

    @staticmethod
    def playing_snapshot_allows_analysis(games, *, active_human_session: bool = False):
        return playing_snapshot_allows_analysis(
            games, active_human_session=active_human_session)

    @staticmethod
    def refresh_fair_play(service, token, username, is_current, *,
                          active_human_session: bool = False) -> bool:
        return refresh_playing_snapshot(
            service, token, username, is_current,
            active_human_session=active_human_session)
