# M4.6 PR #41 — повторное ревью: точечное P0 Fair Play исправление

**Дата:** 2026-11-02. **Проверенный SHA PR #41:** `da10b6c25586af46aea3ba5ed2679690874a5cdc`.
**Статус:** блокирующее замечание к технической приёмке. **После исправления и зелёного CI — снова независимое ревью, затем ручная Windows-проверка владельца.**
**CI проверенного SHA:** https://github.com/feshakomissarov-prog/chess/actions/runs/41379342194 — success, 439 PASS.

## Подтверждено исправленным

Проверка `game_id + line_revision + line_hash + account/session`, немедленная блокировка после `gameStart` с человеком, UI masks оценок и PV, ограниченное представление полной/частичной оценочной линии, scoped dashboard, расширенные карточки, `cwd` у Windows launchers. Упомянутые основные регрессии существуют, CI успешен.

## P0 — существующий разрешённый review не инвалидируется при некоторых потерях безопасного статуса

**Исходники:**
- `src/chess_coach/window.py:_lichess_status`: статусы `Ограничение Lichess (429)`, `Переподключение…`, `Ошибка подключения` и подобные вызывают `_disable_position_evaluation`/`_pause_batch_analysis`, но **не** вызывают `_revoke_review_safety`. После успешного `_review_safety_games_received(...)` у `review_workspace.training_allowed` и `evaluation_allowed` остаётся `True`, хотя состояние сети неизвестно. `_invalidate_analysis_preflight` сейчас используется лишь для `Не подключено` и явных сценариев.
- `src/chess_coach/window.py:_revoke_oauth_lichess` вызывает `self.lichess.revoke_oauth_credential()` без предварительного `_invalidate_analysis_preflight`/`_revoke_review_safety`. Сервис меняет auth generation, прекращает сессию и выдаёт статусы `Отзываем сохранённый OAuth-токен…` и `OAuth-токен отозван и удалён локально.`; эти статусы `_lichess_status` не инвалидируют review lease. Пользователь продолжает видеть сохранённый engine PV до нового явного действия.
- `src/chess_coach/ui/game_review.py` корректно закрывает `show_hint/check_training_move` после вызова `revoke_safety`, но системная инвалидизация вызывается не на всех переходах.

**Исправить одним небольшим блоком в существующем PR #41:**
1. Унифицировать событие `network/auth safety unknown`: перед тем как перестать доверять Lichess сессии, сразу сбрасывать review safety lease, прерывать текущую review-тренировку, скрывать график, оценки, PV, классы ошибок. Статусы 429, reconnect, auth error, revoke OAuth, logout — инвариант. Не ограничиваться остановкой живого Stockfish worker.
2. В момент явного revoke OAuth сбрасывать UI/online-state и safety preflight **до** сетевого запроса. Успешный/неуспешный результат не должен неявно снова давать разрешение, нужен новый актуальный полный preflight.
3. Не делать автоматический re-enable при восстановлении сети; разрешение только после нового `check_active_games_for_analysis` c полной валидной проверкой. Сохранить отдельно изолированный `--offline` режим без доступной Lichess human-партии.
4. Централизовать guard так, чтобы будущее изменение статуса не забывало ещё один UI-контекст. Не ломать остальные position/batch fair-play проверки.

**Обязательные детерминированные тесты (в дополнение к существующим):**
- Предварительно `review_workspace.evaluation_allowed=True` после safe snapshot → `_lichess_status('Ограничение Lichess (429): ...')` → `False`, график `_values()==[]`, PV скрыт, exercise остановлен, `show_hint` не показывает решение.
- Аналогично `Переподключение…`, `Ошибка подключения`/ошибка авторизации; после `Подключено...` разрешение **не** восстановилось само.
- Revoke OAuth: с ранее разрешённым обзором → пользователь подтверждает отзыв → lease отозван **синхронно до старта фоновой сети**, независимо от результата; PAT не отзывается.
- Поздний ответ safety предыдущей auth generation и после смены review линии остаётся невалидным.
- Физический Lichess/OAuth/PAT не использовать в автоматических тестах. Полный Windows CI на exact head SHA, обновить `docs/handoffs/M4_6_EXECUTION_LOG.md` и `docs/PROJECT_STATUS.md`. PR оставить draft, **не merge**.

## Границы

**Не добавлять** новые функции, не переписывать dashboard/review, не начинать M4.7 или образовательную таксономию, не менять пользовательские секреты и SQLite. В конце: итоговый SHA, CI, результат тестов, что осталось непроверенным физически. После этого проверка ChatGPT и ручной тест владельца.
