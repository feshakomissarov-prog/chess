from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.dashboard import dashboard_snapshot, game_card_text


def _save(repo, game_id, *, color="white", winner="white", status="mate", opponent="Friend"):
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5", color, opponent, False, "5+0",
                       status, winner, white_player="Me", black_player=opponent,
                       started_at="2026-11-02T12:00:00Z")


def test_dashboard_empty_database_uses_unknown_labels_not_fake_zero_rates(tmp_path):
    repo = GameRepository(tmp_path / "dashboard-empty.sqlite3")
    try:
        snapshot = dashboard_snapshot(repo)
        metrics = {row["key"]: row for row in snapshot["metrics"]}
        assert metrics["games"]["value"] == "0"
        assert metrics["record"]["value"] == "н/д"
        assert metrics["analysis"]["value"] == "н/д"
        assert metrics["puzzles"]["value"] == "н/д"
        assert snapshot["recent_games"] == []
    finally:
        repo.shutdown()


def test_dashboard_aggregates_real_sqlite_results_and_returns_only_three_recent_games(tmp_path):
    repo = GameRepository(tmp_path / "dashboard-data.sqlite3")
    try:
        _save(repo, "dashboard-win")
        _save(repo, "dashboard-draw", color="black", winner=None, status="draw", opponent="Draw")
        for index in range(5):
            _save(repo, f"dashboard-{index}", opponent=f"Opponent {index}")
        snapshot = dashboard_snapshot(repo)
        metrics = {row["key"]: row for row in snapshot["metrics"]}
        assert metrics["games"]["value"] == "7"
        assert metrics["record"]["value"] == "6 / 1 / 0"
        assert metrics["analysis"]["value"] == "0%"
        assert len(snapshot["recent_games"]) == 3
        assert all(row.get("game_id") for row in snapshot["recent_games"])
        assert metrics["record"]["source"] == "SQLite: games.color + winner"
    finally:
        repo.shutdown()


def test_recent_game_card_does_not_invent_result_without_owner_color():
    title, detail = game_card_text({
        "opponent": "Unknown color", "color": None, "winner": "white",
        "status": "mate", "started_at": "2026-11-02",
    })
    assert title == "Результат н/д с Unknown color"
    assert "2026-11-02" in detail
