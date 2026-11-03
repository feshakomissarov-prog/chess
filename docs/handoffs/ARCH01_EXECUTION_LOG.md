# ARCH-01 execution log

Working branch: `codex/arch-01-modular-refactor` from `main` SHA `3e7f2cd48b16762062e9414761efd13556b55837`. One draft PR only; no merge.

## W0 — baseline and inventory

- Baseline Windows CPython 3.12.10 / PySide6, Qt, shiboken6 6.12.0.
- `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -o addopts= -q --tb=short`: **553 passed in 147.45 s**.
- `python -m compileall -q src tests scripts`: PASS.
- Isolated offline QApplication/MainWindow initialization and close, with `assert window.lichess is None`: PASS.
- `git diff --check`: PASS before adding W0 docs.
- Main architectural hotspots and public imports are recorded in [ARCH01_CURRENT_TO_TARGET_MAP.md](../architecture/ARCH01_CURRENT_TO_TARGET_MAP.md).
- No user SQLite, PGN, tokens, real Lichess/OAuth, or Stockfish were accessed. No runtime behavior changed in W0.

**Next:** W1 domain DTOs/ports and explicit composition root; contract/import-boundary tests.

## W1 — domain contracts and composition root

- Добавлены минимальные immutable доменные модели (`GameRef`, `GameSnapshot`, `ClockSnapshot`, `AnalysisScore`, `SourceCoverage`, `JobContext`) и `Protocol` ports для game store/feed, анализа, отмены, событий и snapshot чтения.
- Создан `chess_coach.bootstrap.ApplicationRuntime`: `__main__` собирает один repository/runtime после single-instance проверки; `MainWindow` принимает runtime или создаёт его для совместимого прямого запуска. Внешние entrypoints `python -m chess_coach` и `chess_coach.window.MainWindow` сохранены.
- Офлайн runtime использует изолированные временные settings и SQLite; manual-connect не создаёт LichessService до пользовательского действия; обычный runtime сохраняет auto-restore. Сервис кешируется в runtime, чтобы избежать дубликатов.
- Тесты: `tests/test_architecture_w1.py` (DTO инварианты и AST import boundaries, offline profile cleanup, manual-connect intent); `tests/test_launcher.py` и ранее существовавшие проверки окна отражают новый seam и сохраняют исходные behavioral assertions.
- Первые целевые проверки выявили три устаревших тестовых patch point в `test_window.py`; они перенаправлены на реальный инфраструктурный модуль. Тесты поведения и ожидания не ослаблены.
- Целевой прогон после исправлений: **19 passed** (`test_architecture_w1.py`, `test_launcher.py` и три ключевых window tests).
- Full suite до test patch обновления: **557 passed, 3 failed** из-за названных monkeypatch-путей; после обновления целевые тесты PASS. Полный прогон будет повторён перед W1 commit.
- Архитектурная карта дополнена текущей структурой W1 и оставшимися boundary-conversion gates.
- Никаких новых библиотек, схемных изменений, реального Lichess/OAuth, Stockfish или пользовательских данных.

**Next:** повторить полный suite и isolated offline GUI smoke; зафиксировать W1, затем начать W2 use-case coordinators с тестами поведения до переноса orchestration.

## W1 gate — завершён

- Повтор полного offscreen suite после адаптации инфраструктурных patch points: **560 passed in 146.46 s**.
- `compileall -q src tests scripts`: PASS.
- Изолированный offline `QApplication` + `MainWindow(offline_mode=True)`, `lichess is None`, close и удаление только временного каталога: PASS.
- `git diff --check`: PASS (CRLF нормализация предупреждала о замене переводов строк, ошибок whitespace нет).
- Commit: `6f64f88e95dd21382d9977136caff61ea4a5499c`, опубликован в ветке PR #44.

## W2 — application state and game-session ownership (в работе)

- Выделен `application.app_state.WorkspaceStateStore`: чтение/запись resumable route, review target, geometry/sidebar/board preferences. Он использует минимальный Settings Protocol и immutable snapshot, без зависимости от Qt widgets.
- Выделен `application.game_sessions.GameSessionCoordinator`: owns registry and explicit selected game id. `MainWindow` передаёт создание сессий через `register` и совместимо читает существующий mapping/property; фоновая регистрация не выбирает партию.
- Сохранены ключи QSettings и semantics восстановления review: stale line открывается с ply 0; сохранённый review target не стирается при записи другого route; удаление resume target удаляет только game-id key, как ранее.
- Тесты `test_application_state.py`, `test_game_sessions.py`, `test_architecture_w1.py`, `test_window.py`: целевой Qt набор PASS; проверены offline restore/workspace and game selection regressions.
- W2 ещё не завершён: анализ, archive/backfill и dashboard coordinators пока остаются в старых owners. Не объявлять их вынесенными до отдельного тестируемого переноса.

### W2 checkpoint 1

- Изменённые файлы: `application/app_state.py`, `application/game_sessions.py`, `window.py`, новые unit tests, архитектурная карта.
- Целевые проверки `test_application_state.py`, `test_game_sessions.py`, `test_architecture_w1.py` и весь `test_window.py`: PASS. `compileall` и `git diff --check`: PASS.
- Commit: `e849ac43c81b9d05e17b4555e180fbe114b05ebe`, опубликован в той же ветке / PR #44.
- Поведение online callbacks не переписано: сохранённые handlers остаются authoritative, но registry/session selection теперь имеет явного application owner. Полные use-case flows ещё не извлечены.

**Next:** закончить W2 там, где перенос явно снижает ответственность без смены UX; затем W3 начать с одной SQLite transaction/connection policy и тематических store adapters, не менять схему.

## W3 — SQLite responsibility split (в работе)

- `SQLiteUnitOfWork` (`infrastructure/sqlite/transactions.py`) теперь создаёт connection с теми же timeout/foreign_keys/Row settings и закрывает её после штатного commit либо rollback. `GameRepository._connection()` оставлен делегирующим compatibility seam.
- `SQLiteMigrationMixin` (`migrations.py`) владеет initialization, backup-first проверкой, schema migration и integrity path; `SCHEMA_VERSION=19`, таблицы/DDL/data migration не менялись.
- `GameRepository` уменьшен с примерно 3000 строк до компактной фасадной части (~420 строк по текущему файлу) и наследует тематические persistence mixins: игры/заметки, архив, анализ/оценки/фазы, backfill+engine jobs и Puzzle Activity. Общая identity helper вынесена отдельно и переэкспортируется через старый `lichess.repository`.
- Один физический SQLite и существующий single-writer ThreadPoolExecutor сохранены. WAL и pool не включались; CAS-проверки и бизнес-транзакции остались в соответствующих store methods.
- Тесты без изменения личных данных: `21 passed` puzzle/connection close; `219 passed` после первых store migrations; `248 passed` combined Lichess/SQLite/analysis/dashboard suite после analysis-store split; `272 passed` после game read/notes split; `179 passed` после archive split; `40 passed` migration/backup/connection subset; `29 passed` save/revision/archive subset. Единый expanded suite после переносов: `287 passed, 2 failed` только из-за пропущенного импорта `chess` в extracted migration mixin; после фикса миграционные suite и эти две регрессии повторно прошли: `18 passed`. Полный expanded suite после этой одноимпортной правки пока не перезапускался.
- Тест `test_sqlite_uow.py` проверяет commit/rollback, foreign keys, façade UOW ownership и неизменность user_version.
- Новых зависимостей и schema migrations нет; тесты используют только временные SQLite и fake data.

**Next:** повторить единый регрессионный SQLite set после всех извлечений; затем перенести LichessService responsibilities, сохранив его сигналы/методы фасада.

## W3 checkpoint — завершённая часть

- Фасад `GameRepository` теперь 412 строк и делегирует themed stores/migrations через compatibility inheritance; прежние imports/methods остаются доступны.
- Expanded combined suite до последнего missing-import fix: `287 passed, 2 failed`; после исправления `chess` binding расширенный migration subset и обе конкретные regressions: `18 passed`. Независимо подтверждены `272 passed` на game/archive/analysis/dashboard suites до самого последнего archive/snapshot extraction, `179 passed` archive+migration suite и `29 passed` snapshot/revision suite.
- `compileall`, architecture/UOW subset: **18 passed**; `git diff --check`: PASS.
- Schema и миграции не менялись: версия 19, backing up old database прежде чем schema apply — existing tests.
- W3 код и тесты опубликованы commit `1819339a99ee19d179d98773bb3a7ffdef4e5206`.
- Перед итоговой приёмкой W9 требуется единый полный test suite, который заново покроет все переносы на точном final SHA.

## W4 — Lichess service decomposition (в работе)

- `LichessService` оставлен `QObject` фасадом с прежними сигналами, constructor/import path; основные обязанности вынесены в `service_auth.py`, `service_sync.py`, `service_games.py`.
- Auth adapter владеет token selection, account/session generation, callbacks, event stream/recovery. Sync adapter владеет архивом, puzzle activity и ready-analysis backfill. Games adapter владеет active games, challenges, move/takeback and lifecycle-close methods.
- Все методы продолжают работать через тот же `self.client`, `self.repository`, callbacks и generation; не создан новый сервис, storage slot, token flow или network client.
- Тесты API/OAuth/puzzles/backfill/stream plus facade/interface checks: **166 passed**; только fake transport/stores. Первый тестовый прогон выявил mock patch path, перенаправленный на фактический `service_sync` boundary; product path не менялся.
- CI/physical Windows Lichess/OAuth не запускались; integration tests используют fakes.

**Next:** W4 gate complete после full Lichess regressions; затем W5 вводит operation registry and independent resource lanes без единой блокирующей очереди.

## W4 gate — завершён

- Совместимый `LichessService` фасад и его API/OAuth/stream/backfill тесты прошли: **166 passed**.
- Целевой расширенный повтор Lichess после начала W5 обнаружил, что извлечение общей HTTP-блокировки оставило старую ссылку в NDJSON пути. Исправлено: поток не занимает слот коротких HTTP запросов, а Retry-After защищён отдельным lock. Повторные Lichess/regression проверки прошли.
- Реальных Lichess запросов не было.

## W5 — registry операций и независимые линии исполнения (завершён)

- Добавлен `application.background_work.BackgroundWorkCoordinator`: реестр operation id, типизированные стадии, lane/status/progress/cancellation reason/error code. Каждая операция сохраняет собственный thread; общий executor/FIFO для работы приложения не вводился.
- `ApplicationRuntime` передаёт координатор в один `LichessService`. Существующий `_thread` compatibility seam регистрирует задачу, сохраняет тип остановки и generation/account context в памяти; текущее поведение worker signals и shutdown сохранено.
- Удалён lock, который сериализовал целый archive scan с другими сетевыми потоками. Lichess transport ограничивает только отдельный HTTP-запрос; между запросами архивный sync уступает слот.
- Запросная калитка выбирает следующий bounded HTTP request по приоритету (interactive/safety прежде background, FIFO в одном приоритете). Долгие NDJSON streams не занимают этот слот. 429/Retry-After остаётся общим безопасным cooldown.
- Закрытие runtime сначала ждёт зарегистрированные worker operations и не закрывает SQLite, пока worker ещё жив; окно повторяет закрытие через существующий retry path.
- `operation_id` добавлен в allowlist структурной диагностики; никакие account id, credentials, URL, payload, PGN или FEN туда не передаются. Добавлены fake tests для lanes, ошибки/отмены/прогресса, архива с уступкой между запросами и admission интерактивного запроса раньше ожидающего background запроса.
- Проверки: `test_background_work.py` + `test_lichess_background_lanes.py`: **6 passed**; расширенный fake Lichess/OAuth/backfill/window набор в первом W5 прогоне: **355 passed** до добавления теста приоритета; отдельный повтор включал Lichess facade/stream/reconnect/429 и work lanes после исправления NDJSON lock. `compileall` и `git diff --check`: PASS.
- Потоковые запросы всё ещё используют транспортный 35-секундный timeout и отмену через stop event; следующий короткий интерактивный запрос не ждёт весь поток. UI-initiated operations, которые синхронно выполняют SQL/HTTP, остаются предметом W7/W8 audit.

**Next:** W6 вынести владение UCI process/analysis cancellation в `StockfishManager`, сохранив существующий Fair Play lease и настройки анализа.

## W6 — Stockfish process manager (завершён)

- `engine_manager.StockfishManager` теперь централизует запуск через скрытый python-chess UCI transport, регистрацию процессов, повторное использование процесса локальной оценки позиции, безопасный release и запрет новых процессов после shutdown.
- `ApplicationRuntime` владеет одним менеджером и закрывает его только после остановки зарегистрированных фоновых Lichess workers и до закрытия SQLite. `MainWindow` передаёт тот же manager в анализ позиции, одиночный послепартийный анализ и обе ветки создания массового runner.
- Поиск/отмена поиска по-прежнему выполняются существующими python-chess analysis contexts и stop events; управление параметрами nodes/depth/time, hash/threads/MultiPV осталось в прежних компонентах. Исправления Fair Play не обходились и не ослаблялись: connected review продолжает создавать анализатор только после текущего live preflight; batch runner делает fail-closed check до каждого job и периодически во время запуска.
- Добавлены fake engine tests на открытие/однократный release, reuse позиционного процесса, close-all и запрет запуска после shutdown. Адаптированы только test injection points к явному manager dependency; проверяемые assertions отмены, stale-result, safety revocation и порядка `engine.quit` перед SQLite сохранены.
- Проверки: целевой lifecycle/Fair Play/position/MultiPV набор — **29 passed in 4.19 s**; расширенный engine + batch + event detectors + architecture + window набор повторно завершён без падений; `compileall -q src tests scripts`: PASS; `git diff --check`: PASS. Fake движки и временные файлы; настоящий Stockfish не запускался.

**Next:** W7 выделить dialog/page classes из `window.py` в тематические UI модули, сохранив совместимые imports/signals и текущее визуальное поведение.

## W7 — UI views and dialogs (основные views извлечены)

- Отдельные модули `ui/dialogs.py` и `ui/analytics_dialogs.py` теперь содержат настройки, онлайн-игру, пакетный анализ, активные партии, подключение Lichess, статистику, детальную карточку партии и графики. `chess_coach.window` повторно экспортирует прежние классы для текущих entrypoints/tests.
- Созданы `ui/pages.HomePage`, `PlayPage`, `ResultPage`, `LibraryPage` и `PuzzleHistoryPage`. Home/Play/Result используют внешние callbacks; Library получает строки и отдаёт выбранные действия; PuzzleHistory получает список локальных аккаунтов, а SQLite/сеть остаются за application shell. `GameReviewWorkspace`, `MoveTable`, `BoardView`, theme/dashboard components повторно используются без второй реализации доски.
- Домашние фильтры/метрики, Play layout, страницы результата, библиотека и история задач сохраняют прежнее содержание и сигналы. Открытые Library/PuzzleHistory представления остаются в существующей модальной оболочке, чтобы не менять подтверждённую навигацию в рамках архитектурного этапа.
- Добавлены UI regressions на callbacks, adaptive card placement, result actions, library selection/paging, compatibility imports и отсутствие импорта `window` из извлечённых компонентов. Тесты запускались offscreen.
- Итоговый W7 регрессионный набор: `test_window.py`, credential dialogs, diagnostics, UI pages и engine manager — **217 passed in 39.78 s**. `compileall` после перестройки модулей: PASS; `git diff --check`: PASS.
- Текущее ограничение: `MainWindow` всё ещё содержит application workflow/callback orchestration (в частности puzzle replay, online event dispatch и некоторые async result handlers); компоненты больше не содержат владельца состояния приложения. W8/W9 boundary audit и оставшаяся декомпозиция use cases проверят, что этот shell постепенно сводится к маршрутизации, не переписывая бизнес-логику или экран.

**Next:** W8 расширить structured operation correlation/redaction, архитектурные import checks и fault-path regression; закрыть обнаруженные UI→worker и lifecycle coupling.

## W8 — diagnostics, operation correlation и границы (выполняется)

- Начата регистрация долгих операций, запущенных из `MainWindow`: чтение карточек главной, batch Stockfish, оценка позиции, установка движка и анализ завершённой партии теперь стартуют через `ApplicationRuntime.work_coordinator`. Координатор регистрирует их в отдельных потоках и не объединяет сетевые и engine lanes в блокирующую очередь. Совместимый thread-shaped handle сохраняет существующие проверки `is_alive()`/`join()` и остановку по событиям.
- На рабочем потоке операции задаётся random 32-hex `operation_id` диагностического контекста; события начала/завершения получают один correlation id. Arbitrary cancellation text сводится к безопасному статическому коду, а диагностический allowlist не сериализует exception message, токены, email, пути и игровые данные.
- Регрессии проверяют отмену с безопасным reason code, корреляцию в событиях операции, исключение произвольного текста, allowlist operation id и редактирование поддельных секретов/пути/email в диагностическом файле. Добавлено Qt-подтверждение, что анализ партии действительно зарегистрирован в engine lane.
- Первый узкий прогон выявил только ошибочное ожидание теста: контекст `run_id` безопасно очищал распознаваемый шаблон `token=...`, а не произвольный текст. Исправлен fixture; повтор целевых diagnostic/background/Lichess/Qt сценариев: **18 passed**.
- На момент записи W8 ещё не закрыт: необходимы статический аудит оставшихся thread owners и полный итоговый suite в W9. Отдельные dialog-owned workers статистики/индекса дебютов и engine probe сохраняют прежние владельцы; приёмка должна подтвердить, что их shutdown/ownership корректны. Физическая Windows-визуальная проверка и сетевые/секретные сценарии не выполнялись.

**Next:** завершить W8 audit и обновить карту/PROJECT_STATUS; затем W9 exact-head полный Windows CI, compileall, diff-check и isolated offline window smoke.
