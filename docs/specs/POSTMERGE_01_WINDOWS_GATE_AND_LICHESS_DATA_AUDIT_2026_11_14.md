# POSTMERGE-01 + DATA-01 — один короткий цикл после PR #47: Windows gate и проверка аналитики Lichess

<!-- verification-contract: docs/process/VERIFICATION_PROTOCOL.md -->

**Дата:** 14.11.2026 · **Статус:** утверждённое ограниченное ТЗ к передаче Codex, реализация ещё НЕ начата  
**Нормативное правило:** [AGENTS.md](../../AGENTS.md) → [CC-VERIFY-01](../process/VERIFICATION_PROTOCOL.md) → [шаблон ТЗ](./_TASK_TEMPLATE.md).  
**Baseline:** `main` на `394ad979f2507183ccd2cc56c074efcfd43fed41`, после merge [#47](https://github.com/feshakomissarov-prog/chess/pull/47) (code `4f5fa663`, merge `2ab3dde`) и [#48](https://github.com/feshakomissarov-prog/chess/pull/48) (merge `394ad979`).  
**Source of truth:** [единый реестр PR #47](PR47_CONSOLIDATED_REVIEW_REGISTER_2026_11_12.md), [execution log](../handoffs/STAB_01_EXECUTION_LOG.md), [дорожная карта](../ROADMAP.md). Исторические ошибки НЕ перенумеровывать.  
**Работа в новом Draft PR от текущего main; нет самостоятельного merge.** Для текущего файла создан отдельный документационный Draft PR. Исполнитель должен продолжить в нём (или в одной согласованной кодовой ветке от свежего main), а не открывать серию мелких PR.

## 0. Короткое решение по трём блокам и ответственность

| Блок | Что уже сделал ChatGPT | Что остаётся Codex | Что остаётся владельцу |
| --- | --- | --- | --- |
| **1. Постоянный регламент** | **ГОТОВО В MAIN:** PR #48 слит. Корневой `AGENTS.md`, `docs/process/VERIFICATION_PROTOCOL.md`, шаблон `_TASK_TEMPLATE.md`, `spec-contract.yml`, документационный gate и workflow-конкурентность доступны в main. CI PR #48: [spec contract PASS](https://github.com/feshakomissarov-prog/chess/actions/runs/43579688681) и [Windows PASS](https://github.com/feshakomissarov-prog/chess/actions/runs/43579688686). | Просто **соблюдать** L1→L2→L3 и контракт каждой новой спецификации. Не переделывать процесс второй раз. | В идеале добавить [готовую инструкцию](../process/CHATGPT_PROJECT_INSTRUCTIONS.md) в настройки проекта ChatGPT «Шахматы»: GitHub не может установить персональные инструкции за пользователя. |
| **2. Финальный Windows gate** | Принял явное согласие владельца по UI и смержил PR #47. **Найден реальный post-merge CI failure**, описан ниже, несмотря на зелёный CI #594 для PR HEAD. | Одно адресное исправление нестабильной геометрии/инициализации Home + тест. После исправления один полный финальный Windows CI. | **Не нужно** заново фотографировать всю страницу Home и проверять уже одобренные цвета/графики. P0 ≤1 с / Lichess/Qt safety остаются отдельно отмеченными, без навязанного запуска на личном ПК. |
| **3. DATA-01 / полнота Lichess** | Прочитал текущую реализацию. Архив уже запрашивает `accuracy=true,evals=true`; отдельный ready-analytics job получает **опубликованные** per-game JSON с лимитом **25 запросов/UTC-день**, до 10 за порцию. Счётчики 953, 40 и 244 **не эквивалентны**. Подтверждений массовой потери анализа нет. | Сначала **локальный read-only аудит структуры и семантики счетчиков на тестовой SQLite**, поясняющие KPI/diagnostic tooltip/отчёт; не писать новый массовый sync и не включать network audit без явного основания и разрешения. | Ничего дополнительно сейчас. Когда будет готов инструмент и устранён Qt safety gate — отдельное разрешение для **read-only** проверки собственной SQLite или добровольно предоставленной безопасной копии. |

**Принцип:** владелец сообщил «интерфейс по сути готов, с Lichess вроде всё нормально». Это означает **не раздувать** новые задачи. Важнее проверить надёжность текущей ветки и правдивость диагностики, чем строить очередной dashboard/reimport. Никакой новой AI-отладки, UI redesign или DIST-01 в этом задании.

## 1. POSTMERGE-01 — реальный сбой CI на main после merge #47

### Подтверждённые данные

- До merge: [Windows run #594](https://github.com/feshakomissarov-prog/chess/actions/runs/43588751885), code `4f5fa663181f8dfbc2d775b7ef6b6955eb66b54f`: 1161 pytest PASS, L1/L2 Home visual PASS, full native visual PASS.
- После merge: **[Windows run #43596085647](https://github.com/feshakomissarov-prog/chess/actions/runs/43596085647)** на `main`, merge SHA `2ab3dde656db0f78ba3bcfbbe46c9c0371bcd896`: **FAIL** в `L1/L2 Home chart review → L2 native 100% and compact Home chart captures`.
- Точный traceback в `scripts/capture_visual_master.py`, функция `wait_home`, около строки 340: **`AssertionError: Home card 1 exceeds its scroll viewport width: 640 > 611`**. Лог до этого успешно снял 1672×941 и часть 1280×720 кадров; сбой случился при очередном capture после смены размера/state. Зависимые полные `test` и `visual-evidence` jobs **SKIPPED**, не PASS.
- Это **доказанный отказ CI на свежем main, но не доказательство постоянного дефекта при каждом запуске**. Возможна зависимость Qt layout от порядка show/resize/reparent/polish/timing, cached minimum size/sizeHint. RCA установить по измерениям. **Не** утверждать, что само слияние логически изменило рабочий код.
- Использовать **существующий стабильный ID `R47-H-01 / R47-QA-02`** из единого реестра; добавить запись `POSTMERGE-01` как событие/evidence, **не создавать дубликат старого дефекта**. Сохранить старый CI PASS и дополнительно текущий RED.

### Цель и ожидаемое поведение

**PM-01 / P1** — все recent game cards `Home` после первоначального рендера, перехода Home→Play→Home, resize narrow↔wide, 100%, 125% и 150% **должны занимать ширину ≤ viewport** с реальным `QRect`. Длинная строка соперника читаема/эллипсизируется с подсказкой, не выталкивает viewport. При компактной высоте скролл идёт внутри соответствующей зоны, карточки доступны. Большой Home и текущий дизайн не менять.

**Диагностика/исправление:**
1. Сначала сравнить code/base PR #47 с merge/main; в изолированном Windows CI воспроизвести сценарий; собрать `width`, `minimumWidth`, `sizeHint.width`, `minimumSizeHint.width`, QRect родителя/viewport, layout direction, Qt scale, момент после show/resize/queued updates. После reproduce выбрать **причину**, а не hardcoded 611px/640px.
2. Добавить L1 targeted воспроизведение компактного layout: после первого 1672×941 состояния последовательно 1280×720, Home→Play→Home и повторный resize, проверяя bounded geometry после обработанного event loop (без искусственной длительной sleep).
3. Исправить только source layout/minimum width/Qt geometry ordering. Не **удалять или ослаблять** утверждение `card.width <= viewport.width`, не фильтровать неудобные размеры, не маскировать ошибку увеличением тестового монитора, не запускать Qt у владельца.
4. Сохранить доступность кнопок и текстов, не ломать уже принятую `HIMG-09.EDGE` или mini-board.
5. Проверить два последовательных прогона **быстрого целевого сценария** в изолированном Windows environment перед L3. Если воспроизведение intermittent — логировать частоту и bounding размеры, не объявлять `FLAKE` без RCA.

**PM-02 / P2** — после исхода PM-01 проверить CI workflow: `home-chart-review` как `needs:` для остальных jobs не должен маскировать результаты; отсутствие полных jobs на failed main **нельзя считать зелёным 1161 PASS именно для main**. Перед сдачей корректно разнести статусы `PASS/FAIL/SKIPPED` в отчёте. Не изменять архитектуру CI без нужды; CC-VERIFY-01 из main уже решает отмену устаревших PR runs и docs-only pushes.

## 2. DATA-01 — доказательная сверка Lichess без ненужной загрузки

### Что код уже делает — НЕ реализовывать повторно

**Источник:** `src/chess_coach/lichess/api.py`, `lichess/archive.py`, `application/dashboard.py`, `infrastructure/sqlite/archive_store.py` и `jobs.py`.

- `stream_user_games(...)`: синхронизация завершённых партий через Lichess с `evals=true,accuracy=true,opening=true,division=true` — **архив уже пытается получить доступную аналитику**. Это не только PGN без оценок.
- `export_game_json(...)` для готовой аналитики: `GET /game/export/{gameId}` с `accuracy=true,evals=true`; **не заказывает новый серверный анализ**.
- `refresh_ready_analytics` ограничивает одну порцию `limit <= 10`; `reserve_lichess_analysis_request` использует default `daily_limit=25` на UTC-день; 429/retry/account generation/backoff не обходить.
- `dashboard.py` **разделяет**: число distinct завершённых партий; партии с любыми сохранёнными позиционными оценками; партии с Lichess source evals; Stockfish evals; официальную `games.lichess_accuracy_white/black` выбранной стороны; локальную `analysis_game_accuracy` на current-line runs. Комбинированная средняя по доступным партиям выбирает **официальную Lichess accuracy → Stockfish → расчёт по серверным оценкам**, по одной оценке на партию.
- `lichess_analysis_backfill_summary.checked` — число строк backfill с `accuracy_checked_at` или `evals_checked_at` (отдельно перепроверенные игры), а не число всех игр с анализом. `accuracy` в этой summary — число backfill-игр с опубликованной точностью выбранной стороны и статусом; это не глобальное количество точностей Home.
- Исторический пользовательский screenshot: 3325 completed, 2063 с любыми оценками, 953 с Lichess evals, 1823 со Stockfish evals, 40 со встроенной **официальной** Lichess accuracy, 244 отдельно проверено, 204 без дополнительных серверных данных, 146 ожидают повторения. **Это снимок даты 14.11.2026, не константы и не доказательство нынешнего API-покрытия.** Источники evals пересекаются; числа не складывать.

### DATA-01.A / P1 — read-only coverage audit (обязателен)

Создать безопасный **offline diagnostic** для явно выбранного `account_id`; сначала использовать **временную синтетическую SQLite** из имеющихся тестовых fixtures и переиспользовать существующий repository/read-model, не создавать вторую SQLite-теневую модель. Предпочтительный интерфейс — маленькая чистая функция `audit_lichess_coverage(repository, account_id)` + ограниченный CLI/отображение в «Диагностика операций», **не требующие Lichess token**. Подробный UI экран по умолчанию **не нужен**.

Аудит обязан выдавать в одном выводе с понятными знаменателями:

1. Distinct completed games в выбранном `account_id`, даты покрытия (если timestamps есть), и число игр с валидным `source_game_id`/Lichess.
2. `games_with_any_position_scores`, `games_with_lichess_position_scores`, `games_with_stockfish_scores`, пересечение источников и отдельно `no_scores`, `partial`, `complete` на **актуальной линии**. Не считать устаревшую/несовпавшую `line_revision/line_hash` текущей.
3. `official_lichess_accuracy_player_side` (valid 0–100 по сохранённой стороне в `account_games`), наличие значений только противоположной стороны, invalid/out-of-range/unknown side, `local_stockfish_accuracy_count`, `local_server_based_accuracy_count`, `combined_distinct_accuracy_count`; объяснить, что `40` — знаменатель одного вида метрики, не «только 40 оценённых игр».
4. Блок ready-backfill: `total/checked/accuracy/evals/no_server_data/pending/errors/retry_waiting/requests_today/retry_at/last_checked_at` **с ясным указанием, что denominator отличается от общего анализа**. `204 no_server_data` говорит только «в конкретных проверках не найдено новых данных», а не «партия на Lichess вообще не анализировалась».
5. Вывести `scope`, `UTC snapshot time`, `source/schema version`, `all_time / optional last_90d` отдельно, не смешивать в одной средней. Везде `N / denominator`, не выдавать неполные выборки за весь архив.
6. **Приватность:** stdout/log/CI artifact по умолчанию — **только агрегированные числа без username, токенов, game IDs, PGN/FEN, токенных URL, абсолютных домашних путей**. Для расследования конкретной строки — только локальный приватный вывод на разрешённой стороне, не в публичный PR.
7. Использовать безопасное SQLite read mode/transaction `query_only`; не создавать migration/job/события и не писать в рабочий DB; если текущий `Repository._connection()` вызывает миграцию или dirty-операции, **не использовать его для диагностики на живой БД**. Отделить audit-query от initialize/migration. Для фактически работающего DB/WAL нужна согласованная snapshot/backup процедура перед чтением — нельзя применять `immutable=1` к активной базе и игнорировать WAL.

**Приёмка:** tiny fixture с 6–12 играми: белые и чёрные, overlapping Lichess + Stockfish scores, Lichess accuracy только у противоположной стороны, 0 как валидная accuracy, partial vs full, archive without evals, изменённая line revision, archived stale run, queued/retry/no_data; числа expected рассчитаны вручную. Тесты не вызывают `http/engine` и не изменяют исходную SQLite. Performance-цель — запросы bounded и индексируемые на 3k+ записей, не обещать «<1s» для полного незнакомого DB без замера.

### DATA-01.B / P2 — пользовательские подписи (минимально)

После доказательства структуры данных привести **только объяснение** существующих KPI/страницы фоновых операций к корректной терминологии (без редизайна Home):

- `Lichess 88.6% (40)` → tooltip/accessibility `Официальная точность Lichess: 40 партий выбранной стороны; это не количество всех игр с оценками`.
- `Lichess с оценками: 953` → `Партии с доступными позиционными оценками Lichess; источники Lichess/Stockfish могут пересекаться`.
- Ready analytics `Проверено 244/3325` → пояснение `число проверок наличия уже опубликованной per-game аналитики, НЕ число проанализированных партий`. Дневной лимит — локальный бюджет именно fetch, не заказ нового сервера.
- Все значения реальные и scoped; UI compact, строки не увеличивать до огромных popup; source provenance доступен через компактные tooltips и «Диагностика операций». Использовать существующий стиль и доступность screen-reader.
- **Если после аудита окажется, что и существующая UI терминология понятна, не менять код ради косметики.** Лучше короткая достоверная памятка в приложении и read-only диагностика.

### DATA-01.C — условная дальнейшая проверка серверной полноты (НЕ автоматическое исполнение)

Внешняя сверка «у Lichess проанализировано X, локально Y» **не подтверждается одним локальным агрегатом**. Требует отдельного разрешения владельца на его данные и доступное API. Только при фактическом конфликте идентификаторов/сохранённой accuracy:

- Сначала локальный anonymized audit и выбор **нескольких** подтверждённых проблемных game IDs в приватном окружении, затем авторизованные точечные `GET` в рамках уже существующего лимита/429/backoff; **не делать массовый повторный fetch всех 3k+** без доказанной необходимости.
- Ни один `POST` на заказ компьютерного анализа; не нарушать fair play и не обращаться к live human games для показа engine eval.
- Запрещено удалять старые данные, менять original accuracy/PGN или накатывать миграцию «на всякий случай». Если найден баг синка — отдельное точное RED/FIX/CI/owner разрешение внутри того же ограниченного задания.
- Если всё согласуется — закрыть DATA-01 как `LOCAL COVERAGE AUDITED` с оговоркой `EXTERNAL RECONCILIATION NOT RUN`, а не обещать 100% идентичность серверу.

## 3. Действия владельца (минимум)

1. **Сейчас:** больше ничего запускать не нужно; визуальный Home владелец уже принял. Никаких повторных скриншотов 13 экранов, повторной установки, Dr.Web exclusions.
2. **После адресного исправления CI:** посмотреть одну компактную Home-карточку при обычном использовании, **если приложение уже безопасно запущено**. Не просим создавать второй процесс или запускать local pytest, учитывая `R47-SAFE-01`. На его машине причина 4 прежних аварий `Qt6Core.dll` не установлена.
3. **Для DATA-01:** отдельное добровольное решение: либо пока достаточно **синтетического/локального offline-аудита логики**, либо разрешить **только read-only проверку** предварительно созданной безопасной копии пользовательской БД (после Qt safety-проверок; без публикации личных данных). Текущая фраза «вроде ок» **не считается согласием открыть реальную SQLite агенту**.
4. **P0:** Home < = 1000 мс от первого видимого кадра до ранее сохранённых достоверных метрик на своей БД остаётся **NOT MEASURED**, даже если «на глаз быстро». Не запускать расширенный перформанс-пилот без отдельного согласования; можно отложить без притворного PASS.

## План проверок и стоимость CI

**CC-VERIFY-01** уже в `main`; не придумывать иной процесс. Время — ориентиры; `docs-only` PR не требует дорогих Windows runs и не является новым code evidence.

### Уровень 1

**Адресные проверки (L1).**

- **POSTMERGE:** `git diff --check`; `python -m py_compile src/chess_coach/ui/pages/home.py scripts/capture_visual_master.py`; `python -m pytest -o addopts= -q tests/test_visual_master_final_regressions.py tests/test_ui_pages.py` **или точные node IDs** нового geometry RED-теста. Изолированная native Windows с одним переходом 1672×941→1280×720; не весь 100/125/150 visual suite.
- **DATA:** `python -m pytest -o addopts= -q tests/test_dashboard.py tests/test_m46_lichess_analytics_backfill.py` либо **только новые** `tests/test_lichess_coverage_audit.py`; `python -m py_compile <изменённые файлы>`. Вся сеть/движок mocked, тесты создают временную SQLite. CI проверяет отсутствие user identifiers в stdout.
- **RED→GREEN:** старое assertion card ≤ viewport остаётся строгим. Для счетчиков независимый ожидаемый ручной dataset с перекрывающимися источниками.
- **Стоимость:** L1 только затронутые тесты; при невозможности native Windows targeted runner собрать тематический минимальный сценарий, не выполнять full regression на каждый двухстрочный фикс.

### Уровень 2

**Тематическая регрессия (L2).**

- Изолированный native Qt Windows: повторный Home 1672×941, компакт 1280×720 и один высоко-DPI случай; 3 recent карточки, длинный соперник, Play→Home, после scroll третья карточка, предотвращённый overflow, текущая корректность графиков и popup.
- Data fixtures: 0/1/3k games (последний — **синтетические** масштабные данные), два аккаунта, отсутствие токена/интернета, реальные backfill статус-кейсы `queued / has_data / no_server_data / partial / retry / 429`, смена профиля, safety gate. Уточнить denominator и цвет.
- Проверить, что PR #48 workflow отменяет старые PR runs и Markdown-only изменения не запускают полный Windows suite, **но** не отключает обязательный L3. Никаких изменений шифрования, SQLite migration, Fair Play или Lichess authentication без нового явного объёма.

### Уровень 3

**Финальный exact-head CI (L3).**

- **Один** окончательный Windows pytest, compileall, offline-window smoke, native visual matrix для нового **code SHA**. Если Windows job на PR не дошёл до test из-за home-chart-review — исправить сначала L1/L2, потом проверять полный L3.
- В отчёте: `code SHA`, `L1 evidence`, `L2 real Qt captures`, `L3 exact-head run URL / job statuses`, `DATA audit expected/actual synthetic counts`, privacy statement, explicit `OWNER acceptance pending`.
- На повторных L3 записывать, почему targeted тест не обнаружил регрессию, и усилить coverage. **Не запускать** 1100+ тестов после каждой косметической правки.
- Основная SQLite и локальная среда владельца не участвуют в CI. Реальные P0/Lichess/Qt safety — самостоятельные OWNER gates, не синтетический PASS.

## Критерии приёмки и доказательства

| ID | Проверяемое требование | Доказательство | Статус при составлении |
| --- | --- | --- | --- |
| **CC-VERIFY-01** | Постоянные правила и проверка ТЗ в main; старые CI runs отменяются | [PR #48](https://github.com/feshakomissarov-prog/chess/pull/48), merge `394ad979`, быстрый spec gate PASS | **DONE in main** |
| **PM-01 / R47-H-01** | Ни одна Home card не превышает width своего viewport после переключения | Native geometry repeated transitions; red [main run](https://github.com/feshakomissarov-prog/chess/actions/runs/43596085647), green targeted/L3 SHA | **CONFIRMED RED on main — FIX NEEDED** |
| **DATA-01.A** | Агрегированный read-only отчет различает 953 evals и 40 official accuracy, backfill checked, пересечения, scoped counts | 6–12 fixture games, synthetic 3k, hand-verified counts, no writes/network | **NOT IMPLEMENTED** |
| **DATA-01.B** | Значения в UI подписаны понятными источниками и знаменателями; без изменения цветовой палитры | Existing compact tooltip / page screenshot + unit test | **VERIFY / MINIMAL FIX** |
| **DATA-01.C** | Не выполняется неразрешённый серверный массовый запрос; при отсутствии конфликта ничего повторно не загружается | Mock HTTP counters == 0, local-only report | **EXPLICITLY OUT OF SCOPE by default** |
| **R47-GATE-01 / TD-06** | Сохранённые Home метрики ≤1000 мс на рабочем профиле | Только пользовательская безопасная разрешённая покадровая проверка | **OWNER NOT MEASURED** |
| **R47-SAFE-01** | Нет неразрешённых локальных Qt/Dr.Web запусков | Контракт границ / risk acknowledgement | **OPEN** |

**Финальное решение:** после успешного exact-head L3 и независимого ревью Codex предоставляет результат и останавливается; **no auto merge**, без DIST-01/инсталлятора, без новой массовой синхронизации. Новые реальные дефекты сначала сопоставлять с существующими TD/R47 ID; не дублировать старые находки.


## Итог выполнения и независимой проверки — 14.11.2026

**Эта запись закрывает первоначально незавершённые критерии только в пределах изолированного CI**; исторические статусы в разделах выше сохранены как исходные требования. Владелец просмотрел UI и разрешил merge PR #49 при успешной независимой проверке; независимое чтение кода и [Windows CI #599](https://github.com/feshakomissarov-prog/chess/actions/runs/43608145277) выполнены.

| ID | Итог | Проверенные доказательства |
| --- | --- | --- |
| `CC-VERIFY-01` | **PASS** | L1: 62 target tests, L2: real native Qt transitions и DPI 100/125/150%, L3: 1167 pytest, compileall, offline smoke, native visual **все PASS** на точном **code SHA `97d2a8d74cf06438f6d6f5e6e33a63cfb2ca9d8d`**. |
| `PM-01 / R47-H-01 / R47-QA-02` | **FIXED / CI VERIFIED** | До компоновки `_DashboardGameCard` получает начальный размер по `recent_scroll.viewport().width()`, geometry assertion `card.width() <= viewport.width()` сохранён; L2 повторяет Home→Play→Home, narrow↔wide. Баг `640>611` на прежнем main восстановлен в истории. |
| `DATA-01.A` | **PASS на синтетических snapshot, OWNER REAL DB NOT AUDITED** | Модуль audit без импорта sqlite3 на application-слое, отдельный CLI через `mode=ro`, `query_only`, read transaction и aggregate result без account/game IDs, пересечения/validity/backfill/current-line fixture. |
| `DATA-01.B` | **PASS** | KPI/detail tooltip объясняют разные знаменатели official accuracy, Lichess evals, Stockfish и checked backfill; UI layout не менялся. |
| `DATA-01.C` | **NOT RUN / NOT AUTHORIZED** | Реальный запрос Lichess и реальная SQLite не выполнялись; 953/40/244 — объяснение сохранённых ранее чисел по коду, а не внешний live reconciliation. |
| `R47-GATE-01 / TD-06 / R47-SAFE-01` | **OWNER / OPEN** | Home ≤1с на реальной SQLite не измерен, локальные Qt6Core.dll события не исследованы, запрещённые местные smoke не запускались. |

**Docs-only коммиты после L3:** пересмотрены статусы в `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`, `docs/TECHNICAL_DEBT.md` и [едином реестре §J](PR47_CONSOLIDATED_REVIEW_REGISTER_2026_11_12.md) без правок `src/`, `tests/` или исполняемых scripts; по CC-VERIFY-01 повторный full L3 не требуется. Обязательна только быстрая проверка spec-contract на финальном документальном HEAD. **Merge только по ранее данному явному согласию владельца.**
