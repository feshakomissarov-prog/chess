"""Capture the production puzzle page with a native Windows Qt window and demo data."""

from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import tempfile
import time

import chess
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chess_coach.diagnostics import shutdown_logging
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.window import MainWindow


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    output = Path(os.environ.get("CC_PUZZLE_VISUAL_OUTPUT", "visual-evidence/puzzle-training"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cc-puzzle-capture-",
                                     ignore_cleanup_errors=True) as local_data:
        os.environ["LOCALAPPDATA"] = local_data
        app = QApplication([])
        assert app.platformName() == "windows", f"Expected native Windows Qt, got {app.platformName()}"
        window = MainWindow(offline_mode=True)
        window.show_puzzle_training()
        window.show()
        app.processEvents()
        page = window._puzzle_training_page
        assert page is not None

        def wait_for(predicate) -> None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                QTest.qWait(30)
            raise AssertionError(
                f"Qt training state did not settle: status={page.status.text()!r}, "
                f"account={page.account_id!r}, "
                f"puzzle={getattr(page.state.puzzle, 'puzzle_id', None)!r}")

        def settle_account_reads() -> None:
            request_ids = set(window._puzzle_history_callbacks)
            wait_for(lambda: all(request_id not in window._puzzle_history_callbacks
                                 for request_id in request_ids))

        settle_account_reads()
        payload = {
            "game": {"id": "visual-fixture", "pgn": "1. e4 e5 2. Nf3"},
            "puzzle": {"id": "PZ42", "initialPly": 2, "lastMove": "g1f3",
                       "solution": ["d7d5"], "themes": ["fork", "middlegame"],
                       "rating": 1620, "plays": 910},
        }
        page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
        page.status.setText("Найдите лучший ход · визуальный fixture")
        now_ms = int(time.time() * 1000)
        page.set_recent_history([
            {"puzzle_id": f"VIS-{index}", "result": result,
             "created_at_ms": now_ms - index * 86_400_000,
             "source": "lichess" if index % 2 == 0 else "local",
             "win": result == "success", "solved": result == "success",
             "mistakes": 1 if result == "mistake" else 0,
             "themes": [theme]}
            for index, (result, theme) in enumerate((
                ("success", "fork"), ("mistake", "pin"), ("hinted", "middlegame"),
                ("success", "endgame"), ("revealed", "mateIn2"),
                ("skipped", "discoveredAttack"), ("success", "rookEndgame"),
                ("hinted", "hangingPiece")), start=1)
        ], local_count=14,
           local_stats={"attempts": 14, "solved": 9, "success_percent": 64.3},
           theme_stats={"attempts": 4, "solved": 3, "success_percent": 75.0})
        app.processEvents()
        size_text = os.environ.get("CC_PUZZLE_VISUAL_SIZE", "1672x941")
        width, height = (int(part) for part in size_text.lower().split("x", 1))
        window.resize(width, height)
        QTest.qWait(200)
        assert window.isVisible()
        assert window.width() <= width and window.height() <= height
        assert page.board.isVisible() and page.fetch.isVisible() and page.hint.isVisible()
        physical_board_side = min(page.board.width(), page.board.height()) * page.board.devicePixelRatioF()
        assert physical_board_side >= 320, (
            f"Puzzle board is too small: {page.board.width()}x{page.board.height()} logical, "
            f"DPR={page.board.devicePixelRatioF():.2f}, {physical_board_side:.0f} physical"
        )
        for widget in (page.fetch, page.hint, page.reveal, page.repeat, page.next):
            rect = widget.rect().translated(widget.mapTo(window, QPoint(0, 0)))
            assert window.rect().contains(rect), (
                f"{widget.objectName() or widget.text()} outside window: {rect.getRect()}"
            )

        def save(name: str) -> None:
            QCursor.setPos(window.mapToGlobal(QPoint(width + 20, height + 20)))
            QTest.qWait(80)
            image = window.grab().toImage()
            assert not image.isNull()
            path = output / f"{size_text}-{name}.png"
            assert image.save(str(path))
            print(f"{path.name}: {image.width()}x{image.height()} physical Qt pixels; "
                  f"DPR={image.devicePixelRatio()}")

        save("ready")

        page.set_accounts([{"account_id": "local", "username": "Локальный профиль"},
                           {"account_id": "lichess:visual-b", "username": "Профиль B"}], "local")
        page.account_selector.setCurrentIndex(page.account_selector.findData("lichess:visual-b"))
        app.processEvents()
        settle_account_reads()
        save("account-switch")
        page.account_selector.setCurrentIndex(page.account_selector.findData("local"))
        app.processEvents()
        settle_account_reads()

        failed = {"attempt_id": "visual-save-failure", "session_id": page._session_id,
                  "account_id": page._session_account_id, "puzzle_id": page._session_puzzle_id}
        page._pending_attempt_saves[failed["attempt_id"]] = failed
        page._attempt_save_completed(failed["attempt_id"], False, {}, "OperationalError")
        save("save-error")

        page._pending_attempt_saves.pop(failed["attempt_id"], None)
        page.retry_save.hide()
        page.set_authorization_needed(True)
        page.set_message("Для новых задач требуется разрешение puzzle:read."
                         " Сохранённые задачи доступны офлайн.")
        save("oauth-consent")
        page.set_authorization_needed(False)

        def fixture(puzzle_id: str) -> dict:
            return {"game": {"id": f"visual-{puzzle_id}", "pgn": "1. e4 e5 2. Nf3"},
                    "puzzle": {"id": puzzle_id, "initialPly": 2, "lastMove": "g1f3",
                               "solution": ["d7d5"], "themes": ["fork"],
                               "rating": 1500, "plays": 12}}

        for puzzle_id in ("NEXT-A", "NEXT-B", "NEXT-C", "OLD-101"):
            cached_payload = fixture(puzzle_id)
            window.game_repository.cache_training_puzzle(
                "local", puzzle_id, json.dumps(cached_payload))

        page.source.setCurrentIndex(page.source.findData("saved"))
        window._request_local_training_puzzle(page, "saved", "")
        wait_for(lambda: page.state.puzzle is not None and
                 page.state.puzzle.puzzle_id.startswith("NEXT-"))
        first_local_id = page.state.puzzle.puzzle_id
        window._request_local_training_puzzle(page, "saved", "")
        wait_for(lambda: page.state.puzzle is not None and
                 page.state.puzzle.puzzle_id != first_local_id)
        assert page.state.puzzle.puzzle_id != first_local_id
        save("local-next")

        window._request_local_training_puzzle(
            page, "replay", "", puzzle_id="OLD-101", source_hint="local")
        wait_for(lambda: page.state.puzzle is not None and
                 page.state.puzzle.puzzle_id == "OLD-101")
        assert page.state.puzzle.puzzle_id == "OLD-101"
        save("old-history-replay")
        center = page.board._square_center
        start = center(chess.F3).toPoint()
        end = center(chess.D5).toPoint()
        QTest.mousePress(page.board, Qt.MouseButton.RightButton, pos=start)
        QTest.mouseMove(page.board, end, 100)
        QTest.mouseRelease(page.board, Qt.MouseButton.RightButton, pos=end)
        QTest.mouseClick(page.board, Qt.MouseButton.RightButton, pos=center(chess.E4).toPoint())
        assert page.board._annotation_arrows and page.board._annotation_squares
        save("user-marks")

        page.board.move_intent.emit(chess.Move.from_uci("d7d5"))
        assert page.state.status.value == "success"
        save("solved")

        page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
        page.board.move_intent.emit(chess.Move.from_uci("d7d6"))
        assert page.state.status.value == "mistake_retry"
        page._hint()
        save("mistake-hint")

        page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
        page._hint()
        page.board.move_intent.emit(chess.Move.from_uci("d7d5"))
        assert page.state.status.value == "success"
        save("solved-after-hint")

        page.close()
        window.close()
        app.processEvents()
        shutdown_logging()


if __name__ == "__main__":
    main()
