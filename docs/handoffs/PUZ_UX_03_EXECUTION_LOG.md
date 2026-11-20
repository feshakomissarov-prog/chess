# PUZ-UX-03 execution log

**PR:** Draft #52 — `codex/puz-ux-03-online-puzzle-playability-2026-11-20`
**Specification:** [`PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md`](../specs/PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md)
**Base:** `b9e4b72d578e98db2ba345959c8474028d304cdc`
**Code SHA:** `ed96ecfe29dd2c6d42e8fdf2802c92f3fcc9f571`
**Merge:** not performed

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
