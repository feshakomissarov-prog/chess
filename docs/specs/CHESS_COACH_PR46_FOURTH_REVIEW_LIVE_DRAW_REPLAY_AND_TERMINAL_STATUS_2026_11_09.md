> **УСТАРЕЛО / НЕ ОТПРАВЛЯТЬ CODEX ОТДЕЛЬНО.** Объединённое ревью, доказательства и весь тестовый охват находятся в [PR46 — единый пакет аудита и исправлений](CHESS_COACH_PR46_CONSOLIDATED_SOURCE_AUDIT_AND_SINGLE_FIX_BATCH_2026_11_09.md). Старый R4 документ сохранён только для истории, чтобы исключить несколько независимых двухчасовых циклов.

# Chess Coach — PR #46, четвёртое независимое ревью LIVE-01: повтор stream-состояния и приоритет финального результата

**Дата:** 09.11.2026  
**Вердикт:** **REQUEST CHANGES: два адресных случая в LIVE-01**, без повторного переписывания уже исправленных Home/UI/Fair Play.  
**Проверенный HEAD:** \`6fa202f9f2c9ee4be2785684912c4ddf5642a23f\`  
**Проверенный кодовый SHA:** \`eaae8914621ec0159f80797915b2a381e377a09f\`  
**PR:** [#46](https://github.com/feshakomissarov-prog/chess/pull/46), **draft**. Исправлять **только текущую ветку** \`codex/unified-desktop-ux-2026-11-08\`. **Не создавать новый PR, не делать merge в main**. Перед правками проверить актуальный HEAD.  
**Нормативная история:** [третье ТЗ R3-01/LIVE-01](CHESS_COACH_PR46_THIRD_REVIEW_FRESH_CARDS_AND_LIVE_DRAW_2026_11_09.md), [LIVE-01 технический долг](../ux/TECH_DEBT_LIVE_LICHESS_DRAW_OFFERS_AND_RESIGN_LOCK_2026_11_09.md), [основное UX-ТЗ](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md).

## 0. Что предыдущий Codex исправил правильно — не переделывать

- **CI на точном итоговом SHA подтверждён** через GitHub Actions: [run 42602284418](https://github.com/feshakomissarov-prog/chess/actions/runs/42602284418) имеет head_sha \`6fa202f9...\`, **test SUCCESS (977 passed / 429.74 s)** и **visual-evidence SUCCESS**. Тесты также проходили на предшествующем кодовом SHA; report-only коммит не изменил работу.
- **R3-01 — PASS по source review.** В \`dashboard_preview_snapshot\` отдельная SQLite read transaction \`BEGIN\` обеспечивает согласованность COUNT и top-3; \`_render_dashboard_snapshot\` теперь берёт актуальный список из preview и переносит оценки только для строго совпавшего \`game_id + line_revision + account/clock scope + owner color\` при действующем gate. Есть тесты на G4/G3/G2, неизвестный цвет и отсутствие старых графиков. Не повторять работу.
- **Часть LIVE-01 — PASS по source review.** Ожидающее \`offer_draw\` больше **не выключает независимую кнопку «Сдаться»**; \`accept_draw\` и \`decline_draw\` не могут быть одновременно отправлены через service; входящее подтверждённое предложение показывает обе CTA. Сохранены idempotency и Fair Play. Не откатывать этот прогресс.
- **РЕАЛЬНЫЙ two-account Lichess smoke НЕ ПРОВЕДЁН:** Codex честно поставил \`REAL_LIVE_UNVERIFIED\`; этот отдельный этап обязателен для пользовательской приёмки. Successful CI/offline не доказывает end-to-end сетевой сценарий.

**Причина нового цикла:** в коде обнаружены два конкретных граничных случая, которых нет в семи дополнительных регрессиях: *старый повтор stream после ACK* и *терминальный результат с сохранённым draw flag*. Первый непосредственно противоречит требованию «повторный offer исключён, пока это не разрешит актуальное состояние сервера».

---

## 1. R4-01 [P1]: duplicate/stale gameState после HTTP ACK ошибочно снимает pending offer

### 1.1. Подтверждённая кодом последовательность

Источник: [\`src/chess_coach/lichess/service_games.py:72–196\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/lichess/service_games.py#L72-L196).

1. После \`service.draw(game_id, True)\` сервис ожидает событие с \`wdraw\`/\`bdraw\` и не повторяет POST.
2. При завершении HTTP запроса (2xx/429/timeout) \`_action\` присваивает \`action_state["ack_sequence"] = self._game_authoritative_sequence[...]\`. Счётчик \`_game_authoritative_sequence\` увеличивается на 1 **на каждом обработанном \`gameFull/gameState\`**, не проверяя идентичность и фактическую новизну серверного состояния.
3. В \`reconcile_game_actions\` при \`sequence > ack_sequence\` для \`offer_draw\` достаточно \`status in {"created","started","playing"}\` и \`own_draw_flag is False\`, чтобы объявить действие reconciled и очистить \`_pending_actions\`.
4. **Но** stream может повторно прислать старый \`gameState\` (тот же \`moves\`, \`status\`, \`wdraw=False\`, \`bdraw=False\`) после HTTP ACK, например при reconnect/replay или из очереди событий. Счётчик увеличивается, хотя семантически ничего не изменилось.
5. Итог: сервис ошибочно снимает pending, GUI получает \`session_action_reconciled\`; \`session.draw_offered_by_me=False\`, потому что пришёл тот же старый флаг → кнопка \`«Предложить ничью»\` становится enabled → второй POST возможен **без подтверждения, что первое предложение действительно исчезло**.

**Новые тесты Codex** \`test_draw_offer_pending_lifecycle_never_reposts_and_resolves_on_fresh_absence\` используют состояние с *новым ходом* \`e2e4 e7e5\` и не покрывают повтор неизменённого \`gameState\`. Сам \`session.accepted_snapshot_sequence\` в \`session.py\` тоже увеличивается после любого успешно применённого \`gameState\`, включая дубликат старой линии; поэтому существующий \`window.py::_lichess_game_event\` generation guard этого не предотвращает.

Это **доказанный логический дефект** из правил счётчиков. Поведение реального Lichess stream при конкретном повторе ещё нужно подтвердить в live smoke; не выдавать гипотезу сетевой частоты за установленный факт.

### 1.2. Как исправить минимально

- Не использовать только \`sequence > ack_sequence\` как доказательство новой серверной истины. Разделить **«новое событие пришло»** и **«обнаружено новое достоверное серверное состояние относительно исходного offer»**.
- Explicit \`own_draw_flag=True\` после исходного \`False\` является подтверждением offer, а terminal result — завершением партии; отсутствие offer можно считать достоверным лишь при **обоснованном изменении сервера, связанном с жизненным циклом предложения**. Например, после **подтверждённого продолжения линии ходов** или прямого server-resync со свежими данными, когда достоверно известно, что offer больше не активен. Повтор \`gameFull/gameState\` с **идентичными** \`moves/status/draw flags\` **не доказывает** отсутствие offer. Также обычное clock-only/update не должно стирать pending.
- В случае неоднозначного HTTP/stream: **никаких автоматических повторных POST**. Допустимо показать статус «Проверяем состояние предложения ничьей» и выполнить существующий **ограниченный** путь authoritative game resync, если реально доступен и безопасность уже соблюдена. Если достоверный источник недоступен, сохранить fail-safe idempotency и объяснить пользователю, что можно сдаться, но не выдавать старый snapshot за новый.
- Не делать бесконечное ожидание «всё заблокировано»: **«Сдаться» остаётся доступно** при реально активной игре, что уже исправлено. Входящее предложение соперника по проверенному актуальному флагу по-прежнему должно быть можно принять/отклонить, не удаляя случайно старый outgoing pending.
- Сохранить account/auth generation fencing и \`request_id\` deduplication; не вводить таймер, который просто чистит запись и разрешает опасный повтор. Не делать новые сетевые API вызовы «наугад». Если подтверждение очередности между HTTP и stream инженерно невозможно без специального server read, зафиксировать **конкретный** fallback в report и не объявлять режим полностью надёжным.

### 1.3. Обязательные точечные тесты

1. Исходный \`gameState\`: \`moves="e2e4", wdraw=False\`. POST offer возвращает 2xx. **После HTTP ACK приходит точная копия исходного event с теми же moves/status/draw flags**. Ожидаемо: pending offer **сохраняется**, второй вызов \`service.draw(...,True)\` **не создаёт второго POST**, UI не показывает разрешённый повтор. Повторить для rate-limit 429 и ambiguous timeout.
2. То же после replay \`gameFull\`, после clock-only update и при account auth-generation switch — никаких ложных разрешений или утечек предыдущей партии.
3. После реального \`wdraw=True\` впервые пришедшего после offer — pending успешно reconciled, \`session.draw_offered_by_me=True\` и кнопка повторного offer выключена.
4. После подтверждённого нового хода соперника / реального перехода в состояние отсутствующего предложения — корректное согласованное восстановление допустимых действий **без второго автоматического POST**.
5. Incoming \`bdraw=True\` на своей стороне: независимо от stale outgoing pending пользователь видит \`Принять / Отклонить\`, но нельзя отправить оба одновременно; «Сдаться» отдельно доступна.

---

## 2. R4-02 [P2]: статус «Партия завершена» может быть перезаписан устаревшим сообщением о ничьей

Источник: [\`src/chess_coach/window.py:_refresh_online\`](https://github.com/feshakomissarov-prog/chess/blob/6fa202f9f2c9ee4be2785684912c4ddf5642a23f/src/chess_coach/window.py#L7838-L7979).

- Сначала при \`finished_view\` UI устанавливает \`game_status = "Партия завершена · <исход>"\`.
- В конце этого же рендера независимо от \`finished_view\` новый блок делает \`if session.draw_offered_by_opponent\` → \`"Соперник предлагает ничью"\` или \`elif session.draw_offered_by_me\` → \`"Предложение ничьей подтверждено; ожидаем ответ"\`. Это **перезаписывает** финальный результат, если terminal \`gameState\` содержит оставшийся \`wdraw/bdraw\` флаг. Даже если в нормальном live Lichess обнуляет флаги, UI **не должен зависеть от этого неявного условия**.
- Возможный результат: после подтверждённого \`resign/draw/mate\` игра завершилась, кнопки скрыты, но главная строка уверяет «Ожидаем ответ соперника».

**Исправление:** приоритет authoritative terminal/result над любыми draw-offer уведомлениями. Блок текстов предложения ничьей выполнять **только пока session действительно active**; на завершённой партии отображать итог/сохранение/возможность Review. Не удалять сами draw flags с целью замаскировать проблему, не менять status серверного game result.

**Тест:** \`gameFull/gameState(status="draw" или "resign", wdraw=True/bdraw=True)\` → \`game_status\` содержит \`"Партия завершена"\` и правильный исход, **не содержит** \`"ожидаем ответ"\` и \`"Соперник предлагает ничью"\`; кнопки offer/accept/decline/resign disabled/hidden согласно finished view. Проверить также human timeout и terminal state после draw accepted.

---

## 3. Definition of Done

- [ ] Исправлены только R4-01 и R4-02, сохранены уже работающие R3-01/Home, LIVE-01 (independent resign + incoming CTA), Fair Play, SQLite, реальная игра.
- [ ] Регрессии на точно повторённый \`gameState\` после ACK, clock-only replay, фактически новый state, terminal result + draw flags. **Никаких auto resend при неоднозначном HTTP.**
- [ ] Полный локальный \`pytest\`, \`compileall\`, \`git diff --check\`, Windows Actions \`test\` и \`visual-evidence\` **SUCCESS на новом точном code+report HEAD**. Не ломать существующие **977 passed**.
- [ ] В \`docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md\` отражены фактический root cause, SHA, все тесты и нюансы source-stream HTTP ordering; **REAL_LIVE_UNVERIFIED** сохраняется, пока действительно не было двух живых аккаунтов.
- [ ] Draft PR #46 остаётся draft, \`main\` не тронут, пользовательские файлы/DB не изменены, новых PR нет.
- [ ] Затем отдельная ручная приёмка: 13/13 side-by-side UX, Windows cold start с личной БД, **две живые Lichess учётные записи** для offer→decline→later incoming offer→accept и независимого resign. Не объявлять эту ручную проверку пройденной до реального воспроизведения.

**Ключевой принцип:** полная система не должна основывать право повторить исходящее предложение ничьей на **порядковом номере полученного сообщения** вместо достоверно изменённого серверного состояния.
