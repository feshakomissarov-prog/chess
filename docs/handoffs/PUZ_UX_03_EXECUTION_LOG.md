# PUZ-UX-03 execution log

**PR:** Draft #52 — `codex/puz-ux-03-online-puzzle-playability-2026-11-20`
**Specification:** [`PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md`](../specs/PUZ_UX_03_ONLINE_PUZZLE_PLAYABILITY_2026_11_20.md)
**Base:** `b9e4b72d578e98db2ba345959c8474028d304cdc`
**Code SHA:** pending consolidated commit
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

Latest targeted set: **73 passed in 36.57 s**.

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

**Status: PENDING exact branch SHA.** Draft push will run the targeted Windows regression set, native 100/125/150% Qt screenshots, and the explicit public live smoke on an isolated Windows runner. Artifacts and visual inspection will be recorded here after the run. The smoke must return HTTP 200 for all three distinct IDs and complete the interactive path before L2 can be called green.

### L3 — full Windows CI

**Status: PENDING.** Only after L2 is green, mark this PR Ready for review to trigger the automatic exact-head Windows test/compile/offline-smoke/native visual jobs. No manual `workflow_dispatch` is needed. Return the open PR to Draft after the run; do not merge. If executable code changes after L3, run L2 and L3 again for the new code SHA.

## Live access and UX acceptance

- **ONLINE-LIVE-PUBLIC:** pending isolated Windows runner; no claim before actual HTTP and UI evidence exists.
- **ONLINE-LIVE-OAUTH:** `BLOCKED (no authorized separate test profile available to this environment)`. The fake OAuth thread-affinity test passed; this is not an authenticated live test. No owner token/profile or credential store was read.
- **Owner machine / owner SQLite:** not accessed; no local Qt application was started.
- **UX checklist:** to be filled against the live-run frames and trace after L2, not inferred from pytest.

| UX question from §8.2 | Result | Evidence |
| --- | --- | --- |
| Whose turn is it? | PENDING L2 visual review | live Qt frames |
| Was my move accepted, and can I see the opponent reply? | PENDING L2 | live event/frame sequence |
| Was the move illegal or a legal puzzle mistake? | PENDING L2 | wrong-move frame and rollback |
| Is the task finished, and what can I do next? | PENDING L2 | solved/loss/auto-next frames |
| Is the result saving, and can I continue while it saves? | PENDING L2 | save-pending/commit timing captures |
| Which rating changes? | PENDING L2 | CC rating UI and persistence evidence |
| What happens after network failure; is there any demo fallback? | PENDING L2 | no-token/network-negative test and capture |
| Can I use the wheel to inspect prior moves? | PENDING L2 | live wheel frame/event |
| What do the green/red results and streak mean? | PENDING L2 | multi-result native capture |
| Where is the delay? | PENDING L2 | measured input/paint, SQLite commit, public GET, prepared-next values |

## Timing evidence

Values are intentionally left blank until collected from actual run output/artifacts; synthetic timings will be identified as such.

| Stage | Measurement | Conditions / source |
| --- | --- | --- |
| Input → first painted feedback | pending | public live Qt mouse input; p50/p95 |
| SQLite commit | pending | isolated temporary SQLite on Windows runner |
| Public Lichess GET | pending | sequential unauthenticated GETs for the three named IDs; p50/p95 |
| Prepared next position visible | pending | instrumented prepared-next handoff on native Qt capture |
| Fake SQLite delay responsiveness | 50/500/3000 ms cases | targeted pytest; GUI event loop remains live |
| Fake GET delay responsiveness | 100/1200/3000 ms cases | targeted pytest; GUI event loop remains live |
