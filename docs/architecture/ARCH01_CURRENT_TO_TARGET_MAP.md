# ARCH-01: карта исходной системы и переноса

**Исходная точка:** `main` SHA `3e7f2cd48b16762062e9414761efd13556b55837` (merge PR #43). Рабочая ветка: `codex/arch-01-modular-refactor`.

## W0 baseline

- Windows 11 / PowerShell; CPython 3.12.10 x64, PySide6/Qt/shiboken6 6.12.0.
- `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -o addopts= -q --tb=short`: **553 passed, 147.45 s**.
- `python -m compileall -q src tests scripts`: PASS.
- Isolated `QApplication` + `MainWindow(offline_mode=True)`, `assert window.lichess is None`, close: PASS.
- `git diff --check`: PASS (до добавления этого документа; исходное дерево без tracked изменений).
- Не запускались реальные Lichess/OAuth, Stockfish, пользовательская SQLite/PGN.

## Исходные компоненты и прямые обязанности

| Текущий источник | Наблюдаемая ответственность | Целевой владелец / совместимость |
| --- | --- | --- |
| `__main__.py`, `single_instance.py` | CLI, изоляция offline/manual, single instance, запуск | Сохранить entrypoint и аргументы; composition root собирает зависимости |
| `window.py` — 7004 строки; `MainWindow` ~4959 строк / 202 метода | Root shell, маршруты, game state, сервисы, прямое repository/engine управление, 8 `threading.Thread` мест | `ui.shell` thin wiring; страницы/dialogs/components + application coordinators; `window.py` остаётся import façade |
| `lichess/repository.py` — 3063 строки; `GameRepository` ~2970 строк / 110 методов | SQLite lifecycle, backup/schema, games, revisions, accounts, analytics, jobs, puzzles, read models | Единая `connections/transactions/migrations`; разделённые тематические stores; совместимый `GameRepository` facade; схема/версия без изменения |
| `lichess/service.py` — 1272 строки; `LichessService` ~1251 строки / 53 метода | Auth, HTTP coordination, stream signals, workers, archive/backfill/puzzle/game commands | Стабильный façade/signals; auth, stream/API adapters, archive/puzzle adapters и background policy разнесены |
| `lichess/{api,auth,session,archive,puzzles,batch_analysis,analysis,statistics}.py` | Уже выделенные части transport, identity/session, sync, analyses and statistics; application policies местами смешаны с adapters | Переносить только связанную бизнес-оркестрацию; текущие публичные контракты сохранить |
| `engine_manager.py`, `uci.py`, `position_analysis.py` | Скрытый UCI запуск/поиск, часть engine lifecycle в UI | Stockfish manager owns subprocess/cancel/admission; существующий UCI factory остаётся инфраструктурным адаптером |
| `ui/{game_review,move_table,dashboard,display,theme,sounds,workspace_state}.py`, `board_view.py` | Выделенные визуальные компоненты | Повторно использовать; не копировать markup/board и не менять layout/UX |
| `diagnostics.py` | Privacy-safe rotating logs and anonymized report | Сохранить настройки/формат; добавить operation correlation и безопасные event fields |

## Публичные imports и runtime-маршруты, которые сохраняются

- `python -m chess_coach`, `chess_coach.window.MainWindow` и существующие dialog/test imports.
- `chess_coach.lichess.repository.GameRepository`, `chess_coach.lichess.service.LichessService` и их публичные методы/signals.
- `chess_coach.lichess.analysis.CompletedGameAnalyzer`, `chess_coach.lichess.batch_analysis.BatchAnalysisRunner`, графики/таблицы, BoardView, CLI, `.cmd`/ярлык и single-instance.
- Runtime цепочки: window → LichessService signals → session → serialized SQLite writes; finished-review → Fair Play lease → analyzer/Stockfish → revision guarded save; archive/backfill/puzzle → Lichess transport → bounded SQLite transaction; dashboard/home/review → account-scoped queries → UI render.

## Статическая карта связей исходного `window.py`

AST-инвентаризация выявила около 81 обращений к repository-путям, 313 обращений к Lichess/service-путям, 149 обращений к engine-путям и 8 мест создания потоков. `window.py` непосредственно управляет домашними/аналитическими read workers, batch/puzzle/archive flow, session moves, auth dialogs и position analysis. Внутри `statistics.py` есть обращения к `_connection`/`_wait_before_read`; это временное coupling к SQLite, которое W3 снимет через read-store/UoW API. Не считать метрики атрибутов точным графом вызовов: это поисковый снимок до рефакторинга, его воспроизводимый AST тест будет хранить реальные boundary checks.

## Целевая карта зависимостей

```text
__main__ → bootstrap → application coordinators → domain / ports
                                  ↓                       ↑
ui shell/pages/dialogs → app commands/read models         infrastructure adapters
                                                           ├─ lichess client/auth/events/sync
                                                           ├─ sqlite transactions + thematic stores
                                                           ├─ engine UCI/StockfishManager
                                                           └─ settings/diagnostics
```

`domain` не импортирует Qt, SQLite, HTTP или файловую/secret инфраструктуру. `application` не импортирует конкретные widgets/HTTP/SQL. `infrastructure` не импортирует UI. Root shell не выполняет HTTP, SQL statements, engine search или job scheduling самостоятельно. Один orchestration coordinator управляет регистрацией/приоритетом и cancellation, но interactive Lichess, safety/control, background API, engine и SQLite writer сохраняют отдельные исполнительные линии.

## Этапы и исходные владельцы

### W1 завершённая часть

- Добавлен framework-independent пакет `chess_coach.domain`: immutable `GameRef`, `GameSnapshot`, `ClockSnapshot`, `AnalysisScore`, `SourceCoverage`, `JobContext`; перечисления задают source и тип оценки.
- `domain.ports` содержит Protocol-контракты для чтения/записи игры, feed, движка, отмены, событий и snapshot provider. Доменные типы не зависят от Qt, SQLite, сетевого транспорта или UI.
- `chess_coach.bootstrap` стал явной точкой создания runtime, настроек и ровно одного `GameRepository`. `MainWindow` принимает готовый `ApplicationRuntime`; прямое конструирование окна и `python -m chess_coach` сохранены.
- Runtime сохраняет режимы профиля: `--offline` создаёт временные настройки/SQLite; `--manual-connect` откладывает создание LichessService до пользовательского намерения; обычный запуск создаёт сервис для прежнего auto-restore. Runtime кеширует экземпляр сервиса, чтобы повторное действие не создавало второй.
- `tests/test_architecture_w1.py` защищает типовые инварианты, import boundaries, временную изоляцию offline и создание ручного Lichess-клиента только по запросу. Существующие тесты launcher перенастроены на новый composition seam, без ослабления assertions.
- `application.app_state.WorkspaceStateStore` сериализует route/geometry/sidebar/board и immutable target сохранённого разбора; UI передаёт snapshot, store не импортирует Qt и не решает визуальную навигацию.
- `application.game_sessions.GameSessionCoordinator` владеет реестром server sessions и выбранным game id. Новая регистрация фоновой партии не переключает selection; `MainWindow` сохраняет прежние имена полей как совместимые точки доступа, а создание/выбор сессий идёт через coordinator.

**Осталось в W1:** typed boundary conversions и no-import-side-effects proof расширить по мере появления application coordinators и инфраструктурных адаптеров; W1 commit фиксирует минимальные реально используемые контракты, без пустых plugin-слоёв.

### W3 — SQLite ownership и тематические фасады

- `infrastructure.sqlite.transactions.SQLiteUnitOfWork` владеет connection settings, foreign-key enforcement, commit/rollback и обязательным close. `GameRepository._connection()` сохранён как совместимый delegating seam.
- `infrastructure.sqlite.migrations.SQLiteMigrationMixin` владеет backup-first initialization и additive migrations; `SCHEMA_VERSION` остаётся 19, migration SQL/data logic не менялась.
- `GameRepository` теперь является компактным public façade с существующими именами методов и тематическими mixins: `GamesStoreMixin`, `ArchiveStoreMixin`, `AnalysisStoreMixin`, `AnalysisJobsStoreMixin`, `PuzzleActivityStoreMixin`.
- Файлы ответственности: `games.py`, `archive_store.py`, `analysis.py`, `jobs.py`, `puzzles.py`; `line.py` содержит каноническую идентичность линии/PGN-поля, `migrations.py` — DDL/data migration, `transactions.py` — соединение и transaction context.
- Общие константы/идентичность линии вынесены в `constants.py`/`line.py`; public import names повторно экспортируются из `lichess.repository`.
- Единый одинарный SQLite writer executor, очередь, CAS/ревизии и транзакционные границы сохранены в фасаде до отдельной безопасной миграции writer-lane coordination. WAL/pool/schema/backup behavior не изменён.
- Storage-specific AST checks запрещают инфраструктуре импортировать UI.

### W4 — Lichess service façade and responsibility split

- `LichessService` остаётся единственным `QObject` владельцем прежних Qt signals, клиентской зависимости и lifecycle state; публичный import path и методы сохраняются.
- Auth/session generation/callback/event stream находятся в `service_auth.py`; archive, Puzzle Activity и ready-analysis orchestration — в `service_sync.py`; active games/challenges/moves/takeback/actions/close — в `service_games.py`.
- `LichessService` наследует эти mixins, его файл оставлен около 140 строк (сигналы + constructor/façade), без второго client/service и без новой OAuth flow.
- Existing fake API/OAuth/stream/backfill suites и facade/interface tests проверяют auth switch, reconnect/429, pending moves/takeback и signal signatures; реальный API не используется.

| Этап | Исходные точки | Проверяемое изменение |
| --- | --- | --- |
| W0 | `window.py`, `repository.py`, `service.py`, entrypoints, tests | Этот baseline/map, imports/routes/threads inventory |
| W1 | package root, DTOs/contracts, entrypoints | `domain` types/ports, explicit bootstrap, architecture import tests |
| W2 | `window.py`, `session.py`, `analysis.py`, `archive.py` | app-state and session registry extracted; remaining analysis/archive/dashboard workflows migrate incrementally |
| W3 | `repository.py`, `statistics.py` | one connection/transaction policy + responsibility stores + compatibility facade; same schema |
| W4 | `service.py`, `api.py`, `auth.py`, streams/sync modules | stable signals/methods, adapter ownership and request boundaries |
| W5 | service background threads, archive/backfill/puzzles, `batch_analysis.py` | coordinator registry + independent resource lanes; no global blocking queue |
| W6 | `engine_manager.py`, `uci.py`, `window.py` engine handlers | Stockfish manager/admission/cancel/save contracts |
| W7 | `window.py` dialogs/pages and existing `ui/*` | extraction and compatibility re-exports; preserve dimensions/content |
| W8 | `diagnostics.py`, imports/tests/docs | correlation ID/redaction, architecture checks, manual smoke |
| W9 | full repository | exact SHA Windows CI, compatibility/fair-play/data audit, draft PR report |

## Зависимости before/after и временные фасады

| Сейчас | После ARCH-01 | Временный compatibility facade |
| --- | --- | --- |
| `MainWindow` directly coordinates UI, service, repository, workers and engine | Shell invokes application ports/commands and renders typed read models | `chess_coach.window` re-exports public windows/dialogs; fields/signals needed by existing tests remain documented |
| `GameRepository` owns all SQL domains and transaction helpers | One `SQLiteUnitOfWork`/connection policy + independent game/analysis/account/job/puzzle/read stores | `lichess.repository.GameRepository` delegates to those stores with same public API |
| `LichessService` owns auth, streams, sync workers and workflows | Auth/session/events/API adapters + use cases + explicit background/control policy | `lichess.service.LichessService` keeps existing Qt signals/methods |
| Engine lifecycle mixed in UI/worker functions | `StockfishManager` implements process lifecycle and cancellation port; `AnalysisCoordinator` checks authorization | Existing analyzer and batch runner APIs delegate to manager; no second engine |
| Direct per-operation daemon threads and hidden queue/locks | One registry/policy coordinator plus independent interactive/safety/background/engine/write lanes | Existing signals forward lifecycle/progress IDs to UI |

No benchmark, new product feature, Rust toolchain, data migration, WAL behavior change or secondary database is part of this map.

### W5 — background operation registry and lanes

- `application.background_work.BackgroundWorkCoordinator` records operation id, kind, lane, account/auth generation context, progress, terminal state, and cancellation/error reason. It starts an independent thread per operation; it is a registry/policy component, not a shared blocking executor.
- LichessService's existing `_thread` compatibility method registers work with the coordinator when one is supplied; standalone service construction continues using its previous daemon-thread path.
- Bounded HTTP requests use a priority gate that serializes a single transport call and admits interactive/safety work before queued background requests. NDJSON streams do not hold that gate. Archive sync no longer owns a lock across its whole scan and yields between HTTP pages.
- Retry-After is guarded independently from request admission so streams and short requests observe the same cooldown without a stream blocking a board move.
- Runtime shutdown waits for registered service work before repository shutdown; if work remains alive the close attempt is retried rather than closing its SQLite dependency.
- Structured diagnostics accept the random operation id, but not account identity or secret-bearing execution context.

### W6 — Stockfish process ownership

- `engine_manager.StockfishManager` owns python-chess UCI subprocess registration, creation refusal during application shutdown, one cached process for local position evaluation, release and close-all.
- `ApplicationRuntime` owns one manager; completed-game, batch and position evaluators receive its factory/position lease. Existing analyzer/batch APIs remain compatible and continue to own search settings/results.
- Search interruption remains on python-chess `Analysis.stop()` plus the existing stop events. No global running-engine queue was added.
- The manager is a lifecycle boundary; it is not an authorization bypass. Connected post-game analysis remains gated by the existing live Lichess Fair Play preflight and current session/account checks, and batch analysis retains its per-job fail-closed safety check.

### W7 — extracted UI views and dialogs

- `ui.dialogs` contains the Settings, online-game, batch-analysis, active-games, and OAuth/PAT dialogs. `ui.analytics_dialogs` contains statistics/detail dialogs and evaluation/rating graphs. The previous `chess_coach.window` import names are compatibility re-exports.
- `ui.pages` contains Home, Play, Result, Library, and Puzzle History views. Home/Play/Result take callbacks from MainWindow; Library emits selection/actions; Puzzle History renders local account/attempt data and offline practice. The existing `GameReviewWorkspace`, `BoardView`, `MoveTable`, dashboard formatters, and theme components are reused.
- The modal Library and Puzzle History presentation remains in its prior dialog shell, preserving current routing and behavior; these views no longer build their internal widget trees inside `MainWindow`.
- `MainWindow` still owns application-level async routing, live session state and some workflow callbacks. It no longer defines the extracted view classes or home/play/result/library/puzzle view markup; remaining shell/workflow boundary review continues in W8.

### W8 — privacy-safe diagnostics and registered shell operations

- MainWindow-owned dashboard refresh, batch Stockfish, position evaluation, engine installation and completed-game analysis use the runtime coordinator. Each keeps its own thread and cooperative stop event; lanes are metadata/admission policy, not one blocking application queue.
- The coordinator places a random operation id in diagnostic context for the worker lifetime and emits start/finish events with stable operation kind, lane and status. Cancellation reasons are allowlisted static codes; arbitrary caller text is discarded.
- Diagnostic context only accepts a strict random operation id. Structured event fields remain allowlisted; exceptions contribute type/code and bounded function/line trace, not message or payload. Tests cover secret-like error text, email, filesystem path, invalid IDs and reason-code redaction.
- Remaining direct threads are intentionally local owners: OAuth helper/service workers under their service lifecycle, statistics/opening-index workers owned by the modal statistics dialog, and short-lived UCI probe/process monitoring. They are not placed behind archive/engine work; W9 must validate application close and preservation of their existing lifecycle contracts.
- Current W8 audit does not add empty future plugins. Competency/educational model and repertoire tree remain documented future contracts; no GPT, ML, Rust, database schema or settings-key change is included.

### W9 — delivery gate

- Full local Windows Python 3.12 run: **589 passed**; compileall, isolated offline `QApplication`/`MainWindow` init-close, and diff-check passed.
- Hosted Windows CI must be linked from the exact published head SHA before this architecture task is considered complete. Physical GUI acceptance remains with the owner after independent review.
