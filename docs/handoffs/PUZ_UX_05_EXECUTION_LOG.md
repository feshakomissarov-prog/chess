# PUZ-UX-05 execution log

**Branch:** `codex/puz-ux-05-owner-live-next-replay`
**PR:** [#57 — PUZ-UX-05: restore reliable Lichess puzzle flow](https://github.com/feshakomissarov-prog/chess/pull/57)
**Base:** `main` / `53e6f5957e9e04530d165c9a143e0ee60a97292b`
**Final code SHA:** `ec16b0fd9e9d326e8d1edd8e7b8fc89317faaa93`
**Status:** open Draft, not merged; authenticated owner acceptance pending.

## Scope

Implements PUZ05-01…11 as one package: source-aware safe replay normalization; distinct Home NEW and saved REPLAY routes; bounded unique NEXT selection from the official batch response; immediate and legible auto-next state; one-click hint and reveal; safe result-tile replay; immediate result/streak feedback; stable verdict and actionable network recovery; review navigation and board input continuity; Home archive performance and clean journey shutdown; real-Qt journey coverage.

No `puzzle:write` or result POST was added. The public live request was unauthenticated. Owner SQLite, OAuth and keyring were not accessed or modified.

## RED → GREEN / L1

The PUZ05 journey was run against the exact `main` base before implementation. It exposed the documented Home replay normalization, repeated-ID NEXT, timer/checkbox, instant hint/reveal, tile source, and error/verdict paths. On the PR head, the focused PUZ05 RED/GREEN suite and compile checks passed in Windows CI run [#44923863434](https://github.com/feshakomissarov-prog/chess/actions/runs/44923863434). The tests use isolated state and fixture/fake transports; the public GET is separately recorded below.

Focused failures during the first Windows journey were ordinary test-driver synchronization/observation issues: waiting for the wrong-move flash instead of its recovery timer, reading a separate QSettings instance instead of the isolated MainWindow settings, and emitting non-ASCII console JSON under Windows cp1252. Each was corrected in the journey driver. Product behavior was then exercised end to end; no speculative race hunt was added.

## L2 — one themed Windows Qt journey

Successful final L2 ran on code SHA `acf604a2ab7c5ae22c1397d584fb2d07ad49785e`, workflow run [#44923863434](https://github.com/feshakomissarov-prog/chess/actions/runs/44923863434), artifact [puz-ux-05-l2-acf604…](https://github.com/feshakomissarov-prog/chess/actions/runs/44923863434/artifacts/14714762319) (`sha256:ac0a6faa1982552b2b64b5d1058abb7197d454348b4d233ac3bf1594b1f9c361`). The Windows MainWindow journey used actual Qt mouse and wheel events, buttons, worker callbacks, isolated temporary data, three display scales and produced 27 PNG frames plus an action journal. I opened and visually reviewed the frames, including compact 125%/150% views.

Observed journey: Home NEW and saved REPLAY; placeholder while loading; hint/reveal/repeat; right-click arrows, wheel review and return; 10 distinct task IDs; a wrong then correct attempt and result ribbon; auto-next checked and unchecked by visible checkbox clicks; manual recovery; network error and recovery. The journal contains 87 user actions, `owner_database_or_oauth_touched=false`, and `authenticated_lichess_verified=false`.

The same themed L2 included a 3,400-game synthetic Home archive regression and a read-only live `GET https://lichess.org/api/puzzle/0haqU` (HTTP 200, no Authorization header). The actual public payload was normalized by the production path. Deterministic batch fixtures are explicitly separate from this HTTP proof; they do not claim an authenticated account test.

## L3 — final exact code SHA

The first L3 attempt on `acf604a2` passed Home review but the existing visual master harness clicked Home NEW while expecting the saved replay route; its `visual-evidence` job failed at that stale capture assumption and its full test job was cancelled when the follow-up run superseded it. The capture driver was corrected to click the visible Home REPLAY button (`d77b6d4`); product behavior did not change. The next L3 on `7558ad7` exposed a real compact-layout board-size regression in the special 150% trainer capture. The active neutral ribbon was hidden in compact mode until result feedback is available, restoring board space (`14f9b03`); a regression test was added. The first GREEN run after this product fix caught a positional test-fixture constructor error (`ec16b0f` corrected the fixture, not product code). RED/GREEN and final exact-head L3 were rerun on this final SHA.

Final exact-head Windows L3 passed on `ec16b0fd9e9d326e8d1edd8e7b8fc89317faaa93`, workflow run [#44928237622](https://github.com/feshakomissarov-prog/chess/actions/runs/44928237622). The Home L1/L2 job, full `1291 passed` offscreen suite (13m14s), compileall, isolated offline MainWindow initialization, native Windows visual master, and dedicated PUZ trainer captures at 100%, 125%, and 150% all passed. CI used Windows Server 2025, Python 3.12.10, PySide6/Qt 6.12.0. I opened and inspected the final exact-SHA images for the ready puzzle screen, hint/error feedback, solved state, and Home history replay; board and primary controls remain visible at 150% / 1120×640. The final visual artifact is [visual-master](https://github.com/feshakomissarov-prog/chess/actions/runs/44928237622/artifacts/14716592926), digest `sha256:08e1fcf2ae1045687ff5c4e217bad17577700f80cd9a5faf4bfe17579524f37e`; Home chart artifact [home-chart-l1-l2](https://github.com/feshakomissarov-prog/chess/actions/runs/44928237622/artifacts/14716975139), digest `sha256:6078d746540fdbbddac1ac61fea579edfff6a4d7d32499432943ab952c75b7cc`. PR #57 remains unmerged; keep it Draft for independent review and owner acceptance.

## PUZ05 acceptance matrix

| ID | Code / L1 | Windows L2 | L3 | Owner |
| --- | --- | --- | --- | --- |
| PUZ05-01 Home replay compatibility | Implemented / verified | Clicked Home NEW + saved replay; fixtures cover legacy, full API and malformed candidates | passed, exact SHA | pending |
| PUZ05-02 duplicate-safe unique NEXT | Implemented / verified | 10 unique visible task IDs; repeat-ID batch covered by focused fixtures | passed, exact SHA | pending |
| PUZ05-03 real next and auto transition | Implemented / verified | Manual NEXT, visible auto ON/OFF and recovery exercised | passed, exact SHA | pending |
| PUZ05-04 visible checkbox state | Implemented / verified | Checked and unchecked via visible mouse clicks; frames inspected | passed, exact SHA | pending |
| PUZ05-05 instant hint/reveal | Implemented / verified | One-click actions exercised without modal | passed, exact SHA | pending |
| PUZ05-06 safe result tile replay | Implemented / verified | Active/finished replay behavior covered by focused suite/journey | passed, exact SHA | pending |
| PUZ05-07 immediate hint/result feedback | Implemented / verified | Wrong/correct feedback and ribbon observed | passed, exact SHA | pending |
| PUZ05-08 stable verdict and recovery UI | Implemented / verified | Error, retry/recovery and loading placeholder observed | passed, exact SHA | pending |
| PUZ05-09 review/input continuation | Implemented / verified | Right-click marks, wheel review and return exercised | passed, exact SHA | pending |
| PUZ05-10 Home/archive/cleanup regression | Implemented / verified | 3,400-game synthetic archive; journey shuts down cleanly | passed, exact SHA | owner archive not inspected |
| PUZ05-11 end-to-end owner Lichess gate | Automated prep only | Public unauthenticated GET only; 10-task journey uses isolated deterministic batch fixtures | pending | **owner acceptance required** |

## Owner handoff

The Draft PR is ready for independent review and manual Windows acceptance. On the owner's Windows copy, please verify the real authenticated `puzzle:read` journey and history, 10 actual NEXT tasks, saved Home replay, result tiles, one-click hint/reveal, visible auto-next checkbox and timer, network recovery, and display at the owner's DPI. The public GET and deterministic batch fixture are not evidence of authenticated Lichess acceptance. No merge has been performed or authorized.
