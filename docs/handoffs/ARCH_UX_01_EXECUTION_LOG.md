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
- At this checkpoint, inline Play form and safety-gated internal library details are implemented. Still outstanding: richer global background inspector, remaining A7/A8 integration and route details, full A10 boundary audit, responsive geometry checks across all target sizes, and final 24-group acceptance matrix. No claim that A2–A12 are complete is made yet.

### A6 focused follow-up

- Period filtering now uses `COALESCE(started_at, updated_at)`, so a recent metadata refresh cannot make an old game appear recent. The per-game Lichess refresh control is hidden from the regular catalog; the existing recovery method remains available to a diagnosed recovery flow.
- Regression: `test_library_period_filter_uses_game_date_not_metadata_update_time` and UI assertion that ordinary catalog does not expose the refresh action — PASS (3 relevant tests).

### A6 internal details pane checkpoint

- Added a split catalog/details layout inside the existing library page. Selection loads `load_game_review` through the existing `BackgroundWorkCoordinator` INTERACTIVE lane; it reuses the established `ReviewGraph` and game review read model. The graph exposes whole-line score/source/coverage data and emits a selected ply back to the existing game/review route. No new metrics calculation, SQLite, service, or engine manager was added.
- Safety is checked before the worker starts and again before rendering. On safety revoke or route exit, the request is cancelled/generation-invalidated and the actual graph values, markers and metric labels are cleared. Official accuracy remains separately labeled from local engine assessment.
- Regressions: `test_library_detail_reuses_review_graph_and_masks_until_safety_gate`, `test_library_detail_loads_in_worker_only_after_safety_and_masks_on_revoke`, and the worker-backed library query/stale-navigation test — **3 passed**. `compileall` and `git diff --check` — PASS. GUI geometry remains subject to the later A11 review and physical owner acceptance.
- The page now places filters and selected-game detail beside the paginated catalog rather than across a compressed global filter row. A 1280×720 Qt offscreen geometry regression confirms the list and inspector retain usable minimum widths and the library itself has no page-level scroll area (`test_library_uses_bounded_three_zone_layout_at_laptop_size`). This is a structural automated check, not a substitute for physical-DPI inspection.

## A10 — bounded library read boundary (partial)

- Added `application.library.GameLibraryQueryCoordinator` to build the existing repository query from account-scoped filters and clamp page size. The application still uses the same repository facade and SQLite file.
- Library SQL now runs on a registered `INTERACTIVE` worker through the existing `BackgroundWorkCoordinator`; changing route cancels the active request, generation checks reject stale replies, and the Qt slot only renders the bounded result page. No extra executor/queue was introduced.
- Regression `test_library_sqlite_query_runs_off_ui_thread_and_ignores_stale_navigation` verifies the actual thread and page behavior. Relevant library route/pinning/filter tests: PASS. Full-suite and static architecture audit remain pending.
- Remaining A10 work: several historic MainWindow callbacks still own presentation plus use-case orchestration (notably puzzle history, game analysis dialogs, and some sync status). They have not been mechanically split; each remains scheduled for evidence-based scope review.

## A7/A9 — analysis queue and background inspector integration (checkpoint)

- The durable Stockfish batch controls now open as an embedded Analysis workspace page, reusing the existing BatchAnalysisDialog control logic, engine admission, safety checks and queue. The route context remembers whether Back should return to the queue or the Analysis landing page. No new queue or engine instance was introduced.
- The header's background-operations entry is now an internal page showing the shared work-coordinator snapshot plus the persisted Lichess ready-analysis backfill counters, retry status, pause/resume and manual-check actions. It uses the existing account-scoped repository/service path; it does not create a new HTTP client or poll from the UI thread. Lichess JSON export remains the only automatic ready-analysis source.
- “Активные партии” now navigates to the existing pinned-game area in My Games and requests the existing server list there; selecting a server-returned item opens that exact session. It no longer opens a separate active-games OS dialog for this route.
- Puzzle history's compatibility host is a QWidget embedded in the shell; existing task history, account selector, replay/reveal/reset and sync actions are retained. The separate training and pilot UI decisions remain unchanged.
- Focused page/window regression set: **12 passed**; compileall and git diff --check — PASS. Real service, OAuth, archive and Stockfish were not used. The older compatibility ActiveGamesDialog class remains for direct tests/legacy callers but is not used by the primary shell action.
- Settings now opens as a non-modal internal stack page, using the existing tabbed SettingsDialog implementation and existing QSettings/credential handlers. Save commits through the former application flow; Cancel/Back discards widget edits, and the previous route is selected before the page is removed to avoid transient route-history corruption. Sound assets, IDs, defaults, volume behavior and QSettings keys are unchanged. Regression `test_settings_open_inside_shell_and_cancel_discards_edits` — PASS.

### P0 — home analytics visibility gate

- The Home dashboard now masks cached engine-derived analysis/error counts and per-game move metrics unless the process has a fresh successful account-wide `/api/account/playing` snapshot, or the runtime is explicitly isolated `--offline`. It masks immediately when safety becomes pending/unknown/denied, on account/auth changes, and as soon as a human or unknown online game is observed through either `gameStart` or `gameFull/gameState`.
- The mask is applied to the actual visible metric/card labels, not only the source model. Official Lichess accuracy and non-analytic game metadata remain visible. Existing review-space safety policy remains the single authority; this is a display projection, not a second permission mechanism.
- Qt regression verifies visible Home labels/card fields under pending/denied-equivalent state and after a human active session, then checks local display restoration only after the test explicitly removes the session in a synthetic fixture. Relevant Fair Play/review/external/library checks: **6 passed**; compileall PASS.
- The same fail-closed display lease now masks the entire cached Statistics/Progress tabs before they are shown. A user-triggered refresh uses the same account-wide active-games API and the shared `playing_snapshot_allows_analysis` policy; stale generation/account replies and incomplete lists do not restore analytics. A Qt test verifies actual widget visibility for pending, fresh synthetic safe response, human-game revoke and stale response: `test_progress_cached_analytics_is_visually_masked_until_fresh_safety_snapshot` — PASS. Statistics regressions: **6 passed**.

### A2 — dashboard hierarchy checkpoint

- The visible home content now uses the approved progress/next-activity/recent-games hierarchy. The activity card states that a personalized recommendation needs sufficient evidence and links to the existing saved puzzle history; it does not fabricate a recommended lesson. Three recent completed game cards remain clickable. Global play, games, analysis, learning, progress and settings entry points remain in the shell sidebar.
- Removed the visible “Другие разделы и синхронизация” drawer and its duplicate navigation/pilot actions. Legacy callback objects remain hidden compatibility hooks only; the primary routes are the six sidebar destinations and the dedicated pages. Free play and AI controls are available in the embedded Play page.
- Home has no page-level QScrollArea. At 1360×860, the Qt regression checks the two-column content, three synthetic recent cards and visible activity CTA; offline connection gating is checked against the Play page. Focused home/navigation tests: **13 passed**; compileall/diff check PASS. Physical 1280×720 and DPI verification remains in A11/manual review.

## A3 — embedded game creation form (implemented, pending full regression)

- `PlayPage` now presents one in-shell form for free board, built-in Lichess AI (levels 1–8), friend invite, username challenge, and resume active games. Color, clock, username, level, and rated controls appear only for relevant modes; AI is always casual. Values are submitted as a typed dictionary to the shell; the page itself does not call HTTP or mutate sessions.
- The shell routes requests through the existing Lichess service façade, keeps duplicate-submit protection, stores existing AI preferences, and displays status in the form. Legacy internal mode buttons remain hidden compatibility hooks; they are not visible user routes.
- Fake service Qt regression covers AI parameters, online gating/duplicate requests, and friend invite dispatch. Focused page/window tests: **2 passed**. Full regression still pending.

### Final coverage matrix — pending implementation

| ID | Route / outcome | Verification |
|---|---|---|
| COV-01…24 | To be completed stage-by-stage | To be completed |
