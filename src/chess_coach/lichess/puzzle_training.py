"""Source-aware puzzle normalization and deterministic training state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import io
import math
from typing import Iterable

import chess
import chess.pgn


class PuzzleSource(str, Enum):
    LICHESS_NEW = "lichess_new"
    LICHESS_ACTIVITY = "lichess_activity"
    LOCAL = "chess_coach_local"


class PuzzleOutcome(str, Enum):
    SUCCESS = "success"
    MISTAKE = "mistake"
    HINTED = "hinted"
    REVEALED = "revealed"
    SKIPPED = "skipped"


def practice_rating_delta(current: int, puzzle_rating: int, score: float) -> int:
    """Transparent local Elo-like practice delta; never the official Lichess rating."""
    expected = 1.0 / (1.0 + math.pow(10.0, (int(puzzle_rating) - int(current)) / 400.0))
    return int(round(16.0 * (float(score) - expected)))


@dataclass(frozen=True)
class PuzzlePlayablePosition:
    puzzle_id: str
    player_to_move_fen: str
    expected_user_moves: tuple[str, ...]
    themes: tuple[str, ...] = ()
    rating: int | None = None
    plays: int | None = None
    source: PuzzleSource = PuzzleSource.LICHESS_NEW
    last_move: str | None = None
    game_id: str | None = None
    review_fens: tuple[str, ...] = ()
    review_san: tuple[str, ...] = ()

    def board(self) -> chess.Board:
        return chess.Board(self.player_to_move_fen)


def _validated_moves(board: chess.Board, raw_moves: Iterable[object]) -> tuple[str, ...]:
    moves: list[str] = []
    for raw in raw_moves:
        try:
            move = chess.Move.from_uci(str(raw))
        except (ValueError, TypeError) as exc:
            raise ValueError("В решении есть некорректный ход.") from exc
        if move not in board.legal_moves:
            raise ValueError("Решение содержит нелегальный шахматный ход.")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise ValueError("В задаче отсутствует решение.")
    return tuple(moves)


def select_next_local_candidate(candidates: Iterable[tuple], seen_ids: set[str]) -> tuple | None:
    """Choose the first not-yet-practiced candidate in a deterministic local set."""
    for candidate in candidates:
        first = candidate[0] if isinstance(candidate, tuple) else candidate
        puzzle_id = str(getattr(first, "puzzle_id", first))
        if puzzle_id not in seen_ids:
            return candidate
    return None


def normalize_api_puzzle(payload: dict) -> PuzzlePlayablePosition:
    """Normalize PuzzleAndGame API payload; solution[0] is a user move."""
    try:
        game = payload["game"]
        puzzle = payload["puzzle"]
        if not isinstance(game, dict) or not isinstance(puzzle, dict):
            raise TypeError
        puzzle_id = str(puzzle["id"]).strip()
        initial_ply = int(puzzle["initialPly"])
        raw_solution = puzzle["solution"]
        if not puzzle_id or not isinstance(raw_solution, list):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Ответ Lichess не содержит всех данных задачи.") from exc

    pgn = str(game.get("pgn") or "")
    parsed = chess.pgn.read_game(io.StringIO(pgn))
    if parsed is None or parsed.errors:
        raise ValueError("Не удалось разобрать исходную партию задачи.")
    moves = list(parsed.mainline_moves())
    # initialPly is a zero-based index of the opponent's preparatory move.
    # The puzzle position is the board immediately after that move.
    if initial_ply < 0 or initial_ply >= len(moves):
        raise ValueError("Индекс начальной позиции выходит за пределы партии.")
    board = parsed.board()
    review_fens = [board.fen()]
    review_san: list[str] = []
    for move in moves[:initial_ply + 1]:
        if move not in board.legal_moves:
            raise ValueError("Исходная партия содержит нелегальный ход.")
        review_san.append(f"{board.fullmove_number}{'.' if board.turn else '...'} {board.san(move)}")
        board.push(move)
        review_fens.append(board.fen())
    source_last_move = str(puzzle.get("lastMove") or "").strip() or None
    inferred_last_move = moves[initial_ply].uci()
    if source_last_move:
        try:
            supplied = chess.Move.from_uci(source_last_move)
        except ValueError as exc:
            raise ValueError("Поле последнего хода Lichess повреждено.") from exc
        if supplied != moves[initial_ply]:
            raise ValueError("Последний ход Lichess не совпадает с исходной партией.")
    expected = _validated_moves(board.copy(), raw_solution)
    return PuzzlePlayablePosition(
        puzzle_id=puzzle_id,
        player_to_move_fen=board.fen(),
        expected_user_moves=expected,
        themes=tuple(str(item) for item in puzzle.get("themes", []) if item),
        rating=int(puzzle["rating"]) if puzzle.get("rating") is not None else None,
        plays=int(puzzle["plays"]) if puzzle.get("plays") is not None else None,
        source=PuzzleSource.LICHESS_NEW,
        last_move=inferred_last_move,
        game_id=str(game.get("id") or "") or None,
        review_fens=tuple(review_fens), review_san=tuple(review_san),
    )


def normalize_activity_puzzle(puzzle_id: str, fen: str, solution: object,
                              *, themes: object = (), rating: int | None = None,
                              plays: int | None = None,
                              last_move: str | None = None) -> PuzzlePlayablePosition:
    """Normalize an official PuzzleActivity record.

    PuzzleActivity.fen is already the playable position. Its solution starts
    with the user's move; unlike the old public CSV export it has no prep move
    to apply. ``lastMove`` is metadata about the preceding game move.
    """
    try:
        board = chess.Board(str(fen))
    except (ValueError, TypeError) as exc:
        raise ValueError("Сохранённая позиция повреждена.") from exc
    if isinstance(solution, str):
        import json
        try:
            solution = json.loads(solution)
        except (ValueError, TypeError):
            solution = solution.split()
    if not isinstance(solution, (list, tuple)):
        raise ValueError("В сохранённой задаче нет последовательности ходов.")
    moves = _validated_moves(board.copy(), solution)
    theme_values = _normalize_themes(themes)
    return PuzzlePlayablePosition(
        puzzle_id=str(puzzle_id), player_to_move_fen=board.fen(),
        expected_user_moves=moves, themes=theme_values, rating=rating, plays=plays,
        source=PuzzleSource.LICHESS_ACTIVITY, last_move=(str(last_move).strip() or None)
        if last_move else None,
    )


def _normalize_themes(themes: object) -> tuple[str, ...]:
    if isinstance(themes, str):
        import json
        try:
            themes = json.loads(themes)
        except (ValueError, TypeError):
            themes = [themes]
    if not isinstance(themes, (list, tuple)):
        return ()
    return tuple(str(item) for item in themes if item)


def normalize_legacy_csv_puzzle(puzzle_id: str, fen: str, solution: object,
                                *, themes: object = (), rating: int | None = None,
                                plays: int | None = None) -> PuzzlePlayablePosition:
    """Normalize the explicitly identified legacy CSV format with prep move."""
    try:
        board = chess.Board(str(fen))
    except (ValueError, TypeError) as exc:
        raise ValueError("Сохранённая позиция повреждена.") from exc
    if isinstance(solution, str):
        import json
        try:
            solution = json.loads(solution)
        except (ValueError, TypeError):
            solution = solution.split()
    if not isinstance(solution, (list, tuple)) or not solution:
        raise ValueError("В сохранённой задаче нет последовательности ходов.")
    prep = chess.Move.from_uci(str(solution[0]))
    if prep not in board.legal_moves:
        raise ValueError("Подготовительный ход в сохранённой задаче нелегален.")
    initial_fen = board.fen()
    prep_san = f"{board.fullmove_number}{'.' if board.turn else '...'} {board.san(prep)}"
    board.push(prep)
    moves = _validated_moves(board.copy(), solution[1:])
    return PuzzlePlayablePosition(
        puzzle_id=str(puzzle_id), player_to_move_fen=board.fen(),
        expected_user_moves=moves,
        themes=_normalize_themes(themes),
        rating=rating, plays=plays, source=PuzzleSource.LICHESS_ACTIVITY,
        last_move=prep.uci(), review_fens=(initial_fen, board.fen()),
        review_san=(prep_san,),
    )


def normalize_saved_puzzle_payload(puzzle_id: str, payload: object, *,
                                   source_format: str | None = None
                                   ) -> PuzzlePlayablePosition:
    """Read a cached puzzle only when its source semantics are known."""
    target_id = str(puzzle_id or "").strip()
    if not target_id or not isinstance(payload, dict):
        raise ValueError("Не удалось открыть сохранённую задачу.")

    api_error: Exception | None = None
    try:
        playable = normalize_api_puzzle(payload)
        if playable.puzzle_id != target_id:
            raise ValueError("Сохранённый ответ относится к другой задаче.")
        return playable
    except (KeyError, TypeError, ValueError) as exc:
        api_error = exc

    if source_format not in {"lichess_activity_v1", "lichess_csv_v1"}:
        raise ValueError(
            "Не удалось безопасно восстановить сохранённую задачу: неизвестен формат источника."
        ) from api_error
    candidates = [payload.get("puzzle"), payload]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or candidate.get("puzzle_id") or target_id)
        if candidate_id != target_id:
            continue
        fen = candidate.get("fen") or payload.get("fen")
        solution = (candidate.get("solution") or candidate.get("solution_json")
                    or payload.get("solution") or payload.get("solution_json"))
        if not fen or solution is None:
            continue
        try:
            normalizer = (normalize_legacy_csv_puzzle if source_format == "lichess_csv_v1"
                          else normalize_activity_puzzle)
            return normalizer(
                target_id, str(fen), solution,
                themes=(candidate.get("themes") or payload.get("themes") or ()),
                rating=(candidate.get("rating") or payload.get("rating")),
                plays=(candidate.get("plays") or payload.get("plays")),
                **({"last_move": candidate.get("lastMove") or payload.get("lastMove")}
                   if source_format == "lichess_activity_v1" else {}),
            )
        except (TypeError, ValueError, AttributeError):
            continue
    raise ValueError("Не удалось безопасно восстановить сохранённую задачу.") from api_error


def select_next_api_puzzle(payloads: object, seen_ids: set[str], *,
                           theme: str | None = None
                           ) -> tuple[PuzzlePlayablePosition, dict] | None:
    """Return the first valid, unseen member of a bounded Lichess GET batch."""
    if not isinstance(payloads, (list, tuple)):
        return None
    seen = {str(item) for item in seen_ids}
    for payload in payloads[:5]:
        if not isinstance(payload, dict):
            continue
        try:
            playable = normalize_api_puzzle(payload)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if playable.puzzle_id in seen:
            continue
        if theme and theme != "mix" and theme not in playable.themes:
            continue
        return playable, payload
    return None


def select_api_puzzle_queue(payloads: object, seen_ids: set[str], *,
                            theme: str | None = None,
                            max_count: int = 5) -> list[tuple[PuzzlePlayablePosition, dict]]:
    """Validate and retain a bounded set of distinct, unseen batch members."""
    if not isinstance(payloads, (list, tuple)):
        return []
    limit = max(0, min(5, int(max_count)))
    seen = {str(item) for item in seen_ids}
    queued: set[str] = set()
    result: list[tuple[PuzzlePlayablePosition, dict]] = []
    for payload in payloads[:50]:
        if len(result) >= limit:
            break
        if not isinstance(payload, dict):
            continue
        try:
            playable = normalize_api_puzzle(payload)
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if playable.puzzle_id in seen or playable.puzzle_id in queued:
            continue
        if theme and theme != "mix" and theme not in playable.themes:
            continue
        queued.add(playable.puzzle_id)
        result.append((playable, payload))
    return result


class PuzzleStatus(str, Enum):
    LOADING = "loading"
    READY = "ready"
    OPPONENT_REPLY = "opponent_reply"
    SUCCESS = "success"
    MISTAKE_RETRY = "mistake_retry"
    REVEALED = "revealed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    REVIEW = "review"


@dataclass
class PuzzleTrainingState:
    puzzle: PuzzlePlayablePosition | None = None
    board: chess.Board = field(default_factory=chess.Board)
    solution_index: int = 0
    status: PuzzleStatus = PuzzleStatus.LOADING
    mistakes: int = 0
    hint_level: int = 0
    hints_used: int = 0
    history: list[str] = field(default_factory=list)
    first_failure: bool = False
    _pending_reply: str | None = field(default=None, repr=False)
    _review_snapshots: list[chess.Board] = field(default_factory=list, repr=False)
    _review_labels: list[str] = field(default_factory=list, repr=False)
    _review_cursor: int = field(default=0, repr=False)
    _reviewing: bool = field(default=False, repr=False)
    _review_return_status: PuzzleStatus | None = field(default=None, repr=False)

    def load(self, puzzle: PuzzlePlayablePosition) -> None:
        self.puzzle = puzzle
        self.board = puzzle.board()
        self.solution_index = 0
        self.status = PuzzleStatus.READY
        self.mistakes = 0
        self.hint_level = 0
        self.hints_used = 0
        self.history.clear()
        self.first_failure = False
        self._pending_reply = None
        fens = puzzle.review_fens or (puzzle.player_to_move_fen,)
        self._review_snapshots = [chess.Board(fen) for fen in fens]
        self._review_labels = ["Позиция до задачи"]
        self._review_labels.extend(puzzle.review_san)
        self._review_cursor = max(0, len(self._review_snapshots) - 1)
        self._reviewing = False
        self._review_return_status = None

    @property
    def is_reviewing(self) -> bool:
        return self._reviewing

    @property
    def review_count(self) -> int:
        return len(self._review_snapshots)

    @property
    def review_cursor(self) -> int:
        return self._review_cursor

    @property
    def review_label(self) -> str:
        return self._review_labels[self._review_cursor] if self._review_labels else ""

    def navigate_review(self, direction: int) -> chess.Board:
        """Browse only positions already visible or present before this puzzle."""
        if not self._review_snapshots:
            self._review_snapshots = [self.board.copy(stack=True)]
            self._review_labels = ["Позиция до задачи"]
        if not self._reviewing:
            self._review_return_status = self.status
        self._reviewing = True
        self.status = PuzzleStatus.REVIEW
        self._review_cursor = max(
            0, min(len(self._review_snapshots) - 1, self._review_cursor + int(direction)))
        if int(direction) > 0 and self._review_cursor == len(self._review_snapshots) - 1:
            return self.return_to_solution()
        return self._review_snapshots[self._review_cursor].copy(stack=True)

    def return_to_solution(self) -> chess.Board:
        self._reviewing = False
        self._review_cursor = max(0, len(self._review_snapshots) - 1)
        if self.puzzle is not None and self.status == PuzzleStatus.REVIEW:
            self.status = self._review_return_status or (
                PuzzleStatus.SUCCESS if self.solution_index >= len(
                    self.puzzle.expected_user_moves) else
                PuzzleStatus.OPPONENT_REPLY if self._pending_reply else
                PuzzleStatus.MISTAKE_RETRY if self.mistakes else PuzzleStatus.READY)
        self._review_return_status = None
        return self.board.copy(stack=True)

    def _record_visible_move(self, move: chess.Move) -> None:
        san = self.board.san(move)
        label = f"{self.board.fullmove_number}{'.' if self.board.turn else '...'} {san}"
        self.board.push(move)
        self._review_snapshots.append(self.board.copy(stack=True))
        self._review_labels.append(label)
        self._review_cursor = len(self._review_snapshots) - 1

    def submit(self, move: chess.Move) -> str:
        if self._reviewing or self.status not in {PuzzleStatus.READY, PuzzleStatus.MISTAKE_RETRY}:
            return "ignored"
        if move not in self.board.legal_moves:
            return "illegal"
        expected = self.puzzle.expected_user_moves[self.solution_index]
        alternative_mate = False
        if (move.uci() != expected and self.solution_index == 0
                and len(self.puzzle.expected_user_moves) == 1
                and "mateIn1" in self.puzzle.themes):
            trial = self.board.copy()
            trial.push(move)
            alternative_mate = trial.is_checkmate()
        if move.uci() != expected and not alternative_mate:
            self.mistakes += 1
            self.first_failure = True
            self.status = PuzzleStatus.MISTAKE_RETRY
            if PuzzleOutcome.MISTAKE.value not in self.history:
                self.history.append(PuzzleOutcome.MISTAKE.value)
            return "mistake"
        self._record_visible_move(move)
        self.solution_index = (len(self.puzzle.expected_user_moves)
                               if alternative_mate else self.solution_index + 1)
        self.hint_level = 0
        if self.solution_index >= len(self.puzzle.expected_user_moves):
            self.status = PuzzleStatus.SUCCESS
            self.history.append(PuzzleOutcome.SUCCESS.value)
            return "success"
        self.status = PuzzleStatus.OPPONENT_REPLY
        self._pending_reply = self.puzzle.expected_user_moves[self.solution_index]
        return "correct"

    def apply_opponent_reply(self) -> str:
        if self.status != PuzzleStatus.OPPONENT_REPLY or not self._pending_reply:
            return "ignored"
        reply = chess.Move.from_uci(self._pending_reply)
        if reply not in self.board.legal_moves:
            self.status = PuzzleStatus.UNAVAILABLE
            self._pending_reply = None
            return "unavailable"
        self._record_visible_move(reply)
        self._pending_reply = None
        self.solution_index += 1
        if self.solution_index >= len(self.puzzle.expected_user_moves):
            self.status = PuzzleStatus.SUCCESS
            self.history.append(PuzzleOutcome.SUCCESS.value)
            return "success"
        self.status = PuzzleStatus.READY
        return "reply"

    def hint(self) -> chess.Move | None:
        if self.status not in {PuzzleStatus.READY, PuzzleStatus.MISTAKE_RETRY} or not self.puzzle:
            return None
        if self.hint_level >= 2:
            return None
        self.hint_level = min(2, self.hint_level + 1)
        self.hints_used += 1
        self.history.append(PuzzleOutcome.HINTED.value)
        return chess.Move.from_uci(self.puzzle.expected_user_moves[self.solution_index])

    def reveal(self) -> None:
        if self.puzzle:
            while self.solution_index < len(self.puzzle.expected_user_moves):
                move = chess.Move.from_uci(self.puzzle.expected_user_moves[self.solution_index])
                if move not in self.board.legal_moves:
                    break
                self._record_visible_move(move)
                self.solution_index += 1
            self.status = PuzzleStatus.REVEALED
            self.history.append(PuzzleOutcome.REVEALED.value)

    def skip(self) -> None:
        if self.status not in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED}:
            self.status = PuzzleStatus.SKIPPED
            self.history.append(PuzzleOutcome.SKIPPED.value)
