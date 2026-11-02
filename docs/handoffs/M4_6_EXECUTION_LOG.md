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
| A0 | Issue #40: generation-safe disconnect/revoke, stale stream suppression, Event/Barrier tests | Выполнен, commit `e0a397d` |
| A | Аудит маршрутов/read-model, дизайн-токены, baseline | Выполнен; tokens вынесены в `ui/theme.py` |
| B | AppShell, header/sidebar, responsive route navigation | Выполнен локально; commit checkpoint ниже |
| C | Главная, честные KPI, последние три реальные партии/empty states | Выполнен, checkpoint ниже |
| D | Board workspace sizes/inspector; единый GameReviewWorkspace; интерактивный graph и per-side summary; улучшение ошибки | Выполнен локально, checkpoint ниже |
| E | Оригинальные звуки, mute/volume и dedup событий | Выполнен, commit `61212bd` |
| F | CMD/debug launcher, optional shortcut action, single instance, startup diagnostics | Выполнен, commit `601df64d71242a1bd56818c9466a0b8bb0270a30` |
| G | Workspace restore, loading/notifications/accessibility/responsive behavior | Выполнен, commit `b3b469ed7bf3350dfa68ef58b2e362c85b4b87c8` |
| H | Регрессии, display/DPI acceptance, полный Windows CI на точном SHA, handoff | Локальные проверки PASS; hosted Windows CI run #41370711058 PASS на exact handoff SHA `20eb5780b3a7d6a47019b0ffe47d8ee8d5281283` |

## Решения

- Используем существующие SQLite таблицы и `latest_analysis_detail`; миграция не планируется до доказанной необходимости.
- Новые UI-компоненты выносятся из `window.py`; read-model отделён от Qt painting, график выбирает только сохранённые оценки.
- Любые невычислимые метрики отображаются как «н/д»/«нет данных». Никакого engine analysis при открытии обзора/карточки и никакой помощи в live human game.
- На каждом этапе фиксируются изменённые файлы, команды и результат; непроверенная ручная визуальная приёмка остаётся явно `НЕ ПРОВЕРЯЛАСЬ`.

## Записи по этапам

### Независимое ревью PR #41 — P0: Fair Play lease и review visibility

- Перепроверка safety ответов связывает их с `game_id`, текущими `line_revision`/`line_hash`, выбранным `account_id`, Lichess service, username и `auth_generation`; дополнительно сверяется current SQLite line и активный review route. Переключение партии/route и auth invalidate отбрасывают pending response без побочного изменения уже открытой другой партии.
- Любое новое human/unknown `gameStart`, account disconnect/auth change, fail/429 или устаревшая проверка отзывает уже выданный workspace safety. Активное упражнение останавливается, кандидат и сохранённые PV очищаются из UI, действия check/hint блокируются. Offline isolated mode сохраняет прежний безопасный сценарий.
- В отказном/ожидающем состоянии единый GameReviewWorkspace скрывает оценки графика, quality badges, CP и source/coverage; PGN и обычная навигация ходов сохраняются. Открытие другого сохранённого game сначала отзывает предыдущий lease; подключённый режим запускает preflight для нового game.
- Регрессии fake service проверяют success→human game (до/во время упражнения), A→B→late success, auth-generation/disconnect revoke, incomplete/unknown snapshot и безопасный offline path. Целевой Qt прогон: `pytest tests/test_window.py -k 'review_training_safety or human_game_start_revokes_safety or human_game_start_stops_active_review or late_safe_reply_for_game_a or disconnect_revokes_a_previously_successful' tests/test_game_review.py` — **8 passed**. `compileall` и diff check выполняются на checkpoint.
- Code/test commit: `22fc5e279ae2b40fca541ff5d25f127faff41fa9`. Журнал/статус фиксируются следующим docs checkpoint. Настоящие OAuth/Lichess, Stockfish и пользовательская SQLite не использовались.

### Независимое ревью PR #41 — P1: home read-model, карточки и единый просмотр

- Home dashboard теперь имеет выбор аккаунта и контроля времени с сохранением контекста в QSettings. KPI строятся в SQL scope: число партий выбранного владельца; только официальная Lichess accuracy с `n/N`; complete/partial analysis и охват собственных decision pairs; ошибки на 100 оценённых собственных ходов с явным числителем и знаменателем. Локальная accuracy не подменяет официальный показатель. Архив целиком/список ходов не загружаются.
- Три recent cards ограничены страницей текущего выбранного аккаунта/контроля и содержат официальную accuracy, отдельные inaccuracies/mistakes/blunders, source, статус и охват собственных ходов. Подтверждённого классификатора сильных ходов нет: значение честно `н/д`; candidate `best_move_confirmed` не представлен как подтверждённый.
- Главная разделена на основные действия «Играть» и «Разобрать», contextual resume активной партии/последнего просмотра и закрытый по умолчанию раздел «Ещё» для вспомогательных операций. «Вернуться в текущую партию» остаётся доступно вне раздела «Ещё».
- Unified review: ось графика — полное число ходов; редкие оценки не соединяются через пробелы, клик выбирает ближайшую сохранённую оценку, SAN-навигация сохраняется по всей линии. Tooltip показывает score/mate, источник, статус, coverage и отсутствие интерполяции. Добавлены вкладки «Ходы», «Разбор», «События», условная «Заметки», события/PV для выбранного ply и переходы между пригодными собственными ошибками. Evidence и оценки подчинены P0 safety gate.
- Целевые Qt/SQLite регрессии на dashboard scope/cards, sparse graph (>70 ply, gap, mate, unknown), PV/safety masking и home context — **31 passed** на первом объединённом checkpoint; после финальной правки разрыва графика и home-кнопки объединённый прогон — **23 passed** для dashboard/review/home subset. Launcher targets выполняются отдельно.
- Code commits: график/вкладки `9d4eb1ab001f4bd26be811a5626a80ddecd23e1c`; account-scoped dashboard/home `1e5db1dd7be2cf8197ef4df674067d48d57cb18b`; отдельная проверка gaps `de9c49b7f0f8bffbb1bc7f741f7f3d53f6bd5444`.

### Независимое ревью PR #41 — P2: launcher cwd и layout regressions

- `launch_chess_coach.cmd` и `debug_chess_coach.cmd` переходят в root, найденный через `%~dp0`, до запуска приложения; выставлен UTF-8 code page для диагностик CMD. Runtime test проверяет тот же `pushd "%ROOT%"` контракт из независимого cwd; static contract подтверждает строку в обоих launchers. Само приложение и Credential Manager из теста не стартуют.
- UI regressions подтверждают выбранный account/clock context, CTA и свернутый «Ещё», узкую раскладку, sidebar 212/60 logical px, размеры доски 400/480/560/640 и восстановление окна/workspace. Qt тесты проходят offscreen.
- Launcher commit: `68f5617905d2313fa824c57ab5e2745430c33b1c`. Физические DPI/мониторы, интерактивная визуальная приёмка и exact-head Windows CI будут зафиксированы отдельно ниже.
- Полный локальный прогон на code SHA `0f25ab8759f6bd06dfc1f0f9ec5aa33c4be37364`: `QT_QPA_PLATFORM=offscreen python -m pytest -o addopts= -q` — **439 passed за 123.56 с** на Windows / CPython 3.12.10 / PySide6 6.12.0. `compileall src scripts tests`, `pip check`, `git diff --check origin/main` — PASS. Первый полный прогон дал 437 PASS и два выявленных состояния disabled у checkable `QGroupBox`; заменён на независимый toggle и подтверждены исправления целевыми тестами, затем повторный полный прогон прошёл.
- Автоматическая проверка layout использует Qt logical pixels/offscreen. Физические DPI, multi-monitor, реальные drag/click на доске, звуковое устройство, реальная OAuth/PAT сессия, пользовательская SQLite/PGN, Stockfish и live Lichess — **НЕ ПРОВЕРЯЛИСЬ**. Launcher cwd тестирует системный `pushd` алгоритм из произвольной временной папки и статически связывает его с обоими CMD-файлами; сам `.cmd` с Credential Manager-профилем вручную не запускался.
- Hosted Windows CI на code SHA `1bd888ffc124157221136db2ead9d78b59d89794`: [run #41378791447](https://github.com/feshakomissarov-prog/chess/actions/runs/41378791447) — **SUCCESS, 439 passed за 263.32 с**, Windows Server 2025 / CPython 3.12.10 / PySide6 6.12.0; compileall и isolated offline window initialization PASS. Итоговый код SHA на этом checkpoint совпадал с CI SHA; последующий commit содержит только этот отчёт и status metadata, после него будет отдельная exact-head CI проверка.
- Реальные Lichess/OAuth/Stockfish/пользовательские данные не запускались. PR #41 draft, merge не выполнялся.

### A0 — OAuth/PAT disconnect race (#40)

- `disconnect_account()` теперь только отменяет локальную сессию и сохраняет выбранный credential; PAT никогда не проходит remote revoke. Для OAuth добавлено отдельное подтверждаемое действие revoke, которое захватывает OAuth store, сравнивает credential перед удалением и не может удалить замену.
- События/status/auth/error для Lichess получают `auth_generation`; MainWindow применяет только текущую generation. Запуск stream/game stream атомарно связывает generation с session stop-event. Смена метода инвалидирует callback/streams/games; старый worker не удаляет новую OAuth запись и не меняет сессию с тем же username.
- Добавлены Event-based тесты на PAT disconnect → OAuth, старый 403/429 после переключения, отложенный gameFull, идентичный username, replace-during-revoke и UI-фильтрацию queued status.
- Целевой прогон Windows/Python 3.12 offscreen: `compileall src tests` PASS; `pytest tests/test_lichess_credentials.py tests/test_lichess.py tests/test_window.py -k 'disconnect or event_stream or stream_start or credential or oauth_revoke or previous_auth_generation or manual_connect_defers or manual_connect_restores or new_generation_can_restore or late_old_game_start'`: **34 passed, 194 deselected**.
- Commit: `e0a397d0e2a219103fa46514e978a4fedba12d4d`, pushed to feature branch.
- Ограничение: настоящие Lichess/OAuth не запускались; race доказан fake `Event` concurrency.

### A — исходный аудит

- Подтверждены две параллельные поверхности завершённой партии (`_open_saved_game` и `StatisticsGameDialog`) и единый SQL источник `latest_analysis_detail`; BoardView минимум 440dp мешает пресету 400dp. MainWindow имеет 4K строк, поэтому новые theme/shell/review components будут вынесены в отдельные модули.
- Нет миграции/новой базы в M4.6. Dashboard read-model должен брать ограниченные последние партии и существующие profile queries; открытие экрана остаётся read-only.

### A/B — theme tokens и AppShell

- Добавлен `ui/theme.py` с утверждёнными dark-v1 HEX, общей QSS-темой и вычислением WCAG contrast ratio; обычные primary/secondary тексты проверяются на app/sidebar/surface backgrounds.
- Top navigation перенесена в sidebar: стабильные маршруты Главная/Играть/Мои партии/Аналитика/Задачи; Settings закреплены снизу. Sidebar 212dp на широкой главной и 60dp на доске/узком окне; ручной override живёт в пределах сессии. Header фиксирован на 64dp.
- Маршруты используют существующие экраны/обработчики; игровой поток и Lichess state machine не переписаны. Qt-тесты изолируют хранилище.
- Проверки Windows CPython 3.12.10, Qt offscreen: `compileall src tests` PASS; `pytest tests/test_theme.py tests/test_window.py` — **100 passed**; `git diff --check` PASS.
- При первом полном прогоне выявлены две тестовые fake-service заглушки без generation-tagged signal из A0; заглушки приведены к актуальному контракту, повторный прогон прошёл.
- Ручная визуальная проверка, 150–200% DPI, ширина 1024dp и живой Lichess — **НЕ ПРОВЕРЯЛИСЬ**.
- Следующий этап после A/B: C — dashboard с ограниченным read-model, реальными источниками KPI и тремя последними карточками.

### C — Dashboard и последние партии

- Добавлен `ui/dashboard.py`: четыре KPI считаются агрегатами из текущей SQLite (`games`, завершённые результаты с известным цветом, current `analysis_runs`, `puzzle_attempts`); последний список запрашивается через существующий paginated `search_games(page_size=3)`. Нет полной загрузки PGN/ходов и нет фиктивных чисел; для неизвестного цвета/нулевой выборки отображается «н/д».
- Главная показывает быстрые действия (AI и свободная доска), 3 последних карточки и сохраняет остальные архивные действия под «Ещё». Нажатие карточки открывает существующий просмотр сохранённой партии.
- Регрессии на временной реальной SQLite проверяют пустую базу, результаты/ограничение до 3 карточек и неизвестный цвет. Совместный Qt-прогон `tests/test_theme.py tests/test_dashboard.py tests/test_window.py`: **103 passed** (Python 3.12.10, Qt offscreen). `compileall`/`git diff --check` повторно проверены при checkpoint.
- Результаты корректны относительно `games.color`; для одной игры, связанной с несколькими аккаунтами, домашний свод использует owner color поля games и не умножает строки join-ом. Экран статистики остаётся отдельным фильтруемым источником.
- Следующий этап: D — size presets и единое рабочее пространство разбора; миграция схемы пока не нужна.

### D — Board workspace, unified review, ошибка-упражнение

- Основная и обзорная доски поддерживают 400/480/560/640 dp; применяются фактические approved board light/dark/selection цвета через renderer `chess-widgets`, взаимодействия библиотеки сохранены. Target preset persistится, доступный квадрат clamp-ится до 320dp.
- Добавлен `lichess/game_review.py`: read-only модель current revision, SAN/UCI + before/after FEN snapshots, обе стороны отдельно, owner color, source-tagged official Lichess accuracy, analysis source/status/coverage, notes. Никаких UCI/network calls при открытии; открытие сохранённой/завершённой партии и Statistics-dialog переходят в единый `GameReviewWorkspace`.
- Workspace синхронизирует позицию доски, список ходов, график и selected move. Маркеры имеют отдельные формы good/inaccuracy/mistake/blunder; mate label остаётся mate, не конвертируется в отображаемый CP. Обе стороны показывают count + comparable coverage; denominator=0 отображается «н/д», partial count имеет явную подпись. Официальная accuracy не смешивается с локальными move classes.
- Exercise строится только по собственным mistake/blunder на завершённой стандартной текущей линии. Safe evidence policy `saved-stockfish-pv-improvement-v1`: тот же complete Stockfish run, legal PV/alternative на pre-move ply, depth>=15 ИЛИ nodes>=50k, improvement против сыгранной позиции не менее 30cp с учётом цвета. Нет engine-on-click; неопределённые варианты не называются ошибочными. На время решения скрыты график, inspector/список ходов и детали; локальная копия начинается на ply−1. После подтверждения показывается сравнение; исходная партия/PGN не меняются. Есть hint по запросу, repeat, skip и итог попытки.
- Для подключённого Lichess exercise остаётся закрытым до явного preflight `/api/account/playing`; пустой/только встроенный AI snapshot разрешает; человек, неизвестное/обрезанное состояние, 429/error или устаревший generation блокируют. В изолированном `--offline` учебный локальный сценарий доступен без сети.
- Временная SQLite покрывает owner color/две стороны, source/status/coverage, legal + improved PV, низкое качество/partial, запрет Lichess-engine source, UI синхронизацию и неизменность PGN. API preflight покрыт fake AI/human/unknown и stale generation. Размеры, board palette, unified route и старые window сценарии включены.
- Windows CPython 3.12.10 / Qt offscreen: `compileall src tests` PASS; `pytest tests/test_theme.py tests/test_dashboard.py tests/test_game_review.py tests/test_board_view.py tests/test_window.py`: **138 passed**; `git diff --check` PASS.
- Stockfish real binary, online preflight, Lichess OAuth, 150–200% DPI, визуальная композиция на 1024×700 и пользовательская SQLite — **НЕ ПРОВЕРЯЛИСЬ**.
- Следующий этап: E — оригинальные локальные chess sound cues, mute/volume and event dedup.

### E — локальные звуки и защита от дублей

- Добавлен `ui/sounds.py`: локальные короткие PCM WAV сигналы синтезируются приложением, сторонние звуковые наборы не используются и сеть не вызывается. `QSoundEffect` воспроизводит их неблокирующе; если multimedia/audio недоступны, приложение продолжает работать без звука.
- Сигналы разделены на обычный ход, взятие, шах, рокировку, превращение, предход, окончание партии и уведомление. Приоритет завершения результата выше классификации хода. Настройки ходов/уведомлений/явного выбора исторического хода и громкость 0–100 сохраняются в QSettings; defaults: moves ON, notifications ON, history OFF, volume 45.
- Live own move ждёт authoritative Lichess event, локальный ход подаёт сигнал после принятия контроллером; повторный ACK/stream ply и повторное gameFinish подавляются event deduplicator. Reject и фоновые события не озвучиваются. Premove placement имеет отдельный тихий click; исполнение звучит только после подтверждения. История звучит только при явном клике по записи и включённой настройке; навигация, колесо, refresh и rebuild остаются тихими.
- Добавлены unit и Qt regressions на WAV формат, классификацию check/capture/castle/promotion/mate, move ACK/dedup, pending rejection, отдельный notification cue, mute/volume, premove, повторное завершение, explicit history и отсутствие звука при навигации.
- Windows CPython 3.12.10 / Qt offscreen: `pytest tests/test_sounds.py tests/test_theme.py tests/test_dashboard.py tests/test_game_review.py tests/test_board_view.py tests/test_window.py`: **148 passed**; `compileall src/chess_coach tests` PASS; `git diff --check` PASS. Stage commit: `61212bd Add original configurable chess sounds`.
- Реальное аудиоустройство/задержка <100ms и ручная проверка динамиков на Windows — **НЕ ПРОВЕРЯЛИСЬ**.
- Следующий этап: F — прозрачные CMD/debug launchers, явное создание Desktop shortcut по действию владельца, single-instance и ограниченные startup logs.

### G — сохранение рабочего места, тихие уведомления и адаптивность

- Состояние окна сохраняется debounced в QSettings: geometry/maximized через `saveGeometry`, dark-v1, стабильный последний route, sidebar mode, размер доски, account id без токена; для последнего завершённого review — только `game_id`, `line_revision`, `line_hash`, просмотренный ply и вкладка inspector. PGN/FEN не копируются в settings, SQLite не меняется. Фильтры StatisticsDialog хранятся как небольшой JSON state, выбор восстанавливается только если account/filter option всё ещё доступен.
- При старте автоматическое продолжение выполняется только если последним экраном был review, game всё ещё завершён и стандартный, а revision+hash совпадают. Ply/tab восстанавливаются по совпавшей линии. При изменении линии открывается текущая партия с ply 0 и коротким уведомлением; при недоступной партии остаётся Главная. Startup restore отменяется, если пользователь уже открыл другой экран. На Главной добавлена отдельная кнопка «Продолжить разбор последней партии».
- Геометрия окна восстанавливается в Qt logical px и полностью помещается в `availableGeometry` текущих экранов; недоступная сохранённая геометрия помещается на первый экран. Минимальный размер приложения снижен до 500×360 logical px. На узком окне обзорные KPI перестраиваются в две колонки, игровая доска/панель и GameReview инспектор переходят в вертикальный splitter, основные страницы прокручиваются; статистические вкладки тоже имеют scroll wrapper.
- В MainWindow добавлен accessible status bar для кратких событий sync/error/анализа; Lichess/API/Stockfish ошибки больше не создают неожиданный блокирующий warning modal. Успешный завершённый анализ направляет в тот же GameReviewWorkspace вместо отдельного графического диалога. Навигация, настройки, board и review graph получили доступные имена; график поддерживает Home/End/Left/Right с keyboard focus.
- Добавлены regressions на совпадающий resume, stale revision/hash fallback, окно вне/частично вне экрана, отсутствие захвата экрана отложенным restore, persistence statistics filters, unified analysis completion, inline error notice и keyboard graph navigation.
- Windows CPython 3.12.10 / Qt offscreen: `pytest tests/test_window.py tests/test_game_review.py tests/test_theme.py tests/test_dashboard.py tests/test_launcher.py tests/test_single_instance.py`: **135 passed**; `compileall src/chess_coach tests` PASS; `git diff --check` PASS.
- Проверка на физических DPI/мониторах, изменение разрешения в ходе работы, клавиатурный/скринридер smoke и ручная проверка сохранённого окна — **НЕ ПРОВЕРЯЛИСЬ**; для этого нужен владелец на Windows GUI. Следующий этап: H — полный regression matrix, compile/diff, точный Windows CI и финальный handoff.

### H — полная регрессионная проверка и handoff

- Локальный CPython 3.12.10 / PySide6 6.12.0: начальный полный offscreen pytest запуск и `tests/test_board_view.py` завершались native exit без pytest summary. Event Viewer зафиксировал Application Error 1000 / WER 1001: `python.exe` из `.venv312`, модуль `G:\project\chess\.venv312\Lib\site-packages\PySide6\Qt6Core.dll`, exception `0xc0000409` (BEX64). Причина — созданный в новом palette-тесте `BoardView` не был зарегистрирован в `qtbot`; тест завершался с уничтожением unmanaged Qt widget. После фикса `qtbot.addWidget(widget)` palette regression проходит.
- Проверка исправления: `pytest tests/test_board_view.py` — **21 passed**; затем полный offscreen набор — **428 passed за 118.77 с**. `compileall` и `pip check` — PASS. Запусков реального приложения или пользовательского GUI не было.
- Текущая проверка `git diff --check origin/main` — PASS. Удалены только лишние пустые строки EOF в двух тестах, чтобы у PR diff был чистый whitespace check.
- Полная проверка на физических DPI/нескольких мониторах, клавиатура/скринридер с пользователем, звук через реальное устройство, ручной запуск `.cmd` и создание ярлыка с пользовательским профилем — **НЕ ПРОВЕРЯЛИСЬ**.
- Реальные OAuth/Lichess, PAT, архив, пользовательская SQLite и Stockfish не запускались/не открывались. Их проверка не является частью M4.6 CI.
- Hosted Windows CI run [#41370237285](https://github.com/feshakomissarov-prog/chess/actions/runs/41370237285) на code SHA `45f52dbe73dc5ac0a23e851186a2ce28a79fbbf5` — **SUCCESS**, 428 passed за 236.61 с; compileall и isolated offline window initialization PASS. Повторный полный Windows CI [#41370711058](https://github.com/feshakomissarov-prog/chess/actions/runs/41370711058) на exact handoff SHA `20eb5780b3a7d6a47019b0ffe47d8ee8d5281283` — **SUCCESS**, 428 passed за 227.92 с, Windows Server 2025 / CPython 3.12.10 / PySide6 6.12.0; compileall и isolated offline window initialization PASS. CI на `c8d3e81` ранее выявил unmanaged `BoardView` в palette-тесте; тест теперь использует `qtbot.addWidget`, после исправления local/hosted full runs проходят. Текущая запись — docs-only; после неё проверяется exact final repository head. PR #41 остаётся draft.

### F — пользовательский запуск, single-instance и иконка

- Добавлен `launch_chess_coach.cmd` (путь относительно `%~dp0`, ищет `.venv312` затем `.venv`, запускает `pythonw.exe -m chess_coach --manual-connect`, ничего не устанавливает и не использует временный `--offline` профиль); `debug_chess_coach.cmd` запускает обычный `python.exe`, оставляя терминал для диагностики. Отсутствующий/неподдерживаемый runtime даёт инструкцию и пишет только статический код ошибки в `%LOCALAPPDATA%\ChessCoach\logs\chess-coach-launcher.log`.
- Обновлён opt-in `scripts/create_desktop_shortcut.ps1`: создаёт ярлык лишь при явном запуске пользователем, предпочитает `.venv312`, запускает `--manual-connect`, использует собственную ICO, не создаёт автозапуск. `scripts/create_app_icon.py` генерирует собственный 64px ICO из примитивов stdlib, включён в wheel package data и используется как Qt app icon.
- Добавлен profile-scoped `QLocalServer` guard. Идентификатор — SHA-256 нормализованного каталога существующего `QStandardPaths.AppDataLocation`, без записи токенов или иных данных. Повторный запуск отправляет activate-команду и завершается до создания MainWindow/SQLite writer. Сомнительный socket timeout блокирует новый writer вместо попытки удалить возможный живой lock; только явные missing/refused endpoints проходят stale cleanup.
- Добавлены тесты повторного запуска/активации и независимых временных профилей, CLI второй процесс, относительность launch script, offline isolation, args shortcut, оригинальная ICO signature, существующие privacy diagnostics.
- Windows CPython 3.12.10 / Qt offscreen: `pytest tests/test_launcher.py tests/test_single_instance.py tests/test_diagnostics.py tests/test_window.py`: **121 passed**; `compileall src/chess_coach scripts/create_app_icon.py tests/test_launcher.py tests/test_single_instance.py` PASS; `git diff --check` PASS.
- `.cmd`, COM shortcut creation and interactive activation of a second Windows process on the owner's profile were not executed: no user DB/profile changes are allowed during this task. Tests use separate temp profiles; visible GUI remains for owner's manual check.
- Следующий этап: G — save/restore route + review identity, non-blocking loading/notifications, responsive/accessibility regression pass.

### PR #41 — повторное ревью Fair Play, 02.11.2026

- Централизована классификация Lichess-статусов, означающих неизвестное состояние безопасности. 429, reconnect, ошибки подключения/аккаунта, logout и OAuth revoke синхронно вызывают `_invalidate_analysis_preflight`: review lease и упражнение отзываются, график, события, оценки и PV очищаются; параллельно останавливаются live position analysis и batch analysis.
- OAuth revoke после подтверждения сначала проходит через общий unsafe-status guard и только затем вызывает асинхронный сервис. OAuth success/failure статусы не возвращают разрешение. HTTP 429 отзывает lease, но сохраняет подтверждённую идентичность аккаунта, чтобы по окончании Retry-After не ломать существующее восстановление batch readiness. Подключение само по себе не восстанавливает review lease; нужен новый явный полный preflight.
- Добавлены Qt-регрессии: 429/reconnect/network error/account errors и HTTP 401/403; очистка graph/PV, остановка active exercise, отказ hint, отсутствие auto-enable после подключения и успешный повторный preflight; синхронный revoke до вызова FakeService и отсутствие PAT revoke; поздний ответ после смены линии.
- Локальная Windows/Python 3.12.10 проверка: выбранные regressions — **9 passed**; полный offscreen `pytest -q` завершился на 100% (до добавления только одного дополнительного параметра для `Не подключено`); `tests/test_window.py` прошёл целиком. `compileall` и `git diff --check` — PASS. FakeService используется в тестах; реальный Lichess/OAuth/PAT, пользовательская SQLite и Stockfish не запускались.
- Hosted Windows CI на точном SHA после публикации обязателен и фиксируется ниже; физическая ручная GUI-проверка владельца — **НЕ ПРОВЕРЯЛАСЬ**.
