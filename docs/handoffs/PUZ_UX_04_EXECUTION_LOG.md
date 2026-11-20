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
.venv312\Scripts\python.exe -m pytest -q -o addopts= tests/test_puz_ux_04_flow.py tests/test_puz_ux_04_home.py tests/test_puzzle_training.py tests/test_puzzle_training_ui.py tests/test_puzzle_training_async.py tests/test_puzzle_training_navigation.py tests/test_board_view.py tests/test_dashboard.py tests/test_home_dashboard_last_good.py
```

Result: **96 passed in 60.11s**. MainWindow test screenshots and a JSON interaction trace were written under the pytest temporary directory; retained outside the repository. Also completed a visible native Windows `Chess Coach` window path through the `Обучение` navigation, actual coordinate clicks, board moves, wheel scroll, repeat, timeout, and retry. It started with isolated temporary SQLite and an inert puzzle token; no user credential or user database was accessed. Five distinct new puzzle IDs were observed in succession (`NativeJourney-1`, `-3`, `-4`, `-5`, `-6`); `-1` was wrong then solved (remained red), replay was unrated, and the next four were solved cleanly (green series of four). The visible Home large-archive regression uses 3,300 isolated synthetic rows and asserts under 1,000 ms for the cached first summary.

The native visual session was inspected at the current Windows display size. Automated offscreen frame captures are not published because the local Qt offscreen font bundle rendered Russian glyphs as boxes; the native window screenshots were readable and visually inspected in the interactive session. The three official scale/size captures remain part of the final Windows workflow run.

No real Lichess requests or OAuth flows were run. The native timeout/recovery and five-ID sequence used a fake transport. This does not substitute for the owner's own authenticated Windows acceptance.

## L3

- Exact final code/test SHA: pending commit.
- Windows workflow run: pending.
- Full pytest, `compileall`, isolated offline initialization, native visual matrix, and workflow artifacts: pending exact-head GitHub Actions.

## OWNER ACCEPTANCE REQUIRED

Before merge, the owner opens this draft on their Windows installation and personally checks the actual Lichess account journey, appearance at their display scales, red/green/yellow/grey result semantics, replay, and Home against their own saved archive. The Codex run never opened the owner SQLite or OAuth store. Do not merge until the owner explicitly accepts.
