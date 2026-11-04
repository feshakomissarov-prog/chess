"""Deterministic, version-pinned Lichess-method accuracy from saved evaluations.

This is an educational local estimate. It is never written over official Lichess
accuracy and it refuses incomplete, stale, unsupported, or depth-unknown runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import chess


FORMULA_ID = "lichess-accuracy-2e653ad1-v1"
SUPPORTED_SOURCES = {"stockfish", "lichess-pgn", "lichess-json"}
ACTIVE_STATUSES = {"created", "started", "started_soon", "playing"}


@dataclass(frozen=True)
class AccuracyResult:
    values: dict[str, float | None]
    status: str
    reason: str
    formula_id: str = FORMULA_ID
    source: str | None = None
    coverage: tuple[int, int] = (0, 0)
    depth_range: tuple[int, int] | None = None


def win_percent_cp(cp: int) -> float:
    """Lichess/scalachess centipawn WinPercent, capped at +/-1000cp."""
    cp = max(-1000, min(1000, int(cp)))
    chance = 2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0
    return max(0.0, min(100.0, 50.0 + 50.0 * chance))


def move_accuracy(before: float, after: float) -> float:
    """Lichess AccuracyPercent.fromWinPercents including its +1 bonus."""
    if after >= before:
        return 100.0
    diff = before - after
    raw = (103.1668100711649 * math.exp(-0.04354415386753951 * diff)
           - 3.166924740191411 + 1.0)
    return max(0.0, min(100.0, raw))


def _standard_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _harmonic_mean(values: list[float]) -> float | None:
    if not values:
        return None
    if any(value <= 0.0 for value in values):
        return 0.0
    return len(values) / sum(1.0 / value for value in values)


def _game_scores(rows: list[dict[str, Any]], positions: list[chess.Board]) -> list[float]:
    scores: list[float] = []
    for ply, row in enumerate(rows):
        if row.get("white_cp") is not None:
            scores.append(win_percent_cp(int(row["white_cp"])))
            continue
        mate = row.get("mate_white")
        if mate is None:
            raise ValueError("missing_score")
        mate = int(mate)
        if mate == 0:
            # Lichess Mate(0) means the side to move is mated.
            white_wins = positions[ply].turn == chess.BLACK
            scores.append(win_percent_cp(1000 if white_wins else -1000))
        else:
            scores.append(win_percent_cp(1000 if mate > 0 else -1000))
    return scores


def _accuracy_by_side(win_percents: list[float], start_color: str) -> dict[str, float | None]:
    move_count = max(0, len(win_percents) - 1)
    if not move_count:
        return {"white": None, "black": None}
    window_size = max(2, min(8, move_count // 10))
    bounded_size = min(window_size, len(win_percents))
    window_count = max(0, bounded_size - 2)
    windows = [win_percents[:window_size] for _ in range(window_count)]
    windows.extend(win_percents[index:index + window_size]
                   for index in range(max(0, len(win_percents) - window_size + 1)))
    weights = [max(0.5, min(12.0, _standard_deviation(window))) for window in windows]

    grouped: dict[str, list[tuple[float, float]]] = {"white": [], "black": []}
    for index, (before_white, after_white) in enumerate(zip(win_percents, win_percents[1:])):
        white_moves = ((index % 2 == 0) == (start_color == "white"))
        color = "white" if white_moves else "black"
        before = before_white if color == "white" else 100.0 - before_white
        after = after_white if color == "white" else 100.0 - after_white
        grouped[color].append((move_accuracy(before, after), weights[index]))

    result: dict[str, float | None] = {}
    for color, pairs in grouped.items():
        if not pairs:
            result[color] = None
            continue
        total_weight = sum(weight for _, weight in pairs)
        weighted = sum(value * weight for value, weight in pairs) / total_weight
        harmonic = _harmonic_mean([value for value, _ in pairs])
        result[color] = (weighted + harmonic) / 2.0 if harmonic is not None else None
    return result


def calculate_saved_game_accuracy(game: dict[str, Any], line: dict[str, Any],
                                  analysis: dict[str, Any] | None,
                                  positions: list[chess.Board]) -> AccuracyResult:
    """Calculate both sides only from a complete, same-run scored current line."""
    source = str((analysis or {}).get("source") or "") or None
    ply_count = len(str(line.get("moves") or "").split())
    expected = ply_count + 1
    if str(game.get("status") or "") in ACTIVE_STATUSES:
        return AccuracyResult({"white": None, "black": None}, "unavailable",
                              "Партия ещё не завершена.", source=source, coverage=(0, expected))
    if source not in SUPPORTED_SOURCES or not analysis:
        return AccuracyResult({"white": None, "black": None}, "unavailable",
                              "Нет совместимого источника позиционных оценок.",
                              source=source, coverage=(0, expected))
    if (int(analysis.get("line_revision") or -1) != int(line.get("line_revision") or -2)
            or str(analysis.get("line_hash") or "") != str(line.get("line_hash") or "")):
        return AccuracyResult({"white": None, "black": None}, "unavailable",
                              "Оценки относятся к другой версии линии.", source=source,
                              coverage=(0, expected))
    by_ply = {int(row["ply"]): row for row in analysis.get("assessments", [])}
    covered = [by_ply.get(ply) for ply in range(expected)]
    scored = sum(row is not None and
                 (row.get("white_cp") is not None or row.get("mate_white") is not None)
                 for row in covered)
    if len(positions) != expected or scored != expected:
        return AccuracyResult({"white": None, "black": None}, "incomplete",
                              "Нужны оценки всех позиций, включая исходную; охват неполный.",
                              source=source, coverage=(scored, expected))
    depths = [int(row["depth"]) for row in covered if row.get("depth") is not None]
    if len(depths) != expected:
        return AccuracyResult({"white": None, "black": None}, "incomplete",
                              "Глубина одного или нескольких сохранённых оценок неизвестна.",
                              source=source, coverage=(scored, expected))
    if not ply_count:
        return AccuracyResult({"white": None, "black": None}, "unavailable",
                              "В партии нет ходов для расчёта.", source=source,
                              coverage=(scored, expected))
    try:
        scores = _game_scores([dict(row) for row in covered], positions)
    except (ValueError, TypeError, IndexError):
        return AccuracyResult({"white": None, "black": None}, "incomplete",
                              "Сохранённые оценки нельзя сопоставить с позициями.",
                              source=source, coverage=(scored, expected))
    start_color = "white" if positions[0].turn == chess.WHITE else "black"
    return AccuracyResult(_accuracy_by_side(scores, start_color), "complete",
                          "Локальный расчёт, методика Lichess; неофициальное значение.",
                          source=source, coverage=(scored, expected),
                          depth_range=(min(depths), max(depths)))
