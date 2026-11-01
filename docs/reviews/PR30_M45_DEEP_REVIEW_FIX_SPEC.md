# PR #30 — независимое ревью M4.5 и адресное ТЗ на исправление

**Дата:** 2026-11-01. **Статус:** REQUEST CHANGES / NO MERGE. **Проверенный SHA PR #30:** `8a011442d7d2126d94088d0a4b0ec811ca4e2d2c`. **Целевая ветка исправлений:** существующая `codex/m45-batch-analysis`, без нового feature PR и без переписывания main. **PR:** [#30](https://github.com/feshakomissarov-prog/chess/pull/30). **Предыдущее ТЗ:** [M4.5 v2.0](../specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md). **Журнал:** [M4_5_EXECUTION_LOG.md](../handoffs/M4_5_EXECUTION_LOG.md).

## 0. Итоги аудита и правила исполнения

**Факты, подтверждённые GitHub:** PR #30 draft, mergeable, 31 изменённый файл, HEAD в отрыве от main на 25 коммитов, behind=0 на момент проверки. GitHub Actions run [41282991711](https://github.com/feshakomissarov-prog/chess/actions/runs/41282991711) SUCCESS: **285 тестов PASS**, Windows Python 3.12, compileall и offscreen GUI. PR в main не слит. Прочитаны исходники `repository.py`, `batch_analysis.py`, `analysis.py`, `move_metrics.py`, `event_detectors.py`, `opening_matcher.py`, `phases.py`, `statistics.py`, фрагменты `window.py`, а также тесты и журнал. Проблемы ниже — **результаты статического ревью и проверки существующих unit tests**; исправления пока не выполнены, реальный Stockfish и пользовательская SQLite не запускались.

**Цель:** доработать имеющийся M4.5, устранить P0/P1, не переписывая уже работающие модули и не расширяя задачу до M5. Codex должен выполнить пункты R0–R9 последовательно в **том же PR #30**, записывать каждый checkpoint в журнал и не останавливаться ради рутинных вопросов. Нет разрешения на merge, force-push, реальный OAuth, обращение к персональной SQLite без согласия, реальный массовый Stockfish на персональных данных. Тесты — synthetic/fake, Windows CI на финальном SHA. При конфликте новых документов в main аккуратно подхватить только документационные изменения (git status, fetch/rebase/merge без потери своих изменений).

### Критерии приоритета

- **P0:** честный fail-closed Fair Play при изменении состояния *во время текущего анализа*.
- **P1:** возможность дойти до всего архива; корректные метрики из Lichess, обновление derived facts; точность в паспорте; выборочное уточнение; точность evidence/PV; корректность фаз.
- **P2:** повышенная объяснимость, историография, производительность и UI, которые не блокируют ввод базовой аналитики. Успешные 285 тестов не дают оснований пропустить новые тесты на пропущенные случаи.

## R0 (P0): Revalidate fair play DURING ongoing engine work

**Доказательства:** `src/chess_coach/lichess/batch_analysis.py:88–96,113–144` monitor останавливает UCI лишь при `stop_event/pause_event`; серверная `safety_check` вызывается только раз перед партией (`:114`). `src/chess_coach/window.py:2075–2120` получает `/api/account/playing` только перед партией; событие `gameStart` в `:3018–3040` выставляет pause, но запуск игры в браузере при недоставленном событии не гарантирует немедленной остановки.

**Исправление:** для работающей очереди иметь единую safety-политику: свежесть последнего авторитетного снимка аккаунта, generation/account/session, активная человеческая партия/неопределённый противник, stop_event. Во время длительной обработки продлевать safety snapshot через **ограниченный, нечастый, сериализованный сетевой запрос** с TTL и fail closed при stale/timeout/401/429/disconnect/account switch, либо более надёжный наблюдатель событий с гарантированным сроком валидности и независимой проверкой. Не опрашивать API на каждом ходе/позиции и не обходить rate limits. При неизвестности **прервать текущий UCI analysis через analysis_stop** и приостановить claim/очередь; уже подтверждённые завершённые runs не портить, partial не представлять complete. Даже если Qt не получил gameStart — движок прекращает работу при истечении безопасного TTL. Отсутствие API/сети => pause, а не «safe by default». Синхронизировать доступ к shared session variables/credentials корректно; ошибки worker не передавать в произвольные Qt GUI объекты из потока.

**Acceptance tests:** fake safety=TRUE перед первым game; во время намеренно долгого первого UCI запуска появляется человеческая партия в другой сессии (нет Qt gameStart); после TTL/current snapshot engine.stop вызывается, очередь paused и следующая игра не начинается. Повторить при disconnect, identity mismatch, 429, API timeout, truncated snapshot и официальном встроенном AI; нет false allow для BOT account. Proof на bounded resource usage (один UCI process, ограниченный частотой API). По сравнению с текущими тестами `test_batch_detects_new_human_game_before_next_job` **обязательно добавить mid-game**.

## R1 (P1): Архивная очередь не должна зависать на первых N играх

**Доказательства:** `repository.py:741–767` `list_games_needing_fast_analysis` исключает лишь полный Lichess run, **но не уже complete/queued/failed/cancelled fast Stockfish jobs**. `batch_analysis.py:68–74` выбирает первые `limit` кандидатов до проверки job identity; `repository.py:769–793` повторные выдаёт `already_present`. В UI `window.py:1174,2062–2064` ограничение 10 000 (по умолчанию 1000): когда первые 1000 уже обработаны, следующий вызов снова выбирает их и не продвигается к более старым партиям. Кроме того, `INSERT OR IGNORE` с UNIQUE `analysis_jobs` не позволяет повторно поставлять `failed/cancelled/stale` задания в осмысленный retry.

**Исправление:** выборка должна быть `NOT EXISTS suitable complete stockfish run OR suitable matching job state` в правильных границах **точных engine fingerprint, params, analysis_pass, line_revision/hash**. В очереди separate: queued, running, paused, complete, failed, cancelled, stale, unsupported, no-op; выбор новых задач recent-first должен **пропускать** уже complete/queued/paused/running для данного config, пока не наберётся `limit` ещё не представленных eligible. Для failed/cancelled — явный retry/resume с reset lease/attempt policy, а не вечный INSERT OR IGNORE. Если в базе >10k, второй/третий запуск действительно добирает следующую порцию; желательно bounded repeat/backfill до исчерпания вместо ручного 35-кратного нажатия (с пользовательским cap/opt-in). Исключить `final_sync_pending=1`, исходы `unknownFinish`/unfinished и невалидную/неподдерживаемую линию из batch, не помечать их успешными. Сохранять duplicate-account single engine work.

**Acceptance:** 15 000 synthetic valid games; первый запуск выбирает последние 10k, второй — оставшиеся 5k без повторного прохода. Repeated start после 100 complete не вызывает UCI заново. Набор failed/cancelled/stale корректно возобновляется либо clearly retry, не блокируя весь архив. Смена engine hash/limit создает **новую** измерительную серию; старые completed не стираются. 2 аккаунта с одной партией — один engine-run на line+settings. Сравнить скорость SQL на 10k–35k.

## R2 (P1): Метрическая единица истины + Lichess classifications

**Доказательства:** `repository.py:1217–1270` импортирует PGN evals в `position_assessments`, но **не в `move_classifications`**. `statistics.py:175–215,191–208,485–506,550–564`, `game_page:677–682` считают ошибки и фазы по **старым `move_classifications`**, а новые `move_metrics` с severity v2 используются лишь частью паспорта. `latest_analysis_detail` материализует `move_metrics` по demand, но не создаёт legacy `move_classifications` для Lichess.

**Результат:** полная Lichess партия может иметь scores, но выглядеть с нулём ошибок в библиотеке/агрегатах. В mixed sources latest-complete предпочтение отдаёт одним runs, другие классификации могут быть проигнорированы без предупреждения. Это недопустимо как основание тренера.

**Исправление:** выбрать **один канонический, версионированный источник severity для новых UI/агрегатов** (предпочтительно `move_metrics`; v1 остаётся для истории). Детерминированно вычислять metrices из compatible adjacent pre/post eval pairs по выбранному run, с accurate unknown. При импорте Lichess или lazy scheduled hydration строить `move_metrics` и события по всем пригодным парам; SQL library, opening_statistics, summary, historical_timeline, personal_profile/diagnostics читать из одной versioned policy с нужными account/perspective. Не записывать «0 ошибок», если coverage не полон; показывать n assessed / total own moves. `source preference` user visible and deterministic; нельзя случайно считать complete Lichess, partial review или разные settings одной серией. Не складывать официальный Lichess accuracy с локальной fake accuracy.

**Acceptance:** 2-3-ходовая PGN с [%eval] + однозначным CP loss, **без** legacy move_classifications: Lichess-only UI показывает соответствующую severity/error_count, при отсутствии adjacent score — unknown и denominator excludes it. Равенство показателей summary, game_page, passport, time series/opening. Черные/белые, mate, source selection, incomplete, account-specific attribution и revision stale fixtures.

## R3 (P1): Исправить official Lichess accuracy в паспорте

**Доказательство:** `repository.py:1188` записывает `games.source='lichess_archive'`. `repository.py:1549` и `:1619` выбирают `official_game_accuracy` только если `game["source"] == "lichess"`. Для настоящего Lichess archive результат становится None при сохранённых accuracy_white/black.

**Исправление:** единый определитель Lichess-origin по набору правдивых источников/links, не по одному старому значению; конкретный аккаунт/цвет важен, особенно когда у game 2 account_games links. `latest_analysis_detail`/кэш должны принимать account_id или explicit user_color, не подменять его локальным `games.color` для другого аккаунта. Display accuracy только если соответствующее white/black значение реально не NULL; показать source `lichess`, n=1/0 и absence как «нет данных».

**Acceptance:** импорт через `import_archive_record` с accuracy, сохранённый source `lichess_archive`, открытие detail показывает правильное значение; оба цвета/двойное владение; old games source=lichess backward compatibility.

## R4 (P1): Late Lichess evals не должны оставлять stale derived facts

**Доказательство:** `repository.py:1234–1271` обновляет position_assessments у того же run_id; `materialize_analysis_metrics:1662–1679` делает `INSERT OR IGNORE` и `latest_analysis_detail:1582–1589` перестраивает derived metrics только если в run нет НИ ОДНОЙ metric. Старые метрики и события после дозагрузки остаются устаревшими, пропущенные пары не всегда добавляются.

**Исправление:** при изменении eval scores (new ply or changed comparable value) инвалидировать **только derived facts данного run+metric/detector version**, атомарно пересчитать pair-level move_metrics, affected events/tags в одной SQLite transaction; или использовать детерминированный replacement job с revision and content fingerprint. Ensure source unchanged if incoming weaker depth; repeat same payload => true no-op. Не уничтожать notes/other raw runs, не удалять old historical versions не по policy, не делать UCI. Cached summaries invalidate. Reclassification при новой detector_version должна видеть старые и новые tags отдельно/статусы, не оставлять активными устаревшие false positives.

**Acceptance:** initial PGN evals на ply1/2, classify; same-line refresh add ply3/4 + revise depth -> новые metrics/events корректны; repeat => no row growth; replacing score меняет severity и убирает false previous event; rollback на artificial error; никакой Stockfish не запускается.

## R5 (P1): Реализовать маленький selective-review orchestration path

**Доказательства:** `analysis.py:163–272` уже реализует review_selected_positions, `repository.py:1714` и `event_detectors.py:156` выбирают critical plies, но `batch_analysis.py:76–181` claim только `analysis_pass='fast'`. `window.py` не вызывает selection/review. Значит end-to-end pipeline «quick→targeted deeper» пока не существует.

**Исправление:** не переписывать analyzer. После fast/готового Lichess metrics выбирать bounded unresolved positions, create persistent review jobs with exact line_hash+engine+settings, worker drives `review_selected_positions` while satisfying R0 safety constraints. Review results/alternatives store in existing tables, allow reclassification of confirmed motifs when evidence sufficient. Если нет надёжной проверки, сохранить `candidate`, не делать verified. UI показывает coverage fast vs review отдельно, caps on review/game/batch; Selective pass может быть opt-in и по умолчанию недорогим, но должен быть **реально доступен** через последовательную массовую очередь. Установка multipv/depth>... не требуется ради фразы «глубокий».

**Acceptance:** synthetic game with blunder & missed candidate → fast complete → selected review generated → review run/alternatives persisted → no duplicate on restart → status cannot appear full merely because sparse review run exists; safety pause works mid-review. Test no eligible case performs zero deep UCI calls.

## R6 (P1): Ошибка указателя лучшего хода для упущенной возможности

**Доказательства:** `move_metrics.py:95–115` берёт `opponent_before = by_ply[opponent.ply - 1]` и `best_uci = _first_pv_move(opponent_before)`. Но *ответ игрока* происходит на позиции **после хода соперника**, то есть `by_ply[opponent.ply]`. Сохранённый best_move_position_ply также относится не к той позиции. UI может показывать лучший ход предыдущей стороны (нелегальный для ответного хода).

**Исправление:** вычислить expected reply from position `ply=opponent.ply` (= `fact.ply - 1`), validate legal move on board FEN/current line and mover, preserve event chain and source; если нет pre-reply best PV, оставлять NULL/candidate, не подставлять чужой ход. Посмотреть все similar off-by-one PV references, включая evidence/навигацию.

**Acceptance:** синтетическая 4-ply последовательность с разными лучшими ходами до и после ошибки соперника; best для missed_opportunity всегда легален стороне, которая должна отвечать; confirmed opportunity только при проверенном действии, не из 1 PV/cp.

## R7 (P1): Фазы не должны превращать миттельшпиль в unknown

**Доказательство:** `phases.py:46–53`: если middle_start определён, но end_start неизвестен (партия закончилась до эндшпиля), все ply после middle переходят в `unknown`, потому что middlegame требует `end_start is not None`. Это искажает ключевой срез статистики.

**Исправление:** если `middle_start` есть, `ply>=middle_start`, а `end_start=None` — phase=`middlegame`. Если middle отсутствует, end есть — консервативно unknown перед endgame, без ложной opening. Тесты на отсутствие эндшпиля, ранний эндшпиль, обе границы неизвестны, off-by-one и правильный `_apply_phase_labels` после поздней phase hydration.

## R8 (P1/P2): Eligibility/coverage/source UI and profile integrity

- `repository.py:741–766` не проверяет `final_sync_pending`, `import_error`, фактическую завершённость, legal move line. Иначе batch может считать промежуточные/заведомо некорректные игры. Фильтр/проверка непосредственно в SQL и в runner перед UCI.
- `repository.py:1517–1560,1572–1629` latest run выбирается лишь complete before partial, без явной политики по source/settings/coverage. Review run status partial может внезапно выбраться после изменений, а Lichess/Stockfish могут смешаться между экранами. Реализовать clear deterministic `selected_metric_run` policy, preserve independent raw reports.
- `statistics.py:367–434` при отсутствии clock-фильтра составляет профиль из разных контролей; текущий тренер должен видеть предупреждение и по возможности отдельные buckets (blitz, rapid и т.д.), иначе сравнительный вывод provisional. Нет оснований ради этого переписывать UI целиком.
- `analysis_coverage` currently counts scored positions, а не sufficient own move pairs; «complete» для lichess-pgn по N оценкам post-move может не дать оценку before first move. Явно разделить полноту **оценок** и **сопоставимых собственных ходов**. 1 incomplete move => unknown, а не good.
- `repository.py:72–105,387–388,482–511` каждый запуск повторяет `DROP VIEW / CREATE VIEW`; при уже v16 это необязательное изменение схемы. По возможности guard by schema version, выполнить только на миграции; не разрушать существующие данные и не переписывать другие таблицы.
- `opening_matcher` сохраняет verified CC0 dataset, но opening departure ≠ ошибка. Не превращать отсутствие подходящей ECO позиции в доказательство незнания дебюта.
- `event_detectors`: registry of many codes ≠ implemented motif detectors. Убедиться, что в UI и отчёте deferred не объявлен verified. По V2 полной глубокой тактической детекции нет — честно маркировать PARTIAL, а не писать «всё реализовано».

## R9: Regression tests, CI, transparent final report

Добавить новые unit/integration tests, в частности:
1. Mid-game remote human game detection with delayed/absent Qt event; expired safety proof; 429/timeout/disconnect; UCI stop and no next job.
2. 15k synthetic games in pages of 10k/5k, exactly once per engine/settings and proper retry failed/cancelled.
3. Lichess-only PGN source=`lichess_archive`: correct own severity (both colors), library errors, official accuracy, n/N, phases and history.
4. Late Lichess eval changes materialized metrics/events without UCI; no duplicates; negative stale events removed.
5. Fast→auto-selected review, cap, alternatives, pause/restart and preserved audit trail.
6. Correct reply best move in missed opportunity, legal move check, candidate-only until verified.
7. Phase middle with no end, Lichess division/source override, full/partial source policy.
8. Upgrade v9→v16 on synthetic SQLite with notes/attempts/analyses retained and backup verified; future version rejects; repeated restart no excessive DDL, integrity_check and FK.
9. UI Qt offscreen game passport: accuracy when archive source; first seen errors from Lichess; cross-account color; click shows position **before** decision.
10. Performance 10k+ synthetic games for queries and memory, factual SQL plans; no claims about real user machine without a run.

Run full Windows Python3.12 CI on **final current SHA**, compileall, offscreen Qt, check code formatting, `pip check`, `git diff --check`. Record exact command, counts, SHA, Windows runtime and tests NOT RUN. No user database / personal OAuth / real Stockfish unless a separate explicit user opt-in after a consistent SQLite backup. Deliver one updated PR #30 still draft, journal updated with errors fixed/remaining and final REVIEW REQUEST. **Do not merge.**

## Mandatory compact answer from Codex after fixing

«PR #30 head SHA: ...; R0–R9 DONE/PARTIAL/DEFERRED individually; new regression tests [...]; CI run link and exact count [...]; evidence of fair-play safety and full-archive paging; SQLite migration version/backup tested; real DB/Lichess/UCI NOT RUN; risks [...].» If a P0 remains, say NO-GO and leave draft.

## Review outcome

**NO-GO merge** on audited SHA. Recommended blocking fixes **R0–R7** (R1/R2/R3/R4 especially affect declared core product goals). R8 minimal safety and metric coherence; a deep tactical taxonomy and verified opportunity conversion may remain clearly PARTIAL if evidence is unavailable, but must not invent conclusions. R9 required final verification.
