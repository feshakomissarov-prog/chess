"""Read-only, denominator-aware aggregates over current game revisions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .repository import GameRepository


class StatisticsService:
    def __init__(self, repository: GameRepository) -> None:
        self.repository = repository
        self._cache: dict[tuple, Any] = {}
        self._cache_revision = repository.statistics_revision

    def _get_cached(self, key: tuple) -> Any | None:
        revision = self.repository.statistics_revision
        if revision != self._cache_revision:
            self._cache.clear()
            self._cache_revision = revision
        value = self._cache.get(key)
        return deepcopy(value) if value is not None else None

    def _store_cached(self, key: tuple, value: Any) -> Any:
        self._cache[key] = deepcopy(value)
        return value

    def summary(self, *, account_id: str | None = None, since: str | None = None,
                until: str | None = None, opponent: str | None = None,
                clock: str | None = None, game_source: str | None = None,
                analysis_source: str | None = None, side: str | None = None,
                rating_min: int | None = None, rating_max: int | None = None,
                opening: str | None = None, result: str | None = None,
                analyzed: str | None = None) -> dict[str, Any]:
        cache_key = ("summary", account_id, since, until, opponent, clock, game_source,
                     analysis_source, side, rating_min, rating_max, opening, result, analyzed)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        clauses: list[str] = ["g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = []
        join = ""
        select = ("g.game_id,g.status,g.winner,g.color,g.pgn,g.started_at,g.updated_at,"
                  "g.white_rating,g.black_rating,g.lichess_accuracy_white,"
                  "g.lichess_accuracy_black,g.opening_eco,g.opening_name")
        if account_id:
            join = " JOIN account_games ag ON ag.game_id=g.game_id "
            select = ("g.game_id,g.status,g.winner,g.color,g.pgn,g.started_at,g.updated_at,"
                      "g.white_rating,g.black_rating,g.lichess_accuracy_white,"
                      "g.lichess_accuracy_black,g.opening_eco,g.opening_name,"
                      "ag.color AS account_color,ag.player_rating AS account_player_rating,"
                      "ag.opponent AS account_opponent,ag.player_name AS account_player_name")
            clauses.append("ag.account_id=?")
            params.append(account_id)
        if since:
            clauses.append("COALESCE(g.started_at,g.updated_at)>=?")
            params.append(since)
        if until:
            clauses.append("COALESCE(g.started_at,g.updated_at)<?")
            params.append(until)
        if opponent:
            clauses.append(("ag.opponent" if account_id else "g.opponent") + " LIKE ?")
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
        color_expr = "ag.color" if account_id else "g.color"
        if result == "win":
            clauses.append(f"g.winner={color_expr}")
        elif result == "loss":
            clauses.append(f"g.winner IS NOT NULL AND g.winner<>{color_expr} AND "
                           "g.status NOT IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        elif result == "draw":
            clauses.append("(g.status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "OR g.pgn LIKE '%[Result \"1/2-1/2\"]%')")
        elif result == "unknown":
            clauses.append("g.winner IS NULL AND g.status NOT IN "
                           "('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        if analyzed in {"yes", "no"}:
            exists = ("EXISTS (SELECT 1 FROM analysis_runs ar JOIN game_revisions gr "
                      "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                      "WHERE ar.game_id=g.game_id AND gr.is_current=1 AND ar.status='complete' "
                      + ("AND ar.source=? " if analysis_source else "") + ")")
            if analyzed == "yes":
                clauses.append(exists)
            else:
                clauses.append(f"NOT {exists}")
            if analysis_source:
                params.append(analysis_source)
        where = " AND ".join(clauses)
        with self.repository._connection() as db:
            games = [dict(row) for row in db.execute(
                f"SELECT DISTINCT {select} FROM games g{join} WHERE {where}", params)]
            ids = [game["game_id"] for game in games]
            result = {"filters": {"since": since, "until": until, "opponent": opponent,
                                  "clock": clock, "game_source": game_source,
                                  "analysis_source": analysis_source, "side": side,
                                  "opening": opening, "result": result, "analyzed": analyzed},
                      "account_scope": "selected account" if account_id else
                                       "mixed accounts / local color fallback",
                      "games": len(games), "wins": 0, "losses": 0, "draws": 0,
                      "unknown_or_aborted": 0,
                      "confirmed_moves": 0, "evaluated_moves": 0,
                      "move_coverage_percent": None, "own_assessed_moves": 0,
                      "analysis_games": 0,
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
                return self._store_cached(cache_key, result)
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
            analysis_games = db.execute(
                f"SELECT COUNT(DISTINCT ar.game_id) FROM analysis_runs ar "
                f"JOIN game_revisions gr ON gr.game_id=ar.game_id "
                f"AND gr.line_revision=ar.line_revision WHERE ar.game_id IN ({marks}) "
                "AND gr.is_current=1 AND ar.status='complete' "
                + ("AND ar.source=?" if analysis_source else ""),
                [*ids, *source_params]).fetchone()[0]
            result["analysis_games"] = int(analysis_games)
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
            result["analysis_coverage"] = round(100 * result["analysis_games"] / len(games), 1) if games else 0.0
            for side in ("white", "black"):
                accuracies = [g[f"lichess_accuracy_{side}"] for g in games
                              if g[f"lichess_accuracy_{side}"] is not None]
                result["lichess_accuracy"][side] = round(sum(accuracies) / len(accuracies), 1) if accuracies else None
            return self._store_cached(cache_key, result)

    def filter_options(self) -> dict[str, list[dict[str, str]]]:
        """Small selector data sets, queried only when the statistics screen opens."""
        with self.repository._connection() as db:
            accounts = [dict(row) for row in db.execute(
                "SELECT account_id,username FROM accounts ORDER BY username COLLATE NOCASE")]
            clocks = [str(row[0]) for row in db.execute(
                "SELECT DISTINCT clock FROM games WHERE clock IS NOT NULL AND clock<>'' "
                "ORDER BY clock LIMIT 30")]
        return {"accounts": accounts, "clocks": clocks}

    def game_page(self, *, account_id: str | None = None, since: str | None = None,
                  until: str | None = None, opponent: str | None = None,
                  clock: str | None = None, game_source: str | None = None,
                  side: str | None = None,
                  opening: str | None = None, result: str | None = None,
                  analyzed: str | None = None, analysis_source: str | None = None,
                  page: int = 0, page_size: int = 100) -> dict[str, Any]:
        """Return a bounded party list using the same active filter dimensions as summary."""
        clauses = ["g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = []
        join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
        if account_id:
            clauses.append("ag.account_id=?")
            params.append(account_id)
        if since:
            clauses.append("COALESCE(g.started_at,g.updated_at)>=?")
            params.append(since)
        if until:
            clauses.append("COALESCE(g.started_at,g.updated_at)<?")
            params.append(until)
        opponent_expr = "COALESCE(ag.opponent,g.opponent)" if account_id else "g.opponent"
        if opponent:
            clauses.append(f"{opponent_expr} LIKE ?")
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
        if opening:
            clauses.append("(g.opening_name LIKE ? OR g.opening_eco LIKE ?)")
            params.extend([f"%{opening}%", f"%{opening}%"])
        color_expr = "ag.color" if account_id else "g.color"
        if result == "win":
            clauses.append(f"g.winner={color_expr}")
        elif result == "loss":
            clauses.append(f"g.winner IS NOT NULL AND g.winner<>{color_expr} AND "
                           "g.status NOT IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        elif result == "draw":
            clauses.append("(g.status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "OR g.pgn LIKE '%[Result \"1/2-1/2\"]%')")
        elif result == "unknown":
            clauses.append("g.winner IS NULL AND g.status NOT IN "
                           "('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        if analyzed in {"yes", "no"}:
            exists = ("EXISTS (SELECT 1 FROM analysis_runs ar JOIN game_revisions gr "
                      "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                      "WHERE ar.game_id=g.game_id AND gr.is_current=1 AND ar.status='complete' "
                      + ("AND ar.source=? " if analysis_source else "") + ")")
            clauses.append(exists if analyzed == "yes" else f"NOT {exists}")
            if analysis_source:
                params.append(analysis_source)
        where = " AND ".join(clauses)
        page = max(0, int(page))
        page_size = max(1, min(200, int(page_size)))
        cache_key = ("page", account_id, since, until, opponent, clock, game_source,
                     side, opening, result, analyzed, analysis_source, page, page_size)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        selected = ("g.game_id,g.status,g.winner,g.color,g.pgn,g.started_at,g.updated_at,"
                    "g.white_player,g.black_player,g.opponent,g.clock,g.source,g.opening_eco,"
                    "g.opening_name,g.lichess_accuracy_white,g.lichess_accuracy_black")
        if account_id:
            selected += ",ag.opponent AS account_opponent,ag.player_name AS account_player_name,ag.color AS account_color"
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            total = db.execute(f"SELECT COUNT(DISTINCT g.game_id) FROM games g{join} WHERE {where}",
                               params).fetchone()[0]
            rows = db.execute(
                f"SELECT DISTINCT {selected} FROM games g{join} WHERE {where} "
                "ORDER BY COALESCE(g.started_at,g.updated_at) DESC,g.game_id LIMIT ? OFFSET ?",
                [*params, page_size, page * page_size]).fetchall()
        return self._store_cached(cache_key, {"items": [dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size})
