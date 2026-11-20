"""Source-aware puzzle normalization and deterministic training state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import io
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
    for move in moves[:initial_ply + 1]:
        if move not in board.legal_moves:
            raise ValueError("Исходная партия содержит нелегальный ход.")
        board.push(move)
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
    )


def normalize_activity_puzzle(puzzle_id: str, fen: str, solution: object,
                              *, themes: object = (), rating: int | None = None,
                              plays: int | None = None) -> PuzzlePlayablePosition:
    """Normalize the historical CSV/activity format whose first move is prep."""
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
    board.push(prep)
    moves = _validated_moves(board.copy(), solution[1:])
    theme_values = themes
    if isinstance(theme_values, str):
        import json
        try:
            theme_values = json.loads(theme_values)
        except (ValueError, TypeError):
            theme_values = [theme_values]
    if not isinstance(theme_values, (list, tuple)):
        theme_values = ()
    return PuzzlePlayablePosition(
        puzzle_id=str(puzzle_id), player_to_move_fen=board.fen(),
        expected_user_moves=moves,
        themes=tuple(str(item) for item in theme_values if item),
        rating=rating, plays=plays, source=PuzzleSource.LICHESS_ACTIVITY,
        last_move=prep.uci(),
    )


class PuzzleStatus(str, Enum):
    LOADING = "loading"
    READY = "ready"
    OPPONENT_REPLY = "opponent_reply"
    SUCCESS = "success"
    MISTAKE_RETRY = "mistake_retry"
    REVEALED = "revealed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


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

    def load(self, puzzle: PuzzlePlayablePosition) -> None:
        self.puzzle = puzzle
        self.board = puzzle.board()
        self.solution_index = 0
        self.status = PuzzleStatus.READY
        self.mistakes = 0
        self.hint_level = 0
        self.hints_used = 0

    def submit(self, move: chess.Move) -> str:
        if self.status not in {PuzzleStatus.READY, PuzzleStatus.MISTAKE_RETRY}:
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
            self.status = PuzzleStatus.MISTAKE_RETRY
            self.history.append(PuzzleOutcome.MISTAKE.value)
            return "mistake"
        self.board.push(move)
        self.solution_index = (len(self.puzzle.expected_user_moves)
                               if alternative_mate else self.solution_index + 1)
        self.hint_level = 0
        if self.solution_index >= len(self.puzzle.expected_user_moves):
            self.status = PuzzleStatus.SUCCESS
            self.history.append(PuzzleOutcome.SUCCESS.value)
            return "success"
        reply = chess.Move.from_uci(self.puzzle.expected_user_moves[self.solution_index])
        if reply not in self.board.legal_moves:
            self.status = PuzzleStatus.UNAVAILABLE
            return "unavailable"
        self.board.push(reply)
        self.solution_index += 1
        if self.solution_index >= len(self.puzzle.expected_user_moves):
            self.status = PuzzleStatus.SUCCESS
            self.history.append(PuzzleOutcome.SUCCESS.value)
            return "success"
        self.status = PuzzleStatus.READY
        return "correct"

    def hint(self) -> chess.Move | None:
        if self.status not in {PuzzleStatus.READY, PuzzleStatus.MISTAKE_RETRY} or not self.puzzle:
            return None
        self.hint_level = min(2, self.hint_level + 1)
        self.hints_used += 1
        self.history.append(PuzzleOutcome.HINTED.value)
        return chess.Move.from_uci(self.puzzle.expected_user_moves[self.solution_index])

    def reveal(self) -> None:
        if self.puzzle:
            self.status = PuzzleStatus.REVEALED
            self.history.append(PuzzleOutcome.REVEALED.value)

    def skip(self) -> None:
        if self.status not in {PuzzleStatus.SUCCESS, PuzzleStatus.REVEALED}:
            self.status = PuzzleStatus.SKIPPED
            self.history.append(PuzzleOutcome.SKIPPED.value)
