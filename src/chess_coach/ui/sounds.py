"""Licensed, selectable chess sound recordings and deduplicated event routing."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import chess
from PySide6.QtCore import QObject, QUrl
from PySide6.QtWidgets import QApplication

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:  # Audio is an optional runtime facility; the board remains usable.
    QSoundEffect = None


_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "sounds"
_CATALOG_PATH = _ASSET_DIR / "catalog.json"


def _load_catalog() -> dict:
    with _CATALOG_PATH.open("r", encoding="utf-8") as stream:
        catalog = json.load(stream)
    cues = catalog.get("cues")
    if not isinstance(cues, dict) or set(cues) != {"game_start", "move", "capture"}:
        raise ValueError("Некорректный каталог звуков Chess Coach.")
    for cue, definition in cues.items():
        options = definition.get("options")
        if not isinstance(options, list) or not options:
            raise ValueError(f"Для сигнала {cue} нет вариантов звука.")
        ids = {item.get("id") for item in options}
        if len(ids) != len(options) or definition.get("default") not in ids:
            raise ValueError(f"Некорректные варианты или умолчание для сигнала {cue}.")
        for item in options:
            filename = item.get("file")
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError(f"Некорректный файл в каталоге звуков {cue}.")
    return catalog


SOUND_CATALOG = _load_catalog()
SOUND_IDS = tuple(SOUND_CATALOG["cues"])
DEFAULT_SOUND_SELECTIONS = {
    cue: definition["default"]
    for cue, definition in SOUND_CATALOG["cues"].items()
}


def _options_by_id(cue: str) -> dict[str, dict]:
    return {option["id"]: option
            for option in SOUND_CATALOG["cues"][cue]["options"]}


def selected_sound_ids(selections: dict[str, str] | None = None) -> dict[str, str]:
    """Return valid selections, falling back safely for missing/old QSettings values."""
    selections = selections or {}
    result = {}
    for cue, default_id in DEFAULT_SOUND_SELECTIONS.items():
        choice = str(selections.get(cue, default_id))
        result[cue] = choice if choice in _options_by_id(cue) else default_id
    return result


def sound_asset_path(sound_id: str) -> Path | None:
    for cue in SOUND_IDS:
        option = _options_by_id(cue).get(sound_id)
        if option is not None:
            return _ASSET_DIR / option["file"]
    return None


def ensure_sound_assets(directory: Path, selections: dict[str, str] | None = None) -> dict[str, Path]:
    """Return selected bundled recordings; ``directory`` remains for API compatibility."""
    del directory
    choices = selected_sound_ids(selections)
    output = {
        cue: _ASSET_DIR / _options_by_id(cue)[sound_id]["file"]
        for cue, sound_id in choices.items()
    }
    missing = [str(path) for path in output.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Не найдены аудиоресурсы: " + ", ".join(missing))
    return output


def classify_move_sound(board: chess.Board, move: chess.Move, *, game_finished: bool = False) -> str:
    after = board.copy(stack=True)
    if move not in after.legal_moves:
        raise ValueError("Нельзя озвучить нелегальный ход.")
    after.push(move)
    if board.is_capture(move):
        return "capture"
    if game_finished or after.is_checkmate() or after.is_stalemate() or after.is_game_over():
        return "game_end"
    return "move"


class SoundEventDeduplicator:
    def __init__(self, capacity: int = 2048):
        self.capacity = max(32, int(capacity))
        self._played: OrderedDict[tuple[str, int], None] = OrderedDict()

    def claim(self, game_id: str, ply: int) -> bool:
        key = (str(game_id), int(ply))
        if key in self._played:
            self._played.move_to_end(key)
            return False
        self._played[key] = None
        while len(self._played) > self.capacity:
            self._played.popitem(last=False)
        return True


class ChessSoundPlayer(QObject):
    def __init__(self, directory: Path, *, moves_enabled: bool = True,
                 notifications_enabled: bool = True, history_enabled: bool = False,
                 volume: int = 45, selections: dict[str, str] | None = None,
                 effect_factory: Callable | None = None, parent=None):
        super().__init__(parent)
        self.moves_enabled = bool(moves_enabled)
        self.notifications_enabled = bool(notifications_enabled)
        self.history_enabled = bool(history_enabled)
        self.volume = max(0, min(100, int(volume)))
        self.selections = selected_sound_ids(selections)
        self.deduplicator = SoundEventDeduplicator()
        self.effect_factory = effect_factory or QSoundEffect
        self.effects: dict[str, object] = {}
        self._effects_by_id: dict[str, object] = {}
        self.available = self.effect_factory is not None
        if not self.available:
            return
        try:
            for cue in SOUND_IDS:
                for option in SOUND_CATALOG["cues"][cue]["options"]:
                    path = sound_asset_path(option["id"])
                    if path is None or not path.is_file():
                        raise FileNotFoundError(f"Не найден звук {option['id']}.")
                    effect = self.effect_factory(self)
                    effect.setSource(QUrl.fromLocalFile(str(path)))
                    effect.setLoopCount(1)
                    effect.setVolume(self.volume / 100)
                    self._effects_by_id[option["id"]] = effect
            self._select_effects()
        except (OSError, RuntimeError, ValueError):
            self.effects.clear()
            self._effects_by_id.clear()
            self.available = False

    def _select_effects(self) -> None:
        self.effects = {
            cue: self._effects_by_id[sound_id]
            for cue, sound_id in self.selections.items()
            if sound_id in self._effects_by_id
        }

    def configure(self, *, moves_enabled: bool, notifications_enabled: bool,
                  history_enabled: bool, volume: int,
                  selections: dict[str, str] | None = None):
        self.moves_enabled = bool(moves_enabled)
        self.notifications_enabled = bool(notifications_enabled)
        self.history_enabled = bool(history_enabled)
        self.volume = max(0, min(100, int(volume)))
        if selections is not None:
            self.selections = selected_sound_ids(selections)
            self._select_effects()
        for effect in self._effects_by_id.values():
            effect.setVolume(self.volume / 100)

    def play_move(self, game_id: str, ply: int, before: chess.Board, move: chess.Move,
                  *, game_finished: bool = False, source: str = "confirmed") -> bool:
        if game_finished and source != "history":
            return self.play_game_end(game_id, ply,
                                      cue="capture" if before.is_capture(move) else "move")
        if source not in {"confirmed", "optimistic", "history"}:
            return False
        if source == "history":
            if not self.history_enabled:
                return False
        elif not self.deduplicator.claim(game_id, ply):
            return False
        if not self.moves_enabled:
            return False
        cue = classify_move_sound(before, move)
        return self._play(cue)

    def play_game_end(self, game_id: str, ply: int, *, cue: str = "move") -> bool:
        if not self.deduplicator.claim(f"game-end:{game_id}", 0) or not self.moves_enabled:
            return False
        return self._play(cue)

    def play_premove(self, event_key: str) -> bool:
        if not self.deduplicator.claim(f"premove:{event_key}", 0) or not self.moves_enabled:
            return False
        return self._play("premove")

    def play_pending_move(self, request_id: str) -> bool:
        """Give immediate neutral feedback without claiming a move was accepted.

        The configured recording remains reserved for an authoritative server
        acceptance. A pending-input beep is a system cue, not a chess sound asset.
        """
        request_id = str(request_id or "").strip()
        if (not request_id or not self.moves_enabled
                or not self.deduplicator.claim(f"pending-move:{request_id}", 0)):
            return False
        QApplication.beep()
        return True

    def play_notification(self, event_key: str) -> bool:
        if not self.deduplicator.claim(f"notification:{event_key}", 0) or not self.notifications_enabled:
            return False
        return self._play("notification")

    def preview(self, cue: str = "move", sound_id: str | None = None) -> bool:
        """Preview the selected or explicitly highlighted library option."""
        if cue not in SOUND_CATALOG["cues"] or not self.moves_enabled:
            return False
        selected_id = sound_id or self.selections[cue]
        if selected_id not in _options_by_id(cue):
            return False
        effect = self._effects_by_id.get(selected_id)
        if effect is None:
            return False
        effect.play()
        return True

    def _play(self, cue: str) -> bool:
        if cue == "premove":
            return False
        if cue not in self.effects:
            cue = "move"
        effect = self.effects.get(cue)
        if effect is None:
            return False
        effect.play()
        return True

    def play_game_start(self, game_id: str) -> bool:
        """Play once for a newly started game, never for replay or reconnect."""
        if (not game_id or not self.moves_enabled
                or not self.deduplicator.claim(f"game-start:{game_id}", 0)):
            return False
        return self._play("game_start")
