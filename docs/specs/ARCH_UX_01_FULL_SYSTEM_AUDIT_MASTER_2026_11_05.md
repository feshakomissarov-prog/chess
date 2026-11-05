# ARCH-UX-01 · MASTER REVIEW — единое комплексное ревью приложения и исправления одним циклом

> **СТАТУС: РАБОЧИЙ ЧЕРНОВИК · НЕ ПЕРЕДАВАТЬ CODEX ДО ЗАКРЫТИЯ ОБОИХ БЛОКОВ.** Редактируется/сохраняется в GitHub после каждого значимого шага. Последний подтверждённый code draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), HEAD `7a3a6775a0a27e60c0bea7cfbcd4f28d734e939b`, base `main`, draft/unmerged. Docs draft [PR #45](https://github.com/feshakomissarov-prog/chess/pull/45) — единственное место редактирования ТЗ. [Windows CI #286](https://github.com/feshakomissarov-prog/chess/actions/runs/42005438175) **SUCCESS, 750 PASS** на baseline SHA, но это не сертификат полноты GUI/сети.
>
> **Решение владельца 05.11.2026:** запрещён процесс «нашли 2–5 дефектов → отдельное ТЗ → отдали Codex». Отныне **один полный набор всех доступных замечаний**, два блока независимого аудита, затем один большой fix-цикл в PR #44, далее единая приёмка по матрице. До закрытия всего аудита **не посылать Codex промежуточные исправления**. Документ — durable checkpoint на случай потери контекста/времени/интернета. Прежний RF9 — всего лишь первые два пункта этого документа, **не отдельная работа**.

## 0. Воспроизводимость и границы независимого аудита

- Источники: точный GitHub SHA кодовой ветки; утверждённые [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md), [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md), [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [UX-COV-01…24](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md), [ARCH-UX-01](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), прежние RF1…RF9 и hosted CI.
- Реальное пользовательское устройство/GUI, PAT/OAuth, Lichess online, реальная пользовательская SQLite и UCI Stockfish в этой независимой GitHub проверке недоступны. Не писать «визуально проверил» без фактического GUI-прогона. Статус каждого замечания: **DEFECT (прямое несоответствие в source)**, **HIGH-RISK CONTRACT (доказуемый отсутствующий guard/test, требующий детерминированного repro)**, **VERIFY (проверка, не утверждение об ошибке)**. Не создавать ложных дефектов из одного подозрения.
- Охват дерева на старте: 233 Git blobs, 121 Python файла, из них **79 src и 41 tests**, `window.py` ~7033 строк. Нужна полная карта действий/кнопок и 24 групп, не только changed diff.
- Ограничения: Windows Python 3.12+, PySide6, python-chess, одна SQLite и один управляемый UCI Stockfish, modular monolith; **Fail-closed Fair Play** для любой живой human/unknown онлайн партии и сетевой неопределённости. Не скачивать/анализировать пользовательские токены/БД, не раскрывать персональные идентификаторы в GH, не сливать PR.

## 1. ДВА БЛОКА АУДИТА (checkpoint)

- **БЛОК I — архитектура и данные:** Lichess auth, генерации/401/429, сетевые действия и очереди, игровые snapshots/часы/premove/takeback, Fair Play, SQLite migration+revisions, аналитика CP/mate/accuracy/WDL, архив/задачи и инкрементальные cursor, Stockfish/PGN, Qt worker lifecycle, performance, failures/restart/shutdown, privacy. Проверить исходники и уже существующие тесты; сохранить все находки в этом файле. Статус: **ЗАВЕРШЁН КАК ПЕРВЫЙ ДОКУМЕНТИРОВАННЫЙ ПРОХОД**, §2 и §3; перед финалом провести перекрёстный sanity-check.
- **БЛОК II — UI, кнопки и end-to-end:** утверждённые шесть основных экранов + Настройки/фон/игра/после игры/разбор/PGN-FEN/задачи/вложенные окна, каждая видимая команда и error/empty/loading states, geometry 1280×720/1366/1600/1920, DPI 125%/150%, accessibility, звуки AS-IS, переходы, сортировки, фильтры, счётчики, все 24 COV. Отдельная matrix кнопок: control→handler→предусловие→error→response→test→статус. Статус: **НЕ ЗАВЕРШЁН**.
- **ФИНАЛЬНЫЙ GATE:** единый deduplicated P0/P1/P2 список, deterministic repro + тесты для каждого, отрицательные и cross-page сценарии, свидетельства CI и живого smoke. Только тогда заменить шапку на **ГОТОВО К ПЕРЕДАЧЕ CODEX**. Одного прохода по тестам нельзя назвать «всё без ошибок». Нельзя обещать математически найти абсолютно все баги.

## 2. Блок I — подтверждённые замечания (СОХРАНЕНО, НЕ ФИНАЛЬНО)

### CORE-01 · P0 · DEFECT — старый POST игры может удалить токен новой auth-сессии (source race)

**Доказательство:** `src/chess_coach/lichess/service_games.py::send_move` в worker захватывает token через `self.token_store.get()`, а при HTTP 401 вызывает **позднее** `self.token_store.delete()` и `self._connection_stop.set()`. `src/chess_coach/lichess/service_auth.py::set_auth_method/_cancel_active_session` изменяет `auth_method`, `_auth_generation` и `_connection_stop`, при этом `token_store` — динамическое alias `selected_token_store`, а в `send_move` **нет generation/identity check**. Сценарий: отправить ход в A; во время ожидания сетевого ответа переключиться на B/другой способ; старый ответ A=401 удалит **слот B** и остановит **новый** stream. Это прямое нарушение session identity и сохранности credential.

**ТЗ:** capture `AuthOperationContext` (generation, account, credential-slot identity, stop_event, game_id, request_id) до отправки; любые *mutating auth side-effects* 401 применять только если context ещё текущий **и** удаляемая credential совпадает с токеном использованного запроса (atomic/CAS under lock), иначе stale response dropped. Не удалять/не отзывать токен при неопределённом HTTP/429; старую pending move до definitive response считать отдельно. Qt signals с typed generation. Tests с barriers: A send_move delayed→B auth/login→A 401; два PAT аккаунта; PAT→OAuth; reconnect same slot with a different token; old 429 and old 200; убедиться, что B credential/stream survives, не отправлена auto-retry.

### CORE-02 · P0 · HIGH-RISK CONTRACT — Lichess action workers читают *текущий* token после enqueue и не имеют request-generation fencing

**Доказательство:** `LichessGameActionsMixin::_action`, `_challenge`, `takeback`, `cancel_open_invitation`, `fetch_active_games`, `challenge_username`, `create_ai_game` получают `self.token_store.get()` **внутри запускаемого thread** и используют mutable `self.auth_method`/session; у callback `_challenge_ready`, `_active_games_received`, `_active_games_error`, `_challenge_status` в `window.py` нет generation argument/guard. События от старой сессии могут запустить запрос под новой credential или обновить UI/активные игры нового профиля. Вызывающие safety checks, где есть request_id + identity guard, уже лучше — не ломать.

**ТЗ:** для каждой командной операции game/action/challenge/cancel/active snapshot захватить account, session generation, expected game owner/intent при клике; проверить перед HTTP и перед UI mutation; user-scoped game ID и challenge ID; запрещать старому work менять новую UI сессию, завершать старый запрос без срыва нового; исключить дублирование POST при race. Активные игры не должны подмешиваться из A в B, не должны открываться после смены auth. Tests с deterministic `Event` barriers для каждой категории, A→B, A→logout, delayed 401/429/200, old challenge events, finished game auto-reconnect, idempotency.

### CORE-03 · P1 · DEFECT — архив v3 статистики не перерисовывается в уже открытом Progress

**Доказательство:** `MainWindow._start_legacy_metrics_batch` делает `statistics_page._schedule_refresh()` **перед** SQLite migration; `MainWindow._legacy_metrics_batch_finished` обновляет только `statistics_page.set_metric_migration_status`, фоновый инспектор и Home. `StatisticsDialog._refresh_timer` single-shot, после завершения batch queries не приходят без изменения фильтра. Получается устаревший summary/phases/coverage при корректных v3 данных. Предыдущее [RF9](ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md) подтверждает кодовый сценарий.

**ТЗ:** coalesced source-scoped generation-safe auto-requery для **видимого** Progress после каждой результативной порции и финального завершения, без запроса на каждую строку, без сброса фильтров/выбранного scroll/tab. Qt test: Progress открыт ДО migration, legacy A+B + три sources, задержанная пачка, без клика ожидаем обновления всех отображаемых KPI из v3.

### CORE-04 · P1 · DEFECT — UI Progress выводит счётчик миграции из аккаунта Главной

**Доказательство:** `_legacy_metrics_account_scope()` опирается на `dashboard_account.currentData()` из скрытого Home-adapter, хотя `StatisticsDialog.account.currentData()` — собственный фильтр Progress. `set_metric_migration_status` слепо публикует Home-scoped `n/N`; completion cache по revision блокирует повторный пересчёт scope. При Home A / Progress B можно увидеть неверный статус.

**ТЗ:** разделить global migration executor и status read models в каждом экране; scope на Progress — собственный account (и source только если label подразумевает source), Home/Background — явно подписанный scope; filter/revision invalidation, async count, generation guard. Qt case A=5/B=15/All=20, с Home A и Progress B + late A callback, завершённая migration + switch. Скрывать engine-derived coverage при Fair Play blocked.


### CORE-05 · P1 · DEFECT — онлайн-инкремент часов показывается в миллисекундах как секунды

**Доказательство:** `src/chess_coach/lichess/session.py::_apply_game_full_metadata` и `_apply_clock_state` принимают Lichess `increment,winc,binc` **в миллисекундах**: типовая игра 3+2 — `increment=2000`, `white_inc=black_inc=2000`. `MainWindow._update_clock` (оба пути: ожидание часов и обычный live) пишет `f"+{owner_increment} с после подтверждённого хода"` напрямую. Получается `+2000 с` вместо `+2 с`. В unit-тестах проверяется валидация live clock и UI board, но не этот label при ненулевом increment.

**ТЗ:** единый тип `milliseconds` в модели и `format_increment_ms` только для presentation; 2000→`+2 с`, 10_000→`+10 с`, sub-second без выдуманных округлений, clock snapshot/paused/offline. Не менять физическую валидацию миллисекунд. Test Qt: Lichess gameFull 3+2/15+10, оба пути _update_clock, correspondences, same board after finish. Подписанные часы/Control должны быть согласованы.


### CORE-06 · P1 · DEFECT — импорт Lichess теряет известное точное время начала партии

**Доказательство:** `src/chess_coach/infrastructure/sqlite/archive_store.py::import_archive_record` сначала использует `PGN headers.UTCDate/Date` (почти всегда присутствуют, только день) и записывает `started_at=датаT00:00:00UTC`, `game_time_precision='date_only'`; серверный **авторитетный `record.createdAt` в миллисекундах** читается лишь `if started_at is None`, то есть обычно отбрасывается. `commit_archive` дополнительно сохраняет `started_at=COALESCE(started_at, ?)` — уже занесённое ложное `00:00` невозможно улучшить простым повторным импортом. `StatisticsService._filter_sql` фильтрует по `g.started_at`, а Games fallback сортирует по нему. Следствие — неточное время и неверные граничные дни/периоды, несмотря на достаточный Lichess source timestamp.

**ТЗ:** выбирать `createdAt` для `timestamp` при валидном серверном времени; `PGN UTCDate+UTCTime` только когда оба достоверны; `Date` даёт строго `date_only`, без invented wall time. При поступлении нового более точного `createdAt` **повысить** точность legacy `date_only` с сохранением первичной/последней timestamp provenance и without reordering via `updated_at`. При повреждённом createdAt оставить date-only, не invent UTC. Старую пользовательскую SQLite обновлять bounded/idempotent при **уже полученном** server event, не делать remote scan ради исправления времени. Test synthetic PGN Date + JSON createdAt known, import again, calendar boundary, account filters, lastMoveAt chronology and unknown/date_only cases.

### CORE-07 · P1 · DEFECT — вкладка «Обучение → история задач» обращается к SQLite синхронно в GUI thread на вводе и переключениях

**Доказательство:** `src/chess_coach/window.py::open_puzzle_history_local` и `show_puzzle_history` вызывают `game_repository.list_puzzle_accounts()` непосредственно из handler Qt; `render_page()` делает `game_repository.puzzle_history_page(... page_size=100)`, `refresh_account_summary()` делает `puzzle_statistics, puzzle_rating_trend, puzzle_sync_state`, и всё это вызывается из `search.textChanged`/page controls и account-selection. Эти операции читают локальную SQLite/агрегаты в **главном GUI потоке** без debounce и без app-owned worker. При большой истории/одновременной SQLite миграции можно заморозить/задержать интерфейс, нарушив ARCH-UX-01 требования к data-loading.

**ТЗ:** использовать общий app-owned background query coordinator + generation-scoped cancel/stale-drop и debounce поисковых строк; вернуть только page-size dataset и aggregate, без тысяч QListWidget; при затяжном запросе не терять текущую игру/доску/фокус. Показать loading/empty/error; Qt tests с delayed SQLite read, вводом N клавиш, fast account switch и user actions во время write lock, QThread/Qt ownership, background-safe result dispatch. Не запускать сетевое puzzle API от просмотра истории и не заменять existing replay/reveal.

### CORE-08 · P1 · DEFECT — действия «предложить ничью / сдаться / заявить ничью» не защищены от повторных POST и cross-generation callback

**Доказательство:** `src/chess_coach/window.py::resign_online` показывает диалог, затем вызывает `self.lichess.resign/abort`; `handle_draw` сразу делает `self.lichess.draw(...True)`; `claim_draw_button.clicked` вызывает `lichess.claim_draw` напрямую. В `_refresh_online` эти кнопки enabled по `active`, **никакого in-flight request_id или disable-until-authoritative-answer** для resign/draw/claim. `LichessGameActionsMixin::_action` для каждого нажатия создаёт новый поток/POST, а HTTP ответ без отдельного typed command state. Пользователь может повторно отправить предложение/сдачу, особенно на медленном/429 соединении. Аналогичный fixed state уже есть у takeback — использовать ту же модель.

**ТЗ:** явные typed one-shot pending states/operation generation на каждый game_id/action; coalescing duplicate click, disabled/visible progress, server authoritative stream acknowledgement и reconciliation после unknown POST (никогда не переотправлять неподтверждённый ход/сдачу «на всякий случай»), renew only when server state clearly permits; 401/403/429 и смена auth не позволят старому callback воздействовать на новый profile. Tests double click, delayed 200/error, reconnect, status changes during modal, stale request with second active game.


### CORE-09 · P1 · DEFECT — ошибка чужой Lichess-команды сбрасывает ожидающий вызов в другом workflow

**Доказательство:** `LichessGameActionsMixin::_action` используется для `resign`, `abort`, `draw`, `claim_draw`, `claim_victory`, cancel и при ошибке отправляет только **нетипизированный `self.error.emit(str(exc))`**. `MainWindow._lichess_error` реагирует на **любой** `error` с `self._awaiting_game_start=False` и скрывает `cancel_challenge_button`. Сценарий: ожидается username challenge, затем параллельное завершение/операция другой game с 403; общий handler ошибочно считает challenge завершённым. Отдельные auth generation guards для game stream не исправляют этот UI state.

**ТЗ:** одна типизированная `OperationFailure(kind,game_id/challenge_id,request_id,auth_generation,http_code)`, сообщения для конкретного workflow. Ошибка resign/draw не изменяет pending invitation, ошибка archive/analytics не разлогинивает, ошибка challenge не затрагивает чужую игру. User-visible error on relevant page and background inspector; no token/PGN in logs. Regression two simultaneous game sessions + challenge + cross-generation 401/403/429 in deterministic ordering.

### CORE-10 · P1 · HIGH-RISK UI LIVENESS — SQLite в GUI hot paths auth/finish/notes, не только в конструкторах

**Проверенные вызовы:** `MainWindow._lichess_authenticated` и `_lichess_event_stream_ready` вызывают `game_repository.ensure_archive_account(username)` из Qt slots; `_show_result` делает `has_pending_write/get_game/export_pgn` и `_track_game_finalization` также читает SQLite synchronously; `Library.save_selected_note` вызывает `current_line` и `save_note` внутри click handler; `Puzzle open/history` и `StatisticsDialog.__init__` уже покрыты CORE-07/UI-06. При writer lock это может блокировать GUI, часы, доску и cancel button. **Эти операции действительно синхронны в коде**; зависание на реальной базе еще не измерено.

**ТЗ:** app-owned serialized DB writer/read-only worker models для критичных Qt slots, без разрушения game finalization correctness: строго atomic CAS финальной линии с уведомлением Qt signal/revision, чёткое progress/loading; no duplicated writes, stale profile/generation not consumed. Обязательные worst-case Qt barrier tests: writer holds `BEGIN IMMEDIATE`, login account A→B, finish game last move, save note, show Progress/Puzzle; paint/clock updates/Disconnect remain responsive, final move persisted eventually or explicit error. Не перемещать саму отрисовку/Qt signals в worker thread. Ограниченная задержка shutdown, no SQLite concurrency corruption.


### CORE-11 · P0 · DEFECT — локальный список active game sessions не разделён по аккаунтам после logout/switch

**Доказательство:** `GameSessionCoordinator.sessions` — один `dict[game_id,session]` без `account_id` (см. `application/game_sessions.py`). `MainWindow.online_sessions` — публичный alias на этот dict. `_lichess_authenticated` переключает выбранного пользователя/архив, но **не удаляет и не помечает stale sessions прежнего аккаунта**; `disconnect_lichess` и `_lichess_status("Не подключено")` снимают лишь неподтверждённый ход, старые `status='started/playing'` остаются. `_update_library_active_games` публикует **все** локальные `online_sessions` без owner filter, `_resume_library_game` выбирает session только по game_id и `resume_current_game` позволяет открыть старую доску под другим аккаунтом; `_run_background_sync` и safety guards видят старую human session и могут ложно блокировать safe B (консервативно, но неверно). При переключении A→B поток A закрывается, его stale status может оставаться `started` неопределённо долго.

**ТЗ:** связать каждую session с `account identity/auth_generation` и ownership/capabilities; при смене A→B detach/hide все A active from B controls и вводов, сохранить локально достоверную историю A/finished pending как read-only без потери; A→logout не открывает active actions без valid stream; return to A выполняет authoritative full resync, а не использует старые cached clocks/moves; service.send_move не отправляет B token для A game. Для нескольких active игр *одного* аккаунта функциональность остаётся. При safety check old A human session **не** объявлять B safe, но не использовать stale A как постоянную блокировку после достоверного B snapshot; пока B unknown — fail closed. Tests: two isolated synthetic accounts A (human live), B (AI/no live), A→B→A, stale A gameStart late, library active list and global Return button, game ownership before POST, clock/resume, Fair Play per-account verified snapshots, sqlite pending finalization retention. Без реального Lichess.

## 3. Блок I — контрольный перечень для дальнейшего изучения (НЕ ЯВЛЯЕТСЯ НАХОДКАМИ)

- P0: full chain user account/PAT/OAuth/token store & reconnect; stale events/stream and human game safety; Lichess active games AI/HUMAN validator against real documented API; per-account live session commands; clock authority and game finalization writes.
- P0: current-only line_revision/line_hash + analysis source, mate and cp and accuracy semantics; saved v2→v3 from all three scored sources; empty complete vs partial scored; official Lichess vs local-method formula; legacy database backup/rollback.
- P1: archive lastMoveAt vs createdAt, cursor inclusivity, limited NDJSON pagination, late games; Lichess API stream finite vs live resource gate; puzzle history retries and duplicate attempts; Lichess analysis backfill and Retry-After/resume.
- P1: background thread/process leaks, concurrency priorities, durable SQLite writer, cancellation, GUI blocking, QSettings/threads at shutdown, UCI install verification/security and post-install rollback.
- P1: tests for migrations from *realistic old schema*, many accounts/sources, long timestamps, high DPI, local PGN/FEN, invalid and corrupt data, 429 and auth switching. No acceptance with green CI alone.


## 4A. Блок II — подтверждённые замечания UI, controls, navigation (СОХРАНЕНО, НЕ ФИНАЛЬНО)

### UI-01 · P1 · DEFECT — кнопка «Назад» внутри Review обходит единый router

**Доказательство:** `src/chess_coach/ui/game_review.py` связывает `back.clicked` с `return_requested`; `MainWindow` соединяет с `_return_from_review()`, который для **любой сохранённой** партии всегда вызывает `show_saved_games()`. Но сохранённую партию можно открыть из `Прогресс`, `Главная`, `Мои партии` и `Анализ`, а глобальная `navigate_back()` поддерживает `WorkspaceNavigator` и origin. В результате две кнопки «Назад» в одном окне возвращают в разные места; из «Прогресса» встроенная в Review кнопка неожиданно выбрасывает в «Мои партии», теряя исходный контекст и фильтр.

**ТЗ:** `GameReviewWorkspace.return_requested` возвращает через **тот же navigation history/return target**, что и shell Back, с устойчивым `RouteEntry` для `Прогресс→Review`, `Мои партии→Review`, `Главная→Library→Review`, `Анализ→PGN/FEN→Review`. Предусмотреть fallback if origin destroyed и restored route; ephemeral PGN не записывается в архив. Qt tests: обе кнопки вернули одинаковый destination, сохранили source/account/page/scroll/selected game и активную онлайн игру.

### UI-02 · P1 · DEFECT — «Разобрать» доступно до сохранения финальной линии, клики могут бездействовать или открыть старую редакцию

**Доказательство:** `MainWindow._show_result` при `final_sync_pending` или `has_pending_write` честно сообщает «ожидаем финальную историю/сохраняем», но **`self.finished_review_button.setEnabled(bool(session.moves))`** (аналогично `result_review_button`), даже если финальная версия не подтверждена. `MainWindow.review_finished_game` делает `get_game` и `_open_saved_game` без сравнения сохранённой `moves/status/line_revision` с финальной `session`. Если запись отсутствует — нажатие молча ничего не делает; если хранится прежний snapshot — открывает старую партию.

**ТЗ:** disable/relabel «Разобрать» пока `final_sync_pending`, SQLite in-flight или mismatch; после confirmed saved exact line/hash/current, enable, открыть только persisted completed version; при storage error — visible «Не удалось сохранить / Повторить» без запуска Stockfish, без потери уже подтверждённых ходов. Test delayed SQLite final write, stale previous line, failure/retry, double click, safe review state, same board/clock after finish.

### UI-03 · P1 · DEFECT — послепартийная кнопка «Вызвать снова» может быть активна при отсутствии Lichess-соединения

**Доказательство:** `_show_result` устанавливает `finished_challenge_button.setEnabled(not self.offline_mode)`, не проверяя `_online_actions_allowed()`, stream-ready/429. В результате в обычном режиме с отключённым Lichess CTA выглядит доступной; `_challenge_again()` вызывает `play_ai/challenge_username`, где `_online_actions_allowed` отвергает запрос без ясного сообщения в игровом workspace. Нет явного disabled reason/accessible tooltip.

**ТЗ:** CTA привязать к server readiness, auth gen и точному типу соперника; состояние `Ожидание соединения/429/Недоступно` с пояснением, без false click, либо открывать Play и показывать нужный status. Не создавать вызов автоматически; подтверждение соперника/AI уровня через форму. Qt tests finished game→offline/disconnect/429/auth switch/stream ready, button state, no duplicate challenge.


### UI-04 · P1 · DEFECT — на вкладке «Играть» нет видимой отмены ожидающего вызова

**Доказательство:** `PlayPage._emit_create` выставляет pending и выключает единственную главную кнопку; **PlayPage не содержит команды `Отменить вызов`**. `MainWindow._challenge_status` показывает `cancel_challenge_button`, но этот виджет создан в правом боковом блоке **game_page**, а пользователь по-прежнему находится на **play_page**, пока вызов ожидает ответа. Для friend URL аналогично: `_challenge_ready` показывает `cancel_invitation_button` на game_page, а ссылка — в отдельном modal QDialog. Есть вызовы service.cancel_username_challenge / cancel_open_invitation, но они недостижимы из видимого «Играть», где user инициировал запрос. Требование COV-03: pending имеет status **и cancel в том же контексте**. Также `_play_mode_page.set_status` сбрасывает форму pending при статусе «ожидаем», хотя `_awaiting_game_start` в window остаётся true.

**ТЗ:** явная кнопка отмены в Play form (username friend AI creation phases according to supported API), идентичный request id и active challenge id; не принимать cancellation до появления id как серверный успех — при pending выставить локальный cancel flag и отменить серверный challenge сразу после позднего id; если POST уже подтверждён, API cancel + authoritative reconciliation; current generation guard. Не показывать модалку как единственный способ получить friend link: inline read-only link + Copy + Cancel в Play, модалка по решению владельца опционально, если не мешает навигации. Qt тесты waiting friend/username, created link, cancel before/after ID, reconnect, 429, switch to live game, no repeated POST.

### UI-05 · P1 · DEFECT — редактируемое поле заметки сохраняет текст предыдущей выбранной партии в новую

**Доказательство:** `LibraryPage.note` — одна `QLineEdit` вне списка. `LibraryPage._selection_changed` вызывает `show_game_detail(game)`, но **ни `_selection_changed`, ни `show_game_detail` не сбрасывают/привязывают `note` к game_id**. Обработчик `save_selected_note` берёт **актуальную выбранную** партию из QListWidget. Поэтому при вводе заметки для A, выборе B, нажатии «Сохранить» текст A записывается в B. Это документированное поведение кода, не просто cosmetic.

**ТЗ:** привязать draft текста к выбранному `game_id` (и `line_revision`, если поле относится к ply 0); при переключении показать сохранённый draft соответствующей партии либо честный вопрос о несохранённых изменениях; не записывать draft A в B, disable Save на пустом поле/нет партии; при сохранении чёткое подтверждение, старые заметки доступны в Review tabs; ошибки хранения видимы. Тест A typed unsaved→B select→Save, A→B→A restore, line reimport, stale selection after async query, no UI thread blocking.

### UI-06 · P2/P1 · DEFECT — «Прогресс» строит SQL filter options синхронно в QWidget constructor

**Доказательство:** `StatisticsDialog.__init__` на GUI thread вызывает `options = self.service.filter_options()`, который выполняет SQLite query; любой вход через `MainWindow.show_statistics()` впервые создаёт `EmbeddedStatisticsPage` там же. Это противоречит согласованному RF-06 «никаких SQLite данных в QWidget constructors» и создаёт заморозку при длительной SQLite transaction/migration. На 1280×720 окно может выглядеть «не открылось», даже если фоновый statistics worker потом исправен.

**ТЗ:** показать скелетон/загрузку без SQL в конструкторах; app-owned scoped data loader возвращает filter options с generation guard, безопасное сохранение ранее выбранных account/clock и контролем до новых option; no cross-thread QObject mutation; bounded SQLite; Qt test: задержать SQL read под отдельным write lock, открыть Progress и проверить, что paint/кнопки отвечают, filter options arrive without stale overwrites.

### UI-07 · P2 · DEFECT — у пустого списка партий «Прогресса» неверный диапазон «Строки 1–0 из 0»

**Доказательство:** `StatisticsDialog._apply_result` всегда рисует `f"Строки {self._page * 100 + 1}–{self._page * 100 + len(self._rows)} из {parties['total']}"`, даже при `rows=[]`, `total=0`. Это честное SQL-нулевое значение, но ложный номер начальной строки. Корректный empty-state уже есть (`games_empty`), нужно убрать противоречие.

**ТЗ:** при total=0 выводить `Партий в выборке нет` и disabled pagination; после изменения фильтра/периода reset page без значений 1–0, stale selection disabled. Qt tests пустой archive, no score under Fair Play mask, source with zero matches.

### UI-08 · P2 · HIGH-RISK CONTRACT — прогресс во время auto-миграции должен сохранять текущую страницу

**Источник:** `StatisticsDialog._schedule_refresh()` безусловно делает `self._page=0`. После доработки CORE-03 новый автоматический requery ПОСЛЕ каждого batch через этот handler сбросит список на первую страницу и снимет выделенную партию. В первоначальном коде такого live reload ещё нет, поэтому это **не отдельный доказанный текущий баг**, а обязательный guard интеграции CORE-03.

**ТЗ:** новый метод `refresh_data_preserving_filters_and_position()` (или явный `reset_page=False`) для событий пересчёта, stable game_id/page/scroll/active tab, nonblocking stale replies; тест на страницу 4/10 и открытый detail, migration batch завершён, UI не прыгает.


### UI-09 · P1/P2 · HIGH-RISK GEOMETRY — FEN editor «Открыть позицию» может оказаться ниже видимой области на 1280×720

**Проверенная структура:** `AnalysisLandingPage` содержит в обычном `QVBoxLayout` (без `QScrollArea`) три карточки, отдельный `QGroupBox` с текстовым PGN/FEN `QTextEdit(maxHeight=150)`, а затем `PositionSetupWidget` с жёсткой доской `setFixedSize(320,320)`, строкой выбора фигур, рокировкой/EP, тремя кнопками и status. С учётом шапки/Windows font scale суммарная минимальная вертикальная геометрия **может** превысить 720px и обрезать нижний action `Открыть позицию в разборе`; этот эффект ещё не измерен на native Windows, поэтому классификация **HIGH-RISK, НЕ подтверждённый визуальный факт**.

**ТЗ/QA:** средствами Qt измерить `QRect` критических FEN controls (320 board + actions) относительно visible viewport для 1280×720/1366×768, 125%/150% DPI, expanded/collapsed FEN editor, PGN input; при воспроизведении сделать локальную прокрутку **только на странице Анализ** или адаптивную высоту editor (не глобальный scroll Home/Library) и скролл к кнопке/Keyboard focus. Все шахматные поля и 64 клетки кликабельны и верно mapped; не терять EP/castling rights.

### UI-10 · P2 · DEFECT — последняя успешная синхронизация задач исчезает из tooltip после загрузки списка

**Доказательство:** `MainWindow.show_puzzle_history::refresh_account_summary` устанавливает `summary.setToolTip("Последняя успешная синхронизация: …" + error)`, но затем вызывается `render_page()`, который безусловно делает `summary.setToolTip(_puzzle_attempt_summary(state["items"]))`, перетирая состояние синхронизации. При любом переключении аккаунта `select_account()` снова вызывает два метода в том же порядке. Пользователь теряет доступ к доступной диагностике и времени sync из единственной подсказки.

**ТЗ:** разделить tooltip источника/синхронизации и summary subset попыток (отдельные labels/tooltips), не показывать персональные секреты; Qt test initial first page, switch account, retry error and empty attempts → last_success / status остаются доступны, выбор попытки не стирает диагностический tooltip.

### UI-11 · P2 · HIGH-RISK PERFORMANCE — поиск партий создаёт новый запрос на каждый символ без debounce

**Доказанная связь:** `LibraryPage.search.textChanged`, `opponent_filter.textChanged` и `opening_filter.textChanged` сразу вызывают `_emit_filters()`; `MainWindow` отменяет прежний `_library_query_handle` и запускает новый background query. Это может создавать десятки потоковых задач при быстром вводе/SQLite writer lock. Сама SQLite остаётся в worker, поэтому **не утверждать подтверждённое зависание** без fake lock/инструментирования.

**ТЗ:** измерить actual bounded worker backlog; если воспроизводится, 200–350ms debounce только для typing filters, сразу обновлять combo filters и явные page buttons; latest-generation result only, no lost account/source after async options update. Тест fake slow SQLite, 15 символов подряд, upper bound started calls и close/cancel; не тормозить возврат к живой игре.

## 4. Блок II — список TODO матрицы UI / функциональной согласованности

Требуются source review и тесты, чтобы отличить неактивный compatibility adapter от реально видимой кнопки:

1. Shell, route/back, sidebar collapse, Lichess status/readiness, header; root Back visibility, auth status text wrap.
2. Home: compact KPI and 3 newest games, mini trend, account scope, truthful empty/unsafe states, single CTA for next exercise, no unnecessary mode/time control.
3. Play: 5 mode options and valid parameters/rated, explicit create, cancel, pending; check for alternative modal duplicate via legacy callbacks.
4. Live game: same board, clocks, player identity, history navigation, premove, accept/decline takeback/draw, claim/resign/abort, multi-games, live reconnect, final sync and postgame two buttons only.
5. Library: pinned active, pagination, side filters, search ECO Russian, sort date, graph, details→review, export/notes, sync/recovery; no clipped controls.
6. Analysis: three cards, ephemeral text/file PGN and FEN editor, board validity, CP/Mate/AdvantageBar/Δ graph, drill-down, source and provenance, batch queue/stop/resume, fail-closed.
7. Training: puzzle attempts history, refresh token scope, offline solve/reveal/repeat, live safety; defer novel trainer/voice and piloted 5–10 UI.
8. Progress: all 8+ filters, openings/phase/game timeline, account and source, official vs local percentages, chart, stats reload and drilldown.
9. Settings/background/sounds: every control must be hooked and readable; puzzle/archive auto-sync independent; engine configure/test/install, WAV preview/preset as-is; rate-limit inspector.
10. Universal: geometry, accessibility, keyboard, Windows 125/150%, async UI spinners/stale workers, destroyed widget Qt signals, privacy logs, failure/empty/loading states.


## 6. ЗАКРЫТАЯ ПО ИНВЕНТАРЮ МАТРИЦА 24 СОГЛАСОВАННЫХ ГРУПП (доказательства handlers + недостающие сценарии)

**Принцип:** по состоянию на SHA `7a3a6775…` найдены текущие UI класса `ui/pages/*.py`, `ui/dialogs.py`, `ui/analytics_dialogs.py`, `ui/game_review.py`, `window.py` и смежные application/SQLite/Lichess/Stockfish исходники. В колонке «Требуемая проверка» часть строк — **проверить**, а не «уже доказанная поломка». Нельзя слепо переписывать все рабочие разделы. Каждый visible control должен иметь тест клика или подтверждённое reason why disabled/hidden.

| Группа | Фактические controls/source | Главный риск и обязательный сквозной acceptance |
|---|---|---|
| **COV-01 Shell** | sidebar 6 nav, settings, Collapse/Expand, Connect/Disconnect, safety retry, shell Back, Return-to-game, compact status | CORE-01/02/11 account generations; Home root Back hidden; unknown/reconnect/429/disconnect available, stale account not restored on late callback; keyboard + accessibility/tooltip, DPI |
| **COV-02 Home** | 3 KPIs games/scored/official accuracy, progress trend, training, latest 3 cards, Все партии/Прогресс | CORE-03/04 scope migration and no cross-account; last 3 by game chronology not DB updatedAt; card opens exact ID with Library filters/page; official accuracy masked; no redundant control-of-time UI |
| **COV-03 Play** | embedded mode Combo local/AI/friend/username/active, color, clock/custom, AI level, Rated, one main CTA; pending status | **UI-04** pending cancel in Play; color/clock validation/only supported rated modes, duplicate click/unknown result, URL inline copy + cancellation, no automatic second game |
| **COV-04 Active games** | ActiveGamesDialog refresh/open, Library pinned sessions, Return to Game | **CORE-02/11** per-account source, active list from server + local reconciliation, multi-game switch, never open A under B; same board restored without override |
| **COV-05 Live commands** | move/clock/premove/flip, takeback offer+accept+decline, draw offer+accept+decline, claim-draw, resign, abort/claim-victory, cancel outgoing invitations | **CORE-01/08/09**, milliseconds **CORE-05**; all visible/disabled correctly, confirm destructive action, single POST, unknown response no resend, server snapshot ownership, no human engine advice |
| **COV-06 Local board** | Local mode free, flip, undo, board dimensions, local history; historical premove-training Combo hidden | Keep separate premove-training/test move UI **HIDDEN**, normal online premove preserved; source-only legacy adapter not a visible feature; check local mode switching cannot erase active online session |
| **COV-07 Board and history** | BoardView click/drag/legal highlight/promotion/manual arrows, history back/forward, cancel premove | all 64 squares at 1280×720 & 150% DPI; clocks, status and animations do not cover bottom rank; history remains factual during live human; no unintended move in past ply |
| **COV-08 Finished** | same `game_page` board + frozen clocks + history; actions Разобрать/Вызвать снова and secondary PGN | **UI-02/03** saved final exact revision before Review, no fake finished snapshot or automatic UCI, no result modal, no ghost rematch, rematch available only when server ready |
| **COV-09 Archive** | Library «Синхронизировать», Background status/settings; durable incremental/backfill/Retry-After | **CORE-06** accurate createdAt, pagination boundary, 429, manually pause/resume, account context, imported duplicates, failed restore; **NO pilot 5–10 UI** |
| **COV-10 Ready Lichess** | Background manual check, pause, Retry state, quota settings, source summary | independently scheduled official ready-data JSON vs local Stockfish, no new server analysis request, no loop on 429, no unauthorized cached evaluations; status can recover after restart |
| **COV-11 Library** | search/opponent/opening and 8 selectors, pagination, active rows, chosen detail, PGN export, note+Save, Review, Stockfish, single recovery refresh | **UI-05** note belongs to correct game; **UI-11** typing request backpressure; filters and scroll persist across Review, exact game ID, no stale SQL worker result, no hidden data after revoke |
| **COV-12 Review** | Return/Export PGN, board+AdvantageBar+ReviewGraph, Evaluation/Δ, marker toggle, phase chooser, tabs moves/analysis/events/notes, PV | **UI-01** return semantics from all origins; source/coverage/CP/mate typed, both sides, accepted offline/Fair Play only, graph click correct ply, review branch is ephemeral and non-destructive |
| **COV-13 Error exercises** | Разобрать мои ошибки, previous/next, Check/Hint/Skip/Continue/Repeat/Return | no training in active human; preserve existing error-based workflow without new full tutor, mark unverified hint/line clearly, state reset and sound AS-IS |
| **COV-14 Stockfish settings** | Find/Browse/Install official, UCI Verify, nodes/depth/time preset, Threads/Hash, settings Save/Cancel | no engine from page constructor, explicit UCI admission, binary hash/signature rollback, settings persistence and cancellation, physical native UCI test owner-only |
| **COV-15 Batch** | Start/Resume/Pause/Cancel with confirmation, retry failed, selective review, budget, advanced, log open | one durable queue & one manager, no multi UCI, cancel keeps completed results, after 429/reconnect resume due safely, UI leave without cancelling queue, no large wall controls |
| **COV-16 Progress** | Overview/Games/Openings-Phases/Critical; Account/Period/Clock/Side/Result/Game source/Analysis source/Analyzed/Opening/Opponent, opening classify 25, chart, detail/position, Batch | **CORE-03/04 UI-06/07/08**; no SQL in ctor, correct read-model scope and update while open, no 1–0 of 0, preserve current tab and page 4 during batch; don't confuse official with local |
| **COV-17 Puzzles** | account, search, result, prev/next, refresh, saved attempt board, Replay, Reveal confirmation, Reset, Close | **CORE-07 UI-10** background SQLite, independent puzzle credential and auto sync, account scope and repeat offline, source tooltip last success/error persists, no pilot UI |
| **COV-18 Sounds** | WAV selectors per cue, master volume, start/move/capture preview, toggles moves/notifications/history, Classical 45% | **AS-IS** catalog/default IDs/QSettings/cue triggers; physical listen test; no refactor/asset modifications without owner consent |
| **COV-19 Auth** | ConnectionDialog OAuth/PAT radio, restore, browser connect, token validate/local delete/OAuth revoke, Cancel/Close; shell Disconnect | **CORE-01/02/11**, no auto token access in manual-connect, no token in logs or GH, immediate safe mask, old request cannot wipe new slot; real owner PAT test later |
| **COV-20 Diagnostics** | Open logs, Copy anonymized report with explicit consent, token status not value, SQLite status and UCI errors | no token/PGN personal paths in reports, sane explicit user choice, nonblocking report generation, 429/403 source-specific status and errors |
| **COV-21 Workspace restore** | history back, selected account & filters, last review ply/tab, sidebar & window geometry, single instance | **UI-01/CORE-11**, A→B auth and restore must not revive A live control, ephemeral PGN never saved, Back semantics stable, multi-display DPI frame visible |
| **COV-22 Chess facts** | opening matcher Russian/ECO, phases, critical event filters, Win% accuracy, move metrics, CP/mate/WDL provenance | **CORE-06**, move metrics v2→v3, scope and source, Mate(0) both colors, no fake 100000 CP, partial/no eval not counted as complete, no fabricated 03:00 |
| **COV-23 Background inspector** | Refresh, Pause Lichess, Manual ready check, Back, shared workers statuses/queue, local v3 migration | **CORE-03/04**, true per-source label and scope, 0–N progress, independent archive/puzzle, background worker priority never blocks active move/safety; no PII |
| **COV-24 PGN/FEN & position** | Analysis 3 cards, PGN text/file, PositionSetup piece selector/turn/castling/EP/reset/clear/open, Review CP/Δ and local eval | **UI-09** 1280×720 expanded editor geometry, no silent loss of castling/EP, line hash, legal SAN, ephemeral external review not imported into local archive, no unsafe engine admission |

**Вывод матрицы:** 24/24 групп внесены с реальными точками входа/проверками. Нельзя считать 24/24 физически PASS: текущий source audit проверяет связи и риски; множество факторов требует синтетического GUI и владельческого Windows pilot. Не удалять уже работающие режимы за пределами утверждённой UX карты.

## 7. ЕДИНАЯ ТЕСТОВАЯ ПРОГРАММА и release gate вместо десятков мелких RF

### 7.1 Строгие automated сценарии — охват матрицей, НЕ только счётчик pytest

1. **Identity/mutation concurrency P0:** synthetic accounts A/B and PAT/OAuth slots; async barriers delayed move 401, resigned action 403, active-game snapshot late A, username challenge/cancel late, reconnect same username old generation, token compare-and-swap; no B token deletion and no POST A action under B. Include A human live→B safe AI→A active again, per-account session registry, Fair Play unknown/429. Confirm clicks are blocked on all forbidden stages.
2. **Data integrity P0:** temp SQLite fixtures with old schema/metrics v2, game line revisions/PGN and official Lichess accuracy, 10k+ mixed saved games and multi-account owners. Repeated archive/PGN/JSON imports, backfill cancel/restart, line CAS, exact canonical score, terminal mate both colors, full/partial/no-source classifications. Simulate writer busy and failures, no personal DB.
3. **Network throttling/failure P0–P1:** fake Lichess account/playing official AI/human/unknown and 50-game saturated snapshot, stream started/ready/lost/reconnected, 429 Retry-After, 401 for background analytics vs account endpoint, 403 scope, stale success. No rate-limit storms, no optimistic engine readiness; manual control remains available.
4. **Game UX P1:** live 3+2 / 15+10 / correspondence / no-clock, full GUI geometry for both colors/premoved/drag/cancel/history/finish while other game active; double clicks on irreversible actions; result when SQLite write pending or fails; Review only when actual saved exact final; opponent rematch never auto-POST.
5. **Full UI keyboard and click walk P1:** six sidebar screens + Settings and nested pages, each enabled button must perform its advertised action or explain disabled/empty state; simulate navigation Home→Library→Review→Back and Progress→Review→Back, external PGN/FEN branch, accepted/revoked safety, batch pause/resume, puzzles repeat, opening critical position drill-down. Check offscreen and visible Windows synthetic.
6. **Performance/liveness P1:** hold SQLite writer lock while opening Progress/Puzzles, updating Home, finishing online game, switching auth and saving notes; 50 rapid search edits, 5 queued account switches; GUI paint/clock/cancel responsive and no QObject accessed after destruction. Bounded worker registry, no stale result over current screen.
7. **Stats correctness P1:** real legacy-format synthetic scores from Lichess-PGN/JSON and local Stockfish in A/B, scored partial > empty complete, revocation hides cached values in label/tooltip/accessibility, official vs local accuracy separate; Progress opened before and after v3 migration, filters no page jump or wrong account counts.
8. **Layout/accessibility P2:** 1280×720, 1366×768, 1600×900, 1920×1080, 100/125/150% Windows DPI, sidebar expanded/collapsed, font size, long username/opponent, huge timeline/labels, both colors and two move lists. Ensure mouse hit map each board cell, buttons within viewport, tooltip/accessibleName, no global Home/Library scroll.
9. **Audio/asset preservation P2:** tests validate every existing cue/preset WAV default ID/QSettings keys, no changed assets and physical preview audible; game start / move/capture / premove / history / notification. Confirm no duplicate start sounds after reconnect.

### 7.2 Codex execution instructions — one consolidated PR, not new micro-specs

- BEFORE changes: exact SHA/branch/dirty diff, baseline full `pytest`, `compileall`, `git diff --check`; users' `.venv312/` unmodified.
- Create **one tracked issues → root causes → files → reproduction/test → state table** in `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md`. All CORE/UI items; each **DEFECT** gets red→green testcase, **HIGH-RISK** gets deterministic reproduction and fix **only if confirmed**, **VERIFY** gets explicit `PASS` evidence or `BLOCKED`. No blindly feature-rewrite.
- Work in **existing code draft PR #44** only. Separate commits by subsystem inside same PR acceptable; no new P0 implementation branches or false merging. Existing separate docs draft PR #45 remains review source, don't merge it.
- Re-run full tests and **native visible Windows app on synthetic profiles** with realistic 2-account fixtures and each required screen/action state. A hosted CI passing means only its declared steps; fake Lichess cannot prove real OAuth/PAT/Stockfish. Capture synthetic-only screenshot/checklist, never upload owner screenshots or personal data.
- Get **new actual completed/SUCCESS hosted Windows CI on final exact SHA**; update `docs/PROJECT_STATUS.md` and log with evidence, not an empty claims table. No real user SQLite access, no OAuth/PAT/browser profile or live game creation without owner permission. No automatic Stockfish on human play.
- A single final independent review must cross-check **all CORE/UI IDs, regression tests, old/working UX preservation**, not just latest diff. If a new confirmed P0 is discovered during fixes, add it to the same master checklist and same PR rather than issuing another serial RF task. OWNER physical pilot is separate gate; only owner may approve merge.

### 7.3 24-group acceptance report template Codex must complete

`COV-ID | visible control(s) | exact handler | conditions/expected side effect | fake test ID | native smoke evidence | PASS/FAIL/BLOCKED | why | commit SHA`

Every one of 24 rows must have owner of its verification, and notable sub-actions (45+ controls) expanded as subrows. **No acceptance as «750+ PASS, therefore everything is fine».** If UI cannot be visually run, mark BLOCKED truthfully, still test with Qt offscreen and explain gaps. Accept production-readiness only after code/source pass and owner explicitly confirms real Windows/PAT/Stockfish smoke.

## 8. Условия единственной будущей передачи Codex — пока ЗАКРЫТО

После **Блок I завершён / Блок II завершён**, проверить минимум: P0 frozen gates, P1 end-to-end scenarios, P2 geometry/wording, UI action matrix with code references, red→green tests, synthetic old DB fixture, actual native Windows GUI smoke by Codex in each tab/button state, full pytest + compileall + Git diff check + exact-head completed Windows CI. PR #44 draft, no merge until owner explicitly approves physical pilot. Давая отчёт, не скрывать реальные blockers и не пытаться заменить реальный Lichess настоящим PAT/real DB. **Никаких отдельных «RF10–RF20»** из этого master-документа.
