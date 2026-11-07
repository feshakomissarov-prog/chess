# MY-GAMES-UX-02 · реестр кнопок, функций и будущих обязательств

**Дата:** 07.11.2026. **Тип:** живой реестр сохранности функционала (feature ledger), работающий вместе с docs/specs/MY_GAMES_UI_REWORK_CODEX_SPEC_2026_11_07.md.  
**Предназначение:** после внедрения проверять каждое действие по ID и помнить, **что стало видимым**, что переехало, что скрыто по контексту, что отложено. Нельзя считать «скрыто» синонимом «удалено из приложения».  
**База инвентаризации:** src/chess_coach/ui/pages/library.py и window.py из code PR #44 SHA 5c375b070e6644dbded5fca62d960fc09c90651e. **Наличие обработчика/виджета в source ≠ пройденный реальный Windows тест.** После §15 и перед исполнением перепроверить новый SHA.

## Статусы

- **EXISTS** — присутствует в контрольном исходнике; **REDESIGN** — пользовательское действие сохраняется, интерфейс переделывается; **MOVE** — функция доступна, другое место; **PRESERVE** — сохранять поведение без упрощения его механики; **ADD** — требуется новая презентация/минимальная связка; **FIX ROUTE** — источник уже подтверждает расхождение ожидаемой семантики; **CONTEXT** — видно только при уместном состоянии; **PARKED** — не выполнять в этом ТЗ.
- **Верификация после реализации** пока для ВСЕХ строк = **NOT TESTED**. Только ссылки на реальные тесты+CI/native Windows evidence вправе заменить её на IMPLEMENTED/TESTED. Не ставить PASS заранее.

## A. Оболочка, поиск и список

| ID | Контрол / способность | База на SHA | Целевое действие | После |
| --- | --- | --- | --- | --- |
| MG-01 | Sidebar 6 маршрутов | EXISTS | PRESERVE; «Мои партии» выделен, общий shell без новых вкладок | NOT TESTED |
| MG-02 | Global «← Назад» | EXISTS | PRESERVE; возврат к предыдущему workspace, не pager | NOT TESTED |
| MG-03 | Общий «Фон» | EXISTS | PRESERVE; единственный обычный переход к Background | NOT TESTED |
| MG-04 | Заголовок «Мои партии» | EXISTS | REDESIGN: оставить существующий уровень/типографику | NOT TESTED |
| MG-05 | Краткая сводка архива + last sync | PARTIAL | ADD/REDESIGN: правдивый account-scoped N и confirmed timestamp; без выдуманных цифр | NOT TESTED |
| MG-06 | Ручная синхронизация архива | EXISTS | MOVE: «Обновить архив» в компактный верхний ряд, retry/resume через существующий сервис | NOT TESTED |
| MG-07 | Автоматическая синхронизация/429/resume | EXISTS | PRESERVE; никакого обхода квот/таймеров, не путать с локальным SF | NOT TESTED |
| MG-08 | Search по архиву | EXISTS | PRESERVE; дебаунс, SQL paging и отмена старых workers | NOT TESTED |
| MG-09 | Filter toggle | EXISTS | REDESIGN: «Фильтры (N)» рядом с поиском; реальный активный счётчик | NOT TESTED |
| MG-10 | Фильтр период/дата | EXISTS | PRESERVE; не удалять, пользователь решил оставить | NOT TESTED |
| MG-11 | Фильтр account | EXISTS | PRESERVE; owner/generation + all-account scope там, где доступно | NOT TESTED |
| MG-12 | Фильтры цвет, результат, source, контроль | EXISTS | PRESERVE; всё в раскрываемом блоке | NOT TESTED |
| MG-13 | Фильтры наличие анализа/источник | EXISTS | PRESERVE; официальное vs локальное, не смешивать | NOT TESTED |
| MG-14 | Фильтры opponent, opening/ECO | EXISTS | PRESERVE; при очистке не сбросить выбранный account | NOT TESTED |
| MG-15 | Сброс фильтров | если отсутствует — ADD | ADD: сброс только search/filter state, корректное page 1 | NOT TESTED |
| MG-16 | Список завершённых партий | EXISTS | REDESIGN, сохранить QListWidget/delegate/paging, не делать тысячи QWidget | NOT TESTED |
| MG-17 | Состояние результата win/loss/draw | EXISTS | PRESERVE; от перспективы игрока, текст+цвет | NOT TESTED |
| MG-18 | Имя opponent / AI level | EXISTS, есть источник рассогласования (VID-02) | FIX display: единый подтверждённый opponent во всех поверхностях | NOT TESTED |
| MG-19 | Дата, время, контроль, ECO/дебют | EXISTS | PRESERVE и компактизировать, не фабриковать при unknown | NOT TESTED |
| MG-20 | **Мой цвет в строке** | в карточке не отражён достаточно ясно | ADD: «Белыми» / «Чёрными» или unknown | NOT TESTED |
| MG-21 | **Моя accuracy в строке** | в row delegate отсутствует | ADD: source-aware own %, «Не оценено» и coverage, без массового N+1 SQL | NOT TESTED |
| MG-22 | Неточности/ошибки/зевки в строке | не основная пользовательская цель списка | **НЕ ВЫВОДИТЬ В СТРОКЕ**; данные остаются в detail и Review | NOT TESTED |
| MG-23 | Активные партии | EXISTS | CONTEXT: pinned отдельный список «В ИГРЕ», вернуть нужный game ID | NOT TESTED |
| MG-24 | Предыдущая/следующая страница + номер | EXISTS | MOVE: **все ТРИ элемента вместе строго под левым списком** | NOT TESTED |
| MG-25 | Selected item / scroll / filters restore | EXISTS в коде и тестах частично | PRESERVE/REGRESSION: A/B, stale, navigation, resize, PGN revision | NOT TESTED |

## B. Правая карточка, график, работа с партией

| ID | Контрол / способность | База на SHA | Целевое действие | После |
| --- | --- | --- | --- | --- |
| MG-26 | Правая панель выбранной игры | EXISTS | REDESIGN: компактная постоянно видимая панель с fixed footer на desktop | NOT TESTED |
| MG-27 | Заголовок/score белые-чёрные | EXISTS | REDESIGN: verified opponent/score/my color; не повторять result 3 раза | NOT TESTED |
| MG-28 | Реальная дата/контроль/источник | EXISTS | PRESERVE, уточнять после late final PGN без потери selection | NOT TESTED |
| MG-29 | Две карточки «Моя/Соперник» accuracy | существующие values white/black общим текстом | REDESIGN: own side mapping, source per value, partial/unknown states | NOT TESTED |
| MG-30 | Источники Lichess/Stockfish | EXISTS | PRESERVE: разные source/provenance, Lichess preferred для one-line official when valid | NOT TESTED |
| MG-31 | «Неточности/Ошибки/Зевки» | EXISTS | REDESIGN: столбцы Я / Соперник; null ≠ 0; без дублированных значений | NOT TESTED |
| MG-32 | Coverage X/Y | EXISTS | REDESIGN: правильные единицы и знаменатели, не присваивать обеим сторонам общий X/Y как их own | NOT TESTED |
| MG-33 | Local phase accuracy | EXISTS в review.local_phase_accuracy, в UI под раскрытием | REDESIGN: три ячейки Дебют/Миттельшпиль/Эндшпиль, только real own data | NOT TESTED |
| MG-34 | Compact ReviewGraph | EXISTS | REDESIGN: clear zero, white-above/dark-below fill, без per-move symbols | NOT TESTED |
| MG-35 | Полный ReviewGraph CP/Δ/mate/markers | EXISTS, не в library | PRESERVE AS-IS в Review, compact не регрессирует main graph | NOT TESTED |
| MG-36 | Клик по графику → selected ply Review | EXISTS | PRESERVE с account/revision/safety guard | NOT TESTED |
| MG-37 | «Открыть разбор» | EXISTS | PRESERVE, главное действие; открывает выбранную PGN, **не** создаёт engine job | NOT TESTED |
| MG-38 | «Анализ Stockfish» | EXISTS, но callback идет в show_batch_analysis() | **FIX ROUTE:** ручной analysis именно выбранной завершённой партии; мгновенный статус, SF job после safety | NOT TESTED |
| MG-39 | «Экспорт PGN» | EXISTS | PRESERVE; вторичная кнопка в pinned action bar | NOT TESTED |
| MG-40 | «Заметки» и сохранённые данные | EXISTS | MOVE: компактное вторичное действие в правой карточке / при узком окне в «Ещё»; note draft/revision не терять | NOT TESTED |
| MG-41 | Полезное «...» | частично | CONTEXT: только существующие вторичные функции; не придумывать бесполезные пункты | NOT TESTED |
| MG-42 | «Обновить эту партию из Lichess» | EXISTS, recovery hidden | CONTEXT: оставить только при диагностированном случае; не делать крупной кнопкой | NOT TESTED |
| MG-43 | «Фоновые операции» в нижней строке | EXISTS, дублирует header | **HIDE duplicate** в library footer; header action остаётся | NOT TESTED |
| MG-44 | «Настройки загрузки» внизу | EXISTS | MOVE в Settings → Lichess/загрузка; не удалить настройку | NOT TESTED |
| MG-45 | Старый Library «Закрыть» | EXISTS hidden | HIDE; глобальный Back остаётся | NOT TESTED |
| MG-46 | Fair Play mask/recheck | EXISTS | PRESERVE/TEST: no old engine hints, no graph, no accuracy while human live/unknown | NOT TESTED |
| MG-47 | Пустые/загрузка/отмена/ошибка | PARTIAL | REDESIGN: каждый state честен, статус конкретной операции, никаких placeholder zero | NOT TESTED |
| MG-48 | Account switch / async / late source update | EXISTS частично | PRESERVE/TEST A→B→A, source revision, no wrong metric/PGN/notes | NOT TESTED |
| MG-49 | Responsive 100/125/150%, narrow window | PARTIAL, V-01/V-03 current problems | REDESIGN/TEST: 2 columns or master→detail, footer always reachable | NOT TESTED |

## C. Сохранять в смежных экранах, НЕ переделывать этой задачей

| ID | Функция | Решение |
| --- | --- | --- |
| MG-50 | Lichess OAuth/PAT, Win Credential Manager | PRESERVE; никаких secret changes |
| MG-51 | Глобальные настройки звуков и выбор sound cues | PRESERVE AS-IS, текущий UX scope не затрагивает |
| MG-52 | Игровая доска/премувы/живые часы/концовка на той же доске | PRESERVE; MY-GAMES не переписывает game flow |
| MG-53 | Mass Stockfish queue + local jobs | PRESERVE; single-game priority integration без потери batch |
| MG-54 | SQLite PGN, line revisions, archive cursor/checkpoint/notes | PRESERVE; no migration without proven necessity |
| MG-55 | Review navigation, events, study FEN/PGN, training from analyzed errors | PRESERVE; полный Review без графической деградации |
| MG-56 | Source-aware Lichess ready analytics, API limits | PRESERVE; «Обновить архив» НЕ заказ server analysis |
| MG-57 | Single-instance / window restore / keyboard accessibility | PRESERVE; новая страница не ломает рабочую оболочку |

## D. Отложенные идеи (чтобы не забыть)

| ID | Идея | Сейчас | Где позже |
| --- | --- | --- | --- |
| MG-F01 | Поиск по тексту собственных заметок | PARKED, notes сохраняются | отдельное решение об индексации/полнотекстовом поиске |
| MG-F02 | Диагностика причин ошибок по сериям партий | PARKED | Анализ / Прогресс |
| MG-F03 | Рекомендация урока на основе ошибок выбранной партии | PARKED | Обучение, после компетентностной модели |
| MG-F04 | Персональный репертуар дебютов и подготовка к сопернику | PARKED | Исследование дебютов / Анализ |
| MG-F05 | Адаптивный учебный план / ИИ-тренер | PARKED | Обучение / Разбор, последние milestones |
| MG-F06 | Запуск Lichess cloud analysis из приложения | НЕ ЗАКАЗЫВАТЬ и не показывать псевдокнопку | только после отдельного продукта и API/safety review |
| MG-F07 | Альтернативные источники accuracy/metric reconciliation | PARKED, источники не смешивать | глубокая диагностика качества данных |

## E. Протокол обновления после каждого UX PR

Ответственный разработчик/ревьюер обязан:
1. Взять **новый exact code SHA** и для каждого изменённого MG-XX записать **IMPLEMENTED, TESTED, PARTIAL, BLOCKED или NOT IMPLEMENTED**, ссылку на тест/CI/artifact, конкретную UI точку, account/safety limitations. Ключевые MG-01…49 должны иметь явный результат.
2. Если UI-контрол удалён визуально, в реестре отдельно указать **«действие доступно по маршруту X»** или честно **«пользовательская функция больше недоступна»**; второе нельзя silently merge без владельца.
3. Нельзя считать PASS на основании рендера/теста без клика, или синтетического fakes как подтверждения live API/реального DPI.
4. При переходе на следующий экран редактировать **его** реестр и карту навигации без переписывания уже утверждённых решений MY-GAMES.

**Примечание:** этот реестр — дизайнерский/функциональный контроль, **не отдельное задание Codex** и не новая очередь задач для текущего §15 PR #45.
