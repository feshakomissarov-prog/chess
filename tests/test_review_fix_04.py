from __future__ import annotations

import threading
import time

import shiboken6
from PySide6.QtCore import QSettings

from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.statistics import StatisticsService
from chess_coach.ui.analytics_dialogs import StatisticsDialog
from chess_coach.ui.statistics_workers import StatisticsWorkerHub
from chess_coach.window import MainWindow


def _save_game(repo, game_id, *, started_at="2026-11-03T12:00:00+00:00",
               color="white", status="finished", winner="white"):
    repo.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", color, "Fixture",
                       False, "5+0", status, winner, started_at=started_at)
    return repo.current_line(game_id)


def _save_analysis(repo, game_id, source, *, scores=None, status="complete",
                   classifications=None):
    line = repo.current_line(game_id)
    return repo.save_analysis(
        game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
        source=source, engine_name="Fixture", engine_version="synthetic", parameters={},
        status=status, assessments=scores or [], classifications=classifications or [])


def _save_review_run(repo, game_id):
    line = repo.current_line(game_id)
    candidate = {"game_id": game_id, "line_revision": line["line_revision"],
                 "line_hash": line["line_hash"], "started_at": None}
    parameters = {"pass": "review"}
    repo.enqueue_analysis_jobs([candidate], analysis_pass="review",
                               engine_version="review-fixture", parameters=parameters)
    job = repo.claim_next_analysis_job("review-fixture-worker", engine_version="review-fixture",
                                       analysis_pass="review", parameters=parameters)
    assert job is not None
    run_id = _save_analysis(repo, game_id, "lichess-json",
                            scores=[{"ply": 1, "white_cp": 35}])
    repo.finish_analysis_job(job["job_id"], "review-fixture-worker", status="complete",
                             result_run_id=run_id)


def test_lichess_history_and_opening_slices_match_distinct_scored_library_scope(tmp_path):
    repo = GameRepository(tmp_path / "review-fix-04-history.sqlite3")
    try:
        account = repo.ensure_archive_account("history-owner")
        other_account = repo.ensure_archive_account("other-owner")
        ids = []
        for game_id, source, color in (
                ("json-white-1", "lichess-json", "white"),
                ("json-black-2", "lichess-json", "black"),
                ("pgn-white-3", "lichess-pgn", "white"),
                ("sf-white-1", "stockfish", "white"),
                ("sf-black-2", "stockfish", "black"),
                ("sf-white-3", "stockfish", "white")):
            _save_game(repo, game_id, color=color)
            _save_analysis(repo, game_id, source,
                           scores=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 25}])
            ids.append(game_id)

        for game_id in ("class-only-1", "class-only-2"):
            _save_game(repo, game_id)
            _save_analysis(repo, game_id, "lichess-json", classifications=[
                {"ply": 1, "mover": "white", "eval_loss_cp": 100, "label": "mistake",
                 "phase": "opening", "algorithm_version": "fixture"}])
        _save_game(repo, "empty-complete")
        _save_analysis(repo, "empty-complete", "lichess-json", status="complete")
        _save_game(repo, "accuracy-only")
        with repo._connection() as db:
            db.execute("UPDATE games SET lichess_accuracy_white=88 WHERE game_id='accuracy-only'")

        _save_game(repo, "stale-line")
        _save_analysis(repo, "stale-line", "lichess-json",
                       scores=[{"ply": 1, "white_cp": 10}])
        repo.save_snapshot("stale-line", "startpos", "d2d4 d7d5", "white", "Fixture",
                           False, "5+0", "finished", "white",
                           started_at="2026-11-03T12:00:00+00:00")

        _save_game(repo, "review-only")
        _save_review_run(repo, "review-only")
        _save_game(repo, "neighbor-month", started_at="2026-10-22T12:00:00+00:00")
        _save_analysis(repo, "neighbor-month", "lichess-json",
                       scores=[{"ply": 1, "white_cp": 20}])
        _save_game(repo, "other-account-game")
        _save_analysis(repo, "other-account-game", "lichess-json",
                       scores=[{"ply": 1, "white_cp": 15}])

        with repo._connection() as db:
            for game_id in (*ids, "class-only-1", "class-only-2", "empty-complete",
                            "accuracy-only", "stale-line", "review-only", "neighbor-month"):
                db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                           "VALUES (?,?,?,?,?,?)",
                           (account, game_id, f"remote-{game_id}", "fixture",
                            "black" if game_id == "json-black-2" or game_id == "sf-black-2" else "white",
                            "Fixture"))
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                       "VALUES (?,?,?,?,?,?)",
                       (other_account, "other-account-game", "remote-other", "fixture", "white", "Fixture"))

        service = StatisticsService(repo)
        library = repo.search_games(account_id=account, analysis_source="lichess")
        summary = service.summary(account_id=account, analysis_source="lichess")
        page = service.game_page(account_id=account, analysis_source="lichess")
        timeline = service.historical_timeline(account_id=account, analysis_source="lichess")
        openings = service.opening_statistics(account_id=account, analysis_source="lichess")
        expected = {"json-white-1", "json-black-2", "pgn-white-3", "neighbor-month"}
        assert library["total"] == 4
        assert {item["game_id"] for item in library["items"]} == expected
        assert summary["games"] == summary["analysis_games"] == 4
        assert page["total"] == 4
        assert {item["game_id"] for item in page["items"]} == expected
        assert sum(row["games"] for row in timeline) == 4
        assert sum(row["analyzed_games"] for row in timeline) == 4
        assert all(row["analyzed_games"] <= row["games"] for row in timeline)
        assert sum(row["games"] for row in openings) == 4
        assert {row["period"] for row in timeline} == {"2026-10", "2026-11"}

        all_history = service.historical_timeline(account_id=account)
        assert sum(row["games"] for row in all_history) == repo.search_games(account_id=account)["total"]
        assert sum(row["analyzed_games"] for row in all_history) == 7
        assert sum(row["wins"] + row["draws"] + row["losses"]
                   for row in all_history) == sum(row["games"] for row in all_history)

        all_page = {row["game_id"]: row for row in service.game_page(account_id=account)["items"]}
        empty = all_page["empty-complete"]
        assert empty["technical_analysis_status"] == "complete"
        assert empty["analysis_status"] is None
        assert empty["technical_analysis_source"] == "lichess-json"
        assert empty["selected_analysis_source"] is None
        assert empty["has_position_scores"] is False

        # Source strings are values, never SQL fragments.
        injected = "lichess' OR 1=1 --"
        assert service.summary(account_id=account, analysis_source=injected)["games"] == 0
        assert service.historical_timeline(account_id=account,
                                           analysis_source=injected) == []
    finally:
        repo.shutdown()


class _BarrierStatisticsService:
    def __init__(self, repository, started, release):
        self._real = StatisticsService(repository)
        self._started = started
        self._release = release

    def summary(self, **filters):
        self._started.set()
        if not self._release.wait(5):
            raise TimeoutError("test barrier not released")
        return self._real.summary(**filters)

    def game_page(self, **filters):
        return self._real.game_page(**filters)

    def personal_profile(self, **filters):
        return self._real.personal_profile(**filters)


def _start_barrier_query(dialog, repository, started, release):
    dialog.service = _BarrierStatisticsService(repository, started, release)
    dialog._refresh_timer.stop()
    dialog._refresh_now()
    assert started.wait(2)


def test_statistics_worker_can_finish_after_dialog_cpp_object_is_destroyed(
        qtbot, tmp_path):
    repo = GameRepository(tmp_path / "worker-dialog-destroy.sqlite3")
    started, release = threading.Event(), threading.Event()
    dialog = StatisticsDialog(repo)
    dialog._refresh_timer.stop()
    _start_barrier_query(dialog, repo, started, release)
    hub = dialog._worker_hub
    worker = dialog._query_thread
    assert worker in hub.active_threads

    # Bypass deferred close to reproduce parent/application destruction while
    # the non-cancellable SQLite read remains in flight.
    shiboken6.delete(dialog)
    assert not shiboken6.isValid(dialog)
    release.set()
    qtbot.waitUntil(lambda: worker not in hub.active_threads, timeout=4000)
    assert not worker.is_alive()
    repo.shutdown()


def test_opening_matcher_worker_can_finish_after_dialog_is_destroyed(
        qtbot, tmp_path, monkeypatch):
    from chess_coach.lichess import opening_matcher

    started, release = threading.Event(), threading.Event()

    def slow_match_recent(_matcher, _repository, *, limit):
        assert limit == 25
        started.set()
        if not release.wait(5):
            raise TimeoutError("opening test barrier not released")
        return 0

    monkeypatch.setattr(opening_matcher.OpeningMatcher, "match_recent", slow_match_recent)
    repo = GameRepository(tmp_path / "opening-worker-destroy.sqlite3")
    dialog = StatisticsDialog(repo)
    dialog._refresh_timer.stop()
    dialog._start_opening_refresh()
    assert started.wait(2)
    hub = dialog._worker_hub
    worker = dialog._opening_thread
    assert worker in hub.active_threads
    shiboken6.delete(dialog)
    assert not shiboken6.isValid(dialog)
    release.set()
    qtbot.waitUntil(lambda: worker not in hub.active_threads, timeout=4000)
    assert not worker.is_alive()
    repo.shutdown()


def test_statistics_worker_keeps_main_window_alive_until_result_dispatch_finishes(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "worker-window.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.show_statistics()
    page = window._statistics_dialog
    started, release = threading.Event(), threading.Event()
    _start_barrier_query(page, window.game_repository, started, release)
    worker = page._query_thread

    window.show()
    window.close()
    assert window.isVisible()
    assert worker in window._statistics_workers
    release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=6000)
    assert not worker.is_alive()
    assert not window._statistics_workers


def test_two_profile_pages_share_safe_worker_dispatch_without_cross_delivery(
        qtbot, tmp_path):
    repos = [GameRepository(tmp_path / "worker-profile-one.sqlite3"),
             GameRepository(tmp_path / "worker-profile-two.sqlite3")]
    dialogs = [StatisticsDialog(repo) for repo in repos]
    barriers = [(threading.Event(), threading.Event()) for _ in repos]
    hub = dialogs[0]._worker_hub
    assert dialogs[1]._worker_hub is hub
    workers = []
    try:
        for dialog, repo, (started, release) in zip(dialogs, repos, barriers):
            dialog._refresh_timer.stop()
            _start_barrier_query(dialog, repo, started, release)
            workers.append(dialog._query_thread)
        for dialog in dialogs:
            shiboken6.delete(dialog)
        for _started, release in barriers:
            release.set()
        qtbot.waitUntil(lambda: all(worker not in hub.active_threads for worker in workers),
                        timeout=4000)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        for _started, release in barriers:
            release.set()
        for repo in repos:
            repo.shutdown()


def test_empty_complete_run_is_rendered_as_no_position_evaluations(qtbot, tmp_path):
    repo = GameRepository(tmp_path / "empty-run-ui.sqlite3")
    try:
        _save_game(repo, "empty-complete")
        _save_analysis(repo, "empty-complete", "stockfish", status="complete", classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 100, "label": "mistake",
             "phase": "opening", "algorithm_version": "fixture"}])
        dialog = StatisticsDialog(repo)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog._refresh_timer.stop()
        dialog._refresh_now()
        qtbot.waitUntil(lambda: not dialog._worker_hub.active_threads, timeout=5000)
        qtbot.wait(20)
        assert dialog._query_thread is None, dialog.summary_label.text()
        assert dialog.games.count() == 1, dialog.summary_label.text()
        row_text = dialog.games.item(0).text()
        assert "Позиционных оценок нет" in row_text
        assert "технический статус run: complete" in row_text
        assert "Классификация без позиционных оценок" in row_text
        assert "ошибки 1" in row_text
        assert "Оценено позиций: 0/" not in row_text
        row = dialog.service.game_page()["items"][0]
        assert row["analysis_status"] is None
        assert row["technical_analysis_status"] == "complete"
        assert row["own_classified_mistakes"] == 1
    finally:
        repo.shutdown()
