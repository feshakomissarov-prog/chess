# ARCH-01 execution log

Working branch: `codex/arch-01-modular-refactor` from `main` SHA `3e7f2cd48b16762062e9414761efd13556b55837`. One draft PR only; no merge.

## W0 — baseline and inventory

- Baseline Windows CPython 3.12.10 / PySide6, Qt, shiboken6 6.12.0.
- `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -o addopts= -q --tb=short`: **553 passed in 147.45 s**.
- `python -m compileall -q src tests scripts`: PASS.
- Isolated offline QApplication/MainWindow initialization and close, with `assert window.lichess is None`: PASS.
- `git diff --check`: PASS before adding W0 docs.
- Main architectural hotspots and public imports are recorded in [ARCH01_CURRENT_TO_TARGET_MAP.md](../architecture/ARCH01_CURRENT_TO_TARGET_MAP.md).
- No user SQLite, PGN, tokens, real Lichess/OAuth, or Stockfish were accessed. No runtime behavior changed in W0.

**Next:** W1 domain DTOs/ports and explicit composition root; contract/import-boundary tests.
