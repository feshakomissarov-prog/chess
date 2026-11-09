# Chess Coach · PR #46 — два дефекта ручной проверки: лишнее окно выбора цвета и медленная первичная загрузка данных

**Дата:** 09.11.2026.  
**Источник:** первое ручное визуальное тестирование владельца на Windows, четыре скриншота: Home, панель задач с двумя окнами Chess Coach, Play (Lichess AI), Analysis landing.  
**Статус:** **P1 visual-smoke + P1 startup responsiveness**: подтверждённый orphan Qt widget и воспроизводимая владельцем задержка около 30 секунд в отображении данных; конкретная доля задержки по SQL/сети подлежит инструментальному измерению, не объявлять гипотезу доказанной. Обе правки — одна узкая работа в **текущем Draft [PR #46](https://github.com/feshakomissarov-prog/chess/pull/46)**.  
**Проверенный кодовый SHA:** \`024128de162895b3a547fcbf56a8be5312500234\`. Получить свежий HEAD перед правкой.  
**Ветка для изменения:** \`codex/unified-desktop-ux-2026-11-08\`. Не создавать новый PR и не делать merge в \`main\`.  
**Контракт:** [основное мастер-ТЗ](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md), Play UX-01/02/03 и критерии §20/§26.  
**Важно:** исправлять **реальный код** PlayPage и цепочку начального чтения Home/архива/Прогресса. Не генерировать PNG, не менять утверждённые 13 экранов и не перестраивать навигацию.

## 1. Наблюдение владельца и подтверждённая причина

При запуске Chess Coach на экране **«Главная»** поверх основного приложения появляется **небольшое отдельное стандартное Windows-окно с собственными кнопками минимизации/максимизации/закрытия и combo «Случайный цвет»**. На панели задач видны **два окна Chess Coach**. Маленькое окно не является согласованным режимом игры, не должно существовать. На экране «Играть» в основном окне уже есть правильные radio controls «Белыми / Чёрными / Случайно»; дублировать их внешним ComboBox нельзя.

**Root cause прямо в исходнике:**

- [\`src/chess_coach/ui/pages/play.py:115–119\`](https://github.com/feshakomissarov-prog/chess/blob/024128de162895b3a547fcbf56a8be5312500234/src/chess_coach/ui/pages/play.py#L115-L119): \`self.color = QComboBox()\` **без родителя**, и \`self.color.hide()\`. Этот QComboBox служит **скрытым legacy state model** для выбранного цвета; он **НЕ включён в layout**, поскольку уже есть современные \`self.color_control\` radio buttons.
- [\`src/chess_coach/ui/pages/play.py:469–473\`](https://github.com/feshakomissarov-prog/chess/blob/024128de162895b3a547fcbf56a8be5312500234/src/chess_coach/ui/pages/play.py#L469-L473): \`_update_form()\` вызывает \`self.color.setVisible(online)\`. Для online mode \`online=True\`. Поскольку QComboBox **не имеет parent**, Qt показывает его как **самостоятельный top-level window**. Page может находиться в скрытом \`QStackedWidget\`, но parentless popup показывается самостоятельно даже на Home — это точно совпадает с пользовательским screenshot.
- Аналогичная скрытая \`self.mode_selector = QComboBox()\` в \`play.py:106\` тоже не имеет родителя (в коде она скрыта, но потенциально остаётся orphan top-level \`QWidget\`). Её следует безопасно parent к PlayPage, не менять state API и не делать отдельным visible control.
- [\`play.py:432–451\`](https://github.com/feshakomissarov-prog/chess/blob/024128de162895b3a547fcbf56a8be5312500234/src/chess_coach/ui/pages/play.py#L432-L451): \`_restore_mode_preferences()\` устанавливает \`self.color.setCurrentIndex(...)\` внутри \`QSignalBlocker(self.color)\`, поэтому [\`_sync_color_buttons()\`](https://github.com/feshakomissarov-prog/chess/blob/024128de162895b3a547fcbf56a8be5312500234/src/chess_coach/ui/pages/play.py#L374-L379) может НЕ сработать и отображаемые радиокнопки цвета остаются **без выбранного значения**. На пользовательском Play screenshot все три круга выглядят пустыми. Это близкий визуальный дефект, который можно исправить в том же месте.

**Побочный признак в скриншотах:** над приложением в правом верхнем углу частично отображается «кадров/с N/A / ГП / ЦП / LAT N/A» — по виду это может быть **внешний GPU-performance overlay**, а не элемент Chess Coach. Его **НЕ надо** автоматически приписывать приложению или убирать из Qt, пока не доказано обратное. Для чистого визуального сравнения пользователь может отключить системный overlay самостоятельно. Это **не** часть P1-исправления лишнего окна.

## 2. Узкое обязательное исправление

1. Для **state-only** QComboBox \`self.color\` и \`self.mode_selector\` установить родителя, например \`QComboBox(self)\`, чтобы Qt управлял их lifetime внутри PlayPage. Оба виджета должны остаться **скрытыми**: \`self.color\` — только хранилище \`currentData()/findData()/currentIndexChanged\`, \`self.mode_selector\` — программная совместимость/выбор режима. **Не добавлять их в layout** и не создавать вторую видимую форму.
2. **Удалить либо заменить на постоянное скрытие** строку \`self.color.setVisible(online)\` в \`PlayPage._update_form\`. Показывать/скрывать **только \`self.color_control\`** с тремя радио-кнопками, которые уже присутствуют в layout. При смене ai/friend/challenge/local \`self.color\` как hidden model **всегда** \`not isVisible()\` и \`not isWindow()\`.
3. После чтения QSettings в \`_restore_mode_preferences\` **явно синхронизировать** видимые radio controls выбранного цвета с \`self.color.currentData()\` **после** установки значения, даже когда сигналы модели блокировались через \`QSignalBlocker\`. Допускается вызвать \`_sync_color_buttons()\` после \`del blockers\` либо безопасно эквивалентным способом. Инициализация PlayPage до первого показа также обязана выставить **ровно одну** radio checked. Не создавать рекурсивные setCurrentIndex/save loops.
4. Сохранить двустороннюю связь: пользователь нажал «Чёрными» → hidden combo получает \`black\` → backend create request использует \`black\`, per-mode QSettings сохраняют его; переключение режимов и рестарт восстанавливают правильную видимую radio-кнопку. Убедиться, что выставление цвета при \`Вызвать снова\` тоже синхронизирует radio.
5. Не изменять existing Play mode cards, layout 2×2, one form, API/серверные вызовы, другие settings, Store/SQLite, engine, Fair Play и CSS-тему.

**Не нужно переписывать архитектуру на отдельную новую state-model или убирать \`self.color\`:** это сломает обратную совместимость многочисленных \`window.py\` и тестов, которые обращаются к combo через \`findData\`, \`setCurrentIndex\`, \`currentData\`. Узкая правка parent+always-hidden+sync достаточна. Проверить аналогичные orphan hidden widgets в PlayPage, **но не расширять** scope на всё приложение без отдельного доказательства.

## 3. Обязательная регрессия: не только тесты цвета, но и отсутствие второго окна

Добавить tests, предпочтительно в \`tests/test_pr46_review_fixes.py\` либо Play UI tests:

- **Start on Home:** создать \`MainWindow\` в изолированном offscreen/Windows Qt, выполнить \`show()\` и обработать event loop; \`play_page.color.parent() is not None\`, \`not play_page.color.isWindow()\`, \`not play_page.color.isVisible()\`. То же для \`mode_selector\`.
- Получить \`QApplication.topLevelWidgets()\` после GUI launch; среди **видимых** top-level widgets приложения **не должно быть отдельного \`QComboBox\` с «Случайный цвет»**. В ситуации без специально открытых других диалогов ожидается только основной MainWindow. Не включать сам Qt popup, когда пользователь намеренно раскрыл combo часов/уровня.
- **Play mode transitions**: ai → friend → challenge → local → ai; \`self.color\` остаётся спрятанным и parented, видна только утверждённая radio group в online modes, локальный не содержит сетевых параметров; никакого второго Windows taskbar window.
- **Initial QSettings:** сохранённые ai color random/white/black восстанавливают **ровно одну checked radio**. Проверить режим friend/challenge, смену, перезапуск. «Радио checked» совпадает с \`self.color.currentData()\`.
- **Input:** выбрать «Чёрными» → currentData()=black → заполнить Create → fake Lichess request содержит black; не выполняет случайно второй POST.
- **Rematch prefill:** ранее подтверждённый цвет human/AI отображается radio checked, не только в скрытой модели.
- **Close:** после закрытия MainWindow/PlayPage не остаётся visible orphan \`QComboBox\`, топ-левел окна Chess Coach и второй значок на панели задач.

Проводить Qt тесты изолированно, не сравнивать \`topLevelWidgets()\` с чужими окнами, оставленными другими test fixtures. Нужен реальный manual Windows smoke: обычный запуск приложения, Home, Play, Analysis, закрытие, проверка панели задач/Alt+Tab. Screenshot до/после самого бага необязателен, достаточно нового чистого Windows screenshot Home и Play + результатов тестов.

## 4. Критерий приёмки и сдача Codex

- [ ] При запуске на Home **нет** плавающего окна «Случайный цвет» и второй иконки Chess Coach на панели задач.
- [ ] Все пять sidebar routes / Play cards / mode transitions работают.
- [ ] В Play radio «Белыми/Чёрными/Случайно» визуально выбран **ровно один** согласно сохранённому значению.
- [ ] Backend и QSettings по-прежнему получают правильный цвет; AI/human rematch работает.
- [ ] Focus/Review/PGN/Archive/Fair Play не изменены без необходимости.
- [ ] Новые focused tests, полный pytest, \`compileall\`, \`git diff --check\`, Windows CI \`test\` и \`visual-evidence\` прошли **на новом точном SHA**.
- [ ] Исполнитель запускает **реальный GUI Windows** (если доступен) и явно проверяет **отсутствие второго окна** на панели задач/в Alt+Tab. Offscreen CI один не является доказательством Windows taskbar поведения — честно отметить недоступную проверку.
- [ ] Обновлён \`docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md\` (новый SHA, root cause, патч, тесты, GUI evidence или честное limitation), Draft PR #46 не смержен.

**Общий вердикт:** визуальная структура близка к утверждённой; убрать лишнее окно и исправить первичную выдачу локальных данных, **не переделывая весь UX**. После этих двух правок продолжить сравнительную приёмку UX-01…UX-13.


---

## 5. ВТОРОЙ БЛОК: локальные партии и статистика появляются только через ~30 секунд после запуска [P1]

### 5.1. Наблюдение и технический разбор

Владелец наблюдает: при обычном запуске Chess Coach **локальные партии и статистика на «Главной» и в других разделах появляются примерно через полминуты**, хотя архив уже сохранён в SQLite. Требование: **не ждать сетевой синхронизации Lichess, тяжёлой аналитики или подтверждения Fair Play для показа безопасных локальных метаданных**.

На проверенном SHA `024128de…` обнаружен **конкретный дефект согласования scope и preview**:

1. `src/chess_coach/ui/pages/home.py` — `HomePage.__init__` восстанавливает `_preferred_account` из `workspace/selected_account`, но скрытый `dashboard_account` сначала содержит только «Все локальные партии» и фактически имеет `currentData() is None`.
2. `src/chess_coach/window.py::_refresh_home_dashboard` запускает **оба** потока: быстрый `dashboard_preview_snapshot` и тяжёлый `dashboard_snapshot`. При пустом `currentData()` применяется fallback по имени пользователя из `workspace/selected_account` **только если аккаунт уже присутствует в combo**; на первом запросе список ещё пуст.
3. `window.py::_dashboard_preview_result_ready` после появления `_selector_options` сначала вызывает `_home_view.set_selector_options()`, которое выбирает запомненный аккаунт, а потом сравнивает `dashboard_account.currentData()` с `snapshot["filter"]["account_id"]`. Для первого preview scope `None` уже не совпадает с выбранным личным аккаунтом → preview отбрасывается. При этом `_launch_pending_dashboard_preview()` не гарантирует запуск **нового scope**, поскольку `_dashboard_preview_pending` может быть `None`; UI остаётся в состоянии загрузки.
4. Полный `dashboard_snapshot` дополнительно выполняет тяжёлую агрегацию точности/охвата, `StatisticsService.summary()`, и `game_card_metrics()` для трёх партий. Это не обязательно быстро при тысячах партий/позиций. `_dashboard_thread` один на всё; повторные запросы откладываются `DashboardQueryCoordinator.defer`.
5. Подтверждение подключения Lichess вызывает `_lichess_authenticated` и повторные refresh, затем `_archive_account_ready_result` меняет account scope; отдельно `_request_dashboard_display_safety` требует актуальной сетевой проверки, прежде чем **показывать engine-derived accuracy/PV/errors**. Это правильно с точки зрения Fair Play, но нельзя связывать с показом **нейтральных** локальных фактов (количество, даты, названия, последние партии).
6. В `application/dashboard.py::dashboard_preview_snapshot` «быстрый» SQL делает `COUNT(DISTINCT ...)` и `ORDER BY` выражение по `last_move_at`/`started_at`; выражение может не использовать обычный индекс. Там же запрашиваются puzzle-history/trend. На реальной большой БД **это гипотеза дополнительной стоимости**, не измеренный факт — требуется замер и `EXPLAIN QUERY PLAN`.

**Не приписывать все 30 секунд одному фактору заранее:** отличить (A) инициализацию/выбор аккаунта и отбрасывание preview, (B) медленный SQL или SQLite lock, (C) конкурирующие background writes, (D) авторизацию и проверку безопасности. Один зелёный CI / offscreen Qt не подтверждает реальную скорость на большой БД.

### 5.2. Обязательная реализация: последовательная выдача реальных данных

**Порядок событий после старта приложения:**

1. **First paint:** показать shell, выбранный по сохранённому setting аккаунт и плейсхолдеры только там, где данные действительно ещё не прочитаны. Не ждать Lichess.
2. **Fast local read:** прочитать из SQLite только **committed** и **account-scoped** количество завершённых партий и последние 3 записи (без PGN parsing, load_game_review, Stockfish, full summary, сети). Отрисовать их **сразу после готовности**, даже если тяжёлая сводка ещё выполняется. Не показывать данные одного аккаунта под именем другого.
3. **Background enrich:** затем отдельно дополнить точность, оценённые ошибки, диаграммы, puzzle-trend и более тяжёлые метрики по фактическим данным; не обнулять/перерисовывать уже показанные последние партии всякий раз, когда меняется только safety state. Избегать лишних полного `dashboard_snapshot` при частых callback от Lichess.
4. **Safety:** при активной игре против человека, отсутствующем подтверждении или неизвестном online state **НЕ раскрывать** сохранённые движковые оценки, accuracy/PV, event severity, графики оценки, классификацию ошибок ни в GUI, ни в tooltip, ни в accessibility text. Но имя/дата/количество/исход и **другие действительно нейтральные факты** могут показываться локально без сети (если соответствуют действующему account). **Не менять/ad-hoc ослаблять Fair Play gate.**
5. **Other screens:** «Анализ → Разбор партии → Мои партии» и «Прогресс» должны быстро показывать сохранённые базовые данные и контролы без ожидания Lichess; тяжёлую аналитику можно заполнять позднее в тех же существующих страницах, не изменяя маршруты/контракты.

**Устранить конкретный дефект preview scope:**

- Восстановить `workspace/selected_account` до первого `_refresh_home_dashboard`; получить точный `account_id` безопасно, без синхронного тяжёлого запроса на GUI thread, или отдельно инициировать новый preview сразу после загрузки account options.
- Переход `None` → `lichess:username` должен запускать **новую актуальную preview generation** и показывать её, **не ждать** полного предыдущего `dashboard_snapshot`. Не принимать старый `None`-snapshot за новый аккаунт; не переписывать фильтры пользователя.
- Учитывать `_preferred_account`, `dashboard_account.currentData()`, `dashboard_queries.is_current(generation)`, `_dashboard_preview_operation_id` и `_dashboard_preview_pending`. Сохранить защиту stale result на account switch / shutdown.
- Разнести lifetime **preview** и **aggregate**: тяжёлый одинарный `dashboard_thread` не должен блокировать новый короткий preview; не допускать бесконечной отмены preview при последовательных auth callbacks. Результаты старого аккаунта **не показывать даже на кадр**.
- Сохранять старые подтверждённые цифры в рамках **того же аккаунта** во время refresh, либо показывать короткий точный status — не делать пустую страницу на каждом повторном запросе.

**Профилирование и ограниченное ускорение SQL:**

- Добавить безопасную диагностику **длительности** этапов (`monotonic/perf_counter`): bootstrap UI, initial account selection, preview submitted/SQL/result rendered, aggregate submitted/SQL/result rendered, account restoration, Fair Play preflight. Логировать только duration ms, category, counts и технические статусы, **не PGN, никнеймы, токены, содержимое БД/SQL params**. Логи не должны быть spam/дорогой операцией.
- Измерить на копии БД аналогичного размера и на тестовом fixture 3–10 тыс. партий, в том числе при параллельной записи. Проверить `EXPLAIN QUERY PLAN` для `COUNT` и `ORDER BY` preview. Если сортировка по вычисляемому выражению устраняет использование индекса, подобрать безопасный индекс/материализованный ключ или небольшой indexed read path **с сохранением прежнего детерминированного порядка партий**; никакой destructive migration/перезаписи user data и без самостоятельного повышения SQLite schema_version ради гипотезы.
- Проверить, что `PRAGMA busy_timeout`, `SQLiteUnitOfWork`, writer queue, threading и preview cancel state не приводят к сериализации за сетевыми задачами/многосекундному ожиданию. Сеть/Stockfish не должны запускаться ради домашней сводки.
- Не создавать ложный «instant cache» из устаревших/чужих данных. Если нужен persistent summary cache — только с проверкой `account_id + data revision`, атомарным обновлением, корректным invalidate после новых партий/анализа; лучше **сначала** исправить race и быстрый запрос без нового кэша.

### 5.3. Проверяемые критерии быстродействия

**Целевой UX, а не обещание времени без бенчмарков:** при повторном обычном запуске на рабочем Windows desktop с уже сохранённой локальной БД и выбранным аккаунтом **количество и три последние партии должны отображаться ориентировочно за ≤1 секунду после появления основного окна**. Для cold start/большого архива собрать конкретные p50/p95 и обоснованно описать отклонения; никакой фиктивной instant-подстановки значений. Тяжёлые оценки могут догружаться отдельно, но **не должны задерживать базовые данные на ~30 секунд**.

В отчёте показать **измерение «до / после»** именно по реальным локальным данным, а также `last_game_first_paint_ms`, `count_first_paint_ms`, `full_metrics_ready_ms`. Без доступа к Windows GUI или эквивалентной БД — дать воспроизводимый smoke/benchmark-script, результаты fixture и **честно отметить** недоступную проверку на личной базе.

### 5.4. Регрессионные тесты (обязательны)

1. **Stored account race:** `workspace/selected_account="lichess:test"`, начальный ComboBox пока `None`, preview `None` приходит с `_selector_options`; пользовательский выбранный аккаунт отображён; preview нового `lichess:test` действительно отправлен/отображён без ожидания старого aggregate. `None` данные никогда не показаны как `test`.
2. **Slow aggregate:** принудительно держать `dashboard_snapshot` заблокированным 10–30 условных секунд (barrier/mock), preview на выбранном аккаунте должен отобразить число и 3 партии раньше его завершения, UI event loop отвечает на clicks; никаких реальных sleep 30 s в тестах.
3. **Fast repeated auth:** смена `account A → account B` и 2–3 несинхронных auth callbacks не приводят к постоянной отмене preview; stale A не отображается у B; смена аккаунта начинает новую generation.
4. **Fair Play:** online auth pending, active human game, denied/unknown active game snapshot → быстрые нейтральные метаданные доступны, **engine-derived metrics/PV, cached tooltips, graph hidden**. Подтверждение безопасности разрешает именно положенные элементы без повторной тяжёлой загрузки архива.
5. **Large SQLite fixture:** 3–10 тысяч синтетических игр + несколько анализов, варианты account scoped / all games, отсутствие индексов/плохой query plan выявляются профилем; тест verify sorted recent rows, count, no duplicate и no full-PGN parse на preview.
6. **Library/Progress:** открыть каждую страницу сразу после cold launch/во время background writes: local list/первые значения показываются независимо от Lichess, worker generation/Back корректны.
7. **No regressions:** обычный connected Lichess, `--manual-connect` (использует **ту же реальную локальную базу без автоматического подключения**) и `--offline` (**изолированная ВРЕМЕННАЯ база**, **НЕ** подходит для оценки скорости личного архива). Никакого автоматического POST ради статистики.

### 5.5. Сдача Codex по второму блоку

- Зафиксировать в итоговом `docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md` **точный обнаруженный root cause** (включая invalidation preview scope), список затронутых функций, benchmark метод и цифры, количество/результат новых тестов, статус обычного Windows GUI smoke.
- **Обязательная объединённая приёмка двух блоков:** отсутствует внешнее окно «Случайный цвет»; выбран верный radio; в `Home` видны локальный счётчик и последние 3 партии сразу без ожидания Lichess; «Мои партии»/«Прогресс» не ждут сеть для нейтральных данных; Fair Play и SQLite account isolation неизменны.
- Запустить полный pytest, `compileall`, `git diff --check`, Windows CI jobs `test` и `visual-evidence` на **точном новом HEAD**; PR #46 оставить draft, `main` не трогать, 13 side-by-side визуальных эталонов — следующая отдельная приёмка.

**Не расширять скоуп** на общий редизайн, новые дебюты или переписывание SQLite. Это два адресных исправления действующего PR #46.
