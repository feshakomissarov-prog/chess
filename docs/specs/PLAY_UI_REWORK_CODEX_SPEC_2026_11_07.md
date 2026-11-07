# Chess Coach — PLAY-UX-02: доработка существующей вкладки «Играть»

**Версия:** 1.0 · 07.11.2026  
**Статус:** проект для будущего ЕДИНОГО ТЗ всех экранов. Владелец согласовал **4 карточки выбора режима + 1 общую контекстную панель параметров + отдельную полосу активных партий**. Окончательный графический макет этого экрана ещё не утверждён. **НЕ передано Codex, НЕ выполнять сейчас.**  
**Характер задачи:** рефакторинг существующего Python/PySide6 Windows UI Chess Coach, а НЕ создание новой программы. Палитра, общий sidebar, shell и маршрутизация сохраняются.

**УТВЕРЖДЁННЫЕ ВИЗУАЛЬНЫЕ ЭТАЛОНЫ ВЛАДЕЛЬЦА — 07.11.2026, 19:43.** Два изображения размещены локально на Windows ПК владельца в папке `C:\Users\Михаил\Desktop\Пример\`:

1. **Lichess AI** — `C:\Users\Михаил\Desktop\Пример\Изображение ChatGPT 7 нояб. 2026 г., 19_38_56-1` (реальное расширение .png/.jpg/.jpeg определить по содержимому папки). Изображает **selected Lichess AI**: 4 плитки в сетке 2×2, активная полоса, общая панель AI с цветом, контролем времени, уровнями 1–8 и одной кнопкой «Играть против Lichess AI».
2. **Друг по ссылке** — `C:\Users\Михаил\Desktop\Пример\Изображение ChatGPT 7 нояб. 2026 г., 19_38_58-2` (реальное расширение определить). Изображает **selected Друг по ссылке / приглашение УЖЕ создано**: цвет, контроль, обычная/рейтинговая, подтверждённая ссылка, кнопки копирования и отмены **ВМЕСТО повторного «Создать приглашение»**.

**Оба изображения одобрены владельцем по внешнему виду.** Это **два состояния ОДНОЙ и ТОЙ ЖЕ существующей вкладки**, а не два окна/две страницы. КОДЕКСУ обязательно открыть **оба файла** на Windows, сравнивать композицию/поля/отступы/selection с натуральными Qt screenshots, приложить сравнительные снимки и проверить hit-tests. Если файл в рабочем окружении недоступен — запросить доступную копию, **не** утверждать «проверено по референсу» без изображения. Изображения и данные на них — художественные демонстрационные: `Boreas_91`, `ad_fesha`, счётчик игр, 3+2, уровень 4, URL `abc123`, rated и часы — **НЕ производственные значения и НЕ тест доказанного ответа Lichess**. В реальном приложении только source-backed данные и валидация реального endpoint.

**Покрытие визуальными эталонами:** AI = форма до отправки; Friend = готовая ссылка после server ACK. **Не показаны** friend editing до отправки, challenge username, local и разные empty/pending/cancel/error states; их реализовывать строго по разделам §3–7 и проверять отдельными собственными Windows screenshots, а не угадывать по двум рисункам. Состояние с полосой активных игр условное: при N=0 полоса исчезает.

**Сопроводительные документы:**
- ../ux/PLAY_OWNER_DECISIONS_2026_11_07.md — краткие решения владельца.
- ../ux/PLAY_FEATURE_LEDGER_2026_11_07.md — пофункциональный реестр «сохранить/перенести/добавить/не делать».
- ../ux/MY_GAMES_OWNER_DECISIONS_2026_11_07.md — стиль и правило работы соседней вкладки.
- Утверждённая ранее UX-B1 (документация PR #45) §C и §15.6 VOICE-04/05 — старые согласованные требования; при необходимости сверять, не создавать конкурирующих исправлений.

## 0. Что исполнитель читает перед кодом

**Контроль на момент написания:** source PR #44 HEAD 5c375b070e6644dbded5fca62d960fc09c90651e, docs PR #45 HEAD f7822ca5d2129aa493155e090b6ae1cc790c74f3. Перед последующей реализацией **заново проверить актуальные SHA** и то, что параллельная работа Codex уже исправила; не повторять исправленные VOICE-04/05 и не переносить старый код поверх нового.

**Source map:**
- src/chess_coach/ui/pages/play.py — PlayPage, 5 режимов из _MODES (local/ai/friend/challenge/active), общий mode_selector, QComboBox color/clock/level, rated checkbox, username, custom minutes/increment, start button, pending, cancel, invitation URL/copy/cancel, active panel. Уже создаётся QGridLayout карточек, но они **сразу скрываются** циклом setVisible(False). Пользователь сейчас видит дублирующие формы/карточки в различных состояниях.
- src/chess_coach/window.py — _build_play_page, show_play, _create_play_request, _track_play_request, _render_play_pending, _cancel_play_challenge_wait, _update_play_mode_availability, show_active_games, open_active_game, start_local_game, _challenge_ready, cancel_friend_invitation и статусные callbacks.
- src/chess_coach/lichess/service_games.py — create_ai_game, open_friend_game, challenge_username, fetch_active_games, cancel_pending_friend_challenge, cancel_open_invitation; здесь уже есть tracking request/generation и защита от повторов.
- src/chess_coach/ui/dialogs.py::OnlineGameDialog и старые Home shortcuts — альтернативный путь создания AI/друга/вызова с дополнительными диалогами. Пройти все call sites: новая PlayPage не должна создавать ещё один способ отправки POST или ломать существующие Home shortcuts.

**ВАЖНО — несоответствие скриншота и source:** на предоставленных владельцем актуальных Windows-снимках видны сразу пять карточек режимов **и** выпадающий selector. Но в проверенном SHA PR #44 файле play.py все карточки в конце __init__ скрываются через setVisible(False). Значит, скриншоты и выбранный source SHA **не доказаны как одна и та же сборка**. До кодовых правок Codex обязан записать точную версию установленного EXE/commit, сравнить актуальный source и наблюдаемые сценарии, затем устранить только реально существующий дубль. Не считать любой скрин исходной версии доказательством того, что source именно такой.

**Связанный риск live→local:** если уже идёт Lichess human match, переключение карточки не закрывает session и не разрешает Stockfish. Явное открытие свободной доски должно чётко объяснить переход в локальную workspace при сохранении серверной live-сессии и возможности возврата; если unsaved local line будет потеряна — запросить подтверждение. Не заменять «активная онлайн-игра» состоянием «свободная доска» в Lichess account guards.

**Инварианты:** текущая партия не сбрасывается выбором режима; никаких сетевых POST без явной CTA; server-authoritative success/cancel; auth-generation guard A/B; без выдуманного времени, результата, приглашения; никакого engine hint во время live human play. Никаких новых SQLite-миграций, Lichess credentials, отдельных приложений.

## 1. Задача и путь пользователя

1. Открыть «Играть». Увидеть **четыре понятных формата без раскрытия комбобокса**.
2. Кликнуть карточку нужного формата. Нижняя **единая панель** сразу показывает параметры **только этого формата**, уже заполненные значениями прошлого раза.
3. При необходимости изменить 1–2 параметра. Нажать **одну главную кнопку**, уникальную для формата. Карточка выбора **не** запускает игру.
4. При сети: увидеть inline «создаём / ждём / ссылка готова / ошибка / отменяем», не отдельное большое модальное окно. При успешном gameStart перейти на **существующую** игровую доску.
5. При действующей игре нажать «Продолжить», **не** создавать новую.

**Понятия:** «Выбрать режим» ≠ «Создать вызов» ≠ «Продолжить начатую игру». Не смешивать.

## 2. Компонентная композиция

**Существующая оболочка** слева (Главная / Играть / Мои партии / Анализ / Обучение / Прогресс, Настройки), общий Back/Фон/соединение. Текущие тёмно-синяя и приглушённо-фиолетовая палитры полностью сохраняются, как в «Моих партиях».

**При ширине обычного ноутбука:**
1. Заголовок «Играть» — одна смысловая шапка. Убрать ненужный второй крупный «Выберите режим игры» и многословный текст, повторяющий очевидное.
2. При N>0 активных игр: тонкая полоса **«Активная партия/Активные партии N»**, соперник, ход/контроль только при валидном snapshot и CTA «Продолжить»; при N>1 «Все активные (N)». При N=0 полоса не занимает место.
3. Подпись «Новая партия» (необязательная маленькая).
4. **Сетка 2×2** из четырёх больших кликабельных mode tiles: верхний ряд **«Lichess AI» / «Друг по ссылке»**, второй **«Вызвать игрока» / «Свободная доска»**. На плитке 1 небольшая тематическая иконка, название и ОДНА строка объяснения. Одинаковая ширина/высота, 12–16 logical px между плитками. Одна selected — фиолетовая рамка/фон в текущих tokens.
5. Сразу под плитками **одна общая панель параметров** с собственным заголовком выбранного режима, compact field groups, inline status и **одной главной CTA**. Переключение плитки заменяет контент панели на месте. Никаких дополнительных QDialog и других больших кнопок «Начать» в самих плитках.

~~~text
┌──────────────────────────────────────────────────────────────┐
│ ИГРАТЬ                              ← существующая оболочка   │
│ [АКТИВНА · соперник · Ваш ход      Продолжить]  (если есть)  │
│                                                              │
│ [ Lichess AI          ] [ Друг по ссылке     ]               │
│ [ Вызвать игрока      ] [ Свободная доска    ]               │
│                                                              │
│ ┌──────── Настройки · ВЫБРАННОЕ: Lichess AI ───────────────┐ │
│ │ Ваш цвет:  [Случайный] [Белыми] [Чёрными]              │ │
│ │ Контроль:  [3+2] [5+0] [10+0] [15+10] [Без часов*]     │ │
│ │            [Свой…] → [Минуты 1..180] [Инкремент 0..180]│ │
│ │ Уровень:   [1] [2] [3] [4] [5] [6] [7] [8]            │ │
│ │ Статус: ...                      [Играть против AI]     │ │
│ └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
~~~

**Ширина:** не растягивать один combo, кнопку или explanatory label на 1400 px. Поля объединить в 2–3 смысловые строки с компактными переключателями. Плавающую пустоту внизу экрана оставить как нормальное пространство, но не создавать гигантский top-only form.

**Доступность:** одна группа с radio-like exclusive selection, доступные текстовые названия, focus ring и Tab/Shift+Tab/Enter/Space, viewport no overlap. Реализация через штатные Qt layout managers, не абсолютные координаты. На 1280×720 помещается сетка 2×2+CTA; на 1120×640 и 125/150% разрешён **один локальный вертикальный scroll**, но нет горизонтальной прокрутки всей страницы и скрытых кнопок. Не занимать экран тремя вложенными скроллами.

## 3. Общие контролы для онлайн-режимов

**Цвет:** три кнопки-сегмента **«Случайный» / «Белыми» / «Чёрными»** (в API random/white/black). Выбран один. Изменение в AI не меняет цвет друга/вызова. При friend сервис может физически назначить сторону при создании ссылки — отражать серверную истину.

**Время:** компактные пресеты **3+2, 5+0, 10+0, 15+10, Без часов, Свой…**. По «Свой…» появляются только два компактных spinbox: минуты 1…180, добавление 0…180 секунд. Сохранять custom values, даже если пользователь временно переключился на пресет. Сохранить существующий validation (initial ≤10800s, increment ≤180s). «Без часов» = реальный API unlimited только там, где endpoint его подтвердил; **не писать «переписка» как синоним**. При incompatible server/rated option корректно блокировать/объяснять, не придумывать поддержку. Эти данные — настройки отдельного режима.

**Рейтинговая игра:** только friend и challenge, понятный segmented control **«Обычная» / «Рейтинговая»**. AI всегда нерейтинговый; local не имеет рейтинга. Не создавать отдельный режим «Рейтинговая» в плитках — это параметр, а не вид соперника. При rated/unlimited проверять реальный серверный контракт: неприемлемое сочетание не отправлять как гарантированное.

**Ник:** единственное поле **«Имя пользователя Lichess»** только для challenge. Trim пробелов, пустое значение → disabled CTA/inline причина, не посылать запрос. На AI/friend/local не показывать.

**Визуальный контракт:** один и тот же цвет/time/rated control визуально стоит на том же месте при переключении между AI/Friend/Challenge, но читает и пишет **разные state objects**. Никаких пяти копий полей, одновременно видимых.

## 4. Полное отображение каждого из четырёх режимов

### 4.1 Lichess AI (mode ai)

Показывать: цвет, время, выбор силы 1–8 (8 компактных прямых кнопок, ровно одна активна), CTA **«Играть против Lichess AI»**. Не показывать ник, rated, ссылку, список активных игр в панели AI.

При клике CTA → существующий _create_play_request(options mode=ai) → LichessService.create_ai_game (НЕ локальный Stockfish); показать «Создаём партию против Lichess AI…» и disabled повторный CTA. При confirmed game → existing Game Workspace. Если auth offline/429 — понятная inline подсказка «Подключите Lichess…» и запуск disabled; сам mode tile остаётся выбираемым.

Настройки AI: color, clock preset, custom minutes/increment, level (1–8). Мигрировать **старые** workspace/ai_color, ai_clock_limit, ai_clock_increment, ai_level, если сохранены; не сбросить уровень на 1 из-за текущего конструктора UI. Запомнить состояние **до** отправки/при переключении, а не только после успешного серверного ответа.

### 4.2 Друг по ссылке (mode friend)

**Режим редактирования:** color, time, regular/rated, CTA **«Создать приглашение»**. Нет username и уровня AI.

**После нажатия:** state pending, CTA не отправляет повторный POST, текст **«Создаём приглашение…»**. Если ID или URL ещё не получены, **не создавать фальшивую ссылку**. Если доступно только cancel request без challenge ID — оставить исходную server-safe отмену существующего сервиса; «отмена запрошена» ≠ «отменено».

**Только когда подтверждена реальная ссылка:** state invitation_ready, inline «Приглашение создано · ждём друга», readonly URL, **«Копировать ссылку»**, уместная **«Отменить приглашение»**. Главная «Создать приглашение» должна исчезнуть или быть disabled, чтобы не создавать второе приглашение. Копировать реальную подтверждённую ссылку, дать краткое «Скопировано». Не выставлять чужие URL/имена в CI или публичную документацию.

**Отмена:** использовать существующие cancel_open_invitation / cancel_pending_friend_challenge с точными request ID + generation. При late acceptance/cancel_too_late не считать операцию успешной до ACK. При rejected/expired вернуться к редактированию с прежними color/time/rated. Когда друг начал партию — открыть existing Game Workspace.

Внешний «Создать приглашение» — отдельная операция, **не** то же, что «Отправить вызов игроку по нику».

### 4.3 Вызвать игрока (mode challenge)

Показывать: **имя Lichess**, color, time, regular/rated, CTA **«Отправить вызов»**. Не показывать поле ссылки, кнопки копирования и AI level.

После нажатия: существующий service challenge_username(username, color, clock_limit, increment, rated), inline **«Ожидаем ответ [ник]…»**, кнопка **«Отменить вызов»**, если сервер реально допускает безопасную отмену. ID вызова может прийти позже; существующая cancellation state machine должна сохраниться, а не скрывать строку как «отменено».

При server accepted → game workspace; rejected/timeout/cancelled → статус+форма снова с тем же nickname и настройками. Не пытаться автоматически повторить POST после reconnect/429/unknown response. Не открывать QInputDialog или OnlineGameDialog поверх новой панели.

### 4.4 Свободная доска (mode local)

Показывать только объяснение **«Локальная доска — можно делать ходы за обе стороны»**, CTA **«Открыть свободную доску»**. Ни color/rated/time/username/AI level — этих параметров свободная доска сейчас **не требует**.

CTA вызывает **существующий** start_local_game; никаких запросов Lichess. Важно: простой выбор карточки **не сбрасывает** ongoing local board, аннотации, PGN или online session. Если явный старт новой свободной доски уничтожает несохранённые локальные ходы, перед разрушительным действием предусмотреть сохранение/подтверждение, не слепо «перезапускать» при возврате на экран. Историческая тренировка предходов — настройка **уже на игровом workspace**, не новая форма в Play.

## 5. Активные игры — отдельный быстрый вход

Это **не пятая плитка новой игры**. При N=1 отдельная верхняя компактная полоса «В ИГРЕ · [соперник] · [цвет] · [достоверный ход/контроль]» и CTA **«Продолжить»**. При N>1 компактный перечень (или «Все активные N» → список) с чётким выбором game ID. После клика existing open_active_game/resume_current_game, не POST на создание.

При N=0 полоса отсутствует. При loading/error/429 не показывать выдуманный ноль, а корректный статус; текущая локально известная сессия при недоступной сети не должна теряться. На входе в «Играть» и при known auth/game events обновлять список по подходящему TTL+debounce (без бесконечного polling) — **учесть VOICE-05 из текущего §15, не дублировать реализованное**. Должны работать account/generation guards и current server snapshot. Закрытие окна Chess Coach не делает active remote game безопасной для анализа.

Существующее глобальное **«Вернуться к партии»** остаётся рабочим вне страницы Play; new strip не подменяет глобальную навигацию.

## 6. Память пользовательских параметров: НЕ ТОЛЬКО одна общая форма

**Хранить отдельно для каждого режима**, со стабильными QSettings ключами (mode-prefixed keys/валидированный JSON):
- ai: color, preset/custom clock, custom minute/increment, level.
- friend: color, preset/custom clock, custom minute/increment, rated.
- challenge: username, color, preset/custom clock, custom minute/increment, rated.
- local: без параметров.

Отдельно selected mode. При возвращении на страницу показывать последнюю выбранную плитку и её сохранённые значения. При AI→Friend→Challenge→AI и при перезапуске параметры **AI не заменяются friend values**. Сохранять разумно при изменениях/смене плитки (QSettings/debounce), **а не только при успешном нажатии CTA**. Миграция существующих workspace/ai_* обязательна. Не сохранять Lichess OAuth/Token или служебный pending game ID в обычных полях Play.

**Изоляция account:** username последнего вызова следует хранить per connected account либо не показывать на B без явного consent; исключить случайную отправку вызова с чужим ником после A→B. Все source-driven pending URL, challenge ID, statuses и auth generation живут **в текущей серверной сессии, не в памятке настроек**. После перезапуска нельзя автоматически объявлять старую ссылку активной без fresh server verification.

**Pending + переключение плитки:** выбранный режим может смениться визуально, но исходящий запрос НЕ отменяется автоматически. Предусмотреть маленький общий persistent status о pending запросе (или ясно блокировать смену с объяснением), чтобы пользователь не потерял состояние invitation/challenge. Одновременно не разрешать второй несовместимый POST, пока неизвестен исход первого. При смене account старые ответы игнорируются.

## 7. Действия, обработчики и состояния

| Код | UI действие | Что делает | Нельзя |
| --- | --- | --- | --- |
| PL-01 | Выбрать карточку | selected mode + нижняя панель | сетевой POST, сброс доски |
| PL-02 | Цвет | random / white / black per mode | менять чужой mode state |
| PL-03 | Время | 6 presets per mode | посылать без submit |
| PL-04 | Свой контроль | 1..180 минут, 0..180 сек | терять значения при смене |
| PL-05 | Уровень AI | 1..8 | выводить у друга |
| PL-06 | Обычная / Рейтинговая | rated bool | делать AI рейтинговым |
| PL-07 | Имя пользователя | trimmed username | пустой POST |
| PL-08 | Играть против AI | existing AI create facade | duplicate API request |
| PL-09 | Создать приглашение | existing friend open facade | показывать URL до подтверждения |
| PL-10 | Копировать ссылку | clipboard confirmed friendUrl | копировать выдуманный URL |
| PL-11 | Отменить приглашение | safe cancel ID/generation | ложное «отменено» до ACK |
| PL-12 | Отправить вызов | challenge username facade | QInputDialog duplicate |
| PL-13 | Отменить вызов | safe cancel request/late ID | new call while unknown |
| PL-14 | Открыть свободную доску | existing start_local_game | сброс только от выбора tile |
| PL-15 | Продолжить | chosen verified active game ID | открыть чужой stale session |
| PL-16 | Все активные N | развернуть список | duplicate fifth mode tile |
| PL-17 | Статусы | pending, ready, rejected, error, cancelled, unknown | безмолвный spinner |
| PL-18 | Global Back, sidebar, Фон | существующие shell маршруты | создавать новую оболочку |
| PL-19 | Повторный вход | restore selected mode and fields | автоматический create |
| PL-20 | Старые Home shortcuts | вести в согласованный flow | новые modal дубли POST |

**Один source of truth pending-state** из существующего Lichess service/session; не создавать параллельную fake Qt модель. Набор UI состояний для каждого онлайн режима: editing, invalid, offline, submitting, pending_server_id, invitation_ready (только друг), awaiting_player, cancel_requested, accepted/game_open, rejected, failed, unknown_outcome (не повторять POST).

**Блокировка:** кнопка выбора режима остаётся видима даже без сети, но CTA онлайн-режима disabled с объяснением почему. Свободная доска всегда доступна без сети. Не скрывать карточку Lichess лишь из-за отсутствия подключения.

## 8. Responsive и визуальная приёмка

**Проверить настоящие Windows Qt:** 1672×941, 1360×860, 1280×720, 1120×640, scaling 100/125/150%, sidebar open/closed, длинные usernames, русская локализация, custom time expanded, active N=0/1/3, invitation ready + pending + failed. В каждой сцене: нет обрезанных кнопок, взаимного перекрытия, пустого немого верхнего блока и горизонтального page scroll. Primary CTA reachable.

Классически: размеры по Qt layouts, не absolute position; одинаковая высота четырёх карточек, одна selected, параметрические поля в 1–3 компактных строках, читаемые подсказки, видимое переключение состояния. Tab order и keyboard activation; selection не вызывает необратимые действия, Enter на главной CTA защищён pending guard. Не переписывать текущие colors/QSS tokens ради эффекта.

## 9. Тестовые сценарии и доказательства

Обязательные Red→Green fake/Qt tests:
1. Точно 4 tile, 1 selected, скрытый mode combo, **ни один tile click не вызывает create_ai_game/open_friend_game/challenge_username/start_local_game**.
2. Для AI: color × clock × levels 1/8, offline, pending; no rated/username; reuse старых AI defaults.
3. Для друга: color, custom time, rated, create pending → actual URL → copy → cancel, late ID, cancel_too_late, server rejection, network lost/unknown outcome; двойной click CTA = не больше одного POST.
4. Для challenge: nickname blank/valid, pending, cancel before/after challenge ID, declined/accepted, account A→B, 429; no invitation link UI.
5. Параметры AI 5+0 lvl4, friend rated 10+0, challenge username custom 7+3; AI→friend→challenge→AI, navigate away/back, app restart, B account; each set persists correctly, no cross-account nick.
6. Active N=0/1/3, auth changes, gameStart/gameFinish, stale ID and reconnect; correct resume, no engine hint, TTL+429 safe behavior.
7. Local free board selection doesn't reset; explicit start opens current workspace; if unsaved line, correct confirmation. Premove training control still works on board.
8. Qt geometry and hit tests in all responsive scenes; no overlapped/pushed off CTA. Windows screenshots **populate real fixture states**, not empty only.
9. Full regression: all six routes, online session identity/fencing, no new POST on ordinary navigate/resize, home shortcut one path, local board sound/premove, no settings secret leak.

**Доказательство:** new code SHA, changed files, test names/CI links, actual native Windows screenshots before/after and UI action matrix PL-XX with **IMPLEMENTED / TESTED / PARTIAL / BLOCKED**. Screenshot existence ≠ passed visual review. Independent review then owner Windows pilot only by permission. No automatic merge.

## 10. Объём и последовательность последующего исполнения

1. После текущего ARCH-UX-01/§15 PR45 reread current code HEAD, отметить что уже выполнено; no competing edits to PR44.
2. Вместо пяти скрытых/дублирующих режимов построить 4 selectable tiles и 1 context form; вынести active mode в conditional strip. **Переиспользовать исходные backend actions, signals, pagination/session state**, не создавать второй client.
3. Add per-mode persistence/legacy defaults/account guard, one-start CTA state machine, server-confirmed URL and cancellation; rebuild only necessary UI, не PGN/SQLite/Lichess client.
4. Испытать все PL-XX из реестра, Win Qt screenshots at all sizes; source audit + independent acceptance.
5. Включить в **будущее единое UX ТЗ всех вкладок** только по отдельной команде владельца; Codex сейчас занят другим циклом.

**Отложено (не реализовать тайно):** случайный matchmaking/rated поиск нового соперника; локальный Stockfish как AI-оппонент; отдельные локальные настройки стартового FEN на этой странице (FEN editor уже в Анализе); открытие серверного cloud analysis; AI coach/Training features. **Следующий отдельный дизайнерский разбор** — игровая доска и состояния после завершения, не часть перестановки карточек «Играть».
