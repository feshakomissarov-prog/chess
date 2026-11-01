"""Explicit, conservative phase boundaries inferred from board material/development."""

from __future__ import annotations

import chess


PHASE_FALLBACK_VERSION = "cc-phase-material-v1"


def infer_phase_boundaries(boards: list[chess.Board]) -> tuple[int | None, int | None]:
    """Infer phase starts from positions; never use an arbitrary move-number cutoff.

    ``boards`` includes the initial position at index/ply 0. The method is a rough,
    provenance-labeled fallback: middlegame starts after both sides develop at least
    two minor pieces or the queens are exchanged. Endgame starts only when both queens
    are gone and at most four rooks/minors remain on the board.
    """
    middle: int | None = None
    end: int | None = None
    home_minor_squares = {
        chess.WHITE: {chess.B1, chess.C1, chess.F1, chess.G1},
        chess.BLACK: {chess.B8, chess.C8, chess.F8, chess.G8},
    }
    for ply, board in enumerate(boards):
        developed = {}
        for color in (chess.WHITE, chess.BLACK):
            developed[color] = sum(
                1 for piece_type in (chess.KNIGHT, chess.BISHOP)
                for square in board.pieces(piece_type, color)
                if square not in home_minor_squares[color]
            )
        if middle is None and (all(developed[color] >= 2 for color in (chess.WHITE, chess.BLACK))
                               or (not board.pieces(chess.QUEEN, chess.WHITE)
                                   and not board.pieces(chess.QUEEN, chess.BLACK))):
            middle = ply
        remaining = sum(len(board.pieces(piece_type, color))
                        for piece_type in (chess.ROOK, chess.BISHOP, chess.KNIGHT)
                        for color in (chess.WHITE, chess.BLACK))
        if (end is None and not board.pieces(chess.QUEEN, chess.WHITE)
                and not board.pieces(chess.QUEEN, chess.BLACK) and remaining <= 4):
            end = ply
    return middle, end


def phase_at_ply(middle_start: int | None, end_start: int | None, ply: int) -> str:
    if middle_start is not None and ply < middle_start:
        return "opening"
    if end_start is not None and ply >= end_start:
        return "endgame"
    if middle_start is not None and ply >= middle_start:
        return "middlegame"
    return "unknown"
