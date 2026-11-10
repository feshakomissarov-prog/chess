# Chess Coach — технический долг

**Актуализировано:** 11.11.2026 · **База ревью:** `main` после [merge PR #46](https://github.com/feshakomissarov-prog/chess/pull/46), кодовый merge SHA [`16ddf1f4`](https://github.com/feshakomissarov-prog/chess/commit/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405).

Здесь хранятся **неблокирующие** замечания к уже объединённому коду. Они не означают, что все сценарии приложения проверены вручную: проведён широкий **статический** проход 82 Python-модулей `src`, основного UI, Lichess, SQLite, Stockfish, миграций и критичных рабочих цепочек, с просмотром набора тестов; полный pytest независимо **не запускался**. Последний зафиксированный exact-head Windows CI PR #46: [1115 passed и visual smoke 100/125/150%](https://github.com/feshakomissarov-prog/chess/actions/runs/42898354938). Проверка 13 эталонных UX-экранов бок о бок, production SQLite, реальный двухсторонний Lichess и пользовательский Stockfish всё ещё требуют отдельной ручной приёмки.

**Правило:** при подтверждении потери пользовательских данных, утечки токенов, нарушения Fair Play или другого опасного дефекта — эскалировать сразу, **не прятать в обычный техдолг**. Никакие перечисленные ниже исправления не выполнены этим документом.

## Приоритет P2 — исправить при следующем плановом цикле

### TD-01 · При рестарте Home теряет известную дату последних партий

**Статус:** подтверждено исходником и синтетическими результатами.

- В `dashboard_preview_snapshot()` читаются `g.last_move_at` и `g.game_time_precision` ([dashboard.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/application/dashboard.py#L93-L106>)), необходимые `format_game_activity_time()` ([display.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/ui/display.py#L48-L67>)).
- При записи сохранённого Tier A `_split_snapshot()` эти два поля не сериализует ([dashboard_cache.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/application/dashboard_cache.py#L188-L218>)).
- Поэтому после восстановления из кэша в UI может появляться «Время начала неизвестно», хотя до закрытия дата известна. Это видно и в отчётной телеметрии до/после восстановления, собранной при PR #46.

**Критерий исправления:** кешировать только необходимые, но **все нужные** поля времени, с версионированием, проверкой типов и точности (`date_only` не выдавать за timestamp); Qt-регрессия «из SQLite → restart → те же подписи и порядок трёх карточек». Проверить валидность старого cache-format и fallback.

### TD-02 · При ошибке нового фонового aggregate пропадают уже показанные last-good показатели

**Статус:** исходник подтверждён, детерминированный failure-тест требуется.

- Обработчик `_dashboard_result_ready` при `error is not None` безусловно пишет «н/д» во **все** карточки и заменяет подписи ([window.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/window.py#L3638-L3665>)), даже когда `_latest_dashboard_snapshot` содержит сохранённые достоверные метрики того же профиля.
- Ситуация: Home показывает старую точность/рейтинг с корректной меткой «обновляется» → фоновое чтение падает (SQLite locked / I/O) → видимые результаты исчезают. Отсутствующие показатели нельзя подменять фиктивными нулями.

**Критерий исправления:** оставить last-known-good по текущему scope, отдельно сообщить «обновление временно недоступно», не стирать источники/дату; только при полном отсутствии сохранённых результатов показывать «н/д». Тест на error callback с валидным B и на отсутствие B, включая Fast account switch и Fair Play.

### TD-03 · Резервный cache-файл не используется при валидном JSON с повреждённым Tier A/B

**Статус:** подтверждённая логическая ветка.

- `DashboardSnapshotCache._read_document()` возвращает первый документ, если его **внешний** envelope корректен ([dashboard_cache.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/application/dashboard_cache.py#L364-L405>)).
- `load()` проверяет `_valid_tier_a` / `_valid_tier_b` **после** этого, затем возвращает `None`, уже не пробуя `.previous` ([dashboard_cache.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/application/dashboard_cache.py#L407-L442>)).
- Значит повреждённые/частично записанные данные текущего профиля внутри формально валидного JSON лишают пользователя исправной резервной копии.

**Критерий исправления:** проверять per-scope данные каждого кандидата и переходить к следующему, если кандидат структурно невалиден. Не использовать чужой аккаунт, старую формулу или неподдерживаемую схему. Fault-injection: `main` valid-envelope/invalid-tier, `.previous` valid; обе повреждены; несколько профилей в одном документе.

### TD-04 · Глобальная dashboard revision провоцирует лишний тяжёлый пересчёт при изменениях другого профиля

**Статус:** подтверждённый механизм; частоту и фактический ущерб производительности измерить.

- SQLite-триггеры обновляют **одну** `dashboard_data_revision(singleton=1)` при изменении источников любых профилей, включая `account_games`, `puzzle_attempts`, `puzzle_player_rating_history`, `analysis_jobs` ([migrations.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/infrastructure/sqlite/migrations.py#L452-L532>)).
- `DashboardSnapshotCache.load()` сравнивает Tier B revision с этой глобальной ревизией ([dashboard_cache.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/application/dashboard_cache.py#L407-L442>)); `_request_dashboard_preview` считает такой Tier B stale и направляет запрос на фоновый полный aggregate ([window.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/window.py#L3355-L3430>)).
- Даже если партия другого аккаунта никак не относится к текущему фильтру, его Home получает ненужное обновление. Благодаря last-good это **не обязательно видимый лаг**, но нагрузка на SQLite и частота пересчётов могут расти.

**Критерий исправления:** измерения на двух изолированных профилях и no-op sync; source-aware/per-profile или per-facet invalidation только после доказательства корректности зависимостей (не отключать triggers для настоящих изменений `analysis_jobs` / `game_positions`). Тест A→изменение B→A не пересчитывается; изменение A → только релевантные карточки/агрегаты обновляются.

## Приоритет P3 — плановый housekeeping

### TD-05 · Повторная установка Stockfish оставляет неограниченные каталоги предыдущих версий

**Статус:** подтверждён исходником; объём диска зависит от реального числа переустановок.

- `install_official_stockfish()` при уже существующем target переименовывает его в `.<name>.previous-<timestamp>`, а после успешной установки удаляет только временный каталог `work`, не удаляя и не ограничивая `backup` ([engine_manager.py](<https://github.com/feshakomissarov-prog/chess/blob/16ddf1f4a41389bb9b45ca4c97bd0bebc767c405/src/chess_coach/engine_manager.py#L365-L382>)).
- При повторных установках остаются полные копии движка. Имена только с точностью до секунды могут конфликтовать при частых повторениях на Windows. При ошибке замены старый target корректно восстанавливается — **эту защиту не ломать**.

**Критерий исправления:** policy хранения одной последней резервной копии / ограничение числа и объёма, чистка только после проверенной новой версии, уникальные временные имена, безопасный rollback/cancel. Тест «установить, повторно установить, авария во время замены, штатная отмена, удалить лишнюю копию» на Windows.

---

## Не смешивать с дефектами

- **UX-01…UX-13:** не объявлять автоматически принятыми лишь по native smoke; требуется отдельное визуальное сопоставление с утверждёнными эталонами.
- **DIST-01:** будущий однокликовый установщик Windows с проверенными версиями зависимостей и Stockfish — **запланированное развитие, не баг**. Приступать после отдельного решения владельца.
- **Экран упражнений по ошибкам:** владелец отдельно отложил обсуждение удобства; **новое ТЗ здесь не формируется**.
- **Кнопка «Вернуть ход» в Lichess AI:** владелец перепроверил и подтвердил работоспособность, открытой претензии нет.

**Источник трёх ранее известных замечаний:** [историческая запись независимого ревью](https://github.com/feshakomissarov-prog/chess/blob/design/my-games-target-ux-2026-11-07/docs/reviews/PR46_POST_E1EA4BE_TECHNICAL_DEBT_2026_11_10.md). Настоящий документ в `main` — **единый текущий реестр технического долга**.
