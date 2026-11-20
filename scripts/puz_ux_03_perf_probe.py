"""Measure cold and warm native Qt puzzle input on isolated Windows runners.

The parent launches ten fresh child processes (one public unauthenticated GET
and one real board input each), then measures fifty warm legal-but-wrong board
inputs in one process. It records stages without assigning a cause to delays.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import chess
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
from chess_coach.ui.theme import VISUAL_TOKENS, stylesheet as desktop_stylesheet
from puz_ux_03_live_public_smoke import BoardPaintObserver, click_move, wait_until


PUZZLE_ID = "0haqU"
PROCESS_STARTED = time.perf_counter()


class InputObserver(QObject):
    def __init__(self, board) -> None:
        super().__init__(board)
        self.mouse_presses: list[float] = []
        board.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonPress:
            self.mouse_presses.append(time.perf_counter())
        return False


def fetch_public() -> tuple[dict, float]:
    url = f"https://lichess.org/api/puzzle/{PUZZLE_ID}"
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "ChessCoach-PUZ-UX-03-perf-probe"})
    started = time.perf_counter()
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"Lichess returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    return payload, round((time.perf_counter() - started) * 1000, 2)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 2)


def setup_page() -> tuple[QApplication, PuzzleTrainingPage, dict, dict]:
    app_started = time.perf_counter()
    app = QApplication([])
    app.setStyleSheet(desktop_stylesheet(VISUAL_TOKENS))
    app_ready_ms = round((time.perf_counter() - app_started) * 1000, 2)
    payload, get_ms = fetch_public()
    page_started = time.perf_counter()
    page = PuzzleTrainingPage()
    page.resize(1440, 900)
    page.show()
    page.raise_()
    page.activateWindow()
    page_ready_ms = round((time.perf_counter() - page_started) * 1000, 2)
    wait_until(app, lambda: page.isActiveWindow(), timeout_ms=5000)
    page.board.setFocus()
    app.processEvents()
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess",
                     rating_eligible=False)
    wait_until(app, lambda: page.state.puzzle is not None, timeout_ms=5000)
    stages = {"process_to_app_ready_ms": app_ready_ms,
              "app_ready_to_public_get_ms": get_ms,
              "page_constructor_show_ms": page_ready_ms,
              "process_started_utc": datetime.now(timezone.utc).isoformat()}
    return app, page, payload, stages


def choose_wrong(page: PuzzleTrainingPage) -> chess.Move:
    expected = chess.Move.from_uci(page.state.puzzle.expected_user_moves[0])
    return next(move for move in page.state.board.legal_moves if move != expected)


def cold_child(index: int) -> None:
    app, page, _payload, stages = setup_page()
    paint_observer = BoardPaintObserver(page)
    page._automation_click_ms = []
    page._interaction_samples = []
    cpu_started = time.process_time()
    observer = InputObserver(page.board)
    gaps: list[float] = []
    last = [time.perf_counter()]
    heartbeat = QTimer(page)
    heartbeat.setInterval(10)

    def tick() -> None:
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now

    heartbeat.timeout.connect(tick)
    heartbeat.start()
    move = choose_wrong(page)
    before = len(page._input_feedback_ms)
    first_interaction = time.perf_counter()
    click_move(app, page, move)
    wait_until(app, lambda: len(page._input_feedback_ms) > before, timeout_ms=5000)
    feedback_ms = page._input_feedback_ms[-1]
    trace = {
        "process_index": index,
        "total_start_to_feedback_ms": round((time.perf_counter() - PROCESS_STARTED) * 1000, 2),
        "first_click_sequence_ms": round((time.perf_counter() - first_interaction) * 1000, 2),
        "input_to_first_paint_ms": round(float(feedback_ms), 2),
        "mouse_press_count": len(observer.mouse_presses),
        "automation_click_calls": list(page._automation_click_ms),
        "interaction_dispatch": page._interaction_samples[-1]
        if page._interaction_samples else None,
        "focus_at_input": page.board.hasFocus(),
        "cpu_time_during_first_input_ms": round((time.process_time() - cpu_started) * 1000, 2),
        "active_python_threads_at_input": threading.active_count(),
        "board_paint_durations_ms": paint_observer.durations_ms,
        "event_loop_gap_max_ms": percentile(gaps, 1.0),
        "stages": stages,
        "status": page.state.status.value,
    }
    print("PERF_COLD_RESULT " + json.dumps(trace, sort_keys=True))
    heartbeat.stop()
    for timer in page.findChildren(QTimer):
        timer.stop()
    page.close()
    page.deleteLater()
    app.processEvents()
    app.quit()
    app.processEvents()


def warm_process() -> dict:
    app, page, _payload, stages = setup_page()
    paint_observer = BoardPaintObserver(page)
    page._automation_click_ms = []
    page._interaction_samples = []
    observer = InputObserver(page.board)
    heartbeat_gaps: list[float] = []
    last = [time.perf_counter()]
    heartbeat = QTimer(page)
    heartbeat.setInterval(10)

    def tick() -> None:
        now = time.perf_counter()
        heartbeat_gaps.append((now - last[0]) * 1000)
        last[0] = now

    heartbeat.timeout.connect(tick)
    heartbeat.start()
    move = choose_wrong(page)
    timings: list[dict] = []
    for sample in range(50):
        feedback_before = len(page._input_feedback_ms)
        start = time.perf_counter()
        click_move(app, page, move)
        wait_until(app, lambda: len(page._input_feedback_ms) > feedback_before,
                   timeout_ms=5000)
        timings.append({
            "sample": sample + 1,
            "input_to_first_paint_ms": round(float(page._input_feedback_ms[-1]), 2),
            "source_to_destination_ms": (page._interaction_samples[-1].get(
                "source_to_destination_ms") if page._interaction_samples else None),
            "full_automation_sequence_ms": round((time.perf_counter() - start) * 1000, 2),
        })
        wait_until(app, lambda: not page._wrong_flash, timeout_ms=1500)
    trace = {
        "samples": timings,
        "input_to_first_paint_p50_ms": percentile(
            [item["input_to_first_paint_ms"] for item in timings], .50),
        "input_to_first_paint_p95_ms": percentile(
            [item["input_to_first_paint_ms"] for item in timings], .95),
        "input_to_first_paint_max_ms": percentile(
            [item["input_to_first_paint_ms"] for item in timings], 1.0),
        "event_loop_gap_p95_ms": percentile(heartbeat_gaps, .95),
        "event_loop_gap_max_ms": percentile(heartbeat_gaps, 1.0),
        "mouse_press_count": len(observer.mouse_presses),
        "board_paint_durations_ms": paint_observer.durations_ms,
        "setup_stages": stages,
    }
    heartbeat.stop()
    for timer in page.findChildren(QTimer):
        timer.stop()
    page.close()
    page.deleteLater()
    app.processEvents()
    app.quit()
    app.processEvents()
    return trace


def parent(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    child_results = []
    for index in range(10):
        command = [sys.executable, str(Path(__file__).resolve()), "--cold-child", str(index + 1)]
        launch_started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                   env=os.environ.copy())
        process_wall_ms = round((time.perf_counter() - launch_started) * 1000, 2)
        if completed.returncode:
            raise RuntimeError(
                f"Cold Qt child {index + 1} exited {completed.returncode}:\n{completed.stdout}\n"
                f"{completed.stderr}")
        line = next((item for item in completed.stdout.splitlines()
                     if item.startswith("PERF_COLD_RESULT ")), None)
        if line is None:
            raise RuntimeError(f"Cold Qt child {index + 1} returned no timing result")
        child_result = json.loads(line.removeprefix("PERF_COLD_RESULT "))
        child_result["parent_observed_process_wall_ms"] = process_wall_ms
        child_results.append(child_result)
    warm = warm_process()
    cold_inputs = [item["input_to_first_paint_ms"] for item in child_results]
    result = {
        "method": "10 separate cold Windows Qt processes + 50 warm real board inputs",
        "oauth": "not used; direct unauthenticated public GET only",
        "authorization_header_sent": False,
        "cold_samples": child_results,
        "cold_input_to_first_paint_p50_ms": percentile(cold_inputs, .50),
        "cold_input_to_first_paint_p95_ms": percentile(cold_inputs, .95),
        "cold_input_to_first_paint_max_ms": percentile(cold_inputs, 1.0),
        "warm": warm,
        "causal_attribution": "not inferred; stage timings are evidence only",
    }
    target = output / "first-input-performance.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PERF_SUMMARY " + json.dumps({
        "cold_count": len(child_results),
        "cold_p50_ms": result["cold_input_to_first_paint_p50_ms"],
        "cold_p95_ms": result["cold_input_to_first_paint_p95_ms"],
        "warm_count": len(warm["samples"]),
        "warm_p50_ms": warm["input_to_first_paint_p50_ms"],
        "warm_p95_ms": warm["input_to_first_paint_p95_ms"],
        "event_loop_max_ms": warm["event_loop_gap_max_ms"],
        "output": str(target),
    }, sort_keys=True))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--cold-child":
        cold_child(int(sys.argv[2]))
        return
    output = Path(os.environ.get("CC_PUZ_UX_03_PERF_OUTPUT", "live-evidence/perf"))
    parent(output)


if __name__ == "__main__":
    main()
