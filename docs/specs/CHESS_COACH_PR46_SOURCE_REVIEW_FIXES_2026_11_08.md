# CHESS COACH — PR #46: ОБЯЗАТЕЛЬНЫЕ ИСПРАВЛЕНИЯ ПЕРЕД ВИЗУАЛЬНОЙ ПРИЁМКОЙ

**Дата:** 08.11.2026  
**Статус:** **REQUEST CHANGES — НЕ ГОТОВО К ПОЛНОЙ UX-ПРИЁМКЕ**, но это **адресное исправление существующего Draft PR #46**, не новый большой рефакторинг.  
**Репозиторий:** \`feshakomissarov-prog/chess\`  
**Целевой Draft PR:** [#46](https://github.com/feshakomissarov-prog/chess/pull/46), рабочая ветка \`codex/unified-desktop-ux-2026-11-08\`, **проверенный кодовый HEAD \`1d2f929d94114ac1e2c8940fb45b7ac99ddf753b\`**. Перед исправлением сверить актуальный HEAD; если уже изменился — проверить, что замечания не закрыты последующими коммитами.  
**Основное утверждённое ТЗ:** [CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md), прежде всего §§03,07,09,11,18–20,26.  
**Это документ в отдельной UX-документационной ветке.** Не требуется менять или мерджить эту ветку. Исправления должны попасть в **существующий PR #46**, не в \`main\` и не в новый конкурирующий PR. Автор не изменял код #46 при ревью.

## 1. РЕЗУЛЬТАТ НЕЗАВИСИМОГО АУДИТА

### 1.1. Что подтверждено и не требует повторного переписывания

- PR #46 действительно меняет реальный код: пять основных sidebar routes, 4 карточки Analysis landing, вложенный Разбор с тремя tabs и встроенным архивом, Play mode cards, editor/FEN, Review no-data.
- GitHub Actions на **точном проверенном SHA \`1d2f929...\`**: Windows workflow \`42468115624\` завершён успешно; jobs **\`test\` и \`visual-evidence\` оба SUCCESS**. Отчёт исполнителя указывает 939 прошедших локальных тестов; эта цифра проверена **по отчёту**, мы не перезапускали pytest в своей среде.
- Отчёт [CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md](https://github.com/feshakomissarov-prog/chess/blob/codex/unified-desktop-ux-2026-11-08/docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md) **честно** сообщает, что 13 финальных side-by-side сравнений не сохранены; PR оставлен draft, пользовательские untracked файлы не затронуты. Это правильно.
- Для внешних PGN уже изменён \`color=None\`, что устраняет прежнюю грубую фиктивную атрибуцию белых владельцу. Existing Fair Play/generation mechanisms не следует заменять без доказанного дефекта.
- Finish во Focus уже делает \`focus_mode_button.setChecked(False)\` в \`_show_result\` — **эту часть повторно реализовывать НЕ НУЖНО**. Технически есть корректное состояние той же доски.
- Не тратить следующий цикл на косметическое совершенствование изображений или FUTURE (дебютный explorer, Weakness, Opponent, новый Learning). Следующий шаг — **точечное закрытие функциональных расхождений ниже**, затем визуальная приёмка пользователем.

### 1.2. Общий вердикт

**Нельзя пока говорить владельцу «всё окей, переходи к финальным визуальным тестам».** Есть как минимум **три функциональных NOW-пробела**: внешняя партия не анализируется автоматически локальным Stockfish; многоигровый PGN использует модальный диалог/не сохраняет двухэтапный выбор; async-result не привязан к активному вложенному инструменту. Ещё два подтверждённых нарушения договорённостей касаются Back/source и «Вызвать снова». Это не UI-вкусовщина — требования прямо зафиксированы в §§09,11,26.

**Критерий выхода:** поправить эти пять пунктов, обновить тесты, пройти Windows CI. Только после этого возобновлять полную визуальную приёмку всех 13 PNG. При этом **сохранить работающие улучшения PR #46** и его draft-статус до фактической визуальной приёмки.

---

## 2. КРИТИЧНЫЕ ПРАВКИ (P1) — ДО ВИЗУАЛЬНЫХ ТЕСТОВ

### CC46-P1-01. У внешних PGN/SAN/FEN/редактора НЕТ согласованного автоматического анализа Stockfish

**Контракт:** основное ТЗ §09.2–09.3, §11.4, §26.1 «Автоматический Stockfish», «PGN/SAN scope», «FEN/editor scope».

**Что найдено в текущем коде:**

- [\`src/chess_coach/window.py:2941–3010\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L2941-L3010): \`_external_study_parse_finished\` создаёт \`EphemeralReviewRepository\`, вызывает \`review_workspace.load_game(..., ephemeral=True)\` и сразу открывает Review. **Запуска движка после Open нет**.
- [\`src/chess_coach/ui/game_review.py:919–926\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/ui/game_review.py#L919-L926): даже ручная кнопка \`Проанализировать Stockfish\` **скрыта**, когда \`self._ephemeral_study=True\`. Поэтому в Review внешнего материала нельзя получить обещанный анализ этим маршрутом.
- [\`window.py:8541–8601\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L8541-L8601): существующий ручной путь ищет **сохранённую игру в personal repository**, а не ephemeral, поэтому просто показать кнопку и переиспользовать этот метод **недостаточно**.
- В текущем PR не добавлено отдельного пользовательского \`QSettings\`/UI-checkbox **«Автоматически анализировать открытые PGN и позиции»**. Существующий \`lichess/auto_ready_analytics\` — **другой продуктовый процесс**, подменять им настройку local external analysis запрещено.

**Что сделать:**

1. Добавить **ОДИН** чекбокс с точным понятным названием в существующий раздел «Stockfish и анализ» (SettingsDialog + QSettings); **default ON, если ключ ещё не установлен**, явный OFF сохраняется после restart. Не сбрасывать старые параметры SF/аудио/аккаунта.
2. После **осознанного** \`Открыть на доске\` (из textarea или выбора конкретной PGN из списка) / \`Открыть в разборе\` из редактора: **немедленно** открыть временный Review, а **после успешной parse/legality и свежей safety/admission проверки** поставить **один** local Stockfish job через существующий engine coordinator. Не запускать при paste, выборе файла, клике в tab, выборе строки списка, hover, Back или выборе сохранённой игры.
3. **PGN/SAN:** оценить **всю главную сыгранную линию**, не только последнюю позицию, не все variations; результат должен питать реальный график и корректно размеченные ошибки в том объёме, который инженерно поддерживает система. **FEN/editor без move history:** **одна текущая позиция**, никакого фиктивного графика целой партии или «моих» ошибок.
4. **Transient state:** не добавлять чужие PGN/FEN/notes/evals в personal SQLite/library, не выдавать \`external\` за account game, не менять данные оригинальной партии. Дать понятные состояния NO_ENGINE / BLOCKED_FAIR_PLAY / QUEUED / RUNNING / PARTIAL / READY / ERROR / CANCELLED; реальные \`X/Y\` только при известном denominator. Не запускать дубли engine jobs.
5. **Fair Play:** реальный active human session / неизвестное разрешение / смена auth/account / невалидный engine → **fail closed**, никаких eval/PV/graph/hints в GUI или accessibility text; \`--offline\` использовать только если уже явно обеспечена настоящая безопасная изоляция. Не создавать собственный «разрешатель» в обход текущего manager. 
6. Предусмотреть честную ручную повторную попытку в Review внешней партии при OFF-настройке и/или после снятия BLOCKED, если проверка безопасности позволяет; не делать кнопку, которая снова идёт в saved-game-only analysis и гарантированно не работает.

**Минимальные проверки:** настройка ON/OFF, persist, no engine on paste/tab/hover; 1 PGN mainline with N plies → один разрешённый job и соответствующее покрытие; FEN → одна позиция; mode OFF → Review без автозапуска, но рабочая явно выбранная manual CTA; без engine/без сети/active human → только честное blocked; смена tab/account во время worker → stale results ignored; внешний source не превращается в own game; повторный Open не оставляет два engine jobs.

**P1**, поскольку пользователь специально переутвердил **автоматический Stockfish** как принципиальную цель импорта и обзора, а сейчас его полностью нет.

### CC46-P1-02. Многоигровый PGN — неверный UX: \`QInputDialog\` и немедленное открытие

**Контракт:** основное ТЗ §§09.2,26.1,26.2 UX-10.

**Доказательство:** [\`window.py:2954–2971\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L2954-L2971) вызывает \`QInputDialog.getItem(..., "Партии в файле:", ...)\` **прямо во время обработки результата**, затем **сразу** открывает выбранную партию. В [\`ui/pages/analysis.py:_build_import_tab\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/ui/pages/analysis.py) встроенного chooser пока нет. Это противоречит договорённому \`in-tab selector → выделить → Открыть на доске\`. Для нескольких **вставленных** PGN используются те же неверные модальные окна и даже текст \`Партии в файле\`.

**Что сделать:**

1. Внутри **уже существующего** таба \`Анализ → Разбор партии → Вставить ходы\` создать **один** компактный scroll-chooser: «Найдено N партий. Выберите одну». Строка №, достоверные White/Black, Date, Result, ECO при наличии; при отсутствующих headers нейтральная подпись без придуманных имён.
2. Применять **один и тот же chooser** для (a) выбранного .pgn файла с 2+ играми и (b) 2+ PGN непосредственно вставленных в textarea. Никакого нового modal/top-level route/23-го PNG.
3. Выбор строки **только выделяет** candidate; **основная существующая кнопка «Открыть на доске»** подтверждает только selected game и только после этой команды может запускаться local SF по CC46-P1-01. Не автоселектить первую и не открывать её по клику на строку.
4. \`Отмена выбора файла\`/\`Назад к тексту\` сохраняет прежний текстовый draft (включая Paste), не создаёт ephemeral Review или engine job; повторная загрузка должна сбрасывать только **устаревший набор candidates**, не пользовательский черновик.
5. Для N=1 допустим привычный простой прямой Open, для FEN один однопозиционный путь, без бесполезного chooser. Ошибки parsе/limit остаются inline рядом с полем.

**Tests:** file 2 games, paste 2 games, empty/missing tags, cancel/return draft, repeated file generation, click row without Review, single Open without double worker, no QInputDialog call. UI-тест должен assert **встроенный список**, а не просто факт парсинга \`{"games":[...]}\`.

### CC46-P1-03. Асинхронный import callback не привязан к активной вложенной вкладке

**Контракт:** §09 сохранность вкладок/черновиков, §18 safety/state isolation, §26.3 «Parse-route guard».

**Доказательство:** [\`window.py:2941–2950\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L2941-L2950) проверяет generation и только \`screen_stack.currentWidget() is analysis_page\`. Но \`analysis_page\` — **один и тот же QWidget** для landing, tab «Мои партии», tab «Вставить ходы» и tab «Расставить позицию». Пользователь может быстро перейти в другой tab **без смены screen_stack**. Late parse после этого открывает Review независимо от текущего инструмента. \`_shell_screen_changed\` отменяет worker только при **уходе с целой analysis_page**, не при смене внутренних tabs.

**Что сделать:** хранить source (paste/file/editor), originating nested tab, generation, account/auth context и корректное cancelled state; при переключении таба/landing/другом Open инвалидировать или удержать только данные черновика **без автоматического открытия Review**; никакой stale late callback не должен ни переключать экран, ни запускать Stockfish, ни показывать ошибку на чужом экране. Status/error выводить в **правильном активном инструменте**. Привязать new chooser к тому же generation/owner logic, чтобы выбранная игра из старой загрузки не открывалась после новой.

**Tests:** async parse started on tab 1 → switch to Archive tab 0 → worker completion → **no Review/engine**; started in Editor tab 2 → switch to Paste tab 1 → no Review; switch to landing → no Review; A parse→B parse→late A callback ignored; switch account/auth generation→stale ignored.

---

## 3. ВАЖНЫЕ ПРАВКИ (P2) — ДО ЗАВЕРШЕНИЯ PR #46

### CC46-P2-04. Back из временного Review возвращает на landing, а не в инструмент-источник

**Доказательство:** [\`window.py:3012–3017\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L3012-L3017) вызывает \`show_analysis()\`, который принудительно \`analysis_page.show_landing()\`. Исчезает контекст \`Вставить ходы\` или \`Расставить позицию\`. Требование §09 — source-aware Back с сохранением tab/draft/selected game при возвращении.

**Исправить:** запоминать точный источник внешнего Review (tab индекс, текст, текущую selected candidate/редакторскую позицию) и при возврате восстанавливать **Анализ → Разбор партии → соответствующий tab**, без повторного чтения/запуска движка/сброса черновика. Отдельный Back из nested workspace должен вести на 4 landing cards. Не сломать обычный saved Review→archive/Home и возврат к live game. Автотесты для PGN и Editor.

### CC46-P2-05. Кнопка «Вызвать снова» использует старые modal-формы, а не единый Play

**Доказательство:** [\`window.py:5287–5302\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L5287-L5302) вызывает \`play_ai()\` или \`challenge_username()\`. Это прежние [\`OnlineGameDialog\` / \`QInputDialog\`](https://github.com/feshakomissarov-prog/chess/blob/1d2f929d94114ac1e2c8940fb45b7ac99ddf753b/src/chess_coach/window.py#L7010-L7051) и после подтверждения выполняют серверный POST прямо из модального flow. Контракт §§07.4,26.1: **переход в существующую форму Play с предзаполненными данными**, без другого второго основного UI-пути и без POST до явного Play CTA.

**Исправить:** AI → \`show_play()\` + selected \`ai\` + last valid confirmed color/time/level; human → \`show_play()\` + \`challenge\` + verified nickname + last valid options. Если данных не хватает — объяснить и дать исправить, не дублировать вызов. Клик «Вызвать снова» должен давать **0 сетевых POST**; только отдельный click form Start вызывает один POST. У локальной доски нет серверной кнопки rematch; есть локальная «Новая свободная доска» с подтверждением несохранённой линии. Не разрушать native rematch backend, если он существовал: можно оставить как внутреннюю возможность, но без другого модального пути в целевом UX.

**Tests:** AI → правильная выбранная карточка+prefilled fields, no post; human → nickname+controls, no post; click Start exactly one request; invalid nickname blocked; local no network CTA. Разрешённые authenticated/server ACK safeguards сохраняются.

### CC46-P2-06. Дополнить отчёт и доказательства перед финальным owner review

Существующий отчёт в PR #46 допускается **дополнить**, не переписывать с нуля. После исправлений:

- актуализировать SHA/codebase, число тестов и доказательства; отдельно отметить, что **прежние 939 PASSED относились к предыдущему HEAD**, а не доказывают исправления новых пяти пунктов;
- для каждого CC46-* зафиксировать \`DONE / PARTIAL / BLOCKED\`, file/line, user scenario, test names, screenshots, неизменность данных/Fair Play; при настоящем подтверждённом блокере описать **минимальный** fallback по master §26.5, а не отменять целевую функцию без доказательства;
- обновить Windows CI **на новом exact HEAD**; сохранить draft PR до завершения 13 side-by-side реальных Windows GUI кадров;
- существующие шесть screenshot-evidence были получены **до части финальных Play-изменений**. После исправления core P1/P2 и при отдельной пользовательской визуальной приёмке нужны **финальные**, а не устаревшие, изображения. Честное отсутствие живых сетевых ACK/Stockfish-проверок в отчёте сохранять, не заменять фиктивным PASS.

---

## 4. РАБОЧИЕ ГРАНИЦЫ И КРИТЕРИЙ ГОТОВНОСТИ К ВИЗУАЛЬНЫМ ТЕСТАМ

**НЕ ТРОГАТЬ БЕЗ ДЕЙСТВИТЕЛЬНОГО ДЕФЕКТА:** main, PR #44/#45, исходные approved UX-01…UX-13 PNG, существующие Play board handlers и серверную admission/429 логику, source-aware Lichess/Stockfish accuracy, работающие Library filters/notes/PGN/SQLite/Progress/Puzzles/Settings/audio/Background, цепочку сохранения finished game на той же доске. Не переписывать 19 файлов ещё раз ради «стиля».

**Повторно проверить P0:** вся аналитика Stockfish ограничена Fair Play во время human online, source ownership external = neutral, нет зависимости от real user DB для тестов, нет автосетевых POST, account/auth generation fencing и no forced SQLite import.

**Definition of Done — до сообщения владельцу «можно переходить к визуальным тестам»:**

- [ ] CC46-P1-01: внешний PGN/SAN/FEN/editor после явного Open имеет разрешённый локальный Stockfish job + правильную transient data model; settings default ON/OFF persistence; manual fallback; safety negative tests.
- [ ] CC46-P1-02: **встроенный** multi-PGN chooser и **вторая** явная Open CTA, одинаково для file/paste; больше нет \`QInputDialog\` для выбора партии.
- [ ] CC46-P1-03: inner-tab/source/generation/auth guard для всех поздних callback.
- [ ] CC46-P2-04: Back temporary Review восстанавливает исходный nested tool + draft, не landing.
- [ ] CC46-P2-05: AI/human «Вызвать снова» → уже существующий Play form с prefill и **0 POST** до явного Start.
- [ ] Все названные выше focused tests + полный test suite проходят на новом HEAD, \`compileall\`, \`git diff --check\`, Windows Actions \`test\`/\`visual-evidence\` **на точном новом кодовом SHA**.
- [ ] Обновлены implementation report и PR description с достоверными выполненными/непроверенными состояниями; PR остаётся draft, никаких merge/force push.
- [ ] Передан владельцу точный SHA и краткое «исправлено, можно переходить к 13 визуальным тестам», **без ложной декларации**, что сами side-by-side уже выполнены, если они ещё не выполнены.

**Если истинный технический блокер затрагивает только одну из P1 работ**, Codex обязан продолжить прочие пункты, но не выдавать работу как полностью соответствующую master NOW. Каждый fallback по §26.5 должен содержать конкретную reproduction/доказательство, scope, последствия и тест, а не мотив «это проще».

**Итог текущего аудита:** базовый код/CI заслуживают сохранения; **REQUEST CHANGES**, а не отказ от PR #46. Исправить точечные NOW gaps, после чего **отдельно** проводить визуальную приёмку 13 изображений вместе с владельцем.
