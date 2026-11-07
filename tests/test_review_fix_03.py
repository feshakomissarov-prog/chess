from __future__ import annotations

from datetime import datetime, timezone

from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.statistics import StatisticsService


def _save_game(repo: GameRepository, game_id: str) -> dict:
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", "white", "Fixture",
                       False, "5+0", "finished", "white",
                       started_at=datetime.now(timezone.utc).isoformat())
    return repo.current_line(game_id)


def _save_run(repo: GameRepository, game_id: str, line: dict, source: str,
              *, status: str = "complete", scores: list[dict] | None = None,
              classifications: list[dict] | None = None) -> str:
    return repo.save_analysis(
        game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="Fixture", engine_version="synthetic",
        parameters={}, status=status, assessments=scores or [],
        classifications=classifications or [])


def test_lichess_progress_filter_is_pgn_json_union_and_matches_library(tmp_path):
    repo = GameRepository(tmp_path / "review-fix-03-source-union.sqlite3")
    try:
        line = _save_game(repo, "pgn-only")
        _save_run(repo, "pgn-only", line, "lichess-pgn", scores=[{"ply": 1, "white_cp": 15}])

        line = _save_game(repo, "json-only")
        _save_run(repo, "json-only", line, "lichess-json", scores=[
            {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 20}])
        # A newer completed but empty run must not hide the scored partial run.
        _save_run(repo, "json-only", line, "lichess-json", status="complete")

        line = _save_game(repo, "both-sources")
        _save_run(repo, "both-sources", line, "lichess-pgn", scores=[{"ply": 1, "white_cp": 10}])
        _save_run(repo, "both-sources", line, "lichess-json", scores=[{"ply": 1, "white_cp": 30}])

        line = _save_game(repo, "stockfish-only")
        _save_run(repo, "stockfish-only", line, "stockfish", scores=[{"ply": 1, "white_cp": 45}])

        _save_game(repo, "accuracy-only")
        with repo._connection() as db:
            db.execute("UPDATE games SET lichess_accuracy_white=91,lichess_accuracy_black=88 "
                       "WHERE game_id='accuracy-only'")

        _save_game(repo, "metadata-only")
        account_a = repo.ensure_archive_account("fixture-a")
        account_b = repo.ensure_archive_account("fixture-b")
        metadata_line = repo.current_line("metadata-only")
        with repo._connection() as db:
            db.execute("INSERT INTO lichess_ready_analysis_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (account_a, "metadata-only", metadata_line["line_revision"],
                        "lichess-json", "fixture-v1", "{}", "{}", "[]", "[]", "fixture"))

        stale_line = _save_game(repo, "stale-line")
        _save_run(repo, "stale-line", stale_line, "lichess-json",
                  scores=[{"ply": 1, "white_cp": 12}])
        repo.save_snapshot("stale-line", "startpos", "d2d4 d7d5", "white", "Fixture",
                           False, "5+0", "finished", "white")

        line = _save_game(repo, "classification-only")
        _save_run(repo, "classification-only", line, "lichess-json", classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 130, "label": "mistake",
             "phase": "opening", "algorithm_version": "fixture"}])

        # The same imported game may belong to multiple accounts; union queries
        # remain distinct by game ID and preserve each account's scope.
        with repo._connection() as db:
            for account, remote_id in ((account_a, "json-a"), (account_b, "json-b")):
                db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                           "VALUES (?,?,?,?,?,?)",
                           (account, "json-only", remote_id, "fixture", "white", "Fixture"))

        library = repo.search_games(analysis_source="lichess")
        library_ids = {item["game_id"] for item in library["items"]}
        assert library["total"] == 3
        assert library_ids == {"pgn-only", "json-only", "both-sources"}
        assert repo.search_games(account_id=account_a, analysis_source="lichess")["total"] == 1
        assert repo.search_games(account_id=account_b, analysis_source="lichess")["total"] == 1

        service = StatisticsService(repo)
        summary = service.summary(analysis_source="lichess")
        page = service.game_page(analysis_source="lichess")
        assert summary["games"] == summary["analysis_games"] == 3
        assert summary["scored_positions"] == 4
        assert page["total"] == 3
        assert {item["game_id"] for item in page["items"]} == library_ids
        assert service.summary(analysis_source="stockfish")["analysis_games"] == 1
        assert service.game_page(analysis_source="stockfish")["total"] == 1
        assert service.summary(analysis_source="lichess", analyzed="yes")["games"] == 3

        # Secondary progress views apply the same source union and account scope.
        openings = service.opening_statistics(account_id=account_a, analysis_source="lichess")
        timeline = service.historical_timeline(account_id=account_a, analysis_source="lichess")
        profile = service.personal_profile(account_id=account_a, analysis_source="lichess")
        assert sum(item["games"] for item in openings) == 1
        assert sum(item["games"] for item in timeline) == 1
        assert profile["historical"]["games"] == 1
        assert sum(item["games"] for item in profile["historical_timeline"]) == 1
    finally:
        repo.shutdown()


def test_analysis_coverage_requires_current_position_scores_and_prefers_scored_run(tmp_path):
    repo = GameRepository(tmp_path / "review-fix-03-scored-coverage.sqlite3")
    try:
        _save_game(repo, "classification-only")
        line = repo.current_line("classification-only")
        _save_run(repo, "classification-only", line, "stockfish", classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 150, "label": "blunder",
             "phase": "opening", "algorithm_version": "fixture"}])

        _save_game(repo, "accuracy-only")
        with repo._connection() as db:
            db.execute("UPDATE games SET lichess_accuracy_white=93 WHERE game_id='accuracy-only'")

        line = _save_game(repo, "scored-partial")
        _save_run(repo, "scored-partial", line, "stockfish", status="partial", scores=[
            {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": -100}], classifications=[
                {"ply": 1, "mover": "white", "eval_loss_cp": 100, "label": "mistake",
                 "phase": "opening", "algorithm_version": "fixture"}])
        # This run is newer, complete, and has no CP/mate scores. It must not
        # displace the earlier scored line in summary or page selection.
        _save_run(repo, "scored-partial", line, "stockfish", status="complete")

        line = _save_game(repo, "scored-complete")
        _save_run(repo, "scored-complete", line, "stockfish", scores=[
            {"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 20}])

        line = _save_game(repo, "stale-scored")
        _save_run(repo, "stale-scored", line, "stockfish", scores=[{"ply": 1, "white_cp": 8}])
        repo.save_snapshot("stale-scored", "startpos", "d2d4 d7d5", "white", "Fixture",
                           False, "5+0", "finished", "white")

        review_line = _save_game(repo, "review-only")
        candidate = {"game_id": "review-only", "line_revision": review_line["line_revision"],
                     "line_hash": review_line["line_hash"], "started_at": None}
        parameters = {"pass": "review"}
        repo.enqueue_analysis_jobs([candidate], analysis_pass="review",
                                   engine_version="fixture-review", parameters=parameters)
        job = repo.claim_next_analysis_job("fixture-worker", engine_version="fixture-review",
                                           analysis_pass="review", parameters=parameters)
        assert job is not None
        review_run = _save_run(repo, "review-only", review_line, "stockfish",
                               scores=[{"ply": 1, "white_cp": 50}])
        repo.finish_analysis_job(job["job_id"], "fixture-worker", status="complete",
                                 result_run_id=review_run)

        service = StatisticsService(repo)
        summary = service.summary()
        assert summary["games"] == 6
        assert summary["analysis_games"] == 2
        assert summary["scored_positions"] == 4
        assert summary["analysis_coverage"] == round(200 / 6, 1)
        assert summary["own_assessed_moves"] > 0
        assert summary["classifications_by_phase"].get("unknown", {}).get("mine", {}).get("mistake") == 1
        selected = {item["game_id"]: item for item in service.game_page()["items"]}
        assert selected["scored-partial"]["analysis_status"] == "partial"
        assert selected["scored-partial"]["scored_positions"] == 2
        assert selected["classification-only"]["scored_positions"] == 0
        assert service.summary(analysis_source="stockfish", analyzed="yes")["games"] == 2

        empty = service.summary(analysis_source="nonexistent-source")
        assert empty["games"] == 0
        assert empty["analysis_games"] == 0
        assert empty["analysis_coverage"] is None
    finally:
        repo.shutdown()


def test_statistics_dialog_uses_lichess_union_and_hides_cached_values_on_revoke(
        qtbot, tmp_path):
    from chess_coach.ui.analytics_dialogs import StatisticsDialog

    repo = GameRepository(tmp_path / "review-fix-03-dialog.sqlite3")
    try:
        _save_game(repo, "dialog-classification-only")
        line = repo.current_line("dialog-classification-only")
        _save_run(repo, "dialog-classification-only", line, "lichess-json", classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 100, "label": "mistake",
             "phase": "opening", "algorithm_version": "fixture"}])

        dialog = StatisticsDialog(repo)
        qtbot.addWidget(dialog)
        dialog.show()
        lichess_index = dialog.analysis_source.findData("lichess")
        assert lichess_index >= 0
        assert "PGN + JSON" in dialog.analysis_source.itemText(lichess_index)
        qtbot.waitUntil(lambda: dialog._query_thread is None and
                        dialog.summary_label.text().startswith("Партии:"), timeout=5000)
        assert "нет подтверждённых оценок позиций" in dialog.summary_label.text()
        dialog.analysis_source.setCurrentIndex(lichess_index)
        assert dialog._filters()["analysis_source"] == "lichess"

        # The numeric data may remain cached internally, but revocation must
        # hide its actual widgets from the visible Qt hierarchy.
        dialog.set_safety_display_allowed(True)
        assert dialog.tabs.isVisibleTo(dialog)
        assert dialog.summary_label.isVisibleTo(dialog)
        dialog.set_safety_display_allowed(False, "Разрешение отозвано.")
        qtbot.wait(1)
        assert dialog.tabs.isHidden()
        assert dialog.analysis_mask.isVisibleTo(dialog)
        assert not dialog.summary_label.isVisibleTo(dialog)
        assert not dialog.games.isVisibleTo(dialog)
        dialog.close()
    finally:
        repo.shutdown()
