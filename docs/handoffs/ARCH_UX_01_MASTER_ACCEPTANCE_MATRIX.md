# ARCH-UX-01 MASTER — матрица сквозной приёмки

Базовая проверка: 05.11.2026 · повторная сверка §10: 06.11.2026 · кодовая ветка `codex/arch-01-modular-refactor` · draft PR #44.

Матрица отделяет автоматические проверки и видимый синтетический Windows-прогон от действий, которым необходимы реальные Lichess, OAuth/PAT, Stockfish или данные владельца. `PASS` означает закрытый синтетический/локальный контракт; физические и сетевые ограничения указаны отдельно и не подразумеваются как пройденные.

## CORE-01…CORE-11

| ID | Обработчик / ответственность | Регрессия | Результат | Ограничение |
|---|---|---|---|---|
| CORE-01 | `LichessAuthMixin.capture_operation_context`, `operation_token`, `reject_current_credential_if_matches`; `send_move` | `test_stale_send_move_401_cannot_delete_or_stop_replacement_auth_session`, credential switch tests | PASS | Только fake Credential Store/HTTP; реальные PAT/OAuth не проверялись. |
| CORE-02 | `LichessGameActionsMixin` operation contexts и generation-scoped callbacks | `test_queued_game_action_does_not_read_replacement_account_token`, `test_old_game_full_event_after_account_switch_is_discarded`, `test_late_stream_ready_after_stale_or_cancelled_session_is_ignored`; `test_rv2_stream_and_cancel_finish_when_both_threads_cross_barrier` | PASS | События Lichess смоделированы. |
| CORE-03 | `MainWindow._legacy_metrics_batch_finished` → `EmbeddedStatisticsPage.refresh_data_preserving_position` | `test_application_resumes_bounded_v2_materialization_in_background_and_masks_scope`, `test_progress_refresh_preserves_page_selection_tab_and_scroll` | PASS | Временная SQLite и синтетические v2 runs. |
| CORE-04 | `_legacy_metrics_account_scope`, generation/scope rejection | `test_application_resumes_bounded_v2_materialization_in_background_and_masks_scope`, `test_new_account_home_scope_never_falls_back_to_all_accounts` | PASS | Аккаунты синтетические; старые ответы отброшены. |
| CORE-05 | `format_clock_increment_ms`, `MainWindow._update_clock` | `test_clock_increment_is_converted_from_milliseconds`, `test_live_clock_increment_bounds_cover_both_colors_and_reject_bad_payloads` | PASS | Проверено преобразование 2000 ms → 2 s и валидаторы; live-server clocks не подключались. |
| CORE-06 | `archive_store.import_archive_record` точность `createdAt` и обновление date-only | `test_archive_import_records_date_precision_and_unknown_date_is_not_recent`, `test_archive_row_shows_last_move_date_used_for_recent_order` | PASS | Fake archive records/temp SQLite; нет удалённого сканирования. |
| CORE-07 | Асинхронные puzzle-history account/page/summary queries, generation cancellation | `test_puzzle_history_falls_back_from_empty_connected_account_and_has_local_selector`, `test_puzzle_history_sql_paging_search_and_outcome_cover_all_2501_rows`; `test_rv2_delayed_puzzle_account_read_cannot_reopen_training` | PASS | SQLite временная; реальный puzzle API не запускался. |
| CORE-08 | `session_action_started/completed`, per-game one-shot pending actions | `test_duplicate_game_action_is_posted_once_while_first_request_is_pending`, `test_finished_game_rematch_requires_ready_stream_and_elapsed_retry_after`; `test_rv2_game_post_reconciles_only_applied_owner_snapshot` | PASS | Fake HTTP; повторные действия не отправляются. |
| CORE-09 | `session_operation_failed` маршрутизирует ошибку конкретной команде/партии | `test_game_action_failure_does_not_clear_pending_username_challenge`; challenge 429 retry regression | PASS | Ошибка `draw` не сбрасывает ожидающий вызов. |
| CORE-10 | Auth profile, финализация, статистика, история задач и заметки используют registered workers | `test_progress_filter_options_load_in_background_not_dialog_constructor`, `test_library_note_write_runs_off_ui_thread_and_remains_bound_during_selection_change`, `test_finished_game_write_finalizes_after_navigation_away_from_board` | PASS | Проверены thread ownership и задержанные workers; долгий реальный диск/антивирусный lock и время отклика не измерялись (SLA не задан). |
| CORE-11 | `GameSessionCoordinator` owner/generation registry, `_current_account_sessions`, detach/resync | `test_session_registry_isolates_account_and_auth_generation`, `test_account_switch_detaches_live_session_and_rejects_delayed_game_snapshot`, `test_late_safety_snapshot_cannot_authorize_old_account_session` | PASS | Два synthetic аккаунта; реальный multi-account stream не проверялся. |

## UI-01…UI-11

| ID | Обработчик / видимый эффект | Регрессия | Результат | Ограничение |
|---|---|---|---|---|
| UI-01 | `GameReviewWorkspace.return_requested` → `_return_from_review` → общий `WorkspaceNavigator` | `test_saved_review_return_uses_the_same_origin_as_shell_back` | PASS | Проверены Progress origin, tab и period filter. |
| UI-02 | `_show_result`, `_start_finalization_probe`, `review_finished_game` | `test_finished_game_write_finalizes_after_navigation_away_from_board`, `test_background_finished_game_sync_waits_for_sqlite_without_stealing_active_game` | PASS | Разбор доступен только по подтверждённой сохранённой линии; ошибку реального диска владелец не воспроизводил. |
| UI-03 | `_refresh_online_controls`, `_challenge_again` | `test_finished_game_rematch_requires_ready_stream_and_elapsed_retry_after` | PASS | Реальное создание rematch не выполнялось. |
| UI-04 | `PlayPage.cancel_pending_button` → `_cancel_play_challenge_wait` → service cancel → авторитетное подтверждение | `test_play_challenge_pending_cancel_uses_existing_lifecycle_without_resending`, `test_username_challenge_cancel_is_sent_for_pending_and_known_server_id`, `test_username_challenge_cancel_429_keeps_pending_stream_retryable`; `test_rv2_pending_render_survives_accepted_status_before_game_start` | PASS | 429 не считается подтверждённой отменой; fake Lichess. |
| UI-05 | Library note draft bound to selected game/line | `test_library_note_draft_stays_bound_to_the_game_where_it_was_typed`, `test_library_note_write_runs_off_ui_thread_and_remains_bound_during_selection_change`; `test_rv2_late_note_failure_cannot_enable_empty_selection` | PASS | Временная SQLite. |
| UI-06 | `EmbeddedStatisticsPage` async filter-options loader | `test_progress_filter_options_load_in_background_not_dialog_constructor` | PASS | SQL запуск проверен вне GUI thread; native медленный диск не тестировался. |
| UI-07 | `EmbeddedStatisticsPage._apply_result` empty state/page controls | `test_statistics_empty_states_and_account_action_are_truthful` | PASS | Пустой диапазон — «Нет партий…», обе кнопки страниц отключены. |
| UI-08 | `refresh_data_preserving_position` | `test_progress_refresh_preserves_page_selection_tab_and_scroll` | PASS | 105 строк, страница 2, выбор, вкладка и прокрутка сохранены после refresh. |
| UI-09 | `AnalysisLandingPage.position_setup.open_button` geometry | `test_analysis_position_editor_open_action_fits_1280x720_viewport`; `test_position_setup_uses_shared_board_and_emits_ephemeral_fen` | PASS для 1280×720/100% | Ручной native 125%/150% DPI и expanded FEN editor не подтверждены; требуется владельцу. |
| UI-10 | Puzzle history summary/sync diagnostics tooltips | `test_puzzle_history_falls_back_from_empty_connected_account_and_has_local_selector`; `test_auto_puzzle_history_sync_is_opt_in_to_existing_credential_only` | PASS | Синтетические состояния/ошибки; live sync не запускалась. |
| UI-11 | `LibraryPage._typing_filter_timer` (275 ms), latest generation query | `test_library_typing_filters_coalesce_to_one_query`, `test_library_sqlite_query_runs_off_ui_thread_and_ignores_stale_navigation` | PASS | Fake/temporary SQLite; не является latency benchmark. |

## UX-COV-01…UX-COV-24

| COV | Действия и проверяемые handlers | Автотесты / фактический видимый native smoke | Итог | Что осталось непроверенным |
|---|---|---|---|---|
| 01 Shell | шесть маршрутов, Back, collapse, connect/disconnect, safety status, return-to-game | `test_shell_has_six_typed_routes_and_games_analysis_progress_are_in_stack`; видны Home/Play/Library/Analysis/Training/Progress и sidebar | PASS (synthetic/offline) | Реальный reconnect/429 в аккаунте. |
| 02 Главная | KPI, account scope, 3 recent cards, Progress/Library, safe empty state | `test_home_dashboard_masks_cached_analysis_when_safety_is_pending_or_human_game_starts`; synthetic Home/card layout просмотрены | PASS | Owner account metrics и фактическая история. |
| 03 Играть | mode/color/clock/AI level, create, pending/cancel/error | `test_embedded_play_form_uses_existing_lichess_facade_and_prevents_duplicate_requests`, `test_play_challenge_pending_cancel_uses_existing_lifecycle_without_resending`; видимая локальная доска проверена | PASS (form/local); online create BLOCKED | Серверное принятие/отмена вызова. |
| 04 Активные партии | refresh/open/select/return, account ownership | `test_session_registry_isolates_account_and_auth_generation`, stale account tests | BLOCKED для native live list | Синтетический offline экран не может подтвердить API playing response. |
| 05 Live commands | move/clock/premove/takeback/draw/resign/abort/claim/cancel | Board/session and action de-duplication tests; локальные click-click moves видны в GUI | BLOCKED для реальной партии | Все POST/ack, server clocks and command error states требуют fake UI или owner online pilot; не запускалось. |
| 06 Локальная доска | free board обеими сторонами, undo, flip, local history; скрытые старые training actions | native e4/d5 play; `test_offline_undo_button_is_separate_from_premove_cancel` | PASS | Ручной drag/arrow pixel QA отдельно не выполнялся в этом прогоне. |
| 07 Доска/история | click/drag, legality, premove, promotion, history and highlights | `test_premove_drag_outside_or_escape_cancels_without_changing_position`, `test_clicking_or_dragging_own_piece_cancels_queued_premove_before_move_intent`, online pending tests; board/history визуально присутствовали | PASS (synthetic) | Native online premove/visual drag. |
| 08 Завершение | final event/SQLite exact line, review/export/rematch | `test_finish_before_terminal_state_persists_pending_then_syncs_final_ply`, UI-02/UI-03 tests; synthetic saved review seen | BLOCKED для online finish | Нет реальной партии/серверного окончания. |
| 09 Архив sync | full/incremental/pause/resume/429/cursor | `test_archive_sync_resumes_backfill_deduplicates_and_advances_checkpoint`, background fake-service tests; Library visible | BLOCKED для live API | OAuth/API и реальные архивные страницы не запрашивались. |
| 10 Ready Lichess data | background JSON backfill, queue, Retry-After, pause/manual check | `tests/test_m46_lichess_analytics_backfill.py`; Background inspector visible in offline state | BLOCKED для live API | Настоящие JSON exports и server availability не проверены. |
| 11 Библиотека | search/filter/page/sort/detail/export/note/review/sync | note/search/source tests; 2 synthetic games and detail actions visible | PASS (synthetic/local) | Real archive and export to user-selected file not executed. |
| 12 Разбор | board/list/CP-mate/graph/source/provenance/safety/return/export | synthetic saved analysis review visually inspected; route, source, Fair Play tests | PASS (synthetic) | Live safety lease/real server metrics. |
| 13 Ошибки/упражнения | previous/next/check/hint/skip/continue/repeat/return | local synthetic puzzle solved; `test_puzzle_replay_is_local_legal_and_does_not_change_history_or_send_requests` | PASS (local) | No real Stockfish hint or user history. |
| 14 Stockfish | Find/Browse/Install/UCI/preset/nodes/threads/hash/Save/Cancel | manager admission/cancel/fake-engine tests; settings visually inspected without invoking engine | BLOCKED для real UCI | No real engine run/install or file selection. |
| 15 Batch | start/resume/pause/cancel/retry/selective review/log | synthetic batch/worker tests, progress controls inspected; no process launched | PASS (fake); real UCI BLOCKED | Physical Stockfish analysis. |
| 16 Progress | 10 filters, pages, openings/phases/critical, charts/drilldown/batch | synthetic 105-row preserve test and visible Progress with synthetic rows | PASS | Owner data and real account-filter combinations. |
| 17 Puzzle history | account/search/page/outcome/replay/reveal/reset/sync diagnostics | 2,501-row SQL paging and local replay tests; Training/history visible | PASS (local/synthetic) | Network refresh/token scope not run live. |
| 18 Sounds | selectors/default IDs/QSettings/start/move/capture/volume/toggles | `tests/test_sounds.py`, settings sound controls visible | BLOCKED для слуховой приёмки | Физическое воспроизведение/качество звука на аудиоустройстве не подтверждено. |
| 19 Auth | OAuth/PAT/restore/disconnect/revoke/cancel | fake credential/generation regressions; connection controls inspected without opening auth | BLOCKED для реальной auth | No saved token or browser profile accessed. |
| 20 Diagnostics | open logs/copy anonymized report/storage and UCI errors | diagnostics privacy tests; diagnostics settings page visible | PASS (synthetic/local) | Physical file-open/export action not exercised. |
| 21 Workspace restore | Back, selected account/filter/review ply/tab/sidebar/window/single instance | route/filter tests, single-instance suite | PASS (tests) | Multi-monitor/125–150% DPI physical restore. |
| 22 Chess facts | openings/phases/accuracy/mate/WDL/source coverage | golden/local-metric/mate tests; visible Progress labels/source coverage on synthetic data | PASS | No owner-game correctness audit; real Lichess accuracy unavailable. |
| 23 Background inspector | refresh/pause/manual ready/local metrics/queue | synthetic state and account-stale tests; offline empty state visible | PASS (synthetic) | No actual connected queues or 429. |
| 24 PGN/FEN/position | cards/text/file/setup/turn/castling/EP/temporary review | FEN/parser/rights Qt tests and 1280×720 geometry; Analysis/editor visible | PASS для logic/100% geometry | Native expanded-editor click-through at display scaling and external file dialog not exercised. |

## MA-08 — детальная матрица видимых действий (subactions)

`PASS (test)` относится к автоматическому тесту с временными данными/fake сервисом; `VISUAL` означает, что раздел отрисован и осмотрен в обычном Windows GUI при 1360×860. Захват кадров не равен ручному клику мышью по каждой кнопке. Действия, требующие реального аккаунта/сервера/движка, честно отмечены `BLOCKED`. Скриншоты находятся только во временной папке Windows и не включены в репозиторий. Итоговый commit SHA и hosted CI фиксируются в PR после публикации.

| № | COV / видимый control → handler | Ожидаемое действие / состояние | Регрессия | Windows GUI | Статус / ограничение |
|---:|---|---|---|---|---|
| 01 | Shell → `Главная` / `show_home` | Показать локальные KPI/последние партии и безопасное пустое состояние | `test_m46_home_has_progress_activity_and_no_legacy_action_drawer` | `home.png` | PASS (test + visible render), offline synthetic |
| 02 | Shell → `Играть` / `show_play` | Открыть выбор режима, не сбрасывая текущую локальную партию | `test_sidebar_play_opens_mode_picker_without_resetting_local_game` | `play.png` | PASS (test + visible render) |
| 03 | Shell → `Мои партии` / `show_saved_games` | Открыть библиотеку; асинхронная загрузка не блокирует окно | `test_library_sqlite_query_runs_off_ui_thread_and_ignores_stale_navigation` | `library.png` (loading state captured) | PASS (test); завершённое визуальное состояние требует владельца проверить |
| 04 | Shell → `Анализ` / `show_analysis` | Открыть лендинг без запуска Stockfish | `test_analysis_position_editor_open_action_fits_1280x720_viewport`, `test_analysis_landing_describes_existing_opening_statistics_honestly` | `analysis.png` | PASS (test + visible render); UCI не запускался |
| 05 | Shell → `Обучение` / `open_puzzle_history_local` | Открыть локальную историю без сети/OAuth | `test_my_puzzles_home_action_opens_local_history_without_network_or_oauth` | `training.png` (loading state captured) | PASS (test); визуальная загрузка истории требует владельца проверить |
| 06 | Shell → `Прогресс` / `show_statistics` | Открыть Progress с независимыми фильтрами и пустыми состояниями | `test_progress_filter_options_load_in_background_not_dialog_constructor` | `progress.png` | PASS (test + visible render), synthetic empty DB |
| 07 | Shell → кнопка «Назад» / `navigate_back` | Вернуть предыдущую страницу/контекст | `test_saved_review_return_uses_the_same_origin_as_shell_back` | На скриншотах видна кнопка «Назад» | PASS (test); физический клик не выполнялся |
| 08 | Shell → сворачивание sidebar | Свернуть/развернуть левое меню без потери маршрута | `test_shell_has_six_typed_routes_and_games_analysis_progress_are_in_stack` | Все шесть route labels видны | PASS (test); pixel-state click не выполнялся |
| 09 | Главная → «Все партии» / Library route | Открыть список сохранённых партий | `test_home_has_rating_kpi_and_three_recent_game_cards` | `home.png` | PASS (test + видимая кнопка) |
| 10 | Главная → клик по карточке партии | Открыть именно выбранную партию | `test_recent_game_card_does_not_show_zero_coverage_when_analysis_is_missing` | Synthetic card rendered | PASS (test); настоящий архив не использовался |
| 11 | Главная → KPI/источник accuracy | Показывать только достоверный источник и охват | `test_home_dashboard_masks_cached_analysis_when_safety_is_pending_or_human_game_starts` | `home.png` показывает честное н/д | PASS (test + visual) |
| 12 | Играть → режим «Свободная доска» / `_create_play_request` | Создать локальную доску для обеих сторон | `test_embedded_play_form_uses_existing_lichess_facade_and_prevents_duplicate_requests` | `play.png` | PASS (test + visible render); онлайн POST отсутствует |
| 13 | Играть → параметры цвета/контроля | Передать выбранные параметры без скрытой конвертации | `test_ai_dialog_has_russian_controls_and_all_eight_visible_levels` | Форма Play видна | PASS (test); реальная партия не создавалась |
| 14 | Играть → AI level 1–8 | Сохранить выбор уровня только после подтверждения диалога | `test_cancel_ai_dialog_has_no_network_or_saved_preference_side_effect` | В этом смоке AI-диалог не открывался | PASS (test); no live Lichess |
| 15 | Играть → создание AI, HTTP 403 | Снять блокировку только соответствующего завершившегося запроса | `test_failed_ai_create_releases_only_its_pending_play_request` | Не применимо к offline UI | PASS (fake Qt/service) |
| 16 | Играть → создание AI, HTTP 429/timeout | Сохранить one-shot lock до авторитетной сверки, не дублировать POST | `test_unknown_ai_create_keeps_one_shot_locked_until_server_resolution` | Не применимо к offline UI | PASS (fake Qt/service); real Retry-After не запускался |
| 17 | Играть → поздняя ошибка старого запроса | Не снимать блокировку новой партии/поколения | `test_late_old_create_failure_cannot_release_new_play_request` | Не применимо к offline UI | PASS (fake Qt/service) |
| 18 | Играть → друг по ссылке, ожидание ID / cancel | Отобразить отмену до server ID и сохранить intent до ответа | `test_friend_create_has_visible_safe_pending_cancel_before_server_id` | Play render виден; запрос не запускался | PASS (fake Qt click/state) |
| 19 | Играть → отмена до ID, затем HTTP 429 | Не объявлять отмену завершённой, оставить retry | `test_friend_pending_cancel_429_is_retryable_and_never_claimed_success` | Не применимо к offline UI | PASS (fake API/barrier); live server не запускался |
| 20 | Играть → отмена до ID, после успешного создания | Отменить ровно полученный challenge ID один раз | `test_friend_invitation_cancel_before_post_response_cancels_exact_server_id` | Не применимо к offline UI | PASS (fake API/barrier) |
| 21 | Играть → отмена во время accept | Не отправлять cancel после принятия; показать, что запрос опоздал | `test_friend_invitation_cancel_during_accept_never_cancels_accepted_game`, `test_late_friend_cancel_is_reported_as_unconfirmed_not_success` | Не применимо к offline UI | PASS (fake API + Qt status) |
| 22 | Играть → username challenge cancel | Оставить stream и отменять только подтверждённый ID | `test_username_challenge_cancel_is_sent_for_pending_and_known_server_id` | Не применимо к offline UI | PASS (fake API); username не запрашивался |
| 23 | Активные партии → обновить/выбрать/вернуться | Принадлежность аккаунту и явный выбор сессии | `test_session_registry_isolates_account_and_auth_generation`, `test_library_pins_active_games_and_resumes_only_selected_session` | Live list отсутствует offline | BLOCKED: нужен разрешённый Lichess аккаунт |
| 24 | Активная игра → локальная доска/clock | Ходить только после валидного authoritative snapshot; часы по серверным данным | `test_active_game_blocks_all_actions_until_valid_game_full`, `test_online_clocks_use_authoritative_snapshot_and_pause_in_history_or_unknown_state` | Только статическая страница Play | PASS (test); live clock BLOCKED |
| 25 | Активная игра → предход | Отправить один раз после своего хода и повторной legality-проверки | `test_online_premove_is_available_on_opponent_turn_and_sent_once_after_state` | Не применяется offline | PASS (fake session); live premove BLOCKED |
| 26 | Активная игра → подтверждение хода | Pending move отображается, но PGN/серверная позиция обновляются только подтверждением | `test_online_pending_move_is_rendered_optimistically_without_persisting_until_confirmation` | Не применяется offline | PASS (fake stream); live ack BLOCKED |
| 27 | Активная игра → предложение ничьей | One-shot lock снимается лишь после изменения `wdraw/bdraw` | `test_draw_offer_and_acceptance_have_distinct_authoritative_effects` | Кнопка не нажималась | PASS (fake authoritative state) |
| 28 | Активная игра → принять предложение ничьей | Команда отличается от offer; ждёт terminal gameState | `test_draw_offer_and_acceptance_have_distinct_authoritative_effects` | Кнопка не нажималась | PASS (fake authoritative state) |
| 29 | Активная игра → отказаться от ничьей | Не путать отказ с offer/accept; ждать server state | `test_draw_offer_and_acceptance_have_distinct_authoritative_effects` + action reconciliation tests | Кнопка не нажималась | PASS (service contract); real game BLOCKED |
| 30 | Активная игра → сдача/abort/claim | Повторные POST заблокированы до server-confirmed finish | `test_successful_game_action_stays_one_shot_until_authoritative_reconciliation`, `test_429_game_action_is_not_reposted_until_authoritative_effect` | Live action panel недоступна offline | PASS (fake service); live actions BLOCKED |
| 31 | Активная игра → takeback accept/decline | Обновлять доску/PGN только по authoritative rewind | `test_takeback_offer_ui_accept_and_server_rewind_refresh_history_and_pgn` | Не применимо offline | PASS (fake session); real takeback BLOCKED |
| 32 | Live event другой партии → ожидающий challenge | Ошибка изоляции не должна снимать unrelated pending workflow | `test_unrelated_game_error_does_not_clear_pending_challenge` | Не применимо offline | PASS (Qt fake event) |
| 33 | Локальная доска → ход белых/чёрных и undo | Правила chess.Board, локальный ход и undo разделены от premove cancel | `test_offline_undo_button_is_separate_from_premove_cancel` | `play.png` показывает свободную доску форму | PASS (test); повторный native move не выполнялся в этом цикле |
| 34 | Доска → click-click/drag/drop | Источник/цель и legal move соответствуют одной позиции | `test_free_local_board_moves_both_sides_by_clicks_and_drag` | Board not captured in this smoke | PASS (Qt tests); physical mouse pixels BLOCKED |
| 35 | Доска → собственная фигура при активном premove | Удалить premove до создания следующего намерения хода | `test_clicking_or_dragging_own_piece_cancels_queued_premove_before_move_intent` | Не применяется offline | PASS (Qt test); online visual BLOCKED |
| 36 | Доска → Escape/отмена premove | Удалить только локальный intent; не отправлять POST | `test_cancel_premove_button_clears_queued_intent`, `test_cancel_premove_does_not_undo_a_move_already_submitted_to_lichess` | Кнопка не нажималась | PASS (tests); live POST не запускался |
| 37 | Завершение → переходы/финальный write | Не подменять экран; дождаться durable финального состояния | `test_finish_before_final_state_keeps_result_screen_then_updates_saved_pgn` | Review screenshot не снят этой итерацией | PASS (synthetic DB); live finish BLOCKED |
| 38 | Завершение → «Разобрать» | Открыть сохранённую точную финальную линию после проверки safety | `test_finished_game_write_finalizes_after_navigation_away_from_board` | Не запускалось онлайн | PASS (Qt/fake); real safety lease BLOCKED |
| 39 | Завершение → «Вызвать снова» | Не создавать rematch без подтверждённой готовности stream/cooldown | `test_finished_game_rematch_requires_ready_stream_and_elapsed_retry_after` | Не запускалось онлайн | PASS (fake stream); live rematch BLOCKED |
| 40 | Библиотека → поиск/фильтр/страница | Выборка соответствует SQL; старые ответы после навигации игнорируются | `test_library_sqlite_query_runs_off_ui_thread_and_ignores_stale_navigation`, `test_library_opponent_and_opening_filters_query_current_sqlite_line` | `library.png` loading state | PASS (tests); loaded visual needs owner check |
| 41 | Библиотека → экспорт выбранного PGN | Прочитать и записать файл без блокировки Qt heartbeat | `test_saved_game_pgn_export_repository_read_does_not_block_qt_heartbeat` | Файл не экспортировался | PASS (barrier temp export); user-selected native file dialog BLOCKED |
| 42 | Библиотека → заметка партии A/B | Draft привязан к `(game_id,line_revision)` и восстанавливается только для владельца | `test_library_keeps_independent_note_drafts_per_selected_game`, `test_library_note_write_runs_off_ui_thread_and_remains_bound_during_selection_change` | В этот smoke заметки не вводились | PASS (Qt + SQLite temp) |
| 43 | Библиотека → «Анализ Stockfish» | Только завершённая supported game, safety preflight до процесса | `test_normal_mode_never_uses_user_confirmation_as_stockfish_preflight` | Engine не запускался | PASS (safety tests); real UCI BLOCKED |
| 44 | Разбор → список ходов/доска/источники | Ход, сторона, позиция и источник остаются согласованными | `test_completed_pair_allows_selecting_white_halfmove`, source-selection/mate regressions | Synthetic Review виден в prior smoke | PASS (synthetic tests); personal PGN BLOCKED |
| 45 | Разбор → график/масштаб/Mate | Mate отображается отдельно от cp и не выдумывает CPL | `test_review_graph_scales_large_scores_and_delta_bars_inside_plot`, `test_checkmate_review_shows_mate_bar_accuracy_and_terminal_event_without_fake_cp` | Review not recaptured in current smoke | PASS (Qt/golden); no live analysis |
| 46 | Разбор → fair-play revoke/pending/denied | Все сохранённые аналитические сведения маскируются до актуального allow | `test_fair_play_revocation_masks_every_rendered_analysis_surface` | Не применяется offline | PASS (negative Qt visibility tests); live preflight BLOCKED |
| 47 | Разбор → «Разобрать мои ошибки»/hint/check | Применять только сохранённый approved analysis; исходный PGN не менять | `test_review_moves_graph_and_training_share_position_without_mutating_saved_pgn`, `test_saved_stockfish_pv_is_accepted_only_when_legal_same_run_and_better` | Тренировка не запускалась в этом smoke | PASS (synthetic workflow); real Stockfish BLOCKED |
| 48 | Анализ → импорт PGN/FEN и временное исследование | Проверить ввод, не добавляя данные в archive/SQLite | `test_external_pgn_and_fen_are_parsed_offline_without_database_or_engine_work`, `test_external_study_invalid_input_and_stale_response_do_not_open_review` | `analysis.png` | PASS (test + visible render); external file dialog не запускался |
| 49 | Анализ → очередь/пакет Stockfish | Существующая очередь и узловый бюджет сохраняются; отмена прекращает процесс корректно | `test_batch_queue_cancel_requires_confirmation_and_preserves_results`, `test_pre_cancelled_analysis_never_creates_engine_process` | Engine не запускался | PASS (fake engine); real engine BLOCKED |
| 50 | Progress → аккаунт/период/контроль/цвет | Каждая фильтрация обновляет SQL и сохраняет текущую страницу при фоновой миграции | `test_finishing_legacy_metrics_batch_refreshes_without_losing_progress_position` | `progress.png` | PASS (Qt + visible render) |
| 51 | Progress → вкладки/строки/детали позиции | Tab/game/scroll сохраняются; детализация открывает тот же game id/ply | `test_progress_refresh_preserves_page_selection_tab_and_scroll` | `progress.png`, empty synthetic state | PASS (test); данные владельца не использовались |
| 52 | Обучение → открыть/replay/check/skip задачи | Локальная задача не меняет историю и не отправляет запрос | `test_puzzle_replay_is_local_legal_and_does_not_change_history_or_send_requests` | `training.png` loading state | PASS (test); visual loaded task needs owner check |
| 53 | Обучение → история/поиск/счётчики | SQLite paging/denominators, синхронизация отдельно | `test_puzzle_history_sql_paging_search_and_outcome_cover_all_2501_rows` | Экран отрисован в loading state | PASS (test); live sync BLOCKED |
| 54 | Settings → звуки/selectors/preview | Каталог/выбор сохраняются без дублирования проигрывания | `tests/test_sounds.py`, sound settings regressions | `settings.png` (General tab) | PASS (test + visible render); слуховая оценка BLOCKED владельцу |
| 55 | Settings → Stockfish path/nodes/Cancel | Сохранять бюджет; cancel не запускать/не скачивать движок | `test_stockfish_settings_persist_without_launching_engine`, `test_saved_custom_node_limit_survives_settings_reopen_and_preset_changes_are_explicit` | Settings visible; engine not started | PASS (test); physical UCI BLOCKED |
| 56 | Settings → Lichess OAuth/PAT | Секреты раздельны в Credential Manager, экран не читает их при `--offline` | `test_explicit_offline_window_never_constructs_or_uses_lichess_credentials`, credential-store tests | Offline indicator visible, auth form not opened | PASS (test); real login BLOCKED |
| 57 | Settings → Диагностика/открыть логи | Логи обезличены; открыть безопасный каталог/отчёт | `test_diagnostics_are_allowlisted_redacted_and_utf8`, `test_settings_diagnostic_report_requires_explicit_opt_in` | Diagnostics tab not captured separately | PASS (privacy test); native file-open not run |
| 58 | Background → pause/resume/manual ready check | Очередь привязана к аккаунту и Retry-After; без account API не вызывается | `test_queued_backfill_status_from_previous_account_is_ignored`, analytics backfill fake suite | `background.png` (loading state) | PASS (tests); live Lichess BLOCKED |
| 59 | Background → local legacy metrics batch | Продолжать ограниченными порциями, не сбрасывая Progress | `test_starting_legacy_metrics_batch_preserves_progress_page`, `test_finishing_legacy_metrics_batch_refreshes_without_losing_progress_position` | Progress visible | PASS (Qt/temp SQLite) |
| 60 | Diagnostics/background → отмена/ошибка/429 | Показывать конкретную операцию; не ломать соседние игровые действия | `test_game_action_failure_does_not_clear_pending_username_challenge`, `test_friend_pending_cancel_429_is_retryable_and_never_claimed_success` | Fake errors only | PASS (fake); реальный 429 BLOCKED |
| 61 | Архив → полный/инкрементальный sync/cancel | Один checkpoint, resume без повторной загрузки и без потери данных | `test_archive_sync_resumes_backfill_deduplicates_and_advances_checkpoint` | Sync не запускался | PASS (synthetic); live API BLOCKED |
| 62 | Lichess ready analytics → old saved games/Retry-After | Документированный JSON export; не вызывать внутренний route создания анализа | `tests/test_m46_lichess_analytics_backfill.py` | Нет аккаунта в offline mode | PASS (fake transport); live Lichess BLOCKED |
| 63 | Авторизация → connect/disconnect/reconnect/account switch | Старый ответ не меняет выбранную учётную запись и не разрешает Fair Play | `test_account_switch_detaches_live_session_and_rejects_delayed_game_snapshot`, `test_late_safety_snapshot_cannot_authorize_old_account_session` | Auth controls не нажимались | PASS (fake generation); live OAuth/PAT BLOCKED |
| 64 | Workspace restore → last route/filter/sidebar/window size | Восстановить только безопасное состояние, не перебивать активный экран | `test_workspace_review_resumes_same_revision_ply_and_inspector_tab`, `test_delayed_workspace_restore_never_steals_a_page_already_opened_by_user` | Обычное окно 1360×860 | PASS (tests); multi-monitor/DPI BLOCKED владельцу |
| 65 | Shutdown → активный worker/SQLite | Завершить операции до освобождения repository/QProcess | `test_window_close_waits_for_workers_before_shutting_down_sqlite`, Qt lifecycle suite | Smoke process штатно закрылся | PASS (tests + smoke); зависший real UCI не запускался |
| 66 | Source labels → official vs local metrics | «Н/Д» при отсутствии метрики, не выдавать смешанный источник | `test_home_dashboard_masks_cached_analysis_when_safety_is_pending_or_human_game_starts`, source golden tests | Home/Progress labels visible | PASS (test + visual) |
| 67 | Timestamps/order → archive reimport | Не понижать точный `started_at` до `date_only` и не менять chronology | `test_archive_reimport_never_degrades_authoritative_start_timestamp` | Не визуальный контроль | PASS (temporary SQLite) |
| 68 | SQLite detail/export read → GUI heartbeat | Длинное чтение PGN/detail выполняется вне main Qt thread | `test_saved_game_analysis_repository_read_does_not_block_qt_heartbeat`, `test_saved_game_pgn_export_repository_read_does_not_block_qt_heartbeat` | Window remained responsive in barrier Qt tests | PASS (controlled barrier; owner disk not tested) |
| 69 | Единичный game action → delayed/duplicate click | Одна команда на game/command/generation до authoritative reconcile | `test_successful_game_action_stays_one_shot_until_authoritative_reconciliation`, `test_429_game_action_is_not_reposted_until_authoritative_effect` | Fake HTTP only | PASS (service fake); live POST blocked |
| 70 | Error path → unrelated active workflow | Ошибка другой партии/challenge не сбрасывает pending controls | `test_unrelated_game_error_does_not_clear_pending_challenge`, `test_game_action_failure_does_not_clear_pending_username_challenge` | Qt synthetic event path | PASS (fake Qt) |

## Видимый Windows smoke и общий итог

Один обычный видимый Python 3.12 offline launch на временных synthetic SQLite/settings: Home; Play (локальные e4/d5 click-click); Library; Analysis; Training (локальный пазл); Progress; Settings tabs; saved-game Review; Background Operations. Корректная позиция задачи и офлайн-обозначение были проверены; фикстура с некорректным одноходовым решением была заменена валидной и в итоговый PASS не включена. Реальные Lichess/OAuth/PAT, пользовательская SQLite, Stockfish/UCI, игровой сервер, внешний файловый экспорт и физическая слышимость звука не запускались. Скриншоты владельца не использовались и в репозиторий не добавлялись.

Владелец: выполнить online-only ручные проверки таблицы (COV-03/04/05/08/09/10/14/18/19) при отдельном разрешённом подключении; подтвердить звук; осмотреть DPI 125%/150% и multi-monitor restore. Draft PR #44 остаётся неслитым; эти ограничения являются отдельной ручной приёмкой, а не PASS.

## Повторная сверка §10 (RV2-01…08)

Проверены ссылки и контракты всех строк CORE, UI, COV и 70 поддействий. Указанные ранее статусы относятся к своим тестам и прогонам. Ниже новые доказательства по изменённым путям. Для прочих действий доказательство этого цикла — соответствующие тесты полного набора, где они есть. Уровень покрытия не повышался по одному совпадению имени теста.

| Пункт | Строки матрицы | Проверка и результат |
|---|---|---|
| RV2-01 | COV-03, 05, 19 / 18–22, 63, 65, 69 | `test_rv2_stream_and_cancel_finish_when_both_threads_cross_barrier`: два потока захватывают исходные locks, старый код достигает timeout, новый завершает оба вызова, Qt heartbeat отрабатывает. |
| RV2-02 | COV-03 / 18–21 | `test_rv2_successful_friend_request_does_not_retain_credentials`: начальный запрос и ещё 100 успешных созданий, нулевая карта завершённых запросов. Сценарии принятия и отмены покрыты существующими fake-тестами. |
| RV2-03 | COV-03 / 19–21 | `test_rv2_partial_friend_accept_429_does_not_repeat_create_or_cancel`, `test_rv2_friend_partial_success_rolls_back_only_after_definitive_rejection`: частичный успех, отказ 403, rollback 429, точечная отмена по ID и отсутствие повторного create. |
| RV2-04 | COV-03, 19 / 16, 19, 63 | `test_rv2_pre_id_unknown_is_retained_until_explicit_session_reset`: исход неизвестен, повтор заблокирован до управляемого сброса сессии. UI предлагает проверить активные партии и приглашения на Lichess перед повторным созданием после переподключения. |
| RV2-05 | COV-05, 07, 08 / 24–32, 69 | `test_rv2_game_post_reconciles_only_applied_owner_snapshot`, `test_rv2_authoritative_offer_during_http_is_retained_until_completion`: невалидная или устаревшая линия и флаг соперника не подтверждают свою команду. Валидный ответ до или после завершения HTTP снимает блокировку. |
| RV2-06 | COV-02, 03 / 12–22 | `test_rv2_pending_render_survives_accepted_status_before_game_start`: одна блокировка для Home и Play, статус принятия сохраняет ожидание, пока не подтверждено начало соответствующей партии. `test_friend_create_has_visible_safe_pending_cancel_before_server_id` проверяет кнопки на странице «Играть» и в боковой панели игры. |
| RV2-07 | COV-08, 11, 12 / 37, 41, 68 | `test_rv2_export_rejects_revision_changed_before_worker_read`, `test_rv2_export_preserves_destination_and_exact_pgn`: старая цель сохранена при смене ревизии или ошибке замены, оригинальные байты экспортированы при успехе. |
| RV2-08 | COV-11 / 42 | `test_rv2_late_note_failure_cannot_enable_empty_selection`: устаревшая ошибка сохранения не меняет кнопку текущей партии. |
| Дополнительная гонка навигации | CORE-07, COV-17, 21 / 05–07, 53 | `test_rv2_delayed_puzzle_account_read_cannot_reopen_training`: задержанный ответ истории задач не перехватывает навигацию из «Прогресса». |

Windows, обычный Qt backend `windows`, 1360×860, временная SQLite и настройки. `QTest.mouseClick` прошёл шесть основных маршрутов, «Настройки», запуск локальной доски, экспорт в временный файл и открытие разбора. Сохранено 10 кадров в `%TEMP%/chess-rv2-native-lc12ultx`. Диалог выбора файла заменён фиксированным временным путём. Физическая мышь, полный проход остальных контролов, DPI 125/150%, живые OAuth/PAT, Stockfish и звук на устройстве — **НЕ ЗАПУСКАЛОСЬ**.


## VISUAL MASTER — промежуточная проверка 06.11.2026

Затронуты COV-02, 03, 04, 07, 11, 12, 15, 22, 23. Новый визуальный gate ещё открыт. В журнале указаны каталоги трёх пар и незавершённые требования. `test_visual_master_dashboard_refresh_renders_after_selector_bootstrap` подтверждает повторную отрисовку снимка аккаунта, `test_batch_queue_and_remote_active_games_stay_inside_workspace` проверяет явное открытие выбранной активной партии из Play. Сравнения Windows выполнялись на синтетической SQLite. Полный connected click-through и DPI 125/150% — НЕ ЗАПУСКАЛИСЬ.

### VISUAL MASTER — контрольная точка продолжения

| Раздел | Проверка | Статус |
| --- | --- | --- |
| Главная | Native Windows, три синтетические сохранённые партии, три размера окна | Отрисовано. Общая точность и официальный рейтинг задач добавлены; визуальная сходимость остаётся открытой. |
| Мои партии | Native Windows, список и выбранная партия с сохранённым Stockfish-результатом | Отрисовано. Сетка строк и правая панель ещё отличаются от арта. |
| Фоновые операции | Native Windows, четыре карточки и пустое состояние; pytest на очереди двух аккаунтов | Отрисовано. Реальная загрузка и полная история всех операций не проверены. |
| Часы и игровые состояния | Qt с синтетическими снимками 3+2 до и после первых ходов, история и потеря синхронизации | Автотесты PASS. Реальные таймеры Lichess и физический DPI 125/150% не проверены. |
| Разбор | Тесты локальной точности по полной линии, границ фаз, цвета и Fail Closed | Целевые проверки. Живой Stockfish и личные данные не использовались. |
## VIS4 addendum — дополнительные проверки и состояния

| Пункт / действие | Регрессия и ожидаемое действие | Native visual evidence | Статус / ограничение |
|---|---|---|---|
| VIS4-01 Главная | Возврат Home→Library→Background→Home и resize 1028→1672; sidebar разворачивается автоматически, если владелец явно не сворачивал его; 3 последние карточки содержат реальные synthetic source-aware метрики и графики | `pair-home-*.png`, кадры Home для трёх target sizes | PASS по синтетике; композицию с утверждённым артом ожидает независимое визуальное ревью |
| VIS4-01 Мои партии | Штатная загрузка временной SQLite; минимум 3 строки, выбранные `game_id` и `line_revision`, загруженный review, два источника, метрики и график до кадра | `pair-games-*.png`, `capture.txt` содержит фактические ID/размеры | PASS по строгим capture assertions; живые данные не использованы |
| VIS4-01 Фоновые операции | Отдельные active и offline состояния; archive/Stockfish running, analytics waiting, puzzle completed и 4 durable events | `pair-background-active-*.png`, `pair-background-offline-*.png` | PASS synthetic; реальные сетевые операции не выполнялись |
| VIS4-02 capture validity | Любая незагруженная/пустая выбранная партия или отсутствие graph/metrics вызывает ошибку capture; используется обычный `MainWindow` и production workers | Workflow публикует исходные кадры и пары | PASS локально; hosted artifact ждёт финального CI |
| VIS4-03 фактические размеры | Assertion по `QWidget.size()`, `QImage.size()` и DPR; target не маркируется по одному имени файла | `capture.txt` для scale 1/1.25/1.5 | PASS на перечисленных логических размерах; физические DPI 125/150% не проверялись |
| VIS4-04 Все недостающие + retry | Старые failed/cancelled/stale повторяются только при явном checkbox, по одному разу на запуск, без цикла; аккаунты изолированы | Неприменимо | PASS: `test_all_missing_retry_requeues_old_failures_once_per_account_and_run`, dialog forwarding regression |
| VIS4-05 Library controls / sort tooltip | Нет доступных действий без выбранной загруженной записи; повторный вход/resize не открывает плавающее окно сортировки | Library кадры и проверка widget parent/visibility | PASS: `test_library_sort_help_never_opens_a_floating_window_on_reentry`; физическая мышь не использовалась |
| VIS4-06 сохранность VIS3 | Auto-resume, актуальность ply, progress coalescing и backlog продолжают проходить существующие регрессии | Background active state | PASS по локальному полному pytest; реальные Lichess/Stockfish не запускались |

Локальные кадры не коммитятся. Подтверждённый артефакт Windows Actions [run #351](https://github.com/feshakomissarov-prog/chess/actions/runs/42257307039#artifacts) содержит все пары «арт / окно Windows», source PNG и `capture.txt`; capture прошёл на exact implementation SHA `e16b18fc0dee86cbca1969a051afe9fa8e90fcbb`. Runs #349/#350 честно завершили visual capture ошибкой; после перехода на `Set-DisplayResolution` фактическая геометрия прошла. Документирование run #351 создаёт новый SHA, для которого требуется отдельный exact-head CI. См. [execution log](ARCH_UX_01_EXECUTION_LOG.md) для статусов и ручных ограничений.

## VIS5 и дополнение владельца — 06.11.2026

| Область | Воспроизводящий тест / обработчик | Видимая проверка | Статус / ограничение |
|---|---|---|---|
| Home preview до тяжёлой сводки | `dashboard_preview_snapshot`; `DashboardQueryCoordinator.snapshot(wait_for_writes=False)`; `test_dashboard_preview_returns_committed_local_rows_without_waiting_for_queued_writes`; `test_dashboard_snapshot_can_read_committed_data_without_waiting_for_writer_queue`; Qt: `test_home_shows_local_archive_preview_while_full_dashboard_aggregate_is_pending` на 2500 синтетических строках | `1672x941-home.png`, `1360x860-home.png`, `1280x720-home.png` | PASS: число/последние карточки сразу, локальный рейтинг задач из preview; тяжёлая сводка асинхронна. Точные latency/SLA не заявлялись. |
| Честный статус готовой аналитики | `BackgroundOperationsPage.set_snapshot` скрывает ready percentage; проверенные/ожидающие/повтор/источник остаются текстом | active/offline Background кадры | PASS синтетически; реальный Lichess не подключался. |
| Канонический дебют и значения метрик | `game_opening_name` используется Home/Library/Review; метрики без classifier остаются `н/д` | Home, Library, Review кадры | PASS: C60 показывается согласованно; реальные архивные данные не использовались. |
| Popup сортировки Library | `LibraryPage` больше не создаёт orphan QLabel; `test_library_sort_help_never_opens_a_floating_window_on_reentry` | Windows native переход Library плюс проверка всех видимых `QApplication.topLevelWidgets()` | PASS: лишнее окно отсутствует; пояснение доступно скринридеру через accessibleDescription. |
| Единый dark styling | `_shell_screen_changed` применяет VISUAL_TOKENS всем shell routes; tabs/checkbox/radio/combo/progressbar в stylesheet | Все десять экранов показаны в обычном Windows окне | PASS по визуальной доступности/согласованности палитры; точное совпадение с артом — НЕ ПРИНЯТО, ожидает независимой оценки. |
| Capture state integrity | production workers + temp SQLite; strict assertions на selected IDs/revision, источники, график и геометрию | 18 кадров/состояний в `visual-evidence/final-native/` | PASS локально; артефакты CI появятся только после exact-head Windows CI. |

Полный локальный прогон после обновления fake-сигнатуры: **829 PASS** с worker warnings-as-errors; Python 3.12.10. `compileall -q src scripts tests`, `git diff --check` и native Windows GUI smoke **18/18 PASS**. Windows CI #42278388653: SUCCESS на SHA `062cef0592bbc2fd80f2ea029beee5a9f5ac6723` (829 PASS, compileall, offline init, native visual-artifact PASS): https://github.com/feshakomissarov-prog/chess/actions/runs/42278388653. Exact-head проверка документационного follow-up будет запущена отдельно. Реальный OAuth/Lichess, пользовательские данные/SQLite, Stockfish и физический display DPI не использовались.

## VIS6-01…05 — acceptance matrix (локальная проверка)

| Пункт | Обработчик / regression | Native visual evidence | Статус |
|---|---|---|---|
| VIS6-01 Home быстрый preview | dashboard_preview_snapshot + независимый interactive OperationHandle; Qt stale-generation, 3500 партий, account A→B, queued writer | Home 1672×941 / 1360×860 / 1280×720; быстрое обновление текущего account | PASS на synthetic SQLite; конкретная задержка личной БД не воспроизведена |
| VIS6-02 Lichess ready analytics | SQLite summary/next eligible + fake transport; 429, Retry-After, budget, pause, no-data TTL | Background active/offline; видимая причина и срок следующей проверки | PASS на fake state; реальное расписание аккаунта не проверено |
| VIS6-03 утверждённые экраны | canonical opening, truthful source/coverage; пары Home, Library, Background | 3 пары × 3 окна в visual-evidence/vis6-final; capture.txt проверяет реальные dimensions | PASS capture/data readiness; эстетический GO только после независимого сравнения |
| VIS6-04 общая система экранов | theme token regression, Qt rating-graph pixel test, screen safety regressions | 25 состояний: Play, Analysis, Training, Progress tabs, Settings tabs, active game, saved review + эталонные экраны | PASS в обычном Windows Qt при масштабе 100%; hardware DPI не проверялся |
| VIS6-05 скорость/evidence | 5-run benchmark + EXPLAIN, strict production MainWindow capture | synthetic benchmark: 100 = 5.66 ms median; 3500 = 26.77 ms median; 25 кадра/состояния | PASS локально; exact Windows CI pending |

Не выполнялись реальные OAuth/PAT, Lichess, пользовательская SQLite/PGN, Stockfish, звук и физические DPI 125/150%. Артефакт GitHub Actions будет приложен запуском на финальном SHA. PR #44 остаётся draft, main не меняется.

### VIS6 exact local test checkpoint — 07.11.2026

| Gate | Итог |
|---|---|
| Полный pytest, Qt worker warnings as errors | **837 PASS / 263.68 s** (после исправления capture fixture) |
| compileall src/scripts/tests | PASS |
| git diff --check | PASS |
| Native Windows screenshot gate после финальных правок | **25 strict states PASS**, 1672×941 / 1360×860 / 1280×720, Qt scale 100% |
| Benchmark 100 / 3500 synthetic games | **5.59 ms / 26.60 ms median** за 5 проб; планы индексов проверены |
| GitHub Windows CI на финальном commit | Ожидается |

Реальные Lichess/OAuth, пользовательская SQLite/PGN, настоящий Stockfish, physical Windows DPI и устройство вывода звука не проверялись.

### VIS6 evidence correction — 2026-11-07

| Пункт | Повторная проверка | Итог |
|---|---|---|
| VIS6-05 synthetic Progress capture | Исправлено время последней синтетической партии: оно гарантированно раньше now; проверены 8 партий и выбранный аккаунт | PASS: ранее missing row был ошибкой fixture, SQL-выборка работала корректно |
| Native Windows evidence | Повторный production MainWindow capture, строгие проверки данных/размеров и пары с PNG для Home, Library, Background | PASS: 25/25 состояний; локальные пары доступны в `visual-evidence/vis6-final/` |
| GitHub artifact | Runs `42315427643` and `42317068946` exposed fixture timestamp and cp1252 output; fixes are in `f1fdc28427bec04b47b51d549cfdacf677b49ff0` | Windows run #367 / `42317639521` SUCCESS; 837 PASS, visual scales 1/1.25/1.5 PASS; [screenshots artifact](https://github.com/feshakomissarov-prog/chess/actions/runs/42317639521#artifacts) |

Обновлённые benchmarks: Home preview на временной synthetic SQLite — 100 партий **5.66 ms median**, 3500 партий **26.77 ms median** (5 выборок; EXPLAIN использует индексы). Реальные пользовательские данные и Lichess не использовались.

## VISUAL MASTER §15 — сводная трассировка требований

| ID / группа | Ожидаемое действие / состояние | Проверка | Итог |
|---|---|---|---|
| V-01 | Адаптивная компоновка без обрезки и наложений, реальные logical/pixel размеры | `scripts/capture_visual_master.py`; capture проверяет QRect карточек Background, event heading/list, Back button и height-for-width статусов; `test_background_page_compact_layout_has_no_overlapping_cards_or_events` | PASS для Qt capture (1672×941, 1360×860, 1280×720); physical Windows DPI 125/150% — BLOCKED |
| V-02 | Три утверждённых арта сопоставлены с загруженными Home/Library/Background | пары `pair-home-*`, `pair-games-*`, `pair-background-active-*` в visual-evidence и Actions artifact | Скриншоты получены; высокая визуальная сходимость — НЕ ПРИНЯТА, ждёт независимой/владельческой оценки |
| V-03 | Общая тема и доступные действия всех наполненных разделов | 25 synthetic view states, существующая UX-COV матрица и 70-control smoke | Авто-покрытие PASS; ручное прохождение каждой кнопки — BLOCKED |
| T-01 | OAuth revoke не удаляет заменённый credential | barrier regression conditional delete | PASS |
| T-02 | Поздние операции не меняют аккаунт после reconnect/switch | account/generation Qt and fake-service regressions | PASS |
| T-03 | Partial/invalid payload не засчитывается как готовые данные | official JSON/schema and malformed/partial fixtures | PASS; real endpoint — BLOCKED |
| T-04 | Быстрый Home worker не зависит от handle до старта | синхронный fast-worker regression | PASS |
| T-05 | close останавливает backfill | service-close stop-event regression | PASS |
| T-06 | «Проверено», «данные получены», no-data и retry имеют точный смысл | SQLite summary and ready-analytics UI tests | PASS на synthetic DB |
| R-01 | Home preview не ждёт тяжёлый агрегат | 3500-game temporary SQLite benchmark + delayed aggregate Qt test | PASS; медиана 26.77 ms за пять прошлых проб |
| R-02 | Lease не дублирует live работу, orphan queue восстанавливается | live lease and stale lease SQLite regression; свежий приоритет + резерв старому backlog | PASS |
| R-03 | Параллельные чтения/записи сохраняют целостность | existing transaction/concurrency suite на временной SQLite | PASS по автотестам; аварийный тест пользовательской БД — BLOCKED |
| R-04 | Lichess rate limits и Retry-After соблюдаются | fake HTTP 429/Retry-After, account-switch fences | PASS; реальный API не вызывался |
| R-05 | Fair Play fail-closed; движок не обходит активную человеческую игру | fake safety/preflight and game lifecycle Qt tests | PASS на fake data; реальные аккаунты/Stockfish — BLOCKED |
| VID-01…09 | Все девять наблюдений видеоревью учтены как кодовые/визуальные проверки без потери данных | Windows captures по состояниям Home, Library, Background, Play, review, clocks, settings; см. VID-01…09 test map предыдущей таблицы | Частично PASS; физическое воспроизведение видео/звук и manual interaction — BLOCKED |
| VOICE-01…02 | Home quick preview, ясные заголовки и подсказки | Home snapshot/UI tests and screenshots | PASS автоматизировано; визуальная приемка открыта |
| VOICE-03 | Короткая tooltip точности без потери provenance | `test_home_accuracy_tooltip_is_compact_but_accessible_description_keeps_provenance` | PASS |
| VOICE-04 | Карточки 5 режимов и условные настройки | `test_ui_pages.py` Play mode tests; capture `play` states | PASS автоматизировано; физический click-through — BLOCKED |
| VOICE-05 | Active games обновляются с безопасной TTL и показывают authoritative mini-board | fake active list/game event and stale account tests; capture `active-games` | PASS fake-only; live stream — BLOCKED |
| VOICE-06 | Ходовой звук не дублируется и ждёт успешного ACK | `test_online_move_sound_waits_for_authoritative_ack_and_deduplicates_replays`; settings/audio UI capture | PASS по логике; задержка реального устройства вывода — BLOCKED |
| VOICE-07 | Ручной Stockfish-analysis приоритетен, batch безопасно возобновляется | fair-play Qt cancellation/priority/resume tests | PASS fake engine; настоящий Stockfish не запускался |
| VOICE-08 | Чёткая причина фоновой паузы, manual-check не выглядит inert | `test_live_game_defers_only_fairness_sensitive_background_workflows`; handler asserts zero requests during live game | PASS fake-only |
| VIS-15 narrow layout | Карточки Background, кнопка диагностики и события не перекрываются; ready-status помещается на 720p/125%; открытие выбранной партии не теряет detail-worker | `test_background_diagnostics_toggle_does_not_overlap_recent_events`, `test_background_page_compact_layout_has_no_overlapping_cards_or_events`, `test_background_ready_status_fits_720p_and_keeps_full_schedule_accessible`, `test_library_initial_selection_requests_details_once`; visible synthetic capture strict geometry | Clipping исправлен; CI #397 выявил и подтвердил двойной detail request, исправлен и покрыт регрессией; новый exact-head CI требуется |
| VOICE-09 | Свежая игра получает приоритет, старый backlog не голодает; выбранная партия может получить due retry | `test_ready_analytics_queue_prioritizes_recent_games_without_starving_old_backlog`; scoped retry tests | PASS fake JSON/temp SQLite; настоящая публикация JSON на Lichess — BLOCKED |
| VOICE-10 | Home recent card открывает именно выбранную игру в Review | Home selected game/revision tests and capture | PASS; live account Fair Play remains enforced |
| PARKED | Полный граф путей приложения | Explicitly excluded by owner | НЕ ВЫПОЛНЯЛОСЬ |

**Ручная приёмка:** подтвердить физическую читаемость при DPI 100/125/150%, звуковую задержку на устройстве, live Lichess reconnect/official JSON and actual Stockfish lifecycle; проверить три доступных screenshot pairs. Автоматический и native capture не выдаются за ручной приём владельца.
