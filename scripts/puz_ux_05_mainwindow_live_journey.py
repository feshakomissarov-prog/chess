"""Owner-like PUZ-UX-05/06 journey on an isolated Windows Qt runner.

Genuine unauthenticated Lichess PuzzleAndGame GETs supply distinct task FENs
and lines. Rated submission uses only the isolated synthetic adapter; this is
explicitly not an authenticated Lichess acceptance test.
"""

from __future__ import annotations

from datetime import datetime, timezone
import faulthandler
import hashlib
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
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from chess_coach.diagnostics import shutdown_logging
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.theme import VISUAL_TOKENS, stylesheet as desktop_stylesheet
from chess_coach.window import MainWindow


OUTPUT = Path(os.environ.get(
    "CC_PUZ_UX_06_OUTPUT", os.environ.get(
        "CC_PUZ_UX_05_OUTPUT", "visual-evidence/puz-ux-06-l2")))
JOURNEY_TRACE: dict = {}
_ACTIVE_APP = None
_ACTIVE_WINDOW = None
_CLEANUP_DONE = False
MATE_IN_2_PUBLIC_IDS = [
    "TCKXn", "n7Jiz", "kI1Ql", "IwzsF", "DKVLc",
    "GNNig", "CJTjO", "2rGbA", "TPUJL", "e4jaa",
]
MATE_IN_1_PUBLIC_IDS = [
    "BOTyj", "4g6Bs", "PuCzf", "ugoI2", "9FTAb",
    "lq8m1", "h999q", "MdQwv", "tm6v1", "ehjSy",
]


def cleanup() -> None:
    """Close the isolated Qt window and repository even after an assertion."""
    global _CLEANUP_DONE
    if _CLEANUP_DONE:
        return
    _CLEANUP_DONE = True
    faulthandler.cancel_dump_traceback_later()
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

    def __init__(self, *, service=None, user=None):
        if service != "ChessCoach.Lichess.Puzzles":
            raise AssertionError("Puzzle journey tried to access a non-puzzle credential")
        self.user = user

    def get(self):
        return ("PUZ06-SYNTHETIC-RATED-CREDENTIAL" if self.user == "puzzle-rated-token"
                else "PUZ06-SYNTHETIC-READ-CREDENTIAL")

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
        self.real_sequence: list[dict] = []
        self.require_real_sequence = False
        self.rated_posts: list[dict] = []
        self.block_rated = False
        self.rated_started = threading.Event()
        self.release_rated = threading.Event()
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
        if count < 1 or count > 5:
            raise AssertionError(f"Unexpected bounded batch size: {count}")
        if self.real_sequence:
            batch = self.real_sequence[:count]
            del self.real_sequence[:len(batch)]
            fresh = [str(row["puzzle"]["id"]) for row in batch]
            fingerprints = [hashlib.sha256(
                (str(row["puzzle"].get("fen") or "") + "\0" +
                 " ".join(row["puzzle"].get("solution") or [])).encode("utf-8")
            ).hexdigest() for row in batch]
            self.calls.append({"mode": "public-puzzle-fixture-batch", "angle": angle,
                               "difficulty": difficulty, "count": count,
                               "fresh_ids": fresh, "position_solution_sha256": fingerprints,
                               "authenticated": False, "rating_post": False})
            return {"puzzles": batch}
        if self.require_real_sequence:
            self.calls.append({"mode": "controlled-public-sequence-exhausted",
                               "angle": angle, "difficulty": difficulty,
                               "count": count, "authenticated": False})
            raise LookupError("No unused public puzzle remains in this isolated L2 sequence")
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

    def set_real_sequence(self, payloads: list[dict]) -> None:
        self.require_real_sequence = True
        self.real_sequence = [json.loads(json.dumps(row)) for row in payloads]

    def submit_puzzle_round(self, _token, angle, puzzle_id, win, *, rated, next_count):
        if rated is not True or next_count != 1:
            raise AssertionError("Rated L2 must model exactly one explicit rated round")
        self.rated_posts.append({"angle": angle, "puzzle_id": puzzle_id,
                                 "win": bool(win), "rated": rated,
                                 "next_count": next_count,
                                 "transport": "synthetic-fake-response; no HTTP POST"})
        if self.block_rated:
            self.rated_started.set()
            if not self.release_rated.wait(15):
                raise TimeoutError("Synthetic rated ACK was not released by the GUI journey")
        return {"rounds": [{"id": puzzle_id, "win": bool(win), "ratingDiff": 7}],
                "glicko": {"rating": 1807, "deviation": 55, "provisional": False},
                "puzzles": []}

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


def public_puzzle_sequence(puzzle_ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Fetch genuine public PuzzleAndGame payloads; never send Authorization."""
    payloads: list[dict] = []
    evidence: list[dict] = []
    seen_positions: set[str] = set()
    for puzzle_id in puzzle_ids:
        url = f"https://lichess.org/api/puzzle/{puzzle_id}"
        print(f"PUZ06_PUBLIC_GET_BEGIN {puzzle_id}", flush=True)
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "ChessCoach-PUZ-UX-06-public-L2",
        })
        with urlopen(request, timeout=25) as response:
            status, raw = int(response.status), response.read()
        payload = json.loads(raw.decode("utf-8"))
        puzzle = normalize_api_puzzle(payload)
        if status != 200 or puzzle.puzzle_id != puzzle_id:
            raise AssertionError(f"Public puzzle GET did not return {puzzle_id}")
        fen = str(payload["puzzle"].get("fen") or puzzle.player_to_move_fen)
        line = tuple(str(move) for move in payload["puzzle"].get("solution") or [])
        fingerprint = hashlib.sha256((fen + "\0" + " ".join(line)).encode("utf-8")).hexdigest()
        if fingerprint in seen_positions:
            raise AssertionError(f"Public puzzle {puzzle_id} repeated a prior FEN/line")
        seen_positions.add(fingerprint)
        print(f"PUZ06_PUBLIC_GET_OK {puzzle_id} HTTP {status}", flush=True)
        payloads.append(payload)
        evidence.append({"id": puzzle_id, "status": status,
                         "position_solution_sha256": fingerprint,
                         "theme_ids": list(payload["puzzle"].get("themes") or []),
                         "solution_plies": len(line), "authorization_header_sent": False})
        # Stay below Lichess' shared API rate budget while checking unique IDs.
        time.sleep(4.0)
    return payloads, evidence


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
    print(f"PUZ06_CLICK_BEGIN {button.objectName()}", flush=True)
    observed = []

    def inspect_modal():
        modal = app.activeModalWidget()
        observed.append(bool(modal))
        if modal is not None:
            modal.reject()

    QTimer.singleShot(80, inspect_modal)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    print(f"PUZ06_CLICK_SENT {button.objectName()}", flush=True)
    wait_until(app, lambda: bool(observed), 1000)
    if observed[0]:
        raise AssertionError(f"{button.objectName()} unexpectedly opened a modal dialog")
    print(f"PUZ06_CLICK_COMPLETE {button.objectName()}", flush=True)


def click_checkbox(app, checkbox, checked):
    """Toggle a visible checkbox with a Qt mouse event and verify its state."""
    if checkbox.isChecked() != checked:
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton,
                         pos=QPoint(checkbox.width() // 2, checkbox.height() // 2))
    wait_until(app, lambda: checkbox.isChecked() == checked, 1000)


def choose_combo_item_with_mouse(app, combo, data):
    index = combo.findData(data)
    if index < 0:
        raise AssertionError(f"Visible theme option not found: {data}")
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    rect = view.visualRect(combo.model().index(index, 0))
    if not rect.isValid() or rect.isNull():
        raise AssertionError(f"Theme option is not visible in the popup: {data}")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    wait_until(app, lambda: combo.currentData() == data, 1500)


def choose_theme_with_mouse(app, page, data, search_text):
    """Search and select a theme through the visible full-size theme list."""
    theme_list = page.theme_list
    if theme_list.isVisible():
        search = page.theme_search
        print(f"PUZ06_THEME_SEARCH_BEGIN {data}", flush=True)
        QTest.mouseClick(search, Qt.MouseButton.LeftButton)
        QTest.keyClick(search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(search, search_text)
        item = next((theme_list.item(index) for index in range(theme_list.count())
                     if theme_list.item(index).data(Qt.ItemDataRole.UserRole) == data), None)
        if item is None:
            raise AssertionError(f"Visible theme item not found for {data}")

        def item_is_visible():
            rect = theme_list.visualItemRect(item)
            return (not item.isHidden() and rect.isValid()
                    and theme_list.viewport().rect().contains(rect.center()))

        wait_until(app, item_is_visible, 2000)
        rect = theme_list.visualItemRect(item)
        print(f"PUZ06_THEME_ITEM_CLICK_BEGIN {data}", flush=True)
        QTest.mouseClick(theme_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=rect.center())
        wait_until(app, lambda: page.selected_theme() == data, 2000)
        print(f"PUZ06_THEME_CLICKED {data} via visible theme list", flush=True)
        return
    choose_combo_item_with_mouse(app, page.theme, data)


def click_dialog_button_with_mouse(app, title, text, trigger):
    deadline = time.monotonic() + 8
    clicked: list[bool] = []

    def locate_and_click():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QDialog) and dialog.windowTitle() == title:
            button = None
            if isinstance(dialog, QMessageBox) and text in {"Да", "Yes"}:
                button = dialog.button(QMessageBox.StandardButton.Yes)
            if button is None:
                button = next((item for item in dialog.findChildren(QPushButton)
                               if item.text() == text), None)
            if button is not None:
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                clicked.append(True)
                return
        if time.monotonic() < deadline:
            QTimer.singleShot(20, locate_and_click)

    QTimer.singleShot(0, locate_and_click)
    trigger()
    wait_until(app, lambda: bool(clicked), 9000)


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
    # The live MainWindow walk has a bounded job timeout; emit a periodic stack
    # only while this one diagnostic scenario is active so a Qt modal/block is
    # located promptly instead of waiting for the runner cancellation dump.
    faulthandler.dump_traceback_later(90, repeat=True)
    visual_only = os.environ.get(
        "CC_PUZ_UX_06_VISUAL_ONLY", os.environ.get("CC_PUZ_UX_05_VISUAL_ONLY", "0")) == "1"
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
        "CC_PUZ_UX_06_SIZE", os.environ.get("CC_PUZ_UX_05_SIZE", "1672x941")
    ).lower().split("x", 1))
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
    actions.append({"action": "mouse-click", "target": "Home / Тренировка",
                    "loaded_id": page.state.puzzle.puzzle_id})
    wait_until(app, lambda: page._prepared_online_puzzle is not None, 30000)
    capture(page, "first-new-puzzle", journal)
    if visual_only:
        trace = {"public_get": public_get_evidence,
                 "authenticated_lichess_verified": False,
                 "l2_transport": "deterministic GUI fixture derived from one live public response",
                 "actions": [{"action": "mouse-click",
                  "target": "Home / Тренировка",
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
    print("PUZ06_REVEAL_BEGIN", flush=True)
    click_without_modal(app, page.reveal)
    print("PUZ06_REVEAL_COMPLETE", flush=True)
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
    print("PUZ06_REVIEW_RETURN_CLICKED", flush=True)
    if page.state.is_reviewing:
        raise AssertionError("Return-to-solution button left the revealed line in review mode")
    actions.append({"action": "mouse-wheel-and-click",
                    "target": "solution review / Вернуться к решению",
                    "visible_moves_only": True})
    QTest.mouseClick(page.repeat, Qt.MouseButton.LeftButton)
    print("PUZ06_REPEAT_CLICKED", flush=True)
    if page._session_puzzle_id != "0haqU" or not page._is_replay:
        raise AssertionError("Repeat button did not replay the exact current puzzle")
    actions.append({"action": "hint-reveal-repeat-buttons", "result": "passed"})

    # Home exposes distinct NEW and saved REPLAY paths. Seed only the isolated
    # runner database and trigger each actual visible button.
    click_dialog_button_with_mouse(
        app, "Оставить текущую задачу?", "Да",
        lambda: QTest.mouseClick(page.home_button, Qt.MouseButton.LeftButton))
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

    click_dialog_button_with_mouse(
        app, "Оставить текущую задачу?", "Да",
        lambda: QTest.mouseClick(replay_page.home_button, Qt.MouseButton.LeftButton))
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    QTest.mouseClick(window.home_activity_button, Qt.MouseButton.LeftButton)
    page = window._puzzle_training_page
    wait_until(app, lambda: page.state.puzzle is not None and not page._is_replay)
    adapter.current_id = page.state.puzzle.puzzle_id
    solve_visible_line(app, page, journal, "training-batch-preface")
    wait_until(app, lambda: "Сохранено в SQLite" in page.persistence_status.text(), 15000)
    prefetch_handle = getattr(window, "_puzzle_prefetch_handle", None)
    if prefetch_handle is not None:
        wait_until(app, lambda: not prefetch_handle.is_alive(), 20000)
    mate_in_2_payloads, mate_in_2_evidence = public_puzzle_sequence(MATE_IN_2_PUBLIC_IDS)
    if len({row["puzzle"]["id"] for row in mate_in_2_payloads}) != 10:
        raise AssertionError("Public L2 sequence does not contain ten distinct Lichess IDs")
    if len({item["position_solution_sha256"] for item in mate_in_2_evidence}) != 10:
        raise AssertionError("Ten public puzzle responses did not contain ten distinct positions")
    choose_theme_with_mouse(app, page, "mateIn2", "Мат в 2")
    adapter.set_real_sequence(mate_in_2_payloads)
    page.clear_prepared_puzzles()
    previous = page._session_puzzle_id
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != previous, 20000)
    actual_start_id = page._session_puzzle_id
    if actual_start_id != MATE_IN_2_PUBLIC_IDS[0]:
        raise AssertionError("Theme selection did not start the first genuine mate-in-2 puzzle")
    capture(page, "real-public-mate-in-2-sequence-start", journal)
    actions.append({"action": "mouse-select-theme", "theme": "mateIn2",
                    "first_public_id": actual_start_id,
                    "genuine_distinct_public_puzzles": 10})
    clean_one = page._session_puzzle_id
    solve_visible_line(app, page, journal, "streak-clean-one")
    seen = {clean_one}
    if page._session_streak != 1:
        raise AssertionError("First clean task did not advance the visible streak")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != clean_one, 20000)
    clean_two = page._session_puzzle_id
    seen.add(clean_two)
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
    seen.add(wrong_id)
    solve_visible_line(app, page, journal, "wrong-then-correct")
    tile = next(row for row in page._session_results if row["puzzle_id"] == wrong_id)
    if tile.get("mark") != "✕" or not tile.get("completed"):
        raise AssertionError("Wrong-then-correct lost its red completed result")
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != wrong_id, 20000)
    seen.add(page._session_puzzle_id)

    # Ten separate public Lichess IDs, each with its own fetched PuzzleAndGame
    # FEN and solution, are used through visible NEXT actions.
    if page.auto_next.isChecked():
        QTest.mouseClick(page.auto_next, Qt.MouseButton.LeftButton)
    current_page = page
    while len(seen) < 10:
        if len(seen) == 5:
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
    if not seen.issubset(set(MATE_IN_2_PUBLIC_IDS)):
        raise AssertionError(f"The ten-task sequence included a non-public or synthetic ID: {seen}")
    capture(page, "ten-unique-sequential-tasks", journal)
    solve_visible_line(app, page, journal, "complete-tenth-public-task")
    wait_until(app, lambda: "Сохранено в SQLite" in page.persistence_status.text(), 15000)
    prefetch_handle = getattr(window, "_puzzle_prefetch_handle", None)
    if prefetch_handle is not None:
        wait_until(app, lambda: not prefetch_handle.is_alive(), 20000)
    mate_in_1_payloads, mate_in_1_evidence = public_puzzle_sequence(MATE_IN_1_PUBLIC_IDS)
    choose_theme_with_mouse(app, page, "mateIn1", "Мат в 1")
    adapter.set_real_sequence(mate_in_1_payloads)
    page.clear_prepared_puzzles()
    previous = page._session_puzzle_id
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: page._session_puzzle_id != previous, 20000)
    if page._session_puzzle_id != MATE_IN_1_PUBLIC_IDS[0]:
        raise AssertionError("Theme switch did not load a genuine public mate-in-1 task")
    capture(page, "public-mate-in-1-autonext-ready", journal)

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
    # Stabilize the screen for the explicit network recovery actions below.
    click_checkbox(app, page.auto_next, False)

    # Network failure keeps the current result and makes the retry button usable.
    prefetch_handle = getattr(window, "_puzzle_prefetch_handle", None)
    if prefetch_handle is not None:
        wait_until(app, lambda: not prefetch_handle.is_alive(), 20000)
    page.clear_prepared_puzzles()
    adapter.fail_next = True
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

    # Keep auto-next off while switching profiles/screens, then enter the
    # distinct rated mode through Home's visible CTA and the consent dialog.
    click_checkbox(app, page.auto_next, False)
    rated_payloads, rated_evidence = public_puzzle_sequence(
        ["lq8m1", "h999q", "MdQwv"])
    consent_cancel_payloads, consent_cancel_evidence = public_puzzle_sequence(["9FTAb"])
    wait_until(app, lambda: not getattr(window, "_puzzle_prefetch_handle", None)
               or not window._puzzle_prefetch_handle.is_alive(), 20000)
    page.clear_prepared_puzzles()
    QTest.mouseClick(page.home_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    capture(window, "home-dual-mode-actions", journal)
    adapter.set_real_sequence(consent_cancel_payloads)
    click_dialog_button_with_mouse(
        app, "Рейтинг задач Lichess", "Отмена",
        lambda: QTest.mouseClick(window.home_rated_button, Qt.MouseButton.LeftButton))
    page = window._puzzle_training_page
    wait_until(app, lambda: page.mode == "TRAINING" and page.attempt_mode == "TRAINING"
               and page._session_puzzle_id == "9FTAb", 20000)
    if adapter.rated_posts:
        raise AssertionError("Canceling rated consent made a rated request")
    capture(page, "rated-consent-cancel-keeps-training", journal)
    actions.append({"action": "mouse-cancel-rated-consent",
                    "fallback_mode": page.mode, "rated_posts": len(adapter.rated_posts),
                    "public_id": page._session_puzzle_id})
    solve_visible_line(app, page, journal, "consent-cancel-training-still-works")
    wait_until(app, lambda: "Сохранено в SQLite" in page.persistence_status.text(), 15000)
    QTest.mouseClick(page.home_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    adapter.set_real_sequence(rated_payloads[:1])
    click_dialog_button_with_mouse(
        app, "Рейтинг задач Lichess", "Разрешить на Lichess",
        lambda: QTest.mouseClick(window.home_rated_button, Qt.MouseButton.LeftButton))
    page = window._puzzle_training_page
    wait_until(app, lambda: page.attempt_mode == "LICHESS_RATED"
               and page.state.puzzle is not None, 20000)
    if page._session_puzzle_id != "lq8m1":
        raise AssertionError("Home rated CTA did not enter a genuine public rated task")
    capture(page, "rated-mode-first-entry", journal)
    actions.append({"action": "mouse-click-and-consent",
                    "target": "Home / На рейтинг Lichess",
                    "mode": page.attempt_mode, "public_id": page._session_puzzle_id,
                    "rated_transport": "fake only; no HTTP POST"})

    # Switching modes inside the workspace must be a real click and must not
    # send a result for an untouched task. Each ensuing task is a distinct
    # public PuzzleAndGame response.
    wait_until(app, lambda: not getattr(window, "_puzzle_prefetch_handle", None)
               or not window._puzzle_prefetch_handle.is_alive(), 20000)
    adapter.set_real_sequence(rated_payloads[1:2])
    before_posts = len(adapter.rated_posts)
    click_dialog_button_with_mouse(
        app, "Переключить режим?", "Да",
        lambda: QTest.mouseClick(page.training_mode_button, Qt.MouseButton.LeftButton))
    wait_until(app, lambda: page.attempt_mode == "TRAINING"
               and page.state.puzzle is not None and page._session_puzzle_id != "lq8m1", 20000)
    if adapter.rated_posts:
        raise AssertionError("Switching away from an untouched rated task sent a result")
    capture(page, "mode-switched-to-training", journal)
    actions.append({"action": "mouse-click-mode-switch", "mode": "TRAINING",
                    "result_posts": len(adapter.rated_posts)})

    wait_until(app, lambda: not getattr(window, "_puzzle_prefetch_handle", None)
               or not window._puzzle_prefetch_handle.is_alive(), 20000)
    auto_next_payloads, auto_next_evidence = public_puzzle_sequence(["ehjSy"])
    adapter.set_real_sequence([rated_payloads[2], auto_next_payloads[0]])
    click_dialog_button_with_mouse(
        app, "Переключить режим?", "Да",
        lambda: QTest.mouseClick(page.rated_mode_button, Qt.MouseButton.LeftButton))
    wait_until(app, lambda: page.attempt_mode == "LICHESS_RATED"
               and page.state.puzzle is not None and page._session_puzzle_id == "MdQwv", 20000)
    capture(page, "mode-switched-to-rated", journal)
    if len(adapter.rated_posts) != before_posts:
        raise AssertionError("An untouched task produced a rated submission during mode switch")
    wait_until(app, lambda: page._prepared_online_puzzle is not None
               and page._prepared_online_puzzle["puzzle"].puzzle_id == "ehjSy", 20000)
    click_checkbox(app, page.auto_next, True)
    adapter.block_rated = True
    adapter.release_rated.clear()
    prior_rated_id = page._session_puzzle_id
    solve_visible_line(app, page, journal, "rated-mode-confirmed-win")
    wait_until(app, adapter.rated_started.is_set, 10000)
    QTest.qWait(1700)
    if page._session_puzzle_id != prior_rated_id:
        raise AssertionError("Rated auto-next advanced before Lichess ACK")
    capture(page, "rated-result-waiting-for-ack", journal)
    adapter.release_rated.set()
    wait_until(app, lambda: page.rated_status.text().startswith("✓ Lichess подтвердил"), 10000)
    if len(adapter.rated_posts) != 1:
        raise AssertionError("One rated solve did not produce exactly one fake Lichess round")
    if (adapter.rated_posts[0]["puzzle_id"] != "MdQwv"
            or adapter.rated_posts[0]["transport"] != "synthetic-fake-response; no HTTP POST"):
        raise AssertionError("Rated confirmation did not match the exact puzzle/fake transport")
    capture(page, "rated-server-confirmation", journal)
    wait_until(app, lambda: page._session_puzzle_id == "ehjSy", 10000)
    if page.attempt_mode != "LICHESS_RATED":
        raise AssertionError("Post-ACK auto-next did not retain the rated mode")
    capture(page, "rated-auto-next-after-ack", journal)
    actions.append({"action": "mouse-solve-rated-task",
                    "official_ack_modeled": True, "real_rated_post": False,
                    "fake_round_count": len(adapter.rated_posts)})

    click_dialog_button_with_mouse(
        app, "Оставить текущую задачу?", "Да",
        lambda: QTest.mouseClick(page.home_button, Qt.MouseButton.LeftButton))
    wait_until(app, lambda: window.screen_stack.currentWidget() is window.home_page)
    wait_until(app, lambda: "1807" in window.home_page.official_puzzle_rating.text(), 5000)
    capture(window, "home-official-rating-after-server-ack", journal)
    actions.append({"action": "home-rating-refresh",
                    "displayed_rating": window.home_page.official_puzzle_rating.text(),
                    "source": "isolated fake server response; not authenticated acceptance"})

    trace = {"public_get": public_get_evidence,
             "rated_public_tasks": rated_evidence,
             "consent_cancel_public_task": consent_cancel_evidence,
             "genuine_public_mate_in_2_evidence": mate_in_2_evidence,
             "genuine_public_mate_in_1_evidence": mate_in_1_evidence,
             "rated_auto_next_public_evidence": auto_next_evidence,
             "authenticated_lichess_verified": False,
             "l2_transport": "public unauthenticated GET payloads; synthetic fake rated response only",
             "rated_posts": adapter.rated_posts,
             "genuine_public_puzzle_count": len(set(
                 ["0haqU"] + MATE_IN_2_PUBLIC_IDS + MATE_IN_1_PUBLIC_IDS)),
             "public_get_response_count": (1 + len(mate_in_2_evidence)
                                           + len(mate_in_1_evidence)
                                           + len(rated_evidence)
                                           + len(consent_cancel_evidence)
                                           + len(auto_next_evidence)),
             "batch_calls": adapter.calls, "unique_ids_in_ten_task_run": sorted(seen),
             "actions": actions, "frames": journal,
             "owner_database_or_oauth_touched": False}
    (OUTPUT / "action-journal.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    # GitHub Actions' Windows console may use cp1252 even when the journal is
    # UTF-8; keep its one-line console copy ASCII while the artifact stays UTF-8.
    print("PUZ06_ACTION_JOURNAL " + json.dumps(trace, ensure_ascii=True, sort_keys=True))
    cleanup()


if __name__ == "__main__":
    try:
        run()
    finally:
        cleanup()
