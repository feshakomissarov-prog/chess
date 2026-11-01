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

    @staticmethod
    def _filter_sql(*, account_id: str | None, since: str | None, until: str | None,
                    opponent: str | None, clock: str | None, game_source: str | None,
                    analysis_source: str | None, side: str | None,
                    rating_min: int | None, rating_max: int | None,
                    opening: str | None, result: str | None,
                    analyzed: str | None) -> tuple[str, str, list[object]]:
        """Return one bounded SQL filter definition shared by summary and game list."""
        clauses = ["g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = []
        join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
        color = "ag.color" if account_id else "g.color"
        rating = ("ag.player_rating" if account_id else
                  "CASE g.color WHEN 'white' THEN g.white_rating ELSE g.black_rating END")
        if account_id:
            clauses.append("ag.account_id=?")
            params.append(account_id)
        if since:
            clauses.append("COALESCE(g.started_at,g.updated_at)>=?")
            params.append(since)
        if until:
            clauses.append("COALESCE(g.started_at,g.updated_at)<?")
            params.append(until)
        if opponent:
            clauses.append(("COALESCE(ag.opponent,g.opponent)" if account_id else "g.opponent") + " LIKE ?")
            params.append(f"%{opponent}%")
        if clock:
            clauses.append("g.clock=?")
            params.append(clock)
        if game_source:
            clauses.append("g.source=?")
            params.append(game_source)
        if side in {"white", "black"}:
            clauses.append(f"{color}=?")
            params.append(side)
        if rating_min is not None:
            clauses.append(f"{rating}>=?")
            params.append(rating_min)
        if rating_max is not None:
            clauses.append(f"{rating}<=?")
            params.append(rating_max)
        if opening:
            clauses.append("(g.opening_name LIKE ? OR g.opening_eco LIKE ? OR EXISTS ("
                           "SELECT 1 FROM opening_matches om WHERE om.game_id=g.game_id "
                           "AND om.line_revision=g.line_revision AND "
                           "(om.name LIKE ? OR om.eco LIKE ? OR om.family LIKE ?)))")
            params.extend([f"%{opening}%", f"%{opening}%", f"%{opening}%",
                           f"%{opening}%", f"%{opening}%"])
        if result == "win":
            clauses.append(f"g.winner={color}")
        elif result == "loss":
            clauses.append(f"g.winner IS NOT NULL AND g.winner<>{color} AND "
                           "g.status NOT IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        elif result == "draw":
            clauses.append("(g.status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "OR g.pgn LIKE '%[Result \"1/2-1/2\"]%')")
        elif result == "unknown":
            clauses.append("g.winner IS NULL AND g.status NOT IN "
                           "('draw','stalemate','repetition','50moves','insufficientMaterial') "
                           "AND g.pgn NOT LIKE '%[Result \"1/2-1/2\"]%'")
        analysis_exists = ("EXISTS (SELECT 1 FROM analysis_runs ar JOIN game_revisions gr "
                           "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                           "WHERE ar.game_id=g.game_id AND gr.is_current=1 "
                           "AND ar.status IN ('complete','partial') "
                           + ("AND ar.source=? " if analysis_source else "") + ")")
        if analyzed == "yes":
            clauses.append(analysis_exists)
            if analysis_source:
                params.append(analysis_source)
        elif analyzed == "no":
            clauses.append(f"NOT {analysis_exists}")
            if analysis_source:
                params.append(analysis_source)
        elif analysis_source:
            # A chosen source always constrains the game set, even when the GUI's
            # analyzed selector is set to its default "any" value.
            clauses.append(analysis_exists)
            params.append(analysis_source)
        return join, " AND ".join(clauses), params

    @staticmethod
    def _filtered_cte(join: str, where: str, account_id: str | None) -> str:
        color = "ag.color" if account_id else "g.color"
        rating = "ag.player_rating" if account_id else (
            "CASE g.color WHEN 'white' THEN g.white_rating ELSE g.black_rating END")
        return ("WITH filtered_games AS (SELECT DISTINCT g.game_id,g.status,g.winner,g.pgn,"
                "g.started_at,g.updated_at,g.white_rating,g.black_rating,"
                "g.lichess_accuracy_white,g.lichess_accuracy_black,g.source,g.clock,"
                f"{color} AS my_color,{rating} AS my_rating FROM games g{join} WHERE {where})")

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
        join, where, params = self._filter_sql(
            account_id=account_id, since=since, until=until, opponent=opponent, clock=clock,
            game_source=game_source, analysis_source=analysis_source, side=side,
            rating_min=rating_min, rating_max=rating_max, opening=opening,
            result=result, analyzed=analyzed)
        filtered = self._filtered_cte(join, where, account_id)
        latest = (", latest AS (SELECT ar.game_id,ar.line_revision,ar.run_id,"
                  "ROW_NUMBER() OVER (PARTITION BY ar.game_id,ar.line_revision "
                  "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,"
                  "ar.finished_at DESC,ar.run_id DESC) AS rank "
                  "FROM analysis_runs ar JOIN game_revisions gr "
                  "ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                  "JOIN filtered_games fg ON fg.game_id=ar.game_id "
                  "WHERE gr.is_current=1 AND ar.status IN ('complete','partial') "
                  + ("AND ar.source=? " if analysis_source else "") + ")")
        source_params = [analysis_source] if analysis_source else []
        selected = filtered + latest + ", chosen AS (SELECT * FROM latest WHERE rank=1)"
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            # Keep all aggregate reads on one SQLite snapshot while a background writer
            # may be importing more games.
            db.execute("BEGIN")
            base = db.execute(filtered + " SELECT COUNT(*) AS games,"
                "SUM(CASE WHEN status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "OR pgn LIKE '%[Result \"1/2-1/2\"]%' THEN 1 ELSE 0 END) AS draws,"
                "SUM(CASE WHEN NOT (status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "OR pgn LIKE '%[Result \"1/2-1/2\"]%') AND winner=my_color THEN 1 ELSE 0 END) AS wins,"
                "SUM(CASE WHEN NOT (status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "OR pgn LIKE '%[Result \"1/2-1/2\"]%') AND winner IS NOT NULL AND winner<>my_color "
                "THEN 1 ELSE 0 END) AS losses,"
                "SUM(CASE WHEN NOT (status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "OR pgn LIKE '%[Result \"1/2-1/2\"]%') AND winner IS NULL THEN 1 ELSE 0 END) AS unknown_count,"
                "AVG(lichess_accuracy_white) AS accuracy_white,"
                "AVG(lichess_accuracy_black) AS accuracy_black FROM filtered_games", params).fetchone()
            move_count = db.execute(filtered + " SELECT COUNT(*) FROM game_moves gm "
                "JOIN game_revisions gr ON gr.game_id=gm.game_id AND gr.line_revision=gm.line_revision "
                "JOIN filtered_games fg ON fg.game_id=gm.game_id WHERE gr.is_current=1", params).fetchone()[0]
            analysis_count = db.execute(selected + " SELECT COUNT(*) FROM chosen", [*params, *source_params]).fetchone()[0]
            evaluated_count = db.execute(selected + " SELECT COUNT(*) FROM chosen c "
                "JOIN position_assessments pa ON pa.run_id=c.run_id WHERE pa.ply>0 "
                "AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)",
                [*params, *source_params]).fetchone()[0]
            classification_sql = (selected + ", class_rows AS (SELECT mc.*,fg.my_color FROM chosen c "
                "JOIN move_classifications mc ON mc.run_id=c.run_id "
                "JOIN filtered_games fg ON fg.game_id=mc.game_id WHERE mc.ply>0) "
                "SELECT COUNT(CASE WHEN mover=my_color AND eval_loss_cp IS NOT NULL THEN 1 END) AS own_count,"
                "COUNT(CASE WHEN mover=my_color AND eval_loss_cp IS NOT NULL "
                "AND label IN ('mistake','blunder') THEN 1 END) AS own_errors,"
                "AVG(CASE WHEN mover=my_color AND eval_loss_cp IS NOT NULL THEN eval_loss_cp END) AS avg_cpl,"
                "COUNT(CASE WHEN mover=my_color AND eval_loss_cp>=200 THEN 1 END) AS swings "
                "FROM class_rows")
            own = db.execute(classification_sql, [*params, *source_params]).fetchone()
            phase_rows = db.execute(selected + " SELECT COALESCE(mc.phase,'unknown') AS phase,"
                "CASE WHEN mc.mover=fg.my_color THEN 'mine' ELSE 'opponent' END AS perspective,"
                "mc.label,COUNT(*) AS count FROM chosen c "
                "JOIN move_classifications mc ON mc.run_id=c.run_id "
                "JOIN filtered_games fg ON fg.game_id=mc.game_id WHERE mc.ply>0 "
                "GROUP BY COALESCE(mc.phase,'unknown'),perspective,mc.label",
                [*params, *source_params]).fetchall()
            trend = db.execute(filtered + " SELECT COALESCE(started_at,updated_at) AS date,"
                "my_rating AS rating,game_id FROM filtered_games WHERE my_rating IS NOT NULL "
                "ORDER BY date DESC,game_id DESC LIMIT 500", params).fetchall()
        games_count = int(base['games'] or 0)
        draws, wins, losses = (int(base[key] or 0) for key in ('draws','wins','losses'))
        unknown = int(base['unknown_count'] or 0)
        own_count = int(own['own_count'] or 0)
        phases: dict[str, dict[str, dict[str, int]]] = {}
        for row in phase_rows:
            phases.setdefault(row['phase'], {}).setdefault(row['perspective'], {})[row['label']] = int(row['count'])
        rating_trend = [dict(row) for row in reversed(trend)]
        stats = {"filters": {"since": since, "until": until, "opponent": opponent,
                              "clock": clock, "game_source": game_source,
                              "analysis_source": analysis_source, "side": side,
                              "opening": opening, "result": result, "analyzed": analyzed},
                 "account_scope": "selected account" if account_id else "mixed accounts / local color fallback",
                 "games": games_count, "wins": wins, "losses": losses, "draws": draws,
                 "unknown_or_aborted": unknown, "confirmed_moves": int(move_count),
                 "evaluated_moves": int(evaluated_count),
                 "move_coverage_percent": round(100 * evaluated_count / move_count, 1) if move_count else None,
                 "own_assessed_moves": own_count,
                 "analysis_games": int(analysis_count),
                 "own_error_rate_per_100": round(100 * int(own['own_errors'] or 0) / own_count, 1) if own_count else None,
                 "avg_cpl_cp": round(float(own['avg_cpl']), 1) if own['avg_cpl'] is not None else None,
                 "swings_200cp": int(own['swings'] or 0),
                 "lichess_accuracy": {
                     "white": round(float(base['accuracy_white']), 1) if base['accuracy_white'] is not None else None,
                     "black": round(float(base['accuracy_black']), 1) if base['accuracy_black'] is not None else None},
                 "analysis_coverage": round(100 * analysis_count / games_count, 1) if games_count else 0.0,
                 "classifications_by_phase": phases, "rating_trend": rating_trend}
        return self._store_cached(cache_key, stats)

    def filter_options(self) -> dict[str, list[dict[str, str]]]:
        """Small selector data sets, queried only when the statistics screen opens."""
        with self.repository._connection() as db:
            accounts = [dict(row) for row in db.execute(
                "SELECT account_id,username FROM accounts ORDER BY username COLLATE NOCASE")]
            clocks = [str(row[0]) for row in db.execute(
                "SELECT DISTINCT clock FROM games WHERE clock IS NOT NULL AND clock<>'' "
                "ORDER BY clock LIMIT 30")]
        return {"accounts": accounts, "clocks": clocks}

    def opening_statistics(self, *, account_id: str | None = None,
                           analysis_source: str | None = None,
                           since: str | None = None, until: str | None = None,
                           min_games: int = 1) -> list[dict[str, Any]]:
        """Opening and own-color error aggregates with explicit SQL provenance.

        This streams grouped rows from SQLite and does not materialize games or plies.
        ``mean_cpl_cp`` uses only classified, non-mate comparable own moves.
        """
        clauses = ["g.status NOT IN ('created','started','started_soon','playing')",
                   "g.variant='standard'", "g.line_revision=gr.line_revision",
                   "gr.is_current=1"]
        params: list[object] = []
        if account_id:
            clauses.append("ag.account_id=?")
            params.append(account_id)
        if since:
            clauses.append("COALESCE(g.started_at,g.updated_at)>=?")
            params.append(since)
        if until:
            clauses.append("COALESCE(g.started_at,g.updated_at)<?")
            params.append(until)
        if analysis_source:
            clauses.append("ar.source=?")
            params.append(analysis_source)
        # Local games have no account_games row; use their stored perspective. Account
        # aggregates use the account-specific color, avoiding mixed-account attribution.
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            rows = db.execute(
                "WITH ranked AS (SELECT ar.*,ROW_NUMBER() OVER(PARTITION BY ar.game_id,"
                "ar.line_revision ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,"
                "ar.finished_at DESC,ar.run_id DESC) AS rank FROM analysis_runs ar "
                "JOIN game_revisions rr ON rr.game_id=ar.game_id AND rr.line_revision=ar.line_revision "
                "WHERE rr.is_current=1 AND ar.status IN ('complete','partial') "
                + ("AND ar.source=? " if analysis_source else "") + "), chosen AS ("
                "SELECT * FROM ranked WHERE rank=1) "
                "SELECT COALESCE(om.eco,g.opening_eco,'unknown') AS eco,"
                "COALESCE(om.name,g.opening_name,'unknown') AS opening,"
                "COALESCE(om.family,CASE WHEN g.opening_name IS NULL THEN 'unknown' "
                "ELSE g.opening_name END) AS family,"
                "COALESCE(ag.color,g.color) AS my_color,g.source AS game_source,"
                "COALESCE(ag.account_id,'local') AS account_scope,"
                "COUNT(DISTINCT g.game_id) AS games,"
                "COUNT(CASE WHEN mc.mover=COALESCE(ag.color,g.color) AND mc.ply>0 "
                "AND mc.eval_loss_cp IS NOT NULL THEN 1 END) AS evaluated_own_moves,"
                "COUNT(CASE WHEN mc.mover=COALESCE(ag.color,g.color) AND mc.ply>0 "
                "AND mc.eval_loss_cp IS NOT NULL AND mc.label IN ('mistake','blunder') "
                "THEN 1 END) AS own_errors,"
                "AVG(CASE WHEN mc.mover=COALESCE(ag.color,g.color) AND mc.ply>0 "
                "AND mc.eval_loss_cp IS NOT NULL AND mc.phase='opening' "
                "THEN mc.eval_loss_cp END) AS mean_opening_cpl_cp "
                "FROM games g JOIN game_revisions gr ON gr.game_id=g.game_id "
                "LEFT JOIN opening_matches om ON om.game_id=g.game_id AND om.line_revision=g.line_revision "
                "LEFT JOIN account_games ag ON ag.game_id=g.game_id "
                "LEFT JOIN chosen ar ON ar.game_id=g.game_id AND ar.line_revision=g.line_revision "
                "LEFT JOIN move_classifications mc ON mc.run_id=ar.run_id "
                "WHERE " + " AND ".join(clauses) +
                " GROUP BY COALESCE(om.eco,g.opening_eco,'unknown'),"
                "COALESCE(om.name,g.opening_name,'unknown'),COALESCE(om.family,g.opening_name,'unknown'),"
                "COALESCE(ag.color,g.color),g.source,COALESCE(ag.account_id,'local') "
                "ORDER BY games DESC,eco,opening,my_color,account_scope "
                , [*([analysis_source] if analysis_source else []), *params]).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            if int(item["games"] or 0) < max(1, int(min_games)):
                continue
            own_count = int(item["evaluated_own_moves"] or 0)
            errors = int(item["own_errors"] or 0)
            item["own_errors_per_100_evaluated"] = round(100 * errors / own_count, 1) if own_count else None
            item["opening_provenance"] = "chess-coach-cc0" if item["eco"] != "unknown" else "unknown"
            item["analysis_source"] = analysis_source or "latest-complete-preferred"
            output.append(item)
        return output

    def game_page(self, *, account_id: str | None = None, since: str | None = None,
                  until: str | None = None, opponent: str | None = None,
                  clock: str | None = None, game_source: str | None = None,
                  side: str | None = None,
                  opening: str | None = None, result: str | None = None,
                  analyzed: str | None = None, analysis_source: str | None = None,
                  page: int = 0, page_size: int = 100) -> dict[str, Any]:
        """Return a bounded party list using the same active filter dimensions as summary."""
        join, where, params = self._filter_sql(
            account_id=account_id, since=since, until=until, opponent=opponent, clock=clock,
            game_source=game_source, analysis_source=analysis_source, side=side,
            rating_min=None, rating_max=None, opening=opening, result=result, analyzed=analyzed)
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
        selected += ", (SELECT om.eco FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS matched_opening_eco, " \
                    "(SELECT om.name FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS matched_opening_name, " \
                    "(SELECT om.lichess_conflict FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS opening_conflict"
        if account_id:
            selected += ",ag.opponent AS account_opponent,ag.player_name AS account_player_name,ag.color AS account_color"
        owner_color = "ag.color" if account_id else "g.color"
        latest_run = ("SELECT ar.run_id FROM analysis_runs ar JOIN game_revisions rr "
                      "ON rr.game_id=ar.game_id AND rr.line_revision=ar.line_revision "
                      "WHERE ar.game_id=g.game_id AND ar.line_revision=g.line_revision "
                      "AND rr.is_current=1 AND ar.status IN ('complete','partial') "
                      + ("AND ar.source=? " if analysis_source else "") +
                      "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,"
                      "ar.finished_at DESC,ar.run_id DESC LIMIT 1")
        selected += f", ({latest_run}) AS latest_run_id, " \
                    f"(SELECT ar.status FROM analysis_runs ar WHERE ar.run_id=({latest_run})) AS analysis_status, " \
                    f"(SELECT ar.source FROM analysis_runs ar WHERE ar.run_id=({latest_run})) AS selected_analysis_source, " \
                    f"(SELECT COUNT(*) FROM game_positions gp WHERE gp.game_id=g.game_id " \
                    f"AND gp.line_revision=g.line_revision) AS total_positions, " \
                    f"(SELECT COUNT(*) FROM position_assessments pa WHERE pa.run_id=({latest_run}) " \
                    f"AND (pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL)) AS scored_positions, " \
                    f"(SELECT COUNT(*) FROM move_classifications mc WHERE mc.run_id=({latest_run}) " \
                    f"AND mc.mover={owner_color} AND mc.label='inaccuracy') AS own_inaccuracies, " \
                    f"(SELECT COUNT(*) FROM move_classifications mc WHERE mc.run_id=({latest_run}) " \
                    f"AND mc.mover={owner_color} AND mc.label='mistake') AS own_mistakes, " \
                    f"(SELECT COUNT(*) FROM move_classifications mc WHERE mc.run_id=({latest_run}) " \
                    f"AND mc.mover={owner_color} AND mc.label='blunder') AS own_blunders, " \
                    f"CASE {owner_color} WHEN 'white' THEN g.lichess_accuracy_white " \
                    f"WHEN 'black' THEN g.lichess_accuracy_black END AS owner_lichess_accuracy"
        page_params = ([analysis_source] * 7 if analysis_source else []) + [*params]
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            total = db.execute(f"SELECT COUNT(DISTINCT g.game_id) FROM games g{join} WHERE {where}",
                               params).fetchone()[0]
            rows = db.execute(
                f"SELECT DISTINCT {selected} FROM games g{join} WHERE {where} "
                "ORDER BY COALESCE(g.started_at,g.updated_at) DESC,g.game_id LIMIT ? OFFSET ?",
                [*page_params, page_size, page * page_size]).fetchall()
        return self._store_cached(cache_key, {"items": [dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size})
