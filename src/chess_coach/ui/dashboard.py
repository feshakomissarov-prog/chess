"""Bounded, read-only dashboard data sourced from the existing SQLite repository."""

from __future__ import annotations

from typing import Any


def dashboard_snapshot(repository) -> dict[str, Any]:
    """Return four honest KPIs and at most three recent completed games.

    Aggregates run in SQLite. This never loads move rows, PGN bodies or all games.
    """
    repository._wait_before_read()
    with repository._connection() as db:
        games = db.execute(
            "SELECT COUNT(*) FROM games WHERE status NOT IN "
            "('created','started','started_soon','playing')").fetchone()[0]
        outcome = db.execute(
            "SELECT COUNT(CASE WHEN g.winner=g.color THEN 1 END) AS wins,"
            "COUNT(CASE WHEN g.winner IS NOT NULL AND g.winner<>g.color "
            "AND g.status NOT IN ('draw','stalemate','repetition','50moves',"
            "'insufficientMaterial') THEN 1 END) AS losses,"
            "COUNT(CASE WHEN g.status IN ('draw','stalemate','repetition','50moves',"
            "'insufficientMaterial') THEN 1 END) AS draws,"
            "COUNT(CASE WHEN g.color IN ('white','black') AND "
            "(g.winner IN ('white','black') OR g.status IN ('draw','stalemate','repetition',"
            "'50moves','insufficientMaterial')) THEN 1 END) AS known "
            "FROM games g WHERE g.status NOT IN ('created','started','started_soon','playing')"
        ).fetchone()
        analyzed = db.execute(
            "SELECT COUNT(DISTINCT ar.game_id) FROM analysis_runs ar "
            "JOIN game_revisions gr ON gr.game_id=ar.game_id AND gr.line_revision=ar.line_revision "
            "WHERE gr.is_current=1 AND ar.status IN ('complete','partial') "
            "AND NOT EXISTS (SELECT 1 FROM analysis_jobs aj WHERE aj.result_run_id=ar.run_id "
            "AND aj.analysis_pass='review')"
        ).fetchone()[0]
        puzzles = db.execute(
            "SELECT COUNT(*) AS attempts,COALESCE(SUM(win),0) AS wins FROM puzzle_attempts"
        ).fetchone()

    recent_page = repository.search_games(completed_only=True, page=0, page_size=3)
    known = int(outcome["known"] or 0)
    total_games = int(games or 0)
    analyzed_count = int(analyzed or 0)
    puzzle_attempts = int(puzzles["attempts"] or 0)
    return {
        "metrics": [
            {"key": "games", "label": "Завершённые партии", "value": str(total_games),
             "detail": "Локальная библиотека", "source": "SQLite: games"},
            {"key": "record", "label": "Мои результаты",
             "value": (f"{int(outcome['wins'] or 0)} / {int(outcome['draws'] or 0)} / "
                       f"{int(outcome['losses'] or 0)}") if known else "н/д",
             "detail": f"Победы / ничьи / поражения · {known} с известным цветом",
             "source": "SQLite: games.color + winner"},
            {"key": "analysis", "label": "Покрытие анализа",
             "value": f"{round(100 * analyzed_count / total_games)}%" if total_games else "н/д",
             "detail": f"{analyzed_count} из {total_games} текущих линий",
             "source": "SQLite: current analysis_runs"},
            {"key": "puzzles", "label": "Задачи Lichess",
             "value": f"{int(puzzles['wins'] or 0)} / {puzzle_attempts}" if puzzle_attempts else "н/д",
             "detail": "Решено / попыток · локальная история",
             "source": "SQLite: puzzle_attempts"},
        ],
        "recent_games": list(recent_page["items"]),
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
    details = " · ".join(filter(None, (str(game.get("started_at") or "Дата н/д"),
                                         str(game.get("clock") or "Контроль н/д"),
                                         str(game.get("opening_name") or ""))))
    return f"{result} с {opponent}", details
