"""Application-owned bounded dashboard SQL queries and read models."""

from __future__ import annotations

import threading

from typing import Any

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
            self.repository, account_id=account_id, clock=clock)

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
                       clock: str | None = None) -> dict[str, Any]:
    """Return scoped KPIs plus at most three completed games.

    All archive-wide counts are SQL aggregates. Only three current game lines are
    loaded for cards, and this function never parses stored PGN or starts UCI.
    """
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
            "'insufficientMaterial') THEN g.game_id END) AS draws,"
            f"AVG(CASE WHEN g.source IN ('lichess','lichess_archive') OR g.source_game_id IS NOT NULL THEN CASE {color} WHEN 'white' THEN g.lichess_accuracy_white "
            "WHEN 'black' THEN g.lichess_accuracy_black END END) AS owner_accuracy,"
            f"COUNT(DISTINCT CASE WHEN (CASE {color} WHEN 'white' THEN g.lichess_accuracy_white "
            "WHEN 'black' THEN g.lichess_accuracy_black END) IS NOT NULL "
            "AND (g.source IN ('lichess','lichess_archive') OR g.source_game_id IS NOT NULL) "
            "THEN g.game_id END) AS accuracy_games "
            f"FROM games g{join} WHERE {where}", params).fetchone()

        analysis_where = " AND ".join(clauses)
        run_params = list(params)
    with repository._connection() as db:
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

    summary = StatisticsService(repository).summary(account_id=account_id, clock=clock)
    activity_trend = (repository.puzzle_rating_trend(account_id, limit=6)
                      if account_id and hasattr(repository, "puzzle_rating_trend") else [])
    total = int(row["games"] or 0)
    accuracy_n = int(row["accuracy_games"] or 0)
    accuracy = row["owner_accuracy"]
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
        {"key": "accuracy", "label": "Точность Lichess", "value":
         f"{float(accuracy):.1f}%" if accuracy is not None and accuracy_n else "н/д",
         "detail": f"Официальная точность: {accuracy_n}/{total} партий с данными" if total else
         "Нет завершённых партий с официальной оценкой",
         "source": "Lichess accuracy; локальная accuracy не рассчитывается"},
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
                                     order_by_game_time=True)
    games = [dict(game) for game in recent["items"]]
    for game in games:
        game["review_metrics"] = game_card_metrics(repository, game)
    return {"metrics": metrics, "recent_games": games,
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
    total = int(side.get("total") or 0)
    errors = int(side.get("inaccuracy") or 0) + int(side.get("mistake") or 0) + int(side.get("blunder") or 0)
    # Current detectors do not yet implement a verified "strong move" fact.
    # Candidate best_move_confirmed is intentionally not presented as verified.
    return {
        "accuracy": f"{float(own_color_accuracy):.1f}%" if own_color_accuracy is not None else "н/д",
        "accuracy_source": accuracy_source,
        "assessments": list(analysis.get("assessments") or []),
        "moves": {int(move.ply): str(move.san) for move in view.get("moves") or []},
        "errors": str(errors) if coverage else "н/д",
        "inaccuracies": str(int(side.get("inaccuracy") or 0)) if coverage else "н/д",
        "mistakes": str(int(side.get("mistake") or 0)) if coverage else "н/д",
        "blunders": str(int(side.get("blunder") or 0)) if coverage else "н/д",
        "strong": "н/д",
        "status": str(analysis.get("status") or "без анализа"),
        "coverage": f"{coverage}/{total}",
        "source": str(analysis.get("source") or "нет данных"),
        "strong_detail": "подтверждённый классификатор отсутствует",
        "owner_accuracy": f"{float(own_color_accuracy):.1f}%" if own_color_accuracy is not None else "н/д",
        "opponent_accuracy": f"{float(opponent_accuracy):.1f}%" if opponent_accuracy is not None else "н/д",
        "owner_inaccuracies": str(int(side.get("inaccuracy") or 0)) if coverage else "н/д",
        "owner_mistakes": str(int(side.get("mistake") or 0)) if coverage else "н/д",
        "owner_blunders": str(int(side.get("blunder") or 0)) if coverage else "н/д",
        "opponent_inaccuracies": str(int(opponent_side.get("inaccuracy") or 0))
        if opponent_side.get("assessed") else "н/д",
        "opponent_mistakes": str(int(opponent_side.get("mistake") or 0))
        if opponent_side.get("assessed") else "н/д",
        "opponent_blunders": str(int(opponent_side.get("blunder") or 0))
        if opponent_side.get("assessed") else "н/д",
        "opponent_coverage": f"{int(opponent_side.get('assessed') or 0)}/{int(opponent_side.get('total') or 0)}",
    }
