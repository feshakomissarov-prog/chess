> **07.11.2026 · CURRENT ARCH-UX-01 CHECKPOINT: `9e369640b028914402fabf3378ad574bcfd4fa97` · code CI PASS / owner visual+live perf НЕ ОК.** Exact [Windows CI #42279790718](https://github.com/feshakomissarov-prog/chess/actions/runs/42279790718) SUCCESS, 829 pytest, compileall and startup; independent inspection of [artifact #13534378317](https://github.com/feshakomissarov-prog/chess/actions/runs/42279790718/artifacts/13534378317), 18 Windows Qt screenshots and approved 3-art pairs. Confirmed functional improvements: bounded local Home preview, unified QSS palette on all routes, canonical openings, synthetic sorting orphan popup gone, misleading Lichess 3% bar hidden. Remaining: preview for new scope is still **deferred behind old running full query**, scope-clearing false 0, no representative 3500-game measured first-render/SLA for real 5–7 min bug, ready-analysis status does not prominently show next due/25 daily/Retry-After; visual fidelity to art still materially under target and populated other screens not evidenced. [ONE consolidated VISUAL MASTER §14 VIS6](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) + [FULL MASTER §17](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) in docs-only draft PR #45. Code in same draft #44; main unchanged/unmerged; owner connected pilot NOT OPEN, privacy and Fair Play protected.

> **LATEST 06.11.2026 · independent VIS5 gate code PASS / visual НЕ ОК, exact SHA `3cf9c82bc7c3c96b05d3025cf81286d7fde7e6bf`.** [Windows CI #352](https://github.com/feshakomissarov-prog/chess/actions/runs/42258376344) verified 824 PASS, native screenshot and compileall. Actual ZIP [#13522844726](https://github.com/feshakomissarov-prog/chess/actions/runs/42258376344/artifacts/13522844726) downloaded/extracted and 3 art-vs-Qt pairs visually inspected. VIS4 source and screenshot dimensions fixed; nevertheless all three pages **still fall materially short of the exact approved style**. Additional code-proven Home contradictory opening labels from inconsistent `eco/opening_eco` fallback; synthetic Library fixture opponent names differ (fixture problem). [One unified §13 VIS5 spec](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) + [MASTER §16](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) in draft docs PR #45; implementation same draft code PR #44, not merge/main. Need exact-window native visual iteration, design measurement, source tests and independent gate. No owner live connected Windows pilot yet.

> **CURRENT 06.11.2026 · Code PR #44 `c7d1ac4f7032704371faff67401fb23937ad3688` code+visual gate НЕ ОК.** Hosted [Windows CI #344](https://github.com/feshakomissarov-prog/chess/actions/runs/42180895879) SUCCESS **820 PASS**; downloadable artifact #13485053887 verified with 3 opened comparison PNGs. Owner-approved 3-screen fidelity still fails: Home 60px sidebar at 1672, Library image captured empty/unselected despite synthetic rows, Background sample not representative connected activity; 1280x720 at scale1 actually 1028x720. Source bug all-missing Stockfish retry checkbox ignored, initial Library action prematurely enabled. **ONE unified correction [VISUAL MASTER §12 VIS4](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md)** / [MASTER §15](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md); same draft PR #44, docs-only draft PR #45, main unchanged. VIS3 batch paging/server completeness/auto-resume/live signal are source-backed improvements. Owner real-Lichess/manual pilot is NOT OPEN; physical DPI/sound/UCI not tested.

> **06.11.2026 · VISUAL MASTER independent gate НЕ ОК (SHA `6c2829177850136425727eb34c38c30a867a8800`).** [Windows CI #336](https://github.com/feshakomissarov-prog/chess/actions/runs/42153675412) verified SUCCESS **816 PASS**, compileall/startup, but 3 approved owner-art visual matches remain incomplete/uninspectable (local screenshot paths only; 0 Actions artifacts). Source gaps: SF auto-pause has no post-game auto-resume; one-shot default 2-game batch not full missing archive; missing ply0 Lichess runs can suppress SF; Background page SF progress not live. **Single [§11 VIS3 correction spec](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md)** plus [FULL MASTER §14](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) in docs-only draft #45; code corrections in same draft PR #44. Independent full source/visual gate remains closed; **no owner manual pilot/merge**. Real connected Lichess, DPI/audio/UCI/owner DB still not run.

> **06.11.2026 · OWNER APPROVED VISUAL REDESIGN — EXECUTION SPEC READY, CODE NOT YET UPDATED.** One [approved 3-screen UI/technical execution spec](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_VISUAL_MASTER_EXECUTION_2026_11_06.md) + [FULL MASTER §13](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) now in docs-only PR #45. Approved references: Main dashboard (3 KPIs incl SF/Lichess accuracy + rated puzzle chart; 3 long game rows with right mini charts), My Games (search/filter, compact list, vertical readable detail statistics and evaluation graph), Background Operations (four true worker statuses, live progress, last sync and recent events). Exact palette/geometry, source-aware local Lichess-method accuracy, server ready-data refetch, resumable bulk Stockfish paused during any game, inline Play active games, board initial size, premove arrow and server clock fixes specified. Codex **MUST** open PNGs locally and compare Windows screenshots 1:1 against each reference, not pass on Qt smoke alone. Pre-change code #44 SHA `317512a57e62b5c691bfbb25d0471b5fe8e4b1ad`; both PRs draft/unmerged; no code implementation has been claimed. The owner's real application is connected Lichess; offline is only internal synthetic testing. No main/merge or live credentials/owner DB, strict Fair Play. After one full Codex implementation and exact-head Windows CI, independent broad review and then owner visual pilot.

> **06.11.2026 · ARCH-UX-01 code gate: ОК ДЛЯ РУЧНОЙ WINDOWS-ПРИЁМКИ, НЕ МЕРДЖ.** Independently re-reviewed code PR #44 exact HEAD `317512a57e62b5c691bfbb25d0471b5fe8e4b1ad`: [Windows CI #317](https://github.com/feshakomissarov-prog/chess/actions/runs/42115060159) completed/SUCCESS, 808 pytest PASS plus compileall and isolated offline window. RV2-01…08 fixes and tests inspected; no newly confirmed source-level blocker. Independent signoff and precise manual checklist in [MASTER §11](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) on docs-only PR #45. Matrix inventories 11 CORE/11 UI/24 COV/70 actions; 10 native synthetic QTest mouseClick steps are reported by Codex, not a physical mouse test of all controls. **Owner manual Windows pilot NOW OPEN:** offline navigation/board/PGN/revision/notes/Training/Progress, actual audio/DPI/1280×720, then optionally approved manual Lichess connection, real archived files and Stockfish with backup and strict Fair Play. Previous НЕ ОК reviews remain history and are superseded by this new SHA-specific code gate. Do not merge PR #44 or #45 without explicit owner approval; main unchanged.

> **LATEST 05.11.2026 · INDEPENDENT GATE AFTER MA-01…MA-09: НЕ ОК.** Current code PR #44 SHA `c6c06577d78660c38a328dfa3539a313c9cb650d` and [Windows CI #313](https://github.com/feshakomissarov-prog/chess/actions/runs/42068371676) were verified: completed/SUCCESS, 792 PASS, compileall/offline window PASS. Nonetheless source-backed defects remain, consolidated as **RV2-01…08 in [MASTER §10](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/ux-information-architecture-2026-11-04/docs/specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md)** (docs-only PR #45). Critical: inconsistent order of `_auth_lock/_pending_action_lock` between stream and pre-ID cancel; friend-challenge try-return bypasses cleanup; partial server create→accept failure can orphan known challenge; pre-ID 429 leaves canceled UI request without state; game action reconciliation precedes accepted chess snapshot; Home→Play pending cancel state absent; PGN worker writes uncaptured revision to destination; asynchronous note error alters wrong selected draft controls. Matrix now 70 subactions, but native smoke only rendered 9 routes without physical click-through. **Code gate NO; no owner manual pilot yet, do not merge or touch main**. Single comprehensive repair cycle in existing code PR #44, red→green, lock barriers, fake Lichess, full Windows CI at next SHA, renewed broad acceptance. Genuine PAT/OAuth, user files, real Stockfish, physical audio and DPI remain owner-only later.

> **LATEST 05.11.2026 · INDEPENDENT REVIEW OF IMPLEMENTED MASTER — NOT ACCEPTED.** В кодовом draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) SHA `10b3ffae3c0a402d1aa7e2a5c7013f42648216b2` подтверждён завершённый [Windows CI #305](https://github.com/feshakomissarov-prog/chess/actions/runs/42039601150): **SUCCESS, 774 PASS, compileall и offline startup PASS**. Однако повторное source-review обнаружило ошибки состояния challenge при create failure, преждевременное снятие pending игровых POST до server ack, понижение сохранённой timestamp при reimport PGN date-only, сброс чужого pending challenge через `_lichess_error`, один общий note draft с потерей данных, недоступную отмену friend invite до ID и недостающие subaction acceptance cases. Также найдено: автозапуск миграционного batch вызывает `_schedule_refresh()` с потерей page/selection в открытом Progress до последующего preserving refresh. Синхронный GUI SQL под writer-lock требует воспроизведения. Все замечания и единое исправляющее ТЗ записаны в [MASTER §9 MA-01…MA-09](specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md) docs-only draft PR #45. **Решение: НЕ ОК, не начинать owner manual Windows pilot, не merge.** Codex должен закрыть все source/blocker пункты и матрицу одним циклом в текущем PR #44 с red→green, full pytest, native fake GUI и final-head Windows CI. Реальный PAT/Stockfish/пользовательскую SQLite и данные не использовать; ручная приёмка позже.

## 05.11.2026 — ОБА БЛОКА SOURCE-РЕВЬЮ ЗАВЕРШЕНЫ: одно общее ТЗ MASTER, не серия RF

Базовый code draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), HEAD `7a3a6775a0a27e60c0bea7cfbcd4f28d734e939b` (unmerged), [Windows CI #286](https://github.com/feshakomissarov-prog/chess/actions/runs/42005438175) **750 PASS/SUCCESS**. По требованию владельца выполнен **двухблочный независимый source-аудит** без новых code commits: Блок I — архитектура, SQLite, Lichess, Stockfish, Fair Play, фоновые задачи, data integrity; Блок II — UI 6+ разделов, все видимые controls, переходы, сценарии и 24 UX coverage-группы. **Итоговый единый документ [ARCH-UX-01 FULL SYSTEM AUDIT MASTER](specs/ARCH_UX_01_FULL_SYSTEM_AUDIT_MASTER_2026_11_05.md)** в docs-only draft PR #45 содержит 11 CORE и 11 UI source findings/risk contracts, матрицу COV-01…24 и tests/physical-gate. Статусы прямых source-дефектов отличены от риск-гипотез, требующих fake reproduction.

**Критические P0:** поздний 401 из `send_move` может удалить новый выбранный PAT/OAuth slot и остановить новую сессию; Lichess action/background events без account+generation fencing; GameSessionCoordinator один общий `dict[game_id]`, старые активные A сессии могут оставаться в «Моих партиях» B и спровоцировать wrong-account action. **Далее P1:** корректность миграционных read models Progress и scope, `+2000 с` вместо реальных `+2 с` в часах, даты Lichess PGN supersede JSON createdAt, SQL в Qt handlers, повторные POST команд, неверные statuses, Play cancel UI, финальная Review до durable save, чужая заметка, DB in Stats constructor и др. P2/verify — FEN geometry 720, note/tooltips, source opening/geometry accessibility and 24-group matrices.

Предыдущий RF9 **не передавать самостоятельно**: он включён как CORE-03/04 MASTER. Единственный будущий кодовый цикл — исправление и репродукции всего MASTER в existing draft PR #44, затем **одно полное независимое ревью** с сопоставлением всех 24 групп и synthetic native Windows pilot, CI exact SHA. **Владелец должен отдельно проверить настоящие Lichess/PAT/Stockfish/SQLite и дать разрешение merge.** MAIN/мердж не менять, user data не раскрывать.

---
## 05.11.2026 — независимое ревью RF8: CI 750 PASS, активное REVIEW-FIX-09

Проверен exact code HEAD `7a3a6775a0a27e60c0bea7cfbcd4f28d734e939b` в draft [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44), hosted [Windows run #286](https://github.com/feshakomissarov-prog/chess/actions/runs/42005438175) **completed/SUCCESS, 750 PASS**, compileall/offline window PASS без видимых thread warnings в job log. Исходники и RF8 tests подтверждают v2→v3 миграцию `stockfish`+`lichess-pgn`+`lichess-json`, app-owned bounded batch без сети/UCI, выбор scored run перед пустым complete и filter-source для главного Progress→Review действия. Codex заявил о native synthetic `VISIBLE_GUI_SMOKE_PASS`; физического owner-PAT/Stockfish/DB теста нет.

Независимое source-review выявило два пока непроверенных тестами UX-контракта. (1) `MainWindow._start_legacy_metrics_batch` вызывает `StatisticsDialog._schedule_refresh` только **до** записи; `_legacy_metrics_batch_finished` обновляет лишь миграционный QLabel и Home, но **не обновляет summary/phases/historical/coverage открытого Progress**, так что KPI могут остаться от старого v2 до ручного filter toggle. RF8 Qt test открывает Progress уже после завершения batch — не покрывает race. (2) `_legacy_metrics_account_scope` использует только `dashboard_account.currentData()` (профиль Главной), хотя Progress имеет отдельный `StatisticsDialog.account.currentData()`; статус миграции может показывать A вместо выбранного B. После полного завершения глобальной миграции revision shortcut ещё и блокирует обновление старой подписи после переключения scope. Эти дефекты подтверждены кодом, не физическим UI-замером.

**Новое исполняемое ТЗ:** [REVIEW-FIX-09 RF9-01…03](specs/ARCH_UX_01_REVIEW_FIX_09_PROGRESS_LIVE_REFRESH_AND_SCOPE.md) в docs-only draft PR #45. Исполнять короткий fix в существующем code draft PR #44; Qt barrier tests с Progress открытым до миграции и A/B счетчиками, native Windows smoke, новый exact-head CI. **RF8 core code accepted; merge approval пока не дан.** Main не менять, Fair Play fail closed, владелец проверяет реальный Lichess/PAT и личную SQLite отдельно.

---
## 05.11.2026 — независимое ревью RF7: Windows CI #279 744 PASS, но нужен REVIEW-FIX-08

Проверен фактический code [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) SHA `41b35221ac58d61c6c205933190b4d5449c8f778`, [Windows CI #41989708089](https://github.com/feshakomissarov-prog/chess/actions/runs/41989708089) **completed/SUCCESS, 744 PASS**, compileall, offline init. RF7-02 Mate(0) WDL для обеих сторон, RF7-03 независимое расписание задач, RF7-04 terminal last-move accuracy в исходниках и Qt/SQLite-тестах подтверждены. Реальные Lichess/PAT/личная SQLite/Stockfish **не проверены**. Кодовый PR пока **НЕ ПРИНЯТ к merge**.

Выявлены точные оставшиеся недостатки: (1) v2→v3 повышение фильтров в `move_metrics` / `analysis_coverage` / `StatisticsService` не сопровождается конвертацией старых scored `lichess-pgn`/`lichess-json` анализов: RF7-кандидаты migration SQL ограничены `ar.source='stockfish'`; сводка по историческим ошибкам и coverage может стать частичной. (2) `materialize_stale_analysis_metrics_batch` существует только в repository/test API, **не запущен** из рабочего app scheduler — UI пересчитывает только избранную партию при открытии деталей, а Progress всего архива остаётся с неполными derived KPI. (3) `latest_analysis_detail` ранжирует `complete` выше `partial` без требования usable CP/mate, хотя `StatisticsService.summary` ранжирует scored первым: Review может показать пустой источник при существующем scored run. (4) Собственный нативный smoke-харнесс Codex завершился FAIL из-за ожидания метрики на карточке вместо деталей; отдельные Qt-тесты PASS, но физический smoke требует повторения.

**Нормативное продолжение:** [ARCH-UX-01 REVIEW-FIX-08 RF8-01…RF8-04](specs/ARCH_UX_01_REVIEW_FIX_08_LEGACY_ALL_SOURCES_AND_REVIEW_SELECTION.md), docs-only draft PR #45. Реализовать в прежнем code draft PR #44 с old-format test SQLite по всем трём источникам, app-owned idle worker, scored run selection, native Windows synthetic smoke PASS, полным Qt/Python suite и новым exact-head Windows CI. В исходном #44 ничего этим ревью не коммитилось, `main` и пользовательские данные не меняли, merge не разрешён.

---
## 05.11.2026 — независимое ревью RF6: CI 721 PASS, но код пока НЕ ПРИНЯТ; активное REVIEW-FIX-07

Проверен точный HEAD кодового [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) `e0947646db73d2cfa6a6baf0b2d3eccc5fed3c2c` относительно предыдущего `881a8d8f…`. [GitHub Windows CI #41979505071](https://github.com/feshakomissarov-prog/chess/actions/runs/41979505071) **completed/SUCCESS / 721 PASS**, compileall и offline-window PASS. Реальные PAT/Lichess, Stockfish и пользовательская SQLite **не проверялись**.

**Найдены подтверждённые по source и непокрытые CI проблемы:** (1) `move_metrics.py` изменил логику вычисления accuracy, оставив `METRIC_VERSION='cc-move-metrics-v2'`; старые metrics `accuracy=NULL` уже присутствуют, а `analysis.py::latest_analysis_detail` rematerialize выполняет лишь если **нет ни одной** строки — новые показатели не появятся в прежних сыгранных партиях. (2) `move_metrics::_typed_score` на `mate_white=0` создаёт `Mate(0)` независимо от реальной очереди хода, хотя python-chess `MateGiven.mate()==0` и `Mate(0).mate()==0`: WDL белых может стать 0/1000 при победе белых по законной доске. (3) `_auto_sync_timer` запускается только при `auto_archive_sync`, вследствие чего `auto_puzzle_history_sync=True` с выключенным архивом не обновляет задачи ни на stream-ready, ни периодически; оба переключателя независимы в Settings. (4) local accuracy whole-game может использовать подтверждённую терминальную доску с отсутствующей оценкой, но `build_move_facts` требует `after.depth` и оставляет per-move последнего матового хода NULL.

**Исполняемое исправление:** [**REVIEW-FIX-07 RF7-01…RF7-05**](specs/ARCH_UX_01_REVIEW_FIX_07_LEGACY_METRICS_WDL_AUTO_SYNC.md) в docs-only draft PR #45. Исполнять точечно в существующем code draft PR #44: legacy SQLite red→green fixtures, миграция/доначисление только из сохранённых позиций (без UCI), WDL оба терминальных победителя, 4 комбинации auto-flag, детерминированный terminal last-move accuracy, full tests, native GUI и новый exact-head Windows CI. Старые RF1–RF6 не повторять. **Нет разрешения на merge в main; физическая приёмка владельца далее.**

---
## 05.11.2026 — дополнение к REVIEW-FIX-06: формула Lichess Accuracy + форсированный мат

Исследованы **официальная lichess.org/page/accuracy** и первоисточники lila **AccuracyPercent.scala**, **tree/eval.scala**, **Advice.scala** и scalachess **eval.scala** (SHA blob зафиксированы в [LICHESS_STOCKFISH_ACCURACY_MATE_V1](specs/LICHESS_STOCKFISH_ACCURACY_MATE_V1.md)). Подтверждены sigmoid Win% с CP clamp ±1000, move accuracy по изменению Win% с **+1 uncertainty bonus** в текущем Lichess, volatility-weighted/harmonic game accuracy, специальные Mate/Mate(0)/MateGiven без выдуманных CP. **Код PR #44 на SHA 881a8d8f… уже имеет расчёт точности полной партии в domain/accuracy.py, фактически вызываемый lichess/game_review.py; дублировать его нельзя.** Найдены конкретные пробелы: move_metrics.py записывает **accuracy=None для всех ходов** и severity unknown для mate переходов; position_analysis.py генерирует искусственное score(mate_score=100000); ui/game_review.py делает пустой advantage bar при любой матовой оценке. При этом полная Postgame analysis сохраняет CP и Mate раздельно корректно.

По решению владельца в [REVIEW-FIX-06](specs/ARCH_UX_01_REVIEW_FIX_06_INDEPENDENT_REVIEW.md) добавлены **RF6-09…RF6-11**: проверка формулы по актуальному Scala и golden fixtures; per-move local accuracy и события форсированного мата без подмены CP; Review/Progress/vertical bar/coverage с разделением server official vs Chess Coach local. Codex, уже получивший RF6-01…08, должен перечитать обновлённое ТЗ и выполнить все **RF6-01…11** одним циклом в draft PR #44, без merge/main, с новым exact-head Windows CI и физической приёмкой. Статус программных исправлений RF6-09…11 — **ТЗ создано, код ещё не изменён**.

---

## 05.11.2026 — независимое ревью RF5: Windows CI 687 PASS, **к слиянию НЕ ПРИНЯТО**, активное REVIEW-FIX-06

Проверен exact кодовый HEAD `881a8d8f49c2cbdae7ffb1f5c4d79d0f091bafc9` в [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44): GitHub [Windows run #41945044572](https://github.com/feshakomissarov-prog/chess/actions/runs/41945044572) **completed/SUCCESS** на том же SHA, **687 passed**, compileall и offline window PASS. Реальный Lichess/PAT/пользовательские партии не проверены (BLOCKED), физическая приёмка владельца не дана.

Сопоставление `f957f994… → 881a8d8f…`, актуальных source paths, согласованного UX/ARCH-UX и тестов выявило существенные дефекты **за рамками 687 PASS**. Подтверждено: `service_sync.py` передаёт фоновую ошибку 401/403 строкой «Ошибка получения готовой аналитики…», а `window.py::_lichess_status_clears_authenticated_identity` воспринимает `Ошибка` как повод сбросить account/stream; кнопка `Отключить` привязана к online-actions readiness и становится disabled во время pending/429; новый Home переключает ещё отсутствующий в combo аккаунт в «Все локальные партии» до регистрации на stream-ready; переход Recent→Library ищет target только в одной текущей странице и теряет ID при сохранённых фильтрах; ранее работающий periodic archive+puzzle auto-sync (10 min, auth/finish) удалён; временный safety 429 не имеет гарантированного UI-доступного retry. Также проверить collapsed 60px sidebar и safety/provenance tooltip. Эти ошибки не закрыты тестом «переход открыл Library» и кодовым smoke.

**Исполняемое ТЗ:** [**ARCH-UX-01 REVIEW-FIX-06 RF6-01…RF6-08**](specs/ARCH_UX_01_REVIEW_FIX_06_INDEPENDENT_REVIEW.md), в docs-only draft PR #45. Codex продолжает только текущий code draft PR #44, не переигрывает предыдущие RF, подтверждает red→green тесты, новый exact-head Windows CI и native Qt smoke; далее независимый review и физическая приёмка владельца. **Никаких main/merge, PAT/PGN/личной SQLite, обхода Fair Play.**

---

## 05.11.2026 — Windows-пилот: продукт НЕ ПРИНЯТ; новое единое REVIEW-FIX-05

Пользователь прислал **три снимка физического Windows GUI** (Главная с пометками №1–8; Играть; поведение после повторного подключения по сохранённому PAT). Выявлены не соответствующие утверждённым UX-B1/ARCH-UX: глобальный Back на корне Главной, перегруженная/обрезанная шапка со счётчиками backfill, лишний фильтр контроля времени на Home, сохранённая аналитика «скрыто» при подключённом аккаунте без автоматической safety-проверки, подмена «Следующего занятия» помесячным трендом задач, многословные 3 карточки Recent без видимого «Все партии» и прямой переход карточки в Review вместо деталей. В «Играть» владелец одобрил форму, но обнаружил несогласованные признаки connected и фактической готовности создать AI партию/переподключиться.

Независимый source audit PR #44 на SHA `f957f994275933e0b6eb19134fa8cb34c39286e3` выявил: `MainWindow._lichess_authenticated` устанавливает `_lichess_connected=True` до подтверждения stream-ready; `_dashboard_analysis_safety_allowed` сбрасывается, а автоматический `_request_dashboard_display_safety` после авторизации/stream-ready **не стартует**; `_analysis_safety_error` маскирует код ошибки как `type(message).__name__` (для строки «str»); UI валидности `/api/account/playing` и official AI signature требуется отдельный тест; `HomePage.dashboard_clock`, 4 KPI вместо 2–3, `activity_trend` в «Занятии», `_open_saved_game` на клике Recent, скрытый `home_games_button`, `header.lichess_analysis_status` со 210px и дублирующая верхняя кнопка Настроек. Это **проверенные по коду факты/риски**, не доказательство одной конкретной сетевой ошибки на машине владельца.

**Нормативное исполняемое ТЗ:** [ARCH-UX-01 REVIEW-FIX-05](specs/ARCH_UX_01_REVIEW_FIX_05_WINDOWS_UX_LICHESS.md) — RF5-00…RF5-12: UI корневой навигации, header/sidebar, Lichess OAuth/PAT/reconnect+readiness, single-flight fail-closed safety preflight/маскировка, реальная Главная, сохранение одобренной Play form, все шесть маршрутов и 24 группы функций, native Windows screenshots, точный новый CI и повторная ручная приёмка. Исполнять **в одном существующем code draft PR #44**, документы — в docs-only draft PR #45; прошлый [CI #253](https://github.com/feshakomissarov-prog/chess/actions/runs/41922206819) **675 PASS** относится к коду **до этого исправления**. Реальное подключение владельца до сих пор **не подтверждено тестом**. Не трогать пользовательские SQLite/PAT/PGN и не выдавать fake HTTP за реальный Lichess. Оба PR draft, no merge.

---

## 05.11.2026 — независимая проверка RF4: код принят к физической Windows-проверке

Проверен итоговый SHA кодового [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44): `f957f994275933e0b6eb19134fa8cb34c39286e3`. Hosted [Windows CI #253](https://github.com/feshakomissarov-prog/chess/actions/runs/41922206819) **completed/SUCCESS**, **675 PASS**, compileall и isolated offline window; прежнего предупреждения `PytestUnhandledThreadExceptionWarning` в полном логе нет. Независимо просмотрены source-filtered historical/opening current-line score контракты, отдельные состояния пустых технических анализов, app-owned `StatisticsWorkerHub`, generation/close guards, барьерные тесты и ожидание потоков при закрытии MainWindow. **RF4-01/02 принимаются по коду и CI; новых подтверждённых блокирующих дефектов не найдено.** Статус кодового PR: **разрешена ручная физическая Windows-приёмка владельца, не merge**.

До окончательного разрешения владельца остаются реальные Lichess/OAuth, персональная SQLite (только владельцем), физический UI/DPI, кликабельность доски, часы и завершение партии, звук, настоящий Stockfish, синхронизация/оценки и согласованность шести разделов. PR #44 и docs-only PR #45 сохраняются draft. `main` не изменялся; **никакого автоматического merge**.

---

## 05.11.2026 — independent review of REVIEW-FIX-03 · REVIEW-FIX-04

Проверен [кодовый draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) на exact HEAD `e102167d89ff1702756f4d42eaa8d17f4906d410`. [Windows CI #250](https://github.com/feshakomissarov-prog/chess/actions/runs/41908931616) **completed/SUCCESS**, 669 passed, compileall и isolated offline window; **1 подтверждённое PytestUnhandledThreadExceptionWarning** в stats worker (`Signal source has been deleted`), видно в настоящем CI log. **RF3-01/RF3-02 реализованы. PR #44 пока не принят.**

Создано [адресное ТЗ REVIEW-FIX-04](specs/ARCH_UX_01_REVIEW_FIX_04.md): **RF4-01** — source-filtered historical timeline и дебютные срезы должны иметь те же current-line CP/mate game counts, что Home/Library/Progress, без посторонних source и empty completed runs; **RF4-02** — исправить реальную гонку закрытия `StatisticsDialog`/MainWindow и фонового `chess-coach-statistics`/opening worker, не подавляя сигнал-ошибку. Codex продолжает только текущий draft PR #44, тесты + новый Windows exact-head CI, затем независимое ревью и ручная Windows-приёмка. ТЗ — docs-only draft PR #45. Ничего не слито.

---

## 04.11.2026 — повторное независимое ревью RF2, REVIEW-FIX-03

Кодовый [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) на SHA `97785aa8a2d6697d06c0e012480f26b8103a104f`: hosted Windows [CI #247](https://github.com/feshakomissarov-prog/chess/actions/runs/41887011647) **SUCCESS** (666 passed, compileall и isolated offline window). По коду RF2-01…RF2-04 внедрены, но независимая приёмка **пока не дана**: в `StatisticsDialog` источник «Lichess» всё ещё жёстко означает `lichess-pgn`, без `lichess-json`, а `StatisticsService.summary` рассчитывает `analysis_games` по completed/partial runs, не требуя CP/mate, в отличие от каталога/Home. Сохранён адресный [REVIEW-FIX-03](specs/ARCH_UX_01_REVIEW_FIX_03.md), RF3-01…RF3-02. Поправить в том же draft PR #44 с регрессиями и новым exact-head Windows CI; прежние этапы не повторять, main и пользовательские данные не менять. Физический Windows pilot владельца — после успешного ревью. Docs-only PR #45 не сливать.

---

## 04.11.2026 — независимое ревью исправлений RF-01…15, дополнительное REVIEW-FIX-02

Проверен фактический SHA **`c93712ab92c6845cd1b0db28832a9f75e4332171`** кодового [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44). Hosted Windows [CI #245](https://github.com/feshakomissarov-prog/chess/actions/runs/41879012568) **completed/SUCCESS** на этом SHA: offscreen tests, compileall и isolated offline launch; отчёт Codex сообщает **658 PASS**. От предыдущей проверки RF-01…RF-15 внесены адресные исправления. **Независимая приёмка: ПОКА НЕ ПРИНЯТО.**

Создано [ARCH-UX-01 REVIEW-FIX-02](specs/ARCH_UX_01_REVIEW_FIX_02.md) на четыре дополнительные проверки/исправления **RF2-01…RF2-04**: live Lichess clock при законно накопленном increment; планировщик фоновой загрузки при day reset/Retry-After и отличие cumulative checked от batch checked; недублируемые full/partial KPI + единое определение анализа в фильтре; понятное отличие последнего хода от начала партии в сортировке. Это **не повтор** A0–A12 или RF-01…RF-15. Применить в **том же draft PR #44** с тестами и новым exact-head Windows CI; кодовый HEAD при создании ТЗ не изменяли. Ручной Windows pilot и разрешение merge — позже.

---

## 04.11.2026 — физический Windows-пилот: REVIEW-FIX-01 v1.2 (актуальнее нижних записей)

Владелец проверил реально запущенный ARCH-UX-01 из [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) (проверяемый SHA `2b1c724b4503140992ad8d80f8c5044c861eb7cd`), прислал шесть скриншотов: Главная (дата и 3 recent, метрики coverage), Игра (доска/часы), Review (отсутствует сравнимая точность), «Мои партии» (хронология, формат времени, поиск Каро-Канн, источники Lichess/Stockfish и прозрачность фонового backfill), Массовый анализ (перегруженная UI-страница). **Код пока НЕ принят**.

Уточнено **единое исполняемое корректирующее ТЗ [ARCH-UX-01 REVIEW-FIX-01 v1.2](specs/ARCH_UX_01_REVIEW_FIX_01.md)**: прежние RF-01…RF-07 + RF-08…RF-15 с точными проверками дат/архивных счетчиков, доли *партий* с анализом, игрового viewport/clock provenance, локализованного открытия, stockfish vs lichess-pgn vs lichess-json, тихого resumable backfill, source-backed точности по опубликованной Lichess формуле (включая мат и неполный охват), компактного batch UX и общей cross-screen консистентности. Это точечные правки **одного существующего кодового PR #44**, а не запуск нового A0–A12 / переписывание монолита.

**Важно:** 3 карточки Home — согласованный лимит, не доказательство отсутствующих партий. Данные `3302/3304` и `1122/156448` требуют проверки определений, account scope и актуальности; никаких утверждений о потере данных без SQLite/архивного read-only аудита. Готовые Lichess позиции/official accuracy нельзя автоматически получить для всех 3302 игр, их существующий backfill оставлять rate-limited, не запускать server-side анализ. Для точности различать *официальную серверную* и *неофициальный локальный расчёт по методике Lichess*, с охватом и Fair Play gate.

Документ обновлён в **docs-only draft PR #45**, runtime код не тронут; [CI #244](https://github.com/feshakomissarov-prog/chess/actions/runs/41774974147) подтверждает **старый** кодовый HEAD, после Codex требуется новый exact-head Windows CI, новые тесты, повторное независимое ревью и физическая Windows-проверка владельцем. PR #44 и #45 не сливать без отдельного разрешения.

---

## 04.11.2026 — ARCH-UX-01 independent review: REVIEW-FIX-01

Проверен кодовый [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) на SHA `2b1c724b4503140992ad8d80f8c5044c861eb7cd`; Windows [CI #244](https://github.com/feshakomissarov-prog/chess/actions/runs/41774974147) SUCCESS на этом SHA, handoff сообщает 632 local PASS. **Решение: КОД ПОКА НЕ ПРИНЯТ.** Сформировано [исправляющее ТЗ ARCH-UX-01 REVIEW-FIX-01](specs/ARCH_UX_01_REVIEW_FIX_01.md), RF-01…RF-07: безопасность cached official accuracy, полнота редактора FEN, ephemeral review state, custom clock, игровые даты, async read selectors, финальные UX/lifecycle regression gates. Это **адресная корректировка**, без повторного W0–W9/A0–A12 и без нового кодового PR.

ТЗ размещено в **docs-only draft PR #45**, чтобы сохранить исходный проверенный HEAD кодового PR #44; Codex по прямой передаче владельцем вносит исправления **в существующий PR #44**, затем новый Windows CI на **новом** точном SHA и повторное независимое ревью. Оба PR остаются draft, **не merge**. Физическая проверка Windows/DPI/звука/реального Lichess и Stockfish ещё не выполнялась.

---

## 04.11.2026 — финальный предпусковой аудит ARCH-UX-01 v1.1

**ГОТОВО ТЗ К ПЕРЕДАЧЕ CODEX, НЕ ГОТОВ К ПРИЁМКЕ КОД:** [единое ARCH-UX-01 v1.1](specs/ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md) полностью перепроверено по согласованным UX и source текущего кодового [PR #44](https://github.com/feshakomissarov-prog/chess/pull/44). [Аудит с 14 адресными находками](reviews/ARCH_UX_01_PRE_FLIGHT_AUDIT.md) фиксирует Final-Game `final_sync_pending`/pending SQLite write, единый fail-closed Fair Play во всех новых views, single-session navigation, обычный premove vs ненужную тренировку, pilot import UI исключения, звуки/QSettings, связность CP/Δ/AdvantageBar, сохранённый 720 размер доски, оконные/Qt boundary и bounded UI data loading. **Никаких обязательных новых benchmark/SLA, Rust или микросервисов**; ADR-0001 сохранён. Опубликованы актуальные AGENTS, PCD, PRD, ROADMAP, PRODUCT_VISION, архитектурный ADR/target, UX документы, README и ТЗ.

Код **не менялся** этим docs-only [PR #45](https://github.com/feshakomissarov-prog/chess/pull/45), Codex ещё не запущен. PR #44 остаётся draft на известном 9a59b21 с **604 PASS только для существующего кода**, не новых экранов. По прямой передаче владельцем Codex обязан проверить HEAD и реализовать этапы A0–A12 в одном кодовом PR (предпочтительно продолжить #44), затем новый exact-head Windows CI, независимый review и физическая приёмка; **no auto merge**. Исторические handoff ниже не отменяют этого решения.

---

## 04.11.2026 — единое ARCH-UX-01 ТЗ для desktop Codex сформировано

**Решение владельца:** не запускать отдельные архитектурный и UI кодовые циклы: использовать существующий [ARCH-01 draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) (9a59b21, Windows 604 PASS, **не слит**) как проверенную модульно-монолитную базу; выдать **одно большое ТЗ**, которое адаптирует архитектуру к утверждённому интерфейсу. Создано [ARCH-UX-01 — единое исполняемое ТЗ](specs/ARCH_UX_01_UNIFIED_EXECUTION_SPEC.md), этапы A0–A12, матрица 24 функций, Fair Play, SQLite/сеть/engine, полный UX, тесты и Windows pilot. Рекомендация реализации: Codex продолжает **один существующий кодовый draft PR #44**, получает UX docs из PR #45 выборочно (не merge docs ветки), объединяет изменения в одном цикле; никакого rewrite с нуля/микросервисов. Если PR #44 уже изменился к началу, зафиксировать новую точку и исключить дублирование.

Утверждённые P0/P1: live fairness/game state/данные важнее UX; предходы обычные AS-IS, отдельная тренировка/пилотный импорт не выводятся; спецкоманды visible right; finish на той же доске, обе CTA; звуки/графики/фоновые операции без функциональной потери. Не реализовывать новые GPT/ML/voice/полный explorer/персональные курсы. Документы обновлены в [docs-only draft PR #45](https://github.com/feshakomissarov-prog/chess/pull/45), **без изменения runtime кода и без merge**. Передача задания Codex — отдельное действие владельца, после него Codex обязан вести handoff и PR draft.

---

## 04.11.2026 — уточнения владельца по сохранению функциональности при UX редизайне

Владелец согласовал размещение второстепенных функций, но **отклонил перенос исторических тестовых кнопок**: отдельная «Тренировка предходов» и пробная загрузка 5–10 партий/попыток в новом UI не нужны. **Обычные предходы и их текущая реализация сохраняются.** Заметки остаются второстепенными; полная, инкрементальная, автоматическая и ручная синхронизация, лимиты, пауза и диагностика — обязательны; обновление одной партии Lichess — лишь диагностическое восстановление, не основное действие. Повторное решение задач, история и ответы сохраняются, конкретную структуру страницы задач обсуждаем позже. **Возврат хода, ничья, ответы на предложения, сдача и допустимые спецкоманды размещаются на видимой правой панели игры**, не в меню «Ещё». Критические моменты, фазы, графики/оценки и звуки AS-IS обязательны.

Приоритетный контракт: [UX-B1 §K](ux/UX_APPROVED_BASELINE_V1.md), [UX-GAME-01](ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md), [аудит 24 групп с исключениями](ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md). Исторические пункты ниже про «перенести тренировку/пилоты в Advanced» **заменены этим решением**. **PR #44 ARCH-01**: exact `9a59b21`, Windows 604 PASS, остаётся draft и требует решения владельца/физической проверки; **PR #45** — только UX docs, draft. Архитектурные последствия редизайна обсудить отдельно **до** запуска следующего Codex задания; это не разрешение переписывать модульный монолит на микросервисы или менять код.

---

## 04.11.2026 — финализирован игровой экран; аудит 24 существующих функций перед UX редизайном

**Утверждено владельцем:** на финальном ходе **никуда не переходим**, не показываем отдельный результат/оверлей/попап; та же доска и список ходов, часы остановлены (или режим без контроля), справа только **«Разобрать»** и **«Вызвать снова»**. Весь ходовой интерфейс продолжает работать; новый вызов только по нажатию, не утверждать native rematch без API. [Полный финальный wireframe](ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md). «Анализ» (вариант B), вертикальная шкала оценки, исходный интерактивный CP/Δ-график подтверждены в [UX-A2](ux/UX_ANALYSIS_APPROVED_V2.md). **Звуки: сохранить полностью существующие WAV/default/выбор/пресеты/громкость/QSettings без изменений.** Внутреннее обучение пока сознательно не детализируется; существующую историю задач и тренировки не терять.

**Проверен исходный код PR #44 exact `9a59b21bb91adbada7b58baf9dff45d0afccb27d`:** [аудит 24 групп работающих функций](ux/EXISTING_FEATURES_TO_UX_COVERAGE_AUDIT.md) сопоставляет реальные точки входа с новой IA и выявляет важные мелкие возможности (тренировка предходов, заметки и выборочное обновление партий, история пазлов/повторение, локальное обновление классификации, фазовая/критическая аналитика, архивные пилоты и diagnostics). В новом UX **нельзя silently delete**: сохранять и разместить в логичных «···»/advanced routes; передать покрытие как обязательный contract в последующее большое Codex-ТЗ, которое **пока не составлено и не отправляется**. [Draft PR #45](https://github.com/feshakomissarov-prog/chess/pull/45) — только UX docs, [draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) — архитектура, **не слиты**.

---

## 04.11.2026 — проверка ARCH-FIX; утверждён «Анализ» и поведение после партии; идея подготовки к сопернику

**PR #44 ARCH-01:** новый head `9a59b21bb91adbada7b58baf9dff45d0afccb27d`, [Windows CI #41741484833](https://github.com/feshakomissarov-prog/chess/actions/runs/41741484833) — SUCCESS, 604 PASS + compileall/offline init. Повторная независимая проверка подтвердила целевые исправления ARCH-FIX-01–04. **Технический ОК к следующей ручной/совмещённой UX-приёмке, но PR остаётся draft, не слит**. По решению владельца не отправлять сейчас отдельный пакет будущих небольших изменений Codex: собрать их позднее вместе с единым UX implementation-ТЗ, чтобы не тратить время на заменяемый интерфейс; никаких ослаблений Fair Play.

**UX в draft PR #45:** утверждён **Вариант B раздела Анализ** ([UX-A2](ux/UX_ANALYSIS_APPROVED_V2.md)) — три главные карточки, внутри разбора PGN из архива/файла/буфера, FEN и ручной редактор; рядом с доской **узкая вертикальная шкала текущей оценки**, снизу **сохранён текущий интерактивный график** с режимами изменения оценки/Δ, CP/mate, маркерами ошибок. Новый сценарий игры: **после окончания не перенаправлять автоматически**, сохранять финальную позицию и список ходов, давать ждать документированно поддерживаемый реванш/повторный вызов и **самостоятельно** перейти к разбору; [wireframe и состояния UX-GAME-01](ux/GAME_WORKSPACE_AND_FINISH_STATES_V1.md). Логика clocked/no-clock, сеть, Fair Play подробно описаны. Личные учебные KPI и детальный opening explorer будут обсуждаться дальше.

**Дальний продуктовый горизонт:** пользователь предложил исследование **публичных партий конкретного соперника Lichess** (первоначально до 500 завершённых), дебютного репертуара, вариантов и результатов **раздельно по цветам**, в дальнейшем глубокая подготовка в отдельных ветках. Предварительный дизайн, статистические ограничения, разрешённые источники и безопасность — [исследовательский документ](research/OPPONENT_PREPARATION_LONG_TERM_FEATURE.md); ROADMAP помечен как будущая, **не ближайшая** функция.

**Это фиксация дизайна, НЕ старт реализации UX.** Не merge PR #44 или #45 без отдельного согласия владельца.

---

## 04.11.2026 — UX: утверждена основа меню и страниц, спорные сценарии анализа открыты

**Согласовано владельцем:** шесть рабочих пунктов **Главная · Играть · Мои партии · Анализ · Обучение (включая Задачи) · Прогресс**, Настройки отдельно. Постоянная левая навигация и профиль Lichess, одно desktop окно с внутренними страницами. Главная — четыре блока без глобальной прокрутки. «Играть» — согласованная внутренняя форма AI/друг по ссылке/ник/локальная доска, контроль/цвет/уровень. «Мои партии» — верхний статус синхронизации и разных источников L/S, центральный скроллируемый список с **активными партиями всегда в верхнем приоритете**, справа фильтры; подробности завершённой партии включают **компактный график всей игры**, source/coverage, открытие/разбор/PGN. Обучение с задачами и планом; отдельный Прогресс с будущими настраиваемыми метриками; настройки и рабочий игровой экран — согласованы концептуально. Все утверждённые поля, действия, статусы, границы безопасности и Markdown-wireframes в **[UX-B1](ux/UX_APPROVED_BASELINE_V1.md)**.

**Не утверждено:** внутри Анализа точные сценарии «Разбор партии» (мои/внешний PGN/временный просмотр) и «Анализ позиции» (FEN/редактор/отдельный инструмент). «Массовый анализ» и «Исследование дебютов» согласованы как направления. UI для персональной статистики обучения/прогресса и будущего репертуара детализируем позже. Главный [живой UX-документ](ux/CHESS_COACH_FULL_INTERFACE_WORKING_DRAFT.md) помечает утверждённое и открытое по отдельности. **Никаких Codex UI задач пока не выдавать; draft PR #45 не merge.** Архитектурный PR #44 — независимая работа.

---

## 04.11.2026 — начата комплексная проработка UX всей программы (обсуждение, не реализация)

Владелец предоставил эскизы Главной (4 области) и «Моих партий» (верхняя синхронизация / средний scrollable список с приоритетом активных / правая фильтрация) и согласовал основу внутренней страницы «Играть». С ChatGPT прорабатываются **каждая страница, карточка, отдельное рабочее пространство, переходы, фильтры, пустые/ошибочные состояния, данные/источники и адаптация ноутбука**. Общая страница Главной/Архива не скроллится; внутренняя лента партий — да. Утверждён пока **подход**, не окончательная IA: обсуждается вариант шести главных разделов (Главная, Играть, Мои партии, Анализ, Обучение [вкл. Задачи], Прогресс + Настройки отдельно), а также обязательное разделение хранилища партий и рабочих инструментов анализа. Не объявлять эти предложения уже утверждённым итоговым ТЗ.

**Рабочая концепция:** [CHESS_COACH_FULL_INTERFACE_WORKING_DRAFT.md](ux/CHESS_COACH_FULL_INTERFACE_WORKING_DRAFT.md). Нынешний документ intentionally living; после согласования всех экранов будет отдельное полное ТЗ Codex для нового UX PR. Приложение, SQLite, Fair Play и методики расчётов сейчас не изменяются. [ARCH-01 draft PR #44](https://github.com/feshakomissarov-prog/chess/pull/44) живёт отдельно от UX и не считается принятым/слитым. Исходные нарисованные владельцем изображения не публиковать в репозитории без отдельного решения.

---

## 03.11.2026 — принято целевое архитектурное решение ARCH-01

После объединения PR #41 (M4.6) и PR #42 (утверждённая модель компетенций) владелец согласовал **модульный монолит Python 3.12 + PySide6**: один Windows desktop продукт, внутренние независимые модули, единую координацию асинхронных заданий без глобальной очереди для живых ходов, чёткие транзакционные SQLite-границы, отдельный менеджер Stockfish, непрерывный fail-closed Fair Play и обезличенную диагностику. Целевая платформа — обычный Windows-ноутбук 4 CPU cores / 8 ГБ RAM / SSD; Rust остаётся возможной *точечной* будущей оптимизацией при доказанном bottleneck. Обязательные benchmarks/latency SLA **сейчас отложены по решению владельца**.

[Целевая архитектура](architecture/CHESS_COACH_TARGET_ARCHITECTURE.md) · [исполняемое Codex ТЗ ARCH-01](architecture/ARCHITECTURE_REFACTOR_EXECUTION_SPEC.md) · [ADR-0001](architecture/ADR-0001-MODULAR-MONOLITH.md). **ARCH-01 пока не реализован**: Codex должен открыть отдельный draft PR от актуального main, затем независимое code review и физический Windows pilot владельца; merge только после нового разрешения. Новые фичи GPT/дебютов/ML/voice и ROADMAP в рамках архитектурного этапа не изменять.

---

## 03.11.2026 — M4.6 объединена с main; утверждённая модель Chess Coach

**Код M4.6:** [PR #41](https://github.com/feshakomissarov-prog/chess/pull/41) объединён по разрешению владельца после ручной проверки. Проверенный head `2c3ccbd99c0c33526fb2bd996ab777a0ec5c8d31`, [Windows CI #41625429734](https://github.com/feshakomissarov-prog/chess/actions/runs/41625429734) — SUCCESS, 553 PASS; merge commit `27b42d3dc482efeff3c64ad3399f96bc74fcb041`. Физическую ручную проверку выполнил владелец; автоматическое тестирование не заменяет проверку настоящего Lichess, устройства звука и DPI.

**Продукт:** утверждена [модель A × (B1+B2) → C → D → E](research/CHESS_COACH_APPROVED_COMPETENCY_MODEL.md), [девять исследовательских направлений](research/CHESS_COACH_NINE_FOLLOWUP_DIRECTIONS.md), рабочая карта [17 семейств / 263 подтем](research/CHESS_COMPETENCY_MODEL_STRESS_TEST.md). Шахматные диагнозы — только на проверенных фактах с явным покрытием; голос после полноценного интерактивного обучающего тренера. ROADMAP намеренно не обновлён. Следующий отдельный этап — архитектурный рефакторинг без изменения пользовательских функций, после которого развиваются новые образовательные модули.

---
## 02.11.2026 — M4.6 в разработке

Начата отдельная ветка `codex/m46-desktop-experience` от актуального main (`3387dd38643eaa4ea2fac17fa17ba08cfc43e47f`); draft PR M4.6 будет отдельным от слитого PR #30. Обязательный первый этап — закрытие race #40 по S0–S2; далее A–H по [ТЗ M4.6 v1.1](specs/M4_6_DESKTOP_EXPERIENCE.md). Актуальный поэтапный handoff: [M4_6_EXECUTION_LOG.md](handoffs/M4_6_EXECUTION_LOG.md). Реальные секреты/Lichess, личная SQLite/архив и Stockfish не используются в тестах.

Независимое ревью PR #41 P0–P2 исправлено в той же ветке. P0 закрывает Fair Play lease race и скрывает сохранённые оценки/PV в unsafe review; P1 добавляет account/clock-scoped home read-model и карточки, основную навигацию «Играть/Разобрать», полную ось sparse-графика и сохранённые события/PV с safety gate; P2 фиксирует cwd CMD-launchers и регрессии для фильтров/layout. Windows hosted CI на SHA `1bd888ffc124157221136db2ead9d78b59d89794` [успешен](https://github.com/feshakomissarov-prog/chess/actions/runs/41378791447): **439 PASS**, compileall и isolated offline window initialization PASS. Полный локальный offscreen Windows/Python 3.12.10 прогон — также **439 PASS**, `pip check`/diff check PASS. Этот статусный commit документационный; после него запускается exact-head CI. Реальные OAuth/Lichess, пользовательская SQLite/архив и Stockfish не запускались. Тот же draft [PR #41](https://github.com/feshakomissarov-prog/chess/pull/41), merge не выполнялся.

## 02.11.2026 — M4.5/M4.5.1 принят пользователем и слит; следующий этап M4.6

**PR #30 слит в main**: [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), merge SHA `39f4a703c50b5aed7976eb95abf28ed2ee9799cf`. На точном кодовом SHA `4e6af52d01dd637a4afee4716960a8b343524556` [Windows CI #110](https://github.com/feshakomissarov-prog/chess/actions/runs/41360769481) — **375 PASS**, compileall/offscreen. Владелец в реальном Windows-пилоте подтвердил, что Stockfish анализирует партии, график оценки построен. Он также видел отдельные ошибки jobs; причины не установлены. Обычный «Открыть сохранённую партию» показывал доску/ходы без синхронного графика (разбор был в отдельном StatisticsGameDialog). Эти ограничения/UX должны быть устранены/прозрачно отображены в M4.6, не возобновляя бесконечную M4.5 правку. Реальное повторное подключение OAuth/PAT и серверная причина прежнего HTTP 403 не подтверждены окончательно; не писать, что всё OAuth исправлено на живом сервере. Ранее раскрытый PAT следует отозвать, токены не передавать в GitHub.

**План:** [M4.6 Desktop Experience v1.1](specs/M4_6_DESKTOP_EXPERIENCE.md) утверждено к реализации. Центральная функция — единый завершённый game review: доска/ходы/интерактивный график с метками good/inaccuracy/mistake/blunder/unknown, показатели **обеих сторон** с source/coverage и корректным «н/д» для отсутствующей официальной точности; **«Разобрать мои ошибки»** переносит в позицию непосредственно ДО моего ошибочного хода, предлагает найти лучший/другой доказанно приемлемый ход, минимальный fallback с одним проверенным улучшением. Также dark-v1, боковая навигация, размер доски, запуск двойным кликом без EXE, звуки, сохранение рабочего состояния.

**Технический долг — сознательно не блокировали merge:** [Issue #40](https://github.com/feshakomissarov-prog/chess/issues/40), auth/PAT async disconnect + stale stream; устранить первым шагом M4.6 по [S0–S2](reviews/PR30_C0_C2_SWITCH_DISCONNECT_RACE_FIX_SPEC.md). До исправления не переключать метод и отключение в быстрой последовательности. Отдельная ветка `codex/m46-desktop-experience` от main, отдельный draft PR, CI на точном финальном SHA и ручной визуальный тест владельца. M4.7 обучение остаётся черновиком до завершения M4.6, M5 голос — после обсуждения обучения.

---

## 02.11.2026 — PR #30 C0–C2: Lichess credentials and HTTP 403 diagnostics

В `codex/m45-batch-analysis` продолжается существующий draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`; M4.6 не начинался. C0 реализует OAuth PKCE и отдельный PAT connection flow с Credential Manager слотами `oauth-access-token` и `personal-access-token`; выбранный enum хранится в QSettings. C1 добавляет безопасную классификацию/диагностику account 401/403/429; реальная причина 403 не установлена без разрешённого живого запроса. C2 добавляет регрессии на credential isolation, validation-before-save, auto-restore, late worker results, UI masking и отсутствие секретов в логах/QSettings.

Windows CPython 3.12.10 / Qt offscreen локально: **375 тестов PASS**, `compileall`, `pip check`, `git diff --check` PASS. Windows Actions run [#109](https://github.com/feshakomissarov-prog/chess/actions/runs/41360194914) на SHA `6ae0ec7ec4d590d46639c1d86edafeb92d1dc942`: **SUCCESS, 375 passed**, compileall и isolated offline window initialization PASS. Последующий docs-only итоговый commit должен пройти exact-head CI отдельно. Реальные OAuth/Lichess, пользовательская SQLite/архив и Stockfish не использованы. Подробности и ручные шаги — в [M4.5 execution log](handoffs/M4_5_EXECUTION_LOG.md). Владелец должен отозвать ранее раскрытый PAT на Lichess до использования и только локально создать новый минимальный PAT; не присылать секрет в переписке.

---

## 02.11.2026 — идеи будущего учебного режима сохранены, код не начинался

[Черновик M4.7 Training Arena и GitHub-референсы](research/M4_7_TRAINING_ARENA_IDEAS_AND_GITHUB_REFERENCES.md): локальная игра от выбранной позиции против Stockfish, уровни помощи, дебютный sparring, оценка и сохранение собственных планов/стрелок, повтор без подсказок. Изучены сохранённые репозитории Opening Trainer, Tabia, Blitzr, CARA, DaisyChess, Maia, qam4/chess-coach, Mainline, Chesser. **Решение: пересмотреть и скорректировать идеи после реализации/приёмки M4.6, перед началом M5 (голос).** Это не утверждённое техническое задание, в текущем draft PR #30 ничего не меняется. Учебная engine-помощь — только в отдельном безопасном локальном режиме; Board API Lichess остаётся без engine hints во время игры.

---

## Решение о следующем этапе 01.11.2026 — M4.6 Chess Coach Desktop Experience (ТЗ готово, код НЕ начат)

После ручного Windows-пилота M4.5 пользователь согласовал оформление **тёмного шахматного кабинета**: единый стиль, боковое меню с automatic collapse на доске, стандартная **средняя доска 480dp** с настройками 400/560/640, точные цвета и отступы, 3 информативные карточки партий (accuracy, own errors, blunders, confirmed strong moves, coverage), продолжение активной партии/разбора, звуки ходов, запуск через ярлык/.cmd без обязательного EXE, масштабирование, сохранение рабочего состояния, фундамент для M5/M6.

**Source of truth для дизайна:** [полное ТЗ M4.6](specs/M4_6_DESKTOP_EXPERIENCE.md) (только текст, точные HEX, px, wireframe и acceptance tests). **Очередность:** сначала закончить текущий [draft PR #30](https://github.com/feshakomissarov-prog/chess/pull/30) по [M4.5.1](specs/M4_5_1_WINDOWS_MANUAL_UX_FOLLOWUP.md); только после review/приёмки/merge начинать M4.6 в новой feature-ветке/новом draft PR. Код M4.6, .exe и новая схема SQLite **не создавались** при записи этого handoff. M5 голос, M6 ИИ-тренер — позже. Дизайн-токены и слоты инспектора готовят совместимость с ними без реализации этих функций. Ручной пилот: 3291 партии в исходной базе, проверенный SQLite backup, не изменять личную БД без явного действия владельца.

---

## Handoff 01.11.2026 — M4.5 утверждено как большое самостоятельное задание (спецификация v2.0)

**Текущее решение:** владелец согласовал модель «сырой анализ → карточка партии → критические события → множественные тактические метки → агрегаты → направления обучения». Сначала новые партии, старые для истории; текущий профиль = недавние игры. Один Stockfish, быстрый массовый проход и **только по необходимости** лёгкое уточнение; расширенные классификаторы должны повторно работать по сохранённым фактам без массового UCI. В библиотеке важны средняя accuracy, counts ошибок/партию, по фазам и покрытие. Прежнее M4.5 v1 заменено более точным [ТЗ v2](specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md).

**Точка входа Codex:** [ТЗ v2](specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md) + [M4.5 execution log](handoffs/M4_5_EXECUTION_LOG.md). Кодекс самостоятельно выполняет все этапы 0–I в одной feature-ветке/draft PR с checkpoints, **не сливает main**, не выполняет реальных сетевых/персональных базовых операций без разрешения. Для восстановления после потери чата достаточно GitHub: AGENTS → ТЗ → журнал → текущий PR SHA. [Эпик #28](https://github.com/feshakomissarov-prog/chess/issues/28).

**Прямо перед исполнением:** M3–M4 уже в main через PR #26, docs roadmap PR #27 merged. SQLite текущей схемы v9; не пересоздавать игры, таблицы/архив; миграции только осторожные additive+backup/integrity. Известные условия и ограничения ручной приёмки — техдолг [#5](https://github.com/feshakomissarov-prog/chess/issues/5). ТЗ составлено и опубликовано; **реализация M4.5 ещё не начата** на момент этой записи. После старта Codex обновляет этот handoff и execution log фактическим состоянием.

---

## Решение 01.11.2026 — аналитика до голоса (M4.5)

Состояние: PR #26 **merged в main** (commit `586f08b7344cd181b696bfe9ac6c3a5cdf2685e4`), Windows CI **212 PASS**. Ручные smoke 6/6 и загрузка партий/задач владельцем выполнены для предшествующей версии; новые функции PR #26 не прошли отдельную ручную проверку. Технический долг сознательно отложен в [Issue #5](https://github.com/feshakomissarov-prog/chess/issues/5).

**Новый следующий крупный этап M4.5 — массовый анализ партий и доказательная персональная статистика** до M5 (голос) и M6 (ИИ-тренер). См. [ROADMAP](ROADMAP.md) и [единое ТЗ для Codex](specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md). Публичный документированный Lichess API позволяет читать готовые оценки, но не подтверждает массовый заказ серверного анализа: основной механизм вычисления недостающего покрытия — локальный Stockfish. Не запускать на пользовательской SQLite/реальном OAuth без отдельного решения владельца.

---

## Ручная приёмка и новый интеграционный кандидат — 01.11.2026

Владелец подтвердил 6/6 ручных GUI smoke-проверок и сообщил о полной
загрузке личных партий и попыток задач в свою SQLite.
Новые требования: колесо мыши для просмотра ходов, визуальный выбор превращения,
ферзь при предходе, удержание фигуры до отпускания мыши после ответа соперника,
автоматическое фоновое обновление архива и задач.

Интеграция переносит код всего стека до PR #25 на актуальный main с сохранением
исследовательской и продуктовой документации main. Новый код обязан пройти
Windows CI и отдельную проверку ручных сценариев. Автообновление использует
только ранее предоставленные OAuth-разрешения, без фонового открытия браузера;
--offline и режим до ручного подключения остаются без сети. Реальная SQLite не
является частью репозитория и должна иметь проверенную резервную копию до
нового локального обновления.

## Актуализация 01.11.2026 — PR #25 b2ef275: технический GO для ручного offline smoke (текущая)

**Последний проверенный head:** [draft PR #25](https://github.com/feshakomissarov-prog/chess/pull/25) `b2ef2752df027d6ff13fa93ef0ce8e8955c492e2` (base #24, всё ещё НЕ СЛИТ). [CI #41265056278](https://github.com/feshakomissarov-prog/chess/actions/runs/41265056278): **206 PASS за 128.21 с**, Windows/Python 3.12.10/Qt-PySide6-shiboken6 6.12.0, compileall и isolated offscreen GUI PASS.

**Итог независимого повторного ревью:** [PR25_B2EF275_FINAL_REVIEW.md](reviews/PR25_B2EF275_FINAL_REVIEW.md). Ранее найденная инверсия official AI/BOT устранена: `source='ai'` + `opponent.id=null` + `opponent.ai 1..8` — единственное явное исключение; BOT-account и неизвестные признаки fail-closed; серверная идентичность сохраняется после gameStart/account-playing и не подменяется gameFull/gameState. Ранее принятые Cancel, смена аккаунта, 429/лимит snapshot, миграция v9, 35k summary и UCI lifecycle прошли CI без новых регрессий. **Новых блокеров на изменённом SHA не выявлено.**

**Разрешена только следующая ступень: один ручной `--offline` GUI smoke владельца в отдельной Windows/Python3.12+ `.venv312`.** Полные команды и ожидаемые проверки — в ревью. `--offline` не читает OAuth и не открывает постоянную SQLite. Отдельно: пользовательские SQLite, реальные Lichess/OAuth, Stockfish, puzzles и интерактивная проверка обновлённого окна **NOT RUN**; локальный native crash Python3.11/shiboken6 не объяснён. **Не merge** до разрешения PR #8→main (всё ещё mergeable=false), проверки интеграционного SHA, реального пилота/backup и явного разрешения владельца. Дорожная карта M3/M4→M5→M6 не менялась.

## Актуализация 01.11.2026 — PR #25 SHA b027763, CI 200 PASS, критический fair-play нюанс (текущая)

**Подтверждённый head:** [draft PR #25](https://github.com/feshakomissarov-prog/chess/pull/25) `b027763de71bea6fb655e8a593471c0ae7590ba9`, base PR #24, **все PR #8–#25 не слиты**. [Windows CI run #41263521194](https://github.com/feshakomissarov-prog/chess/actions/runs/41263521194): **200 PASS за 95.92 с**, Python 3.12.10, Qt/PySide6/shiboken6 6.12.0, compileall и offscreen window smoke PASS. Три прежних P1 (Cancel connected, incomplete API list, stale session snapshot) по коду и fake/Qt тестам исправлены.

**Новый P0 fair-play:** [финальное независимое ревью](reviews/PR25_FINAL_SHA_FAIR_PLAY_REVIEW.md). По [официальным правилам Lichess](https://lichess.org/page/fair-play) разрешён engine при игре с **встроенным AI**, а **не** с обычным BOT-аккаунтом (Board API). В `window.py::_active_game_is_human` и `session.py::is_ai_opponent` стоит неверное исключение `opponent.title == 'BOT'`; официальный встроенный AI обозначается `source:'ai'` и `opponent.ai:1–8` с `id:null`. Текущая логика позволяет engine при титуле BOT, но блокирует при настоящем AI. Описаны точные регрессии и тесты; review оставлен [в PR #25](https://github.com/feshakomissarov-prog/chess/pull/25). **NO-GO merge / real Stockfish до узкого исправления в существующем draft PR25**. Шахматная fair-play проверка должна быть conservative при неизвестном типе соперника.

**Следующий шаг:** Codex исправляет классификацию official Lichess AI vs BOT и тесты (другие принятые миграции/статистику не переписывать), выдаёт новый SHA+CI; после повторного независимого аудита — **ручной offline smoke владельца**, затем согласованный пилот личной SQLite с backup и Lichess/Stockfish отдельно. PR #8→main ещё `mergeable=false`, без разрешения владельца ничего не сливать. Локальный shiboken6 crash не объяснён. Исследовательские документы и ROADMAP не менять этим техническим фиксом.

## Актуализация 01.11.2026 — независимый аудит PR #25 (текущая)

**Последний код:** [draft PR #25](https://github.com/feshakomissarov-prog/chess/pull/25) `aa4289e56edfaa6834ecffffcd50061922c67ecd`, base PR #24 `codex/m3-windows-diagnostics`. Ни одна ветка #8–#25 не слита. **CI подтверждён** по [run #41261611961](https://github.com/feshakomissarov-prog/chess/actions/runs/41261611961) и [run #41261837883](https://github.com/feshakomissarov-prog/chess/actions/runs/41261837883): **194 passed** (последний лог: 65.08s), Python 3.12.10, PySide6/Qt/shiboken6 6.12.0, compileall и isolated window PASS.

**Большинство старых замечаний устранено:** v9 PuzzleAttempt UID без mutable rating/plays; SQLite migration и переназначение pilot links; статистика через bounded CTE с 35k-game regression; source filters; ожидание finished у QProcess. **Новые выявленные блокеры — P1:** при connected preflight кнопка `QProgressDialog Отменить` больше не устанавливает stop_event для уже запущенного Stockfish (переиспользуется dialog с обработчиком старого request ID); неполный/некорректный ответ `/api/account/playing?nb=50` может ошибочно трактоваться как «нет human»; при disconnect/reconnect во время preflight late reply старого аккаунта может разрешить запуск. Подробности: [независимое ревью PR #25](reviews/PR25_INDEPENDENT_REVIEW_AND_ACCEPTANCE.md), замечания оставлены [в PR #25](https://github.com/feshakomissarov-prog/chess/pull/25).

**Решение: пока НЕ MERGE, не запускать настоящий UCI/live OAuth до адресных исправлений и повторного CI.** Пользовательская SQLite, реальные Lichess parties и puzzle activity — NOT RUN, старый native crash Windows/Python3.11 не объяснён. **PR #8→main остаётся mergeable=false**, потребуется отдельная интеграция с сохранением обновлённых `PRODUCT_VISION`, `README`, `ROADMAP` и docs/research. Следующее Codex действие — исправить три preflight/cancel guard-регрессии в существующем draft PR #25 и дать новый SHA/CI. Далее — один ручной offline smoke владельца (Python 3.12+), затем отдельно согласованный online pilot с backup, затем интеграция.

# Chess Coach — текущее состояние и handoff

**Основа проекта от 31.10.2026; последние обновления — 01.11.2026.** Репозиторий: https://github.com/feshakomissarov-prog/chess  
**Локальный путь владельца:** `G:\project\chess`. У других исполнителей он может быть иным.  
**Код M0/M1/M2 работает из `main`; функции M3/M4 — только в draft-цепочке PR #8–#24, не слиты.** Вся история и документация GitHub доступны следующему Codex и следующему чату без данного диалога.

## Актуализация 01.11.2026 — карта всех шахматных направлений и педагогики (исследование)

Пока Codex исправляет замечания к stacked PR #8–#24, сохранены три новых аналитических документа: [22 области шахмат × 12 сквозных навыков](research/CHESS_SKILL_TAXONOMY.md), [как шахматный тренер диагностирует и снимает непонимание](research/CHESS_COACHING_PRACTICE.md) и [честная карта покрытия Chess Coach + будущие модули/запросы GitHub](research/CHESS_COACH_COVERAGE_GAPS.md). Они основаны на официальных материалах Steps Method, FIDE, Lichess Practice, ChessDojo и других публичных учебных описаниях, с пометкой, что единого обязательного сценария работы тренера нет.

**Никаких новых задач Codex, слияний, реальных Lichess/Stockfish тестов или изменений архитектуры по этим документам не инициировано.** Прежняя последовательность M3/M4 → M5 → M6 остаётся без изменений; EDU-модули будут добавляться отдельно и постепенно после приёмки текущей ступени. Главное различие: наличие Stockfish/статистики = данные/оценка, **а не готовая способность обучать любому шахматному навыку**.

## Актуализация 01.11.2026 — полный тренер и аудит PR #24 (текущая)

**Продуктовая цель уточнена владельцем:** [PRODUCT_VISION.md](PRODUCT_VISION.md). Chess Coach должен быть **полноценным адаптивным тренером, снимающим непонимание во всех областях шахмат**, а не продуктом ради одной уникальной функции. Чужие проекты из [исследовательского каталога](research/OPEN_SOURCE_CHESS_PROJECTS.md) — технические образцы и идеи с учётом лицензий. Пять волн ссылок уже включены в каталог (последняя — детекторы мотивов, обучение выбору плана и проверяемые объяснения).

**Новый код M3–M4:** последовательные draft PR [#18](https://github.com/feshakomissarov-prog/chess/pull/18)→[#19](https://github.com/feshakomissarov-prog/chess/pull/19)→[#20](https://github.com/feshakomissarov-prog/chess/pull/20)→[#21](https://github.com/feshakomissarov-prog/chess/pull/21)→[#22](https://github.com/feshakomissarov-prog/chess/pull/22)→[#23](https://github.com/feshakomissarov-prog/chess/pull/23)→[#24](https://github.com/feshakomissarov-prog/chess/pull/24). **Последний SHA:** `e57926b976d31ec198add0da8373570ac26d1d17`, branch `codex/m3-windows-diagnostics`, base `codex/m3-puzzle-pilot`, draft. Не слит. В базе PR #8–#17; PR #15 закрыт как заменённый. Реальные Lichess/OAuth/Stockfish ещё NOT RUN.

**CI проверен по GitHub run #41258443123:** Windows Server 2025, Python 3.12.10, PySide6/Qt/shiboken6 6.12.0, **184 passed**, compileall и offline window PASS. Локальный crash Python 3.11.5/shiboken6 не диагностирован. Владельцем GUI на head #24 пока не проверен; ранее head #17 offline smoke 7/7 PASS, но без новой свободной доски.

**Углублённое ревью:** [PR24_REVIEW_FIXES_AND_MERGE_GATES.md](specs/PR24_REVIEW_FIXES_AND_MERGE_GATES.md), комментарий в [PR #24](https://github.com/feshakomissarov-prog/chess/pull/24). **Остались блокеры/замечания до merge:** **идемпотентность Puzzle Activity ошибочно зависит от mutable `puzzle.rating/plays/themes` — повторная загрузка той же попытки создаёт дубль, необходима миграция существующих UID;** `StatisticsService.summary` материализует весь архив и создаёт гигантские `IN` списки, тест 1005 недостаточен; `analysis_source` не всегда фильтрует список игр, если `analyzed=any`; fair-play guard движка видит только локальные `online_sessions`; `SettingsDialog` может уничтожить QProcess probe до подтверждения выхода; конфликт PR #8→main `mergeable=false` из-за параллельных docs. **Не merge.** Следующее Codex задание — компактный fix PR поверх #24 с тестами, затем безопасная интеграция документации и контролируемый ручной пилот владельца с backup, только по его решению.

## Актуализация 01.11.2026 — ручной smoke владельца и следующий большой блок (текущая)

**Последний head:** [draft PR #17](https://github.com/feshakomissarov-prog/chess/pull/17) `ae14a56cd77b88a3bdac32696342a96152c627f3`, base `codex/pr14-deep-audit-ci`, **не слит**. Windows CI [run #41252722837](https://github.com/feshakomissarov-prog/chess/actions/runs/41252722837): **156 passed**, Python 3.12.10, compileall, Qt offscreen PASS. Последняя 10-секундная UX-правка read-only на shutdown подтверждена статическим ревью/CI.

**Ручной тест владельца:** `python.exe -m chess_coach --offline` — 7 сценариев отмечены PASS: окно, локальная доска, стрелки, undo/premove, история, библиотека/статистика, настройки/закрытие. **Обнаружен UX-баг:** в локальной партии нельзя обычной мышью ходить за чёрных после белого хода, поскольку BoardView перехватывает жесты как premove за выбранный цвет. Нужно разделить свободную доску для двух сторон и отдельную тренировку предходов. В `--offline` кнопка Lichess и реальные партии **намеренно недоступны**: SQLite и QSettings временные, OAuth не читается. Это не ошибка авторизации; пустая библиотека ожидаема.

**Следующее большое задание Codex:** [M3_M4_REAL_WORLD_PILOT_AND_ANALYTICS_UI.md](specs/M3_M4_REAL_WORLD_PILOT_AND_ANALYTICS_UI.md): блоки A–G — свободная доска за оба цвета, понятный ручной online startup, **пилот 5–10 завершённых игр** (без отметки полного backfill), резервная копия SQLite, настройка **уже реализованного** UCI Stockfish, настоящий экран статистики с фильтрами/графиком и затем личные задачи. Голос и полный редизайн **позже**. Не начинать массовый реальный импорт, запуск Stockfish или OAuth-тесты без разрешения владельца. Codex может сделать UI и fake tests, владельцу оставить ручной реальный pilot. В main не merge без команды.

**Остаточный риск:** native crash на прежнем локальном Python 3.11.5/shiboken6, причина не установлена. Последний ручной offline smoke владельца завершился без аварии; это хороший сигнал, но не доказательство устранения старого native crash. Не обходить Dr.Web.

## Актуализация 01.11.2026 — PR #17, отмена Stockfish (текущая)

[draft PR #17](https://github.com/feshakomissarov-prog/chess/pull/17), head `5613346755d3f333a145b9847aed7668e141f52e`, base `codex/pr14-deep-audit-ci` (PR #16), **не слит**. PR #15 закрыт без merge. Windows GitHub Actions [run #41252069980](https://github.com/feshakomissarov-prog/chess/actions/runs/41252069980), Python 3.12.10: **156 passed**, compileall, offline-window smoke PASS. Локально Python 3.11.5: 80 `test_lichess` PASS, один Qt close fake analyzer PASS; весь локальный Qt suite **не повторяли** из-за native crash.

**Подтверждено ревью:** в `_analyze_interruptibly` теперь правильные `SimpleEngine.analysis()` и `SimpleAnalysisResult.stop()`, обработка отмены/ошибок и `engine.quit()`; при закрытии GUI ждёт окончания analysis worker до SQLite shutdown. В PR #17 оставлен review.

**Оставшийся P2 timeout UX:** по истечении 10 секунд окно включается, но `_shutdown_requested=True`, а LichessService уже закрыт — полноценную работоспособность нельзя обещать. При зависании анализа нужна явная стадия «ожидаем безопасной остановки»/корректное восстановление, с Qt regression test. Не блокирует статически проверенный обычный cancel path, но важно до merge.

**Native crash ещё не устранён/не локализован:** Python 3.11.5 + PySide6/shiboken6 6.12.0, два `0xC0000005` в `shiboken6.abi3.dll` и более ранний `0xC0000374` в `ntdll.dll`. Dr.Web `PsInject (43), deny [5]` заблокировал WerFault.exe для соответствующих PID **после** аварий; это не доказательство первопричины. Нельзя менять защиту/исключения/ExecutionPolicy. Реальный Stockfish, Lichess/OAuth и архив — NOT RUN.

**Дальше:** Codex точечно исправляет timeout UX в draft PR #17; пользователь делает один контролируемый `--offline` smoke в своём Windows, без массовых тестов. При повторной аварии остановиться и сохранить Event Viewer/консоль. Затем разрешённый владельцем малый пилот данных, решение о последовательном merge stacked PR #8–#17. **Ничего не мержить без команды владельца.**

## Актуализация 01.11.2026 — PR #16 и локальная ошибка Python (текущая)

[draft PR #16](https://github.com/feshakomissarov-prog/chess/pull/16) `7d93e7c04320a73d5dcff2d6be09b6782cacb47e`, base `codex/m3-m4-review-fixes` (PR #14); PR #15 закрыт без merge как заменённый. `main` и вся цепочка #8–#16 не слиты. Windows GitHub Actions run #41250753509 **152 passed на Python 3.12.10**, compileall и offscreen window smoke PASS. Локально Python 3.11.5: `test_lichess.py` 78 pass, file-backed SQLite integration 8 pass; полный pytest-qt suite закончился native access violation `python.exe` при `_close_widgets`. Dr.Web заблокировал `C:\Windows\System32\WerFault.exe` («Процессу запрещено изменение запущенного приложения»). **Причинная связь с падением не установлена; нельзя отключать/обходить антивирус**.

Ревью подтвердило большую часть исправлений предыдущих P0/P1, но нашло два **незакрытых P1**: `engine.stop()` на `chess.engine.SimpleEngine` отсутствует в реальном python-chess (нужно `SimpleAnalysisResult.stop()` через поддерживаемый `analysis()` flow); `MainWindow.closeEvent` не управляет daemon Stockfish analysis worker, возможно позднее SQLite/Qt использование. Полное новое ТЗ на адресные исправления и read-only диагностику: [PR16_ENGINE_CANCELLATION_AND_NATIVE_CRASH.md](specs/PR16_ENGINE_CANCELLATION_AND_NATIVE_CRASH.md), review оставлен в PR #16. **До решения не рекомендованы merge, реальный массовый импорт и локальный повтор полного Qt suite без диагностики.**

Следующий Codex job: **одно большое ТЗ на два конкретных lifecycle дефекта плюс безопасная диагностика native crash**; только fake engine/network/temp DB, отдельный draft follow-up PR поверх #16, report actual tests/CI и `NOT RUN`. После этого ручной offline smoke владельца, пилот 2–5 своих завершённых игр с явного разрешения, Stockfish на одной завершённой игре, затем последовательная приёмка stacked PR.

## Актуализация 01.11.2026 — аудит PR #14 (последняя)

**Новый head:** [draft PR #14](https://github.com/feshakomissarov-prog/chess/pull/14), `fd184182f32b485f16b3c921af42913c30f09284`, base `codex/m4-puzzles`, **не слит**. Цепочка PR #8–#14 остаётся draft. GitHub Actions run [#41247808011](https://github.com/feshakomissarov-prog/chess/actions/runs/41247808011) на Windows/Python 3.12.10: **138 passed**, compileall и offline Qt PASS. Реальные Lichess/OAuth/Stockfish/GUI E2E: **NOT RUN**.

**Что подтверждено кодом:** game export API использует `until` и `division=true`; атомарный archive upsert с SQLite cursor; descending puzzle pagination с checkpoint; улучшение result classification, глобальный human game guard при старте Stockfish, retry-кнопка после restore 429.

**Новое обязательное ТЗ по результатам углублённой проверки:** [PR14_DEEP_AUDIT_AND_ACCEPTANCE.md](specs/PR14_DEEP_AUDIT_AND_ACCEPTANCE.md), review добавлен в [PR #14](https://github.com/feshakomissarov-prog/chess/pull/14). **Остающиеся подтверждённые/существенные проблемы:** raw-only variant вызывает исключение `chess.Board('')` при открытии в библиотеке; `LichessService.close` не отменяет архив/puzzle worker до shutdown writer; сохранение opponent между двумя аккаунтами неверно; долгий backfill может потерять игры из-за `incremental_since=now` и 24h overlap; UCI Stockfish version cache key часто unknown; после неуспешного 429 restore онлайн-кнопки могут активироваться без подтверждения OAuth. **Полный архив пользователя пока не загружать; merge не рекомендован.**

Следующий Codex job: **все пункты большого ТЗ одним заданием, по небольшим блокам A–G с тестами**, один stacked fix PR поверх #14 или аккуратное обновление нужных веток; затем ручная приёмка владельца, малый live pilot, только затем решение о последовательном merge. Не менять Dr.Web, не запускать реальный OAuth/полный импорт/Stockfish без разрешения владельца.

## Актуализация 01.11.2026 — аудит M3/M4 перед реальным импортом

**Новая спецификация на исправления:** [M3_M4_REVIEW_FIXES_BEFORE_IMPORT.md](specs/M3_M4_REVIEW_FIXES_BEFORE_IMPORT.md).

Codex опубликовал **шесть вложенных draft PR**, **не слитых в main**: [#8 P0](https://github.com/feshakomissarov-prog/chess/pull/8) `f4c22807` (base main), [#9 SQLite](https://github.com/feshakomissarov-prog/chess/pull/9) `0c98ae7` (base #8), [#10 Archive](https://github.com/feshakomissarov-prog/chess/pull/10) `bbdad30` (base #9), [#11 Stockfish](https://github.com/feshakomissarov-prog/chess/pull/11) `6b0d954` (base #10), [#12 Stats](https://github.com/feshakomissarov-prog/chess/pull/12) `f023e74` (base #11), [#13 Puzzles](https://github.com/feshakomissarov-prog/chess/pull/13) `7b6ad4271de2e2ae2b24ddb58eed326f6c7decf8` (base #12). **CI последней ветки Windows/Python 3.12.10: 126 passed**, без live E2E.

**Найдены блокирующие ошибки до импорта реальных игр:** game export ошибочно использует `before` вместо официального `until`; архив пишет через async writer и отдельно продвигает checkpoint/foreign key, возможна гонка; incremental puzzle activity ошибочно предполагает ascending порядок, возможны пропущенные попытки. Дополнительно `division=true` не запрашивается, check Stockfish fair-play покрывает лишь анализируемую игру, статистика может считать abandoned/unknown как ничьи, а подключение после restore 429 может оставаться disabled. **Не запускать массовый импорт пока не исправлено и не проверено!**

В `--offline` кнопка подключения к Lichess **должна быть неактивна** и новая база пустая: это безопасная изоляция, не баг. На обычном запуске при статусе «Подключено» connect также disabled, есть «Отключить». Если после cooldown 429 connect не восстанавливается — отдельный баг. **PR #8 сейчас имеет mergeable=false из-за неразрешённого совмещения с main**, не пытаться слить напрямую без сохранения новых документов.

**Следующий шаг:** дать Codex полный аудит-ТЗ (ссылкой выше), исправления и regression tests в соответствующих stacked PR. После исправлений — пользовательский `--offline` smoke, затем осторожный явно разрешённый онлайн-тест 2–5 партий, потом большой архив и puzzles. Не делать самостоятельного merge. `docs/M3_M4_PROGRESS.md` пока в ветке #13, не в main.

## 1. Цель проекта

Windows-приложение Chess Coach (Python, PySide6) — персональный тренер: играть через Lichess, **записывать размышления пользователя по каждому изменению позиции, начиная с первого хода белых**, после партии распознавать голос, анализировать Stockfish, обсуждать ошибки и создавать упражнения. **Во время сетевой партии с человеком никакого Stockfish/ИИ-подсказывания.**

## 2. Git: что уже слито

| Инкремент | GitHub | Результат |
| --- | --- | --- |
| **M0** | [PR #2](https://github.com/feshakomissarov-prog/chess/pull/2) — merged | `chess-widgets==0.5.0`, адаптер доски, локальный контроллер, клик-клик, drag, предходы, ручные стрелки, SAN, просмотр истории, настройки |
| **M1/M2 — основной клиент Lichess** | [PR #3](https://github.com/feshakomissarov-prog/chess/pull/3) — merged | OAuth PKCE, токен в Windows Credential Manager, Board API/NDJSON, игра с другом по ссылке и логину, Lichess AI, takeback, часы, pending-ходы, PGN/SQLite, экраны Home/Game/Result/Review, ярлык Windows, offscreen тесты |
| **Merge M0** | `9df0efe348892092a5639e5af6e4a1e7dcd92787` | В main |
| **Merge Lichess** | `cd293e4dc744e6891f4626c7764870e531bff846` | В main |

Во время merge PR #2/#3 возникали конфликты со свежими документами main. Они разрешены с сохранением документации main и новейшего кода/README из PR #3. **В будущем создавать новые feature-ветки от `main`, не продолжать писать в старые ветки M0/M1.** Не делать merge/force push без разрешения владельца.

## 3. Что реально проверялось

- **Codex:** по отчёту 95 passed в Qt offscreen, `compileall` PASS, `git diff --check` PASS на Windows Python **3.11.5**. **Независимой CI-проверки Python 3.12 нет**; `pyproject.toml` требует `>=3.12`.
- **Владелец:** запускал GUI на Windows, подключал аккаунт Lichess, играл с Lichess AI и другом, использовал premove, проверял часть новых переходов. На последней итерации провёл несколько ручных партий, сообщил, что мерцающих окон уже не видел. **Полный тест takeback, reconnect, длинных партий, финального PGN на последнем коде пока не подтверждён.**
- **ChatGPT:** проверял исходники PR через GitHub, но **не запускал Windows/pytest или реальные запросы к Lichess**. Рекомендовал принятие прототипа в main с открытым техдолгом.
- **Риск автозапуска сети:** при запуске ярлыком Codex зафиксировал неожиданное восстановление сохранённой OAuth-сессии с серверными событиями. Не доказано, отправился ли ожидавший ход. Открытие обычного GUI со старым OAuth **не равно офлайн smoke**. Вопрос в [Issue #5](https://github.com/feshakomissarov-prog/chess/issues/5).
- **Антивирус:** Dr.Web 31.10.2026 20:06 сообщил о заблокированном доступе к защищаемому объекту; по скриншоту путь/процесс неизвестен. Проверка Defender Codex не заменила анализ Dr.Web. Не отключать защиту и не добавлять исключения.

## 4. Новый большой пул (пересмотрено 01.11.2026)

**Актуальное единое ТЗ:** [M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md) · [эпик #6](https://github.com/feshakomissarov-prog/chess/issues/6). **Большое ТЗ с несколькими самостоятельными PR, голос пока отложен.**

**Состояние P0:** [draft PR #8](https://github.com/feshakomissarov-prog/chess/pull/8), head `f4c22807d96f7c753a6e110c37f703d251032174`, base `main`, **не слит**. Код PR #8: исправлены ручные стрелки, безопасный `--offline`, SQLite close, ввод только после валидного gameFull, изоляция QSettings, окно «Активные партии» и Windows CI. **Windows Python 3.12.10 CI подтверждён 108 passed**. Codex сообщил о видимом окне с `python.exe -m chess_coach --offline`, но ручной тест владельца и запуск ярлыка `pythonw.exe` не подтверждены; не утверждать реальный Lichess E2E. Новых блокирующих дефектов статическим ревью текущего PR не выявлено; решение о merge — за владельцем.

**Новые приоритеты:**
1. **Stage 1, M3:** эволюция существующей SQLite без потери `games`: accounts, отдельные строки `game_moves`, `game_positions`, редактируемые `game_notes`, версионированные revisions для takeback, миграция старых PGN, библиотека.
2. **Stage 2, M3:** полный первый NDJSON импорт завершённых Lichess-партий с checkpoint/resume, затем incremental only; активные партии уже есть в P0, не реализовывать повторно.
3. **Stage 3, M4:** оценки Lichess при наличии и локальный Stockfish после игры, кэшированные analysis runs, график по каждому ply, ошибки и фазы партии.
4. **Stage 4, M4:** отдельный экран сводной статистики (W/D/L, swing оценки, неточности/ошибки/зевки по дебюту/миттельшпилю/эндшпилю, контроль, период, coverage).
5. **Stage 5, M4:** **личные задачи Lichess**, `GET /api/puzzle/activity` (OAuth `puzzle:read`, NDJSON `before/since/max`) и `GET /api/puzzle/dashboard/{days}`: отдельные `puzzles`/`puzzle_attempts`/`puzzle_sync_state`, история и статистика.
6. **Позднее M5:** микрофон/последовательность нарисованных вариантов, связь с позицией и временем, воспроизведение; [историческое голосовое ТЗ](specs/M3_M4_ARCHIVE_ANALYSIS_VOICE_THOUGHTS.md) остаётся основой только для этого этапа. Данные об отдельном текстовом комментарии и стабильном position uid подготовить в M3.
7. **M6:** ИИ-тренер после локальной аналитики и размышлений, отдельное согласование.

**Главное ограничение:** Stockfish и ИИ-подсказки нельзя использовать во время своей активной Lichess партии против человека. Реальный массовый импорт или OAuth изменения только с разрешения владельца. Dr.Web/execution policy не менять. CI/tests headless. Каждый stage reviewable, **не merge самостоятельно**.

## 5. Архитектурные инварианты

- Lichess — единственный источник истины для онлайн партии. `GameController` и `BoardView` не могут «утвердить» ход за сервер.
- Optimistic move в UI не означает сохранённый ход; confirmed UCI → `LichessGameSession` → упорядоченный SQLite writer → PGN. Учитывать конечную синхронизацию `final_sync_pending`.
- Предход не делает ход в authoritative позиции, выполняется только когда после события соперника остаётся легальным. В отличие от предугадываемого ответа, обычный premove **не защищает от зевка** при неожиданном ходе.
- Takeback: через `POST /api/board/game/{gameId}/takeback/yes|no`. На сервере история может стать короче; локальные PGN и очередь должны согласоваться. Локальное Undo/Cancel premove — другие команды.
- Никаких подсказок движка/GPT против человека; ручные стрелки — правая кнопка; не менять управление стрелками без решения владельца.
- Токены OAuth и личные игровые данные не помещать в git/log. Ресурсы и лицензии: код chess-widgets MIT, python-chess GPL и SVG pieces отдельно; итоговая лицензия приложения ещё требует решения перед распространением.

## 6. Куда смотреть новому исполнителю

Сначала `AGENTS.md`, затем `docs/PROJECT_STATUS.md` (этот файл), `README.md`, `docs/PRD.md`, `docs/ROADMAP.md`, [новое единое ТЗ](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md), [эпик #6](https://github.com/feshakomissarov-prog/chess/issues/6) и относящуюся к текущему PR маленькую задачу (#7 / #5 / #4). Затем соответствующие исходники и тесты.

**Ключевые файлы:** `src/chess_coach/board_view.py`, `controller.py`, `window.py`, `lichess/{api,auth,service,session,repository}.py`, `tests/test_{board_view,controller,window,lichess}.py`, `scripts/create_desktop_shortcut.ps1`.

## 7. Запуск на Windows

В PowerShell, если рабочее дерево чистое:

```powershell
cd G:\project\chess
git status --short
git switch main
git pull --ff-only
.\.venv\Scripts\python.exe -m chess_coach
```

**Не удалять локальные изменения ради switch/pull.** При установленном ярлыке `Chess Coach.lnk` можно запускать двойным щелчком; он использует `.venv\Scripts\pythonw.exe` и может автоматически восстановить OAuth-соединение. Если нужен настоящий офлайн-тест, код должен явно гарантировать отсутствие обращений к сети. Для новой установки ориентироваться на `README.md` и Python >=3.12; пока версия 3.12 не подтверждена тестами.

**Ближайшее решение (01.11.2026):** пользователь передаёт Codex одно большое ТЗ [M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md](specs/M3_M4_DATA_ARCHIVE_ANALYTICS_PUZZLES.md), реализация внутри — по небольшим проверяемым блокам/PR. Сначала завершить P0 и осознанно принять #8, затем SQLite/импорт, после Stockfish/сводная аналитика и задачи Lichess. Голос позднее.

## M4.5 — активная работа (2026-11-01)

Единое утверждённое ТЗ v2.0 [M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md](specs/M4_5_BATCH_ANALYSIS_PERSONAL_DIAGNOSTICS.md), этапы 0–I выполняются в ветке `codex/m45-batch-analysis` от актуального `main` `6c3692c827294386fd070da9f9e7f086429320d9`, draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`. Последний кодовый checkpoint `415c9e1c7fa8cb0c3da1170ff2176bc87cd0e495` (в PR). Этапы 0/A/B/C/D/E/F/G/H/I выполнялись с отдельными commits и записями; подробные SHA и результаты — в [M4_5_EXECUTION_LOG.md](handoffs/M4_5_EXECUTION_LOG.md). Schema v16 аддитивно хранит точность дат; старые game/PGN facts не перестраивались. Реализованы версия-совместимая очередь, метрики/события, bounded selective review, CC0 opening transpositions, phase provenance, библиотека/паспорт, per-color recent/historical profile, source-aware aggregates и evidence positions. Окно массового анализа включает явный старт, pause/resume/cancel, параметры CPU/Hash/лимита, fail-closed Lichess preflight перед каждой игрой и восстановление совместимых заданий после рестарта; возобновление/отмена ограничены точной парой engine hash + параметрами.

Подтверждения кода: Windows Python 3.12.10 локально `python -m pytest -q` — **285 PASS**, включая корпус из 20 hand-authored synthetic mini-reports; compileall, pip check и diff-check PASS. Windows CI [run #76](https://github.com/feshakomissarov-prog/chess/actions/runs/41282799453) на SHA `415c9e1c7fa8cb0c3da1170ff2176bc87cd0e495`: **SUCCESS, 285 passed за 119.67 с**, Python 3.12.10, Qt/PySide6/shiboken6 6.12.0, compileall и изолированная офлайн-инициализация Qt окна PASS. Ранее run #75 на предыдущем code checkpoint также прошёл (265 PASS).

Ограничения/приёмка: real opportunity conversion остаётся **PARTIAL** — нет подтверждённой связи candidate → последующее действие, поэтому процента нет. Большой набор из 20 отчётов не заменяет оценку на 20 реальных/контрольных партиях. Визуальная ручная проверка GUI, пользовательская SQLite/PGN, живые OAuth/Lichess и настоящий Stockfish — **NOT RUN/NOT TOUCHED**. PR остаётся draft; ничего не слито.

## M4.5 second-pass review — текущий handoff (2026-11-01)

В ветке `codex/m45-batch-analysis`, draft PR [#30](https://github.com/feshakomissarov-prog/chess/pull/30), реализуются R10–R17 из [second-pass spec](reviews/PR30_M45_SECOND_PASS_REVIEW_FIX_SPEC.md). Подробные коммиты, тесты и ограничения записаны в [execution log](handoffs/M4_5_EXECUTION_LOG.md).

- R10: selective review newest-first, SQL pages, quota расходуется только на новые jobs; 200 synthetic games доказывают progression 10+10 без запуска UCI.
- R11: schema v17 additive refresh checkpoint по account/game/revision/hash; no-eval/partial/changed/API failure и retry deadline; fake export 80 партий проходит 25+25+25+5, archive backfill cursor сохраняется.
- R12: opportunity metrics account-relative по mover и выбранному account color; один shared analysis run на два linked accounts; глобальная owner-relative метка `opponent_blunder` больше не сохраняется.
- R13: source score completeness отдельно от собственных сравнимых решений; PGN root `%eval` сохраняется в ply 0 только при наличии; GUI обозначает missing first-move baseline.
- R14: opening provenance отдельно маркирует Lichess ECO и CC0 matcher, конфликт остаётся видимым.
- R15: obsolete detector rows/tags остаются историей, но исключены из активного отчёта и review planning.
- R16: локальная accuracy честно остаётся NULL/unsupported; UI поясняет причину и coverage, Lichess accuracy остаётся отдельной.
- R17/CI: Windows Python 3.12.10 полный offscreen CI на финальном SHA ещё ожидается; локальный полный прогон достиг 100%, но двойной `-q` подавил явную summary, поэтому локальный PASS пока не утверждается. Обновить этот статус по фактическому Actions run.

Реальные Lichess/OAuth, архив, личная SQLite и настоящий Stockfish не запускались/не открывались. Fair Play synthetic regressions включают активное изменение snapshot, timeout/429 и lease loss. PR не слит и должен оставаться draft.

### Второй проход review R10–R17 — CI checkpoint

Windows Actions run [#86](https://github.com/feshakomissarov-prog/chess/actions/runs/41329457378) на code SHA `44782f00374456f35cfc9ab45b453b8fdb3d0ce6` — **SUCCESS**, 310 passed за 201.80 с; Windows Server 2025, Python 3.12.10 x64, PySide6/Qt/shiboken6 6.12.0. `compileall` и isolated offline window initialization PASS. Подробный status каждого R10–R17 и ручные ограничения — в [execution log](handoffs/M4_5_EXECUTION_LOG.md). Final exact-head verification is recorded there.

Документационный head `9ca4b32e75744db980bd4540579a3bbcc12eee65` проверен Windows Actions run [#87](https://github.com/feshakomissarov-prog/chess/actions/runs/41329890613): SUCCESS, 310 passed за 224.16 с; compileall и isolated offline window init PASS. После обновления журнала запускается завершающий точный CI на новом head.

## M4.5.1 U10–U16 — актуальный handoff (2026-11-01)

Продолжение: тот же `codex/m45-batch-analysis`, тот же draft [PR #30](https://github.com/feshakomissarov-prog/chess/pull/30), base `main`. Обязательное ревью-ТЗ: [U10–U16](reviews/PR30_M451_U0_U9_FOLLOWUP_REVIEW_SPEC.md); полный статус этапов и результаты см. [журнал исполнения](handoffs/M4_5_EXECUTION_LOG.md). Исторические статусы выше описывают предыдущие checkpoint и не являются текущим состоянием.

- U10: локальный position evaluation в обычном режиме требует успешного актуального Lichess `/api/account/playing` preflight; активная человеческая игра, неизвестный/усечённый ответ, устаревшая сессия, 429 или disconnect блокируют запуск и отменяют выдачу. Fake-service/fake-engine проверки PASS; абсолютный сетевой race с новой игрой остаётся ограничением короткого lease, реальный Lichess не проверялся.
- U11–U15: bounded discovery существующего вложенного Stockfish; скрытое до подтверждения решение Lichess Puzzle; честные знаменатели статистики и реально свёрнутые фильтры; реактивная fail-closed готовность Batch Analysis; локальный выбор истории задач по аккаунту без сетевых запросов. Регрессионные тесты добавлены.
- U16: M4.6 не реализуется; из origin/main перенесён только будущий документ спецификации. Ранее установленный Stockfish 19 проверен UCI без переустановки. Ни личная SQLite, ни OAuth, ни архив не использовались.
- Локально на Windows Python 3.12.10: полный offscreen набор **350 тестов PASS**, целевые проверки — **13 PASS**; compileall и diff-check PASS. Windows CI на точном SHA `78aff6310a0ff963946897aa3d0fb9214ef3ac03` — **SUCCESS**, run [#98](https://github.com/feshakomissarov-prog/chess/actions/runs/41349352168); offscreen tests, compileall и isolated offline window initialization PASS. Последующий документационный commit требует нового exact-head CI.
- GUI-пилот, Lichess, OAuth, пользовательские данные и анализ двух партий остаются **НЕ ПРОВЕРЕНЫ**. Следующая ручная приёмка должна ограничиться ровно двумя завершёнными партиями после создания пользователем резервной копии собственной SQLite.

PR остаётся draft. M4.6 не начинать до отдельного принятия M4.5/M4.5.1 владельцем.

## M4.5.1 B0–B4 — текущая работа (2026-11-02)

Продолжение в `codex/m45-batch-analysis`, draft PR #30; применено ТЗ [B0–B4 live pilot](https://github.com/feshakomissarov-prog/chess/blob/chatgpt/pr30-live-pilot-multipv-puzzles-logs-arrows/docs/reviews/PR30_M451_LIVE_PILOT_ENGINE_PUZZLES_LOGGING_ARROWS.md). MultiPV больше не задаётся через `SimpleEngine.configure`; selective режим передаёт `multipv` параметр поиску, ошибки остаются `failed` без автоматического retry. Добавлены постоянные обезличенные ротационные логи и доступ/opt-in отчёт в настройках, `since`-ограничение и пропуск неизменённых Lichess puzzle записей, нулевой sync больше не запрашивает dashboard, ручные стрелки ПКМ заметно толще при сохранении отдельного тонкого предхода.

Локально Windows Python 3.12.10, Qt offscreen: полный набор **360 тестов PASS за 115.92 с** (целевой набор из затронутых файлов ранее — 180 PASS). Установленный Stockfish 19 (не переустанавливался) реально завершил анализ синтетической партии `e2e4 e7e5`, 3 позиции × 3000 узлов; analysis run записан в изолированную временную SQLite. Реальные Lichess/OAuth/архив/личная SQLite и массовый Stockfish не запускались. Windows Actions для implementation SHA `38c051b11b9776165fc0648b56fa1f945f1d226a` — SUCCESS, run [#103](https://github.com/feshakomissarov-prog/chess/actions/runs/41355300614): Windows Server 2025/Python 3.12.10, 360 PASS, compileall и isolated offline-window initialization PASS. Последующая только-документационная фиксация статуса проверяется новым exact-head CI.

### 02.11.2026 — PR #41: повторный P0 Fair Play fix

После review исправлена централизованная инвалидизация review safety при потере доверенного статуса Lichess (429, reconnect, network/auth failures, logout, OAuth revoke). OAuth lease снимается синхронно до запуска асинхронного отзыва. Любой статус восстановления подключения не возвращает engine PV или тренировочные подсказки: новое разрешение выдаётся только свежим явным полным preflight. На 429 lease снимается, но подтверждённое account connection сохраняется для корректного завершения Retry-After и batch recovery.

Добавлены Qt regression-тесты для статусов logout/429/reconnect/error/401/403, активного упражнения и подсказок, OAuth revoke до FakeService network action без затрагивания PAT, смены линии и повторного явного preflight. Целевые тесты Python 3.12.10: **9 passed**; полный offscreen pytest набор прошёл на 100%, compileall и diff check — PASS. Windows CI [#41382896839](https://github.com/feshakomissarov-prog/chess/actions/runs/41382896839) на кодовом SHA `cc4afbab8c54ed98ecfed764203183dfafc1ba97` — **SUCCESS**, все этапы за 6:02. Этот docs-only commit проверяется новым exact-head CI. Реальные Lichess/OAuth/PAT, пользовательская SQLite и Stockfish не запускались; PR #41 остаётся draft.

Последующее R1 review исправляет восстановление Lichess после transient stream disconnect. Authenticated identity теперь отдельно сохраняется при account event-stream reconnect и game-stream/final-sync error; online availability возвращается только по generation-checked status после фактически полученного события. Review engine lease остаётся закрытым до нового полного preflight. Targeted Qt regressions: **13 passed**; `tests/test_lichess.py` и `tests/test_window.py`, compileall и diff check — PASS. Exact-head Windows CI ожидается; см. [M4.6 execution log](handoffs/M4_6_EXECUTION_LOG.md). Реальные Lichess/OAuth и пользовательские данные не затрагивались.

R1b дополнительно разрешает безопасное восстановление idle event stream: transport сообщает ready сразу после успешного HTTP response, включая пустые NDJSON keepalive; blank lines не преобразуются в игровые события. `auth_generation`/`connection_stop` отменяют stale ready callbacks. После stream 429 истечение Retry-After не включает online actions или credential flow; требуется подтверждённый stream-ready status. Review lease по-прежнему закрыт до полного нового preflight. Целевые Qt regressions **17 passed**, связанные тестовые модули, compileall и diff check — PASS; exact-head Windows CI ожидается. См. [журнал M4.6](handoffs/M4_6_EXECUTION_LOG.md).
