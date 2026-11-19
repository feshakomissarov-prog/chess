"""Capture the production puzzle page with a native Windows Qt window and demo data."""

from __future__ import annotations

import os
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
        payload = {
            "game": {"id": "visual-fixture", "pgn": "1. e4 e5 2. Nf3"},
            "puzzle": {"id": "PZ42", "initialPly": 2, "lastMove": "g1f3",
                       "solution": ["d7d5"], "themes": ["fork", "middlegame"],
                       "rating": 1620, "plays": 910},
        }
        page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
        page.status.setText("Найдите лучший ход · визуальный fixture")
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

        page.close()
        window.close()
        app.processEvents()
        shutdown_logging()


if __name__ == "__main__":
    main()
