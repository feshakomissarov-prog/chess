"""Capture truthful native-Windows screenshots from isolated synthetic SQLite data.

The production MainWindow, dashboard workers, library query/detail workers and
background status reader are used unchanged. No Lichess transport, credential
store, user settings, user database or real engine is opened.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import math
from hashlib import sha256
from types import SimpleNamespace

import chess
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QImage, QPainter
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QLabel, QListWidgetItem, QScrollArea, QToolTip, QWidget,
)

from chess_coach.bootstrap import create_runtime
from chess_coach.application.dashboard import dashboard_snapshot, _home_graph_points
from chess_coach.application.background_work import OperationLane
from chess_coach.lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from chess_coach.ui.pages.home import HomeEvaluationGraph
from chess_coach.ui.pages.background import BackgroundOperationsPage
from chess_coach.ui.pages.puzzles import PuzzleHistoryPage
from chess_coach.window import MainWindow


REFERENCE_HASHES = {
    "01_HOME_APPROVED.png": "45066d18a5ba324dff62a2001059cfe0131c293d9cb46eeb07b3f19e38885c52",
    "02_MY_GAMES_APPROVED.png": "42df53480ffea4dd720638b753c7f7ca798af3f2bf4efabb907be19671ef5cbe",
    "03_BACKGROUND_APPROVED.png": "87e37894fb09832cdfaa0190b6816cae8679098194459d9780f1d17c7b7ab67a",
}
REFERENCE_MAP = {
    "home": "01_HOME_APPROVED.png",
    "games": "02_MY_GAMES_APPROVED.png",
    "background": "03_BACKGROUND_APPROVED.png",
}
TARGET_SIZES = ((1672, 941), (1360, 860), (1280, 720))
MOVE_TEXT = (
    "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 "
    "e1g1 f8e7 f1e1 b7b5 a4b3 d7d6 c2c3 e8g8"
)
SCORES = (0, 18, -7, 34, 9, 42, 20, 61, 40, 78, 55, 91, 72, 113, 89, 127, 146)


def wait_for(condition, description: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out waiting for {description}")
        QTest.qWait(25)


def assert_action_target(window: QWidget, widget: QWidget, description: str) -> None:
    """Check a real visible click target, not only a container's dimensions."""
    assert widget.isVisibleTo(window), f"{description} is hidden"
    rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
    assert rect.width() >= 24 and rect.height() >= 24, (
        f"{description} has a truncated click target {rect.getRect()}"
    )
    assert window.rect().contains(rect), (
        f"{description} extends outside the actual window: {rect.getRect()}"
    )
    if hasattr(widget, "accessibleName"):
        assert widget.accessibleName() or getattr(widget, "text", lambda: "")(), (
            f"{description} lacks an accessible label"
        )


def assert_no_widget_overlap(first: QWidget, second: QWidget, description: str) -> None:
    assert first.isVisible() and second.isVisible(), f"{description}: a required widget is hidden"
    first_rect = QRect(first.mapToGlobal(QPoint(0, 0)), first.size())
    second_rect = QRect(second.mapToGlobal(QPoint(0, 0)), second.size())
    assert not first_rect.intersects(second_rect), (
        f"{description}: {first_rect.getRect()} overlaps {second_rect.getRect()}"
    )


def assert_label_fits(label: QLabel, description: str) -> None:
    assert label.isVisible(), f"{description} is hidden"
    if label.hasHeightForWidth():
        required = label.heightForWidth(max(1, label.width()))
        assert required <= label.height(), (
            f"{description} is vertically clipped: required={required}, actual={label.height()}, "
            f"label_width={label.width()}, label_min_height={label.minimumHeight()}, "
            f"label_size_hint={label.sizeHint().width()}x{label.sizeHint().height()}, "
            f"parent={label.parentWidget().objectName() if label.parentWidget() else None} "
            f"{label.parentWidget().width()}x{label.parentWidget().height()}"
        )
    elif not label.wordWrap():
        required = label.fontMetrics().horizontalAdvance(label.text())
        assert required <= label.width(), (
            f"{description} is horizontally clipped: required={required}, actual={label.width()}, "
            f"text={label.text()!r}"
        )


def assert_scroll_action(scroll: QScrollArea, widget: QWidget, description: str) -> None:
    # Qt defaults to a 50 px margin. At 125%/150% a valid bottom-row action
    # can be shorter than that extra margin even though the full click target
    # fits in the viewport; validate reachability with no artificial padding.
    scroll.ensureWidgetVisible(widget, 0, 0)
    QApplication.processEvents()
    assert widget.isVisible(), f"{description} is hidden"
    bounds = QRect(widget.mapTo(scroll.viewport(), QPoint(0, 0)), widget.size())
    assert scroll.viewport().rect().contains(bounds), (
        f"{description} cannot be fully reached in its local scroll area: "
        f"widget={bounds.getRect()}, viewport={scroll.viewport().rect().getRect()}, "
        f"content=({scroll.widget().width()}, {scroll.widget().height()}), "
        f"scroll_range=({scroll.verticalScrollBar().minimum()}"
        f"..{scroll.verticalScrollBar().maximum()}), "
        f"scroll_value={scroll.verticalScrollBar().value()}"
    )
    assert bounds.width() >= 24 and bounds.height() >= 24, (
        f"{description} has a truncated click target: {bounds.getRect()}"
    )


def assert_native_tooltip_bounds(window: QWidget, widget: QWidget, description: str) -> None:
    tooltip = widget.toolTip().strip()
    assert tooltip, f"{description} must have a full explanatory tooltip"
    anchor = widget.mapToGlobal(widget.rect().center())
    QToolTip.showText(anchor, tooltip, widget)
    QTest.qWait(120)
    popup = QApplication.activePopupWidget()
    if popup is not None and popup.isVisible():
        screen = window.screen()
        assert screen is not None
        popup_global = QRect(popup.mapToGlobal(QPoint(0, 0)), popup.size())
        assert screen.availableGeometry().contains(popup_global), (
            f"{description} tooltip extends beyond the usable display: "
            f"{popup_global.getRect()} vs {screen.availableGeometry().getRect()}"
        )
    QToolTip.hideText()


def seed_synthetic_repository(repo, settings) -> tuple[str, str]:
    account_id = repo.ensure_archive_account("visual-synthetic")
    settings.setValue("workspace/selected_account", account_id)
    settings.setValue("workspace/sidebar_explicit", False)
    settings.setValue("workspace/sidebar_collapsed", False)
    opponents = ("Synthetic Owl", "Synthetic Fox", "Synthetic Rook")
    for index in range(8):
        days_ago = max(0, 7 - index)
        # Keep every fixture timestamp in the past even when the wall clock is
        # before 18:30 UTC. Replacing the time-of-day on today's date used to
        # put the newest synthetic game in the future and exclude it from the
        # Progress dialog's inclusive-until-now filter.
        fixture_time = datetime.now(timezone.utc) - timedelta(days=days_ago + 1, hours=1)
        game_id = str(index)
        winner = "white" if index % 3 else "black"
        opponent = opponents[index % 3]
        repo.save_snapshot(
            game_id, "startpos", MOVE_TEXT, "white",
            opponent,
            False, "180+2", "resign", winner,
            white_player="Visual Coach", black_player=opponent,
            started_at=fixture_time.isoformat())
    repo.wait_for_writes()
    with repo._connection() as db:
        db.executemany(
            "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent,"
            "player_rating,opponent_rating) VALUES (?,?,?,?,?,?,?,?)",
            [(account_id, str(index), f"synthetic-{index}",
              (datetime.now(timezone.utc)
               - timedelta(days=max(0, 7 - index) + 1, hours=1)).isoformat(), "white",
              opponents[index % 3], 1680 + index * 7, 1660 + index * 5)
             for index in range(8)])
        db.execute("UPDATE games SET source='lichess_archive',source_game_id='synthetic-0' "
                   "WHERE game_id='0'")
        db.execute("UPDATE games SET source='lichess_archive',source_game_id='synthetic-1' "
                   "WHERE game_id='1'")
        db.execute("UPDATE games SET source='lichess_archive',source_game_id='synthetic-6' "
                   "WHERE game_id='6'")
        db.execute("UPDATE games SET source='lichess_archive',source_game_id='synthetic-7',"
                   "lichess_accuracy_white=88.4,lichess_accuracy_black=84.7 WHERE game_id='7'")
        db.execute("UPDATE games SET opening_eco='C60',opening_name='Испанская партия' "
                   "WHERE game_id IN ('5','6','7')")
    repo.wait_for_writes()

    for game_id, sign in (("5", 1), ("6", 1), ("7", -1)):
        line = repo.current_line(game_id)
        repo.save_analysis(
            game_id, line_revision=line["line_revision"], line_hash=line["line_hash"],
            source="stockfish", engine_name="Synthetic Fixture", engine_version="fixture-v1",
            parameters={"nodes": 100},
            assessments=[{"ply": ply, "white_cp": sign * value, "depth": 16,
                          "played_uci": "e2e4" if ply == 1 else None}
                         for ply, value in enumerate(SCORES)],
            classifications=[])
    repo.wait_for_writes()
    assert repo.materialize_game_accuracy_batch()["processed"] >= 2
    today = datetime.now(timezone.utc)
    repo.save_puzzle_player_rating(account_id, [
        [day.year, day.month - 1, day.day, 1640 + n * 9 + (n % 4) * 13]
        for n, day in enumerate(today - timedelta(days=57 - 3 * n)
                                 for n in range(20))])
    puzzle_fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    repo.import_puzzle_activities("visual-synthetic", [
        {"date": int((today - timedelta(days=index)).timestamp() * 1000),
         "win": bool(index % 3),
         "puzzle": {"id": f"visual-puzzle-{index:02}", "fen": puzzle_fen,
                    "solution": ["f1c4", "g8f6"], "themes": ["opening", "developing"],
                    "rating": 1320 + index * 17, "plays": 90 + index * 11}}
        for index in range(18)])

    # Seed durable but synthetic background states through their public stores.
    repo.finish_archive_incremental(account_id, 1_799_000_000_000, last_sync_new_count=8)
    repo.record_puzzle_error(account_id, "synthetic fixture state")
    repo.finish_puzzle_incremental(account_id, 1_799_000_000_000, last_sync_new_count=12)
    repo.finish_puzzle_backfill(account_id)
    repo.enqueue_lichess_analysis_backfill(account_id)
    for game_id, accuracy, evals in (
            ("0", "queued", "queued"),
            ("1", "has_data", "partial"),
            ("6", "no_server_data", "no_server_data"),
            ("7", "has_data", "has_data")):
        line = repo.current_line(game_id)
        repo.record_lichess_analysis_backfill(
            account_id, game_id, accuracy_status=accuracy, evals_status=evals,
            line_revision=line["line_revision"], line_hash=line["line_hash"])
    with repo._connection() as db:
        db.execute("UPDATE archive_sync_state SET backfill_complete=0,imported_count=8 "
                   "WHERE account_id=?", (account_id,))
        db.execute("UPDATE puzzle_sync_state SET imported_count=12 WHERE account_id=?",
                   (account_id,))

    return account_id, "7"


def seed_stockfish_job(repo, account_id: str, root: Path) -> tuple[BatchAnalysisRunner, dict, dict]:
    binary = root / "synthetic-stockfish.exe"
    binary.write_bytes(b"synthetic screenshot fixture; never executed")
    runner = BatchAnalysisRunner(repo, binary, options=FastPassOptions(nodes=20_000, hash_mb=64))
    line = repo.current_line("6")
    repo.enqueue_analysis_jobs(
        [{"game_id": "6", "line_revision": line["line_revision"],
          "line_hash": line["line_hash"]}], analysis_pass="fast",
        engine_version=runner.engine_key, parameters=runner.parameters, retry_failed=False)
    complete = repo.claim_next_analysis_job(
        "visual-complete", engine_version=runner.engine_key, analysis_pass="fast",
        parameters=runner.parameters, account_id=account_id)
    assert complete is not None
    completed_run = repo.list_analyses("6")[0]
    repo.finish_analysis_job(complete["job_id"], "visual-complete", status="complete",
                             result_run_id=completed_run["run_id"])
    missing = next(game for game in repo.list_games_needing_fast_analysis(
        account_id=account_id, limit=10, engine_version=runner.engine_key,
        parameters=runner.parameters) if game["game_id"] == "0")
    repo.enqueue_analysis_jobs([missing], analysis_pass="fast", engine_version=runner.engine_key,
                               parameters=runner.parameters)
    active = repo.claim_next_analysis_job(
        "visual-active", engine_version=runner.engine_key, analysis_pass="fast",
        parameters=runner.parameters, account_id=account_id)
    assert active is not None
    return runner, active, complete


def make_window(app: QApplication):
    runtime = create_runtime(offline_mode=True)
    account_id, selected_game_id = seed_synthetic_repository(runtime.repository, runtime.settings)
    # Catch schema/fixture errors before the asynchronous UI worker obscures them.
    snapshot = dashboard_snapshot(runtime.repository, account_id=account_id)
    assert len(snapshot.get("recent_games") or []) == 3
    runner, active_job, complete_job = seed_stockfish_job(
        runtime.repository, account_id, runtime.data_root)
    runtime.settings.setValue(
        f"batch/plan/{account_id}",
        {"engine": runner.engine_key, "nodes": runner.options.nodes,
         "hash": runner.options.hash_mb, "review": False})
    window = MainWindow(runtime=runtime)
    # This inert identity supplies only an account scope to the read-only
    # background view. Offline mode still disables every transport/API path.
    window.lichess = SimpleNamespace(username="visual-synthetic", auth_generation=1)
    window._lichess_connected = False
    window.show()
    assert QTest.qWaitForWindowExposed(window, 5000), "Native MainWindow did not become exposed"
    return window, runtime, account_id, selected_game_id, runner, active_job, complete_job


def wait_home(window, account_id: str) -> None:
    try:
        wait_for(lambda: window._dashboard_options_loaded
                 and window.dashboard_account.currentData() == account_id
                 and bool(window._latest_dashboard_snapshot)
                 and not window._latest_dashboard_snapshot.get("is_preview")
                 and window._dashboard_metric_values["games"].text() == "8"
                 and window._home_view.puzzle_preview_board.isVisible()
                 and window._home_view.puzzle_preview_board.width() >= 112
                 and window._home_view.puzzle_preview_board.height() >= 112
                 and bool(window._home_view._puzzle_preview)
                 and str(window._home_view._puzzle_preview.get("puzzle_id", "")).startswith(
                     "visual-puzzle-")
                 and len([window._home_view._recent_games_layout.itemAt(i).widget()
                          for i in range(window._home_view._recent_games_layout.count())
                          if window._home_view._recent_games_layout.itemAt(i).widget()
                          and window._home_view._recent_games_layout.itemAt(i).widget().objectName() == "metricCard"]) == 3,
                 "Home dashboard account data, accuracy and all three recent-game cards",
                 timeout=90)
    except AssertionError:
        print("Dashboard diagnostics:", {
            "options_loaded": window._dashboard_options_loaded,
            "account_data": window.dashboard_account.currentData(),
            "account_items": [(window.dashboard_account.itemText(i), window.dashboard_account.itemData(i))
                               for i in range(window.dashboard_account.count())],
            "metric_values": {key: value.text() for key, value in window._dashboard_metric_values.items()},
            "metric_details": {key: value.text() for key, value in window._dashboard_metric_details.items()},
            "recent_layout_count": window._home_view._recent_games_layout.count(),
        })
        raise
    assert window._dashboard_metric_values["accuracy"].text().endswith("%")
    assert "Lichess" in window._dashboard_metric_details["accuracy"].text()
    assert "не равно числу всех партий с позиционными оценками" in (
        window._dashboard_metric_details["accuracy"].toolTip())
    assert "разные показатели с разными знаменателями" in (
        window._dashboard_metric_details["games"].toolTip())
    game_detail = window._dashboard_metric_details["games"].text()
    assert game_detail.count("С оценками:") == 1, (
        "The Home completed-games KPI must not repeat its source summary."
    )
    assert window._dashboard_metric_details["analysis"].isHidden(), (
        "The hidden legacy analysis KPI must not duplicate details in the games card."
    )
    recent_cards = [window._home_view._recent_games_layout.itemAt(i).widget()
                    for i in range(window._home_view._recent_games_layout.count())
                    if window._home_view._recent_games_layout.itemAt(i).widget()
                    and window._home_view._recent_games_layout.itemAt(i).widget().objectName() == "metricCard"]
    assert all(card.findChild(HomeEvaluationGraph) is not None for card in recent_cards), (
        "All three synthetic Home games require a real rendered evaluation graph."
    )
    for index, card in enumerate(recent_cards, start=1):
        viewport = window._home_view.recent_scroll.viewport()
        if card.width() > viewport.width():
            root = card.layout()
            details = card.findChild(QWidget, "dashboardDetailsColumn")
            graph = card.findChild(QWidget, "dashboardGraphColumn")
            print("Home card geometry at failed settle:", {
                "card": card.geometry().getRect(),
                "minimum_width": card.minimumWidth(),
                "minimum_size_hint": card.minimumSizeHint().width(),
                "size_hint": card.sizeHint().width(),
                "viewport": viewport.geometry().getRect(),
                "viewport_width": viewport.width(),
                "scroll_widget": (
                    window._home_view.recent_scroll.widget().width(),
                    window._home_view.recent_scroll.widget().height()),
                "root_direction": root.direction() if root else None,
                "root_minimum": root.minimumSize().width() if root else None,
                "details_minimum": details.minimumSizeHint().width() if details else None,
                "graph_minimum": graph.minimumSizeHint().width() if graph else None,
                "window_size": (window.width(), window.height()),
            }, flush=True)
        assert card.width() <= viewport.width(), (
            f"Home card {index} exceeds its scroll viewport width: "
            f"{card.width()} > {viewport.width()}"
        )
        if card.width() < 400:
            assert card._responsive_details_layout.direction() == (
                QBoxLayout.Direction.TopToBottom), (
                    "Home details should stack only below their measured fit width."
                )
        for label_index, label in enumerate(card.findChildren(QLabel), start=1):
            if label.isVisible():
                assert_label_fits(
                    label,
                    f"Home card {index}, text {label_index} ({label.text()!r})",
                )
        graph = card.findChild(HomeEvaluationGraph)
        assert graph is not None and graph.width() >= 160, (
            f"Home card {index} evaluation graph is too narrow: "
            f"{graph.width() if graph is not None else 'missing'}"
        )
        assert graph.points, f"Home card {index} has no source-backed graph points."


def wait_selected_review(page, expected_game_id: str) -> None:
    def ready() -> bool:
        selected = page.selected_game()
        return bool(
            selected and str(selected.get("game_id")) == expected_game_id
            and selected.get("line_revision") is not None
            and page._detail_game is not None
            and page._detail_game.get("line_revision") == selected.get("line_revision")
            and page.detail_open.isEnabled()
            and page.detail_status.text().startswith("Stockfish")
            and page.detail_graph.isVisible()
            and page.detail_graph.full_ply_count >= 16
            and all(badge.isVisible() for badge in page.source_badges.values())
            and all(caption.isVisible() and value.text()
                    for caption, value in page.metric_rows.values()))

    try:
        wait_for(ready, "selected evaluated game, both sources, complete metrics and nonempty graph")
    except AssertionError:
        print("Library diagnostics:", {
            "selected": page.selected_game(),
            "detail_title": page.detail_title.text(),
            "detail_status": page.detail_status.text(),
            "detail_metrics": page.detail_metrics.text(),
            "graph_visible": page.detail_graph.isVisible(),
            "graph_positions": page.detail_graph.full_ply_count,
            "badges": {key: (value.text(), value.isVisible())
                       for key, value in page.source_badges.items()},
            "metric_rows": {key: (caption.isVisible(), value.text())
                            for key, (caption, value) in page.metric_rows.items()},
        }, flush=True)
        raise


def wait_review_workspace(page, expected_game_id: str) -> None:
    def ready() -> bool:
        review = page.review or {}
        game = review.get("game") or {}
        analysis = review.get("analysis") or {}
        return bool(
            str(game.get("game_id")) == expected_game_id
            and game.get("line_revision") is not None
            and analysis.get("source") == "stockfish"
            and page.graph.full_ply_count >= 16
            and page.side_metrics_summary.rowCount() >= 4
            and page.title.text())

    wait_for(ready, "loaded selected game review, Stockfish data and graph")


def navigate_to_games(window: MainWindow, selected_game_id: str) -> None:
    preexisting_top_level_widgets = set(QApplication.topLevelWidgets())
    QTest.mouseClick(window._nav_buttons["analysis"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(window.analysis_page.buttons["games"], Qt.MouseButton.LeftButton)
    page = window.library_page
    assert not page.findChildren(QWidget, "sortDescription")
    qt_floats = [widget for widget in QApplication.topLevelWidgets()
                 if widget is not window and widget not in preexisting_top_level_widgets
                 and widget.isVisible()]
    assert not qt_floats, (
        "Entering My Games must not open a sorting/help popup; visible top-level widgets: "
        + ", ".join(f"{type(widget).__name__}:{widget.windowTitle()}" for widget in qt_floats))
    wait_for(lambda: page.games.count() >= 3 and page.selected_game() is not None,
             "library rows from the real query worker")
    item = next((page.games.item(index) for index in range(page.games.count())
                 if isinstance(page.games.item(index), QListWidgetItem)
                 and str((page.games.item(index).data(Qt.ItemDataRole.UserRole) or {}).get("game_id"))
                 == selected_game_id), None)
    assert item is not None, f"selected synthetic record {selected_game_id!r} was not loaded"
    rect = page.games.visualItemRect(item)
    assert rect.isValid() and rect.width() > 0, "selected row is outside the loaded page"
    QTest.mouseClick(page.games.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    wait_selected_review(page, selected_game_id)
    assert page.detail_title.text().startswith("Победа")
    selected = page.selected_game()
    expected_opponent = selected.get("account_opponent") or selected.get("opponent")
    assert expected_opponent in page.detail_players.text(), (
        "The selected row and player score must identify the same synthetic opponent.")
    assert "88.4% · Lichess" in page.detail_metrics.text()
    assert "Stockfish · Оценки ходов" == page.source_badges["Stockfish"].text()
    assert "Lichess · Официальная точность" == page.source_badges["Lichess"].text()


def create_fake_active_operations(window, account_id: str):
    release_events = []
    handles = []
    for kind, lane, progress in (
            ("lichess_archive_sync", OperationLane.NETWORK_BACKGROUND, (42, 100)),
            ("stockfish_batch_analysis", OperationLane.ENGINE, (9, 16))):
        release = threading.Event()
        release_events.append(release)
        handle = window.runtime.work_coordinator.start(
            lambda event=release: event.wait(), kind=kind, lane=lane,
            account_id=account_id, auth_generation=0, stop_event=release,
            thread_name=f"visual-{kind}")
        handles.append(handle)
        wait_for(lambda item=handle: item.snapshot.status.value == "running",
                 f"synthetic operation {kind} to enter running state")
        handle.update_progress(*progress)
    return release_events, handles


def wait_background(window, *, active: bool) -> None:
    page = window._background_operations_page
    if active:
        try:
            # The initial status view intentionally performs its SQLite reads
            # on a worker. On a cold hosted Windows runner, Qt's first native
            # window and dashboard startup can contend for the same process
            # resources; allow that production read to finish instead of
            # treating the normal async loading state as a broken UI.
            wait_for(lambda: page.summary.text().startswith("Сейчас:")
                     and page.workflow_badges["archive"].text() == "Идёт"
                     and page.workflow_badges["stockfish"].text() == "Идёт"
                     and page.workflow_badges["puzzles"].text() == "Готово"
                     and page.workflow_badges["ready"].text() == "Ожидает"
                     and page.recent_events.count() >= 3,
                     "active archive/Stockfish with waiting ready-analysis, completed puzzles and events",
                     timeout=60)
        except AssertionError:
            print("Background diagnostics:", {
                "summary": page.summary.text(),
                "operations": [(item.context.kind, item.status.value)
                               for item in window.runtime.work_coordinator.snapshots()],
                "badges": {key: value.text() for key, value in page.workflow_badges.items()},
                "statuses": {key: value.text() for key, value in page.workflow_status.items()},
                "events": page.recent_events.count(),
            }, flush=True)
            raise
    else:
        try:
            wait_for(lambda: page.summary.text() == "Сейчас активных задач нет"
                 and not any(operation.status.value in {"queued", "running"}
                             and getattr(operation.context, "kind", "")
                             not in {"background_status_read", "dashboard_read",
                                     "dashboard_preview_read", "dashboard_options_read"}
                             for operation in window.runtime.work_coordinator.snapshots()),
                 "honest offline state with no active background workflows")
        except AssertionError:
            print("Background diagnostics:", {
                "summary": page.summary.text(),
                "operations": [(item.context.kind, item.status.value)
                               for item in window.runtime.work_coordinator.snapshots()],
                "badges": {key: value.text() for key, value in page.workflow_badges.items()},
                "statuses": {key: value.text() for key, value in page.workflow_status.items()},
            }, flush=True)
            raise


def exercise_play_mode_targets(window: MainWindow) -> None:
    page = window._play_mode_page
    create_spy = QSignalSpy(page.create_requested)
    for mode in ("local", "ai", "friend", "challenge"):
        button = page.buttons[mode]
        assert_scroll_action(page.content_scroll, button, f"Play mode {mode}")
        assert_action_target(window, button, f"Play mode {mode}")
        assert button.accessibleName() or button.text(), f"Mode {mode} needs an accessible label"
        # Native Qt's synthesized click follows the real screen pointer at high
        # DPI; position it explicitly so this verifies an actual click target.
        QCursor.setPos(button.mapToGlobal(button.rect().center()))
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        QTest.qWait(10)
        assert page.mode_selector.currentData() == mode, (
            f"Clicking {mode} must select that mode exactly once."
        )
        assert all(not card.isHidden() for card in page.cards), (
            "All game-mode cards must remain visible after selection."
        )
        expected_active_panel = page.active_games.count() > 0
        assert page.active_panel.isVisible() == expected_active_panel, (
            "The active-games panel is visible only when confirmed games are listed."
        )
        assert create_spy.count() == 0, "Selecting a mode must not submit a game/challenge POST."
    page.active_games.clearSelection()
    page._clear_active_preview("Синтетическая позиция не загружена.")
    assert page.active_preview_board.isHidden()
    assert not page.active_open.isEnabled()
    page.select_mode("local")
    page.content_scroll.verticalScrollBar().setValue(0)


def capture(window: MainWindow, output: Path, name: str, width: int, height: int, *,
            review_selection: str = "last") -> QImage:
    if window.isMaximized() or window.isFullScreen():
        window.showNormal()
    window.resize(width, height)
    QTest.qWait(30)
    current = window.screen_stack.currentWidget()
    if current is window.play_page:
        page = window._play_mode_page
        assert page.form_rows["Режим"].isHidden(), (
            "Mode cards are the primary navigation; a duplicate visible combo wastes compact space."
        )
        mode = str(page.mode_selector.currentData())
        assert all(not card.isHidden() for card in page.cards), (
            "Every game mode must remain reachable while Active is selected."
        )
        expected_active_panel = page.active_games.count() > 0
        assert page.active_panel.isVisible() == expected_active_panel, (
            "Active-games content is visible only when confirmed games are listed."
        )
        if mode in page.buttons:
            assert_scroll_action(page.content_scroll, page.buttons[mode], f"Play mode {mode}")
            assert_action_target(window, page.buttons[mode], f"Play mode {mode}")
        assert page.form_card.isAncestorOf(page.start_button), (
            "The primary start action belongs to the selected mode form, matching UX-01/02/03."
        )
        assert_scroll_action(page.content_scroll, page.start_button, "Play start action")
        assert_action_target(window, page.start_button, "Play start action")
        if page.active_open.isVisibleTo(window):
            assert_action_target(window, page.active_open, "Continue selected game action")
        page.content_scroll.verticalScrollBar().setValue(0)
        QTest.qWait(20)
    if current is window.game_page:
        assert window.game_status.isVisible()
        assert_label_fits(window.game_status, "Game status")
        assert window.moves.isVisible() and window.moves.table.rowCount() > 0
        assert window.game_splitter.orientation() == (
            Qt.Orientation.Vertical if window._game_layout_should_stack()
            else Qt.Orientation.Horizontal)
        assert_no_widget_overlap(window.board, window.game_status, "Game board and status")
        assert_no_widget_overlap(window.board, window.moves, "Game board and move history")
        assert_scroll_action(window.game_controls_scroll, window.clear_marks_button,
                             "Clear board annotations")
        assert_action_target(window, window.clear_marks_button, "Clear board annotations")
        window.game_controls_scroll.verticalScrollBar().setValue(0)
        QTest.qWait(20)
    training_page = (current if isinstance(current, PuzzleHistoryPage)
                     else current.findChild(PuzzleHistoryPage))
    # A hidden PuzzleHistoryPage remains a child of the shell when another
    # route is active. Its last query need not be populated until Training is
    # opened, so inspect it only while it contributes pixels to this capture.
    if training_page is not None and training_page.isVisibleTo(window):
        assert training_page.history.count() > 0
        assert training_page.board.isVisible()
        assert_no_widget_overlap(training_page.board, training_page.solution,
                                 "Puzzle board and solution text")
        for button, name_ in ((training_page.replay, "Repeat puzzle"),
                              (training_page.reveal, "Reveal solution"),
                              (training_page.reset_replay, "Reset puzzle"),
                              (training_page.close_button, "Close puzzle history")):
            assert_action_target(window, button, name_)
        assert_label_fits(training_page.solution, "Puzzle solution/explanation")
        assert training_page.history.horizontalScrollBar().maximum() == 0, (
            "Puzzle history must elide long rows instead of adding horizontal scrolling."
        )
        for index in range(training_page.history.count()):
            item = training_page.history.item(index)
            assert item.toolTip(), "Elided puzzle history rows must preserve full details on hover."
    if current is window.review_workspace:
        page = current
        assert page.review and page.moves_list.table.rowCount() > 0
        if page.training_allowed:
            assert page.side_metrics_summary.rowCount() >= 4
        assert page.graph.full_ply_count >= 16
        assert_no_widget_overlap(page.review_board, page.side_metrics_summary,
                                 "Review board and comparison table")
        assert_action_target(window, page.export_button, "Export PGN")
        assert_action_target(window, page.training_button, "Review mistakes")
        last_ply = len(page.review.get("moves") or {})
        if review_selection == "last":
            # Select the last move through the production review API so its table
            # row is scrolled into view before verifying the real clickable target.
            page.select_ply(last_ply)
            QTest.qWait(50)  # allow the native splitter/table geometry pass to settle
            QApplication.processEvents()
        elif review_selection not in {"keep", "keep-variation"}:
            raise ValueError(f"Unknown review capture selection: {review_selection}")
        selected_ply = int(page.selected_ply)
        row = (page.moves_list.item_by_ply(selected_ply) if selected_ply else
               page.moves_list.item_by_ply(1))
        variation_id = page._selected_variation_id
        if variation_id and review_selection == "keep-variation":
            row = next((page.moves_list.table.item(index, 0)
                        for index in range(page.moves_list.table.rowCount())
                        if (data := page.moves_list.table.item(index, 0).data(
                            Qt.ItemDataRole.UserRole))
                        and isinstance(data, tuple) and data[0] == "variation"
                        and data[1] == variation_id and data[2] == page._selected_variation_ply),
                       None)
        assert row is not None and row.text().strip(), "Selected-line move row must be populated."
        bounds = page.moves_list.table.visualItemRect(row)
        if not (bounds.isValid() and page.moves_list.table.viewport().rect().contains(bounds)):
            print("review move target geometry:", {
                "last_ply": last_ply, "selected_ply": page.selected_ply,
                "rows": page.moves_list.table.rowCount(),
                "row_index": page.moves_list.table.row(row) if row else None,
                "item_ply": row.data(Qt.ItemDataRole.UserRole) if row else None,
                "bounds": bounds.getRect(), "viewport": page.moves_list.table.viewport().rect().getRect(),
                "table": page.moves_list.table.geometry().getRect(),
                "visible": page.moves_list.table.isVisible(),
                "scroll": page.moves_list.table.verticalScrollBar().value(),
                "scroll_max": page.moves_list.table.verticalScrollBar().maximum(),
                "page": page.geometry().getRect(),
                "split": page.workspace_splitter.geometry().getRect(),
                "board_column": page.board_column.geometry().getRect(),
                "inspector": page.review_inspector.geometry().getRect(),
                "review_layout": [page.layout().itemAt(i).geometry().getRect()
                                  for i in range(page.layout().count())],
                "inspector_layout": [page.review_inspector.layout().itemAt(i).geometry().getRect()
                                     for i in range(page.review_inspector.layout().count())],
            })
        assert bounds.isValid() and page.moves_list.table.viewport().rect().contains(bounds), (
            "The selected move must be visible and clickable in the review table."
        )
        if variation_id and review_selection == "keep-variation":
            QTest.mouseClick(page.moves_list.table.viewport(), Qt.MouseButton.LeftButton,
                             pos=bounds.center())
            assert page._selected_variation_id == variation_id
        elif selected_ply:
            QTest.mouseClick(page.moves_list.table.viewport(), Qt.MouseButton.LeftButton,
                             pos=bounds.center())
            assert page.selected_ply == selected_ply
        if review_selection == "last":
            page.select_ply(0)
    if current is window.home_page:
        for route, button in window._nav_buttons.items():
            if route in {"home", "play", "analysis", "training", "progress"}:
                assert button.isVisible() and button.isEnabled()
                assert_action_target(window, button, f"Navigation {route}")
    if isinstance(current, BackgroundOperationsPage):
        cards = [item[0] for item in current._workflow_cards]
        assert all(left.geometry().bottom() < right.geometry().top()
                   for left, right in zip(cards, cards[1:])), (
            f"background workflow cards overlap at {width}x{height}: "
            f"{[card.geometry().getRect() for card in cards]}"
        )
        assert cards[-1].geometry().bottom() < current.recent_events_title.geometry().top(), (
            f"background cards overlap the recent events heading at {width}x{height}"
        )
        assert not current.recent_events.geometry().intersects(current.close_button.geometry()), (
            f"background events overlap the back button at {width}x{height}"
        )
        for key, label in current.workflow_status.items():
            required = (label.heightForWidth(label.width()) if label.hasHeightForWidth()
                        else label.sizeHint().height())
            assert required <= label.height(), (
                f"background {key} status text is clipped at {width}x{height}: "
                f"required={required}, actual={label.height()}"
            )
        print(f"background geometry: {width}x{height}, no overlaps; status labels fit")
    if current is window.home_page:
        expected_account = window.dashboard_account.currentData()
        if expected_account:
            wait_home(window, str(expected_account))
        progress = window._home_view
        board_minimum = 176 if width >= 1500 else 112
        assert progress.puzzle_preview_board.width() >= board_minimum
        assert progress.puzzle_preview_board.height() >= board_minimum
        overview_viewport = progress.home_overview_scroll.viewport()
        viewport_left = overview_viewport.mapToGlobal(QPoint(0, 0)).x()
        viewport_right = viewport_left + overview_viewport.width()
        for key in ("games", "accuracy"):
            metric = progress.dashboard_metrics[key]
            metric_left = metric.mapToGlobal(QPoint(0, 0)).x()
            metric_right = metric_left + metric.width()
            assert metric_left >= viewport_left and metric_right <= viewport_right, (
                f"Home {key} KPI is horizontally clipped at {width}x{height}: "
                f"metric={metric_left}:{metric_right}, "
                f"viewport={viewport_left}:{viewport_right}"
            )
        graph_rect = QRect(progress.puzzle_sparkline.mapToGlobal(QPoint(0, 0)),
                           progress.puzzle_sparkline.size())
        trend_rect = QRect(progress.progress_trend.mapToGlobal(QPoint(0, 0)),
                           progress.progress_trend.size())
        if progress.progress_trend.isVisible():
            assert not graph_rect.intersects(trend_rect), (
                f"Home puzzle trend label overlaps its graph at {width}x{height}: "
                f"graph={graph_rect.getRect()}, label={trend_rect.getRect()}"
            )
            assert_label_fits(progress.progress_trend, "Home puzzle rating trend label")
        recent_cards = [window._recent_games_layout.itemAt(i).widget()
                        for i in range(window._recent_games_layout.count())
                        if window._recent_games_layout.itemAt(i).widget() is not None
                        and window._recent_games_layout.itemAt(i).widget().objectName() == "metricCard"]
        for card_index, card in enumerate(recent_cards, start=1):
            for label_index, label in enumerate(card.findChildren(QLabel), start=1):
                if label.isVisible():
                    assert_label_fits(label, f"Home {width}x{height}, card {card_index}, "
                                               f"label {label_index}")
        if width >= 1500 and height >= 900:
            recent = window._home_view.recent_scroll
            card_heights = [window._recent_games_layout.itemAt(i).widget().height()
                            for i in range(window._recent_games_layout.count())
                            if window._recent_games_layout.itemAt(i).widget() is not None]
            print(
                f"home layout: window={window.size().width()}x{window.size().height()}, "
                f"page={window.home_page.height()}, recent={recent.height()}px, "
                f"content={recent.widget().height()}px, "
                f"scroll_max={recent.verticalScrollBar().maximum()}, "
                f"cards={card_heights}")
            assert window._home_view.recent_scroll.verticalScrollBar().maximum() == 0, (
                "The approved wide Home reference requires all three recent cards visible "
                "without an internal scroll bar."
            )
    elif current is getattr(window, "library_page", None):
        game = window.library_page._detail_game or {}
        wait_selected_review(window.library_page, str(game.get("game_id") or ""))
        page = window.library_page
        page_actions = (page.previous, page.following, page.sync_button,
                        page.background_button, page.settings_button,
                        page.detail_open, page.export_game, page.analyze_game,
                        page.retry_analysis_button)
        for button in page_actions:
            if button.isVisible() and button.isEnabled():
                assert_action_target(window, button, button.accessibleName() or button.text())
        assert page.search.toolTip() == "Поиск по сопернику, дебюту или дате"
        assert_native_tooltip_bounds(window, page.search, "Library search")
        selected = page.selected_game()
        selected_item = page.games.currentItem()
        assert selected and selected_item and selected_item.toolTip(), (
            "The selected library row must preserve full text/source in its tooltip."
        )
        assert_label_fits(page.detail_title, "Library selected-game title")
        if page.detail_players.isVisible():
            assert_label_fits(page.detail_players, "Library selected players/score")
        if height <= 700:
            row_rect = page.page_actions_row.geometry()
            stack_rect = window.screen_stack.rect()
            row_in_stack = QRect(
                page.page_actions_row.parentWidget().mapTo(window.screen_stack,
                                                           row_rect.topLeft()),
                row_rect.size())
            buttons = (page.previous, page.following, page.sync_button,
                       page.background_button, page.settings_button)
            print("compact library geometry:", {
                "page": page.geometry().getRect(), "stack": stack_rect.getRect(),
                "row": row_rect.getRect(), "row_stack": row_in_stack.getRect(),
                "buttons": [(button.text(), button.geometry().getRect(),
                             button.mapTo(window.screen_stack, button.rect().bottomRight()).y())
                            for button in buttons],
            }, flush=True)
            assert row_rect.isValid() and stack_rect.contains(row_in_stack), (
                "Compact library must keep its pagination/action row inside the visible screen stack."
            )
            assert all(button.isVisible()
                       and stack_rect.contains(button.mapTo(window.screen_stack,
                                                           button.rect().bottomRight()))
                       for button in buttons), (
                "Compact library pagination and primary actions must remain visible."
            )
    elif current is getattr(window, "_background_operations_page", None):
        active = any(
            operation.status.value == "running"
            and getattr(operation.context, "kind", "") in {
                "lichess_archive_sync", "stockfish_batch_analysis"}
            for operation in window.runtime.work_coordinator.snapshots())
        wait_background(window, active=active)
        page = window._background_operations_page
        assert page.recent_events.count() >= 3
        if width >= 1500 and height >= 900:
            print(
                f"background layout: window={window.size().width()}x{window.size().height()}, "
                f"page={page.height()}px, events={page.recent_events.height()}px, "
                f"viewport={page.recent_events.viewport().height()}px, "
                f"scroll_max={page.recent_events.verticalScrollBar().maximum()}, "
                f"rows={page.recent_events.count()}, "
                f"row_heights={[page.recent_events.sizeHintForRow(i) for i in range(page.recent_events.count())]}, "
                f"row_rects={[page.recent_events.visualItemRect(page.recent_events.item(i)).getRect() for i in range(page.recent_events.count())]}, "
                f"cards={[card.height() for card, _icon, _title in page._workflow_cards]}")
            row_rects = [page.recent_events.visualItemRect(page.recent_events.item(i))
                         for i in range(page.recent_events.count())]
            viewport = page.recent_events.viewport().rect()
            assert all(rect.isValid() and viewport.contains(rect) for rect in row_rects), (
                "Every synthetic recent event row must be fully visible in the wide reference."
            )
            assert page.recent_events.verticalScrollBar().value() == 0
        print(
            f"background {width}x{height}: page={page.height()}px, "
            f"events={page.recent_events.count()} rows/{page.recent_events.height()}px, "
            f"cards={[card.height() for card, _icon, _title in page._workflow_cards]}")
    # Windows may clamp a top-level window to the usable work area (taskbar and
    # native frame). Treat that as the real display geometry, and fail only if
    # the toolkit creates a window larger than the requested test target.
    assert window.width() <= width and window.height() <= height, (
        f"Requested maximum {width}x{height}, actual Qt window is "
        f"{window.width()}x{window.height()}, minimum={window.minimumWidth()}x"
        f"{window.minimumHeight()}, maximized={window.isMaximized()}, "
        f"page={type(window.screen_stack.currentWidget()).__name__}"
    )
    assert window.isVisible(), "capture target is not visible"
    # Click-driven route navigation can leave the pointer on a control and
    # produce a floating tooltip in the screenshot. Clear it and move the
    # pointer outside the target window before taking the actual frame.
    QToolTip.hideText()
    QCursor.setPos(window.mapToGlobal(QPoint(width + 20, height + 20)))
    QTest.qWait(80)
    QToolTip.hideText()
    QApplication.processEvents()
    image = window.grab().toImage()
    assert not image.isNull()
    dpr = float(image.devicePixelRatio())
    expected_pixels = QSize(round(window.width() * dpr), round(window.height() * dpr))
    pixel_size_delta = (abs(image.width() - expected_pixels.width()),
                        abs(image.height() - expected_pixels.height()))
    assert max(pixel_size_delta) <= 1, (
        f"Actual logical window {window.width()}x{window.height()} at DPR {dpr} "
        "should capture as "
        f"{expected_pixels.width()}x{expected_pixels.height()}, got "
        f"{image.width()}x{image.height()}"
    )
    filename = f"{width}x{height}-{name}.png"
    assert image.save(str(output / filename))
    print(f"{filename}: window {window.width()}x{window.height()} logical px, "
          f"capture {image.width()}x{image.height()} px, DPR {image.devicePixelRatio()}")
    return image


def capture_home_metric_hover_evidence(window: MainWindow, output: Path,
                                       width: int, height: int) -> None:
    """Capture real pointer hovers over both large Home KPIs and their details."""
    window.resize(width, height)
    QTest.qWait(60)
    original_values = {key: widget.text() for key, widget in
                       window._dashboard_metric_values.items()}
    window._dashboard_metric_values["games"].setText("3325")
    window._dashboard_metric_values["accuracy"].setText("82.8%")
    assert window._dashboard_metric_values["games"].font().pixelSize() >= 40
    assert window._dashboard_metric_values["accuracy"].font().pixelSize() >= 40
    targets = (
        ("games-value", window._dashboard_metric_values["games"], False),
        ("games-detail", window._dashboard_metric_details["games"], True),
        ("accuracy-value", window._dashboard_metric_values["accuracy"], False),
        ("accuracy-detail", window._dashboard_metric_details["accuracy"], True),
    )
    screen = window.screen()
    assert screen is not None
    for name, widget, expects_tooltip in targets:
        assert widget.isVisibleTo(window), f"Home KPI hover target is not visible: {name}"
        QToolTip.hideText()
        QTest.mouseMove(widget, widget.rect().center())
        if expects_tooltip:
            deadline = time.monotonic() + 1.8
            while not QToolTip.isVisible() and time.monotonic() < deadline:
                QTest.qWait(50)
            assert QToolTip.isVisible(), f"Native Qt hover did not open tooltip: {name}"
            assert QToolTip.text() == widget.toolTip()
            popup = next((item for item in QApplication.topLevelWidgets()
                          if item.isVisible()
                          and item.windowType() == Qt.WindowType.ToolTip), None)
            if popup is None:
                popup = QApplication.activePopupWidget()
            assert popup is not None and popup.isVisible(), f"No native popup for {name}"
            popup_font = popup.font()
            pixel_size = popup_font.pixelSize()
            assert popup.width() <= 480, (
                f"Home tooltip exceeds 480 logical px: {name} width={popup.width()}"
            )
            assert pixel_size in (-1, 0) or pixel_size <= 14, (
                f"Home tooltip inherited large KPI font: {name} pixel-size={pixel_size}"
            )
            print(f"Home native tooltip {name}: {popup.width()}x{popup.height()} logical px; "
                  f"font pixel-size={pixel_size}; text={QToolTip.text()!r}", flush=True)
        else:
            QTest.qWait(900)
            assert not QToolTip.isVisible(), (
                f"Large KPI value must expose detail without a large tooltip: {name}"
            )
            print(f"Home KPI value hover {name}: no popup; value font pixel-size="
                  f"{widget.font().pixelSize()}", flush=True)
        QApplication.processEvents()
        image = screen.grabWindow(0).toImage()
        assert not image.isNull()
        path = output / f"{width}x{height}-home-tooltip-{name}.png"
        assert image.save(str(path)), f"Could not save native hover evidence: {path}"
        QToolTip.hideText()
    QCursor.setPos(window.mapToGlobal(QPoint(width + 20, height + 20)))
    QToolTip.hideText()
    for key, value in original_values.items():
        window._dashboard_metric_values[key].setText(value)


def make_pair(reference_dir: Path, output: Path, reference_file: str,
              actual: QImage, label: str) -> None:
    path = reference_dir / reference_file
    expected = REFERENCE_HASHES[reference_file]
    assert sha256(path.read_bytes()).hexdigest() == expected, f"approved reference changed: {path.name}"
    art = QImage(str(path))
    assert not art.isNull()
    art = art.scaled(actual.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(actual.width() * 2 + 16, actual.height(), QImage.Format.Format_RGB32)
    canvas.fill(QColor("#43465a"))
    painter = QPainter(canvas)
    painter.drawImage(0, 0, art)
    painter.drawImage(actual.width() + 16, 0, actual)
    painter.end()
    assert canvas.save(str(output / f"pair-{label}.png"))


def capture_long_game_graph_cases(window: MainWindow, output: Path,
                                  width: int, height: int) -> QImage:
    """Capture production Home cards with structurally realistic saved scores."""
    original = window._latest_dashboard_snapshot
    assert original and len(original.get("recent_games") or []) == 3
    snapshot = deepcopy(original)
    scenarios = ("continuous-long", "twenty-ply-gap", "mate-and-clipped")
    for game, scenario in zip(snapshot["recent_games"], scenarios):
        assessments = []
        anchors = ([(1, -160), (60, -60), (120, 80), (180, 20),
                    (240, 200), (300, 140), (360, 280), (420, 140),
                    (480, 220), (540, 80), (600, 170)]
                   if scenario != "mate-and-clipped" else
                   [(1, -120), (80, 20), (160, 80), (240, 160),
                    (320, 250), (360, 800), (380, 900), (400, 1400),
                    (420, 200), (440, 500), (460, -1000), (480, -1000),
                    (500, -200), (520, -780), (540, -450), (580, -100),
                    (600, 150)])

        def score_at(ply: int) -> int:
            for (left_ply, left_cp), (right_ply, right_cp) in zip(anchors, anchors[1:]):
                if ply <= right_ply:
                    fraction = (ply - left_ply) / (right_ply - left_ply)
                    base = left_cp + (right_cp - left_cp) * fraction
                    # Small, deterministic local movement avoids an artificial
                    # high-frequency sawtooth while keeping dense real plies.
                    return round(base + 18 * math.sin(ply / 11) + 8 * math.cos(ply / 23))
            return anchors[-1][1]

        for ply in range(1, 601):
            if scenario == "twenty-ply-gap" and 281 <= ply <= 300:
                continue
            if scenario == "mate-and-clipped" and ply >= 581:
                assessments.append({"ply": ply, "white_cp": None, "mate_white": 601 - ply})
                continue
            cp = score_at(ply)
            assessments.append({"ply": ply, "white_cp": cp, "mate_white": None})
        metrics = dict(game.get("review_metrics") or {})
        metrics["graph_points"] = _home_graph_points(assessments)
        metrics["total_positions"] = 601
        metrics["coverage"] = ("580/600" if scenario == "twenty-ply-gap" else "600/600")
        metrics["status"] = "complete"
        metrics["source"] = "synthetic saved assessments"
        game["review_metrics"] = metrics

    try:
        window._render_dashboard_snapshot(snapshot)
        graphs = [
            window._recent_games_layout.itemAt(index).widget().findChild(HomeEvaluationGraph)
            for index in range(window._recent_games_layout.count())
            if window._recent_games_layout.itemAt(index).widget() is not None
            and window._recent_games_layout.itemAt(index).widget().objectName() == "metricCard"
        ]
        assert len(graphs) == 3 and all(graph is not None for graph in graphs)
        for graph in graphs:
            host = graph.parentWidget()
            for object_name in ("dashboardGraphTitle", "dashboardGraphLegend",
                                "dashboardGraphSource"):
                assert host.findChild(QLabel, object_name) is None, (
                    f"Home graph must omit permanent density label {object_name}."
                )
            card = host.parentWidget()
            assert card.findChild(QLabel, "dashboardAnalysisCoverage") is None, (
                "Home recent cards must not show a permanent analysis coverage label."
            )
            assert "Источник: synthetic saved assessments" in graph.accessibleDescription()
            assert "Охват сохранённых позиций:" in graph.accessibleDescription()
        QApplication.processEvents()
        for graph in graphs:
            graph.repaint()
            assert not graph.grab().isNull()
        by_point_count = {len(graph.points): graph for graph in graphs}
        assert set(by_point_count) == {580, 581, 600}
        long_graph = by_point_count[600]
        gap_graph = by_point_count[580]
        mate_graph = by_point_count[581]
        print("Graph diagnostics:", [
            {"points": len(graph.points),
             "cp": sum(point.get("white_cp") is not None for point in graph.points),
             "connect_flags": sum(point.get("connect_from_previous") is True
                                  for point in graph.points),
             "segments": len(graph._drawn_cp_segments),
             "first": graph.points[:2], "last": graph.points[-2:]}
            for graph in graphs
        ], flush=True)
        assert len(long_graph._drawn_cp_segments) == 599
        assert len(gap_graph._drawn_cp_segments) == 578
        assert not any(start <= 280 < end for start, end in gap_graph._drawn_cp_segments)
        assert len(mate_graph._drawn_cp_segments) == 579
        assert len(mate_graph._mate_markers) == 1
        assert mate_graph._mate_markers[0][2:] == (581, 20)
        assert "Мат: белые через 1" in next(
            target[3] for target in mate_graph._hit_targets if target[2] == 600)
        assert mate_graph._clipped_markers == []
        raw_scores = [cp for _ply, cp, _x, _y, _tip in mate_graph._raw_cp_points]
        assert max(raw_scores) >= 1200 and min(raw_scores) <= -900
        assert any(150 <= cp <= 250 for cp in raw_scores)
        image = capture(window, output, "home-long-game-graph-cases", width, height)
        print("Long-game graph cases: 600 plies; one 20-ply assessment gap; "
              "20 mate assessments rendered as one interval; unbounded CP paths clip cleanly.")
        return image
    finally:
        window._render_dashboard_snapshot(original)


def capture_home_long_opponent_case(window: MainWindow, output: Path,
                                    width: int, height: int) -> QImage:
    """Check a real Home card with a long opponent name at this native size."""
    original = deepcopy(window._latest_dashboard_snapshot)
    assert original and len(original.get("recent_games") or []) == 3
    snapshot = deepcopy(original)
    long_name = ("Synthetic Opponent With A Deliberately Long Display Name "
                 "For Home Card Wrapping And Geometry 0123456789")
    snapshot["recent_games"][0]["opponent"] = long_name
    snapshot["recent_games"][0]["account_opponent"] = long_name
    try:
        window._render_dashboard_snapshot(snapshot)
        QApplication.processEvents()
        card = window._recent_games_layout.itemAt(0).widget()
        assert card is not None and card.objectName() == "metricCard"
        opponent = card.findChild(QLabel, "dashboardOpponent")
        assert opponent is not None and opponent.text() == long_name
        assert_label_fits(opponent, f"Home long opponent name at {width}x{height}")
        recent = window._home_view.recent_scroll
        assert recent.horizontalScrollBar().maximum() == 0, (
            f"Home recent cards must not create horizontal overflow at {width}x{height}."
        )
        viewport = recent.viewport()
        card_rect = QRect(card.mapTo(viewport, QPoint(0, 0)), card.size())
        assert card_rect.left() >= 0 and card_rect.right() <= viewport.width(), (
            f"Home long-name card exceeds its viewport at {width}x{height}: "
            f"card={card_rect.getRect()} viewport={viewport.rect().getRect()}"
        )
        if width >= 1500 and height >= 900:
            assert recent.verticalScrollBar().maximum() == 0
            cards = [window._recent_games_layout.itemAt(index).widget()
                     for index in range(window._recent_games_layout.count())
                     if window._recent_games_layout.itemAt(index).widget() is not None
                     and window._recent_games_layout.itemAt(index).widget().objectName()
                     == "metricCard"]
            assert len(cards) == 3
            assert all(viewport.rect().contains(
                QRect(item.mapTo(viewport, QPoint(0, 0)), item.size())) for item in cards)
        return capture(window, output, "home-long-opponent-name", width, height)
    finally:
        window._render_dashboard_snapshot(original)
        QApplication.processEvents()


def capture_home_rating_review(window: MainWindow, output: Path,
                               width: int, height: int) -> list[QImage]:
    """Capture two truthful date/rating shapes through the production Home UI."""
    chart = window._home_view.puzzle_sparkline
    original_points = deepcopy(chart.points)
    today = datetime.now(timezone.utc).date()
    fixtures = (
        ("high-amplitude-long-period", (90, 72, 54, 36, 18, 0),
         (1120, 2700, 1420, 2450, 1690, 2620)),
        ("low-amplitude-short-period", (6, 4, 2, 1, 0),
         (1802, 1813, 1806, 1817, 1810)),
    )
    images = []
    try:
        for name, ages, ratings in fixtures:
            chart.set_points([
                {"day": (today - timedelta(days=age)).isoformat(),
                 "rating": rating, "attempts": (12, 8)}
                for age, rating in zip(ages, ratings)
            ])
            window.resize(width, height)
            QTest.qWait(30)
            QApplication.processEvents()
            ancestor = chart
            while ancestor is not None:
                layout = ancestor.layout()
                if layout is not None:
                    layout.activate()
                ancestor = ancestor.parentWidget()
            QApplication.processEvents()
            chart.repaint()
            QApplication.processEvents()
            assert chart.isVisibleTo(window), "Home puzzle-rating chart must be visible."
            assert 2 <= len(chart._x_label_bounds) <= 4
            assert 2 <= len(chart._y_label_bounds) <= 5
            assert not any("−" in label or label.startswith("-")
                           for label in chart._x_axis_labels)
            image = capture(window, output, f"home-rating-{name}", width, height)
            images.append(image)
            QApplication.processEvents()
            chart_dpr = float(chart.devicePixelRatioF())
            chart_image = QImage(
                round(chart.width() * chart_dpr), round(chart.height() * chart_dpr),
                QImage.Format.Format_ARGB32)
            chart_image.setDevicePixelRatio(chart_dpr)
            chart_image.fill(Qt.GlobalColor.transparent)
            chart_painter = QPainter(chart_image)
            chart.render(chart_painter, QPoint())
            chart_painter.end()
            assert chart_image.save(str(
                output / f"{width}x{height}-home-rating-{name}-chart.png"))
            label_bounds = chart._x_label_bounds + [
                rect for _value, rect in chart._y_label_bounds]
            out_of_bounds = [bounds.getRect() for bounds in label_bounds
                             if not chart.rect().contains(bounds)]
            if out_of_bounds:
                print("Home rating label geometry:", {
                    "fixture": name, "window": (width, height),
                    "chart": chart.size().toTuple(),
                    "chart_rect": chart.rect().getRect(),
                    "plot_rect": chart._plot_rect().getRect(),
                    "x_labels": [(label, rect.getRect()) for label, rect in
                                 zip(chart._x_axis_labels, chart._x_label_bounds)],
                    "y_labels": [(value, rect.getRect()) for value, rect in
                                 chart._y_label_bounds],
                    "out_of_bounds": out_of_bounds,
                }, flush=True)
                capture(window, output, f"home-rating-bounds-debug-{name}",
                        width, height)
                raise AssertionError(
                    f"Home rating labels extend beyond chart bounds at {width}x{height}: "
                    f"{out_of_bounds}")
            assert all(not first.intersects(second)
                       for bounds in (chart._x_label_bounds,
                                      [rect for _value, rect in chart._y_label_bounds])
                       for i, first in enumerate(bounds)
                       for second in bounds[i + 1:])
    finally:
        chart.set_points(original_points)
        QApplication.processEvents()
    return images


def verify_home_play_return(window: MainWindow, account_id: str, output: Path,
                            width: int, height: int) -> QImage:
    """Exercise the real sidebar route Home → Play → Home on the native window."""
    before = [str(game.get("game_id") or "")
              for game in (window._latest_dashboard_snapshot or {}).get("recent_games", [])]
    assert len(before) == 3
    QTest.mouseClick(window._nav_buttons["play"], Qt.MouseButton.LeftButton)
    assert window.screen_stack.currentWidget() is window.play_page
    QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
    wait_home(window, account_id)
    after = [str(game.get("game_id") or "")
             for game in (window._latest_dashboard_snapshot or {}).get("recent_games", [])]
    assert after[:3] == before[:3]
    assert window._home_view.recent_scroll.horizontalScrollBar().maximum() == 0
    cards = [window._recent_games_layout.itemAt(index).widget()
             for index in range(window._recent_games_layout.count())
             if window._recent_games_layout.itemAt(index).widget() is not None
             and window._recent_games_layout.itemAt(index).widget().objectName() == "metricCard"]
    assert len(cards) == 3 and all(card.isVisibleTo(window) for card in cards)
    return capture(window, output, "home-return-from-play", width, height)


def main() -> None:
    # Hosted Windows may use cp1252 for redirected stdout/stderr even though
    # screenshot labels and test output contain Russian UI text.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    output = Path(os.environ.get("CC_VISUAL_OUTPUT", "visual-evidence")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Approved reference screenshots can contain profile names or game data.
    # Keep side-by-side comparisons outside the directory uploaded by CI.
    compare_output = Path(os.environ.get(
        "CC_VISUAL_COMPARE_OUTPUT",
        str(Path(tempfile.gettempdir()) / "chess-coach-visual-pairs" / output.name),
    )).resolve()
    compare_output.mkdir(parents=True, exist_ok=True)
    # Keep diagnostics generated by this one-shot offline capture away from the
    # owner's normal Chess Coach logs. The real profile/database/credential
    # store are never consulted by this synthetic capture.
    isolated_local_app_data = Path(os.environ.get(
        "CC_VISUAL_LOCALAPPDATA", str(output / "isolated-localappdata"))).resolve()
    isolated_local_app_data.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(isolated_local_app_data)
    app = QApplication([])
    assert app.platformName() == "windows", f"Expected native Windows Qt, got {app.platformName()}"
    configured_sizes = os.environ.get("CC_VISUAL_TARGET_SIZES", "").strip()
    target_sizes = TARGET_SIZES
    if configured_sizes:
        parsed = []
        for value in configured_sizes.split(","):
            width_text, separator, height_text = value.strip().lower().partition("x")
            assert separator and width_text.isdigit() and height_text.isdigit(), (
                f"Invalid CC_VISUAL_TARGET_SIZES entry: {value!r}"
            )
            parsed.append((int(width_text), int(height_text)))
        assert parsed, "CC_VISUAL_TARGET_SIZES cannot be empty"
        target_sizes = tuple(parsed)
    reference_dir = Path(__file__).resolve().parent.parent / "tests" / "visual" / "reference"
    for filename, digest in REFERENCE_HASHES.items():
        assert sha256((reference_dir / filename).read_bytes()).hexdigest() == digest

    (window, runtime, account_id, selected_game_id, _runner, active_job,
     _complete_job) = make_window(app)
    release_events = []
    operation_handles = []
    captured: dict[str, QImage] = {}
    geometry: list[str] = []
    try:
        # Production Home worker owns its query. No snapshot is passed directly
        # to the renderer and no production method is overridden.
        wait_home(window, account_id)
        if os.environ.get("CC_VISUAL_SCOPE", "").strip() == "home-chart-review":
            for width, height in target_sizes:
                window.resize(width, height)
                QApplication.processEvents()
                QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
                wait_home(window, account_id)
                capture_home_rating_review(window, output, width, height)
                capture_long_game_graph_cases(window, output, width, height)
                # Repeat the layout path after each size change so the native
                # L2 gate catches card-width state retained across navigation.
                for _repeat in range(2):
                    QTest.mouseClick(window._nav_buttons["play"], Qt.MouseButton.LeftButton)
                    QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
                    wait_home(window, account_id)
            if len(target_sizes) > 1:
                # Exercise compact → wide as well as wide → compact without
                # introducing a timer/sleep into the geometry assertion.
                width, height = target_sizes[0]
                window.resize(width, height)
                QApplication.processEvents()
                QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
                wait_home(window, account_id)
            print(f"Targeted Home chart review captured {len(target_sizes) * 2} native states.")
            return
        if os.environ.get("CC_VISUAL_SCOPE", "").strip() == "review-ux-01":
            window._set_sidebar_collapsed(False)
            navigate_to_games(window, selected_game_id)
            selected = window.library_page.selected_game()
            assert selected and window._open_saved_game(dict(selected))
            wait_review_workspace(window.review_workspace, selected_game_id)
            page = window.review_workspace
            page.training_allowed = True
            page.evaluation_allowed = True
            page.repeat_analysis_button.setVisible(True)
            for width, height in target_sizes:
                page.set_training_safety_result(
                    True, "Синтетическая проверка для снимка.", evaluation_allowed=True)
                window.resize(width, height)
                QApplication.processEvents()
                page.select_ply(0)
                captured[f"review-mainline-{width}x{height}"] = capture(
                    window, output, "review-mainline", width, height, review_selection="keep")
                page.select_ply(1)
                captured[f"review-white-ply-{width}x{height}"] = capture(
                    window, output, "review-white-ply", width, height, review_selection="keep")
                page.select_ply(2)
                captured[f"review-black-ply-{width}x{height}"] = capture(
                    window, output, "review-black-ply", width, height, review_selection="keep")
                fork_ply = min(2, len(page.review["moves"]))
                page.select_ply(fork_ply)
                board = page.review["positions"][fork_ply]
                main_move = (page.review["moves"][fork_ply].uci
                             if fork_ply < len(page.review["moves"]) else None)
                alternate = next((move for move in board.legal_moves if move != main_move), None)
                assert alternate is not None, "Synthetic review position needs an alternate legal move."
                page._review_board_move_intent(alternate)
                branch = page._review_variations[-1]
                page._selected_variation_id = branch["id"]
                page._selected_variation_ply = 1
                branch["request_ids"][1] = f"visual-{width}-{height}"
                branch["pending"][1] = True
                page.select_variation_ply(branch["id"], 1)
                captured[f"review-variation-pending-{width}x{height}"] = capture(
                    window, output, "review-variation-pending", width, height,
                    review_selection="keep-variation")
                page.set_variation_evaluation(
                    branch["id"], 1, branch["request_ids"][1],
                    {"score_white_cp": 34, "depth": 18})
                captured[f"review-variation-evaluated-{width}x{height}"] = capture(
                    window, output, "review-variation-evaluated", width, height,
                    review_selection="keep-variation")
                page.revoke_safety("Fair Play: активная человеческая партия")
                captured[f"review-fair-play-blocked-{width}x{height}"] = capture(
                    window, output, "review-fair-play-blocked", width, height,
                    review_selection="keep")
            (output / "capture.txt").write_text(
                f"SHA: {os.environ.get('CC_VISUAL_SHA', 'local working tree')}\n"
                "Platform: isolated native Windows Qt; synthetic SQLite only.\n"
                "No reference image or personal data was uploaded by this capture job.\n"
                "Frames: mainline, selected white/black ply, pending/evaluated variation, "
                "Fair Play blocked, 1672x941/1280x720 and requested DPI scales.\n",
                encoding="utf-8")
            print(f"REVIEW-UX-01 native Qt capture: PASS ({len(captured)} frames)")
            return
        for target_index, (width, height) in enumerate(target_sizes):
            QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
            wait_home(window, account_id)
            window._set_sidebar_collapsed(False)
            image = capture(window, output, "home", width, height)
            captured[f"home-{width}x{height}"] = image
            scale_factor = float(os.environ.get("QT_SCALE_FACTOR", "1"))
            if width >= 1500 or width <= 1280:
                long_name_image = capture_home_long_opponent_case(
                    window, output, width, height)
                captured[f"home-long-opponent-name-{width}x{height}"] = long_name_image
            if target_index == 0:
                returned_home = verify_home_play_return(
                    window, account_id, output, width, height)
                captured[f"home-return-from-play-{width}x{height}"] = returned_home
            if ((scale_factor == 1.0 and target_index == 0)
                    or (scale_factor == 1.25 and width <= 1280 and height <= 720)):
                capture_home_metric_hover_evidence(window, output, width, height)
            if (width, height) == (target_sizes[0][0], target_sizes[0][1]) or width <= 1280:
                graph_case_image = capture_long_game_graph_cases(
                    window, output, width, height)
                captured[f"home-long-game-graphs-{width}x{height}"] = graph_case_image
            if width <= 1280 and height <= 720:
                scroll = window._home_view.home_overview_scroll.verticalScrollBar()
                assert scroll.maximum() > 0, (
                    "Compact Home must expose vertical scrolling for the full left overview."
                )
                old_scroll_value = scroll.value()
                scroll.setValue(scroll.maximum())
                app.processEvents()
                button_rect = QRect(
                    window.home_activity_button.mapToGlobal(QPoint(0, 0)),
                    window.home_activity_button.size())
                viewport_rect = QRect(
                    window._home_view.home_overview_scroll.viewport().mapToGlobal(QPoint(0, 0)),
                    window._home_view.home_overview_scroll.viewport().size())
                assert viewport_rect.contains(button_rect), (
                    "The next-puzzle CTA must be fully visible after scrolling compact Home."
                )
                captured[f"home-scroll-bottom-{width}x{height}"] = capture(
                    window, output, "home-scroll-bottom", width, height)
                scroll.setValue(old_scroll_value)
                app.processEvents()
                recent_scroll = window._home_view.recent_scroll.verticalScrollBar()
                assert recent_scroll.maximum() > 0, (
                    "Compact Home must expose scrolling through all three recent games."
                )
                old_recent_scroll_value = recent_scroll.value()
                recent_scroll.setValue(recent_scroll.maximum())
                app.processEvents()
                recent_cards = [
                    window._recent_games_layout.itemAt(i).widget()
                    for i in range(window._recent_games_layout.count())
                    if window._recent_games_layout.itemAt(i).widget() is not None
                    and window._recent_games_layout.itemAt(i).widget().objectName() == "metricCard"
                ]
                assert len(recent_cards) == 3
                window._home_view.recent_scroll.ensureWidgetVisible(
                    recent_cards[-1], 0, 0)
                app.processEvents()
                last_card_rect = QRect(
                    recent_cards[-1].mapToGlobal(QPoint(0, 0)),
                    recent_cards[-1].size())
                recent_viewport = window._home_view.recent_scroll.viewport()
                recent_viewport_rect = QRect(
                    recent_viewport.mapToGlobal(QPoint(0, 0)), recent_viewport.size())
                assert recent_viewport_rect.contains(last_card_rect), (
                    "The last recent-game card must be fully accessible in compact Home: "
                    f"card={last_card_rect.getRect()} viewport={recent_viewport_rect.getRect()} "
                    f"card_size={recent_cards[-1].size().width()}x{recent_cards[-1].size().height()} "
                    f"viewport_size={recent_viewport.size().width()}x{recent_viewport.size().height()} "
                    f"scroll={recent_scroll.value()}/{recent_scroll.maximum()}"
                )
                captured[f"home-recent-scroll-bottom-{width}x{height}"] = capture(
                    window, output, "home-recent-scroll-bottom", width, height)
                recent_scroll.setValue(old_recent_scroll_value)
                app.processEvents()
            if width == target_sizes[0][0] and height == target_sizes[0][1]:
                puzzle_id = window._home_view._puzzle_preview["puzzle_id"]
                QTest.mouseClick(window.home_activity_button, Qt.MouseButton.LeftButton)
                wait_for(lambda: window._puzzle_page is not None
                         and window.screen_stack.currentWidget() is window._puzzle_page
                         and window._puzzle_page.findChild(PuzzleHistoryPage) is not None,
                         "exact saved puzzle route from Home", timeout=30)
                puzzle_page = window._puzzle_page.findChild(PuzzleHistoryPage)
                wait_for(lambda: puzzle_page.history.count() > 0
                         and puzzle_page.reset_replay.isEnabled()
                         and puzzle_page.board._interaction_enabled
                         and puzzle_page.solution.text().startswith("Офлайн-упражнение"),
                         "selected Home puzzle in playable local replay", timeout=30)
                selected_attempt = puzzle_page.history.currentItem().data(Qt.ItemDataRole.UserRole)
                assert selected_attempt["puzzle_id"] == puzzle_id, (
                    "Home CTA must open the same saved puzzle ID as the preview."
                )
                captured["home-puzzle-exact-replay"] = capture(
                    window, output, "home-puzzle-exact-replay", width, height)
                window.show_home()
                wait_home(window, account_id)
                QTest.mouseClick(window.sidebar_toggle, Qt.MouseButton.LeftButton)
                assert window.sidebar.width() == 252
                assert all(button.text().endswith(str(button.property("route_label")))
                           for button in window._nav_buttons.values())
                expanded = capture(
                    window, output, "home-sidebar-expanded-after-toggle", width, height)
                captured["home-sidebar-expanded-after-toggle"] = expanded

            navigate_to_games(window, selected_game_id)
            image = capture(window, output, "games", width, height)
            captured[f"games-{width}x{height}"] = image
            make_pair(reference_dir, compare_output, REFERENCE_MAP["games"], image,
                      f"games-{width}x{height}")

        # Active visual state is a local fake operation registry over synthetic
        # durable SQLite state. It never starts a Lichess request or UCI process.
        release_events, operation_handles = create_fake_active_operations(window, account_id)
        window.show_background_analytics()
        wait_background(window, active=True)
        window._show_batch_live_progress(account_id, window._analysis_session_generation, 9, 16)
        for width, height in target_sizes:
            image = capture(window, output, "background-active", width, height)
            captured[f"background-active-{width}x{height}"] = image
            make_pair(reference_dir, compare_output, REFERENCE_MAP["background"], image,
                      f"background-active-{width}x{height}")

        for release in release_events:
            release.set()
        for handle in operation_handles:
            handle.join(2.0)
        runtime.repository.finish_analysis_job(active_job["job_id"], "visual-active", status="paused")
        window._show_batch_live_progress(account_id, window._analysis_session_generation, 0, 0)
        window._refresh_background_operations_page()
        QTest.qWait(120)
        wait_background(window, active=False)
        offline_sizes = tuple(dict.fromkeys((target_sizes[0], target_sizes[-1])))
        for width, height in offline_sizes:
            image = capture(window, output, "background-offline", width, height)
            captured[f"background-offline-{width}x{height}"] = image
            make_pair(reference_dir, compare_output, REFERENCE_MAP["background"], image,
                      f"background-offline-{width}x{height}")

        # One actual native-window frame for each remaining required route.
        # These pages share the production shell and the same synthetic SQLite.
        width, height = target_sizes[0]
        for route in ("play", "analysis"):
            QTest.mouseClick(window._nav_buttons[route], Qt.MouseButton.LeftButton)
            QTest.qWait(80)
            assert window.screen_stack.currentWidget() is (
                window.play_page if route == "play" else window.analysis_page)
            if route == "analysis":
                window.analysis_page.external_text.setPlainText(
                    '[Event "Synthetic visual review"]\n[White "Visual Coach"]\n'
                    '[Black "Synthetic Opponent"]\n[Result "*"]\n\n'
                    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *")
                window.analysis_page.set_external_status(
                    "Синтетическая PGN для проверки формы; архив и сеть не изменяются.")
            if route == "play":
                exercise_play_mode_targets(window)
            image = capture(window, output, route, width, height)
            captured[route] = image
            if route == "play":
                for mode in ("ai", "friend", "challenge"):
                    button = window._play_mode_page.buttons[mode]
                    QCursor.setPos(button.mapToGlobal(button.rect().center()))
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    assert window._play_mode_page.mode_selector.currentData() == mode
                    image = capture(window, output, f"play-mode-{mode}", width, height)
                    captured[f"play-mode-{mode}"] = image
                window._play_mode_page.select_mode("local")
                board = chess.Board()
                board.push_uci("e2e4")
                window._play_mode_page.set_active_games([{
                    "gameId": "synthetic-active-ai", "source": "ai",
                    "opponent": {"id": None, "ai": 3}, "color": "black",
                    "speed": "correspondence", "daysPerTurn": 3,
                    "fen": board.fen(), "isMyTurn": True,
                }], status="Синтетическая подтверждённая партия · без сети")
                window._play_mode_page.active_games.setCurrentRow(0)
                assert window._play_mode_page.active_preview_board._board.fen() == board.fen()
                captured["play-active-games"] = capture(
                    window, output, "play-active-games", width, height)

        QTest.mouseClick(window._nav_buttons["training"], Qt.MouseButton.LeftButton)
        wait_for(lambda: window._puzzle_page is not None
                 and window.screen_stack.currentWidget() is window._puzzle_page
                 and window._puzzle_page.findChild(PuzzleHistoryPage) is not None,
                 "local training history page creation", timeout=30)
        training = window._puzzle_page.findChild(PuzzleHistoryPage)
        wait_for(lambda: training is not None and training.history.count() > 0
                 and not training.summary.text().startswith(
                     ("Загружаем", "Читаю")),
                 "populated local synthetic training history", timeout=30)
        training.history.setCurrentRow(0)
        wait_for(lambda: training.solution.text().startswith("Задача "),
                 "selected synthetic exercise and board position")
        captured["training"] = capture(window, output, "training", width, height)

        QTest.mouseClick(window._nav_buttons["progress"], Qt.MouseButton.LeftButton)
        wait_for(lambda: window.screen_stack.currentWidget() is window._statistics_dialog
                 and not window._statistics_dialog.summary_label.text().startswith(
                     ("Обновляем статистику", "Загружаем статистику")),
                 "local progress summary", timeout=60)
        progress = window._statistics_dialog
        wait_for(lambda: progress.account.findData(account_id) >= 0,
                 "synthetic account in progress filters", timeout=30)
        progress.account.setCurrentIndex(progress.account.findData(account_id))
        try:
            wait_for(lambda: progress._rows
                     and f"Аккаунт: {progress.account.currentText()}" in progress.mixed_warning.text()
                     and progress.summary_label.text().startswith("Партии: 8 ·"),
                "account-scoped progress statistics", timeout=60)
        except AssertionError:
            print("Progress diagnostics:", {
                "account": (progress.account.currentText(), progress.account.currentData(),
                            [(progress.account.itemText(i), progress.account.itemData(i))
                             for i in range(progress.account.count())]),
                "summary": progress.summary_label.text(),
                "scope": progress.mixed_warning.text(),
                "rows": len(progress._rows),
                "games_widget": progress.games.count(),
                "query_alive": bool(progress._query_thread and progress._query_thread.is_alive()),
                "pending_refresh": progress._pending_refresh,
            }, flush=True)
            raise
        wait_for(lambda: bool(progress.trend.points),
                 "synthetic account rating trend in Progress overview", timeout=20)
        progress.tabs.setCurrentIndex(0)
        captured["progress-overview"] = capture(
            window, output, "progress-overview", width, height)
        progress.tabs.setCurrentIndex(1)
        wait_for(lambda: progress.games.count() > 0 or progress.games_empty.isVisible(),
                 "account-scoped progress game table")
        captured["progress-games"] = capture(window, output, "progress-games", width, height)
        progress.tabs.setCurrentIndex(2)
        QTest.mouseClick(progress.opening_refresh_button, Qt.MouseButton.LeftButton)
        wait_for(lambda: progress.opening_table.count() > 0
                 or "нет данных" in progress.opening_summary.text().casefold()
                 or "ошиб" in progress.opening_summary.text().casefold(),
                 "account-scoped local opening/phase analysis", timeout=60)
        captured["progress-openings"] = capture(
            window, output, "progress-openings", width, height)
        progress.tabs.setCurrentIndex(3)
        QTest.qWait(100)
        captured["progress-critical"] = capture(
            window, output, "progress-critical", width, height)

        window.open_settings()
        assert window.screen_stack.currentWidget() is window._settings_dialog
        tabs = window._settings_dialog.settings_tabs
        for tab_index in range(tabs.count()):
            tabs.setCurrentIndex(tab_index)
            QTest.qWait(80)
            tab_name = "-".join(tabs.tabText(tab_index).casefold().replace("/", " ").split())
            captured[f"settings-{tab_index}"] = capture(
                window, output, f"settings-{tab_name}", width, height)

        window.start_local_game()
        assert window.screen_stack.currentWidget() is window.game_page
        for uci in ("e2e4", "e7e5", "g1f3", "b8c6"):
            window.on_move_intent(chess.Move.from_uci(uci))
        assert window.controller.ply == 4
        captured["active-game"] = capture(window, output, "active-game", width, height)

        navigate_to_games(window, selected_game_id)
        selected = window.library_page.selected_game()
        assert selected and str(selected.get("game_id")) == selected_game_id
        assert window._open_saved_game(dict(selected))
        wait_review_workspace(window.review_workspace, selected_game_id)
        review = window.review_workspace
        move_count = len(review.review["moves"])
        first_move = review.moves_list.item_by_ply(1)
        assert first_move is not None and review.moves_list.visualItemRect(first_move).intersects(
            review.moves_list.viewport().rect()), "Review start must show the first SAN row."
        assert review.selected_ply == 0 and review.review_board._board.fen() == \
            review.review["positions"][0].fen()
        captured["game-review-start"] = capture(
            window, output, "game-review-start", width, height, review_selection="keep")
        review.select_ply(move_count)
        QTest.qWait(30)
        selected_san = review.moves_list.currentItem()
        assert review.selected_ply == move_count
        assert selected_san is not None and selected_san.data(Qt.ItemDataRole.UserRole) == move_count
        assert review.moves_list.visualItemRect(selected_san).intersects(
            review.moves_list.viewport().rect()), "Selected final SAN row must be visible."
        assert review.review_board._board.fen() == review.review["positions"][move_count].fen()
        captured["game-review-last"] = capture(
            window, output, "game-review-last", width, height, review_selection="keep")
        review.select_ply(0)
        QTest.qWait(30)
        first_move = review.moves_list.item_by_ply(1)
        assert review.selected_ply == 0 and first_move is not None
        assert review.moves_list.visualItemRect(first_move).intersects(
            review.moves_list.viewport().rect()), "Returning to start must reveal first SAN row."
        assert review.review_board._board.fen() == review.review["positions"][0].fen()
        captured["game-review-start-reset"] = capture(
            window, output, "game-review-start-reset", width, height, review_selection="keep")
        captured["game-review"] = capture(window, output, "game-review", width, height)

        # Settings are captured once per tab at the largest target size.
        compact_size_count = sum(1 for width, height in target_sizes
                                 if width <= 1280 and height <= 720)
        graph_case_count = sum(
            1 for index, (width, _height) in enumerate(target_sizes)
            if index == 0 or width <= 1280
        )
        long_opponent_count = sum(
            1 for width, _height in target_sizes if width >= 1500 or width <= 1280)
        play_return_count = int(bool(target_sizes))
        expected_count = (3 * len(target_sizes) + len(offline_sizes) + 18
                          + 2 * compact_size_count
                          + tabs.count() + graph_case_count
                          + long_opponent_count + play_return_count)
        assert len(captured) == expected_count, (
            f"Expected {expected_count} screenshot states, got {len(captured)}"
        )
    finally:
        for release in release_events:
            release.set()
        # Remove inert fake identity before normal offline shutdown.
        window.lichess = None
        window.close()
        app.processEvents()
        assert runtime._closed or window._shutdown_requested

    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    details = [f"{key}: capture {image.width()}x{image.height()} px; "
               f"DPR {image.devicePixelRatio()}"
               for key, image in captured.items()]
    (output / "capture.txt").write_text(
        f"SHA: {os.environ.get('CC_VISUAL_SHA', 'local working tree')}\n"
        f"Platform: Windows · QT_SCALE_FACTOR={scale}\n"
        "Fixture: isolated temporary SQLite, fake account identity, fake operation registry.\n"
        "Network/API calls: none. Credential store: none. Real Stockfish: none.\n"
        "Library condition: selected game 7, current line revision, Stockfish + Lichess metadata, "
        "17 ply evaluations, official accuracy and a populated graph.\n"
        "Background active: synthetic archive + Stockfish running; ready-analysis waiting; "
        "puzzle sync complete; three-or-more durable event rows.\n"
        "Background offline: no active operation registry entries; queued/paused durable work remains visible.\n"
        "Home KPI hover frames use real QTest pointer movement and native Qt popups; large-value popups are suppressed.\n"
        "Window QSize was asserted equal to each logical target; QImage pixel size was asserted as logical size × DPR.\n"
        f"Logical targets: {target_sizes}\n"
        + "\n".join(details) + "\n",
        encoding="utf-8")
    print(f"VIS6 native visual smoke: PASS ({len(captured)} strict synthetic states; "
          f"logical targets {target_sizes}; actual window and pixel dimensions verified)")


if __name__ == "__main__":
    main()
