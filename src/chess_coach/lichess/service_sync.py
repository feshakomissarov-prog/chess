"""Archive, Puzzle Activity and ready-analysis synchronization workflows."""

from __future__ import annotations

import threading

from .api import LichessError
from .archive import ArchiveSync
from .puzzles import PuzzleActivitySync
from .auth import (CLIENT_ID, OAuthCallbackServer, WindowsCredentialStore,
                   authorize_and_store, create_authorization_request)

class LichessSyncMixin:
    def _emit_sync_event(self, context, signal_name: str, *payload) -> bool:
        if not self.is_operation_context_current(context):
            return False
        self.session_sync_event.emit(
            context.generation, context.request_id, signal_name, tuple(payload))
        # Preserve the public legacy signal API for external consumers. The
        # desktop shell subscribes to the generation-tagged event above.
        getattr(self, signal_name).emit(*payload)
        return True

    def _refresh_puzzle_player_rating(self, repo, username: str, token: str,
                                      stop: threading.Event) -> None:
        fetch = getattr(self.client, "get_puzzle_rating_history", None)
        if not callable(fetch) or stop.is_set():
            return
        account_id = repo.ensure_archive_account(username)
        if not repo.puzzle_player_rating_refresh_due(account_id):
            return
        try:
            points = fetch(username, token)
        except LichessError:
            return  # Preserve local history. The common gate still records 429.
        if not stop.is_set():
            repo.save_puzzle_player_rating(account_id, points)

    def cancel_archive_sync(self) -> None:
        if self._archive_stop is not None and not self._archive_stop.is_set():
            self._archive_stop.set()

    def pilot_puzzle_activity(self, limit: int, repository=None,
                              puzzle_token_store=None) -> None:
        """Explicitly import a bounded sample using the separate puzzle:read token."""
        context = self.capture_operation_context(command="puzzle_activity_pilot")
        repo = repository or self.repository
        if repo is None:
            self._emit_sync_event(context, "puzzle_pilot_finished", 0, 0, "", "Хранилище архива не подключено.")
            return
        if limit not in (5, 10):
            self._emit_sync_event(context, "puzzle_pilot_finished", 0, 0, "", "Можно загрузить только 5 или 10 попыток.")
            return
        if self._puzzle_stop is not None and not self._puzzle_stop.is_set():
            self._emit_sync_event(context, "puzzle_pilot_finished", 0, 0, "", "Загрузка истории задач уже выполняется.")
            return
        stop = threading.Event()
        self._puzzle_stop = stop

        def work():
            callback = None
            received = new_count = 0
            run_id = ""
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                store = puzzle_token_store
                if store is None:
                    from .auth import WindowsCredentialStore
                    store = WindowsCredentialStore(service="ChessCoach.Lichess.Puzzles",
                                                   user="puzzle-activity-token")
                puzzle_token = store.get()
                if puzzle_token:
                    username = str(self.client.get_account(puzzle_token)["username"])
                else:
                    callback = OAuthCallbackServer()
                    self._puzzle_callback = callback
                    request = create_authorization_request(callback.redirect_uri, CLIENT_ID,
                                                           scopes="puzzle:read")
                    if not webbrowser.open(request.url):
                        raise LichessError("Не удалось открыть браузер для разрешения puzzle:read.")
                    puzzle_token, username = authorize_and_store(self.client, store, callback, request)
                if stop.is_set():
                    self._emit_sync_event(context, "puzzle_pilot_finished", 0, 0, "", "Пробный импорт отменён.")
                    return
                self.puzzle_username = username
                sync = PuzzleActivitySync(self.client, repo)
                result = sync.run_pilot(
                    username, puzzle_token, stop, limit=limit,
                    progress=lambda count, added: self._emit_sync_event(
                        context, "puzzle_pilot_progress", count, added))
                received = int(result["received_count"])
                new_count = int(result["new_count"])
                run_id = str(result["run_id"])
                status = str(result["status"])
                message = {"complete": "Пробная загрузка задач завершена.",
                           "cancelled": "Пробная загрузка задач отменена.",
                           "error": "Пробная загрузка завершилась ошибкой."}.get(status, status)
                if result.get("error"):
                    message += " " + str(result["error"])
                self._emit_sync_event(context, "puzzle_pilot_finished", received, new_count, run_id, message)
            except Exception as exc:
                self._emit_sync_event(context, "puzzle_pilot_finished", received, new_count, run_id, str(exc))
            finally:
                if self._puzzle_stop is stop:
                    self._puzzle_stop = None
                if callback is not None and self._puzzle_callback is callback:
                    self._puzzle_callback = None
                if callback:
                    callback.close()
        self._thread(work, "chess-coach-puzzle-pilot")

    def sync_archive(self, *, full: bool = False, background: bool = False) -> None:
        """Sync archive in a worker; background runs never show modal dialogs."""
        context = self.capture_operation_context(command="archive_sync")
        if self.repository is None:
            if not background:
                self._emit_sync_event(context, "archive_finished", 0,
                                      "Хранилище архива не подключено.")
            return
        if self._archive_stop is not None and not self._archive_stop.is_set():
            if not background:
                self._emit_sync_event(context, "archive_finished", 0,
                                      "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop
        account_name = self.username
        def work():
            count = 0
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                token = context.credential_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = account_name or str(self.client.get_account(token)["username"])
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                sync = ArchiveSync(self.client, self.repository)
                count = sync.run(username, token, stop, full=full,
                                 progress=(None if background else
                                           lambda n, game_id: (
                                               self._emit_sync_event(context, "archive_progress", n, game_id)
                                               if not stop.is_set()
                                               else None)))
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                message = "Отменено." if stop.is_set() else f"Завершено, новых партий: {count}"
                if background:
                    self._emit_sync_event(context, "background_sync_finished", "партий", message)
                else:
                    self._emit_sync_event(context, "archive_finished", count, message)
            except Exception as exc:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                if background:
                    self._emit_sync_event(context, "background_sync_finished",
                                          "партий", f"Ошибка: {exc}")
                else:
                    self._emit_sync_event(context, "archive_finished", count, str(exc))
            finally:
                if self._archive_stop is stop:
                    self._archive_stop = None
        self._thread(work, "chess-coach-archive-sync")

    def sync_puzzle_activity_background(self, repository=None) -> None:
        """Use an existing puzzle:read token only; never start OAuth automatically."""
        repo = repository or self.repository
        if (repo is None or (self._puzzle_stop is not None and not self._puzzle_stop.is_set())
                or not self.username):
            return
        stop = threading.Event()
        self._puzzle_stop = stop
        context = self.capture_operation_context(command="puzzle_activity_background")
        expected_username = self.username
        def work():
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                store = WindowsCredentialStore(
                    service="ChessCoach.Lichess.Puzzles", user="puzzle-activity-token")
                token = store.get()
                if not token:
                    return  # First-time authorization requires a user click.
                username = str(self.client.get_account(token)["username"])
                if (stop.is_set() or not self.is_operation_context_current(context)
                        or username.casefold() != (expected_username or "").casefold()):
                    if (not stop.is_set() and self.is_operation_context_current(context)
                            and username.casefold() != (expected_username or "").casefold()):
                        self._emit_sync_event(context, "background_sync_finished",
                                              "задач", "Аккаунты не совпадают; обновление пропущено")
                    return
                count = PuzzleActivitySync(self.client, repo).run(
                    username, token, stop)
                self._refresh_puzzle_player_rating(repo, username, token, stop)
                if not stop.is_set() and self.is_operation_context_current(context):
                    self._emit_sync_event(context, "background_sync_finished",
                                          "задач", f"Завершено, новых попыток: {count}")
            except Exception as exc:
                if not stop.is_set() and self.is_operation_context_current(context):
                    self._emit_sync_event(context, "background_sync_finished",
                                          "задач", f"Ошибка: {exc}")
            finally:
                if self._puzzle_stop is stop:
                    self._puzzle_stop = None
        self._thread(work, "chess-coach-puzzle-background")

    def cancel_puzzle_sync(self) -> None:
        if self._puzzle_stop is not None:
            self._puzzle_stop.set()
        if self._puzzle_callback is not None:
            self._puzzle_callback.cancel()

    def connect_and_sync_puzzle_activity(self, repository=None,
                                         puzzle_token_store=None) -> None:
        """Explicitly request puzzle:read in a separate credential slot, then sync history."""
        context = self.capture_operation_context(command="puzzle_activity_sync")
        repo = repository or self.repository
        if repo is None:
            self._emit_sync_event(context, "puzzles_finished", 0, "Хранилище архива не подключено.")
            return
        if self._puzzle_stop is not None and not self._puzzle_stop.is_set():
            self._emit_sync_event(context, "puzzles_finished", 0, "Загрузка истории задач уже выполняется.")
            return
        stop = threading.Event()
        self._puzzle_stop = stop
        def work():
            callback = None
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                store = puzzle_token_store
                if store is None:
                    from .auth import WindowsCredentialStore
                    store = WindowsCredentialStore(service="ChessCoach.Lichess.Puzzles",
                                                   user="puzzle-activity-token")
                puzzle_token = store.get()
                if puzzle_token:
                    username = str(self.client.get_account(puzzle_token)["username"])
                else:
                    callback = OAuthCallbackServer()
                    self._puzzle_callback = callback
                    request = create_authorization_request(callback.redirect_uri, CLIENT_ID,
                                                           scopes="puzzle:read")
                    if not webbrowser.open(request.url):
                        raise LichessError("Не удалось открыть браузер для разрешения puzzle:read.")
                    puzzle_token, username = authorize_and_store(self.client, store, callback, request)
                if stop.is_set():
                    self._emit_sync_event(context, "puzzles_finished", 0, "Синхронизация отменена.")
                    return
                self.puzzle_username = username
                account_id = repo.ensure_archive_account(username)
                sync_mode = ("incremental" if repo.puzzle_sync_state(account_id).get("backfill_complete")
                             else "initial backfill")
                self._emit_sync_event(context, "puzzles_started", sync_mode)
                sync = PuzzleActivitySync(self.client, repo)
                count = sync.run(username, puzzle_token, stop,
                                 progress=lambda n: self._emit_sync_event(context, "puzzles_progress", n),
                                 scanned=lambda n: self._emit_sync_event(context, "puzzles_scanned", n))
                self._refresh_puzzle_player_rating(repo, username, puzzle_token, stop)
                status = ("Синхронизация отменена." if stop.is_set()
                          else "История задач синхронизирована.")
                if not stop.is_set() and count > 0:
                    try:
                        dashboard = self.client.get_puzzle_dashboard(puzzle_token, 30)
                        repo.save_puzzle_dashboard(account_id, 30, dashboard)
                    except Exception:
                        status += " Сводка Lichess недоступна; локальная статистика сохранена."
                elif not stop.is_set():
                    status += " Новых попыток нет; сводный endpoint не запрашивался повторно."
                self._emit_sync_event(context, "puzzles_finished", count, status)
            except Exception as exc:
                self._emit_sync_event(context, "puzzles_finished", 0, str(exc))
            finally:
                if self._puzzle_stop is stop:
                    self._puzzle_stop = None
                if callback is not None and self._puzzle_callback is callback:
                    self._puzzle_callback = None
                if callback:
                    callback.close()
        self._thread(work, "chess-coach-puzzle-sync")

    def refresh_archive_game(self, game_id: str) -> None:
        """Explicitly refresh one completed, already-linked game; never fetch the archive."""
        context = self.capture_operation_context(game_id=game_id,
                                                 command="refresh_archive_game")
        if self.repository is None:
            self._emit_sync_event(context, "archive_game_refresh_finished",
                                  game_id, "Локальное хранилище недоступно.")
            return
        def work():
            try:
                if not self.is_operation_context_current(context):
                    return
                token = context.credential_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                if not self.is_operation_context_current(context):
                    return
                ArchiveSync(self.client, self.repository).refresh_game(username, token, game_id)
                self._emit_sync_event(context, "archive_game_refresh_finished",
                                      game_id, "История этой партии обновлена из Lichess.")
            except Exception as exc:
                self._emit_sync_event(context, "archive_game_refresh_finished", game_id, str(exc))
        self._thread(work, "chess-coach-archive-game-refresh")

    def pilot_import_archive(self, *, max_games: int = 5) -> None:
        """Run one explicitly requested 5–10 game sample, separate from backfill."""
        context = self.capture_operation_context(command="archive_pilot")
        if self.repository is None:
            self._emit_sync_event(context, "archive_pilot_finished", 0, 0,
                                  "Локальное хранилище архива недоступно.")
            return
        if self._archive_stop is not None and not self._archive_stop.is_set():
            self._emit_sync_event(context, "archive_pilot_finished", 0, 0,
                                  "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop
        def work():
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                token = context.credential_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                result = ArchiveSync(self.client, self.repository).run_pilot(
                    username, token, stop, max_games=max_games,
                    progress=lambda received, new, game_id:
                    self._emit_sync_event(context, "archive_pilot_progress", received, new, game_id))
                message = ("Пробный импорт отменён; уже записанные партии сохранены."
                           if result["status"] == "interrupted" else "Пробный импорт завершён.")
                self._emit_sync_event(context, "archive_pilot_finished",
                                      int(result["received"]), int(result["new"]), message)
            except Exception as exc:
                received = new = 0
                if self.username:
                    account_id = f"lichess:{self.username.strip().lower()}"
                    runs = self.repository.list_archive_pilot_runs(account_id, limit=1)
                    if runs:
                        received, new = int(runs[0]["received_count"]), int(runs[0]["new_count"])
                self._emit_sync_event(context, "archive_pilot_finished", received, new,
                                      f"Пробный импорт завершился ошибкой: {exc}")
            finally:
                if self._archive_stop is stop:
                    self._archive_stop = None
        self._thread(work, "chess-coach-archive-pilot")

    def refresh_lichess_analysis_background(self, *, generation: int,
                                            limit: int = 10,
                                            daily_limit: int = 25,
                                            force_retry: bool = False) -> None:
        """Resume one small, authenticated JSON backfill batch on the current account."""
        if (self.repository is None or not self.username or self._closing
                or not self.is_auth_generation_current(generation)):
            return
        context = self.capture_operation_context(command="lichess_analysis_backfill")
        if context.generation != generation:
            return
        if self._analysis_backfill_stop is not None and not self._analysis_backfill_stop.is_set():
            return
        stop = threading.Event()
        self._analysis_backfill_stop = stop
        username = self.username

        def work():
            try:
                if not self.is_auth_generation_current(generation) or stop.is_set():
                    return
                if not self.is_operation_context_current(context):
                    return
                token = context.credential_store.get()
                if not token:
                    return
                account_id = self.repository.ensure_archive_account(username)
                if not self.repository.lichess_analysis_backfill_has_due_work(
                        account_id, daily_limit=daily_limit,
                        force_retry=force_retry):
                    # Avoid spending an account/playing API request when the
                    # durable local queue is paused, complete, cooling down,
                    # or has already reached its daily request budget.
                    summary = self.repository.lichess_analysis_backfill_summary(account_id)
                    retry_at = self.repository.lichess_analysis_retry_after(account_id)
                    next_eligible = self.repository.lichess_analysis_backfill_next_eligible_at(
                        account_id, daily_limit=daily_limit, force_retry=force_retry) or ""
                    message = ("приостановлено" if self.repository.lichess_analysis_backfill_paused(account_id)
                               else "пауза · Retry-After" if retry_at
                               else "очередь готовой аналитики пуста")
                    self.lichess_analysis_backfill_finished.emit(
                        generation, username,
                        int(summary.get("checked") or 0), int(summary.get("total") or 0),
                        int(summary.get("accuracy") or 0), int(summary.get("evals") or 0),
                        int(summary.get("no_data") or 0), int(summary.get("errors") or 0),
                        message, next_eligible)
                    return
                active_games = self.client.get_playing_games(token)
                from .session import classify_lichess_opponent
                if any(classify_lichess_opponent(game) != "official_ai"
                       for game in active_games):
                    next_eligible = self.repository.lichess_analysis_backfill_next_eligible_at(
                        account_id, daily_limit=daily_limit) or ""
                    self.lichess_analysis_backfill_finished.emit(
                        generation, username,
                        0, 0, 0, 0, 0, 0,
                        "отложено · есть человеческая/неизвестная активная партия", next_eligible)
                    return
                report = ArchiveSync(self.client, self.repository).refresh_ready_analytics(
                    username, token, stop, limit=max(1, min(10, int(limit))),
                    daily_limit=max(1, min(100, int(daily_limit))),
                    force_retry=force_retry,
                    generation_is_current=lambda: (
                        self.is_operation_context_current(context) and not self._closing),
                    progress=lambda done, total, game: self.lichess_analysis_backfill_progress.emit(
                        generation, username, done, total, game))
                fatal_status = report.get("fatal_status")
                if fatal_status in {401, 403}:
                    account_id = self.repository.ensure_archive_account(username)
                    self.repository.set_lichess_analysis_backfill_paused(account_id, True)
                    self.background_operation_status.emit(
                        generation, "ready_analytics", int(fatal_status),
                        f"Очередь готовой аналитики приостановлена (HTTP {fatal_status}).")
                elif fatal_status == 429:
                    self.background_operation_status.emit(
                        generation, "ready_analytics", 429,
                        f"Пауза API; повтор не раньше чем через {self.client.rate_limit_remaining} с.")
                if not self.is_operation_context_current(context):
                    return
                summary = self.repository.lichess_analysis_backfill_summary(
                    self.repository.ensure_archive_account(username))
                next_eligible = self.repository.lichess_analysis_backfill_next_eligible_at(
                    account_id, daily_limit=daily_limit) or ""
                message = (f"пауза · HTTP {fatal_status}" if fatal_status in {401, 403} else
                           "пауза · Lichess ограничил запросы" if fatal_status == 429 else
                           "пауза · Lichess ограничил запросы" if int(report.get("errors", 0))
                           and self.client.rate_limit_remaining else
                           "пауза · суточный лимит достигнут" if report.get("limited") else
                           "приостановлено" if report.get("paused") else "проверка завершена")
                self.lichess_analysis_backfill_finished.emit(
                    generation, username,
                    int(summary.get("checked") or 0), int(summary.get("total") or 0),
                    int(summary.get("accuracy") or 0),
                    int(summary.get("evals") or 0), int(summary.get("no_data") or 0),
                    int(summary.get("errors") or 0), message, next_eligible)
            except Exception as exc:
                if self.is_operation_context_current(context):
                    status = getattr(exc, "status", None)
                    account_id = self.repository.ensure_archive_account(username)
                    if status == 429:
                        retry_after = max(60, int(getattr(exc, "retry_after", 60)))
                        self.repository.set_lichess_analysis_retry_after(account_id, retry_after)
                        message = f"пауза · HTTP 429 · повтор не раньше чем через {retry_after} с"
                        self.background_operation_status.emit(
                            generation, "ready_analytics", 429,
                            f"Пауза API; повтор не раньше чем через {retry_after} с.")
                    elif status in {401, 403}:
                        self.repository.set_lichess_analysis_backfill_paused(account_id, True)
                        message = f"пауза · HTTP {status}"
                        self.background_operation_status.emit(
                            generation, "ready_analytics", int(status),
                            f"Очередь готовой аналитики приостановлена (HTTP {status}).")
                    else:
                        message = f"ошибка · {type(exc).__name__}"
                    summary = self.repository.lichess_analysis_backfill_summary(account_id)
                    next_eligible = self.repository.lichess_analysis_backfill_next_eligible_at(
                        account_id, daily_limit=daily_limit) or ""
                    self.lichess_analysis_backfill_finished.emit(
                        generation, username,
                        int(summary.get("checked") or 0), int(summary.get("total") or 0),
                        int(summary.get("accuracy") or 0), int(summary.get("evals") or 0),
                        int(summary.get("no_data") or 0), int(summary.get("errors") or 0),
                        message, next_eligible)
            finally:
                if self._analysis_backfill_stop is stop:
                    self._analysis_backfill_stop = None
        self._thread(work, "chess-coach-lichess-analysis-backfill")

    def refresh_missing_archive_evaluations(self, *, limit: int = 25) -> None:
        """Explicit, bounded refresh of missing Lichess PGN evals using saved OAuth only."""
        context = self.capture_operation_context(command="refresh_archive_evaluations")
        if self.repository is None:
            self._emit_sync_event(context, "archive_eval_refresh_finished", 0, 0, "Архив недоступен.")
            return
        if self._archive_stop is not None and not self._archive_stop.is_set():
            self._emit_sync_event(context, "archive_eval_refresh_finished", 0, 0,
                                  "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop

        def work():
            refreshed = selected = 0
            try:
                if stop.is_set() or not self.is_operation_context_current(context):
                    return
                token = context.credential_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                result = ArchiveSync(self.client, self.repository).refresh_missing_evaluations(
                    username, token, stop, limit=limit,
                    progress=lambda done, total, game_id:
                    self._emit_sync_event(context, "archive_eval_refresh_progress", done, total, game_id))
                refreshed = int(result["refreshed"])
                selected = int(result["selected"])
                message = ("Обновление оценок отменено." if result["cancelled"] else
                           "Оценки Lichess обновлены.")
                self._emit_sync_event(context, "archive_eval_refresh_finished",
                                      refreshed, selected, message)
            except Exception as exc:
                self._emit_sync_event(context, "archive_eval_refresh_finished",
                                      refreshed, selected, str(exc))
            finally:
                if self._archive_stop is stop:
                    self._archive_stop = None

        self._thread(work, "chess-coach-archive-eval-refresh")

    def cancel_lichess_analysis_backfill(self) -> None:
        if self._analysis_backfill_stop is not None:
            self._analysis_backfill_stop.set()
