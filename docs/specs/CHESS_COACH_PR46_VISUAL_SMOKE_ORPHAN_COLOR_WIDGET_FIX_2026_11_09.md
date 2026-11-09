# Chess Coach · PR #46 — исправить лишнее системное окно «Случайный цвет» и синхронизацию выбора цвета

**Дата:** 09.11.2026.  
**Источник:** первое ручное визуальное тестирование владельца на Windows, четыре скриншота: Home, панель задач с двумя окнами Chess Coach, Play (Lichess AI), Analysis landing.  
**Статус:** **подтверждённый дефект Qt/PySide6, P1 visual-smoke**, одна узкая работа в **текущем Draft [PR #46](https://github.com/feshakomissarov-prog/chess/pull/46)**.  
**Проверенный кодовый SHA:** \`024128de162895b3a547fcbf56a8be5312500234\`. Получить свежий HEAD перед правкой.  
**Ветка для изменения:** \`codex/unified-desktop-ux-2026-11-08\`. Не создавать новый PR и не делать merge в \`main\`.  
**Контракт:** [основное мастер-ТЗ](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md), Play UX-01/02/03 и критерии §20/§26.  
**Важно:** исправлять **реальный код** PlayPage, не генерировать новые PNG и не перестраивать основную композицию.

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

**Вердикт:** остальной интерфейс на пользовательских скриншотах выглядит в целом близким к согласованным каркасу и цветовой гамме; это **маленькая конкретная Qt-ошибка**, не повод переделывать все 13 экранов заново. После этой правки продолжить сравнительную визуальную приёмку.
