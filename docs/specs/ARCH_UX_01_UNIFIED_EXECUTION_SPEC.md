# ARCH-UX-01 — единое ТЗ Codex: адаптация архитектуры ARCH-01 и полный согласованный Desktop UX Chess Coach

**Дата решения:** 04.11.2026 · **Редакция v1.1: техническая сверка архитектуры, кода и согласованного UX перед передачей Codex**\
**Статус:** единое исполняемое ТЗ для desktop Codex, подготовлено по прямому поручению владельца. **Передача Codex — отдельным сообщением владельца; не начинать работу самовольно.**\
**Результат:** один большой проверяемый кодовый цикл — существующая архитектура + согласованный интерфейс + необходимые интеграционные исправления; не два параллельных редизайна. Проверяемая карта рисков и кода — [предстартовый аудит ARCH-UX-01](../reviews/ARCH_UX_01_PRE_FLIGHT_AUDIT.md).\
**Ограничение:** никаких самостоятельных merge в main, force-push, изменения личных данных и живых Lichess операций без разрешения владельца.

## 0. Короткий приказ исполнителю

Привести нынешний Chess Coach к утверждённой структуре Desktop UX, **используя уже реализованный модульный монолит ARCH-01**. Это итерация поверх кода PR #44: технические и шахматные возможности сохраняются; исключены только конкретно отклонённые владельцем UI-режимы. Не переписывать приложение, SQLite, движок и шахматные алгоритмы; не строить микросервисы. Создать единый непротиворечивый Qt shell из шести разделов, сохранить один реальный игровой session, один review, одну базу, один Stockfish manager, один Lichess service, одну политику Fair Play и существующий планировщик. Исправить остающиеся связи MainWindow с UI/workflow ровно настолько, насколько нужно для надёжной новой навигации. Внутри одной рабочей ветки пройти этапы A0–A12 с реальными тестами и поэтапными коммитами.

**Это ТЗ ЗАМЕНЯЕТ прежнее требование ARCH-01 «не менять UX» только для нового рабочего цикла.** Всё уже принятое в ARCH-01 по потокам, данным, safety и совместимости остаётся обязательным. W0–W9 + ARCH-FIX-01…04 считать исходной реализованной базой, **не выполнять заново**.

### 0.1. Репозиторий и исходные состояния

- Репозиторий: https://github.com/feshakomissarov-prog/chess
- Проверенная исходная архитектура: [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), ветка codex/arch-01-modular-refactor, известный SHA **9a59b21bb91adbada7b58baf9dff45d0afccb27d**. На нём Windows hosted CI [#171](https://github.com/feshakomissarov-prog/chess/actions/runs/41741484833) SUCCESS, **604 passed**, compileall и isolated offline UI init; ручная Windows-проверка и живые OAuth/Stockfish не проводились.
- Утверждённый новый UX и этот документ: [draft PR #45](https://github.com/feshakomissarov-prog/chess/pull/45), ветка chatgpt/ux-information-architecture-2026-11-04. PR #45 — документационный источник, не отдельный исполняющий код PR.
- У PR #44 и #45 база main, **оба не слиты** при подготовке ТЗ. Перед запуском перепроверить точные HEAD, state, status и рабочее дерево; если они изменились — выбрать актуальные, не перетирать чужую работу.
- **Рекомендуемый способ исполнения:** продолжать уже существующую кодовую ветку/один draft PR #44, расширив его объём по этому утверждённому ТЗ и явно обновив описание PR. Так архитектурные изменения W0–W9 не дублируются в новом PR. Не сливать #44 для начала UX и не создавать конкурирующую реализацию от старого main. Рабочую UX-документацию из ветки PR #45 перенести в кодовую ветку явным выборочным копированием согласованных файлов на зафиксированном SHA, **без автоматического merge всего docs PR #45** и без потери уже имеющихся изменений. Если в момент запуска PR #44 уже принят/слит — начать отдельную единственную UX feature-ветку от актуального main и зафиксировать в журнале причину изменения маршрута.
- Сохранить PR #45 draft до решения владельца об итоговой документации. Никаких самостоятельных закрытий/слияний PR #44/#45.

### 0.2. Источники требований и приоритеты

Читать **в указанном порядке**: [AGENTS.md](../../AGENTS.md), этот документ, [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md), [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md), [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [UX-COV-01…24](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md), [последний UX рабочий документ](../ux/CHESS_COACH_FULL_INTERFACE_WORKING_DRAFT.md), [PROJECT_STATUS](../PROJECT_STATUS.md), [PRODUCT_VISION](../PRODUCT_VISION.md), [целевая архитектура](../architecture/CHESS_COACH_TARGET_ARCHITECTURE.md), [ARCH-01 execution spec](../architecture/ARCHITECTURE_REFACTOR_EXECUTION_SPEC.md), [ARCH-01 актуальная карта](https://github.com/feshakomissarov-prog/chess/blob/codex/arch-01-modular-refactor/docs/architecture/ARCH01_CURRENT_TO_TARGET_MAP.md), [ARCH-01 handoff](https://github.com/feshakomissarov-prog/chess/blob/codex/arch-01-modular-refactor/docs/handoffs/ARCH01_EXECUTION_LOG.md), исходники и тесты **текущей рабочей ветки**.

При противоречиях: **(P0) Fair Play, целостность данных, безопасность личности и действующих партий** → **(P1) последнее конкретное решение владельца 04.11.2026** → **(P2) утверждённые UX-B1/UX-A2/UX-GAME-01 и coverage с уточнениями** → **(P3) действующие функциональные контракты PR #44** → **(P4) старые M4.6/ARCH-01 ТЗ и исторический ROADMAP**. Не восстанавливать устаревшую идею из старой таблицы вместо более позднего решения. Если вопрос меняет продуктовый смысл, зафиксировать выбор и не додумывать новые функции; независимые безопасные этапы продолжить.

**Приоритет работ:** P0 Fair Play/правильность игры/целостность данных → P1 сохранение нужных функций → P2 согласованный UX/навигация → P3 границы архитектуры и отзывчивость → P4 полировка. P0 касается не только запуска движка, но и немедленного сокрытия кешированных engine-derived данных на всех маршрутах. Не ставить внешний вид выше жизненного цикла игр и доступа к уже имеющимся данным.

## 1. Что утверждено и что НЕ делать

### 1.1. Согласованная основа

1. ОДНО Windows desktop приложение на Python >=3.12, PySide6, python-chess, chess-widgets, SQLite, Lichess и локальном Stockfish. **Модульный монолит в одном процессе Qt** + управляемый дочерний UCI процесс. Ни микросервисов, ни REST-сервера для UI, ни Rust/C# миграции, ни облачной БД.
2. **Шесть основных разделов слева:** Главная, Играть, Мои партии, Анализ, Обучение (включая задачи), Прогресс. **Настройки отдельно снизу.** Одно главное окно и внутренние страницы. При игре/review sidebar может сворачиваться; глобальное «Вернуться к партии» сохраняет live session.
3. **Главная — четыре смысловые области**, включая левое меню, краткий прогресс, одно доступное следующее занятие/упражнение, три последние завершённые партии. Без global scroll.
4. **Играть — одна встроенная форма:** Lichess AI, друг/ссылка, ник соперника, локальная свободная доска; цвет, допустимые контроли времени и параметры. Не плодить модальные окна создания.
5. **Партия — одна постоянная доска, часы и ходы.** Справа ВИДИМАЯ панель специальных команд, включая «Возврат хода», предложение ничьей/ответы на неё, «Сдаться», другие только когда API/режим допускают. Никаких поисков takeback в «Ещё». Premove и отмену оставить с нынешним поведением.
6. **После finish тот же board widget, те же часы и ходы, никуда не переходить.** Вместо активных игровых команд справа ровно две новые основные кнопки «Разобрать» и «Вызвать снова». Никакой ResultPage, отдельной карточки результата, баннера, overlay, popup или автоматического Stockfish. Ходовую историю можно листать. Новый вызов — только по явному клику; серверные приглашения отображать лишь если реально подтверждены.
7. **Мои партии:** верхняя компактная сводка импорта и источников; центральный scrollable каталог с активными партиями в закреплённой группе; справа фильтры; внутренние подробности завершённой партии с **настоящим компактным графиком всей партии** (при безопасном полном допуске и наличии данных), числами и источником.
8. **Анализ (вариант B): три направления** — Разбор партии / PGN (включая FEN/редактор одной доски), Массовый анализ, Исследование дебютов. В разборе обязательно переиспользовать имеющиеся **AdvantageBar** и интерактивный **CP/Δ-график** с существующими маркерами, фазами, обеими сторонами и сохранёнными вариантами.
9. **Обучение** — один верхний раздел, задачи внутри. Но подробное проектирование уроков, персонального плана и интерфейса нового решателя **отложено владельцем**. Текущие история задач, повторное решение, показ ответа, сброс/проверка и «Разобрать мои ошибки» остаются доступны из нового shell без второго механизма решения.
10. **Прогресс** — самостоятельная статистическая страница: текущие подтверждённые данные, критические моменты, дебюты/фазы, рейтинг-тренды, фильтры и переход в исходную партию; никакой выдуманной степени освоения.
11. **Настройки** — сохраняют все прежние параметры: доска, звуки, Stockfish, Lichess, фоновая синхронизация, диагностика, общие. Звуки **строго AS-IS**: каталог/WAV/default IDs, выбранные варианты, preview, preset, громкость, события, QSettings; допустим только визуальный перенос.
12. **Задачи второго уровня:** действующие ручные и автоматические sync, пауза/возобновление, Retry-After/429, лимиты и статусы обязательны; **обновление одной партии Lichess не является обычным CTA**, только диагностический recovery по конкретному сбою. Заметки к партиям остаются второстепенными.

### 1.2. Явно отвергнуто / отложено

- Отдельная **«Тренировка предходов»** и кнопка тестового хода соперника как пользовательский режим — **не включать в новый UI**. Но обычные реальные premoves в online human/AI играх **не трогать**. Не удалять машинально код и regression tests до проверки, что ими не пользуется действующая логика.
- **«Пробная загрузка 5–10 партий/попыток» — не нужна даже в Advanced/Диагностике.** Не выводить соответствующие кнопки; при этом тестовые fake fixtures, восстановление синхронизации, archive checkpoint, backfill и privacy diagnostics остаются.
- Отдельная автоматическая «массовая загрузка новых задач Lichess» без подтверждённого источника; новый учебный движок, обучаемый ML, GPT tutor, голос, дебютный репертуар, полный opening tree/explorer, исследование чужих 500 партий, собственный Stockfish replacement — **не реализовывать** в рамках этого ТЗ. **Исключение:** утверждённые минимальные UI-входы PGN/FEN и ручной расстановки/исследования линии входят в U6; это ограниченная работа над общим просмотрщиком, не отдельная новая аналитическая подсистема.
- Не превращать неготовые возможности в активные кнопки/ложные метрики. Для «Исследование дебютов» — честно открыть реально существующую статистику дебютов (или статический поясняющий блок без ложного CTA), не выдавая её за готовое дерево вариантов.
- Не менять алгоритмы official accuracy/Stockfish classification, пороги, denominators, источник score, schema version, PGN lineage, fair-play lease rules, OAuth/PAT credential policy без отдельного доказанного исправления.
- Не вводить обязательные p95/SLA, 3k/10k/50k тестовые наборы, новые языки, новый global service locator, event bus ради красоты и новые зависимости без лицензий/технической нужды. Сохранить отзывчивость, но не выдумывать непроверенные performance numbers.
- Не сливать и не выпускать UX без реальной Windows-проверки владельца.

## 2. Целевая архитектура, скорректированная под новый UX

### 2.1. Обязанности слоёв

**bootstrap/ApplicationRuntime:** одна точка композиции экземпляров и жизненного цикла. Один LichessService façade (при подключении), один GameRepository façade/одна SQLite, один StockfishManager/EngineAnalysisCoordinator, одна FailClosed FairPlay policy, один BackgroundWorkCoordinator, одна группа app/session/route controllers. Переключение страниц НЕ создаёт новые сервисы, клиентские event streams, process Stockfish или очереди.

**domain:** immutable game identity/revision, typed score CP|mate|missing, provenance, result/clock snapshots и уже существующие шахматные вычисления. Без Qt widgets, SQL/HTTP, OAuth и GUI-текстов. Только реально нужные типы; не создавать сотни DTO для каждого QLabel.

**application:** сценарии и право действия. Продолжить существующие application.game_sessions, app_state, archive_sync, dashboard, engine_analysis, fair_play и background_work. Добавить/уточнить координаторы только при реальной ответственности: routing/selection, library+detail read model, review/current position, user-facing background inspector/progress, progress dashboard. Эти координаторы владеют stale generation / request id / cancellation и возвращают account-scoped immutable read models, но не импортируют QWidget и не рисуют экраны.

**infrastructure:** существующие lichess.service_* adapters, SQLite thematic stores/UoW, Stockfish UCI, settings, diagnostics. Одна физическая база, существующие migration+backup, одна writer discipline, replayable jobs, условие account/revision/hash на commit. Никакого HTTP во время write transaction и никаких Qt object в worker результатах.

**ui:** AppShell/MainWindow — видимый набор screens, Qt signal wiring, навигация, focus, dialog ownership, rendering; страницы и components, использующие published use cases/read models. Переиспользовать существующие HomePage, PlayPage, LibraryPage, GameReviewWorkspace, MoveTable, AdvantageBar, ReviewGraph, StatisticsDialog, PuzzleHistoryPage, существующие sounds/theme/board; переносить постепенно. Не создавать вторую доску или новый графический алгоритм, пока существующий удовлетворяет контракту.

**Совместимость:** сохранить публичные импорты window.MainWindow, window.SettingsDialog, lichess.repository.GameRepository, lichess.service.LichessService и начальный модуль python -m chess_coach. Legacy фасады разрешены как временный compatibility debt с tests, но НЕ отдельная вторая бизнес-логика. Не переписывать event feed ради замены имен папок.

### 2.2. Route и пользовательское состояние

Определить один typed route/state контракт (имена маршрутов на усмотрение Codex, стабильные и независимые от русских надписей):
- Основные: home / play / games / analysis / training / progress / settings.
- Внутренние: live-game workspace, game details, game review, analysis-source picker, batch control, puzzles-history, diagnostics и т.п.
- Состояние: выбранный account/auth_generation; selected game_id и session; line_revision/hash, selected ply; filters/search/period/scroll для каталога и прогресса; последний review/workspace; sidebar collapse/board size; запущенные operation IDs только через coordinator.
- Back/return восстанавливает **точный предыдущий контекст**. Навигация между вкладками не рвёт live session, не подтверждает онлайн-ход за сервер, не запускает import/Stockfish/OAuth/refresh и не сбрасывает очередь.
- Для нескольких активных игр: один selected workspace + реестр других текущих сессий; открытие чужой карточки не выбирает молча другую партию. Глобальная команда возврата с видимым счетчиком.
- Page widget/Qt signals подписывать/отписывать один раз, защищать закрытое окно/уничтоженный QObject и stale callback. Никаких глобальных singleton страниц с внешней скрытой DB ссылкой.

### 2.3. Точки ответственности, где нужна доработка ARCH-01

На PR #44 уже есть bootstrap.py, domain/models.py, application/{game_sessions,app_state,dashboard,archive_sync,engine_analysis,fair_play,background_work}, infrastructure/sqlite, service_auth/service_games/service_sync и ui/pages. **Это база, не новая программа.**

После выбора новой IA:
- MainWindow перестаёт быть местом хранения дублирующихся source-of-truth для выбранной сессии/аналитики/архива. Любые оставшиеся напрямую UI → SQL/HTTP/UCI вызовы в цепочках новых экранов переносить в подходящий существующий coordinator. Presentation handlers и локальное состояние Qt виджетов могут оставаться в shell.
- Выделить один общий **game workspace view/controller**, который отображает authoritative live snapshot и finished snapshot; не менять BoardView при finish, не создавать ResultPage. Правый control panel state-driven: waiting / active / reconnect / finished, с поддержкой live commands и их ответов.
- **Games/Details** переиспользуют repository scoped queries и verified assessments; мини-график получает безопасную projection того же источника, что и ReviewGraph, а не собственную модель ошибочных CP.
- **Review/Analysis** один workspace и один source selection route. Внешний PGN/FEN/редактор открываются в ephemeral session, никогда автоматически не загрязняют личный архив/статистику. Исследовательские варианты не изменяют исходную партию и revision.
- **Progress** агрегирует только versioned existing analytics; библиотека, detailed review и прогресс получают те же provenance/coverage. Новая публичная страница не означает новый SQLite store.
- **Training entry** — временный тонкий адаптер к работающим puzzle history/replay и тренажёру собственных ошибок. Не создавать отдельную domain.learning имплементацию без утверждённой методики.
- **Unified background inspector** — один presentation read model для уже существующих operation lanes/jobs; управляет архивом, готовыми Lichess evals, puzzle sync и Stockfish batch из надлежащих use-case команд. Не создавать вторую очередь из UI.

### 2.4. Потоки, ресурсы и жизненный цикл

Сохранить ARCH-01 независимые lanes: интерактивные Lichess события/ходы и часы; safety/control; конечные архив/puzzle/ready-analytics запросы с limit/backoff; локальный Stockfish admission; SQLite writer/short readers. **Одного глобального blocking FIFO не должно быть.** И безопасность, и игровые события — P0 с независимым admission: живой ход не ждёт длительный preflight, а немедленное revoke/mask не ждёт игровую очередь; затем foreground review > пользовательская sync > auto backfill > maintenance.

Каждый async результат обязан содержать **применимые** account/generation/operation/game/revision контексты; для общей задачи без game_id нужны account+generation+operation ID, а не фиктивный game_id. При logout, reconnect, смене аккаунта/маршрута, отмене, закрытии приложения — stale callback не должен менять выбранную игру, UI, БД, допуск анализа или запускать Stockfish. **Смена только маршрута** отменяет устаревшее UI read/presentation, но не нужные серверные сессии, архив или durable job; смена account/auth отменяет соответствующие privileged jobs и leases. Jobs могут жить дольше страницы; закрытие приложения завершает workers. Все callbacks от worker доставлять в Qt main thread через queued signals с проверкой lifetime QObject и generation.

### 2.5. Проверяемое соответствие архитектуры и экранов без дублирования

| Экран / действие | Авторитетное состояние / владелец | Доступ к данным и частота | Запрещённый анти-паттерн |
| --- | --- | --- | --- |
| Shell, header, Back, route | `WorkspaceStateStore` + root navigator; selected account/game из `GameSessionCoordinator` | Lazy page creation, навигационный snapshot, восстановление фильтров; **нет HTTP при открытии страницы** | Второй router/selected game и service instance на каждую вкладку |
| Live game, часы, premove, finish | `GameSessionCoordinator`, Lichess authoritative stream; запись через GameRepository/UnitOfWork | **Одна BoardView**, UI слушает выбранную сессию; clock tick отделён от сети; final_sync не зависит от ResultPage | Повторный stream при возврате, локально подтверждённый онлайн-ход, потеря финальной записи |
| Главная | `DashboardQueryCoordinator`, bounded account-scoped read models | 3 последние **завершённые** партии и небольшой тренд, отмена stale запросов по account/filter/revision | Full PGN parse/новая DB connection на каждую перерисовку |
| Мои партии / детали | Существующие thematic stores за GameRepository facade; при необходимости library/detail coordinator | Model/view+pagination/virtualization, debounce фильтров, read-only graph projection по game/revision | Тысячи QWidget, ещё один SQLite file, второй алгоритм CP↔mate |
| Анализ / review | `EngineAnalysisCoordinator` + единая `application.fair_play`; данные из analysis stores | Общий ReviewGraph/AdvantageBar, явное engine request и source-backed read model | Auto-Stockfish на открытие, cached score до preflight, собственный engine manager |
| Прогресс | Существующие Statistics/metrics queries, account-scoped source coverage | Lazy tab/query; агрегаты, drill-down в тот же Review | Новый metrics store, выдуманное mastery или смешение L/S accuracy |
| Обучение → Задачи | Существующие puzzle store/service, puzzle replay и training ошибок | Встроенная оболочка действующей истории попыток/повторения | Второй solver или обещание выдачи любых новых задач |
| Фоновые операции / Настройки | `BackgroundWorkCoordinator` + штатные durable sync/batch; прежние QSettings | Один inspector, throttled/coalesced Qt status events; user intent запускает операции | Общая блокирующая очередь, новые фоновые jobs при смене route, токены в QSettings |

**Проверка связности и отзывчивости:** UI → application intents/read models → infrastructure, никаких новых UI SQL/HTTP/UCI. Допустимые старые Qt façade-зависимости документировать, постепенно вытеснять на новых путях, не делать бесполезных wrapper-слоёв и десятков пустых DTO. Пагинация должна оставаться account-scoped, индексы/кеш не перестраивать без доказанной необходимости; инвалидация при смене account, game line_revision/hash и обновлении источника анализа. Нужны coalescing/debounce повторных refresh, bounded query/windowing, cancellation, контроль жизни QObject и queued Qt signals: в workers нет живых QWidget/SQLite.Row/connection. **Нельзя запускать новую долговременную операцию из конструктора страницы**. Не вводить обязательные FPS/p95/RAM/SLA, но замеченные зависания и конфликтные resource lanes исправить с воспроизводимым тестом.

## 3. Неприкосновенные cross-cutting контракты

### 3.1. Fair Play — P0, никаких компромиссов

Human live game или неизвестное/неполное состояние списка партий, сеть 401/403/429, reconnect, смена account/auth_generation, revoke/disconnect/отозванный lease → **fail closed**. Синхронно скрыть любой engine-derived score, AdvantageBar, CP/Δ график, miniature graph в карточке, PV, категории ошибок, локальные тактические советы, «Разобрать мои ошибки», расширенную статистику, которую нельзя показать при active human. Нельзя на мгновение нарисовать кешированные оценки до проверки.

Любая попытка разрешённого review, анализа позиции или нового Stockfish request → один свежий authoritative preflight со строгой привязкой account, game, line revision/hash, route, operation и generation; проверить вновь после ожидания engine admission и перед сохранением. **Отсутствие engine process ≠ право показать уже сохранённые подсказки.** Не делегировать решение UI. В изолированном --offline нет real network/credential access; --manual-connect не читает credential store до явного клика. Никаких обходов через импорт внешнего PGN, карточку архива, Прогресс или Обучение.

### 3.2. Достоверность шахматной аналитики

- Не выдумывать official Lichess accuracy из locally counted good moves. Официальная accuracy, PGN eval/analysis JSON и Stockfish CP/mate — **разные источники и версии**. Наличие score не означает полноту.
- CP|mate|missing typed, mate не переводить в условные +10/+20 pawn. В графиках неполные последовательности — с разрывами; mate отдельно, без интерполяции численной шкалой CP. Позиция, SAN, ply, цвет/перспектива, source, depth/coverage — строго из данных.
- Проценты и rate только с честным n/N (решения ≠ позиции), качественные категории только по сохранённым verified assessments; нехватка данных = «Нет данных», не «0 ошибок». Не менять метрические определения и классификацию ради UX.
- Карточка завершённой партии (мини-график), полный review, Home/Progress должны показывать одинаковые согласованные оценки, происхождение и охват для одной game/revision/account.
- Дебюты и ECO только из существующего проверенного opening matcher/словаря; не придумывать перевод/причинный диагноз. Анализ соперника по сотням партий — отдельная будущая идея, **не здесь**.

### 3.3. Одна SQLite и секреты

- Одни user paths, существующий SCHEMA_VERSION, backup/integrity, idempotent archive/Puzzle/analysis run/queue, account scope, line CAS, recovery. Никакой новой «базы под UX», destructive remigration/downgrade и автоматического импорта PGN.
- Ни network, ни UCI, ни тяжёлая PGN/SQLite агрегация в Qt main thread; request cancellation и bounded result sets при десятках тысяч архивных записей.
- OAuth/PAT сохранять в разных Windows Credential Manager slots; QSettings без токенов; никакой случайной network init в безопасном QA; anonymized diagnostics только opt-in/allowlist. Никаких персональных PGN/SQLite/PAT/записей владельца в GitHub, автотестах или отчётах.

## 4. UI реализации: единые acceptance-контракты страниц

### U1. Общая оболочка / маршруты

Слева постоянное меню из 6 пунктов в утверждённом порядке, Настройки внизу, статус Lichess account/connected/offline/manual и фоновая активность. В игре/review автоматическое узкое состояние sidebar с доступной навигацией. Header показывает «Вернуться к партии» при live session. Единый Back, восстановление выбранной строки/фильтров/scroll и review ply; старые QDialogs нормальных сценариев заменяются страницами/панелями, **кроме** native file picker, auth, подтверждения необратимого действия и глубоко технических параметров.

Использовать текущий dark-v1 (тёмные surfaces, тёплый акцент, разборчивый контраст), **согласованные ориентиры** размеров доски 400/480/560/640 logical px (по умолчанию 480, с укладкой в viewport без обрезания). **В PR #44 реально есть 400/480/560/720 и «на всю область»**: сохранить обратную совместимость ранее записанного `board_target_size=720` и прочих значений, не сбрасывать настройку молча; на маленьком экране адаптивно уменьшать визуальный размер до сохранения часов и правых игровых кнопок. Смену списка пресетов не превращать в порчу QSettings. Не вводить новую дизайн-тему без отдельного решения; не делать зелёный главным акцентом. Общая страница Главной и Моих партий не прокручивается; рабочие списки/ходы/таблицы могут скроллиться внутри.

### U2. Главная

Четыре утверждённые области: (1) левый sidebar, (2) 2–3 достоверных KPI + небольшой реальный тренд/«нет данных» и переход в Прогресс, (3) одно **реально доступное** действие по существующей истории задач/повторному решению или упражнению по подтверждённой ошибке с честным fallback «Выберите сохранённую задачу»/«Недостаточно данных», (4) три последние **завершённые** партии и переход ко всему каталогу/подробностям. Никакой ложной персональной рекомендации или несуществующей «следующей задачи». График/категории masked согласно Fair Play. Реальные account/time control filters и сохранённые предпочтения, no full archive scan на Qt thread. Размещение responsive, без глобальной прокрутки/обрезки важных CTA.

### U3. Играть / создать игру

Встроенная страница AI (уровни 1–8), друг по ссылке, вызов по нику, свободная локальная доска. Цвет (белые/чёрные/случайный), clock presets 3+2, 5+0, 10+0, 15+10, custom, unclocked только в поддерживаемых сценариях; rated/casual там, где API допускает. Параметры не сбрасывать бесполезно при переключении режима. Create single-click idempotent, ожидание/отмена challenge, copy link, безопасные 401/429/retry. Не обещать локальный бот или отдельную «Тренировку предходов». Не вызывать OAuth/создание игры из простого просмотра страницы.

### U4. Игровой workspace / часы / спецкоманды / завершение

Одна и та же физическая BoardView: сверху удалённый/верхний игрок + читаемые часы, снизу нижний + часы, справа локально скроллируемые ходы и всегда видимый блок игровых команд. При flip ориентация и подписи соответствуют фактическому виду. Нет часов → «Без контроля времени», не фиктивные 00:00. Не подтверждать ход или +2 до серверного gameState, reconnect placeholder не выдаёт угаданное время. Локальные premoves/отмена, takeback и accept/reject, предложение/принятие/отказ от ничьей, claim draw, сдача, отмена pending challenge и другие штатные действия работают по авторитетным state/API. Сдача и другие необратимые команды — с подтверждением; disabled/hidden state с понятной причиной.

**Спецкоманды НЕ за «Ещё»**: основной активный набор и поступившие ответы всегда в правом блоке, видны на обычном ноутбуке. Помощь движка/оценка живой human game отсутствуют.

**При достоверном finish** — оставить ту же BoardView, часы заморожены, список ходов и навигация доступны. **Критично:** в PR #44 `window.py::_show_result` не только переключает `ResultPage`, но проверяет `final_sync_pending`, ожидаемую SQLite запись, соответствие сохранённых ходов/статуса и запускает `_poll_result_write`. Перенести эту проверку в независимый от страницы lifecycle/coordinator: удаление `ResultPage` **не должно отменить финальную синхронизацию, подтверждение сохранения или recovery при ошибке записи**. Финальную историю не считать сохранённой до server/SQLite confirmation, исключить бесконечное Qt polling при сбое. Убрать активные игровые команды и в этой же правой зоне показать ровно два новых главных действия **«Разобрать» / «Вызвать снова»**. В status line можно кратко показать «Партия завершена · 1–0». Не открывать ResultPage/overlay/modal, не запускать engine. «Разобрать» → существующий единый ReviewWorkspace после fresh safety. «Вызвать снова» → только явно инициированный новый documented Lichess challenge тому же username, если доступно; для AI/local contextual equivalent; API rematch не выдумывать. При реально полученном серверном входящем вызове inline «Принять/Отклонить», без автоaccept и бесконечного spinner. Повторный double-click не создаёт два challenge.

### U5. Мои партии / карточки / подробности

Сверху bounded сводка: total/active/completed, official L eval и accuracy отдельно от Stockfish, source coverage, фоновые X/Y и ошибки/429, «Синхронизировать», «Фоновые операции», настройки загрузки. Ни вызов Stockfish, ни полный импорт от одного открытия каталога.

Центр — собственный scrollable paginated/model-based список; все **активные партии закреплены сверху** или их компактный счётчик остаётся видимым даже при фильтрации завершённого архива. Active cards: соперник, контроль, цвет, authoritative status/чей ход; никакой unsafe analysis. Completed: дата, соперник, цвет, результат, time control, ECO, source, quality counts с coverage. Справа фильтры поиск, период, результат, сторона, контроль, ECO, opponent, есть официальный/локальный анализ, без анализа. При возврате сохранение строки/scroll/filter.

Клик в подробности — **внутренняя** панель, не новое OS окно. Обязателен мини-график **всей сохранённой партии**, со score/mate/missing и gaps, SAN, нулём, источником/покрытием, реальным end ply; если недоступен — нейтральное состояние. По preflight во всех unsafe состояниях до paint/tooltip график и критические метки скрыты. «Открыть» (доска без engine auto-start), «Разобрать», «Экспорт PGN»; secondary заметка+save. Одиночный refresh из Lichess — не стандартная кнопка; допустить только recovery для явно ошибочной записи в диагностическом потоке под общим лимитом.

### U6. Анализ / единый workspace

Landing: 3 большие карточки: «Разбор партии / PGN», «Массовый анализ», «Исследование дебютов» (пока только реально существующая статистика с честным указанием границ; без фиктивного дерева). Общая точка выбора источника для разбора: из Моих завершённых; файл .pgn; вставка PGN из буфера; FEN; расстановка позиции мышью. Всё открывать **в одном** review/position workspace; ephemeral external studies не добавлять в games без явного отдельного save. Работа с ходами, исследование альтернативной линии из выбранной позиции не переписывают исходный PGN/revision.

Сохранить существующий interactive ReviewGraph (режимы **Оценка CP / Изменение Δ**, все маркеры, полная ось ply, sparse source gaps) и AdvantageBar **рядом с доской** с текущей безопасной оценкой выбранной позиции; оценки обеих сторон и доказанные quality tags, phases/events, saved PV, синхронная навигация доска↔ходы↔график, export and error replay. Не делать второй набор score calculations. Открытие PGN/позиции не запускает Stockfish; запуск только по явному действию и полному допуску.

«Массовый анализ» — существующая durable batch очередь с running/queued/paused/retry/cancel, выбором партий/настроек, selective deeper review, правильным resume, error source, one Stockfish manager. Главная страница и архив показывают лишь краткое состояние и ссылку сюда, а не вторую очередь.

### U7. Обучение (только совместимое ядро)

Один верхний маршрут «Обучение». Первой доступной функцией оставить **действующие задачи/историю попыток Lichess**: поиск/фильтрация, рейтинг-тренд/результаты, повторное решение, reveal/reset, и рабочее «Разобрать мои ошибки» из Review. Показать только реально доступные данные; API истории задач ≠ гарантия выдачи новых задач. Не разрабатывать новый полноценный план обучения, темы, репертуар, уроки, GPT, mastery meter, новую большую страницу решателя. Существующий puzzle UI может временно переиспользоваться в тонкой встраиваемой странице; **не выдавать историю решённых задач за живую ленту новых**. Если сохранённых попыток/ошибок нет, показывать честное пустое состояние. Дополнительная UX-сетка будет отдельным будущим решением, не блокирует этот цикл.

### U8. Прогресс

Одна внутренняя страница с реальными, account-scoped read models из текущего StatisticsDialog, вкладками Обзор / Партии / Дебюты и фазы / Критические моменты / доступные тренды. Фильтры account, period, clock, side, result, source, analyzed, source type; текущий 90-дневный период vs история — не смешивать. Рейтинг-тренд, локальная классификация, проверенные event/motif links в исходную позицию через общий review. Указывать source, N, coverage и «н/д»; не объявлять причинную слабость и освоение навыка на основании одного плохого хода. Не создавать второй metrics store.

### U9. Фоновые операции / настройки / звуки

Одна глобальная компактная индикация операции и открываемый inspector: archive, ready Lichess analytics/backfill, puzzles history, Stockfish batch; per-source состояние, X/Y, queued/paused/rate-limited/failed/no_server_data, Retry-After, лимиты, pause/resume/retry и reason. Не сериализовать живую игру за фоном. Главная/Мои партии/Обучение/Анализ могут ссылаться на **тот же** inspector с разными фильтрами, но без разных очередей.

Настройки: общие, доска/управление, **Звуки — полный AS-IS**, Stockfish nodes/depth/time/Threads/Hash/custom presets/engine selection/probe, Lichess OAuth/PAT/revoke/disconnect, автосинхронизация/лимиты/пауза, система/обезличенная диагностика. Сохранить QSettings keys и пользовательские значения; cancel/save не должны случайно менять engine, токены или network mode. **Пробных загрузок/пилотов нет ни в главном UI, ни в Advanced.** Служебные тесты сохраняются. Не отключать Dr.Web/Defender или обходить защиту.

### U10. Адаптивность и визуальная согласованность

Целевые Windows 10/11 laptop 4 cores / 8 GB / SSD, экраны 1280×720, 1366×768, 1920×1080, 100/125/150% DPI. Один набор visual tokens и Qt logical dp. Не увеличивать board самопроизвольно до размера, при котором кнопки/часы исчезают. Sidebar и правый inspector адаптивны; компактный режим не теряет takeback/ничью/сдачу. No global scroll Home/Games, локальный scroll moves, catalog, analytics/detail. Все состояния loading/empty/error/partial/stale/retry, keyboard focus/мышь, drag/click, звуковые настройки, отсутствие мерцания QDialog и icon/launcher работают. Никаких несанкционированных файлов новых шрифтов/ассетов/лицензий.

## 5. Обязательная карта сохранения 24 групп

Полная исходная карта и кодовые точки: [EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md). Codex в журнале напротив каждого ID фиксирует **reused / moved / not exposed by owner / technical recovery**, UI path и тест/ручную проверку.

| ID | Итоговый контракт |
| --- | --- |
| COV-01 | Sidebar, статус/переключение Lichess, offline/manual, connection/revoke — сохранить |
| COV-02 | Home verified KPI + 3 завершённые партии, реальные фильтры — встроить |
| COV-03 | AI, друг по ссылке, username, clock/color/rated, create/cancel — встроить |
| COV-04 | Все активные Lichess партии сверху в каталоге + возврат к выбранной — сохранить |
| COV-05 | Live stream, часы, premove, takeback, draw, surrender, claim, flip — **видимые side commands** |
| COV-06 | Свободная доска/локальный undo — сохранить; **premove-training/test move UI НЕ выводить** |
| COV-07 | Ходы, стрелки/круги, выбор позиции, отображение и focus — сохранить |
| COV-08 | Finish без ResultPage, та же доска и две новые главные команды — заменить UX |
| COV-09 | Полная/инкрементальная sync и checkpoint — сохранить; **pilot UI НЕ выводить** |
| COV-10 | Готовая Lichess accuracy/evals, фон/лимиты/429/пауза — сохранить |
| COV-11 | Архив, PGN export, заметка вторично, единичный refresh recovery-only — сохранить |
| COV-12 | Review workspace, AdvantageBar, CP/Δ graph, source, phases, PV — переиспользовать |
| COV-13 | Упражнения по своим подтверждённым ошибкам, hint/retry/без подсказки — сохранить |
| COV-14 | Stockfish менеджер, настройки/найти/проверить, bounded resources — сохранить |
| COV-15 | Batch очередь, selective review, resume/pause/cancel — одна control plane |
| COV-16 | Полная текущая статистика, критические моменты, фильтры, рейтинг-тренд — Прогресс |
| COV-17 | Puzzle attempts, replay/reveal/reset, sync — Обучение; **pilot UI НЕ выводить** |
| COV-18 | Все WAV/defaults/preview/QSettings/volume/notification — **100% AS-IS** |
| COV-19 | OAuth/PAT slots, restore/revoke и credential guards — сохранить |
| COV-20 | Privacy-safe logs, anonymized report, diagnostics/recovery — сохранить |
| COV-21 | Workspace resume, filters/sidebar/geometry, single instance — сохранить |
| COV-22 | ECO/RU opening, phases, events, provenance — сохранить |
| COV-23 | Единый viewer фоновых задач, приоритетные независимые lanes — сохранить |
| COV-24 | FEN/position review, CP/Δ/AdvantageBar и safety gates — сохранить |

**Не считать неприсутствие training/pilot кнопки потерей функции:** это явное решение владельца. **Не считать отсутствие нормального premove приемлемым:** обычный premove — обязательный функционал. Переиспользуемый скрытый QA код не должен создавать доступной тестовой вкладки.

## 6. Этапы desktop Codex A0–A12 в ОДНОМ большом draft PR

Каждый этап: самостоятельный разумный commit, тесты/границы, короткая запись в **docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md** (создать в кодовой ветке), SHA и что ещё не проверено. **Не останавливаться за согласованием мелких Qt деталей; выбирать минимальный вариант в пределах утверждённого контракта.** При safety/data blocker не запускать опасную операцию, продолжить независимые этапы.

| Этап | Работа | Минимальный gate |
| --- | --- | --- |
| **A0** | Проверить PR #44/#45 HEAD, git status; зафиксировать baseline exact SHA, CI, 604 прежних теста как ориентир, а не предположение. Выборочно перенести **эту v1.1** спецификацию, UX и актуальные AGENTS/PRD/PCD/ROADMAP/PROJECT_STATUS из PR #45 без неконтролируемого merge, добавить журнал; описать архитектурные границы, отложенные фичи и diff-to-goals | До изменения — full tests, compileall, diff check, isolated offline Qt init; без реальных данных |
| **A1** | Уточнить runtime composition, typed route/back stack, page lifecycle, активные sessions и safety masking; shell/sidebar/header, без нового service locator | Offline/manual credentials/network 0, nav не создаёт workers, session сохраняется |
| **A2** | Новая Главная/четыре зоны и сохранение state/фильтров, source-backed read models, три карточки | No global scroll, корректное n/N и masked home, stale jobs не рисуются |
| **A3** | Встроенная «Играть» + состояния создания AI/friend/user/free board, cancel challenge | UI events и fake Lichess; no duplicate challenge; без premove-training UI |
| **A4** | Игровая правая панель и состояния takeback/draw/resign/accept/claim, clock/no-clock, обычный premove, несколько live sessions | Visible controls, phase state matrix, deterministic server authority/reconnect/clock/fairplay tests |
| **A5** | Окончание игры без ResultPage **при сохранении final_sync/pending-write/error-recovery жизненного цикла**, тот же board/часы/ходы, «Разобрать» / «Вызвать снова», safe challenge | Same board instance, подтверждённая запись финальной линии, bounded retry без бесконечного poll, no auto Stockfish, no auto challenge, no overlay/modal |
| **A6** | «Мои партии» 3 зоны, pinned active, virtualized completed, filters/restoration, подробности с mini score graph, заметка+PGN, recovery-only single refresh | Sparse/mate/missing/unsafe graph tests, thousands synthetic rows, no UI blocking |
| **A7** | «Анализ» landing, единый PGN/FEN/board review, saved ReviewGraph CP/Δ + AdvantageBar, batch control; честная дебютная точка входа | Same review source/graphs, source coverage, ephemeral PGN not imported, no on-open engine |
| **A8** | «Прогресс» из existing StatisticsDialog, критические моменты/фазы/фильтры; «Обучение» как тонкий вход в existing puzzle history/replay/error trainer | Без ложных новых учебных фич/метрик; existing puzzle tests и deep links |
| **A9** | Глобальный background inspector, настройки и sound AS-IS, pilot buttons hidden, recovery paths | No new queues, 429 backoff, same settings/audio selections, no credential leakage |
| **A10** | Довести UI/application boundaries и общее переиспользование read models, unload/destroy semantics, stale callbacks, Qt thread ownership, ресурсный shutdown, главные баги интеграции | AST/import/contract checks, no direct UI SQL/HTTP/engine, no regressions W0–W9 |
| **A11** | Polish desktop и Windows DPI/mouse/focus/clock/game controls/nav/empty states, все 24 группы функций, bounded UI data loading и ручной checklist | 1280×720/1366×768/1920×1080, icon/.cmd, no missing control, visual smoke |
| **A12** | Финальная проверка, diff audit, documentation/handoff, exact-head hosted Windows CI, независимое review, владелец физически проверяет; PR остаётся draft | Все автоматические проверки SUCCESS на exact code SHA, факт/ограничения записаны, **НЕ MERGE** |

**Зависимости:** A4/A5 проходят безопасность до полировки; A6–A9 используют уже работающие domain services. Допускается переставить независимые A6–A9 для производительности, но нельзя сдавать финал с mock кнопками. Если UI / safety конфликт — прерывать опасное действие, не ослаблять guard.

## 7. Тестовые и отрицательные сценарии — не опускать

1. Включить human live игру и открыть через Home/Games/Details/Progress/Analysis/Training сохранённые оценки: **нет score, PV, графика, blunder insights, тренажёра** даже на 1 кадр; после revoke немедленное скрытие.
2. Login/reconnect/switch account/401/403/429/incomplete playing list: не выдавать analysis lease, не показывать stale результаты, не сохранять на чужой game_id.
3. --offline: физически нет OAuth/credential/network, локальный разрешённый анализ после правильного изолированного preflight; --manual-connect не читает token до клика.
4. Две активные игры: выбрать A, фон сообщает B; selection остаётся A, отдельный resume B, premove и часы каждого из правильного snapshot.
5. Переход с живой партии на Главную/Прогресс/Мои партии и обратно: не терять stream/game/clock/premove, не создавать новую подписку и не отправлять queued move без авторитетного сигнала.
6. Premove: обычный быстрый после отправки хода, отмена, takeback/illegal/stale; поведение прежнее. В UI нет тренировки предходов.
7. AI 3+2, human timed/no-clock, local board; неверный 00:00 не показывать, при reconnect не дублировать инкремент.
8. Активная партия: takeback/offer draw/accept/decline/resign/claim доступны справа и не спрятаны; если API запрещает — понятный disabled. Double-click/late event не создают повторной команды.
9. Mate/resign/draw/timeout/abandon: finish только по authoritative, same board object and selected moves, freeze clocks, no ResultPage; две основные CTA, без banner/overlay/engine.
10. Нажать «Вызвать снова» один/дважды, дождаться server response, incoming challenge с generation guard; без автоматического соглашения/нового game_id до подтверждения.
11. Две разные оценки Lichess/Stockfish, sparse graph, mate=0, no data, partial n/N — Home/Details/Review/Progress согласованы; нет фальшивой official accuracy.
12. Внешний PGN/FEN: parse/validate (некорректный PGN, несколько партий, FEN side-to-move/castling/en-passant), временный отдельный study без записи в личную БД, исходный game/revision/PGN/личная статистика не мутируют; engine не запускается при открытии. Редактор допускает анализ только валидной позиции; ходы исследовательской линии никогда не отправляются в текущую Lichess-партию.
13. Archive/page switching во время backfill: действующий фоновый job не создаётся снова; pause/resume/Retry-After/лимиты/429 работают; live move не ждёт конечную NDJSON выгрузку.
14. Разбор → «Разобрать мои ошибки» → повтор без подсказки → назад: законный verified move, source evidence, gate; никакого второго trainer logic.
15. Задачи: фоновая история, повтор/сброс/показ ответа, account filter, нет ложной кнопки получения новых произвольных задач, нет pilot controls.
16. Звуки: каталог/IDs, классический пресет, громкость, выбор/preview, sound cues и QSettings совпадают ДО/ПОСЛЕ UX и restart. Физический звук на Windows — отдельно.
17. Settings cancellation и restart: OAuth/PAT не попадают в plain text, отозванный token не возвращается как старый, offline QA не читает чужой профиль.
18. На существующих синтетических fixtures и тестах пагинации проверить bounded SQL read model, отсутствие создания QWidget на каждый элемент архива и восстановление фильтра/scroll. **При наличии уже готового датасета на 3k+ игр** выполнить дополнительный smoke/профиль; **не вводить обязательный новый 3k/10k/50k нагрузочный suite** в нарушение ADR-0001. Ресурсные проблемы измерять при обнаружении, не придумывать FPS/p95/RAM и не вводить новый SLA.
19. DPI/layout 1280×720/1366×768/1920×1080 и 100/125/150%: часы и **видимые game controls** не обрезаны, Home/Games не имеют полного scroll, таблицы могут иметь свой.
20. Повторная навигация/закрытие окна/пауза/долгий HTTP/queued UCI: нет обращения к удалённым QObject, zombie threads, бесконечных запросов, second Stockfish process, stale DB commit или unexpected data migration.

Использовать deterministic fake HTTP, auth, UCI и temp SQLite, pytest-qt offscreen; не заменять тесты screenshot утверждениями по одному лишь статическому source. Все прошлые tests PR #44 должны продолжить проходить (если число изменилось — документировать почему).

## 8. Приёмка и QA-протокол

**Автоматическая проверка Codex на Windows Python 3.12:** фактически доступная команда полного набора pytest; compileall src/tests/scripts; git diff --check; тесты Qt offscreen; isolated QApplication+MainWindow в --offline и --manual-connect без сети, токенов и пользовательской БД; архитектурные import boundaries/контракты; smoke маршрутов и работы fake event streams; CI на **точном конечном SHA**. Список тестов не урезать ради зелёного статуса. Не считать прошлые 604 PASS свидетельством о будущем UX PR.

**Физическая Windows-проверка владельца после независимого review (не выполняется Codex молча):**
- Двойной клик/ярлык, тёмный интерфейс, меню, Главная 4 области, 6 пунктов и Настройки; маленький экран/DPI.
- Создать AI и тестовую разрешённую human игру, предход, часы 3+2, доступность takeback/ничьей/сдачи, несколько активных партий, завершить игру и остаться на той же доске; попробовать новый вызов по явному клику.
- «Мои партии» — активные выше завершённых, sync/фоновые статусы, 3-зонный layout, фильтры/возврат, mini graph и сохранённый полноценный CP/Δ-график + AdvantageBar, source/coverage.
- Локальная история задач/повтор решения, критические моменты/фазы, настройки Stockfish, фоновые задачи с паузой и лимитами; обычное обновление страницы НЕ запускает заново import/engine.
- Настройки и прослушивание звуков; персональная база/настоящие аккаунты — только после резервной копии и явного разрешения владельца. Реальный OAuth/Stockfish E2E без согласия **НЕ ЗАПУСКАТЬ**.
- Отдельный чеклист verify 24 coverage IDs с указанием excluded-by-owner; честно отметить непроверенные операции.

**Отчёт Codex — кратко по-русски:** итоговый code SHA/PR, этапы A0–A12, файлы/модули, что было повторно использовано и что реально перенесено, тесты и их точный результат/Windows CI link, 24-ID coverage matrix, что не проверялось, blockers/risks, точный manual smoke checklist. Журнал handoff — подробный и восстановимый **в GitHub**, не в длинных сообщениях владельцу.

## 9. Stop conditions, вопросы и Definition of Done

**Stop только для опасной операции**, если нужен реальный токен, приватная SQLite/PGN, изменение схемы, автоматический server analysis, лицензия или реальный online/live act без разрешения. Не останавливать независимый безопасный блок из-за косметической мелочи. При выявленной критичной регрессии — исправить в том же единственном draft PR с новыми тестами и повторным exact-head CI, не создавая россыпь случайных PR.

**Done** означает одновременно: выполнены U1–U10; все 24 coverage ID получили проверенный маршрут/явное решение владельца; ARCH-01 границы и P0 safety сохранены, нет дублей DB/engine/service/queues; прежние обычные premoves, sounds и sync работают; старые training/pilot buttons в UX отсутствуют; нет fake реализованных GPT/opening-explorer функций; полный Windows CI SUCCESS exact SHA; независимое review и физическая ручная приёмка запрошены, **но merge без владельца запрещён**.

Новые архитектурные изменения **не делают проект микросервисным**: это сознательное решение для единого Windows-приложения с одним владельцем состояния игры и локальной SQLite. Если появится доказанный технический bottleneck, вопрос отдельных процессов/нового языка решается позднее на измерениях, **не в этом ТЗ**.
