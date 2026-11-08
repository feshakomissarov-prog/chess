# ARCH-UX-01 · REVIEW-FIX-02 — дополнительная точечная корректировка после независимого ревью

**Дата:** 04.11.2026  
**Кодовая база:** существующий draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), HEAD `c93712ab92c6845cd1b0db28832a9f75e4332171`.  
**CI:** [Windows #245](https://github.com/feshakomissarov-prog/chess/actions/runs/41879012568) **completed / success** именно на указанном SHA; job `test` успешно выполнил offscreen pytest (658), compileall и isolated offline launch.  
**Решение независимого ревью:** **Пока не принимать.** RF-01…RF-15 не переделывать заново; устранить ниже только подтверждённые недостатки с минимальными регрессионными тестами. Физическая приёмка на Windows после исправлений остаётся обязательной.

Нормативные документы: [ARCH-UX-01 v1.1](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), [REVIEW-FIX-01 v1.2](ARCH_UX_01_REVIEW_FIX_01.md), [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [аудит функций](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md), [AGENTS](../../AGENTS.md). Приоритет: действующая игра/Fair Play/личные данные → достоверные показатели → тихая синхронизация → UX. Ничего не менять в `main`, не создавать новый кодовый PR, не сливать.

## RF2-01 · P1: часы Lichess не должны пропадать после накопления инкрементов

**Доказательство в коде:** `src/chess_coach/lichess/session.py::_apply_clock_state` (текущий HEAD) использует:
```python
upper = (self.clock_initial + max(increments) + 2_000
         if self.clock_initial else _UNKNOWN_CONTROL_SAFE_MS)
if any(value > upper for value in times):
    self.clock_state_valid = False
    return
```
Это неверно для легального контроля с инкрементом: при 3+2 (`initial=180000` мс, `winc=2000` мс) игрок, быстро сделавший несколько ходов, может накопить **185000+ мс**, хотя верхняя граница жёстко равна `184000`. Валидный авторитетный `gameState` будет отвергнут и часы в пользовательском UI окажутся в состоянии синхронизации. Имеющийся тест `test_server_clock_accepts_correspondence_units_and_rejects_corruption` проверяет длинные часы/ошибку, но не **нормальное накопление банкового времени**.

**Изменить:** валидировать тип, знак, диапазон и монотонность событий **с учётом числа реальных серверно подтверждённых ходов и легально накопленных инкрементов**, а не одного `increment`. Строго отбрасывать явно невозможные/испорченные величины (перепутанные seconds/ms, значения за пределами документированной категории), но **не делать слепой clamp** и не подменять time authority локальной логикой. Проверить `gameFull.clock.initial/increment` и `gameState.wtime/btime/winc/binc` на документированные единицы; не переносить единицы из export JSON `clock` на live stream без подтверждения. Не ломать переписочные партии, no-clock, reconnect placeholder или обычный таймер с монотонной интерполяцией.

**Тесты:** 3+2 initial 180000/increment 2000, несколько подряд быстрых ходов с накоплением 186000/188000 (валидно); обе стороны, повтор/устаревшее событие; 10+0 без роста, 15+10 и long time control, correspondence, missing initial, malformed seconds-vs-milliseconds, non-integer, отрицательные и слишком большие времена; корректное отображение и смена выбранной партии. Никакого вызова Stockfish/HTTP для тестов.

## RF2-02 · P1: фоновые Lichess batches должны просыпаться по due time, а не по накопленному счётчику

**Доказательство в коде:** `src/chess_coach/window.py::_lichess_analysis_backfill_finished` получает `checked` из **кумулятивного** `lichess_analysis_backfill_summary(account_id)["checked"]` (`service_sync.py::refresh_lichess_analysis_background`), однако планирует следующий таймер при `checked >= 10 and state == "проверка завершена"`. Это **не количество обработанных игр в последней порции**. После 10 исторически проверенных партий даже порция из 1 вновь запускает таймер, тогда как при 429/daily limit/cooldown через due time **следующее пробуждение не назначается**. Отдельно `service_sync.py` при отсутствии due возвращает лишь общий статус (пусто/Retry-After); это не критерий следующего запуска.

**Изменить:** сохранить одну существующую durable очередь и лимиты 10 per batch / 25 per day / account+auth generation, но разделить:
- счётчики **общего состояния** (`total/checked/accuracy/evals/no_data`) — исключительно для UI;
- решение scheduler, полученное из **факта выполненной порции**, существования следующей due работы и **persisted earliest next-eligible time** (per-facet cooldown, суточный бюджет, 429, pause/resume). Никаких inferred scheduling по `checked >= 10`;
- тихое возобновление после daily reset, Retry-After и возвращения приложения из сна/переподключения, пока сессия пользователя и разрешения позволяют;
- отсутствие busy polling и цепочек бесконечных пустых 60-секундных рабочих потоков; дубли timer/workers коалесцировать, отменять на logout/switch/revoke/close и не блокировать live game lane;
- если `paused/auth denied/human live/unknown`, никакой API request до нового авторитетного preflight и явного разрешения. Не запускать server-side анализ, только проверять **уже готовые** данные.

**Тесты:** на fake-clock/durable SQLite: 9/10/10 исторически checked и новый batch 0/1/10, чтобы UI totals не управляли таймером; достижение лимита 25/day и возобновление на следующий UTC день **без перезапуска приложения**; независимые due даты accuracy vs evals; 429 Retry-After, no-data TTL, pause→resume, logout/switch, human-gameStarted, airplane/offline, shutdown, повторное подключение. Проверять **количество реальных fake HTTP calls** и отсутствие повторных workers в пустой очереди. Не ускорять poll ценой нарушения Lichess limit.

## RF2-03 · P1: метрики «полный / частичный анализ» и фильтр должны согласоваться для одной партии

**Доказательство в коде:**
- `application/dashboard.py::dashboard_snapshot` считает `complete_count` и `partial` в двух независимых `COUNT(DISTINCT CASE ... game_id)`. Если у одной текущей линии существуют **полный и частичный** `analysis_runs` (например, Stockfish и Lichess-PGN), одна партия одновременно добавляется в оба числа. Тогда `полный + частичный > партии с анализом`, хотя подпись предполагает взаимоисключающие статусы.
- `infrastructure/sqlite/games.py::search_games`: `analysis=yes` считает игру с **хотя бы одной подтверждённой классификацией хода**, даже без scored CP/mate (`move_classifications`), а KPI `dashboard_snapshot` использует исключительно наличие `position_assessments` со score. Таким образом выборка `Любой анализ` и количество «партий с анализом» на Главной иногда разные при одном фильтре аккаунта/контроля — прежняя жалоба владельца по данным.

**Изменить:** единая account-scoped и current-revision определённая категория **«есть позиционные оценки»** во всех основных фильтрах и KPI; `classification-only` можно явно обозначить **отдельным** состоянием, но нельзя скрыто включать в `Любой анализ`, если большой KPI считает другое. Для каждой партии выбрать ровно один общий статус из `full`, `partial`, `none` (при наличии нескольких источников `full` имеет приоритет), но source sets Stockfish/Lichess могут пересекаться и маркируются как такие. Не менять исходные анализы, не смешивать official accuracy-only с scored анализом, не нарушать line_revision/hash и Fair Play. Один SQL/read-model контракт, bounded pagination и корректная cache invalidation, без избыточной полной агрегации каждой страницы.

**Тесты:** synthetic current-line game с двумя run (один complete 100%, другой partial 20%), двумя частичными, classification-only без CP, accuracy-only JSON metadata, stale revision, Stockfish+Lichess overlap и отдельные аккаунты. Инварианты: `full + partial == analyzed distinct games`, `no analysis == completed - analyzed` в одном scope, выборка `analysis=yes` равна KPI filtered set, `stockfish + lichess` может превосходить analyzed, но подписи честные; UI и SQLite показывают одинаковые значения в Home/Мои партии/Progress, включая 0/N и revoked Fair Play.

## RF2-04 · P2: дата, определяющая порядок каталога, должна быть понятна в видимой карточке

**Доказательство в коде:** `games.py::search_games(order_by_game_time=True)` сортирует в первую очередь по `last_move_at`, а `LibraryPage.show_game_detail` и общий форматтер выводят `started_at`. Партия, начатая вчера и завершённая сегодня, будет расположена среди сегодняшних игр с **вчерашней** датой; это выглядит как повторно сломанная сортировка, хотя SQL сортирует по другому событию. Пользователь ранее специально просил даты в естественном хронологическом порядке.

**Изменить:** явно закрепить продуктовый смысл «Последние партии» и «Мои партии» — **последний подтверждённый ход / завершение при наличии**, иначе известное начало. В карточке/деталях указывать `Завершена/Последний ход: DD.MM.YYYY HH:mm` и/или `Начата: …`, не маскируя одно другим. Для `date_only` не придумывать время, для legacy неизвестной точности — честный placeholder. Если нужен более простой UI, разрешается сортировать по подтверждённому началу вместо последнего хода, **но тогда сортировка и текст на экране должны отражать одну и ту же временную ось**, со стабильным fallback и проверкой соответствия архивным timestamp semantics.

**Тесты:** игра A начата вчера/закончена сегодня, B начата сегодня и ещё активна, C архив date-only, D timestamp unknown, E старый анализ обновлён сегодня — порядок, подписи, главные последние 3, фильтры/пагинация/локальная TZ согласованы; нет фиктивного `03:00` для PGN Date.

## Отчёт и повторная приёмка

- **Один** кодовый draft PR #44, **четыре** RF2 пункта с явно указанными тестами/коммитами. Не повторять все ARCH-01/ARCH-UX и RF-01…15, не менять пользовательскую SQLite/токены, schema migration без необходимости, Stockfish/Fair Play политику. Звуки/обычный premove/видимые специальные команды/ReviewGraph и все рабочие функции сохранить.
- Записать исполнение в `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md` как `REVIEW-FIX-02`, привести diff, тесты, финальный code HEAD, известные риски, точную ссылку Windows CI. Обновить `docs/PROJECT_STATUS.md` **в кодовой ветке**.
- Полный Python 3.12 offscreen pytest, compileall, `git diff --check`, offline/manual network+secret isolation, новая exact-head Windows CI. Старый #245 SUCCESS относится только к исходному коду до исправления.
- Результат остаётся **draft**, **никаких merge**, сначала повторное независимое ревью, затем отдельный ручной Windows pilot владельца.
