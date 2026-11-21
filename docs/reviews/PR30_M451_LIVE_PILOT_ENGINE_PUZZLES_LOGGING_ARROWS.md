# PR #30 — ТЗ B0–B4 по реальному Windows-пилоту: Stockfish, задачи, логи, стрелки

Дата: 2026-11-02. Код: 6180277d46ebcae261e0dd482703b08ad9dec1cd, draft PR #30, ветка codex/m45-batch-analysis. Предыдущий CI #99: 350 PASS, но реальный Stockfish не анализировал партии. Источники контекста: docs/handoffs/M4_5_EXECUTION_LOG.md, docs/specs/M4_5_1_WINDOWS_MANUAL_UX_FOLLOWUP.md, docs/specs/M4_6_DESKTOP_EXPERIENCE.md. **Исполнять только в той же ветке и PR #30, НЕ начинать M4.6.**

## 0. Что произошло

Владелец на своём Windows-ПК подтвердил UCI readiness Stockfish 19, но при запуске анализа (максимум 2 партии, 20 000 nodes/позицию, Hash 64MB, retry failures OFF, selective review OFF) увидел ошибку:

    EngineError: cannot set MultiPV which is automatically managed

В окне было: Fast очередь 3, готово 0, ошибки 3; «Обработано сейчас: 0». **Три ошибки в агрегате не обязательно все принадлежат текущему запуску**, поскольку счётчики показывают сохранённую очередь. Нельзя утверждать количество сломавшихся в этой попытке без проверки.

Другие замечания: 1) стрелки ПКМ слишком тонкие — пользователь приложил снимок; 2) синхронизация примерно 1,5 тыс. задач Lichess повторно долго загружает старую историю, в отличие от быстрого обновления нескольких тысяч партий; 3) нужны надёжные обезличенные логи, доступные в приложении, для расследования Codex. Реальную SQLite и OAuth мы не читаем, владельцу ранее сделаны корректные backup.

## B0. P0 — исправить Python-chess MultiPV и реальный анализ

Точная причина подтверждена исходниками src/chess_coach/lichess/analysis.py:117–118 и :228: вызов engine_instance.configure с опцией MultiPV = 1 или N. В python-chess это managed UCI option; передача в configure и вызывает именно показанную ошибку. Документация: https://python-chess.readthedocs.io/en/stable/engine.html , реализация: https://github.com/niklasf/python-chess/blob/master/chess/engine.py . **Это не поломка установленного бинарника Stockfish 19 и не повод снова его скачивать.**

1. Fast/standard pass: configure только Threads и Hash; анализ одной линии через engine.analysis(board, limit) без настройки MultiPV. Selective review: configure только Threads и Hash; количество линий указывать аргументом engine.analysis(board, limit, multipv=N), N по действующему ограничению 2–3. Никогда не конфигурировать также другие managed варианты UCI_Chess960, UCI_Variant, Ponder.
2. Проверить сбор результатов 1PV и 2PV, корректный score white POV, mate, PV, depth, info, остановку SimpleAnalysisResult.stop(), quit, cancellation, leases, progress, safe fair-play monitor. При исключении job FAILED, не COMPLETE, и неполный run не должен подменить валидный предыдущий.
3. Изменить FakeEngine в тестах: метод configure ДОЛЖЕН выбрасывать ошибку для любой managed option, чтобы прежний дефект больше не проходил зелёный CI. Добавить 1PV fast, 2PV selective, reuse engine, cancel/restart, error reason tests. Если локальный официальный Stockfish 19 доступен исполнителю, дополнительно выполнить ОДИН ограниченный реальный UCI-analysis на синтетическом PGN/temp SQLite с бюджетом 2000–5000 nodes без OAuth и личных игр. На CI, где Stockfish не установлен, дополнительно строгий fake/mocked UCI test; не утверждать реальный smoke, если его не выполняли.
4. Сохранить параметры/cached runs, не переименовывать key анализов без необходимости. Старые FAILED jobs не реанимировать молча; повтор ТОЛЬКО по галочке retry или отдельному action. После фикса владелец сможет снова выбрать не более 2 завершённых партий: 20k nodes, 64MB, selective OFF, no human live games.
5. Разделить в UI «сейчас: запрошено / попыток / готово / failed / skipped» и «вся очередь: ожидает / история ошибок». Излишек queued=3 при limit=2 не означает, что будут проанализированы 3; фактический лимит на 2 попытки должен сохраняться, no hidden third run. При exception показать краткую причину, job ID и ссылку на локальную диагностику; если UCI handshake успешен, не говорить «движок не установлен».
6. Не выполнять реальный анализ всех 3291 партий; не использовать чужую SQLite в автоматических тестах.

Definition of Done B0: НИ ОДИН engine.configure не передаёт MultiPV, строгие тесты ловят managed option, synthetic analysis через реальный Stockfish PASS на доступном Windows-ПК, если executable действительно доступен; Queue+Fair Play+cache не регрессировали, old FAILED остаются historical.

## B1. P1 — диагностическое журналирование без утечки персональных данных

Ввести единый Python stdlib logging, с event names, UTC/offset ISO timestamps, severity, module/component, duration_ms, thread, run_id/job_id и scrubbed exception type + limited traceback. Корреляция: startup -> batch run -> job -> UCI configure -> analyze -> persistence, puzzle sync -> request -> page -> import -> checkpoint -> result. Логи работают без консоли в будущем Pythonw launcher.

1. Каталог: %LOCALAPPDATA%\ChessCoach\logs (через QStandardPaths.AppLocalDataLocation или строго проверенный Windows user path), вне Git и SQLite. RotatingFileHandler 5MB и до 4 архивов, fail-gracefully при запрете записи. Не добавлять DB table ради логов.
2. События обязательны: app_start, schema_version, batch_started (max2, nodes20k, Hash64), job_claimed, uci_opened, uci_configured, analysis_started, progress_sampled (не чаще раз в 5–10с), job_complete, job_failed (exception cause, traceback), batch_ended; puzzle_sync_started (backfill/incremental), HTTP method+API path TEMPLATE (не query с токенами), request duration/status, scanned/inserted/unchanged, DB duration, checkpoint, finish/error, rate_limit_429, cancel/retry. Только реальные события, а не журналы-муляжи.
3. **Privacy gate**: не логировать OAuth/Access tokens, headers Authorization, OAuth code/verifier, email/username, полный user home path, соперников, PGN/FEN, списки ходов/PV, полные puzzle records, заметки/микрофон, raw Lichess body. Если нужно game reference, использовать безопасный session-scoped surrogate ID, не raw личные game IDs. Пути движка — basename/version/digest без полного home-directory. Санитизировать exceptions и tracebacks до записи. Redaction тест с секретами и Unicode, без автоматического upload/telemetry.
4. Экран «Настройки → Диагностика»: «Открыть папку логов», «Скопировать обезличенный отчёт». Отчёт: версия кода/commit SHA если доступна, Python/Qt/python-chess, SQLite schema number, Stockfish name/verified status, короткий последний ERROR code, количество попыток/удач/ошибок последней batch, время. Не включать личные партии/аккаунт/секреты. Пользователь выбирает, отправлять ли файл.
5. Не писать на GUI thread спам per-position/per-clock; sampling и bounded I/O, fatal error flush; логирование не замедляет предход. Проверить fresh startup, Pythonw, отказ прав, rotation, simultaneous workers. При повторном запуске лог доступен.
6. В PR handoff указать точный путь к логам и как владелец может передать редактированную диагностику Codex.

Definition of Done B1: после искусственного EngineError есть редактированный безопасный файл с первопричиной, job/run ID и фазой, кнопка открывает путь, токены и PGN не попадают даже в traceback.

## B2. P1 — инкрементальное обновление задач без повторной выгрузки 1500 старых попыток

Факт по коду src/chess_coach/lichess/puzzles.py:119–151: при incremental sync запрашивается stream_puzzle_activity(before=cursor, max_items=1000), БЕЗ since=lower_bound, а даты затем отсекаются в Python. Поэтому даже без новых задач может повторно приходить до 1000 известных попыток. Вместе с dashboard/get_account и backfill это объясняет вероятную нагрузку, но фактическое узкое место требуется измерить из B1.

1. **Разделить initial/backfill и incremental.** Первичный исторический импорт полного архива задач обязателен один раз, resumable через существующие puzzle_sync_state.backfill_before/backfill_complete; не отменять/не «срезать» часть ~1500 старых попыток ради скорости. Когда backfill complete, не начинать его заново без явной команды/повреждения прогресса.
2. В incremental запросе корректно применять серверные границы since + before + max с сохранённым incremental_since, разумным ограниченным overlap для одинаковых timestamp и поздних записей. Убедиться в реальной семантике Lichess API (inclusive/exclusive, сортировка, совместимость since и before): https://lichess.org/api#tag/Puzzles/operation/apiPuzzleActivity. Если серверные границы не работают как ожидается, безопасный ограниченный head fetch + early-stop и честная диагностика, не выдумывать API.
3. Zero-new refresh: 0 bulk upserts, одна маленькая bounded проверка (или допустимый TTL), не перекачивать 1000 давно известных попыток. Если 1501 новые в окне — все 1501 по страницам, без пропуска. Same-ms timestamp tie и _expand_boundary только при необходимости; сохранить existing stable puzzle_attempt_uid/idempotency для двух аккаунтов.
4. Курсоры сдвигать только после durable commit нужной страницы; при 429, cancellation, HTTP error, malformed NDJSON — не помечать успешной проверки, не терять watermark/backfill. Respect Retry-After, no uncontrolled retry/burst or duplicate background + manual run. Не запускать OAuth при открытии локальной истории.
5. Измерять по отдельности network wait, bytes/records received, parse, DB upsert, dashboard request. UI: «проверено / новых / уже были / последнее успешное обновление / режим: первичная загрузка либо обновление», elapsed; 0 новых = «История актуальна». При большой первичной загрузке честно показывать время и прогресс, но не заставлять пользователя повторно импортировать на каждое открытие.
6. Никакого широкого rebuild SQL таблиц; база v17 остаётся, если existing indexes достаточны. Тесты synthetic 1500, 2501, 0/1/100/1501 new, same timestamp, 429, cancel/resume, two accounts, duplicate seen, server paging, 2nd refresh no duplicate and much smaller request volume. Реальные данные владельца не открывать в tests.
7. Если после оптимизации реальная сеть Lichess всё равно медленна, предоставить диагностический breakdown, не заявлять «починили» только по fake tests.

Definition of Done B2: на 1500 уже сохранённых fake attempts при 0 новых API не возвращает и SQLite не перезаписывает 1000 старых; complete backfill остаётся complete, real performance измеряется владельцем отдельно.

## B3. P2 — стрелки правой кнопки мыши заметно толще, но не закрывают доску

Код src/chess_coach/board_view.py:455–475 рисует width = side*0.12*0.88. Для side~55dp это ~5.8dp: действительно скорее тонкая линия. Требуется новый заметно более широкий дефолт без изменения управления.

1. Annotation arrow (ПКМ): толщина тела = 0.20–0.23 стороны клетки; пример при side55: 11–13dp; min8dp, max17dp. Голова длина ~0.33–0.40 side, ширина ~0.30–0.36 side с аккуратным переходом тела. Round cap, antialiasing, alpha ~170–205 и кончик до центра целевой клетки, чтобы не закрыть фигуру. Линия короткого одношагового и длинного диагонального хода должна быть видимой.
2. Premove arrow — другая смысловая сущность, может оставаться немного тоньше/синей; не меняйте premove logic и Fair Play ради красоты. Цвет сейчас не требуется менять — тёмная палитра M4.6 отдельная задача.
3. Масштабирование при board size 400/480/560/640, flip для чёрных, dpi 125/150; повторная правая стрелка toggle on/off, квадратная пометка, clearing history и right drag не ломаются. Qt/QPainter geometry tests и визуальный screenshot smoke, без OCR; не писать annotations в DB.
4. Дополнительная настройка «обычные/толстые стрелки» сейчас не обязательна; по умолчанию сделать именно толще.

## B4. Windows CI, восстановление контекста и следующий ручной пилот

1. Выполнять последовательно B0 → B1 → B2 → B3 → B4 в текущем PR #30; коммиты после логических частей, updating docs/handoffs/M4_5_EXECUTION_LOG.md, docs/PROJECT_STATUS.md и PR description. Не менять M4.6 code, не создавать параллельный кодовый PR.
2. Полный Windows Python 3.12 CI на ТОЧНОМ финальном SHA, offscreen Qt, compileall, pip check, diff check; не забыть B0 strict fake UCI и B2 paging tests. Нельзя считать «реальный Stockfish работает» только потому, что UCI handshake отвечает: надо выполнить bounded реальный UCI analysis synthetic game на локальном exe, если Codex действительно на ПК с installed Stockfish.
3. Никакой новой миграции SQLite без доказанной необходимости; не переписывать/не удалять user SQLite, токены, ранее проверенный бэкап, не переустанавливать Stockfish 19. Не выполнять анализ всей коллекции 3291 партий.
4. После фикса и следующего независимого ревью владелец вновь запустит **не более двух** пригодных завершённых партий с 20k/64MB, retry OFF, selective OFF и подтверждённым отсутствием live human games. Старые FAILED jobs остаются, пока явно не выбраны для retry. Проверит источники в паспортах, graph и unchanged PGN, потом отдельно измерит refresh puzzles, затем оценит новые широкие стрелки.
5. Финальный handoff: B0–B4 DONE/PARTIAL, URL исходного ТЗ, финальный SHA, CI URL/count, реальный Stockfish synthetic UCI status PASS/NOT RUN, puzzle network stats до/после, место логов + способ безопасно передать, инструкция на 2 партии, все known limitations. PR #30 остаётся **draft**, не merge до ручной приёмки.

**Stop condition:** Если реальный synthetic UCI анализ после B0 падает, это БЛОКЕР M4.5 и нельзя заявлять READY к пользовательскому массовому пилоту. Если проблема с API пагинацией требует отдельной проверки, остановить только опасную оптимизацию, пометить B2 PARTIAL и продолжить B3/B4, не теряя задачи.
