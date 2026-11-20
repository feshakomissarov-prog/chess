# PUZ-UX-03 execution log

**PR:** Draft #52 — `codex/puz-ux-03-online-puzzle-playability-2026-11-20`
**Specification:** [`PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md`](../specs/PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md)
**Base:** `b9e4b72d578e98db2ba345959c8474028d304cdc`
**Code SHA:** `cead2ace37d5913314ead22de784957a90a48018`
**Merge:** not performed

## Scope delivered in this pass

- The puzzle reducer records only known/pre-puzzle and already displayed positions for review; the page routes wheel and arrow-key review through those snapshots and can return to the authoritative position.
- Correct player moves and opponent replies are rendered separately using the existing `chess-widgets` piece animation. A legal wrong move is shown in red for 420 ms and then rolled back without changing the solution index. Hints have two visible stages.
- Clean online solves can auto-advance after 1250 ms. One authenticated puzzle is prefetched in the background; stale account/theme/difficulty responses are ignored, and a changed theme restarts prefetch after the cancelled request exits.
- The session tape and streak are independent of SQLite acknowledgement. Persistence/network status are separate. A successful database commit is acknowledged in the UI; save failures can be retried without replacing the win/loss verdict.
- SQLite schema v28 adds additive account-scoped practice rating and idempotent rating events. Historical/replayed tasks remain unrated, existing puzzle activity is retained, and Lichess's official rating is read-only.
- Ordinary training presents only online Lichess tasks or an explicit consent/connection state. Existing history and replay remain available through their existing navigation; replay is marked unrated.
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

**PASS at exact code SHA `abed33e3f90c9537cb6f526cc9998fa960e73318`.** [Windows workflow run 44683982062](https://github.com/feshakomissarov-prog/chess/actions/runs/44683982062), targeted job passed and artifact [puz-ux-03-l2-abed33e3](https://github.com/feshakomissarov-prog/chess/actions/runs/44683982062/artifacts/14606210276) contains 59 real Qt captures/traces. Public unauthenticated GETs returned HTTP 200 for distinct IDs `0haqU`, `00008`, and `0000D`; real Qt flow exercised a legal wrong move and rollback, both hints, correct moves/opponent replies, hinted solve, clean win, auto-next, and a wheel/mark interaction. The script is isolated to this PR's Windows L2 job.

The three DPI screenshots were opened and visually inspected: 100% `1672x941-ready.png` (DPR 1.0), 125% `1280x720-ready.png` (physical `1600x900`, DPR 1.25), 150% `1120x640-ready.png` (physical `1680x960`, DPR 1.5). Branding/colors remain consistent; the shorter layouts use scrollable side panels and keep the board/actions visible. Live frames `01-...-wrong-visible.png` and `02-...-wrong-reverted.png` show the red transient followed by rollback; `16-...-clean-win.png` shows the success verdict and saving status.

**Final code delta after this pass:** Home's saved-puzzle button now says “Открыть повтор” and describes the read-only/no-rating behavior; the shared visual capture now expects that replay rather than a playable offline trainer. Addressed by `cead2ace`; the PR was Draft during the next synchronize-triggered L2, so its public live smoke and DPI captures must be repeated for this final UI/capture delta before L3.

The first targeted L2 attempt on `91335c0` stopped before native/live capture at an existing `test_home_dashboard_refresh_is_background_and_drops_stale_generation` 1-second worker-start wait (`361 passed, 1 failed`). The exact test passed once standalone and five additional consecutive times locally under offscreen Qt. No product regression was reproduced; a draft synchronize rerun is needed before L3.

### L3 — full Windows CI

**Status: PENDING final code SHA.** After L2 for `cead2ace` is green, mark this PR Ready for review to trigger the automatic exact-head Windows test/compile/offline-smoke/native visual jobs. No manual `workflow_dispatch` is needed. Return the open PR to Draft after the run; do not merge. If executable code changes after L3, run L2 and L3 again for the new code SHA.

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
| Input → first painted feedback | warm-input p50 `5.43 ms` (8 samples, range `4.23–17.16 ms`); p95 `3087.05 ms` including first interaction outlier | live Qt trace. First source→destination automation sample `3087.66 ms`, destination→state `11.99 ms`; event-loop gap max `3102.61 ms`, p95 `50.52 ms`. Board paint events mostly ~2.1–3.7 ms (one `8.32 ms`). Treat the first-input outlier as unresolved/possibly test-dispatch delay, not as a verified real-user stall. |
| Wrong-move visibility | `417.86 ms` | live frame timestamps show wrong feedback then rollback; timer begins after paint. |
| SQLite commit | 100%: `134.96 ms`; 125%: `43.77 ms`; 150%: `38.41 ms` | synthetic visual capture fixture, isolated temporary DB; not owner DB. |
| Public Lichess GET | p50 `138.60 ms`; p95/sample max `315.93 ms` | real sequential unauthenticated GETs: `0haqU` 315.93, `00008` 138.60, `0000D` 138.37 ms; all HTTP 200. |
| Prepared next position visible | `0.0139–0.0161 ms` | synthetic capture fixture handoff. |
| Live auto-next | `1251.84 ms` | real Qt timestamps: clean win 05:57:32.889679Z → next public puzzle ready 05:57:34.141516Z. |
| Fake SQLite delay responsiveness | 50/500/3000 ms cases | targeted pytest; GUI event loop remains live |
| Fake GET delay responsiveness | 100/1200/3000 ms cases | targeted pytest; GUI event loop remains live |
