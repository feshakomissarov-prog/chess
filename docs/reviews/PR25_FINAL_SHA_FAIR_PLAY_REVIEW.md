# PR #25 follow-up — проверка финального SHA и классификация Lichess AI/BOT для fair play

**Дата:** 2026-11-01. **Независимое ревью** PR [#25](https://github.com/feshakomissarov-prog/chess/pull/25), HEAD **`b027763de71bea6fb655e8a593471c0ae7590ba9`**, base `codex/m3-windows-diagnostics` (#24), draft и unmerged. Это дополнение к [первому ревью PR25](PR25_INDEPENDENT_REVIEW_AND_ACCEPTANCE.md), **не новое задание на другие M3–M4 блоки**.

## Проверка заявления Codex о новых исправлениях

**Windows CI подтверждён на конкретном SHA:** [run #41263521194](https://github.com/feshakomissarov-prog/chess/actions/runs/41263521194), Windows Server 2025/Python **3.12.10**/PySide6, Qt, shiboken6 **6.12.0**, `200 passed in 95.92s`, compileall и isolated offline-window smoke PASSED. Локальный native crash Python 3.11.5/shiboken6 по-прежнему не имеет установленной причины. Полного интерактивного Windows smoke и реального Lichess/Stockfish **не было**.

**Три P1 предыдущего ревью исправлены по коду и fake/Qt-тестам:**

1. **Cancel:** единый `_cancel_analysis_action(request_id)` работает в фазах `preflight` и `running`. Успешный callback `_analysis_safety_games_received` не обнуляет request ID до завершения анализа; обработчик Qt теперь выставляет сохранённый `stop_event` и не сохраняет complete run. Тест `test_connected_analysis_cancel_stops_fake_analyzer_without_saving_complete_run` нажатие fake GUI кнопки проверяет.
2. **Fail-closed snapshot:** `LichessClient.get_playing_games` проверяет каждую запись `nowPlaying`; malformed/empty gameId, отсутствующий opponent и ответ ровно на лимите 50 → `LichessError`. Есть фальшивые API-фикстуры, реальные HTTP вызовы не выполнялись.
3. **Смена аккаунта:** `_analysis_session_generation`, `_analysis_safety_phase`, request ID, `service` identity и `username` сверяются перед допуском. `disconnect_lichess`, `connect_lichess`, `_lichess_authenticated`, `_lichess_status('Не подключено')` инвалидируют preflight. Qt-тесты disconnect, A→B, reconnect и старые ответы PASS.

**SQLite миграция v9, puzzle identity и 35k-game summary из прежнего состояния PR #25 не переписывались.** Их прежнее независимое ревью остаётся в силе.

## Новый существенный дефект — P0 (fair play), а также функциональная инверсия встроенного AI/BOT

**Официальные источники (сверены 2026-11-01):**

- [Lichess Fair Play policy](https://lichess.org/page/fair-play): **engine assistance permitted vs Lichess AI**, но **prohibited vs a Lichess Bot account** в обычной игре / Board API. BOT API является отдельно описанным исключением, но Chess Coach здесь использует `board:play`, а не BOT API.
- [Официальная схема /api/account/playing](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-account-playing.yaml) — `nowPlaying[].opponent` для встроенного AI имеет **`id: null, username, ai: 1..8`**, сама игра `source: ai`, в отличие от пользовательского аккаунта.
- [Официальный пример активной AI-партии](https://github.com/lichess-org/api/blob/master/doc/specs/examples/account-playing-ai.json.yaml): `source:'ai'`, `opponent: { ai:1, id:null, username:'Stockfish level 1' }`, **нет `title:'BOT'`**.
- [Официальный пример gameStart vs AI](https://github.com/lichess-org/api/blob/master/doc/specs/examples/stream-gameStart-ai.json.yaml): та же сигнатура `source:'ai'` и `opponent.ai`.

**Фактическая ошибка в нашем коде:**

1. `window.py::_active_game_is_human(game)`, ~стр. 3050:
   ```python
   return not (isinstance(opponent, dict) and opponent.get("title") == "BOT")
   ```
   Запись, содержащая `title:'BOT'`, **ошибочно объявляется безопасной** для Stockfish. Но Lichess запрещает движковую помощь во время партии с **чужим BOT-аккаунтом**. Тест `test_connected_analysis_is_blocked_by_unopened_human_game_and_allowed_only_for_proven_bot` даже специально проверяет ложное разрешение `{opponent:{title:'BOT'}}`. Встроенная AI-партия `source:'ai',opponent:{ai:1,id:null}` тем временем помечается человеческой и **блокирует анализ** (безопасный, но неверный UX). **Титул BOT нельзя использовать как исключение fair play.**
2. `session.py::LichessGameSession.is_ai_opponent`, ~стр. 82, тоже `return self.opponent_title=='BOT'`. Это влияет на `window.py::_lichess_event(gameStart)`, `_lichess_game_event`, анализ `online_sessions` и кнопки возврата хода. При `gameStart` BOT-аккаунта и `title:'BOT'` выполняющийся Stockfish **не отменяется**, хотя должен. Настоящий встроенный AI без `title BOT` обрабатывается как человеческий.

**Исправление:** один явно определённый `is_official_lichess_ai_game(game)`/устойчивое значение в `LichessGameSession`, основанное на **достоверном `source == 'ai'` плюс корректном `opponent.ai` (integer 1–8) и `opponent.id is None`**, а не имени соперника и не `opponent.title == BOT`. Не доверять полю `source`, полученному от пользователя; данные серверных `gameStart` и `/api/account/playing` уже получены из доверенного API. Обрабатывать отсутствие/конфликт признаков **консервативно как запрещающий** случай. Классификация при `gameFull` не должна перезаписывать достоверное `source` мусором. Если сессия была восстановлена без достаточно авторитетных полей, до проверки считать human/unknown. `Bot API` не поддерживается как исключение.

**Регрессионные тесты fake API + Qt (не требуется реальный OAuth):**

- Server `/api/account/playing` возвращает только одну нормальную встроенную AI-партию `source:'ai', opponent:{id:null,username:'Stockfish level 1',ai:1}` → preflight **позволяет** анализ завершённой **другой** партии, если нет human/бот-аккаунтов, и `online_sessions` не блокирует.
- Server возвращает активную игру с **BOT-account** `opponent:{id:'somebot',username:'SomeBot',title:'BOT'}`, `source:'friend'` → **блокирует** до запуска UCI. Даже если поле `title` обычно отсутствует в текущей официальной схеме, тест нужен для безопасной классификации при обогащённых ответах API.
- Server возвращает `source:'ai'` с некорректным/отсутствующим `ai` или `id`, или неизвестный `source` → не допускать анализ автоматически.
- `gameStart` с официальной AI signature **не отменяет** уже выполняемый анализ, а `gameStart` с BOT-account **выставляет stop_event**; события Board API `gameFull/gameState` должны сохранять правильный тип.
- `LichessGameSession.is_ai_opponent` корректен и после `open_active_game`, и после `gameStart`. Существующие тесты `test_ai_takeback...`, выставляющие `session.opponent_title='BOT'`, обновить на **официальную форму AI**, не маскировать дефект тестовыми ложными признаками.
- Сохранить previously fixed connected Cancel, 50-limit fail-closed, смену аккаунта, SQLite v9 и 35k-game тест, `200+` Windows CI.

**Статус:** `b027763` — **NO-GO real Stockfish / main merge** из-за неверной fair-play классификации. После небольшого исправления **в существующем PR #25** и нового Windows CI можно переходить к **одному offline Windows GUI smoke** владельца; live OAuth/личная SQLite/реальный Stockfish — только по отдельному решению и backup.

## Интеграция / ROADMAP

`PR #8` → `main` всё ещё имеет `mergeable=false`; остальные PR #9–#25 стеки. **Никаких auto merges.** Следующий интеграционный шаг — draft PR/временная интеграционная ветка с ручным сохранением docs `main` и повторным CI на фактическом интеграционном SHA.

Отдельные [PRODUCT_VISION](../PRODUCT_VISION.md), [22 области и 12 навыков](../research/CHESS_SKILL_TAXONOMY.md), [методика тренера](../research/CHESS_COACHING_PRACTICE.md), [матрица образовательных пробелов](../research/CHESS_COACH_COVERAGE_GAPS.md) остаются исследованиями, а не текущими Codex-задачами. Лестницу M3/M4 → M5 → M6 **не менять** из-за этого фикса.
