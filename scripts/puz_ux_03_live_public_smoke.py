"""Play a small set of real public Lichess puzzles in the production Qt page.

This is a one-shot interactive L2 smoke for an isolated Windows runner, never a
pytest test. It performs three documented, sequential, unauthenticated GETs and
records only public puzzle metadata plus genuine widget screenshots.
"""

from __future__ import annotations

from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import math
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import chess
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from chess_coach.diagnostics import shutdown_logging
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.theme import VISUAL_TOKENS, stylesheet as desktop_stylesheet
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage


PUZZLE_IDS = ("0haqU", "00008", "0000D")
BASE_URL = "https://lichess.org/api/puzzle/"


def fetch_public_puzzles() -> list[tuple[dict, dict]]:
    result = []
    seen: set[str] = set()
    for puzzle_id in PUZZLE_IDS:
        url = BASE_URL + puzzle_id
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        request = Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "ChessCoach-PUZ-UX-03-live-smoke"})
        try:
            with urlopen(request, timeout=20) as response:
                status = int(response.status)
                raw = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"Public Lichess GET returned HTTP {exc.code} for {puzzle_id}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Public Lichess GET failed for {puzzle_id}: {exc}") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        if status != 200:
            raise RuntimeError(f"Public Lichess GET returned HTTP {status} for {puzzle_id}")
        payload = json.loads(raw.decode("utf-8"))
        playable = normalize_api_puzzle(payload)
        if playable.puzzle_id in seen:
            raise AssertionError(f"Expected distinct known public puzzle id, got {playable.puzzle_id}")
        seen.add(playable.puzzle_id)
        metadata = {
            "url": url, "timestamp_utc": timestamp, "status": status,
            "id": playable.puzzle_id, "game_pgn_length": len(str(payload["game"].get("pgn", ""))),
            "solution_count": len(playable.expected_user_moves), "get_ms": round(elapsed_ms, 2),
            "themes": list(playable.themes), "rating": playable.rating,
        }
        print("LIVE_GET " + json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        result.append((payload, metadata))
    return result


def wait_until(app: QApplication, predicate, *, timeout_ms: int = 2500) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(15)
    raise AssertionError("Qt live-puzzle state did not settle within the smoke timeout")


def click_move(app: QApplication, page: PuzzleTrainingPage, move: chess.Move) -> None:
    starting_index = page.state.solution_index
    starting_mistakes = page.state.mistakes
    start = page.board._square_center(move.from_square).toPoint()
    end = page.board._square_center(move.to_square).toPoint()
    QTest.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=start)
    if move.promotion:
        # BoardView deliberately opens the normal piece picker for user input.
        # Select the puzzle's expected piece through its actual Qt button while
        # QDialog.exec() runs its nested event loop.
        promotion = {"selected": False, "attempts": 0}

        def choose_promotion() -> None:
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                expected_name = chess.piece_name(move.promotion)
                button = next((item for item in dialog.findChildren(QPushButton)
                               if item.accessibleName() == expected_name), None)
                if button is not None:
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    promotion["selected"] = True
                    return
            promotion["attempts"] += 1
            if promotion["attempts"] < 100:
                QTimer.singleShot(10, choose_promotion)

        QTimer.singleShot(0, choose_promotion)
    QTest.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=end)
    if move.promotion and not promotion["selected"]:
        raise AssertionError("Promotion picker did not accept the expected piece")
    wait_until(app, lambda: page.state.solution_index > starting_index
               or page.state.mistakes > starting_mistakes)


def capture(page: PuzzleTrainingPage, output: Path, index: int, label: str,
            events: list[dict]) -> None:
    QApplication.processEvents()
    timestamp = datetime.now(timezone.utc).isoformat()
    filename = f"{index:02d}-{timestamp.replace(':', '').replace('+00:00', 'Z')}-{label}.png"
    image = page.grab().toImage()
    if image.isNull() or not image.save(str(output / filename)):
        raise AssertionError(f"Could not capture genuine Qt frame: {filename}")
    events.append({"timestamp_utc": timestamp, "event": label, "file": filename,
                   "status": page.status.text(), "puzzle_id": page.state.puzzle.puzzle_id
                   if page.state.puzzle else None})
    print(f"FRAME {timestamp} {filename}")


def instrument_first_feedback(page: PuzzleTrainingPage) -> list[dict]:
    """Measure synchronous stages of the first live input without changing behavior."""
    samples: list[dict] = []
    targets = (
        (page.state, "submit", "state.submit"),
        (page.board, "set_interaction_enabled", "board.interaction"),
        (page.board, "set_position", "board.set_position"),
        (page.board, "set_feedback_squares", "board.feedback"),
        (page, "_show_verdict", "page.verdict"),
        (page, "_record_input_feedback", "page.record_paint"),
        (page, "_record_first_failure", "page.first_failure"),
        (page, "_update_session_display", "page.session_display"),
    )
    for owner, name, label in targets:
        original = getattr(owner, name)

        def timed(*args, _original=original, _label=label, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                samples.append({"stage": _label,
                                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})

        setattr(owner, name, timed)
    return samples


def solve_public_line(app: QApplication, page: PuzzleTrainingPage, output: Path,
                      frames: list[dict], label_prefix: str) -> None:
    """Enter only the solver's plies; the production page plays replies itself."""
    moves = page.state.puzzle.expected_user_moves
    for _ in range(max(3, len(moves) * 2 + 2)):
        if page.state.status.value == "success":
            return
        if page.state.status.value == "opponent_reply":
            capture(page, output, len(frames), f"{label_prefix}-awaiting-reply", frames)
            wait_until(app, lambda: page.state.status.value != "opponent_reply")
            capture(page, output, len(frames), f"{label_prefix}-opponent-replied", frames)
            continue
        if page.state.status.value not in {"ready", "mistake_retry"}:
            raise AssertionError(
                f"Unexpected live puzzle status while solving: {page.state.status.value}")
        index = page.state.solution_index
        if index >= len(moves):
            raise AssertionError("Puzzle left an unfinished solution index")
        raw_move = moves[index]
        print("LIVE_MOVE " + json.dumps({
            "id": page.state.puzzle.puzzle_id, "index": index,
            "uci": raw_move, "fen": page.state.board.fen(),
        }, sort_keys=True))
        click_move(app, page, chess.Move.from_uci(raw_move))
        if page.state.status.value == "mistake_retry":
            raise AssertionError(
                f"Public solution move was rejected: id={page.state.puzzle.puzzle_id} "
                f"index={index} uci={raw_move} fen={page.state.board.fen()}")
    raise AssertionError(
        f"Public solution exceeded bounded interaction steps: "
        f"id={page.state.puzzle.puzzle_id} index={page.state.solution_index} "
        f"status={page.state.status.value}")


def play(output: Path, dpi_scale: str) -> None:
    debug_trace = (output / "live-smoke-stacks.log").open("w", encoding="utf-8")
    faulthandler.dump_traceback_later(45, repeat=True, file=debug_trace)
    os.environ["QT_SCALE_FACTOR"] = dpi_scale
    app = QApplication([])
    app.setStyleSheet(desktop_stylesheet(VISUAL_TOKENS))
    page = PuzzleTrainingPage()
    page.resize(1440, 900)
    page.show()
    page.raise_()
    page.activateWindow()
    wait_until(app, lambda: page.isActiveWindow())
    page.board.setFocus()
    app.processEvents()
    puzzles = fetch_public_puzzles()
    frames: list[dict] = []

    # A clean completion on the second real puzzle drives the production timer
    # into the third real online puzzle, with no database/MainWindow dependency.
    prepared = {item[0]["puzzle"]["id"]: item for item in puzzles}
    auto_next = {"advanced": False}

    def next_real(_theme: str, _difficulty: str) -> None:
        if auto_next["advanced"]:
            return
        auto_next["advanced"] = True
        raw, _meta = prepared["0000D"]
        page.load_puzzle(normalize_api_puzzle(raw), raw, source="lichess",
                         rating_eligible=False)
        capture(page, output, len(frames), "auto-next-third-live", frames)

    page.new_puzzle_requested.connect(next_real)
    page.auto_next.setChecked(True)

    # First puzzle: actual legal board input, visible wrong-move flash/rollback,
    # two hints, real solution and (when present) opponent replies.
    raw, _ = prepared["0haqU"]
    page.load_puzzle(normalize_api_puzzle(raw), raw, source="lichess", rating_eligible=False)
    capture(page, output, len(frames), "live-ready", frames)
    first_input_stages = instrument_first_feedback(page)
    board = page.state.board.copy()
    expected = chess.Move.from_uci(page.state.puzzle.expected_user_moves[0])
    wrong = next((move for move in board.legal_moves if move != expected), None)
    if wrong is None:
        raise AssertionError("Live puzzle has no alternate legal move for wrong-move UX check")
    click_move(app, page, wrong)
    if page.state.status.value != "mistake_retry":
        raise AssertionError("Real Qt input did not register the wrong legal move")
    capture(page, output, len(frames), "wrong-visible", frames)
    wait_until(app, lambda: not page._wrong_flash, timeout_ms=1000)
    capture(page, output, len(frames), "wrong-reverted", frames)
    page._hint()
    capture(page, output, len(frames), "hint-one", frames)
    page._hint()
    capture(page, output, len(frames), "hint-two-arrow", frames)
    solve_public_line(app, page, output, frames, "correct")
    if page.state.status.value != "success":
        raise AssertionError("Did not finish first public puzzle in production page")
    capture(page, output, len(frames), "hinted-win", frames)

    # Second puzzle: actual annotation gesture, wheel through only already
    # visible positions, a clean multi-ply solve, and production auto-next.
    raw, _ = prepared["00008"]
    page.load_puzzle(normalize_api_puzzle(raw), raw, source="lichess", rating_eligible=False)
    capture(page, output, len(frames), "second-live-ready", frames)
    center = page.board._square_center(chess.E2).toPoint()
    QTest.mouseClick(page.board, Qt.MouseButton.RightButton, pos=center)
    if chess.E2 not in page.board._annotation_squares:
        # The mark square is chosen from visible occupied squares if e2 is empty.
        square = next(iter(page.state.board.piece_map()))
        QTest.mouseClick(page.board, Qt.MouseButton.RightButton,
                         pos=page.board._square_center(square).toPoint())
    wheel_pos = page.board.rect().center()
    wheel_event = QWheelEvent(
        QPointF(wheel_pos), QPointF(page.board.mapToGlobal(wheel_pos)),
        QPoint(), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False)
    QCoreApplication.sendEvent(page.board, wheel_event)
    capture(page, output, len(frames), "mark-and-wheel", frames)
    if page.state.is_reviewing:
        page.return_to_solution.click()
        if page.state.is_reviewing:
            raise AssertionError("Return-to-solution control did not restore the live position")
    solve_public_line(app, page, output, frames, "second")
    if page.state.status.value != "success":
        raise AssertionError("Did not finish second public puzzle in production page")
    capture(page, output, len(frames), "clean-win", frames)
    wait_until(app, lambda: auto_next["advanced"], timeout_ms=2500)
    if page.state.puzzle.puzzle_id != "0000D":
        raise AssertionError("Production auto-next did not load the third public API position")

    # Third public position is now visible in the same live Qt page; exercise
    # the user's first move and finish its known public solution as well.
    solve_public_line(app, page, output, frames, "third")
    if page.state.status.value != "success":
        raise AssertionError("Did not finish third public puzzle after auto-next")
    capture(page, output, len(frames), "third-live-win", frames)

    trace = {"dpi_scale": dpi_scale, "authenticated": False,
             "requests": [meta for _raw, meta in puzzles], "frames": frames,
             "input_to_first_paint_ms": [round(value, 2)
                                         for value in page._input_feedback_ms],
             "input_to_first_paint_samples": [
                 {"kind": kind, "ms": round(value, 2)}
                 for kind, value in zip(page._input_feedback_kinds,
                                        page._input_feedback_ms)],
             "first_input_stage_timings": first_input_stages,
             "elapsed_seconds": round(sum(float(meta["get_ms"]) for _raw, meta in puzzles) / 1000, 3),
             "rating_eligible": False}
    input_samples = sorted(page._input_feedback_ms)
    get_samples = sorted(float(meta["get_ms"]) for _raw, meta in puzzles)
    if input_samples:
        trace["input_to_first_paint_p95_ms"] = round(
            input_samples[min(len(input_samples) - 1,
                              math.ceil(0.95 * len(input_samples)) - 1)], 2)
    trace["public_get_p50_ms"] = round(get_samples[len(get_samples) // 2], 2)
    trace["public_get_p95_ms"] = round(
        get_samples[min(len(get_samples) - 1,
                        math.ceil(0.95 * len(get_samples)) - 1)], 2)
    (output / "live-public-trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LIVE_TRACE " + json.dumps({"dpi_scale": dpi_scale,
                                    "puzzles": [meta["id"] for _raw, meta in puzzles],
                                    "frames": len(frames),
                                    "elapsed_seconds": trace["elapsed_seconds"]},
                                   ensure_ascii=False))
    page.close()
    app.processEvents()
    shutdown_logging()
    faulthandler.cancel_dump_traceback_later()
    debug_trace.close()


def main() -> None:
    output = Path(os.environ.get("CC_PUZ_UX_03_LIVE_OUTPUT", "live-evidence"))
    output.mkdir(parents=True, exist_ok=True)
    play(output, os.environ.get("CC_PUZ_UX_03_LIVE_DPI", "1"))


if __name__ == "__main__":
    main()
