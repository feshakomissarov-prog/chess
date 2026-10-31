# Chess Coach — текущее состояние и handoff

**Актуально на 31.10.2026.** Репозиторий: https://github.com/feshakomissarov-prog/chess  
**Локальный путь владельца:** `G:\project\chess`. У других исполнителей он может быть иным.  
**Код работает из ветки `main`.** Вся история и документация GitHub доступны следующему Codex и следующему чату без данного диалога.

## Актуализация 01.11.2026 — follow-up глубокого аудита PR #14

- Изменения опубликованы в [draft PR #15](https://github.com/feshakomissarov-prog/chess/pull/15), ветка `codex/pr14-deep-audit` поверх PR #14 (`codex/m3-m4-review-fixes`, base SHA `fd184182f32b485f16b3c921af42913c30f09284`); `main` не менялась. Первый Windows CI run #41250368749 завершился `150 passed, 1 failed, 2 errors`: исправлены устаревший setup connected state и `close()` в test doubles; повторный прогон ожидается.
- Закрываются пункты A–G из [PR14_DEEP_AUDIT_AND_ACCEPTANCE.md](specs/PR14_DEEP_AUDIT_AND_ACCEPTANCE.md): raw-only сохранение нестандартных партий, безопасное закрытие фоновых workers, account-specific opponent, backfill anchor и точечное обновление игры, идентичность/отмена Stockfish, 429/reconnect диагностика и SQLite интеграционные регрессии.
- Локально на Windows / Python 3.11.5: 78 тестов `test_lichess.py` и 8 file-backed SQLite тестов прошли, включая 2501 попытку puzzle sync с resume; `compileall` и `git diff --check` прошли. Qt suite после native access violation не запускался повторно; добавленные Qt-регрессии ждут Windows CI.
- Владелец прислал сообщение `python.exe - Ошибка приложения` о чтении невалидного адреса. Dr.Web toast показывает: «Процессу запрещено изменение запущенного приложения», PID `26032`, путь `C:\Windows\System32\WerFault.exe`. Скриншот не доказывает связь между этим блокированием и native crash; причины не установлены. Защита/исключения не менялись.
- Реальные Lichess/OAuth/Stockfish и личные данные не использовались. Слияния не выполнялись; интерактивную проверку GUI следует отложить до разбора crash владельцем и результатов CI.

## Актуализация 01.11.2026 — аудит M3/M4 перед реальным импортом

**Новая спецификация на исправления:** [M3_M4_REVIEW_FIXES_BEFORE_IMPORT.md](specs/M3_M4_REVIEW_FIXES_BEFORE_IMPORT.md).

**Исправления аудита (01.11.2026):** отдельный draft [PR #14](https://github.com/feshakomissarov-prog/chess/pull/14) поверх #13, head `7b5716b98b4a3edc0a626ae7e35eebec61c43e2f`. Games archive использует `until` и защищает timestamp boundary; SQLite archive record + account mapping + checkpoint атомарны; puzzle activity идёт в descending порядке и возобновляется по durable cursor. Stockfish block глобален для активных human sessions; статистика отделяет draw от unknown/aborted и выбирает один analysis run; retry restore после 429 доступен после cooldown. Schema `user_version=5`. Локально Windows/Python 3.11.5 — 138 passed; GitHub Actions Windows Server 2025/Python 3.12.10 — run #41247619397 SUCCESS, 138 passed + compileall/offline-window smoke. Реальные Lichess данные и Stockfish не использовались; слияния не выполнялись. Детали и ручные проверки — в [M3_M4_PROGRESS.md](M3_M4_PROGRESS.md).

Codex опубликовал **шесть вложенных draft PR**, **не слитых в main**: [#8 P0](https://github.com/feshakomissarov-prog/chess/pull/8) `f4c22807` (base main), [#9 SQLite](https://github.com/feshakomissarov-prog/chess/pull/9) `0c98ae7` (base #8), [#10 Archive](https://github.com/feshakomissarov-prog/chess/pull/10) `bbdad30` (base #9), [#11 Stockfish](https://github.com/feshakomissarov-prog/chess/pull/11) `6b0d954` (base #10), [#12 Stats](https://github.com/feshakomissarov-prog/chess/pull/12) `f023e74` (base #11), [#13 Puzzles](https://github.com/feshakomissarov-prog/chess/pull/13) `7b6ad4271de2e2ae2b24ddb58eed326f6c7decf8` (base #12). **CI последней ветки Windows/Python 3.12.10: 126 passed**, без live E2E.

**Найдены блокирующие ошибки до импорта реальных игр:** game export ошибочно использует `before` вместо официального `until`; архив пишет через async writer и отдельно продвигает checkpoint/foreign key, возможна гонка; incremental puzzle activity ошибочно предполагает ascending порядок, возможны пропущенные попытки. Дополнительно `division=true` не запрашивается, check Stockfish fair-play покрывает лишь анализируемую игру, статистика может считать abandoned/unknown как ничьи, а подключение после restore 429 может оставаться disabled. **Не запускать массовый импорт пока не исправлено и не проверено!**

В `--offline` кнопка подключения к Lichess **должна быть неактивна** и новая база пустая: это безопасная изоляция, не баг. На обычном запуске при статусе «Подключено» connect также disabled, есть «Отключить». Если после cooldown 429 connect не восстанавливается — отдельный баг. **PR #8 сейчас имеет mergeable=false из-за неразрешённого совмещения с main**, не пытаться слить напрямую без сохранения новых документов.

**Следующий шаг:** дать Codex полный аудит-ТЗ (ссылкой выше), исправления и regression tests в соответствующих stacked PR. После исправлений — пользовательский `--offline` smoke, затем осторожный явно разрешённый онлайн-тест 2–5 партий, потом большой архив и puzzles. Не делать самостоятельного merge. `docs/M3_M4_PROGRESS.md` пока в ветке #13, не в main.

## 1. Цель проекта

Windows-приложение Chess Coach (Python, PySide6) — персональный тренер: играть через Lichess, **записывать размышления пользователя по каждому изменению позиции, начиная с первого хода белых**, после партии распознавать голос, анализировать Stockfish, обсуждать ошибки и создавать упражнения. **Во время сетевой партии с человеком никакого Stockfish/ИИ-подсказывания.**

## 2. Git: что уже слито

| Инкремент | GitHub | Результат |
| --- | --- | --- |
| **M0** | [PR #2](https://github.com/feshakomissarov-prog/chess/pull/2) — merged | `chess-widgets==0.5.0`, адаптер доски, локальный контроллер, клик-клик, drag, предходы, ручные стрелки, SAN, просмотр истории, настройки |
| **M1/M2 — основной клиент Lichess** | [PR #3](https://github.com/feshakomissarov-prog/chess/pull/3) — merged | OAuth PKCE, токен в Windows Credential Manager, Board API/NDJSON, игра с другом по ссылке и логину, Lichess AI, takeback, часы, pending-ходы, PGN/SQLite, экраны Home/Game/Result/Review, ярлык Windows, offscreen тесты |
| **Merge M0** | `9df0efe348892092a5639e5af6e4a1e7dcd92787` | В main |
| **Merge Lichess** | `cd293e4dc744e6891f4626c7764870e531bff846` | В main |

Во время merge PR #2/#3 возникали конфликты со свежими документами main. Они разрешены с сохранением документации main и новейшего кода/README из PR #3. **В будущем создавать новые feature-ветки от `main`, не продолжать писать в старые ветки M0/M1.** Не делать merge/force push без разрешения владельца.

## 3. Что реально проверялось

- **Codex:** по отчёту 95 passed в Qt offscreen, `compileall` PASS, `git diff --check` PASS на Windows Python **3.11.5**. **Независимой CI-проверки Python 3.12 нет**; `pyproject.toml` требует `>=3.12`.
- **Владелец:** запускал GUI на Windows, подключал аккаунт Lichess, играл с Lichess AI и другом, использовал premove, проверял часть новых переходов. На последней итерации провёл несколько ручных партий, сообщил, что мерцающих окон уже не видел. **Полный тест takeback, reconnect, длинных партий, финального PGN на последнем коде пока не подтверждён.**
- **ChatGPT:** проверял исходники PR через GitHub, но **не запускал Windows/pytest или реальные запросы к Lichess**. Рекомендовал принятие прототипа в main с открытым техдолгом.
- **Риск автозапуска сети:** при запуске ярлыком Codex зафиксировал неожиданное восстановление сохранённой OAuth-сессии с серверными событиями. Не доказано, отправился ли ожидавший ход. Открытие обычного GUI со старым OAuth **не равно офлайн smoke**. Вопрос в [Issue #5](https://github.com/feshakomissarov-prog/chess/issues/5).
- **Антивирус:** Dr.Web 31.10.2026 20:06 сообщил о заблокированном доступе к защищаемому объекту; по скриншоту путь/процесс неизвестен. Проверка Defender Codex не заменила анализ Dr.Web. Не отключать защиту и не добавлять исключения.

## 4. Новый большой пул (пересмотрено 01.11.2026)

**Актуальное единое ТЗ:** [M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md) · [эпик #6](https://github.com/feshakomissarov-prog/chess/issues/6). **Большое ТЗ с несколькими самостоятельными PR, голос пока отложен.**

**Состояние P0:** [draft PR #8](https://github.com/feshakomissarov-prog/chess/pull/8), head `f4c22807d96f7c753a6e110c37f703d251032174`, base `main`, **не слит**. Код PR #8: исправлены ручные стрелки, безопасный `--offline`, SQLite close, ввод только после валидного gameFull, изоляция QSettings, окно «Активные партии» и Windows CI. **Windows Python 3.12.10 CI подтверждён 108 passed**. Codex сообщил о видимом окне с `python.exe -m chess_coach --offline`, но ручной тест владельца и запуск ярлыка `pythonw.exe` не подтверждены; не утверждать реальный Lichess E2E. Новых блокирующих дефектов статическим ревью текущего PR не выявлено; решение о merge — за владельцем.

**Новые приоритеты:**
1. **Stage 1, M3:** эволюция существующей SQLite без потери `games`: accounts, отдельные строки `game_moves`, `game_positions`, редактируемые `game_notes`, версионированные revisions для takeback, миграция старых PGN, библиотека.
2. **Stage 2, M3:** полный первый NDJSON импорт завершённых Lichess-партий с checkpoint/resume, затем incremental only; активные партии уже есть в P0, не реализовывать повторно.
3. **Stage 3, M4:** оценки Lichess при наличии и локальный Stockfish после игры, кэшированные analysis runs, график по каждому ply, ошибки и фазы партии.
4. **Stage 4, M4:** отдельный экран сводной статистики (W/D/L, swing оценки, неточности/ошибки/зевки по дебюту/миттельшпилю/эндшпилю, контроль, период, coverage).
5. **Stage 5, M4:** **личные задачи Lichess**, `GET /api/puzzle/activity` (OAuth `puzzle:read`, NDJSON `before/since/max`) и `GET /api/puzzle/dashboard/{days}`: отдельные `puzzles`/`puzzle_attempts`/`puzzle_sync_state`, история и статистика.
6. **Позднее M5:** микрофон/последовательность нарисованных вариантов, связь с позицией и временем, воспроизведение; [историческое голосовое ТЗ](specs/M3_M4_ARCHIVE_ANALYSIS_VOICE_THOUGHTS.md) остаётся основой только для этого этапа. Данные об отдельном текстовом комментарии и стабильном position uid подготовить в M3.
7. **M6:** ИИ-тренер после локальной аналитики и размышлений, отдельное согласование.

**Главное ограничение:** Stockfish и ИИ-подсказки нельзя использовать во время своей активной Lichess партии против человека. Реальный массовый импорт или OAuth изменения только с разрешения владельца. Dr.Web/execution policy не менять. CI/tests headless. Каждый stage reviewable, **не merge самостоятельно**.

## 5. Архитектурные инварианты

- Lichess — единственный источник истины для онлайн партии. `GameController` и `BoardView` не могут «утвердить» ход за сервер.
- Optimistic move в UI не означает сохранённый ход; confirmed UCI → `LichessGameSession` → упорядоченный SQLite writer → PGN. Учитывать конечную синхронизацию `final_sync_pending`.
- Предход не делает ход в authoritative позиции, выполняется только когда после события соперника остаётся легальным. В отличие от предугадываемого ответа, обычный premove **не защищает от зевка** при неожиданном ходе.
- Takeback: через `POST /api/board/game/{gameId}/takeback/yes|no`. На сервере история может стать короче; локальные PGN и очередь должны согласоваться. Локальное Undo/Cancel premove — другие команды.
- Никаких подсказок движка/GPT против человека; ручные стрелки — правая кнопка; не менять управление стрелками без решения владельца.
- Токены OAuth и личные игровые данные не помещать в git/log. Ресурсы и лицензии: код chess-widgets MIT, python-chess GPL и SVG pieces отдельно; итоговая лицензия приложения ещё требует решения перед распространением.

## 6. Куда смотреть новому исполнителю

Сначала `AGENTS.md`, затем `docs/PROJECT_STATUS.md` (этот файл), `README.md`, `docs/PRD.md`, `docs/ROADMAP.md`, [новое единое ТЗ](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md), [эпик #6](https://github.com/feshakomissarov-prog/chess/issues/6) и относящуюся к текущему PR маленькую задачу (#7 / #5 / #4). Затем соответствующие исходники и тесты.

**Ключевые файлы:** `src/chess_coach/board_view.py`, `controller.py`, `window.py`, `lichess/{api,auth,service,session,repository}.py`, `tests/test_{board_view,controller,window,lichess}.py`, `scripts/create_desktop_shortcut.ps1`.

## 7. Запуск на Windows

В PowerShell, если рабочее дерево чистое:

```powershell
cd G:\project\chess
git status --short
git switch main
git pull --ff-only
.\.venv\Scripts\python.exe -m chess_coach
```

**Не удалять локальные изменения ради switch/pull.** При установленном ярлыке `Chess Coach.lnk` можно запускать двойным щелчком; он использует `.venv\Scripts\pythonw.exe` и может автоматически восстановить OAuth-соединение. Если нужен настоящий офлайн-тест, код должен явно гарантировать отсутствие обращений к сети. Для новой установки ориентироваться на `README.md` и Python >=3.12; пока версия 3.12 не подтверждена тестами.

**Ближайшее решение (01.11.2026):** пользователь передаёт Codex одно большое ТЗ [M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md), реализация внутри — по небольшим проверяемым блокам/PR. Сначала завершить P0 и осознанно принять #8, затем SQLite/импорт, после Stockfish/сводная аналитика и задачи Lichess. Голос позднее.
