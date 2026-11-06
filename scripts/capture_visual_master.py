"""Capture truthful native-Windows screenshots from isolated synthetic SQLite data.

The production MainWindow, dashboard workers, library query/detail workers and
background status reader are used unchanged. No Lichess transport, credential
store, user settings, user database or real engine is opened.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import threading
import time
from hashlib import sha256
from types import SimpleNamespace

import chess
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem

from chess_coach.bootstrap import create_runtime
from chess_coach.application.dashboard import dashboard_snapshot
from chess_coach.application.background_work import OperationLane
from chess_coach.lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from chess_coach.ui.game_review import ReviewGraph
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


def seed_synthetic_repository(repo, settings) -> tuple[str, str]:
    account_id = repo.ensure_archive_account("visual-synthetic")
    settings.setValue("workspace/selected_account", account_id)
    settings.setValue("workspace/sidebar_explicit", False)
    settings.setValue("workspace/sidebar_collapsed", False)
    for index in range(8):
        game_id = str(index)
        winner = "white" if index % 3 else "black"
        repo.save_snapshot(
            game_id, "startpos", MOVE_TEXT, "white",
            ("Synthetic Owl", "Synthetic Fox", "Synthetic Rook")[index % 3],
            False, "180+2", "resign", winner,
            white_player="Visual Coach", black_player=f"Synthetic {index}",
            started_at=f"2026-11-{index + 1:02d}T18:30:00+00:00")
    repo.wait_for_writes()
    with repo._connection() as db:
        db.executemany(
            "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
            "VALUES (?,?,?,?,?,?)",
            [(account_id, str(index), f"synthetic-{index}",
              f"2026-11-{index + 1:02d}T18:30:00+00:00", "white",
              f"Synthetic {index}") for index in range(8)])
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
                 and window._dashboard_metric_values["games"].text() == "8"
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
    recent_cards = [window._home_view._recent_games_layout.itemAt(i).widget()
                    for i in range(window._home_view._recent_games_layout.count())
                    if window._home_view._recent_games_layout.itemAt(i).widget()
                    and window._home_view._recent_games_layout.itemAt(i).widget().objectName() == "metricCard"]
    assert all(card.findChild(ReviewGraph) is not None for card in recent_cards), (
        "All three synthetic Home games require a real rendered evaluation graph."
    )


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


def navigate_to_games(window: MainWindow, selected_game_id: str) -> None:
    QTest.mouseClick(window._nav_buttons["games"], Qt.MouseButton.LeftButton)
    page = window.library_page
    assert page.sort_description.parentWidget() is page
    assert page.sort_description.isHidden() and not page.sort_description.isWindow(), (
        "Sorting help must stay a tooltip, never a floating top-level window."
    )
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
    assert "88.4% · Lichess" in page.detail_metrics.text()
    assert "Stockfish · Есть данные" == page.source_badges["Stockfish"].text()
    assert "Lichess · Есть данные" == page.source_badges["Lichess"].text()


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
        wait_for(lambda: page.summary.text().startswith("Сейчас:")
                 and page.workflow_badges["archive"].text() == "Идёт"
                 and page.workflow_badges["stockfish"].text() == "Идёт"
                 and page.workflow_badges["puzzles"].text() == "Готово"
                 and page.workflow_badges["ready"].text() == "Ожидает"
                 and page.recent_events.count() >= 3,
                 "active archive/Stockfish with waiting ready-analysis, completed puzzles and events")
    else:
        try:
            wait_for(lambda: page.summary.text() == "Сейчас активных задач нет"
                 and not any(operation.status.value in {"queued", "running"}
                             and getattr(operation.context, "kind", "")
                             != "background_status_read"
                             for operation in window.runtime.work_coordinator.snapshots()),
                     "honest offline state with no active background operations")
        except AssertionError:
            print("Background diagnostics:", {
                "summary": page.summary.text(),
                "operations": [(item.context.kind, item.status.value)
                               for item in window.runtime.work_coordinator.snapshots()],
                "badges": {key: value.text() for key, value in page.workflow_badges.items()},
                "statuses": {key: value.text() for key, value in page.workflow_status.items()},
            }, flush=True)
            raise


def capture(window: MainWindow, output: Path, name: str, width: int, height: int) -> QImage:
    window.resize(width, height)
    QTest.qWait(30)
    current = window.screen_stack.currentWidget()
    if current is window.home_page:
        expected_account = window.dashboard_account.currentData()
        if expected_account:
            wait_home(window, str(expected_account))
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
    elif current is window.library_page:
        game = window.library_page._detail_game or {}
        wait_selected_review(window.library_page, str(game.get("game_id") or ""))
    elif current is window._background_operations_page:
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
    assert window.size() == QSize(width, height), (
        f"Requested {width}x{height}, actual Qt window is {window.width()}x{window.height()}"
    )
    assert window.isVisible(), "capture target is not visible"
    image = window.grab().toImage()
    assert not image.isNull()
    dpr = float(image.devicePixelRatio())
    expected_pixels = QSize(round(width * dpr), round(height * dpr))
    assert image.size() == expected_pixels, (
        f"Logical window {width}x{height} at DPR {dpr} should capture as "
        f"{expected_pixels.width()}x{expected_pixels.height()}, got "
        f"{image.width()}x{image.height()}"
    )
    filename = f"{width}x{height}-{name}.png"
    assert image.save(str(output / filename))
    print(f"{filename}: window {window.width()}x{window.height()} logical px, "
          f"capture {image.width()}x{image.height()} px, DPR {image.devicePixelRatio()}")
    return image


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


def main() -> None:
    app = QApplication([])
    assert app.platformName() == "windows", f"Expected native Windows Qt, got {app.platformName()}"
    output = Path(os.environ.get("CC_VISUAL_OUTPUT", "visual-evidence")).resolve()
    output.mkdir(parents=True, exist_ok=True)
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
        for width, height in target_sizes:
            QTest.mouseClick(window._nav_buttons["home"], Qt.MouseButton.LeftButton)
            wait_home(window, account_id)
            window._set_sidebar_collapsed(False)
            image = capture(window, output, "home", width, height)
            captured[f"home-{width}x{height}"] = image
            make_pair(reference_dir, output, REFERENCE_MAP["home"], image, f"home-{width}x{height}")

            navigate_to_games(window, selected_game_id)
            image = capture(window, output, "games", width, height)
            captured[f"games-{width}x{height}"] = image
            make_pair(reference_dir, output, REFERENCE_MAP["games"], image, f"games-{width}x{height}")

        # Active visual state is a local fake operation registry over synthetic
        # durable SQLite state. It never starts a Lichess request or UCI process.
        release_events, operation_handles = create_fake_active_operations(window, account_id)
        window.show_background_analytics()
        wait_background(window, active=True)
        window._show_batch_live_progress(account_id, window._analysis_session_generation, 9, 16)
        for width, height in target_sizes:
            image = capture(window, output, "background-active", width, height)
            captured[f"background-active-{width}x{height}"] = image
            make_pair(reference_dir, output, REFERENCE_MAP["background"], image,
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
            make_pair(reference_dir, output, REFERENCE_MAP["background"], image,
                      f"background-offline-{width}x{height}")

        expected_count = 3 * len(target_sizes) + len(offline_sizes)
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
    details = [f"{key}: window {window_width}x{window_height} logical; "
               f"image {image.width()}x{image.height()} px; DPR {image.devicePixelRatio()}"
               for key, image in captured.items()
               for window_width, window_height in (
                   tuple(map(int, key.rsplit('-', 1)[1].split('x'))),)]
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
        "Window QSize was asserted equal to each logical target; QImage pixel size was asserted as logical size × DPR.\n"
        f"Logical targets: {target_sizes}\n"
        + "\n".join(details) + "\n",
        encoding="utf-8")
    print(f"VIS4 native visual smoke: PASS ({len(captured)} strict synthetic states; "
          f"logical targets {target_sizes}; actual window and pixel dimensions verified)")


if __name__ == "__main__":
    main()
