"""Immutable value objects shared across UI, application and infrastructure edges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class GameRef:
    account_id: str
    game_id: str
    line_revision: int
    line_hash: str

    def __post_init__(self) -> None:
        if not self.account_id or not self.game_id or not self.line_hash:
            raise ValueError("GameRef requires account, game and line identity")
        if self.line_revision < 1:
            raise ValueError("line_revision must be positive")


@dataclass(frozen=True, slots=True)
class GameSnapshot:
    ref: GameRef
    source: str
    status: str
    fen: str
    moves_uci: tuple[str, ...]
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class ClockSnapshot:
    white_ms: int | None
    black_ms: int | None
    active_color: bool | None
    increment_ms: int = 0
    sampled_at_monotonic: float | None = None

    def __post_init__(self) -> None:
        for value in (self.white_ms, self.black_ms):
            if value is not None and value < 0:
                raise ValueError("clock values cannot be negative")
        if self.increment_ms < 0:
            raise ValueError("increment cannot be negative")


class ScoreKind(StrEnum):
    CP = "cp"
    MATE = "mate"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class AnalysisScore:
    kind: ScoreKind
    value: int | None
    perspective: str = "white"

    def __post_init__(self) -> None:
        if self.perspective not in {"white", "black"}:
            raise ValueError("perspective must be white or black")
        if self.kind is ScoreKind.MISSING:
            if self.value is not None:
                raise ValueError("missing score must not carry a value")
        elif self.value is None:
            raise ValueError("cp and mate scores require a value")

    @classmethod
    def cp(cls, centipawns: int, perspective: str = "white") -> AnalysisScore:
        return cls(ScoreKind.CP, int(centipawns), perspective)

    @classmethod
    def mate(cls, moves: int, perspective: str = "white") -> AnalysisScore:
        return cls(ScoreKind.MATE, int(moves), perspective)

    @classmethod
    def missing(cls, perspective: str = "white") -> AnalysisScore:
        return cls(ScoreKind.MISSING, None, perspective)


class AnalysisSource(StrEnum):
    LICHESS = "lichess"
    STOCKFISH = "stockfish"
    USER = "user"


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source: AnalysisSource
    version: str
    covered: int
    total: int
    captured_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("source version is required")
        if self.covered < 0 or self.total < 0 or self.covered > self.total:
            raise ValueError("coverage must satisfy 0 <= covered <= total")


@dataclass(frozen=True, slots=True)
class JobContext:
    operation_id: str
    kind: str
    priority: int
    account_id: str | None = None
    auth_generation: int | None = None
    game: GameRef | None = None

    def __post_init__(self) -> None:
        if not self.operation_id or not self.kind:
            raise ValueError("job identity and kind are required")
