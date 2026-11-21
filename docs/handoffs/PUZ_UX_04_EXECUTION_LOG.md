# PUZ-UX-04 execution log

**Branch:** `codex/puz-ux-04-lichess-workflow`
**Base:** `main` / PR #52 result, `30cf8f0aef34a5ef219882971e3e5624bff0d22b`
**Status:** Draft code PR; owner Windows acceptance pending; no merge.

## Scope completed

Implemented PUZ04-01…10 as one package: restore solution interaction on forward review navigation; clear puzzle-scoped manual board marks on first own-piece selection; enforce one visible NEXT action and bounded unique online IDs; explicit loading/error/retry behavior; result tiles and streak state; unrated replay; account-scoped auto-next default OFF while retaining saved settings; Home slow-load status and refresh with last-good retention; isolated large archive coverage.

## RED → GREEN

On detached source worktree `30cf8f0` (before the changes), the two new gesture regressions failed:

- `tests/test_puz_ux_04_flow.py::test_wheel_forward_returns_playable_board` — after scrolling forward to the end the state remained `REVIEW` and the board was non-interactive.
- `tests/test_puz_ux_04_flow.py::test_lmb_piece_selection_clears_puzzle_annotations_only` — right-drag arrows remained after selecting an own piece.

Both passed on this branch. The isolated baseline worktree has been removed after recording the result.

## L1

- `python -m py_compile` on the five changed application modules and two new tests — PASS.
- `git diff --check` — PASS.
- Addressed review, annotation, unique NEXT, result/replay, MainWindow network recovery, and Home large archive tests — PASS (included in L2 below).

## L2

The single themed regression command on Windows used the temporary application database and inert fake Lichess transport:

```powershell
.venv312\Scripts\python.exe -m pytest -q -o addopts= tests/test_puz_ux_03_interaction.py tests/test_puz_ux_04_flow.py tests/test_puz_ux_04_home.py tests/test_puzzle_training.py tests/test_puzzle_training_ui.py tests/test_puzzle_training_async.py tests/test_puzzle_training_navigation.py tests/test_board_view.py tests/test_dashboard.py tests/test_home_dashboard_last_good.py
```

Result after the final targeted compatibility follow-up: **115 passed in 69.64s**. MainWindow test screenshots and a JSON interaction trace were written under the pytest temporary directory; retained outside the repository. Also completed a visible native Windows `Chess Coach` window path through the `Обучение` navigation, actual coordinate clicks, board moves, wheel scroll, repeat, timeout, and retry. It started with isolated temporary SQLite and an inert puzzle token; no user credential or user database was accessed. Five distinct new puzzle IDs were observed in succession (`NativeJourney-1`, `-3`, `-4`, `-5`, `-6`); `-1` was wrong then solved (remained red), replay was unrated, and the next four were solved cleanly (green series of four). The visible Home large-archive regression uses 3,300 isolated synthetic rows and asserts under 1,000 ms for the cached first summary. Native puzzle capture at `QT_SCALE_FACTOR=1.5`, size 1120×640 also passed all ready, prefetch error, account switch, save error, OAuth consent, local-next, history replay, user marks, mistake/hint, solved, and prepared-next frames; the ready, mistake/hint, and solved PNGs were opened and visually reviewed.

The native visual session was inspected at the current Windows display size. Automated offscreen frame captures are not published because the local Qt offscreen font bundle rendered Russian glyphs as boxes; the native window screenshots were readable and visually inspected in the interactive session. The three official scale/size captures remain part of the final Windows workflow run.

After the first exact-head workflow stopped in its Home visual preflight, I reproduced the same `623 > 611` recent-card overflow on both this branch and detached `main` (`30cf8f0`). Fixed the card width to follow the scroll viewport and added a resize regression across 1360×860, 1280×720, and 1120×640. The first exact-head L3 then passed Home L1/L2 but the wide native capture found that the compact recent-card arrangement still needed vertical scrolling. Set the wide-card layout breakpoint to 740 px; the native capture at both 1360×860 and 1672×941 now passes, and the wide PNGs were opened and visually reviewed. The first full PUZ DPI capture then identified a 7 logical pixel board-height shortfall at 1120×640/1.5×; reduced compact-only page margins and spacing, and the local native capture now passes with all controls inside the window. This small layout compatibility work is included because the required PUZ04 Home and task-resolution UX acceptance plus official L3 were blocked by it.

No real Lichess requests or OAuth flows were run. The native timeout/recovery and five-ID sequence used a fake transport. This does not substitute for the owner's own authenticated Windows acceptance.

## L3

- Exact final code/test SHA: `d5ea63ed7a8ab4ec01ed6c0723b4e72490c466fa`.
- Initial Windows workflow `44836494683` on `26af60a`: targeted PUZ04 tests passed (62); L3 stopped at the baseline-reproducible Home overflow. Workflow `44839076105` on `fbc6997`: native Home matrix and wide captures passed, then PUZ DPI capture found a 309 px board at 1120×640/1.5×; that exact-size issue was fixed and visually rechecked. Workflow `44840439522` on `96c8fec`: all Home and puzzle native DPI captures passed, but full pytest reported 1277 passed / 4 failed: three compatibility assertions in existing PUZ-UX-03 tests (replay tape, review return CTA, changed QWidget result tape representation) and the new end-to-end test waiting for optional prefetch. Fixed the replay bookkeeping, preserved a clickable return affordance when wheel reaches the current position, updated the legacy test to inspect result tiles, and made the end-to-end user journey test the normal visible NEXT fallback when prefetch is unavailable. Final exact-head workflow `44843027897` on `d5ea63e`: **1281 passed in 653.59s**, `compileall` and isolated offline window initialization passed; native Windows visual evidence, Home L1/L2 and PUZ capture at 1.0×, 1.25× and 1.5× all passed. Local final targeted L2 passed 115 tests; `compileall`, `git diff --check`, and the local 1120×640/1.5× puzzle capture passed. Full native visual artifact: [visual-master](https://github.com/feshakomissarov-prog/chess/actions/runs/44843027897/artifacts/14681064652); Home targeted capture artifact: [home-chart-l1-l2](https://github.com/feshakomissarov-prog/chess/actions/runs/44843027897/artifacts/14682350371).

## OWNER ACCEPTANCE REQUIRED

Before merge, the owner opens this draft on their Windows installation and personally checks the actual Lichess account journey, appearance at their display scales, red/green/yellow/grey result semantics, replay, and Home against their own saved archive. The Codex run never opened the owner SQLite or OAuth store. Do not merge until the owner explicitly accepts.
