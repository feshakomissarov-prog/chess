# ARCH-UX-01 REVIEW-FIX-07 — независимое ревью RF6: старые анализы, Mate(0) WDL, независимый auto-sync и terminal accuracy

**Дата:** 05.11.2026. **Приёмка:** RF6 по коду пока НЕ принят, требуется адресный fix-цикл **RF7-01…RF7-05**, не повтор предыдущих этапов.  
**Кодовый baseline:** [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), ветка `codex/arch-01-modular-refactor`, HEAD `e0947646db73d2cfa6a6baf0b2d3eccc5fed3c2c`.  
**Проверенный CI:** [Windows run #41979505071](https://github.com/feshakomissarov-prog/chess/actions/runs/41979505071) завершён **SUCCESS** на точном SHA: **721 PASS**, compileall, isolated offline QApplication/MainWindow, Windows Server 2025, Python 3.12.10, Qt 6.12. Это тестовый факт, **не** доказательство реального Lichess, PAT, Stockfish, DPI, личной SQLite или UI со старыми данными.

**Основание:** изучены diff `881a8d8f49c2cbdae7ffb1f5c4d79d0f091bafc9...e0947646db73d2cfa6a6baf0b2d3eccc5fed3c2c`, действующие реализации анализа/Sqlite/Stockfish/Qt, RF6-тесты и исходник python-chess. **Ниже только проверенные дефекты или непосредственно воспроизводимые контрактные пробелы.** Нельзя утверждать, что все ошибки физически проявились на Windows владельца — реальные данные не тестировались.

**Приоритеты:** P0 Fair Play и сохранность существующих данных; P1 исправление реальных статистических/фоновых потоков и матовых типов; P2 UX и регрессии. Только текущий code draft PR #44. `main` и docs-only PR #45 не сливать, личные credential/SQLite/PGN не читать и не публиковать, live human Stockfish запрещён.

## RF7-01 — P1 HIGH: настоящая обратная совместимость со старыми Stockfish run и их сохранёнными метриками

**Доказанная первопричина:**
- `src/chess_coach/lichess/move_metrics.py` ранее сохранял `move_metrics.accuracy=NULL` на **всех** ходах. RF6 добавил расчёт `accuracy`, но оставил `METRIC_VERSION = "cc-move-metrics-v2"`, то есть **тот же** ID метрик.
- `src/chess_coach/infrastructure/sqlite/analysis.py::latest_analysis_detail` сейчас делает rematerialize только если `SELECT 1 FROM move_metrics WHERE run_id=? LIMIT 1` **не вернул строку**. Для миллионов потенциально сохранённых прежних v2-строк есть строка с `accuracy=NULL`, поэтому новый код не запускается. `list/detail/Progress` продолжает показывать `local_move_accuracy: н/д` у фактически анализированных Stockfish партий.
- `Statistics.summary` теперь фильтрует `accuracy_formula_id = FORMULA_ID`; старые NULL-строки и без нового mate-event не засчитываются. Новые тесты RF6 создают **новые** run и не проверяют открытие/обновление SQLite, заполненной предыдущей версией приложения.

**Требуется:**
1. Явная версия **materializer algorithm**, отличающая старый `cc-move-metrics-v2` (NULL accuracy, старые WDL) от нового, например version bump `cc-move-metrics-v3` либо отдельное `materialization_revision/formula_id` с консистентным discovery. Если version bump — обновить **все** SQL joins/filter/coverage, `Statistics.summary`, review, selectors и tests, иначе старые метрики перестанут быть видны.
2. В рамках **той же одной SQLite**, без обязательной destructive migration, внедрить **bounded, resumable, idempotent** обработку старых **сохранённых** run `source=stockfish` по `run_id` и `line_revision/hash`. Пересчитывать `accuracy`, `accuracy_source`, `accuracy_formula_id`, mate evidence только из `position_assessments` + `game_moves` + `game_revisions.initial_fen`: никаких сетевых запросов или Stockfish/UCI.
3. На открытии конкретной партии с legacy run обеспечивать *работоспособную* ленивую актуализацию **в background worker** (не SQLite-перезапись на GUI thread), а общий Progress аккуратно сообщает "n/N пересчитано" или `не завершено`; можно организовать отдельную ограниченную deferred очередь с progress/cancel, но не запускать массовую запись тысяч партий от каждого экрана.
4. Обеспечить неизменность исходных analysis_run/position_assessments, official lichess_accuracy, Chess PGN, user accounts, сохранённые `review` run/альтернативы, старые исследовательские метрики и events. Исторические версии evidence audit доступны; текущая версия mate events не дублируется и не смешивается со старой при re-materialization.
5. Тест: создать SQLite, **сохранить Stockfish analysis до новой формулы** (`cc-move-metrics-v2`, `accuracy=NULL`, несколько положений CP/Mate). Открыть в новом `load_game_review` и Progress; получить допустимую per-move local accuracy и отдельные mate events, не меняя official accuracy/оценок, не вызывая Stockfish, не зависая UI; repeated open и interrupted/resumed migration дают тот же результат. В тестах должна быть **проверена реальная миграция с legacy rows**, а не только новый `save_analysis`.

## RF7-02 — P1 HIGH: исправить WDL инверсию терминального Mate(0) после победы белых

**Доказанная ошибка типов:**
- `src/chess_coach/lichess/analysis.py` сохраняет `score=info["score"].pov(chess.WHITE)`, `mate_white=score.mate()`. У python-chess `MateGiven.mate() == 0` **и** `Mate(0).mate() == 0`, то есть сериализация `mate_white=0` не различает «белые уже поставили мат» и «белые заматованы» без **легальной доски и очереди хода**.
- `src/chess_coach/domain/accuracy.py` в RF6 правильно использует `board.is_checkmate(), board.turn` для Win%.
- Но `src/chess_coach/lichess/move_metrics.py::_typed_score` без доски делает `PovScore(Mate(int(mate_white)), WHITE)`. `_white_wdl` также не передаёт board/turn. При terminal `mate_white=0` после победы **белых** это `Mate(0)` (= проиграли белые) вместо `MateGiven` (= победили белые), что записывает **WDL win=0, loss=1000** и портит обучающие/статистические интерпретации. Проверено по исходнику [python-chess engine.py](https://github.com/niklasf/python-chess/blob/master/chess/engine.py): `Score.mate()` предупреждает о слиянии `MateGiven/Mate(0)`; `Mate(0).wdl()` = `Wdl(0,0,1000)`, `MateGiven.wdl()` = `Wdl(1000,0,0)`.
- Контрпример: легальная Scholar's Mate `e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7`: после `Qxf7#` `board.turn==BLACK`; `mate_white=0` должен давать **победу белых**, WDL белых win=1000, loss=0. Для Fool's Mate (`f2f3 e7e5 g2g4 d8h4`) обратная сторона, WDL белых loss=1000.

**Требуется:**
1. Единая типизированная `Cp|Mate|MateGiven|Unknown` с `board.turn/is_checkmate` в `_white_wdl` и других derive consumers; **никогда** не конвертировать `mate=0` в `Mate(0)` без подтверждения терминального контекста.
2. Для нетерминальной `mate=0` сохранять `wdl=NULL` и `accuracy=NULL` как противоречивый/непроверенный источник, не подменять «выигрыш чёрных». Mate!=0 сохраняет корректный POV. CP продолжает обычную модель; Stockfish WDL ≠ Lichess Win%.
3. После исправления выполнить bounded rematerialization legacy WDL в составе RF7-01. Обновить `wdl_model/version` при смене алгоритма, оставить provenance.
4. Golden интеграционные тесты обеих сторон с реальными legal `chess.Board()` и `MateGiven`/ `Mate(0)`, terminal row score=0, missing terminal score, nonterminal mate0, WDL before/after, future `Review/Progress`. **Не только** проверить `accuracy`: проверить persisted `wdl_after_json` и отсутствие инверсии.

## RF7-03 — P1: автоматическая синхронизация истории задач должна работать без включённой синхронизации архива

**Доказанная зависимость, которой нет в UI:**
- `ui/dialogs.py` предоставляет два **независимых** флажка `lichess/auto_archive_sync` и `lichess/auto_puzzle_history_sync`.
- Но `window.py::_lichess_event_stream_ready` запускает `_auto_sync_timer` и стартовый `QTimer.singleShot(3000, _run_background_sync)` **только внутри if `auto_archive_sync`**. И `window.py::_save_settings_dialog` останавливает тот же таймер, если архив отключён, даже если puzzle history включена. Сама `_run_background_sync` проверяет оба флага независимо, но без timer она не вызывается. Поэтому при `auto_archive_sync=False`, `auto_puzzle_history_sync=True` история задач **не обновляется автоматически ни на login, ни по таймеру**.
- Имеющийся `test_background_sync_restores_incremental_archive_and_saved_puzzle_history` проверяет только оба True; `test_settings_expose_restored_automatic_archive_and_puzzle_sync` только факт сохранения checkbox, не реальное планирование задачи.

**Требуется:**
1. Запускать/останавливать общий auto-sync timer по условию **`auto_archive_sync OR auto_puzzle_history_sync`** (или независимые таймеры, если это принципиально нужно, **без второго сетевого scheduler**). Внутри `_run_background_sync` продолжать строго уважать каждый individual flag, `puzzle:read` token, account consistency, player live priority, shared rate limit.
2. Режимы `archive=off, puzzles=on`; `archive=on,puzzles=off`; `both=on`; `both=off`, переключения через Settings в уже подключённом аккаунте **без reconnect**: startup, periodic due, pause/cancel и disabled/unavailable token. Включение puzzles после архива OFF должно инициировать bounded attempt, архив не загружать.
3. Тесты fake client, fake clock/QTimer: expected `service.puzzle_calls > 0` при отключённом archive, `service.archive_calls == 0`; сохранение настройки, 429/human game/offline и stale timers. Никакого automatic OAuth и новых puzzles; только сохранённая история с уже выданным `puzzle:read`.

## RF7-04 — P1: терминальный мат без engine score должен засчитываться и для точности последнего хода

**Контрактная ошибка на воспроизводимом edge case:**
- RF6 обновил `domain/accuracy.py::calculate_saved_game_accuracy`, допускающий `complete` **точность всей партии**, когда законная конечная `board.is_checkmate()` не имеет `after` eval/depth; источник terminal board отдельно обозначается `terminal_positions_derived=1`.
- Но `lichess/move_metrics.py::build_move_facts` считает per-move accuracy **только при `before.depth is not None and after.depth is not None`**. Для того же последнего `Qh4#` или `Qxf7#` с отсутствующей terminal оценкой `after={}` получается `accuracy=None`, хотя `before` оценено и terminal board **точно** известно. Получается несогласованность: общая accuracy включает последний ход, а детализация пишет «Точность хода: н/д».
- `tests/test_accuracy.py::test_terminal_checkmate_without_engine_row_is_derived_with_terminal_provenance` проверяет только game accuracy, а `tests/test_m45_move_metrics.py::test_mate_zero_only_uses_terminal_board_and_checkmate_event_is_verified` для terminal row подаёт `depth=20`. **Нет** интеграционного теста для именно missing terminal assessment.

**Требуется:**
1. При legal `after_board.is_checkmate()`, сохранённом валидном `before score/depth` и `after score` отсутствует: создать typed terminal outcome с `source=board-terminal`, рассчитать локальную accuracy последнего хода по предельному Win%, не требовать фиктивной engine depth для законно терминальной доски; terminal event с доказательством board/is_checkmate. Аналогично если terminal row есть, но `depth=None`. Не придумывать `after.depth=20`.
2. Нет per-move accuracy при отсутствии **before** score/depth, при нетерминальном missing after, при `Mate(0)` в нетерминальной позиции и при расходящихся версиях линии; отсутствие `CP` остается NULL, `mate` отдельно.
3. Добавить отдельную provenance / terminal_derived field в event/evidence и read model; если SQLite schema не меняется — можно хранить source для accuracy в versioned source/evidence, чётко отличимый от two-engine-scores. Это значение не подменяет official Lichess.
4. Тесты: Fool's Mate и Scholar's Mate, last row missing и present depth=None, one missing before, nonterminal missing after, repeated materialization, `Review/Progress` n/N, Fair Play masking.

## RF7-05 — независимая приёмка, которую не заменяет «721 PASS»

1. Проверить исходный и текущий HEAD, незакоммиченные изменения и сохранность `.venv312/`. RF7 — **отдельный адресный fix-цикл**, ничего не переписывать из завершённых ARCH/RF1–RF6; no main.
2. Добавить **красные до исправления → зелёные после** regression tests для RF7-01..04 на синтетических temp SQLite, без Live Lichess/OAuth/PAT, браузера и пользовательской DB. Только сохранённые synthetic CP/Mate scored rows, никакой движок для миграции. Все существующие 721 тесты остаются зелёными.
3. Нужны **реальные старые данные, но синтетически подготовленные по СТАРОМУ формату**, то есть fixture устанавливает прежние `metric_version/accuracy=NULL`, затем запускает новый код и проверяет version/source/матовые events и Progress. Нельзя выдавать new-run-only tests за поддержку сохранённой базы пользователя.
4. Qt/Windows smoke открыть под fake safety allow законную завершённую партию с матом и **подтверждённой старой сохранённой аналитикой**: per-move accuracy, Mate±/mate0, AdvantageBar, move details, provenance, no false CP и личной информации. Отдельно показать case `auto_archive=False,auto_puzzles=True` в активных Settings без записи в реальный токен. No engine on live human/unknown/429.
5. Run full pytest с предупреждениями потоков как ошибками, compileall, diff check, **новый Windows CI на exact final SHA**. Отметить настоящие `completed/SUCCESS`, не `in_progress`. Обновить журнал исполнения `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md` и `docs/PROJECT_STATUS.md` в кодовом PR #44.
6. PR #44 оставлять draft, owner физический Lichess/PAT/Stockfish/DPI приемка **остается BLOCKED**, merge только после владельческого согласия. Docs-only PR #45 использовать как источник этого ТЗ, не сливать и не менять `main`.

**Приёмка RF7:** старые игры в существующей БД без повторного анализа теперь получают новые локальные показатели корректно и без повреждения истории; WDL Mate(0) обеих сторон соответствует доске; отдельная автоматическая синхронизация задач работает без архива; последний матовый ход с доказанным board-terminal не теряет точность; безопасность и версия данных документированы.
