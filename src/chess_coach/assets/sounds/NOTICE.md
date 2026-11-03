# Библиотека звуков Chess Coach

`catalog.json` — локальный каталог доступных сигналов, вариантов и источников.
Настройки пользователя хранят только ID выбранного варианта; внешние файлы и
сетевые источники во время игры не открываются.

Все звуковые ресурсы ниже опубликованы как CC0 1.0. Позиции 2, 5, 6, 12, 25
для обычного хода и позиция 9 для взятия соответствуют выбранному владельцем
списку из 32 превью ChessGameWorld. Лицензии и исходные страницы:
[каталог лицензий ChessGameWorld](https://www.chessgameworld.com/chesser/licenses.html).

| Cue / option | Файл | Автор и источник | Обработка в Chess Coach | SHA-256 |
| --- | --- | --- | --- | --- |
| Начало партии · единственный вариант | `game-start-cc0-v1.wav` | `Board Start`, el_boss, [Freesound](https://freesound.org/people/el_boss/sounds/546121/) | Моно PCM16, 44.1 kHz; без изменений | `ebba50cf1fd406f1189f60dd80757034b18b635c5d57d9f320bc74298bba2522` |
| Ход 02 · Wooden tap | `move-wooden-tap-v1.wav` | Kenney, [Impact Sounds](https://kenney.nl/assets/impact-sounds) | Файл CC0-превью ChessGameWorld | `15286fcbf23b21fc9b125c26da3f5ecce5f2183fc9649869eb4be576a79c25b9` |
| Ход 05 · Alabaster board | `move-alabaster-board-v1.wav` | mh2o, [Freesound](https://freesound.org/people/mh2o/sounds/351518/) | Файл CC0-превью ChessGameWorld | `995ac604b9f4b5a52964350b99189847c3725b385d5b44a1df54dbc8faf724e0` |
| Ход 06 · Queen on wood (по умолчанию) | `move-queen-on-wood-v1.wav` | Alnotreally, [Freesound](https://freesound.org/people/Alnotreally/sounds/801349/) | Из превью ChessGameWorld вырезан участок 0.100–0.430 s; усиление 2.10× до пика 84%; плавные края 3 ms; 48 kHz mono PCM16 | `780be78003bd68a255e4db852a1b44fd33a40cbd511b5987492eeaa174055e62` |
| Ход 12 · Wooden desk tap | `move-wooden-desk-tap-v1.wav` | AidanSounds, [Freesound](https://freesound.org/people/AidanSounds/sounds/704935/) | Файл CC0-превью ChessGameWorld | `564e2174b792e9b8752e217d3a9447d74fc43bff1681153159cdb25ced126eed` |
| Ход 25 · Wooden chess placement | `move-chess-placement-v1.wav` | Jeff Final Boss / El Final Boss, [RouteNote Licensing](https://licensing.routenote.com/sound-effect/board-game-wood-piece-placement) | Файл CC0-превью ChessGameWorld | `84f7a2718936b976ca0f0321a5015770cac8eb2a68094a64264e635563f99136` |
| Взятие 09 · Falling chess pieces (по умолчанию) | `capture-chess-pieces-drop-v1.wav` | IENBA, [Freesound](https://freesound.org/people/IENBA/sounds/755250/) | Файл CC0-превью ChessGameWorld | `54b09cc749259315866099117e91048dd3c7671ab18512495001e3721bf054f1` |

Начальный сигнал оставлен из уже включённого `Board Start`; сигнал MyInstants
остаётся только личным референсом и в приложение не копируется. Удалены прежние
непринятые `Piece Slide` и `Piece Capture` варианты. Владелец должен подтвердить
звучание всех превью в приложении перед окончательной художественной приёмкой.
