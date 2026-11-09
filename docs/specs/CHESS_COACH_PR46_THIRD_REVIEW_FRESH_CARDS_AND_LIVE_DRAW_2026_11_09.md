# Chess Coach · PR #46 — третье независимое ревью: свежесть Home и LIVE-01 (ничья/сдача)

**Дата:** 09.11.2026  
**Решение:** **REQUEST CHANGES по двум узким подтверждённым проблемам**; остальной выполненный UX и ускорение первого показа сохранить.  
**Проверенный SHA:** \`e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095\` (включая report-only коммит); код последних быстрых Home-исправлений уже присутствует в этой версии.  
**Исполнитель:** desktop Codex, существующие ветка \`codex/unified-desktop-ux-2026-11-08\` и Draft [PR #46](https://github.com/feshakomissarov-prog/chess/pull/46). Новый PR не создавать; \`main\` не менять, merge не выполнять. Перед началом сверить свежий HEAD.  
**Контракты:** [финальное UX-ТЗ](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md), §§07,18–20,26; [TECH DEBT LIVE-01](../ux/TECH_DEBT_LIVE_LICHESS_DRAW_OFFERS_AND_RESIGN_LOCK_2026_11_09.md).  
**Граница:** только дефекты R3-01 и LIVE-01, соответствующие регрессии и отчёт. Не повторять всю архитектуру dashboard, Game, SQLite или Play; не активировать FUTURE-карточки.

## 1. Что установлено на предыдущем HEAD: не переписывать

1. **GitHub Actions подтверждены** на точном SHA \`e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095\`: run [42591965080](https://github.com/feshakomissarov-prog/chess/actions/runs/42591965080), **Windows test SUCCESS (970 passed / 491.78 s)** и **visual-evidence SUCCESS**, artifact \`visual-master-e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095\`. Проверено по jobs и test log, а не только словам исполнителя.
2. \`PlayPage.mode_selector\` и \`PlayPage.color\` — теперь parented (\`QComboBox(self)\`), \`_update_form\` не показывает state-only combo, QSettings восстанавливает radio. **Дефект второго окна исправлен на уровне кода**; отдельно собственный реальный Windows taskbar/Alt+Tab после исправления ещё должен подтвердить пользователь при визуальном тестировании.
3. \`dashboard_preview_snapshot\` выполняет ограниченные local SQLite queries для счётчика и трёх последних партий; network/Stockfish/тяжёлые accuracy CTE откладываются. Первоначальный account scope восстанавливается раньше первого запроса; preview-запрос независим от старой тяжёлой агрегированной операции. Новые тесты на fixture и результаты SQL убедительны.
4. В отчёте указаны SQL-времена на read-only 750 партиях, synthetic 2 500–3 000 партий и честное ограничение: **реальный обычный Windows GUI cold-start на личной БД НЕ измерялся**. Следовательно, цифру «250–300 мс» считать достижением тестового сценария, а **не доказанным SLA реального запуска**. Финальное измерение у владельца всё ещё необходимо.
5. **Визуальная приёмка всех UX-01…UX-13 не завершена.** Successful visual-evidence workflow — это генерация screenshot evidence, не покадровое сравнение с утверждёнными PNG.
6. Пользователь ранее сообщил **реальное двухстороннее воспроизведение LIVE-01** (исходящая ничья оставила неактивные кнопки; позднейшее входящее предложение не отобразилось; «Сдаться» также заблокирована). Этот технический долг [сохранён](../ux/TECH_DEBT_LIVE_LICHESS_DRAW_OFFERS_AND_RESIGN_LOCK_2026_11_09.md). Теперь, после исполнения предыдущего блока, пришло время включить его в следующий цикл исправлений.

**Вердикт:** ускорение первого отображения, Play Qt color и предыдущие P1/P2/R2 считаем пройденными **по источнику и тестам**. Не откатывать их ради двух описанных ниже проблем.

---

## 2. R3-01 [P2] При появлении новой партии быстрый Home обновляет счётчик, но показывает СТАРЫЕ три карточки

### 2.1. Фактическая причина в коде

[\`window.py::_render_dashboard_snapshot\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/src/chess_coach/window.py#L3470-L3610):

- при \`preview=True\` и наличии прошлого полного \`_latest_dashboard_snapshot\` с тем же account/clock включается \`preserve_aggregate=True\`;
- если у прошлого full snapshot есть хотя бы одна карточка, включается **\`preserve_cards=True\` без проверки идентичности game_id/line_revision/актуального порядка**;
- затем \`games = previous_snapshot["recent_games"]\` при \`preserve_cards=True\`, **независимо** от нового \`snapshot["recent_games"]\`;
- счётчик \`games\` уже обновляется новым preview, но блок «Последние партии» фактически остаётся из старого полного snapshot вплоть до завершения нового тяжёлого \`dashboard_snapshot\`;
- \`_latest_dashboard_snapshot\` также остаётся предыдущим full snapshot до результата aggregate.

Это прямой **source-backed дефект свежести** после добавления/завершения партии или изменения статуса: счётчик может показать 3316, а список всё ещё содержит прежние три партии, хотя быстрый запрос уже нашёл новую. Тест [\`tests/test_pr46_review_fixes.py::test_same_scope_dashboard_preview_preserves_aggregate_review_cards\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/tests/test_pr46_review_fixes.py#L16) проверяет сохранность старых графиков, но не замену списка при изменившемся наборе партий.

### 2.2. Что требуется

1. При same-scope preview **источником списка и порядка последних партий должен быть НОВЫЙ bounded SQLite preview**, а не старый full snapshot. Счётчик и карточки должны относиться к одной и той же версии уже committed данных (в пределах консистентности read transaction).
2. Для совпадающих записей \`game_id + line_revision + account_id/owner scope\` разрешено **переиспользовать старые графики/accuracy/metadata** из ранее полностью разрешённого aggregate, но **только при корректном source/provenance и действующем Fair Play gate**. Не переносить старые оценочные сведения на новый/изменённый \`line_revision\`, другую сторону, другой аккаунт или неизвестный источник. Для новых карточек показывать **достоверные нейтральные сведения** и «Оценки загружаются…» до full aggregate. Для исчезнувших из top-3 партий карточки убирать немедленно.
3. Если новый preview содержит те же три партии (и совпадают ревизии), **сохранить старые графики и accuracy**, как теперь делает исправление прошлого этапа: нельзя заново сбрасывать ReviewGraph при каждом refresh. Менять этот контракт запрещено.
4. Не терять доступность кнопки/действия «Открыть разбор» у новой карточки; source-aware clicked \`game_id\`, account scope, line revision нужно проверять в актуальном state. Не показывать карточку как «Я» при неизвестном owner color.
5. Не смешивать full snapshot прошлого аккаунта с preview другого. При account switch очищать старое, при late callback отбрасывать stale generation. При active human game или неопределённом Fair Play **скрывать все engine-derived accuracy/errors/graphs/accessible/tooltip**, но не нейтральные партии. Не выдавать fake source/accuracy.
6. Многопоточность: preview и aggregate могут завершиться в разном порядке; уже существующий coordinator и generation fence должны обеспечить итоговую корректность списка, без приостановки UI и без повторной принудительной тяжёлой загрузки.
7. Не создавать новый persistent cache, не модифицировать SQLite и не менять сортировку по времени без отдельного профилирования. **Минимальная правка render/merge**, поскольку быстрый SQL уже работает.

### 2.3. Tests R3-01

- **Новая партия:** есть full для A с top-3 \`G3,G2,G1\`, затем в SQLite committed \`G4\`. При медленном/заблокированном aggregate, preview показывает счётчик+1 и **\`G4,G3,G2\` сразу**, \`G4\` без выдуманной оценки, а \`G3/G2\` сохраняют старые безопасные графики. **Не ждать** второго full result.
- **Тот же набор:** refresh top-3 не изменился → сохраняются оценки и \`ReviewGraph\`, без пустого graphless-кадра. Предыдущая regression остаётся зелёной.
- **Изменение линии:** \`G3\` тот же \`game_id\`, но новый \`line_revision\` → не переносить старую оценку; именно новый revision виден для открываемого Review.
- **Удаление/изменение завершённости:** если игра вышла из top-3 фильтра, она не остаётся на карточке до полного пересчёта; count и список согласованы.
- **Account A→B:** ни на один кадр не показывать A-derived карточки/графики B; stale preview/aggregate отбрасываются.
- **Fair Play:** baseline full имеет оценки, затем gate неизвестен или active human — preview может показывать neutral game, но не раскрывает graphs/CP/errors в DOM/tooltip/accessibility.
- **Timing:** UI-обновление после быстрого preview не привязано к завершению искусственно задержанного full aggregate; используй event barrier в тесте, без настоящего \`sleep(30)\`.

---

## 3. LIVE-01 [P1] Предложение ничьей блокирует «Сдаться» и входящая ничья не отображается

### 3.1. Подтверждённый пользовательский сценарий

Реальная человеческая партия между двумя Lichess аккаунтами. Игрок A из Chess Coach послал предложение ничьей; игрок B увидел его; кнопка A стала disabled и не восстановилась в ходе дальнейшей игры. Позднее B отправил A новую ничью; A **не увидел** входящее предложение и не мог принять. У A также осталась disabled кнопка «Сдаться». B вынужден был сдаться со своей стороны.

### 3.2. Подтверждённые кодовые предпосылки — не считать заранее полным диагнозом

- [\`window.py::_lichess_session_action_started/completed/reconciled\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/src/chess_coach/window.py#L980-L1009) хранит pending по \`(game_id,command)\`; completed удаляет только статусы \`{-2,400,401,403,404,422}\`, остальные дожидаются reconciled.
- [\`window.py::_refresh_online\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/src/chess_coach/window.py#L7898-L7910) вычисляет \`pending_action = any(game_id == session.game_id ...)\` и при **любом** незавершённом действии **одновременно** выключает \`resign_button\`, \`draw_button\`, \`decline_draw_button\`, \`claim_draw_button\`. Это даёт доказанную цепочку **застрявший pending offer → disabled resign**, но не доказывает, почему сам pending не reconciliation.
- [\`lichess/service_games.py::_action\` и \`_action_effect_reconciled\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/src/chess_coach/lichess/service_games.py#L70-L184): \`offer_draw\` очищается только после более нового authoritative event с \`wdraw\`/ \`bdraw == True\`, изменившимся от прежнего флага; событие может не наблюдаться или быстро сброситься; 2xx HTTP само по себе не очищает pending. Возможны stale pending и вечное выключение независимых controls. Нужна фактическая timeline/reconnect проверка.
- [\`lichess/session.py::apply_state\`](https://github.com/feshakomissarov-prog/chess/blob/e95ba4aa4dcbbe4bf6e93e7bb58f4a0eccf63095/src/chess_coach/lichess/session.py#L320-L326) вычисляет \`draw_offered_by_opponent\` по \`bdraw/wdraw\` из каждого актуального \`gameState\`; проверить, что нет ложного сброса при временно неполном событии и что render включает CTA «Принять ничью» / «Отклонить».
- Возможность повторного предложения ничьей **регулируется правилами Lichess и контекстом хода**; требование НЕ в том, чтобы заставить сервер принять повторный offer в любой момент. Требование — не зависать и всегда корректно обрабатывать входящее предложение и безопасный выход через сдачу.

### 3.3. Целевое поведение, без опасного ослабления безопасности

1. Исходящий \`offer_draw\`: clear state-machine \`in_flight → HTTP_ACK/authoritative_offer_seen/declined/expired/unknown\`; не считать 2xx автоматическим фактом согласованной ничьей, но использовать как подтверждение обработки команды. На сетевой ошибке/неоднозначном ответе **не отправлять автоматически повторный POST**; корректно обновлять допустимые controls после authoritative resync.
2. **Независимость команд:** ожидающий \`offer_draw\` **не блокирует \`resign\`** активной партии. Дополнительные draw-actions ограничивать командно и контекстно, а не одним \`any_pending\`. Для повторного \`resign\` с неизвестным серверным исходом сохранять защиту dedup/authoritative status — не допускать автоматического повтора опасных POST. При pending \`resign\` не рисовать доступную вторую сдачу.
3. Входящий настоящий \`draw offer\` должен отображаться в Game workspace с «Принять ничью»/«Отклонить», статусом и понятным feedback. При актуальном incoming offer клиент может обработать его, даже если **прошлый исходящий offer ещё числится pending в отставшем внутреннем state**, при условии валидного authoritative game/session и отсутствия действительно конфликтующей операции.
4. Authoritative Lichess \`gameFull/gameState\` и reconnection/resync могут подтверждать, отклонять или снимать stale outgoing offers **только по доказанным новым данным**, а не по случайному локальному таймеру или выдуманному status. Нужна ограниченная lifecycle стратегия для неразрешённого pending с явным state «синхронизируем» и переходом после fresh resync. Отказ 400/403/429, connection drop, смена account/generation должны быть учтены по существующему fail-closed/idempotency контракту.
5. Отображение нельзя «исправить» включением бессмысленной кнопки, которая теперь отправит повторное предложение невзирая на server legality. Не делать auto-accept/auto-resign, не отключать Fair Play, не менять шахматную позицию и часы без server ACK.
6. Предпочтительно диагностировать **почему** конкретное исходящее предложение не reconciled, а не просто скрывать pending из UI, сохраняя утечку state. У каждого \`request_id\`, \`command\`, \`game_id\`, \`auth generation\` должны быть согласованные признаки состояния без бесконечного необъяснимого pending.

### 3.4. Обязательные тесты LIVE-01

- **Двусторонний сценарий на fake Lichess stream:** A предлагает ничью → B видит предложение → B продолжает игру/предложение прекращается по серверу → B позже предлагает ничью → A видит и может принять/отклонить; кнопка A «Сдаться» остаётся доступна, если матч server-active и нет своего pending resign.
- **Проверка управляющего кода:** \`_pending_game_actions\` содержит \`(game_id,"offer_draw")\` → resign **enabled**; \`(game_id,"resign")\` → повторный resign недоступен; независимые draw accept/decline разрешены только по authoritative incoming offer, без конфликтующего duplicate POST.
- **HTTP 2xx при пропущенном флаге**: pending outgoing draw не должен навсегда выключать всю панель; при отсутствии уверенности запустить свежий bounded authoritative resync, показать честный status; не повторять POST.
- **HTTP 400/403/429/timeout, reconnect/stream replay, account switch, stale generation, terminal game, long-running move**: не зависает/не принимает ложное предложение, не допускает дублированных команд, корректная обратная связь.
- **Моментальный GUI status:** CTA и incoming status видны/доступны на действующем Game workspace, включая Focus mode и возврат из других страниц; без скрытого маленького «одноразового» уведомления.
- **Реальный ручной smoke с ДВУМЯ тестовыми Lichess-аккаунтами** после unit/integration, с журналом событий и визуальной проверкой. Если реальные аккаунты/токены недоступны Codex, тестировать deterministic fake stream, **в итоговом отчёте пометить REAL_LIVE_UNVERIFIED**, не заявлять полное исправление подтверждённым на сервере.

### 3.5. Диагностика LIVE-01 без утечки пользовательских данных

Временная шкала по монотонному времени: тип и стадия outbound команды, обезличенный request_id/game_id, generation, HTTP status, reconcile event, server \`wdraw/bdraw\` boolean, \`draw_offered_by_opponent\`, \`pending_commands\`, status партии, enabled/visible кнопок. **Не сохранять** реальные токены, никнеймы, PGN, текст приватных переписок или account IDs в публичном PR/log. Логирование должно быть безопасно, отключаемо или агрегированно. Код критичных сетевых действий менять только в минимальной степени с сохранением серверных ограничений.

---

## 4. Сдача PR #46 после исправления

- [ ] Исправлен R3-01: при NEW committed game / changed revision обновляется топ-3 мгновенно даже при заблокированном тяжёлом aggregate; графики прежних актуальных партий сохраняются по ID+revision+scope; новый neutral card не получает старых показателей.
- [ ] Исправлен LIVE-01: исходящая ничья не блокирует resign; входящая отображается и обрабатывается; pending lifecycle безопасен, нет двойных POST и обхода server ACK.
- [ ] Уточнены/добавлены реальные focused tests + текущие 970 тестов не потеряны. По возможности настоящие 2 live accounts; при недоступности честный \`REAL_LIVE_UNVERIFIED\`.
- [ ] Повторно локальный pytest, \`compileall\`, \`git diff --check\`; GitHub Windows \`test\`/\`visual-evidence\` **SUCCESS на точном новом HEAD**.
- [ ] В \`docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md\` отразить: новый SHA, оба исправления с путями, результаты тестов, реальный статус smoke (изолированный Windows GUI / личная БД / два живых человека / Stockfish), наличие/отсутствие screenshot comparison 13/13.
- [ ] Существующие настройки/архив/сохранение PGN/SQLite/цвет/Play/Review и Fair Play неизменны по поведению; пользовательская БД и untracked файлы не тронуты; Draft PR остаётся draft.
- [ ] Никогда не писать «всё готово для merge», пока **13 визуальных эталонов**, реальные startup и human LIVE-01 не пройдут соответствующую ручную приёмку или не будут явно обозначены как непроверенные.

**Не расширять до нового UI-цикла.** После успешного кодового исправления перейти к пользовательскому визуальному сравнению UX-01…UX-13 и двум ручным smoke: обычный cold start с собственной БД, две учётные записи Lichess для ничьих/сдачи.
