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

| Этап | Исходные точки | Проверяемое изменение |
| --- | --- | --- |
| W0 | `window.py`, `repository.py`, `service.py`, entrypoints, tests | Этот baseline/map, imports/routes/threads inventory |
| W1 | package root, DTOs/contracts, entrypoints | `domain` types/ports, explicit bootstrap, architecture import tests |
| W2 | `window.py`, `session.py`, `analysis.py`, `archive.py` | game/app-state/analysis/dashboard coordinators; Qt handlers forward only |
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
