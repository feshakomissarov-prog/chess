"""Server-authoritative online game state and one-shot move intent handling."""

from __future__ import annotations

from dataclasses import dataclass
import time
import uuid
from typing import Any

import chess

from .repository import GameRepository


LICHESS_GAME_SOURCES = frozenset({
    "lobby", "friend", "ai", "api", "tournament", "position", "import",
    "importlive", "simul", "relay", "pool", "arena", "swiss",
})
LICHESS_PLAYER_TITLES = frozenset({
    "GM", "WGM", "IM", "WIM", "FM", "WFM", "NM", "CM", "WCM", "WNM", "LM",
})

# Lichess reports all live clock values in milliseconds. These bounds reject
# unit mixups/corrupt payloads without discarding legitimate correspondence games.
_MAX_CLOCK_MS = 365 * 24 * 60 * 60 * 1000
_MAX_INCREMENT_MS = 3 * 60 * 60 * 1000
_UNKNOWN_CONTROL_SAFE_MS = 24 * 60 * 60 * 1000
_CLOCK_EVENT_TOLERANCE_MS = 2_000


def classify_lichess_opponent(game: Any) -> str:
    """Classify a server game conservatively for fair-play decisions.

    Only Lichess's explicit AI source/signature is the built-in AI exception.
    BOT titles identify account opponents and are never an engine-safety allow.
    """
    if not isinstance(game, dict):
        return "unknown"
    source = game.get("source")
    opponent = game.get("opponent")
    if (not isinstance(source, str) or source not in LICHESS_GAME_SOURCES
            or not isinstance(opponent, dict)):
        return "unknown"

    title = opponent.get("title")
    if title is not None and not isinstance(title, str):
        return "unknown"
    if source == "ai":
        level = opponent.get("ai")
        if ("id" in opponent and opponent.get("id") is None
                and type(level) is int and 1 <= level <= 8
                and ("title" not in opponent or title is None)):
            return "official_ai"
        return "unknown"

    # AI-shaped or null-identity records under another source are contradictory.
    if "ai" in opponent or "id" not in opponent:
        return "unknown"
    opponent_id = opponent.get("id")
    username = opponent.get("username")
    if (not isinstance(opponent_id, str) or not opponent_id.strip()
            or not isinstance(username, str) or not username.strip()):
        return "unknown"
    if title == "BOT":
        return "bot_account"
    if title is not None and title not in LICHESS_PLAYER_TITLES:
        return "unknown"
    return "human"


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
        # Cached sessions survive reconnects, but remain bound to the account
        # that created them. A session without an owner is never eligible for
        # online actions or Fair Play decisions.
        self.owner_username: str | None = None
        self.auth_generation: int | None = None
        # Unowned is unknown, not detached: safety consumers treat active
        # unknown sessions conservatively while presentation stays account-scoped.
        self.detached = False
        self.initial_fen = "startpos"
        self.moves = ""
        self.color = chess.WHITE
        self.opponent = "Соперник"
        self.opponent_title: str | None = None
        self.game_source: str | None = None
        self.opponent_info: dict | None = None
        self.white_player: str | None = None
        self.black_player: str | None = None
        self.started_at: str | None = None
        self.status = "created"
        self.white_ms = self.black_ms = None
        self.white_inc = self.black_inc = 0
        self.clock_initial = 0
        self.clock_control = "-"
        self.clock_state_valid: bool | None = None
        self.clock_metadata_invalid = False
        self.speed: str | None = None
        self.days_per_turn: int | None = None
        self._clock_ply_seen: int | None = None
        self.rated = False
        self.rated_metadata_valid = False
        self.winner = None
        self.final_sync_pending = False
        # A newly opened game is not playable until its stream supplies and we
        # validate a complete authoritative gameFull snapshot.
        self.awaiting_authoritative_snapshot = True
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
        self._last_persisted_signature: tuple | None = None
        self._last_clock_at = time.monotonic()

    def bind_owner(self, username: str, generation: int) -> None:
        username = str(username or "").strip().casefold()
        if not username:
            raise ValueError("session owner username is required")
        if self.owner_username not in (None, username):
            raise ValueError("session cannot be reassigned to a different Lichess account")
        self.owner_username = username
        self.auth_generation = int(generation)
        self.detached = False

    def detach(self) -> None:
        self.detached = True
        self.awaiting_authoritative_snapshot = True
        self.pending_uci = None
        self.pending_request_id = None
        self._pending_ply = None
        self._pending_prefix = ()
        self.premove = None

    def belongs_to(self, username: str | None, *, generation: int | None = None) -> bool:
        account = str(username or "").strip().casefold()
        if not account or self.detached or self.owner_username != account:
            return False
        return generation is None or self.auth_generation == int(generation)

    @property
    def is_my_turn(self) -> bool:
        return (not self.awaiting_authoritative_snapshot
                and self.board.turn == self.color
                and self.status in {"started", "playing"})

    @property
    def is_ai_opponent(self) -> bool:
        return classify_lichess_opponent({
            "source": self.game_source,
            "opponent": self.opponent_info,
        }) == "official_ai"

    def set_server_game_identity(self, game: dict) -> None:
        """Store the immutable opponent identity from gameStart/account-playing."""
        speed = game.get("speed")
        if isinstance(speed, str):
            self.speed = speed
        days = game.get("daysPerTurn")
        self.days_per_turn = days if type(days) is int and 1 <= days <= 14 else None
        source = game.get("source")
        self.game_source = source if isinstance(source, str) else None
        opponent = game.get("opponent")
        self.opponent_info = dict(opponent) if isinstance(opponent, dict) else None
        self.opponent = "Соперник"
        self.opponent_title = None
        if self.opponent_info:
            username = self.opponent_info.get("username")
            opponent_id = self.opponent_info.get("id")
            ai_level = self.opponent_info.get("ai")
            official_ai_name = (f"Lichess AI · уровень {ai_level}"
                                if self.is_ai_opponent else None)
            self.opponent = (official_ai_name
                             or (username if isinstance(username, str) and username else None)
                             or (opponent_id if isinstance(opponent_id, str) and opponent_id else None)
                             or "Неизвестный соперник")
            title = self.opponent_info.get("title")
            self.opponent_title = title if isinstance(title, str) else None

    @property
    def input_locked(self) -> bool:
        return bool(self.awaiting_authoritative_snapshot
                    or self.pending_uci or self.takeback_action_pending
                    or self.takeback_requested_by_me
                    or self.takeback_requested_by_opponent
                    or self.takeback_accept_pending or self.takeback_decline_pending)

    @property
    def can_queue_speculative_premove(self) -> bool:
        """A single premove may be entered on the optimistic board while our POST awaits ACK."""
        if (not self.pending_uci or self.premove is not None
                or self.awaiting_authoritative_snapshot
                or self.status not in {"started", "playing"}
                or self.takeback_action_pending or self.takeback_requested_by_me
                or self.takeback_requested_by_opponent):
            return False
        return self.render_board.turn != self.color

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

    accepted_snapshot_sequence = 0

    def apply_event(self, event: dict) -> GameSnapshot:
        kind = event.get("type")
        if kind not in {"gameState", "gameFull"}:
            return self.snapshot()
        if kind == "gameFull":
            state = event.get("state")
            initial_fen = event.get("initialFen")
            if (not isinstance(state, dict) or not isinstance(state.get("moves"), str)
                    or not isinstance(state.get("status"), str)
                    or not isinstance(initial_fen, str) or not initial_fen):
                raise ValueError("Lichess не прислал полный начальный снимок партии.")
        incoming_fen = event.get("initialFen", self.initial_fen) if kind == "gameFull" else self.initial_fen
        if self.moves and incoming_fen != self.initial_fen:
            return self.snapshot()
        state = event.get("state", event)
        if not isinstance(state, dict) or not isinstance(state.get("moves"), str):
            raise ValueError("Lichess не прислал список ходов.")
        incoming_moves = tuple(state["moves"].split())
        confirmed_moves = tuple(self.moves.split())
        if self.final_sync_pending:
            return self._apply_final_sync_event(event, kind, state, incoming_fen,
                                                incoming_moves, confirmed_moves)
        if self.status not in {"created", "started", "playing", "started_soon"}:
            return self.snapshot()
        own_takeback_key = "wtakeback" if self.color == chess.WHITE else "btakeback"
        opponent_takeback_key = "btakeback" if self.color == chess.WHITE else "wtakeback"
        own_takeback_flag = bool(state.get(own_takeback_key, False))
        opponent_takeback_flag = bool(state.get(opponent_takeback_key, False))
        is_shorter = len(incoming_moves) < len(confirmed_moves)
        is_prefix = incoming_moves == confirmed_moves[:len(incoming_moves)]
        is_extension = incoming_moves[:len(confirmed_moves)] == confirmed_moves
        if (self.pending_uci and kind == "gameState" and incoming_moves
                and not is_prefix and not is_extension):
            # A diverging stream is not a reason to retry an ambiguous POST, but
            # the speculative intent can no longer be safely tied to this line.
            self._clear_pending()
            self.premove = None
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
                return self.snapshot()
        elif confirmed_moves and not is_extension:
            return self.snapshot()
        if incoming_fen == self.initial_fen and not is_shorter and is_extension:
            board = self.board.copy(stack=True)
            remaining = incoming_moves[len(confirmed_moves):]
        else:
            board = chess.Board() if incoming_fen == "startpos" else chess.Board(incoming_fen)
            remaining = incoming_moves
        for uci in remaining:
            board.push_uci(uci)
        if kind == "gameFull":
            self._apply_game_full_metadata(event, incoming_fen)
        if kind in {"gameState", "gameFull"}:
            self.status = state.get("status", self.status)
            self.moves = " ".join(incoming_moves)
            if kind == "gameFull" and not any(key in state for key in ("wtime", "btime")):
                self.white_ms = self.black_ms = None
                self.clock_state_valid = None
                self._clock_ply_seen = None
            if is_shorter:
                # A server-confirmed takeback starts a new clock-line baseline.
                self._clock_ply_seen = None
                self.clock_state_valid = None
            self._apply_clock_state(state, reanchor=kind == "gameFull")
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
            self.board = board
            if kind == "gameFull":
                self.awaiting_authoritative_snapshot = False
            if self.pending_uci and self._pending_ply is not None:
                if (len(incoming_moves) >= self._pending_ply
                        and incoming_moves[:len(self._pending_prefix)] == self._pending_prefix
                        and incoming_moves[self._pending_ply - 1] == self.pending_uci):
                    self._clear_pending()
                elif (len(incoming_moves) >= self._pending_ply
                      and incoming_moves[:len(self._pending_prefix)] == self._pending_prefix):
                    # The authoritative server selected another move at the
                    # pending ply. Never resend or execute its dependent premove.
                    self._clear_pending()
                    self.premove = None
                elif kind == "gameFull":
                    # gameFull is a fresh snapshot after reconnect, so it resolves an
                    # ambiguous POST outcome without retrying the move.
                    self._clear_pending()
                    self.premove = None
                elif self.status not in {"started", "created", "playing"}:
                    self._clear_pending()
            self.accepted_snapshot_sequence += 1
            self._save()
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

    def _apply_game_full_metadata(self, event: dict, incoming_fen: str) -> None:
        self.initial_fen = incoming_fen
        rated = event.get("rated")
        self.rated_metadata_valid = type(rated) is bool
        if self.rated_metadata_valid:
            self.rated = rated
        # A reconnect may start with gameFull and never repeat gameStart. Preserve
        # the server's time-control classification from either authoritative event.
        speed = event.get("speed")
        if isinstance(speed, str) and speed in {
                "bullet", "blitz", "rapid", "classical", "correspondence", "unlimited"}:
            self.speed = speed
        days = event.get("daysPerTurn")
        if type(days) is int and 1 <= days <= 14:
            self.days_per_turn = days
        created_at = event.get("createdAt") or event.get("created_at")
        if created_at and not self.started_at:
            try:
                self.started_at = time.strftime("%Y.%m.%d", time.gmtime(int(created_at) / 1000))
            except (TypeError, ValueError, OverflowError):
                self.started_at = str(created_at)[:10].replace("-", ".")
        clock = event.get("clock") or {}
        initial = clock.get("initial") if isinstance(clock, dict) else None
        increment = clock.get("increment") if isinstance(clock, dict) else None
        valid_initial = (type(initial) is int and 0 < initial <= _MAX_CLOCK_MS)
        valid_increment = (type(increment) is int and 0 <= increment <= _MAX_INCREMENT_MS)
        self.clock_initial = initial if valid_initial else 0
        self.white_inc = increment if valid_increment else 0
        self.black_inc = self.white_inc
        if valid_initial and valid_increment:
            self.clock_control = f"{initial // 1000}+{increment // 1000}"
            self.clock_metadata_invalid = False
        elif initial is None and increment is None:
            self.clock_control = "-"
            self.white_ms = self.black_ms = None
            self.clock_state_valid = None
            self.clock_metadata_invalid = False
        else:
            self.clock_control = "-"
            self.white_ms = self.black_ms = None
            self.clock_state_valid = False
            self.clock_metadata_invalid = True

    def can_interpolate_clock(self) -> bool:
        """Extrapolate only once the opening timer has started."""
        if self.clock_state_valid is not True or self._clock_ply_seen != len(self.moves.split()):
            return False
        # Lichess starts ordinary games' clocks after the second ply. An
        # authoritative deduction can also establish an earlier forced start.
        active_ms = self.white_ms if self.board.turn == chess.WHITE else self.black_ms
        return self._clock_ply_seen >= 2 or (
            self.clock_initial > 0 and active_ms is not None and active_ms < self.clock_initial
        )

    def _apply_clock_state(self, state: dict, *, reanchor: bool = False) -> None:
        """Validate live millisecond snapshots against the confirmed line and increments."""
        supplied = {"wtime": state.get("wtime", self.white_ms),
                    "btime": state.get("btime", self.black_ms),
                    "winc": state.get("winc", self.white_inc),
                    "binc": state.get("binc", self.black_inc)}
        times = (supplied["wtime"], supplied["btime"])
        increments = (supplied["winc"], supplied["binc"])
        has_times = any(name in state for name in ("wtime", "btime"))
        if not has_times:
            return
        if any(type(value) is not int or value < 0 or value > _MAX_CLOCK_MS
               for value in times):
            self.clock_state_valid = False
            self.clock_metadata_invalid = True
            return
        if any(type(value) is not int or value < 0 or value > _MAX_INCREMENT_MS
               for value in increments):
            self.clock_state_valid = False
            self.clock_metadata_invalid = True
            return
        plies = len(self.moves.split())
        white_moves = (plies + 1) // 2
        black_moves = plies // 2
        if self.clock_initial:
            white_upper = min(_MAX_CLOCK_MS, self.clock_initial
                              + self.white_inc * white_moves + _CLOCK_EVENT_TOLERANCE_MS)
            black_upper = min(_MAX_CLOCK_MS, self.clock_initial
                              + self.black_inc * black_moves + _CLOCK_EVENT_TOLERANCE_MS)
        else:
            white_upper = black_upper = _UNKNOWN_CONTROL_SAFE_MS
        if times[0] > white_upper or times[1] > black_upper:
            self.clock_state_valid = False
            self.clock_metadata_invalid = True
            return

        # Repeated / delayed stream frames may carry an older clock snapshot for
        # the same line. Keep the latest accepted values; do not mark a valid game
        # as corrupt just because a stale frame arrived after it.
        previous_ply = self._clock_ply_seen
        if (self.clock_state_valid is True and previous_ply is not None
                and self.white_ms is not None and self.black_ms is not None):
            if plies < previous_ply:
                return
            if plies == previous_ply and not reanchor:
                if times[0] > self.white_ms or times[1] > self.black_ms:
                    return
                if times == (self.white_ms, self.black_ms):
                    return
            elif plies > previous_ply:
                white_advanced = (plies + 1) // 2 - (previous_ply + 1) // 2
                black_advanced = plies // 2 - previous_ply // 2
                white_gain = self.white_inc * white_advanced
                black_gain = self.black_inc * black_advanced
                if (times[0] > self.white_ms + white_gain + _CLOCK_EVENT_TOLERANCE_MS
                        or times[1] > self.black_ms + black_gain + _CLOCK_EVENT_TOLERANCE_MS):
                    return
        self.white_ms, self.black_ms = times
        self.white_inc, self.black_inc = increments
        self.clock_state_valid = True
        self.clock_metadata_invalid = False
        self._clock_ply_seen = plies
        self._last_clock_at = time.monotonic()

    def _apply_final_sync_event(self, event: dict, kind: str, state: dict,
                                incoming_fen: str, incoming_moves: tuple[str, ...],
                                confirmed_moves: tuple[str, ...]) -> GameSnapshot:
        """Accept only a terminal, monotonic server snapshot after gameFinish."""
        incoming_status = state.get("status")
        if incoming_status in {None, "created", "started", "started_soon", "playing"}:
            return self.snapshot()
        if (len(incoming_moves) < len(confirmed_moves)
                or incoming_moves[:len(confirmed_moves)] != confirmed_moves):
            return self.snapshot()
        board = chess.Board() if incoming_fen == "startpos" else chess.Board(incoming_fen)
        for uci in incoming_moves:
            board.push_uci(uci)
        if kind == "gameFull":
            self._apply_game_full_metadata(event, incoming_fen)
        self.initial_fen = incoming_fen
        self.moves = " ".join(incoming_moves)
        self.status = str(incoming_status)
        if "winner" in state:
            self.winner = state.get("winner")
        self._apply_clock_state(state, reanchor=kind == "gameFull")
        self.board = board
        if kind == "gameFull":
            self.awaiting_authoritative_snapshot = False
        self.final_sync_pending = False
        self._clear_pending()
        self.premove = None
        self.accepted_snapshot_sequence += 1
        self._save()
        return self.snapshot()

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
        self.premove = None
        return True

    def _clear_pending(self) -> None:
        self.pending_uci = None
        self.pending_request_id = None
        self._pending_ply = None
        self._pending_prefix = ()

    def clear_unconfirmed_move_intent(self) -> None:
        """Drop UI-only move intent on disconnect without retrying an ambiguous POST."""
        self._clear_pending()
        self.premove = None

    def queue_premove(self, move: chess.Move) -> bool:
        speculative = self.can_queue_speculative_premove
        if (self.status not in {"started", "playing"}
                or (self.is_my_turn and not speculative)
                or self.premove is not None
                or (self.input_locked and not speculative)):
            return False
        board = self.render_board if speculative else self.board
        piece = board.piece_at(move.from_square)
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
                or self.awaiting_authoritative_snapshot
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
        if self.status not in {"created", "started", "playing", "started_soon"}:
            return
        self.status = status
        if winner is not None:
            self.winner = winner
        self.final_sync_pending = True
        self._clear_pending()
        self.premove = None
        self._save()

    def snapshot(self) -> GameSnapshot:
        return GameSnapshot(self.game_id, self.board.copy(), self.color, self.opponent,
            self.status, self.white_ms, self.black_ms, self.white_inc, self.black_inc,
            self.rated, self.initial_fen, self.moves, self.winner, self.is_my_turn,
            self.draw_offered_by_opponent)

    def _save(self) -> None:
        signature = (
            self.initial_fen, self.moves, self.color, self.opponent, self.rated,
            self.clock_control, self.status, self.winner, self.white_player,
            self.black_player, self.started_at, self.final_sync_pending,
        )
        if (signature == self._last_persisted_signature
                and not self.repository.has_write_error(self.game_id)):
            return
        self.repository.save_snapshot(self.game_id, self.initial_fen, self.moves,
            "white" if self.color == chess.WHITE else "black", self.opponent,
            self.rated, self.clock_control, self.status, self.winner,
            white_player=self.white_player, black_player=self.black_player,
            started_at=self.started_at, final_sync_pending=self.final_sync_pending,
            account_username=self.owner_username)
        self._last_persisted_signature = signature
