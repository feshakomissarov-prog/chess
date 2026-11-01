# PR #30 — C0–C2 code review: асинхронное отключение и переключение OAuth/PAT

**Дата:** 2026-11-02. **Проверенный HEAD:** `4e6af52d01dd637a4afee4716960a8b343524556`. **CI:** [Windows #110](https://github.com/feshakomissarov-prog/chess/actions/runs/41360769481) — 375 PASS, compileall и Qt offscreen PASS. **Контекст:** [основное C0–C2 ТЗ](PR30_M451_OAUTH_403_SECURE_CREDENTIALS_SPEC.md), [draft PR #30](https://github.com/feshakomissarov-prog/chess/pull/30). Документ основан на независимом статическом ревью: текущие C0–C2 в целом реализованы; **одна доказанная гонка при смене типа подключения требует коррекции до окончательного merge**. Реальные Lichess/OAuth/PAT не проверялись этим ревьюером. Не менять личный архив SQLite, секреты, не переустанавливать Stockfish и не начинать M4.6.

## S0 — P1: отключение читает уже НОВЫЙ метод подключения

**Фактический код:** `src/chess_coach/lichess/service.py:625–648`, `disconnect_account()` вызывает `self._cancel_active_session()` и запускает `threading.Thread(work)`. Только **внутри фонового work()** читает `self.token_store.get()` и `self.auth_method`; но оба являются динамическими, а `self.token_store` выбирает credential slot по **текущему**, уже изменённому `auth_method`.

**Детерминированно воспроизводимая гонка:** пользователь подключён через PAT и нажал «Отключить»; до запуска work() переключил radio на OAuth, для которого есть сохранённый действующий credential. Старый work() теперь читает **OAuth** credential вместо PAT, выполняет `client.revoke_token(oauth_token)` и `oauth_store.delete()`. В итоге **отозван не тот токен**, нарушено «оба независимых метода», и при этом сервис асинхронно обнуляет уже новое `self.username` и отправляет `Не подключено`. Обратное переключение OAuth→PAT также способно испортить статус новой сессии. Если пользователь повторно подключится до завершения work(), старое work() вызывает `self._connection_stop.set()` на **новом** stop-event и ломает stream.

**Требование:**
- Захватывать `auth_generation`, **метод, выбранный credential store**, session-specific stop-event и контекст до запуска фонового work, под нужными lock. Никакого позднего `self.token_store`/`self.auth_method`/`self._connection_stop` для принятия решений старого disconnected work.
- **PAT disconnect никогда не вызывает remote revoke**; только остановить текущую сессию. Если выбран OAuth, remote revoke можно выполнить лишь для токена, который принадлежал именно этому захваченному OAuth-контексту; свежий OAuth, полученный после начала disconnect, не трогать. Решить явную семантику: «Отключиться» (остановить сессию, сохранить token для restore) отдельно от «Отозвать OAuth на сервере» и «Удалить локальный PAT»; не менять существующий UX молча.
- Старый disconnect worker не должен устанавливать `username=None`, `connection_stop.set()`, `status_changed("Не подключено")` или `error.emit` для **нового** auth_generation. Ошибка старой операции может попасть в обезличенный log, но не в GUI новой успешной сессии.
- Если выбранный слот был заменён новым токеном во время ожидания remote revoke, нельзя после ответа сервера удалять или отзывать новую запись. Для этого сравнивать revision/identity токена локально без логирования самого токена; тест должен покрывать replace-after-start.
- Ни один секрет не должен быть сохранён в GitHub, QSettings, SQLite, тестовых fixtures с реальным токеном или diagnostics.

## S1 — P1: старые event-stream/status-сигналы могут отключить новое подключение

**Доказательство:** `service.py:_start_event_stream` захватывает `auth_generation` и `connection_stop`, но после выхода из цикла **безусловно** вызывает `self.status_changed.emit("Не подключено")`. Аналогично в старой ветке `except RateLimitError` / `except LichessError` могут отправляться `Переподключение…`, ошибка 403 или другие статусы, когда пользователь уже переключился и новая сессия подтвердилась. `window.py:_lichess_status` обрабатывает «Не подключено» как реальную потерю доступа и отменяет анализ/предход и online controls. Защита по generation имеется в `finally` только для управления флагом `_event_stream_started`, **не для UI-событий**.

**Требование:** перед каждой управляющей GUI status/error/event emit из старого event stream и сессии проверять актуальность `auth_generation` и identity `connection_stop`; желательно централизованная функция emit-if-current или generation-tagged signals с проверкой на стороне GUI для задержанных queued Qt-сигналов. Не чинить "костылём", разрешающим показывать Stockfish при неизвестной онлайн-партии — fair play должен оставаться fail-closed. При смене аккаунта очищать/отвязывать **старые** online_sessions и запоздалые game events так, чтобы прежняя партия не стала текущей и не смешалась с новым аккаунтом.

**Acceptance:** поток A блокируется перед отправкой terminal status; переключить auth method, получить `connected` от потока B; освободить A; UI продолжает показывать B-connected, Stream B не останавливается, чужие events не попадают в новую сессию, engine safety остаётся fail closed до нового preflight.

## S2 — тесты и короткий путь к ручному пилоту

1. В `tests/test_lichess_credentials.py` добавить **детерминированные tests с Event/Barrier**, без `sleep()`-догадок:
   - disconnect(PAT A) → немедленно select OAuth B (оба store заполнены) → старый work освобождается: **0 remote revoke, OAuth B остаётся, username/status B не сброшены, его stop-event не тронут**;
   - disconnect(OAuth A) → select PAT B → устаревшие сигналы не портят B; повторный disconnect / reconnect без утраты PAT;
   - pending disconnect(OAuth A) → новый OAuth credential в том же store → старая операция **не удаляет/не отзывает новую запись**;
   - event stream A при switch в B выдаёт поздние `Не подключено`/403/429/gameStart/gameFull → B не теряет connected controls и не принимает чужие events; отсутствие сохранения секретов;
   - аккаунты с **одинаковым username**, но разной сессией, чтобы нельзя было подтвердить старое queued signal одним совпадением строки;
   - no network before explicit click при `--manual-connect`; успешная restore выбранного credential без повторного ручного ввода, обе credential slots остаются независимыми.
2. Текущий стандарт: полный Windows Python 3.12 / Qt offscreen suite (не менее 375 PASS при новых тестах), compileall, `pip check`, `git diff --check`; CI на exact финальном SHA. Синтетические fake credentials, **никаких настоящих Lichess запросов/токенов в CI**.
3. Исполнить **в текущей ветке `codex/m45-batch-analysis` и том же draft PR #30**. Обновить `docs/handoffs/M4_5_EXECUTION_LOG.md` и статус; не merge, не force push. Никакой дополнительной UX-перестройки: это один адресный race fix до пользовательской проверки.
4. **После фикса** владелец самостоятельно отзывает ранее раскрытый PAT на сайте, создаёт новый (не присылая в чат), тестирует через приложение OAuth и PAT по одному, перезапуск и восстановление, осторожное переключение/отключение; затем, если Lichess account API проверен и активной human game нет, ограниченный Stockfish анализ **ровно 2 завершённых партий** (20k nodes, 64 MiB Hash, retry OFF, selective OFF). Не запускать весь архив.
5. Если Lichess и новый PAT вновь отвечают HTTP 403 на /api/account, собирать только обезличенный stage/status/response_type/time — причина этого серверного отказа до реального сетевого пилота остаётся неизвестной. **Никакая имитация OAuth 200 в CI не доказывает, что реальный сервер примет токен.**

**Decision:** C0–C2 код принят для дальнейшего развития, однако при переключении двух типов обнаружена реальная гонка с риском отзыва чужого сохранённого OAuth credential. S0–S1 исправить перед финальной ручной приёмкой; S2 проверить точным Windows CI. Никаких новых крупных ТЗ, переход к M4.6 только после приёмки M4.5.1.
