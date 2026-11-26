# PUZ-UX-06 execution log

**Branch:** `codex/puz-ux-05-owner-live-next-replay`

**PR:** [#57 — PUZ-UX-05/06](https://github.com/feshakomissarov-prog/chess/pull/57)

**Base/head before this package:** `4c2feba39339869667d2ae186a93f72ef96a17a4`

**Status:** implementation and Windows verification in progress; PR remains Draft; not merged.

## Scope

One package implements PUZ06-01…12 and the confirmed PUZ05 findings in PUZ06 §13: distinct unrated Training and official Lichess rated entry points; an in-workspace mode switch; source-correct activity replay; bounded, distinct puzzle queues; frozen account/mode/theme/difficulty attempt data; shared exit confirmation; scoped cooldown and recovery; and server-confirmed rating display. The PUZ05 regressions cover the prior history, review, error, account-switch, and Home archive behaviors.

No owner SQLite, OAuth token, or keyring entry has been accessed. Windows runners use isolated storage and inert synthetic credentials. Training never submits a rated result. CI models a rated response with fake transport; it sends no rated HTTP POST. The only public Lichess requests in the Windows journey are unauthenticated read-only puzzle GETs.

## Verification plan

- **L1:** on the Windows runner, compile the touched modules and run the targeted PUZ05/06 contracts, puzzle UI/store/API tests, and the focused Home archive/close regressions. The new activity, queue, history-exit, cooldown, filter-freeze, and account-placeholder cases are named in `tests/test_puz_ux_05_live_flow.py`; two-mode and official request/outbox cases are in `tests/test_puz_ux_06_modes.py` and `tests/test_puz_ux_06_rated.py`.
- **L2:** one themed native Windows Qt journey on the same PR revision. It drives Home and page controls with Qt mouse/wheel events, queries genuine public task IDs and checks distinct position/solution hashes, enters both modes, tests consent cancel/accept, hint/reveal/repeat, history/review/next, 10 distinct tasks, auto-next and recovery, and captures wide plus 125%/150% screens. A synthetic server ACK is clearly identified and is not authenticated Lichess acceptance. The same job runs the selected large Home archive and close regressions.
- **L3:** one full Windows workflow on the final code SHA while PR #57 remains Draft, including full pytest, compileall, isolated offline startup and the native visual/Home matrix. No code edits after this run; Markdown-only log updates do not require another code run.

## Current checks

- Local `python -m compileall` for changed Python modules and tests: **PASS** (syntax only; local pytest/Qt was not run).
- Local `git diff --check`: **PASS**.
- L1/L2/L3 Windows runner results: **PENDING**.
- Public GET, authenticated account behavior, and real rated result POST: authenticated account and rated POST intentionally **NOT RUN**; public read-only checks are recorded in the L2 action journal.
- Owner manual Windows acceptance and independent review: **PENDING**.

## Acceptance matrix

| Requirement | Code status | Runner status | Owner status |
| --- | --- | --- | --- |
| PUZ06-01…12: two modes, official server rating, explicit permission, durable attempt outcome | Implemented; verify on L1/L2/L3 | Pending | Pending |
| PUZ05/§13: activity FEN, genuine queue, unified exits, 429 recovery, filter freeze, profile placeholder, review text | Implemented; targeted cases added | Pending | Pending |
| Ten genuine public puzzles, real Qt interactions, captures | Journey driver prepared; no cloned IDs | Pending | Pending |
| Rated ACK and ambiguity behavior | Fake transport only; no real rated POST | Pending | Authenticated owner acceptance required |

## Results and artifacts

Populate after the runner executions with exact code SHA, test counts, Windows workflow/run links, artifact IDs and digests. Open and inspect the L2 action journal and representative native frames at each configured scale before marking visual acceptance complete.

## PUZ-UX-06 §14 continuation (2026-11-26)

This continuation is limited to the four owner-confirmed §14 findings. It starts from the already reviewed PR #57 code head `0bf366b52aa0deb04676868542f2defcc441a786`; the focused RED-only test commit was `e06232f5a5b26b45bc268cdffbac5b28180033a8`.

- **RED evidence:** Windows L1 run [45802087262](https://github.com/feshakomissarov-prog/chess/actions/runs/45802087262) executed the focused PUZ06 tests. The exact-base check passed; five newly added §14 assertions failed as expected and 53 related tests passed. Failures isolated nested activity reconciliation, synchronous rated NEXT blocking, durable frozen payload/pre-POST recovery, and close-without-send persistence. The Qt waits are bounded at 3 seconds and include the expected state/status in their predicate.
- **First GREEN attempt:** Windows run [45804533567](https://github.com/feshakomissarov-prog/chess/actions/runs/45804533567) passed exact-base RED and compile, then stopped in an existing SQLite migration assertion: the unchanged test expected `user_version == 30` after the PUZ06 recovery schema moved to 31. The other 23 tests in that store/API command passed in 51.10 seconds. Updated that single expected schema version; this was a test contract correction, not a production failure.
- **Qt blocker localization:** Windows run [45804990341](https://github.com/feshakomissarov-prog/chess/actions/runs/45804990341), at SHA `6b96dbcd6f2f5fb53307eadf4a41972baa7a0338`, passed store/API, UI/worker, compile, and exact-base RED. Its combined interaction step remained active for more than 9 minutes. The public Actions UI hid live logs behind sign-in, so no specific test name was observable; the job has a 15-minute cap. Split the combined step into three bounded (3-minute) file groups and enabled 20-second `faulthandler_timeout` stack diagnostics. This avoids another long silent wait while preserving all assertions.
- **Localized RED/GREEN attempt:** run [45806543674](https://github.com/feshakomissarov-prog/chess/actions/runs/45806543674) passed compile, store/API, UI/worker, and legacy interaction/PUZ05 contracts. It localized three failures to `tests/test_puz_ux_06_modes.py` in 2.05 seconds: `win` undefined when building the first-mistake row, retry cooldown did not disable a hidden button, and an auto-next test did not select the rated mode. These were corrected directly; no tests were relaxed.
- **Implementation in progress:** (1) strict account + official nested `PuzzleActivity` identity, strict `bool`/integer fields, a narrow timestamp window, and unique-match-only confirmation; no invented rating delta; (2) synchronous gate for every unresolved rated status, training-mode preservation, and no rated prefetch; (3) frozen payload persistence, preflight failures remain `not_sent`, and `sending` is persisted immediately before POST; startup alone converts interrupted `sending` to `unknown`; prepared/not-sent retry uses the saved payload; explicit reauthorization does not send; (4) a rated-only close choice persists board progress as a local `not_sent` abort marker and sends nothing, then shows the abort state on re-entry.
- **Local checks after the implementation edits:** `git diff --check` and Python 3.12 `py_compile` for changed production/test modules passed. Local pytest/Qt and owner-profile GUI/database access were not run.
- **Next verification:** targeted L1 is pending on the implementation SHA; then one §14-themed Windows L2; then one final exact-head Windows L3. Exact run links, test totals, inspected artifact names and hashes will be recorded here after each runner completes. No real rated POST is used in CI. PR #57 remains Draft; merge is not authorized.
