# PR #16 — аудит native crash и жизненного цикла Stockfish

Дата: 2026-11-01. Проверен [PR #16](https://github.com/feshakomissarov-prog/chess/pull/16) на SHA `7d93e7c04320a73d5dcff2d6be09b6782cacb47e`, base `codex/m3-m4-review-fixes`, draft. PR #15 закрыт как заменённый. GitHub Actions run [#41250753509](https://github.com/feshakomissarov-prog/chess/actions/runs/41250753509): **152 passed на Windows/Python 3.12.10**, compileall и Qt offscreen window initialization прошли. Локально Windows Python 3.11.5: 78 test_lichess + 8 file-backed SQLite; полный Qt-suite прерван native access violation, причина НЕ установлена.

## Что подтверждено

Код и интеграционные тесты подтверждают добавление защиты raw-only PGN для unsupported variants, отмены архивных/puzzle workers перед закрытием SQLite writer, owner-specific opponent в account_games, закреплённого start-cursor для long backfill, корректных SQLite rollback/resume и отдельного OAuth puzzle token. Прямых признаков вредоносных команд или отключения антивируса в изменениях не обнаружено. **CI success — не доказательство локальной GUI стабильности.**

## P1 — реальная отмена Stockfish работает некорректно

**Доказательство:** `src/chess_coach/lichess/analysis.py::_analyze_interruptibly()` создаёт stopper thread, вызывающий `engine.stop()` во время `engine.analyse()`. Но в используемой библиотеке `python-chess` **`chess.engine.SimpleEngine` не имеет метода `stop()`**; стоп есть у **`SimpleAnalysisResult.stop()`**. Метод SimpleEngine не заявлен в upstream `niklasf/python-chess/chess/engine.py` (класс SimpleEngine). Текущее `except Exception: pass` скрывает AttributeError; реальный анализ продолжается до окончания node limit, и лишь затем функция может поднять InterruptedError. Тест `test_engine_stop_exception_is_reported_as_cancelled_analysis` использует ложный mock `StoppableEngine.stop` и поэтому не проверяет API настоящего engine.

**Исправить:** перейти на `engine.analysis(board, limit)` с управляемым `SimpleAnalysisResult.stop()` (или иной официально поддерживаемый UCI cancel flow). Во время анализа проверять stop_event, корректно завершать iterator/stream и освобождать ресурсы; при отмене не сохранять частичный run как `complete`; `engine.quit()` даже при exception; не подвешивать UI. Не смешивать graceful stop и forced process kill: если нужен kill для зависшего UCI binary, таймауты и безопасная отдельная политика.

**Тесты:** mock *реального протокола* `analysis()` / `SimpleAnalysisResult.stop` БЕЗ `engine.stop`; cancellation while calculating first position, on gameStart human event, error in stream, stop during engine startup, quit called, no completed cache saved. При необходимости проверить официальное API `python-chess` в установленной версии.

## P1 — shutdown не контролирует собственный Stockfish worker

**Доказательство:** `window.py::_start_game_analysis` создаёт daemon `threading.Thread(...)` без сохранения handle, кроме `self._analysis_stop`; `MainWindow.closeEvent` вызывает `self.lichess.close()` и `game_repository.shutdown()`, **не вызывает `self._analysis_stop.set()` и не ждёт завершения analysis worker**. При закрытии возможны продолжающиеся UCI/SQLite обращения после жизненного цикла окна, а также сигналы к уничтожаемым Qt объектам. Не утверждать, что именно это вызвало локальную access violation: это отдельный подтверждённый дефект жизненного цикла.

**Исправить:** хранить analysis worker, при close выставлять stop, ждать завершения по ограниченной стратегии в общем shutdown с архивом/puzzles, только потом закрывать SQLite writer и Qt widgets; запретить вторую параллельную задачу анализа до окончательного завершения первой; отменять при начале живой игры с человеком; сигналы прогресса/завершения не должны обращаться к удалённому GUI. Qt event loop должен оставаться отзывчивым, никаких бесконечных join в GUI thread.

**Тесты:** QApplication/qtbot окно + fake engine с блокирующим analysis iterator; закрытие во время расчёта; stop вызван, thread закончился и quit вызван до shutdown БД; нет `RuntimeError: cannot schedule new futures after shutdown`, Qt signal/use-after-free. Проверить несколько последовательных запусков анализа и прерываний.

## P0-диагностика — local Python native access violation / Dr.Web

Сообщено: Windows Python 3.11.5, процесс `python.exe` завершился «ошибка чтения памяти» во время полного pytest-qt suite в `_close_widgets`; Dr.Web выдал «Процессу запрещено изменение запущенного приложения» для **`C:\Windows\System32\WerFault.exe`, PID 26032**. WerFault — штатный Windows Error Reporting. Его запуск может **следовать за падением** Python; сообщение Dr.Web **не доказывает причинной связи или заражения**. По текущим данным нельзя установить, была ли причина в PySide6/Qt, тестовых ресурсах, коде closeEvent, несовместимых native DLL, видеодрайвере или другом процессе.

**Не отключать Dr.Web/Defender, не создавать исключения, не менять ExecutionPolicy, не удалять/подменять WerFault.exe.** Нельзя утверждать «ошибка устранена», пока не найден воспроизводимый сценарий или собраны данные.

**Задачи по диагностике:**
1. Read-only: собрать из Windows Event Viewer Application (обычно Event ID 1000 Application Error и 1001 Windows Error Reporting) **точное время, failing process, exception code, faulting module**; из Dr.Web — карточку события с временем/путём/действием. В отчёт не включать токены/имена пользователя/персональные пути без редактирования. Не запускать новые crash tests автоматически и многократно.
2. Проверить версии PySide6, shiboken6, pytest-qt, Python 3.11.5/3.12.10, PATH/plugin DLL, отсутствие второго Qt binding; сопоставить с CI, где 152 pass.
3. Без повторного десятка падающих полных прогонов: **по одному изолированному тестовому модулю/подмножеству** через `QT_QPA_PLATFORM=offscreen`, `python -X faulthandler -m pytest ... -q`, начиная с окна, закрытия worker и события Qt destroy. Использовать только fake transport/engine и временную SQLite. При первом воспроизведении остановиться, сохранить stack/log/exit code. Не прикреплять memory dumps публично.
4. Не обходить антивирус ради прохождения теста. Если проблема локальная и не воспроизводится в CI, честно поставить `LOCAL NATIVE CRASH: UNRESOLVED`; не подменять результат `Windows CI: PASS`.

## Приёмка

Один ограниченный follow-up PR поверх #16 (draft), с `analysis()`/cancel, управляемым shutdown Stockfish и тестами. В `docs/M3_M4_PROGRESS.md` указать SHA, actual tests, error code/faulting module если доступен, отличается ли воспроизведение на Python 3.12.10, и незакрытые риски. Пользовательский полноценный GUI smoke **пока не начинать**, если на локальной машине повторяется падение Python. Реальные Lichess/OAuth/Stockfish/архивные данные без разрешения не использовать, ничего не merge.

Дальше: после стабилизации Windows GUI — ручной offline smoke, резервная копия пользовательской SQLite, online pilot 2–5 завершённых партий, реальный Stockfish на одной завершённой партии по отдельному разрешению, и только затем решение о последовательном merge PR #8–#16.
