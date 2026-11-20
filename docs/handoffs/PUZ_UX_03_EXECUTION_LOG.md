# PUZ-UX-03 execution log

**PR:** Draft #52 — `codex/puz-ux-03-online-puzzle-playability-2026-11-20`
**Specification:** [`PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md`](../specs/PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md)
**Base:** `b9e4b72d578e98db2ba345959c8474028d304cdc`
**Final production-code SHA:** `c6ccb8aca7667caa2abc97e59208441768f15768` (latest head `6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac` adds only capture/workflow verification changes)
**Merge:** not performed

## §13 follow-up review evidence — 2026-11-20

This follow-up supersedes the earlier §13 verification snapshots below for the addressed behavior. Existing defect IDs were retained; no duplicate IDs were added.

- **Code SHA:** `c6ccb8aca7667caa2abc97e59208441768f15768` (last executable change; replay terminal text). The latest targeted test-only commit is `ed95127`; executable source was not changed afterward.
- **L1 GREEN:** [workflow run 44714071416](https://github.com/feshakomissarov-prog/chess/actions/runs/44714071416), Windows Python 3.12: **61 passed in 125.60 s**. A new replay-verdict test first failed against the preceding code SHA with **1 failed, 60 passed**, proving the inaccurate auto-next promise; the fix now reports “Повтор решён. Рейтинг не изменён.”
- **L2 PASS:** [workflow run 44714071416](https://github.com/feshakomissarov-prog/chess/actions/runs/44714071416), artifact [puz-ux-03-l2-c6ccb8ac](https://github.com/feshakomissarov-prog/chess/actions/runs/44714071416/artifacts/14619391283). The job ran real Qt on Windows, fetched and played public unauthenticated Lichess puzzles `0haqU`, `00008`, `0000D` (HTTP 200), ran the production `MainWindow` adapter scenario against those real public responses with isolated temporary SQLite, and captured the 100/125/150% display matrix. The artifact includes timestamped sequences for legal-wrong rollback, hints, opponent replies, win/auto-next, wheel review, saved-history replay, skip, controlled network failure, account switch, and hardest prefetch.
- **MainWindow evidence:** 10 public GETs, no Authorization header or credential sent. The successful initial save ACK and auto-next both showed CC rating `1501`; replay remained `1501` while the local attempt count advanced `1 → 2`. Skip reached `skipped`; controlled network failure stayed on an explicit Lichess error with no demo; account switch restarted a scoped request; prefetch retained `hardest`. Its fake profile/credential was only a harness adapter, not live OAuth.
- **Replay verdict:** the final native frame `puz-ux-03-mainwindow/10-…-mainwindow-interactive-unrated-history-replay.png` now explicitly says “Повтор решён. Рейтинг не изменён.” and shows SQLite save confirmation. It no longer promises auto-next.
- **Visual inspection:** opened real `live-ready` frames at 100%, 125%, and 150%, plus wrong-move, clean-win, wheel-review and replay-completion frames. The compact instruction is fully visible at each scale. Theme colors, board, actions, and side panel remain legible; the 125/150% layouts scroll the side pane vertically as intended. Full-resolution files are in the L2 artifact above; for example `puz-ux-03-live-public/scale-1.5/00-…-live-ready.png`, `puz-ux-03-live-public/scale-1.5/16-…-clean-win.png`, and `puz-ux-03-mainwindow/10-…-mainwindow-interactive-unrated-history-replay.png`.
- **Authorized OAuth:** `BLOCKED (no authorized separate test profile available)`. MainWindow uses fake local profile resolution and a fake credential marker; the HTTP adapter explicitly sends no credential. This verifies production page/signals/workers/save ACK/prefetch plumbing with public real puzzle responses, not authenticated batch/OAuth behavior.

### §13.2 first-input investigation

The earlier `2357.82 ms` first-feedback / `2358.47 ms` destination-click outlier was **not reproduced** in the latest Windows runner measurement. The dedicated trace covers **10 separate cold Qt processes** and **50 warm board inputs**, records focus, click dispatch, first paint, board paint, event-loop gaps, CPU, Python thread count, startup, page construction, and public GET duration, and does not infer causality.

| Measurement | Result | Interpretation |
| --- | --- | --- |
| Cold input → first painted feedback | n=10; p50 `30.75 ms`, p95/max `31.62 ms` | Board was focused; all ten below 100 ms. |
| Warm input → first painted feedback | n=50; p50 `5.44 ms`, p95 `6.22 ms`, max `31.86 ms` | Repeated real Qt input on a position fetched from Lichess. |
| Warm Qt event-loop gaps | p95 `17.00 ms`, max `39.44 ms` | No 2.3 s stall in this sample. |
| First cold process to feedback | `629.96 ms` total; public GET `387.60 ms`; page construct/show `103.75 ms`; input-to-paint `31.13 ms` | Separates setup/network time from the first board input. |

The old outlier remains **unattributed**: the new trace establishes that normal cold/warm board input is fast under these isolated runner conditions, but does not prove why the one earlier QtTest destination dispatch stalled. For this measured run, p95 is below the §13 gate of 100 ms; do not generalize it to owner hardware or live OAuth.

### §13 UX checklist, refreshed against final L2

| Question from §8.2 | Result | Evidence / limit |
| --- | --- | --- |
| Whose turn is it? | YES | Real public ready/reply frames show turn changes. |
| Was my move accepted, and can I see opponent reply? | YES | Multi-ply lines show accepted move, reply wait, then animated reply. |
| Was the move illegal or a legal puzzle mistake? | PARTIAL | Public legal-wrong move is red and rolls back; illegal-move distinction has addressable tests, not a separate public interaction. |
| Did the task finish and what next? | YES | Public clean win/auto-next, hinted completion, MainWindow skip, and explicit network error/no fallback captured. |
| Is result saving visible; can I continue? | YES in isolated integration | MainWindow save ACK and auto-next screenshots show SQLite success; fake delayed-DB tests cover nonblocking behavior. Owner DB was not used. |
| Which rating changes? | YES for local/replay contract; OAuth PARTIAL | CC rating stayed at the save ACK value through auto-next and replay did not change it; Lichess official rating is read-only. No authenticated profile was tested. |
| What happens after network failure; any demo fallback? | YES for controlled failure | MainWindow frame shows an explicit Lichess connection error, no demo; a real network outage/recovery was not induced. |
| Can I inspect previous moves with the wheel? | YES | Real public Qt frame/trace records wheel back and forward with review state; reducer tests cover the played-position limit. |
| What do red/green and streak mean? | YES for visible tape | Wrong/hinted/clean outcomes and streak counters are visible in native frames. |
| Is there a delay and at which stage? | PARTIAL / measured | Cold/warm input values above; the previous isolated 2.36 s dispatch remains unattributed. |

**Final Windows L3:** completed after this follow-up on exact head `6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac`; see the final record appended below. PR #52 was returned to Draft after CI; no merge was performed.

## Scope delivered in this pass

- The puzzle reducer records only known/pre-puzzle and already displayed positions for review; the page routes wheel and arrow-key review through those snapshots and can return to the authoritative position.
- Correct player moves and opponent replies are rendered separately using the existing `chess-widgets` piece animation. A legal wrong move is shown in red for 420 ms and then rolled back without changing the solution index. Hints have two visible stages.
- Clean online solves can auto-advance after 1250 ms. One authenticated puzzle is prefetched in the background; stale account/theme/difficulty responses are ignored, and a changed theme restarts prefetch after the cancelled request exits.
- The session tape and streak are independent of SQLite acknowledgement. Persistence/network status are separate. A successful database commit is acknowledged in the UI; save failures can be retried without replacing the win/loss verdict.
- SQLite schema v28 adds additive account-scoped practice rating and idempotent rating events. Historical/replayed tasks remain unrated, existing puzzle activity is retained, and Lichess's official rating is read-only.
- Ordinary training presents only online Lichess tasks or an explicit consent/connection state. Existing history and replay remain available through their existing navigation; replay is marked unrated. The Home saved-task card now labels its action “Открыть повтор” and states that rating/statistics do not change.
- Compact Home overview and KPI cards are allowed to shrink below their content size hints so the KPIs stay inside the 150%-DPI viewport; regression coverage asserts both cards fit.
- A one-shot live smoke script fetches the three exact public IDs `0haqU`, `00008`, and `0000D` sequentially without authorization, then exercises the production Qt puzzle page using mouse, wheel, annotation, hints, wrong-move rollback, full lines, opponent replies, and a clean-solve auto-next. It writes a credential-free public request trace and timestamped Qt frames. It is invoked only by the PUZ-UX-03 draft L2 job, not by pytest.

## Verification record

### L1 — targeted (Windows Python 3.12.10, isolated task venv)

Latest focused L1 before the final visual/L2 run: **19 passed in 9.20 s** (`test_puz_ux_03_interaction.py`, `test_puzzle_training_ui.py`, `py_compile`, `git diff --check`). The L2 job then reran the complete PUZ-UX-03 targeted set: **362 passed in 205.95 s**.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m py_compile <changed Python files>
python -X faulthandler -m pytest -o addopts= -q -W error::pytest.PytestUnhandledThreadExceptionWarning `
  tests/test_puzzle_training.py tests/test_puzzle_training_ui.py `
  tests/test_puzzle_training_navigation.py tests/test_puzzle_training_async.py `
  tests/test_puzzle_training_store.py tests/test_puzzle_api_adapter.py `
  tests/test_board_view.py `
  tests/test_window.py::test_theme_change_restarts_cancelled_prefetch_and_uses_selected_difficulty `
  tests/test_puz_ux_03_interaction.py tests/test_puz_ux_03_rating.py
git diff --check
```

The suite includes OAuth URL dispatch on the Qt GUI thread using a fake callback/token flow, plus fake GET delays of 100/1200/3000 ms and fake SQLite delays of 50/500/3000 ms. It does not access Lichess or a user database. The initial new acceptance tests were run against the pre-change implementation and produced the expected failures before the fixes.

### L2 — themed Windows Qt + live public Lichess

**PASS at final code SHA `ed96ecfe29dd2c6d42e8fdf2802c92f3fcc9f571`.** [Windows workflow run 44688377671](https://github.com/feshakomissarov-prog/chess/actions/runs/44688377671): targeted set **362 passed in 194.87 s**. Artifact [puz-ux-03-l2-ed96ecfe](https://github.com/feshakomissarov-prog/chess/actions/runs/44688377671/artifacts/14607532362) contains 59 real Qt captures/traces. Public unauthenticated GETs returned HTTP 200 for distinct IDs `0haqU`, `00008`, and `0000D`; real Qt flow exercised a legal wrong move and rollback, both hints, correct moves/opponent replies, hinted solve, clean win, auto-next, and a wheel/mark interaction. The script is isolated to this PR's Windows L2 job.

The three DPI screenshots were opened and visually inspected: 100% `1672x941-ready.png` (DPR 1.0), 125% `1280x720-ready.png` (physical `1600x900`, DPR 1.25), 150% `1120x640-ready.png` (physical `1680x960`, DPR 1.5). Branding/colors remain consistent; the shorter layouts use scrollable side panels and keep the board/actions visible. Live frames `01-...-wrong-visible.png` and `02-...-wrong-reverted.png` show the red transient followed by rollback; `16-...-clean-win.png` shows the success verdict and saving status. This pass also contains the Home compact KPI fit change found by native capture.

**Final UI/capture delta:** Home's saved-puzzle button now says “Открыть повтор” and describes the read-only/no-rating behavior; the shared visual capture now expects that replay rather than a playable offline trainer. Addressed before the final L2 pass; the exact-code live smoke and DPI captures above include this delta.

Intermediate verification findings: an earlier draft L2 on `91335c0` stopped before native/live capture at an existing `test_home_dashboard_refresh_is_background_and_drops_stale_generation` 1-second worker-start wait (`361 passed, 1 failed`); the exact test passed once standalone and five additional consecutive times locally. The next exact-head L2 passed. Earlier native visual jobs also exposed the Home CTA/replay mismatch and then the compact Home KPI overflow; the CTA was clarified and the KPI clipping was reproduced by a new RED test and corrected. These were fixed before this final green L2.

### L3 — full Windows CI

**PASS at exact head `21b4da198cdba2ea7111fc2f80f67462c108fa2c`** (code unchanged from the final L2 SHA; this head adds the evidence log only). [Windows workflow run 44689277033](https://github.com/feshakomissarov-prog/chess/actions/runs/44689277033), run 686: **1223 passed in 748.37 s** on Windows, Python 3.12.10 / Qt 6.12.0; compileall and isolated offline window smoke passed. The native `visual-evidence` job passed and uploaded [visual-master-21b4da198cdba2ea7111fc2f80f67462c108fa2c](https://github.com/feshakomissarov-prog/chess/actions/runs/44689277033/artifacts/14608426370). The 100%, 125%, and 150% captures from the final code SHA were opened and visually checked; see paths and observations in L2 above (code SHA is identical; only this log commit differs). The Home chart native capture job also passed, including the 1120×640 compact geometry regression. PR #52 was returned to Draft after the run; no merge was performed. If executable code changes after L3, repeat L2 and L3 for the new code SHA.

## Live access and UX acceptance

- **ONLINE-LIVE-PUBLIC:** PASS, unauthenticated only; see the L2 trace and frames linked above.
- **ONLINE-LIVE-OAUTH:** `BLOCKED (no authorized separate test profile available to this environment)`. The fake OAuth thread-affinity test passed; this is not an authenticated live test. No owner token/profile or credential store was read.
- **Owner machine / owner SQLite:** not accessed; no local Qt application was started.
- **UX checklist:** completed against the live-run frames/trace and synthetic-only fixture captures below. This is not an overall UX acceptance: partial/negative items remain explicitly marked.

| UX question from §8.2 | Result | Evidence |
| --- | --- | --- |
| Whose turn is it? | YES | Live ready/wrong frames show “Ваш ход”; live replies switch to the player's turn (frames 00, 06, 08). |
| Was my move accepted, and can I see the opponent reply? | YES | Live frames 05–08 and 12–15 show accepted player move, separate reply wait, and opponent move; production animation is visible. |
| Was the move illegal or a legal puzzle mistake? | PARTIAL | Real legal-but-wrong move is red and rolls back (frames 01–02). Illegal chess moves are distinguished by addressable Qt tests, not a live puzzle interaction. |
| Is the task finished, and what can I do next? | YES for solve/next; PARTIAL for a loss | Live hinted solve frame 09, clean win frame 16, and auto-next frame 17. A dedicated live skip/loss path was not exercised. |
| Is the result saving, and can I continue while it saves? | PARTIAL (synthetic persistence evidence) | DPI fixture captures show pending/save-error states; fake-delay tests cover 50/500/3000 ms SQLite responsiveness. No owner DB or live authenticated account was used. |
| Which rating changes? | PARTIAL | UI separates read-only official Lichess rating and CC practice rating; 100/125/150% fixture screens show this. Authenticated online rating sync was not available. |
| What happens after network failure; is there any demo fallback? | PARTIAL | No-token consent and fake failure/no-fallback tests pass; no live network disconnect/reconnect was induced. No offline/demo puzzle was used as an online substitute. |
| Can I use the wheel to inspect prior moves? | PARTIAL | Live wheel interaction emitted review status on frame 11; reducer/navigation tests verify only played positions and return-to-current. Full manual back-and-forward through multiple live moves was not captured. |
| What do the green/red results and streak mean? | YES in the visible session tape | Live red X on hinted solve, green check on clean solve, and streak/clean count on frame 16; no authenticated rating implication is claimed. |
| Where is the delay? | PARTIAL, with measurements | API/prepared-next/paint and move stages measured below. The first automation source-to-destination sample is a 3.09 s outlier; the runner trace cannot establish whether it reflects real-user input latency or automation dispatch delay. |

## Timing evidence

Values are intentionally left blank until collected from actual run output/artifacts; synthetic timings will be identified as such.

| Stage | Measurement | Conditions / source |
| --- | --- | --- |
| Input → first painted feedback | warm-input p50 `6.67 ms` (8 samples, range `4.76–17.88 ms`); p95 `2357.82 ms` including first interaction outlier | live Qt trace. First source→destination automation sample `2358.47 ms`, destination→state `11.97 ms`; event-loop gap max `2374.68 ms`, p95 `66.90 ms`; board paint max `10.03 ms`. The cause of the first-action outlier is not confirmed; report it as unresolved instead of attributing it solely to UI or the automation harness. |
| Wrong-move visibility | `438.58 ms` | live frame/timing trace; feedback painted then rolled back. |
| SQLite commit | 100%: `42.20 ms`; 125%: `52.88 ms`; 150%: `41.79 ms` | synthetic visual capture fixture, isolated temporary DB; not owner DB. |
| Public Lichess GET | p50 `138.96 ms`; p95/sample max `603.04 ms` | real sequential unauthenticated GETs: `0haqU` 603.04, `00008` 128.35, `0000D` 138.96 ms; all HTTP 200. |
| Prepared next position visible | `0.0166–0.0198 ms` | synthetic capture fixture handoff. |
| Live auto-next | `1257.34 ms` | real Qt timestamps: clean win 06:35:25.628383Z → next public puzzle ready 06:35:26.885724Z. |
| Fake SQLite delay responsiveness | 50/500/3000 ms cases | targeted pytest; GUI event loop remains live |
| Fake GET delay responsiveness | 100/1200/3000 ms cases | targeted pytest; GUI event loop remains live |

## Final §13 verification — exact head 6e2464f (2026-11-20)

- **L1:** [focused workflow run 44716254768](https://github.com/feshakomissarov-prog/chess/actions/runs/44716254768) passed on exact head `6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac`; targeted RED/GREEN contracts and compile checks passed. The workflow's PUZ L2 job was correctly skipped on this test-harness-only delta; the immediately preceding production-code L2 on `c6ccb8a` is recorded above.
- **L2:** PASS on production-code SHA `c6ccb8aca7667caa2abc97e59208441768f15768`, [run 44714071416](https://github.com/feshakomissarov-prog/chess/actions/runs/44714071416), artifact [puz-ux-03-l2-c6ccb8ac](https://github.com/feshakomissarov-prog/chess/actions/runs/44714071416/artifacts/14619391283). Its real public Lichess + Qt screenshots were opened and visually checked at 100%, 125%, and 150% DPI, including replay completion and the corrected instruction line.
- **L3:** PASS on exact head `6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac`, [Windows run 44716659563](https://github.com/feshakomissarov-prog/chess/actions/runs/44716659563). Windows Server 2025 / Python 3.12.10: **1232 passed in 510.35 s**; compileall and isolated offline `MainWindow` smoke passed. Both Windows native jobs passed, including the complete visual capture suite and puzzle trainer at 100%, 125%, and 150% DPI. The run's visual artifact is [visual-master-6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac](https://github.com/feshakomissarov-prog/chess/actions/runs/44716659563/artifacts/14620556355); Home chart capture artifact is [home-chart-l1-l2-6e2464fd9bfe1b6a9becb3329b18ba6d70a95fac](https://github.com/feshakomissarov-prog/chess/actions/runs/44716659563/artifacts/14620086334).
- The 2357.82 ms first-input outlier remains unattributed; the separate measured cold/warm input trace and its limits are documented in §13.2 above.
- OAuth remains `BLOCKED` because no authorized separate test profile is available. Public puzzle data was unauthenticated; owner SQLite, OAuth store, and computer were not accessed. PR #52 is **Draft**, open, and unmerged.

## Final §14 follow-up — six missed scenarios (2026-11-20)

**Executable code SHA:** `34771ccf7df04e9224c2122cba3bcb5d0f08921b`. Existing defect IDs are retained; no duplicate issue IDs were added.

| Existing ID | Result on this pass | Evidence |
| --- | --- | --- |
| **PUZ03-11.ONLINE-ONLY-UX** | FIXED: a history replay stays interactive but does not emit/save a normal attempt, change the local result tape, streak/counters, local attempts/success statistics, or CC practice rating. UI says `Повтор не записывается`. | RED before fix: `test_training_replay_does_not_change_attempt_totals_statistics_or_rating` and page replay tests. GREEN after fix: targeted L1 and MainWindow L2 cross-account history replay; before/after account B attempts `1 → 1`, stats `{attempts: 1, solved: 1, success_percent: 100.0}` unchanged, rating `1500 → 1500`. |
| **PUZ-07.ACCOUNT-BOUND** | FIXED: programmatic cross-account replay invalidates delayed account-list callbacks, blocks the selector signal, and restores that account's auto-next preference. | RED delayed-worker test reproduced the competing online request. GREEN MainWindow L2 replayed the saved B row while B's account worker was held, released it, and kept puzzle `0haqU` in replay. The same run then switched back to A and loaded a scoped task. |
| **PUZ03-08.PREFETCH / PUZ03-11.ONLINE-ONLY-UX** | FIXED: the authorize control passes `selected_difficulty()`; advanced values survive the fake callback to the Lichess batch request. | RED Qt control test showed easiest/hardest collapsed to normal. GREEN parameterized Qt + fake OAuth callback tests cover `easiest`, `normal`, `hardest`; L2 batch trace's final request was `hardest`, public GET 200, and its screenshot shows the newly applied `0haqU` position. OAuth token was fake and was not sent in the public HTTP request. |
| **PUZ03-09.STREAK** | FIXED: a first wrong move remains a red `✕` if the user later skips or reveals; clean skip and hint-then-skip remain neutral. Error and streak counters increment once. | RED wrong→skip/reveal tests observed the row overwritten with `–`. GREEN parametrized Qt tests and MainWindow L2 wrong→skip tape (`✓ ✓ ✕`); the frame shows the skip verdict and retained red mark. |
| **PUZ03-10.RATING** | FIXED: ACKed account+puzzle IDs are cached on the page; stale prepared items are invalidated whether their callback arrives before or after the ACK. Duplicate puzzle does not preview a second Δ and cannot create another rating event. | RED duplicate-prefetch test observed false `+10`. GREEN tests place the ACK before late prepared delivery; L2 adapter deterministically returns `0haqU` twice, waits for the first save ACK, then shows the duplicate with no delta. A completed duplicate solve records one further local attempt but rating event count remains one. |
| **PUZ03-07.SAVE-NONBLOCK** | FIXED: malformed legacy JSON (`{broken`, `[]`, `null`) is safely stored without rating; valid JSON still rates normally. | RED repository tests reproduced `UnboundLocalError`. GREEN three malformed cases plus valid payload in targeted L1; no schema change. |

### §14 L1 — addressable RED→GREEN

- Local isolated L1 first reproduced the six regressions. After fixes the focused interaction/store/UI/MainWindow tests passed; one old replay test still expected attempts to be written, was corrected to the §14 no-statistics contract, and the thematic local set passed **74 tests in 38.30 s**. OAuth callback extension separately passed **10 tests in 2.30 s**. All commands used the task venv and offscreen Qt; no owner profile or SQLite was opened.
- Windows focused run [44731656492](https://github.com/feshakomissarov-prog/chess/actions/runs/44731656492), exact code SHA `34771ccf7df04e9224c2122cba3bcb5d0f08921b`: **78 passed in 72.00 s**, `py_compile` passed.
- An earlier first attempt at native L2 [44729993712](https://github.com/feshakomissarov-prog/chess/actions/runs/44729993712) passed L1 and the standalone live puzzle pages, but MainWindow capture stopped on an obsolete harness expectation (`00008` instead of the intentionally repeated `0haqU`). The expectation was corrected; another visual review also tightened the wait until the OAuth response had actually loaded. It was not counted as a completed L2.

### §14 L2 — themed Windows Qt and public Lichess

- **PASS** [run 44735699544](https://github.com/feshakomissarov-prog/chess/actions/runs/44735699544), artifact [puz-ux-03-l2-c7a30195](https://github.com/feshakomissarov-prog/chess/actions/runs/44735699544/artifacts/14628398454), exact code/test SHA `c7a30195d81695bb8040f9eec6c68beec37bf7e1`. L1 in the same run: **78 passed in 175.66 s**, py_compile passed.
- Native public Qt page fetched and solved IDs `0haqU`, `00008`, `0000D` at 100%, 125%, and 150% DPI, with 22 real Qt frames per scale. Opened and visually checked the ready-state frames at all three scales: `puz-ux-03-live-public/scale-1/00-2026-11-20T123043.879037+0000-live-ready.png`, `scale-1.25/00-2026-11-20T123059.315888+0000-live-ready.png`, and `scale-1.5/00-2026-11-20T123105.351751+0000-live-ready.png`. The board, action row, Chess Coach palette, and task details remain visible; at larger scales the task list adapts to a searchable selector and the right pane scrolls rather than clipping its content.
- MainWindow scenario used real public GET responses and an inert fake credential in isolated temporary SQLite. Its JSON confirms replay attempts `1 → 1`, statistics unchanged, replay rating `1500 → 1500`, current rating `1501` before and after auto-next, duplicate ID ineligible for another rating delta, wrong→skip kept the red mark, account-switch request restarted, and advanced OAuth difficulty `hardest → hardest`. Public requests returned HTTP 200; no authorization header or fake credential was sent. The fake OAuth flow is not authenticated-user acceptance.
- Opened MainWindow captures from this run: `06-...-mainwindow-duplicate-unrated-after-ack.png` shows no duplicate rating preview; `15-...-mainwindow-interactive-unrated-history-replay.png` shows the completed interactive replay and “Повтор не записывается” while retaining CC rating `1500`; `16-...-mainwindow-skip-visible.png` shows the skipped outcome, red `✕`, and pending save; `20-...-mainwindow-advanced-hardest-prefetch.png` shows “Самая сложная” and the loaded public position. Local extracted review copy: `visual-evidence/section14-l2-c7a30195/` (untracked; CI artifact is the durable evidence).
- Timing from this Windows runner: 10 separate cold input probes p50 `17.65 ms`, p95 `18.69 ms`; 50 warm inputs p50 `2.59 ms`, p95 `2.88 ms` (max `16.28 ms`); warm event-loop gap max `22.16 ms`. Real public GET p50/p95 were `211.65/447.58 ms` on the standalone trace. However, the first live automation interaction again has an outlier: source→destination `9087.26 ms`, first paint `9086.7 ms`, max event-loop gap `9151.23 ms`; the other samples and separate input probe are fast. Cause is **unresolved** and not attributed to the UI or harness. Runner timings are not owner-machine results.
- OAuth authority remains **BLOCKED**: only the existing fake callback/credential path was exercised. No separate authorized test profile was available. No token/profile, owner SQLite, or owner computer was accessed.

### §14 L3 — final acceptance

The first full Windows L3 was initiated on exact head `39c495912f04b42d3baf4bc89a385b5f421bc4a3` while preparing final acceptance. Its native visual and focused jobs passed, and the full suite reported **1248 passed, 1 failed**: `test_advanced_difficulty_prefetch_accepts_selected_key[hardest]`. The worker had emitted its completion event, but the assertion only waited for Python thread termination; Qt's queued completion could be delivered just after that condition became true. This was a synchronization defect in the test, not a confirmed production prefetch failure.

The test now waits for the observable contract (`page._prepared_online_puzzle` is installed) so Qt continues dispatching queued signals. The exact parameterized test passed once and in 10 further consecutive runs (**22/22 cases** total); `py_compile` and `git diff --check` passed. No production code changed after the successful L2 at `34771ccf7df04e9224c2122cba3bcb5d0f08921b`, so that real public-Lichess/native-Qt L2 and its 100/125/150% captures remain the thematic L2 evidence for the final executable code.

The final full Windows L3 **PASSED** at exact evidence HEAD `f48d9e3c84b7e19a09e11c2f35300489860d76d8` (executable code/test SHA `c7a30195d81695bb8040f9eec6c68beec37bf7e1`): [run 44737135223](https://github.com/feshakomissarov-prog/chess/actions/runs/44737135223). Windows Server 2025 / Python 3.12.10 / Qt 6.12.0: **1249 passed in 541.49 s**, then `compileall` and isolated offline MainWindow smoke passed; the required Windows native visual job passed and uploaded [visual-master-f48d9e3](https://github.com/feshakomissarov-prog/chess/actions/runs/44737135223/artifacts/14628524875). I opened the native trainer ready-state captures at 100%, 125%, and 150% and verified their rendered layout. PUZ-specific real-public-Lichess + live Qt captures were separately inspected in the passing L2 artifact above; these L3 frames are the standard fixture-based visual matrix.

There were two complete Windows runs in this §14 acceptance cycle: the earlier run on `39c4959` found one failure after 1248 passes in the test's completion wait; the final run above passed after the harness was changed to wait for the Qt-visible prepared puzzle. This second full run was required to verify the corrected test on the final head; no full suite was rerun during the small implementation edits.

Final status: PR #52 remains **Draft**, open, and unmerged. No owner computer, owner SQLite, or OAuth profile was accessed. The authorized OAuth profile remains unavailable; the recurring ~9.09 s first live automation interaction outlier is still **unresolved and unattributed**, despite fast separate cold/warm input probes. No new defect IDs were added. No merge is authorized or performed.

## §15 consolidated follow-up — six scenarios (2026-11-20)

All six cases from §15.2 were handled in one code/test package on the existing PR #52 branch. Existing defect IDs were retained; no new debt IDs were created.

| Existing ID | RED → FIXED → PASS evidence |
| --- | --- |
| **PUZ03-07.SAVE-NONBLOCK** — post-COMMIT false failure | RED tests reproduced failures in the malformed-payload/read-model ACK path. Fixed theme extraction and separated durable commit acknowledgment from optional read-model enrichment. Final targeted L1 passed; MainWindow integration confirmed the committed row receives a success ACK. |
| **PUZ03-07.SAVE-NONBLOCK** — pending/failed attempt durability | RED MainWindow lifecycle tests reproduced missing retry after page/account transitions and restart. Added an additive SQLite v29 attempt outbox, scoped restore/retry, idempotent ACK cleanup, and explicit close decision when durable enqueue itself fails. Final L1 and L2 account A→B→A/reopen recovery passed. |
| **PUZ03-10.RATING** — account-scoped official rating | RED page/MainWindow coverage reproduced stale official rating on switching to a profile with no data. Account reset now clears official rating/date before the scoped read. L2 confirmed no-data state and account-specific local rating. |
| **PUZ-07.ACCOUNT-BOUND / PUZ03-08.PREFETCH** — separate puzzle credential identity | RED two-identity tests reproduced gaming-account/puzzle-token mismatch and token rotation. Prefetch now requires the validated puzzle credential identity and rejects stale token-generation callbacks. Fake-credential tests passed; no credential was sent in public GET requests. |
| **PUZ03-08.PREFETCH / PUZ03-05.NEXT** — Next single-flight | RED delayed-fetch tests reproduced duplicate requests from repeated Next clicks. Added a per-fetch gate and restore behavior after completion/failure. Qt delayed GET/retry/429 coverage and L2 MainWindow screenshots passed. |
| **PUZ03-09.STREAK** — reducer history isolation | RED reducer case showed puzzle A events remained after loading B. `load` now resets per-puzzle history while the session result tape remains intact. Reducer and Qt result-history tests passed. |

### §15 L1 — targeted RED→GREEN

- RED reproductions were recorded in Windows runs [44789050959](https://github.com/feshakomissarov-prog/chess/actions/runs/44789050959) and [44790305318](https://github.com/feshakomissarov-prog/chess/actions/runs/44790305318). They exposed the expected six scenario failures and account/lifecycle integration gaps before the fixes.
- Final Windows targeted run [44801146864](https://github.com/feshakomissarov-prog/chess/actions/runs/44801146864), exact code/test SHA `63d53cda8980891c8cea14c7e73b655f69d65878`: **91 passed in 100.42 s**; changed modules/tests compiled. The workflow also ran the single themed L2 described below.
- An earlier full L3 at `304c2e150bbd9025661d8c14a29491e4548705dc` reached **1260 passed, 2 failed** because two pre-existing schema migration tests still asserted schema version 28 after the additive migration to 29. Both assertions were updated; the two exact tests passed locally (**2 passed**) before the final L3. This was the only reason for a final full rerun.

### §15 L2 — one themed Windows Qt run with real public Lichess data

- **PASS** [run 44801146864](https://github.com/feshakomissarov-prog/chess/actions/runs/44801146864), artifact [puz-ux-03-l2-63d53c](https://github.com/feshakomissarov-prog/chess/actions/runs/44801146864/artifacts/14661866201), exact code/test SHA `63d53cda8980891c8cea14c7e73b655f69d65878` (artifact SHA256 `5eac10021f1f8751edf5b37f1722f956576b60b649a11709aa7474a01000d418`). Actual native Qt sessions used public Lichess puzzles at 100%, 125%, and 150% DPI. The frames include ready, wrong-visible/reverted, both hint stages, solved-after-hint, and next puzzle. I opened and visually checked the ready frames at all three scales, plus MainWindow's outbox-recovery frame.
- The same run drove MainWindow with real public GET puzzle responses and isolated temporary SQLite. It verified repeat has no attempt/statistics/rating change (`1→1`, `1500→1500`), auto-next keeps CC rating `1501`, duplicate IDs remain unrated, official rating clears on an account with no history, wrong→skip retains its red result, account-switch restarts the scoped request, advanced `hardest` prefetch works, outbox recovery remains account-scoped, and Next is single-flight. 12 public GETs returned HTTP 200; no Authorization header or fake credential was sent.
- Request/interaction timing in the same Windows runner: 10 cold UI input samples p50/p95 `28.58/30.51 ms`; 50 warm input samples p50/p95 `5.46/6.57 ms`, event-loop maximum gap `36.48 ms`. Individual public GETs in the MainWindow trace were roughly `175–348 ms`. These timings characterize the hosted runner, not the owner's device. The previous first-interaction outlier remains **unattributed**; these measurements do not establish its cause.
- An automatic synchronize-triggered L2 in this same workflow was the one intended thematic L2; no separate manual visual rerun was added. No authorized OAuth test profile was available: `ONLINE-LIVE-OAUTH: BLOCKED`. MainWindow integration used an inert fake credential and unauthenticated public GET only; it is not authenticated-user acceptance.

### §15 L3 — final full Windows CI, exact code/test SHA

- **PASS** [run 44801169562](https://github.com/feshakomissarov-prog/chess/actions/runs/44801169562), exact code/test SHA `63d53cda8980891c8cea14c7e73b655f69d65878`: Windows Server 2025 / Python 3.12.10, **1262 passed in 926.02 s (15:26)**; `compileall`, isolated offline MainWindow smoke, and required `visual-evidence` job all passed. The native trainer matrix captured 100%, 125%, and 150% DPI. Artifact: [visual-master-63d53c](https://github.com/feshakomissarov-prog/chess/actions/runs/44801169562/artifacts/14661727233).
- The L3 workflow's PUZ-specific L2 job was skipped because the PR was marked Ready while dispatching the required exact-head CI; the dedicated L2 run immediately before it had already passed on the same exact code/test SHA. The PR was returned to **Draft** after the run. No code changed after this L3; the remaining journal/spec/PR-description edits are Markdown-only and do not trigger full Windows CI under the workflow path filters.

### Handoff and limitations

- §15.2 statuses are **RED → FIXED → PASS** for all six cases; the matrix and reviewer decision below are updated accordingly. Owner's authorized OAuth profile and machine-specific acceptance remain **NOT TESTED**. No owner SQLite, OAuth store, or computer was accessed.
- PR #52 remains open, Draft, and unmerged. Handoff is for one consolidated independent reacceptance; no merge was performed.

## §16 consolidated follow-up — four scenarios (2026-11-20)

All four §16 scenarios were handled together in the same Draft PR #52. Existing defect IDs were retained; no new IDs or debt entries were created. Final executable/test SHA: `1ddb81c7a2a4b653c263d587d67932f3ae7abdc8`.

| Existing ID / scenario | RED → FIXED → evidence |
| --- | --- |
| **PUZ03-10.RATING / PUZ-07.ACCOUNT-BOUND** — account switch while scoped data is loading | RED reproduced stale A rating/history before B callback. Account reset now clears scoped metrics/history immediately, and loading/failure stays explicit. L1/L2/L3 PASS; three DPI frames inspected. |
| **PUZ03-07.SAVE-NONBLOCK P1** — outbox failure during shutdown | RED reproduced close-path race. Thread-safe attempt save states now feed close confirmation independently of queued Qt delivery. L1/L2/L3 PASS. |
| **PUZ-07.ACCOUNT-BOUND / PUZ03-08.PREFETCH** — credential changes after result emit | RED reproduced stale first-fetch/prefetch apply. A background credential fingerprint recheck now gates GUI application without exposing key material. L1/L2/L3 PASS; authorized OAuth BLOCKED. |
| **PUZ03-07.SAVE-NONBLOCK P2** — account snapshot reattaches ACKed outbox row | RED reproduced stale retry UI after ACK. ACK IDs are tracked before queued callback and snapshot is reconciled at read/apply boundaries. L1/L2/L3 PASS. |

### §16 L1 — targeted RED→GREEN

- RED on baseline `6aecb2056342c5f714987723210e9b62a336e59e`: **5 failed in 4.14 s** across four scenarios, with token-rotation parameterized into two cases.
- An intermediate focused Windows L1 on `ea069b5` exposed a real shutdown race when a delayed GET completed as the coordinator was stopping. The worker shutdown guard was fixed in `ccb5fa4` and the focused CI passed.
- Final focused Windows run [44812359325](https://github.com/feshakomissarov-prog/chess/actions/runs/44812359325): **96 passed** on final code/test SHA. Final local addressable tests: **14 passed in 9.39 s**; changed modules compiled and `git diff --check` passed.
- The first intermediate L2 exposed a fake OAuth adapter that returned a token without updating its fake keyring. This was a harness issue; adapter and OAuth test were corrected before the final L2.

### §16 L2 — one themed Windows Qt run

- **PASS** [run 44812359325](https://github.com/feshakomissarov-prog/chess/actions/runs/44812359325), [artifact 14668582153](https://github.com/feshakomissarov-prog/chess/actions/runs/44812359325/artifacts/14668582153). Real public puzzle GETs (`0haqU`, `00008`, `0000D`) returned HTTP 200 and were opened in native Qt/MainWindow flows using isolated temporary SQLite and fake credentials; no auth header was sent.
- Actual Windows Qt account-switch-loading and ready frames were captured and opened at 100%, 125%, and 150% DPI. The loading frames show no stale A rating/history while B loads; branding/colors remain in place and the side panel is scrollable at the larger scales. Save-error and OAuth-consent frames were also checked at 150%.
- Runner input timing: cold p50/p95 `17.96/19.25 ms` (10), warm `2.96/3.38 ms` (50), maximum warm event-loop gap `24.63 ms`; nine public GETs took `177.65–612.69 ms`. The first interaction outlier near `9.09 s` remains unresolved and unattributed.

### §16 L3 — final exact-code Windows CI

- **PASS** [run 44813478950](https://github.com/feshakomissarov-prog/chess/actions/runs/44813478950), exact executable/test SHA `1ddb81c7a2a4b653c263d587d67932f3ae7abdc8`: Windows Server 2025 / Python 3.12.10, **1267 passed in 622.63 s (10:22)**. `compileall`, isolated offline MainWindow smoke, and required native visual evidence all passed. [Visual artifact 14668189027](https://github.com/feshakomissarov-prog/chess/actions/runs/44813478950/artifacts/14668189027).
- I opened and inspected the final runner account-switch-loading Qt screenshots at [100%](C:/Users/Михаил/AppData/Local/Temp/visual-master-1ddb81c7/puzzle-training/scale-1/1672x941-account-switch-loading.png), [125%](C:/Users/Михаил/AppData/Local/Temp/visual-master-1ddb81c7/puzzle-training/scale-1.25/1280x720-account-switch-loading.png), and [150%](C:/Users/Михаил/AppData/Local/Temp/visual-master-1ddb81c7/puzzle-training/scale-1.5/1120x640-account-switch-loading.png). These are actual Windows Qt runner captures. The separate focused L2 provided the real-public-Lichess data path on the same code/test SHA.
- PR #52 remains Draft and unmerged. Authorized test OAuth profile was unavailable (`ONLINE-LIVE-OAUTH: BLOCKED`); owner SQLite, OAuth store, and computer were not accessed. Owner acceptance remains pending. Documentation-only updates after this exact-code L3 do not alter executable/test SHA; one independent consolidated reacceptance is requested.

## §17 consolidated follow-up — three scenarios (2026-11-21)

Completed the three §17 cases as one package in Draft PR #52. Existing IDs only: **PUZ03-08.PREFETCH**, **PUZ-07.ACCOUNT-BOUND**, **PUZ03-07.SAVE-NONBLOCK**, **PUZ03-05.NEXT**. No new defect IDs or follow-up debt were added. Final executable/test SHA: `450bc4a210c4edf97a0a725aef3d129b649c55ac`.

### L1 — RED → FIXED → GREEN

- Baseline RED [44820184382](https://github.com/feshakomissarov-prog/chess/actions/runs/44820184382): **7 failed, 96 passed**. It reproduced current-task lockout after prefetch credential failure, a second same-ID save worker, and stale prepared results after scope changes.
- A following focused attempt [44821250083](https://github.com/feshakomissarov-prog/chess/actions/runs/44821250083) had **5 failed, 98 passed** from harness mismatch only: changed non-blocking notice wording, missing rating in test payload, and a mock signature that did not accept `on_invalid`. The account-switch parameter in the first attempt was also a false positive because that test bypassed the real validator; the production generation guard was then tested directly and discarded the stale callback.
- Final Windows L1 [44823084863](https://github.com/feshakomissarov-prog/chess/actions/runs/44823084863), exact code/test SHA above: **103 passed in 80.37 s**, changed modules/tests compile passed. Local new-case node groups: **7 passed in 4.78 s**; `git diff --check` passed.
- Fixes: speculative prefetch failures report via non-blocking network status/authorization CTA while preserving active reducer/board and attempt scope; initial-fetch errors remain fail-closed. Same-attempt saves are single-flight and state updates are tied to the captured worker state, so a post-COMMIT ACK cannot create a competing retry failure. Prepared puzzle scope is revalidated at the final asynchronous apply boundary.

### L2 — one thematic native Windows Qt/MainWindow run

- **PASS** [44823084863](https://github.com/feshakomissarov-prog/chess/actions/runs/44823084863), [artifact 14672593269](https://github.com/feshakomissarov-prog/chess/actions/runs/44823084863/artifacts/14672593269). The MainWindow integration used public Lichess puzzle GETs `0haqU`, `00008`, `0000D` (HTTP 200), fake inert credential, and isolated temporary SQLite. No Authorization header or credential was sent.
- MainWindow trace confirmed the active public puzzle ID/FEN/status/account remained unchanged after stale credential validation, with `board_enabled=true` and `allow_submit=true`. A barrier around durable enqueue plus same-ID retry confirmed one worker, one row, no extra rating event, successful SQLite acknowledgement, and no credential in trace.
- Actual Qt frames for prefetch-error/current-task-actionable were opened at 100%, 125%, and 150%; the 100% MainWindow frame showed real public puzzle `0haqU` with the non-blocking error. Save-retry state was checked at 150%. Evidence retained in CI artifact; local inspection copy is untracked at `visual-evidence/section17-l2-450bc4a/`.
- Timing: 10 cold probes p50/p95/max `25.67/26.40/26.40 ms`; 50 warm probes `5.02/5.50/25.40 ms`; max warm event-loop gap `32.99 ms`. The live public UI trace still shows an isolated `2772 ms` first-input sample at 100% and `2801.91 ms` maximum event-loop gap. Cause is unresolved and unattributed; no claim of performance fix is made.

### L3 — final exact-head Windows CI

- **PASS** [Windows run 44824004020](https://github.com/feshakomissarov-prog/chess/actions/runs/44824004020), exact executable/test SHA `450bc4a210c4edf97a0a725aef3d129b649c55ac`: Windows Server 2025 / Python 3.12.10, **1274 passed in 633.28 s (10:33)**, compileall and isolated offline MainWindow smoke passed. Native `visual-evidence` passed and uploaded [artifact 14673412032](https://github.com/feshakomissarov-prog/chess/actions/runs/44824004020/artifacts/14673412032). The PUZ-specific L2 job was skipped on the Ready-for-review L3 event; its dedicated run had passed immediately beforehand on the same code/test SHA.
- Opened and visually inspected L3 ready-state captures at 100%, 125%, and 150% DPI, plus all three L2 prefetch-error frames and a live-public MainWindow frame. Chess Coach styling, board, action row, side-panel scrolling, and non-blocking network notice were present.
- No authorized OAuth profile was available (`ONLINE-LIVE-OAUTH: BLOCKED`). No owner SQLite, OAuth store, or computer was accessed. PR #52 remains open Draft and unmerged. Markdown-only evidence updates follow the L3; the handoff is for one consolidated independent reacceptance.
