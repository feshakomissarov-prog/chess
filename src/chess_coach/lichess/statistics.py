"""Read-only, denominator-aware aggregates over current game revisions."""

from __future__ import annotations

from typing import Any

from .repository import GameRepository


class StatisticsService:
    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository

    def summary(self, *, account_id: str | None = None, since: str | None = None,
                until: str | None = None, opponent: str | None = None,
                clock: str | None = None, game_source: str | None = None,
                analysis_source: str | None = None, side: str | None = None,
                rating_min: int | None = None, rating_max: int | None = None,
                opening: str | None = None) -> dict[str, Any]:
        clauses: list[str] = ["g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = []
        join = ""
        select = "g.*"
        if account_id:
            join = " JOIN account_games ag ON ag.game_id=g.game_id "
            select = "g.*,ag.color AS account_color,ag.player_rating AS account_player_rating"
            clauses.append("ag.account_id=?")
            params.append(account_id)
        if since:
            clauses.append("COALESCE(g.started_at,g.updated_at)>=?")
            params.append(since)
        if until:
            clauses.append("COALESCE(g.started_at,g.updated_at)<?")
            params.append(until)
        if opponent:
            clauses.append("g.opponent LIKE ?")
            params.append(f"%{opponent}%")
        if clock:
            clauses.append("g.clock=?")
            params.append(clock)
        if game_source:
            clauses.append("g.source=?")
            params.append(game_source)
        if side in {"white", "black"}:
            clauses.append(("ag.color" if account_id else "g.color") + "=?")
            params.append(side)
        if rating_min is not None:
            clauses.append(("ag.player_rating" if account_id else
                            "CASE g.color WHEN 'white' THEN g.white_rating ELSE g.black_rating END")+">=?")
            params.append(rating_min)
        if rating_max is not None:
            clauses.append(("ag.player_rating" if account_id else
                            "CASE g.color WHEN 'white' THEN g.white_rating ELSE g.black_rating END")+"<=?")
            params.append(rating_max)
        if opening:
            clauses.append("(g.opening_name LIKE ? OR g.opening_eco LIKE ?)")
            params.extend([f"%{opening}%", f"%{opening}%"])
        where = " AND ".join(clauses)
        with self.repository._connection() as db:
            games = [dict(row) for row in db.execute(
                f"SELECT DISTINCT {select} FROM games g{join} WHERE {where}", params)]
            ids = [game["game_id"] for game in games]
            result = {"filters": {"since": since, "until": until, "opponent": opponent,
                                   "clock": clock, "game_source": game_source,
                                   "analysis_source": analysis_source},
                      "account_scope": "selected account" if account_id else
                                       "mixed accounts / local color fallback",
                      "games": len(games), "wins": 0, "losses": 0, "draws": 0,
                      "unknown_or_aborted": 0,
                      "confirmed_moves": 0, "evaluated_moves": 0,
                      "move_coverage_percent": None, "own_assessed_moves": 0,
                      "own_error_rate_per_100": None, "avg_cpl_cp": None,
                      "swings_200cp": 0, "lichess_accuracy": {"white": None, "black": None},
                      "analysis_coverage": 0.0, "classifications_by_phase": {}}
            result["rating_trend"] = []
            for game in games:
                me = game.get("account_color") or game["color"]
                draw_status = game["status"] in {
                    "draw", "stalemate", "repetition", "50moves", "insufficientMaterial",
                } or '[Result "1/2-1/2"]' in game.get("pgn", "")
                aborted = game["status"] in {"aborted", "noStart", "unknownFinish", "cheat"}
                if draw_status:
                    result["draws"] += 1
                elif game["winner"] == me:
                    result["wins"] += 1
                elif game["winner"]:
                    result["losses"] += 1
                else:
                    result["unknown_or_aborted"] += 1
                rating = (game.get("account_player_rating") if account_id else
                          game["white_rating"] if me == "white" else game["black_rating"])
                if rating is not None:
                    result["rating_trend"].append({
                        "date": game["started_at"] or game["updated_at"],
                        "rating": rating, "game_id": game["game_id"],
                    })
            result["rating_trend"].sort(key=lambda item: item["date"])
            if not ids:
                return result
            marks = ",".join("?" for _ in ids)
            account_join = " LEFT JOIN account_games ag ON ag.game_id=g.game_id AND ag.account_id=?" if account_id else ""
            account_color_expr = "COALESCE(ag.color,g.color)" if account_id else "g.color"
            move_rows = db.execute(
                f"SELECT gm.game_id,gm.ply,gm.mover,g.color FROM game_moves gm "
                f"JOIN game_revisions gr ON gr.game_id=gm.game_id AND gr.line_revision=gm.line_revision "
                f"JOIN games g ON g.game_id=gm.game_id WHERE gm.game_id IN ({marks}) AND gr.is_current=1",
                ids).fetchall()
            result["confirmed_moves"] = len(move_rows)
            latest_source_clause = "AND ar.source=?" if analysis_source else ""
            source_params = [analysis_source] if analysis_source else []
            classifications = db.execute(
                f"""WITH latest AS (
                    SELECT ar.game_id,ar.line_revision,ar.run_id,
                           ROW_NUMBER() OVER (PARTITION BY ar.game_id,ar.line_revision
                                              ORDER BY ar.finished_at DESC,ar.run_id DESC) AS rank
                    FROM analysis_runs ar JOIN games g ON g.game_id=ar.game_id
                    JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision
                    WHERE g.game_id IN ({marks}) AND gr.is_current=1 AND ar.status='complete'
                    {latest_source_clause}
                )
                SELECT mc.*,{account_color_expr} AS my_color FROM latest l
                JOIN analysis_runs ar ON ar.run_id=l.run_id AND l.rank=1
                JOIN move_classifications mc ON mc.run_id=ar.run_id
                JOIN games g ON g.game_id=mc.game_id{account_join}
                WHERE mc.ply>0 ORDER BY mc.game_id,mc.ply""",
                [*ids, *source_params, *([account_id] if account_id else [])]).fetchall()
            assessed = [row for row in classifications if row["eval_loss_cp"] is not None]
            evaluated = db.execute(
                f"""WITH latest AS (
                    SELECT ar.game_id,ar.line_revision,ar.run_id,
                           ROW_NUMBER() OVER (PARTITION BY ar.game_id,ar.line_revision
                                              ORDER BY ar.finished_at DESC,ar.run_id DESC) AS rank
                    FROM analysis_runs ar JOIN games g ON g.game_id=ar.game_id
                    JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision
                    WHERE g.game_id IN ({marks}) AND gr.is_current=1 AND ar.status='complete'
                    {latest_source_clause}
                )
                SELECT DISTINCT pa.game_id,pa.ply FROM latest l
                JOIN analysis_runs ar ON ar.run_id=l.run_id AND l.rank=1
                JOIN position_assessments pa ON pa.run_id=ar.run_id
                WHERE pa.ply>0 AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)""",
                [*ids, *source_params]).fetchall()
            evaluated_ids = {(row["game_id"], row["ply"]) for row in evaluated}
            result["evaluated_moves"] = len(evaluated_ids)
            if result["confirmed_moves"]:
                result["move_coverage_percent"] = round(
                    100 * len(evaluated_ids) / result["confirmed_moves"], 1)
            own = [row for row in assessed if row["mover"] == row["my_color"]]
            result["own_assessed_moves"] = len(own)
            if own:
                errors = sum(row["label"] in {"mistake", "blunder"} for row in own)
                result["own_error_rate_per_100"] = round(100 * errors / len(own), 1)
                result["avg_cpl_cp"] = round(sum(row["eval_loss_cp"] for row in own) / len(own), 1)
                result["swings_200cp"] = sum(row["eval_loss_cp"] >= 200 for row in own)
            for row in classifications:
                phase = row["phase"] or "unknown"
                counts = result["classifications_by_phase"].setdefault(phase, {})
                side = "mine" if row["mover"] == row["my_color"] else "opponent"
                side_counts = counts.setdefault(side, {})
                side_counts[row["label"]] = side_counts.get(row["label"], 0) + 1
            result["analysis_coverage"] = round(100 * len({game_id for game_id, _ in evaluated_ids}) /
                                                len(games), 1) if games else 0.0
            for side in ("white", "black"):
                accuracies = [g[f"lichess_accuracy_{side}"] for g in games
                              if g[f"lichess_accuracy_{side}"] is not None]
                result["lichess_accuracy"][side] = round(sum(accuracies) / len(accuracies), 1) if accuracies else None
            return result
