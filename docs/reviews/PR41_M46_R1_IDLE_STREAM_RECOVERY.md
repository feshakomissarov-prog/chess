# PR #41 — R1b: «тихий» event stream после reconnect не восстанавливает online availability

**Дата:** 2026-11-02. **Проверенный head:** `4350b27a3b607fb7802a5eed60e65d7693308bd7`. **CI:** [Windows #41391918417](https://github.com/feshakomissarov-prog/chess/actions/runs/41391918417), **452 PASS**, compileall и offscreen window initialization PASS. PR по-прежнему draft; не merge. Ревью ограничено последним R1. Все предыдущие основные P0–P2 corrections приняты по коду/CI.

## Находка R1b — P1, блокирует допуск к ручному пилоту

**Фактически доказуемая цепочка:**

1. `src/chess_coach/lichess/service.py::_start_event_stream.work()`: при `LichessError` выставляется `recovering_stream=True`, отправляется `Переподключение…`; `src/chess_coach/window.py:_lichess_status` отключает `_lichess_connected` и онлайн-действия, без восстановления review lease.
2. Успешный сигнал `Поток восстановлен: Owner` генерируется **только внутри `for event in self.client.stream_events(...)`**, непосредственно перед первым **непустым JSON event**. Если stream подключился и сервер присылает только keepalive, сигнал не произойдёт.
3. `src/chess_coach/lichess/api.py::stream_ndjson()` выполняет `if not line: continue`: все пустые строки keepalive отбрасываются и никогда не доходят до `service.py`.
4. **Официальная документация Lichess** на `/api/stream/event`: «An empty line is sent every 7 seconds for keep alive purposes», а при открытии заново отправляются **только существующие активные games/challenges**. Если у аккаунта нет игр или приглашений, поток может быть восстановлен и жив при отсутствии JSON events; online availability навсегда остаётся выключенной, хотя соединение работоспособно. Официальная спецификация: https://github.com/lichess-org/api/blob/master/doc/specs/tags/board/api-stream-event.yaml
5. Новые тесты `test_event_stream_recovery_is_reported_only_after_same_generation_event` и `test_transient_event_stream_recovery_restores_availability_not_review_lease` проверяют сценарий с **обязательным JSON `challenge` после reconnect**, а не idle / only-keepalive.

**Отдельная оговорка:** при общем event-stream HTTP 429 `_lichess_connected` остаётся true, хотя `recovering_stream=True`. `_online_actions_allowed()` проверяет только account flag и истечение cooldown. Поэтому по истечении Retry-After онлайн-кнопки могут стать доступны **до подтверждения фактического восстановления event stream**. Это противоположная сторона той же ошибки: смешаны authenticated identity, transport readiness и engine review lease. Не превращать устранение в бессрочное отключение аккаунта.

## Минимально требуемая правка внутри существующего PR #41

- Развести **authenticated identity**, **event-stream availability** и **review Fair Play lease**. Последний всегда остаётся revoked на reconnect/429 до нового явного complete preflight, даже если transport снова доступен.
- Сигнал «поток восстановлен» должен приходить при **факте реально восстановленного соединения**, без требования будущего шахматного события: например, при получении новой keepalive от HTTP или после успешного установления HTTP stream с актуальными headers. Не считать достаточным прохождение локального таймера. Поскольку `stream_ndjson` скрывает пустые строки, добавить безопасную/типизированную транспортную signal/callback семантику без выдачи пустого keepalive в `_lichess_event` как игровой event.
- Подтверждение должно быть привязано к текущим `auth_generation` и `connection_stop`; устаревшее/отменённое восстановление не включает кнопки. Повторный онлайн-поток не должен по ошибке запускать ещё один OAuth/PAT flow.
- Для `429` не разрешать online actions только из-за того, что закончился cooldown, **пока глобальный event stream не подтвердил восстановление**; сохранять возможность корректной попытки восстановления и не мешать timeout. Не распространять статус ошибки отдельного `game stream` на authenticated identity всего аккаунта.
- Сохранить работающие сценарии: logout/401/403/OAuth revoke; active human gameStart; preflight привязку к game/line/account/session; блокировку CP/PV/подсказок во время human live; отдельный offline mode. Никаких изменений реальных пользовательских данных или токенов.

## Регрессионные сценарии

1. Fake event-stream: initial success → transient disconnect → new stream returns only `b"\n"` keepalive/валидные заголовки, **нет ни одного game/challenge JSON** → account available after observed live stream, review lease remains false, graph/PV hidden.
2. Вариант: retry после 429, cooldown истёк, но stream ещё не подтверждён — online actions остаются заблокированы; затем подтверждение от сервера допускает действия, не включает review lease.
3. Stale generation и cancelled `connection_stop`: пришедшая позже keepalive/stream-ready сигнал не восстанавливает доступ.
4. Existing game event reconnect scenario, single game stream failure, logout/401/403/revoke OAuth продолжают проходить.
5. Полный Windows CI на **точном итоговом SHA** и обновление execution log. PR не сливать, GUI owner's manual validation отдельно.

## Вердикт

**R1 закрыт частично**, а не полностью. P1 «залипание offline UI на пустом event stream» критично для пользовательской работоспособности после типичного временного обрыва. После одной ограниченной доработки и CI — технический допуск к ручной проверке без новой полной ревизии M4.6.
