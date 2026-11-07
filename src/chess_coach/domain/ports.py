"""Narrow application-facing ports; concrete adapters live outside the domain."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from .models import AnalysisScore, GameRef, GameSnapshot


@runtime_checkable
class Cancellation(Protocol):
    def is_set(self) -> bool: ...


@runtime_checkable
class GameStore(Protocol):
    def get_game(self, game_id: str) -> dict[str, Any] | None: ...
    def current_line(self, game_id: str) -> dict[str, Any] | None: ...
    def save_snapshot(self, game_id: str, initial_fen: str, moves: str, *args: Any,
                      **kwargs: Any) -> Any: ...
    def validate_game_ref(self, ref: GameRef) -> bool: ...


@runtime_checkable
class AnalysisEngine(Protocol):
    def analyze(self, snapshot: GameSnapshot, *, nodes: int,
                cancellation: Cancellation) -> Sequence[AnalysisScore]: ...
    def stop(self, reason: str) -> None: ...


@runtime_checkable
class GameFeed(Protocol):
    def send_move(self, game_id: str, uci: str, request_id: str | None = None) -> Any: ...
    def is_auth_generation_current(self, generation: int) -> bool: ...


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: str, *, operation_id: str, reason: str | None = None,
             fields: dict[str, Any] | None = None) -> None: ...


@runtime_checkable
class SnapshotProvider(Protocol):
    def read_game_snapshot(self, ref: GameRef) -> GameSnapshot | None: ...


Progress = Callable[[int, int], None]
