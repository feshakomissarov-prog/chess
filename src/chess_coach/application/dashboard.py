"""Application-owned bounded dashboard SQL queries and read models."""

from __future__ import annotations

import threading

from typing import Any

from ..domain.accuracy import FORMULA_ID
from ..lichess.game_review import load_game_review
from ..lichess.statistics import StatisticsService


_COMPLETED = "('created','started','started_soon','playing')"


class DashboardQueryCoordinator:
    def __init__(self, repository) -> None:
        self.repository = repository
        self._lock = threading.RLock()
        self._generation = 0
        self._pending: tuple[int, str | None, str | None] | None = None

    def begin_request(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def defer(self, request: tuple[int, str | None, str | None]) -> None:
        with self._lock:
            self._pending = request

    def take_pending(self) -> tuple[int, str | None, str | None] | None:
        with self._lock:
            pending, self._pending = self._pending, None
            return pending

    def clear_pending(self) -> None:
        with self._lock:
            self._pending = None

    def snapshot(self, *, account_id: str | None, clock: str,
                 query=None) -> dict:
        return (query or dashboard_snapshot)(
            self.repository, account_id=account_id, clock=clock,
            wait_for_writes=False)


def dashboard_preview_snapshot(repository, *, account_id: str | None = None,
                               clock: str | None = None,
                               stop_event: threading.Event | None = None) -> dict[str, Any]:
    """Read the local game count and latest rows without waiting on queued writes.

    This bounded preview is emitted before the more expensive source-aware
    aggregate. SQLite readers see committed rows while the single writer keeps
    working; pending writes must not hold the whole Home page blank.
    """
    join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
    color = "ag.color" if account_id else "g.color"
    opponent = "ag.opponent AS account_opponent" if account_id else "NULL AS account_opponent"
    player = "ag.player_name AS account_player_name" if account_id else "NULL AS account_player_name"
    color_value = "ag.color AS account_color" if account_id else "g.color AS account_color"
    clauses = [f"g.status NOT IN {_COMPLETED}"]
    params: list[object] = []
    if account_id:
        clauses.append("ag.account_id=?")
        params.append(account_id)
    if clock:
        clauses.append("g.clock=?")
        params.append(clock)
    where = " AND ".join(clauses)
    activity_epoch = ("CASE WHEN g.last_move_at>0 THEN g.last_move_at/1000.0 "
                      "WHEN g.game_time_precision='timestamp' THEN unixepoch(g.started_at) "
                      "WHEN g.game_time_precision='date_only' "
                      "THEN unixepoch(substr(g.started_at,1,10)) ELSE NULL END")
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("dashboard preview superseded")
    with repository._connection() as db:
        db.set_progress_handler(
            lambda: 1 if stop_event is not None and stop_event.is_set() else 0, 1000)
        db.execute("PRAGMA busy_timeout=250")
        total = int(db.execute(f"SELECT COUNT(DISTINCT g.game_id) FROM games g{join} "
                               f"WHERE {where}", params).fetchone()[0] or 0)
        rows = db.execute(
            f"SELECT g.*, {opponent}, {player}, {color_value} FROM games g{join} "
            f"WHERE {where} ORDER BY ({activity_epoch}) IS NULL,{activity_epoch} DESC,"
            "g.game_id DESC LIMIT 3", params).fetchall()
        db.set_progress_handler(None, 0)
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("dashboard preview superseded")
    games = []
    for row in rows:
        game = dict(row)
        game["review_metrics"] = {"status": "loading", "coverage": "—", "source": "—"}
        games.append(game)
    scope = "Выбранный аккаунт" if account_id else "Все локальные партии"
    if clock:
        scope += f" · {clock}"
    metrics = [
        {"key": "games", "label": "Завершённые партии", "value": str(total),
         "detail": "Локальные записи SQLite", "source": "games + account_games"},
        {"key": "accuracy", "label": "Точность",
         "value": "—", "detail": "Подготавливаем сводку точности…",
         "source": "SQLite; появится после фоновой полной сводки"},
        {"key": "analysis", "label": "Партии с анализом", "value": "—",
         "detail": "Подготавливаем сводку охвата…", "source": "SQLite"},
        {"key": "errors", "label": "Ошибки на 100 оценённых ходов", "value": "—",
         "detail": "Подготавливаем статистику ходов…", "source": "SQLite"},
    ]
    puzzle_rating = (repository.puzzle_player_rating(account_id)
                     if account_id and hasattr(repository, "puzzle_player_rating") else {})
    activity_trend = (repository.puzzle_rating_trend(account_id, limit=6)
                      if account_id and hasattr(repository, "puzzle_rating_trend") else [])
    return {"metrics": metrics, "recent_games": games,
            "puzzle_player_rating": puzzle_rating, "activity_trend": activity_trend,
            "scope": scope,
            "filter": {"account_id": account_id, "clock": clock}, "is_preview": True}


def _dashboard_accuracy_stats(db, *, join: str, color: str, where: str,
                              params: list[object]) -> dict[str, Any]:
    """Fast source-aware archive accuracy aggregate shared by preview and full read."""
    row = db.execute(f"""
        WITH scoped AS (
            SELECT g.game_id,g.line_revision,{color} AS player_color,
                CASE WHEN g.source IN ('lichess','lichess_archive') OR g.source_game_id IS NOT NULL
                     THEN CASE {color} WHEN 'white' THEN g.lichess_accuracy_white
                                       WHEN 'black' THEN g.lichess_accuracy_black END END official
            FROM games g{join} WHERE {where}
        ), candidates AS (
            SELECT s.game_id, ar.run_id, ar.finished_at, ar.source,
                CASE s.player_color WHEN 'white' THEN ac.white_accuracy
                                    WHEN 'black' THEN ac.black_accuracy END value
            FROM scoped s JOIN analysis_runs ar ON ar.game_id=s.game_id
            JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.is_current=1
                AND ar.line_revision=s.line_revision AND gr.line_revision=ar.line_revision
                AND gr.line_hash=ar.line_hash
            JOIN analysis_game_accuracy ac ON ac.run_id=ar.run_id
                AND ac.formula_id=? AND ac.run_finished_at IS ar.finished_at
            WHERE ar.status='complete' AND NOT EXISTS (
                SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id
                AND aj.analysis_pass='review')
        ), ranked AS (
            SELECT *,ROW_NUMBER() OVER (PARTITION BY game_id,source='stockfish'
                ORDER BY CASE source WHEN 'stockfish' THEN 0 WHEN 'lichess-json' THEN 1 ELSE 2 END,
                         finished_at DESC,run_id DESC) priority
            FROM candidates WHERE value IS NOT NULL
        ), scores AS (
            SELECT s.game_id,CASE WHEN s.official BETWEEN 0 AND 100 THEN s.official END official,
                MAX(CASE WHEN r.source='stockfish' THEN r.value END) stockfish,
                MAX(CASE WHEN r.source<>'stockfish' THEN r.value END) server_local
            FROM scoped s LEFT JOIN ranked r ON r.game_id=s.game_id AND r.priority=1
            GROUP BY s.game_id
        )
        SELECT AVG(official) official_mean,COUNT(official) official_count,
            AVG(stockfish) stockfish_mean,COUNT(stockfish) stockfish_count,
            COUNT(CASE WHEN official IS NOT NULL AND stockfish IS NOT NULL THEN 1 END) overlap,
            COUNT(COALESCE(official,stockfish,server_local)) combined_count,
            AVG(COALESCE(official,stockfish,server_local)) combined_mean
        FROM scores
    """, (*params, FORMULA_ID)).fetchone()
    return dict(row)


def dashboard_options(repository) -> dict[str, list[dict[str, str]]]:
    """Return small account/clock selectors; no game rows are loaded."""
    with repository._connection() as db:
        accounts = [dict(row) for row in db.execute(
            "SELECT account_id,username FROM accounts ORDER BY username COLLATE NOCASE")]
        clocks = [str(row[0]) for row in db.execute(
            "SELECT DISTINCT clock FROM games WHERE clock IS NOT NULL AND clock<>'' "
            "ORDER BY clock LIMIT 30")]
    return {"accounts": accounts, "clocks": clocks}


def dashboard_snapshot(repository, *, account_id: str | None = None,
                       clock: str | None = None, wait_for_writes: bool = True) -> dict[str, Any]:
    """Return scoped KPIs plus at most three completed games.

    All archive-wide counts are SQL aggregates. Only three current game lines are
    loaded for cards, and this function never parses stored PGN or starts UCI.
    """
    if wait_for_writes:
        repository._wait_before_read()
    join = " JOIN account_games ag ON ag.game_id=g.game_id " if account_id else ""
    color = "ag.color" if account_id else "g.color"
    clauses = [f"g.status NOT IN {_COMPLETED}"]
    params: list[object] = []
    if account_id:
        clauses.append("ag.account_id=?")
        params.append(account_id)
    if clock:
        clauses.append("g.clock=?")
        params.append(clock)
    where = " AND ".join(clauses)
    with repository._connection() as db:
        row = db.execute(
            f"SELECT COUNT(DISTINCT g.game_id) AS games,"
            f"COUNT(DISTINCT CASE WHEN g.winner={color} THEN g.game_id END) AS wins,"
            f"COUNT(DISTINCT CASE WHEN g.winner IS NOT NULL AND g.winner<>{color} "
            "AND g.status NOT IN ('draw','stalemate','repetition','50moves','insufficientMaterial') "
            "THEN g.game_id END) AS losses,"
            "COUNT(DISTINCT CASE WHEN g.status IN ('draw','stalemate','repetition','50moves',"
            "'insufficientMaterial') THEN g.game_id END) AS draws "
            f"FROM games g{join} WHERE {where}", params).fetchone()

        analysis_where = " AND ".join(clauses)
        run_params = list(params)
    with repository._connection() as db:
        accuracy_stats = db.execute(f"""
            WITH scoped AS (
                SELECT g.game_id,g.line_revision,{color} AS player_color,
                    CASE WHEN g.source IN ('lichess','lichess_archive') OR g.source_game_id IS NOT NULL
                         THEN CASE {color} WHEN 'white' THEN g.lichess_accuracy_white
                                           WHEN 'black' THEN g.lichess_accuracy_black END END official
                FROM games g{join} WHERE {where}
            ), candidates AS (
                SELECT s.game_id, ar.run_id, ar.finished_at, ar.source,
                    CASE s.player_color WHEN 'white' THEN ac.white_accuracy
                                        WHEN 'black' THEN ac.black_accuracy END value
                FROM scoped s JOIN analysis_runs ar ON ar.game_id=s.game_id
                JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.is_current=1
                    AND ar.line_revision=s.line_revision AND gr.line_revision=ar.line_revision
                    AND gr.line_hash=ar.line_hash
                JOIN analysis_game_accuracy ac ON ac.run_id=ar.run_id
                    AND ac.formula_id=? AND ac.run_finished_at IS ar.finished_at
                WHERE ar.status='complete' AND NOT EXISTS (
                    SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id
                    AND aj.analysis_pass='review')
            ), ranked AS (
                SELECT *,ROW_NUMBER() OVER (PARTITION BY game_id,source='stockfish'
                    ORDER BY CASE source WHEN 'stockfish' THEN 0 WHEN 'lichess-json' THEN 1 ELSE 2 END,
                             finished_at DESC,run_id DESC) priority
                FROM candidates WHERE value IS NOT NULL
            ), scores AS (
                SELECT s.game_id,CASE WHEN s.official BETWEEN 0 AND 100 THEN s.official END official,
                    MAX(CASE WHEN r.source='stockfish' THEN r.value END) stockfish,
                    MAX(CASE WHEN r.source<>'stockfish' THEN r.value END) server_local
                FROM scoped s LEFT JOIN ranked r ON r.game_id=s.game_id AND r.priority=1
                GROUP BY s.game_id
            )
            SELECT AVG(official) official_mean,COUNT(official) official_count,
                AVG(stockfish) stockfish_mean,COUNT(stockfish) stockfish_count,
                COUNT(CASE WHEN official IS NOT NULL AND stockfish IS NOT NULL THEN 1 END) overlap,
                COUNT(COALESCE(official,stockfish,server_local)) combined_count,
                AVG(COALESCE(official,stockfish,server_local)) combined_mean
            FROM scores
        """, (*params, FORMULA_ID)).fetchone()
        coverage = db.execute(
        "WITH scoped AS (SELECT DISTINCT g.game_id,g.line_revision FROM games g" + join +
        " WHERE " + analysis_where + "), runs AS (SELECT ar.game_id,ar.source,ar.status,"
        "COUNT(DISTINCT CASE WHEN pa.white_cp IS NOT NULL OR pa.mate_white IS NOT NULL "
        "THEN pa.ply END) scored,(SELECT COUNT(*) FROM game_positions gp "
        "WHERE gp.game_id=ar.game_id AND gp.line_revision=ar.line_revision) total "
        "FROM analysis_runs ar JOIN game_revisions gr ON gr.game_id=ar.game_id "
        "AND gr.line_revision=ar.line_revision AND gr.is_current=1 JOIN scoped s "
        "ON s.game_id=ar.game_id AND s.line_revision=ar.line_revision "
        "LEFT JOIN position_assessments pa ON pa.run_id=ar.run_id "
        "WHERE ar.status IN ('complete','partial') AND NOT EXISTS ("
        "SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id "
        "AND aj.analysis_pass='review') GROUP BY ar.run_id), "
        "game_coverage AS (SELECT game_id,MAX(scored>0) has_scores,"
        "MAX(scored>0 AND status='complete' AND scored>=total) is_full,"
        "MAX(scored>0 AND source='stockfish') has_stockfish,"
        "MAX(scored>0 AND source IN ('lichess-pgn','lichess-json')) has_lichess "
        "FROM runs GROUP BY game_id) "
        "SELECT COUNT(CASE WHEN has_scores THEN 1 END) analyzed,"
        "COUNT(CASE WHEN is_full THEN 1 END) complete_count,"
        "COUNT(CASE WHEN has_scores AND NOT is_full THEN 1 END) partial,"
        "COUNT(CASE WHEN has_stockfish THEN 1 END) stockfish,"
        "COUNT(CASE WHEN has_lichess THEN 1 END) lichess "
            "FROM game_coverage", run_params).fetchone()

    summary = StatisticsService(repository).summary(
        account_id=account_id, clock=clock, wait_for_writes=wait_for_writes)
    activity_trend = (repository.puzzle_rating_trend(account_id, limit=6)
                      if account_id and hasattr(repository, "puzzle_rating_trend") else [])
    total = int(row["games"] or 0)
    accuracy_n = int(accuracy_stats["official_count"])
    accuracy = accuracy_stats["combined_mean"]
    combined_n = int(accuracy_stats["combined_count"])
    sf_n = int(accuracy_stats["stockfish_count"])
    official_mean = accuracy_stats["official_mean"]
    stockfish_mean = accuracy_stats["stockfish_mean"]
    analyzed_complete = int(coverage["complete_count"] or 0)
    analyzed_partial = int(coverage["partial"] or 0)
    analyzed_games = int(coverage["analyzed"] or 0)
    assessed = int(summary["own_assessed_moves"] or 0)
    own_total = int(summary["total_own_moves"] or 0)
    error_rate = summary.get("own_error_rate_per_100")
    scope = "Выбранный аккаунт" if account_id else "Все локальные партии · смешанная перспектива"
    if clock:
        scope += f" · {clock}"
    metrics = [
        {"key": "games", "label": "Завершённые партии", "value": str(total),
         "detail": scope, "source": "SQLite: games + account_games"},
        {"key": "accuracy", "label": "Точность", "value":
         f"{float(accuracy):.1f}%" if accuracy is not None else "н/д",
         "detail": (
             f"Lichess: {f'{official_mean:.1f}%' if official_mean is not None else '—'} ({accuracy_n})\n"
             f"Stockfish: {f'{stockfish_mean:.1f}%' if stockfish_mean is not None else '—'} ({sf_n})"
             if total else "Нет завершённых партий"),
         "source": (
             f"{combined_n}/{total} партий. Один вклад за свою сторону: официальная точность "
             "Lichess, затем локальный расчёт по полному Stockfish, затем по полным серверным оценкам. "
             f"Обе раздельные выборки содержат {int(accuracy_stats['overlap'])} общих партий. "
             f"Формула локального расчёта: {FORMULA_ID}.")},
        {"key": "analysis", "label": "Партии с анализом",
         "value": (f"{analyzed_games}/{total} ({100 * analyzed_games / total:.1f}%)"
                   if total else "0/0 · н/д"),
         "detail": (f"Полный охват: {analyzed_complete} · частичный: {analyzed_partial} · "
                    f"Stockfish: {int(coverage['stockfish'] or 0)} · "
                    f"Lichess с оценками: {int(coverage['lichess'] or 0)} · "
                    f"оценено моих ходов: {assessed}/{own_total}"
                    if total else "Нет завершённых партий для расчёта доли анализа"),
         "source": "Distinct completed games with current-line position scores; source counts may overlap"},
        {"key": "errors", "label": "Ошибки на 100 оценённых ходов",
         "value": f"{float(error_rate):.1f}" if error_rate is not None else "н/д",
         "detail": (f"Ошибки {int(summary['own_error_count'])} / {assessed} оценённых собственных ходов · "
                    f"всего собственных ходов {own_total}"),
         "source": "Текущие версии cc-move-metrics-v3; знаменатель: оценённые собственные ходы"},
    ]
    recent = repository.search_games(completed_only=True, page=0, page_size=3,
                                     account_id=account_id, clock=clock,
                                     order_by_game_time=True,
                                     wait_for_writes=wait_for_writes)
    games = [dict(game) for game in recent["items"]]
    for game in games:
        game["review_metrics"] = game_card_metrics(repository, game)
    return {"metrics": metrics, "recent_games": games,
            "puzzle_player_rating": repository.puzzle_player_rating(account_id) if account_id else {},
            "activity_trend": activity_trend,
            "activity_trend_source": "Lichess puzzle attempts saved locally" if activity_trend else None,
            "scope": scope, "filter": {"account_id": account_id, "clock": clock}}


def game_card_metrics(repository, game: dict[str, Any]) -> dict[str, Any]:
    """Summarize one bounded line and its current saved assessment, never UCI."""
    color = game.get("account_color") or game.get("color")
    try:
        view = load_game_review(repository, game)
    except (KeyError, ValueError):
        return {"accuracy": "н/д", "inaccuracies": "н/д", "mistakes": "н/д",
                "blunders": "н/д", "strong": "н/д", "status": "линия недоступна",
                "coverage": "0/0", "source": "нет данных"}
    analysis = view.get("analysis") or {}
    side = view["per_side"].get(color, {}) if color in {"white", "black"} else {}
    opponent_color = "black" if color == "white" else "white" if color == "black" else None
    opponent_side = view["per_side"].get(opponent_color, {}) if opponent_color else {}
    own_color_accuracy = view["official_accuracy"].get(color) if color in {"white", "black"} else None
    opponent_accuracy = (view["official_accuracy"].get(opponent_color)
                         if opponent_color else None)
    accuracy_source = "Lichess" if own_color_accuracy is not None else None
    if own_color_accuracy is None:
        own_color_accuracy = (view.get("local_game_accuracy") or {}).get(color)
        if own_color_accuracy is not None:
            accuracy_source = "Stockfish · локально" if analysis.get("source") == "stockfish" else "Локальный расчёт"
    coverage = int(side.get("assessed") or 0)
    classified = int(side.get("classified") or 0)
    total = int(side.get("total") or 0)
    errors = int(side.get("inaccuracy") or 0) + int(side.get("mistake") or 0) + int(side.get("blunder") or 0)
    # Current detectors do not yet implement a verified "strong move" fact.
    # Candidate best_move_confirmed is intentionally not presented as verified.
    return {
        "accuracy": f"{float(own_color_accuracy):.1f}%" if own_color_accuracy is not None else "н/д",
        "accuracy_source": accuracy_source,
        "assessments": list(analysis.get("assessments") or []),
        "moves": {int(move.ply): str(move.san) for move in view.get("moves") or []},
        "errors": str(errors) if classified else "н/д",
        "inaccuracies": str(int(side.get("inaccuracy") or 0)) if classified else "н/д",
        "mistakes": str(int(side.get("mistake") or 0)) if classified else "н/д",
        "blunders": str(int(side.get("blunder") or 0)) if classified else "н/д",
        "strong": "н/д",
        "status": str(analysis.get("status") or "без анализа"),
        "coverage": f"{coverage}/{total}",
        "source": str(analysis.get("source") or "нет данных"),
        "strong_detail": "подтверждённый классификатор отсутствует",
        "owner_accuracy": f"{float(own_color_accuracy):.1f}%" if own_color_accuracy is not None else "н/д",
        "opponent_accuracy": f"{float(opponent_accuracy):.1f}%" if opponent_accuracy is not None else "н/д",
        "owner_inaccuracies": str(int(side.get("inaccuracy") or 0)) if classified else "н/д",
        "owner_mistakes": str(int(side.get("mistake") or 0)) if classified else "н/д",
        "owner_blunders": str(int(side.get("blunder") or 0)) if classified else "н/д",
        "opponent_inaccuracies": str(int(opponent_side.get("inaccuracy") or 0))
        if opponent_side.get("classified") else "н/д",
        "opponent_mistakes": str(int(opponent_side.get("mistake") or 0))
        if opponent_side.get("classified") else "н/д",
        "opponent_blunders": str(int(opponent_side.get("blunder") or 0))
        if opponent_side.get("classified") else "н/д",
        "opponent_coverage": f"{int(opponent_side.get('assessed') or 0)}/{int(opponent_side.get('total') or 0)}",
    }
