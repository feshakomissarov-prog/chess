# STAB-01 — журнал выполнения фазы A и анализ DIST-01

**Рабочая ветка:** `codex/stab-01-stabilization-material`
**База:** `origin/main` на SHA `d11a60e` (проверена до начала изменений).
**Состояние:** [Draft PR #47](https://github.com/feshakomissarov-prog/chess/pull/47); `OWNER_ACCEPTANCE_REQUIRED`; merge/release не выполнялись.
**Граница данных:** личная SQLite, реальные настройки/токены и аккаунт владельца не открывались. Все запуски smoke создали отдельные synthetic DB/settings под `%TEMP%`.

## Что изменено

- Home cache теперь валидирует каждый scope в основном и `.previous` документах, сохраняет поля даты/точности последних партий, принимает предыдущую схему после additive migration и загружает last-good независимо от SQLite revision. Предварительное чтение выполняется worker-ом до показа Home; ошибки aggregate не очищают показанные числа.
- SQLite schema v26 добавляет профильную dashboard revision и триггеры для зависимых данных; открытия текущей базы не пересоздают эти триггеры. Ревизия другого аккаунта не инвалидирует Home текущего аккаунта.
- CTA запуска и продолжения партии вынесены в нижнюю фиксированную панель Play. Страница ограничивает ширину контента фактическим viewport и перестраивает карточки по ширине страницы. При законной сетевой блокировке рядом с CTA показывается причина; readiness trace не содержит токенов.
- Состояние навигации различает явный выбор пользователя и адаптивное сворачивание; на широком экране меню с подписями раскрывается после auto-collapse.
- Фоновая страница получила внутреннюю прокрутку на низкой высоте, чтобы строки процессов не накладывались.
- MAT-01 считает разницу фигур по подтвержденной позиции и перспективе игрока, не читает оценку движка. В восстановлении authoritative позиции показывает `н/д` до подтверждения.
- Повторная установка Stockfish создаёт уникальный backup name и после проверенной замены оставляет только одну резервную копию; ошибка замены сохраняет предыдущий рабочий каталог.

## TD-01…TD-09 и MAT-01 — RED → FIX → GREEN

Статус GREEN ниже относится к указанному автоматическому или synthetic/native доказательству. Это не заменяет пользовательскую приемку на личном профиле.

| ID | Исходный дефект / RED evidence | Исправление | GREEN evidence | Остаток |
|---|---|---|---|---|
| TD-01 | Tier A сериализовал недавние партии без `last_move_at`/`game_time_precision`; восстановленная карточка теряла известную дату. | Cache сохраняет эти поля и валидирует целочисленный timestamp и точность даты. | `tests/test_dashboard_cache.py`: timestamp/date-only round-trip и совместимость кэша; Home restart smoke содержит три карточки с датами. | На пользовательской базе не проверено.
| TD-02 | Ошибка фонового aggregate заменяла last-good числа на `н/д`. | Ошибка обновления оставляет last-good значения/источники; сообщение об ошибке находится рядом со сводкой. | `tests/test_home_dashboard_last_good.py`: error callback с Tier B и без Tier B; Windows full pytest exact-head CI PASS. | Реальный `SQLITE_BUSY` на личной базе не моделировался.
| TD-03 | Валидный JSON envelope с повреждённым Tier останавливал поиск и игнорировал здоровый `.previous`. | Каждый документ и scope проверяется целиком; следующий кандидат рассматривается при структурной ошибке. | `tests/test_dashboard_cache.py`: invalid primary/valid previous, повреждённые данные и scope isolation. | Read-only disk/оба одновременно повреждённых файла проверены fault case, но Windows ACL личного профиля не проверялся.
| TD-04 | Глобальная revision инвалидировала Tier B для несвязанного аккаунта. | Добавлена `dashboard_profile_revision(account_id)` с зависимостями, областью по аккаунту и защитой от no-op UPDATE. | `tests/test_pr46_post_audit_regressions.py`: изменение B не меняет revision A; `tests/test_m45_analysis_store.py`: реальные source changes сохраняют триггеры. | Требуется наблюдение на активном пользовательском синке.
| TD-05 | Переустановка Stockfish оставляла неограниченные `.previous-*`; timestamp мог совпасть. | Уникальные backup names и bounded cleanup после UCI-проверенной замены. | `tests/test_engine_manager.py`: повторная установка, конфликтные имена, failure/rollback. Тесты используют fake transport и временные каталоги. | Реальную загрузку Stockfish и реальный бинарник не запускал.
| TD-06 | Начальный Home показывал данные после тяжёлого чтения/aggregate; отсутствие сохранённого полного snapshot растягивало warm start. | Async preload last-good до отображения, немедленный paint полного совместимого snapshot, background refresh без очистки чисел. | В реальном Windows Qt с отдельными synthetic SQLite и процессами: warm restart 3319 → **116.03 ms**; после G4 3320 → **170.39 ms**. В каждом случае widgets содержали count, sourced accuracy `68.0%` (Lichess), rating `1492` и целевую тройку recent cards. Снимок в PR: [Home 3320](evidence/stab01/home-3320-cached-launch.png). | **НЕ ACCEPTED:** личная DB, reboot Windows, повторные пользовательские cold starts, account A→B→A и сеть владельца Codex недоступны; Codex не открывал их. Один synthetic first calculation без saved Tier B занял **1092.06 ms** и относится к документированному first-ever calculated-cache cold path. Это не измерение подходящего уже-cached сценария.
| TD-07 | При активной партии карточки вытесняли основную кнопку в конец прокручиваемого Play content. | Нижняя action bar вне scroll; active games раскрываются только при наличии активных игр; content подстраивается по ширине. | `tests/test_corr_gate_regressions.py`: 0/1/3 active, 1280×720/1366×768/1920×1080, геометрия и QTest click. Native Play smoke прошёл при physical 1280×720 на 125% и 150%; 100% final visual — отдельная запись ниже. | На пользовательском Lichess UI требуется ручная проверка готового соединения/активной партии.
| TD-08 | Восстановление узкого меню могло считать responsive auto-collapse намеренным. | Startup отделяет explicit preference от auto state; широкий экран раскрывает подписи, явное сворачивание сохраняется. | Два изолированных `QSettings` профиля в `tests/test_corr_gate_regressions.py`; native visual smoke включает оба режима sidebar. | После реального reboot восстановление QSettings владельца ожидает ручную проверку.
| TD-09 | Непонятно, скрыта кнопка, отключена safety gate или ожидается запрос. | Кнопка остается на экране; соседний статус объясняет not-ready/pending. Переход readiness логируется безопасной структурой `mode/visible/enabled/geometry/safe_state/request_generation`, без credentials. | `tests/test_corr_gate_regressions.py`: offline reason, ready/local clicks и single action; проверка 0 network POST при disabled. `tests/test_home_dashboard_last_good.py`/window regressions проверяют single pending request. | Ready Lichess event-stream acceptance без реального API потребует owner smoke; реальные 429 остаются fake transport tests.
| MAT-01 | Требовался честный баланс материала во время игры без Stockfish и без speculative state. | Pawn=1, N/B=3, R=5, Q=9, K=0; показатель ориентирован для игрока; считает только authoritative board и replay snapshot. | `tests/test_material_balance.py`: начальная 0, захват пешки +1, потеря слона −2, стороны, en passant, promotion/underpromotion, invalid/Chess960. Qt test видит ноль до server confirmation и −1 после; EngineManager не получает вызовов. | Ручной live game/late correction владельцем не проверены; советов, eval/PV и движка код не запускает.

## HOME timing trace (synthetic Windows, фактические Qt labels)

`T0` в smoke harness фиксируется непосредственно перед `window.show()`; таким образом интервал включает показ окна и является немного более строгим, чем отсчёт от первого paint. `current_summary_ms` принимается только если видимые QLabel одновременно показывают целевой count, исходные accuracy/rating и порядок recent `game_id`.

| Run | Между запусками | Видимые значения / источник | current_summary_ms | Cache / примечание |
|---|---|---|---:|---|
| clean profile cold calculation | Synthetic DB 3319; до этого Tier B отсутствовал | 3319 / 68.0% Lichess / 1492 / три recent cards | 1092.06 ms | Первичный расчёт, cache сохраняется в фоне; first-ever cache исключение, не warm pass.
| second process | Без изменений, новый Python-процесс | 3319 / 68.0% Lichess / 1492 / те же 3 cards с датами | 116.03 ms | Cache hit, 0 full aggregate calls; QSettings и DB раздельные от пользователя.
| after G4 | Добавлена synthetic завершённая партия `seed-new`, новый процесс | 3320 / last-good 68.0% Lichess / 1492 / новый `seed-new` + следующие две cards | 170.39 ms | Новая Tier A показана до aggregate; один aggregate завершился в 1110.97 ms и не блокировал Home.
| next unchanged restart | Уже записана сводка после G4, новый Windows Qt/Python процесс | 3320 / 68.0% Lichess / 1492 / `seed-new`, `seed-0`, `seed-old-03315` | **128.18 ms** | Screenshot: [Home 3320](evidence/stab01/home-3320-cached-launch.png). 0 aggregate calls; last-good cache hit.

Все эти числа созданы в тестовом профиле. `3321`, `82.8%`, `1917` владельца не использовались и не подставлялись в тесты. Не запускалось: реальный профиль владельца, холодный запуск Windows после reboot, серия 2–3 владельческих warm launches. Поэтому жёсткий пользовательский P0 критерий **остается `OWNER_ACCEPTANCE_REQUIRED`, не PASS**.

## Нативная Windows-проверка

- Полный synthetic visual harness использует production `MainWindow`, SQLite repository и рабочие Qt widgets, но fake identity, offline transport, временную базу/настройки и отсутствие Stockfish. Используются actual window geometry, screenshots, QWidget bounds и QTest clicks.
- На 100% DPI запрошены 1280×720/1366×768/1920×1080 logical; Windows ограничивает top-level высоту 1920×1080 до доступной work-area, что harness отражает по фактическому размеру окна. Проверки clickable geometry используют реальный доступный rect. **33 strict synthetic states PASS**, включая Home, Play, активную игру, оба sidebar состояния, settings и analysis. Скриншоты: [expanded](evidence/stab01/sidebar-expanded-100.png), [explicit collapsed](evidence/stab01/sidebar-collapsed-100.png).
- Для physical 1280×720, 1366×768, 1920×1080 при 125%/150% запуск использует соответствующие Qt logical bounds `1024×576`, `1093×614`, `1536×864` и `853×480`, `911×512`, `1280×720`. При Qt raster rounding допускается ≤1 физический пиксель; clipping/click bounds остаются строгими.
- При 150% узкие physical 1280×720/1366×768 bounds прошли Home/Play/Background, CTA и active-open checks. Дальнейший all-pages visual sweep остановился на существующем clipping `PuzzleHistoryPage.solution` (не TD-01…09/MAT-01; за пределами затронутой страницы). [Active Play 150%](evidence/stab01/play-active-1280x720-150.png).
- При 125% Home/Play/Background на всех трёх logical bounds прошли, в том числе [active Play 1280×720 physical](evidence/stab01/play-active-1280x720-125.png). Общий visual sweep остановился позднее на `clear_board_annotations` внутри Active Game screen в 1280×720 physical. Остальные responsive строки этот общий sweep не прошёл, поэтому отчет ограничивает вывод целевыми Home/Play сценариями.
- Артефакты добавляются в `docs/handoffs/evidence/stab01/` только из synthetic DB.
- Exact-head GitHub Windows `visual-evidence` первоначально выявил, что при 1672×941 три 140 px recent карточки имели `scroll_max=13`. Исправлены только интервалы внутри существующей карточки: межсекционный 20→16 px, между recent rows 12→6 px; размеры карточек, шрифты, цвета и состав не менялись. Повторный native Windows `scripts/capture_visual_master.py` подтвердил `scroll_max=0` на 1672×941 и завершился **33 strict synthetic states PASS**. Exact-head CI на кодовом SHA `ac2cfd56fa9f07da933d22f249d3680da19adc22`, run `42980712161`: Windows `visual-evidence` PASS и полный Windows test job PASS (pytest, compile, isolated offline window).

## Автоматические проверки

- `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m pytest -q` — финальный запуск достиг `[100%]`, ошибок/traceback в завершившемся выводе нет. В финальном прогоне исправлен cache-regression, который привязывал retained accuracy к индексу вместо `game_id`.
- Один последующий exact-head Windows CI прогон дал `1140 passed, 1 failed`: у регрессионного теста preview worker проверял успешный `close()` пока отдельный startup aggregate ещё выполнялся. Тест уточнён — перед проверкой закрытия ждёт завершения независимого aggregate; целевой тест на Windows/Python 3.12 локально прошёл три последовательных запуска. Полный CI после этого изменения ожидается на текущем HEAD.
- `QT_QPA_PLATFORM=offscreen .venv312/Scripts/python.exe -m compileall -q src scripts` — выполнено после правок, синтаксических ошибок нет.
- `git diff --check` — выполнено после правок, whitespace ошибок нет. Git выдал только предупреждение об автоматической смене LF→CRLF для перечисленных изменённых файлов.
- Native Windows run `scripts/capture_visual_master.py` при 100% — 33 synthetic состояния PASS.
- Native Windows run `scripts/capture_visual_master.py` при 125% и 150% — целевые Home/Play/Background сценарии и реальные QWidget границы до последующего тестового экрана проходят. Полный all-pages visual run заканчивается assertion на другой, незатронутой странице; подробности приведены выше, поэтому это не оформлено общим PASS.
- `scripts/home_dashboard_smoke.py` — отдельные native Windows процессы, фактические Qt widgets; завершённые состояния записаны в `window-state.json` и screenshot Home включён выше.
- Реальный Lichess, OAuth/PAT, Stockfish download/UCI binary, личная база и restart/reboot владельца — **НЕ ЗАПУСКАЛИСЬ**.
- Screenshots для локальной проверки пока лежат в `%TEMP%\chess-coach-stab01-*`; это не артефакты CI и не личные данные. Переносить будут только нужные synthetic кадры.

## DIST-01 — feasibility / лицензии / поддержка (не реализовывать в фазе A)

**Предлагаемая сборочная форма:** Python 3.12.x pinned patch + полностью зафиксированные версии всех direct/transitive wheels и hashes; PyInstaller `onedir` x64; один Inno Setup per-user `ChessCoach-Setup.exe`; pinned Stockfish 19 Windows x64 binary + exact upstream source/hash/notices. Python, Qt, приложение и движок лежат внутри поставки; pip/интернет во время setup/startup не нужны. Auto-updater, service, release и installer не создавались.

**Сложность:** средняя/высокая, ориентир для отдельного согласованного этапа: 2–4 инженерные недели до стабильного signed Setup, без учёта юридического review, ожидания сертификата и поддержки пользователей. Основные трудоёмкие места — Qt plugin/native DLL discovery, python-chess data files, keyring backends, Stockfish AV false positives, clean VM / DPI matrix, сохранность AppData при upgrade/uninstall, rollback DB schema и подписанный pipeline. Размер и cold/warm start frozen build сейчас не измерены; до этого нельзя обещать объём или время старта.

**Лицензионные решения:**

- `python-chess` обозначен upstream как GPL-3.0; бинарное распространение требует корректного выполнения GPL obligations по source/notices и совместимости лицензии приложения. Это главный открытый вопрос, который нельзя решить выбором packager.
- PySide6/Qt for Python доступен под LGPLv3/GPLv3 или коммерческими условиями. При LGPL придётся соблюдать условия динамической замены библиотеки/notices/relinking и собрать точный список реально используемых Qt modules; если берутся GPL-only модули, анализ меняется. Нужен разбор всей фактической комплектации.
- Stockfish — GPL-3.0. Если фиксированный движок поставляется вместе с приложением, приложить соответствующую версию исходников/изменений и уведомления.
- PyInstaller имеет GPL license с Bootloader Exception; exception разрешает встраивать bootloader в комбинацию, но не отменяет лицензии упакованных зависимостей.
- Inno Setup 7 просит коммерческих пользователей приобрести лицензию. Нужна проверка применимых условий использования проекта; если это коммерческое распространение — заложить стоимость.
- Остаются отдельные аудиты `chess-widgets`/Cburnett SVG, Python runtime, Shiboken/Qt bundled third parties, keyring/platform dependencies, sounds/icons, MSVC runtime и иных фактически замороженных модулей. Полная SBOM на текущем lock еще отсутствует.

**Поддержка/безопасность:** без подписи Microsoft SmartScreen может показывать предупреждение; не обещать обратное. Перед авто-установкой нужны signing key custody, доверенный manifest/signature, SHA-256, защита от downgrade/redirect/zip-slip, offline/error policy и тест restore. Без этого безопасный первый релиз — вручную скачиваемый подписанный (если сертификат куплен) Setup с открытием официальной Release страницы, без скачивания/исполнения installer приложением. GitHub Actions Windows runner и Inno Setup способны собрать это, но время сборки, размер и cold start пока `НЕ ЗАПУСКАЛОСЬ`.

Проверенные первичные сведения: [Qt for Python license overview](https://doc.qt.io/qtforpython-6/), [Qt LGPL/GPL obligations](https://www.qt.io/development/open-source-lgpl-obligations), [python-chess upstream license](https://github.com/niklasf/python-chess), [Stockfish upstream](https://github.com/official-stockfish/Stockfish), [PyInstaller license and bootloader exception](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt), [Inno Setup commercial terms](https://jrsoftware.org/isinfo.php). Это технический feasibility вывод, не юридическое заключение.

## Итоговая приемка / открытые действия владельца

1. После публикации draft PR проверить exact-head Windows CI, полный `pytest`, `git diff --check` и приложенные native traces.
2. На личной Windows DB выполнить холодный старт после перезагрузки, два обычных повтора и launch после новой партии; записать каждый отдельный T0/T1 и видимые `count/accuracy/source/rating/3 cards`. Любой подходящий запуск >1000 ms означает **FAIL**.
3. Проверить реальные Play сценарии: offline reason, готовый Lichess stream (ровно один старт), активная AI партия (видны Continue и основной CTA), затем 0/1/3 active cases при установленных DPI/разрешении.
4. Проверить sidebar после перезапуска: default wide expanded; explicit collapse сохраняется; responsive resize и route changes не портят preference.
5. MAT-01 проверить в local board и завершить одну разрешённую онлайн-партию против человека: цифры меняются только по подтвержденной позиции, без eval/PV/hints.
6. Оставить PR draft до этой проверки. Лишь после владельческого результата можно обновить статус TD registry и считать фазу A принятой. DIST-01 ждёт отдельного решения владельца.
