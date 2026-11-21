"""PUZ-UX-05 regressions for source, NEXT and ordinary puzzle gestures."""

import chess
import chess_coach.lichess.puzzle_training as puzzle_training_module
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

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
    old_shape = {"puzzle": {"id": "saved-legacy", "fen": LEGACY_FEN,
                             "solution": ["c7c5", "g1f3"], "themes": ["opening"]}}
    playable = normalize_saved_puzzle_payload("saved-legacy", old_shape)
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


def test_batch_duplicate_then_unique_and_exhausted_recovery():
    current = api_payload("already-seen")
    malformed = {"puzzle": {"id": "malformed", "solution": ["bad"]}}
    fresh = api_payload("new-from-batch")
    selected = select_next_api_puzzle(
        [current, malformed, fresh], {"already-seen"}, theme="mix")
    assert selected is not None, "Batch selector must skip duplicate and malformed IDs"
    assert selected[0].puzzle_id == "new-from-batch"
    assert select_next_api_puzzle([current, malformed], {"already-seen"}) is None


def test_online_dedup_uses_local_and_imported_attempt_ids(tmp_path):
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
    assert len(page._pending_attempt_saves) == 1


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
        seen.add(puzzle.puzzle_id)

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
