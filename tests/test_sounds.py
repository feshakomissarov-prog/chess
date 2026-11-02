import wave

import chess

from chess_coach.ui.sounds import (
    ChessSoundPlayer, classify_move_sound, ensure_sound_assets,
)


class FakeEffect:
    def __init__(self, _parent=None):
        self.source = None
        self.volume = None
        self.play_count = 0
    def setSource(self, source): self.source = source
    def setLoopCount(self, count): self.loops = count
    def setVolume(self, volume): self.volume = volume
    def play(self): self.play_count += 1


def _player(tmp_path, **options):
    return ChessSoundPlayer(tmp_path / "sounds", effect_factory=FakeEffect, **options)


def test_versioned_recording_assets_are_packaged_mono_pcm_wav(tmp_path):
    assets = ensure_sound_assets(tmp_path / "sounds")
    assert set(assets) == {"move", "capture"}
    for path in assets.values():
        assert path.is_file()
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 44_100
            assert 0.07 <= wav.getnframes() / wav.getframerate() <= 0.25
            assert wav.getnframes() > 100
    assert assets["move"] != assets["capture"]


def test_move_sound_classification_obeys_result_and_move_priority():
    board = chess.Board()
    assert classify_move_sound(board, chess.Move.from_uci("e2e4")) == "move"
    capture_board = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    assert classify_move_sound(capture_board, chess.Move.from_uci("e4d5")) == "capture"
    castle_board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert classify_move_sound(castle_board, chess.Move.from_uci("e1g1")) == "move"
    checking_board = chess.Board("4k3/8/8/8/8/8/4R3/4K3 w - - 0 1")
    assert classify_move_sound(checking_board, chess.Move.from_uci("e2e7")) == "move"
    promotion_board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
    assert classify_move_sound(promotion_board, chess.Move.from_uci("a7a8q")) == "move"
    mate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    mate_move = chess.Move.from_uci("f7g7")
    assert mate_move in mate.legal_moves
    after_mate = mate.copy()
    after_mate.push(mate_move)
    assert after_mate.is_checkmate()
    assert classify_move_sound(mate, mate_move) == "game_end"


def test_optimistic_move_and_duplicate_server_ack_play_once(tmp_path):
    player = _player(tmp_path)
    board = chess.Board()
    move = chess.Move.from_uci("e2e4")
    assert player.play_move("game", 1, board, move, source="optimistic")
    assert not player.play_move("game", 1, board, move, source="confirmed")
    assert sum(effect.play_count for effect in player.effects.values()) == 1


def test_muting_volume_history_and_premove_notification_preferences(tmp_path):
    player = _player(tmp_path, moves_enabled=False, notifications_enabled=False,
                     history_enabled=False, volume=45)
    board = chess.Board()
    move = chess.Move.from_uci("e2e4")
    assert not player.play_move("local", 1, board, move)
    player.configure(moves_enabled=True, notifications_enabled=False,
                     history_enabled=False, volume=0)
    assert not player.play_move("history-game", 1, board, move, source="history")
    assert not player.play_premove("premove-1")
    assert not player.play_notification("challenge-muted")
    assert all(effect.volume == 0 for effect in player.effects.values())
    assert all(effect.play_count == 0 for effect in player.effects.values())


def test_notifications_use_separate_original_cue_and_toggle(tmp_path):
    player = _player(tmp_path)
    assert player.play_notification("challenge-1")
    assert player.effects["move"].play_count == 1
    player.configure(moves_enabled=True, notifications_enabled=False,
                     history_enabled=False, volume=45)
    assert not player.play_notification("challenge-2")


def test_game_end_deduplicates_across_finish_before_final_move_order(tmp_path):
    player = _player(tmp_path)
    assert player.play_game_end("game", 3)
    board = chess.Board()
    assert not player.play_move("game", 4, board, chess.Move.from_uci("e2e4"),
                                game_finished=True)
    assert player.effects["move"].play_count == 1


def test_history_playback_is_explicit_and_does_not_share_live_dedupe(tmp_path):
    player = _player(tmp_path, history_enabled=True)
    board = chess.Board()
    move = chess.Move.from_uci("e2e4")
    assert player.play_move("game", 1, board, move, source="history")
    assert player.play_move("game", 1, board, move, source="history")
    assert player.effects["move"].play_count == 2
    assert player.play_move("game", 1, board, move, source="confirmed")
    assert player.effects["move"].play_count == 3


def test_missing_audio_device_fails_gracefully(tmp_path):
    def unavailable(_parent=None):
        raise RuntimeError("audio device unavailable")
    player = ChessSoundPlayer(tmp_path / "sounds", effect_factory=unavailable)
    assert not player.available
    assert player.effects == {}
    assert not player.play_premove("premove")


def test_preview_is_explicit_and_does_not_consume_live_game_dedupe(tmp_path):
    player = _player(tmp_path)
    assert player.preview()
    assert player.effects["move"].play_count == 1
    assert player.preview("capture")
    assert player.effects["capture"].play_count == 1
    board = chess.Board()
    assert player.play_move("next-game", 1, board, chess.Move.from_uci("e2e4"))
    assert player.effects["move"].play_count == 2


def test_recording_sources_are_preloaded_once_and_reused_for_later_games(tmp_path):
    created = []
    def factory(parent=None):
        effect = FakeEffect(parent)
        created.append(effect)
        return effect
    player = ChessSoundPlayer(tmp_path / "sounds", effect_factory=factory)
    assert set(player.effects) == {"move", "capture"}
    assert len(created) == 2
    assert all(effect.source.isLocalFile() for effect in player.effects.values())
    assert player.effects["move"].source.toLocalFile().endswith("move-recording-v1.wav")
    assert player.effects["capture"].source.toLocalFile().endswith("capture-recording-v1.wav")
    board = chess.Board()
    e4 = chess.Move.from_uci("e2e4")
    for game_id in ("game-1", "game-2", "game-3"):
        assert player.play_move(game_id, 1, board, e4)
    assert player.effects["move"].play_count == 3
    assert len(created) == 2


def test_game_finishing_capture_uses_one_capture_recording_without_double_audio(tmp_path):
    player = _player(tmp_path)
    board = chess.Board("7k/8/8/3p4/4P3/8/8/K7 w - - 0 1")
    move = chess.Move.from_uci("e4d5")
    assert player.play_move("capture-end", 1, board, move, game_finished=True)
    assert not player.play_game_end("capture-end", 1, cue="capture")
    assert player.effects["capture"].play_count == 1
    assert player.effects["move"].play_count == 0
