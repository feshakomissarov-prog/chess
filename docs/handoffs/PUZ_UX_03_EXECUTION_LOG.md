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

- **PASS** [run 44731656492](https://github.com/feshakomissarov-prog/chess/actions/runs/44731656492), artifact [puz-ux-03-l2-34771ccf](https://github.com/feshakomissarov-prog/chess/actions/runs/44731656492/artifacts/14627185658), exact executable SHA `34771ccf7df04e9224c2122cba3bcb5d0f08921b`.
- Native public Qt page fetched and solved IDs `0haqU`, `00008`, `0000D` at each requested scale; each emitted 22 real Qt frames. The ready-state captures were opened and visually checked: `puz-ux-03-live-public/scale-1/00-...-live-ready.png` (100%), `scale-1.25/00-...-live-ready.png` (125%), and `scale-1.5/00-...-live-ready.png` (150%). Board, action buttons, scrollable theme/details panels, and Chess Coach palette remain legible; the public puzzle ID is `0haqU` in all three frames.
- MainWindow scenario used real public GET responses and an inert fake credential in isolated temporary SQLite. Its JSON confirms replay attempts `1 → 1`, local stats unchanged, rating `1500 → 1500`, first puzzle rating acknowledged at `1501` and still `1501` through auto-next, duplicate puzzle unrated, wrong→skip tape retained, cross-account replay remained selected after delayed worker release, and advanced OAuth difficulty `hardest → hardest`. Public GETs returned HTTP 200; the fake credential and authorization header were absent from requests.
- Opened MainWindow captures include `mainwindow-duplicate-unrated-after-ack.png`, `mainwindow-interactive-unrated-history-replay.png`, `mainwindow-skip-visible.png` (visible `✕` retained), and `mainwindow-advanced-hardest-prefetch.png` (new public position loaded, no spinner). Complete evidence is in the linked artifact under `puz-ux-03-mainwindow/`; local extracted review copy is `visual-evidence/section14-l2-34771ccf/`.
- Input timing from the Windows runner: 10 cold samples p50 `22.58 ms`, p95 `23.40 ms`; 50 warm samples p50 `3.58 ms`, p95 `4.13 ms`; maximum event-loop gap `29.53 ms`. MainWindow public GET samples ranged `172–342 ms`; the standalone live trace also records per-request times. These are runner measurements, not owner-machine results.
- OAuth authority remains **BLOCKED**: only the existing fake callback/credential path was exercised. No separate authorized test profile was available. No token/profile, owner SQLite, or owner computer was accessed.

### §14 L3 — pending

The first full Windows L3 was initiated on exact head `39c495912f04b42d3baf4bc89a385b5f421bc4a3` while preparing final acceptance. Its native visual and focused jobs passed, and the full suite reported **1248 passed, 1 failed**: `test_advanced_difficulty_prefetch_accepts_selected_key[hardest]`. The worker had emitted its completion event, but the assertion only waited for Python thread termination; Qt's queued completion could be delivered just after that condition became true. This was a synchronization defect in the test, not a confirmed production prefetch failure.

The test now waits for the observable contract (`page._prepared_online_puzzle` is installed) so Qt continues dispatching queued signals. The exact parameterized test passed once and in 10 further consecutive runs (**22/22 cases** total); `py_compile` and `git diff --check` passed. No production code changed after the successful L2 at `34771ccf7df04e9224c2122cba3bcb5d0f08921b`, so that real public-Lichess/native-Qt L2 and its 100/125/150% captures remain the thematic L2 evidence for the final executable code.

One final full exact-head Windows L3 remains pending on the test-harness + evidence-log HEAD. The PR was returned to Draft before this harness-only push; it will be briefly marked Ready only to trigger the final L3, then returned to Draft. No merge is authorized or performed.
