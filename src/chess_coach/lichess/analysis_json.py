"""Validated normalization of documented Lichess finished-game JSON analytics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ANALYTICS_SCHEMA_VERSION = "lichess-game-json-v1"
_PLAYER_COUNTS = ("inaccuracy", "mistake", "blunder", "acpl")
_PHASES = ("opening", "middlegame", "endgame")


@dataclass(frozen=True)
class NormalizedGameAnalytics:
    accuracy: dict[str, int | None]
    player_analysis: dict[str, dict[str, Any]]
    assessments: tuple[dict[str, Any], ...]
    move_annotations: tuple[dict[str, Any], ...]
    array_present: bool
    array_complete: bool
    issues: tuple[str, ...]

    @property
    def has_accuracy(self) -> bool:
        return any(value is not None for value in self.accuracy.values())


def _integer(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def normalize_game_analytics(payload: dict, expected_plies: int) -> NormalizedGameAnalytics:
    """Normalize only the documented players.*.analysis and GameJson.analysis fields.

    Move-analysis array slot ``i`` belongs to ply ``i + 1``. Missing or malformed
    entries retain their slot; they never shift subsequent scores onto another move.
    """
    expected_plies = max(0, int(expected_plies))
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, dict):
        players = {}
    issues: list[str] = []
    accuracy: dict[str, int | None] = {}
    player_analysis: dict[str, dict[str, Any]] = {}

    for color in ("white", "black"):
        player = players.get(color)
        player = player if isinstance(player, dict) else {}
        raw = player.get("analysis")
        raw = raw if isinstance(raw, dict) else {}
        normalized: dict[str, Any] = {}
        score = _integer(raw.get("accuracy"), minimum=0, maximum=100)
        accuracy[color] = score
        if score is not None:
            normalized["accuracy"] = score
        if "accuracy" in raw and score is None:
            issues.append(f"invalid_{color}_accuracy")
        for key in _PLAYER_COUNTS:
            if key in raw:
                item = _integer(raw.get(key), minimum=0, maximum=10_000_000)
                if item is None:
                    issues.append(f"invalid_{color}_{key}")
                else:
                    normalized[key] = item
        phases = raw.get("phases")
        if phases is not None:
            if not isinstance(phases, dict):
                issues.append(f"invalid_{color}_phases")
            else:
                phase_values = {}
                for phase in _PHASES:
                    if phase in phases:
                        value = _integer(phases.get(phase), minimum=0, maximum=100)
                        if value is None:
                            issues.append(f"invalid_{color}_{phase}_accuracy")
                        else:
                            phase_values[phase] = value
                if phase_values:
                    normalized["phases"] = phase_values
        player_analysis[color] = normalized

    raw_analysis = payload.get("analysis") if isinstance(payload, dict) else None
    array_present = "analysis" in payload if isinstance(payload, dict) else False
    assessments: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    array_complete = True
    if array_present:
        if not isinstance(raw_analysis, list):
            issues.append("analysis_not_array")
            array_complete = False
        else:
            if len(raw_analysis) != expected_plies:
                issues.append("analysis_length_mismatch")
                array_complete = False
            if len(raw_analysis) > expected_plies:
                issues.append("analysis_has_extra_plies")
            for index, entry in enumerate(raw_analysis[:expected_plies]):
                if entry is None:
                    array_complete = False
                    continue
                if not isinstance(entry, dict):
                    issues.append(f"invalid_analysis_ply_{index + 1}")
                    array_complete = False
                    continue
                has_eval = "eval" in entry
                has_mate = "mate" in entry
                if has_eval and has_mate:
                    issues.append(f"conflicting_score_ply_{index + 1}")
                    array_complete = False
                    continue
                item: dict[str, Any] = {"ply": index + 1, "white_cp": None,
                                        "mate_white": None, "depth": None}
                if has_eval:
                    cp = _integer(entry.get("eval"), minimum=-1_000_000, maximum=1_000_000)
                    if cp is None:
                        issues.append(f"invalid_eval_ply_{index + 1}")
                        array_complete = False
                        continue
                    item["white_cp"] = cp  # 0 is a real, usable score.
                elif has_mate:
                    mate = _integer(entry.get("mate"), minimum=-100_000, maximum=100_000)
                    if mate is None:
                        issues.append(f"invalid_mate_ply_{index + 1}")
                        array_complete = False
                        continue
                    item["mate_white"] = mate  # mate-in-zero remains a mate marker.
                else:
                    array_complete = False
                if has_eval or has_mate:
                    assessments.append(item)

                annotation: dict[str, Any] = {"ply": index + 1}
                best = entry.get("best")
                if isinstance(best, str) and len(best) <= 16:
                    annotation["best"] = best
                variation = entry.get("variation")
                if isinstance(variation, str) and len(variation) <= 2048:
                    annotation["variation"] = variation
                judgment = entry.get("judgment")
                if isinstance(judgment, dict):
                    name = judgment.get("name")
                    comment = judgment.get("comment")
                    if name in {"Inaccuracy", "Mistake", "Blunder"}:
                        annotation["judgment"] = {"name": name}
                        if isinstance(comment, str):
                            annotation["judgment"]["comment"] = comment[:500]
                if len(annotation) > 1:
                    annotations.append(annotation)

    return NormalizedGameAnalytics(accuracy, player_analysis, tuple(assessments),
                                   tuple(annotations), array_present, array_complete,
                                   tuple(dict.fromkeys(issues)))
