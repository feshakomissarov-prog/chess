# PR #30 — второе независимое ревью M4.5, корректировки R10–R16

**Дата:** 2026-11-01. **Проверенный HEAD:** `5872eb207fd11701f383e94db12427fc03abdded`. **PR:** [#30](https://github.com/feshakomissarov-prog/chess/pull/30), `codex/m45-batch-analysis` → `main`, draft, NOT MERGED. **Статус:** REQUEST FOLLOW-UP FIXES / **NO-GO для первого реального массового запуска и merge**, пока не устранены R10–R13. **Предшествующее ревью:** [R0–R9](PR30_M45_DEEP_REVIEW_FIX_SPEC.md). **Общая спецификация:** [M4.5](../specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md); [execution log](../handoffs/M4_5_EXECUTION_LOG.md).

## 0. Состояние проверки и правила работы

Независимо сверены фактические исходники и тесты на SHA выше: `batch_analysis.py`, `analysis.py`, `repository.py`, `statistics.py`, `move_metrics.py`, `event_detectors.py`, `phases.py`, `archive.py`, `api.py`, фрагменты `window.py`, R0–R9 checkpoint log и регрессионные тесты. [Windows CI #41325699409](https://github.com/feshakomissarov-prog/chess/actions/runs/41325699409) = SUCCESS, 303 теста PASS; не подменять это гарантией новых, непроверенных сценариев.

**R0–R9 в целом реализованы**, не переделывать их с нуля. `Fair Play` теперь имеет монитор для активной UCI-задачи; `R1` fast-очередь стала постраничной; source-specific `move_metrics` и `official_accuracy` исправлены; есть selective review, correct opportunity PV и phases. Ниже — **новые конкретные пробелы второго ревью**, появившиеся/оставшиеся после R0–R9. Все проблемы установлены статическим просмотром; новое тестирование реального Lichess/UCI/личной SQLite **не проводилось**. Если конкретный дефект воспроизведение опровергнет, приложить failing/passing deterministic regression и обоснование в журнале, не переписывать код напрасно.

Codex: продолжить **в исходном `codex/m45-batch-analysis` и PR #30**, обновить ветку документацией из текущего main без разрушительных reset/force push. Коммит после логического исправления, запись в `M4_5_EXECUTION_LOG.md`. Выполнить R10–R16 последовательно, строго сохранить настоящие работающие R0–R9, не открывать реальную личную SQLite/токены и не запускать массовый UCI на персональном архиве. Нельзя самостоятельно merge PR #30 или переводить его из draft, пока нет review.

## R10 — P1 — selective review зацикливается на первых N партиях и нарушает newest-first

**Факты:**
- `src/chess_coach/lichess/batch_analysis.py:274–309`: `enqueue_selected_reviews()` получает `runs = repository.list_reviewable_analysis_runs(...)`, формирует `move_plies`, вызывает idempotent `enqueue_analysis_jobs`, но **`selected_total += len(move_plies)` выполняется даже если `result["queued"] == 0`** (job уже queued/complete). При лимите `budget_total=100` первый набор уже обработанных событий постоянно съедает бюджет следующих запусков.
- `repository.py:903–926`: `list_reviewable_analysis_runs` завершает `ORDER BY game_id`, не по `started_at DESC`. Поэтому независимо от user requirement **недавние** события могут вообще не доходить до review, а старые/алфавитные ID монополизируют бюджет.
- `tests/test_m45_batch.py:202–255` проверяет duplicate только на одной партии и поэтому не доказывает прогресс очереди с большим архивом.

**Исправление:** запрос кандидатов с deterministic newest-first (проверенная дата игры, null-last, затем стабильный game_id), исключить **точные уже представленные review jobs** (в соответствии с review parent/source/engine/budget/config), набирать quota именно из **новых, ещё не стоящих в очереди** review move plies. При наличии queued/running/paused не дублировать, при complete skip, для failed/cancelled — explicit retry-policy. Не удалять выполненные review результаты. Важно: selection должен не подсчитывать уже просмотренные как новый бюджет; поддерживать pagination/continuation, SQL index и bounded memory/SQL queries для 10k–35k архива. Не пытаться получить все `run_id` в Python только ради первых 100 позиций, если можно ограничить на стороне SQLite.

**Acceptance:** >=200 синтетических reviewable игр с 1 критическим ходом, `budget_total=10`. Первый запуск ставит последние 10, второй после complete — следующие 10, и так до конца; все job_id unique по config; re-run без новых кандидатов делает 0 jobs и 0 UCI. Проверка дат и null ordering, несколько аккаунтов/общая игра, partial Lichess, различие budget/engine params, queue restart и корректные счетчики UI. Добавить отдельные тесты для `SELECT newest-first` с противоположной сортировкой по game_id.

## R11 — P1 — целевая дозагрузка Lichess eval снова и снова проверяет одни и те же первые партии

**Факты:**
- `repository.py:726–747`: `list_games_missing_lichess_evaluations` возвращает **неизменно самые свежие** missing games; нет `last_checked_at`, курсора/проверенного обхода, `not_analyzed_on_server` и `retry_after`.
- `archive.py:166–190`: `refresh_missing_evaluations()` берёт первые N (обычно 25), вызывает GET export. Если на Lichess у этой партии **не было** оценки, она не появится только от GET; очередной ручной запуск снова запрашивает **те же первые N**. Старые missing games недостижимы; `refreshed` учитывает успешный export, а не прирост actual scored positions.

**Исправление:** persistent bounded refresh-checkpoint/cursor (можно минимально расширить SQL schema v16→v17 additive, а лучше уже имеющуюся sync state/таблицу при отсутствии смешения с backfill); семантика distinct `not_checked` / `checked_no_evals` / `partial` / `changed` / `api_failure`, `last_checked_at` и опциональный сдержанный `next_retry_at`. При повторном явном сканировании выбирать **следующую** необследованную партию, а не повторять первые 25, пока сервер не обновился/истек optional cooldown. Отдельный opt-in «перепроверить ранее без оценок», без бесконечного polling. При 429/timeout соблюдать Retry-After, не пропускать в checkpoint как успешно проверенную, не трогать архивные backfill cursor. Сообщать `selected / checked / gained_scored_pairs / unchanged / failed`; при abort сохранять checkpoint атомарно по проверенным game_id.

**Acceptance:** 80 synthetic missing игр, лимит25, fake Lichess export без eval; четыре последовательных явных вызова проверяют 25+25+25+5 разных игр и не требуют engine. Повтор без opt-in возвращает 0 (или истёкший cooldown с явным статусом), 429 на середине не теряет не проверенные; частичное появление новых eval обновляет метрики и отчёт. Предпочтение newest-first среди НЕ проверенных, включая null-date, два аккаунта, собственность и rev/hash, транзакционная миграция с backup/rollback если меняется schema.

## R12 — P1 — «упущенные возможности» агрегируются не только для владельца аккаунта

**Факты:**
- `move_metrics.py:110–150`: `build_move_facts` создаёт `missed_opportunity` при последовательных ошибках **любой** стороны, никак не получая selected owner color; сами события position-relative, что нормально для raw facts.
- `statistics.py:588–673`, особенно `:635–638`: `historical_timeline` считает `(SELECT COUNT(*) FROM analysis_events ... event_type='missed_opportunity') opportunity_candidates` **без проверки, что `analysis_events.ply` принадлежит `ag.color`**. Один и тот же game может попасть в профиль белого игрока с пропущенными шансами ЧЁРНЫХ и наоборот; у тренера получаются неправильные направления для задач.
- `event_detectors.py:132–134` `opponent_blunder` зависит от `user_color`, который в `repository.py:1639–1642` берётся из глобального `games.color` (или архивного последнего `player_color`), тогда как один game_id может быть привязан к двум аккаунтам. Глобальный event_run не может одновременно означать «соперник» для обеих сторон.

**Исправление:** хранить move-relative events и `mover` независимо от аккаунта; **фильтровать события при персональной агрегации через `game_moves.mover = account_games.color` для missed opportunity**, и `opponent move` через `!=`. Для owner-relative labels рассчитать perspective во время чтения или хранить account-scoped projection, не ставить глобальную «opponent» причину без scope. Не дублировать движковый run для двух linked accounts; raw scores общие. Все current/historical/profile/phase/opening counters и drill-down должны быть согласованы по владельцу и selected run. `candidate` не объявлять verified conversion percentage.

**Acceptance:** одна synthetic партия с двумя account links (белый и чёрный), цепочки ошибка соперника→ответ с обеих сторон; ровно свои opportunity_candidates видны каждому профилю, чужие не попадут; один raw Stockfish run, одинаковый selected run. Эмуляция позднего archive sync аккаунта противоположного цвета не изменяет ранее созданные глобальные события семантически; report source/denominators остаются корректными.

## R13 — P1 — «Lichess complete» по post-move evals скрывает незакрытую первую пару

**Факты:**
- `repository.py:749–800, 787–794`: fast-enqueue исключает Lichess `status='complete'`, когда число сохранённых post-move оценок `>=COUNT(game_moves)`.
- `repository.py:1320–1371`: import устанавливает `complete` при наличии eval у всех plies 1..N, **без необходимости оценённого начального ply 0**.
- `move_metrics.py:84–108`: для хода `ply=1` нужна пара `assessment(ply=0), assessment(ply=1)`. В обычном annotated PGN начальная оценка может отсутствовать, поэтому quality первого хода белых остаётся unknown, хотя `Lichess complete` блокирует fast-pass Stockfish на эту игру. Наша модель уже различает scored_positions и comparable_own_moves, но **планировщик дыр не закрывает**.

**Исправление:** split `source_eval_completeness` и `decision_comparability`. Для критериев «всё, что нужно паспорту» учитывать доступные own adjacent pairs; не считать число пост-хoдовых оценок достаточной гарантией. **Ограниченность/экономика важна:** не заставлять считать Stockfish всю длинную игру ради одной missing ply0, если можно сделать targeted inexpensive evaluation на конкретной missing position, без смешения разных источников в одной канонической severity-серии (explicit bridge/provenance/compatibility policy). В качестве более консервативного первого решения допускается оставить Lichess complete для source coverage и **явно** показывать incomplete own-move quality первого хода; но тогда queue не обещает «все решения оценены» и UI должен иметь независимую targeted backfill опцию либо честный unsupported статус. Не делать автоматически ложные/несравнимые CP пары из разных engine budgets.

**Acceptance:** PGN с eval после каждого реального хода (ply 1..N) и без ply0; `source evals N/N`, `comparable own moves (N_own-1)/N_own` (для белых), первый ход severity=unknown; планировщик/GUI показывает конкретный missing requirement, не «полностью всё оценено». Там, где дополнение поддерживается, заполняет пропуск без изменения определения Lichess score и не микширует несовместимые оценки. Контрольный PGN с настоящим ply0 и всеми scores даёт полное own pair coverage.

## R14 — P2 — у неочевидного source нет оправдания подменять происхождение дебюта

`statistics.py:277–355`, строка `opening_provenance = "chess-coach-cc0" if eco != "unknown" else "unknown"`: `ECO` мог быть получен из **Lichess** `games.opening_eco`, без `opening_matches`. Это ложное утверждение источника классификации дебюта. Исправить SQL projection с `opening_matches` присутствием/действительным `om.dataset_revision` vs `g.opening_eco`; показать Lichess и CC0 источник раздельно, конфликт оставить видимым. Тест на ECO только из API без `opening_matches` и на CC0 conflict.

## R15 — P2 — reclassify не снимает устаревшие события и метки

`repository.py:1856–1887`: `reclassify_analysis_events` вставляет только новые `event_key` через `INSERT OR IGNORE`. Если новая версия детектора не выдаёт старое событие, оно останется в `analysis_events` как активное; `_load_analysis_events` читает их все. Правильно сохранять историю версий и отделять *active detector version* от старого evidence, либо транзакционно replace только **собственную текущую версию** и её tags, не трогая чужие обработчики/другие runs/личные notes. Idempotence и no-UCI. Тест: detector сначала выдаёт candidate, после смены policy отказывается; current report его не показывает, historical evidence не теряется там, где хранение необходимо.

## R16 — P2 — source completeness and official/local accuracy UX contract

`repository.py:1746–1747,1813–1814` явно возвращает `local_game_accuracy=None`. Это честнее выдуманной Lichess-точности, однако продуктовая задача — видеть точность для *всех* обработанных локальным Stockfish партий. Не выдавать CPL за проценты. На отдельном этапе сформулировать **версионированную** локальную оценку качества/accuracy (например из нормализованных WDL и её агрегации), проверить на synthetic examples и согласовать источники/интерпретацию. Если метод не доказан — сохранить NULL и показать в UI причину/покрытие, а не 0%. При данных Lichess accuracy и Chess Coach accuracy выводить отдельно, не смешивать две серии. Закрывать R16 как PARTIAL/DEFERRED допустимо с конкретной UI маркировкой, не блокировать исправление R10–R13.

## R17 — необходимые регрессионные проверки и handoff

1. Проверить R10–R13 прежде всего на synthetic/fake. Добавить regression tests в `tests/test_m45_batch.py`, `test_m45_analysis_store.py`, `test_m45_profiles.py`, `test_m45_move_metrics.py`, `test_m45_openings.py`; делать тест на **разные** 10+10 review jobs, а не один повтор.
2. Fair Play R0 не регрессировать: mid-search внешняя человеческая игра, stalled network/timeout и lease-lost; монитор не маскирует неизвестное состояние; API calls bounded. Не запускать network request на каждый ply.
3. Migration: если schema версия меняется, использовать additive transactional DDL, SQLite backup API/integrity/rollback. Existing v9→v16→v17 fixture and fresh install; не копировать/удалять исходные games/moves/puzzles.
4. Windows Python 3.12 full CI на **новом точном SHA**; compileall/offscreen UI; SQL performance benchmark 10k/35k synthetic games; no real OAuth/Stockfish/personal DB; не подменять реальные E2E synthetic test.
5. Обновить [execution log](../handoffs/M4_5_EXECUTION_LOG.md) и [PROJECT_STATUS](../PROJECT_STATUS.md): R10–R17 DONE/PARTIAL/DEFERRED; exact SHA, tested commands, passing tests, security/data risks; PR #30 оставить draft, никакого merge.
6. Следующий независимый reviewer должен уметь понять по GitHub, что именно исправлено, и воспроизвести новые тесты.

**Definition of Done:** прошедший второй review, настоящий progression fast + review newest-first с ограниченными бюджетами, целевой Lichess refresh без starvation, персональные opportunities без загрязнения чужими ходами, правильно отражённая полнота pairs, source provenance и честные оставшиеся PARTIAL. Указанные R10–R13 — **blocking для real-archive pilot**; R14–R16 — quality-debt, который лучше исправить в этом же PR без неоправданных переписок.

**Отдельное примечание:** успешные 303 PASS на ревизии `5872eb207fd11701f383e94db12427fc03abdded` остаются полезным baseline. Это не утверждение, что все R0–R9 не работают; адресные найденные дефекты не были покрыты имеющимися тестовыми сценариями.
