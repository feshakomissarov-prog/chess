from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.dashboard import dashboard_snapshot, game_card_text


def _save(repo, game_id, *, color="white", winner="white", status="mate", opponent="Friend"):
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", color, opponent, False, "5+0",
                       status, winner, white_player="Me", black_player=opponent,
                       started_at="2026-11-02T12:00:00Z")


def _save_scored(repo, game_id, source):
    _save(repo, game_id, opponent=f"{source} opponent")
    line = repo.current_line(game_id)
    repo.save_analysis(
        game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="Synthetic" if source == "stockfish" else "Lichess",
        engine_version="test", parameters={}, status="complete",
        assessments=[{"ply": ply, "white_cp": 25 * ply} for ply in range(4)],
        classifications=[])


def test_dashboard_empty_database_uses_unknown_labels_not_fake_zero_rates(tmp_path):
    repo = GameRepository(tmp_path / "dashboard-empty.sqlite3")
    try:
        snapshot = dashboard_snapshot(repo)
        metrics = {row["key"]: row for row in snapshot["metrics"]}
        assert metrics["games"]["value"] == "0"
        assert metrics["accuracy"]["value"] == "н/д"
        assert metrics["analysis"]["value"] == "0/0 · н/д"
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
        assert metrics["analysis"]["value"] == "0/7 (0.0%)"
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
    assert "02.11.2026" in detail


def test_source_filters_and_dashboard_count_distinct_usable_current_line_games(tmp_path):
    repo = GameRepository(tmp_path / "source-counts.sqlite3")
    try:
        for index in range(10):
            _save_scored(repo, f"sf-{index}", "stockfish")
        for index in range(8):
            _save_scored(repo, f"pgn-{index}", "lichess-pgn")
        for index in range(4):
            _save_scored(repo, f"json-{index}", "lichess-json")
        for index in range(2):
            _save_scored(repo, f"both-{index}", "stockfish")
            line = repo.current_line(f"both-{index}")
            repo.save_analysis(f"both-{index}", line_revision=line["line_revision"],
                               line_hash=line["line_hash"], source="lichess-pgn",
                               engine_name="Lichess", engine_version=None, parameters={},
                               status="partial",
                               assessments=[{"ply": 1, "white_cp": 10}], classifications=[])
        for index in range(3):
            _save(repo, f"official-{index}")
            with repo._connection() as db:
                db.execute("UPDATE games SET source='lichess_archive',source_game_id=?,"
                           "lichess_accuracy_white=90,lichess_accuracy_black=80 WHERE game_id=?",
                           (f"remote-{index}", f"official-{index}"))
        for index in range(4):
            _save(repo, f"metadata-empty-{index}")
        account = repo.ensure_archive_account("metadata-fixture")
        for index in range(4):
            game_id = f"metadata-empty-{index}"
            revision = repo.current_line(game_id)["line_revision"]
            with repo._connection() as db:
                db.execute("INSERT INTO lichess_ready_analysis_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (account, game_id, revision, "lichess-json", "fixture-v1",
                            "{}", "{}", "[]", "[]", "2026-11-04T00:00:00Z"))
        classification_game = "metadata-empty-0"
        classification_line = repo.current_line(classification_game)
        repo.save_analysis(
            classification_game, line_revision=classification_line["line_revision"],
            line_hash=classification_line["line_hash"], source="stockfish",
            engine_name="Fixture", engine_version="test", parameters={}, status="complete",
            assessments=[], classifications=[{"ply": 1, "mover": "white",
                                                "label": "mistake", "eval_loss_cp": 120}])
        for index in range(3):
            game_id = f"stale-{index}"
            _save_scored(repo, game_id, "stockfish")
            repo.save_snapshot(game_id, "startpos", "d2d4 d7d5 g1f3", "white",
                               "Changed line", False, "5+0", "mate", "white")
        _save(repo, "no-data")

        scoped_account = repo.ensure_archive_account("analysis-scope-fixture")
        with repo._connection() as db:
            for game_id in ("sf-0", classification_game):
                db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                           "VALUES (?,?,?,?,?,?)",
                           (scoped_account, game_id, game_id, "fixture", "white", "Opponent"))

        assert repo.search_games(analysis="yes")["total"] == 24
        assert repo.search_games(analysis_source="stockfish")["total"] == 12
        assert repo.search_games(analysis_source="lichess")["total"] == 14
        assert repo.search_games(analysis_source="official_accuracy")["total"] == 3
        assert repo.search_games(analysis="no")["total"] == 11
        assert repo.search_games(analysis="yes", analysis_source="stockfish")["total"] == 12
        assert repo.search_games(analysis="yes", analysis_source="lichess")["total"] == 14
        assert repo.search_games(analysis="yes")["total"] == 24
        assert repo.search_games(analysis="no")["total"] == 11
        from chess_coach.lichess.statistics import StatisticsService
        assert StatisticsService(repo).summary(analyzed="yes")["games"] == 24
        assert StatisticsService(repo).summary(analyzed="no")["games"] == 11
        assert repo.search_games(account_id=scoped_account, analysis="yes")["total"] == 1
        assert repo.search_games(account_id=scoped_account, analysis="no")["total"] == 1
        assert StatisticsService(repo).summary(
            account_id=scoped_account, analyzed="yes")["games"] == 1
        assert StatisticsService(repo).summary(
            account_id=scoped_account, analyzed="no")["games"] == 1
        scoped_metrics = {row["key"]: row for row in dashboard_snapshot(
            repo, account_id=scoped_account)["metrics"]}
        assert scoped_metrics["analysis"]["value"] == "1/2 (50.0%)"
        assert "Полный охват: 1 · частичный: 0" in scoped_metrics["analysis"]["detail"]
        metrics = {row["key"]: row for row in dashboard_snapshot(repo)["metrics"]}
        assert metrics["analysis"]["value"] == "24/35 (68.6%)"
        assert "Полный охват: 24 · частичный: 0" in metrics["analysis"]["detail"]
        assert "Stockfish: 12" in metrics["analysis"]["detail"]
        assert "Lichess с оценками: 14" in metrics["analysis"]["detail"]
    finally:
        repo.shutdown()


def test_home_activity_trend_uses_bounded_saved_puzzle_history_or_honest_empty_state(tmp_path):
    from datetime import datetime, timezone

    repo = GameRepository(tmp_path / "home-puzzle-trend.sqlite3")
    try:
        account = repo.ensure_archive_account("trend-fixture")
        assert dashboard_snapshot(repo, account_id=account)["activity_trend"] == []
        records = []
        for index, (month, rating) in enumerate((("2026-09", 1450), ("2026-10", 1500),
                                                  ("2026-11", 1540))):
            stamp = datetime.fromisoformat(f"{month}-05T12:00:00+00:00").timestamp()
            records.append({"date": int(stamp * 1000) + index, "win": True,
                            "puzzle": {"id": f"trend-{index}", "fen": "startpos",
                                       "solution": ["e2e4"], "themes": ["opening"],
                                       "rating": rating, "plays": 100}})
        repo.import_puzzle_activities("trend-fixture", records)
        snapshot = dashboard_snapshot(repo, account_id=account)
        assert len(snapshot["activity_trend"]) == 3
        assert [item["average_rating"] for item in snapshot["activity_trend"]] == [1450, 1500, 1540]
        assert snapshot["activity_trend_source"] == "Lichess puzzle attempts saved locally"
    finally:
        repo.shutdown()


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


def test_home_and_library_order_by_game_activity_not_recent_metadata_updates(tmp_path):
    repo = GameRepository(tmp_path / "chronology.sqlite3")
    try:
        _save(repo, "played-yesterday", opponent="Yesterday")
        _save(repo, "started-month-ago", opponent="Month ago")
        _save(repo, "date-unknown", opponent="Unknown date")
        with repo._connection() as db:
            db.execute("UPDATE games SET started_at=?,game_time_precision='timestamp',last_move_at=? "
                       "WHERE game_id='played-yesterday'",
                       ("2026-11-03T08:00:00Z", 1793700000000))
            db.execute("UPDATE games SET started_at=?,game_time_precision='timestamp',"
                       "last_move_at=NULL,updated_at=? WHERE game_id='started-month-ago'",
                       ("2026-10-04T10:00:00Z", "2026-11-04T12:00:00Z"))
            db.execute("UPDATE games SET started_at=NULL,game_time_precision='unknown',"
                       "last_move_at=NULL,updated_at=? WHERE game_id='date-unknown'",
                       ("2026-11-04T13:00:00Z",))
        expected = ["played-yesterday", "started-month-ago", "date-unknown"]
        assert [g["game_id"] for g in repo.search_games(
            completed_only=True, order_by_game_time=True)["items"]] == expected
        assert [g["game_id"] for g in dashboard_snapshot(repo)["recent_games"]] == expected
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
