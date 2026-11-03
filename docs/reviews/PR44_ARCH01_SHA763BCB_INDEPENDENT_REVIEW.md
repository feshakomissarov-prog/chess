# PR #44 ARCH-01 — независимый source review, SHA 763bcb2

**Дата:** 2026-11-03 · **Точный head:** `763bcb25a5c83feeb583db135823e7dd5135e8b6`.  
**База:** `main` `3e7f2cd48b16762062e9414761efd13556b55837`.  
**PR:** https://github.com/feshakomissarov-prog/chess/pull/44 — open, draft, unmerged, mergeable.  
**Exact-head CI:** https://github.com/feshakomissarov-prog/chess/actions/runs/41703457406 — SUCCESS; job #126152487481 лог: **589 passed in 296.01s**; compileall и isolated offline window init PASS. 12 коммитов, 56 изменённых файлов.

## Вердикт

**NO-GO merge.** Положительный результат технической реструктуризации подтверждён, но обнаружены **три P1**: реальная GUI-регрессия в специальных режимах, неполная координация ограниченных сетевых потоков и отсутствие лимита конкурирующих Stockfish-процессов. Дополнительно **P1 архитектурный контракт**: целевой use-case/Fair Play/analysis owner из исходного ТЗ во многом остался в `MainWindow`; не выдавать перемещение виджетов за завершённый рефакторинг. **Не утверждается, что весь интерфейс протестирован физически**. Никакого выявленного нового P0 Fair Play bypass в просмотренном коде; отсутствие выявления не означает доказательство полной безопасности.

## ARCH-FIX-01 · P1 · Полный CLI → runtime → MainWindow нарушает --offline / --manual-connect UI

**Воспроизводимость по точному исходнику, без догадок:**
- `src/chess_coach/__main__.py::main()`: `runtime = create_runtime(offline_mode=args.offline, manual_connect_mode=args.manual_connect, asynchronous_storage=True)`; далее `MainWindow(runtime=runtime)`. Старые keyword flags к `MainWindow` не передаются.
- `src/chess_coach/window.py:260–273`: `self.offline_mode = self.runtime.offline_mode`, `self.manual_connect_mode = self.runtime.manual_connect_mode`, т.е. фактическая информация *есть*.
- `src/chess_coach/window.py:390–401`: после `if self.lichess` написано **`elif manual_connect_mode:`** и **`elif offline_mode:`**, где это *аргументы __init__ с default False*. При штатном CLI через runtime оба false; блоки не выполняются.
- `window.py:608–619` `_disable_online_controls()` только из этой ветки должен выключить `connect_button`, `friend_link_button`, `username_button`, `ai_button`, challenge/import UI и показать «Офлайн-режим · Lichess отключён». В штатном `--offline` его не вызывают; в `--manual-connect` отсутствуют явные подпись/tooltip ручного режима. Низкоуровневая offline-изоляция в `ApplicationRuntime` сохраняется (нет LichessService), но визуальный и интерактивный контракт сломан.
- `tests/test_launcher.py` подтверждает передачу **только** `runtime` в FakeWindow, `tests/test_architecture_w1.py` проверяет отдельную фабрику, CI offscreen smoke использует `MainWindow(offline_mode=True)`, то есть не тестирует новый реальный путь.

**Исправление:** единственный источник истины `self.runtime.offline_mode/manual_connect_mode`; не дублировать параметры. Вариант: после проверки совместимости аргументов использовать исключительно `self.offline_mode`/`self.manual_connect_mode` для всех последующих UI/safety conditions. Не допустить открытия Credential Manager в manual до явного клика; offline вообще не должен создавать LichessService, читать QSettings личного профиля или идти в сеть.

**Тесты:** интеграционный `launcher.main(["--offline"])` и `launcher.main(["--manual-connect"])` с real QApplication/MainWindow, fake single instance/isolated paths and fake credential stores, не FakeWindow; assert visible `connection.text()`, `connect_button` enabled/disabled, online action buttons disabled/hidden, offline DB=temp only, no network, manual no credential get until user intent. Test both direct legacy ctor and injected runtime ctor; normal startup auto restore unchanged. `show()/processEvents()` и проверка Qt visibility, не только полей.

## ARCH-FIX-02 · P1 · Длинные archive/puzzle NDJSON streams обходят новый приоритетный API gate

**Источник:** `src/chess_coach/lichess/api.py:48–64` `_PriorityRequestGate` сериализует и отдаёт приоритет только `LichessClient._call()` (`api.py:249`). Но `stream_ndjson()` (`api.py:550–615`) **полностью обходит gate** — обоснование в комментарии «Long-lived event streams do not reserve the bounded-request gate». Тем же `stream_ndjson()` вызываются **не только живые** `stream_events/stream_game`, но и **конечные многопартийные exports** `stream_user_games()` (`api.py:377–394`) и `stream_puzzle_activity()` (`api.py:396–406`), у которых возможны страницы до **10 000 записей**. Таким образом archive и puzzle stream могут работать одновременно и вне declared low-priority admission; interactive/safety request имеет приоритет лишь среди `_call`-запросов, но не среди этих stream starts. Это не доказывает реальный lag; **доказывает отсутствие обещанной координации**.

**Исправление:** классифицировать streams явно: 
- persistent game/event streams не удерживают bounded gate и живут независимо; high priority, свои reconnect/stop/429 semantics;
- finite archive / puzzle NDJSON получают **low-priority bounded admission/budget**, limit concurrent finite streams на одного клиента (например 1), chunking checkpoint с разумным max page и cooperative yield/cancel между чанками; high priority short HTTP и safety должны быть допущены без ожидания целого многотысячного export; не удерживать shared gate на весь stream и не запирать игру/часы.
- сохранить documented API parameters, backfill TTL/daily quota, 429 Retry-After, account/generation isolation. Если серверный транспорт не поддерживает preemption in-flight, явно описать это ограничение и обеспечить bounded chunk/request size/timeout; не утверждать абсолютный priority preemption.

**Новые детерминированные тесты:** fake transport с `stream_user_games` + `stream_puzzle_activity` одновременно; assert finite stream limit и порядок admissions, interactive `send_move`/safety допускается раньше ожидающего background между чанками; live event stream остаётся независим; cancellation while waiting and app shutdown don't hang. В текущих `tests/test_lichess_background_lanes.py` первая проверка использует `FakeArchiveSync/Client.request`, **не настоящий `stream_ndjson`**, а вторая проверяет только `_call`. Дополнить настоящими транспортными фейками.

## ARCH-FIX-03 · P1 · StockfishManager — реестр процессов, но не единый admission/resource policy

**Источник:** `src/chess_coach/engine_manager.py::StockfishManager.open_engine()` **каждый раз** вызывает `self._engine_factory` и заносит новый процесс в `_engines`, без проверки занятости другого foreground/batch/position процесса. `position_engine()` кеширует движок отдельно, но живёт параллельно `open_engine()`. `window.py` передаёт `engine_factory=self.stockfish_manager.engine_factory` независимо для batch и completed-game run; position evaluation использует `position_engine()`. Итого менеджер в настоящий момент *владеет* процессами, но **не ограничивает** параллельную вычислительную нагрузку. `BackgroundWorkCoordinator` только присваивает lane/status, engine semaphore/scheduler отсутствует. Для целевой машины 4 CPU/8GiB из нашего ТЗ это не завершённый W5/W6 acceptance.

**Исправление:** разделить admission на `interactive completed-analysis`, `local position`, `batch` с общим bounded policy, предсказуемой очередью/уступкой, отменой и гарантией, что 2 больших анализа не запускаются без явного обоснованного разрешения пользователя. Сохранить исходные бюджетные UCI параметры, один живой reusable position engine при возможности, без nested Qt waits/мертвых блокировок. Проверку Fair Play выполнять **и перед admission, и после ожидания** непосредственно до запуска/продолжения Stockfish; затем при сохранении результат снова сверяется с lease/account/revision. Если новый gate добавляет ожидание — сделать его cancellable и безопасным при exit.

**Тесты:** fake engine factory c counters + synchronization barriers: request batch и completed game одновременно, position eval, limit configured 1 engine process (или документированный иной bounded policy), cancel while queued doesn't start factory, auth generation revoke while waiting never starts engine, close_all drains, no silent missing result; no real engine in CI. Отдельная регрессия: `position_analysis.evaluate_position()` сейчас получает `position_engine()` **до** `stop_event.is_set()` — pre-cancelled call не должен создавать engine. Не вводить Rust и performance benchmarks.

## ARCH-FIX-04 · P1 architecture gap / честность выполнения W2, W6 и W8

**Факт:** `window.py` после переноса визуальных компонентов — **≈4 906 строк**, более чем 150/200 связанных методов, и по-прежнему владеет состояниями `_analysis_safety_request_id`, `_analysis_safety_context`, `_review_safety_request_id`, `_analysis_worker_context`, `_position_eval_safety_lease`, workflow `_start_batch_analysis`, `_run_background_sync`, `_request_review_training_safety`, DB-aware `_refresh_home_dashboard` и raw application logic. `application/game_sessions.py` — только registry/selection (55 строк), `application/app_state.py` — settings snapshot (75 строк); исходные `application.analysis_coordinator`, `application.fair_play`, `application.archive_sync`, `application.dashboard_queries` или функциональный эквивалент владельцев **не выделены**. `docs/architecture/ARCH01_CURRENT_TO_TARGET_MAP.md` само признаёт «remaining analysis/archive/dashboard workflows migrate incrementally». Это несоответствие широкому заявлению «ARCH-01 W0–W9 полностью сделано» и требованию следующего блока иметь тонкую оболочку UI.

**Избежать бессмысленной перекладки строк и второго safety источника:** выделить *минимально достаточные* application coordinators для: (а) ownership/invalidation of safety lease; (б) completed-game and batch/position engine requests with bounded admission; (в) archive/ready-backfill orchestration; (г) dashboard snapshot job ownership. UI должен направлять намерения и показывать состояния, не удерживая самостоятельное право «разрешить Stockfish». Интегрировать с уже существующим preflight проверенным fail-closed (не переписывать его логику заново). Отдельно отметить поддерживаемые compatibility facades и оставшийся долг; нет цели «<500 строк любой ценой» или полного переписывания сложной логики за один коммит.

**Тесты:** integration seam `intent→application coordinator→Fake Lichess/Stockfish/SQLite→UI mask`; `allow→revoke→allow` и 429/reconnect/logout/session switch; no stored eval/PV surfaced while live human; reentry after wait safe; no new DB schema/migrations or analysis math. AST import-boundary tests сейчас проверяют только будущие `domain/application` packages, но **не** исключают прямые `window→sqlite/engine/network` зависимости, так что они не доказывают достижения цели. Можно оставить TODO с точной ответственностью, если перенос конкретного обработчика слишком рискован, но нельзя считать критические safety owners «completed» без выделения.

## Неблокирующие замечания

- `application/background_work.py::BackgroundWorkCoordinator.close()` ждёт зарегистрированные потоки, но **сам не подаёт stop_event**; полагается на предшествующие owner shutdown. Это допустимо лишь если контракт explicit, есть bounded exit с UI retry, и tests of uncooperative/in-flight workers. Не заявлять принудительную остановку.
- `_PriorityRequestGate` ожидает через `Condition.wait()` без cancel token; при shutdown в ожидании долгого запроса low-priority worker может задержать app quit. Добавить отмену с сохранением FIFO/high-priority fairness в рамках ARCH-FIX-02.
- Текущие Qt/UI tests и 589 PASS — хорошие реальные подтверждения отсутствия ряда прежних регрессий, но ни real Windows pixels/DPI/audio, ни Lichess/OAuth/PAT, ни пользовательная SQLite физически не проверены. Не считать CI физическим пилотом.

## Стабильные результаты, которые важно сохранить

- W3: тематические SQLite `games/analysis/jobs/puzzles/archive`, один `GameRepository` façade, схема `SCHEMA_VERSION=19`, backup/CAS не изменены.
- W4: `LichessService` QObject facade с сохранёнными Qt Signal signatures + service_auth/games/sync mixins, единый client и token stores.
- W7: pages `Home/Play/Result/Library/Puzzles` и extracted `Settings/Statistics/OnlineGame` dialogs; публичные импорты сохранены, UI не редизайнили.
- W8: correlation IDs, redaction tests и без секретов в логах, без Rust/microservices/новых зависимостей.
- W9: exact-head Windows 589 PASS, compileall/offline init success; реальный пилот всё ещё необходим.

## Условия нового GO

1. Исправить ARCH-FIX-01–04 **в этом же draft PR #44**, не трогать main и не merge.
2. Не создавать отдельный giant rewrite и не ломать уже выполненное разбиение. Минимальные semantics-preserving changes с новыми детерминированными contract/Qt/worker тестами и без ослабления старых 589 тестов.
3. Запустить полный Windows CI exact SHA, compileall, изолированный **настоящий CLI** offline/manual smoke, fake transport concurrency, fake engine admission, cancel/rollback/429/Fair Play transitions и `git diff --check`.
4. Отчёт Codex: SHA, exact CI, фиксы по IDs с путями/строками, evidence для bounded lanes и single admission, честный перечень неизвлечённых workflow owners. **Следом независимое ревью, затем физическая ручная проверка владельца, потом отдельное решение merge.**

