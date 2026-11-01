# Независимый аудит PR #25: исправления M3/M4, оставшиеся риски и критерии ручной приёмки

**Дата:** 2026-11-01. **Проверенный head:** [draft PR #25](https://github.com/feshakomissarov-prog/chess/pull/25), `aa4289e56edfaa6834ecffffcd50061922c67ecd`, branch `codex/pr24-review-fixes`, base `codex/m3-windows-diagnostics` (PR #24), `merged=false`. **Исходный `main` не содержит код PR #8–#25, ни один PR без разрешения владельца не сливать.**

**CI фактически проверен:** GitHub [run #41261611961](https://github.com/feshakomissarov-prog/chess/actions/runs/41261611961), а также второй [run #41261837883](https://github.com/feshakomissarov-prog/chess/actions/runs/41261837883) на том же head. Оба SUCCESS. Лог последнего: **194 passed in 65.08s**, Python 3.12.10, PySide6/Qt/shiboken6 6.12.0, compileall и offscreen window-init PASS. Локальный Windows/Python 3.11.5 native crash в `shiboken6.abi3.dll` ранее зафиксирован; **не воспроизведён и не объяснён в этом ревью**. Тестов с OAuth, личными партиями, пользовательской SQLite или реальным Stockfish не было.

**Итог:** основные P0 предыдущего аудита исправлены, но **GO для слияния/реального UCI ещё нет** из-за регрессии отмены анализа и недоказанной полноты серверного списка активных игр. Нужна небольшая адресная правка, затем повторная проверка и ручной пилот. Не переписывать статистику или миграцию ещё раз.

## 1. Принятые исправления (по исходникам и фикстурам)

| Предыдущий дефект | Что сделано | Независимая оценка |
|---|---|---|
| Puzzle UID зависел от mutable `rating/plays/themes` | `puzzle_attempt_uid(account_id,puzzle_id,date_ms,win)`, используемый в `repository.py` и `puzzles._key`; метаданные задачи UPSERT | **Исправлено по коду.** Две загрузки события с изменёнными `rating/plays/themes` дают один UID, тогда как другой день/аккаунт — другой. Непредставимый случай двух попыток одинакового puzzle и `date_ms`/win без серверного immutable ID необходимо документировать. |
| Историческая SQLite v8 | `SCHEMA_VERSION=9`; backup до изменения; транзакция; temp old rows, mapping old→new, dedupe через `ROW_NUMBER`, перевод pilot links, index | **Исправлено по коду**, целевой file-backed тест `test_v8_puzzle_attempt_migration_deduplicates_legacy_uids_and_preserves_pilot_links`. Счётчики ранее выполненных pilot runs **сознательно не переписываются**, это исторические значения, они могут отличаться от физического числа уникальных попыток после миграции. UI должен не выдавать их за текущую уникальную статистику. |
| `StatisticsService.summary` загружал весь архив и миллион движений в память, giant SQL `IN` | Общий CTE `filtered_games`, агрегаты `COUNT/SUM/AVG` в SQLite, аналитические `chosen`/latest run, rating_trend LIMIT 500 | **Исправлено по коду**; в реальном файле тест на **35 000** игр и ограниченную страницу. Не утверждать, что тест эквивалентен миллионам реальных ходов или десяткам анализов на партию: реальные performance/pilot ещё впереди. |
| Фильтр источника при `analyzed=any` | Предикат `EXISTS completed analysis_runs` теперь действует независимо от `analyzed=yes`; `analyzed=no` означает отсутствие выбранного источника | **Исправлено**: `summary` и `game_page` используют один `_filter_sql`; реальная SQLite фикстура stockfish-only/lichess-only/both/neither. |
| Неучтённые сессии на Lichess | Перед стартом движка вызывается background `get_playing_games`, HTTP/429 fail-closed; активные human определяются консервативно через `opponent.title != BOT` | **Частично:** хороший защитный шаг, но ответ API может быть неполным (см. P1 ниже), а допуск после успешного ответа создаёт регрессию кнопки «Отменить». |
| UCI `QProcess.kill()` → немедленный `deleteLater()` | `SettingsDialog` передаёт `quit\n`, потом `kill` при таймауте, ждёт `finished`; `MainWindow` ведёт множество живых probe-процессов и не закрывается, пока оно не пусто | **Логически исправлено**, тесты с fake `QProcess` проверяют задержанный `finished`, Cancel, повторный probe. **Реальный UCI process не тестировался**, вывод о причине локального native crash не делать. |
| Сохранённый custom `analysis_limit_value` стирался при новом открытии настроек | Убран неявный `_apply_analysis_preset()` при конструировании SettingsDialog | **Исправлено**, есть Qt тест на reopen 333 333 nodes. |

## 2. Оставшиеся замечания — блокируют merge-ready

### P1-A. После успешного серверного guard кнопка «Отменить» у Stockfish не вызывает stop_event

**Место:** `src/chess_coach/window.py::_start_game_analysis()`, `_analysis_safety_games_received()`, `_launch_game_analysis()`, ориентир строки 2953–3092.

**Точная цепочка по коду:**
1. При подключённом аккаунте создаётся `QProgressDialog` и его `canceled` соединён с `lambda: _cancel_analysis_safety_check(request_id)`.
2. Когда сервер подтвердил отсутствие активной human-game, `_analysis_safety_games_received` **обнуляет `_analysis_safety_request_id`**, затем передаёт **тот же dialog** в `_launch_game_analysis(..., stop=stop, dialog=self._analysis_dialog)`.
3. В `_launch_game_analysis` обработчик `dialog.canceled.connect(stop.set)` выполняется **только при `dialog is None`**.
4. После начала Stockfish нажатие «Отменить» вызывает старый обработчик `_cancel_analysis_safety_check(request_id)`. Поскольку `request_id != None`, этот обработчик сразу делает `return`. **`stop.set()` не вызывается**, фоновый анализ продолжается. Автоматический/ручной close окна может выставить stop, но это не замена кнопки Cancel. В offline неподключённом режиме Cancel работает, поэтому фикстура offline не обнаруживает регрессию.

**Минимальное исправление:** привязывать кнопку Cancel к одному lifecycle-aware контроллеру `_cancel_analysis_action`, который в состоянии `preflight` завершает попытку и игнорирует поздний reply, а в состоянии `running` вызывает **именно сохранённый `stop_event.set()`**; либо явно отсоединять старый slot и подключать `stop.set` при переходе в RUNNING. Никаких повторных запусков Stockfish по позднему reply и дублей сигналов `finished`. Статус «Проверяем…» сменить на «Анализ позиции X…».

**Обязательный Qt integration test:** connected fake service → empty `nowPlaying` → fake analyzer запущен → click `QProgressDialog.cancelButton()` → stop_event set, mock worker quit, SQLite не сохраняет `complete`, диалог закрывается, окно работает; отдельно cancel **во время preflight**, поздний result не запускает UCI.

### P1-B. Нельзя трактовать потенциально неполный/некорректный список active games как доказательство их отсутствия

**Место:** `src/chess_coach/lichess/api.py::get_playing_games` примерно строки 229–237 и `window.py::_analysis_safety_games_received`.

Метод делает `GET /api/account/playing?nb=50` и выполняет
```python
return [game for game in games if isinstance(game, dict) and game.get("gameId")]
```
То есть **молча отбрасывает** malformed entries. Если `nowPlaying=[{"opponent":{"username":"Human"}}]` без `gameId`, результат становится пустым, и запускающий анализ UI не блокирует Stockfish. Если API вернул 50 игр (запрошенный `nb`), нельзя **доказать**, что они исчерпывают активные игры аккаунта. Теоретически необследованная человеческая игра может находиться за пределом выборки; особенно при большом числе correspondence. Не надо заявлять, что это происходит в типовом Lichess ответе: это **граничное условие/неполное доказательство безопасности**, а не наблюдённая реальная ошибка.

**Исправить:** validation **всех** элементов `nowPlaying`, без silent filtering (на незнакомом schema fail-closed); при ответе ровно 50 и отсутствии гарантированной полной пагинации — **fail-closed или официальный подтверждающий способ доказать полноту**, прежде чем запускать движок. Достаточно консервативного отказа «Список активных партий может быть неполным — анализ нельзя безопасно начать». Учесть `opponent.title=BOT` только при достоверном поле. Отказ/429/потеря сети безопасны, тесты fake JSON на malformed entry, >=50 BOT entries и 1 hidden human. Не нужны реальные API-запросы.

### P1-C. Привязка проверки к конкретному аккаунту и состоянию подключения (гонка)

**Место:** `_start_game_analysis` сохраняет `(request_id, game_id, engine_path, options)`, а `_analysis_safety_games_received` использует только `request_id`; **не перепроверяет `_lichess_connected` и identity аккаунта** после асинхронного запроса. `disconnect_lichess` просто вызывает `self.lichess.disconnect_account()`, без явной отмены `_analysis_safety_request_id`.

**Сценарий:** запрос начат для аккаунта A, затем пользователь нажал Disconnect (или вошёл в B), затем пришёл разрешающий ответ A. Обработчик может запустить Stockfish уже после отключения/смены аккаунта. Нет гарантии свежести безопасности на момент запуска. Это race относительно безопасности, **проверить и исправить**.

**Исправить:** при disconnect/reconnect/смене active account отменять preflight, записывать `account_id` или неизменяемую identity токена (без сохранения секрета в лог/БД), проверять identity + connected state + request generation на callback, не допускать запуск при устаревшем ответе. Не отправлять секреты через Qt signals. При server gameStart mid-preflight — отменить; guard при start work уже действующий сохранить.

**Тест:** fake delayed A snapshot, disconnect A, приём late A → **нет запуска**; переключение A→B, late A → **нет запуска**; retry с новым request ID запускает анализ только после его собственного достоверного empty snapshot. fake 429/error всегда блокирует.

## 3. Не блокеры, но важно держать открытыми

- `PR #8` → `main` **mergeable=false**, из-за накопившихся независимых docs в `main`. PR #25 mergeable=true относительно **PR #24**, но это не значит, что stack готов к `main`. Требуется отдельная интеграционная ветка, разрешение конфликтов без потери `README`, `PRODUCT_VISION`, `ROADMAP`, `PROJECT_STATUS` и [трёх новых документов об областях шахмат, педагогике и gap matrix](../research/CHESS_COACH_COVERAGE_GAPS.md).
- `puzzle_pilot_runs.new_count` после v9 dedupe остался **историческим счётчиком попыток при исходной загрузке**, а `puzzle_attempts` содержит исправленные уникальные записи. Это допустимо при явной подписи «исторически записано», **не считать их одинаковыми метриками после миграции**.
- SQLite stress **35k games** проверен, но тест добавляет лишь **1375 moves**, не воспроизводит десятки миллионов ходов или многократные двигательные run. Не создавать это автоматически на компьютере пользователя — это test/nightly нагрузка.
- В `--manual-connect` приложение открывает **постоянную** SQLite **до** OAuth. Миграция v9 произойдёт в момент запуска даже без нажатия «Подключить Lichess». Обязательно backup пользовательской БД и `integrity_check` **на копии** до первого запуска. Самостоятельно пользовательские данные не переносить и не удалять.
- Пользовательская Windows Python 3.11.5 раньше имела `0xC0000005` в shiboken6. Windows CI Python 3.12.10 успешен, но не устанавливает причину. Не отключать Dr.Web/Defender, не создавать исключения, не запускать повторяющийся crash-loop и не публиковать дампы с секретами.
- Играть партию с человеком и получать engine suggestions в live UI нельзя (fair-play). Любой полноценный live OAuth/UCI эксперимент — **по решению владельца**, не Codex.

## 4. Окончательные критерии GO

1. Fixed Cancel Qt test и смоделированное отключение/переключение аккаунта с задержанным ответом.
2. Неполные/невалидные `nowPlaying` fail-closed; full snapshot или формально доказуемый guard.
3. Нет регрессий в миграции v8→v9, даже если у одного run было несколько legacy UID на одно событие; duplicate metadata sync показывает 0 новых.
4. `summary` и `game_page` совпадают на source/analyzed с реальной SQLite; stress 35k+ не возвращается к giant `IN`.
5. Windows Python 3.12/Qt offscreen pytest, compileall, offline smoke PASS **на новом SHA**; сохранены собственные документы `main` при интеграции.
6. **После этого** пользователь выполняет **один** ручной offline GUI smoke на финальной ветке, а дальше по явной команде — безопасный онлайн-пилот с backup. Ни один draft PR нельзя мержить автоматически.

## 5. Рекомендуемый следующий Codex job

**Не делать ещё один большой PR-пакет.** Обновить существующий draft PR #25 с тремя узкими guard-fixes и Qt/fake-API тестами, отдельно отложить Git-интеграцию. Последний SHA/CI передать владельцу. До нового SHA: **NO MERGE, NO REAL ENGINE / LIVE OAUTH PILOT**.

**План ручной проверки после исправлений:**
- Установленный владельцем Python 3.12+, отдельный venv `.venv312`. Убедиться в чистом `git status --short`; `git fetch origin`; перейти на branch исправленного PR; проверить `git rev-parse HEAD`.
- **Offline:** `./scripts/run_windows_manual_smoke.ps1` (Windows PowerShell синтаксис `.\scripts\run_windows_manual_smoke.ps1`); свободные ходы `e4 e5 Nf3 Nc6` кликами/drag, два цвета, стрелки, undo, отдельный режим предходов, библиотека/статистика на временной базе, настройки, закрытие. В этом режиме Lichess отключён **ожидаемо**.
- **Persistent:** только после отдельного подтверждения владельца и проверенного backup: `.\scripts\run_windows_manual_smoke.ps1 -Mode manual-connect -ConfirmPersistentProfile`. Режим не делает OAuth до клика, но SQLite уже постоянная. Сначала открыть локальную библиотеку **без нажатия Подключить**.
- **Live:** по отдельной команде 5 завершённых партий, PGN/dedup/restart/stats, 1 завершённая игра с доверенным UCI, Cancel и отсутствие живых человеческих партий, затем puzzle pilot. Полную синхронизацию не начинать ради smoke.

**Roadmap:** [PRODUCT_VISION](../PRODUCT_VISION.md), [модули обучения](../research/CHESS_COACH_COVERAGE_GAPS.md) — изучать позже. M3–M4 → M5 → M6 не менять из-за текущего ревью.
