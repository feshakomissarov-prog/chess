# M4.6 — Desktop Experience execution log

**Статус:** реализация начата 2026-11-02. Рабочая ветка `codex/m46-desktop-experience`, отдельный draft PR. Источник требований — [`M4_6_DESKTOP_EXPERIENCE.md`](../specs/M4_6_DESKTOP_EXPERIENCE.md) v1.1 и issue [#40](https://github.com/feshakomissarov-prog/chess/issues/40). Не выполнять merge самостоятельно.

## Базовая точка

- `origin/main` на старте: `3387dd38643eaa4ea2fac17fa17ba08cfc43e47f`; merge PR #30 присутствует в истории.
- Локальная ветка создана от этого SHA. Существующая незатреканная `.venv312/` оставлена без изменений.
- Исходное приложение: единый большой `window.py`, отдельные `StatisticsGameDialog` и `_open_saved_game`, существующий `GameRepository` schema v17 и `latest_analysis_detail`; текущая тема светлая, навигация не соответствует stable routes; BoardView min size 440.
- Реальная SQLite, токены, OAuth, Lichess, пользовательский архив и Stockfish в автоматических проверках не используются.

## Этапы

| Этап | Содержание | Статус / доказательства |
| --- | --- | --- |
| A0 | Issue #40: generation-safe disconnect/revoke, stale stream suppression, Event/Barrier tests | Выполнен локально, commit pending |
| A | Аудит маршрутов/read-model, дизайн-токены, baseline | В работе |
| B | AppShell, header/sidebar, responsive route navigation | Ожидает A0/A |
| C | Главная, честные KPI, последние три реальные партии/empty states | Ожидает B |
| D | Board workspace sizes/inspector; единый GameReviewWorkspace; интерактивный graph и per-side summary; улучшение ошибки | Ожидает C |
| E | Оригинальные звуки, mute/volume и dedup событий | Ожидает D |
| F | CMD/debug launcher, optional shortcut action, single instance, startup diagnostics | Ожидает E |
| G | Workspace restore, loading/notifications/accessibility/responsive behavior | Ожидает F |
| H | Регрессии, display/DPI acceptance, полный Windows CI на точном SHA, handoff | Ожидает A0–G |

## Решения

- Используем существующие SQLite таблицы и `latest_analysis_detail`; миграция не планируется до доказанной необходимости.
- Новые UI-компоненты выносятся из `window.py`; read-model отделён от Qt painting, график выбирает только сохранённые оценки.
- Любые невычислимые метрики отображаются как «н/д»/«нет данных». Никакого engine analysis при открытии обзора/карточки и никакой помощи в live human game.
- На каждом этапе фиксируются изменённые файлы, команды и результат; непроверенная ручная визуальная приёмка остаётся явно `НЕ ПРОВЕРЯЛАСЬ`.

## Записи по этапам

### A0 — OAuth/PAT disconnect race (#40)

- `disconnect_account()` теперь только отменяет локальную сессию и сохраняет выбранный credential; PAT никогда не проходит remote revoke. Для OAuth добавлено отдельное подтверждаемое действие revoke, которое захватывает OAuth store, сравнивает credential перед удалением и не может удалить замену.
- События/status/auth/error для Lichess получают `auth_generation`; MainWindow применяет только текущую generation. Запуск stream/game stream атомарно связывает generation с session stop-event. Смена метода инвалидирует callback/streams/games; старый worker не удаляет новую OAuth запись и не меняет сессию с тем же username.
- Добавлены Event-based тесты на PAT disconnect → OAuth, старый 403/429 после переключения, отложенный gameFull, идентичный username, replace-during-revoke и UI-фильтрацию queued status.
- Целевой прогон Windows/Python 3.12 offscreen: `compileall src tests` PASS; `pytest tests/test_lichess_credentials.py tests/test_lichess.py tests/test_window.py -k 'disconnect or event_stream or stream_start or credential or oauth_revoke or previous_auth_generation or manual_connect_defers or manual_connect_restores or new_generation_can_restore or late_old_game_start'`: **34 passed, 194 deselected**.
- Ограничение: настоящие Lichess/OAuth не запускались; race доказан fake `Event` concurrency.

### A — исходный аудит

- Подтверждены две параллельные поверхности завершённой партии (`_open_saved_game` и `StatisticsGameDialog`) и единый SQL источник `latest_analysis_detail`; BoardView минимум 440dp мешает пресету 400dp. MainWindow имеет 4K строк, поэтому новые theme/shell/review components будут вынесены в отдельные модули.
- Нет миграции/новой базы в M4.6. Dashboard read-model должен брать ограниченные последние партии и существующие profile queries; открытие экрана остаётся read-only.
