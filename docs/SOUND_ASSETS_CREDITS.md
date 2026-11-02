# Chess Coach sound assets

Chess Coach bundles two short CC0 board-piece recordings from RouteNote Licensing. The sounds are loaded once when the player is created and reused for subsequent moves and games.

| Cue | Source / license | Packaged file | Segment and processing |
| --- | --- | --- | --- |
| Move | [Board Game Wood Piece Slide](https://licensing.routenote.com/sound-effect/board-game-wood-piece-slide) by El Final Boss / Jeff Final Boss, CC0 | `src/chess_coach/assets/sounds/move-recording-v1.wav` | Public MP3 source, 0.34 s. Segment 0.020–0.240 s; mono PCM16 WAV at 44.1 kHz; 4 ms edge fades; amplitude reduced to 70%. No pitch or tempo changes. |
| Capture | [Board Game Wood Piece Capture Chess](https://licensing.routenote.com/sound-effect/board-game-wood-piece-capture-chess) by El Final Boss / Jeff Final Boss, CC0 | `src/chess_coach/assets/sounds/capture-recording-v1.wav` | Public MP3 source, 0.24 s. Segment 0.020–0.230 s; mono PCM16 WAV at 44.1 kHz; 4 ms edge fades; amplitude reduced to 62%. No pitch or tempo changes. |

Packaged WAV SHA-256: move `EF107AA4619720657A0CC5CE78E403979457214DA571FBCB7B110BF9B8230E4E`; capture `2DA679C627979AD926C8A86BADFF9AC72B312641066D4226AED0F2E28ED620D7`.

The capture page describes a louder chess-piece capture recorded on a wooden board for the contributor's chess app. The slide page describes a wooden-board piece-slide effect made for a chess game. The two cues are distinct source recordings; neither is generated from tones, procedural noise, or the former synthesized click assets.

The RouteNote pages state CC0 and provide public playback/download sources without an application account. Their public metadata exposes these MP3 source URLs:

- Move: <https://storage.googleapis.com/routenote-licensing-storage/public/sound_effects/7292/546118__el-boss__piece-slide.mp3>
- Capture: <https://storage.googleapis.com/routenote-licensing-storage/public/sound_effects/7292/546120__el-boss__piece-capture.mp3>

No attribution is legally required by CC0; Chess Coach keeps this credit and processing record for provenance. The in-app settings provide separate previews, volume, mute, and a “Классический” preset. Automated tests check the WAV format and routing. **Listening quality, repeat-game playback on a physical Windows audio device, and perceived loudness still require owner verification.**
