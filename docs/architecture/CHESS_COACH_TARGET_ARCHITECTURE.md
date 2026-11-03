# Chess Coach — целевая архитектура модульного монолита

**Статус:** архитектурное решение владельца, 03.11.2026 · **следующий отдельный этап после M4.6**. Этот документ определяет направление и инварианты. Исполняемое ТЗ для Codex — [ARCHITECTURE_REFACTOR_EXECUTION_SPEC.md](ARCHITECTURE_REFACTOR_EXECUTION_SPEC.md). Не путать архитектурный этап с будущими продуктовыми фичами.

**Базовая версия:** PR #41, кодовый SHA `2c3ccbd99c0c33526fb2bd996ab777a0ec5c8d31`, Windows CI #41625429734, 553 PASS; merged в main как `27b42d3dc482efeff3c64ad3399f96bc74fcb041`. Модель компетенций и девять исследовательских направлений фиксируются отдельно в PR #42. До запуска Codex брать актуальный `main`, а не хардкодить старый SHA.

## 1. Что означает «модульный монолит»

**Монолит** здесь — один продукт, один desktop release и один основной Python/Qt процесс; без микросервисов, обязательного облачного backend, HTTP-интерфейсов между внутренними частями и отдельных серверов для хранения данных. Внешний **Stockfish остаётся отдельным дочерним UCI-процессом** — это нормальное исключение. Lichess — внешний сервис, а не наша «микрослужба».

**Модульность** — внутренние компоненты имеют отдельную ответственность, явные интерфейсы, односторонние зависимости и собственные тесты. Они живут в одном репозитории и одном процессе, но не могут произвольно читать Qt widget state, лазить в таблицы SQLite, запускать engine или обходить safety gate.

Модульный монолит — не «неизменяемый большой скелет, на который навешиваем модули». Это *единая поставка с внутренними архитектурными границами*. Это **не** требование динамических плагинов или загрузки произвольного кода.

**Принято:** Windows 10/11, ориентир — обычный ноутбук с 4 ядрами CPU, 8 ГБ RAM, SSD; Python 3.12+, PySide6, python-chess, SQLite, Lichess API, внешний Stockfish UCI. Нет требования 2 ядра/4 ГБ. Нет решения переписать на Rust сейчас.

## 2. Подтверждённая причина рефакторинга

На SHA `ddeb810` (предыдущий архитектурный аудит; после M4.6 размеры приблизительно те же): `src/chess_coach/window.py` ≈6 982 строк, из них `MainWindow` ≈4 946 строк / ≈200 методов; `lichess/repository.py` ≈2 808 строк / ≈109 методов; `ui/game_review.py` ≈1 498; `lichess/service.py` ≈1 269. Слишком много разных сценариев связаны общим состоянием центральных классов. Проблема не в размере Python-кода на диске или недостаточной «скорости Python», а в *связанности*, рисках UI-состояния, конкуренции I/O, транзакционной дисциплине, ответственности за safety и тестопригодности.

**Не ставить искусственную цель «каждый файл ≤500 строк»**. Оцениваем направление зависимостей, ответственность, устойчивость регрессий. Не переводить любую функцию в абстракцию только ради числа строк.

## 3. Целевые зоны и границы ответственности

Схема направлений (*конкретные имена можно уточнять на этапе переноса с сохранением контракта*):

```text
src/chess_coach/
  __main__.py                         # CLI flags / single instance / process entry
  bootstrap.py                        # composition root: one place, где собираем зависимости
  domain/
    models.py                         # typed immutable IDs, game snapshots, clocks, scores, provenance
    chess_rules.py                    # pure python-chess helpers, no Qt / SQLite / network
    metrics/                          # pure versioned scoring, coverage, phases, evidence
    learning/                         # FUTURE: competencies, traces, interventions; not implemented now
    ports.py                          # Protocols for engine, storage, game feeds, scheduler, safety
  application/
    game_sessions.py                  # online/local lifecycle, moves, clocks, session generations
    analysis_coordinator.py           # completed-game work, cancellation, source/lease coordination
    fair_play.py                      # single fail-closed authorization/preflight/lease control
    background_work.py                # coordinator/scheduler of low-priority durable jobs
    stockfish_manager.py              # UCI subprocess lifecycle and work admission
    archive_sync.py                   # orchestrate existing Lichess import/backfill, no HTTP primitives
    dashboard_queries.py              # prepare read-models / provenance, no widgets
    app_state.py                      # explicit route/account/game revision state
  infrastructure/
    lichess/
      client.py                       # existing REST/NDJSON adapter and rate limits
      auth.py                         # Credential Manager, OAuth/PAT isolation, auth generations
      events.py                       # event/game streams adapter, reconnect, stale cancellation
      sync.py                         # archive, ready analytics, puzzles adapters; source transforms
    sqlite/
      connections.py                  # single SQLite transaction policy and writer lifecycle
      migrations.py                   # schema + backups, same DB / same compatibility
      games.py, analysis.py, jobs.py  # separated storage concerns, same transactions
      accounts.py, puzzles.py, views.py
      facade.py                       # temporary GameRepository public compatibility
    engine/
      uci.py                          # hidden process adapter, UCI limits/options/cancellation
      discovery.py
    settings.py                       # existing keys / persistence, no secrets
    diagnostics.py                    # structured privacy-safe logging and correlation IDs
  ui/
    shell.py                          # thin MainWindow: ownership of root widgets, routing
    pages/
      home.py, play.py, review.py, analytics.py, puzzles.py, library.py
    dialogs/
      settings.py, lichess_connection.py, ai_game.py, batch_analysis.py,
      statistics.py, active_games.py
    components/
      board.py, clocks.py, moves.py, graphs.py, sounds.py, common.py
    presenters/                       # UI mapping from snapshots to widgets, optional as useful
  window.py                           # temporary import-compatibility façade, then small re-export
```

Имена — **целевая карта, а не команда создать десятки пустых файлов**. Разбивать существующие классы по связанным функциям; не порождать циклический import или boilerplate, не переписывать все имена за один коммит.

### Зависимости

- `domain` — только стандартная библиотека и `python-chess`, **без PySide6, Lichess REST, SQLite, файловой системы/ключей и Stockfish process**. Значения typed, по возможности immutable; преобразование форматов на границе.
- `application` зависит от `domain` и портов/контрактов, но **не от конкретных Qt widgets**, HTTP response DTO или SQL table names. Для уведомления UI допускается порт/событие/Qt adapter, но не `QLabel.setText()` внутри business coordinator.
- `infrastructure` реализует порты `domain/application`, не импортирует `ui`. Только адаптеры обращаются к токенам, реальной сети, engine и SQLite.
- `ui` зависит от опубликованных сценариев `application` и immutable read models; не подменяет safety, не открывает свои соединения к DB и не делает собственный Lichess HTTP запрос. `bootstrap` связывает объекты и подписывает сигналы.
- **Временно** разрешён `window.py`/GameRepository/LichessService фасад для старых импортов и тестов; исключения инвентаризировать и постепенно убирать. Избегать «строгой чистой архитектуры» ценой бесконечных адаптеров.

### Основные интерфейсы, а не глобальные объекты

- `GameSnapshot`: account/game ID, line revision/hash, player side, FEN/moves, source status, timestamp. Не передавать QWidget/SQLite.Row через слой.
- `Assessment`: ply, **typed cp | mate | missing**, perspective, depth/nodes, engine/source version, coverage.
- `AnalysisAuthorization`: явно привязанный к account, auth_generation, game, revision/hash, route/current safety snapshot, сроку жизни; revoke мгновенно лишает права показывать и запускать анализ.
- `BackgroundJob`: id/type/account/generation, priority, cancellation token, durable cursor/TTL/backoff, progress event.
- `ReadModel`: immutable account-scoped DTO для Home/Review/Analytics; источник+охват+timestamp обязательны для спорных метрик.
- `UnitOfWork`: атомарная запись набора сущностей, expected revision/hash на момент **commit**, изоляция; проверка отмены и auth generation до commit.
- `EngineRequest`: explicit approved position/game context, nodes/depth, cancel token; `EngineResult` source/version, no UI references.

Это эволюционный дизайн. Существующий контракт методов и сигналов сохранять через adapters/facades до завершения миграции.

## 4. Единый диспетчер фоновых операций — центральное изменение

### 4.1. Не путать «единая координация» с «одна очередь на всё»

**Нельзя** заставлять живой игровой event stream, ход пользователя, safety preflight, запись PGN и долгий Stockfish ждать в общей последовательной очереди. Это ухудшит блиц. Нужен один **политический координатор** (наблюдаемое состояние задач, приоритеты, отмена, backpressure), но *разные исполнительные линии*:

1. **UI thread / Qt event loop:** отрисовка, выбор ходов, обновление часов, сигнал пользователя; только быстрые действия и сериализованные UI изменения. Никаких HTTP, CPU-heavy анализа всей партии, долгих SQLite-запросов, sleep/process wait.
2. **Interactive Lichess lane:** game stream и отправка ходов — отдельные неблокирующие worker/sockets, не заперты за фоновым full-archive scan. Состояние игры обновляется из **авторитетных** событий, stale generations отбрасываются.
3. **Safety/control lane:** проверка активных человеческих партий и auth/preflight — высокий приоритет, bounded timeout, fail-closed. При недоступности проверки никакой Stockfish оценки/PV/обучающей подсказки. Low-priority jobs не могут удерживать lock и блокировать safety на время целой многотысячной синхронизации.
4. **Lichess background API lane:** согласованный rate limit, для архивов/готовой аналитики/задач **не более одного короткого background HTTP request одновременно**; отдавать слот более важным интерактивным/control запросам **между** запросами. Запрещён lock на весь многостраничный import, если это блокирует интерактивные действия. При human game и неизвестном safety статусе по имеющимся правилам background analytics stop/pause.
5. **Local engine lane:** Stockfish UCI управляется отдельно, обычно один engine owner с ограничением его Threads/Hash; batch уступает foreground completed-game analysis только с безопасной остановкой/очередью, без двух конкурирующих subprocess. Engine не должен отбирать CPU в live human game и никогда не запускается без действующего разрешения.
6. **SQLite writer lane:** один управляемый transaction/writer policy, очередь с ограничением размера, короткие атомарные записи; read connections/snapshots отделены. Не держать DB write transaction во время HTTP/Stockfish.

**События и приоритеты:** `interactive_move` и `game_state` > `safety_control` > `foreground_completed_review` > `user_requested_sync` > `auto_backfill` > `maintenance`. Реализация может иметь резервируемую контрольную lane (сеть) вместо буквального сравнения целочисленных приоритетов. **Онлайн-ход не должен ждать тяжёлую очередь**. Приоритет не разрешает safety bypass.

### 4.2. Состояние задания

Queued → Running → Completed / Paused / Cancelled / RateLimited / Failed / Stale. Строго различать `no_server_data` и ошибку сети. Сохранять и возобновлять только **уже имеющие durable domain jobs** (archive checkpoints, analytics backfill, batch engine jobs), не придумывать новый универсальный SQL job-store с миграциями без необходимости.

- `account_id`, `auth_generation`, `game_id`, `line_revision/hash`, `operation_id` проходят сквозь event/result.
- Disconnect/logout/revoke/account switch закрывают соответствующие jobs/leases, уведомления старой generation не меняют новый UI.
- Cancellation cooperative + bounded hard-stop only for subprocess; any stale network reply must fail before commit.
- 401/403, 429 Retry-After, low network connectivity вызывают bounded pause, **не polling loop**.
- Background progress по источнику и виду задачи должен показываться в UI «Lichess · проверено X/Y», без модальных alert; не выводить игровые данные/секреты в логи.
- Никакого автоматического Lichess server analysis request через недокументированные endpoints.

## 5. Fair Play — нерушимый системный контракт

**Не переносить безопасность в отдельный «необязательный плагин» и не ослаблять ради нового scheduler.** В normal-connected режиме Stockfish, engine оценки, анализ, verified hints, move classifications, PV, график, phase derived metrics доступны для завершённых партий *только* после **свежего** полного preflight. Human live game, unknown/partial account list, 429/reconnect/401/403, logout, auth generation switch — **fail closed**. Revoke **синхронно маскирует** все уже отображённые аналитические элементы и инвалидирует lease; process stop и отказ от stale DB result обеспечиваются независимо.

`--offline` допускает локальный анализ только в действительно изолированном offline runtime (нет клиента/учётных данных/сети), как в текущих тестах. `--manual-connect` не читает credentials и не создаёт network до явного пользователя. Auto-restored token **не** равно analysis authorization.

**Safety invariants должны иметь owner:** единый `application.fair_play`/lease manager, который использует состояние сессии/полный validated preflight; UI masks через события и не принимает собственные решения об открытии оценок. Engineering test: все replay/reconnect/login/revocation варианты + screenshot/processEvents после masked -> refreshed; engine not spawned when revoke/pending; db stale result not committed.

**В перспективе обучения:** GPT-объяснения/контрпримеры/PV/упражнения не могут быть каналом обхода Fair Play во время live human play.

## 6. SQLite: одно хранилище, управляемые чтение и запись

- Оставить **одну SQLite**, существующую схему, ссылки и `SCHEMA_VERSION`. Схему не менять в рамках чистого переноса модулей, если нет строгой необходимости. Существующий pre-migration backup и integrity_check обязательно сохранить.
- Разделить `GameRepository` **по ответственности, не по отдельным SQLite-файлам**: game snapshots/revisions, accounts, analysis runs/assessments, background jobs/backfill, puzzles, cached dashboard/read models; сохранять совместимый фасад для импорта и тестов.
- Общий `connection/transaction/UnitOfWork` обеспечивает `BEGIN IMMEDIATE` там, где нужен CAS, rollback, no network/engine under DB transaction, thread-safety, shutdown/drain. Разделение writer/reader не означает опасное одновременное использование **одного SQLite connection из разных потоков**.
- Одна serial writer политика для конкурирующих изменений, но быстрые scoped snapshot reads могут идти через короткоживущие read connections. Продумать индексирование/инвалидацию аналитических read models без полного пересчёта статистики в GUI.
- Прежние `line_revision/hash` и `account_id` — обязательный guard внутри commit для любых отложенных результатов. Нельзя «обновить метаданные» старого PGN, подменив current line или raw_pgn.
- `WAL` не включать автоматически только из-за рефакторинга: это отдельное изменение поведения backup/shutdown/locking, требующее собственных проверок. При необходимости — обоснование и отдельный gate.
- Пользовательская БД **не применяется к автоматическим тестам**; миграцию тестируем только на копии/синтетическом файле. No schema downgrade, no silent deletion of saved evaluations.

## 7. Stockfish: отдельный управляющий слой

- `StockfishManager` владеет процессом и explicit lifecycle: discovered/path/verified UCI → Idle → Running → Cancelling → Closed/Failed; subprocess Windows hidden, без CMD всплывающих окон. Не открывать новый процесс при отмене до start; проверять cancel после start и перед saving result.
- Настройки `nodes` (включая **1 000 000**), depth, Threads, Hash, MultiPV, time, engine/version идентифицируют job/run и cache; resume использует исходные параметры, а не текущие настройки.
- Foreground analysis completed game и batch use one admission policy, понятная отмена и graceful shutdown; нельзя абстракцией испортить CLI installed engine discovery или забрать control над пользовательским Stockfish без разрешения.
- Сама шахматная работа уже выполняется **нативным Stockfish**. Python занимается orchestration; перенос orchestration на Rust не ускорит search.

## 8. Наблюдаемость и диагностика без тяжёлого профилирования

Нужна **единая схема событий** для диагностики гонок и причин отказов, базируясь на существующих privacy-safe rotating logs, а не новый telemetry backend:
- `operation_id`/correlation_id, component, event, kind, anonymized account/game identity, auth generation, line revision/hash (при необходимости хеш, не PGN), state before/after, cancellation reason, error class, thread/worker ID, bounded elapsed_ms и queue wait_ms **опционально как диагностическое поле**.
- Event families `AUTH`, `GAME`, `LICHESS_HTTP`, `SAFETY`, `QUEUE`, `SQLITE_TX`, `STOCKFISH`, `UI_ROUTE`; success/failure transitions, not every 250ms clock tick and not every pixel redraw. Устойчивость: логирование никогда не должно блокировать game input.
- Строго не писать OAuth/PAT, cookie, raw PGN, персональные SQLite, username, личные заметки/вопросы, game stream payload, произвольный FEN без явной opt-in диагностики. Уже существующее anonymized report в настройках сохранить; никаких автоматических отправок серверу, opt-in сохраняется.
- Debugging: путь «задание queued → запрос Lichess → JSON validation → DB transaction CAS → UI refresh» виден по одному correlation_id, без секретов. Race tests проверяют отсутствие credential leakage.
- **Важно:** по решению владельца сейчас **не вводим обязательные SLA и регулярные latency benchmark suites**. Не строим отдельный profiling dashboard и не блокируем CI по времени; измерения производительности/нагрузочные 3k/10k/50k партий — отдельный будущий этап. Но код не должен делать I/O в UI thread или допускать очевидные приоритетные инверсии; добавляем дешёвые диагностические hooks, чтобы позже измерять реальные задержки.

## 9. Будущий аналитический и образовательный контур

Цель продукта утверждена: `A × (B1: увидеть, оценить, рассчитать, выбрать, исполнить + B2: воспроизвести, сформулировать гипотезу, объяснить, перенести, проконтролировать, отрефлексировать) → C доказательная диагностика → D персонализированное объяснение и обучение → E проверка усвоения`. Карта 17 семейств/263 явно описанных подтем — рабочая, **не исчерпывающий мировой стандарт**. Девять исследовательских направлений — не девять новых milestone.

Создать только **границы / DTO / provenance и возможные placeholders** для будущего:
- Fact: `source + version + evidence + coverage + uncertainty`.
- Chess competency map отдельна от Stockfish/Lichess/PGN и от GPT prompt implementation.
- Training intervention не выдумывает психологические причины из PGN; диагноз подтверждается несколькими свидетельствами.
- GPT — future explanation interface, **не источник шахматной истины**, по инициативе пользователя и с Fair Play guard. Никаких облачных запросов в текущем архитектурном PR.
- Дебютное дерево и граф вариантов должны в дальнейшем жить как domain/application feature, а не как тысячи новых методов `MainWindow`. **Не реализовывать их сейчас**.

## 10. Python или Rust: решение с отложенным выбором

**Сейчас Python + Qt сохраняем.** Сама работа движка уже нативная. Нельзя утверждать, что Python медленный без замеров конкретного bottleneck. Цель архитектурного PR — устранить блокировки на потоках, лишние зависимости и плохую организацию I/O.

**Дверь для нативного ускорения открыть на уровне портов** (typed snapshots, pure data structures, deterministic contracts). В будущем можно заменить измеренный CPU-heavy компонент на Rust/PyO3 или C++ extension с тем же API, Python fallback, сборкой Windows CI/packaging и license audit. Рассматриваем только если после локальной алгоритмической оптимизации установлено, что Python CPU/RAM — объективная причина проблемы. Не вводить Rust toolchain, FFI, параллельный язык, новую систему сборки **в текущем этапе**. Переход на Rust в будущем не должен менять транзакционные и Fair Play-инварианты. **Используем SQLite, PostgreSQL не вводим.**

## 11. Критерии принятия архитектуры

1. Всё ещё одно Windows приложение, **тот же UX**: открытие ярлыком, темы/карточки/разбор/часы/звуки, Lichess OAuth/PAT, offline/manual, восстановление workspace, intro local and AI 3+2.
2. Существующий полный Windows тестовый набор (исходно **553 PASS**) не теряет тестов; добавляются contract/Qt fake integration и migrations/no secret logs, без «переписывания тестов для зелёного CI».
3. Нет дополнительного реального OAuth/Stockfish/Lichess/личной SQLite в CI, engine if simulated must be fake; physical smoke остаётся у владельца.
4. Нет изменений математики точности, оценки мата, категорий ходов, coverage, источника и лицензий; не добавлять новые обучение/дебюты/голос.
5. Транзакции, account/game revision guards, Fair Play revocation и stale events сохраняются и тестируются.
6. В PR приложить **таблицу зависимостей before/after**, краткий текст по каждой выделенной группе, объяснение новых ports/facades и точные регрессионные тесты. Нет магического числа строк как критерия.
7. Архитектурное изменение — отдельный **draft PR Codex от актуального main**. Код-ревью ChatGPT → физическая Windows-проверка владельца → только после нового явного разрешения merge.

## 12. Что не входит сейчас

Любой глобальный визуальный редизайн, смена языка, Python версия, переписывание Qt на веб, microservices, cloud deployment, Rust/C++ FFI, распределённая база, новый формат PGN/SQLite без обоснования, обязательная производительность/нагрузочные тесты, продуктовый AI tutor/voice, новое дебютное дерево, внешнее ML, массовая загрузка персональных данных и изменения ROADMAP.

Этот документ — критерий **будущей структуры**. Непосредственные шаги, acceptance и последовательность реализации см. [исполняемое ТЗ](ARCHITECTURE_REFACTOR_EXECUTION_SPEC.md).
