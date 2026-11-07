> **ВАЖНО — ПОВТОРНАЯ НЕЗАВИСИМАЯ ПРОВЕРКА 08.11.2026:** следующий Codex gate на SHA `09766297a1ff0e5d960681116d8763959d1c871f` завершён: Windows CI #408 **894 PASS**, но выявлены **4 подтверждённых code/UI остатка и 1 незакрытый пользовательский аудиоконтракт**. **Актуальное единственное ТЗ для следующего исправления — §E внизу этого файла (POST-01…05)**; прежние CORR-01…16 ниже остаются **историей закрываемого этапа**, не запускать их повторно без red regression. Общий статус **NO GO до устранения POST-01…04 и независимой проверки**, а физический звук/личные интеграции/художественная приёмка — отдельный внешний gate.

# ARCH-UX-01 — НЕЗАВИСИМОЕ CODE + VISUAL REVIEW / ЕДИНОЕ КОРРЕКТИРУЮЩЕЕ ТЗ

**Дата:** 07.11.2026. **Review target:** code draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), **exact HEAD `afb2cc63958da048fcc6683555f909942e94400d`**. **Базовый предыдущий checkpoint:** `5c375b070e6644dbded5fca62d960fc09c90651e`; **6 коммитов / 28 изменённых файлов** относительно этого checkpoint. **Автор исправлений:** Codex; **независимый source+artifact review** отдельно. **СТАТУС: NO GO / КОРРЕКЦИИ ОБЯЗАТЕЛЬНЫ ДО РУЧНОЙ ПРИЁМКИ.**

**Связь с одним MASTER:** [ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md §15–15.7](../specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md), включая V-01…03, T-01…06, R-01…05, VID-01…09, VOICE-01…10 и утверждённые три PNG. Этот файл — **один заключительный независимый packet исправлений по результатам проверки SHA**, а не новое продуктовое ТЗ, не VIS7/RF и не переписывание уже выполненных задач. **Код исправлять только в том же PR #44 после передачи владельцем. PR #45 содержит документы. Без merge/main, без прямого доступа к личным данным.** Отложенный [NAVIGATION_PATH_AUDIT_PARKED](../specs/NAVIGATION_PATH_AUDIT_PARKED_2026_11_07.md) не запускать.

## A. Проверенный факт, границы доказательств и честный gate

- GitHub Actions **[run #401](https://github.com/feshakomissarov-prog/chess/actions/runs/42397313867)**, exact SHA `afb2cc6`: **test job SUCCESS**, по независимо прочитанному raw job log — **864 passed in 430.27 s**, Python 3.12.10, unhandled-thread warning as error; `compileall` и isolated offline `MainWindow` PASS. **Visual-evidence job SUCCESS**. Предыдущие неуспешные #396–399 не переинтерпретировать как баги финального SHA; увеличение ожидания теста с 12 до 45 секунд после bounded 43-record migration само по себе допустимо, **но duration не доказывает отсутствие starvation/deadlock**.
- Независимо получен архив **[Windows artifact #13583773303](https://github.com/feshakomissarov-prog/chess/actions/runs/42397313867/artifacts/13583773303)**, 28,344,217 B, digest `sha256:ffd50e849ef70b16acc7137dd6b84cb6353bf707103492f5106a8abed111086e`. Извлечены и визуально **открыты реальные PNG** из `scale-1/`, `scale-1.25/`, `scale-1.5/`: **64 synthetic state captures** (26+19+19), включая native Windows screen pairs with 3 owner-approved refs, кадры narrow UI и review/game/training. Истинность ZIP/HEAD подтверждена `capture.txt`, job log, PNG pixels. Это **нативный Windows runner с QT_SCALE_FACTOR=1/1.25/1.5, а не физический дисплей/DPI владельца**.
- Фактический **V-02/V-03 визуальный gate FAILED**, несмотря на PASS «64 screenshot states»: ниже приводятся **видимые клиппинги, текстовые пересечения, недоступные кнопки**. Автоскрипт `scripts/capture_visual_master.py:368–500` проверяет геометрию в основном для Background и Library и пиксельный размер кадра; **не** проверяет Play CTA, Game, Training, Review на clipping/overlap. Скриншот `PASS` ≠ usable UI и ≠ сходство с утверждённым артом.
- Исходники **последних 6 коммитов и текущего SHA** сравнены с единым §15 (OAuth, generation, backfill/SQL, Dashboard, Play, sound, Lichess, Stockfish/Review, Qt UI), прочитан новый regression test file `tests/test_visual_master_final_regressions.py` и матрица приёмки. **Не запускались локально отдельно pytest или настоящий live Lichess/Stockfish**, потому что code audit доступен через GitHub и точный CI, а личные сервисы/Windows PC недоступны. Не выдавать анализ кода без red test за доказанный live failure.
- Часть прежних проблем действительно закрыта по source (например, lifecycle backfill close, Home preview handle-id, invalid official accuracy count, статус finished turn). **Не откатывать** проверенные fixes при доработке. Но **нельзя закрывать весь MASTER**, пока нет red→green ниже и owner acceptance.

**Классы:** `CONFIRMED VISUAL` — непосредственно видимый дефект в exact-head PNG; `CONFIRMED SOURCE` — логика в текущем файле явно не удовлетворяет контракту; `INVESTIGATE` — опасная ветка, но runtime root cause требует теста; `ACCEPTANCE GAP` — проверка не выполнена и не должна называться PASS.

## B. Единая очередь правок в том же PR #44

### CORR-01 · P0 gate · Play: на compact DPI невозможно нажимать карточки режимов — CONFIRMED VISUAL

**Source screenshot:** [artifact #13583773303](https://github.com/feshakomissarov-prog/chess/actions/runs/42397313867/artifacts/13583773303) `scale-1.5/1120x640-play.png` (1680×960 physical), также `scale-1.25/1280x720-play.png`. **Пять карточек отображены, но их кнопки обрезаны рамкой**: «Свободная доска», «Играть против AI», «Играть с другом», «Вызвать игрока», «Показать партии» видны частично, зоны действия/подписи недоступны, одновременно сверху всё ещё занимает высоту дублирующая combo-форма. **Source:** `ui/pages/play.py` создаёт `grid` карточек + основную form layout, помещает их в обычный QVBox без доступного scroll/responsive collapse. Связь **VOICE-04, V-03, VID-09**.

**Fix/accept:** при 1120×640 @Qt1.5 и 1280×720 @Qt1.25 все пять способов **визуально читаемы и реально выбираемы мышью/Tab/Enter**, без обрезанных CTA и скрытых ключевых полей; допустим **локальный** scroll режима (не общий scroll game board), adaptive grid, компактный selector/одна динамическая панель выбранного режима, но не потерять нужные controls, cancel и link. Один клик выбирает режим, второй явно подтверждает создать/пригласить, без двойного POST. Red screenshot/Qt geometry+QTest mouseHit + screen after each mode → green, включая `local/ai/friend/challenge/active`.

### CORR-02 · P1 · Play active panel самопроизвольно показывается в чужом режиме — CONFIRMED SOURCE

**Source:** `window.py:2674–2693`: `show_play()` всегда вызывает `_refresh_active_games_for_play()`, который `set_active_games(status="Обновляю…")` без проверки текущего режима. `ui/pages/play.py::set_active_games()` заканчивает `self.active_panel.show()`; `window.py:6290` после этого может рендерить ответ, потому что панель уже видима. При выбранной `local`/AI активная panel занимает место и конкурирует с режимными настройками. При `mode=="active"` `PlayPage._update_form` наоборот скрывает **все карточки** и оставляет выбор через combo — откат от карточной навигации.

**Fix/accept:** fetch списка может идти асинхронно при входе в Play, но **visible active panel only when mode == active**, без эффектов от late response; карточки доступные для переключения сохраняются при активном режиме либо есть равнозначный быстрый доступ к 5 режимам по утверждённому UX. Tests: arrive local with connected fake, fetch pending/result/error, switch AI then active, on gameFinish, auth A→B, no panel flicker or extra requests; no unexpected game stream opening.

### CORR-03 · P1 · активная мини-доска показывает позицию другой партии при bad/missing FEN — CONFIRMED SOURCE

**Source:** `ui/pages/play.py::_show_active_preview`: когда `fen` отсутствует/невалиден, меняет только текст «Позиция ещё не подтверждена сервером» и выходит без `active_preview_board.set_position`/clear. После выбора другого game_id UI **продолжает рисовать прежний board**; при очистке списка рендерит `chess.Board()` даже при надписи «Нет подтверждённой позиции» — это стартовая позиция, которую пользователь может ошибочно счесть authoritative.

**Fix/accept:** preview identity = `account + gameId + auth_generation + confirmed_line_version`. Invalid/absent FEN → **нейтральная заглушка без чужого доска-состояния**, disable irrelevant Continue if game missing/stale, никаких стартовых «подтверждённых» позиций. `valid A → invalid B → valid B → empty → account switch`, rapid multiple responses: deterministic Qt test and screenshots. No background per-game stream for thumbnails. **VOICE-05, T-02**.

### CORR-04 · P1 · Игровая доска и controls при 150% / 1120×640 физически перекрываются — CONFIRMED VISUAL

**Exact PNG** `scale-1.5/1120x640-active-game.png` плюс `scale-1.25/1280x720-active-game.png`: правый status/button/header текст **накладывается на кнопки и режется вертикально** («Сначала выберите…», заголовок списка ходов, «Очистить пометки»), навигационные controls пересекаются с техническим сообщением, сама доска сжата; `active-game` всего лишь fixture local, проблема возникает **без сети**. **V-03/VID-09/finish-on-board**.

**Fix/accept:** reflow доска + sidebar (при необходимости column → stacked/compact/scroll only sidebar) с минимально разумным board size и обязательной видимостью game actions, status, move history, premove/turn; кнопки целиком кликабельны, labels не пересекаются, keyboard focus доступен. Снять **active local, live AI, finished human/correspondence (fake safe)** в 1120×640/1.5 и 1280×720/1.25; bbox + glyph/text clipping assertions (не только внешний QRect), inspect PNG. Не превращать игру в страницу со скрытой доской и обязательным бесконечным page scroll.

### CORR-05 · P1 · Training: текст пересекает доску и обрезается список и действия — CONFIRMED VISUAL

**Exact PNG** `scale-1.5/1120x640-training.png`: текст «Практика по сохранённой попытке…», номер задачи/темы и explanation **проходят через видимый chessboard**; строки истории отрезаны по горизонтали (появляется horizontal scrollbar), «Показать решение…», «Сбросить упражнение…» не помещаются целиком; это логически **наполненный**, а не empty экран. **V-03/UX-COV**.

**Fix/accept:** отдельные вменяемые layout zones для доски, выбранной задачи, истории, действий; никаких floating overlaps, горизонтальный scroll для обычных текстовых list rows заменить переносом/деликатным elide+tooltip без потери game info. Доска и controls внутри viewport при Qt scales 1/1.25/1.5; запись synthetic, no new puzzles engine. Regression checks board QRect ∩ label/solution QRect = empty and key actions visible; настоящий PNG red→green.

### CORR-06 · P1 · Review: таблица и coverage наложены, разбор трудно читать — CONFIRMED VISUAL

**Exact PNG** `scale-1.5/1120x640-game-review.png`: «Охват 17/17 позиций» видимо накладывается на строку **«Ошибки»** сравнительной таблицы слева. Board/headline/plot/CTA много мелких строк, таблица ходов справа в fixture пустая при наличии 16 ходов и данных на графике (проверить выбранный source/filter). Это не доказательство порчи ходов, но **кадр не удовлетворяет читаемости**. **VID-07/09, V-03**.

**Fix/accept:** reflow metrics+coverage into separate rows with height-for-width, no overlay or clipped moves, stable selected ply/SAN and focus. Test actual 17-ply fixture with explicit selected move from table and plot, nonclassified and partially assessed; screenshot with populated move table, 1120/1.5 and 1280/1.25; pixel & click review. Never show unknown move as “?” error marker.

### CORR-07 · P1/P2 · Library compact footer/action text clipped — CONFIRMED VISUAL

**Exact PNG** `scale-1.5/1120x640-games.png`: справа/снизу footer `8 па…` и часть действий/подписей обрезаны, selected information and export/navigation cramped. `scripts/capture_visual_master.py:417–442` проверяет **только QRect кнопок внутри stack**, а не реальные рамки глифов, elide, доступность текста. **V-01/V-02/VID-01**.

**Fix/accept:** readable pagination, full primary actions with min-height and tooltips, local detail scroll, selected/opponent/canonical source always unambiguous; Qt test visual text-fit and Tab/click across all visible actions 1120/1.5 + 1280/1.25; do not assert only window.width.

### CORR-08 · P1 · 3 owner-approved arts still not accepted: iterative visual design gate — CONFIRMED VISUAL

**Pairs actually inspected at exact SHA:** `scale-1/pair-home-1672x941.png`, `pair-games-1672x941.png`, `pair-background-active-1672x941.png` (reference LEFT, native actual RIGHT). Home: tiny opponent/result/accuracy rows, radically smaller graph/card typography vs approved reference; Library: scoreboard/overview graph/metrics lack approved hierarchy and significant area/spacing; Background: tiny workflows/status/progress/event hierarchy, visually sparse/inconsistent vs original art. Same dark palette != same accepted design. This is **not** claim of data calculation bug. Need explicit aesthetic and functional review in both 1672×941 + compact frames, not merely file exists.

**Fix/accept:** iterate three real Windows screens *against supplied unmodified owner-approved PNGs* `tests/visual/reference/01_HOME_APPROVED.png`, `02_MY_GAMES_APPROVED.png`, `03_BACKGROUND_APPROVED.png`; side-by-side compare typography/relative size/spacing/graph footprint/status hierarchy at 1672×941 and downscaled compact equivalent. Assess person-readable visual similarity, all actions/metrics/truthful source; note acceptable intentional differences due to real data and accessibility. Re-capture **actual** populated 3 screens and whole shell including CORR-04…07 on exact final HEAD. Owner final approval remains external. **V-02 / VIS6-03 / VID-01 / VOICE-02**.

### CORR-09 · P1 · «Свежая завершённая» Lichess correspondence неверно приоритизируется по start date — CONFIRMED SOURCE

**Source:** `infrastructure/sqlite/jobs.py:151–168::list_lichess_analysis_backfill` uses `g.started_at >= UTC_now-14d` to decide «fresh» and queue. **Counterexample:** correspondence started >14d ago but finished just now is treated as **old** while very recently started other games receive priority. Owner VOICE-09 explicitly asked recently **completed** first. The rest of fairness reserve is constructive and should remain.

**Fix/accept:** priority uses **authoritatively confirmed completion/last move timestamp**, fallback source-aware start only when later event timestamp truly absent; prevent metadata write timestamps falsely promoting old games. Hard cap daily quota, TTL, account/generation, starvation reserve remain. Tests: 40-day correspondence completed NOW vs started yesterday finished a week ago, 3,500 old + 10 new, unknown timestamp, same-day fairness and no duplicate game/rev. Explain exact ordering and indices, benchmark.

### CORR-10 · P1 · «Проверить сейчас» сбрасывает TTL всех партий вместо адресного retry — CONFIRMED SOURCE

**Source:** `window.py:1696–1722` calls `_start_lichess_analysis_backfill(force_retry=True)` **for entire account**. `infrastructure/sqlite/jobs.py:117–123` and `219–228` with `force_retry` run an **account-wide UPDATE** resetting `accuracy_next_retry_at / evals_next_retry_at` for ALL `no_server_data/partial/error` rows. The maximum daily count/retry-after still enforced; **do not falsely claim** this bypasses the whole rate limit. It **does override per-game no-data/TTL** and cannot target a selected just-finished game. **VOICE-09, VID-06, R-04**.

**Fix/accept:** two explicit semantics: `global check eligible now` obeys all per-item due/TTL and account budgets, `recheck selected completed game` permits only approved scoped exception per game+revision with explicit user action/reason, **not account-wide mass reset**. Distinguish “не готово/TTL/429/day budget/уже проверяем” inline. Test 100 no-data games with due tomorrow + one selected fresh after server web-analysis, simulated API delayed→valid official JSON; click must not make other 100 immediately due nor initiate unrestricted network; never auto-create server analysis.

### CORR-11 · P1 · sound move still waits network POST; latency owner requirement incomplete — CONFIRMED SOURCE / UX

**Source:** `window.py::on_move_intent` stores sound intent; `lichess/service_games.py` emits `session_move_accepted` only **AFTER `client.send_move()` returns**; `window.py::_lichess_session_move_accepted` then plays cue. This prevents false sound on reject and deduplicates authoritative replay (good) but for 500–1500ms REST lag user still hears the click **only after network**. New test `test_send_move_ack_emits_sound_eligibility_only_after_success` asserts this protocol behavior, **not** input→audible cue SLA. **VOICE-06 07:26–07:38**.

**Fix/accept:** design separate instant **pending/submitted** UI audio response for legal local submission from definitive move sound if necessary, dedupe game+ply+uci/request, reject/rollback/no false confirmed sound. Timestamp input, HTTP start, ACK, stream and sound; 0/500/1500ms fake transport; premove/duplicate/reject/takeback; audio enabled/disabled and real Windows device owner test. If protocol cannot guarantee a legal accepted move instantaneously, explicitly reflect that tradeoff — don't fake a confirmed move before server. Aim perceptual immediate feedback without breaking correctness.

### CORR-12 · P1 · finished timed game with malformed clock falsely labelled «без часов» — CONFIRMED SOURCE

**Source:** `window.py:7440–7460`: branch `if session.clock_state_valid is False and (no_regular_clock or finished)` sets `“Завершена · без часов”` for **any finished timed game**, even where `session.speed="blitz"` and previously known `clock_control="180+2"` but server sent invalid/missing clock. Branch `7473–7491` similarly says `“Партия завершена · без часов”` on finished timed game with missing times. Must not lose known time control. **VID-03**.

**Fix/accept:** distinguish `timed but invalid/stale snapshot`, `timed finished with known control`, `correspondence`, `unlimited`, `unknown`, never infer “untimed” merely because timeout/status finished/invalid clock; fail closed on extrapolation but show “Контроль 3+2 · часы остановлены / данные недоступны”, test malformed wtime then terminal, correspondence and backfill, no imagined time. Additional case `session.py::_apply_game_full_metadata` currently doesn't always populate `speed/daysPerTurn` from a sole authoritative gameFull snapshot; **investigate/reproduce first** reconnect without gameStart and correct unknown classification if real.

### CORR-13 · P1 · Home→Review direct route uses blocking SQLite on GUI thread and loses Home-origin context — CONFIRMED SOURCE + investigate

**Source:** `window.py:3354–3374::_open_home_game_details` synchronously invokes `self.game_repository.get_game(game_id)` in button callback; unlike protected async Library workers, this **may freeze Qt** with writer lock/slow SQLite. It merges stale Home projection `current.update(record)` without a fresh account/selected-scope check on click; `_open_saved_game:8500–8502` assigns `_active_route="games"` even when opened from Home. Root-cause cross-account leak **not claimed proven**, but design diverges from VOICE-10: direct, account/revision safe, BACK→Home.

**Fix/accept:** async cancellable account-scoped game read with request token generation, current account/rev/line checks before rendering, preserve origin Home for Back and selection, placeholder when archive finalizing; no row from A can open for B after identity change. Tests with SQLite `BEGIN IMMEDIATE`/busy writer, A→B and rapid click, late DB result, pending import, no UI blocking (>200ms Qt responsiveness while DB pending), two Back routes (Home vs Library), reveal only permitted live-safe fields.

### CORR-14 · P1 investigate · lease and priority real concurrency not proven

- **R-02 SQLite leased row:** `infrastructure/sqlite/jobs.py:50–70` reclaims `running` when `updated_at` >10min old; new test proves recent not requeued & artificially 2020 orphan reclaimed, **not** genuine >10min live worker with heartbeat. Simulate active long HTTP, process exit, two workers on same repository and re-enqueue after 10min; ensure exactly once request/commit per line revision, durable ownership and crash recovery. If legitimate lease can overlap, fix with owner+heartbeat/claim CAS. If bounded source request always <10min proven, record evidence and reject issue as false positive. Don't expand into new queue/schema without proof.
- **VOICE-07 MANUAL > BATCH:** `window.py::_analysis_safety_games_received` now calls `_pause_batch_analysis(manual=False)` then starts manual ENGINE lane, but no captured test proves **bounded release and eventual resume with a real competing batch**; existing tests assert timer cancellation/resume, not engine hand-off, multi-thread responsiveness or batch cursor. Add deterministic barrier using fake engine and real coordinator: batch holds lease, manual requested, pause current position, release manual in bounded window, batch continues from durable cursor after finish/error/cancel. Test auth switch/active human interrupts, no Fair Play bypass. Fix ONLY if red evidence.

### CORR-15 · P2 · chess-standard classifications in move table still ambiguous — CONFIRMED SOURCE

**Source:** `ui/move_table.py:12–23` currently `inaccuracy → “!”`, `mistake → “?!”`, whereas standard chess notation interprets `!` = good move, `?!` = dubious, `?` = mistake; `??` = blunder. VID-07 addressed neutral unknown (good) but existing glyph mapping still miscommunicates actual evaluation. **Fix/accept:** either use chess-standard NAG symbols with accurate names, or **unambiguous separate icons** (triangle/colors/text legend) not resembling contrary notation; keep source classifications true, Unknown remains neutral. Test each severity/tooltip, accessibleDescription, pgn fixture, no evaluator fabricated.

### CORR-16 · P1 gating · screenshot harness cannot prove passing whole shell — CONFIRMED EVIDENCE GAP

**Source:** `scripts/capture_visual_master.py:368–500` only asserts specific Background workflow box intersections, wide Home scroll, some Library footer boxes and `QImage.size == logical×DPR`. The CI log prints **64 strict states PASS** while PNGs above show Play/Training/Review/Game clipping and overlap.

**Fix/accept:** strict Windows visual gate must validate at **all major screens**: critical label text-fit (`fontMetrics`/heightForWidth), primary action full click target inside viewport, no title/board/legend/table/scrollbar overlap, selected account identity/provenance, populated move table at selected ply, accessible keyboard controls, hover tooltip geometry and screen-readers. Add red test **against existing bad image states/geometry** before fix so original could not pass; after iterate, run same capture. Audit `1672×941 @100%, 1360×860 @100%, 1280×720 @100/125%, 1120×640 @150%` plus physical owner DPI as final separate gate. Pair art QA is **manual independent** with actual PNG review, not “pair file produced”. A green CI still cannot stand in for real owner Lichess/Stockfish acceptance.

## C. No-false-bugs and accepted changes to preserve

1. **Confirmed PASS only within tested boundary**: exact-head CI 864 PASS, no Qt worker uncaught exceptions, compileall, Qt startup, PNG artifact integrity and logical×DPR sizes. This is meaningful and must stay green after edits. Earlier CI #399 migration timer is not necessarily a production deadlock: 43 records / bounded 10-row portions plus 45s test is reasonable until counter-evidence.
2. Background high-DPI `ready` status and compact cards were corrected in commits after `191e93c`; **do not indiscriminately revert** them. In latest artifact at narrow 150%, the previous Background cards collision is not reproduced. New UI fix must preserve no overlap & due semantics there.
3. Sources in sidebar/Review/Library may be intentionally hidden by a **fresh Fair Play gate** while a human live game is underway. Never force Stockfish/evals while any remote active human correspondence game exists — even if browser-only. Passive safe work may continue only if separately verified. No server-analysis creation, no 429/day budget bypass.
4. Real hardware DPI, connected Lichess/OAuth/PAT, actual owner SQLite/PGN, physical audio and real UCI Stockfish **were not part of CI**; do not make synthetic data leak into user data, and do not label these runtime gates `PASS`. Ask owner before any real-account test; provide repeatable local acceptance script with private data staying on their PC.
5. **No features** beyond the corrections; future navigation graph stays **PARKED**. No PR merge or `main` modifications.

## D. Single Codex corrective execution + acceptance format

**Do not just edit the acceptance matrix to PASS.** First establish test/evidence for each CORR item with `CONFIRMED / EXPECTED / NOT REPRODUCED / BLOCKED`, and `red → code fix → green`. Do **not** invent runtime causes for investigate items; don't refactor unrelated services or weaken security.

**One working branch:** current `codex/arch-01-modular-refactor` / same **draft PR #44**. Baseline exact SHA `afb2cc63958da048fcc6683555f909942e94400d`; before changes re-check current PR HEAD. Owner-approved master §15 and actual PNG from code `tests/visual/reference/` remain immutable targets. Prioritize: (i) UI interaction/blockers **CORR-01,04,05,06**, (ii) truth/security **CORR-02,03,09,10,12,13**, (iii) conditional concurrency **CORR-14**, (iv) glyph/click/latency/fidelity **CORR-07,08,11,15,16**. Related fixes may be grouped into one patch/test but must retain separate traceability.

**Required evidence on new exact SHA:**
- Pytest full Windows 3.12.10 with worker warnings-as-errors, `compileall`, offline startup, diff check, all existing 864 tests preserved and corrected/new regressions added; deterministic barriers for auth/cross-account/SQLite/lease/Stockfish scheduling/move ACK.
- Native Windows GUI **64+ populated states** for exact scale set with **working geometry/text fit & actual click-through**. Three original PNG ↔ final actual image pairs at 1672×941; updated narrow Play/Game/Training/Review/Library before/after (at least screenshot file paths & tested bbox), independent human visual sign-off; provide artifact download URL.
- `ID → source (file:line) → red reproducer or screenshot → patch → test → green image → remaining limits` matrix, one concise PR #44 report with SHA+CI+artifact. Every P1 with no red proof marked `NOT PROVEN`, not “fixed”.
- Owner's physical Windows + permitted real-data verification **still requires explicit consent and remains final external acceptance**. Do not use private credentials/data automatically, do not merge.

**Final disposition as of `afb2cc6`:** **CI PASS / exact artifact inspected / VISUAL + OWNER ACCEPTANCE NOT PASS / CORRECTION REQUIRED.** This document is the *only* corrective work packet for this gate; retain existing unified §15 master and historical stages as references. No background execution authorized by this review file alone.


---

## E. Повторная независимая проверка после CORR-01…16 · **08.11.2026** · SHA `09766297a1ff0e5d960681116d8763959d1c871f`

### E.0 Проверенные факты / честная оценка улучшений

**Target:** [draft code PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) exact `09766297a1ff0e5d960681116d8763959d1c871f`; **3 новых коммита относительно `afb2cc6`, 30 изменённых файлов** по GitHub compare (включая документы/тесты; не путать с отчётом 24 файлов из другого UI-diff). PR открыт, draft, не merged. **[Windows CI #408 / run 42419662079](https://github.com/feshakomissarov-prog/chess/actions/runs/42419662079) SUCCESS:** независимо прочитаны job log, **894 passed in 467.18 s** (Python 3.12.10, unhandled worker warning as errors), compileall, isolated MainWindow; visual-evidence job PASS.

**Визуальное доказательство действительно получено и прочитано, не только доверие Codex:** скачан/разобран [Windows artifact #13593435525](https://github.com/feshakomissarov-prog/chess/actions/runs/42419662079/artifacts/13593435525) (**29,154,373 bytes**, `sha256:e8a8878143468d581607db492277b0da3e7e18c332aab6f3a4335795d69a9e37`), осмотрены `scale-1`, `scale-1.25`, `scale-1.5`, **30+23+23 = 76 native synthetic states** и owner-approved art pairs (Home/Library/Background). Подтверждён значительный прогресс: компактные Play/Training/Game/Review теперь отрисованы, нет ранее заметных пересечений, доступны локальные scroll и кнопки; готовые данные Lichess не фабрикуются; меры по OAuth/generation, SQLite leases и daily budget получили новые тесты. **Не требовать переделки всех CORR-01…16.**

Но логический review выявил оставшиеся ошибки, **которых CI/76 screenshots не тестируют**. Классификация: POST-01,02,03,04 — `CONFIRMED SOURCE` / `CONFIRMED VISUAL` и должны иметь red→green. POST-05 — **неисполненный UX/physical-audio acceptance**, не выдавать его за доказанную поломку sound API.

### POST-01 · P1 · «Повторить проверку выбранной партии» на деле запускает массовый backfill — CONFIRMED SOURCE

**Точная цепочка:**
- `src/chess_coach/window.py:7825–7853::retry_selected_analysis()`: вызывает `_start_lichess_analysis_backfill(generation, manual=True, preferred_game_id=selected_id)` и сообщает: **«Повторно проверяется только выбранная партия»**.
- `src/chess_coach/infrastructure/sqlite/jobs.py:146–184::list_lichess_analysis_backfill()`: `preferred_game_id` — только `CASE WHEN q.game_id=? THEN 0 ELSE 1 END` внутри **ORDER BY**, при `limit=10` результат дополняется 9 прочими due-играми. `archive.py:265–410::refresh_ready_analytics()` делает сетевые вызовы **для ВСЕХ returned candidates**. Значит подтверждённое обещание «только одна» неверно, один ручной клик может расходовать дополнительные до 9 запросов и дневную квоту. Если выбранная игра вообще не due, могут проверяться **только посторонние**.
- Имеющийся `tests/test_corr_gate_regressions.py::test_manual_global_backfill_check_does_not_clear_account_wide_ttls` вызывает **limit=1** и потому не ловит производственный `limit=10`.

**Fix:** отдельный exact game+account+line_revision **strict-scope parameter/selection** (не просто preferred ORDER BY) для ручной адресной проверки, с due + budget + Retry-After, если выбранная revision устарела/готовые данные уже есть — объяснить статус и **не расходовать квоту на другие игры**. Глобальная фоновая дорожка по-прежнему обрабатывает до 10 по справедливому свежему/старому приоритету. **Red:** 20 due unrelated + selected partial/no-data + case selected not due/expired; one click produces **ровно 0 или 1 network request for selected sourceGameId**, never unrelated; quota 25, 429, TTL, duplicate clicks, cancel/auth switch. Green per-game report and click-UI. **Требование CORR-10 осталось частично незакрытым.**

### POST-02 · P1 security/account isolation · Избранный Library account A не сверяется с текущим OAuth/PAT account B — CONFIRMED SOURCE

**Source:** `window.py:7835–7853` берёт `account_id=game.get("account_id")` при наличии `lichess:`, но **не сравнивает его с каноническим авторизованным `self.lichess.username`**. Локальный `retry_lichess_analysis_game(account_A, game_A)` в `jobs.py:186–220` проверяет связь игры с **запрошенным A**, а не право токена текущей сессии B на этот account. Затем `_start_lichess_analysis_backfill` всегда запускает `LichessService.refresh_lichess_analysis_background` с username **B** и тем же `preferred_game_id=A.game`. Итог: локальная TTL/очередь **A изменяется без актуального разрешения A**; реальные API расходуют лимиты **B** и статус может ложно объявить проверенной игру A. Это нарушение границы акаунтов, даже если чужой HTTP JSON не записался.

**Fix:** перед **любой** SQLite mutation проверить canonical selected `account_id` ↔ current authenticated account identity, auth_generation, source game linked/current revision; при mismatch — остановка без изменения БД и без запросов, понятная подсказка «Выберите/подключите аккаунт X». При асинхронности повторить fence непосредственно перед network и commit. **Red:** локальный archive A, подключенный Lichess B, Library filter A/All, одна и та же game_id связана с A+B, + late auth A→B; zero modifications to either account and zero network on mismatch, credentials not exposed. Green negative + positive A→A.

### POST-03 · P1 · Finished timed game снова показывает ложное «без часов», если clock snapshot **отсутствует**, а не повреждён — CONFIRMED SOURCE

**Source:** `window.py:7560–7599::_update_clock()`. Исправлено только `session.clock_state_valid is False`, и тест `test_finished_timed_game_with_invalid_clock_keeps_known_time_control` задаёт именно `False`. Но обычный reconnect `session.clock_state_valid is None`, `session.white_ms=black_ms=None`, `session.clock_control="180+2"`, `status="mate"` приводит в следующую ветку: `if white_ms is None or black_ms is None` → `"Без часов"` / `"Партия завершена · без часов"`. Ложное утверждение, будто партии вообще не было с контролем, несмотря на известный timed clock. **Тот же предыдущий CORR-12, но не покрытый случай.**

**Fix:** unified distinct states: timed known control + finished/no snapshot → «Контроль 3+2 · партия завершена; часы недоступны/остановлены», но **не «без часов»**; running timed missing snapshot → «ждём серверные часы»; correspondence/unlimited → honestly «без обычных часов» / days-per-turn; no invented milliseconds. **Red:** status mate + speed blitz + 180+2 + state None/white_ms None/black_ms None; same with state False, stale values, correspondence, unlimited, missing metadata; require exact UI labels. Validate GameFull-only reconnect and terminal gameFinish.

### POST-04 · P2/P1 UX · Review показывает стартовую позицию, но скроллит список на поздние ходы — CONFIRMED VISUAL + SOURCE

**Source PNGs** в exact artifact: `scale-1.5/1120x640-game-review.png`, `scale-1.25/1280x720-game-review.png`. В кадрах chessboard в **стартовой позиции**, заголовок «Начальная позиция · +0.00» и диаграмма на полуходе **0**, а список ходов прокручен к **строкам №5–8 / №6–8**. Ранние ходы `1.e4...` не видны; активный SAN визуально не соответствует положению доски. **Никакого доказательства пропажи/порчи партии нет** — это сбитое viewport/selection состояние.

**Source:** `ui/game_review.py:803–842::load_game` устанавливает `selected_ply=0` и начальную доску, `ui/move_table.py:271–293::select_ply(0)` при отсутствии item with ply0 вызывает **только `clearSelection()`**, не scroll-to-top; при других ply `PositionAtBottom` может оставлять контекст неудобным. Native capture `scripts/capture_visual_master.py:953–955` не утверждает, что видимые строки и текущая доска согласованы.

**Fix:** при выбранной начальной позиции (`ply=0`) явно прокрутить SAN table на первый видимый полный ход, без выделения ложного ply; при любом другом ply scrollToItem `EnsureVisible` или deterministic positioning plus selected-cell highlighting после queued layout; клавиатура, graph, previous/next, initial→last→initial. **Red:** loaded 17-ply review, initial board and row1 visible at 1120/1.5; select ply17 => SAN 9.h3 visibly selected + board position17, jump ply0 => row1 visible and board initial; late QTimer cannot revert viewport. Positive screenshot + Qt visualItemRect / scrollbar assertions.

### POST-05 · P1 UX / OWNER AUDIO GATE · «мгновенный звук хода» всё ещё не обеспечен; protocol-safe choice must be explicit — CONFIRMED REQUIREMENT GAP, не дефект подтверждения

**Source:** `window.py::on_move_intent` ставит `pending sound intent`, `lichess/service_games.py::send_move` подтверждает только после HTTP return; `window.py::_lichess_session_move_accepted` вызывает `sound_player.play_move` только на ACK. Новый `test_online_move_is_pending_immediately_but_confirmed_sound_waits_for_server` буквально требует `calls == []` **до** ACK при 0/0.5/1.5 seconds — это корректная антифальсификация **подтверждённого** звука, но **не решает исходную жалобу из аудио** на длинную задержку от клика до слышимого отклика. В UI pending-текст появляется сразу — это не звук.

**Decision/accept:** обсудить и реализовать (если UX contract требует именно слышимый моментальный отклик) два независимых сигнала: немедленный **neutral pending-input click** при действительно допустимом намерении и один истинный **confirmed move** после ACK; разные assets/meaning, no duplicate on stream, rejection/rollback no confirmed move, premove/castling/promotion. Если моментальный звук принципиально не разрешён выбранными настройками/продуктовым решением, **не заявлять VOICE-06 полностью исправленным**, задокументировать пользовательский trade-off и оставить физическую приёмку без подмены. Synthetic measured timing + real Windows audio on owner PC remain needed. Не запускать реальные сетевые шахматы для звукового теста без отдельного согласования.

### E.1 Что не нужно повторно «исправлять» и какие gate остаются открыты

- По новому ZIP **предыдущие gross-overlap/clipping Play/Training/Game/Review на 150% в основном устранены**, для последних 76 нативных кадра действительно существуют. Отличие трёх actual Home/Library/Background от approved art остаётся **эстетическим acceptance**, не доказанным corruption/invalid metric bug; согласовывает владелец. Не делать бесконечный refactor из-за пиксельного сходства.
- Результаты fake-only 894/894, CAS/lease heartbeat, 3510 archive selection, connected safety guards, revised SQLite schema v22 — настоящие полезные регрессии. В новой итерации добавить узкие тесты POST, не откатывать уже фиксированное. Не утверждать, что schema migration на личной SQLite/real Windows verified: источники данных синтетические.
- Physical DPI/real audio, PAT/OAuth/Lichess, реальные пользовательские SQLite/PGN и Stockfish/UCI всё ещё **NOT TESTED**. Это внешние gates после исправлений с разрешения владельца, не повод придумывать production failures сейчас. Fair Play live-human остаётся fail-closed.
- **До повторной code+visual проверки POST-01…04 — NO GO для final acceptance/merge.** POST-05 требует точного продукта/физического теста, не опровергает успешный CI.

### E.2 Один компактный Codex corrective cycle в существующем PR #44

**Base exact SHA `09766297a1ff0e5d960681116d8763959d1c871f`**, перед началом снова сверить current #44 HEAD; полный MASTER §15+16 и исторические CORR-01…16 сохранить. **ИСПРАВИТЬ ТОЛЬКО POST-01…04 и осмысленно закрыть/пометить POST-05** (а не заново все 16). Test-first `red` on reported exact edge → correct minimum production code change → `green`, no new PR/RF/VIS, no scope expansion, no roadmap/navigation-path work, no direct owner data/credentials/Stockfish, no main/merge.

**Required proof on updated exact SHA:** full Windows pytest (894 previous remain green + new tests), compileall/offline Qt, `git diff --check`, native screenshots at 100/125/150 incl **start review SAN row1 / selected last / reset**, manual single-game request fake transport counters 20+ due rows / A↔B identity, clock None vs False; three unchanged approved art pairs and compact populated surfaces. In PR report matrix per `POST-01…05 | before red test/PNG | code | after green | limitations` and CI+artifact. Review again independently before owner acceptance. **NO MERGE.**
