# Chess Coach · PR #46 — независимое сквозное source review и ОДИН сводный пакет исправлений

**Дата:** 09.11.2026.  
**Решение:** **REQUEST CHANGES / НЕ ПЕРЕДАВАТЬ В РУЧНУЮ ПРИЁМКУ КАК ГОТОВЫЙ РЕЗУЛЬТАТ**. **Дополнение владельца 10.11.2026: P6–P9 и Home Instant Load теперь входят в тот же пакет. Повторное полное source review Codex SHA 2924530: ещё 6 замечаний N1–N6 в §5, выполнять в том же едином цикле.**  
**Проверенный кодовый HEAD:** `fa53772d4b751543ba96d8458c0eecb6454fdda4` (Draft PR [#46](https://github.com/feshakomissarov-prog/chess/pull/46), ветка `codex/unified-desktop-ux-2026-11-08`). Перед началом повторить сверку HEAD; если он изменился, сравнить всё перечисленное с фактическим кодом.  
**Проверенный CI:** [Windows run 42625874641](https://github.com/feshakomissarov-prog/chess/actions/runs/42625874641), оба jobs SUCCESS, заявлено 994 pytest. **Это свидетельство о прогоне Codex, не о том, что найденные ниже сценарии покрыты**.  
**Прежний нормативный пакет:** [PR46 consolidated S1–S4](CHESS_COACH_PR46_CONSOLIDATED_SOURCE_AUDIT_AND_SINGLE_FIX_BATCH_2026_11_09.md); [UX master v2](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md). Прежние мелкие R1–R4 исторические: не выдавать их отдельными заданиями и не откатывать уже правильные изменения.

## 0. Итог независимого ревью и границы доказательств

Просмотрены PR #46 и его свежий diff относительно `6fa202f...`, архитектурные/проектные инварианты и связанные source paths: Lichess service/auth/session/api, `MainWindow`, игровое/Review/UI, Play/Analysis/PGN/FEN/archive, Home/dashboard, Fair Play и tests/Windows workflow. **Подтверждено source-level пять дефектов/нарушений контрактов:** **P1 — 2; P2 — 3**. Дополнительно найден **один пакет недостаточного acceptance evidence** (не считать самостоятельным кодовым дефектом).

**Новые локальные pytest/Windows GUI/два настоящих аккаунта Lichess независимым ревьюером не запускались.** Результат статической проверки не выдавать за динамическое воспроизведение. Codex обязан сначала воспроизвести дефекты короткими fake/Qt tests, выполнить полный прогон после фикса, затем обновить один отчёт. Настоящий live-тест с двумя аккаунтами **отложен владельцем**, токены не запрашивать. Approved 13 PNG уже представлены владельцем как ориентиры, но native side-by-side всё ещё не подтверждён.

### F1 [P1] Подтверждённое предложение ничьей снова становится доступным после позднего старого флага

**Место:** [`session.py:315–332`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/session.py#L315-L332), [`service_games.py:222–306`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L222-L306), [`window.py:8004–8047`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/window.py#L8004-L8047).

**Точный воспроизводимый источник риска:** исходное `wdraw=False` → один HTTP offer → авторитетное `wdraw=True` при тех же ходах (сервис очищает pending как подтверждённый offer) → поздняя повторная копия старого `gameState`/ `gameFull` с `wdraw=False` или вообще без `wdraw`, **без продвижения линии**. `LichessGameSession.apply_event` без проверки содержательной новизны делает `draw_offered_by_me=False`, и кнопка может разрешить **ещё один** `POST /draw/yes`. Исправление S1 фокусировалось на replay *до* первого подтверждения, но не защищает это состояние *после* `True`. Зеркальный сценарий: старый `opponent_draw=False` скрывает действующее входящее предложение.

**Исправить:** согласовать серверный reconciliation и UI/session-представление; не считать изменившийся флаг в позднем event доказательством отмены, если нет проверяемого более нового серверного контекста (line progression, корректный resync semantics / account+game+generation, отсутствие старого replay). Отсутствующее поле `wdraw/bdraw` не эквивалентно подтверждённому false. Не «лечить» фальшивым таймером или ручным обнулением флагов. Не разблокировать повторный irreversible POST при неясности, но **сохранить независимую сдачу**. Сценарий с настоящим новым предложением позже должен оставаться достижимым, когда это действительно подтверждено сервером.

**Тесты:** status-only, missing flags, own true→old false (0/1/2 replay), opponent true→old false, `gameFull` replay, новые/старые ходы, clock-only, HTTP 2xx/429/timeout, два fake-клиента с реальными `LichessGameSession` + UI, смена аккаунта и игры. Считать число HTTP POST; повторов нет.

### F2 [P1] Service-level запрет POST до fresh game snapshot не реализован полностью

**Место:** [`service_games.py:149–215`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L149-L215), [`service_games.py:62–133`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L62-L133), [`service_auth.py:426–460`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_auth.py#L426-L460).

**Причина:** новая UI admission проверяет `_online_actions_allowed` и `awaiting_authoritative_snapshot`, но `LichessService._action` и `takeback` на своём слое проверяют только контекст, остановку, terminal и pending; там **нет связанного с gameId свежего gameFull/admission lease**. При transient reconnect (авторизация/generation та же, `connection_stop` не установлен) вызов сервиса напрямую, либо отложенный callback/вторичная команда, может выполнить POST до переподтверждения позиции. Нельзя считать отключённую Qt-кнопку единственной защитой.

**Исправить:** server-action admission внутри сервиса, связанный с `generation+gameId`, current stream readiness и подтверждённой `gameFull` после каждого disconnect/429/reconnect; связать обновление допуска с фактически *принятым* `session.apply_event` snapshot, не с любым событием транспорта; отсекать queued worker перед HTTP и не создавать повторов. Совместимость с explicit action during outgoing offer и обычным действующим online-контекстом сохранить.

**Тесты:** service calls напрямую и queued calls при loss/reconnect/429/cooldown/auth switch/old gameId, без fresh `gameFull` — **ноль transport POST**; после валидного fresh snapshot — ровно один допустимый POST. Проверить UI и service независимо.

### F3 [P2] `claim_victory` выпал из матрицы конфликтов завершающих команд

**Место:** [`service_games.py:13–29`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L13-L29), [`service_games.py:714–716`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L714-L716).

`claim_victory()` существует, вызывает тот же `_action`, но команда не входит в `GAME_ACTION_CONFLICTS`: одновременно с pending resign/abort/claim_draw/takeback разрешается конкурентный claim victory, и наоборот. Встроить этот завершающий endpoint в общую симметричную матрицу/atomic guard. Если UI не содержит эту кнопку — не добавлять ради исправления, но сервисный контракт обеспечить. Тестировать сочетания обеих сторон и terminal.

### F4 [P2] Тихий ранний выход в `takeback` оставляет вечный pending

**Место:** [`service_games.py:62–133`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/lichess/service_games.py#L62-L133).

В worker сначала резервируется pending, а `operation_token(context)` может вернуть `None` при ещё текущем контексте, но недоступных credentials. В ветке `if not token: return` отсутствует снятие reserved pending и completion/failure. В результате после потери учётных данных без смены generation возврат хода застревает без уведомления. Обеспечить `try/finally`/симметричный lifecycle: при неотправленном POST — definitive `-2` с освобождением сервиса и UI, при ambiguous request — безопасное ожидание подтверждённого состояния без автоматического повтора. Тестировать токен `None`, смену credential slot, disconnect во время ожидания, 401/403/429/timeout и поздний callback.

### F5 [P2] Неполный общий перечень terminal statuses блокирует postgame Review/Stockfish

**Место:** [`window.py:4051–4057`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/window.py#L4051-L4057), [`window.py:5938–5947`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/window.py#L5938-L5947), [`window.py:7896–7905`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/src/chess_coach/window.py#L7896-L7905).

Новая `_refresh_online` считает `repetition`, `50moves`, `insufficientMaterial`, `finished` терминальными, но `_is_live_online_session()` и `_position_eval_context_available()` их не распознают и продолжают блокировать postgame-анализ как при живой игре. Вынести один проверяемый набор terminal statuses для UI, domain и service, не размывая Fair Play: при неизвестном status — fail closed. Тестировать каждый статус в Full/State, итоговые CTA, запрет live POST, разрешение postgame-анализа только после независимого safety admission.

## 1. Дополнительный acceptance-gap (не шестой баг)

Текущий тест `test_two_fake_clients_exchange_draw_offer_continue_then_receive_later_offer` в [`tests/test_lichess_credentials.py`](https://github.com/feshakomissarov-prog/chess/blob/fa53772d4b751543ba96d8458c0eecb6454fdda4/tests/test_lichess_credentials.py#L903-L960) упражняет два экземпляра `LichessService` и отдельные события, но не доводит весь сценарий через **две `LichessGameSession.apply_event` + видимый Qt controller**. Проверка "994 passed" не подтверждает закрытие всех рисковых переходов из прежнего S1–S4 ТЗ. Следовательно: выполнить сквозной fake trace с явной последовательностью POST→HTTP→NDJSON→session→UI, включая stale replay и кнопки обоих игроков; добавить недостающие негативные проверки и матрицу результата.

## 2. ОДНА полная регрессионная матрица — обязательная, без микрозадач

Проверять и фиксировать в отчёте вместе, даже если соответствующая функция не изменена. Кодовые ошибки, найденные при этом, **исправить внутри того же прохода**, а не создавать новое мелкое ТЗ.

**A. Live game / draw / takeback / commands:** F1–F5; HTTP 2xx, 400/401/403/404/422, 429, timeout, late response; terminal все варианты; incoming→accept/decline, outgoing→server reject/continue→later opponent offer; 0–2+ duplicate stream events; clock-only/missing flags; gameFull replay; оба цвета; смена gameId/account/auth method; UI hotkey/direct handlers; pending conflict matrix (включая claim-victory); prompt surrender under outgoing draw; abort/no-moves; premove, history, Focus and Analysis-route switch; no hidden new network POST. Проверить reconciliation и persistence при race HTTP↔NDJSON↔SQLite. Не изобретать право повторного offer, если Board API не даёт безопасного доказательства. Официальная спецификация: https://lichess-org.github.io/api/ .

**B. Connection and safety:** transient disconnect / reconnect, 429 на event stream и game stream, global cooldown, latest gameFull rejected/accepted, stale generation; сохранить доску, часы, ходы, не объявлять окончание только по разрыву; UI и service admission до transport; engine/PV/accuracy/hidden tooltips запрещены во время живой человеческой партии и при неизвестной безопасности.

**C. Shell/Play/Game:** все пять top-level разделов, Back/route persistence/collapsed sidebar, переходы Home→Play→game→Focus→finish→Review→Back; AI, friend link, challenge, free board; color radio, независимые настройки и exact clocks/custom; offline only local; no unsolicited POST; challenge cancel/accept/unknown outcome, no orphan widget.

**D. Analysis/Library/PGN/FEN/Review:** четыре Analysis cards + честные FUTURE; вложенный архив, фильтры, owner/account isolation, Home newest SQLite top3, identical PGNs/multiple candidates, Back/parse generation, transient file/text FEN; position editor legality and castling; Review без метрик и с подтверждённым анализом; source, side/accuracy, graph/move markers, postgame training only with safety lease; stale callbacks, no personal SQLite pollution.

**E. Settings/Progress/Background:** puzzle/history/progress remain reachable; engine verification and manual settings, cancellation of workers, bounded SQLite reads, revision fences, account changes; never modify user PGN/SQLite/tokens. Real Stockfish only in safe environment; otherwise `NOT_VERIFIED`.

**F. Visual / responsive:** actual Windows Qt screenshots UX-01…UX-13 vs approved PNG at 1120×640, 1280×720, 1366×768, 100/125/150% DPI if available. Capture CI != side-by-side. No claim of pass without native comparison; absent assets/environment report specific blocker, not another code defect.

**G. Full regression run:** focused tests proving F1–F5 red-on-before/fixed-on-after, then complete pytest including pre-existing 994, `compileall`, `git diff --check`, Windows CI both jobs **on exact final SHA** (including report-only commit). Document tested/untested separately for each area, list URLs and SHA.

## 3. Требуемый результат Codex — законченный цикл

1. Проверить fresh HEAD PR #46 и весь этот документ. Не создавать новый PR. Не менять main.
2. Воспроизвести F1–F5 на fake streams / Qt tests, затем закрыть их минимальными корректными изменениями, сохранить уже закрытые S1–S4 и UX/архитектурные инварианты. Исправить найденные по смежной матрице проблемы тут же, если они в рамках утверждённого NOW UX и безопасны.
3. Запустить **один полный регрессионный цикл** из §2; актуализировать только один `docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md` с таблицей `ID → root cause → fix → regression test → results → NOT_VERIFIED`.
4. Подтвердить окончательные code SHA и report SHA, exact-head Windows test+visual jobs. Не использовать отчёты предыдущих SHA для результата новой правки.
5. PR оставить **Draft**, не merge, не force-push, не менять пользовательские файлы, PGN, SQLite, токены. Реальную игру двух аккаунтов не проводить; `REAL_LIVE_UNVERIFIED (deferred by owner)` сохранить. При недоступности физической Windows/stockfish/13 screenshot comparison — честно обозначить отдельными непроверенными пунктами, но не объявлять всё успешно принятым.

**Критерий завершения:** F1–F5 закрыты регрессионными тестами, вся матрица проверена настолько, насколько позволяет среда, и остаточные ручные/интеграционные гейты прозрачны. Владелец получает один консолидированный результат, а не предложение нового микро-круга.


---

## 4. Дополнение владельца 10.11.2026 — стартовый экран, завершённые партии, persistent Home

**Статус: утверждённое владельцем дополнение к ЕДИНОМУ пакету PR #46, выполнять в этом же проходе.** Оно имеет приоритет над устаревшими формулировками «Home Instant Load только после PR #46» в отдельном backlog/roadmap; там статус также обновлён. Не ограничиваться F1–F5, **не создавать ещё одну цепочку отдельного PR/ТЗ**.

### P6 [P1 UX/Safety false positive] Сохранённые завершённые партии не должны закрываться экраном Fair Play из-за старта, подключения, 429 или отсутствия preflight

**Подтверждение владельца:** скриншот из настоящего запуска 10.11.2026: разбор **завершённой** партии «lichess AI level 4 — ad_fesha», статус «Завершена», дата 09.11.2026, но слева «Оценки скрыты» и карточка «Аналитика скрыта правилами Fair Play», внизу блокировка после изменения подключения. Это неприемлемый UX для просмотра локальной истории.

**Найденные source paths:** `window.py::_restore_workspace_after_startup`, `_invalidate_analysis_preflight`, `_request_review_training_safety`, `_review_safety_error_received`, `_library_detail_safety_allowed`, `_refresh_dashboard_analysis_visibility`, `_request_dashboard_display_safety_internal`; `ui/game_review.py::revoke_safety/mask_review_analysis`. Проверять реальный источник блокировки и охват Home/Library/Review/Statistics/Progress. Не заменять все эти методы на безусловное `True`: нужны точные границы поведения.

**Продуктовое правило:** просмотр **завершённой** исторической партии, её локально сохранённых оценок, точности, ошибок, графика, названий и источник-метаданных — обычное чтение сохранённых данных. **Не требовать успешного соединения с Lichess, списка активных игр или повторной ручной кнопки «Проверить безопасность» просто для показа истории**, когда нет известной активной человеческой онлайн-партии. При ошибке/429/disconnect/OAuth startup должна оставаться доступна локальная историческая информация, если она действительно существует. Не выдавать отсутствие расчёта за блокировку: пустой source = честный «анализ ещё не выполнен», а не скрытая аналитика; никакой выдуманной оценки.

**Сохраняем строго ограниченную защиту там, где есть реальный риск:** в **идущей онлайн-партии против человека** нельзя получать engine-оценки/PV, подсказки лучшего хода и помощь по текущей позиции; нельзя обходить запрет переходом на другой раздел с совпадающей позицией. В **задачах/тренировочных подсказках** сохранить отдельные принятые условия безопасности. Обычные неинтерактивные исторические агрегаты/статистика сами по себе не являются подсказкой текущего хода. **Проверку безопасности для запуска нового Stockfish/тренировки во время потенциально активной человеческой партии нельзя отключать слепо**, но эта проверка не должна превращаться в блокировку рендера уже сохранённого архива при каждом входе. Никакого расширения Fair Play на все разделы и все завершённые игры «на всякий случай». Не менять реальные online-move/action/finalization guards из S1–S4.

**Регрессии:** открыть завершённый AI game / completed human game из SQLite (Stockfish source, Lichess source, raw-only) сразу после cold start; offline/manual/PAT/OAuth connected/disconnected/429/failed safety / account switch / review→Home→review. График, точность и метрики видны при наличии подтверждённого сохранённого источника; при отсутствии — честное empty state. При реально активной человеческой онлайн-партии engine-assisted подсказки недоступны в любом маршруте, а сетевые игровые команды продолжают соблюдать S1–S4; задачи не ломаются. Проверить, что штатное открытие завершённой партии не делает ненужного сетевого preflight.

### P7 [P2 UX] Приложение ВСЕГДА запускается на «Главной», а не автоматически восстанавливает экран разбора

**Подтверждённая причина:** `window.py::_restore_workspace_after_startup` после `workspace_state.last_route() == "games"` автоматически вызывает `resume_saved_review(show_message=False)`; пользователь увидел старый Review вместо Home.

**Новое правило:** после любого штатного/cold/restart запуска **первым видимым маршрутом всегда Home**, даже если предыдущий сеанс закрыт на Review, Library, Game, Settings, Training или Focus. Разбор/позицию/выбранный ход можно **сохранить как цель для явного «Продолжить разбор»**, но не открывать автоматически. Во время инициализации Lichess/auth/archive/backfill не навигировать автоматически на исторический Review. В случае активной партии можно показать отдельный заметный «Продолжить партию», но без самоперехода. Не затереть сохранённый target просто потому, что стартовая вкладка Home. Проверить Back и первый маршрут на всех 5 разделах/при смене аккаунта.

### P8 [P1 performance/product improvement] Home Instant Load: готовая аналитика сохраняется между запусками

Выполнить в рамках того же текущего пакета требования [Home Instant Load / persistent analytics](CHESS_COACH_HOME_INSTANT_LOAD_PERSISTENT_ANALYTICS_BACKLOG_2026_11_09.md). **Это теперь активный согласованный scope PR #46, а не отложенная идея**.

**Обязательное поведение:**
1. При первом расчёте сохранить и **быструю сводку**, и **полную source-backed аналитику** завершённых партий в локальном versioned persistent cache; запись по мере готовности результата, не только перед закрытием. Важны целостность и privacy.
2. При **повторном открытии приложения** и при **каждом возврате на Home** сразу использовать сохранённый результат, **не запускать каждый раз полный SQLite aggregate**, не сбрасывать UI на placeholders и не создавать видимых мерцаний.
3. Без изменений локальной БД или версии алгоритма — **никаких лишних полных пересчётов**. Синхронизация с внешним Lichess может обнаружить новые записи уже после старта; тогда инвалидировать ровно затронутую область.
4. Изменились завершённые партии, account scope, history задач, coverage/accuracy/line revision, источник или формула метрик → точная инвалидация + фоновое обновление. Предпочтительно инкрементальное; если оно слишком сложно/ненадёжно, один ограниченный full recompute на соответствующий scope с coalescing, без UI блокировки.
5. Кэш по аккаунту/профилю и фильтрам/версии источника; данные другого аккаунта никогда не показывать, старые callback не принимаются; не сохранять engine-hints на живую партию как историческую сводку. Скрытие актуальных engine-подсказок во время активной партии проверяется в момент **показа**, а не считается сохранённым разрешением.
6. Окно/навигация остаются отзывчивыми на обычных Windows ноутбуках; не добавлять обязательные микросервисы, Rust, cloud или новый БД-сервер.

**Проверить:** cold start с готовым кэшем, warm home→other→home многократно, 0/3k/10k синтетических партий, завершение/пересчёт одной партии, новый анализ без новой партии, смена source/version, два разных аккаунта, puzzle-history update, offline/disconnect/429, concurrent SQLite writer, рестарт во время записи кэша, stale callbacks, migration, существующие UX-01…13 screenshots. Замеры до/после для готового и пустого кэша; не придумывать не измеренный SLA. Нельзя ставить «PASS», проверив только unit-test сериализации кэша.

### P9 [P2 visual acceptance] Исправить видимое обрезание / перегрузку Review в присланном реальном скриншоте

На скриншоте 10.11.2026 есть визуально усечённые метаданные/строки боковой панели и верхней области, плотное размещение доски, статуса/графика и действий. Провести native comparison с approved UX-референсами на фактическом размере окна, затем на 1120×640, 1280×720, 1366×768 и DPI 100/125/150 по доступности. Важны полный заголовок/метаданные, удобочитаемые карточки/таблица, рабочий скролл, отсутствие offscreen/overlay. Если среда не позволяет подтвердить визуально — честно `NOT_VERIFIED`, не объявлять это доказанным исправлением без capture/click-through.

### Обновлённый acceptance / отчёт по единому пакету

- В отчёте явно разделить: **F1–F5** прежние source defects; **P6/P7/P9** продуктовые/визуальные регрессии; **P8** одобренное улучшение, не выдавать его за исправленный баг.
- Подтвердить сквозное поведение **Cold start → Home (cached values) → Library → completed Review (исторические оценки видны) → Home instant → restart → Home instant**; с сетевой недоступностью и после успешной синхронизации. Снять native screenshots с реальным заполненным Review.
- Во время реально идущей human online game по-прежнему нет assistance в live game и через обходной маршрут; задачи остаются корректными. В отсутствие человеческой активной партии история не должна зависеть от состояния авторизации.
- Исходники менять **только в текущей PR #46 branch** после запуска Codex; на документационной `design/` ветке хранятся требования. Здесь только ТЗ, никакого merge.
- Финальный exact-SHA Windows CI, полный pytest и diff-check; обновлённые количественные замеры, выполненные/непроверенные GUI, real Lichess two-accounts `DEFERRED`. Ручная приёмка остаётся за владельцем.

**Важное указание владельца:** никаких новых расширений/ужесточений Fair Play вне описанных мест без его явного согласования. Не подменять продуктовые решения дополнительными «страховочными» экранами.


---

## 5. Повторная независимая проверка ВСЕГО приложения — кодовый HEAD 29245300be493a195759c114925040b7df607a0a (10.11.2026)

**Решение:** **REQUEST CHANGES.** Codex сообщил о закрытии F1–F5 и 1074 passed; GitHub подтверждает CI SUCCESS test + visual-evidence в [run #42762438779](https://github.com/feshakomissarov-prog/chess/actions/runs/42762438779) на точном SHA. Подтверждено появление новых тестов, в том числе двух fake-клиентов и согласования состояний с Qt. **Но новые/унаследованные исходные риски остаются; не отправлять владельца на ручную приёмку до закрытия раздела 5.**

**Результат новой независимой source-level проверки:** **6 дополнительных дефектов/нарушений инвариантов: P1 — 3, P2 — 3.** Это количество относится **только к дополнительным замечаниям N1–N6**, не повторяет ранее заявленные F1–F5. Ранее согласованные владельцем **P6–P9** (историческая аналитика не должна скрываться, старт всегда Home, постоянный аналитический кэш, visual polish) выполнять **в том же едином цикле**, а не открывать вторую задачу.

**Охват повторного анализа:** сравнение всего F1–F5 diff, auth/PAT/OAuth, event/game streams, game move/commands, draw/takeback/reconnect, session/Qt signals, Fair Play, review/training, archive/PGN/FEN, Home/dashboard/statistics, SQLite/migrations/jobs, background coordinator, Stockfish, Play/navigation, UI dialogs/pages, bootstrap and tests. Всего кодовая ветка содержит сотни файлов и более 3 МБ Python-исходников; это широкий source audit критичных потоков, **не доказательство исполнения каждого сценария**. Самостоятельные локальный pytest/native GUI/live two-accounts независимым ревьюером не запускались. **1074 passed — только подтверждение ранее выполненного CI Codex**. Ниже каждое замечание сопровождается детерминированным тестом, который исполнитель должен сначала получить красным на 2924530, затем зелёным после исправления, где это достижимо.

### N1 [P1] Новая инверсия порядка блокировок auth ↔ pending: возможный deadlock

**Источник:** [service_games.py:82–101](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L82-L101): `authorize_game_actions` удерживает `_pending_action_lock` и внутри вызывает property `self.auth_generation` → `_auth_lock`. В то же время [service_auth.py:91–105](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_auth.py#L91-L105) (`_emit_session_status`) удерживает `_auth_lock` и вызывает `invalidate_game_action_admission` → `_pending_action_lock`. Аналогичный порядок auth→pending в `_cancel_active_session`.

**Риск:** одновременная обработка gameFull и статуса reconnect/429/disconnect может взаимно заблокировать UI и event-stream worker. Это прямой обратный порядок lock acquisition, не вывод из CI.

**Требование:** установить единый порядок блокировок на всех путях (в т.ч. `_action`, `takeback`, auth/session reset). Исключить вызов property/метода, повторно берущего auth lock, пока удерживается pending lock, либо безопасно упорядочить атомарную проверку lease. Не скрывать race пропуском проверки generation. Добавить двухпоточный barrier/concurrency тест, принудительно пересекающий authorize ↔ status/invalidate ↔ auth switch. Assertions: finite completion (no deadlock), no stale authorization, no POST after status loss; Qt остаётся отзывчивым.

### N2 [P1] Самый важный сетевой POST — отправка хода — не защищён новым service admission

**Источник:** [service_games.py:900–927](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L900-L927): `send_move` получает token и без `_game_action_admission_ready_locked` отправляет `client.send_move`. При этом `_action` и `takeback` уже проверяют new gameFull lease. UI `on_move_intent` обычно блокирует некорректные ходы, но worker может быть поставлен в очередь до disconnect/429, после чего всё ещё иметь token того же auth-generation.

**Контрпример:** fresh gameFull → submit local move → задержанный worker → `invalidate_game_action_admission` при stream loss/429 → запустить worker → POST выполняется вопреки запрету. Ещё хуже: прямой service.send_move до первого gameFull допускается. Текущий тест `test_service_requires_accepted_fresh_game_snapshot_for_every_live_post` проверяет resign, но **не send_move**, вопреки своему названию.

**Требование:** поставить service-level pre-transport admission на `send_move` с generation+gameId+current gameFull lease+cooldown, включая повторную проверку в worker после ожидания credential/priority gate. Не изобретать автоматическую повторную отправку; если POST **точно не был вызван**, вернуть однозначный local rejection и очистить pending intent; после реального timeout сохранить безопасную неопределённость и дождаться серверного state. Тесты на before-first-snapshot, queued-before-disconnect, 429, stale account/game, blocked credential get, ordinary successful move, pending/late ACK, premove и recovery.

**Также проверить общий race** в `_action` и `takeback`: admission проверяется до чтения токена/самого HTTP; если lease стал недействительным во время блокирующего credential get/ожидания сетевого request gate, worker не должен отправлять новую команду после снятия допуска. Отдельный red regression для позднего invalidation, но учитывать неизбежную границу начала уже отправленного HTTP (не объявлять реально начатый POST отменённым).

### N3 [P1] Глобальный event-stream reconnect/429 замораживает все live-партии без пути к fresh gameFull

**Источник:** [window.py:6939–6946](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/window.py#L6939-L6946), [window.py:7148–7154](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/window.py#L7148-L7154), [service_games.py:617–707](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L617-L707), [session.py:377–380](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/session.py#L377-L380). Любой статус глобального «Переподключение» **или 429 одной партии** переводит *все* живые `session.awaiting_authoritative_snapshot=True`. Этот флаг сбрасывается только после принятого `gameFull`. При этом [window.py:1048–1067](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/window.py#L1048-L1067) после global event-stream ready не обеспечивает fresh gameFull для уже работающих независимых потоков партий. `start_game()` игнорирует повторный запуск, если `game_id` уже находится в `_game_stops`. Существующий поток может присылать только `gameState`, но клиент остаётся заблокированным бесконечно.

**Требование:** ограниченно и адресно переподтверждать каждую затронутую live-партию после потери stream/safety с новым accepted `gameFull` или эквивалентным документированным fresh authoritative snapshot. Не считать «event stream ready» сам по себе восстановлением game-state admission; нельзя автоматически перепосылать ходы/команды. Не замораживать все неповреждённые игровые сессии навсегда после 429 другого game stream. Сохранять board/history/clocks/confirmed moves. Проверить два независимых game stream и reconnect только глобального потока; 429 одного потока; старое gameState без gameFull; realigned gameFull; finite retry/backoff; no fake finish, no duplicate stream/POST.

### N4 [P2] Серверный слой позволяет «принять ничью» без входящего предложения

**Источник:** [service_games.py:247–275](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L247-L275) и [service_games.py:933–938](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L933-L938). `draw(...,accepting_offer=True)` без precondition на текущий `opponent_draw_flag=True` делает тот же HTTP yes, что и новое предложение; direct service/re-entrant callback может нечаянно **предложить** ничью вместо принятия. Аналогично decline должен требовать существующего входящего предложения. UI-кнопка не является достаточным контрактом сервиса.

**Требование:** перед резервированием команды и перед transport проверить подтверждённое входящее предложение для `accept_draw/decline_draw` по color+gameId+lease. При missing/false/replayed/stale flag — no POST; настоящий incoming offer → по одной команде; после ответа противоположный ответ и дубль запрещены. Протестировать service direct и Qt.

### N5 [P2] После успешного decline draw и нового same-line false предложение остаётся в UI/pending до следующего хода

**Источник:** [session.py:402–424](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/session.py#L402-L424), [service_games.py:353–467](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L353-L467). Защита F1 правильно не верит **старому** false при той же линии, но не умеет отличать законное новое false после успешного server-confirmed ответа на ничью, если очередной ход ещё не сделан. На UI `draw_offered_by_opponent` остаётся true, pending decline не согласуется (`before=True; after=True`) и кнопки ответов заблокированы. Новый fake двух клиентов проверяет действие Bob decline, но затем **сразу продвигает линию**; сценарий подтверждённого отказа без хода не проверяется.

**Требование:** сохранить защиту от старого same-line replay, но добавить **проверяемое causal acknowledgement** законного decline/withdrawal (server ACK + subsequent authoritative event identity или другой достоверный протокольный признак), не использовать один только clock tick/количество дублированных `gameState` как новизну. Не разрешать второй draw POST/противоположный ответ без подтверждения. Если API не позволяет доказать упорядоченность: честно явно показывать «ожидаем синхронизацию» и выполнять bounded resync, **а не оставлять ложное предложение навсегда**. Fake-клиенты должны покрыть Bob declines → wdraw=False, moves unchanged → no duplicate → UI clears when justified; true→old false replay remains latched; late HTTP ack; missing flags; 429/timeout.

### N6 [P2] Доказуемо неотправленный ход трактуется как ambiguous и блокирует ввод

**Источник:** [service_games.py:906–913](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/lichess/service_games.py#L906-L913) при `operation_token(context) is None` emits `session_move_failed(...status=0)`, **хотя POST точно не был вызван**. [window.py:7323–7339](https://github.com/feshakomissarov-prog/chess/blob/29245300be493a195759c114925040b7df607a0a/src/chess_coach/window.py#L7323-L7339) считает status 0 ambiguous и оставляет `pending_uci`/premove lock (см. `session.fail_submission`). При credential loss без смены generation/без нового stream событие может застрять до внешнего resync.

**Требование:** различать `NOT_SENT` (точно не начали HTTP: token absent/admission denied/queued cancel) и `MAYBE_SENT` (timeout/transport exception после начала отправки). Первый снимает локальный pending и безопасно сообщает отказ, второй запрещает автоматический повтор до подтверждения. Не использовать статус 429 как причину самостоятельно повторять irreversible POST. Unit/Qt tests с пустым credential store в момент запуска worker, без снапшотного события; проверить возврат интерактивности после валидного state. Увязать с N2, не плодить два независимых решения одной state machine.

### Обязательные общие критерии закрытия повторного ревью (ОДИН ЦИКЛ)

1. **N1–N6**: на каждый дефект — воспроизводящий regression test (предпочтительно red-on-parent `2924530`), фикc, зелёный targeted test. Никаких автоматических повторов необратимых команд после неясного исхода HTTP; no illegal assistance during active human game.
2. **Сохранить F1–F5:** существующие 1074 tests не уменьшать; новая матрица включает обе стороны ничьей, claim victory, takeback, terminal statuses, account switching, 401/403/429/timeout, stale and duplicate states. Обязательно перепроверить вызовы **самих ходов** и премувов, а не только game actions.
3. **P6–P9:** не пропустить согласованное владельцем дополнение от 10.11 в разделе 4: всегда Home на cold-start, видеть историю и её сохранённую аналитику без ненужной Lichess проверки, постоянный кэш Home по ревизии между перезапусками, исправление визуальных обрезаний/эталонов. Это **уже утверждённый scope**, не просить нового отдельного задания.
4. **Сквозной full-app smoke** по §2: Home/Play/AI/friend/direct challenge/free board, Game/Focus/result, Library/PGN/FEN/Position editor, Review/training/graph, Statistics/Progress, Settings/background, Stockfish in permitted states, SQLite integrity/privacy, startup/shutdown and restore, offline/multi-account.
5. **CI**: полный pytest, compileall, diff-check, exact-final-HEAD Windows test+visual-evidence; сравнение UX-01–UX-13 по реальным кадрам явно PASS/NOT_VERIFIED (CI capture alone is insufficient). Реальные два аккаунта Lichess/ручной reconnect по указанию владельца остаются **DEFERRED/NOT_VERIFIED**.
6. Один итоговый `docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md`: отличать подтверждённые code fixes, текущие продуктовые изменения и manual gates; кодовый SHA, report SHA, CI URLs, тесты, измерения Home до/после, что не проверено. Один рабочий PR #46 Draft, no merge, no new PR, no force push; не менять персональную базу/PGN/tokens.
7. **Не отдавать промежуточные частичные ответы** «исправил 2 из 6»; исполнитель должен закончить весь пакет и дать единый самопроверенный результат. Если в целевой сквозной матрице обнаружились другие source-доказанные проблемы — закрыть там же, либо явно аргументировать почему безопасно отложены; не маскировать их зелёным CI.

**Итог:** текущий код `2924530` всё ещё **не проходит независимую приёмку**, несмотря на успешный CI. После N1–N6 и P6–P9 требуется ещё одно **единое** полное source/GUI review перед ручной приёмкой владельцем.
