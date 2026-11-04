"""Shared SQL contract for a game having usable, current-line analysis scores."""

from __future__ import annotations

import re


def analysis_run_source_filter(run_alias: str = "ar",
                               source: str | None = None) -> tuple[str, list[str]]:
    """Build a safely parameterized provenance filter for analysis runs.

    The UI source ``lichess`` deliberately represents the union of the two
    physical Lichess sources while preserving those physical values in storage.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", run_alias):
        raise ValueError("Invalid SQL analysis-run alias")
    if source is None:
        return "", []
    if source == "lichess":
        return f"{run_alias}.source IN (?,?)", ["lichess-pgn", "lichess-json"]
    return f"{run_alias}.source=?", [str(source)]


def analysis_run_has_position_scores(run_alias: str = "ar") -> str:
    """SQL predicate for a run containing at least one usable CP/mate score."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", run_alias):
        raise ValueError("Invalid SQL analysis-run alias")
    return ("EXISTS (SELECT 1 FROM position_assessments scored_pa "
            f"WHERE scored_pa.run_id={run_alias}.run_id "
            "AND (scored_pa.white_cp IS NOT NULL OR scored_pa.mate_white IS NOT NULL))")


def current_scored_analysis_exists(game_alias: str = "g", source: str | None = None
                                    ) -> tuple[str, list[str]]:
    """Correlated SQL predicate: current revision has at least one CP/mate score.

    Move classifications and ready-analysis metadata are deliberately not enough
    to count a game as analyzed. ``source`` is restricted to known source labels.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", game_alias):
        raise ValueError("Invalid SQL game alias")
    source_clause, source_params = analysis_run_source_filter("ar", source)
    extra = f" AND ({source_clause})" if source_clause else ""
    predicate = (
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
    return predicate, source_params
