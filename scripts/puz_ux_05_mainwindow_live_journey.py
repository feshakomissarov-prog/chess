"""Owner-like PUZ-UX-05 journey on an isolated Windows Qt runner.

One real, unauthenticated Lichess PuzzleAndGame GET anchors the API shape. The
subsequent deterministic batch adapter derives GUI fixtures from that public
response; it is explicitly not an authenticated Lichess acceptance test.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen

import chess
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from chess_coach.diagnostics import shutdown_logging
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.theme import VISUAL_TOKENS, stylesheet as desktop_stylesheet
from chess_coach.window import MainWindow


OUTPUT = Path(os.environ.get("CC_PUZ_UX_05_OUTPUT", "visual-evidence/puz-ux-05-l2"))
JOURNEY_TRACE: dict = {}
_ACTIVE_APP = None
_ACTIVE_WINDOW = None
_CLEANUP_DONE = False


def cleanup() -> None:
    """Close the isolated Qt window and repository even after an assertion."""
    global _CLEANUP_DONE
    if _CLEANUP_DONE:
        return
    _CLEANUP_DONE = True
    window = _ACTIVE_WINDOW
    app = _ACTIVE_APP
    if window is not None:
        window.close()
    if app is not None:
        deadline = time.monotonic() + 3
        while (window is not None and not window.runtime._closed
               and time.monotonic() < deadline):
            app.processEvents()
            QTest.qWait(30)
        app.processEvents()
    shutdown_logging()


def persist_journey_trace() -> None:
    """Keep an inspectable partial mouse-action trace if the runner is stopped."""
    if not JOURNEY_TRACE:
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "action-journal.json").write_text(
        json.dumps(JOURNEY_TRACE, ensure_ascii=False, indent=2), encoding="utf-8")


class InertCredentialStore:
    """A sentinel used only by local code; it is never sent over HTTP."""

    def __init__(self, **_kwargs):
        pass

    def get(self):
        return "PUZ05-NOT-A-REAL-OAUTH-CREDENTIAL"

    def set(self, _token):
        raise AssertionError("The L2 journey must not persist OAuth credentials")


class BatchAdapter:
    def __init__(self, payload: dict):
        self.template = payload
        self.serial = 0
        self.current_id = None
        self.fail_next = False
        self.block_next = False
        self.block_started = threading.Event()
        self.release_blocked = threading.Event()
        self.calls: list[dict] = []
        self.username = "puz05-isolated-runner"

    def get_account(self, _token):
        return {"username": self.username}

    def get_puzzle_batch(self, _token, *, angle, difficulty, count):
        if self.block_next:
            self.block_next = False
            self.block_started.set()
            if not self.release_blocked.wait(20):
                raise TimeoutError("PUZ-UX-05 initial request was not released")
        if self.fail_next:
            self.fail_next = False
            self.calls.append({"mode": "controlled-network-error", "count": count})
            raise OSError("PUZ-UX-05 controlled public GET outage")
        if count != 5:
            raise AssertionError(f"Expected batch size 5, got {count}")
        self.serial += 1
        current = self.current_id
        batch = []
        if current is None:
            batch.append(json.loads(json.dumps(self.template)))
            self.current_id = str(self.template["puzzle"]["id"])
        elif current:
            batch.append(self._copy(current))
        batch.append({"puzzle": {"id": f"malformed-{self.serial}", "solution": ["x"]}})
        fresh = []
        while len(batch) < 5:
            self.serial += 1
            puzzle_id = f"puz05-sequence-{self.serial:02d}"
            fresh.append(puzzle_id)
            batch.append(self._copy(puzzle_id))
        self.calls.append({"mode": "deterministic-public-shape-fixture",
                           "angle": angle, "difficulty": difficulty, "count": count,
                           "duplicate_first": current, "malformed_skipped": True,
                           "fresh_ids": fresh, "authenticated_lichess_verified": False})
        return {"puzzles": batch}

    def _copy(self, puzzle_id):
        payload = json.loads(json.dumps(self.template))
        payload["puzzle"]["id"] = puzzle_id
        return payload


class LichessService:
    username = "puz05-isolated-runner"
    puzzle_username = None

    def __init__(self, client):
        self.client = client


def public_get() -> tuple[dict, dict]:
    url = "https://lichess.org/api/puzzle/0haqU"
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "ChessCoach-PUZ-UX-05-public-L2"})
    started = time.perf_counter()
    with urlopen(request, timeout=25) as response:
        status, raw = int(response.status), response.read()
    payload = json.loads(raw.decode("utf-8"))
    puzzle = normalize_api_puzzle(payload)
    if status != 200 or puzzle.puzzle_id != "0haqU":
        raise AssertionError("Public API GET did not return the documented PuzzleAndGame form")
    evidence = {"url": url, "status": status, "puzzle_id": puzzle.puzzle_id,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "authorization_header_sent": False, "authenticated": False,
                "response_top_level_keys": sorted(payload),
                "puzzle_keys": sorted(payload["puzzle"]),
                "game_keys": sorted(payload["game"])}
    print("PUZ05_PUBLIC_GET " + json.dumps(evidence, sort_keys=True))
    return payload, evidence


def wait_until(app, predicate, timeout_ms=15000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(15)
    raise AssertionError("Windows Qt user flow did not settle before timeout")


def capture(widget, name, journal):
    QApplication.processEvents()
    QTest.qWait(80)
    QApplication.processEvents()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{len(journal):02d}-{name}.png"
    if not widget.grab().save(str(path)):
        raise AssertionError(f"Unable to save Qt screenshot: {path}")
    frame = {"frame": path.name, "widget": widget.objectName() or type(widget).__name__,
             "size": [widget.width(), widget.height()]}
    board = getattr(widget, "board", None)
    if board is not None:
        frame["board_size"] = [board.width(), board.height()]
    journal.append(frame)
    print(f"PUZ05_FRAME {path.name}", flush=True)
    persist_journey_trace()


def click_square(page, square, button=Qt.MouseButton.LeftButton):
    QTest.mouseClick(page.board, button, pos=page.board._square_center(square).toPoint())


def play_move(page, raw_move):
    move = chess.Move.from_uci(raw_move)
    print(f"PUZ05_MOVE_BEGIN {page._session_puzzle_id} {raw_move}", flush=True)
    move_action = {"action": "mouse-board-move", "puzzle_id": page._session_puzzle_id,
                   "uci": raw_move, "promotion_choice": "queen" if move.promotion else None,
                   "completed": False}
    if JOURNEY_TRACE:
        JOURNEY_TRACE.setdefault("actions", []).append(move_action)
        persist_journey_trace()
    promotion_choice = []
    if move.promotion:
        app = QApplication.instance()
        deadline = time.monotonic() + 5

        def choose_queen_with_mouse():
            dialog = app.activeModalWidget() if app is not None else None
            if not isinstance(dialog, QDialog) or dialog.windowTitle() != "Превращение пешки":
                if time.monotonic() < deadline:
                    QTimer.singleShot(10, choose_queen_with_mouse)
                else:
                    promotion_choice.append("promotion dialog did not appear")
                return
            queen = next((button for button in dialog.findChildren(QPushButton)
                          if button.accessibleName() == chess.piece_name(chess.QUEEN)), None)
            if queen is None:
                promotion_choice.append("queen choice not found")
                dialog.reject()
                return
            QTest.mouseClick(queen, Qt.MouseButton.LeftButton)
            promotion_choice.append("queen clicked")
            print("PUZ05_PROMOTION_CLICKED queen", flush=True)

        # BoardView opens its real promotion picker inside a nested Qt event loop.
        # This timer then performs the same visible queen-button click as a user.
        QTimer.singleShot(0, choose_queen_with_mouse)
    click_square(page, move.from_square)
    click_square(page, move.to_square)
    if move.promotion and promotion_choice != ["queen clicked"]:
        raise AssertionError(
            f"Promotion picker was not completed by a mouse click: {promotion_choice}")
    print(f"PUZ05_MOVE_CLICKED {page._session_puzzle_id} {raw_move} "
          f"{page.state.status.value}", flush=True)
    if JOURNEY_TRACE:
        move_action["status_after_click"] = page.state.status.value
        move_action["completed"] = True
        persist_journey_trace()


def wheel_board(page, delta):
    point = page.board.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(page.board.mapToGlobal(point)),
                        QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QCoreApplication.sendEvent(page.board, event)


def click_without_modal(app, button):
    observed = []

    def inspect_modal():
        modal = app.activeModalWidget()
        observed.append(bool(modal))
        if modal is not None:
            modal.reject()

    QTimer.singleShot(80, inspect_modal)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: bool(observed), 1000)
    if observed[0]:
        raise AssertionError(f"{button.objectName()} unexpectedly opened a modal dialog")


def click_checkbox(app, checkbox, checked):
    """Toggle a visible checkbox with a Qt mouse event and verify its state."""
    if checkbox.isChecked() != checked:
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton,
                         pos=QPoint(checkbox.width() // 2, checkbox.height() // 2))
    wait_until(app, lambda: checkbox.isChecked() == checked, 1000)


def solve_visible_line(app, page, journal, tag):
    print(f"PUZ05_SOLVE_BEGIN {tag} {page._session_puzzle_id}", flush=True)
    for raw_move in page.state.puzzle.expected_user_moves:
        wait_until(app, lambda: page.state.status.value in {"ready", "mistake_retry"}, 7000)
        play_move(page, raw_move)
        if page.state.status.value == "opponent_reply":
            wait_until(app, lambda: page.state.status.value != "opponent_reply", 5000)
    if page.state.status.value != "success":
        raise AssertionError(f"Mouse solve failed for {tag}: {page.state.status.value}")
    capture(page, f"{tag}-solved", journal)
    print(f"PUZ05_SOLVE_COMPLETE {tag} {page._session_puzzle_id}", flush=True)


def run():
    global _ACTIVE_APP, _ACTIVE_WINDOW
    OUTPUT.mkdir(parents=True, exist_ok=True)
    visual_only = os.environ.get("CC_PUZ_UX_05_VISUAL_ONLY", "0") == "1"
    payload, public_get_evidence = public_get()
    app = QApplication.instance() or QApplication([])
    _ACTIVE_APP = app
    app.setStyleSheet(desktop_stylesheet(VISUAL_TOKENS))
    window = MainWindow(offline_mode=True)
    _ACTIVE_WINDOW = window
    # Offline runtime owns its own temporary QSettings path. Observe the same
    # isolated object the UI writes, rather than an unrelated artifact setting.
    settings = window.settings
    if not window.runtime.offline_data_dir or window.lichess is not None:
        raise AssertionError("The production MainWindow did not initialize isolated storage")
    window.offline_mode = False
    adapter = BatchAdapter(payload)
    window.lichess = LichessService(adapter)
    import chess_coach.window as window_module
    window_module.WindowsCredentialStore = InertCredentialStore
    width, height = (int(part) for part in os.environ.get(
        "CC_PUZ_UX_05_SIZE", "1672x941").lower().split("x", 1))
    window.resize(width, height)
    window.show()
    window.raise_()
    window.activateWindow()
    journal: list[dict] = []
    actions: list[dict] = []
    JOURNEY_TRACE.clear()
    JOURNEY_TRACE.update({"public_get": public_get_evidence,
                          "authenticated_lichess_verified": False,
                          "l2_transport": "deterministic GUI batch fixtures derived from one live public response",
                          "batch_calls": adapter.calls, "actions": actions,
                          "frames": journal, "owner_database_or_oauth_touched": False})

    # First entry starts from the actual Home button.
    capture(window, "home-first-entry", journal)
    adapter.block_next = True
    QTest.mouseClick(window.home_activity_button, Qt.MouseButton.LeftButton)
    page = window._puzzle_training_page
    wait_until(app, adapter.block_started.is_set, 15000)
    if not page.board.isHidden() or not page.board_placeholder.isVisible():
        raise AssertionError("First-entry loading exposed a fake starting board")
    capture(page, "new-task-loading-placeholder", journal)
    adapter.release_blocked.set()
    wait_until(app, lambda: page is not None and page.state.puzzle is not None)
    if page.state.puzzle.puzzle_id != "0haqU":
        raise AssertionError("Home NEW action did not load the public API puzzle")
    adapter.current_id = "0haqU"
    actions.append({"action": "mouse-click", "target": "Home / Решать новые задачи",
                    "loaded_id": page.state.puzzle.puzzle_id})
    wait_until(app, lambda: page._prepared_online_puzzle is not None, 30000)
    capture(page, "first-new-puzzle", journal)
    if visual_only:
        trace = {"public_get": public_get_evidence,
                 "authenticated_lichess_verified": False,
                 "l2_transport": "deterministic GUI fixture derived from one live public response",
                 "actions": [{"action": "mouse-click",
                             "target": "Home / Решать новые задачи",
                             "loaded_id": page.state.puzzle.puzzle_id}],
                 "frames": journal, "visual_only_compact_scale": True,
                 "owner_database_or_oauth_touched": False}
        (OUTPUT / "action-journal.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        cleanup()
        return

    # Exercise right-button annotation and wheel review/return with Qt events.
    pos = page.board._square_center(chess.E2).toPoint()
    QTest.mouseClick(page.board, Qt.MouseButton.RightButton, pos=pos)
    pieces = list(page.state.board.piece_map())
    start = pieces[0]
    end = next((sq for sq in pieces if sq != start), start)
    QTest.mousePress(page.board, Qt.MouseButton.RightButton,
                     pos=page.board._square_center(start).toPoint())
    QTest.mouseMove(page.board, page.board._square_center(end).toPoint(), 60)
    QTest.mouseRelease(page.board, Qt.MouseButton.RightButton,
                       pos=page.board._square_center(end).toPoint())
    if not page.board._annotation_arrows or chess.E2 not in page.board._annotation_squares:
        raise AssertionError("Real right-button clicks did not draw a square and arrow")
    wheel_board(page, 120)
    if page.state.is_reviewing:
        QTest.mouseClick(page.return_to_solution, Qt.MouseButton.LeftButton)
    capture(page, "mouse-right-wheel-return", journal)
    actions.append({"action": "right-click-drag-and-wheel", "review_return_button": True})
    play_move(page, page.state.puzzle.expected_user_moves[0])
    if page.state.status.value == "opponent_reply":
        wait_until(app, lambda: page.state.status.value != "opponent_reply", 5000)
    if page.board._annotation_arrows or page.board._annotation_squares:
        raise AssertionError("A confirmed left-button move did not clear the user marks")

    # A visible hint/reveal is immediate and non-modal; then repeat is a real click.
    click_without_modal(app, page.hint)
    if page._session_results[-1].get("mark") != "•":
        raise AssertionError("Hint did not immediately create a yellow result")
    click_without_modal(app, page.hint)
    if page.state.hint_level != 2 or not page.board._hint_arrows:
        raise AssertionError("Second hint did not immediately reveal an arrow")
    capture(page, "hint-immediate-result", journal)
    click_without_modal(app, page.reveal)
    if page.state.status.value != "revealed":
        raise AssertionError("Reveal button did not finish the task")
    for _ in range(page.state.review_count + 1):
        wheel_board(page, 120)
    if not page.state.is_reviewing:
        raise AssertionError("Mouse wheel did not enter review after showing the solution")
    for _ in range(page.state.review_count + 1):
        wheel_board(page, -120)
    if page.state.review_cursor != page.state.review_count - 1:
        raise AssertionError("Mouse wheel did not return to the latest shown move")
    QTest.mouseClick(page.return_to_solution, Qt.MouseButton.LeftButton)
    if page.state.is_reviewing:
        raise AssertionError("Return-to-solution button left the revealed line in review mode")
    actions.append({"action": "mouse-wheel-and-click",
                    "target": "solution review / Вернуться к решению",
                    "visible_moves_only": True})
    QTest.mouseClick(page.repeat, Qt.MouseButton.LeftButton)
    if page._session_puzzle_id != "0haqU" or not page._is_replay:
        raise AssertionError("Repeat button did not replay the exact current puzzle")
    actions.append({"action": "hint-reveal-repeat-buttons", "result": "passed"})

    # Home exposes distinct NEW and saved REPLAY paths. Seed only the isolated
    # runner database and trigger each actual visible button.
    QTest.mouseClick(page.home_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    account = window.game_repository.ensure_archive_account(adapter.username)
    serialized = json.dumps(payload, ensure_ascii=False)
    window.game_repository.cache_training_puzzle(account, "0haqU", serialized)
    window.game_repository.save_local_puzzle_attempt(
        account, "0haqU", result="success", solved=True, mistakes=0, hints=0,
        duration_ms=10, puzzle_json=serialized, attempt_id="puz05-home-replay-seed",
        rating_eligible=False)
    window._home_view.set_puzzle_preview({"account_id": account, "puzzle_id": "0haqU",
                                         "fen": page.state.puzzle.player_to_move_fen,
                                         "solver_is_white": (
                                             chess.Board(page.state.puzzle.player_to_move_fen).turn
                                             == chess.WHITE),
                                         "rating": page.state.puzzle.rating,
                                         "themes": list(page.state.puzzle.themes)})
    wait_until(app, lambda: window.home_replay_button.isVisible()
               and window.home_replay_button.isEnabled())
    capture(window, "home-saved-puzzle-replay-entry", journal)
    QTest.mouseClick(window.home_replay_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window._puzzle_training_page.state.puzzle is not None
               and window._puzzle_training_page._is_replay)
    replay_page = window._puzzle_training_page
    capture(replay_page, "home-saved-replay", journal)
    before = window.game_repository.local_puzzle_attempt_count(account)
    if replay_page._session_puzzle_id != "0haqU" or not replay_page._is_replay:
        raise AssertionError("Home replay did not load the exact saved task as unrated")
    if window.game_repository.local_puzzle_attempt_count(account) != before:
        raise AssertionError("Saved replay altered local attempt totals")
    actions.append({"action": "mouse-click", "target": "Home / Повторить эту",
                    "replay_unrated": True})

    QTest.mouseClick(replay_page.home_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    QTest.mouseClick(window.home_activity_button, Qt.MouseButton.LeftButton)
    page = window._puzzle_training_page
    wait_until(app, lambda: page.state.puzzle is not None and not page._is_replay)
    adapter.current_id = page.state.puzzle.puzzle_id

    clean_one = page._session_puzzle_id
    solve_visible_line(app, page, journal, "streak-clean-one")
    if page._session_streak != 1:
        raise AssertionError("First clean task did not advance the visible streak")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != clean_one, 20000)
    clean_two = page._session_puzzle_id
    solve_visible_line(app, page, journal, "streak-clean-two")
    if page._session_streak != 2:
        raise AssertionError("Second clean task did not continue the visible streak")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != clean_two, 20000)
    actions.append({"action": "mouse-click", "target": "Следующая задача после второй серии",
                    "loaded_id": page._session_puzzle_id})

    # A wrong answer remains red after retry, and the task still solves by mouse.
    expected = chess.Move.from_uci(page.state.puzzle.expected_user_moves[0])
    wrong = next(move for move in page.state.board.legal_moves if move != expected)
    play_move(page, wrong.uci())
    wait_until(app, lambda: page.state.status.value == "mistake_retry", 3000)
    # _wrong_timer_pending clears as soon as the first red frame is painted;
    # the 420 ms feedback timer still owns the board until _wrong_flash clears.
    wait_until(app, lambda: not page._wrong_flash and not page._wrong_timer.isActive(),
               3000)
    if page._result_tiles[-1].text() != "✕" or page._result_tiles[-1].isEnabled():
        raise AssertionError("First error was not immediately red and locked")
    wrong_id = page._session_puzzle_id
    solve_visible_line(app, page, journal, "wrong-then-correct")
    tile = next(row for row in page._session_results if row["puzzle_id"] == wrong_id)
    if tile.get("mark") != "✕" or not tile.get("completed"):
        raise AssertionError("Wrong-then-correct lost its red completed result")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != wrong_id, 20000)

    # Ten unique batch responses via visible NEXT. Each response contains a
    # duplicate, malformed row and fresh PuzzleAndGame-shaped rows.
    if page.auto_next.isChecked():
        QTest.mouseClick(page.auto_next, Qt.MouseButton.LeftButton)
    seen = {wrong_id, page._session_puzzle_id}
    current_page = page
    while len(seen) < 10:
        if len(seen) == 3:
            revealed_id = current_page._session_puzzle_id
            click_without_modal(app, current_page.reveal)
            if current_page.state.status.value != "revealed":
                raise AssertionError("Reveal did not complete on a single click")
            revealed_row = next(row for row in current_page._session_results
                                if row["puzzle_id"] == revealed_id)
            if revealed_row.get("mark") != "–":
                raise AssertionError("Reveal without a prior error should be neutral")
            capture(current_page, "reveal-neutral-result", journal)
        else:
            solve_visible_line(app, current_page, journal, f"sequence-{len(seen):02d}")
        current = current_page._session_puzzle_id
        before_ids = {row["puzzle_id"] for row in current_page._session_results}
        QTest.mouseClick(current_page.fetch, Qt.MouseButton.LeftButton)
        wait_until(app, lambda: current_page.state.puzzle is not None
                   and current_page._session_puzzle_id != current, 20000)
        selected = current_page._session_puzzle_id
        if selected in seen or selected not in {row for call in adapter.calls
                                                 for row in call.get("fresh_ids", [])}:
            raise AssertionError(f"NEXT repeated or selected an invalid ID: {selected}")
        seen.add(selected)
        if len(seen) > 10:
            break
        actions.append({"action": "mouse-click", "target": "Следующая задача",
                        "loaded_id": selected, "prior_result_tiles": len(before_ids)})
    if len(seen) != 10:
        raise AssertionError(f"Expected ten unique tasks, observed {len(seen)}")
    capture(page, "ten-unique-sequential-tasks", journal)

    # Exercise enabled auto-advance and turning it off before completion.
    click_checkbox(app, page.auto_next, True)
    settings.sync()
    auto_setting_key = f"puzzles/auto_next/{page.account_id}"
    if not settings.value(auto_setting_key, False, type=bool):
        raise AssertionError("A real mouse click did not persist auto-next for this profile")
    capture(page, "auto-next-checked", journal)
    auto_before = page._session_puzzle_id
    solve_visible_line(app, page, journal, "auto-next-clean")
    wait_until(app, lambda: page.state.puzzle.puzzle_id != auto_before, 7000)
    auto_id = page._session_puzzle_id
    solve_visible_line(app, page, journal, "auto-next-disable-after-success")
    click_checkbox(app, page.auto_next, False)
    settings.sync()
    if settings.value(auto_setting_key, True, type=bool):
        raise AssertionError("Turning auto-next off did not persist for this profile")
    capture(page, "auto-next-unchecked-before-timeout", journal)
    QTest.qWait(1700)
    if page._session_puzzle_id != auto_id:
        raise AssertionError("Turning auto-next off after clean success did not stop its timer")
    click_checkbox(app, page.auto_next, True)
    QTest.qWait(1700)
    if page._session_puzzle_id != auto_id:
        raise AssertionError("Turning auto-next on after completion started a new timer")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != auto_id, 20000)
    solve_visible_line(app, page, journal, "auto-next-manual-recovery")

    # Network failure keeps the current result and makes the retry button usable.
    adapter.fail_next = True
    page._prepared_online_puzzle = None
    error_id = page._session_puzzle_id
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: "PUZ-UX-05 controlled public GET outage" in
               page.network_status.text(), 15000)
    if page._session_puzzle_id != error_id or page.state.status.value not in {
            "success", "revealed", "mistake_retry"}:
        raise AssertionError("A network failure replaced the current result")
    capture(page, "network-error-keeps-result", journal)
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != error_id, 20000)
    capture(page, "network-recovered", journal)
    actions.append({"action": "network-error-and-retry", "recovered": True})
    solve_visible_line(app, page, journal, "recovered-task")
    wait_until(app, lambda: "Сохранено в SQLite" in page.persistence_status.text(), 15000)

    trace = {"public_get": public_get_evidence,
             "authenticated_lichess_verified": False,
             "l2_transport": "deterministic GUI batch fixtures derived from one live public response",
             "batch_calls": adapter.calls, "unique_ids_in_ten_task_run": sorted(seen),
             "actions": actions, "frames": journal,
             "owner_database_or_oauth_touched": False}
    (OUTPUT / "action-journal.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    # GitHub Actions' Windows console may use cp1252 even when the journal is
    # UTF-8; keep its one-line console copy ASCII while the artifact stays UTF-8.
    print("PUZ05_ACTION_JOURNAL " + json.dumps(trace, ensure_ascii=True, sort_keys=True))
    cleanup()


if __name__ == "__main__":
    try:
        run()
    finally:
        cleanup()
