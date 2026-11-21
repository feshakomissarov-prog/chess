"""PUZ-UX-05 regressions for source, NEXT and ordinary puzzle gestures."""

from types import SimpleNamespace
import time

import chess
import chess_coach.lichess.puzzle_training as puzzle_training_module
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from chess_coach.board_view import BoardView
from chess_coach.lichess.puzzle_training import normalize_api_puzzle, normalize_activity_puzzle
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

select_api_puzzle_queue = getattr(puzzle_training_module, "select_api_puzzle_queue", None)
normalize_saved_puzzle_payload = getattr(
    puzzle_training_module, "normalize_saved_puzzle_payload", None)
select_next_api_puzzle = getattr(puzzle_training_module, "select_next_api_puzzle", None)

LEGACY_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def api_payload(puzzle_id="puz05-1"):
    return {
        "game": {"id": "fixture-game", "pgn": "1. e4 e5 2. Nf3"},
        "puzzle": {"id": puzzle_id, "initialPly": 2, "solution": ["d7d5"],
                   "themes": ["fork"], "rating": 1500, "plays": 12,
                   "lastMove": "g1f3"},
    }


def center(board, square):
    return board._square_center(square).toPoint()


def solve_d7d5(qtbot, page):
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D7))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D5))


def test_home_saved_replay_legacy_payload_and_separate_new(qtbot):
    assert callable(normalize_saved_puzzle_payload), (
        "PUZ05-01 needs the source-aware saved-puzzle normalizer")
    old_shape = {"puzzle": {"id": "saved-legacy", "fen": LEGACY_FEN,
                             "solution": ["c7c5", "g1f3"], "themes": ["opening"]}}
    playable = normalize_saved_puzzle_payload(
        "saved-legacy", old_shape, source_format="lichess_csv_v1")
    assert playable.puzzle_id == "saved-legacy"
    after_prep = chess.Board(LEGACY_FEN)
    after_prep.push_uci("c7c5")
    assert playable.player_to_move_fen == after_prep.fen()
    assert playable.expected_user_moves == ("g1f3",)
    full = api_payload("saved-full")
    assert normalize_saved_puzzle_payload("saved-full", full).puzzle_id == "saved-full"
    try:
        normalize_saved_puzzle_payload("broken", {"puzzle": {"id": "broken", "fen": "bad"}})
    except ValueError as error:
        assert "сохранённую задачу" in str(error).lower()
    else:
        raise AssertionError("Malformed exact replay must fail with a local replay message")


def test_official_activity_replay_preserves_player_fen_and_first_solution(qtbot):
    from chess_coach.lichess.puzzle_training import normalize_activity_puzzle

    playable = normalize_activity_puzzle(
        "0haqU", "6k1/1q3p2/5prp/2b5/3pN3/1PP2P1P/2Q1R1P1/6K1 b - - 0 1",
        ["d4d3", "e4c5", "d3c2", "c5b7", "c2c1q"],
        last_move="f2f3")
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page.load_puzzle(playable, {"puzzle": {"id": "0haqU"}}, source="replay")
    assert page.state.board.turn == chess.BLACK
    assert page.state.puzzle.expected_user_moves[0] == "d4d3"
    for raw in playable.expected_user_moves:
        move = chess.Move.from_uci(raw)
        promotion_clicked = []
        if move.promotion:
            promotion_deadline = time.monotonic() + 3

            def choose_queen():
                dialog = QApplication.activeModalWidget()
                if isinstance(dialog, QDialog) and dialog.windowTitle() == "Превращение пешки":
                    queen = next((button for button in dialog.findChildren(QPushButton)
                                  if button.accessibleName() == chess.piece_name(chess.QUEEN)), None)
                    if queen is not None:
                        qtbot.mouseClick(queen, Qt.MouseButton.LeftButton)
                        promotion_clicked.append(True)
                        return
                if time.monotonic() < promotion_deadline:
                    QTimer.singleShot(10, choose_queen)
                else:
                    promotion_clicked.append(False)

            QTimer.singleShot(0, choose_queen)
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=center(page.board, move.from_square))
        qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                         pos=center(page.board, move.to_square))
        if move.promotion:
            qtbot.waitUntil(lambda: promotion_clicked, timeout=3000)
            assert promotion_clicked == [True]
        if page.state.status.value == "opponent_reply":
            qtbot.waitUntil(lambda: page.state.status.value != "opponent_reply", timeout=2000)
    assert page.state.status.value == "success"
    assert page.state.board.piece_at(chess.C1).symbol() == "q"
    assert page.persistence_status.text() == "Повтор не записывается"


def test_batch_queue_consumes_all_valid_candidates_and_skips_malformed_game():
    def payload(pid, pgn, move):
        return {"game": {"id": f"game-{pid}", "pgn": pgn},
                "puzzle": {"id": pid, "initialPly": 2, "solution": [move],
                           "themes": ["fork"]}}

    rows = [payload("A", "1. e4 e5 2. Nf3", "d7d5"),
            {"game": None, "puzzle": {"id": "malformed", "initialPly": 2,
                                       "solution": ["d7d5"]}},
            payload("B", "1. d4 d5 2. c4", "e7e6"),
            payload("A", "1. e4 e5 2. Nf3", "d7d5"),
            payload("C", "1. c4 e5 2. Nc3", "g8f6")]
    queue = select_api_puzzle_queue(rows, set(), theme="mix", max_count=5)
    assert [row[0].puzzle_id for row in queue] == ["A", "B", "C"]
    assert len({row[0].player_to_move_fen for row in queue}) == 3


def test_history_row_replay_uses_same_exit_policy_as_result_tiles(qtbot):
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    playable = normalize_activity_puzzle(
        "active-rated", "6k1/1q3p2/5prp/2b5/3pN3/1PP2P1P/2Q1R1P1/6K1 b - - 0 1",
        ["d4d3", "e4c5", "d3c2", "c5b7", "c2c1q"], last_move="f2f3")
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page.set_mode("LICHESS_RATED")
    page.load_puzzle(playable, {}, source="lichess")
    page.set_recent_history([{"puzzle_id": "history-replay", "result": "Решено",
                             "source": "local", "themes": ["fork"]}], local_count=1)
    rated, replayed, saved = [], [], []
    page.rated_result_requested.connect(rated.append)
    page.replay_requested.connect(lambda *args: replayed.append(args))
    page.attempt_saved.connect(saved.append)

    def queue_dialog_click(button_text):
        deadline = time.monotonic() + 3

        def click_when_ready():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                button = next((item for item in dialog.buttons()
                               if item.text() == button_text), None)
                if button is not None:
                    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
                    return
            if time.monotonic() < deadline:
                QTimer.singleShot(10, click_when_ready)

        QTimer.singleShot(0, click_when_ready)

    first_move = chess.Move.from_uci("d4d3")
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                     pos=center(page.board, first_move.from_square))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton,
                     pos=center(page.board, first_move.to_square))
    qtbot.waitUntil(lambda: page.state.status.value != "opponent_reply", timeout=2000)
    queue_dialog_click("Продолжить")
    item = page.history.item(0)
    item_rect = page.history.visualItemRect(item)
    qtbot.mouseClick(page.history.viewport(), Qt.MouseButton.LeftButton,
                     pos=item_rect.center())
    qtbot.waitUntil(lambda: QApplication.activeModalWidget() is None, timeout=2000)
    assert page._session_puzzle_id == "active-rated"
    assert not rated and not replayed and not saved

    queue_dialog_click("Прервать и перейти")
    qtbot.mouseClick(page.history.viewport(), Qt.MouseButton.LeftButton,
                     pos=item_rect.center())
    qtbot.waitUntil(lambda: page.state.status.value == "skipped", timeout=2000)
    assert len(rated) == 1
    assert rated[0]["win"] is False and rated[0]["rated"] is True
    assert len(saved) == 1 and saved[0]["mode"] == "LICHESS_RATED"
    assert replayed == [("local", "history-replay", "local")]


def test_next_and_prefetch_respect_retry_after(qtbot):
    payload = {"game": {"id": "game-current", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "cooldown-current", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["fork"]}}
    next_payload = {"game": {"id": "game-next", "pgn": "1. d4 d5 2. c4"},
                    "puzzle": {"id": "cooldown-next", "initialPly": 2,
                               "solution": ["e7e6"], "themes": ["fork"]}}
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    requested = []
    page.new_puzzle_requested.connect(lambda *args: requested.append(args))
    solve_d7d5(qtbot, page)
    page.set_rate_limit_cooldown(60)
    assert not page.fetch.isEnabled()
    qtbot.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    assert requested == [] and page._session_puzzle_id == "cooldown-current"

    page.set_prepared_puzzle(
        "local", "mix", "normal", normalize_api_puzzle(next_payload), next_payload)
    page._set_controls(True, finished=True)
    assert page.fetch.isEnabled()  # prepared queue remains usable during API cooldown
    qtbot.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    assert page._session_puzzle_id == "cooldown-next"
    assert requested == []


def test_theme_search_and_frozen_attempt_filter_and_profile_placeholder(qtbot):
    payload = {"game": {"id": "game-frozen", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "frozen-theme", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["mateIn2"]}}
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page.theme_search.setText("мат")
    mate_index = next(i for i in range(page.theme_list.count())
                      if page.theme_list.item(i).data(Qt.ItemDataRole.UserRole) == "mateIn2")
    fork_index = next(i for i in range(page.theme_list.count())
                      if page.theme_list.item(i).data(Qt.ItemDataRole.UserRole) == "fork")
    assert not page.theme_list.item(mate_index).isHidden()
    assert page.theme_list.item(fork_index).isHidden()
    page.theme_search.clear()
    assert not page.theme_list.item(fork_index).isHidden()

    page.theme.setCurrentIndex(page.theme.findData("mateIn2"))
    page.difficulty.setCurrentIndex(page.difficulty.findData("normal"))
    page.set_mode("TRAINING")
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    saved = []
    page.attempt_saved.connect(saved.append)
    page.theme.setCurrentIndex(page.theme.findData("mateIn3"))
    page.difficulty.setCurrentIndex(page.difficulty.findData("harder"))
    solve_d7d5(qtbot, page)
    assert saved and saved[0]["selected_theme"] == "mateIn2"
    assert saved[0]["difficulty"] == "normal"
    assert saved[0]["mode"] == "TRAINING"

    saved_results = list(page._session_results)
    page.reset_for_account_switch()
    assert page.board.isHidden()
    assert page.board_placeholder.isVisible()
    page.set_active_account("lichess:profile-b", previous_account="local")
    page.reset_for_account_switch()
    assert page._session_results == []
    page.set_active_account("local", previous_account="lichess:profile-b")
    page.reset_for_account_switch()
    assert page._session_results == saved_results


def test_batch_duplicate_then_unique_and_exhausted_recovery():
    assert callable(select_next_api_puzzle), "PUZ05-02 needs a safe batch selector"
    current = api_payload("already-seen")
    malformed = {"puzzle": {"id": "malformed", "solution": ["bad"]}}
    fresh = api_payload("new-from-batch")
    selected = select_next_api_puzzle(
        [current, malformed, fresh], {"already-seen"}, theme="mix")
    assert selected is not None, "Batch selector must skip duplicate and malformed IDs"
    assert selected[0].puzzle_id == "new-from-batch"
    assert select_next_api_puzzle([current, malformed], {"already-seen"}) is None


def test_compact_board_reserves_space_until_first_result_feedback(qtbot):
    payload = api_payload("compact-feedback")
    page = PuzzleTrainingPage()
    page.resize(820, 620)
    qtbot.addWidget(page)
    page.show()
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    qtbot.wait(20)

    assert page.board.isVisible()
    assert not page.results_tape_scroll.isVisible()

    page._record_first_failure()

    assert page.results_tape_scroll.isVisible()
    assert page._result_tiles[-1].text() == "✕"


def test_windows_journey_uses_mouse_for_promotion_choice(qtbot):
    # This test validates a helper added with the Windows journey driver, not a
    # product behavior present on the unchanged base used by the RED job.
    from scripts.puz_ux_05_mainwindow_live_journey import play_move

    model = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
    board = BoardView(model)
    board.resize(480, 480)
    qtbot.addWidget(board)
    board.show()
    moves = []
    board.move_intent.connect(moves.append)
    page = SimpleNamespace(
        board=board,
        state=SimpleNamespace(status=SimpleNamespace(value="ready")),
        _session_puzzle_id="promotion-fixture",
    )

    play_move(page, "a7a8q")

    assert moves == [chess.Move.from_uci("a7a8q")]


def test_online_dedup_uses_local_and_imported_attempt_ids(tmp_path):
    assert hasattr(GameRepository, "practiced_puzzle_ids"), (
        "PUZ05-02 needs local and imported attempt IDs for deduplication")
    repository = GameRepository(tmp_path / "puz05-dedup.sqlite3")
    try:
        account = repository.ensure_archive_account("puz05-owner-fixture")
        repository.save_local_puzzle_attempt(
            account, "local-practice", result="success", solved=True, mistakes=0,
            hints=0, duration_ms=1,
            puzzle_json='{"puzzle":{"id":"local-practice"}}',
            attempt_id="puz05-local-attempt")
        repository.import_puzzle_activities("puz05-owner-fixture", [{
            "date": 1_795_000_000_000, "win": True,
            "puzzle": {"id": "imported-practice", "fen": chess.STARTING_FEN,
                       "solution": ["e2e4", "e7e5"], "themes": ["fork"],
                       "rating": 1500, "plays": 1},
        }])
        assert repository.practiced_puzzle_ids(account) == {
            "local-practice", "imported-practice"}
    finally:
        repository.shutdown()


def test_auto_next_checkbox_indicator_persists_and_toggles_timer(qtbot, monkeypatch):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    requests = []
    page.new_puzzle_requested.connect(lambda *args: requests.append(args))
    page.load_puzzle(normalize_api_puzzle(api_payload("auto-off")),
                     api_payload("auto-off"), source="lichess")
    assert not page.auto_next.isChecked()
    qtbot.mouseClick(page.auto_next, Qt.MouseButton.LeftButton)
    assert page.auto_next.isChecked()
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D7))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D5))
    assert page.state.status.value == "success"
    qtbot.mouseClick(page.auto_next, Qt.MouseButton.LeftButton)
    qtbot.wait(1650)
    assert not requests, "Turning auto-next off before the timer must cancel the transition"

    page.load_puzzle(normalize_api_puzzle(api_payload("auto-on")),
                     api_payload("auto-on"), source="lichess")
    qtbot.mouseClick(page.auto_next, Qt.MouseButton.LeftButton)
    solve_d7d5(qtbot, page)
    qtbot.waitUntil(lambda: bool(requests), timeout=3000)
    assert requests == [("mix", "normal")]


def test_hint_and_reveal_no_modal_and_immediate_series_feedback(qtbot, monkeypatch):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    first = api_payload("hint-streak")
    page.account_id = "lichess:fixture"
    page.load_puzzle(normalize_api_puzzle(first), first, source="lichess")
    solve_d7d5(qtbot, page)
    assert page._session_streak == 1
    second = api_payload("hint-now")
    page.load_puzzle(normalize_api_puzzle(second), second, source="lichess")
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("Hint/reveal must not show a modal question")))
    qtbot.mouseClick(page.hint, Qt.MouseButton.LeftButton)
    assert page._session_streak == 0
    assert page._result_tiles[-1].text() == "•"
    qtbot.mouseClick(page.reveal, Qt.MouseButton.LeftButton)
    assert page.state.status.value == "revealed"
    assert page._result_tiles[-1].text() == "•"
    attempt = next(item for item in page._pending_attempt_saves.values()
                   if item["puzzle_id"] == "hint-now")
    # The final action is reveal, so the persisted result is revealed while
    # retaining the hint count accumulated earlier in the attempt.
    assert attempt["result"] == "revealed" and attempt["hints"] == 1


def test_result_tile_active_attempt_safe_replay_local_source(qtbot, monkeypatch):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    first = api_payload("active-red")
    page.account_id = "lichess:fixture"
    page.load_puzzle(normalize_api_puzzle(first), first, source="lichess")
    replayed = []
    page.replay_requested.connect(lambda *args: replayed.append(args))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D7))
    qtbot.mouseClick(page.board, Qt.MouseButton.LeftButton, pos=center(page.board, chess.D6))
    qtbot.waitUntil(lambda: page._result_tiles[-1].text() == "✕")
    assert not page._result_tiles[-1].isEnabled()
    qtbot.mouseClick(page._result_tiles[-1], Qt.MouseButton.LeftButton)
    assert page._session_puzzle_id == "active-red"
    assert replayed == []

    qtbot.wait(500)
    solve_d7d5(qtbot, page)
    assert page.state.status.value == "success"
    assert page._result_tiles[-1].isEnabled()

    second = api_payload("active-next")
    page.load_puzzle(normalize_api_puzzle(second), second, source="lichess")
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(page._result_tiles[0], Qt.MouseButton.LeftButton)
    assert page.state.status.value == "skipped"
    assert len(page._pending_attempt_saves) == 2
    assert replayed == [("lichess:fixture", "active-red", "lichess")]


def test_error_keeps_finished_verdict_and_retry_cta(qtbot):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    payload = api_payload("clean-before-error")
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    solve_d7d5(qtbot, page)
    verdict = page.status.text()
    page.set_message("Lichess временно недоступен. Повторите загрузку.")
    assert page.status.text() == verdict
    assert "решена" in verdict.lower()
    assert "временно недоступен" in page.network_status.text()
    assert page.fetch.text() == "Повторить загрузку"
    assert page.state.status.value == "success"


def test_recovery_after_ten_unique_real_qt_clicks(qtbot):
    """Ten distinct API-shaped tasks are selected using visible UI input events."""
    assert callable(select_next_api_puzzle), "PUZ05-02 needs a safe batch selector"
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1050, 720)
    page.show()
    seen = {"journey-1"}
    page.load_puzzle(normalize_api_puzzle(api_payload("journey-1")),
                     api_payload("journey-1"), source="lichess")
    next_id = 2

    def supply(_theme, _difficulty):
        nonlocal next_id
        current = page._session_puzzle_id
        page.set_loading("Получаю следующую задачу Lichess…")
        batch = [api_payload(current), api_payload(f"journey-{next_id}")]
        selected = select_next_api_puzzle(batch, seen)
        assert selected is not None
        puzzle, payload = selected
        next_id += 1
        page.load_puzzle(puzzle, payload, source="lichess")

    page.new_puzzle_requested.connect(supply)
    for _ in range(9):
        solve_d7d5(qtbot, page)
        previous = page._session_puzzle_id
        qtbot.mouseClick(page.next, Qt.MouseButton.LeftButton)
        assert page._session_puzzle_id != previous
        assert page._session_puzzle_id not in seen
        assert page._session_puzzle_id.startswith("journey-")
        seen.add(page._session_puzzle_id)
    assert len(seen) == 10
    assert len({row["puzzle_id"] for row in page._session_results}) == 10
