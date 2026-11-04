# ARCH-UX-01 — REVIEW-FIX-01: корректировка после независимого ревью кодового PR #44

**Дата ревью:** 04.11.2026  
**Решение:** **пока НЕ принимать** реализацию ARCH-UX-01, но **не переделывать весь цикл A0–A12**. Внести адресные исправления ниже в **том же кодовом draft PR #44**; дождаться повторной проверки.  
**Точка ревью:** PR #44, ветка \`codex/arch-01-modular-refactor\`, кодовый HEAD **\`2b1c724b4503140992ad8d80f8c5044c861eb7cd\`** (проверен на GitHub). Windows [run #244](https://github.com/feshakomissarov-prog/chess/actions/runs/41774974147) SUCCESS именно на этом SHA; в handoff заявлено 632 locally passed. Это **прошлые** проверки — не доказательство исправлений ниже. PR #44 draft, не слит.  
**Нормативные источники:** [ARCH-UX-01 v1.1](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), [предстартовый аудит](../reviews/ARCH_UX_01_PRE_FLIGHT_AUDIT.md), [UX-B1](../ux/UX_APPROVED_BASELINE_V1.md), [UX-A2](../ux/UX_ANALYSIS_APPROVED_V2.md), [UX-GAME-01](../ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [карта 24 функций](../ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md), [AGENTS.md](../../AGENTS.md).

## 0. Как исполнять

1. Сначала проверить текущий HEAD PR #44 и состояние рабочего дерева; **не откатывать чужие новые коммиты**, не перезаписывать пользовательские данные и \`.venv312/\`. Если HEAD сдвинулся — сопоставить найденные ниже симптомы с актуальным кодом; уже исправленное повторно не делать.
2. Выполнить только коррекционные пункты **RF-01…RF-07**, в порядке безопасности → шахматной корректности → UX → отзывчивости. Каждый фикс с минимальным regression test и коммитом; без сторонних библиотек и новых schema migrations. Общая архитектура ARCH-01, одна SQLite, один Stockfish, один Lichess, separate lanes и все существующие функции остаются.
3. Документирование и итоговую матрицу — в \`docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md\`: отдельная секция REVIEW-FIX-01, фактические SHA, тесты, оставшиеся физические проверки. **Новый кодовый PR не создавать**, продолжить draft #44; ветку main не менять, PR не сливать.
4. После фиксов выполнить полный текущий offscreen pytest, compileall, diff check, CLI \`--offline\`/\`--manual-connect\` safety, **Windows CI на новом точном HEAD**. Не ссылаться на зелёный run #244 как на CI коррекции. Не требовать живой Lichess/личную SQLite/настоящий Stockfish без разрешения владельца.
5. Не заполнять путь имитациями: если фича не готова, откалибровать UI/задокументировать действующий scope, но не оставлять работающую кнопку с заведомо неверным шахматным смыслом.

## RF-01 — P0: маскировать official Lichess accuracy при fail-closed Fair Play

**Наблюдаемый код:** \`src/chess_coach/window.py::_render_dashboard_snapshot\` считает \`masked = metric["key"] in {"analysis","errors"} and not analysis_allowed\`; \`accuracy\` **в этой проверке нет**, хотя \`src/chess_coach/application/dashboard.py::dashboard_snapshot\` формирует \`owner_accuracy\` и отображает её под заголовком «Точность Lichess». В \`docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md\` даже указано, что official accuracy остаётся видимой при блокировке. Это противоречит консервативному P0 «любые engine-derived оценки и показатели на всех маршрутах скрываются при активной human game/unknown/429/reconnect» в ARCH-UX-01 и UX-B1.

**Сделать:** классифицировать official server accuracy как оценочную аналитику, требующую того же display safety lease, что и CP/error rates. При pending/unknown/denied/human live **до paint** маскировать значение, знаменатель/детали и любые дополнительные tooltip/accessible descriptions, способные раскрыть аналитическую метрику. Не скрывать обычную шахматную фактологию (дату/соперника/результат/контроль/число партий). Применить единообразно к Home, Game Details, Progress, Review, включая повторный показ cached widgets и смену аккаунта. Не изменять сохранённые official значения и provenance в БД.

**Приёмка:** Qt-тест **фактических видимых QLabel/tooltip/accessibility** для 1) изолированного offline, 2) online до preflight, 3) server safe snapshot, 4) human-gameStart/gameFull, 5) 429/reconnect, 6) stale account/generation. При запрете даже кратковременно не видно accuracy/score/graph/PV/errors, после свежего разрешённого preflight корректное восстановление. Negative tests всех маршрутов; никаких новых UCI запусков.

## RF-02 — P1: сохранить корректные права рокировки/взятия на проходе в редакторе FEN

**Наблюдаемый код:** \`src/chess_coach/ui/pages/position_setup.py::PositionSetupWidget._open_position\` **безусловно выполняет** \`self._board.castling_rights = chess.BB_EMPTY\`, \`ep_square = None\`. Даже если пользователь открыл неизменённую стандартную начальную позицию, она превращается в **другую** шахматную позицию без рокировок. В редакторе есть только фигуры и «Сторона, которой ходить», **нет утверждённых в UX-A2 полей castling/en-passant**.

**Сделать:** дать минимальные, однозначные параметры FEN: white K/Q, black k/q и en-passant square (или явный «Нет»). При «Начальная позиция» воспроизводить полностью \`chess.STARTING_FEN\`, в том числе \`KQkq\`, при «Очистить» — пустые права. Редактирование фигур не должно молча утверждать невалидные права; допускается автоматическое снятие прав при удалении соответствующих королей/ладей с явной индикацией или обязательная проверка перед открытием. Не выставлять \`ep_square\` если невозможно подтвердить legal context; сверить \`python-chess Board.is_valid()\` и format. Прежде чем \`position_requested.emit(fen)\`, проверить board и показать нейтральную причину при неверной расстановке; **никогда не открывать её в review/Stockfish как нормальную**.

**Приёмка:** обычный старт → \`chess.STARTING_FEN\`; правомерная позиция с рокировками; корректный en-passant контекст; удаление короля/ладьи; две стороны хода; invalid position без сигнала открытия; mouse click/clear/reset; отсутствие SQL, сети и engine при расстановке. Не писать в \`games\` и не отправлять ходы в Lichess.

## RF-03 — P1: безопасность и навигация временного PGN/FEN review

**Наблюдаемый код:** \`GameReviewWorkspace.load_game(..., ephemeral=True)\` запоминает \`self._ephemeral_study\`, но \`GameReviewWorkspace.set_training_safety_result\` при смене safety вызывает \`self.load_game(game, training_allowed=allowed, training_block_reason=reason)\` **без параметра \`ephemeral\`** и обнуляет \`selected_ply\`. При revoke/повторной проверке временный review может потерять эпизодический флаг, показать обычные analysis controls либо прыгнуть к первому полуходу. Внешняя сессия должна оставаться read-only относительно архива и не путаться со saved game даже после повторных safety events.

**Сделать:** перезагрузка/revoke/allow обязаны сохранять \`ephemeral\` identity, source kind, выбранный ply и контекст маршрута; immutable view state/rebind или точечный маскирующий render без destructive reload. Запрет engine/batch/save-to-archive для внешней study сохраняется независимо от временной смены safety. Возврат в основной Review должен вернуть оригинальный repository и все его callbacks. Не разрешать stale внешней callback перезаписывать открывшуюся живую игру или внутреннюю сохранённую review. Не вводить новую базу/репозиторий.

**Приёмка:** FEN без ходов и PGN с ходами → выбрать ply N → safety pending/revoke/allow → остается тот же выбранный ply, \`ephemeral\` и disabled unsafe actions; навигация Analysis→Games→Analysis и переключение аккаунта с delayed callbacks; persistent \`games\`/analysis_runs unchanged.

## RF-04 — P1: довести согласованную встроенную форму «Играть»

**Наблюдаемый код:** \`src/chess_coach/ui/pages/play.py::PlayPage.clock\` включает без часов, 3+2, 5+0, 10+0, 15+10, но **отсутствует обещанный в U3 custom clock**. Кроме того \`_update_form\` скрывает виджеты, но не обязательно строки-подписи; новый UI должен быть один законченный form, не смесь устаревших параметров.

**Сделать:** добавить пользовательский ввод минут/секунд инкремента с проверками и Lichess API допустимости; не обещать неподдерживаемые unlimited/rated режимы. Сохранить старые presets и сохранённые AI preferences, не менять методы Lichess facade. Скрывать/показывать всю соответствующую строку параметра, включая подпись. Создание по одной кнопке идемпотентно; 400/401/429/timeout отображаются и освобождают pending состояние в соответствии с уже существующей логикой.

**Приёмка:** AI/friend/username/local/active с preset и custom valid/invalid, цвета/rated только где допускается, двойной клик не создаёт второй запрос, выбранные поля не теряются случайно; fake transport и отказ API.

## RF-05 — P1: «последние завершённые» не должны сортироваться по дате изменения аналитики

**Наблюдаемый код:** \`infrastructure/sqlite/games.py::search_games\` сортирует \`ORDER BY g.updated_at DESC, g.game_id\`; \`application/dashboard.py::dashboard_snapshot\` использует \`search_games(...page_size=3, completed_only=True)\` для раздела «Последние партии». Когда у старой партии обновлены Stockfish/metadata, она может вытеснить **недавно сыгранную завершённую** из списка. У каталога аналогично «новые → старые» не привязано к игровому времени.

**Сделать:** для «последних завершённых» выбирать достоверную игровую дату/время (started/finished, при наличии; fallback updated только при отсутствующей достоверной игровой дате). Если допустимо без нарушения API совместимости, ввести \`order_by_game_time\` как явно используемый режим; не менять молча существующий default контракта \`search_games\` для других клиентов. Учитывать PGN date precision и пустые значения; стабильно сортировать при равенстве; менять поиск только в пределах bounded SQL, без full PGN scan/main-thread sort. Для каталога сделать смысл сортировки явным («Дата партии» или «Недавно обновлено») и применить обещанное UX поведение.

**Приёмка:** временная SQLite с партией A сегодня и партией B месяц назад, поздним обновлением анализа B; на Home показывается A раньше B, период продолжает фильтровать по дате партии, при равных timestamps стабильный порядок; active cards по-прежнему pinned выше finished. Нет схемных миграций.

## RF-06 — P2: устранить синхронный SQL в новых точках UI и дублирующиеся действия навигации

**Наблюдаемый код:** \`ui/pages/home.py::HomePage.__init__\` вызывает \`dashboard_options(repository)\` (SQL \`accounts\` и \`DISTINCT clock\` в Qt constructor). \`window.py::show_saved_games\` выполняет \`dashboard_options(self.game_repository)\` на Qt thread до монтирования страницы. Новая архитектура 2.5 и U10 требует lazy/bounded Qt презентацию без DB/blocking work в конструкторе страницы.

**Сделать:** создавать соответствующие selectors из пустого/кешированного read model и подгружать account/clock options через уже существующий \`BackgroundWorkCoordinator\`/short readers, queued signals и generation/account guard. Не блокировать экран при долгом SQLite writer/checkpoint; не создавать отдельный executor. Сохранить активную account/clock фильтрацию, selected route/scroll, действующие signals. Это **точечная** работа, не повод переписывать 6k строк MainWindow ради метрики LOC.

**Приёмка:** instrumented/fake repository блокирует чтение selectors; конструкторы Home/Library и переходы между ними завершаются без SQL в GUI thread, статус виден; новый worker не остаётся после закрытия экрана и не перезаписывает новый account. Тесты на existing selectors, disabled/offline/no-data; повторные переключения не плодят jobs.

## RF-07 — P2: завершить точечный UX acceptance и фиксацию ограничений

1. **Главная U2:** в \`HomePage\` есть KPI/активность/три партии, но из проверенного кода **не видно отдельного небольшого реального тренда** (заявлен U2). Использовать только существующий подтверждённый временной ряд (если он доступен), с N/source и честным «Нет данных» вместо построения фантазий. Без нового metrics store, heavyweight SQL/Stockfish и без перехода на персональный тренер.
2. **Анализ U6:** разрешённая временная PGN/FEN/ручная позиция открывается в \`GameReviewWorkspace\`, но \`review_board\` имеет \`set_interaction_enabled(False)\` за исключением режима учебного ответа. Нужна **минимальная безопасная локальная возможность исследовать альтернативный законный ход/ветку из выбранной позиции** в ephemeral study, не меняя original PGN/revision и не отправляя ход в live Lichess; не строить новый полный explorer. При active human не запускать UCI и не показывать подсказки.
3. **Игровые команды U4:** перепроверить физическую \`QWidget.geometry()\` и видимые области без page scroll на 1280×720 / 1366×768 / 1920×1080, включая high DPI 125/150% и сохранённые 720/480 размер доски; видимые takeback/offer/accept/decline/resign/claim **не должны оказаться под границей экрана**. В offscreen тестах проверять не только \`isVisible()\`, но \`mapTo(window).geometry()/intersection\` или эквивалент. Фокус-режим — по явному действию.
4. **Публичные пилоты:** проверить отсутствие новой «Тренировки предходов», pilot archive/puzzle, single refresh в обычных UX; обычный online premove, диагностическое восстановление, звуки/defaults и archived graphs не потерять.
5. **Финальная запись:** дополнить deterministic тестом, где выбранная партия завершена, одновременно другая активна, окно переключилось в Анализ и SQLite writer задержан/ошибся: сохранение по authoritative line, корректная явная ошибка, bounded retry и возможность recovery; не объявлять «сохранено» преждевременно. Лог должен отличать transient pending от durable failure, без бесконечного Qt timer и без повторного сетевого POST.

**Приёмка RF-07:** функционально предъявить честный Home trend/fallback, законное ephemeral line-branching без записи/engine, протестированные viewport intersections, полный перечень COV-01…24, статус finalization, сохранённый манифест звуков. Реальные Windows DPI/звук/аккаунт/Stockfish остаются для отдельной ручной проверки владельца и **не могут быть названы PASS без запуска**.

## 8. Итоговые условия повторной приёмки

- Все RF-01…RF-07 выполнены и отмечены тестовым доказательством по каждому; отклонение допустимо **только** при доказанном соответствии уже существующего кода, приведённом в handoff с тестом.
- Автотесты не ослаблены и не удалены ради прохождения; новые негативные сценарии добавлены. Нет нового backend, схемы, токенов, фоновых очередей, дублирующего review/аналитики, QSettings regression, личных данных в GitHub.
- Hosted Windows CI SUCCESS **на новом точном code HEAD**. В итоговом отчёте отдельно написать проверенные автоматические сценарии и то, что реально не проверялось вручную.
- PR #44 **остаётся draft и НЕ сливается**, PR #45 — документационный draft. После результата — повторное независимое ревью, только потом решать о Windows pilot владельца и merge.

**Короткий статус:** требуется **корректировка**, полное ТЗ ARCH-UX-01 и ARCH-01 не перезапускать.
