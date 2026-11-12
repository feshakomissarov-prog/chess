"""Synthetic, offline Home persistence smoke used for PR #46 §11 evidence.

All data is disposable and created below the caller-supplied directory. No user
settings, production database, credentials, network transport, or PGN is read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QRect, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from chess_coach.bootstrap import ApplicationRuntime
from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.diagnostics import configure_logging
from chess_coach.lichess.repository import GameRepository
from chess_coach.window import MainWindow


ACCOUNT = "lichess:synthetic-profile"
BASE_TIME = "2026-11-09T00:00:00Z"


def seed(database: Path, count: int = 3319) -> None:
    repository = GameRepository(database)
    repository.ensure_archive_account("synthetic-profile")
    for index, accuracy in enumerate((72.0, 81.0, 64.0)[:min(count, 3)]):
        game_id = f"seed-{index}"
        repository.save_snapshot(
            game_id, "startpos", "e2e4 e7e5 g1f3", "white", f"Opponent {game_id}",
            False, "5+0", "mate", "white",
            started_at=f"2026-11-{9-index:02d}T12:00:00Z",
            account_username="synthetic-profile")
        with repository._connection() as db:
            db.execute("UPDATE games SET source='lichess',lichess_accuracy_white=?,"
                       "lichess_accuracy_black=? WHERE game_id=?",
                       (accuracy, 100.0-accuracy, game_id))
        line = repository.current_line(game_id)
        repository.save_analysis(
            game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
            source="stockfish", engine_name="Synthetic fixture", engine_version="1",
            parameters={"fixture": True}, status="complete",
            assessments=[
                {"ply": 0, "white_cp": 12, "depth": 15, "nodes": 1000, "pv": ["e2e4"]},
                {"ply": 1, "white_cp": -45, "depth": 15, "nodes": 1000, "pv": ["c7c5"]},
                {"ply": 2, "white_cp": 35, "depth": 15, "nodes": 1000, "pv": ["g1f3"]},
                {"ply": 3, "white_cp": -20, "depth": 15, "nodes": 1000, "pv": ["b8c6"]},
            ],
            classifications=[{"ply": 2, "mover": "black", "eval_loss_cp": 80 + index,
                               "label": "mistake", "algorithm_version": "synthetic-v1"}])

    with repository._connection() as db:
        count_old = max(0, count - 3)
        db.executemany(
            "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
            "winner,updated_at,pgn,source,started_at,game_time_precision,"
            "lichess_accuracy_white,lichess_accuracy_black) "
            "VALUES(?, 'startpos','', 'white','Synthetic',0,'5+0','mate','white',?,'',"
            "'lichess',?,'timestamp',68.0,70.0)",
            ((f"seed-old-{index:05d}", BASE_TIME, BASE_TIME) for index in range(count_old)))
        db.executemany(
            "INSERT INTO game_revisions(game_id,line_revision,initial_fen,moves,pgn,line_hash,"
            "created_at,is_current) VALUES(?,1,'startpos','','',?,'2026-11-10T00:00:00Z',1)",
            ((f"seed-old-{index:05d}", f"hash-old-{index:05d}") for index in range(count_old)))
        db.executemany(
            "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent,player_name) "
            "VALUES(?,?,?,?,'white',?,'Synthetic')",
            ((ACCOUNT, f"seed-old-{index:05d}", f"seed-old-{index:05d}", BASE_TIME,
              f"Opponent {index}") for index in range(count_old)))
    repository.save_puzzle_player_rating(
        ACCOUNT, [[2026, 10, 8, 1475], [2026, 10, 9, 1492]])
    repository.shutdown()


def add_game(database: Path) -> None:
    repository = GameRepository(database)
    repository.save_snapshot(
        "seed-new", "startpos", "e2e4 e7e5", "white", "New opponent", False,
        "5+0", "mate", "white", started_at="2026-11-10T12:00:00Z",
        account_username="synthetic-profile")
    with repository._connection() as db:
        db.execute("UPDATE games SET source='lichess',lichess_accuracy_white=77.0,"
                   "lichess_accuracy_black=61.0 WHERE game_id='seed-new'")
    repository.shutdown()


def write_expected(database: Path, root: Path) -> None:
    """Prepare an independent expectation before the measured Qt process starts."""
    root.mkdir(parents=True, exist_ok=True)
    repository = GameRepository(database)
    snapshot = dashboard_snapshot(repository, account_id=ACCOUNT, clock="",
                                  wait_for_writes=True)
    metrics = {item["key"]: item for item in snapshot["metrics"]}
    expected = {
        "games": metrics["games"]["value"],
        "accuracy": metrics["accuracy"]["value"],
        "accuracy_detail": metrics["accuracy"]["detail"],
        "accuracy_source": metrics["accuracy"].get("source"),
        "puzzle_rating": str((snapshot.get("puzzle_player_rating") or {}).get("latest") or ""),
        "recent_ids": [game.get("game_id") for game in snapshot.get("recent_games", [])],
    }
    (root / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    repository.shutdown()


def launch(database: Path, root: Path, run_seconds: int, navigation_cycles: int,
           capture_screenshots: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    configure_logging(root / "logs")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/selected_account", ACCOUNT)
    settings.sync()
    repository = GameRepository(database)
    runtime = ApplicationRuntime(settings, root, repository, True, False)
    expected_path = root / "expected.json"
    if not expected_path.is_file():
        raise RuntimeError("Run the separate 'expect' process before measured 'launch'.")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    target_count = expected["games"]
    target_recent_ids = expected["recent_ids"]
    window = MainWindow(runtime=runtime)
    aggregate_calls = 0
    original_snapshot = window.dashboard_queries.snapshot

    def count_aggregate(**kwargs):
        nonlocal aggregate_calls
        aggregate_calls += 1
        return original_snapshot(**kwargs)

    window.dashboard_queries.snapshot = count_aggregate
    window.resize(1672, 941)
    shown = time.perf_counter()
    window.show()
    state_path = root / "window-state.json"
    first_count = first_useful = first_recent_cards = full_ready = None
    first_useful_snapshot = None
    current_summary_ms = None
    current_summary_cards = None
    count_capture = False
    useful_capture = False
    useful_capture_pending = False
    updated_capture = False
    navigation_done = False
    navigation_ms: list[float] = []
    max_heartbeat_gap_ms = 0.0
    heartbeat_gaps: list[dict] = []
    last_heartbeat = time.perf_counter()
    heartbeat_has_tick = False
    navigation_started_at = None
    navigation_completed = False
    final_state: dict = {}
    timer = QTimer(window)
    heartbeat = QTimer(window)

    def record_heartbeat() -> None:
        nonlocal last_heartbeat, max_heartbeat_gap_ms, heartbeat_has_tick
        now = time.perf_counter()
        if heartbeat_has_tick:
            gap_ms = (now - last_heartbeat) * 1000
            max_heartbeat_gap_ms = max(max_heartbeat_gap_ms, gap_ms)
            if gap_ms >= 40:
                heartbeat_gaps.append({
                    "at_ms": round((now - shown) * 1000, 2),
                    "gap_ms": round(gap_ms, 2),
                    "during_navigation": (navigation_started_at is not None
                                           and not navigation_completed),
                })
        else:
            heartbeat_has_tick = True
        last_heartbeat = now

    heartbeat.timeout.connect(record_heartbeat)
    heartbeat.start(10)

    def write_state() -> None:
        nonlocal first_count, first_useful, full_ready, count_capture, useful_capture
        nonlocal useful_capture_pending
        nonlocal first_recent_cards
        nonlocal updated_capture, navigation_done
        nonlocal first_useful_snapshot, current_summary_ms, current_summary_cards
        now = time.perf_counter()
        games = window._dashboard_metric_values["games"].text()
        accuracy = window._dashboard_metric_values["accuracy"].text()
        rating = window._home_view.puzzle_rating.text()
        snapshot = window._latest_dashboard_snapshot or {}
        visible_recent_ids = [game.get("game_id") for game in snapshot.get("recent_games", [])]
        painted_cards = []
        viewport = window._home_view.recent_scroll.viewport()
        for index in range(window._recent_games_layout.count()):
            widget = window._recent_games_layout.itemAt(index).widget()
            if widget is not None and widget.objectName() == "metricCard":
                top_left = widget.mapTo(viewport, widget.rect().topLeft())
                card_rect = QRect(top_left, widget.size())
                painted_cards.append((widget, card_rect))
        painted_ids = [str(card.property("dashboard_game_id") or "")
                       for card, _rect in painted_cards]
        cards_fully_visible = (len(painted_cards) == min(int(target_count), 3)
                               and all(viewport.rect().contains(rect)
                                       for _card, rect in painted_cards))
        accuracy_detail = window._dashboard_metric_details["accuracy"].text()
        if (current_summary_ms is None and games == target_count
                and visible_recent_ids[:len(target_recent_ids)] == target_recent_ids
                and painted_ids == target_recent_ids[:3] and cards_fully_visible
                and accuracy == expected["accuracy"]
                # A valid last-good Tier B may be rendered with an explicit
                # "updating" prefix while the newer aggregate runs. The saved
                # source/detail must still be present; don't time the background
                # replacement as though it were the first complete Home paint.
                and expected["accuracy_detail"] in accuracy_detail
                and rating == expected["puzzle_rating"]):
            current_summary_ms = round((now - shown) * 1000, 2)
            from PySide6.QtWidgets import QLabel
            current_summary_cards = [{
                "game_id": str(card.property("dashboard_game_id") or ""),
                "labels": [label.text() for label in card.findChildren(QLabel)
                           if label.isVisible()],
            } for card, _rect in painted_cards]
        if games not in {"—", "-"} and first_count is None:
            first_count = round((now - shown) * 1000, 2)
        if first_count is not None and not count_capture:
            count_capture = (bool(window.grab().save(str(root / "home-count-first-paint.png")))
                             if capture_screenshots else True)
        if (accuracy not in {"—", "н/д", "-"} and rating not in {"—", "-"}
                and first_useful is None):
            first_useful = round((now - shown) * 1000, 2)
            first_useful_snapshot = dict(snapshot)
            first_useful_snapshot["_first_useful_accuracy"] = accuracy
            first_useful_snapshot["_first_useful_accuracy_detail"] = window._dashboard_metric_details[
                "accuracy"].text()
            first_useful_snapshot["_first_useful_rating"] = rating
            first_useful_snapshot["_first_useful_stale"] = bool(snapshot.get("_dashboard_stale"))
            first_useful_snapshot["_first_useful_recent_ids"] = [
                game.get("game_id") for game in snapshot.get("recent_games", [])]
        if first_useful is not None and first_recent_cards is None:
            recent = []
            from PySide6.QtWidgets import QLabel
            for index in range(window._recent_games_layout.count()):
                item = window._recent_games_layout.itemAt(index)
                widget = item.widget()
                if widget is not None and widget.objectName() == "metricCard":
                    recent.append({
                        "game_id": str(widget.property("dashboard_game_id") or ""),
                        "labels": [label.text() for label in widget.findChildren(QLabel)
                                   if label.isVisible()],
                    })
            try:
                count_value = int(games.replace(",", ""))
            except ValueError:
                count_value = -1
            expected_cards = min(count_value, 3) if count_value >= 0 else -1
            snapshot_ids = [str(game.get("game_id") or "")
                            for game in snapshot.get("recent_games", [])]
            rendered_ids = [card["game_id"] for card in recent]
            if (len(recent) == expected_cards
                    and snapshot_ids == rendered_ids[:expected_cards]
                    and (expected_cards > 0 or count_value == 0)):
                first_recent_cards = round((now - shown) * 1000, 2)
                if first_useful_snapshot is not None:
                    first_useful_snapshot["_first_useful_cards"] = recent
        if (first_recent_cards is not None and not useful_capture
                and not useful_capture_pending):
            if capture_screenshots:
                useful_capture_pending = True

                def save_useful_paint() -> None:
                    nonlocal useful_capture, useful_capture_pending
                    useful_capture = bool(window.grab().save(
                        str(root / "home-useful-first-paint.png")))
                    useful_capture_pending = False

                # Let Qt deliver the queued layout/repaint events for the
                # bounded recent-game preview before grabbing the native UI.
                QTimer.singleShot(300, save_useful_paint)
            else:
                useful_capture = True
        if (window._dashboard_full_rendered_generation is not None and full_ready is None):
            full_ready = round((now - shown) * 1000, 2)
        if full_ready is not None and not updated_capture:
            updated_capture = (bool(window.grab().save(str(root / "home-updated-tier-b.png")))
                               if capture_screenshots else True)
        if full_ready is not None and navigation_cycles and not navigation_done:
            navigation_done = True

            def exercise_home_navigation() -> None:
                nonlocal navigation_started_at
                navigation_started_at = time.perf_counter()

                def visit_play(index: int) -> None:
                    nonlocal navigation_completed
                    if index >= navigation_cycles:
                        navigation_completed = True
                        return
                    window.show_play()

                    def return_home() -> None:
                        started = time.perf_counter()
                        window.show_home()
                        navigation_ms.append((time.perf_counter() - started) * 1000)
                        QTimer.singleShot(20, lambda: visit_play(index + 1))

                    QTimer.singleShot(20, return_home)

                visit_play(0)

            QTimer.singleShot(250, exercise_home_navigation)
        final_state.update({
            "shown_at_monotonic": shown,
            "timer_origin": ("immediately_before_window.show(); diagnostic only, "
                             "not native first-visible-frame timing"),
            "first_count_ms": first_count,
            "first_useful_ms": first_useful,
            "current_summary_ms": current_summary_ms,
            "current_summary_cards": current_summary_cards,
            "target_games": target_count,
            "target_recent_ids": target_recent_ids,
            "expected": expected,
            "first_recent_cards_ms": first_recent_cards,
            "full_ready_ms": full_ready,
            "games": games,
            "accuracy": accuracy,
            "accuracy_detail": window._dashboard_metric_details["accuracy"].text(),
            "analysis": window._dashboard_metric_values["analysis"].text(),
            "errors": window._dashboard_metric_values["errors"].text(),
            "puzzle_rating": rating,
            "progress": window._home_view.progress_trend.text(),
            "snapshot_stale": bool(snapshot.get("_dashboard_stale")),
            "snapshot_revision": snapshot.get("_dashboard_revision"),
            "recent_ids": [game.get("game_id") for game in snapshot.get("recent_games", [])],
            "painted_recent_ids": painted_ids,
            "recent_cards_fully_visible": cards_fully_visible,
            "recent_card_labels": (first_useful_snapshot or {}).get("_first_useful_cards", []),
            "first_useful_accuracy": (first_useful_snapshot or {}).get("_first_useful_accuracy"),
            "first_useful_accuracy_detail": (first_useful_snapshot or {}).get(
                "_first_useful_accuracy_detail"),
            "first_useful_rating": (first_useful_snapshot or {}).get("_first_useful_rating"),
            "first_useful_stale": (first_useful_snapshot or {}).get("_first_useful_stale"),
            "first_useful_recent_ids": (first_useful_snapshot or {}).get(
                "_first_useful_recent_ids"),
            "first_useful_cards": (first_useful_snapshot or {}).get("_first_useful_cards"),
            "aggregate_calls": aggregate_calls,
            "navigation_cycles": len(navigation_ms),
            "navigation_p50_ms": (sorted(navigation_ms)[len(navigation_ms)//2]
                                   if navigation_ms else None),
            "navigation_p95_ms": (sorted(navigation_ms)[min(
                len(navigation_ms)-1, int(len(navigation_ms)*0.95))]
                                   if navigation_ms else None),
            "navigation_max_ms": max(navigation_ms, default=None),
            "max_heartbeat_gap_ms": round(max_heartbeat_gap_ms, 2),
            "heartbeat_gaps_over_40ms": heartbeat_gaps,
            "count_capture_saved": capture_screenshots and count_capture,
            "useful_capture_saved": capture_screenshots and useful_capture,
            "updated_capture_saved": capture_screenshots and updated_capture,
        })

    timer.timeout.connect(write_state)
    # Use a finer sampling interval so the reported first complete Qt paint is
    # not rounded late by a 50 ms polling cadence.
    timer.start(10)
    QTimer.singleShot(run_seconds * 1000, window.close)
    app.exec()
    state_path.write_text(json.dumps(final_state, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "add", "expect", "launch"))
    parser.add_argument("database", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--count", type=int, default=3319)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--navigation-cycles", type=int, default=0)
    parser.add_argument("--no-screenshots", action="store_true")
    args = parser.parse_args()
    if args.mode == "seed":
        seed(args.database, args.count)
    elif args.mode == "add":
        add_game(args.database)
    elif args.mode == "expect":
        write_expected(args.database, args.root)
    else:
        launch(args.database, args.root, args.seconds, args.navigation_cycles,
               not args.no_screenshots)


if __name__ == "__main__":
    main()
