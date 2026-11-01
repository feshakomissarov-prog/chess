"""Authoritative local game state, kept independent from the board widget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import chess


@dataclass(frozen=True)
class PositionSnapshot:
    board: chess.Board
    ply: int
    live: bool


class GameController:
    """Owns the only mutable game board and validates all move intents."""

    def __init__(self, player_color: chess.Color = chess.WHITE) -> None:
        self.player_color = player_color
        self.board = chess.Board()
        self.view_ply: Optional[int] = None
        self.premove: Optional[chess.Move] = None
        self.last_premove_result: Optional[str] = None
        self.premove_training = False

    @property
    def ply(self) -> int:
        return len(self.board.move_stack)

    @property
    def viewing_live_position(self) -> bool:
        return self.view_ply is None or self.view_ply == self.ply

    def snapshot(self) -> PositionSnapshot:
        ply = self.ply if self.view_ply is None else self.view_ply
        board = self.board.root()
        for move in self.board.move_stack[:ply]:
            board.push(move)
        return PositionSnapshot(board=board, ply=ply, live=self.viewing_live_position)

    def apply_move_intent(self, move: chess.Move) -> bool:
        if not self.viewing_live_position or self.board.is_game_over(claim_draw=True):
            return False
        if move not in self.board.legal_moves:
            return False
        self.board.push(move)
        self.view_ply = None
        return True

    def queue_premove(self, move: chess.Move) -> bool:
        """Queue one intent for player's color while the opponent is to move."""
        if (self.premove_training and self.viewing_live_position
                and self.board.turn != self.player_color):
            piece = self.board.piece_at(move.from_square)
            if piece is None or piece.color != self.player_color:
                return False
            if move.from_square == move.to_square:
                return False
            self.premove = move
            self.last_premove_result = None
            return True
        return False

    def cancel_premove(self) -> None:
        self.premove = None

    def set_premove_training(self, enabled: bool) -> bool:
        """Toggle the local premove exercise without changing the played line.

        Returns whether a queued intent was discarded by the mode change.
        """
        enabled = bool(enabled)
        discarded = not enabled and self.premove is not None
        self.premove_training = enabled
        if not enabled:
            self.cancel_premove()
        return discarded

    def undo_last_move(self) -> bool:
        """Undo one confirmed local ply; online history is server-owned."""
        if not self.board.move_stack:
            return False
        self.board.pop()
        self.view_ply = None
        self.cancel_premove()
        self.last_premove_result = None
        return True

    def apply_test_opponent_move(self, move: chess.Move) -> tuple[bool, str]:
        """Apply a user-selected legal opponent move, then revalidate premove."""
        if (not self.premove_training or not self.viewing_live_position
                or self.board.turn == self.player_color):
            return False, "Сейчас не ход соперника в тестовом режиме."
        if move not in self.board.legal_moves:
            return False, "Этот ход соперника недопустим."
        self.board.push(move)
        result = "Ход соперника выполнен."
        queued = self.premove
        self.premove = None
        if queued is not None:
            matching = [
                candidate
                for candidate in self.board.legal_moves
                if candidate.from_square == queued.from_square
                and candidate.to_square == queued.to_square
                and (queued.promotion is None or candidate.promotion == queued.promotion)
            ]
            if matching:
                self.board.push(matching[0])
                self.last_premove_result = "Предход выполнен."
                result = self.last_premove_result
            else:
                self.last_premove_result = "Предход отменён: недопустим."
                result = self.last_premove_result
        return True, result

    def go_to(self, ply: int) -> None:
        self.view_ply = max(0, min(ply, self.ply))
        self.cancel_premove()
        if self.viewing_live_position:
            self.view_ply = None

    def step_view(self, delta: int) -> None:
        current = self.ply if self.view_ply is None else self.view_ply
        self.go_to(current + delta)

    def go_live(self) -> None:
        self.view_ply = None

    def new_game(self) -> None:
        self.board = chess.Board()
        self.view_ply = None
        self.cancel_premove()
        self.last_premove_result = None

    def san_moves(self) -> list[tuple[int, str, str]]:
        board = self.board.root()
        rows: list[tuple[int, str, str]] = []
        for index in range(0, self.ply, 2):
            white = board.san(self.board.move_stack[index])
            board.push(self.board.move_stack[index])
            black = ""
            if index + 1 < self.ply:
                black = board.san(self.board.move_stack[index + 1])
                board.push(self.board.move_stack[index + 1])
            rows.append((index // 2 + 1, white, black))
        return rows
