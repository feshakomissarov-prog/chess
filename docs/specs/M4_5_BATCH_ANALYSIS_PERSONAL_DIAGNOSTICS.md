# M4.5 — детальное ТЗ для Codex: массовый анализ архива, паспорт партии и диагностическая статистика

**Версия:** 2.0, 01.11.2026. **Статус:** утверждено владельцем для реализации как один большой рабочий блок с последовательными контрольными точками. **Это ТЗ заменяет прежнюю, менее подробную редакцию этого файла.**
**Единая точка входа:** этот файл. **Журнал выполнения:** [M4_5_EXECUTION_LOG.md](../handoffs/M4_5_EXECUTION_LOG.md).
**Эпик:** [#28](https://github.com/feshakomissarov-prog/chess/issues/28). **Стратегия:** [ROADMAP](../ROADMAP.md), [PRODUCT_VISION](../PRODUCT_VISION.md), [AGENTS](../../AGENTS.md), [PROJECT_STATUS](../PROJECT_STATUS.md).
**Исходная реализация:** M0–M4 в main (PR #26, #27). Разработка M4.5 ещё не начиналась на момент публикации этой спецификации.

## 0. Приказ на исполнение: работа без ожидания владельца

Выполнить этапы 0–9 данного документа по порядку. Это **одно большое задание**, а не просьба реализовать первый пункт и остановиться. Codex не должен запрашивать дополнительное ТЗ для очередного этапа и не должен останавливаться на рутинной технической неопределённости: использовать безопасный минимальный вариант, зафиксировать выбор в журнале и продолжить. Если внешний ресурс недоступен, выполнить альтернативный офлайн-путь через fake/fixtures и продолжить независимые этапы. Если отдельная функция сложнее оценённого, поставить строго типизированный статус PARTIAL/DEFERRED с тестом и продолжить остальные, не выдавая заглушку за готовое.

**Режим GitHub при работе ночью:** создать одну feature-ветку от свежего main (предлагаемое имя codex/m45-batch-analysis), один draft PR в main, **коммит после КАЖДОГО этапа** с тестами и обновлением журнала. Этапы 0–9 продолжаются в той же ветке даже если промежуточный draft PR ещё не проверен. В конце запустить все проверки, перевести PR в ready-for-review только при зелёном CI и отсутствии критических незавершённых требований; иначе оставить draft и указать ограничения. Не выполнять squash/merge в main, force-push, самостоятельную смену продуктовых приоритетов или удаление личных файлов. Если инструменты не дают сменить draft status, указать это в PR; работа на ветке продолжается. Для существенного архитектурного выбора: выбрать простое совместимое решение, записать в журнал альтернативы/риски. Для безопасности данных и fair play нельзя обходить условия ниже; такие части можно оставить заблокированными, продолжая независимую разработку.

**Контекст между сессиями:** после каждого этапа обновить [журнал](../handoffs/M4_5_EXECUTION_LOG.md) и [PROJECT_STATUS](../PROJECT_STATUS.md): фактический SHA, ветку/PR, выполненные пункты, результаты тестов, незакрытые ошибки, следующую точную команду/файл. При новом сеансе первым делом прочитать AGENTS, это ТЗ и журнал, проверить git status и последний SHA. Не переписывать историю документа, а добавлять записи. Владелец сможет восстановить контекст **только по GitHub-ссылкам**.

## 1. Зачем мы это делаем и чего НЕ делаем

Пользователь уже импортировал собственные партии и историю задач Lichess. Цель — **быстро покрыть архив шахматными фактами**, затем выделить значимые события в каждой партии, построить локальную статистику и определить *актуальные направления для будущего тренера*, а не приписать человеку мотивы по одному ходу.

После завершения M4.5 пользователь может:
1. Просматривать библиотеку и видеть сразу по каждой партии результат, дебют, статус и полноту анализа, **точность партии**, собственные неточности/ошибки/грубые ошибки, важные события.
2. Открыть **паспорт партии**: точность/качество, ошибки по фазам, график оценки, кликабельные события с исходной позицией и лучшими проверенными альтернативами.
3. Запустить/продолжить анализ всех недостающих партий, **новые раньше старых**, с оценкой бюджета и паузой. Сохранённые оценки Lichess/Stockfish используются повторно; старые партии догоняются постепенно.
4. Видеть сводки: средняя сравнимая точность по партиям, среднее ошибок на партию, ошибки на 100 оценённых собственных ходов, распределение тяжести, дебюты/фазы, использование возможностей, сравнение периодов, полнота данных.
5. Получать **текущий профиль** преимущественно по недавним играм, а не по партиям 5–10-летней давности; весь архив служит для истории развития.
6. Найти по каждому выводу конкретную партию и ход, оценку, вариант, проверенную метку. Неизвестное честно помечено unknown.

**Вне M4.5:** создание или выдача задач, интервальное повторение, уроки, голос, LLM-диагнозы, объяснение субъективных мыслей, самостоятельный анализ чужих live-игр, таблицы эндшпилей Syzygy, классификация сложных стратегических планов как доказанного факта, скачивание чужих шахматных баз без оценки лицензии, редизайн всего приложения.

**Ключевое решение:** использовать один установленный пользователем Stockfish UCI. Никакого ансамбля движков. Лёгкая проверка всей партии и небольшой **условный** углублённый проход по спорным точкам — оптимизация ресурсов, а не обязательная демонстрация высокой глубины. Классификаторы — отдельный дешёвый слой поверх сохранённых фактов.

## 2. Что реально есть сейчас: инвентаризация перед кодом

Не начинать с новой архитектуры «с нуля». В main уже:
- src/chess_coach/lichess/repository.py: SQLite SCHEMA_VERSION=9; таблицы games, accounts, account_games, game_revisions, game_moves, game_positions, game_phase_boundaries, game_notes, analysis_runs, position_assessments, move_classifications, puzzles/puzzle_attempts. SQLite writer и backup через sqlite3.Connection.backup(), integrity_check. Сохранение PGN/ревизий и кеширование.
- src/chess_coach/lichess/analysis.py: CompletedGameAnalyzer, AnalysisOptions, одна позиция на ply, single UCI Stockfish, кеш по fingerprint/parameters, сохранение оценок и классификаций (CLASSIFIER_VERSION=cc-cpl-thresholds-v1). По умолчанию 100k nodes/position, MultiPV=1. Это исходная точка, а не новое требование пересчитывать весь архив на 100k.
- src/chess_coach/lichess/api.py и archive.py: GET архивов Lichess с evals/accuracy/opening/division, индивидуальный export PGN.
- src/chess_coach/lichess/statistics.py: общая статистика, фильтрация, распределение по фазам, coverage; в текущей функции latest-run выбор может смешать разные source/settings при отсутствии явного фильтра.
- src/chess_coach/window.py: базовые библиотека, график и одиночный анализ; src/chess_coach/lichess/service.py — фоновые синхронизации.
- **Обнаруженная проблема:** импорт Lichess сохраняет analysis_runs как complete даже при частичных оценках; для той же линии наличие complete lichess-pgn может блокировать последующее обновление, хотя появились новые значения. Исправить до массовой обработки и покрыть регрессионными тестами.
- Техдолг [#5](https://github.com/feshakomissarov-prog/chess/issues/5): новые UX-прогоны и реальный пользовательский Stockfish E2E не подменять автоматическими тестами.

Выполнить карту исходной схемы, API, зависимостей, текущих UI-сценариев и существующих тестов. Проверить и документировать инварианты, не менять личную БД. Все тесты — на tmp_path и fake engines/fake Lichess.

## 3. Контракт паспорта партии (Game Analysis Report)

Паспорт — **логическое представление** поверх нормализованных таблиц. Не сохранять его единственным огромным JSON и не дублировать исходные PGN/FEN в десятках новых мест. У каждого поля есть source, method_version, completeness/status. Допускается versioned summary cache для быстрого UI; он всегда пересобирается из нормализованных фактов.

### 3.1 Контекст партии

- game_id, текущая line_revision/line_hash/position identifiers, account_id и owner_color; один game_id может принадлежать нескольким аккаунтам — не удваивать движковый расчёт.
- UTC game_started_at (createdAt/достоверная дата PGN), data_precision (timestamp/date_only/unknown), finished_at если действительно известен; **дата импортирования не заменяет дату игры**. Unknown дата не включается молча в недавние окна.
- White/Black, рейтинг обеих сторон если доступен, результат и его определённость (win/draw/loss/unknown/aborted), rated, control category (ultrabullet/bullet/blitz/rapid/classical/correspondence/unknown, если корректно определяется), source.
- Standard chess only; другие варианты и сломанные PGN доступны как архив, но не движковая статистика стандартных шахмат.
- Opening code/name/family/variation, reference dataset revision, last matched position and ply, provenance from Lichess или локального справочника; не путать выход из справочника с шахматной ошибкой.
- Phase per ply: opening/middlegame/endgame/unknown, provenance/version. Lichess division имеет приоритет как известный источник; fallback — отдельный версионированный алгоритм; «до 10-го хода дебют, после 30-го эндшпиль» запрещено выдавать за объективную истину.

### 3.2 Полнота и источник анализа

Паспорт содержит ОТДЕЛЬНО для lichess-pgn, stockfish-fast, stockfish-review:
- last_checked_at, engine or Lichess provenance, engine version/fingerprint, limit kind/value, depth/nodes when present, classifier version, line_hash, result status (missing/partial/complete/stale/failed/cancelled/unknown).
- total plies, scored positions (with ply=0 отдельно), **comparable own move pairs** (до и после хода), coverage %. Партия из 60 ply с 8 оценками не считается полностью анализированной.
- Partial Lichess evals обновляются при позднем экспорте той же партии без дубликатов и без удаления прежних значений; сопоставление по ревизии линии/ply, обновлять оценку только с определённой provenance policy. Несовместимые версии хранить как отдельные run.
- Пользователь должен понимать: «нет ошибок» ≠ «не рассчитано», null ≠ 0, source и version видны.

### 3.3 Обязательные показатели партии

Всегда показывать числитель, знаменатель и источник; если данных нет, значение null/«нет данных».

A. Сыгранные собственные ходы, оценённые собственные ходы, охват собственных ходов и оценённых позиций.
B. Counts своей тяжести: good/neutral, inaccuracy, mistake, blunder, unknown. **«Зевок» и «грубая ошибка» — одна категория blunder, не две суммы.** Отдельные подтверждённые best moves (только когда есть надёжный лучший ход ПЕРЕД решением), иначе unknown. «Блестящий» не реализовывать.
C. По каждой тяжести: общая и по opening/middlegame/endgame/unknown; **N событий за партию** и **на 100 оценённых собственных ходов**. Opponent metrics отдельно и только для анализа предоставленных возможностей.
D. Average CPL для собственных **не-матовых** сопоставимых ходов; mate discontinuities отдельно и не заменять огромным фиктивным CP. Win-chance loss из одного версионированного и воспроизводимого подхода, если модель доступна. «Качество хода» может быть вычислено, «точность» требует отдельного определения.
E. **Официальная Lichess accuracy** (если пришла, для выбранной стороны) отдельно от **Chess Coach accuracy** с явными formula_id/version. Только сопоставимые анализированные значения. Никаких выдуманных 95% из CPL; если локальная формула не готова, показывать официальную или «недоступно». Не переименовывать похожую метрику в официальную точность.
F. Число candidate/confirmed missed opportunities, successful punishments, lost advantages, successful holds/saves, with denominators of objectively eligible opportunities and detection coverage; не делать проценты при нулевом знаменателе.
G. 3–10 важнейших событий с прямой ссылкой game_id/line_revision/ply, типом, severity, before/after, best alternative, evidence/status и фазой.
H. Список ходов и график оценки; на графике отметить границы фаз, метки качества/критических моментов; точность/качество по фазам вывести **отдельно** (не смешивать шкалу cp с процентами).

### 3.4 Состав записи отдельного хода

Ключ: game_id + line_revision + ply + owner account scope where needed. Ссылки на game_positions уже содержат FEN; не плодить вторую копию FEN. Шахматные факты:
- played UCI/SAN, mover, phase, source phase.
- before/after comparable normalized **White POV** cp или typed mate score, win chance if available, score quality and provenance, analysis_run_id.
- cp_loss non-negative for mover (white: eval_before - eval_after; black: eval_after - eval_before) with explicit null for mate/sparse evaluations. Не считать abs(eval_before - eval_after).
- best_uci, best_pv, alternatives only when actually computed on before-position; top candidate(s) and their scores/limits, no imaginary PV from Lichess position score alone.
- severity, severity_policy_version, chess_coach_accuracy if available with formula version, reviewed flag, extraction confidence, comments.
- event references, multiple tags and evidence IDs, with phase and current line.
- Позволить повторный пересчёт severity/event tags **без Stockfish** после изменения политики.

## 4. Обязательный каталог классификации: широкий охват, доказательность

**Две независимые оси**: (1) тяжесть хода, где категории взаимоисключающие: good/neutral, inaccuracy, mistake, blunder, unknown; (2) события и мотивы, где у одного хода может быть 0..N меток. Ход с вилкой и потерей фигуры **один blunder** плюс две возможные метки. Признак на доске не равен установленной причине человеческого решения.

### 4.1 Базовая тяжесть и качество

Классификатор v2 проверяет POV, mate, engine noise, до/после и phase. Историческую v1 не уничтожать: сохранить algorithm_version и возможность пересчёта. Пороги для CPL как начальная совместимая политика: >=50 inaccuracy, >=100 mistake, >=200 blunder; анализировать влияние шкалы на выигранные/проигранные позиции. Рядом можно считать потерю вероятности выигрыша или WDL в отдельной политике. **Не объявлять фиксированные CP-пороги окончательной объективной мерой**, и не менять тихо значение старых labels. Best — подтверждённый совпавший лучший ход или строго проверенная эквивалентная альтернатива; отсутствие PV => best unknown. Параметры и версия обязательны.

### 4.2 Каталог событий (зарегистрировать все коды, но не притворяться, что все детектируются)

**BASE** (обязательно реально): move_quality_decrease; large_eval_swing; winning_to_equal; equal_to_losing; winning_to_losing; allowed_mate; missed_mate; missed_opportunity; opportunity_used; winning_advantage_lost; defensive_resource_missed; strong_defense_confirmed; opponent_blunder; best_move_confirmed (при наличии best_uci), engine_disagreement/uncertain; outcome_unknown. Переходы состояний задавать версионированной метрикой win-chance/WDL или осторожной CP-зоной, а не случайной красивой цифрой.
**MATERIAL/TACTICS** (детектировать rule-based на выборочно углублённых событиях): hanging_piece, hanging_pawn, lost_piece, unfavorable_exchange, overlooked_capture, fork, knight_fork, pin, skewer, discovered_attack, double_attack, overloaded_defender, back_rank_threat, lost_material_via_tactic, missed_tactical_gain.
**KING**: missed_forced_mate, allowed_forced_mate, back_rank_mate, king_exposure_candidate.
**OPENING**: named_position, repertoire_or_book_departure (нейтральный факт), early_material_error, early_eval_drop, recurring_opening_position_error, opening_trap_candidate. Не считать выход из CC0 автоматически плохим ходом.
**ENDGAME/DEFENSE**: lost_draw, failed_to_convert_winning_position, promotion_miss, endgame_exchange_error, missed_saving_resource, defense_success, material_conversion_success.
**STRATEGIC** только tentative/unknown и без учебных диагнозов: pawn_structure_change, isolated_pawn, doubled_pawns, weak_square_candidate, piece_activity_candidate, king_safety_candidate. Можно вычислить дешёвые структурные факты, но **запрещено выводить cause=непонимание стратегии без verified evidence**.
**POSITIVE**: opponent_error_punished, only_good_move_found (подтверждение MultiPV/сравнения кандидатов), stable_advantage_converted, critical_defense_found.
**UNKNOWN**: no_verified_motif — отсутствие доказательств полноценный результат.

Отдельно показать confidence_state: verified / candidate / unknown / rejected; detector_version, conditions, evidence (FEN position reference, played, best move, PV, capture sequence/attack evidence), source run, reference to tags. Не приписывать детектору наблюдение, для которого нет фактов. Оставить расширяемый реестр detector definitions + pure functions; новая метка регистрируется/прогоняется по сохранённым событиям без повторного общего движкового расчёта. Обязательно unit tests на ложные срабатывания, обе стороны, mate, жертвы, fork без реальной выгоды, pinned piece с законным исключением, discovered checks, promotion, en passant. Если детектор ошибается на нетривиальных примерах, понижать статус в candidate, не подменять verified.

### 4.3 Упущенные возможности: особенно важно

Событие возникает, когда предыдущий ход соперника дал **верифицируемую** возможность (существенное улучшение outcome/score или тактическую выгоду), но наш ответ её не сохранил. Установить relation между ply(opponent mistake) и ply(our reply), сравнить eval PRE/POST от нашей перспективы и доступный best alternative, учесть win chance, mate, move quality. Одна и та же ошибка соперника не должна создавать несколько «возможностей» на каждый следующий ход.
- opportunity_created, opportunity_used, opportunity_missed; metric eligible_opportunities и n_used / eligible_count, доля и coverage.
- Механизм без подтверждённого best move может пометить упущенную оценку, но не выдумывать *какую атаку следовало провести*.
- Местами нужно уточнение Stockfish, но массовая классификация может начаться только на сохранённых последовательных оценках; отсутствие финальной уверенности => candidate.
- Для defense аналогично: потерянная возможность свести партию к ничьей / сохранить позицию. Не каждый неидеальный ход в уже проигранной партии превращать в новый «упущенный выигрыш».

### 4.4 Позитивные решения

Положительные события хранить независимо от отрицательных. Подтверждённые лучшие ходы, единственный хороший защитный ход (требует сравнения хотя бы двух кандидатов), наказание ошибки соперника, удержание преимущества. Не называть «brilliant» по жертве или небольшой CPL.

## 5. Текущий профиль и средние агрегаты: без влияния древних партий

**Два принципиально разных вычисления**:
1. Current profile — по последним **90 дням** в рамках одного аккаунта и сопоставимого контроля; если данных мало, расширить окно до 12 месяцев и явно назвать расширение/показать отдельно N. Предварительная достаточность: >=30 полностью или достаточно покрытых партий на сегмент; малые выборки всё равно отображаются как provisional, не ранжируются как подтверждённые слабости. Никаких скрытых 5–10-летних партий в текущем рейтинге проблем.
2. Historical timeline — весь архив, месяцы/кварталы/годы, сравнение сопоставимых контролей/цветов/рейтингов и источников с явным coverage. Одинаковая календарная дата из PGN может иметь date_only precision; не делать ложные временные выводы до секунд. Unknown dates отдельно. Историческая метрика не должна автоматически принимать роль учебного приоритета.

**Метрики на главных страницах, библиотеке и в паспорте:**
- game_count (всего/завершено/валидная линия/с анализом Lichess/Stockfish/partial/без), coverage games и coverage comparable own moves.
- result W/D/L/unknown и процент побед на корректных исходах; не подменять unknown поражением.
- **Средняя точность партий**: арифметическое среднее **официальных Lichess accuracy selected side** только по играм, где она действительно известна, со значением n из N и source=lichess; отдельно локальная версия Chess Coach accuracy при наличии утверждённой formula_id; не смешивать две шкалы и не приписывать официальную точность всему архиву. Средневзвешенная точность по ходам — **другая** метрика, при необходимости отдельно.
- per-game avg blunders/mistakes/inaccuracies на анализированных играх, n analyzed games и share qualifying games. Отдельно абсолютные суммы и per_100_assessed_own_moves (знаменатель — только реально сопоставимые собственные ходы).
- average CPL лишь для не-матовых comparable moves, n, median/p90 если есть смысл. Среднее CP и среднее accuracy не объявлять общим рейтингом уровня.
- % игр с хотя бы одним blunder, % событий opening/middlegame/endgame/unknown, severity by phase, own vs opponent.
- opportunities_created/used/missed, conversion rate **used/eligible**, нулевой знаменатель => null, certainty/coverage.
- opening family/name/ECO and by color: games, WDL, accuracy source, error_rate, first early error, critical positions; разные цвета не смешивать.
- опционально win chance peak/draw saving/conversion outcomes; count verified examples and display proof. Clock pressure не приоритет: хранить существующие часы, но не выделять отдельный крупный UI.
- сравнение периодов только при похожей доле анализа, сопоставимом контроле и источнике; показывать разброс/выборку и формулировать «в этих данных наблюдается», а не «вы стали хуже».
- Каждая сводка должна иметь фильтр account_id, период, color, clock_class, ECO/family, source/method version, analysis coverage и drill-down к game_id+ply; выбранный account обязателен для персонального профиля. Для смешанных аккаунтов — предупреждение, не silent duplicate.

**Выбор «главного» анализа**: явная stable source preference пользовательская; когда один метод неполный, другой полный — показывать их рядом, покрытие каждого отдельно, не заполнять автоматически одну классификацию оценками разных engine/settings без versioned combination policy. В общем счётчике проанализированных игр требуется completeness по тому run, который выбран для метрики. Stats cache key включает фильтры, выбранную source/version и database/statistics revision. Изменения PGN/аккаунта/результатов/метрик инвалидируют корректные агрегаты.

## 6. Производительная архитектура расчёта и экономия ресурсов

### 6.1 Data inventory — почти бесплатно

До запуска Stockfish вычислить покрытие для каждой current game line: валидность, количество ply, какие pre/post оценочные пары существуют по источникам, точность, ECO, границы фаз и наличие ранее рассчитанных событий. Выдать кандидатов по missing components:
- NEED_SOURCE_EVAL, NEED_PAIR_EVAL, NEED_BEST_MOVE, NEED_PHASE, NEED_OPENING, NEED_CLASSIFICATION, NEED_MOTIF, NEED_REPORT; статус UNKNOWN/UNSUPPORTED также самостоятельный.
- Отдельные флаги по каждому pass и версиям. Не трактовать старый classifier v1 как отсутствие движкового результата.
- No-op при повторном сканировании без новых данных.
- Приоритет сортировки: новые завершённые партии первыми, а затем старые по убыванию даты. Unknown date в конце. Обрабатываемый пользовательский диапазон и весь архив поддерживаются; можно включить фоновое продолжение, а не запускать весь архив без разрешения.

### 6.2 Fast pass

Одна UCI engine process, по умолчанию Threads=1, bounded Hash. Сначала импортированные готовые evals Lichess; если они дают достаточно сопоставимых пар для нужной метрики, не пересчитывать их только ради числа. Для недостающих/несовместимых данных — быстрый локальный Stockfish по бюджету nodes/depth/time, ограниченный и отменяемый. Начальный ориентир для тестирования: 10k–30k nodes/position, затем калибровать на 20–30 **синтетических/публичных** тестовых партиях на доступной машине (не объявлять это объективно достаточной глубиной). Допустимы настройки из UI и профили quick/standard, но по умолчанию ограничить нагрузку. Стандартный existing CompletedGameAnalyzer 100k nodes не использовать бесконтрольно для массового pass. Проверить качество/точность классификаций на контрольном deep subset.

У reuse/caching ключ должен включать FEN/EPD c корректными castling/en-passant/side-to-move, UCI fingerprint, limit, MultiPV, engine options и модель нормализации; **не** использовать только ECO или просто placement. Повторяющиеся дебютные позиции особенно выгодны для кеша. Позиционный кеш должен быть bounded и инвалидируемым; при неопределённом лимите/версии — cache miss.

### 6.3 Conditional review pass (НЕ обязательный deep pass каждой партии)

Выбирать ограниченный список событий по серьёзности, неопределённости, оценке mate, подозрению на missed opportunity, нужде в лучшем ходе. Если имеющихся оценок достаточно — skip. Для избранных мест увеличить budget и при необходимости MultiPV=2 (или 3 по настройке), сравнить альтернативы; зафиксировать cost/time/nodes. Кандидатов без доказательств не считать verified. Ограничить бюджет на одну партию и на весь batch. По результатам pilot выяснить, улучшает ли deep pass стабильность severity; если нет, предпочесть быстрый анализ.

### 6.4 Classification pass: никакого нового движка по умолчанию

Pure functions читают snapshot game moves + оценки + best alternatives + phase/opening. Составляют severity, events, 0..N tags, evidence and status. Дешёвое повторное выполнение только при изменении detector_version, новой оценке, изменении line_hash или появлении кандидатов; не повторять Stockfish каждый раз. При нехватке доказательств — NEED_REVIEW, но общий отчёт сохраняется частичным.

### 6.5 Aggregation pass

SQL aggregates и индексированные представления, pages и bounded queries для 10k+ игр, не читать в GUI весь архив. Формировать per-game summaries и per-period aggregates **только** из достоверных выбранных facts, с revision-aware cache invalidation. Сортировка recent-first, курсор очереди, pause/resume, метрики прогресса per games и per positions. Никаких скрытых полных перерасчётов при смене фильтра UI.

### 6.6 Lichess server eval refresh

По ОФИЦИАЛЬНЫМ API читать уже существующие evals/accuracy/opening/division и targeted game export. **Документированного публичного массового заказа серверного анализа нет — не использовать недокументированные POST или браузерные обходы.** Не считать GET cloud-eval полноценным серверным анализом партии. Периодическое обновление новых партий пользуется существующей авторизацией; целевой refresh старых missing Lichess eval — отдельная bounded опция с opt-in, checkpoint, backoff при 429, отказоустойчивостью, без OAuth popup без клика, без бесконечного опроса старых партий. Полное отсутствие Lichess score не мешает локальному Stockfish. Приоритет живой игре у сети.

## 7. Локальная база SQLite: мигрировать только когда действительно нужно

**SQLite — файловая реляционная база данных** приложения. Не вводить PostgreSQL, отдельную analytics DB или ручное копирование таблиц туда-сюда. Существующая схема v9 и таблицы анализа пригодны к расширению.

Действия:
1. Провести audit существующих tables/foreign keys/indexes и diagram dependencies; составить минимальный DDL diff: можно ли остаться на текущей таблице с дополнительными полями/новым derived view; физические таблицы добавлять только при доказанной необходимости.
2. Предварительно рекомендуемая логическая модель:
   - существующие games / account_games / game_revisions / game_moves / game_positions без пересоздания;
   - существующие analysis_runs и position_assessments (при необходимости пара extension fields или новая compact table для alternatives и per-run provenance);
   - analysis_jobs — persistent queue, уникальность по game_id+line_hash+engine_config+pass; account scope для фильтрации, но одну и ту же игру не считать дважды;
   - analysis_coverage — может быть SQL view/derivation без отдельной физической таблицы, если быстро;
   - move_metrics — normalized derived move facts + version, а не новая копия хода/FEN;
   - game_events — событие с event_type, status, key, source, evidence pointer, stable id; event_tags — M:N к event; detector_versions;
   - opening_reference dataset (CC0) и opening_matches только при необходимости; summary cache только если измерена потребность. Не реализовывать 10 таблиц автоматически потому что они перечислены.
3. Schema version increment из 9, только **аддитивные транзакционные миграции**, CREATE TABLE/INDEX IF NOT EXISTS, ALTER TABLE ADD COLUMN при необходимости, без DROP/RENAME/copy-rebuild крупной games/game_moves или массовых UPDATE без необходимости. Структура game_id/current_revision обязана остаться.
4. Миграция: до изменений проверить текущую version, составить план, сделать **консистентную SQLite online backup через Connection.backup()**, проверить PRAGMA integrity_check на backup и достаточность места; соблюдать WAL. Новая транзакция + PRAGMA foreign_keys=ON; изменить schema и PRAGMA user_version атомарно в конце. На ошибке ROLLBACK и понятное сообщение. Backup не включает secrets в Git. Повторный запуск без дубликатов/изменений. Если migration уже выполнена — no-op. Не копировать WAL-файл отдельно во время работы.
5. Тесты: fresh install; upgrade v9 fixture с партиями/notes/puzzles/analysis; upgrade из более старой схемы через существующие миграции; wrong newer user_version => fail; rollback после искусственного сбоя, повторное выполнение, migration при наличии уже созданной одной из новых таблиц, сохранение FK и PGN/history/analysis, VACUUM не обязательный. Создать fixture из синтетических данных. Нельзя запускать миграцию на личной базе разработчика без явного разрешения.
6. Тестировать с конкурентным reader/writer и SQLite WAL/lock; ограничить транзакции, использовать существующий single writer. Никаких чтений скриптом произвольных пользовательских дисков.
7. **Идентичность результата**: game_id+line_revision+line_hash+source+engine fingerprint+params/method version; old revisions не уничтожать, новые derived events «stale» для текущего профиля. Source Lichess partial обновлять без стирания существующих совместимых scores.

**Важно:** если отдельная новая таблица не нужна, **не создавать**. Для временных расчётов использовать Python память с bounded chunks; итоговые факты с устойчивыми ключами сохранять в SQLite. Обосновать миграцию в PR и журнале.

## 8. Дебюты и фазы: локально и без сомнительных диагнозов

Источник: [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings), CC0, поля ECO/name/PGN/UCI/EPD. Проверить версию, происхождение и формулировку license перед включением данных. Не копировать GPL/AGPL программный код. Нормализованное сопоставление позиций (EPD) с учётом транспозиций, использовать последнее известное совпадение при проходе партии; сохранить opening_family/variation и matched_ply. Если не совпало — unknown. Оригинальный opening metadata Lichess отдельно, возможный conflict открыто показывать.

Считать own error rate на собственных ходах в дебютной фазе; color, rating, time control, period, game count, number of evaluated moves, mean comparable accuracy, WDL. Вывод «не знает дебют» разрешён лишь как рабочая гипотеза при достаточной выборке повторяющихся ранних собственных ошибок, а не из-за одиночного выхода из CC0. Показать первые критические позиции и переход в доску.

Если Lichess division нет, реализовать явно маркированный phase fallback по объективным признакам или оставить unknown; покрытие фаз — самостоятельный показатель. Endgame/middlegame не делить по голому номеру хода.

## 9. Qt UX: полный паспорт и сводная библиотека, но без крупного редизайна

**Библиотека партий**: columns / compact badges: дата, соперник, цвет, результат, ECO/name, local/official accuracy (выбранная source), own inaccuracy/mistake/blunder counts, state coverage (not analyzed/partial/complete), что именно оценено. Базовые counts на партию получаются из per-game materialized/read model, не из синхронного UCI. Фильтры анализирован/не анализирован, период, источник, дебют, фаза проблем, тяжесть, цвет, контроль; сортировки и reset filters; paginated 10k+.

**Паспорт партии**: интерактивная доска/ходы + история оценок, plot с **реальными** границами opening/middlegame/endgame (unknown заметно), маркеры severity/opportunities, отдельные per-phase accuracy/quality показатели, суммарные counts per severity, средняя потеря, текущая source/coverage. У списка событий type/severity/evidence; клик на ход открывает правильную позицию ДО решения и best alternative, если есть, без отправки ходов. Переключение источника анализа не изменяет raw games.

**Общая аналитика**: 3 рабочих раздела (без полного редизайна): «Обзор» (текущий профиль, средняя точность с N, error rate, динамика), «Дебюты и фазы» (раздельно белыми/чёрными, выборка/coverage, подтверждённые первые ошибки), «Критические события» (фильтруемый список конкретных позиций, тип/верификация, перспектива). Дополнительные карточки: «где ошибаемся» и «где использовали возможности». Статистика за последние 90 дней по умолчанию при достаточной выборке, архив отдельно по выбору пользователя. UI должен пережить пустую БД, отсутствующий двигатель, 0 eval и mixed account без crash; русский язык.

**Управление очередью**: видимый статус N eligible/queued/processed/error/unknown, ETA только по фактической скорости и с пометкой «оценочно»; старт по явному действию, предпочтение новых игр, pause/resume/cancel, CPU/Hash/limit budget и ограничение сетевого refresh; фоновое продолжение после согласия пользователя. Не запускать бесконечный сбор только из-за открытия вкладки. Автоматически обновлять статистику при новых результатах. Предлагать источник точности явно, а не выдумывать отсутствующие значения.

**UI-архитектура**: вводные QAbstractTableModel/пагинация/worker/сигналы по мере необходимости, но не переписывать весь window.py. GUI thread не исполняет долгие SQLite aggregates/engine. Полная совместимость wheel navigation, premove, promotion, live Lichess.

## 10. Безопасность и fair play — непреложные границы

1. **Ни при каком сценарии Stockfish/другой engine и его PV/подсказки не вычисляются для live human opponent в клиенте**, даже если расчёт исторических партий был начат раньше. Проверка fail-closed на старт/возобновление/смену аккаунта/unknown connection/новую игровую сессию/перед каждым game job, а для уже работающего worker — отмена/пауза при появлении активной человеческой партии. BOT аккаунт ≠ встроенный официальный Lichess AI. Движок разрешён лишь на завершённых партиях и в явно безопасном состоянии; disconnect или unknown => safe pause. Тесты включая гонки/TOCTOU.
2. **Персональные данные:** не коммитить SQLite, PGN, тайные токены, реальные партии, логи с именами противников и приватные графики. Не публиковать файлы на GitHub. Не обращаться к реальному OAuth в тестах. No telemetry.
3. **Ночная автономная работа Codex** разрешает код, локальные синтетические/публичные fixture computations и fake engine. Не разрешает автоматически исполнять миграции/дорогое массовое Stockfish/real OAuth на постоянной личной SQLite владельца. Если Codex имеет доступ к локальной персональной DB, не изменять её без отдельного подтверждённого user opt-in и корректной проверенной backup. Допустимо отдельно проанализировать синтетический архив или специально предоставленную временную копию. Никакого auto-download движка.
4. Lichess API только официальные endpoints, Retry-After и 429, explicit account ownership; сетевые действия не блокируют live play, progress/report не маскируют отказ.
5. Любое нарушение неизменяемых границ — это реальный блокер для соответствующего subtask, но не повод бросать оставшиеся независимые этапы.

## 11. Проверка качества: обязательно до заявления «готово»

**Tests fixtures:** минимум 20 ручных synthetic game reports с ожидаемыми results/accuracy/source/coverage и событиями; 0,1,10,100,10k+ партий (синтетический набор). Both colors, partial Lichess, late incremental eval, no PGN/unknown end, 0 own moves, mate and zero cp, duplicate accounts, stale takeback, rapid/blitz segmented, unknown date, drawn game, missing phase, ECO transposition.

**Metric invariants:**
- Sum severity buckets + unknown = number of own moves within selected scope (and 0 != unknown); per100 denominator = count of **comparable assessed OWN moves**. Unknown not disguised as good.
- Sum W/D/L/unknown = number of games included by filter. Average accuracy: arithmetic mean over N truly available selected-source game accuracy only; no pooled lichess+local. For 0 values => null.
- Blunder on one ply cannot be counted twice because of fork+pin tags.
- Pair before and after same source/limits or explicit comparability rule; correct White POV flip for black.
- On rebuild with changed classifier version existing raw scores not lost, Stockfish not called. Current profile never selects an old game outside window merely to reach sample threshold; if low data, mark small sample.
- Opening/phase mapping: no time off-by-one; synthetic transpositions; unknown phases not silently counted as opening. A game in two account_games links is engine-analyzed only once but aggregated distinctly per selected owner.
- Queues can crash after position and between games, recover without duplicate complete and without orphan UCI process; repeated enqueue idempotent. Engine worker stops live-human game fail closed on network disconnect or uncertain status.
- Partial Lichess PGN assessment remains partial; new late evaluations recognized when identical move line, no duplicate run explosion.

**Windows gates:** Python >=3.12, pytest + pytest-qt QT_QPA_PLATFORM=offscreen, compileall, offline isolated window smoke, lint/diff-check; benchmark SQLite list/filter/report with 10k+ synthetic games, bounded RAM, responsiveness, 429 fakes, migration backup/integrity/rollback. UI manual items separate as NOT RUN unless actually run. Stockfish real binary is optional controlled E2E and cannot be substituted with fake while claiming real. Record precise command, counts, SHA, test outcome, performance parameters, exclusions; no «green» until current SHA CI green.

**Required sample reports saved as *synthetic* fixtures in tests/docs, not personal data:** example game passport, one verified missed opportunity, one uncertain tactical motif, one recent-vs-old comparison, one summary including average game accuracy n/N and mean mistakes per analyzed game n/N.

## 12. План исполнения: отдельные проверяемые этапы в одном большом PR

**M4.5-0 — Audit/contract (обязательно первым)**: карта реальной v9, выявить дефект late Lichess eval, все имеющиеся API/fields; записать schema strategy/data contract and baseline CI. Отдельный commit + log.

**M4.5-A — Foundation/migrations**: минимальный additive schema DDL + fixtures + backup/rollback/reopen/integrity. Existing games untouched. Coverage query/view and revision handling. Commit + log.

**M4.5-B — Lichess hydration**: исправить обновление partial evals и accurate coverage, официальные source-specific runs; targeted refresh backlog bounded/rate-limited, fake network tests. Если OAuth реально отсутствует, fake path обязателен и работа продолжается. Commit + log.

**M4.5-C — Batch engine**: persistent queue and fast low-budget pass, newest-first, existing scores reused, resource controls, caching, cancellation/resume/fail-closed. Fake UCI + thread-lifecycle tests. Commit + log.

**M4.5-D — Move metrics/event candidates**: stable severity v2, cp/mate/WDL/accuracy provenance, opportunity detection, saving/report API and coverage; quality and cross-source tests. Commit + log.

**M4.5-E — Selective review and motif registry**: cheap review selection + capped budget, MultiPV optional, broad 4.2 event schema, verified vs candidate rule-based detectors; positives and negatives, no forced classification. Commit + log.

**M4.5-F — Openings/phases**: integrate CC0 matching with transpositions, full phase provenance, opening error stats by owner color and source; migration/index if proven necessary. Commit + log.

**M4.5-G — Game passport and library UX**: compact per-game accuracy/errors/status columns; phase-aware eval graph, event list/drilldown with before/after position, source/coverage and callbacks; Qt tests. Commit + log.

**M4.5-H — Current/historical aggregations**: 90 days / 12 months small-sample fallback, average game accuracy and errors/game, severities per100, opportunity conversion, opening/phase trends and evidence-driven current focus; SQL performance. Commit + log.

**M4.5-I — Stabilization**: 10k+ synthetic benchmarks, full Windows CI, migration/backup tests, concurrency, old functionality regression, docs + sample reports, complete execution journal. Final PR description with truth table DONE/PARTIAL/NOT RUN/BLOCKED, exact SHA.

**Не останавливать весь проект после очередного шага**: если одно направление невозможно безопасно завершить из-за внешней лицензии, отсутствия реального engine/OAuth или неоднозначной эвристики, оформить критерий deferred и synthetic verification, продолжить остальные. Не делать фальшивых заглушек. Если сборка/тесты красные, сначала попытаться починить, не скрывать проблему, но не прекращать независимые работы.

**Что должны иметь все этапы:** working import and app, tests, checkpoint record in journal, PR commit. Не нужно запускать все CI на каждом промежуточном коммите, но до передачи в review финальный Windows CI обязателен. Если CI не запускается, прямо указать причину и статус, а не ждать ответа владельца.

## 13. Источники реализации и лицензии

Из [нашего исследовательского каталога](../research/OPEN_SOURCE_CHESS_PROJECTS.md) выборочно изучить:
- [hdxsfbr/chess-trainer](https://github.com/hdxsfbr/chess-trainer) — fast coarse pass -> deeper selected positions; проверить заявленную лицензию прежде чем копировать.
- [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings) — CC0 ECO/name/EPD, latest-position backward matching, transpositions; сохранять версию source file.
- [franverona/blitzr](https://github.com/franverona/blitzr) (MIT) — analyze all, per-game accuracy/errors, opening stats, opportunity missed, click-through.
- [vincvloo/Chess-Mistake-Coach](https://github.com/vincvloo/Chess-Mistake-Coach) (MIT) — deterministic categories/position cache; не копировать questionable mate-as-CP into mean CPL.
- [PrestonYadegar/chess-coach](https://github.com/PrestonYadegar/chess-coach) (MIT) — broad motif catalog, background job and pattern evidence; **training/puzzle generation позже**.
- [jcabiles/chess-coach](https://github.com/jcabiles/chess-coach) (MIT) — pure motif functions, null-move opponent-threat probes; учитывать ограничения static exchange approximation и дорогостоящие probes.
- [Praxis-Chess/Praxis-Chess](https://github.com/Praxis-Chess/Praxis-Chess) (MIT) — evidence aggregation, verified diagnosis vs generated hypothesis. LLM portion сейчас не использовать.

Идеи можно изучать, прямой перенос кода/ассетов лишь при соблюдении лицензий, notices и прав. Конкурирующая GPL реализация не становится MIT от копирования. Отчёт по заимствованиям/новым зависимостям обязателен.

## 14. Приоритет требований при конфликте

1. Безопасность пользовательских данных, fair play, отсутствие скрытых network/Stockfish actions.
2. Этот документ v2.0 для M4.5 > старое M4.5 v1 > исторические M3/M4 spec; старые данные/реальные функции не ломать.
3. Сначала корректная полнота и формулы, потом скорость, потом декоративный интерфейс.
4. Для детекторов отсутствие доказательств = unknown, не выдуманное объяснение.
5. По умолчанию новые игры первыми; старые — история, а не автоматический «текущий диагноз».
6. Если можно обойтись существующей SQLite таблицей/view, не делать новые сущности.

**Definition of Done:** пользователь может открыть библиотеку и увидеть корректные общие агрегаты и персональные карточки; отдельные выбранные партии/критические позиции доказательно ведут к своим ходам; большой архив инкрементально и безопасно анализируется недорогим локальным движком; работа возобновляется после перезапуска; показатель «текущий уровень» не испорчен древними партиями; Windows CI зелёный; результат и ограничения восстановимы из GitHub без текущего чата.
