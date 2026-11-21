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
