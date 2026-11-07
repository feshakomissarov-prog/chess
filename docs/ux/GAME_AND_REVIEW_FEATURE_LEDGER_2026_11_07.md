# Chess Coach · GAME-UX-02 — реестр кнопок, сценариев и будущих состояний

**Дата:** 07.11.2026 · **Статус:** реестр для дизайнерского обсуждения, не реализация. Основание — PR #44 HEAD 79005c04b6f4d23058b25a133d2c9e407cc49111, source window.py / ui/game_review.py и 3 скриншота владельца; нужно обновить exact SHA после нынешнего §15. Companion: GAME_AND_REVIEW_TARGET_UX_V1_2026_11_07.md. Отсутствие кнопки на скриншоте НЕ означает отсутствие source feature.

**Маркировка:**
- KEEP = сохранить функцию; MOVE = перенести; SHRINK = уменьшить визуальный вес; CONDITIONAL = показывать по состоянию; COLLAPSE = убрать постоянно занимаемую площадь, не удалить; ADD = новая минимальная презентация; PARKED = не делать в текущем UI цикле;
- вся новая целевая UX работа **NOT IMPLEMENTED / NOT TESTED**, никакого PASS до точного SHA и native Windows evidence;
- будущий «единый CODEX ТЗ» включает эти пункты только после утверждения владельцем.

## A. GAME — шахматная доска, часы и управление (GB-01…GB-38)

| ID | Что уже есть в коде / на скрине | Предлагаемое назначение в целевом интерфейсе | Решение |
| --- | --- | --- | --- |
| GB-01 | QStackedWidget game_page с тем же BoardView | Единственная active/finished/local доска, не плодить GameWindow | KEEP |
| GB-02 | Шахматная доска 8×8, mouse move intent | Большой главный элемент; auto-fit по реальному viewport | KEEP + AUTO-FIT |
| GB-03 | board_target_size: 400/480/560/720/auto | «Авто» нормальный старт; ручной размер вторично в настройках доски | MOVE/SHRINK |
| GB-04 | opponent_label + you_label | Имена, настоящий цвет, над/под доской | KEEP/REDESIGN |
| GB-05 | opponent_clock_label + you_clock_label | Чёткие реальные часы, только если есть; без выдуманного 00:00 | KEEP/CONDITIONAL |
| GB-06 | «Без обычных часов» повторяется сверху/снизу | Предлагается единый «∞ Без контроля времени» в мета строке | SHRINK — OWNER DECISION |
| GB-07 | статус «Ваш ход / Ход соперника» | В одной компактной строке, без дублированного огромного status card | SHRINK |
| GB-08 | Lichess technical «started» | Перевести в человеческий «Игра идёт», подробности — tooltip | SHRINK |
| GB-09 | MoveTable №/White/Black | В правой колонке постоянно, собственный вертикальный scroll | KEEP |
| GB-10 | 4 history buttons "|< < > >|" | Одной строкой под ходами, доступные названия; latest крупнее по смыслу | KEEP/REDESIGN |
| GB-11 | «Актуальная позиция / Просмотр хода N» | Виден возврат к latest; из истории нельзя делать online move | KEEP |
| GB-12 | live move optimistic/pending_uci | Не отправлять второй реальный move POST до ACK, premove отдельно | KEEP |
| GB-13 | premove_intent, board markings | Premove остаётся на доске, без подсказки движка | KEEP |
| GB-14 | «ПРЕДХОД» и label | Только если queued: компактная строка состояния | CONDITIONAL/SHRINK |
| GB-15 | «Отменить предход» / Esc | Маленькая кнопка рядом с queued premove | MOVE/KEEP |
| GB-16 | «Сдаться» | Постоянно видимая live-команда, подтверждение обязательно | KEEP |
| GB-17 | «Предложить ничью» | Всегда доступна, когда разрешена live state | KEEP |
| GB-18 | «Принять/Отклонить ничью» | Inline при авторитетном входящем предложении | CONDITIONAL |
| GB-19 | «Вернуть ход» (AI) / «Предложить возврат хода» (human) | Live-команда, только серверная; не undo local | KEEP |
| GB-20 | «Принять/Отклонить возврат хода» | Inline на том же месте при входящем event | CONDITIONAL |
| GB-21 | «Заявить ничью по правилу» | Только при can_claim_draw() и API-support | CONDITIONAL |
| GB-22 | «Завершённые партии» справа | Уже есть «Мои партии» в sidebar; убрать дубликат из command block | MOVE/HIDE DUPLICATE |
| GB-23 | «Новая партия» справа во время live | Не конкурирует с текущим game, перейти через Play; без reset по клику выбора режима | MOVE/HIDE WHEN LIVE |
| GB-24 | «Перевернуть» | Маленькая видимая команда рядом с доской/нижними часами | MOVE/KEEP |
| GB-25 | «Очистить пометки» | Сохраняет функцию, компактно в действиях доски | SHRINK |
| GB-26 | «Режим фокуса» full-width | Маленький управляющий toggle, увеличение board измеримо | REDESIGN |
| GB-27 | QSplitter right side hides in focus | Focus скрывает right+secondary, сохраняет session/clock, управление 1 клик | REDESIGN |
| GB-28 | Sidebar + Back + Фон | Preserve existing shell; temporary collapse allowed in focus, restore | KEEP |
| GB-29 | Internet reconnect/snapshot | Board stale safely held, no unsafe POST, clock unknown honest | KEEP |
| GB-30 | Online status changed/gameFinished | After confirmed finish same board at last ply | KEEP approved |
| GB-31 | finished_review_button «Разобрать» | One of exactly two main finished actions, only after confirmed PGN and safety | KEEP |
| GB-32 | finished_challenge_button «Вызвать снова» | Second main action, contextual AI/human, new server request only explicit | KEEP |
| GB-33 | old result_page compatibility object | Never navigate to separate ResultPage/overlay by automatic finish | KEEP HIDDEN |
| GB-34 | finalization probe / SQLite confirmation | Disabled «Разобрать» until committed exact final line; display brief state | KEEP |
| GB-35 | Export current PGN / finished_export_button | Secondary, preserve source, no new primary CTA | MOVE/KEEP |
| GB-36 | return to live / resume_current_game | Preserve and reflect current account/game ID, no reset | KEEP |
| GB-37 | multiple live online sessions | No automatic jump to other game, selection via Play/current session | KEEP |
| GB-38 | sound moves/capture/notifications and settings | **AS-IS** events/defaults, no layout-induced duplicate sound | KEEP |

## B. Локальная доска и учебные ограничения (GB-39…GB-50)

| ID | Уже есть | Целевая судьба | Решение |
| --- | --- | --- | --- |
| GB-39 | start_local_game/free board two-sided | Один GAME local layout без сетевых кнопок | KEEP |
| GB-40 | «Отменить последний ход» (undo_local_last_move) | Главная локальная команда, не серверный takeback | KEEP |
| GB-41 | Flip board, arrows, clear annotations | Локальные инструменты, по ситуации | KEEP |
| GB-42 | «Новая партия» в локальном режиме | Создать новую чистую доску только по явному клику | KEEP + UNSAVED GUARD |
| GB-43 | position_eval_button «Анализировать позицию» | Вторичный локальный учебный инструмент с fresh safety preflight | CONDITIONAL |
| GB-44 | position_eval_label с результатом/PV | Только при safety разрешённом учебном запросе, без human live | CONDITIONAL |
| GB-45 | local_mode_combo/«Тренировка предходов» hidden | НЕ делать новый режим на Play/Game, preserve test/internal only | KEEP HIDDEN |
| GB-46 | local history navigate/back/forward | Доступно без engine and server | KEEP |
| GB-47 | local PGN/revisions | Не перезаписывать исходные PGN/notes | KEEP |
| GB-48 | global engine Fair Play mask | При неизвестном active human или настоящем human live скрыть ALL eval/score/PV/hints | KEEP SAFETY |
| GB-49 | выбор карточки local в Play | Не сбрасывать online/current local game до явного start CTA | KEEP |
| GB-50 | challenge cancellation and rematch from finished | Подтверждение серверного исхода, no repeated blind POST | KEEP |

## C. REVIEW — работа с завершённой партией (RV-01…RV-38)

| ID | Сейчас | Предлагаемая целевая судьба | Решение |
| --- | --- | --- | --- |
| RV-01 | GameReviewWorkspace QSplitter of 3 columns | 2 главные колонки board+moves; stats conditional/collapsible | REDESIGN |
| RV-02 | review_board fixed size and alignment | Auto-fit square, не миниатюра из-за пустых metrics | REDESIGN |
| RV-03 | header white/black/results+date/opening | Short, source backed, tooltip for full; no invented «Соперник» | KEEP/SHRINK |
| RV-04 | «К партиям» | К игре/К партиям/К анализу по маршруту входа | ADD CONTEXT |
| RV-05 | Global «← Назад» | Не дублировать логику без явного назначения | KEEP |
| RV-06 | «Экспорт PGN» | Вторичная видимая команда, no PGN rewrite | KEEP |
| RV-07 | 3-column metrics, always 360–370 px | Без анализа **не отнимать место** у board, с анализом foldout | COLLAPSE |
| RV-08 | phase_selector all/opening/middle/end | Только когда реально есть фазовая метрика; иначе нейтрально | CONDITIONAL |
| RV-09 | «Я / Соперник» side_metrics_summary | Сохранить per-side accuracy/source, errors, blunders, coverage; collapse | KEEP/MOVE |
| RV-10 | official Lichess accuracy | Whole-game official only, no false local/phase mixing | KEEP |
| RV-11 | local Stockfish accuracy | Explicit local provenance and completeness | KEEP |
| RV-12 | phase_summary and local phase metrics | No fake percentage if phase not measured | KEEP/CONDITIONAL |
| RV-13 | MoveTable SAN for finished game | Всегда виден, список с собственным scroll и selected ply | KEEP |
| RV-14 | Graph click/ply and keyboard arrows/wheel | Выбор полухода синхронизирует позицию board/history | KEEP |
| RV-15 | AdvantageBar vertical | Только при valid eval and safety, рядом с board | CONDITIONAL |
| RV-16 | selected_move_summary and detailed events/PV | Показать when valid, под move table / expand | KEEP/SHRINK |
| RV-17 | ReviewGraph across bottom | При data; no huge empty chart, resize/fold, compact default | REDESIGN |
| RV-18 | graph_mode evaluation/Δ | Сохранить в расширенном Review, not compact library graph | KEEP |
| RV-19 | error_markers_toggle | Optional per-move markers, не постоянная россыпь значков | KEEP/CONDITIONAL |
| RV-20 | legend for each symbol | Only if markers on; otherwise hide huge explanatory row | REDESIGN |
| RV-21 | «Проанализировать Stockfish» | Главная CTA когда no analysis and safe, manual job | KEEP/PRIORITIZE |
| RV-22 | «Повторить анализ Stockfish» | Secondary when existing complete, no auto-run | KEEP/CONDITIONAL |
| RV-23 | «Открыть на Lichess для запроса анализа» | Только проверенный Lichess URL и ясно внешний переход; не заявлять API-заказ | MOVE SECONDARY |
| RV-24 | «Разобрать мои ошибки · 0» | Не primary если N=0; только candidates N>0 and fresh safe | CONDITIONAL |
| RV-25 | «Предыдущая / Следующая ошибка» | Только при реально verified candidate list | CONDITIONAL |
| RV-26 | «Дополнительные сведения» tabs | Сохранить Разбор/События/Заметки, по раскрытию; no duplication | KEEP COLLAPSED |
| RV-27 | ephemeral controls undo/reset branch | Отдельное study state, оригинальный PGN не меняется | KEEP |
| RV-28 | training exercise check/hint/skip/next | Только в учебном режиме и без premature spoilers | KEEP |
| RV-29 | training repeat without hint | Только после завершённого упражнения | KEEP |
| RV-30 | training «Вернуться к разбору» | Restore selected game+ply, no lost context | KEEP |
| RV-31 | save notes/history | Existing notes non-destructively, no false cross-account | KEEP |
| RV-32 | no analysis / invalid-only payload | Нет chart/обманчивых zero/empty 3rd column, CTA source aware | REDESIGN |
| RV-33 | live human/unknown safety mask | ALL eval/accuracy/PV/hints hidden including accessibility/tooltips/cache | KEEP SAFETY |
| RV-34 | source coverage and classification | Missing ≠ zero; classification is observation, not proven error cause | KEEP |
| RV-35 | analysis job queued/running/complete/fail | Inline status, don't block Qt; no duplicate single-job | ADD PRESENTATION |
| RV-36 | import PGN/FEN/ephemeral study | Respect active safety and origin route; no source hallucination | KEEP |
| RV-37 | returns to game/library/analysis | Source-aware back with selected ply and scroll preservation | ADD ROUTE |
| RV-38 | library mini-graph vs full ReviewGraph | Library graph always clean per owner; Review can keep optional markers/Δ | KEEP DISTINCT |

## D. Состояния: coverage для будущей приёмки

| ID | Показанный сценарий / test fixture | Требование |
| --- | --- | --- |
| ST-01 | AI active, timed, few/many moves | доска максимум, оба часа, история и правильные доступные команды |
| ST-02 | Human active, timed | ничья/возврат/сдаться видны, no engine hints |
| ST-03 | Human/AI active without normal clock | no fake 00:00; компактная корректная подпись |
| ST-04 | Focus on/off, sidebar open/closed | фактическое увеличение доски при доступном пространстве, restore |
| ST-05 | Waiting position / pending move / server ACK | blocked unsafe, status honest |
| ST-06 | Incoming draw/takeback / cancel premove | контекстные Принять/Отклонить/Отменить не спрятаны |
| ST-07 | Live game while browsing history | one-click return to latest, no stale move POST |
| ST-08 | Connection lost/429/auth A→B | last safe board, no stale foreign info/action |
| ST-09 | Finished by mate/resign/draw/timeout | same board+freeze clocks+two CTA; no popup/auto SF |
| ST-10 | Finish saved pending/failed | disable Review until committed exact PGN, brief accurate status |
| ST-11 | AI/human rematch, local again | explicit action, new ID and validated game context |
| ST-12 | Local free board with unsaved moves | undo/flip/marks, explicit new/reset confirmation |
| ST-13 | Local explicit position evaluation with human active elsewhere | Fair Play fails closed and clears hints |
| ST-14 | Review no data / no saved positions | large board+moves+manual analysis CTA, no blank graph/stats |
| ST-15 | Review Lichess official accuracy only | accurate provenance; no fake per-position graph |
| ST-16 | Review complete SF eval & classification | real metrics+graph+eligible exercises |
| ST-17 | Review partial/invalid/mate/gaps | truthful N/A, no fake ±999; graph gaps preserved |
| ST-18 | Review safety pending/blocked or A→B | metrics/PV/tooltips/accessibility all masked |
| ST-19 | Review existing error exercise | check/hint/skip/next/repeat/navigation retained |
| ST-20 | 1120×640/1280×720/1672×941 and DPI 100/125/150 | no clipped CTA, hit-test, readable board+clock, QSplitter geometry |

## E. Функции будущих итераций, НЕ добавлять в текущий layout

| ID | Идея | Решение |
| --- | --- | --- |
| GF-01 | Автоматический персональный тренер в live game | ЗАПРЕЩЕНО при человеческой онлайн-партии |
| GF-02 | Разбор собственных мыслей до просмотра оценки | PARKED: будущая отдельная UX-функция Review/Обучение |
| GF-03 | Полная диагностика слабостей по нескольким играм | PARKED: Анализ/Прогресс |
| GF-04 | Дебютный репертуар и подготовка к человеку | PARKED: Анализ, строго pregame |
| GF-05 | Рекомендации уроков по ошибкам партии | PARKED: Обучение |
| GF-06 | Адаптивная учебная программа | PARKED: Обучение/Прогресс |
| GF-07 | AI-тренер, голос, аватар | PARKED до доказательного шахматного ядра |
| GF-08 | Оценка ходов в активной human Lichess партии | НЕЛЬЗЯ / FAIR PLAY |
| GF-09 | Автоматический Stockfish после финального хода | НЕ ДЕЛАТЬ, нарушает утверждённый explicit choice |

**После будущего выполнения:** для каждого GB/RV/ST ID занести actual SHA, маршрут, обработчик, тест и Windows image/geometry evidence. Если элемент скрыт — указать новую точку доступа, не удалять функцию молча. Пока UI-решения не согласованы с владельцем, весь реестр имеет статус **PROPOSED / NOT TESTED**.
