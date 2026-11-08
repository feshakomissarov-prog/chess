# ARCH-UX-01 · REVIEW-FIX-03 — независимая проверка RF2-01…RF2-04

**Дата:** 04.11.2026  
**Проверенный код:** [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), `codex/arch-01-modular-refactor`, SHA `97785aa8a2d6697d06c0e012480f26b8103a104f`; база `main`, не слит.  
**Автотесты:** [Windows CI #247](https://github.com/feshakomissarov-prog/chess/actions/runs/41887011647), **completed / SUCCESS**, точный проверенный SHA; 666 тестов PASS, compileall и isolated offline window.  
**Результат независимого чтения реализации:** **пока не принимаю** — два оставшихся несовпадения в «Прогрессе». Исправления RF2-01 (часы), RF2-02 (durable next-due scheduler), RF2-04 (даты) и ядро RF2-03 присутствуют; **не перезапускать их и не повторять ARCH-UX-01/RF-01…RF-15**. Сделать **только RF3-01 и RF3-02** ниже (и необходимые тесты). Реальные пользовательские аккаунты/PGN/SQLite/токены не трогать.

Нормативные основания: [REVIEW-FIX-01 v1.2](ARCH_UX_01_REVIEW_FIX_01.md) RF-09/RF-12/RF-15, [REVIEW-FIX-02](ARCH_UX_01_REVIEW_FIX_02.md) RF2-03, [ARCH-UX-01 v1.1](ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md) и [AGENTS.md](../../AGENTS.md).

## RF3-01 · P1 — «Прогресс» должен учитывать **оба** источника оценок Lichess

**Кодовое доказательство:** в `src/chess_coach/ui/analytics_dialogs.py` комбобокс `StatisticsDialog.analysis_source` содержит `("Lichess", "lichess-pgn")` (около строки 459). Поэтому выбор «Lichess» фильтрует **только** `analysis_runs.source='lichess-pgn'`. Новые оценки `lichess-json`, сохранённые фоновым backfill, исключаются из статистики, хотя каталог `src/chess_coach/infrastructure/sqlite/games.py` для «Lichess · оценки ходов» использует объединение `('lichess-pgn','lichess-json')` из `domain/analysis_scope.py`.

**Что сделать:** сохранить `lichess-pgn` и `lichess-json` как отдельные прозрачные provenance, но UI «Lichess» должен означать **union по любому текущему пригодному CP/mate** из двух источников. Если добавляются отдельные подпункты PGN/JSON, их подписи должны быть однозначны. Применить grouped-source predicate во всех `StatisticsService` SQL queries и вкладках, где сейчас `ar.source=?` (summary, selected run, страницы партий, фазы, события/critical moments, coverage, фильтры); **нельзя** просто заменить значение в QComboBox на `"lichess"` — тогда `ar.source='lichess'` даст ноль, потому что такой physical source отсутствует. Использовать один проверенный domain helper `analysis_scope.py` и безопасную параметризацию; не делать `OR` в обобщённом SQL без правильных скобок. `stockfish` и старые source-specific фильтры не ломать.

**Приёмка:** временная SQLite с Lichess-PGN-only, Lichess-JSON-only, обеими, Stockfish-only, official-accuracy-only, metadata-only, stale-line и несколькими аккаунтами. «Мои партии» и «Прогресс» для выбора «Lichess» возвращают **одинаковые distinct game IDs и количество** в одном scope, включая JSON-only. Отдельный источник/текущий run и локальный анализ не смешиваются. Проверить хотя бы summary + список + breakdown/фазы + Qt выбор фильтра.

## RF3-02 · P1 — «Анализ есть у N» в «Прогрессе» должен означать реально оценённые позиции

**Кодовое доказательство:** `StatisticsService._filter_sql` теперь правильно использует `current_scored_analysis_exists()` для фильтра `analyzed=yes/no`. Но `src/chess_coach/lichess/statistics.py::summary` по-прежнему делает:
```python
latest = "... ROW_NUMBER() ... FROM analysis_runs ar ... WHERE ... ar.status IN ('complete','partial') ..."
analysis_count = db.execute(selected + " SELECT COUNT(*) FROM chosen", ...).fetchone()[0]
```
В `chosen` попадает запуск со статусом `complete`/`partial` **без CP/mate**, например classification-only или пустая/техническая оценка; он увеличивает `summary['analysis_games']`, которое прямо показывает `StatisticsDialog`: «Анализ есть у {analysis_games}/{games} партий» (около строки 862). Это противоречит уже исправленным Home и «Мои партии». Дополнительно `ROW_NUMBER` может выбрать самый новый completed run **без оценок** вместо более старого current-revision scored run и показать нулевой охват при существующих позиционных оценках.

**Что сделать:** `summary['analysis_games']` и `summary['analysis_coverage']` рассчитывать из **distinct current-revision games с хотя бы одним usable CP/mate**, без review-only jobs. Конкретные move/classification statistics могут сохранять семантику выбранного run — не выдумывать их из чужого source; но показывать ясно, когда подтверждённого scored run для деталей нет. При нескольких текущих runs со scores один game считается **один раз**, выбранный run для score-coverage имеет scoring preference, а не только `status='complete'`. Согласовать правила с `current_scored_analysis_exists`; если сейчас `analysis_games` используется другими функциями, поправить тех пользователей, не меняя определение обратно. Избежать дублирующих полноархивных PGN-парсингов/UI-thread SQL.

**Приёмка:** синтетическая временная БД: 1) classification-only `complete` без scores, 2) metadata-only, 3) официальный accuracy-only, 4) scored `partial`, 5) scored `complete`, 6) одновременно пустой новый `complete` и старый scored `partial` на **той же текущей линии**, 7) две scored runs разных источников на одной партии, 8) stale revision, 9) две учётные записи. Проверить `analysis_games`, `analysis_coverage`, `scored_positions`, `summary(analyzed=yes/no)`, `StatisticsDialog` текст и равенство source-filtered subsets с `search_games`; 0/0 = «н/д», не фиктивные 0%. При safety revoke не раскрывать analytics значений.

## Завершение

1. Продолжить **тот же кодовый draft PR #44**; перед началом сверить актуальный HEAD (на момент ревью `97785aa8…`). Не переписывать уже исправленное и не менять `main`.
2. Маленькие адресные изменения с тестами; обновить `docs/handoffs/ARCH_UX_01_EXECUTION_LOG.md` и `docs/PROJECT_STATUS.md` в кодовой ветке — отдельная запись REVIEW-FIX-03.
3. Полный offscreen Python 3.12 pytest, `compileall`, `git diff --check`, `--offline` и `--manual-connect` isolation; новый **exact-HEAD hosted Windows CI SUCCESS**. Старый #247 относится только к предыдущему коду.
4. Владелец отдельно проверит реальные Lichess/Stockfish, личную SQLite, звук, физическую геометрию/DPI/игровые часы после независимого ревью. **Не менять реальные данные без разрешения.** Оба PR (#44 code, #45 docs) оставить draft, никакого merge.
