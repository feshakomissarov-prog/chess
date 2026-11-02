# PR #41 — Windows GUI pilot: обязательное UX/functional исправление M4.6

**Дата:** 02.11.2026. **Ручной пилот владельца:** 14 пунктов, 14 скриншотов в чате. **Проверенный до пилота implementation SHA:** d4123ba4e68b37d010602b06174a4b72c87d4b6e, [Windows CI 456 PASS](https://github.com/feshakomissarov-prog/chess/actions/runs/41393886360). **Вердикт: NO-GO на merge PR #41 до исправления и повторного ручного Windows-пилота.** Это приложение к [утверждённому M4.6](../specs/M4_6_DESKTOP_EXPERIENCE.md), не новая дорожная карта.

## 0. Дисциплина и технические факты

Работать **в существующем draft PR #41**, логическими коммитами, без merge и без редактирования независимого draft PR #42 и ROADMAP. Перед кодом перечитать AGENTS.md, M4.6 spec, журнал исполнения, текущую реализацию и Fair Play tests. Данный документ хранится в отдельной docs-ветке, код менять в Codex-ветке PR #41. Скриншоты в чате не републиковать в GitHub (личные аккаунты и данные).

**Независимо подтверждённые причины жалоб:**
- src/chess_coach/window.py: функция _game_date_label и src/chess_coach/ui/dashboard.py: game_card_text показывают сырой started_at; ui/game_review.py: load_game — тоже.
- Главная формирует последние партии как длинные многострочные QPushButton; меню «Ещё» включает повтор навигационных кнопок и backend operations; show_home синхронно запускает _refresh_home_dashboard, что может давать паузу, **нужен замер**.
- ui/game_review.py строит список из одной строки на **ply** и выводит severity на английском; аналогичная проблема в локальной доске.
- _apply_board_size ограничивает рост доски константами inspector_min=320 и высота окна minus 210; выбор «640» не гарантирует размер.
- position_analysis.py вызывает новый chess.engine.SimpleEngine.popen_uci **на каждый позиционный запрос**, фиксирует 2 секунды, затем закрывает процесс. Windows консоль может мигать; весь UCI запуск требует no-console policy.
- ui/sounds.py содержит сгенерированные музыкальные тона, а не привычные короткие натуральные шахматные щелчки. Уточнить отдельно, почему второй game иногда silent.
- ui/game_review.py: check_training_move знает только saved training_proven; иные легальные кандидаты честно помечаются «не удалось проверить», но отдельной проверки движком нет. Подсказка — текст с UCI/ply.
- История задач уже содержит begin_replay/play_replay_move и «Решить снова офлайн», но на показанном экране практически не обнаруживается, вокруг слишком много информации.
- ui/dashboard.py сознательно **не** выдаёт CPL за официальную Lichess accuracy. «Сильные ходы» без надёжного классификатора = н/д. Сохранять честность, а не исправлять н/д вымышленными цифрами.

**Неприкосновенные guards:** игра с живым человеком на Lichess, unknown/network loss/reconnect/429, auth/account/line switch, OAuth revoke → no engine score, PV, arrows, graph, hints, training или batch до нового полного актуального Fair Play preflight. Сохранить проверку account+game+line+generation+service и защиту stale UCI. Изолированный offline mode — отдельно. Никаких реальных OAuth/PAT запросов, пользовательской SQLite/PGN или Lichess-вызовов из Codex/CI.

## 1. Единые данные и подписи на всех страницах

**F1. Даты и время (жалобы 2, 7, 8, 9, 13).** Единый date formatter. Точная ISO 8601 дата-время с offset → локальное время Windows, «02.11.2026 · 14:05». Убрать T, +00:00, секунды и миллисекунды. Если метаданные game_time_precision=date_only или известно лишь число — «02.11.2026», **без фальшивых 00:00 и пометки «точность до дня»**. Непарсируемое → «Дата неизвестна» без падения; оригинальная дата в базе и PGN не изменяется. Применить к карточкам, review, задачам, аналитике, sync.

**F2. Контроль времени (жалоба 3).** Показывать минутно-секундные подписи, при больших значениях часы/дни: raw 300+3 → «5 мин + 3 с»; raw 10800+180 → «3 ч + 3 мин»; 1 day per move → «1 день на ход»; 0+1 → «0 с + 1 с» или ясная эквивалентная подпись. Технический raw value сохранять как itemData для SQL фильтра, не ломать account scope; исключить дубли и сортировать логично. Оговорить, что base и increment могут иметь разные единицы, и не путать их.

**F3. Дебюты на русском (жалобы 2, 9).** ECO-код + детерминированный русский словарь дебютных семейств/известных вариантов, единый компонент для imported Lichess and local opening_matcher. Минимальные семейства: Сицилианская, Каро-Канн, Французская, Алехина, Итальянская партия, Ферзевый гамбит, Английское начало, Грюнфельда, Русская партия, дебют слона, Испанская партия, Голландская, Лондонская система. Нельзя менять ECO или угадывать неверный перевод: если нет точного match, показывать ECO+нейтральный fallback, английский оригинал доступен в details. Никакого auto internet/LLM translate.

**F4. Источник/охват (жалобы 2, 9).** Компактно S = Stockfish, L = официально полученный Lichess analysis, — = нет. Tooltip/accessible label с полным названием и provenance. Показывать «Оценено ходов: 30/35» ТОЛЬКО если обе стороны дроби — сравнимые ходы одной стороны; «Оценено позиций: 70/70» считать отдельно, не путать. Partial coverage отдельная подпись. Не печатать в основных карточках run ID, «cc-move-metrics-v2», «complete», «N=», «latest-complete-preferred», technical source. Подробности — во всплывающей методике.

**F5. Точность и сильные ходы (жалобы 2, 7).** Колонки «Я» и «Соперник» для обоих цветов, Accuracy и классы ходов. Официальная Lichess accuracy, если есть, всегда маркируется именно Lichess; если её нет, показывать — и CTA на вычисление локального анализа, не придумывать %. **Допускается** отдельная метрика «Расчётная точность Chess Coach» только после определения версии алгоритма из сравнимых engine/WDL position data, порогов, покрытий и тестов, с явным отделением от Lichess; CPL не конвертировать произвольной формулой. Оба цвета считать по одному классификатору с source/run и N/N; если у одной стороны нет анализа, там —, а не 0. «Сильные» подтверждать только реальным детектором, иначе —, не именовать best_move_confirmed «сильным» без проверки. Не задерживать всю UI-приёмку ради неподтверждённой accuracy.

**F6. Русификация и доступность.** В основном UI заменить raw «good / blunder / unknown / midgame / complete», UCI и машинные коды понятными терминами; сохранить технические детали через tooltip/«Как рассчитано». Цветовая разметка дополнена значком/tooltip/screen-reader label, не только цветом.

## 2. Главная, выбор режима, дубли (жалобы 2, 3, 4, 10)

**H1. Три последние партии:** вместо сплошных 4 строк текста — компактные разборчивые карточки. Заголовок: результат + соперник; подзаголовок: локальная дата, ECO+русский дебют, нормальный контроль; короткий source S/L/—, «Оценено 30/35 моих ходов». В карточке мини-таблица с колонками **Я | Соперник** и строками **Точность; Неточности; Ошибки; Зевки; Сильные**. Н/д при отсутствии точного источника, показывать coverage по каждой стороне. Tooltip с источником, полными именами метрик. На узком окне адаптивная компактная форма, без горизонтальной обрезки. Нажатие открывает ту же exact game/account/line.

**H2. KPI:** четыре существующих метрики оставить, но читаемые human labels, честные denominator и coverage: 227/156160 не означает точность 0%, «н/д» не ошибка; пояснения и источники — по info tooltip, не огромный технический текст.

**H3. Кнопка sidebar «Играть»:** **не** запускать молча локальную доску. Открыть экран выбора режимов: свободная локальная, против Lichess AI, друг по ссылке, вызов соперника, активные партии/продолжить. Ясно disabled, когда нет подключения. Один клик в sidebar не должен сбрасывать текущую доску/ходы. Кнопки быстрого действия на главной допустимы.

**H4. Убрать «Ещё» как второй sidebar:** сохранить функции, но «Завершённые партии», «Статистика», «Настройки», «Задачи», вызовы друзей и прочие навигационные дубли перенести в **единственные** понятные места. Архивная синхронизация, пробный импорт, batch, диагностические операции — «Мои партии/Аналитика/Настройки → Хранилище и обслуживание». Если «Ещё» осталось — максимум 3 действительно дополнительных действия, без дублирования основной навигации.

**H5. Скорость главной:** show_home должен мгновенно переключать stacked route, а дорогие SQLite query, game_card_metrics/load_game_review выполняются в worker/с кэшем, явно refresh/invalidation и generation guard. GUI не блокировать на 1000–3000 synthetic games. Не делить один SQLite connection между потоками без корректной thread-affinity. Показывать last snapshot/loading skeleton.

## 3. Игровая доска, Stockfish, звуки, лаги (жалобы 5, 6, 11)

**G1. Общая нотация:** новый reusable MoveTable для local/online/saved review: **одна строка на номер полного хода**, минимум «№ | Белые | Чёрные», рядом отдельные компактные post-move score snippets для каждой стороны при наличии данных. 1. e4 e5, 2. Nf3 [пусто] — именно 2 строки, а не 3. Нажатие на конкретный SAN white/black выбирает **правильный ply**, синхронно доску/график/score. Тесты Black-to-move FEN, castling, promotion, mate, incomplete turns, pre-/post-positions, export PGN. Список прокручивается внутри своей панели, без переноса каждой записи на отдельную строку.

**G2. Движковая оценка:** показывать **у notation/рядом с доской**, а не выше всех нотаций в случайном месте; score +0.7/−1.2/M3 с фиксированной и подписанной перспективой «для белых» или переключаемой «для меня», explicit S/source/depth when details expanded. Различать score **до** хода, **после** хода и delta для стороны игрока. Если analysis отсутствует — не показывать 0.0. Best PV как SAN и arrow только из правильной legal pre-move позиции. Fair Play gate обязателен даже для cached данных.

**G3. Никаких всплывающих CMD-консолей:** аудит **каждого** Stockfish launch: position_analysis.py, lichess/analysis.py, lichess/batch_analysis.py, engine_manager.py, Settings engine probe и других реальных launch sites. На Windows использовать no-console startup configuration для subprocess/QProcess/asyncio child process, сохранить stdout/stderr для diagnostics, cancellation и crash detection. Не пытаться «встроить Stockfish» как UI thread — отдельный **невидимый UCI subprocess** является правильной архитектурой. Внешние terminal windows недопустимы при одиночном ходе, партии, batch 10 игр и тесте engine.

**G4. Нормальный анализ после хода:** текущий position_analysis.py создаёт новый UCI process и ждёт Limit(time=2s). Заменить для разрешённой offline/local analysis session на **переиспользуемый долгоживущий engine worker**, не создающий процесс на каждый ply; по ходу асинхронно отменять/заменять предыдущий job и публиковать early depth updates в Qt, вместо ожидания конца двух секунд. UI отзывчив, progress visible, ни одна оценка от старой позиции не попадает на новую. Для свободной локальной доски ранее включённый анализ обновлять **автоматически** после каждого подтверждённого хода; не сбрасывать ON при новой локальной партии. Если Stockfish ещё не установлен/проверен — CTA «Указать Stockfish», не фальшивая оценка. После logout/network loss/account/line switch/gameStart revoked immediately.

**G5. Batch 10 games:** внутренний progress pane с «3/10», безопасной pause/cancel/resume, status/failed reason/coverage, без внешнего CMD и GUI freeze, с верным Fair Play preflight. Worker bounded, sequential safety decisions, sync of actual progress, no duplicate run data/corrupt PGN, safe on stop/restart. Нельзя автоматически скачивать/запускать приватные Lichess запросы.

**G6. Board-size:** заменить hard-coded cap на size presets «Компактная», «Стандартная», «Большая», «Очень большая», «На всю доступную область», перерасчёт размеров с учётом реального layout и DPI, сохранение настройки. В **focus mode** раскрыть доску под максимум экрана, адаптивно скрывая inspector; обычный режим сохраняет controls. На 1920×1080 должно быть достижимо ~800+ logical px при достаточном доступном месте; без обрезки доски. На 1024×700 нельзя требовать гигантскую доску и все панели одновременно — сделать разумную адаптивность.

**G7. Звуки:** текущие музыкальные ноты заменить на короткие узнаваемые **натуральные шахматные щелчки**; move/capture/check/castling/promotion/game_end в едином стиле, мягкая громкость, кнопка Preview в settings, обычный пользовательский volume. Отдельно исправить **после второй новой партии звук перестаёт играть**: QSoundEffect lifecycle, loaded/status, mute/volume reconfigure, path/assets, SoundEventDeduplicator, keys game_id+ply. Не пропускать при новом game_id и не дублировать при optimistic/ack/stream replay. Реальное прослушивание на Windows обязательно. **Лицензия:** оригинальные sounds либо явно совместимый licensed pack с NOTICE; **не копировать Lichess default** — [официальный COPYING Lichess](https://github.com/lichess-org/lila/blob/master/COPYING.md) различает свободные и несвободные samples. Сходство по UX, не нелегальное копирование файлов.

**G8. Perf:** снять измерения Qt event-loop / GUI switching и SQLite/callbacks на seeded temporary 1000–3000 games, отдельно cold/warm. Главная при возврате из local board должна появляться сразу (ориентир first paint 100–200ms на нормальном Windows после прогрева), сама генерация данных в фоне с «Обновляем…». Никаких synchronous run/engine/blocking DB on GUI thread; stale data guarded. Не ставить непереносимые hard time thresholds в GitHub CI, публиковать результаты.

## 4. Единый разбор, график, ошибки (жалобы 7, 8, 12)

**R1. Компоновка:** при **1360×860 и 1920×1080 logical px** одновременно видимы **доска слева, нотация + компактная двухсторонняя статистика справа, график снизу**; без общего обязательного вертикального скролла. Убрать дублирующий большой title «Просмотр сохраненной партии», длинные technical subtitles. Один компактный game header; controls доступны. При 1024×700 и DPI150–200% адаптировать layout, внутренний scroll допустим и обязателен только при физически малой области; ничего не обрезать. Никаких resize jumps при выборе ply.

**R2. Severity на SAN, не словом:** good — зелёный, inaccuracy — жёлтый, mistake — оранжевый, blunder — красный, подтверждённый strong — синий, unknown — серый. Добавить marker/tooltip/a11y; неизвестные оценки **не** красить зелёным. Конкретный выбранный ход и actual source видны.

**R3. Главная информация рядом с board:** выбранный SAN, «до +1.2», «после −0.8», «изменение −2.0 для белых» (signed perspective mathematically verified), S или L и coverage; best move SAN и **стрелка правильного legal хода** from pre-move FEN, arrow played move другим стилем по желанию. Не заставлять переключать tabs «Ходы → Разбор», чтобы увидеть score. При missing partial score честное N/D; mate-aware, no interpolation as true data; offline/online safety remains. Смена selected ply синхронизирует graph, move list, arrows, board and inspectors.

**R4. Graph:** эстетическая новая отрисовка ~160–220dp с grid and numerical axis values +2 / 0 / −2 **для белых**, по hover/click tooltip «ход, оценка до, после, delta, источник», увеличенная hit-area, видимые severity points и selected marker; смена ply работает. Missing score ⇒ графический разрыв, а не нарисованная линия через отсутствующие данные; mate separate marker; clips labelled. Не рисовать выдуманную точку в конце партии. Доступный keyboard selection.

**R5. Table «Я / Соперник»** возле ходов: точность источника (Lichess/local version) + count good/inaccuracy/mistake/blunder/verified strong, coverage per side. Source details только в tooltip/info, не сотня строк технического текста. Вся игра может быть белыми/чёрными.

**R6. Разбор после партии против AI:** в saved game явная CTA «Проанализировать Stockfish» (если нет persisted complete analysis; если есть — «Повторить анализ» по запросу). Использовать существующий post-game analysis pipeline с progress/cancel and preflight, по завершении без перезапуска UI обновить score/graph/metrics в *той же* game revision. Для Lichess: «Показать имеющийся официальный анализ» только если он импортирован; если official Lichess analysis отсутствует, честно так и сообщить, можно предложить «Открыть на Lichess» или documented refresh metadata. **Не обещать неизвестный официальный API, запускающий анализ Lichess на сервере.**

**R7. Ошибка-упражнение:** пользователь видит pre-error position, делает legal alternative, нажимает «Проверить». Подтверждённая сохранённая альтернатива → сравнительный ответ; иные легальные ходы → при safety allowed bounded on-demand Stockfish comparison (сопоставимый POV/budget/analysis provenance), иначе «Не удалось достоверно проверить» без присвоения wrong. Улучшенный/неулучшенный только по evidence; без смешения разных engine budgets как абсолютной истины. Без предварительной утечки best score/arrow, с отменой на human live/429/logout/generation change. PGN не изменять. **Подсказка рисует стрелку от/куда на доске**, последовательная подсказка по запросу, clear on change/quit. Текст — только для accessibility, не сырой UCI/ply как единственный hint. Убрать лишнюю прокрутку и громоздкий title.

## 5. Аналитика и задачи (жалобы 9, 13)

**A1. Обзор:** сделать читаемый смысловой экран, а не dump SQL provenance. Один общий выбранный account, period, clock, side применяется ко всем вкладкам; фильтр visible/persistent, не теряется на switch. 3–5 понятных метрик (W/D/L, покрытие, свои ошибки, что требует внимания, динамика при достаточных данных), 1 график; «Как считается» collapsible; no «avg CPL / source cc-*» в главном поле. При пустой выборке объяснить scope и CTA сменить, не ложное «не подключено».

**A2. Партии:** compact cards: нормальная дата, русский дебют, result, 2-col sides, краткий S/L/—, N/N; англ/технические «accuracy N», «источник...» через tooltip. По клику правильная партия; сохранение фильтра и pagination. Иконки S/L с accessible explanation.

**A3. Дебюты и этапы игры:** объяснить 1 фразой смысл «в каких дебютах/стадиях чаще ошибаетесь». Показать сортируемую таблицу ECO/русское название, партий, цвет, ошибок/100 **оценённых** ходов, coverage + секции opening/middlegame/endgame. Операцию «Сопоставить 25 партий» назвать «Обновить локальную классификацию 25 партий», показывать progress/changed count, без неявной сети; drilldown кликом в конкретные партии и reset. Убрать двойную стену текстовых строк и «latest-complete» в видимом UI.

**A4. Критические моменты:** исправить пропавший account filter. Пользователь уже выбрал account в «Обзор»; вкладка должна использовать те же значения либо ясно требовать конкретный аккаунт **только если он фактически all**. Если критических моментов нет, показать «Пока мало проанализированных партий» и CTA «Проанализировать», а не «не подключены». На реальных данных показывать evidence-backed повторяющиеся тактические/стратегические эпизоды, count/coverage/confidence и открыть exact position, не диагностировать мотивы игрока только из PGN. Empty/error/loading distinct.

**P1. Задачи:** сделать на видимой вкладке отдельный основной CTA **«Решить повторно»**, большой удобный puzzle board, короткие дата/рейтинг/русские themes/результат, последовательность «Выбрать → Решить → Проверить → При необходимости подсказка → Показать решение → Следующая». Из локально сохранённых attempts (скрин показывает 1511), исходную историю не мутировать. Реиспользовать существующий offline puzzle replay, который уже есть в window.py. Переключатель «Практика / История», компактный pagination/search, нормальные подписи; статистика 1–2 строки, а не стена из времён и англ тегов. Скрывать solution до запроса/завершения; при incomplete FEN/moves — причина недоступности конкретной задачи, не crash. **Новые незнакомые задачи по сетевому API не входят** в это исправление без отдельного согласования.

## 6. Матрица соответствия замечаниям владельца

| № | Приоритет | Приёмочный результат |
|---|---|---|
| 1 | уже PASS | Windows shortcut/launcher запускает Chess Coach, не сломать |
| 2 | P1 | Recent games date, русский opening, compact source+N/N, таблица собственные/соперник, честные числа |
| 3 | P1 | Human clock format, сортировка/сохранение raw фильтра |
| 4 | P1 | Нет дублирующих «Ещё», доступны сервисные функции в одном месте |
| 5 | P0/P1 | SAN две полуходовые ячейки на полный ход, score near moves, no CMD, reusable engine, large board, sounds persist |
| 6 | P0 | Мгновенный route switch, тяжёлые DB/analyze в фоне, без freeze |
| 7 | P1 | Доска + таблица нотаций + graph одновременно в desktop; цветные SAN; before/after score + best arrow |
| 8 | P1 | Упражнение с ходом, evidence-backed проверкой и стрелкой hint, без общего лишнего scroll |
| 9 | P1 | Analytics 4 вкладки human-friendly, общий account/scope, meaningful empty |
| 10 | P1 | «Играть» открывает выбор режима вместо local-board reset |
| 11 | P0 | Batch Stockfish внутри UI, progress/cancel, no black console windows |
| 12 | P1 | В completed AI game можно явно запросить локальный Stockfish анализ; официальный Lichess только если доступен |
| 13 | P1 | Задачи можно полноценно решать повторно на board, данные на русском и компактно |
| 14 | handoff | Дополнительные баги владельца записывать отдельно, не объявлять GUI fully validated по одному CI |

## 7. Тесты, evidence, исполнительские checkpoints

**Test M1:** seeded temp SQLite (не личная), owner white/black, 1/3 ply, start black FEN, analysis missing/partial/full/mate, date_only/timestamp+zone, clock 0+1/300+3/10800+180/daily, ECO known/unknown, official vs locally-derived accuracy, source and count. Accurate SAN click mapping and round-trip export. Index row colours and a11y.

**Test M2:** UI nav switches without DB block; filter account+clock across tabs/restore; missing-data empty vs auth-required; Qt offscreen layout screens 1024×700/1360×860/1920×1080 logical; DPI 100/125/150/200 (physical requires owner), focus board, graph exact ply, arrows visible only safe.

**Test M3:** test every Windows UCI launch path with no-console flags and fake processes; multiple local moves use same engine session and discard stale results, batch cancel / engine error / no orphan child. No external login or user's private SQLite. Offscreen tests cannot **prove** physical black console absent — owner must confirm. Performance before/after warm/cold seeded 1000–3000 games; numeric evidence, no flaky hard thresholds in CI.

**Test M4:** sound new game #1/#2, volume/mute persistence, QSoundEffect loaded/errors, duplicate replay, user click sound, original/licensed asset NOTICE and audible manual test. Puzzle saved retry does not corrupt historical success; user input correct/incorrect, solution gating and navigation.

**Test M5 safety:** all old P0 Fair Play tests + new guard for local analyzer/batch/review arrow/graph/hint/any on-demand candidate check; gameStart, logout, 429, reconnect, revoked OAuth, selected account/line, stale async callback. Any failure must fail closed, including source labels hidden if they reveal evaluations.

**Checkpoint A (stability):** engine invisible, persistent, responsive, audio new game. **B (shared foundations):** date/clock/openings, two-sided evidence data, fullmove SAN component. **C (primary UX):** home, play selector, review/graph/training, postgame analysis CTA. **D (supporting UX):** analytics and puzzles; remove duplicate nav. **E:** all regressions and exact-head Windows CI, compileall, git diff --check, manual Windows GUI checklist prepared. Log progress after each checkpoint, don't hide deferred or unverified scenarios.

**Reporting to owner:** full changed file list, commits by checkpoint, test counts, Windows CI direct URL **for exact final SHA**, what is/not available due incomplete accuracy/classifier, saved-game/PKG/PGN unchanged, UI screens for review using synthetic data, list of needed human checks: native click drag, DPI, audible sound, launcher and no CMD flashes, true Lichess connection. PR remains **draft, NO MERGE** until owner accepts physical GUI.

## 8. Решения для потенциальных неоднозначностей

- Точность обоих игроков обязательна как **честная data field**, но нельзя выдумывать проценты при отсутствии official accuracy. Допустить отдельный verifiable Chess Coach accuracy только при утверждённом versioned алгоритме. Место «—» сопровождается reason/CTA.
- Если под «Запросить анализ Lichess» подразумевается запуск официального серверного анализа, **не реализовывать недокументированный endpoint**. Реальная кнопка — Stockfish локально; Lichess показывать только полученный отчет и ссылку на игру.
- «Доска во весь монитор» реализовать через focus/full-available-area mode, скрывая инспектор; в обычном review сохранить три панели, насколько разрешение позволяет.
- Звук «как на Lichess» трактовать как приятный узнаваемый chess click UX, а не перенос коммерческого/несвободного WAV.
- На этом этапе задачи из уже сохранённого local puzzle history — полноценная интерактивная практика; online discovery новых задач отдельно.
- **Визуальный критерий не считается пройденным по тестам:** итоговый физический Windows GUI pilot снова проводит владелец.

**GO** только после (a) не мерцают cmd при ходе или batch, (b) нет явных UI freeze, (c) работает звук на второй игре, (d) нотация fullmove-correct, (e) score/pre-post/arrow and Fair Play correct, (f) review на 1360×860 читаем, (g) главная/аналитика/задачи интуитивны, (h) exact-head Windows CI и ручное подтверждение. До этого — **не merge PR #41**.
