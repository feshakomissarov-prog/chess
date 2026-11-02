# M4.6 PR #41 — последнее ревью: восстановление Lichess после краткого обрыва

**Дата:** 2026-11-02. **Проверенный SHA:** `fcf6fae5a1a6257f9f22d4e5d780427e690873e7`. **Windows CI:** [#41383544159](https://github.com/feshakomissarov-prog/chess/actions/runs/41383544159) **SUCCESS, 448 PASS**. Основные safety-corrections предыдущего ТЗ реализованы корректно, но обнаружена конкретная функциональная регрессия. **НЕ merge и не объявлять M4.6 готовым к ручной приёмке до фикса**.

## R1 — account connection теряется навсегда после временного stream reconnect

**Однозначная цепочка по коду:**

1. `src/chess_coach/window.py:4792–4812`: `_lichess_status("Переподключение…")` вызываeт `_invalidate_analysis_preflight()` (**правильно**, lease сбрасывается), затем из-за `_lichess_status_requires_reauthentication("Переподключение…") == True` выполняет `self._lichess_connected = False` (**ошибочно для временной потери stream**). Точно так же неправомерно сбрасывается аккаунт на `"Потеряно соединение с партией; синхронизация…"` и `"Финальная синхронизация партии…"`.
2. `src/chess_coach/lichess/service.py:763–821`: после сетевого `LichessError` поток посылает `"Переподключение…"`, спит и снова выполняет `stream_events` в том же цикле и той же `auth_generation`. `session_authenticated` при этом **не вызывается**. `"Подключено"` посылается только один раз до начала цикла (`_start_event_stream:779`), **не после успешного reconnect**.
3. `window.py:_lichess_authenticated` — тот путь, который снова ставит `_lichess_connected = True`, но он вызывается только после новой проверки credentials, а не после reconnect stream. Поэтому после временной сетевой ошибки backend может продолжить получать события, а GUI сохраняет `_lichess_connected=False` навсегда — заблокированы онлайн-кнопки, `_online_actions_allowed()`, фоновые синхронизации и свежий review preflight.
4. Событие потери только **одного game stream**, например pending final sync, также сбрасывает доступность **всего** Lichess-аккаунта. Это особенно нежелательно.

## Исправление, минимально и адресно

- **Развести два понятия**: (a) `review_engine_safety_lease` — безусловно отозвать на любом недостоверном/потерянном сетевом статусе; (b) `account_authenticated/connected` — очищать **только** при реальной потере авторизованной сессии, logout, отказе проверки credentials/401/403, явном revoke/switch. Временные `Переподключение`, сбой отдельного game stream и ожидание финальной синхронизации не должны без отдельного факта вызывать безвозвратную потерю authenticated identity.
- Для transient account event-stream outage UX должен честно показывать `reconnecting`, не считать сеть доступной до подтверждения связи и не отправлять stale move; при успешном stream recovery вернуть доступность UI через наблюдаемое подтверждённое событие (отдельный сигнал `stream_recovered` или явный status, без пересоздания авторизации), **но не восстанавливать review lease**. Выбор minimal safe implementation за Codex. Просто бесконтрольно делать `_lichess_connected=True` на строку `Подключено` нельзя без связывания с актуальным generation/account.
- При 429 соблюсти Retry-After; после него не выдавать PV/engine/тренировочные hints без **нового полного safety preflight**.
- Исправить только этот дефект в **том же PR #41**, без расширения функциональности.

## Детерминированные тесты

1. `connected=True, review lease=True`; `Переподключение…` → `review lease=False`, exercise stopped и PV скрыт. Смоделировать **повторное получение stream events после reconnect на той же generation**: не остаётся перманентно `_lichess_connected=False`, UI может продолжить работу после подтверждённого восстановления; review lease **всё ещё False** до свежей проверки.
2. Сбой одного game stream (`Потеряно соединение с партией` и `Финальная синхронизация…`) → review lease отозван, а authenticated account не потерян.
3. Logout/401/403/revoke OAuth → auth account обнуляется и не восстанавливается автоматически; PAT revoke не происходит.
4. 429 не теряет identity, соблюдает cooldown и не позволяет ещё одну safety-проверку до окончания Retry-After.
5. Повторить полный Windows CI на exact SHA; не подключаться к реальному Lichess и не трогать пользовательские PGN/SQLite/token.

## Вердикт

**До исправления R1 — технический NO-GO, PR #41 draft, без merge.** Предыдущее исправление Fair Play подтверждено по коду и CI, это **новая регрессия маршрута восстановления соединения**, не повод переделывать M4.6. После targeted fix/CI — независимая проверка и затем GUI smoke владельца.
