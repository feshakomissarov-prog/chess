"""Framework-independent Chess Coach domain contracts."""

from .models import (
    AnalysisScore,
    AnalysisSource,
    ClockSnapshot,
    GameRef,
    GameSnapshot,
    JobContext,
    ScoreKind,
    SourceCoverage,
)

__all__ = [
    "AnalysisScore", "AnalysisSource", "ClockSnapshot", "GameRef",
    "GameSnapshot", "JobContext", "ScoreKind", "SourceCoverage",
]
