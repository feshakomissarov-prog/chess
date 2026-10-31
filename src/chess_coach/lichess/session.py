"""Server-authoritative online game state and one-shot move intent handling."""

from __future__ import annotations

from dataclasses import dataclass
import time

import chess

from .repository import GameRepository


@dataclass(frozen=True)
class GameSnapshot:
    game_id: str
    board: chess.Board
    color: chess.Color
    opponent: str
    status: str
    white_ms: int | None
    black_ms: int | None
    white_inc: int
    black_inc: int
    rated: bool
    initial_fen: str
    moves: str
    winner: str | None
    is_my_turn: bool
    draw_offered_by_opponent: bool


class LichessGameSession:
    """Only stream events mutate the board; POST success is not a confirmed move."""

    def __init__(self, game_id: str, repository: GameRepository) -> None:
        self.game_id = game_id
        self.repository = repository
        self.initial_fen = "startpos"
        self.moves = ""
        self.color = chess.WHITE
        self.opponent = "Соперник"
        self.status = "created"
        self.white_ms = self.black_ms = None
        self.white_inc = self.black_inc = 0
        self.clock_initial = 0
        self.clock_control = "-"
        self.rated = False
        self.winner = None
        self.draw_offered_by_opponent = False
        self.board = chess.Board()
        self.pending_uci: str | None = None
        self.premove: chess.Move | None = None
        self._last_clock_at = time.monotonic()

    @property
    def is_my_turn(self) -> bool:
        return self.board.turn == self.color and self.status in {"started", "playing"}

    def apply_event(self, event: dict) -> GameSnapshot:
        kind = event.get("type")
        if kind == "gameFull":
            self.initial_fen = event.get("initialFen", "startpos")
            self.rated = bool(event.get("rated", False))
            clock = event.get("clock") or {}
            self.clock_initial = int(clock.get("initial", 0) or 0)
            self.white_inc = int(clock.get("increment", 0) or 0)
            self.black_inc = self.white_inc
            self.clock_control = (f"{self.clock_initial // 1000}+{self.white_inc // 1000}"
                                  if self.clock_initial else "-")
        if kind in {"gameState", "gameFull"}:
            state = event.get("state", event)
            previous_moves = self.moves
            self.status = state.get("status", self.status)
            self.moves = state.get("moves", self.moves)
            self.white_ms = state.get("wtime", self.white_ms)
            self.black_ms = state.get("btime", self.black_ms)
            self.white_inc = state.get("winc", self.white_inc)
            self.black_inc = state.get("binc", self.black_inc)
            self.winner = state.get("winner", self.winner)
            own_draw_flag = "bdraw" if self.color == chess.WHITE else "wdraw"
            self.draw_offered_by_opponent = bool(state.get(own_draw_flag, False))
            self._rebuild_board()
            if self.pending_uci and self.pending_uci in self.moves.split():
                self.pending_uci = None
            elif self.pending_uci and self.moves != previous_moves and self.board.turn == self.color:
                # The server has advanced/refused our submission; never resend it.
                self.pending_uci = None
            self._save()
        self._last_clock_at = time.monotonic()
        return self.snapshot()

    def configure_players(self, white: dict, black: dict, my_color: str) -> None:
        self.color = chess.WHITE if my_color == "white" else chess.BLACK
        player = black if self.color == chess.WHITE else white
        self.opponent = player.get("name", player.get("id", "Соперник"))

    def _rebuild_board(self) -> None:
        self.board = chess.Board() if self.initial_fen == "startpos" else chess.Board(self.initial_fen)
        for uci in self.moves.split():
            self.board.push_uci(uci)

    def begin_submission(self, move: chess.Move) -> bool:
        if self.pending_uci is not None or not self.is_my_turn or move not in self.board.legal_moves:
            return False
        self.pending_uci = move.uci()
        return True

    def queue_premove(self, move: chess.Move) -> bool:
        if self.is_my_turn or self.premove is not None:
            return False
        piece = self.board.piece_at(move.from_square)
        if piece is None or piece.color != self.color:
            return False
        self.premove = move
        return True

    def take_ready_premove(self) -> chess.Move | None:
        if self.premove is None or not self.is_my_turn:
            return None
        move, self.premove = self.premove, None
        if move not in self.board.legal_moves:
            return None
        return move

    def finish(self, status: str, winner: str | None = None) -> None:
        self.status = status
        self.winner = winner
        self._save()

    def snapshot(self) -> GameSnapshot:
        return GameSnapshot(self.game_id, self.board.copy(), self.color, self.opponent,
            self.status, self.white_ms, self.black_ms, self.white_inc, self.black_inc,
            self.rated, self.initial_fen, self.moves, self.winner, self.is_my_turn,
            self.draw_offered_by_opponent)

    def _save(self) -> None:
        self.repository.save_snapshot(self.game_id, self.initial_fen, self.moves,
            "white" if self.color == chess.WHITE else "black", self.opponent,
            self.rated, self.clock_control, self.status, self.winner)
