# Полезные открытые шахматные проекты — исследовательский каталог Chess Coach

**Обновлено:** 2026-11-01 (три волны поиска). **Статус:** исследование и ориентиры, не решение о копировании/интеграции.  
**Проект:** [Chess Coach](https://github.com/feshakomissarov-prog/chess), Windows / Python 3.12+ / PySide6 / python-chess / SQLite / Lichess / локальный Stockfish.

## Как читать каталог

- **A** — открыть и разбирать в первую очередь перед проектированием соответствующей функции.
- **B** — полезная конкретная идея, но не для ближайшего PR.
- **C** — вспомогательная ссылка/сравнение.
- **Лицензия** — результат проверки файла `LICENSE` (если есть) либо явного указания в README. **Не означает**, что любой код, шрифт, набор фигур, модель и данные внутри автоматически лицензированы так же.
- Указанные функции — описание найденных исходников и README на момент проверки; **приложения не устанавливались, функциональность E2E не проверялась**. Не принимать README и бейджи за гарантию качества.
- **Ничего из стороннего кода пока не копировалось в Chess Coach.** Для использования исходного кода отдельно проверить совместимость лицензий и сохранить атрибуцию; для архитектурных идей можно писать собственную реализацию.

## A — первоочередные находки

| Проект, лицензия | Где смотреть реализацию | Что адаптировать для Chess Coach | Когда |
|---|---|---|---|
| **[CARA — Chess Analysis and Review Application](https://github.com/pguntermann/CARA)** · GPL-3.0 | [Аннотации per-ply](https://github.com/pguntermann/CARA/blob/master/doc/annotation_system_implementation.md), [архитектура Qt](https://github.com/pguntermann/CARA/blob/master/doc/architecture_outline.md), `app/controllers/annotation_controller.py`, `app/models/annotation_model.py`, `app/services/annotation_storage_service.py` | **Самый близкий аналог будущих мыслей:** стрелки/круги/текст на позиции, отдельные модель/контроллер/хранилище, выбор видимости, serializing при PGN export, несколько PV-планов, табличный список ходов. **Наш источник истины — SQLite + game_id/line_revision/ply**, PGN — переносимый экспорт. | M5 мыслей; раньше — UX аналитики |
| **[Chess Mistake Coach](https://github.com/vincvloo/Chess-Mistake-Coach)** · MIT | [`analysis.py`](https://github.com/vincvloo/Chess-Mistake-Coach/blob/master/chess_mistake_coach/analysis.py), [`coaching.py`](https://github.com/vincvloo/Chess-Mistake-Coach/blob/master/chess_mistake_coach/coaching.py), [`db.py`](https://github.com/vincvloo/Chess-Mistake-Coach/blob/master/chess_mistake_coach/db.py), [`position_cache.py`](https://github.com/vincvloo/Chess-Mistake-Coach/blob/master/chess_mistake_coach/position_cache.py) | SQLite `games`/`mistakes`/`practice_attempts`/`position_evals`, фазы игры, время на часах, выбор тактического мотива, связь «тип ошибки → тема задач», прозрачная кеширующая обёртка над движком. Сверить метод CP/mate: не копировать фиктивную матовую оценку в средний CPL. | После первого анализа архива |
| **[Praxis Chess](https://github.com/Praxis-Chess/Praxis-Chess)** · MIT | [`ARCHITECTURE.md`](https://github.com/Praxis-Chess/Praxis-Chess/blob/main/ARCHITECTURE.md), разделы Evidence graph, diagnosis, pattern aggregation, drills | Многоступенчатый разбор: engine facts → детерминированная диагностика → проверка утверждений → (опционально) языковое объяснение. Паттерны по множеству партий, план занятий, локальная LLM. **ИИ не назначает истинность шахматным утверждениям.** | Поздний M4 / M6 |
| **[jcabiles/chess-coach](https://github.com/jcabiles/chess-coach)** · MIT | [`ARCHITECTURE.md`](https://github.com/jcabiles/chess-coach/blob/main/ARCHITECTURE.md), `app/motifs.py`, `app/review.py`, `app/analysis.py`, тестовые fake engines | Угроза **до** зевка, категория «hope chess», обзор повторяющихся проблем, собственные blunders как повторяемые задачи, Leitner по категориям. Архитектурный `engine lock`/fake-seam и обязательные тесты; объяснения ИИ только поверх engine facts. **В live партии против человека никаких подсказок**, даже если у них есть режим анализа доски. | Тренер персональных ошибок |
| **[Blitzr](https://github.com/franverona/blitzr)** · MIT | [README](https://github.com/franverona/blitzr#readme), разделы Repertoire / Drilling / Blunders, код `lib/` | Сверять партии с **личным деревом дебютных ходов**, считать своим отклонением только собственный ход; создавать упражнения по упущенным возможностям и ошибкам; **подсказки ступенями:** фигура → клетка → стрелка; сессия из ~15 карточек; статистика с количеством реально разобранных игр. Хранилище SQLite с резервными копиями. | Обучение / дебюты |
| **[Mainline](https://github.com/kleinebossie/Mainline)** · AGPL-3.0 | [README](https://github.com/kleinebossie/Mainline#readme), `src/engine/`, `src/analysis/`, training methodology | Короткие **ежедневные занятия из своих слабостей**, FSRS spaced repetition, сроки пересмотра, прогресс, ограничение времени занятия; чёткое отделение методики обучения от движковой логики; **без LLM обязательно**. | Личный тренировочный план |
| **[DaisyChess](https://github.com/refaustria/daisychess)** · GPL-3.0-or-later (кроме отдельного MIT `apps/site`) | [README](https://github.com/refaustria/daisychess#readme), `packages/engine-core`, разделы Review / Engine registry / Puzzles | Архитектура нескольких движков, оценка/accuracy + coverage, MultiPV, сохранённые отчёты, локальный starter pack пазлов, «игрок моего рейтинга выбирает такой-то ход», Maia. Особо изучить **бюджет памяти движка и остановку ресурсов**. | Анализ / опциональная Maia |
| **[Opening Trainer — PySide6](https://github.com/vancoeur/chess-opening-trainer)** · GPL-3.0-or-later | [README](https://github.com/vancoeur/chess-opening-trainer#readme), `qt_app/`, `opening_trainer/`, [`NOTICE.md`](https://github.com/vancoeur/chess-opening-trainer/blob/main/NOTICE.md) | **Тот же Qt/PySide6 стек:** контрастная боковая навигация, светлая/тёмная тема, дерево дебютов, варианты, PGN, локальный Stockfish и повторения. Приблизительный образец будущего редизайна, не переносить UI буквально. | Визуальный редизайн / дебюты |
| **[En Croissant](https://github.com/franciscoBSalgueiro/en-croissant)** · GPL-3.0 | [README](https://github.com/franciscoBSalgueiro/en-croissant#readme), Tauri/Rust/TypeScript | Организация большой библиотеки партий, нескольких движков и дебютного репертуара. Сравнить сценарии поиска, выбранного хода, исследования позиции и запуска анализа без блокировки GUI. | После UI статистики |
| **[Blunder Tutor](https://github.com/MrLokans/chess-blunder-trainer)** · AGPL-3.0 | [README](https://github.com/MrLokans/chess-blunder-trainer#readme), [публикация об архитектуре](https://mrlokans.work/posts/building-self-hosted-chess-blunder-trainer/) | Замкнутый цикл «импорт Lichess/Chess.com → поиск зевков → задачи именно из них → статистика слабых тем». Отдельные пользовательские базы в multi-user режиме, интервальные повторения и подсказки на доске. | Тренажёр своих ошибок |
| **[CARA — аннотации отдельно от PGN](https://github.com/pguntermann/CARA/blob/master/doc/annotation_system_implementation.md)** · GPL-3.0 | `AnnotationController` → `AnnotationModel` → `AnnotationStorageService`; JSON/compressed PGN tags, checksum | **Технический вывод:** события рисования должны принадлежать точному `game_id + line_revision + ply`, а передача через PGN — отдельный сериализатор. При `takeback` сохранить старые ветки; нельзя связывать заметку только по FEN или перезаписывать чужой PGN. | M5 архитектура |

## B — специализированные тренировки, задачи и объяснения

| Проект, лицензия | Что интересно | Предпочтительное применение |
|---|---|---|
| **[PrestonYadegar/chess-coach](https://github.com/PrestonYadegar/chess-coach)** · MIT | FastAPI/SQLite, очередь задач из личных ошибок + открытой базы Lichess, анализ с кэшированием | Самогенерирующиеся упражнения |
| **[gabriel-kohler/chess-coach](https://github.com/gabriel-kohler/chess-coach)** · GPL-3.0 | Свои проблемы, дебютный репертуар с SRS, оценка вероятностей и упражнений, Maia calibration | Сравнение учебных сценариев и проверки метрик |
| **[tabia](https://github.com/daxaur/tabia)** · MIT | Четыре режима изучения дебютов: Learn/Practice/Drill/Hyper, Lichess OAuth, локальный Stockfish WASM, система стрелок/поворота доски | UX тренажёра дебютов; не копировать OAuth flow без отдельной проверки |
| **[Chesser](https://github.com/scarpent/chesser)** · AGPL-3.0 | Большое дерево репертуара, сохранение собственных пометок/вариантов, SRS, Markdown/inline diagram in comments; [пример репертуара](https://github.com/scarpent/chesser/blob/main/examples/sample_repertoire.json) | Несколько вариантов и комментариев к одной позиции |
| **[chessrepeat](https://github.com/jacokyle01/chessrepeat)** · **файл LICENSE не найден; README заявляет open source** | FSRS с параметрами stability/difficulty для каждого хода, совместные дебютные тренировки | Идея планировщика, **код без лицензионной проверки не брать** |
| **[qam4/chess-coach](https://github.com/qam4/chess-coach)** · Apache-2.0 | Локальный Ollama/Qwen, structured data через собственный движок Blunder, **Socratic mode**: задавать вопросы вместо выдачи лучшего хода; отдельные проверки factual/coaching quality | Будущий голосовой/текстовый диалог и методика «помоги найти самому» |
| **[hdxsfbr/chess-trainer](https://github.com/hdxsfbr/chess-trainer)** · **LICENSE-файл не найден (README-бейдж заявляет MIT)** | **Two-pass анализ:** быстрый depth 12 по всей партии, глубокий depth 20 только по проблемным позициям; deterministic motif explanation (fork/pin/skewer/hanging) | Экономия CPU при большом архиве, **сначала выяснить лицензию** |
| **[Chess Recorder](https://github.com/pguntermann/ChessRecorder)** · GPL-3.0 | iOS/Swift: распознавание шахматной лексики, локальная диктовка и PGN, задержка подтверждения фразы, correction dictionary, интерфейс транскрипта | **В будущем**, при работе с речью: изучить VAD/тайминг/ошибки распознавания. **Это ввод ходов голосом, не запись объяснения мыслей**; нативный Swift код не подходит для переноса на Windows |
| **[Maia Chess](https://github.com/UNO-AI-Lab/maiachess)** · GPL-3.0 | Человекообразные ходы/ошибки в зависимости от уровня, вместо заведомо идеальной игры Stockfish | Эмуляция оппонента своего уровня, **не** выдавать за мысли пользователя |
| **[PyChess](https://github.com/pychess/pychess)** · GPL-3.0 | Зрелый Python-клиент, шахматные состояния, часы, история, PGN и интеграции | Базовые архитектурные сравнения; другой UI (GTK) |
| **[Lucas Chess R2](https://github.com/lukasmonk/lucaschessR2)** · GPL; **архивированная репа** | Богатый каталог тренировок, силовая шкала и варианты упражнений | Идеи учебного плана, **не считать проект активно поддерживаемым** |
| **[Sirius1616/blunderlog](https://github.com/Sirius1616/blunderlog)** · README заявляет MIT, **LICENSE-файл не найден** | «Mistake-to-Mastery» как концепция повторяемого улучшения | Низкий приоритет, проверить зрелость |
| **[Blunder (engine)](https://github.com/qam4/blunder)** · **нет выбранной явной лицензии** | Специальные engine-side coaching facts для `qam4/chess-coach` | Протокол идей, **код не переносить без разрешения** |

## C — открытые данные и инфраструктура

| Ресурс | Лицензия | Практическая польза |
|---|---|---|
| **[Lichess Chess Openings](https://github.com/lichess-org/chess-openings)** | **CC0** (README, файлы `a.tsv`…`e.tsv`) | ECO, имена дебютов, последовательности PGN/UCI, EPD по позиции. **Сильнейший кандидат для импорта открытых данных:** добавить офлайн справочник без API, сопоставлять позиции, учитывать транспозиции. Проверить структуру перед включением в дистрибутив. |
| **[Lichess Open Database](https://database.lichess.org/)** | CC0 для опубликованных баз (проверить условия конкретной выгрузки) | Исходные задачи/позиции; **не скачивать весь объём**, использовать выборку для личных упражнений. |
| **[Lichess Opening Explorer](https://github.com/lichess-org/lila-openingexplorer)** | AGPL-3.0 | API /masters, /lichess, /player, статистика ходов и персональная дебютная подготовка. Для Chess Coach скорее **использовать публичный API с rate limiting**, а не копировать RocksDB/server. |
| **[CARA licence/third parties](https://github.com/pguntermann/CARA/blob/master/LICENSE)** | GPL-3.0 | Пример проекта с PyQt6/Stockfish/python-chess и лицензированием; отдельно учесть иконки/шрифты/Qt. |

## Рекомендации по адаптации — не делать одновременно со следующим Codex PR

### R1. После пилотного импорта и статистики: «Профиль ошибок»

1. Таблица фактов `mistake_events`: `game_id`, `line_revision`, `ply`, `analysis_run_id`, `motif_version`, `motif`, `evidence`, `confidence`. **Не подменять engine eval тактическим диагнозом.**
2. Двухэтапный движок: быстрый проход всей партии, подробный анализ **только** проблемных позиций, с явным бюджетом CPU и кешем.
3. Категории для начала: hanging piece, missed fork, pin, skewer, back rank, missed mate, failure to respond to threat, opening repertoire deviation; неизвестная категория = `unknown`, не фантазировать.
4. Отчёт не только «N ошибок», но и доли **на 100 оценённых собственных ходов**, coverage, по контролю, фазам, часам, открытию/дебютам и тренду времени. Статистику строить на заполненных `analysis_runs`, а не на всех партиях как будто все анализированы.

### R2. Личные задачи из своих партий (после R1)

- `training_items` связаны с конкретной партией, позицией **до** ошибки и `motif`; `training_attempts` хранят историю ответов, не только последний.
- Допускать **почти равнозначные** решения по версионированной оценке двигателя, а не только один UCI. Всегда после завершённой партии; никогда не показывать engine помощь во время активной человеческой игры.
- Сначала простой Leitner, позднее FSRS с хранением `stability`, `difficulty`, `due_at`, `last_review`, `scheduler_version`; пользователь выбирает тренировку 5/10/15 минут.
- Подсказки по уровням: назвать фигуру → подсветить исходную клетку → показать стрелку лучшего хода. Ошибки после повтора приоритизировать.

### R3. Дебютный репертуар

- Связывать личное дерево позиций/вариантов с `Lichess Chess Openings` CC0.
- Личное отклонение — **только собственный ход** за выбранный цвет, не непредусмотренный ход соперника.
- По статистике партий показывать «чаще всего выходим из подготовленного варианта здесь» и дать 3–5-ходовое упражнение.

### R4. M5: голос и история мыслей

- Сопоставить дизайн Chess Coach `annotation_action` с **CARA** (позиционные объекты, слои, PGN-перенос) и **Chess Recorder** (распознавание специфической лексики, интерфейс исправления транскрипта).
- Отдельные `thought_session`, `annotation_events`, `thought_groups`, `audio_segments`, `transcripts` с `game_id + line_revision + ply + time offset`. Речь пользователя — **не UCI command**, а свободное описание гипотез; не путать эти сценарии.
- Точки реплея мыслей **до фактического хода** и сопоставление со Stockfish после игры — наше потенциальное отличие.

### R5. Внешний вид

- Сравнить `Opening Trainer` (PySide6) и `CARA` (PyQt6): единый shell с навигацией, табличная библиотека на `QAbstractTableModel`, подробный разбор позиции, отдельные графики, сохранённые layout/preferences. Не строить диалоги на 30 кнопок.
- Дизайн вынести из логики UI в централизованные стили и tokens; не переписывать шахматный контроллер ради внешности.


## Дополнение: вторая волна поиска — стратегические объяснения, эндшпиль и desktop

Следующие проекты найдены после первоначального сохранения каталога и проверены по README и доступным лицензиям. Отдельные реализации **не запускались**.

| Проект и статус | Проверенная лицензия | Что особенно полезно для Chess Coach |
|---|---|---|
| **[Kibitz](https://github.com/avienu/kibitz)** | Разделение проекта: **BSD-3-Clause** для core crates и **GPL-3.0** для desktop app (README, отдельные `LICENSE-BSD`/`LICENSE-GPL`) | **Стратегическое обучение через дисбалансы** (слабые поля, пешечная структура, активность фигур), двигатель по требованию вместо оценки каждой тихой позиции, эндшпильные упражнения с **Syzygy tablebases** и разницей «удержал ничью / замедлил выигрыш / потерял результат», личный репертуар и задачи FSRS. Изучить разделение детерминированной шахматной логики и объяснений «человеческим языком». |
| **[Hires Chess Trainer](https://github.com/gaelazzo/python_chess)** | MIT (`LICENSE`), отдельные внешние компоненты требуют проверки | **Windows-first Python desktop**, помощник `Improve from your games`: загрузка Chess.com/Lichess → ошибки → собственные learning bases → интервальные повторения. Есть `Syzygy`, поиск дебютов с наибольшей срочностью изучения и упражнения «с обоих цветов». Важно сравнить именно wizard onboarding и связку «список игр → тренировка», а не переносить весь GUI. |
| **[ChessPrep Lab](https://github.com/zhukaizhen/ChessPrep)** | AGPL-3.0 (`LICENSE`) | Lichess **Study PGN → дерево вариантов → тренировка собственных ходов**, подготовка к конкретному сопернику, endgame study, **Maia-3** для человекоподобных ответов при выходе из дебютной подготовки. Акцент на local-only и offline data. |
| **[CrispChess](https://github.com/CrispStrobe/CrispChess)** | MIT для собственного кода (`LICENSE`), но **разные движки/модели имеют отдельные лицензии** | Показательный **мультидвижковый workbench**, syzygy, вариации PGN, репертуар, задачи и даже разные человекоподобные движки. В будущей архитектуре отделить реестр UCI engines от `analysis_runs`; сохранять `engine_id` и `engine_version`. Никаких автоматических загрузок моделей без подтверждения и аудита. |
| **[mateusmazza/chess-opening-trainer](https://github.com/mateusmazza/chess-opening-trainer)** | MIT (`LICENSE`) | Персональный 5–10-минутный распорядок, методика **диагноз → конкретная привычка → лестница упражнений**, тесты «решённые позиции должны уходить из повторений», баланс новых задач и повторений. Исходники в `build/analyze_games.py`, `build/lessons.py`, `build/build_exercises.py` по README. Интереснее обычной SRS-очереди. |
| **[Chessdriller](https://github.com/gtim/chessdriller)** | **LICENSE в корне не обнаружен**; репозиторий **архивирован**, README говорит open source | Идея хранить и редактировать дебютный репертуар **в Lichess Studies**, тренировать дерево ходов с интервальным повторением. Не брать код без проверки прав, интеграцию со Studies делать только по явной авторизации/PGN import. |

### Что пересмотреть в будущей дорожной карте после этих находок

- **Принцип «лучше понять, чем увидеть +0.75»:** стратегические ошибки требуют описания структуры позиции; Stockfish eval — лишь один из фактов. Изучить Kibitz и Praxis, не создавать догадки LLM без проверяемых оснований.
- **Эндшпильные уроки:** Stockfish не заменяет точные окончания; для ограниченного числа фигур использовать Syzygy WDL/DTZ с честной классификацией и требованием наличия tablebase. Не скачивать большие базы автоматически.
- **Подготовка к сопернику:** из собственного архива и доступных **публичных** игр соперника строить сравнительный дебютный профиль. Ограничивать API rate; не следить за конкретными людьми без цели/разрешения пользователя.
- **UX-путь «пять минут на развитие»:** вместо случайной задачи выдавать фокус дня, несколько похожих позиций из своих ошибок, проверку прогресса и затем финальную точку повторения.
- **Речь и планы на доске:** CARA отвечает за геометрию аннотаций, Chess Recorder — за speech input, Kibitz — за объяснение стратегических факторов. **Связка этих трёх независимых подходов с записью реальных мыслей пользователя до хода — потенциальная особенность Chess Coach.**



## Третья волна — самостоятельная оценка игрока, мотивы ошибок, дебютная статистика и шахматные упражнения

**Проверка 2026-11-01:** найдены дополнительные репозитории; для приоритетных проектов прочитаны README, содержимое LICENSE и отдельные файлы исходников. Программы не запускались. **Все ссылки ниже — кандидаты для изучения, а не разрешение копировать код.**

| Приоритет, проект, лицензия | Конкретная интересная реализация | Что стоит адаптировать |
|---|---|---|
| **A1. [WhyBlunder](https://github.com/huytd/whyblunder)** — **LICENSE в корне и LICENSE.md/txt не найдены; права на перенос кода НЕ установлены** | **[js/self-review.js](https://github.com/huytd/whyblunder/blob/main/js/self-review.js)**: чистый алгоритм **сначала самостоятельный разбор без показанных оценок**, затем сопоставление самооценки с движком. Вопросы игроку: насколько хороша позиция (losing/worse/equal/better/winning), насколько хорош его ход (strong/fine/dubious/bad), какую альтернативу он видит и о чём думал. Выбирает до 8 важных моментов, максимум 3 на фазу, причём **смешивает плохие и сильные ходы**, чтобы сам выбор не выдавал правильный ответ. Сопоставляет ошибки, чрезмерный оптимизм/пессимизм, найденные лучшие ходы. Также [js/chess-evaluator.js](https://github.com/huytd/whyblunder/blob/main/js/chess-evaluator.js): оценки, tactical patterns. | **Сильнейший кандидат на следующую инновационную функцию Chess Coach — «Сначала подумай сам».** После окончания партии выделить 5–8 критических позиций, скрыть Stockfish; записать `self_review_attempts` (оценка/ход/уверенность/комментарий/момент), затем показать проверяемый разбор. **Это ретроспективные мысли после партии, а не наша будущая запись мыслей ДО реального хода.** Сохранять обе сущности отдельно. Не копировать JS без выяснения лицензии. Избегать конвертации `mate` в фиктивный CP при расчёте статистики: в исходниках WhyBlunder такое преобразование есть. |
| **A1. [OpeningScanner](https://github.com/atamano/openingscanner)** — MIT (LICENSE) | [lib/catalog/eco-classify.ts](https://github.com/atamano/openingscanner/blob/master/lib/catalog/eco-classify.ts): проходит до 24 полуходов, вычисляет **EPD из FEN (первые 4 поля)** и находит **самый глубокий** дебютный match из открытого справочника Lichess. Транспозиции объединяются, даже если порядок ходов другой. README также описывает `RepertoireAccumulator`, вложенное дерево ходов игрока и экспорт репертуара в PGN с вариациями. | **Для наших 1000+ партий:** локальная метка ECO/название дебюта по позиции с транспозициями, статистика по своим и чужим продолжениям, первый ход отклонения от репертуара, переход из графика частоты в конкретные партии. Хранить версию справочника. Не делать слепой перенос лимита 24 ply: обосновать/настроить. |
| **A2. [Chess Review MCP](https://github.com/Chess-analysis-mcp/tintins-chess-analysis)** — MIT (LICENSE) | Python/FastAPI/`python-chess`, API/MCP-инструменты для просмотра игровых ошибок и профиля: `get_engine_line`, `goto_mistake`, `get_player_profile`; анализ + доска + диаграмма вероятностей, мотивы ошибок, личные задачи, Glicko-2/Storm и повторение своих зевков. [pyproject.toml](https://github.com/Chess-analysis-mcp/tintins-chess-analysis/blob/main/pyproject.toml) подтверждает python-chess+MCP зависимости. | **Будущий M6:** экспорт Chess Coach через **явные read-only инструменты**: список игр, ошибки, линия оценки, поиск похожих ситуаций, статистика профиля. LLM формулирует ответ на **выданных инструментарем** фактах. Запретить MCP-tool доступ к живым human games/секретным токенам; не позволять ИИ запускать массовую сеть или UCI без разрешения. UI анализировать отдельно от MCP. |
| **A2. [ChessGraph MCP](https://github.com/JunENSAI/ChessGraph-MCP)** — MIT (LICENSE) | PostgreSQL хранит шахматную телеметрию, Neo4j — граф позиций/вариантов, Stockfish — доказуемые варианты, Hermes-Agent — персонализированное объяснение. В README архитектурный принцип: **engine searches → deterministic validates → databases give context → language model explains**; bounded traversals + typed MCP. | **Идея, не стек:** наша SQLite и `game_positions` уже достаточны, не добавлять Neo4j/PostgreSQL из-за красивой архитектуры. В будущем подготовить инструменты `get_position_context`, `get_best_line`, `get_similar_mistakes`, `get_player_patterns`, которые возвращают evidence и provenance. |
| **A2. [ExplainMyBlunder](https://github.com/bing-xbx/ExplainMyBlunder)** — GPL-3.0 (LICENSE) | В браузере без облачного API: различает исходный ход и вариант движка, **две линии рядом**, показывает **конкретную тактическую причину** (не защищённая фигура, пропущенная угроза, связка, вилка, мат), продолжает вариант до относительно спокойной позиции. Есть режим **«Explain moves I try»** — пользователь пробует альтернативы, получает объяснение последствий. | Для одной законченной партии показать **«Мой ход / Вместо него»**, визуальный вариант наказания и возможность самому пробовать ходы. Объяснять только проверенное тактическое событие, не отождествлять «оценка упала» и «это вилка» автоматически. Перед переносом GPL-кода — совместимость и атрибуция. |
| **B1. [Chess Endgame Training](https://github.com/supertorpe/chessendgametraining)** — GPL-3.0 (LICENSE) | Тренажёр позиций с **Syzygy** и Stockfish, цели «сохранить ничью / добиться мата», журнал успешности позиции, «Manual mode» для гипотетических ходов **за обе стороны**, FEN с целевой задачей, PGN/FEN export. README и исходники проекта. | Специальная вкладка «Эндшпили»; **Syzygy WDL/DTZ как факт**, не приравнивать его к оценке Stockfish в CP. Из собственной партии брать начальную позицию подходящего эндшпиля, давать цель, оценивать правильность каждого хода по WDL/DTZ. Не скачивать tablebase без согласия и без оценки диска. |
| **B1. [Woodpecker Chess Trainer](https://github.com/gilbertbrandow/woodpecker)** — MIT (LICENSE) | Создание собственных наборов задач из **тактики, позиционных задач и отвлекающих примеров**, расписание повторных циклов; статистика по точности и времени решения **по циклам**; возможность выбирать тему, рейтинг, лимит времени. | Тренировочный план не только «задача дня», а **циклы на 7/14 дней** с фиксацией скорости, уверенности, успешности и трудности. Отвлекающие примеры полезны, чтобы не подсказывать игроку, что каждый пример обязательно содержит вилку. Отличать такой цикл от алгоритма FSRS. |
| **B2. [Listudy](https://github.com/ArneVogel/listudy)** — AGPL-3.0 (LICENSE) | Открытая обучающая платформа шахмат на Elixir/Phoenix/PostgreSQL. README подтверждает структуру web app и переводов. Конкретные тренажёры требуют отдельного разбора исходников перед заимствованием. | Идеи навигации по учебным сценариям и локализации, но прямого технологического соответствия нашей PySide6/SQLite нет. Ниже приоритет, не отвлекать текущего Codex. |
| **B2. [Mistake-o-tron](https://github.com/tailuge/mistake-o-tron)** — GPL-3.0 (LICENSE) | Небольшой web-клиент, переиспользующий **существующие оценки Lichess Stockfish** и шахматную доску Chessground для изучения ошибок. Источник старше, README скромный. | **Принцип экономии CPU:** сначала импортировать уже существующие Lichess оценки, только затем запускать собственный движок по недостающим позициям и с явного согласия. Мы это уже запланировали/реализовали в части M3/M4; использовать как сравнение, не делать повторно. |
| **C. [TimAstier/chess-repertoire-trainer](https://github.com/TimAstier/chess-repertoire-trainer)** — MIT (LICENSE) | Прототип интерактивного повторения линий из Lichess Study, но README прямо говорит, что Study endpoint ограничен SameOrigin, а часть функций в TODO. | **Не считать готовым решением импорта приватных Study.** Для наших тренировок использовать явно экспортированный PGN или официальный разрешённый API, а не обещать обход SameOrigin/OAuth. |

### Специальная рекомендация после третьей волны: «Self-review → Stockfish → тренировка»

Почему **WhyBlunder** интереснее обычного «ошибка в партии»: пользователь **сначала** высказывает гипотезу и выбирает лучшее продолжение, лишь **после** получает анализ Stockfish. Это можно внедрить **раньше голоса** как текстовую тренировку на собственных завершённых партиях, сохраняя абсолютное отделение от активной игры с человеком.

**Предлагаемая схема (только архитектурный backlog, не внедрять сейчас):**

| Сущность | Ключи/содержание |
|---|---|
| `review_sessions` | `review_id`, `game_id`, `line_revision`, `analysis_run_id`, `created_at`, `status`, `review_mode` |
| `review_prompts` | `(review_id, ply)`, `phase`, `selection_version`, `revealed_engine=false` до ответа |
| `review_answers` | `review_id`, `ply`, `self_eval` (losing…winning), `played_move_rating`, `candidate_uci`, `confidence`, `reasoning_text`, `answered_at` |
| `review_comparisons` | `review_id`, `ply`, `run_id`, `comparison_algorithm_version`, `calibration_error`, `missed_threat`, `best_move_found` |

**Правила:** сначала зафиксировать ответ, потом разрешать `reveal`; после restart ещё не показанные оценки остаются скрытыми; позиции выбираются по уже завершённому engine анализу без раскрытия подсказок; смешивать **сильные и плохие** ходы; отказываться от объяснения при неполном `analysis_run`. Не сравнивать «самооценку после партии» с «настоящими мыслями во время игры», это разные типы доказательств.

Связать в будущем с нашей `game_notes` и затем M5 `thought_sessions`, но **не уничтожать** исходные данные личной самооценки при изменении алгоритма.

### Краткий рейтинг новых находок

1. **WhyBlunder self-review** — очень высокая продуктовая ценность и близость к нашей идее; **только идеи, пока не выяснена лицензия**.
2. **OpeningScanner** — готовая проверенная по исходникам схема ECO/EPD для статистики, удачное дополнение к Lichess CC0 openings.
3. **Chess Review MCP / ChessGraph MCP** — архитектурная основа будущего проверяемого ИИ-тренера, **без переноса тяжёлой инфраструктуры**.
4. **ExplainMyBlunder** — доказательство причин ошибок и визуальное сравнение вариантов.
5. **Chess Endgame Training / Woodpecker** — специализированные упражнения после общего профиля ошибок.


## Важные юридические и проектные ограничения

1. Наш [feshakomissarov-prog/chess](https://github.com/feshakomissarov-prog/chess) **публичный, но на момент проверки в корне нет файла `LICENSE`**. Это не открытая лицензия для свободного распространения чужими людьми: публичное чтение ≠ разрешение копировать/перераспространять. Отдельное решение владельца **до первого публичного дистрибутива**.
2. Наш `pyproject.toml` содержит `python-chess`; официальный `python-chess` GPL-3.0-or-later. Также возможны обязательства от Stockfish GPL, PySide6 LGPL/GPL, `chess-widgets`, SVG-фигур. **Требуется аккуратная проверка совместимости и правил распространения; нельзя обещать MIT для всего Chess Coach только потому, что приложение бесплатно.**
3. MIT / Apache-2.0 / GPL / AGPL / CC0 отличаются; **никаких исходников, UI-ресурсов и моделей не встраивать молча**. Архитектуру и UX можно вдохновлённо переосмыслить, а копирование файла требует корректной лицензии/уведомлений.
4. Практика fair play: никаких Stockfish/LLM подсказок для живых партий человека против человека. Возможности «live coach» сторонних приложений подходят для **офлайн учебного режима**, не для наших реальных Lichess-матчей.
5. Этот каталог **не является заданием Codex на внедрение всех этих функций**. Сначала завершить [следующее большое ТЗ](../specs/M3_M4_REAL_WORLD_PILOT_AND_ANALYTICS_UI.md), протестировать настоящие завершённые партии/Stockfish по разрешению владельца; затем выбрать R1/R2.

## Что исследовано глубже первичной страницы

- **CARA:** [техническая документация annotation system](https://github.com/pguntermann/CARA/blob/master/doc/annotation_system_implementation.md) подтверждает per-ply модель, PGN tags, checksum, сигналы контроллера и модель отображения; [архитектура Qt](https://github.com/pguntermann/CARA/blob/master/doc/architecture_outline.md) — QAbstractTableModel и QThreads для движка.
- **Chess Mistake Coach:** прочитаны реальные исходники SQLite schema, position cache wrapper и coaching category mapping.
- **jcabiles/chess-coach:** проверен файл `ARCHITECTURE.md`, engine lifetime serialization и fake engine tests.
- **Praxis Chess:** изучены README/разделы ARCHITECTURE о проверяемой диагностике и паттернах.
- Для остальных проверены как минимум GitHub repo, README и (если есть) LICENSE. **Реальные бинарники и исполняемые сервисы не запускались.**

**Следующий цикл исследования после третьей волны:** способы честной классификации ошибок (включая матовые линии), SRS/FSRS scheduler versioning, характеристики UI для desktop Qt, переносимые события рисунков, роль Maia при выборе человеческого ответа. Дополнительные проекты добавлять здесь по мере проверки.
