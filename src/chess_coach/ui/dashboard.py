"""Bounded dashboard reads backed by existing SQLite facts."""

from __future__ import annotations

from typing import Any

from ..lichess.game_review import load_game_review
from ..lichess.statistics import StatisticsService
from .display import (format_clock_control, format_game_date, opening_tooltip,
                      russian_opening_name)


_COMPLETED = "('created','started','started_soon','playing')"


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
            f"AVG(CASE {color} WHEN 'white' THEN g.lichess_accuracy_white "
            "WHEN 'black' THEN g.lichess_accuracy_black END) AS owner_accuracy,"
            f"COUNT(DISTINCT CASE WHEN (CASE {color} WHEN 'white' THEN g.lichess_accuracy_white "
            "WHEN 'black' THEN g.lichess_accuracy_black END) IS NOT NULL "
            "AND (g.source IN ('lichess','lichess_archive') OR g.source_game_id IS NOT NULL) "
            "THEN g.game_id END) AS accuracy_games "
            f"FROM games g{join} WHERE {where}", params).fetchone()

        analysis_where = " AND ".join(clauses)
        run_params = list(params)
        status = db.execute(
            "WITH scoped AS (SELECT DISTINCT g.game_id FROM games g" + join +
            " WHERE " + analysis_where + "), ranked AS (SELECT ar.game_id,ar.status,"
            "ROW_NUMBER() OVER(PARTITION BY ar.game_id,ar.line_revision ORDER BY "
            "CASE WHEN ar.status='complete' THEN 0 ELSE 1 END,ar.finished_at DESC,ar.run_id DESC) rn "
            "FROM analysis_runs ar JOIN game_revisions gr ON gr.game_id=ar.game_id "
            "AND gr.line_revision=ar.line_revision AND gr.is_current=1 JOIN scoped s ON s.game_id=ar.game_id "
            "WHERE ar.status IN ('complete','partial') AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj "
            "WHERE aj.result_run_id=ar.run_id AND aj.analysis_pass='review')) "
            "SELECT COUNT(CASE WHEN status='complete' THEN 1 END) complete,"
            "COUNT(CASE WHEN status='partial' THEN 1 END) partial FROM ranked WHERE rn=1",
            run_params).fetchone()

    summary = StatisticsService(repository).summary(account_id=account_id, clock=clock)
    total = int(row["games"] or 0)
    accuracy_n = int(row["accuracy_games"] or 0)
    accuracy = row["owner_accuracy"]
    analyzed_complete = int(status["complete"] or 0)
    analyzed_partial = int(status["partial"] or 0)
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
        {"key": "analysis", "label": "Анализ моих ходов",
         "value": f"{assessed}/{own_total}" if own_total else "н/д",
         "detail": (f"Полных партий: {analyzed_complete} · частичных: {analyzed_partial} · "
                    f"охват собственных решений {round(100 * assessed / own_total)}%"
                    if own_total else
                    f"Полных партий: {analyzed_complete} · частичных: {analyzed_partial} · "
                    "нет известных собственных решений"),
         "source": "Текущие analysis_runs + move_metrics; собственные decision pairs"},
        {"key": "errors", "label": "Ошибки на 100 оценённых ходов",
         "value": f"{float(error_rate):.1f}" if error_rate is not None else "н/д",
         "detail": (f"Ошибки {int(summary['own_error_count'])} / {assessed} оценённых собственных ходов · "
                    f"всего собственных ходов {own_total}"),
         "source": "Текущие версии cc-move-metrics-v2; знаменатель: оценённые собственные ходы"},
    ]
    recent = repository.search_games(completed_only=True, page=0, page_size=3,
                                     account_id=account_id, clock=clock)
    games = [dict(game) for game in recent["items"]]
    for game in games:
        game["review_metrics"] = game_card_metrics(repository, game)
    return {"metrics": metrics, "recent_games": games,
            "scope": scope, "filter": {"account_id": account_id, "clock": clock}}


def game_card_metrics(repository, game: dict[str, Any]) -> dict[str, str]:
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
    coverage = int(side.get("assessed") or 0)
    total = int(side.get("total") or 0)
    errors = int(side.get("inaccuracy") or 0) + int(side.get("mistake") or 0) + int(side.get("blunder") or 0)
    # Current detectors do not yet implement a verified "strong move" fact.
    # Candidate best_move_confirmed is intentionally not presented as verified.
    return {
        "accuracy": f"{float(own_color_accuracy):.1f}%" if own_color_accuracy is not None else "н/д",
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


def game_card_text(game: dict[str, Any]) -> tuple[str, str]:
    opponent = str(game.get("account_opponent") or game.get("opponent") or "Соперник неизвестен")
    color = game.get("account_color") or game.get("color")
    winner = game.get("winner")
    if game.get("status") in {"draw", "stalemate", "repetition", "50moves", "insufficientMaterial"}:
        result = "Ничья"
    elif color not in {"white", "black"} or winner not in {"white", "black"}:
        result = "Результат н/д"
    else:
        result = "Победа" if winner == color else "Поражение"
    details = " · ".join((
        format_game_date(game.get("started_at"), precision=game.get("game_time_precision")),
        format_clock_control(game.get("clock")),
        russian_opening_name(game.get("eco"), game.get("opening_name")),
    ))
    return f"{result} с {opponent}", details + "\n" + opening_tooltip(game.get("opening_name"))
