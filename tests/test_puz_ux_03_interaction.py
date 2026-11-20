import chess
import time
import pytest
from PySide6.QtCore import QTimer

from chess_coach.lichess.puzzle_training import (
    PuzzleOutcome, PuzzlePlayablePosition, PuzzleStatus, PuzzleTrainingState,
)
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage


def puzzle(puzzle_id="play", moves=("e2e4",), rating=1500):
    return PuzzlePlayablePosition(
        puzzle_id, chess.STARTING_FEN, tuple(moves), ("fork",), rating=rating,
    )


def online_page(qtbot, moves=("e2e4",), puzzle_id="play"):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.load_puzzle(puzzle(puzzle_id, moves), {"puzzle": {
        "id": puzzle_id, "rating": 1500, "solution": list(moves), "themes": ["fork"]
    }}, source="lichess")
    return page


def test_wrong_legal_move_is_visible_then_reverted_without_changing_authority(qtbot):
    page = online_page(qtbot)
    page.show()
    qtbot.waitExposed(page)
    start_fen = page.state.board.fen()
    page._submit_move(chess.Move.from_uci("d2d4"))
    assert page.board._board.piece_at(chess.D4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert page.state.board.fen() == start_fen
    assert "Неверный" in page.status.text()
    qtbot.waitUntil(page._wrong_timer.isActive)
    assert not page._wrong_timer_pending
    assert page._wrong_flash
    qtbot.wait(520)
    assert page.board._board.piece_at(chess.D2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert page.state.status == PuzzleStatus.MISTAKE_RETRY


def test_correct_user_move_and_opponent_reply_are_separate_qt_steps(qtbot):
    page = online_page(qtbot, ("e2e4", "e7e5", "g1f3"), "multi")
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert page.board._board.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert page.board._board.piece_at(chess.E5) is None
    assert page.state.status == PuzzleStatus.OPPONENT_REPLY
    assert page.board._anim_timer.isActive()
    assert "Верно" in page.status.text()
    qtbot.wait(700)
    assert page.board._board.piece_at(chess.E5) == chess.Piece(chess.PAWN, chess.BLACK)
    assert page.board._anim_timer.isActive()
    assert page.state.status == PuzzleStatus.READY


def test_save_ack_does_not_replace_terminal_verdict(qtbot):
    page = online_page(qtbot)
    saved = []
    page.attempt_saved.connect(saved.append)
    page._submit_move(chess.Move.from_uci("e2e4"))
    attempt = saved[-1]
    assert page.status.text().startswith("✓ Задача решена")
    page._attempt_save_completed(attempt["attempt_id"], True, {"recent": []})
    assert page.status.text().startswith("✓ Задача решена")


def test_replay_success_does_not_promise_auto_next_or_change_any_session_stats(qtbot):
    page = online_page(qtbot)
    page.load_puzzle(page.state.puzzle, page._puzzle_payload, source="replay",
                     rating_eligible=False)
    attempts = []
    page.attempt_saved.connect(attempts.append)
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert page.status.text() == "✓ Повтор решён. Рейтинг не изменён."
    assert "автоматически" not in page.status.text().casefold()
    assert not page._auto_next_timer.isActive()
    assert attempts == []
    assert page._session_results == []
    assert (page._session_streak, page._session_clean, page._session_failed,
            page._session_hinted) == (0, 0, 0, 0)


@pytest.mark.parametrize("terminal", ["skip", "reveal"])
def test_first_failure_stays_red_after_skip_or_reveal(qtbot, terminal):
    page = online_page(qtbot, puzzle_id=f"wrong-{terminal}")
    page._submit_move(chess.Move.from_uci("d2d4"))
    assert page._session_failed == 1
    assert page._session_results[-1]["mark"] == "✕"

    if terminal == "skip":
        page.state.skip()
        page._finish(PuzzleOutcome.SKIPPED)
    else:
        page.state.reveal()
        page._finish(PuzzleOutcome.REVEALED)

    assert page._session_failed == 1
    assert page._session_results[-1]["mark"] == "✕"
    assert "ошибка" in page._session_results[-1]["label"].casefold()


def test_clean_skip_is_neutral_and_hint_then_skip_is_not_clean(qtbot):
    clean = online_page(qtbot, puzzle_id="clean-skip")
    clean.state.skip()
    clean._finish(PuzzleOutcome.SKIPPED)
    assert clean._session_results[-1]["mark"] == "–"
    assert clean._session_failed == clean._session_clean == 0

    hinted = online_page(qtbot, puzzle_id="hint-skip")
    hinted._hint()
    hinted.state.skip()
    hinted._finish(PuzzleOutcome.SKIPPED)
    assert hinted._session_results[-1]["mark"] == "–"
    assert hinted._session_failed == hinted._session_clean == 0


def test_review_wheel_shows_only_played_moves_and_return_restores_current_board(qtbot):
    page = online_page(qtbot, ("e2e4", "e7e5", "g1f3"), "review")
    page._submit_move(chess.Move.from_uci("e2e4"))
    qtbot.wait(700)
    current_fen = page.state.board.fen()
    page.board.wheel_navigate.emit(-1)
    assert page.board._board.piece_at(chess.E4) is not None
    assert page.board._board.piece_at(chess.E5) is None
    assert page.state.board.fen() == current_fen
    assert "Просмотр" in page.status.text()
    page.board.wheel_navigate.emit(1)
    assert page.board._board.fen() == current_fen
    assert "вернитесь к решению" in page.status.text().casefold()
    assert not page.return_to_solution.isHidden()


def test_normal_training_source_has_no_saved_or_offline_choices(qtbot):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    values = [page.source.itemData(i) for i in range(page.source.count())]
    assert values == ["new"]


def test_hint_stages_explain_limit_and_disqualify_clean_win(qtbot):
    page = online_page(qtbot)
    page._hint()
    assert page.hint.text() == "Подсказка (1/2)"
    assert page.board._hint_squares == {chess.E2}
    assert not page.board._hint_arrows
    page._hint()
    assert page.hint.text() == "Подсказка (2/2)"
    assert page.board._hint_arrows == {(chess.E2, chess.E4)}
    page._hint()
    assert "Максимум подсказок" in page.status.text()
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert "с подсказкой" in page.status.text().casefold()


def test_clean_online_success_auto_advances_but_failed_or_hinted_stays(qtbot):
    page = online_page(qtbot, puzzle_id="auto-next")
    page.auto_next.setChecked(True)
    requested = []
    page.new_puzzle_requested.connect(lambda *args: requested.append(args))
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert page.status.text().startswith("✓ Задача решена")
    qtbot.waitUntil(lambda: bool(requested), timeout=2500)
    assert requested == [("mix", "normal")]

    hinted = online_page(qtbot, puzzle_id="hinted")
    hinted.auto_next.setChecked(True)
    hinted._hint()
    hinted_requested = []
    hinted.new_puzzle_requested.connect(lambda *args: hinted_requested.append(args))
    hinted._submit_move(chess.Move.from_uci("e2e4"))
    qtbot.wait(1500)
    assert not hinted_requested


def test_first_failure_breaks_streak_once_and_hint_result_is_not_clean(qtbot):
    page = online_page(qtbot)
    page._session_streak = 4
    page._submit_move(chess.Move.from_uci("d2d4"))
    assert page._session_streak == 0
    assert page._session_failed == 1
    qtbot.wait(460)
    page._submit_move(chess.Move.from_uci("g1f3"))
    assert page._session_failed == 1
    qtbot.wait(460)
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert page._session_clean == 0
    assert page._session_failed == 1
    assert page.results_tape.text().count("✕") == 1


def test_three_second_save_does_not_delay_clean_auto_next(qtbot):
    page = online_page(qtbot, puzzle_id="slow-save")
    page.auto_next.setChecked(True)
    attempt = []
    page.attempt_saved.connect(attempt.append)
    requested = []
    page.new_puzzle_requested.connect(lambda *args: requested.append(args))
    timer = QTimer(page)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: page._attempt_save_completed(
        attempt[-1]["attempt_id"], True, {"practice_rating": {
            "rating": 1508, "delta": 8, "rated_attempts": 1,
        }}))
    page.attempt_saved.connect(lambda _value: timer.start(3000))
    page._submit_move(chess.Move.from_uci("e2e4"))
    qtbot.waitUntil(lambda: bool(requested), timeout=2200)
    assert page.status.text().startswith("✓ Задача решена")
    assert page.persistence_status.text().startswith("● Сохраняется")
    assert page.network_status.text().startswith("Загружаем следующую")
    qtbot.waitUntil(lambda: page.persistence_status.text().startswith("✓ Сохранено"),
                    timeout=1800)


def test_failed_save_queue_keeps_multiple_attempts_and_late_ack_status_scoped(qtbot):
    page = online_page(qtbot, puzzle_id="save-one")
    emitted = []
    page.attempt_saved.connect(emitted.append)
    page._submit_move(chess.Move.from_uci("e2e4"))
    first = emitted[-1]
    page._attempt_save_completed(first["attempt_id"], False, error="disk busy")

    second_position = puzzle("save-two", ("d2d4",))
    page.load_puzzle(second_position, {"puzzle": {"id": "save-two", "rating": 1500,
                                                  "solution": ["d2d4"]}},
                     source="lichess")
    page._submit_move(chess.Move.from_uci("d2d4"))
    second = emitted[-1]
    page._attempt_save_completed(second["attempt_id"], False, error="database locked")
    current_status = page.persistence_status.text()
    assert "database locked" in current_status
    assert not page.retry_save.isHidden()
    assert "(2)" in page.retry_save.text()

    page._retry_attempt_save()
    assert emitted[-1]["attempt_id"] == first["attempt_id"]
    page._attempt_save_completed(first["attempt_id"], True, {"recent": []})
    assert page.persistence_status.text() == current_status
    assert not page.retry_save.isHidden()
    page._retry_attempt_save()
    assert emitted[-1]["attempt_id"] == second["attempt_id"]
    page._attempt_save_completed(second["attempt_id"], True, {"recent": []})
    assert page.persistence_status.text() == "✓ Сохранено в SQLite"
    assert page.retry_save.isHidden()


def test_prepared_online_next_is_consumed_without_waiting_for_a_second_get(qtbot):
    page = online_page(qtbot, puzzle_id="prepared-current")
    page.account_id = "lichess:prepared-user"
    page.auto_next.setChecked(False)
    page._submit_move(chess.Move.from_uci("e2e4"))
    assert page.state.status == PuzzleStatus.SUCCESS
    prepared = puzzle("prepared-next", ("d2d4",), rating=1700)
    requested = []
    page.new_puzzle_requested.connect(lambda *args: requested.append(args))
    page.set_prepared_puzzle(
        page.account_id, "mix", "normal", prepared,
        {"puzzle": {"id": "prepared-next", "rating": 1700,
                    "solution": ["d2d4"], "themes": ["fork"]}},
        {"live_get_ms": 1200, "rating_eligible": True})

    started = time.perf_counter()
    page._next()

    assert page.state.puzzle.puzzle_id == "prepared-next"
    assert not requested
    assert page._prepared_next_ms is not None
    assert page._prepared_next_ms < 250
    assert (time.perf_counter() - started) < 0.250


def test_replayed_local_and_lichess_activity_are_interactive_but_unrated(qtbot):
    import pytest

    for source in ("local", "lichess_activity"):
        page = PuzzleTrainingPage()
        qtbot.addWidget(page)
        attempts = []
        page.attempt_saved.connect(attempts.append)
        page.load_puzzle(puzzle(f"replay-{source}"), {"puzzle": {
            "id": f"replay-{source}", "rating": 1500, "solution": ["e2e4"],
            "themes": ["fork"],
        }}, source=source)

        assert page.board._interaction_enabled
        assert page._allow_submit
        page._submit_move(chess.Move.from_uci("e2e4"))

        assert page.state.status == PuzzleStatus.SUCCESS
        assert attempts == []
        assert page._session_streak == 0
        assert page._session_results == []
        assert page.persistence_status.text() == "Повтор не записывается"
        assert "без рейтинга" in page.turn_label.text().casefold() or page._is_replay


def test_review_roundtrip_preserves_terminal_and_retry_statuses():
    cases = {
        PuzzleStatus.SUCCESS: ("e2e4",),
        PuzzleStatus.REVEALED: ("e2e4",),
        PuzzleStatus.SKIPPED: ("e2e4",),
        PuzzleStatus.UNAVAILABLE: ("e2e4", "a1a8", "d2d4"),
        PuzzleStatus.MISTAKE_RETRY: ("e2e4",),
    }
    for expected, moves in cases.items():
        state = PuzzleTrainingState()
        state.load(puzzle(f"review-{expected.value}", moves))
        if expected == PuzzleStatus.SUCCESS:
            assert state.submit(chess.Move.from_uci("e2e4")) == "success"
        elif expected == PuzzleStatus.REVEALED:
            state.reveal()
        elif expected == PuzzleStatus.SKIPPED:
            state.skip()
        elif expected == PuzzleStatus.UNAVAILABLE:
            assert state.submit(chess.Move.from_uci("e2e4")) == "correct"
            assert state.apply_opponent_reply() == "unavailable"
        else:
            assert state.submit(chess.Move.from_uci("d2d4")) == "mistake"
        assert state.status == expected

        state.navigate_review(-1)
        state.return_to_solution()

        assert state.status == expected


def test_review_return_restores_the_visible_terminal_verdict(qtbot):
    page = online_page(qtbot, puzzle_id="review-verdict")
    page.state.reveal()
    page._finish(PuzzleOutcome.REVEALED)
    assert "Решение показано" in page.status.text()

    page._navigate_review(-1)
    page._return_to_solution()

    assert page.state.status == PuzzleStatus.REVEALED
    assert "Решение показано" in page.status.text()
    assert not page.board._interaction_enabled
