# Chess Coach · PLAY-UX-02 — реестр функций вкладки «Играть»

**Дата:** 07.11.2026. **Статус:** дизайнерский контроль, НЕ реализовано, НЕ передано Codex.  
**Контроль исходника:** code PR #44 SHA 5c375b070e6644dbded5fca62d960fc09c90651e; перечитать новый SHA перед исполнением. Сопутствующее подробное ТЗ: ../specs/PLAY_UI_REWORK_CODEX_SPEC_2026_11_07.md.

**Назначение:** проверять, что после изменения видимых кнопок ни одна функция не потеряна. EXISTS = есть в исходнике, а НЕ пройдена настоящая Windows проверка. Все пункты сейчас **NOT TESTED** для нового UX.

## A. Кнопки и параметры (PL-01…PL-25)

| ID | Существующий элемент / обработчик | Что должно стать | Решение |
| --- | --- | --- | --- |
| PL-01 | PlayPage.mode_selector и пять _MODES cards, cards скрыты | 4 видимых mode tiles в 2×2, 1 выбран; старый combo не виден | REDESIGN / HIDE duplicate |
| PL-02 | color combo: random/white/black | 3 переключателя, запоминать per mode | REDESIGN |
| PL-03 | clock combo: unlimited, 3+2, 5+0, 10+0, 15+10, custom | компактные time chips | REDESIGN |
| PL-04 | spinboxes custom_minutes 1–180, increment 0–180 | раскрывать только после «Свой…», per mode | KEEP / RELOCATE |
| PL-05 | AI level combo 1–8 | 8 компактных кнопок, только AI | REDESIGN |
| PL-06 | rated checkbox | «Обычная / Рейтинговая», только friend/challenge | REDESIGN |
| PL-07 | username field / legacy QInputDialog | единый username input в challenge panel | KEEP / REMOVE DUPLICATE |
| PL-08 | start_button + create_ai_game | AI CTA «Играть против Lichess AI» | KEEP BACKEND |
| PL-09 | create friend open challenge | «Создать приглашение» только friend panel | KEEP BACKEND |
| PL-10 | invitation_link + copy_invitation_button | после подтверждённого URL копировать + статус | KEEP / CONDITIONAL |
| PL-11 | cancel_pending_button / cancel_invitation_button | одно правильное действие отмены в friend state; guard по ID | CONSOLIDATE PRESENTATION |
| PL-12 | challenge_username | CTA «Отправить вызов» с ником, не QInputDialog | KEEP BACKEND |
| PL-13 | cancel_username_challenge | CTA «Отменить вызов» только пока есть корректный pending | KEEP |
| PL-14 | start_local_game | «Открыть свободную доску» без сетевых полей | KEEP |
| PL-15 | open_active_game, resume_current_game | «Продолжить» в отдельной active strip | MOVE |
| PL-16 | active_panel и пятая карточка режима «active» | active strip N>0 и «Все активные N» для нескольких | REDESIGN / HIDE fifth tile |
| PL-17 | form_status / challenge statuses | inline loading/error/cancel/accepted/429/unknown | KEEP / REDESIGN |
| PL-18 | shell Back/Фон/sidebar/account/auth | не менять глобальную оболочку и навигацию | KEEP AS-IS |
| PL-19 | QSettings workspace/ai_* частично, friend/challenge state отсутствует | selected mode + per-mode parameters и migration | ADD |
| PL-20 | legacy OnlineGameDialog, QInputDialog и Home shortcuts | одно безопасное создание, без новых модальных дубликатов | UNIFY ENTRYPOINT |
| PL-21 | глобальный Return to game | сохранить возможность вернуться к живой доске | KEEP |
| PL-22 | layout_modes, focus, Qt widgets | 2×2 adaptive, клавиатура, tooltip, размеры | REDESIGN / TEST |
| PL-23 | отдельные «Играть против AI» кнопки скрытых cards | плитка **только выбирает**, единственная CTA в панели | HIDE DUPLICATES |
| PL-24 | PlayPage._update_form меняет visibility | одна mode-dependent panel без потери значений | KEEP LOGIC / REDESIGN |
| PL-25 | текущая партия сохраняется при show_play | не сбрасывать от tiles, resize, обычной навигации | KEEP / TEST |

## B. Сквозные контракты (PL-26…PL-45)

| ID | Что надо сохранить или добавить | Решение |
| --- | --- | --- |
| PL-26 | online actions запрещены без готового Lichess/при 429; tiles всё равно можно выбрать | KEEP GATING |
| PL-27 | pending guard: 1 пользовательское создание = максимум 1 POST; unknown result не повторять вслепую | KEEP / TEST |
| PL-28 | existing MainWindow._create_play_request → service facade с реальными параметрами выбранного mode | KEEP |
| PL-29 | миграция старых сохранённых AI color/clock/level, не сбрасывать default в 1 | ADD / PRESERVE |
| PL-30 | selected mode и значения друг/вызов не смешиваются с AI при переключениях/перезапуске | ADD |
| PL-31 | живую ссылку и pending серверный запрос не восстанавливать вслепую из QSettings | KEEP SERVER TRUTH |
| PL-32 | account/generation guard и username per account: A не меняет UI B | KEEP / TEST |
| PL-33 | для friend random color реальное назначение стороны / friendUrl проверяются в service | KEEP |
| PL-34 | custom time 1..180 min, increment 0..180 sec, запрет invalid values | KEEP |
| PL-35 | «Без часов» не выдаётся за correspondence, если API вернул другое | KEEP CORRECTNESS |
| PL-36 | при старте новой local доски с несохранёнными ходами — понятное подтверждение при риске потери | ADD IF NEEDED |
| PL-37 | fresh active snapshot при входе в Play / game events, TTL/debounce, учесть §15 VOICE-05 | KEEP §15 / TEST |
| PL-38 | friend cancel до ID и после ID, cancel_too_late, без фиктивного success | KEEP / TEST |
| PL-39 | username challenge cancel with late ID/rejection/server ACK | KEEP / TEST |
| PL-40 | rated только друг и вызов, серверные ограничения, AI не rated | KEEP / TEST |
| PL-41 | свободная доска доступна без сети | KEEP |
| PL-42 | copy link через Qt clipboard, status, no sensitive URL log | KEEP |
| PL-43 | active item конкретный verified game ID, корректный N>1 | KEEP / REDESIGN |
| PL-44 | после confirmed start переход в имеющийся Game Workspace, не второе окно | KEEP |
| PL-45 | никакой Stockfish eval/PV во время active human game | KEEP FAIR PLAY |

## C. Что НЕ нужно переносить на страницу выбора режима, но нельзя ломать (PL-G)

| ID | Функция существующей игровой доски | Статус |
| --- | --- | --- |
| PL-G01 | Доска online AI/friend/challenge и сетевой stream | KEEP, отдельно проверять UI игровой доски |
| PL-G02 | Часы/статусы, сдача, предложение ничьей, takeback, premove и отмена | KEEP НА ДОСКЕ |
| PL-G03 | Локальная свободная доска (ходы за обе стороны, история PGN) | KEEP НА ДОСКЕ |
| PL-G04 | Тренировка предходов, переворот, стрелки и настройки доски | KEEP НА ДОСКЕ/В SETTINGS |
| PL-G05 | Завершение на последней позиции, «Разобрать»/«Вызвать снова» | KEEP approved game-flow |
| PL-G06 | Разбор, Stockfish, статистика, PGN/FEN, тренировки | KEEP в Анализе/Моих партиях |
| PL-G07 | Звуки, auth, OAuth/PAT, Credential Manager, account isolation | KEEP, НЕ рефакторить из-за Play |

## D. Что отложено или нельзя добавлять без решения владельца

| ID | Идея | Решение |
| --- | --- | --- |
| PL-F01 | **Случайный соперник / instant matchmaking Lichess, casual/rated** | **PARKED**: это не вызов по нику и не friend link, отсутствует среди текущих 4 режимов |
| PL-F02 | **Локальный Stockfish в качестве компьютерного противника** | **PARKED**: сейчас AI означает Lichess AI; free board — две стороны, не компьютер |
| PL-F03 | Свой стартовый FEN прямо в Play | **PARKED**: в Анализе уже есть редактор позиции |
| PL-F04 | Глобальная библиотека избранных пресетов (свыше запоминания последнего) | **PARKED** |
| PL-F05 | Отдельный lobby/чат ожидания и push оповещения | **PARKED** |
| PL-F06 | AI-советы/подсказки движка при активной человеческой партии | **NOT ALLOWED (FAIR PLAY)** |
| PL-F07 | Подготовка к сопернику, дебютный репертуар, AI-тренер | **PARKED**: будущее Анализа/Обучения |

## E. Обязательное обновление после будущего UX implementation

Для каждого PL-01…PL-45 будущий Codex/независимый ревьюер обязан заполнить:
- **Actual SHA, видимый UI-адрес, backend handler, поведение**, тест Qt/fake-stream и реальные Windows screenshots;
- статус **IMPLEMENTED / TESTED / PARTIAL / BLOCKED / NOT IMPLEMENTED**, без фиктивного PASS от одного «скриншот есть»;
- если кнопку скрыли: где **теперь** её пользовательская функция, или честное «не доступна» с отдельным решением владельца.

Для PL-G проверить отсутствие регрессий, PL-F не превращать в фиктивные кликабельные пункты. Этот документ **не санкционирует Codex, code PR или merge**.
