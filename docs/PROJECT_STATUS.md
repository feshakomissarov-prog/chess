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
