# Итоговое независимое ревью PR #25 — SHA b2ef275 и переход к ручному пилоту

**Проверено:** 2026-11-01. **Head:** [PR #25](https://github.com/feshakomissarov-prog/chess/pull/25) `b2ef2752df027d6ff13fa93ef0ce8e8955c492e2`, ветка `codex/pr24-review-fixes`, base `codex/m3-windows-diagnostics` (#24), **draft, unmerged**, GitHub сообщает `mergeable=true` **только по отношению к PR #24**. **PR #8 → main** по-прежнему `mergeable=false`; весь стек #8–#25 не интегрирован в `main`.

**Заключение по текущему изменению:** **PASS на уровне кодового ревью и автоматических регрессий; GO для одного изолированного Windows GUI smoke с Python 3.12+**. Не выдавать это за готовность к merge, за проверку пользовательской SQLite или за подтверждённую работоспособность настоящего Lichess/Stockfish.

## Подтверждённые факты

1. **CI на точном SHA:** [Actions run #41265056278](https://github.com/feshakomissarov-prog/chess/actions/runs/41265056278), job `124726024015`: Windows Server 2025, Python **3.12.10**, PySide6/Qt/shiboken6 **6.12.0**, **206 passed за 128.21s**, compileall и изолированное Qt-offscreen окно **PASS**. Лог проверен непосредственно. Локальные `109 API/SQLite + 12 адресных Qt` — только отчёт Codex, отдельно не воспроизводились этим ревью. Ранее наблюдавшийся native crash Windows/Python3.11.5/shiboken6 **не имеет доказанной причины**.
2. **Сверка с официальным Lichess API:** [GameSource enum](https://github.com/lichess-org/api/blob/master/doc/specs/schemas/GameSource.yaml), [GET /api/account/playing schema](https://github.com/lichess-org/api/blob/master/doc/specs/tags/games/api-account-playing.yaml), [stream gameStart AI example](https://github.com/lichess-org/api/blob/master/doc/specs/examples/stream-gameStart-ai.json.yaml). Встроенный AI обозначается `source:'ai'`, `opponent:{id:null,ai:1..8}`; отдельный BOT-account — не исключение fair-play. `LICHESS_GAME_SOURCES` в новом коде совпадает с enum по названиям.
3. **Единый классификатор:** `src/chess_coach/lichess/session.py::classify_lichess_opponent` требует `source=='ai'`, **явный** `opponent.id is None`, `type(level) is int`, `1<=ai<=8`, без противоречащего `title`. `source` из неизвестного enum, неверный `ai`, отсутствующий `id`, `source:'friend'` с `ai`, посторонний `BOT` ⇒ не `official_ai`, а `unknown/bot_account/human`.
4. **Fair-play по всему GUI:** `window.py::_active_game_blocks_engine` допускает только `official_ai`. `LichessGameSession.is_ai_opponent` использует тот же классификатор. `open_active_game()` устанавливает актуальную серверную identity; `gameStart` тоже; `gameFull/gameState` обновляют позицию, **не** класс соперника. BOT/non-AI `gameStart` устанавливает `_analysis_stop`, AI `gameStart` не делает ложную отмену.
5. **Regression tests:** `test_lichess_opponent_classifier_requires_the_official_ai_signature`; `test_session_preserves_authoritative_ai_identity_across_game_state`; `test_connected_analysis_allows_only_official_lichess_ai_and_blocks_bot_accounts`; `test_open_active_game_carries_official_ai_identity_into_session`; `test_game_start_bot_account_stops_analysis_but_builtin_ai_does_not`, отдельно прежние connected Cancel, late reply / reconnect, malformed/50 API fail-closed и takeback. Тесты **есть и прошли в полном Windows CI**.
6. **Не переписывались** миграция Puzzle Activity v8→v9, stable identity, CTE-агрегаты/35k стресс, QProcess lifecycle; действуют предыдущие независимые ревью и тесты. На этом SHA правки только `session.py`, `window.py`, `tests/test_lichess.py`, `tests/test_window.py`, `docs/M3_M4_PROGRESS.md`.

## Вывод о качестве и границы доверия

**Новых блокирующих ошибок в изменённых местах PR25 не выявлено.** Предыдущая P0 инверсия «BOT = допустимый AI» закрыта; предыдущие P1 Cancel, неполный active snapshot и stale session тоже закрыты. Для **кодовой части текущего fix PR** больше не требуется выдавать Codex очередное адресное ТЗ.

**Остаётся проверить отдельно:**
- Один реальный **offline** UI запуск владельцем на Windows/Python3.12+, свободные ходы за два цвета и режим предходов. В offine Lichess, реальные партии и постоянная SQLite недоступны **по проекту**.
- **Full-stack интеграция** с обновлёнными документами `main`: PR #8→main всё ещё конфликтует. Нужен отдельный candidate branch/PR с новым CI SHA и сохранением `README`, `PRODUCT_VISION`, `ROADMAP`, `PROJECT_STATUS` и всего `docs/research`.
- Ручной реальный 5-game архив, авторизация, 1 finished-game UCI, реальные пазлы и миграция на копии пользовательской SQLite **NOT RUN**; принимать отдельным осознанным решением после backup.
- Серверный snapshot Lichess является **моментальным**; нельзя строго гарантировать отсутствие новой игры, начатой на другом устройстве уже после снимка. Существующие gameStart events останавливают анализ, но возможна сетeвая задержка. Не обозначать это как абсолютно надёжный детектор всех будущих игр. Поддерживать строгое разделение live human play и постпартийного движка.

## Безопасный ручной пилот #1 — офлайн (можно после данного ревью)

PowerShell:
```powershell
cd G:\project\chess
git status --short
git fetch origin
git switch codex/pr24-review-fixes
git pull --ff-only origin codex/pr24-review-fixes
git rev-parse HEAD
py -3.12 --version
if (-not (Test-Path .\.venv312\Scripts\python.exe)) { py -3.12 -m venv .venv312 }
.\.venv312\Scripts\python.exe -m pip install -e ".[dev]"
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
.\scripts\run_windows_manual_smoke.ps1
```
**Ожидаемый HEAD:** `b2ef2752df027d6ff13fa93ef0ce8e8955c492e2`. Если `git status --short` не пуст, **остановиться, не сбрасывать изменения**. Если `py -3.12` отсутствует — установить Python 3.12 отдельно **по решению владельца**, скрипт установки не делает. Если `.venv312` уже существует, проверить, что именно Python3.12+.

**Ручная проверка:** на главной/offline открыть «Локальная доска». Режим по умолчанию «Свободная доска (за обе стороны)»: клик e2→e4, чёрными e7→e5; drag g1→f3 и b8→c6; индикатор чередует цвета. Правый клик: стрелка/пометка, ход очистит старую разметку. «Отменить последний ход» и история `|< < > >|`; возврат к последней позиции. Переключить на «Тренировка предходов (мой цвет)»: в позиции чёрных выбрать белый предход, нажать «Сыграть тестовый ход соперника», убедиться, что предход применится только при легальности, Esc снимает. Вернуть «Свободная доска», убедиться, что история не потеряна. «Завершённые партии» и «Статистика партий» на **временной пустой** SQLite, настройки и закрытие через X без ошибки/зависания.

Если native crash, **не повторять циклически**. Зафиксировать время, Python/PySide6/Qt/shiboken6 версии, консоль/Windows Event Viewer code/module/offset, состояние Dr.Web, без изменения антивируса. Не публиковать токены, PGN или приватные пути.

## После offline пилота (пока НЕ запускать автоматически)

Manual-connect использует **постоянную** пользовательскую SQLite и запускает её миграции **ещё до нажатия Connect**. Сначала создать и проверить backup пользовательской БД, потом **только после разрешения владельца** `.\scripts\run_windows_manual_smoke.ps1 -Mode manual-connect -ConfirmPersistentProfile`. Вводит фразу `USE-MY-LOCAL-DATA`. Для реального online пилота — 5 завершённых партий и один анализ **завершённой** партии доверенным локальным UCI, отдельно проверять Cancel/429/дубли/перезапуск, без полной архивации, без подсказок в live human play.

**Решение merge:** технические исправления PR25 приняты к ручной офлайн-приёмке. **Слияние не одобрено**, пока нет ручного пилота, разрешённого интеграционного PR и CI на интеграционном SHA. Долгосрочная лестница M3/M4 → M5 → M6 и образовательные research documents не меняются.
