from __future__ import annotations

import time
import json
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.dashboard_cache import DashboardSnapshotCache
from chess_coach.bootstrap import ApplicationRuntime
from chess_coach.lichess.repository import GameRepository
from chess_coach.window import MainWindow


ACCOUNT = "lichess:synthetic-profile"


def _seed_profile(repository: GameRepository, count: int = 3319) -> None:
    repository.ensure_archive_account("synthetic-profile")
    base = "2026-11-09T00:00:00Z"
    recent = [(f"seed-{index}", f"2026-11-{9-index:02d}T12:00:00Z", 72 + index * 3)
              for index in range(3)]
    for game_id, started_at, _accuracy in recent:
        repository.save_snapshot(game_id, "startpos", "e2e4 e7e5 g1f3", "white",
                                 f"Opponent {game_id}", False, "5+0", "mate", "white",
                                 started_at=started_at,
                                 account_username="synthetic-profile")
    with repository._connection() as db:
        existing = {row[0] for row in db.execute("SELECT game_id FROM games")}
        old = [(f"seed-old-{index:05d}", base) for index in range(max(0, count - 3))]
        old = [row for row in old if row[0] not in existing]
        db.executemany(
            "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
            "winner,updated_at,pgn,source,started_at,game_time_precision,"
            "lichess_accuracy_white,lichess_accuracy_black) "
            "VALUES(?, 'startpos','', 'white','Synthetic',0,'5+0','mate','white',?,'',"
            "'lichess',?,'timestamp',68.0,70.0)",
            ((game_id, base, started_at) for game_id, started_at in old))
        db.executemany(
            "INSERT INTO game_revisions(game_id,line_revision,initial_fen,moves,pgn,line_hash,"
            "created_at,is_current) VALUES(?,1,'startpos','','',?,'2026-11-10T00:00:00Z',1)",
            ((game_id, f"hash-{game_id}") for game_id, _started in old))
        db.executemany(
            "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent,player_name) "
            "VALUES(?,?,?,?,'white',?,'Synthetic')",
            ((ACCOUNT, game_id, game_id, base, f"Opponent {game_id}")
             for game_id, _started in old))
        for game_id, _started, accuracy in recent:
            db.execute("UPDATE games SET source='lichess',lichess_accuracy_white=?,"
                       "lichess_accuracy_black=? WHERE game_id=?",
                       (float(accuracy), float(100 - accuracy), game_id))
    repository.save_puzzle_player_rating(ACCOUNT, [[2026, 10, 8, 1475], [2026, 10, 9, 1492]])


def _metric(snapshot: dict, key: str) -> str:
    return next(item["value"] for item in snapshot["metrics"] if item["key"] == key)


def test_last_good_cache_is_available_after_new_game_changes_revision(tmp_path):
    repository = GameRepository(tmp_path / "last-good.sqlite3")
    _seed_profile(repository)
    cache = DashboardSnapshotCache(repository)
    before_revision = repository.dashboard_data_revision(ACCOUNT)
    prior = dashboard_snapshot(repository, account_id=ACCOUNT, wait_for_writes=False)
    assert _metric(prior, "games") == "3319"
    assert prior["puzzle_player_rating"]["latest"] == 1492
    assert cache.save(ACCOUNT, None, before_revision, prior)

    repository.save_snapshot("seed-new", "startpos", "e2e4 e7e5", "white", "New opponent",
                             False, "5+0", "mate", "white", started_at="2026-11-10T12:00:00Z",
                             account_username="synthetic-profile")
    current_revision = repository.dashboard_data_revision(ACCOUNT)
    assert current_revision != before_revision
    # Existing exact-head behavior drops the valid B document when revision advances.
    recovered = cache.load(ACCOUNT, None, expected_revision=current_revision)
    assert recovered is not None, "the previous verified Tier B must remain readable as stale"
    assert recovered.get("_dashboard_stale") is True
    assert recovered["puzzle_player_rating"]["latest"] == 1492
    repository.shutdown()


def test_background_aggregate_error_keeps_cached_home_numbers(qtbot, tmp_path):
    repository = GameRepository(tmp_path / "aggregate-error.sqlite3")
    _seed_profile(repository)
    revision = repository.dashboard_data_revision(ACCOUNT)
    summary = dashboard_snapshot(repository, account_id=ACCOUNT, wait_for_writes=False)
    cache = DashboardSnapshotCache(repository)
    assert cache.save(ACCOUNT, None, revision, summary)
    settings = QSettings(str(tmp_path / "aggregate-error.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/selected_account", ACCOUNT)
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    before_accuracy = window._dashboard_metric_values["accuracy"].text()
    before_rating = window._home_view.puzzle_rating.text()
    assert before_accuracy not in {"—", "н/д"}
    assert before_rating == "1492"

    generation = window.dashboard_queries.begin_request()
    window._dashboard_result_ready(generation, None, RuntimeError("database locked"), None)
    assert window._dashboard_metric_values["accuracy"].text() == before_accuracy
    assert window._home_view.puzzle_rating.text() == before_rating
    assert "обновление временно недоступно" in (
        window._dashboard_metric_details["accuracy"].text().casefold())
    window.close()
    runtime.close()


def test_real_main_window_first_paint_restores_stale_tier_b_with_fresh_tier_a(
        qtbot, tmp_path):
    repository = GameRepository(tmp_path / "window-restart.sqlite3")
    _seed_profile(repository)
    initial_revision = repository.dashboard_data_revision(ACCOUNT)
    full = dashboard_snapshot(repository, account_id=ACCOUNT, wait_for_writes=False)
    assert _metric(full, "games") == "3319"
    assert full["puzzle_player_rating"]["latest"] == 1492
    assert DashboardSnapshotCache(repository).save(ACCOUNT, None, initial_revision, full)
    repository.save_snapshot("seed-new", "startpos", "e2e4 e7e5", "white", "New opponent",
                             False, "5+0", "mate", "white", started_at="2026-11-10T12:00:00Z",
                             account_username="synthetic-profile")
    settings = QSettings(str(tmp_path / "window-restart.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/selected_account", ACCOUNT)
    settings.sync()
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    assert window._dashboard_metric_values["accuracy"].text() not in {"—", "н/д"}
    assert window._home_view.puzzle_rating.text() == "1492"
    shown_at = time.perf_counter()
    window.show()
    qtbot.waitUntil(lambda: window._dashboard_first_count_paint, timeout=5000)
    first_paint_ms = (time.perf_counter() - shown_at) * 1000
    recent_ids = [game.get("game_id") for game in (window._latest_dashboard_snapshot or {}).get(
        "recent_games", [])]
    # These assert the actual Qt presentation, not a direct helper render.
    assert window._dashboard_metric_values["games"].text() == "3320"
    assert window._home_view.puzzle_rating.text() == "1492"
    assert window._dashboard_metric_values["accuracy"].text() not in {"—", "н/д"}
    assert recent_ids[:3] == ["seed-new", "seed-0", "seed-old-03315"]
    cards = [window._recent_games_layout.itemAt(i).widget()
             for i in range(window._recent_games_layout.count())
             if window._recent_games_layout.itemAt(i).widget() is not None]
    assert "72.0%" in " ".join(label.text() for label in cards[1].findChildren(__import__(
        "PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    # This in-process Qt test can contend with the shared QApplication and the
    # legacy metrics writer; strict startup timing is asserted by the isolated
    # subprocess/native-window smoke test below.
    assert first_paint_ms <= 1000
    qtbot.waitUntil(lambda: window._dashboard_full_rendered_generation is not None, timeout=10000)
    window.close()
    runtime.close()


def test_3319_to_3320_restart_persists_and_paints_real_ui_in_separate_processes(tmp_path):
    script = Path.cwd() / "scripts" / "home_dashboard_smoke.py"
    database = tmp_path / "restart-matrix.sqlite3"
    root = tmp_path / "process-runs"
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(Path.cwd() / "src") + os.pathsep + env.get("PYTHONPATH", "")

    def run(*args: str) -> dict | None:
        completed = subprocess.run(
            [sys.executable, str(script), *args], cwd=str(Path.cwd()), env=env,
            capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        if args[0] == "launch":
            state_path = Path(args[2]) / "window-state.json"
            assert state_path.is_file(), completed.stdout + completed.stderr
            return json.loads(state_path.read_text(encoding="utf-8"))
        return None

    run("seed", str(database), str(root / "seed"), "--count", "3319")
    run("expect", str(database), str(root / "first"))
    before = run("launch", str(database), str(root / "first"), "--seconds", "12")
    assert before["games"] == "3319"
    assert before["accuracy"] == before["expected"]["accuracy"] == "68.0%"
    assert before["puzzle_rating"] == before["expected"]["puzzle_rating"] == "1492"
    assert before["accuracy_detail"] == before["expected"]["accuracy_detail"]
    assert before["recent_ids"][:3] == before["expected"]["recent_ids"]
    assert before["aggregate_calls"] >= 1
    assert before["full_ready_ms"] is not None
    run("add", str(database), str(root / "added"))
    run("expect", str(database), str(root / "second"))
    changed = run("launch", str(database), str(root / "second"), "--seconds", "10")
    assert changed["games"] == "3320"
    assert changed["games"] == changed["expected"]["games"]
    assert changed["recent_ids"][:3] == ["seed-new", "seed-0", "seed-old-03315"]
    assert changed["first_useful_ms"] <= 500
    assert changed["first_useful_accuracy"] == "68.0%"
    assert changed["first_useful_rating"] == "1492"
    assert changed["first_useful_stale"] is True
    assert changed["current_summary_ms"] <= 1000, changed
    assert changed["recent_ids"][:3] == ["seed-new", "seed-0", "seed-old-03315"]
    assert "обновляется" in changed["first_useful_accuracy_detail"]
    # The initial metrics-only paint may precede recent-card reflow. Verify
    # card content at the measured first fully visible current summary instead.
    summary_cards = changed["current_summary_cards"]
    assert [card["game_id"] for card in summary_cards] == [
        "seed-new", "seed-0", "seed-old-03315"]
    seed_zero_card = next(card for card in summary_cards
                          if card["game_id"] == "seed-0")
    assert "Opponent seed-0" in " ".join(seed_zero_card["labels"])
    assert "72.0%" in " ".join(seed_zero_card["labels"])
    new_game_card = next(card for card in summary_cards
                         if card["game_id"] == "seed-new")
    assert "Новая партия" in " ".join(new_game_card["labels"])
    assert changed["aggregate_calls"] >= 1
    assert changed["full_ready_ms"] is not None

    repository = GameRepository(database)
    cache = DashboardSnapshotCache(repository)
    fresh = cache.load(ACCOUNT, None)
    assert fresh is not None and fresh["_dashboard_stale"] is False
    assert _metric(fresh, "games") == "3320"
    repository.shutdown()

    run("expect", str(database), str(root / "third"))
    after = run("launch", str(database), str(root / "third"), "--seconds", "2")
    assert after["games"] == "3320"
    assert after["accuracy"] == after["expected"]["accuracy"]
    assert after["accuracy_detail"] == after["expected"]["accuracy_detail"]
    assert after["puzzle_rating"] == after["expected"]["puzzle_rating"] == "1492"
    assert after["recent_ids"][:3] == after["expected"]["recent_ids"]
    assert after["first_useful_ms"] <= 500
    assert after["first_useful_accuracy"] is not None
    assert after["aggregate_calls"] == 0


def test_home_navigation_remains_responsive_during_real_sqlite_writer_lock(
        qtbot, tmp_path):
    repository = GameRepository(tmp_path / "home-writer-lock.sqlite3")
    settings = QSettings(str(tmp_path / "home-writer-lock.ini"), QSettings.Format.IniFormat)
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window._dashboard_first_count_paint, timeout=3000)

    locked, release = __import__("threading").Event(), __import__("threading").Event()

    def hold_writer_lock():
        connection = sqlite3.connect(repository.path, timeout=2)
        connection.execute("BEGIN IMMEDIATE")
        locked.set()
        release.wait(2)
        connection.rollback()
        connection.close()

    thread = __import__("threading").Thread(target=hold_writer_lock, daemon=True)
    thread.start()
    assert locked.wait(1)
    timer_ticks = []
    from PySide6.QtCore import QTimer
    heartbeat = QTimer(window)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: timer_ticks.append(time.perf_counter()))
    heartbeat.start()
    try:
        window.show_play()
        started = time.perf_counter()
        window.show_home()
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 100
        qtbot.waitUntil(lambda: len(timer_ticks) >= 3, timeout=750)
    finally:
        release.set()
        thread.join(2)
        heartbeat.stop()
    window.close()
    runtime.close()


def test_close_event_does_not_block_qt_while_dashboard_aggregate_finishes(
        qtbot, tmp_path, monkeypatch):
    repository = GameRepository(tmp_path / "home-close-aggregate.sqlite3")
    settings = QSettings(str(tmp_path / "home-close-aggregate.ini"), QSettings.Format.IniFormat)
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    entered, release = __import__("threading").Event(), __import__("threading").Event()

    def blocked_snapshot(**_kwargs):
        entered.set()
        release.wait(3)
        return {"metrics": [], "recent_games": [], "filter": {"account_id": None,
                                                                  "clock": None}}

    monkeypatch.setattr(window.dashboard_queries, "snapshot", blocked_snapshot)
    window.show()
    qtbot.waitUntil(entered.is_set, timeout=5000)
    from PySide6.QtCore import QTimer
    timer_ticks = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: timer_ticks.append(time.perf_counter()))
    heartbeat.start()
    try:
        started = time.perf_counter()
        window.close()
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 350
        qtbot.waitUntil(lambda: len(timer_ticks) >= 3, timeout=750)
    finally:
        release.set()
        heartbeat.stop()
    qtbot.waitUntil(lambda: window._dashboard_thread is None
                    or not window._dashboard_thread.is_alive(), timeout=5000)
    window.close()
    runtime.close()


def test_background_revision_change_retries_then_publishes_stable_tier_b(
        qtbot, tmp_path, monkeypatch):
    repository = GameRepository(tmp_path / "home-revision-churn.sqlite3")
    settings = QSettings(str(tmp_path / "home-revision-churn.ini"), QSettings.Format.IniFormat)
    runtime = ApplicationRuntime(settings, tmp_path, repository, True, False)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    original = window.dashboard_queries.snapshot
    calls = []

    def change_once_while_aggregate_runs(**kwargs):
        snapshot = original(**kwargs)
        calls.append(snapshot)
        if len(calls) == 1:
            repository.save_snapshot("committed-during-aggregate", "startpos", "e2e4",
                                     "white", "Synthetic", False, "5+0", "mate", "white")
        return snapshot

    monkeypatch.setattr(window.dashboard_queries, "snapshot", change_once_while_aggregate_runs)
    window.show()
    qtbot.waitUntil(lambda: window._dashboard_full_rendered_generation is not None,
                    timeout=8000)
    assert len(calls) >= 2
    assert window._dashboard_metric_values["games"].text() == "1"
    assert window._latest_dashboard_snapshot.get("_dashboard_stale") is not True
    revision = repository.dashboard_data_revision()
    persisted = DashboardSnapshotCache(repository).load(None, None)
    assert persisted is not None
    assert persisted["_dashboard_stale"] is False
    assert _metric(persisted, "games") == "1"
    assert persisted["_dashboard_tier_b_revision"] == revision
    window.close()
    runtime.close()
