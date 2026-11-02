# PR #41 M4.6 — независимое ревью Windows pilot UX fixes, 02.11.2026

**Проверенный SHA:** `0c6ab57100368eaf7a6b3584fa9256ac36820d65`. **PR #41:** draft, open, not merged. **Windows Actions [#41413990394](https://github.com/feshakomissarov-prog/chess/actions/runs/41413990394): SUCCESS, 475 PASS**, `compileall` и isolated offline GUI initialization PASS. Сравнивали с `d4123ba4e68b37d010602b06174a4b72c87d4b6e`: 18 changed files, три коммита. **Кодовый NO-GO для manual acceptance до двух связанных P0 safety fixes ниже.** Не расширять и не переделывать готовый GUI.

## P0-A. Блокировка Stockfish обходится обычным подтверждением при отсутствии достоверной Lichess сессии

**Прямой путь:** `src/chess_coach/ui/game_review.py::analysis_button` → `MainWindow._request_saved_game_analysis` (`window.py:5855+`) → `_start_game_analysis` (`window.py:5822–5851`).

Условие `if not (self.lichess and self._lichess_connected)` показывает `QMessageBox.question` «Подтвердите, что сейчас не участвуете в онлайн-партии с человеком» и при Yes **напрямую** вызывает `_launch_game_analysis(game_id, engine_path, options)`. Это **без** `check_active_games_for_analysis`. Оно срабатывает не только при действительно изолированном `--offline`, но и в normal/`--manual-connect` приложении, если пользователь ещё не подключился, Lichess упал, session logout/revoke, или stream recovering. Для этих состояний realtime human-game status неизвестен; субъективная кнопка Yes **не является preflight**.

Сам `CompletedGameAnalyzer.analyze(..., active_human_game_ids=...)` получает лишь snapshot локального `online_sessions`: не видит живую партию, запущенную/продолжающуюся на другом устройстве или в момент сетевого обрыва. Поэтому guard не компенсирует отсутствие `/api/account/playing`.

**Обязательное исправление:** разделить режимы:
- **Изолированный `--offline` без Lichess service/credential/activity** — разрешается локальный анализ завершённых saved games в соответствии с offline policy, без network preflight, с явным указанием offline режима. Не давать обычному desktop/manual-connect режиму представиться изолированным.
- **Любой normal/manual-connect режим и любой существующий Lichess identity/service, даже disconnected/reconnecting/429** — fail closed, **никакого запуска UCI** по пользовательской самоаттестации. Показать «Подключите Lichess и дождитесь новой проверки активных партий»; когда transport готов, полный `check_active_games_for_analysis` с действующими account/generation/line guards. 429 соблюдать Retry-After. Никаких скрытых fallback Yes.
- Сделать единый invariant для всех путей запуска postgame analysis (review CTA, «Мои партии», analytics, batch). Сохранить time/context binding и ограничения на human online game.

**Тесты:** normal/manual-connect при `self.lichess=None`, при `self.lichess` но connected=False, на transient stream reconnect, на 429, на revoke/logout → **не вызывают** `CompletedGameAnalyzer`/`_launch_game_analysis` даже если `QMessageBox.question` mock returns Yes. Только чистый `offline_mode=True` без сервиса может запустить локальное offline engine в изолированном профиле. Connected safe full preflight по fake response допускает, human/unknown/empty truncated/error запрещают по действующим тестам. Никаких реальных вызовов Lichess.

## P0-B. Работающий послепартийный Stockfish не прерывается при сетевой/авторизационной потере доверия

**Прямой путь:** `MainWindow._lichess_status` (`window.py:4964+`) при `Ошибка`/`Переподключение`/`429` вызывает `_invalidate_analysis_preflight` (`window.py:4834–4844`). Он отзывает review, ставит на паузу batch и отменяет позиционную оценку, **но** вызывает `_cancel_analysis_action` **только когда `_analysis_safety_phase == "preflight"`**. Когда preflight успел завершиться и `_analysis_safety_phase == "running"`, `self._analysis_stop` **не устанавливается**. Следовательно, запущенный `CompletedGameAnalyzer` продолжает оценку позиций после потери доверенного статуса. `gameStart` человеческого соперника отдельно выставляет stop, но восстановление/429/logout до получения gameStart остаются опасными.

**Обязательное исправление:** при любом status, отзывающем safety, синхронно `stop.set()` для post-game worker и stop/cancel running analysis, UI ставит «Остановлено: нужна повторная проверка безопасности». Не уничтожать worker thread/process прежде его завершения; cleanup/versioned result guard. Обработчик `analysis_finished` не должен заново показывать/PV или принимать stale successful result после safety generation/session change. При стабильном connected состоянии и отдельном `--offline` allowed path нормальная работа сохраняется.

**Тесты:** fake blocking CompletedGameAnalyzer получает `stop_event`, после safe preflight входит running, затем `Переподключение`, `429`, `Ошибка подключения`, logout, OAuth revoke → stop set, callback from older generation ignored, PV/graph/hint remain masked, no auto restart on recovery; clean cancellation and close without deadlock. Проверить human gameStart не регрессировал.

## P1-C. Некорректная локализация части ECO через слишком широкий fallback

`src/chess_coach/ui/display.py::russian_opening_name` переименовывает **любой ECO D00–D05** как «Лондонская система». Это неверно: D01 — атака Рихтера—Вересова, D03 — атака Торре; D05 в общем случае тоже не следует автоматически именовать «Лондонская система». Тест `tests/test_display.py::test_opening_families_translate_deterministically_and_keep_eco` даже закрепляет недостоверный fallback `D05 → Лондонская система`. Требование владельца «честный русский дебют» требует исключить неоправданную автоматическую подмену ECO: использовать точный verified opening name или безопасный общий «Дебют ферзевой пешки» для неопознанной ветви. Если имя английское в импортированном PGN ясно указывает London, допустима такая локализация; один ECO D05 для этого не достаточен. Добавить кейсы `D01`, `D03`, `D05` with unknown names и named London.

## Уже проверенные положительные изменения

- Date/time formatter, raw clock preservation/filter labels, русские названия известных семей, компактные карточки с двухсторонней статистикой и **н/д** вместо вымышленной Lichess accuracy; исходные full records не меняются.
- Reusable MoveTable with one row per fullmove; correct per-cell ply mapping; graph/board sync, compact evaluation and source.
- UCI helper `hidden_process_options()` using Windows `CREATE_NO_WINDOW` and startup flags, shared across launch sites; `position_analysis` caches engine process and yields incremental `info` back to Qt via signals; nonblocking dashboard worker with generation.
- Best-line legal SAN arrow on chosen review position and hint arrow; safety masking on `set_safety_pending` removes stale arrows/graph/severity. No unverified «strong move»/accuracy invented.
- Local sounds synthesized as original transient clicks with versioned filenames and fresh per-game sound id. Human listening and real Windows QSoundEffect lifecycle remain unverified.
- Analytics and puzzle history labels/navigation improved. Exact UX fit and first-click discoverability cannot be established offscreen.

**Additional nonblocking gaps requiring explicit handoff:** no seeded 1000–3000 game timing benchmark; no actual installed Stockfish (console visibility, process recovery after crash, real eval) or physical 150–200% DPI/audio. Error exercise still only checks cached verified candidate moves: for arbitrary legal candidates text «Не удалось проверить» without on-demand verification. This is an **uncompleted requested capability**, not a false chess judgment; choose whether to explicitly defer with owner approval or implement bounded comparison after safe preflight as originally specified. Also board focus size remains capped by `height-64-210`; verify actual maximum on owner’s desktop rather than claiming 800+ guaranteed.

## Correction scope / release gate

1. Fix **P0-A and P0-B in existing PR #41**; add deterministic regression tests and run full Windows CI **on exact final SHA**. P1-C small data-correctness repair in same change, avoid broader taxonomy rewrite.
2. Keep PR **draft**, no merge, no test on private user data, no actual OAuth/PAT or live Lichess. No new product scope, no ML/GPT integration, no ROADMAP change.
3. After independent diff/CI review, owner reruns physical Windows GUI smoke: shortcut, 2 local games and sound, new move table, large board and review 3-panel view, one postgame + batch Stockfish no flashing CMD, analytic tabs and puzzle retry.
4. No claim that 475 PASS validates visual/audio quality. Final GO for merge only after *owner* confirms physical usability, engine safety intact.
