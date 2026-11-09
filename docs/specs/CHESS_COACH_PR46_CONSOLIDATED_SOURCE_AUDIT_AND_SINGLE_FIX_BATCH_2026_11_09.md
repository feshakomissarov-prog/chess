# Chess Coach · PR #46 — ЕДИНЫЙ ПАКЕТ АУДИТА И ИСПРАВЛЕНИЙ БЕЗ ПООЧЕРЁДНЫХ МИКРО-ТЗ

**Дата аудита:** 09.11.2026.  
**Статус:** **SINGLE CONSOLIDATED AUDIT / REQUEST CHANGES / НЕ ПОДТВЕРЖДЕНА РУЧНАЯ ПРИЁМКА.** Этот документ заменяет мелкое отдельное ТЗ «четвёртого ревью». **Не отдавать Codex как «пару новых правок»**: при передаче требуется целиком выполнить всю регрессионную матрицу и сквозную проверку ниже.  
**Репозиторий:** https://github.com/feshakomissarov-prog/chess  
**Проверенная ветка:** \`codex/unified-desktop-ux-2026-11-08\`; Draft [PR #46](https://github.com/feshakomissarov-prog/chess/pull/46).  
**Проверенный итоговый SHA:** \`6fa202f9f2c9ee4be2785684912c4ddf5642a23f\`; кодовый коммит \`eaae8914621ec0159f80797915b2a381e377a09f\`. Перед выполнением проверить fresh HEAD; если поменялся — пересверить каждый пункт.  
**Базовое UX-ТЗ:** [CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md).  
**История:** [первый разбор PR #46](CHESS_COACH_PR46_SOURCE_REVIEW_FIXES_2026_11_08.md), [второе ревью](CHESS_COACH_PR46_SECOND_REVIEW_FIXES_2026_11_08.md), [третье ревью](CHESS_COACH_PR46_THIRD_REVIEW_FRESH_CARDS_AND_LIVE_DRAW_2026_11_09.md), [четвёртое микро-ТЗ](CHESS_COACH_PR46_FOURTH_REVIEW_LIVE_DRAW_REPLAY_AND_TERMINAL_STATUS_2026_11_09.md). **Они ИСТОРИЧЕСКИЕ, не отдельные очереди задач.** Править по текущему единому документу.  
**Source review** выполнен на коде и diff GitHub, **не выполнялись новые локальные тесты и живой двухсторонний матч**. Успех 977 тестов CI подтверждён на указанном SHA, но тесты не заменяют реальную сеть, настоящую Windows-форму и ручное сравнение изображений.

## 0. РЕШЕНИЕ ПО ПРЕТЕНЗИИ ВЛАДЕЛЬЦА: КТО И КОГДА ВНЁС ДЕФЕКТЫ

Проверен код **до** последней правки LIVE-01 (\`e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095\`) и **после** (\`6fa202f...\`):

| ID | Результат source blame | Статус |
|---|---|---|
| Исходная LIVE-01 — отправил offer, кнопки ничьей и сдачи зависли | **Существовала ДО последнего Codex-исправления**: прежняя \`_refresh_online\` выключала все операции при любом pending, даже offer_draw. Последняя правка исправила эту часть. | Закрыта по source + mocked tests; реальная live-проверка не выполнена |
| **S1** — old/duplicated \`gameState\` после HTTP ACK может снять outgoing offer pending | **НОВЫЙ дефект последнего исправления Codex**: добавлено \`_game_authoritative_sequence\` и условие \`sequence > ack_sequence && own_flag is False\` без проверки содержательной новизны event. Раньше этого fallback не было. | Confirmed code defect |
| **S2** — текст предложения ничьей перезаписывает финал/ошибку | **НОВЫЙ дефект последнего исправления Codex**: в конец \`_refresh_online\` добавлен unconditional \`game_status.setText\` по draw flags после уже отрисованного terminal/error state. | Confirmed code defect |
| **S3** — начата сдача, но ещё можно предложить ничью/вернуть ход/заявить ничью | **НОВЫЙ дефект последнего исправления Codex**: старый global pending-lock выключал все команды. Новая командная схема освобождает независимую сдачу от pending offer, **но не выключает прочие конфликтующие действия при pending resign/abort/claim_draw**. | Confirmed code defect / untested cross-command path |
| Первоначальная задержка Home ~30 s и orphan \`QComboBox\` | Возникли из более раннего UX-цикла, **не новая ошибка именно последней LIVE-01 правки**. Исправлены к предыдущему SHA \`e95ba4...\`. | Закрыто по source/CI; пользовательский cold start и второй значок на Windows надо проверить вручную |

**Ответственность:** последние S1–S3 введены последней кодовой правкой; **отдельная ответственность ревьюера** — проверять смежные операции и stream replay сразу, а не после следующего двухчасового запуска Codex. В следующих ревью **НЕ отдавать отдельно по одному пункту**; всё ниже должно быть проверено вместе.

## 1. ЧТО УЖЕ ПРОВЕРЕНО И НЕ НАДО ПЕРЕПИСЫВАТЬ

**Подтверждено на source/CI:**
- 5 sidebar routes, 4 Analysis cards, 3 nested tools, Play mode cards, inline PGN multi-selector, FEN/editor, common Review, account-scoped Library — архитектурно заведены в PR.
- Сквозные блокировки external engine/Fair Play, transient external storage, верный источник accuracy, nested parse ownership, Back from Review, AI/human rematch и настройки по source review уже были до последнего исправления.
- В Play скрытый state-only QComboBox имеет parent и не показывается отдельным окном; QSettings и radio синхронизированы по source/tests.
- Home fast preview с согласованным SQLite count+top3 внутри одного \`BEGIN\`; freshest preview владеет списком, безопасная аналитика переносится только при \`game_id + line_revision + account/clock + owner color\`. Новые нейтральные строки отображаются сразу. Исправления **R3-01 не откатывать**.
- Pending \`offer_draw\` теперь не блокирует кнопку **«Сдаться»**, а \`accept_draw\` и \`decline_draw\` взаимно исключаются в \`LichessService._action\`. Это нужная функциональность; не восстанавливать глобальную блокировку.
- Точный Windows run [42602284418](https://github.com/feshakomissarov-prog/chess/actions/runs/42602284418): \`test\` и \`visual-evidence\` SUCCESS; по log — **977 passed за 429.74 s**; визуальная artifact создана, но это не side-by-side 13 PNG.

**НЕ подтверждено этим аудитом:** реальный Live матч с двумя аккаунтами (в отчёте \`REAL_LIVE_UNVERIFIED\`), реальный Stockfish binary, first-paint на личной SQLite, Native GUI все 13 экранов, все DPI/размеры. Это не «найденные баги»; это **обязательный acceptance GAP**, который закрывается один раз до merge, а не генерирует бесконечные отдельные ТЗ.

## 2. КОДОВЫЕ ДЕФЕКТЫ: ЕДИНЫЙ ПАКЕТ ДЛЯ ИСПРАВЛЕНИЯ

### S1 [P1] Stale/replayed stream event преждевременно очищает outgoing offer и разрешает повторный POST

**Source:** [\`src/chess_coach/lichess/service_games.py:109–196\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/lichess/service_games.py#L109-L196).

**Причина:** \`reconcile_game_actions\` увеличивает локальную \`_game_authoritative_sequence\` при каждом полученном event. После 2xx/unknown HTTP ответ \`sequence > ack_sequence\` и \`wdraw/bdraw is False\` считаются доказательством, что offer больше не действует. **Точная копия** старого \`gameState\` (те же moves/status/draw flags) после HTTP ACK получает новый sequence и очищает pending, открывая повторное предложение без подтверждённого изменившегося серверного состояния. Clock-only update тоже не является доказательством разрешения нового offer.

**Исправить:** различать *event delivery* и *новое авторитетное состояние*. Дедуп по необходимому нормализованному fingerprint + учёт реального продвижения линии, auth generation и game id. Не считать просто новый номер event свидетельством исчезновения draw offer. Explicit own draw flag true после offer — отдельное подтверждение, terminal status — завершение, а право повторного предложения после отказа/продолжения — только по подтверждённому **свежему** состоянию/семантике сервера. При неоднозначном HTTP resync не делать повтор POST автоматически; пока нет безопасного доказательства — показывать статус и не разблокировать тот же offer. Это **не блокирует независимую сдачу**. 

**Не вводить новый навязанный таймер**, который очищает state без server evidence. Учитывать \`gameFull\` replay, disconnect/reconnect, status-only/missing flag, cross-account context.

### S2 [P2] Draw status перезаписывает более приоритетный результат/состояние

**Source:** [\`src/chess_coach/window.py:7838–7980\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/window.py#L7838-L7980).

**Причина:** \`_refresh_online\` сначала задаёт \`game_status\` для \`finished_view\`, явной ошибки/синхронизации или ожидания подтверждённого snapshot. Затем независимо от этих состояний новые \`if session.draw_offered_by_opponent / elif session.draw_offered_by_me\` **перезаписывают** строку. При terminal \`draw/mate/resign/timeout\` с задержавшимся \`wdraw/bdraw\` будет «ожидаем ответ» вместо итогового результата. Аналогично можно скрыть более важное сообщение \`awaiting_authoritative_snapshot\`/error.

**Исправить:** однозначная приоритетная таблица (terminal final result → safety/initial sync/critical error → ход/ответ сервера с указанием ошибок → актуальное входящее предложение → подтверждённое своё предложение → обычный статус). Отображать предложение как active action **только при подтверждённом сервером активном состоянии**, не скрывать clock/save результат и не реанимировать control на terminal. Не стирать \`draw flags\` вручную, чтобы спрятать симптом.

### S3 [P1] Идущая сдача/прерывание НЕ блокирует конфликтующие действия; командная изоляция асимметрична

**Source UI:** [\`window.py:7910–7980\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/window.py#L7910-L7980).  
**Source backend:** [\`service_games.py:72–101\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/lichess/service_games.py#L72-L101).

**Причина:** при \`pending_commands={"resign"}\` новая \`_refresh_online\` отключает **только** \`resign_button\`, но \`draw_button\`, \`decline_draw_button\`, \`claim_draw_button\` (при can_claim_draw) и \`takeback_button\` могут оставаться enabled, пока session.status всё ещё \`started/playing\`. \`LichessService._action\` защищает от двух противоположных \`accept_draw/decline_draw\` и повтора той же команды, **но не от offer/accept во время pending resign/abort/claim_draw**. Разные endpoint POST могут уйти в одну партию в конфликтующем порядке.

**Инвариант управления:**
- **Пока pending offer_draw:** \`resign\` остаётся разрешённой независимой командой, как требовал владелец. Без повторов \`offer_draw\`; incoming authoritative offer можно обработать при отсутствии уже идущего ответа.
- **Пока pending resign/abort или claim_draw:** запускать другие game-ending draw/abort/resign/takeback команды **нельзя** до authoritative reconcile/terminal status; показывать «Ожидаем подтверждения завершения партии». Не скрывать актуальную позицию/часы и не удалять информацию о поступившем offer.
- При pending \`accept_draw\`/\`decline_draw\`: невозможно отправить противоположный ответ. Решение, доступна ли при этом **отдельная явная сдача**, проверить на правилах Lichess и обеспечить без дублей; исключение не должно лишать человека выхода при зависшем offer. В приоритете безопасность и единственный исход.
- **Двойная защита** UI + service-level atomic pending lock/generation, чтобы быстрые клики/горячие клавиши и гонка Qt signals не обошли disabled. Не навязывать новый глобальный блок на любой pending: только матрица конфликтов.
- При отказе 400/403/422, 429, timeout и reconnect честно сообщать состояние; не отправлять повторные irreversible POST «на всякий случай».

**Сделать одну явную таблицу конфликтов команд** \`offer_draw, accept_draw, decline_draw, resign, abort, claim_draw, takeback\`, валидировать её тестами как unit и Qt. При server terminal все network actions недоступны.

## 3. ОБЯЗАТЕЛЬНАЯ ПОЛНАЯ МАТРИЦА РЕГРЕССИЙ — В ОДНОМ ЗАПУСКЕ CODEX

**Этот пункт является полным acceptance scope для текущей правки LIVE-01, а не новым экраном.** Критерии проверяются на уровне \`LichessService\`, \`LichessGameSession.apply_event\` и \`MainWindow._refresh_online\` вместе; не ограничиваться одной функцией или позитивным тестом. Стараться применять table-driven tests и один CI run.

### A. Исходящее предложение и сообщения сервера

- initial stream → \`offer_draw\` → 2xx: ровно **1** POST; copy of same baseline \`gameState\` после ACK (0, 1, 2 повторения) **НЕ** снимает pending.
- clock-only update, \`gameFull\` replay, reconnect/resubscribe, \`wdraw/bdraw\` missing/false/true: отличать replay от свежей authoritative смены.
- Реальный server-сдвиг с \`own_draw=True\` → подтверждённое предложение, кнопка повторного offer недоступна.
- Реальная игра продолжается и подтверждённо снимает предложение → восстановление права offer **только по реальным правилам Lichess**, без случайных повторов; не требовать принудительного repeat во время запрета.
- 400/401/403/404/422 definitive rejection; 429 с ограничением частоты; timeout/network drop с неизвестным outcome; late HTTP 2xx после другого stream event; stream state до HTTP response; reconnect while pending; stale auth generation; смена game_id. Никакого автоматического повторного POST.
- Два предложения от разных сторон почти одновременно; \`wdraw=True,bdraw=True\` и opposing pending. UI должен предлагать актуальный допустимый ответ, не задваивать запросы и не выдавать неясное состояние за подтверждённое.

### B. Incoming draw и завершение

- Входящее authoritative \`opponent_draw=True\` до/после своего предложения → на основном экране видны «Принять ничью»/«Отклонить» при active game; доступна отдельная «Сдаться», если нет pending terminal action.
- Два быстрых нажатия «Принять», «Принять→Отклонить», «Отклонить→Принять» должны давать максимум **один** response POST.
- Терминальные состояния \`draw, resign, mate, timeout, aborted, outoftime, stalemate\` с любыми \`wdraw/bdraw\` → **финальный результат** и только postgame actions, без текста «ждём ответа» и активных live buttons.
- При \`awaiting_authoritative_snapshot=True\` и уже существующем старом \`opponent_draw=True\`: приоритет safety-sync, нельзя предлагать принять неподтверждённый оффер.
- Incoming while looking at move history, Focus, re-entering game via Play, switching to Analysis and back; ни одно реальное предложение не должно быть незаметно потеряно.
- Исторический сценарий владельца: A предлагает ничью → B видит, не принимает/игра продолжается → B позже предлагает ничью → A видит и отвечает. A может сдаться в допустимом состоянии. Это обязательный **two-account live smoke**, а не только mock.

### C. Конфликтующие команды

- Pending offer → resign остаётся доступна и делает **один** запрос.
- Pending resign/abort → никакие offer/accept/decline/claim/takeback дополнительные POST не допускаются, в GUI соответствующие controls disabled.
- Pending claim_draw → отдельные повторные/конфликтующие команды не отправляются; после terminal результат корректен.
- Pending accept_draw/decline_draw → opposite response blocked; при неясном ответе server safety+idempotency.
- Результат после disconnect + online resume, account switch, Game A→Game B: pending не переносится между аккаунтами/партиями/поколениями авторизации.
- После terminal ни кнопка игры, ни keyboard shortcut, ни вторичный handler не могут отправить новые серверные действия.

### D. Проверка внешней семантики Lichess

- [Официальная Board API документация](https://lichess-org.github.io/api/) подтверждает endpoints \`game stream, draw, resign, takeback, claim-draw\` и запрет движковой помощи в активной человеческой партии. Не строить side-channel engine для теста.
- [Известный исторический report Lichess о сохраняющемся draw-flag после отказа ходом](https://github.com/lichess-org/lila/issues/11339) — зафиксирован в 2022 для Bot API. **Не объявлять доказательством актуального дефекта Board API 2026**, но включить **стресс-кейс \`own_draw=True\` после продвижения ходов и явного отказа**; не считать один флаг достаточной абсолютной истиной, когда server stream может запаздывать. Объединить flags + moves/arrival order + resync safely.
- Если реальный API не предоставляет надёжный критерий, **не изобретать** возможность повторного предложения. Оставлять один понятный pending-state и независимую сдачу, документировать несовершенную наблюдаемость.

## 4. РАСПРЕДЕЛЕНИЕ ПО ВСЕМ БЛОКАМ PR #46 — ЧТО ПРОВЕРИТЬ В ОДНОЙ ПРИЁМКЕ

**Выполнить до сообщения «всё закончено»; если настоящая среда недоступна, указывать именно конкретный НЕПРОВЕРЕННЫЙ пункт. Нельзя выдавать внешний blocker за ещё один малый баг, чтобы отправить Codex на новый круг.**

| Область | Основные целевые состояния и непреложные негативные проверки |
|---|---|
| Shell/Nav | Ровно 5 top-level routes, real Back/deep links, sidebar collapse/reopen, History не теряется при переходах. |
| Play | UX-01/02/03: AI, Friend Link, Challenge, per-mode QSettings, точный цвет/radio, clocks/custom, no orphan \`QComboBox\`, server ACK and no duplicate POST; UX-07 free board не содержит network actions. |
| Game | UX-04/05/06/07: clocks/turn/live, premove, Focus real board size, finish same board & two CTA, human draw/resign/takeback under full matrix §3; не показывать Stockfish во время человеческой партии. |
| Analysis/Archive | UX-08/09/10/11: four cards with WIP truthfulness, full archive+filters/account isolation, file/paste multi-PGN chooser with deliberate Open, parse generations and Back, FEN/editor legal position and transient source. |
| Review | UX-12/13: genuine no-analysis state, with-analysis state, official vs local source, graph/CP/Δ, navigation & learning blocked appropriately, no external owner-side fabrication. |
| Settings/Background/Progress | Stockfish auto setting default ON and stored OFF, queue resource/safety, existing Puzzles/Statistics routes not lost, account switch/late worker not contaminate data. |
| Home/performance | Real cold start with saved account and local SQLite, count+top-3 from consistent snapshot, newest games immediately and matched graphs safely preserved, no 30-s stalls. |
| Regression safety | Active human Fair Play fail closed in all screens, no cached PV/accuracy tooltip or accessibility leak; read-only safe SQLite/PGN/notes; worker stale callbacks; no untracked-file modification. |
| Responsive visual | **Side-by-side every approved UX-01…UX-13 PNG** against actual Windows Qt screenshot; at 1120×640, 1280×720, 1366×768, DPI 100/125/150; user-perceived overlap/tab order/clickability. |
| Real integrations | **Actual** Lichess two-account game and local Stockfish binary smoke when authorized; mocks are useful but not equivalent. |

## 5. ЭТАПЫ И ПОРОГИ ПРИЁМКИ

**Одно поручение, а не серии по паре багов:**

1. Провести необходимый исходный trace и написать **все focused tests** из §3 до/во время исправления. Убедиться, что они падают на старом SHA там, где это возможно и не требует реальной сети.
2. Исправить S1, S2, S3 с минимальным scope. Полную матрицу §3 выполнить тестами; явно отметить несмоделированные/реальные сценарии.
3. После правки **целостно проверить** ВСЕ изменённые механизмы PR, используя таблицу §4 и 68 UX-T cases мастер-ТЗ. При находке дополнительного бага **не останавливать на половине работы и не отправлять новый запрос владельцу**: исправлять в том же рабочем цикле только если это безопасное NOW отклонение, сопровождать proof/regression test.
4. Повторить полный local pytest, \`compileall\`, \`git diff --check\`, Windows CI **оба jobs на финальном SHA**, включая reporter-only коммит. Никаких auto-merge.
5. Если доступен настоящий desktop Windows GUI и approved assets, выполнить **native side-by-side 13/13** в этом же цикле, приложить screenshot evidence. В случае отсутствия доступа — **нет права написать «visual QA completed»**.
6. **Реальный двухсторонний матч** требует авторизованных тестовых аккаунтов Lichess; без них оставить \`REAL_LIVE_UNVERIFIED\` и отдельный план РУЧНОЙ пользовательской проверки, не делать ложный PASS. Это **внешний acceptance gate**, а не повод бесконечно плодить Codex tasks.
7. Доложить в \`docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md\` по таблице: \`confirmed bug/fix/new test/results/GUI screenshot/blocked reason\` и **полный список remaining acceptance gates**. PR #46 остаётся draft до ручного утверждения.

**Запрещено:**
- возвращать старую общую блокировку всех game controls из-за pending offer;
- использовать один \`gameState sequence\` или случайный таймер для dedup опасного POST;
- во время live-human game показывать engine scores/советы;
- изменять \`main\`, мерджить/переписывать PR #44/#45, трогать живые пользовательские PGN/SQLite/notes/токены без запроса;
- выдавать \`pytest SUCCESS\` или Windows screenshot capture workflow за полноценный реальный online smoke/13 side-by-side;
- давать владельцу очередное «запусти Codex ещё раз, у меня новые две мелочи» без полной проверки §3–4.

## 6. ЧЕСТНЫЙ СТАТУС АУДИТА

**Подтверждённых source-дефектов на \`6fa202f...\`: 3 (S1–S3).** Дополнительные сценарии из §3 — **обязательные регрессионные тесты и риск-кейсы, а не фактически обнаруженные отдельные ошибки**. Ошибка «ни одного другого дефекта в коде вообще нет» **НЕ может быть доказана** чтением diff или зелёными 977 тестами. Автор review не имел живых Lichess токенов, личной Windows среды или всех 13 current screenshot side-by-side.

До следующего поручения владельцу нужно именно **одно единое техническое задание**, а не четыре раздельные ссылки. **Этот документ и есть единственный нормативный реестр** для следующего прохода; старое мелкое R4 ТЗ больше не пересылать Codex отдельно. Обнаруженные в процессе исправления проблемы должны быть присоединены к этому **одному** списку и закрыты в рамках того же прохождения, если это безопасно.
