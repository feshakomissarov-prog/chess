# M4.5 — журнал исполнения и восстановление контекста Codex

**Дата создания:** 2026-11-01. **Статус:** IN PROGRESS. **Спецификация:** [M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md](../specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md) v2.0. **Эпик:** [#28](https://github.com/feshakomissarov-prog/chess/issues/28).

## Источник истины для возобновления

1. Прочитать [AGENTS.md](../../AGENTS.md), [ТЗ M4.5](../specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md), этот журнал и [PROJECT_STATUS](../PROJECT_STATUS.md).
2. Проверить GitHub PR, branch, git status, HEAD SHA, текущую схему SQLite и реальные результаты CI. Не делать выводов только из текста старой записи.
3. Продолжить с первого НЕЗАКРЫТОГО этапа; не начинать проект заново и не терять промежуточные commits.
4. Не использовать личную SQLite, OAuth, реальный Stockfish/массив личных партий без соответствующего разрешения и backup. Автономно выполнять код и fake/synthetic tests.

## Контрольные точки (обновляет Codex после каждого завершённого этапа)

| Этап | Цель | Статус | SHA/PR | Тесты/ограничения |
| --- | --- | --- | --- | --- |
| 0 | Аудит схемы v9 и договоры о метриках | DONE | `6bf0078fff22977869f3be786c19b492e4ece39a`, PR #30 | 212 pytest PASS; compileall PASS; baseline main `6c3692c`; личные данные NOT TOUCHED |
| A | Аддитивные миграции, coverage | DONE | `d4c61816424fd61f181411b815f1d0956f4d7b94`, PR #30 | schema v10; 215 pytest PASS; v9 backup/integrity/rollback PASS |
| B | Частичные и поздние оценки Lichess | DONE | `d2da1445ad3573df07af7c7fb6c7d2eccf07c2d1`, PR #30 | 218 pytest PASS; fake export/429 PASS; real network NOT RUN |
| C | Быстрая очередь Stockfish, recent-first | DONE | `c918e5e49ad531a13530851f5e9bdb7672f709a8`, PR #30 | schema v11; 228 pytest PASS; fake UCI/lease/fair-play PASS |
| D | Метрики, severity, события и возможности | DONE | `f9ae45f1e407099350f13103f416594e20f49d1b`, PR #30 | schema v12; 233 pytest PASS; synthetic CP/mate/WDL/source/opportunity tests PASS |
| E | Выборочное уточнение, тактические мотивы | TODO | — | — |
| F | CC0 дебюты, фазы игры | TODO | — | — |
| G | Паспорт партии, список и графики | TODO | — | — |
| H | Агрегаты, точность, текущая и историческая статистика | TODO | — | — |
| I | Нагрузочные тесты, Windows CI, итоговый PR | TODO | — | — |

## Формат следующего чекпойнта

Заполнить одним новым разделом (не стирать прежние):
- UTC дата, рабочая ветка, HEAD SHA, PR URL и base branch.
- Последний завершённый этап и результат; следующие конкретные файлы/классы и первый action.
- Фактически запущенные команды и исход (PASS / FAIL / NOT RUN), CI job/URL; сравнение с baseline.
- Изменения SQLite version/DDL, проверка backups/integrity/rollback и личные данные: NOT TOUCHED, если это действительно так.
- Известные дефекты/потенциальные регрессии/решения, blocked and deferred.
- Риски user data, engine fair play, лицензии, performance benchmark.
- Критерий возобновления после смены сеанса.
- Финальное событие: review state PR, completed vs partial, next manual tests.

## Зафиксированные решения владельца

- Анализ всей истории, но **актуальный профиль только по недавним партиям**; старые используются для исторического тренда.
- Сначала quick pass по всему архиву, затем targeted review только важных позиций; один локальный Stockfish, без глубокого анализа ради глубины.
- Широкая, расширяемая taxonomy шахматных событий, множественные метки + confidence; повторная классификация без полного движкового прохода.
- В библиотеке видны **общие агрегаты**: средняя точность (с источником и покрытием), среднее число ошибок на партию, counts неточностей/ошибок/грубых ошибок.
- Полный паспорт каждой партии как основа сводной аналитики. По периоду, дебюту, фазе, цвету и источнику отдельные сравнимые срезы.
- SQLite миграции минимальные и аддитивные; не копировать большие таблицы туда-сюда.
- Одно большое задание Codex, без ожиданий владельца для рутинных технических решений; зафиксированные checkpoints, один PR без самослияния.
- Сначала M4.5, затем M5 голос, затем M6 ИИ-тренер. Генерацию упражнений не реализовать сейчас.

## Стартовое состояние кода

До M4.5 M0–M4 в main, схема SQLite v9, предыдущий основной Windows CI 212 PASS. Исполнитель должен перепроверить это на актуальном SHA, а не доверять исторической записи.

## Этап 0 — аудит и контракт (2026-11-01)

- Ветка: `codex/m45-batch-analysis`, база `origin/main` `6c3692c827294386fd070da9f9e7f086429320d9`; draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30) → `main`. Audit commit: `6bf0078fff22977869f3be786c19b492e4ece39a`.
- Карта схемы: `GameRepository` в `src/chess_coach/lichess/repository.py`, `SCHEMA_VERSION=9`. Основные связи: `games` → `game_revisions` → `game_moves`/`game_positions`; `analysis_runs` → `position_assessments`/`move_classifications`; аккаунты связаны через `account_games`. `game_phase_boundaries` уже хранит provenance. `games` и нормализованные линии остаются источником истины; существующие строки и revisions не перестраивать.
- Подтверждён дефект поздних Lichess eval: `import_archive_record()` строит новый `lichess-pgn` run только при наличии хотя бы одной оценки и пропускает импорт, если уже существует любой `status='complete'` для той же линии. Неполный набор размечается `complete`, а последующая дозагрузка не объединяется. Исправление: хранить фактический partial/complete coverage и делать idempotent union оценок той же линии/источника; не заменять известную позицию NULL и не смешивать source/configuration.
- Контракт: `analysis_runs` и позиции остаются versioned facts. Покрытие выводится по line_hash/run/source/position count; отдельную coverage-таблицу не создавать без измеренной причины. Персистентная очередь требует минимальной additive-таблицы: текущие `analysis_runs` описывают результат, не lease/retry/pause job. Для множественных тегов/событий нужна компактная additive-модель с FK на существующие ходы/позиции; DDL — после backup/rollback тестов.
- Fair-play gate сохраняется перед каждым запуском/возобновлением и при изменении активных игр; неизвестное состояние — fail-closed. OAuth и пользовательская БД в этой задаче не читаются/изменяются.
- Этап 0 PASS: `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q` — exit 0; 212 тестов (подтверждено `--collect-only`). `python -m compileall -q src tests` — exit 0. База/PGN владельца — NOT TOUCHED, Lichess/OAuth/Stockfish — NOT RUN. Baseline относится к `main` SHA выше.
- Известные baseline риски: одиночный analyzer использует до 100k nodes/position и не подходит как неограниченный batch default; выбор анализа в статистике чувствителен к provenance/settings. Следующий этап — Stage A с синтетическими SQLite fixtures и additive DDL. GitHub Windows CI для audit commit проверяется отдельно; финальный CI ветки ещё NOT RUN.

## Этап A — база массового анализа (2026-11-01)

- Code commit: `d4c61816424fd61f181411b815f1d0956f4d7b94` (schema `10`); текущий draft PR: [#30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`.
- Добавлены только `analysis_jobs` (персистентная очередь с revision/hash/engine/config/pass/status/priority и уникальным idempotency key) и read-only `analysis_coverage` view. Существующие `games`, PGN, revisions, moves, positions, notes, analysis runs/assessments не перемещались и не перестраивались. Покрытие учитывает позицию ply 0 и сопоставимые пары pre/post.
- Новые SQLite-тесты на временных синтетических файлах: свежая схема и view; upgrade v9→v10 с непрерывностью старых party/note/analysis facts; консистентный `.pre-m3-v10.bak` + `integrity_check`; намеренная ошибка после DDL с rollback user_version/schema/data и успешное повторное восстановление. Старые тесты миграции обновлены под версию 10.
- Проверки после изменений: `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q -ra` — exit 0, 215 тестов; `python -m compileall -q src tests` — PASS; `git diff --check` — PASS. Сначала обнаружились три устаревших ожидания `user_version=9` и ошибка теста на ply index; все исправлены, повторный полный набор зелёный.
- Личная SQLite/PGN/OAuth — NOT TOUCHED; реальные Lichess/Stockfish — NOT RUN. Windows CI для нового SHA ожидается отдельно; миграция на владельческом профиле не запускалась.
- Следующий этап B: исправить partial/late Lichess eval hydration в существующем импортере на тех же синтетических repository fixtures, без OAuth/API вызовов.

## Этап B — Lichess eval hydration (2026-11-01)

- Code commit: `d2da1445ad3573df07af7c7fb6c7d2eccf07c2d1`; PR #30 → `main`.
- Частичные `lichess-pgn` runs теперь имеют статус `partial`; поздние оценки одной линии idempotent-merge в существующий run, не создавая дубликат и не стирая score с более высокой глубиной. `complete` означает покрытие каждой post-move позиции. Частичные runs видны в query/detail/statistics и используют отдельный source без подмешивания Stockfish.
- Добавлен bounded (`1..100`) recent-first missing-eval selector и явные `ArchiveSync.refresh_missing_evaluations()` / `LichessService.refresh_missing_archive_evaluations()`. Они используют только уже сохранённую OAuth-сессию при явном вызове, один официальный export endpoint на игру, не запускаются при открытии экрана и не двигают full-backfill/incremental cursors. UI-кнопка будет добавлена на этапе G.
- Регрессии: partial→late-full completion; повторная идентичная загрузка не создаёт run; сохраняется более глубокий старый score; coverage summary учитывает partial; targeted refresh выбирает только неполную партию, не меняет курсор; fake HTTP 429 прекращает цикл и не маскируется/не повторяется. Всё на temp SQLite/fake client.
- Проверки: полный `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q -ra` — exit 0, 218 PASS; `python -m compileall -q src tests` — PASS; `git diff --check` — PASS. Windows Python 3.12.10 локально. Реальные Lichess/OAuth и Stockfish — NOT RUN; личная база NOT TOUCHED.
- Следующий этап D: извлечь versioned move metrics и event candidates из пары before/after assessments (typed mate/CP, правильная перспектива чёрных, unknown без доказательств), расширить сохранение, pure detector registry и regression fixtures.

## Этап C — очередь быстрого анализа (2026-11-01)

- Code commit: `c918e5e49ad531a13530851f5e9bdb7672f709a8`; PR #30 → `main`.
- Схема поднята до v11 аддитивно: `analysis_jobs` расширена lease owner/expiry и ссылкой на результат; при обнаружении промежуточной v10-таблицы поля добавляются без пересоздания/переноса строк. Проверен v10→v11 backup/integrity и сохранение queued job.
- Реализованы recent-first selection для завершённых standard games, bounded быстрый профиль (по умолчанию 20k nodes, предел 30k, Threads=1, Hash ≤128 MB), стабильный ключ job (game/revision/hash/pass/engine/config), atomic claim, lease heartbeat/expiry recovery, stale revision rejection, pause/resume/cancel/failure states и связь с analysis_run. Legacy `standard` cache key сохранён без нового `analysis_pass`, чтобы не терять ранее рассчитанные оценки.
- `BatchAnalysisRunner` использует один явный локальный UCI process последовательно на несколько jobs; не скачивает/не выбирает бинарник сам. Перед каждой job требуется внешний safety snapshot, только `True` пропускает job. False/unknown/exception ставит job на паузу до создания engine. Появившаяся человеческая партия останавливает следующую job. Stop во время текущей позиции не сохраняет незавершённый run; cancel гасит очередь, pause сохраняет последующие queued jobs. Lease recovery тестирует перехват после имитации падения/expiry.
- Fake UCI / temp SQLite регрессии: idempotent enqueue, recent-first, reuse одного engine и результата, legacy cache reuse, fail-closed unknown/active opponent, появление human game между партиями, interrupt+cancel, pause+resume, lease expiry, stale revision, миграции. Реальный Stockfish — NOT RUN.
- Полный Windows/Python 3.12.10 headless тестовый набор: `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q -ra` — exit 0, 228 PASS; compileall — PASS; diff-check — PASS. Личная SQLite/OAuth/Lichess — NOT TOUCHED/NOT RUN. CI для текущего SHA проверяется после публикации.

## Этап D — версионированные метрики ходов и события-кандидаты (2026-11-01)

- Ветка `codex/m45-batch-analysis` → draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`; code commit `f9ae45f1e407099350f13103f416594e20f49d1b`. Schema `12`; documentation checkpoint будет отдельным следующим commit.
- Аддитивно созданы `move_metrics` и `analysis_events`; `games`, PGN, позиции и v1 `move_classifications` не перестраивались. Запись метрик/событий атомарна вместе с `analysis_runs`; coverage view сообщает измеренные ходы и candidate events.
- `move_metrics.py` рассчитывает CP-loss строго с перспективы ходившей стороны, сохраняет White-POV CP и отдельные mate scores, выставляет `cp_loss=null` при mate discontinuity/неполных оценках, severity v2 отдельно от legacy label. WDL рассчитывается только при наличии обеих typed scores через python-chess SF model; версия библиотеки включена в `wdl_model`. Accuracy ходов не выдумывается и остаётся null без утверждённой formula_id. Официальная Lichess game accuracy отдается отдельно с source и выбранным цветом.
- Возможность наказать ошибку соперника сохраняется как `missed_opportunity/candidate`, не verified: требуется comparable потеря на ходе соперника и ответе пользователя; evidence связывает ход соперника, ответ и best move из позиции ДО ошибки, если PV доступен. Это вход в selective review, а не подтверждённая тактическая классификация.
- Старые analysis runs получают v2 facts лениво при запросе паспорта из сохранённых оценок; это bounded на одну партию и не вызывает UCI. Миграция v11→v12 тестирует backup/integrity и сохранность старых party/analysis rows; v9/v10 rollback/upgrade проверки обновлены до v12.
- Полный локальный Windows/Python 3.12.10 headless набор: `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q -ra` — exit 0, **233 PASS**. Целевые migration/metrics tests после последней правки PV — **11 PASS**; `compileall` и `git diff --check` — PASS.
- Личная SQLite/PGN, OAuth, живой Lichess и реальный Stockfish — NOT TOUCHED/NOT RUN. WDL — обозначенная python-chess SF model, не официальный Lichess WDL; opportunity остается candidate и не учитывается как verified conversion. Windows CI для нового SHA — проверить после push.
- Следующий этап E: ограниченный selective review pass и расширяемый pure detector registry с synthetic positive/negative fixtures, без движкового повторного прохода при классификации.
