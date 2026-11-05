import chess
import pytest

from chess_coach.domain.accuracy import (
    FORMULA_ID, _accuracy_by_side, assessment_win_percent,
    calculate_saved_game_accuracy, move_accuracy, win_percent_cp,
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
    assert FORMULA_ID == "lichess-accuracy-a46b4e6d9d80a100-v2"
    assert move_accuracy(win_percent_cp(100), 50.0) == pytest.approx(67.2397212280)
    assert move_accuracy(100.0 - win_percent_cp(100), 50.0) == pytest.approx(100.0)


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


def test_mate_zero_requires_legal_terminal_and_terminal_missing_eval_is_derived():
    from chess_coach.domain.accuracy import _game_scores

    _game, _line, _analysis, nonterminal = _fixture(moves="e2e4 e7e5")
    with pytest.raises(ValueError, match="nonterminal_mate_zero"):
        _game_scores([{"ply": i, "mate_white": 0} for i in range(3)], nonterminal)

    game, line, analysis, positions = _fixture(
        moves="f2f3 e7e5 g2g4 d8h4", rows=[
            {"ply": 0, "white_cp": 0, "depth": 20},
            {"ply": 1, "white_cp": 0, "depth": 20},
            {"ply": 2, "white_cp": 0, "depth": 20},
            {"ply": 3, "white_cp": 0, "depth": 20},
            {"ply": 4, "mate_white": 0, "depth": 20},
        ])
    assert positions[-1].is_checkmate() and positions[-1].turn == chess.WHITE
    assert _game_scores(analysis["assessments"], positions)[-1] < 3.0
    analysis["assessments"][-1] = {"ply": 4, "depth": None}
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.values == {"white": 100.0, "black": 100.0}
    assert result.terminal_positions_derived == 1


def test_terminal_checkmate_without_engine_row_is_derived_with_terminal_provenance():
    game, line, analysis, positions = _fixture(
        moves="f2f3 e7e5 g2g4 d8h4", rows=[
            {"ply": 0, "white_cp": 0, "depth": 20},
            {"ply": 1, "white_cp": 0, "depth": 20},
            {"ply": 2, "white_cp": 0, "depth": 20},
            {"ply": 3, "white_cp": 0, "depth": 20},
        ])
    # Missing terminal score is permitted only from a legally checkmated board.
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.coverage == (4, 5)
    assert result.values["white"] is not None and result.values["black"] is not None


def test_black_to_move_start_position_has_correct_mover_pov():
    from chess_coach.domain.accuracy import move_accuracy_from_assessments

    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
    after = board.copy()
    after.push_uci("e7e5")
    value = move_accuracy_from_assessments(
        {"white_cp": 100}, {"white_cp": 0}, mover="black",
        before_position=board, after_position=after)
    assert value == pytest.approx(100.0)


@pytest.mark.parametrize(("plies", "white", "black"), (
    (1, 100.0, None),
    (2, 100.0, 90.5370918670),
    (10, 100.0, 67.8470035651),
    (50, 47.5970062125, 75.5806019810),
    (100, 48.0, 75.4637809630),
))
def test_scala_accuracy_window_golden_parity(plies, white, black):
    scores = [win_percent_cp(((ply * 137) % 2001) - 1000)
              for ply in range(plies + 1)]
    values = _accuracy_by_side(scores, "white")
    assert values["white"] == pytest.approx(white, abs=1e-9)
    if black is None:
        assert values["black"] is None
    else:
        assert values["black"] == pytest.approx(black, abs=1e-9)


def test_black_to_move_full_game_and_mate_zero_mirror_are_pov_correct():
    game, line, analysis, positions = _fixture(
        moves="e7e5 e2e4", start_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
        rows=[{"ply": ply, "white_cp": value, "depth": 18}
              for ply, value in enumerate((100, 0, 100))])
    result = calculate_saved_game_accuracy(game, line, analysis, positions)
    assert result.status == "complete"
    assert result.values == {"white": 100.0, "black": 100.0}

    board = chess.Board()
    for uci in "f2f3 e7e5 g2g4 d8h4".split():
        board.push_uci(uci)
    assert board.is_checkmate()
    assert assessment_win_percent({"mate_white": 0}, board) == pytest.approx(
        win_percent_cp(-1000))
    mirrored = board.mirror()
    assert mirrored.is_checkmate()
    assert assessment_win_percent({"mate_white": 0}, mirrored) == pytest.approx(
        win_percent_cp(1000))


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
