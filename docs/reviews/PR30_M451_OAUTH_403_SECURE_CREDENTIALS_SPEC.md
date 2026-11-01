# PR #30 — OAuth HTTP 403: безопасная диагностика и локальное хранение подключения

**Дата:** 2026-11-02. **Контекст:** draft PR #30, последняя проверенная реализация b326ec36ba02c3b08bfed3e187d27daecf62380b. **Статус:** адресное ТЗ Codex для текущей ветки codex/m45-batch-analysis, не M4.6. PR оставить draft.

## 0. СРОЧНАЯ безопасность — без раскрытого секрета

Владелец случайно опубликовал personal access token в скриншоте/тексте переписки. **Токен скомпрометирован: пользователь должен отозвать его на https://lichess.org/account/oauth/token до других действий.** Никакую часть его значения не переписывать в код, issue, PR, commit, Codex prompt, конфигурацию, тесты, логи или примеры. Codex НЕ должен использовать этот секрет. Новый персональный токен выпускать только при реальной необходимости, с минимальными разрешениями, локально на ПК владельца. Не запрашивать все полномочия. Для Chess Coach по умолчанию предпочтительна существующая безопасная браузерная OAuth PKCE-авторизация.

## 1. Что фактически воспроизведено

- Две попытки восстановления сохранённого OAuth дали в диагностике: event=api_request_failed, http_status=403, mode=account, component=lichess_api; GUI: «Ошибка подключения» и «Lichess запретил действие».
- Владелец удалил только старое сохранённое подключение из Windows Credential Manager штатным WindowsCredentialStore.delete(), повторил браузерную авторизацию. Браузер показал локальную страницу об успешной передаче OAuth callback, но Chess Coach всё равно не подключился, с тем же отказом 403. Значит callback и проверка доступа приложения — РАЗНЫЕ этапы; браузерная страница сама по себе не доказывает вход.
- Исходники: src/chess_coach/lichess/auth.py:authorize_and_store обменивает code на token, затем вызывает GET /api/account и сохраняет токен только если запрос успешен. В lichess/api.py:_error_message для всякого 403 показывается один общий текст без определения стадии. Lichess API docs: https://lichess-org.github.io/api/ — GET /api/account требует аутентификацию, без дополнительных OAuth scopes; полные разрешения не исправляют этот endpoint автоматически.
- Отдельно созданный на сайте персональный PAT не является токеном, автоматически полученным PKCE-процессом Chess Coach, и не должен импортироваться из сообщения пользователя. Никаких утверждений о блокировке аккаунта без серверного доказательства.

## C0 — P0: хранить подключение безопасно, не в GitHub

1. Оставить OAuth 2.0 PKCE основным методом. При запуске штатной версии делать restore из Windows Credential Manager, без ввода каждый раз. Флаг --manual-connect не совершает запросов до клика, но после клика может восстановить сохранённый токен. Не менять прежний ключ WindowsCredentialStore.SERVICE/USER без явной миграции.
2. Лишь после обмена authorization code, успешного GET /api/account и корректного account identity — записать токен в Credential Manager и показать «Подключено». Никогда не писать секреты в Git, QSettings, SQLite, .env, лог, аргумент командной строки, PowerShell history или HTTP dump.
3. При 401 предлагать отдельную явную кнопку переподключения. При 403 не запускать бесконечную повторную OAuth-авторизацию: показать отдельное «Проверка аккаунта отклонена Lichess (HTTP 403)» и кнопку безопасной диагностики. При 429 соблюдать Retry-After.
4. Опциональный импорт нового PAT рассматривать только если это действительно понадобится после расследования. Если реализуется, секрет пользователь вводит только на своём ПК в masked password input, подтверждает перечень минимально необходимых scopes, после успешной проверки GET /api/account сохраняется ТОЛЬКО в Credential Manager. Полный токен нельзя показывать, логировать, передавать Codex. Не переносить скомпрометированный токен. Предпочтительный MVP — PKCE без импорта PAT.
5. У Chess Coach две роли доступа: board:play + challenge:write для игры, puzzle:read — отдельный credential, как сейчас. Никаких «все разрешения» по умолчанию. При 403 сохранять офлайн-доступ к ранее импортированным партиям и задачам, fair-play preflight остаётся fail-closed, batch analysis блокируется.

## C1 — P0: выяснить, почему GET /api/account отвечает 403

1. Логировать без секретов стадии: oauth_callback_received, oauth_exchange_success/failed, account_check_started, account_check_failed, credential_store_success, connection_ready, restore_started/failed. Для HTTP ошибок: method GET, allowlisted route /api/account, numeric HTTP status, duration, response classification (JSON error vs HTML denial vs other), exception class, correlation ID собственного процесса. Не сохранять response body, URL с query, headers, raw server error text, account name, email, IP, User-Agent full error, персональные данные. Для server JSON допустимо писать только проверенный allowlisted machine-readable error code.
2. Убедиться, что UrlLibTransport и LichessClient.get_account корректно отправляют Authorization: Bearer, GET /api/account, OAuth client ID, PKCE verifier, redirect_uri и content types. Не заменять GET /api/account неавторизованным публичным запросом ради «успешного» входа.
3. 403 может означать отказ сервера/edge protection или отдельную аккаунтную/сетевую политику, но точная причина сейчас НЕ установлена. Разделить серверный отказ и невалидность токена (401), отсутствие scopes и 429. Не просить выпустить десятки токенов, не запускать агрессивные повторы.
4. Экран браузера после callback должен говорить «Код передан Chess Coach, проверку завершит приложение», а не вводить в заблуждение «Авторизация завершена». GUI: понятный этап, статус и кнопка «Открыть обезличенный отчёт». Никаких блокирующих повторяющихся QMessageBox.
5. Если после исправлений Lichess продолжает возвращать 403 на GET /api/account даже с новым корректно выданным PKCE-токеном, зафиксировать sanitized time/status/route/response_type и предложить обращение в Lichess API support. Без обхода правил/ограничений и без передачи настоящего токена.

## C2 — регрессия, приватность, выпуск

- Fake tests: прежний OAuth token valid -> restore OK; invalid 401 -> явная переавторизация; OAuth callback + exchange success + account 403 -> НЕ подключён, новый token НЕ сохранён, безопасное сообщение; 403 HTML/JSON categorization; 429 cooldown; manual mode не делает запросов до клика; keyring unavailable -> локальные данные доступны. Не выводить в UI «connected» до account validation.
- Подставные тестовые строки токенов, OAuth authorization code, email, username, PGN, home paths ни разу не оказываются в stdout, log, diagnostics, QSettings, SQLite и PR. Проверить Unicode Windows-пути, rotation, отсутствие секретов в трассировке.
- Запустить Windows Python 3.12 полные тесты (baseline 360), Qt offscreen, compileall, pip check, git diff --check на точном final SHA. Любой реальный OAuth тест с личным аккаунтом выполняется только при отдельном разрешении владельца на его ПК, не в GitHub Actions; не использовать опубликованный токен вообще.
- Обновить docs/handoffs/M4_5_EXECUTION_LOG.md и docs/PROJECT_STATUS.md; исправления C0–C2 выполнить в старой ветке и PR #30, не merge, не начинать M4.6. Синхронизировать docs-only main без потери кодовых изменений, не force-push.
- Handoff Codex: точный SHA/CI, что подтверждено автоматикой, что требует нового живого OAuth-теста, безопасный лог и простой список проверок владельца; статус DONE/PARTIAL. Перед тестом Stockfish двух завершённых партий нужно сперва пройти account verification и safety preflight. База пользователя/архив/Stockfish 19 не изменяются.

**Данные секретного токена в этом файле отсутствуют и НЕ нужны исполнителю.**