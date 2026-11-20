# PUZZLE-UX-02 — журнал выполнения

Спецификация: [`PUZZLE_UX_02_LICHESS_STYLE_TRAINING_2026_11_19.md`](../specs/PUZZLE_UX_02_LICHESS_STYLE_TRAINING_2026_11_19.md)  
Регламент: [CC-VERIFY-01](../process/VERIFICATION_PROTOCOL.md)  
Draft PR: [#51](https://github.com/feshakomissarov-prog/chess/pull/51)  
Ветка: `codex/puzzle-ux-02-lichess-training-2026-11-19`

## Итог

Реализована отдельная постоянная страница тренировки в стиле Chess Coach с переиспользованием `BoardView` и существующей SQLite. Кодовый HEAD, проверенный L3: `7afc30f66aa2dc569aca6cea1d687212206f3080`. Документационный коммит этого журнала не меняет поведение; кодовый SHA остаётся тем же.

Внешний read-only источник использует `GET /api/puzzle/batch/{angle}` с `puzzle:read`, ограниченной выборкой и обработкой форматов/ошибок. Отправка решения в Lichess и `puzzle:write` не реализованы. Локальные попытки и синхронизированная официальная активность хранятся/показываются раздельно. Схема дополнена аддитивной миграцией v27; рабочая база владельца не открывалась и не изменялась.

## Выполненные требования

| ID | Результат | Доказательство |
| --- | --- | --- |
| PUZ-01 | Выбор темы/угла подключён к запросу; доступны смешанная тема, дебют, миттельшпиль, эндшпиль, связка, вилка, жертва, сквозной удар, открытое нападение, защитный ход, незащищённая фигура, матовые темы, типы эндшпилей, длина и превращения. | UI/API tests в L3; список в `src/chess_coach/ui/pages/puzzle_training.py`. |
| PUZ-02 | Новые задачи запрашиваются через ограниченный read-only GET; разбор источников и ответы API покрыты тестами, в том числе ошибками/лимитами. | Fake transport и unit tests; код L3 SHA. Живой аккаунт/API не использовался. |
| PUZ-03 | Переиспользован `BoardView`: ЛКМ для хода, ПКМ для пользовательских стрелок/выделений; ориентация определяется стороной решения. | Qt tests и native captures ready/user-marks. |
| PUZ-04 | Реализованы последовательность ходов, ответы соперника, неверный ход, подсказка, показ решения, повтор/пропуск и mate-in-one альтернативы. | Pure reducer и Qt сценарии; full pytest. |
| PUZ-05 | Правая карточка задачи и последние попытки; replay доступен из локальной истории. | Изолированный синтетический SQLite fixture; screenshot `solved` показывает предыдущую попытку. |
| PUZ-06 | Локальные результаты отдельно от официального рейтинга/активности; нет автоматической записи в Lichess. | Store/API tests; только GET, отсутствие POST-пути и `puzzle:write` в этом сценарии. |
| PUZ-07 | Фоновые запросы, отмена/устаревшие ответы и offline/error paths обрабатываются отдельно от интерфейса; во время live human game помощь движка не добавлена. | Unit/window tests, offline smoke и full L3. Live OAuth/revoke не проверялся. |
| PUZ-08 | Сохранены навигация, существующая доска/история и другие экраны; схема мигрируется добавочно. | Full pytest и полный visual-master workflow в L3. |
| VISUAL | Сняты реальные Qt PNG на Windows при 100%, 125%, 150%, в ready/user-marks/solved/mistake-hint. | Визуальная проверка всех трёх ready captures и solved 100%; артефакт ниже. |

## Проверки CC-VERIFY-01

### L1 — быстрые адресные проверки

Во время реализации запускались `git diff --check`, `python -m py_compile` для изменённых файлов и адресные pytest по reducer, источникам, API, хранилищу, UI и окну (в том числе негативные случаи). На промежуточных итерациях замечания тестов и геометрии исправлялись до финального прогона. На владельческой машине Qt/pytest не запускались.

### L2 — тематическая/native регрессия

Нативный Windows Qt capture для тренажёра на Windows Server 2025 / Python 3.12.10 / Qt 6.12.0:

| Scale | Размер окна (логический) | Физический PNG |
| --- | ---: | ---: |
| 100% | 1672×941 | 1672×941 |
| 125% | 1280×720 | 1600×900 |
| 150% | 1120×640 | 1680×960 |

На каждой шкале сохранены 4 состояния: `ready`, `user-marks`, `solved`, `mistake-hint`. Скрипт проверил выход окна/кнопок за границы и минимальный размер доски. Визуально проверены компоновка всех ready PNG и solved на 100%: тема и действия видимы, на компактных высотах правая панель прокручивается, предыдущая попытка отображается после решения. Это синтетический offline fixture, а не данные владельца.

### L3 — финальный exact-head

Итоговый [Windows tests run #633](https://github.com/feshakomissarov-prog/chess/actions/runs/44603997659) на точном code SHA `7afc30f66aa2dc569aca6cea1d687212206f3080`:

- `test`: PASS — `1183 passed in 710.60s (0:11:50)`; `python -m compileall -q src tests` PASS; offline `MainWindow` smoke PASS.
- `visual-evidence`: PASS — полный master capture и 12 native Qt PNG тренажёра (4 состояния × 3 DPI), артефакт загружен.
- `L1/L2 Home chart review`: PASS — 62 targeted tests и компактная native Home capture.
- `Spec contract (fast)`: PASS.
- Итог: все обязательные jobs run #633 завершены PASS.

**PNG артефакт:** [visual-master-7afc30f66aa2dc569aca6cea1d687212206f3080](https://github.com/feshakomissarov-prog/chess/actions/runs/44603997659/artifacts/14570307752) (включает `puzzle-training/scale-1`, `scale-1.25`, `scale-1.5`; всего 134 файла). Сохранённые копии для просмотра также доступны в каталоге `visual-evidence` job artifact; в рабочем дереве файлы артефакта не добавлялись.

## Ограничения и OWNER ACCEPTANCE REQUIRED

- Требуется ручная проверка владельцем на его Windows: OAuth-согласие только с `puzzle:read`, получение задач с настоящего Lichess, выбор тем/сложности и реальное поведение доски на привычном дисплее/DPI.
- Не проверялись живые Lichess ответы, аккаунтная история и отзыв OAuth. Автотесты использовали fake transport и изолированную временную SQLite; native capture использовал synthetic puzzles/attempts.
- Проверялись CI runner DPI и размеры окна по таблице, а не физические мониторы владельца. На 125%/150% правая карточка может требовать прокрутки.
- PR остаётся Draft; merge не выполнялся. Приложение на компьютере владельца не запускалось; пользовательская SQLite/секреты не читались и не менялись.



## 20.11.2026 — независимый итог PR #51 после исправлений §7

- **Код:** `078369cc5422b44d2948aa2807227a632f3cb1de`; **рабочий workflow-only commit:** `2fc3b821de4f40af31db9d248ab59ccbf9a9bd18`. Предыдущее утверждение выше «итоговый HEAD `7afc30f`» — **исторический первый L3**, после которого Codex исправил найденные в §7 ошибки.
- **Focused L1/L2:** [run #44634443759](https://github.com/feshakomissarov-prog/chess/actions/runs/44634443759) на `078369cc`: 29 target tests + native puzzle Qt 100/125/150, **PASS**.
- **Полный L3 автоматически, без действий владельца:** `Windows tests` на PR transition `ready_for_review` (один полный прогон вместо ручного `workflow_dispatch`). [run #44645115437](https://github.com/feshakomissarov-prog/chess/actions/runs/44645115437), точный `2fc3b82`: **1198 passed in 577.69s**, compileall, offline-window smoke, native visual и Home review **ALL PASS**.
- **Визуальные доказательства:** [artifact #14588796342](https://github.com/feshakomissarov-prog/chess/actions/runs/44645115437/artifacts/14588796342); просмотрены native screenshots `ready`, `account-switch`, `solved-after-hint`, `compact ready`. Это только synthetic/offline, не рабочий профиль.
- **Незакрытые мелкие UX/метрические пункты** под существующими PUZ-07/PUZ-06, без дублирующих IDs — [спецификация §8](../specs/PUZZLE_UX_02_LICHESS_STYLE_TRAINING_2026_11_19.md). Смена аккаунта обновляет историю, но пока визуально оставляет старую позицию; локальный счётчик в некоторых режимах ограничивается 100. Важное различие: сохранение результата уже привязано к исходному account, **нет доказанной записи в чужой профиль**.
- **OWNER NOT VERIFIED:** реальные Lichess/SQLite/physical DPI, предыдущие Qt safety gates. Владелец разрешил merge при положительной независимой проверке; личный визуальный тест выполнит отдельно. После merge необходимо сначала обновить **локальные исходники, которые запускает ярлык**, не затрагивая SQLite и QSettings.

**Стоимость CI:** один full post-fix L3 после одного тематического PR run; не запускались дополнительные полные прогоны ради обновления Markdown.
