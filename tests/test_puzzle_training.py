import chess
import pytest

from chess_coach.lichess.puzzle_training import (
    PuzzlePlayablePosition, PuzzleStatus, PuzzleTrainingState,
    normalize_activity_puzzle, normalize_api_puzzle,
)


def test_official_batch_requires_no_prep_move():
    payload = {
        "game": {"id": "fixture-game", "pgn": "1. e4 e5 2. Nf3"},
        "puzzle": {"id": "api-fixture", "initialPly": 2, "lastMove": "g1f3",
                   "solution": ["d7d5"], "themes": ["fork"], "rating": 1500,
                   "plays": 40},
    }
    normalized = normalize_api_puzzle(payload)
    board = normalized.board()
    assert board.turn == chess.BLACK
    assert board.peek() == chess.Move.from_uci("g1f3")
    assert normalized.expected_user_moves == ("d7d5",)
    assert normalized.themes == ("fork",)


def test_history_source_replays_one_prep_move():
    normalized = normalize_activity_puzzle(
        "history-fixture", chess.STARTING_FEN, ["e2e4", "e7e5"], themes=["opening"])
    board = normalized.board()
    assert board.turn == chess.BLACK
    assert board.peek() == chess.Move.from_uci("e2e4")
    assert normalized.expected_user_moves == ("e7e5",)
    assert "e2e4" not in normalized.expected_user_moves


def test_reducer_handles_multiplay_reply_and_does_not_finish_early():
    puzzle = PuzzlePlayablePosition(
        "multi", chess.STARTING_FEN, ("e2e4", "e7e5", "g1f3"), ("opening",))
    state = PuzzleTrainingState()
    state.load(puzzle)
    assert state.submit(chess.Move.from_uci("e2e4")) == "correct"
    assert state.status == PuzzleStatus.READY
    assert state.board.peek() == chess.Move.from_uci("e7e5")
    assert state.submit(chess.Move.from_uci("g1f3")) == "success"
    assert state.status == PuzzleStatus.SUCCESS


def test_wrong_move_is_retained_as_mistake_and_position_can_retry():
    state = PuzzleTrainingState()
    state.load(PuzzlePlayablePosition("retry", chess.STARTING_FEN,
                                      ("e2e4",), ("opening",)))
    start_fen = state.board.fen()
    assert state.submit(chess.Move.from_uci("d2d4")) == "mistake"
    assert state.board.fen() == start_fen
    assert state.mistakes == 1
    assert state.submit(chess.Move.from_uci("e2e4")) == "success"


def test_hints_are_staged_and_remain_counted_after_correct_move():
    state = PuzzleTrainingState()
    state.load(PuzzlePlayablePosition("hint", chess.STARTING_FEN,
                                      ("e2e4",), ("opening",)))
    assert state.hint().from_square == chess.E2
    assert state.hint().to_square == chess.E4
    assert state.hints_used == 2
    assert state.submit(chess.Move.from_uci("e2e4")) == "success"
    assert state.hints_used == 2


def test_activity_format_rejects_illegal_prep_move():
    with pytest.raises(ValueError, match="Подготовительный ход"):
        normalize_activity_puzzle("bad", chess.STARTING_FEN, ["e2e5", "e7e6"])


def test_mate_in_one_accepts_an_alternative_legal_mate():
    board = chess.Board("7k/6pp/5KQ1/8/8/8/8/8 w - - 0 1")
    state = PuzzleTrainingState()
    state.load(PuzzlePlayablePosition("mate-one", board.fen(), ("g6h6",), ("mateIn1",)))
    assert state.submit(chess.Move.from_uci("g6g7")) == "success"
    assert state.status == PuzzleStatus.SUCCESS
