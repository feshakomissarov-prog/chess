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

## W1 gate — завершён

- Повтор полного offscreen suite после адаптации инфраструктурных patch points: **560 passed in 146.46 s**.
- `compileall -q src tests scripts`: PASS.
- Изолированный offline `QApplication` + `MainWindow(offline_mode=True)`, `lichess is None`, close и удаление только временного каталога: PASS.
- `git diff --check`: PASS (CRLF нормализация предупреждала о замене переводов строк, ошибок whitespace нет).
- Commit: `6f64f88e95dd21382d9977136caff61ea4a5499c`, опубликован в ветке PR #44.

## W2 — application state and game-session ownership (в работе)

- Выделен `application.app_state.WorkspaceStateStore`: чтение/запись resumable route, review target, geometry/sidebar/board preferences. Он использует минимальный Settings Protocol и immutable snapshot, без зависимости от Qt widgets.
- Выделен `application.game_sessions.GameSessionCoordinator`: owns registry and explicit selected game id. `MainWindow` передаёт создание сессий через `register` и совместимо читает существующий mapping/property; фоновая регистрация не выбирает партию.
- Сохранены ключи QSettings и semantics восстановления review: stale line открывается с ply 0; сохранённый review target не стирается при записи другого route; удаление resume target удаляет только game-id key, как ранее.
- Тесты `test_application_state.py`, `test_game_sessions.py`, `test_architecture_w1.py`, `test_window.py`: целевой Qt набор PASS; проверены offline restore/workspace and game selection regressions.
- W2 ещё не завершён: анализ, archive/backfill и dashboard coordinators пока остаются в старых owners. Не объявлять их вынесенными до отдельного тестируемого переноса.

**Next:** закончить W2 там, где перенос явно снижает ответственность без смены UX; затем W3 начать с одной SQLite transaction/connection policy и тематических store adapters, не менять схему.
