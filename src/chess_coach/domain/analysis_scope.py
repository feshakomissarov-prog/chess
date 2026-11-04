"""Shared SQL contract for a game having usable, current-line analysis scores."""

from __future__ import annotations

import re


_SOURCES = {
    "stockfish": "ar.source='stockfish'",
    "lichess": "ar.source IN ('lichess-pgn','lichess-json')",
    "lichess-pgn": "ar.source='lichess-pgn'",
    "lichess-json": "ar.source='lichess-json'",
}


def current_scored_analysis_exists(game_alias: str = "g", source: str | None = None) -> str:
    """Correlated SQL predicate: current revision has at least one CP/mate score.

    Move classifications and ready-analysis metadata are deliberately not enough
    to count a game as analyzed. ``source`` is restricted to known source labels.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", game_alias):
        raise ValueError("Invalid SQL game alias")
    source_clause = _SOURCES.get(str(source)) if source is not None else None
    if source is not None and source_clause is None:
        raise ValueError(f"Unsupported analysis source: {source}")
    extra = f" AND {source_clause}" if source_clause else ""
    return (
        "EXISTS (SELECT 1 FROM analysis_runs ar "
        "JOIN game_revisions gr ON gr.game_id=ar.game_id "
        "AND gr.line_revision=ar.line_revision AND gr.is_current=1 "
        "JOIN position_assessments pa ON pa.run_id=ar.run_id "
        f"WHERE ar.game_id={game_alias}.game_id "
        f"AND ar.line_revision={game_alias}.line_revision "
        "AND ar.status IN ('complete','partial') "
        "AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj "
        "WHERE aj.result_run_id=ar.run_id AND aj.analysis_pass='review') "
        "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)" + extra + ")"
    )
