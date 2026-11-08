# Chess Coach · PR #46 — Второе независимое ревью кода: два оставшихся адресных исправления

**Дата:** 08.11.2026  
**Вердикт:** **REQUEST CHANGES: 2 конкретных NOW-расхождения.** Все прежние большие исправления P1/P2 повторно не переделывать. После двух правок и обновления CI можно переходить к пользовательской визуальной приёмке UX-01…UX-13.  
**Проверенный SHA PR #46:** \`4fba2126bef1f453fc495f542605bf2ed52579c4\`.  
**Ветка для исправления:** \`codex/unified-desktop-ux-2026-11-08\`, **тот же Draft [PR #46](https://github.com/feshakomissarov-prog/chess/pull/46)**. Не создавать PR #47, не менять main, не делать merge. Перед работой проверить текущий HEAD.

**Нормативные источники:**

- [Полное согласованное мастер-ТЗ](CHESS_COACH_UNIFIED_UX_ANALYST_HANDOFF_2026_11_08.md), особенно §§07,09,18,20,26.
- [Первое ТЗ по ревью PR #46](CHESS_COACH_PR46_SOURCE_REVIEW_FIXES_2026_11_08.md), прежде всего CC46-P1-03 и CC46-P2-05.
- [Реализация PR #46](https://github.com/feshakomissarov-prog/chess/pull/46) и [отчёт](https://github.com/feshakomissarov-prog/chess/blob/codex/unified-desktop-ux-2026-11-08/docs/ux/CHESS_COACH_CODEX_IMPLEMENTATION_REPORT.md).

## 1. Подтверждённый результат повторной проверки

1. **CI подтверждён средствами GitHub**, а не только заявлением Codex: workflow run [42516568691](https://github.com/feshakomissarov-prog/chess/actions/runs/42516568691) на **точном HEAD \`4fba212...\`** имеет оба successful jobs \`test\` и \`visual-evidence\`. \`939 → 959\` локальных тестов — заявлено и отражено в отчёте; собственный pytest этот ревьюер не запускал.
2. **CC46-P1-01 / внешний Stockfish:** кодовая цепочка присутствует — setting \`analysis/auto_external_studies\` default ON / OFF persists, transient \`EphemeralReviewRepository\`, \`_start_external_study_analysis\`, existing engine/admission, обновление Review. Поверочные тесты с mock-анализатором есть. **Реальный бинарник Stockfish и живой Lichess не проверялись**: это отдельная эмпирическая приёмка, не доказанный дефект кода и не основание заново переписывать движок.
3. **CC46-P1-02 / multi-PGN:** \`AnalysisLandingPage.set_candidates()\` теперь встроенный selector и требует второй явной \`«Открыть на доске»\`; modal chooser более не используется, file/paste сходятся.
4. **CC46-P1-03 / табы:** появилась привязка parse по \`generation/source/tab/account/auth\`; переходы между соседними nested tabs охвачены, но **корневой переход на Analysis landing остаётся вне guard** — см. R2-01 ниже.
5. **CC46-P2-04 / Back:** внешнее Review возвращает в конкретный nested tab, восстанавливая draft/выбор; код и тесты присутствуют.
6. **CC46-P2-05 / rematch:** переход в общий Play с no-POST-until-Start реализован, но **не все достоверные параметры прошлого вызова переносятся** — см. R2-02 ниже.
7. Новые тесты не заменяют финальную сверку **13 эталонов**: все side-by-side по-прежнему должны проводиться владельцем и Codex позднее; текущий Draft статус PR корректен.

**Результат:** архитектуру и всю текущую реализацию не переписывать; починить **только R2-01 и R2-02**. Они не связаны с отсутствием экранных снимков и воспроизводимы на уровне Qt/source.

---

## 2. R2-01 [P1] Late parse всё ещё открывает чужой Review после возврата на Analysis landing

**Утверждённое поведение:** при уходе из \`Анализ → Разбор партии → Вставить ходы\` назад на **первый экран «Анализ» (четыре карточки)** выполнявшийся фоновый разбор PGN/FEN не имеет права автоматически открыть Review или запустить Stockfish. Это тот же запрещённый stale-result, что при смене вложенной вкладки. Черновик можно оставить локально для возвращения, но callback не должен красть навигацию.

**Подтверждение в HEAD \`4fba212...\`:**

- [\`window.py#L2996-L3017\`](https://github.com/feshakomissarov-prog/chess/blob/4fba2126bef1f453fc495f542605bf2ed52579c4/src/chess_coach/window.py#L2996-L3017): \`_external_study_parse_finished\` проверяет \`screen_stack.currentWidget() is analysis_page\` и \`tabs.currentIndex()==owner[2]\`, **но не проверяет \`analysis_page.workspace.currentWidget() is analysis_page.breakdown_page\`**.
- [\`analysis.py#L265-L267\`](https://github.com/feshakomissarov-prog/chess/blob/4fba2126bef1f453fc495f542605bf2ed52579c4/src/chess_coach/ui/pages/analysis.py#L265-L267): \`show_landing()\` меняет только **внутренний** \`QStackedWidget\`, не вызывает \`tabs.currentChanged\`; old \`tabs.currentIndex\` остаётся равным 1.
- [\`window.py#L3019-L3024\`](https://github.com/feshakomissarov-prog/chess/blob/4fba2126bef1f453fc495f542605bf2ed52579c4/src/chess_coach/window.py#L3019-L3024): \`_external_study_tool_changed\` отменяет worker при **смене nested tab**, но **не при выходе на корневой landing**. Вызов \`show_analysis()\` внутри существующей \`analysis_page\` также не гарантирует \`screen_stack.currentChanged\`.

**Минимальное исправление:**

- В одном месте ввести/использовать **точный guard source route = analysis_page + breakdown_page + origin tab + generation + account/auth**.
- Инвалидировать/отменять ожидающий parse при **\`show_landing()\`**, \`show_analysis()\` из nested, возврате/уходе на любую другую страницу, изменении nested tab; **не** удалять текстовый черновик без запроса.
- Учесть гонку: если worker уже завершился и Qt completion queued, изменение route **всё равно** делает owner/generation невалидными. Нельзя зависеть только от \`handle.is_alive()\`.
- Для нескольких PGN поздний callback не показывает ни candidate-list на чужом landing, ни Review, ни engines. При возвращении в инструмент результат можно сознательно загрузить заново через явное Open.

**Обязательные Qt regression tests:**

1. Начать медленный parse в tab 1, нажать breadcrumb \`‹ Анализ\`, дождаться completion → на экране **landing 4 cards**, \`review_workspace.review\` не изменился, \`engine_analysis\` не вызывался.
2. После возврата на landing быстро **снова открыть тот же tab 1**, а затем дождаться completion старого parse → всё равно нет Review/candidate от старой generation (даже если queued signal).
3. Та же проверка при \`window.show_analysis()\` пока уже находится на вложенном \`AnalysisLandingPage\`.
4. Сохранность draft и существующих тестов tab switch/auth change/нескольких PGN.

Это **недоведённый CC46-P1-03**, а не новая функция.

---

## 3. R2-02 [P2] Повторный вызов теряет подтверждённые параметры времени/цвета/рейтинга

**Утверждённое поведение:** после финиша клик \`«Вызвать снова»\` в AI/human **только открывает общий Play** с предзаполненными **достоверными** параметрами завершённой партии — противник, color, time limit, increment, rated (если поддержано режимом), AI level. Новый POST строго после отдельной \`Start/Отправить вызов\`. Если конкретное поле не подтверждено, не выдумывать значение: воспользоваться прежним сохранённым предпочтением и/или честно дать отредактировать.

**Подтверждение:**

- [\`window.py#L5383-L5445\`](https://github.com/feshakomissarov-prog/chess/blob/4fba2126bef1f453fc495f542605bf2ed52579c4/src/chess_coach/window.py#L5383-L5445): AI ветка вызывает \`_prefill_play_clock\`, но человеческая ветка устанавливает **только** \`select_mode("challenge")\` и \`username\` — \`session.color\`, \`session.clock_initial\`, \`session.white_inc\`, \`session.rated\` не переносятся; остаются несвязанные старые предпочтения пользователя.
- В той же функции \`_prefill_play_clock\` при любом **кастомном** подтверждённом времени \`7+3\`, \`11+5\` и т. п. ищет такой пункт в preset combo. Если его нет, **заменяет его \`3+2\`** (или другим fallback) вместо \`«Свой…»\` и правильных значений. Такое поведение было явно запрещено принципом «предзаполнить прежними достоверными параметрами».
- Модель [\`lichess/session.py\`](https://github.com/feshakomissarov-prog/chess/blob/4fba2126bef1f453fc495f542605bf2ed52579c4/src/chess_coach/lichess/session.py) уже содержит \`clock_initial\`/инкремент **в миллисекундах**, \`rated\`, \`color\`, \`opponent_info\`, \`game_source\` — не надо добавлять второй источник данных или делать новый запрос Lichess.

**Минимальное исправление:**

- Общая функция prefill для AI и human получает подтверждённые параметры завершённой \`LichessGameSession\`; для human дополнительно переносит last confirmed **color, time, increment, rated** и verified username, для AI — level и время.
- Для нестандартного \`(limit, increment)\` вместо ложного preset выбрать **\`«Свой…»\`** и выставить \`custom_minutes\`, \`custom_increment\` (с учётом поддерживаемых диапазонов). Если серверные значения реально не представимы этой формой, честно показать текущее подтверждённое значение и предложить ручное исправление; **не подставлять молча 3+2**. Time units: миллисекунды→секунды с валидацией.
- Для неизвестного/ненадёжного \`clock_initial\`, \`white_inc\`, \`rated\`, \`opponent_info\` использовать явно существующий saved preference или понятное отсутствие prefill, но не представлять preference как факт прошлой партии.
- **No POST** по клику rematch и **ровно один** POST после явного Start (существующие тесты должны остаться зелёными). Никаких новых модальных экранов; кнопка \`Новая партия\` для local уже имеет подтверждение потери линии, её не трогать.

**Регрессионные тесты:**

1. Завершённая human game с подтверждёнными \`7+3\`, black, rated=True, username: \`Вызвать снова\` → \`challenge\` selected, username, custom 7 min / 3 sec, black, rated checked, **0 POST**, затем одна явная Start → **1 правильный request**.
2. Завершённая AI game с кастомным \`11+5\`, подтверждёнными color/level: после rematch **именно 11+5**, не 3+2, 0 POST до Start.
3. Preset \`5+0\` остаётся preset; unknown/invalid time не превращается в выдуманный confirmed time. 
4. Нет регрессий disabled offline/server retry/429, нет скрытой повторной отправки.

---

## 4. Что не считать новым багом и что НЕ делать в этом цикле

- **Не переписывать** уже работающие inline multi-PGN, source-aware Back, ephemeral repository, справедливую блокировку Stockfish или Play cards.
- **Не объявлять провалом** существующее отсутствие реального Stockfish/живого Lichess и 13 эталонных сопоставлений на данном **source-review** этапе. Это следующий отдельный GUI/real-engine acceptance; если среда не позволяет — честно отразить в отчёте.
- **Не расширять** FUTURE Opening Lab, Weakness, Opponent, обучение и новые страницы. Не менять утверждённые 13 PNG, SQLite migration, PR #44/#45, main.
- Не пытаться обеспечить всё «на глаз» путём ослабления тестов или отключения безопасности.
- Не делать самостоятельный merge; оставить PR draft до визуальной приёмки.
- Дополнительных оформительских замечаний к отчёту нет; локальные числа \`939\`/\`957\` в исторической части старого отчёта относятся к старым SHA, пусть остаются как исторические, но **в итоговой верхней сводке должен быть финальный фактический тестовый результат нового HEAD**.

## 5. Критерий завершения

- [ ] R2-01 и R2-02 закрыты маленькими адресными изменениями на **той же ветке PR #46**.
- [ ] Есть focused Qt regression tests на landing-late-parse и custom AI/human rematch.
- [ ] Полный pytest, compileall, git diff --check и Windows Actions \`test\` + \`visual-evidence\` **успешны на новом точном кодовом SHA**.
- [ ] Implementation report и draft PR description указывают новые фактические тесты, SHA, оставшийся отдельный визуальный QA 13/13 и отсутствие real-engine/native smoke, если их ещё не было.
- [ ] Владелец получает компактное сообщение «две правки выполнены, тесты/CI прошли, можно переходить к сравнению UX-01…UX-13». **НЕ утверждать** что сам визуальный прием уже завершён.

**Объём исполнения намеренно ограничен:** только два воспроизводимых нарушения контракта. По остальным пяти замечаниям предыдущего ревью текущая реализация на уровне кода уже достаточно близка для перехода к визуальной фазе после этих фиксов.
