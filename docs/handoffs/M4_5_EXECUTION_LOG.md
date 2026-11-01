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
| E | Выборочное уточнение, тактические мотивы | DONE | `366635f7a67ca6cb3c12f5b323fb3d64d0dc8c57`, PR #30 | schema v14; 243 pytest PASS; fake UCI MultiPV/cancel/cache PASS |
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

## Этап E — selective review и event detector registry (2026-11-01)

- Ветка `codex/m45-batch-analysis` → draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`; code commit `366635f7a67ca6cb3c12f5b323fb3d64d0dc8c57`. Schema `14`.
- Добавлен полный расширяемый каталог M4.5 event codes. Реализованные, candidate-only и deferred коды помечены раздельно; tactical motifs без надёжных детекторов зарегистрированы как deferred и никогда не генерируются. Pure detectors сохраняют played move, best move/PV и CP/mate evidence; severity и event/tag — независимые оси.
- `event_tags` — M:N таблица с detector version/confidence/evidence. Повторный `reclassify_analysis_events(run_id)` строит метки только из сохранённых assessments и является идемпотентным. Старые результаты не удаляются.
- Selective review планирует не более 12 ходов на партию с приоритетом mate/тяжёлых потерь и общим явным лимитом. `CompletedGameAnalyzer.review_selected_positions()` оценивает только before/after позиции выбранных ходов, ограничивает бюджет до 300k nodes, Threads=1, Hash=128 MB, MultiPV 1..3; требует positive safety callback перед запуском и повторно перед каждым поиском, поддерживает cancel. Реальный UCI не открывается автоматически.
- Результаты альтернатив сохраняются в additive `analysis_alternatives` (schema v14), связанной с существующими positions/run; не перезаписывают обычный analysis. Review cache key включает binary SHA, line revision/hash, plies, nodes, MultiPV и policy; cache hit не создаёт процесс. Статистика и report detail предпочитают полный run partial selective review, чтобы частичный проход не уменьшал покрытие.
- Регрессии: полный каталог/deferred motifs, event confidence и multi-tags, отсутствие ложных fork/pin labels, лимиты planner, real SQLite tag persistence и reclassification no-op, миграции v12/v13→v14 с backup/integrity и сохранением tags/assessments, fake UCI MultiPV alternative persistence/cache reuse/cancel/fail-closed, complete-run preference для stats. Полный локальный Windows/Python 3.12.10 набор — **243 PASS**; compileall и diff-check PASS.
- Реальный Stockfish, OAuth, Lichess, пользовательские игры/SQLite — NOT RUN/NOT TOUCHED. Часть широкого каталога остаётся зарегистрированной, но deferred; candidates не засчитываются как подтверждённые ошибки/конверсии. Windows CI для SHA `1958142…` этапа D был SUCCESS; CI SHA этапа E будет проверен после push.
- Следующий этап F: проверить происхождение/CC0 условий chess-openings, добавить нормализованное EPD matching с транспозициями и provenance phase bounds; считать opening stats по цвету без автоматического диагноза.

## Этап F — справочник дебютов и фазы с provenance (2026-11-01)

- Ветка `codex/m45-batch-analysis`, draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30) → `main`. Code commit `928846588f996b9505435160c26857d0b3aa71c8`; docs checkpoint follows. Этап E Windows CI: [run 69](https://github.com/feshakomissarov-prog/chess/actions/runs/41279136027) — SUCCESS на SHA `743b8ee8937a4d66787a8a23b9a762705cadf3c8`.
- В пакет приложения добавлены upstream TSV `a.tsv`–`e.tsv` из `lichess-org/chess-openings` commit `712a58be746c143f4ab935b68acc50f4bac813eb`. В `opening_data/NOTICE.md` закреплены CC0 1.0, источник и SHA-256 набора. Ветка данных включает ECO/name/PGN; код или GPL/AGPL материалы не копировались. `pyproject.toml` включает TSV/NOTICE в package data.
- Новый offline `OpeningMatcher` строит индекс по нормализованным 4-полевым EPD с legal-en-passant semantics; транспозиции совпадают, проход использует последнее совпадение. Справочник не скачивается приложением и не читается из пользовательского архива. Backfill только явный и bounded (до 100 партий); сетевых/движковых вызовов нет.
- Schema v15 добавляет только `opening_matches` (FK на существующую revision, источник набора, версия алгоритма, ECO/name/family/variation, matched ply, видимый флаг конфликта, timestamp). Lichess `games.opening_eco/opening_name`, PGN и линии не перезаписываются. Миграция v14→v15 проверяет backup, `integrity_check` и сохранение фактов.
- Lichess `division` остаётся источником фаз с приоритетом. Если серверных границ нет, импорт/отдельный bounded repair вычисляет явно маркированный fallback v`cc-phase-material-v1` только по развитию фигур/размену ферзей/остаточному материалу; числовых порогов по номеру хода нет. Если признаков недостаточно, фаза остаётся unknown. Для UI доступен accessor границ с provenance.
- `StatisticsService.opening_statistics()` агрегирует партии по opening, owner color, game source и выбранному analysis source, с числом партий, оценённых собственных ходов, ошибками и средним opening CPL. Открытие-фильтр в общей статистике также видит текущие derived opening matches. Запросы агрегируют в SQLite, не загружают историю ходов целиком.
- Регрессии: реальная локальная загрузка закреплённого набора/checksum, матч последнего известного дебюта без перезаписи Lichess, EPD-транспозиция и legal EP, сохранение конфликта, opening statistics по цвету/source, fallback provenance/приоритет division, v14→v15 backup/целостность/сохранение данных.
- Локальные Windows Python 3.12.10: openings + migrations + deep SQLite audit + Lichess + event detectors — **134 collected**, последние полные прогоны PASS; дополнительно финальный `test_m45_openings.py` после изменения source-aware агрегата — **5 PASS**. `compileall` и `git diff --check` — PASS. Полный suite этапа I ещё впереди. CI F SHA ещё не запускался.
- Личная SQLite/PGN, OAuth, живой Lichess и реальный Stockfish — NOT TOUCHED/NOT RUN. UI для ручного запуска bounded backfill и показа provenance будет завершён на G; массовый анализ не запускался.
- Следующий этап G: библиотека партий и паспорт партии — добавить отображение локального/официального дебюта, статуса/охвата и source, phase-aware оценку, evidence-события с переходом к позиции до хода.

## Этап G — библиотека и паспорт партии (2026-11-01)

- Code checkpoint SHA `0443dd43c4b79fbe5f0eb00ed61f66c579c647ff`; ветка `codex/m45-batch-analysis`, draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30) → `main`. Следующим docs commit будет указан полный SHA.
- Строки библиотеки показывают дату/результат/соперника, локальный match или исходные Lichess ECO/name, конфликт названий, официальную Lichess accuracy владельца при наличии, собственные inaccuracy/mistake/blunder, scored/total positions, run status и analysis source. Выборка остается пагинированной и использует bounded SQL subqueries, без UCI.
- Паспорт показывает official/derived opening отдельно, конфликт, source/method phase boundaries, official accuracy, локальную точность как недоступную без утверждённой формулы, coverage/status, ошибки владельца по фазам и тяжести, а также события и evidence JSON в tooltip. Double-click на событии/ошибке открывает позицию **до** хода. Evaluation graph рисует настоящие phase boundaries и серую unknown-зону.
- Statistics UI содержит явное bounded действие для первых 25 партий без opening matches: локальный CC0 match и phase fallback; фоновый поток корректно сопровождается при закрытии; сети и движка нет. В результате показываются sample, цвет, evaluated own moves и source-aware error rate.
- Official division и материализованный fallback обновляют phase поля сохранённых классификаций текущей ревизии, не трогая PGN/ходы. Fallback backfill ограничен до 100 и только завершёнными standard games.
- Qt/SQLite regressions: list coverage/errors/source, SQL фильтр источника, phase graph attributes, event evidence и before-ply navigation, explicit background bounded action, close during worker; миграция/opening regressions повторно прошли. Локальный Windows/Python 3.12.10 offscreen набор (openings, migrations, SQLite audit, window, Lichess, event detectors) — PASS; новый close-worker тест и opening suite — PASS; compileall/diff-check PASS. Полный набор и CI ожидаются на I.
- Opening match backfill UI и список работают только по явному действию. 10k+ performance benchmark и ручной визуальный GUI review — ещё не выполнялись; следующий H проверяет aggregation/profiles, I — нагрузку/стабилизацию.
- Пользовательская SQLite/PGN, OAuth, реальный Lichess/Stockfish — NOT TOUCHED/NOT RUN.
- Следующий этап H: SQL recent/historical profile, period comparisons, average accuracy/error denominators, phase/opening/source breakdown and evidence links.
