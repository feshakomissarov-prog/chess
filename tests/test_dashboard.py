from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.dashboard import dashboard_snapshot, game_card_text


def _save(repo, game_id, *, color="white", winner="white", status="mate", opponent="Friend"):
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", color, opponent, False, "5+0",
                       status, winner, white_player="Me", black_player=opponent,
                       started_at="2026-11-02T12:00:00Z")


def test_dashboard_empty_database_uses_unknown_labels_not_fake_zero_rates(tmp_path):
    repo = GameRepository(tmp_path / "dashboard-empty.sqlite3")
    try:
        snapshot = dashboard_snapshot(repo)
        metrics = {row["key"]: row for row in snapshot["metrics"]}
        assert metrics["games"]["value"] == "0"
        assert metrics["accuracy"]["value"] == "н/д"
        assert metrics["analysis"]["value"] == "н/д"
        assert metrics["errors"]["value"] == "н/д"
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
        assert metrics["analysis"]["value"] == "0/13"
        assert metrics["errors"]["value"] == "н/д"
        assert len(snapshot["recent_games"]) == 3
        assert all(row.get("game_id") for row in snapshot["recent_games"])
        assert metrics["accuracy"]["source"].startswith("Lichess accuracy")
    finally:
        repo.shutdown()


def test_recent_game_card_does_not_invent_result_without_owner_color():
    title, detail = game_card_text({
        "opponent": "Unknown color", "color": None, "winner": "white",
        "status": "mate", "started_at": "2026-11-02",
    })
    assert title == "Результат н/д с Unknown color"
    assert "2026-11-02" in detail


def test_dashboard_account_and_clock_filters_match_sql_scope(tmp_path):
    repo = GameRepository(tmp_path / "dashboard-filter.sqlite3")
    try:
        _save(repo, "fast-white", color="white", winner="white")
        _save(repo, "slow-black", color="black", winner="black")
        account = repo.ensure_archive_account("Mikhail")
        with repo._connection() as db:
            db.execute("UPDATE games SET source='lichess_archive',lichess_accuracy_white=91,"
                       "lichess_accuracy_black=82 WHERE game_id='fast-white'")
            db.execute("UPDATE games SET source='lichess_archive',lichess_accuracy_white=60,"
                       "lichess_accuracy_black=70 WHERE game_id='slow-black'")
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                       "VALUES (?,?,?,?,?,?)", (account, "fast-white", "fast-white", "now", "white", "Friend"))
            db.execute("UPDATE games SET clock='5+0' WHERE game_id='fast-white'")
            db.execute("UPDATE games SET clock='15+10' WHERE game_id='slow-black'")
        snapshot = dashboard_snapshot(repo, account_id=account, clock="5+0")
        metrics = {row["key"]: row for row in snapshot["metrics"]}
        assert metrics["games"]["value"] == "1"
        assert metrics["accuracy"]["value"] == "91.0%"
        assert metrics["accuracy"]["detail"].startswith("Официальная точность: 1/1")
        assert snapshot["filter"] == {"account_id": account, "clock": "5+0"}
        assert [game["game_id"] for game in snapshot["recent_games"]] == ["fast-white"]
    finally:
        repo.shutdown()


def test_dashboard_card_reports_current_analysis_coverage_and_unknown_strong_moves(tmp_path):
    from chess_coach.ui.dashboard import game_card_metrics

    repo = GameRepository(tmp_path / "dashboard-card.sqlite3")
    try:
        _save(repo, "card-analysis", color="white")
        line = repo.current_line("card-analysis")
        repo.save_analysis("card-analysis", line_revision=line["line_revision"],
                           line_hash=line["line_hash"], source="stockfish",
                           engine_name="Fixture", engine_version="test", parameters={},
                           status="partial", assessments=[
                               {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 0},
                               {"ply": 2, "white_cp": 300}, {"ply": 3, "white_cp": -100}],
                           classifications=[{"ply": 3, "mover": "white", "eval_loss_cp": 100,
                                             "label": "mistake", "phase": "opening",
                                             "algorithm_version": "test"}])
        card = game_card_metrics(repo, repo.get_game("card-analysis"))
        assert card["status"] == "partial"
        assert card["coverage"] != "0/0"
        assert int(card["mistakes"]) + int(card["blunders"]) >= 1
        assert card["strong"] == "н/д"
        assert "классификатор отсутствует" in card["strong_detail"]
    finally:
        repo.shutdown()
