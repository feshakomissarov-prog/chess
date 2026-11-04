import chess
import pytest

from chess_coach.domain.accuracy import (
    FORMULA_ID, calculate_saved_game_accuracy, move_accuracy, win_percent_cp,
)


def _fixture(*, moves="e2e4 e7e5 g1f3", rows=None, source="stockfish",
             status="mate", start_fen="startpos"):
    board = chess.Board() if start_fen == "startpos" else chess.Board(start_fen)
    positions = [board.copy(stack=False)]
    for raw in moves.split():
        move = chess.Move.from_uci(raw)
        assert move in board.legal_moves
        board.push(move)
        positions.append(board.copy(stack=False))
    if rows is None:
        rows = [{"ply": ply, "white_cp": 0, "depth": 16}
                for ply in range(len(positions))]
    line = {"moves": moves, "line_revision": 3, "line_hash": "fixture-hash"}
    game = {"status": status}
    analysis = {"source": source, "status": "complete", "line_revision": 3,
                "line_hash": "fixture-hash", "assessments": rows}
    return game, line, analysis, positions


def test_pinned_lichess_win_percent_and_move_accuracy_golden_values():
    assert win_percent_cp(0) == pytest.approx(50.0)
    assert win_percent_cp(1000) == pytest.approx(97.5447436341)
    assert win_percent_cp(-1000) == pytest.approx(2.4552563659)
    assert move_accuracy(50.0, 50.0) == 100.0
    assert move_accuracy(50.0, 0.0) == pytest.approx(9.5, abs=0.2)
    assert FORMULA_ID == "lichess-accuracy-2e653ad1-v1"


def test_saved_full_line_accuracy_is_per_side_and_zero_loss_is_stable():
    game, line, analysis, positions = _fixture()
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.values == {"white": 100.0, "black": 100.0}
    assert result.coverage == (4, 4)
    assert result.formula_id == FORMULA_ID


def test_accuracy_uses_each_movers_pov_and_harmonic_mean_handles_zero():
    game, line, analysis, positions = _fixture(rows=[
        {"ply": 0, "white_cp": 0, "depth": 20},
        {"ply": 1, "white_cp": 0, "depth": 20},
        {"ply": 2, "white_cp": -100, "depth": 20},
        {"ply": 3, "white_cp": -900, "depth": 20},
    ])
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.values["black"] == 100.0
    assert result.values["white"] < 100.0
    assert 0.0 <= result.values["white"] <= 100.0


def test_same_forced_mate_side_distance_change_is_not_a_false_accuracy_loss():
    game, line, analysis, positions = _fixture(moves="e2e4 e7e5")
    analysis["assessments"] = [
        {"ply": 0, "white_cp": 0, "depth": 20},
        {"ply": 1, "mate_white": 5, "depth": 20},
        {"ply": 2, "mate_white": 2, "depth": 20},
    ]
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.values["white"] == 100.0
    assert result.values["black"] == 100.0


def test_mate_zero_uses_side_to_move_and_lost_mating_line_is_large_loss():
    from chess_coach.domain.accuracy import _game_scores

    game, line, analysis, positions = _fixture(moves="e2e4 e7e5")
    analysis["assessments"] = [
        {"ply": 0, "white_cp": 0, "depth": 20},
        {"ply": 1, "mate_white": 0, "depth": 20},
        {"ply": 2, "mate_white": -3, "depth": 20},
    ]
    scores = _game_scores(analysis["assessments"], positions)
    assert scores[1] > 97.0  # Mate(0) after e4 means black to move is mated.
    analysis["assessments"][0] = {"ply": 0, "mate_white": 3, "depth": 20}
    analysis["assessments"][1] = {"ply": 1, "mate_white": -3, "depth": 20}
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.values["white"] < 20.0
    assert result.values["black"] == 100.0


def test_missing_sparse_stale_or_depthless_evaluations_are_not_filled_with_zero():
    game, line, analysis, positions = _fixture(rows=[
        {"ply": 0, "white_cp": 0, "depth": 20},
        {"ply": 1, "white_cp": 25, "depth": 20},
        {"ply": 2, "white_cp": None, "depth": 20},
        {"ply": 3, "white_cp": 25, "depth": 20},
    ])
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "incomplete"
    assert result.values == {"white": None, "black": None}
    assert result.coverage == (3, 4)
    analysis["assessments"][2] = {"ply": 2, "white_cp": 0, "depth": None}
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "incomplete"
    analysis["line_hash"] = "old-line"
    assert calculate_saved_game_accuracy(game, line, analysis, positions).status == "unavailable"


def test_unknown_source_active_and_no_move_lines_are_not_presented_as_accuracy():
    game, line, analysis, positions = _fixture(source="unknown")
    assert calculate_saved_game_accuracy(game, line, analysis, positions).status == "unavailable"
    game, line, analysis, positions = _fixture(status="playing")
    assert calculate_saved_game_accuracy(game, line, analysis, positions).status == "unavailable"
    game, line, analysis, positions = _fixture(moves="", rows=[
        {"ply": 0, "white_cp": 0, "depth": 20}])
    assert calculate_saved_game_accuracy(game, line, analysis, positions).status == "unavailable"
