"""Read-only, aggregate-only audit of locally saved Lichess analysis coverage.

The caller must supply a connection to an already opened SQLite snapshot (or a
test database). This module never opens a path, runs migrations, contacts
Lichess, or returns game/profile identifiers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any

from ..domain.accuracy import FORMULA_ID
from ..lichess.analysis_json import ANALYTICS_SCHEMA_VERSION


AUDIT_VERSION = "lichess-coverage-audit-v1"
_COMPLETED = "g.status NOT IN ('created','started','started_soon','playing')"
_ACTIVE = "('created','started','started_soon','playing')"
_SOURCES = "('stockfish','lichess-pgn','lichess-json')"


def _rows(db: sqlite3.Connection, query: str, parameters: tuple[Any, ...] = ()):
    return db.execute(query, parameters).fetchall()


def _scope(db: sqlite3.Connection, account_id: str, cutoff: str | None) -> dict[str, Any]:
    date_filter = "" if cutoff is None else " AND COALESCE(g.started_at,'')>=?"
    parameters: tuple[Any, ...] = (account_id,) if cutoff is None else (account_id, cutoff)
    scoped = f"""
        SELECT DISTINCT g.game_id, g.line_revision, g.source, g.source_game_id,
               g.started_at, ag.remote_id, ag.color,
               g.lichess_accuracy_white, g.lichess_accuracy_black
        FROM games g JOIN account_games ag ON ag.game_id=g.game_id
        WHERE ag.account_id=? AND {_COMPLETED}{date_filter}
    """
    games = _rows(db, scoped, parameters)
    game_ids = {row["game_id"] for row in games}
    total = len(game_ids)
    dates = [str(row["started_at"]) for row in games if row["started_at"]]
    remote_ids = sum(bool(str(row["source_game_id"] or row["remote_id"] or "").strip())
                     and (row["source"] in {"lichess", "lichess_archive"}
                          or bool(str(row["source_game_id"] or "").strip()))
                     for row in games)

    run_query = f"""
        WITH scoped AS ({scoped}), runs AS (
            SELECT ar.game_id, ar.source, ar.status, ar.run_id,
                   COUNT(DISTINCT CASE WHEN pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL
                                       THEN pa.ply END) AS scored,
                   (SELECT COUNT(*) FROM game_positions gp
                    WHERE gp.game_id=ar.game_id AND gp.line_revision=ar.line_revision) AS total
            FROM analysis_runs ar
            JOIN scoped s ON s.game_id=ar.game_id AND s.line_revision=ar.line_revision
            JOIN game_revisions gr ON gr.game_id=ar.game_id
                AND gr.line_revision=ar.line_revision AND gr.is_current=1
                AND gr.line_hash=ar.line_hash
            LEFT JOIN position_assessments pa ON pa.run_id=ar.run_id
            WHERE ar.source IN {_SOURCES} AND ar.status IN ('complete','partial')
              AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj
                              WHERE aj.result_run_id=ar.run_id AND aj.analysis_pass='review')
            GROUP BY ar.run_id
        ), game_coverage AS (
            SELECT game_id,
                   MAX(scored>0) AS any_scores,
                   MAX(scored>0 AND status='complete' AND scored>=total) AS complete_scores,
                   MAX(scored>0 AND source IN ('lichess-pgn','lichess-json')) AS lichess_scores,
                   MAX(scored>0 AND source='stockfish') AS stockfish_scores
            FROM runs GROUP BY game_id
        )
        SELECT game_id, any_scores, complete_scores, lichess_scores, stockfish_scores
        FROM game_coverage
    """
    score_rows = _rows(db, run_query, parameters)
    by_game = {row["game_id"]: row for row in score_rows}
    scored_games = {game_id for game_id, row in by_game.items() if row["any_scores"]}
    lichess_games = {game_id for game_id, row in by_game.items() if row["lichess_scores"]}
    stockfish_games = {game_id for game_id, row in by_game.items() if row["stockfish_scores"]}
    complete_games = {game_id for game_id, row in by_game.items() if row["complete_scores"]}

    official = {}
    for row in games:
        game_id = row["game_id"]
        side = row["color"]
        if side not in {"white", "black"}:
            official[game_id] = (None, None, "unknown_side")
            continue
        own = row[f"lichess_accuracy_{side}"]
        opposite = row["lichess_accuracy_black" if side == "white" else "lichess_accuracy_white"]
        is_lichess = (row["source"] in {"lichess", "lichess_archive"}
                      or bool(str(row["source_game_id"] or "").strip()))
        own_valid = (float(own) if is_lichess and own is not None
                     and 0 <= float(own) <= 100 else None)
        opposite_valid = (opposite is not None and 0 <= float(opposite) <= 100)
        own_invalid = is_lichess and own is not None and not 0 <= float(own) <= 100
        official[game_id] = (own_valid, opposite_valid, "invalid" if own_invalid else "ok")

    local_query = f"""
        WITH scoped AS ({scoped}), candidate AS (
            SELECT DISTINCT s.game_id, ar.source,
                CASE s.color WHEN 'white' THEN ac.white_accuracy
                             WHEN 'black' THEN ac.black_accuracy END AS accuracy
            FROM scoped s
            JOIN analysis_runs ar ON ar.game_id=s.game_id AND ar.line_revision=s.line_revision
                AND ar.source IN ('stockfish','lichess-json','lichess-pgn')
                AND ar.status='complete'
            JOIN game_revisions gr ON gr.game_id=ar.game_id
                AND gr.line_revision=ar.line_revision AND gr.is_current=1
                AND gr.line_hash=ar.line_hash
            JOIN analysis_game_accuracy ac ON ac.run_id=ar.run_id
                AND ac.formula_id=? AND ac.run_finished_at IS ar.finished_at
            WHERE NOT EXISTS (SELECT 1 FROM analysis_jobs aj
                              WHERE aj.result_run_id=ar.run_id AND aj.analysis_pass='review')
        )
        SELECT game_id,
               MAX(source='stockfish' AND accuracy BETWEEN 0 AND 100) AS stockfish,
               MAX(source<>'stockfish' AND accuracy BETWEEN 0 AND 100) AS server_based
        FROM candidate GROUP BY game_id
    """
    local_rows = _rows(db, local_query, (*parameters, FORMULA_ID))
    local = {row["game_id"]: row for row in local_rows}
    official_count = sum(value[0] is not None for value in official.values())
    opposite_only = sum(value[0] is None and bool(value[1]) for value in official.values())
    invalid_official = sum(value[2] == "invalid" for value in official.values())
    unknown_side = sum(value[2] == "unknown_side" for value in official.values())
    stockfish_accuracy = {key for key, row in local.items() if row["stockfish"]}
    server_accuracy = {key for key, row in local.items() if row["server_based"]}
    combined = sum(
        official.get(game_id, (None, None, "ok"))[0] is not None
        or game_id in stockfish_accuracy or game_id in server_accuracy
        for game_id in game_ids
    )

    return {
        "completed_games": total,
        "date_coverage": {"first_started_at": min(dates) if dates else None,
                          "last_started_at": max(dates) if dates else None},
        "valid_lichess_game_ids": remote_ids,
        "position_scores": {
            "games_with_any": len(scored_games),
            "games_with_lichess": len(lichess_games),
            "games_with_stockfish": len(stockfish_games),
            "source_overlap": len(lichess_games & stockfish_games),
            "no_scores": max(0, total - len(scored_games)),
            "partial": sum(game_id in scored_games and game_id not in complete_games
                            for game_id in game_ids),
            "complete": len(complete_games),
        },
        "accuracy": {
            "official_lichess_player_side": official_count,
            "official_only_opposite_side": opposite_only,
            "official_invalid_or_out_of_range": invalid_official,
            "official_unknown_player_side": unknown_side,
            "local_stockfish": len(stockfish_accuracy),
            "local_server_based": len(server_accuracy),
            "combined_distinct": combined,
        },
    }


def _ready_backfill(db: sqlite3.Connection, account_id: str, utc_day: str) -> dict[str, Any]:
    row = _rows(db, """
        SELECT COUNT(*) AS total,
               SUM(accuracy_checked_at IS NOT NULL OR evals_checked_at IS NOT NULL) AS checked,
               SUM(accuracy_status IN ('has_data','partial') AND EXISTS (
                   SELECT 1 FROM games g JOIN account_games ag ON ag.game_id=g.game_id
                   WHERE g.game_id=lichess_analysis_backfill.game_id
                     AND ag.account_id=lichess_analysis_backfill.account_id
                     AND ((ag.color='white' AND g.lichess_accuracy_white BETWEEN 0 AND 100)
                       OR (ag.color='black' AND g.lichess_accuracy_black BETWEEN 0 AND 100)))) AS accuracy,
               SUM(evals_status IN ('has_data','partial') AND EXISTS (
                   SELECT 1 FROM analysis_runs ar JOIN position_assessments pa
                     ON pa.run_id=ar.run_id JOIN game_revisions gr
                     ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision
                        AND gr.is_current=1 AND gr.line_hash=ar.line_hash
                   WHERE ar.game_id=lichess_analysis_backfill.game_id
                     AND ar.line_revision=lichess_analysis_backfill.line_revision
                     AND gr.line_hash=lichess_analysis_backfill.line_hash
                     AND ar.source IN ('lichess-pgn','lichess-json')
                     AND ar.status IN ('complete','partial')
                     AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL))) AS evals,
               SUM(accuracy_status='no_server_data' AND evals_status='no_server_data') AS no_server_data,
               SUM(accuracy_status IN ('error','rate_limited','stale_line')
                   OR evals_status IN ('error','rate_limited','stale_line')) AS errors,
               SUM(accuracy_status IN ('queued','running') OR evals_status IN ('queued','running')) AS pending,
               COUNT(DISTINCT CASE WHEN
                   (accuracy_status IN ('no_server_data','partial','error','rate_limited','stale_line')
                    AND accuracy_next_retry_at IS NOT NULL) OR
                   (evals_status IN ('no_server_data','partial','error','rate_limited','stale_line')
                    AND evals_next_retry_at IS NOT NULL) THEN game_id END) AS retry_waiting,
               MAX(MAX(COALESCE(accuracy_checked_at,''),COALESCE(evals_checked_at,''))) AS last_checked_at,
               MIN(accuracy_next_retry_at) AS accuracy_retry_at,
               MIN(evals_next_retry_at) AS evals_retry_at
        FROM lichess_analysis_backfill WHERE account_id=?
    """, (account_id,))[0]
    budget_rows = _rows(db, "SELECT requests,paused,retry_after_at FROM "
                        "lichess_analysis_backfill_budget WHERE account_id=? AND utc_day=?",
                        (account_id, utc_day))
    budget = budget_rows[0] if budget_rows else None
    retry_candidates = [str(row[key]) for key in ("accuracy_retry_at", "evals_retry_at")
                        if row[key]]
    if budget and budget["retry_after_at"]:
        retry_candidates.append(str(budget["retry_after_at"]))
    return {
        "denominator": "rows in lichess_analysis_backfill for selected account; not all archived games",
        "total": int(row["total"] or 0), "checked": int(row["checked"] or 0),
        "accuracy": int(row["accuracy"] or 0), "evals": int(row["evals"] or 0),
        "no_server_data": int(row["no_server_data"] or 0),
        "pending": int(row["pending"] or 0), "errors": int(row["errors"] or 0),
        "retry_waiting": int(row["retry_waiting"] or 0),
        "requests_today": int(budget["requests"] or 0) if budget else 0,
        "paused": bool(budget["paused"]) if budget else False,
        "retry_at": min(retry_candidates) if retry_candidates else None,
        "last_checked_at": str(row["last_checked_at"]) if row["last_checked_at"] else None,
    }


def audit_lichess_coverage(db: sqlite3.Connection, account_id: str, *,
                           snapshot_time: datetime | None = None) -> dict[str, Any]:
    """Return aggregate Lichess coverage for one account without writing data.

    Use a read-only connection to a stable snapshot. This function rejects an
    already active transaction, enables SQLite ``query_only``, and leaves that
    safety flag enabled. It intentionally returns neither account nor game IDs.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("A selected account_id is required")
    if db.in_transaction:
        raise ValueError("The audit requires a connection with no active transaction")
    db.execute("PRAGMA query_only=ON")
    now = snapshot_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("snapshot_time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    snapshot_label = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
    try:
        db.execute("BEGIN")
        all_time = _scope(db, account_id.strip(), None)
        last_90d = _scope(db, account_id.strip(),
                          (now - timedelta(days=90)).date().isoformat())
        ready = _ready_backfill(db, account_id.strip(), now.date().isoformat())
        db.rollback()
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    return {
        "scope": "selected account · completed games · local SQLite snapshot",
        "snapshot_utc": snapshot_label,
        "audit_version": AUDIT_VERSION,
        "schema_user_version": schema_version,
        "analytics_schema_version": ANALYTICS_SCHEMA_VERSION,
        "data_source": "local saved analysis rows; no Lichess request or engine run",
        "all_time": all_time,
        "last_90d": last_90d,
        "ready_backfill": ready,
    }
