import chess
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage


def test_right_annotations_do_not_move_and_lmb_click_click_solves(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    qtbot.waitUntil(lambda: page.board.width() > 160 and page.board.height() > 160)
    board = page.board

    def center(square):
        return board._square_center(square).toPoint()

    start_fen = board._board.fen()
    from_pos, to_pos = center(chess.E4), center(chess.F5)
    qtbot.mousePress(board, Qt.MouseButton.RightButton, pos=from_pos)
    qtbot.mouseMove(board, to_pos, delay=50)
    qtbot.mouseRelease(board, Qt.MouseButton.RightButton, pos=to_pos)
    qtbot.mouseClick(board, Qt.MouseButton.RightButton, pos=center(chess.C3))
    assert board._annotation_arrows == {(chess.E4, chess.F5)}
    assert board._annotation_squares == {chess.C3}
    assert board._board.fen() == start_fen

    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=center(chess.D7))
    qtbot.mouseClick(board, Qt.MouseButton.LeftButton, pos=center(chess.D5))
    assert page.state.status.value == "success"
    assert not board._annotation_arrows and not board._annotation_squares


def test_reveal_requires_confirmation_and_exposes_line_only_after_yes(qtbot, monkeypatch):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.show()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.No)
    page._reveal()
    assert page.solution_line.isHidden()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    page._reveal()
    assert not page.solution_line.isHidden()
    assert "Правильная линия" in page.solution_line.text()
    assert page.state.status.value == "revealed"


def test_attempt_is_bound_to_account_at_load_not_current_selector(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.set_accounts([{"account_id": "lichess:a", "username": "A"},
                       {"account_id": "lichess:b", "username": "B"}], "lichess:a")
    page.load_puzzle(page.state.puzzle, page._puzzle_payload, source="demo")
    page.account_changed.connect(lambda account: setattr(page, "account_id", account))
    attempts = []
    page.attempt_saved.connect(attempts.append)
    page.account_selector.setCurrentIndex(page.account_selector.findData("lichess:b"))
    page._submit_move(chess.Move.from_uci("d7d5"))
    assert attempts[-1]["account_id"] == "lichess:a"


def test_history_replay_carries_original_source_and_account(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.account_id = "lichess:history-user"
    page.set_recent_history([{
        "puzzle_id": "old-puzzle", "result": "Ошибка", "source": "lichess",
        "themes": ["endgame"], "attempt_date_ms": 1_600_000_000_000,
    }])
    replay_signal = getattr(page, "replay_requested", None)
    assert replay_signal is not None, "history replay must carry source/account context"
    captured = []
    replay_signal.connect(lambda *args: captured.append(args))
    page._open_history_item(page.history.item(0))
    assert captured == [("lichess:history-user", "old-puzzle", "lichess")]


def test_history_and_statistics_wait_for_real_save_ack_and_retry_same_attempt(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.set_recent_history([], local_count=0,
                            local_stats={"attempts": 0, "solved": 0, "success_percent": None},
                            theme_stats={"attempts": 0, "solved": 0, "success_percent": None})
    saved = []
    page.attempt_saved.connect(saved.append)
    page._submit_move(chess.Move.from_uci("d7d5"))
    attempt = saved[-1]
    assert page.history.count() == 0
    assert page._local_count == 0
    assert page.status.text().startswith("✓ Задача решена")
    assert page.persistence_status.text().startswith("● Сохраняется")

    page._attempt_save_completed(attempt["attempt_id"], False, {}, "OperationalError")
    assert page.history.count() == 0
    assert page._local_count == 0
    assert "не удалось сохранить" in page.persistence_status.text().casefold()
    page._retry_attempt_save()
    assert saved[-1]["attempt_id"] == attempt["attempt_id"]

    page._attempt_save_completed(attempt["attempt_id"], True, {
        "local_count": 1,
        "local_stats": {"attempts": 1, "solved": 1, "success_percent": 100.0},
        "theme_stats": {"attempts": 1, "solved": 1, "success_percent": 100.0},
        "recent": [{"puzzle_id": "Demo", "result": "success", "solved": True,
                    "created_at_ms": 1_700_000_000_000, "source": "local",
                    "themes": ["fork"]}],
    })
    assert page.history.count() == 1
    assert "Решено" in page.history.item(0).text()
    assert page._local_count == 1
    assert "сохран" in page.persistence_status.text().casefold()


def test_repeat_requires_consent_when_attempt_is_still_active(qtbot, monkeypatch):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    start_fen = page.state.board.fen()
    page.state.hint()
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.No)
    page._repeat()
    assert page.state.board.fen() == start_fen
    assert page.state.hints_used == 1
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    page._repeat()
    assert page.state.status.value == "ready"
    assert page.state.hints_used == 0


def test_history_status_labels_and_theme_list_have_no_horizontal_scroll(qtbot):
    page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(page)
    page.set_recent_history([
        {"puzzle_id": "p1", "result": "success", "solved": True,
         "mistakes": 2, "source": "local", "themes": ["discoveredAttack"]},
        {"puzzle_id": "p2", "result": "hinted", "solved": False,
         "source": "lichess", "themes": ["hangingPiece"]},
    ])
    assert "Решено с ошибками" in page.history.item(0).text()
    assert "С подсказкой" in page.history.item(1).text()
    assert page.history.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.theme_list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
