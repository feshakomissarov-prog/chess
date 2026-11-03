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

## W1 — domain contracts and composition root

- Добавлены минимальные immutable доменные модели (`GameRef`, `GameSnapshot`, `ClockSnapshot`, `AnalysisScore`, `SourceCoverage`, `JobContext`) и `Protocol` ports для game store/feed, анализа, отмены, событий и snapshot чтения.
- Создан `chess_coach.bootstrap.ApplicationRuntime`: `__main__` собирает один repository/runtime после single-instance проверки; `MainWindow` принимает runtime или создаёт его для совместимого прямого запуска. Внешние entrypoints `python -m chess_coach` и `chess_coach.window.MainWindow` сохранены.
- Офлайн runtime использует изолированные временные settings и SQLite; manual-connect не создаёт LichessService до пользовательского действия; обычный runtime сохраняет auto-restore. Сервис кешируется в runtime, чтобы избежать дубликатов.
- Тесты: `tests/test_architecture_w1.py` (DTO инварианты и AST import boundaries, offline profile cleanup, manual-connect intent); `tests/test_launcher.py` и ранее существовавшие проверки окна отражают новый seam и сохраняют исходные behavioral assertions.
- Первые целевые проверки выявили три устаревших тестовых patch point в `test_window.py`; они перенаправлены на реальный инфраструктурный модуль. Тесты поведения и ожидания не ослаблены.
- Целевой прогон после исправлений: **19 passed** (`test_architecture_w1.py`, `test_launcher.py` и три ключевых window tests).
- Full suite до test patch обновления: **557 passed, 3 failed** из-за названных monkeypatch-путей; после обновления целевые тесты PASS. Полный прогон будет повторён перед W1 commit.
- Архитектурная карта дополнена текущей структурой W1 и оставшимися boundary-conversion gates.
- Никаких новых библиотек, схемных изменений, реального Lichess/OAuth, Stockfish или пользовательских данных.

**Next:** повторить полный suite и isolated offline GUI smoke; зафиксировать W1, затем начать W2 use-case coordinators с тестами поведения до переноса orchestration.
