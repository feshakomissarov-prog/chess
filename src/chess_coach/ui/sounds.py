"""Packaged natural chess recordings and deduplicated event routing."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

import chess
from PySide6.QtCore import QObject, QUrl

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:  # Audio is an optional runtime facility; the board remains usable.
    QSoundEffect = None

SOUND_IDS = ("move", "capture")
_SOUND_FILES = {"move": "move-recording-v1.wav",
                "capture": "capture-recording-v1.wav"}


def ensure_sound_assets(directory: Path) -> dict[str, Path]:
    """Return versioned, packaged CC0 recordings; never synthesize at runtime."""
    del directory  # retained for source compatibility; assets ship with the package
    asset_dir = Path(__file__).resolve().parents[1] / "assets" / "sounds"
    output = {cue: asset_dir / filename for cue, filename in _SOUND_FILES.items()}
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
                 volume: int = 45, effect_factory: Callable | None = None, parent=None):
        super().__init__(parent)
        self.moves_enabled = bool(moves_enabled)
        self.notifications_enabled = bool(notifications_enabled)
        self.history_enabled = bool(history_enabled)
        self.volume = max(0, min(100, int(volume)))
        self.deduplicator = SoundEventDeduplicator()
        self.effect_factory = effect_factory or QSoundEffect
        self.effects = {}
        self.available = self.effect_factory is not None
        if not self.available:
            return
        try:
            for cue, path in ensure_sound_assets(Path(directory)).items():
                effect = self.effect_factory(self)
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setLoopCount(1)
                effect.setVolume(self.volume / 100)
                self.effects[cue] = effect
        except (OSError, RuntimeError, ValueError):
            self.effects.clear()
            self.available = False

    def configure(self, *, moves_enabled: bool, notifications_enabled: bool,
                  history_enabled: bool, volume: int):
        self.moves_enabled = bool(moves_enabled)
        self.notifications_enabled = bool(notifications_enabled)
        self.history_enabled = bool(history_enabled)
        self.volume = max(0, min(100, int(volume)))
        for effect in self.effects.values():
            effect.setVolume(self.volume / 100)

    def play_move(self, game_id: str, ply: int, before: chess.Board, move: chess.Move,
                  *, game_finished: bool = False, source: str = "confirmed") -> bool:
        if game_finished and source != "history":
            return self.play_game_end(game_id, ply,
                                      cue="capture" if before.is_capture(move) else "move")
        if source not in {"confirmed", "optimistic", "history"}:
            return False
        if source == "history":
            # History sound is only requested by an explicit move-row click;
            # scrolling and render refreshes never call this path. Replaying
            # that row is intentional and independent of live-event dedupe.
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

    def play_notification(self, event_key: str) -> bool:
        if not self.deduplicator.claim(f"notification:{event_key}", 0) or not self.notifications_enabled:
            return False
        return self._play("notification")

    def preview(self, cue: str = "move") -> bool:
        """Preview one of the two actual recordings without live-event dedupe."""
        if cue not in _SOUND_FILES:
            return False
        return bool(self.moves_enabled and self._play(cue))

    def _play(self, cue: str) -> bool:
        # Special move states use the ordinary move recording; queued premoves
        # do not sound like a move before they are confirmed.
        if cue == "premove":
            return False
        if cue not in self.effects:
            cue = "move"
        effect = self.effects.get(cue)
        if effect is None:
            return False
        effect.play()
        return True
