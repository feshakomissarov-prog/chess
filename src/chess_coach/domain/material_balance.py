"""Material difference for a confirmed standard-chess position."""

from __future__ import annotations

import chess


_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


def material_balance(board: chess.Board, player_color: chess.Color | None = None) -> int | None:
    """Return white-minus-black or player-perspective material, never an evaluation.

    Invalid and Chess960 boards are deliberately unsupported so callers do not
    present a plausible-looking number for a position with unknown semantics.
    """
    if not isinstance(board, chess.Board) or board.chess960 or not board.is_valid():
        return None
    white = sum(len(board.pieces(piece_type, chess.WHITE)) * value
                for piece_type, value in _VALUES.items())
    black = sum(len(board.pieces(piece_type, chess.BLACK)) * value
                for piece_type, value in _VALUES.items())
    difference = white - black
    return difference if player_color is None or player_color == chess.WHITE else -difference


def format_material_balance(value: int | None, *, player_color: chess.Color | None) -> str:
    if value is None:
        return "Материал: н/д"
    signed = f"{value:+d}" if value else "0"
    perspective = "за вас" if player_color is not None else "Белые − Чёрные"
    return f"Материал: {signed} · {perspective}"
