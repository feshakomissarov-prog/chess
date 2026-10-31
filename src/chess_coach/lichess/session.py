"""Server-authoritative online game state and one-shot move intent handling."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid

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
        self.white_player: str | None = None
        self.black_player: str | None = None
        self.started_at: str | None = None
        self.status = "created"
        self.white_ms = self.black_ms = None
        self.white_inc = self.black_inc = 0
        self.clock_initial = 0
        self.clock_control = "-"
        self.rated = False
        self.winner = None
        self.draw_offered_by_opponent = False
        self.takeback_requested_by_opponent = False
        self.takeback_requested_by_me = False
        self.takeback_accept_pending = False
        self.takeback_decline_pending = False
        self.takeback_action_pending: str | None = None
        self.takeback_request_id: str | None = None
        self._own_takeback_flag_seen = False
        self._takeback_anchor: tuple[str, ...] = ()
        self.board = chess.Board()
        self.pending_uci: str | None = None
        self.pending_request_id: str | None = None
        self._pending_ply: int | None = None
        self._pending_prefix: tuple[str, ...] = ()
        self.premove: chess.Move | None = None
        self._last_clock_at = time.monotonic()

    @property
    def is_my_turn(self) -> bool:
        return self.board.turn == self.color and self.status in {"started", "playing"}

    @property
    def input_locked(self) -> bool:
        return bool(self.pending_uci or self.takeback_action_pending
                    or self.takeback_requested_by_me
                    or self.takeback_requested_by_opponent
                    or self.takeback_accept_pending or self.takeback_decline_pending)

    @property
    def render_board(self) -> chess.Board:
        """Disposable board for rendering; confirmed board/history stay untouched."""
        shown = self.board.copy(stack=True)
        if self.pending_uci:
            try:
                move = chess.Move.from_uci(self.pending_uci)
                if move in shown.legal_moves:
                    shown.push(move)
            except ValueError:
                pass
        return shown

    def apply_event(self, event: dict) -> GameSnapshot:
        kind = event.get("type")
        if kind not in {"gameState", "gameFull"}:
            return self.snapshot()
        incoming_fen = event.get("initialFen", self.initial_fen) if kind == "gameFull" else self.initial_fen
        if self.moves and incoming_fen != self.initial_fen:
            return self.snapshot()
        state = event.get("state", event)
        incoming_moves = tuple(state.get("moves", self.moves).split())
        confirmed_moves = tuple(self.moves.split())
        own_takeback_key = "wtakeback" if self.color == chess.WHITE else "btakeback"
        opponent_takeback_key = "btakeback" if self.color == chess.WHITE else "wtakeback"
        own_takeback_flag = bool(state.get(own_takeback_key, False))
        opponent_takeback_flag = bool(state.get(opponent_takeback_key, False))
        is_shorter = len(incoming_moves) < len(confirmed_moves)
        is_prefix = incoming_moves == confirmed_moves[:len(incoming_moves)]
        is_extension = incoming_moves[:len(confirmed_moves)] == confirmed_moves
        # Shortening requires explicit takeback context or a fresh gameFull resync.
        shorter_is_authoritative = (
            kind == "gameFull" or self.takeback_requested_by_me
            or self.takeback_accept_pending
        )
        if is_shorter:
            if not is_prefix:
                return self.snapshot()
            if not shorter_is_authoritative:
                # A takeback flag is a request, not acceptance. Surface the offer but
                # retain the confirmed line until this client explicitly accepts.
                if opponent_takeback_flag:
                    self.takeback_requested_by_opponent = True
                    self._last_clock_at = time.monotonic()
                return self.snapshot()
        elif confirmed_moves and not is_extension:
            return self.snapshot()
        if kind == "gameFull":
            self.initial_fen = incoming_fen
            self.rated = bool(event.get("rated", False))
            created_at = event.get("createdAt") or event.get("created_at")
            if created_at and not self.started_at:
                try:
                    self.started_at = time.strftime("%Y.%m.%d", time.gmtime(int(created_at) / 1000))
                except (TypeError, ValueError, OverflowError):
                    self.started_at = str(created_at)[:10].replace("-", ".")
            clock = event.get("clock") or {}
            self.clock_initial = int(clock.get("initial", 0) or 0)
            self.white_inc = int(clock.get("increment", 0) or 0)
            self.black_inc = self.white_inc
            self.clock_control = (f"{self.clock_initial // 1000}+{self.white_inc // 1000}"
                                  if self.clock_initial else "-")
        if kind in {"gameState", "gameFull"}:
            self.status = state.get("status", self.status)
            self.moves = " ".join(incoming_moves)
            self.white_ms = state.get("wtime", self.white_ms)
            self.black_ms = state.get("btime", self.black_ms)
            self.white_inc = state.get("winc", self.white_inc)
            self.black_inc = state.get("binc", self.black_inc)
            self.winner = state.get("winner", self.winner)
            own_draw_flag = "bdraw" if self.color == chess.WHITE else "wdraw"
            self.draw_offered_by_opponent = bool(state.get(own_draw_flag, False))
            self.takeback_requested_by_opponent = opponent_takeback_flag
            if own_takeback_flag:
                self.takeback_requested_by_me = True
                self._own_takeback_flag_seen = True
            elif self._own_takeback_flag_seen:
                self._own_takeback_flag_seen = False
                self.takeback_requested_by_me = False
                self.takeback_request_id = None
                self._takeback_anchor = ()
            elif kind == "gameFull" and self.takeback_requested_by_me:
                self.takeback_requested_by_me = False
                self.takeback_request_id = None
                self._takeback_anchor = ()

            if is_shorter:
                # A confirmed takeback is a new authoritative branch of the game.
                # Discard pending intents tied to the superseded position.
                self._clear_pending()
                self.premove = None
                self.takeback_requested_by_opponent = False
                self.takeback_requested_by_me = False
                self.takeback_accept_pending = False
                self.takeback_decline_pending = False
                self.takeback_action_pending = None
                self.takeback_request_id = None
                self._own_takeback_flag_seen = False
                self._takeback_anchor = ()
            elif self.takeback_decline_pending and not opponent_takeback_flag:
                self.takeback_decline_pending = False
                self.takeback_request_id = None
                self._takeback_anchor = ()
            elif kind == "gameFull" and self.takeback_accept_pending:
                # Reconnect gave a fresh unchanged snapshot: no accepted rewind to apply.
                self.takeback_accept_pending = False
                self.takeback_request_id = None
                self._takeback_anchor = ()
            self._rebuild_board()
            if self.pending_uci and self._pending_ply is not None:
                if (len(incoming_moves) >= self._pending_ply
                        and incoming_moves[:len(self._pending_prefix)] == self._pending_prefix
                        and incoming_moves[self._pending_ply - 1] == self.pending_uci):
                    self._clear_pending()
                elif kind == "gameFull":
                    # gameFull is a fresh snapshot after reconnect, so it resolves an
                    # ambiguous POST outcome without retrying the move.
                    self._clear_pending()
                elif self.status not in {"started", "created", "playing"}:
                    self._clear_pending()
            self._save()
        self._last_clock_at = time.monotonic()
        return self.snapshot()

    def configure_players(self, white: dict, black: dict, my_color: str) -> None:
        self.color = chess.WHITE if my_color == "white" else chess.BLACK
        self.white_player = white.get("name") or white.get("id") or "?"
        self.black_player = black.get("name") or black.get("id") or "?"
        player = black if self.color == chess.WHITE else white
        self.opponent = player.get("name") or player.get("id") or "Соперник"

    def _rebuild_board(self) -> None:
        self.board = chess.Board() if self.initial_fen == "startpos" else chess.Board(self.initial_fen)
        for uci in self.moves.split():
            self.board.push_uci(uci)

    def begin_submission(self, move: chess.Move) -> bool:
        if self.input_locked or not self.is_my_turn or move not in self.board.legal_moves:
            return False
        self.pending_uci = move.uci()
        self.pending_request_id = uuid.uuid4().hex
        self._pending_ply = len(self.moves.split()) + 1
        self._pending_prefix = tuple(self.moves.split())
        return True

    def fail_submission(self, request_id: str | None, *, definitive: bool) -> bool:
        """Clear only the matching, definitively rejected request; ambiguous POST failures stay pending."""
        if not self.pending_uci or not request_id or request_id != self.pending_request_id:
            return False
        if not definitive:
            return False
        self._clear_pending()
        return True

    def _clear_pending(self) -> None:
        self.pending_uci = None
        self.pending_request_id = None
        self._pending_ply = None
        self._pending_prefix = ()

    def queue_premove(self, move: chess.Move) -> bool:
        if (self.status not in {"started", "playing"} or self.is_my_turn
                or self.premove is not None or self.input_locked):
            return False
        piece = self.board.piece_at(move.from_square)
        if piece is None or piece.color != self.color:
            return False
        self.premove = move
        return True

    def take_ready_premove(self) -> chess.Move | None:
        if self.premove is None or not self.is_my_turn or self.input_locked:
            return None
        move, self.premove = self.premove, None
        if move not in self.board.legal_moves:
            return None
        return move

    def begin_takeback_action(self, action: str) -> str | None:
        if action not in {"offer", "accept", "decline"}:
            return None
        if (self.status not in {"started", "playing"} or self.pending_uci
                or self.takeback_action_pending):
            return None
        if action == "accept" and self.takeback_accept_pending:
            return None
        if action == "decline" and self.takeback_decline_pending:
            return None
        if action == "offer" and (not self.moves or self.takeback_requested_by_me
                                   or self.takeback_requested_by_opponent):
            return None
        if action in {"accept", "decline"} and not self.takeback_requested_by_opponent:
            return None
        self.takeback_action_pending = action
        self.takeback_request_id = uuid.uuid4().hex
        self._takeback_anchor = tuple(self.moves.split())
        if action == "offer":
            self.takeback_requested_by_me = True
            self._own_takeback_flag_seen = False
        elif action == "accept":
            self.takeback_accept_pending = True
        else:
            self.takeback_decline_pending = True
        return self.takeback_request_id

    def finish_takeback_action(self, request_id: str, status: int) -> bool:
        if request_id != self.takeback_request_id or not self.takeback_action_pending:
            return False
        action = self.takeback_action_pending
        self.takeback_action_pending = None
        if status:
            if action == "offer":
                self.takeback_requested_by_me = False
                self._own_takeback_flag_seen = False
            elif action == "accept":
                self.takeback_accept_pending = False
            else:
                self.takeback_decline_pending = False
            self.takeback_request_id = None
            self._takeback_anchor = ()
        return True

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
            self.rated, self.clock_control, self.status, self.winner,
            white_player=self.white_player, black_player=self.black_player,
            started_at=self.started_at)
