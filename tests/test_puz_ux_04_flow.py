"""PUZ-UX-04 regressions use actual Qt input events at the widget boundary."""

import chess
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from chess_coach import window as window_module
from chess_coach.bootstrap import create_runtime
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
from chess_coach.window import MainWindow


def _payload(puzzle_id="Puz04-1"):
    return {"game": {"id": "fixture", "pgn": "1. e4 e5 2. Nf3"},
            "puzzle": {"id": puzzle_id, "initialPly": 2, "solution": ["d7d5"],
                       "themes": ["fork"], "rating": 1500, "plays": 12,
                       "lastMove": "g1f3"}}


def _center(board, square):
    return board._square_center(square).toPoint()


def _wheel(widget, delta):
    point = QPointF(widget.rect().center())
    event = QWheelEvent(point, QPointF(widget.mapToGlobal(point.toPoint())),
                        QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_wheel_forward_returns_playable_board(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    qtbot.waitUntil(lambda: page.board.width() > 160)

    _wheel(page.board, 120)
    assert page.state.is_reviewing
    assert not page.board._interaction_enabled
    _wheel(page.board, -120)

    assert not page.state.is_reviewing
    assert page.board._interaction_enabled
    assert page.return_to_solution.isVisible()
    qtbot.mouseClick(page.return_to_solution, Qt.MouseButton.LeftButton)
    assert page.return_to_solution.isHidden()
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=_center(page.board, chess.D7))
    assert page.board._selected_square == chess.D7


def test_lmb_piece_selection_clears_puzzle_annotations_only(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    board = page.board
    source, target = _center(board, chess.E4), _center(board, chess.F5)
    qtbot.mousePress(board, Qt.MouseButton.RightButton, pos=source)
    qtbot.mouseMove(board, target, delay=20)
    qtbot.mouseRelease(board, Qt.MouseButton.RightButton, pos=target)
    assert board._annotation_arrows

    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=_center(board, chess.D7))
    assert not board._annotation_arrows and not board._annotation_squares
    assert page.state.board.piece_at(chess.D7) == chess.Piece(chess.PAWN, chess.BLACK)


def test_next_is_single_visible_action_unique_id_and_non_silent_retry(qtbot, monkeypatch):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.show()
    page.load_puzzle(normalize_api_puzzle(_payload()), _payload(), source="lichess")
    visible_ctas = [button for button in page.findChildren(QPushButton)
                    if button.isVisible() and button.text().startswith("Следующая задача")]
    assert visible_ctas == [page.next]
    assert page._is_unique_online_puzzle(page.state.puzzle, "mix", "normal") is False

    requests = []
    page.new_puzzle_requested.connect(lambda *args: requests.append(args))
    page.new_puzzle_requested.connect(lambda *_args: page.set_loading("Получаю задачу…"))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: (print("journey: confirm", flush=True),
                                                 QMessageBox.StandardButton.Yes)[1])
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    page.next.click()
    assert len(requests) == 1
    assert page.fetch.text() == "Загружаем…"
    page.set_message("Тайм-аут Lichess. Повторите загрузку.")
    assert page.fetch.isEnabled()
    assert page.fetch.text() == "Повторить загрузку"
    assert "Тайм-аут" in page.network_status.text()


def test_results_tiles_wrong_then_clean_and_unrated_replay(qtbot):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    payload = _payload()
    page.account_id = "lichess:fixture"
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    page.resize(1050, 720)
    page.show()
    board = page.board

    # A real illegal-for-the-puzzle (but legal chess) move immediately marks red.
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=_center(board, chess.D7))
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=_center(board, chess.D6))
    qtbot.waitUntil(lambda: page._result_tiles[0].text() == "✕")
    qtbot.wait(500)
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=_center(board, chess.D7))
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=_center(board, chess.D5))
    assert page.state.status.value == "success"
    assert page._result_tiles[0].text() == "✕"
    assert page._session_streak == 0

    old_attempt_count = len(page._pending_attempt_saves)
    page.repeat.click()
    assert page._is_replay
    assert "Повтор · без рейтинга" in page.turn_label.text()
    page._submit_move(chess.Move.from_uci("d7d5"))
    assert len(page._pending_attempt_saves) == old_attempt_count
    assert len(page._session_results) == 1


def test_mainwindow_real_qt_journey_uses_unique_online_tasks_and_recovers(
        qtbot, monkeypatch, tmp_path):
    """End-to-end UI journey through MainWindow, fake auth and real Qt mouse/wheel input."""
    runtime = create_runtime(offline_mode=True)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    window.offline_mode = False

    class Store:
        def __init__(self, **_kwargs):
            pass

        def get(self):
            return "inert-puzzle-read-fixture"

    class Client:
        def __init__(self):
            self.calls = 0
            self.fail_next = False
            self.all_ids = []

        def get_account(self, _token):
            return {"username": "fixture-user"}

        def get_puzzle_batch(self, _token, **_kwargs):
            if self.fail_next:
                self.fail_next = False
                raise TimeoutError("simulated network timeout")
            self.calls += 1
            puzzle_id = f"Journey-{self.calls}"
            self.all_ids.append(puzzle_id)
            return {"puzzles": [_payload(puzzle_id)]}

    client = Client()
    window.lichess = SimpleNamespace(
        client=client, username=None, puzzle_username=None,
        capture_operation_context=lambda **_kwargs: None,
        close=lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(window_module, "WindowsCredentialStore", Store)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    traces = []
    screenshots = []
    try:
        # Enter from the visible Home navigation with a physical Qt button event.
        qtbot.mouseClick(window._nav_buttons["training"], Qt.MouseButton.LeftButton)
        page = window._puzzle_training_page
        qtbot.waitUntil(lambda: page is not None and page.state.puzzle is not None, timeout=10_000)
        page.auto_next.setChecked(False)

        def capture(name):
            path = tmp_path / f"{name}.png"
            assert window.grab().save(str(path))
            screenshots.append(path.name)
            traces.append({"state": name, "puzzle_id": page._session_puzzle_id,
                           "status": page.state.status.value,
                           "input_enabled": page.board._interaction_enabled,
                           "timestamp": time.time()})

        # Inspect a previous position and use the forward wheel to resume.
        _wheel(page.board, 120)
        assert page.state.is_reviewing
        capture("review")
        _wheel(page.board, -120)
        assert not page.state.is_reviewing and page.board._interaction_enabled
        capture("restored-ready")

        # Mark with right mouse, then left select the user's own piece.
        source, target = _center(page.board, chess.E4), _center(page.board, chess.F5)
        qtbot.mousePress(page.board, Qt.MouseButton.RightButton, pos=source)
        qtbot.mouseMove(page.board, target, delay=20)
        qtbot.mouseRelease(page.board, Qt.MouseButton.RightButton, pos=target)
        capture("arrow-before")
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=_center(page.board, chess.D7))
        assert not page.board._annotation_arrows
        capture("arrow-cleared")

        # A first error stays red after the correct answer completes the puzzle.
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=_center(page.board, chess.D6))
        qtbot.waitUntil(lambda: page._result_tiles[0].text() == "✕", timeout=2000)
        capture("wrong-red")
        qtbot.wait(500)
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=_center(page.board, chess.D7))
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=_center(page.board, chess.D5))
        qtbot.waitUntil(lambda: page.state.status.value == "success", timeout=3000)
        capture("wrong-then-solved")
        first_id = page._session_puzzle_id

        # Four successive new tasks are selected only via the visible CTA.
        seen = {first_id}
        for index in range(4):
            qtbot.mouseClick(page.next, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(lambda: page.state.puzzle is not None
                            and page._session_puzzle_id not in seen, timeout=10_000)
            current_id = page._session_puzzle_id
            assert current_id not in seen
            seen.add(current_id)
            assert page.state.status.value == "ready"
            traces.append({"state": f"new-{index + 1}", "puzzle_id": current_id,
                           "status": page.state.status.value,
                           "input_enabled": page.board._interaction_enabled,
                           "timestamp": time.time()})
            qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                             pos=_center(page.board, chess.D7))
            qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                             pos=_center(page.board, chess.D5))
            qtbot.waitUntil(lambda: page.state.status.value == "success", timeout=3000)

        assert len(seen) == 5
        assert page._session_streak == 4
        assert page._result_tiles[-1].text() == "✓"
        capture("clean-streak")

        # Repeat is deliberately unrated and does not add an attempt tile/save.
        prior_attempts = len(page._pending_attempt_saves)
        repeated_id = page._session_puzzle_id
        qtbot.mouseClick(page.repeat, Qt.MouseButton.LeftButton)
        assert page._is_replay and page._session_puzzle_id == repeated_id
        capture("replay-unrated")
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=_center(page.board, chess.D7))
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=_center(page.board, chess.D5))
        qtbot.waitUntil(lambda: page.state.status.value == "success", timeout=3000)
        assert len(page._pending_attempt_saves) == prior_attempts

        # Fail one direct NEXT after its optional prefetch was unavailable, then recover.
        page._prepared_online_puzzle = None
        client.fail_next = True
        qtbot.mouseClick(page.next, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.fetch.isEnabled()
                        and page.fetch.text() == "Повторить загрузку", timeout=10_000)
        assert "Тайм-аут Lichess" in page.network_status.text()
        capture("timeout-retry")
        qtbot.mouseClick(page.next, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.state.puzzle is not None
                        and page._session_puzzle_id != repeated_id, timeout=10_000)
        capture("network-recovered")
        assert not page.network_status.text()
        assert len(set(client.all_ids)) == len(client.all_ids)
        (tmp_path / "puz-ux-04-trace.json").write_text(
            json.dumps({"puzzle_ids": client.all_ids, "states": traces,
                        "screenshots": screenshots}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    finally:
        window.close()
        assert runtime.close()
