from __future__ import annotations

import json

import chess
import pytest
from PySide6.QtCore import QSettings

from chess_coach.bootstrap import ApplicationRuntime
from chess_coach.lichess.game_review import load_game_review
from chess_coach.lichess.move_metrics import METRIC_VERSION
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.statistics import StatisticsService
from chess_coach.window import MainWindow


def _seed_run(repo: GameRepository, game_id: str, *, source: str,
              moves: str = "e2e4 e7e5", status: str = "complete",
              assessments: list[dict] | None = None) -> str:
    final_board = chess.Board()
    for uci in moves.split():
        final_board.push_uci(uci)
    winner = ("black" if final_board.turn == chess.WHITE else "white") \
        if final_board.is_checkmate() else "white"
    repo.save_snapshot(game_id, "startpos", moves, "white", "Synthetic", False,
                       "300+0", "mate", winner, started_at="2026-10-01T12:00:00+00:00")
    line = repo.current_line(game_id)
    if assessments is None:
        assessments = [
            {"ply": 0, "white_cp": 0, "depth": 20},
            {"ply": 1, "white_cp": 120, "depth": 20},
            {"ply": 2, "white_cp": 10, "depth": 20},
        ]
    run_id = repo.save_analysis(
        game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="SyntheticFixture" if source == "stockfish" else None,
        engine_version="fixture" if source == "stockfish" else None,
        parameters={"fixture": True}, assessments=assessments,
        classifications=[], status=status)
    account = repo.ensure_archive_account("SyntheticA")
    with repo._connection() as db:
        db.execute("UPDATE games SET source='lichess_archive',source_game_id=?,opening_eco='C20',"
                   "opening_name='King Pawn Game',lichess_accuracy_white=84.5,"
                   "lichess_accuracy_black=77.25 WHERE game_id=?",
                   (f"remote-{game_id}", game_id))
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,"
                   "player_name,opponent) VALUES (?,?,?,?,?,?,?)",
                   (account, game_id, f"remote-{game_id}", "synthetic", "white",
                    "SyntheticA", "Synthetic opponent"))
        db.execute("UPDATE move_metrics SET metric_version='cc-move-metrics-v2',"
                   "accuracy=NULL,accuracy_source=NULL,accuracy_formula_id=NULL,"
                   "wdl_model='python-chess-sf-v1:legacy' WHERE run_id=?", (run_id,))
    return run_id


def test_v2_metrics_for_all_physical_sources_upgrade_without_engine_or_data_loss(
        tmp_path, monkeypatch):
    path = tmp_path / "pre-v20-v2.sqlite3"
    repo = GameRepository(path)
    account = repo.ensure_archive_account("SyntheticA")
    run_ids: dict[str, str] = {}
    sources = ("lichess-pgn", "lichess-json", "stockfish")
    for source in sources:
        for index in range(2):
            game_id = f"{source}-{index}"
            run_ids[game_id] = _seed_run(repo, game_id, source=source)
    review_only = _seed_run(repo, "review-only-v2", source="stockfish")
    stale_run = _seed_run(repo, "stale-line-v2", source="lichess-json")
    repo.save_snapshot("stale-line-v2", "startpos", "d2d4 d7d5", "white",
                       "Synthetic", False, "300+0", "mate", "black",
                       started_at="2026-10-01T12:00:00+00:00")
    with repo._connection() as db:
        db.execute("INSERT INTO analysis_jobs(job_id,game_id,line_revision,line_hash,analysis_pass,"
                   "engine_version,parameters_json,status,result_run_id,created_at,updated_at) "
                   "VALUES ('review-only-job','review-only-v2',1,?,'review','fixture','{}',"
                   "'complete',?,'synthetic','synthetic')",
                   (repo.current_line("review-only-v2")["line_hash"], review_only))
    # Lichess account B is deliberately linked to only one old game.
    account_b = repo.ensure_archive_account("SyntheticB")
    repo.save_snapshot("official-only", "startpos", "e2e4", "white", "Synthetic", False,
                       "300+0", "mate", "white")
    with repo._connection() as db:
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color) "
                   "VALUES (?,?,?,?,?)", (account_b, "lichess-pgn-0", "b-shared", "synthetic", "black"))
        db.execute("UPDATE games SET source='lichess_archive',source_game_id='official-only',"
                   "lichess_accuracy_white=90 WHERE game_id='official-only'")
        old_pgn = {game_id: db.execute("SELECT pgn FROM games WHERE game_id=?",
                                       (game_id,)).fetchone()[0] for game_id in run_ids}
        db.execute("PRAGMA user_version=19")
    repo.shutdown()

    # Reopening applies the supported schema migration; the derived-data upgrade
    # below must still be entirely local and must not launch an engine.
    repo = GameRepository(path)
    try:
        assert repo.count_stale_analysis_metrics() == 6
        assert repo.analysis_metrics_migration_status(account_id=account) == {
            "total": 6, "remaining": 6, "done": 0}
        assert repo.analysis_metrics_migration_status(account_id=account_b) == {
            "total": 1, "remaining": 1, "done": 0}
        monkeypatch.setattr("chess.engine.SimpleEngine.popen_uci",
                            lambda *_a, **_k: pytest.fail("no engine during metric upgrade"))
        repo.materialize_stale_analysis_metrics_batch(limit=2)
        repo.materialize_stale_analysis_metrics_batch(limit=25)
        assert repo.count_stale_analysis_metrics() == 0
        assert repo.materialize_stale_analysis_metrics_batch() == {
            "processed": 0, "total": 0, "remaining": 0}

        stats = StatisticsService(repo)
        summary = stats.summary(account_id=account)
        assert summary["analysis_games"] == 6
        assert summary["scored_positions"] > 0
        assert summary["local_move_accuracy_n"] == 2
        page = stats.game_page(account_id=account, analysis_source="lichess", page_size=20)
        assert page["total"] == 4
        assert all(item["has_position_scores"] for item in page["items"])
        openings = stats.opening_statistics(account_id=account, analysis_source="lichess")
        assert sum(row["games"] for row in openings) == 4
        timeline = stats.historical_timeline(account_id=account, analysis_source="lichess")
        assert sum(row["analyzed_games"] for row in timeline) == 4
        assert sum(row["assessed_own_moves"] for row in timeline) == 4
        assert sum(row["evaluated_own_moves"] for row in openings) > 0

        for game_id, run_id in run_ids.items():
            source = game_id.rsplit("-", 1)[0]
            detail = repo.latest_analysis_detail(game_id, 1, account_id=account)
            assert detail["run_id"] == run_id
            assert detail["source"] == source
            assert detail["move_metrics"]
            if source == "stockfish":
                assert any(row["accuracy"] is not None for row in detail["move_metrics"])
            else:
                assert all(row["accuracy"] is None for row in detail["move_metrics"])
                assert detail["official_game_accuracy"] == 84.5
            with repo._connection() as db:
                coverage = db.execute("SELECT measured_moves FROM analysis_coverage WHERE run_id=?",
                                      (run_id,)).fetchone()[0]
                assert coverage == 2
                assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? AND "
                                  "metric_version='cc-move-metrics-v2'", (run_id,)).fetchone()[0] == 2
                assert db.execute("SELECT pgn FROM games WHERE game_id=?", (game_id,)).fetchone()[0] == old_pgn[game_id]
        with repo._connection() as db:
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? AND "
                              "metric_version=?", (review_only, METRIC_VERSION)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE run_id=? AND "
                              "metric_version=?", (stale_run, METRIC_VERSION)).fetchone()[0] == 0
        assert repo.analysis_metrics_migration_status(account_id=account)["done"] == 6
        assert repo.analysis_metrics_migration_status(account_id=account_b)["done"] == 1
    finally:
        repo.shutdown()


def test_detail_and_progress_choose_scored_run_before_newer_empty_complete(
        tmp_path):
    repo = GameRepository(tmp_path / "run-selection.sqlite3")
    try:
        scored = _seed_run(repo, "selection", source="lichess-json", status="partial")
        empty = repo.save_analysis(
            "selection", line_revision=1, line_hash=repo.current_line("selection")["line_hash"],
            source="stockfish", engine_name="Synthetic", engine_version="fixture",
            parameters={"empty": True}, assessments=[], classifications=[], status="complete")
        with repo._connection() as db:
            db.execute("UPDATE analysis_runs SET finished_at='2999-01-01T00:00:00+00:00' "
                       "WHERE run_id=?", (empty,))
        account = "lichess:synthetica"
        row = StatisticsService(repo).game_page(account_id=account, page_size=10)["items"][0]
        assert row["latest_run_id"] == scored
        assert row["selected_analysis_source"] == "lichess-json"
        game = repo.get_game("selection")
        game.update(account_id=account, account_color="white")
        review = load_game_review(repo, game)
        assert review["analysis"]["run_id"] == row["latest_run_id"]
        assert review["analysis"]["source"] == "lichess-json"
        assert review["analysis"]["assessments"]
        assert any(move.cp_loss is not None for move in review["moves"])
    finally:
        repo.shutdown()


def test_progress_analysis_source_filter_is_preserved_in_review(tmp_path):
    repo = GameRepository(tmp_path / "source-filter-review.sqlite3")
    try:
        lichess_run = _seed_run(repo, "source-filter", source="lichess-json", status="partial")
        line = repo.current_line("source-filter")
        stockfish_run = repo.save_analysis(
            "source-filter", line_revision=1, line_hash=line["line_hash"],
            source="stockfish", engine_name="Synthetic", engine_version="fixture",
            parameters={"newer": True}, assessments=[
                {"ply": 0, "white_cp": -30, "depth": 20},
                {"ply": 1, "white_cp": 180, "depth": 20},
                {"ply": 2, "white_cp": -70, "depth": 20},
            ], classifications=[], status="complete")
        stats = StatisticsService(repo)
        row = stats.game_page(account_id="lichess:synthetica", analysis_source="lichess")["items"][0]
        assert row["latest_run_id"] == lichess_run
        game = repo.get_game("source-filter")
        game.update(account_id="lichess:synthetica", account_color="white")
        filtered_review = load_game_review(repo, game, analysis_source="lichess")
        assert filtered_review["analysis"]["run_id"] == row["latest_run_id"]
        assert filtered_review["analysis"]["source"] == "lichess-json"
        assert repo.latest_analysis_detail("source-filter", 1)["run_id"] == stockfish_run
    finally:
        repo.shutdown()


def test_application_resumes_bounded_v2_materialization_in_background_and_masks_scope(
        qtbot, tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.sqlite3"
    repo = GameRepository(db_path)
    account = repo.ensure_archive_account("SyntheticA")
    for index in range(43):
        _seed_run(repo, f"background-{index:02d}", source=(
            "stockfish" if index % 3 == 0 else
            "lichess-json" if index % 3 == 1 else "lichess-pgn"))
    repo.shutdown()
    settings = QSettings(str(tmp_path / "runtime.ini"), QSettings.Format.IniFormat)
    runtime_repo = GameRepository(db_path)
    # A temporary offline profile with a pre-existing synthetic database proves
    # the real startup hook without Credential Manager or network access.
    runtime = ApplicationRuntime(settings, tmp_path, runtime_repo, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show_background_analytics()
    # The startup coordinator deliberately processes at most ten saved runs per
    # writer batch.  This fixture has 43 runs, so a slower hosted Windows runner
    # needs several event-loop turns and SQLite transactions before it is done.
    qtbot.waitUntil(lambda: runtime_repo.count_stale_analysis_metrics() == 0, timeout=45000)
    qtbot.waitUntil(lambda: "43/43" in window._background_operations_page.local_metrics_status.text(),
                    timeout=5000)
    with runtime_repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM move_metrics WHERE metric_version=?",
                          (METRIC_VERSION,)).fetchone()[0] == 86
        assert db.execute("SELECT COUNT(DISTINCT source) FROM analysis_runs").fetchone()[0] == 3
    window.show_statistics()
    qtbot.waitUntil(lambda: window._statistics_dialog is not None, timeout=1000)
    assert not window._statistics_dialog.metric_migration_status.isHidden()
    window._dashboard_analysis_safety_allowed = False
    window._refresh_dashboard_analysis_visibility()
    window._statistics_dialog.set_metric_migration_status(
        "synthetic metrics ready 43/43", safety_allowed=False)
    assert "скрыт" in window._statistics_dialog.metric_migration_status.text().casefold()
    window.close()


@pytest.mark.parametrize(("moves", "source"), [
    ("f2f3 e7e5 g2g4 d8h4", "lichess-pgn"),  # Qh4#
    ("e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7", "stockfish"),  # Qxf7#
])
def test_legacy_mate_positions_materialize_and_render_final_board(
        qtbot, tmp_path, moves, source):
    repo = GameRepository(tmp_path / f"mate-{len(moves)}.sqlite3")
    try:
        board = chess.Board()
        assessments = [{"ply": 0, "white_cp": 0, "depth": 20}]
        for ply, uci in enumerate(moves.split(), 1):
            board.push_uci(uci)
            assessments.append({"ply": ply, "white_cp": 0, "depth": 20})
        assert board.is_checkmate()
        assessments[-1] = {"ply": len(moves.split()), "mate_white": 0, "depth": 20}
        run_id = _seed_run(repo, f"mate-{len(moves)}", source=source,
                           moves=moves, assessments=assessments)
        repo.materialize_stale_analysis_metrics_batch()
        detail = repo.latest_analysis_detail(f"mate-{len(moves)}", 1)
        assert detail["run_id"] == run_id
        assert detail["move_metrics"][-1]["mate_white_after"] == 0
        if source == "stockfish":
            assert any(row["event_type"] == "checkmate_terminal" for row in detail["events"])
        else:
            assert detail["move_metrics"][-1]["accuracy"] is None

        # The desktop review renders the final stored position from the
        # review model's positions list (the smoke harness used to read a
        # nonexistent `board` key and silently failed to check the GUI board).
        from chess_coach.ui.game_review import GameReviewWorkspace
        workspace = GameReviewWorkspace(repo)
        qtbot.addWidget(workspace)
        workspace.load_game(repo.get_game(f"mate-{len(moves)}"), training_allowed=False)
        workspace.select_ply(len(moves.split()))
        assert workspace.selected_ply == len(review_positions := workspace.review["positions"]) - 1
        assert workspace.review_board._board.fen() == review_positions[-1].fen()
        assert workspace.review_board._board.is_checkmate()
    finally:
        repo.shutdown()
