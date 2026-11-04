# ARCH-UX-01 — REVIEW-FIX-05: физический Windows-пилот, UX-шапка, Главная, Lichess-соединение и сквозной аудит шести экранов

**Дата решения:** 05.11.2026. **Приоритет: обязательное единое корректирующее ТЗ desktop Codex.**  
**Основание:** два новых Windows-скриншота «Главная»/«Играть» и третий скриншот повторного подключения, комментарии владельца с пометками 1–8; независимая сверка source code + утверждённого UX. Скриншоты и персональные данные **не загружать в публичный GitHub**, в отчёте только обезличенные технические свидетельства.

**Исходный кодовый baseline:** [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), ветка `codex/arch-01-modular-refactor`, известный HEAD `f957f994275933e0b6eb19134fa8cb34c39286e3`, [Windows CI #253](https://github.com/feshakomissarov-prog/chess/actions/runs/41922206819) **675 PASS / SUCCESS** на этом SHA. Предыдущее кодовое ревью приняло RF4-01/02, но **физический UX-пилот владельца НЕ принят**. Codex сначала сверяет текущий remote/local HEAD и незакоммиченные изменения; уже исправленное не переписывать.

**Нормативный приоритет:** P0 Fair Play/личные данные/корректная игра и подключение; затем **последнее решение владельца и этот Windows-пилот**; затем [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md), [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md), [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [аудит 24 функций](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md); [единое ARCH-UX-01](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), [AGENTS.md](../../AGENTS.md). Старые W0–W9/A0–A12 и REVIEW-FIX-01…04 — **история реализованных этапов, не запускать заново**. Можно делать большой единый цикл RF5-00…RF5-12 в **том же** кодовом draft PR #44.

## 0. Диагноз и что не следует путать

| Пользовательский признак | Подтверждение / кодовая гипотеза | Обязательный результат |
| --- | --- | --- |
| Скрин 1, №1: «← Назад» прямо на Главной | `window.py::_shell_screen_changed` показывает `back_button` по `WorkspaceNavigator.can_go_back` даже при `WorkspaceRoute.HOME` | На корневой Главной «Назад» отсутствует; в деталях/вложенных пространствах история работает |
| №2: status «Lichess · проверено N/M…» сжался в несколько микрострок и перекрывается соседями | Header `QHBoxLayout` содержит отдельную `lichess_analysis_status QLabel` с `setMaximumWidth(210)`, `WordWrap(True)`, постоянные кнопки подключения, отключения, настроек и «Фоновые операции» | Краткая читаемая шапка/статус у профиля; все подробные N/M — в Фоновых операциях, без overlap и toolbar clutter |
| №3: выпадающий «Контроль» на Главной | `ui/pages/home.py::HomePage` прямо добавляет `dashboard_clock`; в утверждённом B (Главная) фильтра контроля нет; он относится к Архиву/Прогрессу | Удалить селектор из Главной, сохранить нужные фильтры на профильных страницах |
| №4–6: три карточки «скрыто» при подключённом аккаунте | `_lichess_authenticated` сбрасывает safety display state в False, **но не вызывает** `_request_dashboard_display_safety`. Последний вызывается лишь по ручному действию в Statistics. Авторизация сама по себе **не доказывает** безопасное состояние | После подлинного stream-ready автоматически и безопасно запросить один свежий полный `/api/account/playing` snapshot; отображать analytics **только** после подтверждённого allow. На human live/unknown/429/403 — честно и компактно закрыть |
| №7 «Следующее занятие» показывает длинную помесячную историю рейтинга задач | `window.py::_render_dashboard_snapshot` записывает `activity_trend` прямо в `HomePage.activity_status` внутри «Следующего занятия» | Перенести тренд в «Мой прогресс», занятие — одно доступное CTA или честное короткое пустое состояние |
| №8 «Последние партии»: много повторяющихся строк «аналитика скрыта»; нет видимого «Все партии» | `_render_dashboard_snapshot` ставит по два повторных safety сообщения **на каждую** карточку; `home_games_button` существует как **скрытый legacy hook**. Клик по карточке вызывает `_open_saved_game`, минуя детали | Ровно три компактные карточки с фактами о партии и одной понятной общей отметкой safety; видимое «Все партии»; клик → детали, не автоматический review |
| Скрин 2: раздел «Играть» пользователь одобрил; но действие создание AI снова пишет «подключитесь», когда шапка показывает «подключено» | `_lichess_authenticated` выставляет `_lichess_connected=True` раньше подтверждения event stream. `_online_actions_allowed` также зависит от 429 rate cooldown; текст ошибки различия не объясняет | Не редизайнить согласованную страницу «Играть»; исправить **общую модель готовности подключения**, причины disabled/pending и сообщения |
| Скрин 3: отключение/повторное подключение по сохранённому PAT не восстановило игровую готовность | Возможные причины: stream readiness, 429/backoff, 401/403/token scope, stale generation, pending retry/connection-stop, safety preflight. **Скриншот сам не доказывает конкретную сетевую ошибку** | Диагностировать реально воспроизводимый переход состояний и устранить причину; не переписывать токены и не «лечить» бесконечными retries |

**Важно:** пользователь **прав**, что интерфейс не должен выглядеть сломанным после подключения. Но «я залогинился» ≠ разрешение показывать engine-derived данные: автоматическую fail-closed проверку нельзя заменить `analysis_allowed=True`. Успешный `get_account` ≠ готовый event stream ≠ подтверждённая игровая активность ≠ разрешение аналитики. Разделы должны честно объяснять эти независимые состояния.

## RF5-00 — Предстартовая карта архитектуры и воспроизведение

1. Проверить Git status/head, PR #44/#45, AGENTS и нормативные UX. Сохранить checkpoint владельца, `.venv312/`, личные файлы. В `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md` создать секцию REVIEW-FIX-05 с точным baseline и **матрицей экран → expected controls → actual controls → route/action → test**.
2. **Запустить приложение на Windows реально, а не только `QT_QPA_PLATFORM=offscreen`** из текущей кодовой ветки; проверить все шесть страниц и вложенные страницы. Визуальные проблемы документировать **обезличенными** снимками либо geometry/видимостью и подписью результата; не коммитить владельческие скриншоты/реальные партии/токен.
3. Сначала воспроизвести офлайн с synthetic fake service/account/game, затем при безопасной возможности **read-only подключение к текущему аккаунту владельца через штатное приложение** (см. RF5-03). Это запрос владельца на проверку соединения, **не разрешение создавать рейтинговые партии или отправлять ходы/чат/челленджи, менять аккаунт, экспортировать/публиковать личные данные**.
4. Разделять проверенное по исходнику, воспроизведённое synthetic, воспроизведённое на Windows и недоступное для проверки без секретов. Никогда не выдавать CLI/offscreen PASS за физическую визуальную приёмку.

## RF5-01 — P1: правильная глобальная навигация, «Назад» и отсутствие дублей

**Файлы:** `src/chess_coach/window.py`, `application/routes.py`, `ui/pages/*`.

- На корне **Главной** кнопка «← Назад» **никогда** не видна и не занимает место; корневое действие «Главная» в sidebar не создает бессмысленный Back. Поправить root/route history invariant, не только `hide()` один раз: показать Home после Play/Games/Progress, восстановление из QSettings, после закрытия внутреннего review, повторный клик Home, `navigate_back()` и history с контекстом.
- Во вложенных **деталях** «Назад» сохраняет выбранную партию, фильтр, scroll, review ply; «Вернуться к партии» при live game остается глобальным и не сбрасывает stream, часы или premove. Кнопка в шапке — только когда действие реально определено, **не второй универсальный Back на корне**.
- **Настройки** — один системный вход внизу sidebar, не постоянный дубликат в шапке (как согласовано UX-B1). Кнопка «Подключить Lichess» не должна выглядеть рабочей рядом с уже подключённым аккаунтом; доступные Connect/Disconnect действия зависят от аутентификации/готовности.
- Убрать недоступные/пустые legacy-дубли из визуальной и клавиатурной навигации, но не вырезать рабочие compatibility façades или методы без тестов. Не восстанавливать старую «Тренировку предходов», пилотные импорты и лишние OS dialogs.
- Тесты: 20+ переходов sidebar/Home/Play/Library/detail/Review/Training/Progress/Settings/Back и live→away→return с устойчивым route state, нулевые неожиданные HTTP/engine jobs от навигации.

## RF5-02 — P1/P2: читаемая общая шапка и аккаунт в sidebar

**Причина:** `MainWindow::_build_ui`, `_apply_header_responsive`; отданные `connection`, `lichess_analysis_status`, `background_operations_button`, `connect_button`, `disconnect_button`, `settings_button` не помещаются рядом друг с другом. Responsive переключается лишь на ширине окна `<820`, хотя реальная **ширина правой content-колонки** меньше на sidebar + margins + DPI.

- Перестроить **один** компактный header и **согласованный sidebar**: название/активный раздел, одна короткая читаемая связь Lichess (offline / нужна авторизация / подключаемся / поток восстанавливается / готов / 429 / auth ошибка), доступ к **одному** инспектору фоновых операций и контекстное «Вернуться к партии». Настройки оставить внизу sidebar. Подключение/отключение размещать как логичное contextual action у статуса/аккаунта, не пять одновременных больших кнопок.
- `50/3303, 9 точность, 953 оценки` и иные многострочные счётчики **не место в глобальном заголовке**: краткий индикатор активности/ошибки и tooltip; полные значения с различением `checked/accuracy/evals/pending/retry/backoff` — в существующем `BackgroundOperationsPage`. Важно сохранить доступ к проблемам/очередям, а не просто спрятать функцию.
- Использовать реальное доступное пространство `header_widget.contentsRect()`, layout size hints, eliding/tooltips, адаптивное скрытие *вторичных* подписей или компактный профильный popover. Не полагаться только на 1920px: тесты 1280×720/1366×768/1600×900/1920×1080, 100/125/150% и expanded/collapsed sidebar, font scale/long account names. Все интерактивные кнопки остаются кликабельны; никаких overlap/clipping/скрытого текста на кнопках.
- Сбои сети и safety должны быть понятны без вывода raw exception, HTTP body, PAT, cookies, пользователя в логах. Хранить техническое состояние в app controller/read model, а не вычислять безопасность по тексту QLabel.

## RF5-03 — P0: восстановить и проверить ПОЛНУЮ цепочку Lichess OAuth/PAT/stream/preflight

**Файлы:** `src/chess_coach/lichess/{service_auth,service_games,service,api}.py`, `window.py`, `application/fair_play.py`, `application/app_state.py`, существующие Lichess clients/tests.

**Подтверждено:**
- `LichessAuthMixin.restore` и `connect_personal_token` проверяют account, затем `session_authenticated`, после чего только запускают `_start_event_stream`.
- `MainWindow::_lichess_authenticated` немедленно делает `_lichess_connected=True`, хотя stream может быть ещё не готов/в 429/backoff; текст «Подключено» получается преждевременным.
- `_online_actions_allowed` дополнительно требует отсутствия rate limit; Play просто пишет «Подключитесь…» даже если account уже подтверждён. Переподключение и safety могут иметь разные поколения.
- В `LichessAuthMixin` существует `token_store` compatibility property, возвращающий `selected_token_store` OAuth/PAT: **НЕ объявлять ошибкой отсутствие этого атрибута**; проверить настоящую жизнь выбранных credential slots и их generation.
- `LichessClient.get_playing_games` требует `opponent.username`, и это **соответствует проверенной текущей официальной схеме**, включая AI. Официальный [AI fixture](https://github.com/lichess-org/api/blob/master/doc/specs/examples/account-playing-ai.json.yaml) содержит `source='ai', opponent={ai:1,id:null,username:'Stockfish level 1'}`; [схема GET /api/account/playing](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-account-playing.yaml) требует `username` также для AI. **Не объявлять это текущим багом и не ослаблять валидатор.** Тестировать официальные AI/human/anonymous fixtures и неизвестные/неполные состояния fail-closed; не разрешать BOT как official AI.

**Обязательная модель:**
1. Раздельные, проверяемые состояния `credential selected → account authenticated → event stream ready / reconnecting / cooldown → current active-games snapshot verified → display safety allowed|denied|unknown`. Одно authoritative account+auth_generation+connection_generation с cancellation, no stale callback. UI может показывать «Аккаунт подтверждён; поток подключается», не «готов к игре».
2. **Play/Create/Resume** gated по реальной доступности нужного endpoint/stream + shared 429 clock, с конкретной короткой причиной и доступным retry после allowed due time. Никаких повторных POST после network timeout, double-click/duplicate challenge или auto-created rating game.
3. PAT и OAuth **раздельные slots**, selected auth method per profile; при reconnect через существующий PAT не открывать ненужный OAuth и не стирать/переиздавать токен. Переходы `connect → disconnect → reconnect → switch credential → reconnect`, закрытие приложения, восстановление после 401/403, RateLimitError(429)+Retry-After и stream cancellation проверить.
4. Устанавливать `_lichess_connected` и `ready` только при **доказуемой** стадии; account status, stream readiness, game action readiness, safety state, background sync readiness — отдельные derived flags в одном coordinator, не два флага со случайными setter в каждом page. Не пересобирать архитектуру и не ломать совместимость façades.
5. При `429` Lichess советует **минимум одну минуту** ожидания и отсутствие запросного спама. Сохранить более длинный `Retry-After`, bounded backoff, коалесценцию; не делать `/api/account/playing` каждую секунду. Не перегружать live move lane фоновыми архивами/backfill.
6. Фактическая проверка Windows desktop через **существующий безопасный сохранённый credential**: `GET /api/account` (проверка идентичности), readiness event stream `/api/stream/event`, один `GET /api/account/playing` при разрешённом состоянии; статус, скрытие/открытие аналитики, disconnect/reconnect. **Не логировать token, HTTP Authorization, account names, game IDs, PGN**. Не создавать/отправлять игры, не отзывать/удалять токены, не отключать защиту и не массово скачивать архив. Если для live проверки нет доступа/прав/времени — отчёт `BLOCKED (live-only)`, fake-transport набор обязателен; **не заявлять реальный PAT PASS без факта**.
7. Проверить совместимость response schema `/api/account/playing` и opponent AI/human/missing fields на официальных **текущих** Lichess fixtures/спеке. В случае неизвестной записи — fail-closed и понятный код причины; **никаких fake allow по имени `AI` или BOT title**. Тесты official AI, human, BOT account, unknown/malformed, truncated 50/50, empty confirmed list, 401/403, 429, late account replies, reconnect during active human, account switch.

## RF5-04 — P0: автоматический, но строго fail-closed доступ к сохранённой аналитике

**Файлы:** `window.py::{_lichess_authenticated,_lichess_event_stream_ready,_request_dashboard_display_safety,_dashboard_display_safety_reply,_dashboard_display_safety_error,_analysis_safety_error,_render_dashboard_snapshot}`; `application/fair_play.py`.

- После успешных **account auth И stream-ready** и отсутствия очевидной активной человеческой партии: запустить **ОДИН** асинхронный/coalesced shared full active-game snapshot с текущим PAT/OAuth, без отдельного запроса на каждый card/page. Пока проверка pending, оставлять CP/mate/accuracy/PV/ошибки/график **скрытыми**; при доказанном отсутствии human live показать автоматически, без необходимости искать «Проверить безопасность» в Прогрессе.
- **Вход в аккаунт сам по себе НЕ открывает оценки.** Denied/unknown/partial/429/401/403/reconnect/timeout/human game должны оставлять всё скрытым, с различимой причиной и корректным ненавязчивым CTA «Повторить проверку» только при разрешённом Retry-After. Не создавать бесконечный polling для раскрытия; при новом stream-ready после recovery — новая single-flight проверка.
- Согласовать состояние safety для Home, Library mini-graph, full Review/AdvantageBar/CP-Δ, Progress, tooltip/accessibility, positions/live games. В инвалидации account/session/gameStart сначала **синхронное revoke/mask**, потом фоновые процедуры. Существующий engine admission/preflight независимо проверяет безопасность перед запуском/после ожидания; **никакой статус dashboard не является единственным допуском Stockfish**.
- Исправить диагностику `_analysis_safety_error`: сейчас вызывает `_dashboard_display_safety_error(request_id, type(message).__name__)`, и для строкового сообщения показывает только `str` вместо причины. Передавать **нормализованный безопасный error kind/status** (`network`, `401`, `403`, `429`, `truncated`, `human-live`, `missing-scope`, `timeout`) в UI и diagnostics allowlist; не показывать raw exception/секреты. Не удалять пользовательские credentials по одной ошибке GET.
- Отдельные safety checks для Review/engine могут использовать **один центральный сервис проверки/lease с известной свежестью и context**, но при engine POST/dispatch нужна повторная допустимая проверка по действующему Fair Play contract; не превращать ручной preflight в «один раз проверил и теперь всегда можно».
- Тесты: воспроизвести скрин — подключённый аккаунт, архив сохранён, safety pending → автоматически confirmed safe → карточки открываются; точно такая же последовательность для human live, unknown opponent, Lichess AI, 429, 401/403, stream reconnect, changed PAT, stale Qt reply, concurrent pages, late gameStart while UI visible. Проверять **видимый текст/graph/tooltip** до и после, не только bool. Для revoked source скрывается и официальный Lichess accuracy, не просто Stockfish.

## RF5-05 — P1: Главная = согласованные четыре зоны, не рабочий экран статистических фильтров

**Файлы:** `ui/pages/home.py`, `application/dashboard.py`, `window.py`.

1. **Убрать на Главной `dashboard_clock` / «Контроль» полностью**, включая место в layout. Не переносить «контроль» в шапку. Утверждённый UX-B1 B не включает такой фильтр; у владельца явное решение удалить. Сохранить историческую QSettings `workspace/dashboard_clock` как неактивный старый ключ, если нужен rollback, **но перестать применять его к Home**; в «Мои партии» и «Прогресс» фильтр контроля работает как прежде.
2. Не плодить отдельный account-filter «Аккаунт» на Home: отображать коротко текущий profile/account scope рядом со статусом/в профиле; при нескольких известных аккаунтах выбирать текущий **в одном общем account coordinator/Settings/Progress**, без автоматического смешения. Account selection, установленный в QSettings при `_lichess_authenticated`, должен реально обновлять `DashboardQueryCoordinator`; существующий `HomePage._preferred_account` сейчас задаётся **до** авторизации и может остаться от предыдущего аккаунта. При смене сессии немедленно invalidate старый read model, scope и async generation; не показывать счётчики другого аккаунта.
3. **2–3 компактных проверенных KPI**, как в UX-B1, вместо текущих 4 равноценных карточек и длинных подписей; согласованный смысл на начальном этапе: (i) завершённые партии, (ii) партии с реально scored анализом N / total и %, (iii) официальная Lichess accuracy только при наличии сервера/coverage. «Ошибки на 100 оценённых ходов» перенести во **Весь прогресс** / подробности, не удалять вычисления. Пользователь не получает ложную accuracy из локального sparse анализа. Выдержать длинные числа, N/N и строки на узких DPI, правильно обрезать secondary.
4. Мини-тренд на базе **реальных** данных рядом с KPI «Мой прогресс» (или доступный нейтральный `Нет данных`). Не использовать рейт задач как «рекомендацию занятия». Для engine-derived тренда действует safety mask; для **чисто фактологического** рейтинга попыток задач — корректный источник, без утечки двигательных метрик.
5. Зона «Следующее занятие»: короткое обоснование + **один реально работающий** CTA в «Обучение»/history replay; если нет доступной сохранённой задачи, честно «Пока нет сохранённых задач» и переход «Обучение» либо импорт истории только по явному действию; никаких выдуманных тем или GPT-рекомендаций.
6. «Последние партии»: **ровно три** компактные карточки по реальной хронологии игры, не по `updated_at` анализа; без двойной строки «аналитика скрыта» на каждой. Фактология (результат, соперник, дата, контроль, цвет, достоверное ECO) остаётся видимой при запрете анализа. Либо **одна** ясная надпись на уровне блока «Оценки скрыты до проверки», либо одно компактное сообщение только в нужной зоне — без визуального мусора. При доступном safety и сохранённых данных показать настоящий source/coverage.
7. Клик по карточке ведёт **внутрь подробностей партии в «Мои партии»** (UX-B1 B→D), **не** сразу в `_open_saved_game` / общий Review. Вторичное «Разобрать» — отдельно, по safety. Добавить реально **видимое** «Все партии» и «Весь прогресс»; текущий `home_games_button` живёт в скрытом legacy host, это не UX CTA.
8. Нет глобального scroll; layout устойчив на трёх screen resolutions и 125% DPI; 2–3 KPI + тренд + занятие + 3 партии и ссылки видимы без обрезания. Счётчики при no data и different profiles честны, никакого дорогостоящего PGN parse в GUI.
9. Тесты snapshot + Qt visual geometry/activation/URL-less route: no Back, no Control, 2–3 KPI, trend in progress not exercise, CTA works, three last games, «Все партии», safe/unknown/denied states; account A→B without stale Home; дата и пагинация как в RF2/RF5.

## RF5-06 — P1: «Играть» сохраняем без редизайна, но исправляем честную готовность действий

Владелец прямо подтвердил, что **сама PlayPage устраивает**. Не менять без необходимости расположение режима/цвета/времени/AI level, CTA и общий dark style. Исправлять только:
- Header/connection readiness из RF5-02/03: `connected` vs `stream not ready` vs `429`; при невозможности начать игру показать точную короткую причину, не бессмысленное «Подключитесь…», когда account authenticated.
- Для уже ожидающего `challenge` не посылать второй POST; Play form не создаёт AI challenge при открытии/перерисовке, не сбрасывает существующую игру/премувы.
- `Без часов` и custom должны быть доступны **только** там, где документированный Lichess endpoint их допускает. Если пользователь выбирает недопустимое, показать причину до POST, не маскировать ошибку.
- Тесты fake rate limit/account ready/stream recovery/PAT and OAuth/no secrets; screenshot comparison Play form unchanged кроме header/status.

## RF5-07 — P1: «Мои партии» — история, фильтры, источник и status фоновых оценок

Из прежних RF-01…04 работа по датам, Каро-Канн, official accuracy, local Stockfish и Lichess JSON уже есть; **не переписывать**. Проверить на текущем коде:
- Даты `lastMoveAt/startedAt/date_only`, хронологическая сортировка и сортируемая подпись совпадают, все завершённые игры доступны по страницам, активные закреплены.
- `Каро`/Caro/B12 фильтруется по нормализованным aliases, нет UI-thread full PGN scan.
- `Источник оценки=Lichess` включает **оба** `lichess-pgn` + `lichess-json` с текущими usable CP/mate; `official accuracy only` и `checked no data` отдельно; `любой анализ` = distinct union scored. Ошибки/охват/источники не зависят от вызова страницы.
- Read-only статус `queued/checked/evals/accuracy/no data/rate limited/retry due` доступен без открывания Progress, не перегружает шапку. Не запускать 3300 live API запросов для «починки цифр».
- Переключение A↔B сохраняет фильтры, не показывает чужую аналитику и корректно отзывает safety.
- Фактический Qt smoke/снимки: list+filters+details+mini graph при safety-allow и masked до него. Synthetic 3k, без новых обязательных стресс-бенчмарков/SLA.

## RF5-08 — P1: «Анализ»/«Обучение»/«Прогресс» соответствуют согласованным границам

**Анализ:** в landing остаются **три** главных направления «Разбор партии/PGN», «Массовый анализ», «Исследование дебютов» (будущий полнофункциональный explorer — не делать). Код сейчас на третьей карточке пишет «Дебюты и фазы» и ведёт в Progress; привести название/подзаголовок к UX-A2/B1 и честно отметить **доступно только имеющееся исследование статистики, дерево/полноценное исследование ещё не реализовано**. Не показывать ложную рабочую кнопку explorer. Временные PGN/FEN editor — допустимы, no auto archive save/engine, safety. CP/Δ graph, AdvantageBar и scores не возвращать на live human.
**Массовый анализ:** проверить компактную страницу, primary pause/resume/retry/cancel, ненавязчивые advanced настройки, отказ от полного пути exe в основном поле, engine readiness, no auto UCI и не блокировать ходы. Не добавлять второй scheduler.
**Обучение:** переход верхнего пункта ведёт к действительно существующей истории/повтору задач, не фейковым персональным занятиям; только имеющиеся функции и честные empty states. No premove training/pilot import.
**Прогресс:** вкладки, source-filtered summary/openings/historical (RF4), official/local accuracy + missing/mate provenance, 24 coverage функции, отмена stale worker after page deletion. Без account data stale и Qt warnings; safety mask/allow share source.
**Настройки:** единственный постоянный пункт sidebar внизу. Кнопка/экран работает, QSettings/звуки/default WAV/previews/громкость/Stockfish setup/token slots не ломать.

Для **всех** маршрутов составить компактную матрицу: control `shown/hidden/contextual`, enabled/disabled и реальное действие; не оставлять активные пустые клики, legacy modal, «Назад» на корне, fake loading, несуществующие данные. Единый MainWindow runtime, прежние Qt adapters, одна SQLite, один engine manager, independent network lanes.

## RF5-09 — P0: игра, финиш, предходы, безопасность — регрессии при переработке оболочки

Не допустить collateral:
- Live game frame: все 64 клетки видимы/drag-работает на 1280×720/1366×768, 125%/150%, часы и видимые справа takeback/draw/accept/reject/resign, premove+cancel, доступная история. При уходе из игры и возврате session сохраняется без нового stream/отправки хода.
- Finish остаётся **в том же board**: часы остановлены, authoritative moves/winner, SQLite finalization lifecycle не теряется, справа только «Разобрать»/«Вызвать снова», без ResultPage/engine auto-run/auto-rematch.
- После `gameStart human`, любого unknown snapshot или 429 — синхронный revoke всех display/engine leases; даже сохранённая offline позиционная оценка не показывается игроку в активной human онлайн-игре. Никаких chess hints/material eval/suggested moves. Простое отображение **взятого материала** не является обязательной функцией этой задачи.
- Sound assets, QSettings, icons/clickable board, recorded puzzles, PGN and SQLite data сохранены.
- Regression automation fake multiplayer + two sessions + delayed final-sync DB write + account reconnect + no human engine.

## RF5-10 — Тестовая матрица, которую нельзя закрыть одним зелёным CI

**A. Unit/integration (fake HTTP, fake service, temp SQLite):** account authenticated but no stream; stream ready with empty complete `nowPlaying`; official AI opponent; active human; malformed opponent; 50/50 truncated; 429/Retry-After; missing permission; 401/403; PAT→disconnect→PAT reconnect, OAuth→disconnect→OAuth; switch auth methods; delayed stale account/stream/safety replies, one safety GET for 6 open pages; error status explains cause, no raw secret; offline/manual no network/credentials before explicit action.

**B. Qt geometry/offscreen screenshots:** Main Home root without Back or Control, no duplicate Settings, sidebar status and short header, real button rectangles don't intersect/overflow at 1280×720, 1366×768, 1600×900, 1920×1080, 100/125/150% (simulate where OS DPI cannot be directly tested); long account names/unicode and variable data; three latest cards, no redundant safety text, live game 64 squares clickable, move table/controls accessible.

**C. Real Windows interactive test on desktop Codex machine:** запустить GUI в обычном Qt режиме из PR #44, открыть **каждый** верхний пункт, Settings и вложенные Details/Review/PGN/FEN/Batch/Background/Training/Progress; проверить собственными глазами и скриншотом/элементными bounds, повторно после переключения sidebar, window size, DPI. Записать таблицу "PASS/FAIL/BLOCKED", со ссылкой на **тестовые** evidence/без пользовательских данных. Нельзя отчитываться "страницы проверены", если запуск был только `QT_QPA_PLATFORM=offscreen` и текстовый pytest.

**D. Read-only real Lichess check, если имеющийся credential доступен и владелец разрешил:** штатно подключить выбранный PAT/OAuth, удостовериться в `GET /api/account`, stream-ready и `GET /api/account/playing`, переходе safety `pending → verified allowed` **только при реально безопасном ответе**; затем disconnect и reconnect. При live human играх **не выполнять ни engine analysis, ни fake safety allow**, не создавать партии, не получать информацию о текущей партии, способную помочь; допустима только диагностическая проверка безопасности/status. При отсутствии разрешения или токена — `BLOCKED`, а не PASS. Любой лог stdout/CI/issue — без секретов/PGN/id.

**E. Regression/coverage:** прежние 675 tests остаются зелёными, добавить новые автоматические тесты по RF5-01…RF5-09; охват всех 24 feature ID сверить с baseline audit, плюс CLI `--offline`/`--manual-connect`, real no-token boot, UI no crashes, no `PytestUnhandledThreadExceptionWarning`.

**F. UI review gate:** сравнить скриншоты версии после исправления с текущими тремя скринами владельца: корневой Home/Back, status clipping, удалённый Control, 2–3 KPI, занятие/тренд, краткие Recent, header + Play. Недостаточно наличия элементов по QWidget API — реально читать текст и смотреть перекрытия. Указать «Play page approved, no redesign» если не менялась.

## RF5-11 — Неприкосновенные границы

- **Нельзя** отключать fail-closed Fair Play для решения жалобы «скрыто»: нельзя `allow=True` только по authenticated, нельзя отключать проверку, подменять `nowPlaying=[]` после сетевой ошибки, скрывать блокирующие ошибки за UI-only флагом.
- **Нельзя** удалять/перегенерировать токены, запускать другой OAuth, публиковать username/PGN/personal paths, выполнять destructive SQLite migration, делать автоматические запросы с реальными владельческими credentials в CI. Авторизованные live диагностические GET только на десктопе владельца.
- **Нельзя** менять серверные лимиты, обходить Retry-After, делать background агрессивным ради UX. Рекомендации только с реальной подтверждённой моделью источников.
- **Нельзя** вводить микросервисы, вторые QMainWindow/SQLite/Stockfish/background coordinator, новую дизайн-палитру или бездумное переписывание монолита. Приоритет точечных app/UI fixes.
- **Нельзя** изменять одобренную владельцем Play form без проверенной функциональной необходимости.
- Не добавлять неподтверждённые автоматические уроки/LLM/голос/новые задачи/пилотный импорт/псевдо-opening explorer.

## RF5-12 — Результат Codex и условия приёмки

1. **Один большой цикл RF5-00…RF5-12** в существующем code draft PR #44 (branch `codex/arch-01-modular-refactor`). При очередной проверке HEAD может отличаться от указанного — всё сравнить до изменения. Структурированные commits, в конце diff summary, никаких перезаписанных чужих изменений.
2. Обновить `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md` и `docs/PROJECT_STATUS.md` **в кодовом PR #44**, таблица каждого RF5 с files/tests/evidence. Указать exact SHA, Python+PySide6 versions, Windows full CI job URL/status. Не повторять огромный исторический handoff и не делать десятки репортажей в чате — вся детализация на GitHub.
3. Полный `pytest` с Qt offscreen / thread warnings as errors на целевом наборе, compileall, `git diff --check`, native Windows app/manual screenshot matrix, CLI offline/manual isolation и **новый hosted Windows CI на точном конечном SHA**; старый #253 (675 PASS) — только исходный baseline. Проверять реальный `SUCCESS`, не `in_progress`.
4. Явные gates:
   - **P0 safety:** unsafe и непроверенный live/account никогда не показывают движковые оценки и не запускают UCI.
   - **P0 account:** PAT/OAuth status/stream/session не противоречат друг другу; диагностический read-only сценарий объясняет отказ и recovery.
   - **P1 UX:** у Home нет Back и Control; toolbar не перекрыт, status компактный; My Progress 2–3 KPI+trend, Next exercise meaningful, Recent 3 compact+Details+All Games, Play layout сохранён.
   - **P1 cross-section:** все шесть разделов и вложенные страницы реально открывались на desktop; screenshot/geometry evidence и неусечённый текст.
   - **Regression:** 24 группы функций, дата/фильтр/аналитика/звук/premove/finalization/Stockfish/fair-play из предыдущих этапов не деградировали.
5. PR **остается draft, main не менять, никакого merge** до независимого ревью и **повторной физической приёмки владельца**. Если live Lichess не удалось протестировать на машине Codex — это не повод выставлять «PASS»: отдельно указать BLOCKED и точную локальную инструкцию владельцу, без запроса переслать токен.
