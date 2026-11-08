# Chess Coach · ANALYSIS-HOME-UX-01 · контроль сохранности функций

**08.11.2026. ДИЗАЙН, не изменение кода.** Источник при составлении — source PR #44, src/chess_coach/ui/pages/analysis.py, window.py, position_setup.py, текущие Background Operations и Progress. До будущего исполнения повторить проверку по exact SHA после текущего §15. Связь с подробным документом: ../specs/ANALYSIS_LANDING_UI_REWORK_CODEX_SPEC_2026_11_08.md.

**Легенда:** KEEP — функция остаётся; MOVE — та же функция по другому пути; REDESIGN — меняется представление; ADD — новый UI маршрут/отображение; DISABLED — будущая видимая карточка без click handler; PARKED — позже; NOT TESTED — **все** пункты до реализации. Любое скрытие старого кнопочного элемента должно сопровождаться точной новой точкой доступа.

## A. Реализованное и целевое (AL-01…AL-20)

| ID | Ранее в исходниках | Целевое состояние и переход | Операция |
| --- | --- | --- | --- |
| AL-01 | «Выбрать партию» на landing | **«Разбор партии» → «Выбрать материал» → единый Review** | REDESIGN |
| AL-02 | «Исследование дебютов → Открыть статистику» | верхняя правая карточка, существующая статистика Прогресса | KEEP + LABEL |
| AL-03 | нет Weakness landing | нижняя левая **«Диагностика слабостей»**, disabled «В разработке» | ADD DISABLED |
| AL-04 | нет Opponent landing | нижняя правая **«Подготовка к сопернику»**, disabled «В разработке» | ADD DISABLED |
| AL-05 | два заголовка «Анализ» | один существующий заголовок shell | HIDE DUPLICATE |
| AL-06 | три карточки 1×3 | четыре 2×2, одна primary action area на карточку | REDESIGN |
| AL-07 | archive game selection | внутри вложенного выбора, тот же saved-game Review | MOVE |
| AL-08 | вставка PGN/FEN, parse, validation | nested «Разбор партии», без постоянного поля landing | MOVE / KEEP |
| AL-09 | Windows «Выбрать файл PGN…» | nested PGN mode, тот же existing picker | MOVE / KEEP |
| AL-10 | PositionSetupWidget: ручная мышиная доска | nested редактор позиции (фигуры, turn, castling, EP, reset/clear/open), без overlay | MOVE / KEEP |
| AL-11 | BatchAnalysisDialog via «Массовый анализ» | один Batch control-plane в **глобальном «Фон»** | MOVE / KEEP |
| AL-12 | pause/resume/cancel/status/queue/retry | остались в общем Background Operations | KEEP |
| AL-13 | engine thresholds/depth/nodes/settings | «Настройки» — конфигурация, не duplicative queue | KEEP / ROUTE |
| AL-14 | progress/статистика собственных дебютов | существующая проверенная статистика, корректный return | KEEP |
| AL-15 | global shell Back/Фон/sidebar Lichess | без изменения маршрутов 6 разделов и current palette | KEEP |
| AL-16 | general background/sync state | два честных компактных header indicators, shared across pages | ADD PRESENTATION |
| AL-17 | Review/FEN/PGN/Study/AI/Stockfish | одна система и сохранённые source workflows, не переписывать | KEEP |
| AL-18 | external PGN/FEN ephemeral mode | не импортировать в личную SQLite автоматически | KEEP |
| AL-19 | редактор на скрине перекрывает элементы | вложенная Qt компоновка без пересечений 100/125/150% | REDESIGN |
| AL-20 | no Stockfish launch on opening Analysis | отсутствие автоматической работы, account/fairplay gating | KEEP |

**Примечание:** новая карточка «Разбор партии» должна открыть **работающий** вложенный chooser, не пустой маршрут. Поэтому реестр AL-07…10 не закрывается, пока мы не согласуем следующий экран. Сохранение существующих бэкендов не значит, что финальный UX уже реализован.

## B. Подробная семантика новых элементов

| ID | Что делает и как проверяется |
| --- | --- |
| AL-H01 | Левый прогресс показывает конкретный понятный охват (например N партий с валидной оценкой из M завершённых импортированных по этому аккаунту), а не одновременно sync % и analysis % |
| AL-H02 | Правый прогресс показывает current Stockfish job X/Y лишь при доказанном знаменателе, иначе idle/unknown |
| AL-H03 | Кнопка «Фон» ведёт к существующей единой очереди, Lichess backfill и archive status, не запускает work автоматически |
| AL-H04 | При смене аккаунта A→B старая очередь/percent не отображается у B |
| AL-H05 | Header indicators реализованы в shared shell, не повторяются отдельными Analysis widgets |
| AL-V01 | Четыре карточки одного размера, четыре правильных icons/status, две enabled/two disabled, native Windows visual match |
| AL-V02 | На 1120×640 и DPI 150% нет clipped buttons/overlap/недоступных disabled-подсказок |
| AL-V03 | Клик по пустой части не вызывает другой handler; клавиатура и screen-reader только для настоящих действий |

## C. Будущее: намеренно НЕ реализовано в данном UX цикле

| ID | Будущий модуль | Где появится | Пока |
| --- | --- | --- | --- |
| AL-F01 | Opening Explorer: ECO, дерево, транспозиции, варианты, sample-size и подтверждённые сравнения | внутри «Исследование дебютов» | PARKED |
| AL-F02 | Глубокая диагностика слабостей: симптомы vs предполагаемые причины, конкретные партии, точечные упражнения | «Диагностика слабостей» | PARKED |
| AL-F03 | Подготовка к человеку по публичному Lichess архиву: ник, фильтры, варианты, evidence | «Подготовка к сопернику» | PARKED |
| AL-F04 | Дебютный репертуар с повторением и учебным планом | «Обучение» | PARKED |
| AL-F05 | Интерактивный AI-тренер, диалог о собственных решениях, до/после движка | Разбор / Обучение | PARKED |
| AL-F06 | Автоматический серверный cloud-анализ Lichess | только отдельный документированный API/safety этап | PARKED |
| AL-F07 | Любые engine-подсказки в live human Lichess game | НИГДЕ | NOT ALLOWED |

## D. Порядок обновления реестра Codex позднее

Для **каждого AL-01…20** сохранить: exact source SHA, UI address, прежний и новый handler, наличие теста Qt / fake-stream / keyboard, verified Windows image и статус **IMPLEMENTED / TESTED / PARTIAL / BLOCKED / NOT IMPLEMENTED**. Для перенесённой функции указать реальную новую точку доступа; нельзя «убрать кнопку» и тихо потерять feature. Для AL-F явно подтверждать отсутствие fake доступных страниц. **Ничего не помечено выполненным заранее.**
