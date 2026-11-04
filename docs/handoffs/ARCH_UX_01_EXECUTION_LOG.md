# ARCH-UX-01 execution log

**Branch:** `codex/arch-01-modular-refactor` · **Code PR:** [#44](https://github.com/feshakomissarov-prog/chess/pull/44) (draft, base `main`)\
**UX source:** documentation PR [#45](https://github.com/feshakomissarov-prog/chess/pull/45), head `dc8edcd7cbfe88f30c3c88640bc40b8157f90be9` (draft, not merged)\
**Scope:** [ARCH-UX-01 v1.1](../specs/ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), stages A0–A12. No merge, force-push, live Lichess, personal token, personal SQLite, or real Stockfish activity.

## A0 — preflight and baseline (completed)

- At start, PR #44 was open/draft/not merged at `9a59b21bb91adbada7b58baf9dff45d0afccb27d`; PR #45 was open/draft/not merged at `dc8edcd7cbfe88f30c3c88640bc40b8157f90be9`.
- PR #44 baseline Windows CI #171: SUCCESS, 604 tests. The baseline was independently reproduced locally on Windows/Python 3.12: `python -m pytest -o addopts='' -q` — 604 passed; `python -m compileall -q src tests scripts` — PASS; `git diff --check` — PASS; isolated `QApplication + MainWindow(offline_mode=True)` init/close — PASS.
- Working tree was clean except the pre-existing untracked `.venv312/`, which was not read, changed, staged, or removed.
- Selectively copied the current normative docs and approved UX material from PR #45 exact head `dc8edcd7cbfe88f30c3c88640bc40b8157f90be9`: AGENTS, README, PCD, PRD, PRODUCT_VISION, PROJECT_STATUS, ROADMAP, architecture docs, ARCH-UX spec/audit, approved UX docs, and supporting research. This was a file-level copy, **not a merge** of PR #45. The PR #44 architecture map and original ARCH-01 execution log remain intact.
- Read the unified spec, all 14 preflight findings, approved UX-B1/A2/GAME-01/COV-01…24 and current working draft. The implementation is an adaptation of ARCH-01, not a rewrite. Critical ordering: final-game durability and Fair Play first; then preserve existing behavior; then route/UX and desktop polish.
- No code was changed in A0. No real user profile, credential store, SQLite, PGN, OAuth, Lichess endpoint, or Stockfish binary was used.

## A1 — typed navigation and runtime route context (in progress)

- Added `WorkspaceRoute` / `WorkspaceNavigator` as a typed shell route history with context support. `MainWindow` now records shell route transitions and the current game/review identity while preserving the existing Qt screen stack and session coordinator.
- This is an incremental boundary, not a replacement runtime/service locator. The same `ApplicationRuntime`, session registry, DB repository, Lichess service, Stockfish manager, work coordinator, and safety checks remain the only instances.
- Offline/manual-connect isolation and route creation are not changed; baseline CLI/runtime tests remain in the full-suite gate.
- Targeted checks: `pytest tests/test_architecture_fix_coordinators.py tests/test_window.py` — **172 passed**; `compileall src tests scripts` — PASS; `git diff --check` — PASS.
- Added navigation history regression covering typed route + account/filter context restoration. Further embedded-route integration is staged with A2–A9; route history currently records shell pages only.

## A5 — finished-game lifecycle (critical portion implemented)

- Finished Lichess games stay on the same persistent `game_page`/`BoardView`; `ResultPage` is no longer shown or added to the active stack. The right panel displays the final state, confirmed-save status, and actions for review, explicit new challenge, and PGN export.
- Added `GameFinalizationCoordinator`. It observes server-sync and SQLite-write phases independently from the active page. An authoritative terminal snapshot updates the session and SQLite; the user may navigate away without stopping reconciliation. Local write checks use bounded exponential delays (maximum 8) and do not invent server moves, resend network commands, or poll forever. Persisted pending sync remains recoverable on next authenticated startup.
- Added regressions for finish-before-final-state, same-board finish, navigation-away during local write confirmation, and bounded lifecycle tracking. Existing terminal event/session tests still pass.
- Not yet complete: friendly “call again” contextual routing for the built-in AI/local route; comprehensive event-order/reconnect shutdown matrix is scheduled for A4/A10. No live network was used.

## A2–A4, A6–A12 — implementation status

Each stage will be appended here with commit SHA, changed responsibilities, focused tests, limitations, and next stage. Coverage IDs COV-01…24 will receive a route and evidence/status in the final acceptance section.

## A2/A3/A4/A6/A7/A8 — shell/page integration pass (in progress)

- Added an in-shell Analysis landing page and six-route navigation (`home`, `play`, `games`, `analysis`, `training`, `progress`) with a Back action and a persistent “Вернуться к партии” action. The existing runtime/services/session registry remain reused; page changes do not create another SQLite, Stockfish manager, Lichess service, or background coordinator.
- Home is now a `QWidget` rather than a global `QScrollArea`; its existing metric cards, recent completed games, and actions are retained. Pilot buttons were removed from the visible home shell. The separate local premove-training selector is hidden while ordinary online premoves and their cancel path remain in code.
- The library filter read path now applies side, result, analysis presence/source, and period predicates in SQLite; page/selection/scroll state is restored when refreshing. Active Lichess sessions are pinned in a distinct list and resume only the selected game. Per-game refresh is no longer shown as a normal library action.
- Statistics and puzzle history are placed in the shell stack using compatibility wrappers over the existing implementations; existing model, filters, replay, and safety owners are reused.
- External PGN/FEN input uses a bounded (4 MiB) worker task through `BackgroundWorkCoordinator`; parse result is accepted only while Analysis remains active. The temporary review uses a read-only in-memory adapter, hides persisted-analysis/engine controls, and is excluded from resume persistence. PGN/FEN parse tests cover standard lines, invalid input, multiple games, custom FEN, no SQLite write, no engine process, and stale navigation.
- During the implementation pass, focused regression run on Windows/Python 3.12: `tests/test_ui_pages.py tests/test_architecture_fix_coordinators.py tests/test_lichess.py tests/test_window.py` — **301 passed**; compileall — PASS. Targeted external PGN/FEN tests — **3 passed**. These results are for the current working tree before this entry's commit; full suite/CI remain pending.
- Still outstanding in this pass: inline Play form (current mode cards route into existing dialogs), internal library details pane with a safety-gated mini graph, richer global background inspector, full A10 boundary audit, responsive geometry checks across all target sizes, and final 24-group acceptance matrix. No claim that A2–A12 are complete is made yet.

### Final coverage matrix — pending implementation

| ID | Route / outcome | Verification |
|---|---|---|
| COV-01…24 | To be completed stage-by-stage | To be completed |
