# Codex ТЗ — ARCH-01: архитектурный рефакторинг Chess Coach без новых функций

**Статус:** утверждённое к передаче после объединения M4.6; архитектурный документ: [CHESS_COACH_TARGET_ARCHITECTURE.md](CHESS_COACH_TARGET_ARCHITECTURE.md). Целевая машина — обычный ноутбук Windows 10/11, 4 CPU cores / 8 GB RAM / SSD. Python 3.12 + PySide6 остаются. **Не внедрять обязательные бенчмарки, SLA или Rust на этом этапе.**

**Цель одного крупного рабочего задания:** Codex открывает **один отдельный draft PR ARCH-01 от последнего `main`**. Внутри него можно и нужно сделать последовательные осмысленные коммиты/вехи с локальными тестами. Ни одного промежуточного merge; финальная последовательность: Windows CI exact SHA → независимое code review → физическая ручная проверка владельца → явное разрешение merge. Не затрагивать PR #41/#42, уже принятые M4.6 behavior и чужие старые ветки.

## Неприкосновенные инварианты — читать перед изменением кода

1. **Поведение пользователя не меняется.** Тёмная тема, sidebar, карточки и три последних партии, два больших часов Lichess 3+2, звуки/настройки/предпрослушивание, все UI route и open game/PGN, local play, online friend/AI games, premove+cancel, finish, analytics, puzzles, review graph, saved PV, упражнения по ошибкам и диалоги должны остаться теми же.
2. **Fair Play строго fail-closed.** Сохранить запрет engine eval/PV/hints/профилей классификации во время активных человеческих Lichess партий или при неизвестных данных, 429, reconnect, logout, revoke. Синхронная маскировка при отзыве lease. Любое новое safety permission — свежий полный preflight, привязанный к account/auth_generation/game/revision/hash/route; автоматический credential restore не выдаёт разрешение. `--offline` допускает местный анализ только в **физически изолированной** offline конфигурации; `--manual-connect` ничего не читает и не подключается до клика.
3. **Достоверность аналитики:** отдельно official Lichess `players.*.analysis.accuracy`/`analysis[]` и local Stockfish; cp/mate/missing — типизированные раздельные состояния, никаких «мат = +10/20 пешек». Никакой локальной выдачи доли хороших ходов за Lichess accuracy. Не пересчитывать метрические версии без отдельного ТЗ, не менять thresholds или \(n/N\) формулы.
4. **Целостность данных:** один SQLite file, те же user paths, backup, schema version, account scoped ownership, transaction/line CAS, writer policy, durable queue/run/workspace. No silent remigration/downgrade, no deleted user data, no change in PGN lineage.
5. **Сеть и токены:** OAuth/PAT в разных Windows Credential Manager slots; ни токена в QSettings/log/URL, ни неожиданных OAuth windows. Backfill лишь готовой Lichess аналитики, без внутреннего API заказа серверного анализа, rate-limit 429/Retry-After, TTL/no_server_data, account generation, дневной бюджет и пользовательская пауза.
6. **Stockfish:** native UCI subprocess hidden on Windows, controlled cancel/stop before process creation and before commit; budget 1–10м batch + existing single-game option, resume with original params. Не показывать CMD, не создавать параллельные engines в конфликтующих сценариях.
7. **Никакой реальной личной информации и API в автотестах.** Only fake HTTP/credentials/Stockfish, synthetic PGN and temp SQLite. Не загружать пользовательские видео/скриншоты, PAT, PGN или DB в GitHub.
8. **Нет новых продуктовых модулей:** ни дебютного графа, ни GPT/AI tutor, ни ML/voice, ни cloud services, ни собственной замены chess engine, ни переписывания UI.

## Измеримый результат этого PR

Не гнаться за минимальным числом файлов/строк. PR должен дать:
- `MainWindow` как root shell/wiring, а не владельца HTTP/DB/Stockfish; отдельные виджеты, страницы, диалоги и use-case coordinators с явными API.
- `GameRepository` как тонкий совместимый façade с доменными хранилищами и **одной транзакционной дисциплиной**, а не центральный 3000-строчный объект.
- `LichessService` как façade/adapter без самостоятельной работы со всеми archive/puzzles/ready-analytics/business policies.
- Согласованный **background coordinator** с явными ресурсными линиями и порядком приоритетов **без блокировки живых ходов** большой очередью.
- `StockfishManager/AnalysisCoordinator` с единым жизненным циклом, поддерживающим все существующие сценарии.
- Pure domain read models/provenance/metrics, которые будущие образовательные фичи смогут использовать без импорта PySide6/SQLite и без новой реализации сейчас.
- Разумная обезличенная корреляционная диагностика I/O/состояний транзакций.
- Сохранённый существующий test suite, новые границы и deterministic гонки, документация схемы зависимостей и manual smoke plan.

## W0. Baseline, инвентаризация и стоп-лист

Прежде чем переносить:
1. `git fetch`, branch **от актуального main**, фиксировать SHA; проверка отсутствия незакоммиченной работы в выбранном рабочем каталоге; личную `.venv312/` не удалять.
2. Полный тест suite в исходной версии (на принятом M4.6 было **553 PASS**, но точное актуальное количество может вырасти), `compileall`, `git diff --check` и isolated offline GUI init. Указать runtime Windows/Python/Qt и сохранять baseline list.
3. Inventory current public imports and runtime routes: `window.MainWindow`, `window.SettingsDialog`, `lichess.repository.GameRepository`, `lichess.service.LichessService`, `lichess.analysis.CompletedGameAnalyzer`, `lichess.batch_analysis.BatchAnalysisRunner`, UI graph/components, `__main__`, CMD launcher, shortcut, single-instance.
4. Снять карту зависимостей (статическая Python AST imports) и места прямых `repository`/`lichess`/`engine` вызовов из Qt; пометить pending/disconnected/auth-generation paths, all long-running work and database ownership. Получить короткий текст `docs/architecture/ARCH01_CURRENT_TO_TARGET_MAP.md` с исходными точками и планом переноса.
5. `README`/ROADMAP/PRD/PCD не переписывать ради архитектуры; добавить ссылку на готовые docs только при необходимости. Никакие измерения FPS/p95/размеров memory **не требуются**.

## W1. Composition root, DTO и контролируемое направление зависимостей

1. Создать `bootstrap.py` или `application/composition.py`, где явно собираются **ровно одна** service instance, repository, background coordinator, analysis safety, engine manager и UI. Не создавать новый глобальный Service Locator или множество import-time singletons.
2. `domain` с `GameRef(account_id/game_id/line_revision/hash)`, `GameSnapshot`, `ClockSnapshot`, `AnalysisScore(cp|mate|missing)`, `AnalysisSource`, `SourceCoverage`, `JobContext(generation/operation_id)`, `Cancellation` и портами/Protocol. Только реально применяемые типы, без сотен классов с одним полем. Dataclasses frozen где разумно.
3. Для Python 3.12 явные thread ownership и boundary conversions: `Qt signals → application DTO`, `JSON/NDJSON → typed DTO`, `sqlite3.Row → immutable read model`. Не отдавать живой SQLite.Row/Qt widget в worker.
4. Сохранить `python -m chess_coach`, CMD, single-instance, working directory, icon, packaged assets. `Settings` local, no tokens exposed. Existing import paths через thin re-export until tests migrated.
5. AST architecture test для запретов `domain -> PySide6/sqlite3/infrastructure/ui`, `application -> PySide6.QtWidgets`, `infrastructure -> ui`; не делать чрезмерно строгий линтер, если API намеренно uses Qt QObject bridge — локально задокументировать исключение.

**Gate W1:** Old entrypoints, fake app init and baseline tests; contract tests for DTO typing and no unintentional new DB/network calls during import.

## W2. Выделить use-case coordinators из MainWindow

1. Разделить сценарии (не UI) на `game_sessions` (online/local moves/active games), `analysis_coordinator` (completed-game analysis+saved review/training), `archive_sync`/backfill, `dashboard_queries`, `app_state` (route/workspace). Не дублировать экземпляры `LichessService` и не создавать вторую правду о current game/auth.
2. Существующие Qt `Signal` обработчики делать тонкими forwarding handlers. При настоящем authoritative `gameFull/gameState` обновляется состояние одной сессии; типовые часы +2, premove, reconnect, live route ведут себя **побитово/семантически** как до переноса.
3. `MainWindow` не получает собственных прав на engine or analysis approval; передаёт intent use-case, получает read model и signal render.
4. Любой переход state пишется с explicit game id/generation/operation id; stale async callback не переоткрывает старую страницу или не меняет новую партию.
5. Сохранить current `ReviewWorkspace` view models/screens без смены content. UI может временно ссылаться на совместимый façade, это **временный контролируемый долг**, не повод для rewriting all tests.

**Gate W2:** source tests на auth/game/safety; synthetic AI game 3+2 and focus return; UI stale async callback and no scoreboard disclosure after safety revoke.

## W3. SQLite repository split при неизменной физической схеме

1. Выделить `infrastructure/sqlite/transactions.py` + `migrations.py` и тематические `games.py`, `analysis.py`, `accounts.py`, `jobs.py`, `puzzles.py`, `read_models.py` только там, где чёткая ответственность. **База одна**, migration version и schema не меняются при переносе.
2. `GameRepository` остаться стабильным public façade; тесты и пользовательские команды должны не заметить перемещения функций. Не допускать `GameRepository()` или отдельные sqlite connections на каждую кнопку через widget. Соблюдать writer thread/connection ownership.
3. Запись `save_snapshot/import_archive/merge_ready_analytics/analysis_run` — transaction boundary with CAS `line_revision/hash`, account ownership и cancellation check **внутри одной транзакции**; sync event stream state and DB writes bounded; never await HTTP while holding `BEGIN IMMEDIATE`.
4. Key/value read model for home/analytics bounded query, three last cards, correct accuracy source coverage, no costly full PGN parse in UI; invalidate cache when account/game revision changes. Don't proactively rewrite query logic or indexes if unnecessary.
5. Keep pre-migration backup logic and `PRAGMA integrity_check`; preserve current queue resumes/backfill budget and `no_server_data` 7-day TTL, source L vs S.
6. **Не включать WAL или новые connection pools только потому что «быстрее»**; если нужно поведение изменить, сначала отдельный deterministic proof and note; допускается оставить текущий mode.
7. Fake concurrent commit test: game revision replaced mid-background request => no partial mutation; auth cancel before commit => rollback; simultaneous reader/writer; crash/restart at durable cursor; no leaked files/connections on shutdown.

**Gate W3:** existing synthetic SQLite migrate/backup suite passes, no schema diffs, idempotent backfill, one writer contract, all account scopes, compatibility facade import tests.

## W4. Lichess adapters и изоляция auth/event lifecycle

1. `LichessService` как стабильная façade with same public Qt Signals and methods, delegate to separated auth/session stream, archive, ready analytics, puzzle adapters and API client. Не создавать новую OAuth flow или переносить credential slots.
2. Thread-safe auth generation and stream-ready semantics; blank NDJSON keepalive is not move event; reconnect 429/401/403 revokes safety lease, does not spuriously reconnect or grant analysis.
3. Convert Lichess `players.*.analysis.accuracy`, `analysis[]` per ply, PGN eval, mate=0, source label correctly via existing parser `analysis_json.py`. Keep depth preference/PGN provenance and backup local originals.
4. Preserve per-account rate limits, shared client policy, retry-after and no-server-data; background auto after confirmed event stream, no requests on empty queue, no loops 10min re-requesting same game.
5. Token access only from secured selected store inside worker, not UI, logs or mutable global. `--manual-connect` (no reads/network until intent), `--offline` (no client/net/secrets), normal auto restore (no OAuth popup).

**Gate W4:** synthetic OAuth/PAT switch/revoke/429, silent stream, fake JSON official schema, wrong line/account, daily budget/TTL, no retry storm, live-human game causes pause. All old tests retained.

## W5. Реальный background coordinator с приоритетами, НЕ общая блокирующая очередь

**Самый рискованный пункт, потому что изменяется orchestration и возможны deadlocks.** Сначала написать **инвариантные fake-worker тесты и схему событий**, потом перенести.

1. Добавить `BackgroundWorkCoordinator` с реестром задач `operation_id`/type/account/generation/priority/status/progress/cancellation/last_failure и lifecycle`; UI получает current work snapshots. Не расширять DB schema generic job table, переиспользовать domain-specific durable states (archive, ready analytics, batch).
2. **Конкуренция по resource lanes:** Qt event loop никогда не блокируется; live game event stream and submitted moves on separate interactive path; control/safety active account list high priority; background Lichess I/O serial under rate limits *per request* и yielding between requests; engine in separate manager; SQLite writes serialized transactional. Network streams долго живущие и не захватывают lock для background operations.
3. Убрать прежнее удержание `_background_api_lock` на полный многопартийный archive scan, если оно блокирует high-priority operations: implement request-level scheduler/admission with bounded per-request timeout, chunking, cooperative pause. Приоритетная защита **между requests**, не убивать транзакцию/HTTP в середине без cancellation semantics.
4. Structured state machine: `queued/running/paused/cancelled/failed/rate_limited/stale/completed`, account+auth_generation owner, no stale progress, exception propagation to UI, no repeated modal notices. When user playing human/unknown/reconnecting, do not start low-priority engine analysis or leak scores; background ready-analytics pause by policy.
5. Fairness: постоянно идущий ArchiveSync не должен навечно starving puzzles/backfill. Не запускай 3300 запросов на startup; current budget 25/day and max10 per portion unchanged, user opt-out respected. Big operation may be resumed from durable cursors exactly once or idempotently.
6. Shut down: cancel signals -> worker completion -> drain/rollback DB writer -> close engine/HTTP -> Qt destroyed. No dangling `QThread`, stale bound callbacks, double-connect signals, or race blocking app quit. Clear error if safe shutdown cannot finish; never `terminate()` Python blindly with active commit.
7. Contract tests fake scheduler: interactive move/active-game safety check happens **before next** background request; backfill paused mid-cursor, resume same account; auth switch rejects stale; 429 Retry-After, worker error, cancel before/after reserve; offline=zero network; mock SQLite txn does not run during HTTP waiting; fake UI event loop always processes input without sync wait.

**Gate W5:** all old tests + new deterministic barriers (threading Events, not arbitrary sleeps), no queue deadlocks, no CLI/sound regression, no Fair Play bypass.

## W6. Stockfish manager и отдельная безопасная orchestration

1. Вынести discovery/UCI subprocess lifecycle/analysis cancellation из `MainWindow` в `StockfishManager` (реализует порт). Не менять `SimpleEngine` calls, official install source, native compiled Stockfish binary, windows `CREATE_NO_WINDOW`/equivalent and current hidden UCI behavior.
2. Context contains verified game+line+lease; no engine_factory when stop_event already set; if revoke while running — kill/quit per existing semantics, drop pending callback; save only if generation and revision still valid in DB transaction.
3. Preserve single finished game analysis, batch passes, selective MultiPV, node/depth budgets (including **1 000 000**), threads/hash, engine version and queue original params; no silent node cap or reconfigure MultiPV in incompatible UCI scope.
4. Engine admission policy consistent with priority lanes: local analysis may use CPU; while active human online, **no engine eval/hints even if window hidden**; offline isolated runs allowed. Account resumed/429 cannot implicitly reopen cached analysis.
5. Integrate with `AnalysisCoordinator` and existing saved completed-game ReviewWorkspace only after valid permission, preserving all masking paths. Screen navigation does not spawn engine.

**Gate W6:** fake UCI subprocess creation counts, revoke/pending, before-commit cancellation, started job resume, build errors, no CMD window on real device acceptance; **no real Stockfish executable in CI**.

## W7. Extract UI views/dialogs and keep interaction exactly the same

1. Move non-shell dialogs from `window.py` into `ui/dialogs`: `SettingsDialog` (tabs+sounds+Stockfish+Lichess), `BatchAnalysisDialog`, `OnlineGameDialog`, connection, active games, statistics; preserve parent/ownership, focus, modality, signals, Cancel/OK atomic settings and compatibility re-exports.
2. Put pages into `ui/pages`: Home, Play, Game Review, Analytics, Library, Puzzles; reuse established `ui/game_review.py`, `ui/move_table.py`, `BoardView`, `ui/theme.py`. Do not copy layout markup or create second board view in parallel. Reuse proven child components and shared theme tokens.
3. `MainWindow` only initializes/wires components, routes requests, applies read-model output, owns Qt root state. Preserve sticky resume state/route, 3 game cards, correct two home actions, score masking, correct graph mate markers/continuous cp lines, sound selections and first/second-game sound, 3+2 two clocks.
4. Avoid Qt double-parent errors: widget belongs to one layout, signals not connected twice after route rebuild, QObject threading safe, `deleteLater` on right thread. Keep scale and font sizes; no CSS-only emergency fixes.
5. Reference screenshots/geometry tests at `1024×700`, `1280×720`, `1360×860`, `1920×1080`; tests use synthetic/Qt offscreen. Don't require real HiDPI screen in CI; physical owner's DPI to confirm. No new graphics or redesign.
6. Keep existing import-compat `chess_coach.window.MainWindow` and test helpers. Document any temporary deprecated aliases.

**Gate W7:** launch via .cmd/.lnk and existing single-instance; all UI Qt tests pass; no hidden dialogs/screens after extraction and no changed visual hierarchy.

## W8. Source-backed diagnostics and future-proof educational boundaries

1. Extend existing privacy-safe logger: `operation_id` across `auth → network ready → background queue → schema parse → SQLite CAS → UI refresh`; `log_event` with event kind and redacted IDs; reason codes for cancel/paused/error/429. Do not log every move, clock tick or FEN to maintain privacy and responsiveness.
2. Existing logging rotation/anonymized report retained, opt-in behavior unchanged; no auto-upload, no token, raw user game, private account or credentials in logs, including exception text. Test redaction of malicious HTTP exception strings containing fake token-like text.
3. Add small pure model for `Fact(source/version/evidence/coverage/uncertainty)` **only if used by current metrics read models**; otherwise document future contract without scaffolding empty plugins. Chess competency model A-B-C-D-E is **future work**, no GPT prompts/network, ML model or opening repertoire tree this PR.
4. Architecture import checks + scope tests; update docs map, source ownership, transaction lock map, key API/signal flow and troubleshooting using correlation IDs.
5. No time budget, p95 regression gate, 3k/10k/50k benchmarks or Rust FFI now. **Future** perf phase may instrument CPU-bound bottlenecks, then choose Rust/PyO3 or C++ only after proof.

**Gate W8:** log/exception/account redaction tests, review documentation links, no unexpected new dependencies, no drift in settings paths/version.

## W9. Финальная приёмка PR (не автоматическое слияние)

**CI:** полный Windows Py3.12/PySide6 suite на exact SHA, compileall, `git diff --check`, isolated offline QApplication; baseline 553 tests retained or superseded **without weakening assertions**. All new tests deterministic and fake. CI run URL exact head and full passing count required. If modifications create flaky tests under CI, fix root cause rather than rerun/skip.

**Manual smoke checklist, to be executed by owner after independent code review:**
- Normal desktop shortcut and auto token restore (no OAuth UI when stored), manual/offline modes and single instance.
- Lichess AI 3+2 both colors, clock initial/turn/increment, mate/finish, second new game, moves/premove/undo restrictions.
- Sound game start/move/capture selected preview, persistence and actual Windows audio.
- Saved completed review: board, PGN, synchronized move list/wheel, graph cp vs mate, both sides statistics, training on verified alternative, no engine when opening review.
- Lichess accuracy JSON backfill 5–10 old games where official source exists, UI background progress, 429 pause/retry, source labels, no overwriting personal PGN; archive session change. Avoid displaying engine hints during active human game.
- Settings multiple tabs at current DPI, nodes=1_000_000, cancel/save; batch pause/resume and no CMD; local SQLite persists after restarts.
- Loss of network/auth, logout and stale callbacks, review score mask; no invented permissions/temporary disconnected bypass.

**Mandatory delivery report from Codex:** changed-files map by W0–W9; dependencies before/after and facade deprecations; why each split improves responsibility; thread / resource lanes; safety lease owner + cancellation rules; transaction and DB schema confirmation; CI URL/sha/count; open limitations and owner manual steps; any intentionally deferred structures and why. Update `docs/PROJECT_STATUS.md` and one handoff log with actual facts; do **not** modify ROADMAP for this internal stage.

**No-go:** fail closed regresses, loss of tests, user DB migration risk, runaway threads, hidden fatal API exceptions, variable NODE limits silently truncated, changed Lichess accuracy source, new CPU-heavy blocking UI, visual/UX drift, secret in logs, exit hangs, one changed desktop shortcut. Stop and fix these before claiming completion.

**Merge:** PR must stay **draft**, no merge until explicit user approval after independent source review + actual Windows pilot. Not even a successful CI authorizes merging by itself.

## Appendix: suggested internal milestones, without splitting user's request into multiple small PRs

```text
ARCH-01a  Baseline + module map + immutable DTOs, composition root
ARCH-01b  Use-case coordinators + preserved Qt signal bridge
ARCH-01c  Split SQLite stores with one writer/transaction policy
ARCH-01d  Split Lichess adapters and add per-request background coordinator
ARCH-01e  Central Stockfish Manager + safety authorization integration
ARCH-01f  Extract UI pages/dialogs, thin window shell
ARCH-01g  Privacy diagnostics, architecture checks, full Windows CI + docs
```

Each milestone must leave a **working, testable state in the same Codex branch**. If one migration step becomes too risky, preserve a compatibility façade and document it instead of forcing a one-shot rewrite; still complete the scoped work before final PR review. Avoid intermediate merges and never promise that moving functions alone makes the program faster.
