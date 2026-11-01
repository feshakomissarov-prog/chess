"""Read-only, denominator-aware aggregates over current game revisions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
            clauses.append("g.started_at>=?")
            params.append(since)
        if until:
            clauses.append("g.started_at<?")
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
                "g.started_at,g.game_time_precision,g.white_rating,g.black_rating,"
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
            classification_sql = (selected + ", class_rows AS (SELECT mm.*,gm.mover,fg.my_color FROM chosen c "
                "JOIN move_metrics mm ON mm.run_id=c.run_id AND mm.metric_version='cc-move-metrics-v2' "
                "JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "JOIN filtered_games fg ON fg.game_id=mm.game_id WHERE mm.ply>0) "
                "SELECT COUNT(CASE WHEN mover=my_color AND cp_loss IS NOT NULL THEN 1 END) AS own_count,"
                "COUNT(CASE WHEN mover=my_color AND severity IN ('mistake','blunder') THEN 1 END) AS own_errors,"
                "COUNT(CASE WHEN mover=my_color AND severity='inaccuracy' THEN 1 END) AS own_inaccuracies,"
                "COUNT(CASE WHEN mover=my_color AND severity='mistake' THEN 1 END) AS own_mistakes,"
                "COUNT(CASE WHEN mover=my_color AND severity='blunder' THEN 1 END) AS own_blunders,"
                "AVG(CASE WHEN mover=my_color AND cp_loss IS NOT NULL THEN cp_loss END) AS avg_cpl,"
                "COUNT(CASE WHEN mover=my_color AND cp_loss>=200 THEN 1 END) AS swings "
                "FROM class_rows")
            own = db.execute(classification_sql, [*params, *source_params]).fetchone()
            per_game = db.execute(selected + ", own_per_game AS (SELECT mm.game_id,"
                "COUNT(CASE WHEN gm.mover=fg.my_color AND mm.cp_loss IS NOT NULL "
                "THEN 1 END) AS assessed,"
                "SUM(CASE WHEN gm.mover=fg.my_color AND mm.severity='inaccuracy' THEN 1 ELSE 0 END) AS inaccuracies,"
                "SUM(CASE WHEN gm.mover=fg.my_color AND mm.severity='mistake' THEN 1 ELSE 0 END) AS mistakes,"
                "SUM(CASE WHEN gm.mover=fg.my_color AND mm.severity='blunder' THEN 1 ELSE 0 END) AS blunders "
                "FROM chosen c JOIN filtered_games fg ON fg.game_id=c.game_id "
                "LEFT JOIN move_metrics mm ON mm.run_id=c.run_id AND mm.metric_version='cc-move-metrics-v2' AND mm.ply>0 "
                "LEFT JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "GROUP BY fg.game_id) SELECT COUNT(CASE WHEN assessed>0 THEN 1 END) AS analyzed_games,"
                "AVG(CASE WHEN assessed>0 THEN inaccuracies END) AS avg_inaccuracies,"
                "AVG(CASE WHEN assessed>0 THEN mistakes END) AS avg_mistakes,"
                "AVG(CASE WHEN assessed>0 THEN blunders END) AS avg_blunders,"
                "AVG(CASE WHEN assessed>0 THEN CASE WHEN blunders>0 THEN 1.0 ELSE 0 END END) "
                "AS share_games_with_blunder "
                "FROM own_per_game", [*params, *source_params]).fetchone()
            phase_rows = db.execute(selected + " SELECT CASE WHEN pb.middle_start_ply IS NOT NULL "
                "AND mm.ply<pb.middle_start_ply THEN 'opening' WHEN pb.end_start_ply IS NOT NULL "
                "AND mm.ply>=pb.end_start_ply THEN 'endgame' WHEN pb.middle_start_ply IS NOT NULL "
                "AND mm.ply>=pb.middle_start_ply THEN 'middlegame' ELSE 'unknown' END AS phase,"
                "CASE WHEN gm.mover=fg.my_color THEN 'mine' ELSE 'opponent' END AS perspective,"
                "mm.severity AS label,COUNT(*) AS count FROM chosen c "
                "JOIN move_metrics mm ON mm.run_id=c.run_id AND mm.metric_version='cc-move-metrics-v2' "
                "JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "JOIN filtered_games fg ON fg.game_id=mm.game_id "
                "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id AND pb.line_revision=mm.line_revision "
                "WHERE mm.ply>0 GROUP BY phase,perspective,mm.severity",
                [*params, *source_params]).fetchall()
            trend = db.execute(filtered + " SELECT started_at AS date,game_time_precision,"
                "my_rating AS rating,game_id FROM filtered_games WHERE my_rating IS NOT NULL "
                "AND started_at IS NOT NULL ORDER BY started_at DESC,game_id DESC LIMIT 500",
                params).fetchall()
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
                 "own_error_count": int(own['own_errors'] or 0),
                 "own_inaccuracies": int(own['own_inaccuracies'] or 0),
                 "own_mistakes": int(own['own_mistakes'] or 0),
                 "own_blunders": int(own['own_blunders'] or 0),
                 "analysis_games": int(analysis_count),
                 "analyzed_games_with_classifications": int(per_game['analyzed_games'] or 0),
                 "avg_inaccuracies_per_analyzed_game": round(float(per_game['avg_inaccuracies']), 2) if per_game['avg_inaccuracies'] is not None else None,
                 "avg_mistakes_per_analyzed_game": round(float(per_game['avg_mistakes']), 2) if per_game['avg_mistakes'] is not None else None,
                 "avg_blunders_per_analyzed_game": round(float(per_game['avg_blunders']), 2) if per_game['avg_blunders'] is not None else None,
                 "share_games_with_blunder_percent": round(100 * float(per_game['share_games_with_blunder']), 1) if per_game['share_games_with_blunder'] is not None else None,
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
            clauses.append("g.started_at>=?")
            params.append(since)
        if until:
            clauses.append("g.started_at<?")
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
                "COUNT(CASE WHEN gm.mover=COALESCE(ag.color,g.color) AND mm.ply>0 "
                "AND mm.cp_loss IS NOT NULL THEN 1 END) AS evaluated_own_moves,"
                "COUNT(CASE WHEN gm.mover=COALESCE(ag.color,g.color) AND mm.ply>0 "
                "AND mm.severity IN ('mistake','blunder') "
                "THEN 1 END) AS own_errors,"
                "AVG(CASE WHEN gm.mover=COALESCE(ag.color,g.color) AND mm.ply>0 "
                "AND mm.cp_loss IS NOT NULL AND pb.middle_start_ply IS NOT NULL "
                "AND mm.ply<pb.middle_start_ply THEN mm.cp_loss END) AS mean_opening_cpl_cp "
                "FROM games g JOIN game_revisions gr ON gr.game_id=g.game_id "
                "LEFT JOIN opening_matches om ON om.game_id=g.game_id AND om.line_revision=g.line_revision "
                "LEFT JOIN account_games ag ON ag.game_id=g.game_id "
                "LEFT JOIN chosen ar ON ar.game_id=g.game_id AND ar.line_revision=g.line_revision "
                "LEFT JOIN move_metrics mm ON mm.run_id=ar.run_id AND mm.metric_version='cc-move-metrics-v2' "
                "LEFT JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id AND pb.line_revision=mm.line_revision "
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

    def _official_accuracy(self, account_id: str, *, since: str, until: str,
                           clock: str | None, side: str | None) -> dict[str, Any]:
        clauses = ["ag.account_id=?", "g.started_at>=?", "g.started_at<?",
                   "g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = [account_id, since, until]
        if clock:
            clauses.append("g.clock=?")
            params.append(clock)
        if side in {"white", "black"}:
            clauses.append("ag.color=?")
            params.append(side)
        with self.repository._connection() as db:
            row = db.execute("SELECT COUNT(*) AS games,"
                "AVG(CASE ag.color WHEN 'white' THEN g.lichess_accuracy_white "
                "WHEN 'black' THEN g.lichess_accuracy_black END) AS accuracy,"
                "COUNT(CASE ag.color WHEN 'white' THEN g.lichess_accuracy_white "
                "WHEN 'black' THEN g.lichess_accuracy_black END) AS n "
                "FROM games g JOIN account_games ag ON ag.game_id=g.game_id WHERE "
                + " AND ".join(clauses), params).fetchone()
        return {"games": int(row["games"] or 0), "n": int(row["n"] or 0),
                "accuracy": round(float(row["accuracy"]), 1) if row["accuracy"] is not None else None,
                "source": "lichess"}

    def personal_profile(self, *, account_id: str | None, clock: str | None = None,
                         side: str | None = None, analysis_source: str | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
        """Return separate current, fallback and all-history views for one account."""
        if not account_id:
            raise ValueError("Personal profile requires a selected account")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        until = now.astimezone(timezone.utc).isoformat()
        start90 = (now - timedelta(days=90)).astimezone(timezone.utc).isoformat()
        start365 = (now - timedelta(days=365)).astimezone(timezone.utc).isoformat()
        historical = self.summary(account_id=account_id, clock=clock, side=side,
                                  analysis_source=analysis_source)
        colors = [side] if side in {"white", "black"} else ["white", "black"]
        segments = {}
        for color in colors:
            common = {"account_id": account_id, "clock": clock, "side": color,
                      "analysis_source": analysis_source}
            recent = self.summary(**common, since=start90, until=until)
            extended = self.summary(**common, since=start365, until=until)
            recent_accuracy = self._official_accuracy(account_id, since=start90, until=until,
                                                       clock=clock, side=color)
            extended_accuracy = self._official_accuracy(account_id, since=start365, until=until,
                                                         clock=clock, side=color)
            recent_complete = self._fully_covered_games(
                account_id, since=start90, until=until, clock=clock, side=color,
                analysis_source=analysis_source)
            extended_complete = self._fully_covered_games(
                account_id, since=start365, until=until, clock=clock, side=color,
                analysis_source=analysis_source)
            use_recent = recent_complete >= 30
            selected = recent if use_recent else extended
            selected_accuracy = recent_accuracy if use_recent else extended_accuracy
            selected_complete = recent_complete if use_recent else extended_complete
            profile_since = start90 if use_recent else start365
            diagnostics = self._personal_diagnostics(
                account_id=account_id, since=profile_since, until=until, clock=clock,
                side=color, analysis_source=analysis_source)
            segments[color] = {
                "current_window": "90 дней" if use_recent else "12 месяцев (расширено)",
                "complete_games": selected_complete,
                "provisional": selected_complete < 30,
                "provisional_reason": ("меньше 30 полностью покрытых партий; выводы не ранжируются как подтверждённая слабость"
                                       if selected_complete < 30 else None),
                "current": selected,
                "current_official_accuracy": selected_accuracy,
                "opening_phase_diagnostics": diagnostics,
                "recent_90_days": {"summary": recent, "complete_games": recent_complete,
                                   "official_accuracy": recent_accuracy},
                "extended_12_months": {"summary": extended, "complete_games": extended_complete,
                                       "official_accuracy": extended_accuracy},
            }
        comparisons = {}
        previous_start = (now - timedelta(days=180)).astimezone(timezone.utc).isoformat()
        for color in colors:
            comparisons[color] = self.compare_periods(
                account_id=account_id, current_since=start90, current_until=until,
                previous_since=previous_start, previous_until=start90,
                clock=clock, side=color, analysis_source=analysis_source)
        return {"account_id": account_id, "clock_scope": clock or "все контроли отдельно не сопоставляются",
                "color_segments": segments,
                "current": segments[side]["current"] if side in segments else None,
                "historical": historical,
                "period_comparison_90d_vs_previous_90d": comparisons,
                "historical_timeline": self.historical_timeline(
                    account_id=account_id, clock=clock, side=side,
                    analysis_source=analysis_source)}

    def _fully_covered_games(self, account_id: str, *, since: str, until: str,
                             clock: str | None, side: str,
                             analysis_source: str | None) -> int:
        clauses = ["ag.account_id=?", "ag.color=?", "g.started_at>=?", "g.started_at<?",
                   "g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = [account_id, side, since, until]
        if clock:
            clauses.append("g.clock=?"); params.append(clock)
        source = "AND ar.source=? " if analysis_source else ""
        if analysis_source:
            params.append(analysis_source)
        with self.repository._connection() as db:
            row = db.execute(
                "SELECT COUNT(DISTINCT g.game_id) FROM games g JOIN account_games ag ON ag.game_id=g.game_id "
                "JOIN analysis_runs ar ON ar.game_id=g.game_id AND ar.line_revision=g.line_revision "
                "JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
                "JOIN analysis_coverage ac ON ac.run_id=ar.run_id WHERE " + " AND ".join(clauses) +
                " AND gr.is_current=1 AND ar.status='complete' " + source +
                "AND ac.scored_positions>=MAX(0,ac.total_positions-1)", params).fetchone()
        return int(row[0] or 0)

    def _personal_diagnostics(self, *, account_id: str, since: str, until: str,
                              clock: str | None, side: str,
                              analysis_source: str | None) -> dict[str, Any]:
        """Bounded current-window opening/phase aggregates with drill-down evidence."""
        clauses = ["ag.account_id=?", "ag.color=?", "g.started_at>=?", "g.started_at<?",
                   "g.status NOT IN ('created','started','started_soon','playing')",
                   "g.variant='standard'", "gr.is_current=1"]
        params: list[object] = [account_id, side, since, until]
        if clock:
            clauses.append("g.clock=?")
            params.append(clock)
        source_clause = "AND ar.source=? " if analysis_source else ""
        source_params: list[object] = [analysis_source] if analysis_source else []
        cte = ("WITH ranked AS (SELECT ar.*,ROW_NUMBER() OVER(PARTITION BY ar.game_id,ar.line_revision "
                "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,ar.finished_at DESC,ar.run_id DESC) rn "
                "FROM analysis_runs ar JOIN game_revisions rr ON rr.game_id=ar.game_id "
                "AND rr.line_revision=ar.line_revision WHERE rr.is_current=1 AND ar.status IN ('complete','partial') "
                + source_clause + "), chosen AS (SELECT * FROM ranked WHERE rn=1), scope AS ("
                "SELECT g.game_id,COALESCE(om.eco,g.opening_eco,'unknown') eco,"
                "COALESCE(om.name,g.opening_name,'unknown') opening,ag.color my_color,ar.run_id "
                "FROM games g JOIN game_revisions gr ON gr.game_id=g.game_id "
                "JOIN account_games ag ON ag.game_id=g.game_id "
                "LEFT JOIN opening_matches om ON om.game_id=g.game_id AND om.line_revision=g.line_revision "
                "LEFT JOIN chosen ar ON ar.game_id=g.game_id AND ar.line_revision=g.line_revision WHERE ")
        where = " AND ".join(clauses)
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            openings = db.execute(cte + where + ") SELECT eco,opening,COUNT(DISTINCT scope.game_id) games,"
                "COUNT(CASE WHEN gm.mover=my_color AND mm.cp_loss IS NOT NULL "
                "AND pb.middle_start_ply IS NOT NULL AND mm.ply<pb.middle_start_ply THEN 1 END) evaluated_moves,"
                "COUNT(CASE WHEN gm.mover=my_color AND mm.severity IN ('mistake','blunder') "
                "AND pb.middle_start_ply IS NOT NULL AND mm.ply<pb.middle_start_ply THEN 1 END) errors "
                "FROM scope LEFT JOIN move_metrics mm ON mm.run_id=scope.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND mm.ply>0 "
                "LEFT JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id AND pb.line_revision=mm.line_revision "
                "GROUP BY eco,opening ORDER BY errors DESC,games DESC,eco LIMIT 20",
                [*source_params, *params]).fetchall()
            phases = db.execute(cte + where + ") SELECT CASE WHEN pb.middle_start_ply IS NOT NULL "
                "AND mm.ply<pb.middle_start_ply THEN 'opening' WHEN pb.end_start_ply IS NOT NULL "
                "AND mm.ply>=pb.end_start_ply THEN 'endgame' WHEN pb.middle_start_ply IS NOT NULL "
                "AND mm.ply>=pb.middle_start_ply THEN 'middlegame' ELSE 'unknown' END phase,"
                "COUNT(CASE WHEN gm.mover=my_color AND mm.cp_loss IS NOT NULL THEN 1 END) evaluated_moves,"
                "COUNT(CASE WHEN gm.mover=my_color AND mm.severity IN ('mistake','blunder') THEN 1 END) errors,"
                "AVG(CASE WHEN gm.mover=my_color AND mm.cp_loss IS NOT NULL THEN mm.cp_loss END) mean_loss_cp "
                "FROM scope LEFT JOIN move_metrics mm ON mm.run_id=scope.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND mm.ply>0 "
                "LEFT JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id AND pb.line_revision=mm.line_revision "
                "GROUP BY phase ORDER BY errors DESC,phase",
                [*source_params, *params]).fetchall()
            evidence = db.execute(cte + where + ") SELECT scope.game_id,mm.ply,"
                "CASE WHEN pb.middle_start_ply IS NOT NULL AND mm.ply<pb.middle_start_ply THEN 'opening' "
                "WHEN pb.end_start_ply IS NOT NULL AND mm.ply>=pb.end_start_ply THEN 'endgame' "
                "WHEN pb.middle_start_ply IS NOT NULL AND mm.ply>=pb.middle_start_ply THEN 'middlegame' "
                "ELSE 'unknown' END phase,"
                "mm.severity AS label,"
                "mm.cp_loss,(SELECT aa.pv_uci FROM analysis_alternatives aa "
                "WHERE aa.run_id=scope.run_id AND aa.ply=mm.ply-1 AND aa.multipv_rank=1) best_pv_uci,"
                "scope.eco,scope.opening,"
                "(SELECT ar.source FROM analysis_runs ar WHERE ar.run_id=scope.run_id) analysis_source "
                "FROM scope JOIN move_metrics mm ON mm.run_id=scope.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' "
                "JOIN game_moves gm ON gm.game_id=mm.game_id AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply "
                "LEFT JOIN game_phase_boundaries pb ON pb.game_id=mm.game_id AND pb.line_revision=mm.line_revision "
                "WHERE gm.mover=scope.my_color AND mm.ply>0 AND mm.cp_loss IS NOT NULL "
                "AND mm.severity IN ('mistake','blunder') ORDER BY mm.cp_loss DESC,scope.game_id,mm.ply LIMIT 20",
                [*source_params, *params]).fetchall()
        opening_rows = []
        for row in openings:
            item = dict(row)
            item["errors_per_100"] = (round(100 * item["errors"] / item["evaluated_moves"], 1)
                                      if item["evaluated_moves"] else None)
            item["hypothesis_eligible"] = item["games"] >= 3 and item["evaluated_moves"] >= 10
            opening_rows.append(item)
        phase_rows = []
        for row in phases:
            item = dict(row)
            item["errors_per_100"] = (round(100 * item["errors"] / item["evaluated_moves"], 1)
                                      if item["evaluated_moves"] else None)
            phase_rows.append(item)
        focus = [dict(row) for row in evidence]
        return {"openings": opening_rows, "phases": phase_rows,
                "current_focus": focus, "focus_is_evidence_not_cause": True}

    def historical_timeline(self, *, account_id: str, clock: str | None = None,
                            side: str | None = None,
                            analysis_source: str | None = None) -> list[dict[str, Any]]:
        """Monthly aggregates across the archive; undated games form their own group."""
        clauses = ["ag.account_id=?", "g.status NOT IN ('created','started','started_soon','playing')"]
        params: list[object] = [account_id]
        if clock:
            clauses.append("g.clock=?"); params.append(clock)
        if side in {"white", "black"}:
            clauses.append("ag.color=?"); params.append(side)
        analysis_clause = "AND ar.source=? " if analysis_source else ""
        cte_params: list[object] = [analysis_source] if analysis_source else []
        where = " AND ".join(clauses)
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            rows = db.execute(
                "WITH ranked AS (SELECT ar.*,ROW_NUMBER() OVER(PARTITION BY ar.game_id,ar.line_revision "
                "ORDER BY CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,ar.finished_at DESC,ar.run_id DESC) rank "
                "FROM analysis_runs ar JOIN game_revisions rr ON rr.game_id=ar.game_id "
                "AND rr.line_revision=ar.line_revision WHERE rr.is_current=1 "
                "AND ar.status IN ('complete','partial') " + analysis_clause + "), chosen AS "
                "(SELECT * FROM ranked WHERE rank=1), "
                "game_rows AS (SELECT g.*,ag.color my_color,ar.run_id,ar.status analysis_status,"
                "CASE WHEN g.started_at IS NULL THEN 'unknown' ELSE substr(g.started_at,1,7) END period,"
                "CASE ag.color WHEN 'white' THEN g.lichess_accuracy_white "
                "WHEN 'black' THEN g.lichess_accuracy_black END official_accuracy,"
                "(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
                "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=ar.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND mm.ply>0 AND gm.mover=ag.color "
                "AND mm.cp_loss IS NOT NULL) assessed_own_moves,"
                "(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
                "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=ar.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND gm.mover=ag.color "
                "AND mm.severity IN ('mistake','blunder')) own_errors,"
                "(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
                "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=ar.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND gm.mover=ag.color "
                "AND mm.severity='inaccuracy') own_inaccuracies,"
                "(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
                "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=ar.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND gm.mover=ag.color "
                "AND mm.severity='mistake') own_mistakes,"
                "(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id "
                "AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=ar.run_id "
                "AND mm.metric_version='cc-move-metrics-v2' AND gm.mover=ag.color "
                "AND mm.severity='blunder') own_blunders,"
                "(SELECT COUNT(*) FROM analysis_events ae WHERE ae.run_id=ar.run_id "
                "AND ae.event_type='missed_opportunity' AND ae.confidence_state='candidate') opportunity_candidates,"
                "(SELECT COUNT(*) FROM analysis_events ae WHERE ae.run_id=ar.run_id "
                "AND ae.event_type='opportunity_used' AND ae.confidence_state='verified') verified_opportunities_used "
                "FROM games g JOIN account_games ag ON ag.game_id=g.game_id "
                "LEFT JOIN chosen ar ON ar.game_id=g.game_id AND ar.line_revision=g.line_revision WHERE "
                + where + ") "
                "SELECT period,my_color,COUNT(*) games,"
                "SUM(CASE WHEN winner=my_color THEN 1 ELSE 0 END) wins,"
                "SUM(CASE WHEN winner IS NOT NULL AND winner<>my_color "
                "AND status NOT IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "AND pgn NOT LIKE '%[Result \"1/2-1/2\"]%' THEN 1 ELSE 0 END) losses,"
                "SUM(CASE WHEN status IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
                "OR pgn LIKE '%[Result \"1/2-1/2\"]%' THEN 1 ELSE 0 END) draws,"
                "AVG(official_accuracy) official_accuracy,COUNT(official_accuracy) accuracy_n,"
                "COUNT(analysis_status) analyzed_games,SUM(assessed_own_moves) assessed_own_moves,"
                "SUM(own_errors) own_errors,SUM(opportunity_candidates) opportunity_candidates,"
                "SUM(own_inaccuracies) own_inaccuracies,SUM(own_mistakes) own_mistakes,"
                "SUM(own_blunders) own_blunders,"
                "SUM(verified_opportunities_used) verified_opportunities_used,"
                "SUM(CASE WHEN game_time_precision='date_only' THEN 1 ELSE 0 END) date_only_games,"
                "SUM(CASE WHEN started_at IS NULL THEN 1 ELSE 0 END) unknown_date_games "
                "FROM game_rows GROUP BY period,my_color "
                "ORDER BY CASE WHEN period='unknown' THEN 1 ELSE 0 END,period,my_color",
                [*cte_params, *params]).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            n = int(item["assessed_own_moves"] or 0)
            candidates = int(item["opportunity_candidates"] or 0)
            item["own_error_rate_per_100"] = round(100 * int(item["own_errors"] or 0) / n, 1) if n else None
            item["opportunity_conversion"] = None
            item["opportunity_conversion_status"] = (
                "not modeled: no verified candidate-to-action linkage" if candidates
                else "no eligible denominator")
            item["official_accuracy"] = round(float(item["official_accuracy"]), 1) if item["official_accuracy"] is not None else None
            item["accuracy_source"] = "lichess"
            output.append(item)
        return output

    def compare_periods(self, *, account_id: str, current_since: str, current_until: str,
                        previous_since: str, previous_until: str, clock: str | None = None,
                        side: str | None = None, analysis_source: str | None = None,
                        coverage_tolerance: float = 10.0) -> dict[str, Any]:
        if not account_id:
            raise ValueError("Period comparison requires a selected account")
        current = self.summary(account_id=account_id, since=current_since, until=current_until,
                               clock=clock, side=side, analysis_source=analysis_source)
        previous = self.summary(account_id=account_id, since=previous_since, until=previous_until,
                                clock=clock, side=side, analysis_source=analysis_source)
        current_coverage = float(current["analysis_coverage"] or 0)
        previous_coverage = float(previous["analysis_coverage"] or 0)
        eligible = (current["games"] >= 30 and previous["games"] >= 30
                    and abs(current_coverage - previous_coverage) <= max(0.0, coverage_tolerance))
        return {"current": current, "previous": previous,
                "eligible_for_comparison": eligible,
                "explanation": ("В этих выборках наблюдается разница; это не доказывает причину."
                                if eligible else "Сравнение предварительное: мало партий или несопоставимое покрытие."),
                "differences": ({"own_error_rate_per_100": (
                    current["own_error_rate_per_100"] - previous["own_error_rate_per_100"]
                    if current["own_error_rate_per_100"] is not None and previous["own_error_rate_per_100"] is not None
                    else None),
                    "games": current["games"] - previous["games"],
                    "analysis_coverage_percentage_points": round(current_coverage - previous_coverage, 1)}
                    if eligible else None)}

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
                    "g.opening_name,g.lichess_accuracy_white,g.lichess_accuracy_black,"
                    "g.game_time_precision")
        selected += ", (SELECT om.eco FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS matched_opening_eco, " \
                    "(SELECT om.name FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS matched_opening_name, " \
                    "(SELECT om.lichess_conflict FROM opening_matches om WHERE om.game_id=g.game_id " \
                    "AND om.line_revision=g.line_revision) AS opening_conflict"
        if account_id:
            selected += ",ag.account_id AS account_id,ag.opponent AS account_opponent,ag.player_name AS account_player_name,ag.color AS account_color"
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
                    f"(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id " \
                    f"AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=({latest_run}) " \
                    f"AND mm.metric_version='cc-move-metrics-v2' AND gm.mover={owner_color} " \
                    f"AND mm.severity='inaccuracy') AS own_inaccuracies, " \
                    f"(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id " \
                    f"AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=({latest_run}) " \
                    f"AND mm.metric_version='cc-move-metrics-v2' AND gm.mover={owner_color} " \
                    f"AND mm.severity='mistake') AS own_mistakes, " \
                    f"(SELECT COUNT(*) FROM move_metrics mm JOIN game_moves gm ON gm.game_id=mm.game_id " \
                    f"AND gm.line_revision=mm.line_revision AND gm.ply=mm.ply WHERE mm.run_id=({latest_run}) " \
                    f"AND mm.metric_version='cc-move-metrics-v2' AND gm.mover={owner_color} " \
                    f"AND mm.severity='blunder') AS own_blunders, " \
                    f"CASE {owner_color} WHEN 'white' THEN g.lichess_accuracy_white " \
                    f"WHEN 'black' THEN g.lichess_accuracy_black END AS owner_lichess_accuracy"
        page_params = ([analysis_source] * 7 if analysis_source else []) + [*params]
        self.repository._wait_before_read()
        with self.repository._connection() as db:
            total = db.execute(f"SELECT COUNT(DISTINCT g.game_id) FROM games g{join} WHERE {where}",
                               params).fetchone()[0]
            rows = db.execute(
                f"SELECT DISTINCT {selected} FROM games g{join} WHERE {where} "
                "ORDER BY CASE WHEN g.started_at IS NULL THEN 1 ELSE 0 END,"
                "g.started_at DESC,g.game_id LIMIT ? OFFSET ?",
                [*page_params, page_size, page * page_size]).fetchall()
        return self._store_cached(cache_key, {"items": [dict(row) for row in rows], "total": total,
                "page": page, "page_size": page_size,
                "pages": (total + page_size - 1) // page_size})
