"""Small original procedural chess cues and deduplicated event routing."""

from __future__ import annotations

import math
import struct
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import chess
from PySide6.QtCore import QObject, QUrl

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:  # Audio is an optional runtime facility; the board remains usable.
    QSoundEffect = None

SOUND_IDS = ("move", "capture", "check", "castle", "promotion", "premove", "game_end", "notification")


def _cue_samples(cue: str, sample_rate: int = 22_050) -> bytes:
    """Synthesize short original wood-like impact clicks; no borrowed samples."""
    patterns = {
        "move": ((0.0, 1.0),),
        "capture": ((0.0, 1.0), (0.042, 0.78)),
        "check": ((0.0, 0.86), (0.038, 0.7)),
        "castle": ((0.0, 0.85), (0.034, 0.85)),
        "promotion": ((0.0, 0.8), (0.038, 0.95), (0.076, 0.8)),
        "premove": ((0.0, 0.55),),
        "game_end": ((0.0, 0.9), (0.055, 0.72), (0.11, 0.58)),
        "notification": ((0.0, 0.55), (0.048, 0.65)),
    }
    impacts = patterns[cue]
    duration = 0.064
    frame_count = int(sample_rate * (max(offset for offset, _gain in impacts) + duration))
    output = bytearray(frame_count * 2)
    for impact_index, (offset, gain) in enumerate(impacts):
        start = int(offset * sample_rate)
        count = int(duration * sample_rate)
        state = 0x9E3779B9 ^ (impact_index * 0x45D9F3B)
        previous_noise = 0.0
        for index in range(count):
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            noise = ((state >> 8) / 0xFFFFFF) * 2.0 - 1.0
            # A short noisy attack with a damped wooden resonance, not a pitched tone.
            previous_noise += 0.22 * (noise - previous_noise)
            t = index / sample_rate
            attack = min(1.0, index / max(1.0, sample_rate * 0.0015))
            envelope = attack * math.exp(-t * 62.0)
            body = 0.62 * math.sin(2 * math.pi * 205 * t) + 0.21 * math.sin(2 * math.pi * 495 * t)
            sample = gain * 0.34 * envelope * (0.76 * previous_noise + body)
            frame = start + index
            old = struct.unpack_from("<h", output, frame * 2)[0] / 32767.0
            value = max(-1.0, min(1.0, old + sample))
            struct.pack_into("<h", output, frame * 2, int(value * 32767))
    return bytes(output)


def ensure_sound_assets(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    output = {}
    for cue in SOUND_IDS:
        # Versioned filenames prevent a previously generated tonal cue from
        # silently overriding this original transient-click set.
        path = directory / f"{cue}-click-v2.wav"
        if not path.is_file():
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22_050)
                wav.writeframes(_cue_samples(cue))
        output[cue] = path
    return output


def classify_move_sound(board: chess.Board, move: chess.Move, *, game_finished: bool = False) -> str:
    after = board.copy(stack=True)
    if move not in after.legal_moves:
        raise ValueError("Нельзя озвучить нелегальный ход.")
    after.push(move)
    if game_finished or after.is_checkmate() or after.is_stalemate() or after.is_game_over():
        return "game_end"
    if move.promotion:
        return "promotion"
    if board.is_castling(move):
        return "castle"
    if after.is_check():
        return "check"
    if board.is_capture(move):
        return "capture"
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
            return self.play_game_end(game_id, ply)
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

    def play_game_end(self, game_id: str, ply: int) -> bool:
        if not self.deduplicator.claim(f"game-end:{game_id}", 0) or not self.moves_enabled:
            return False
        return self._play("game_end")

    def play_premove(self, event_key: str) -> bool:
        if not self.deduplicator.claim(f"premove:{event_key}", 0) or not self.moves_enabled:
            return False
        return self._play("premove")

    def play_notification(self, event_key: str) -> bool:
        if not self.deduplicator.claim(f"notification:{event_key}", 0) or not self.notifications_enabled:
            return False
        return self._play("notification")

    def preview(self) -> bool:
        """Play one short local move click without touching live event dedupe."""
        return bool(self.moves_enabled and self._play("move"))

    def _play(self, cue: str) -> bool:
        effect = self.effects.get(cue)
        if effect is None:
            return False
        effect.play()
        return True
