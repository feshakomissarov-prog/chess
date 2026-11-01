"""Background OAuth, challenge, and NDJSON orchestration for the Qt UI."""

from __future__ import annotations

import threading
import random
import time
import webbrowser

from PySide6.QtCore import QObject, Signal

from .api import LichessClient, LichessError, RateLimitError
from .archive import ArchiveSync
from .puzzles import PuzzleActivitySync
from .auth import (CLIENT_ID, OAuthCallbackServer, WindowsCredentialStore,
                   authorize_and_store, create_authorization_request)


class LichessService(QObject):
    status_changed = Signal(str)
    event_received = Signal(dict)
    game_event_received = Signal(str, dict)
    challenge_ready = Signal(dict)
    challenge_status_changed = Signal(str, bool)
    invitation_cancelled = Signal(str)
    active_games_received = Signal(object)
    active_games_error = Signal(str)
    analysis_safety_games_received = Signal(str, object)
    analysis_safety_error = Signal(str, str)
    error = Signal(str)
    move_failed = Signal(str, str, str, int)
    takeback_action_completed = Signal(str, str, int)
    authenticated = Signal(str)
    archive_progress = Signal(int, str)
    archive_finished = Signal(int, str)
    archive_pilot_progress = Signal(int, int, str)
    archive_pilot_finished = Signal(int, int, str)
    archive_game_refresh_finished = Signal(str, str)
    archive_eval_refresh_progress = Signal(int, int, str)
    archive_eval_refresh_finished = Signal(int, int, str)
    puzzles_progress = Signal(int)
    puzzles_scanned = Signal(int)
    puzzles_finished = Signal(int, str)
    background_sync_finished = Signal(str, str)
    puzzle_pilot_progress = Signal(int, int)
    puzzle_pilot_finished = Signal(int, int, str, str)

    def __init__(self, client: LichessClient | None = None, token_store=None,
                 repository=None, parent=None):
        super().__init__(parent)
        self.client = client or LichessClient()
        self.token_store = token_store or WindowsCredentialStore()
        self.repository = repository
        self._archive_stop: threading.Event | None = None
        self._puzzle_stop: threading.Event | None = None
        self._puzzle_callback: OAuthCallbackServer | None = None
        self._stop = threading.Event()
        self._connection_stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._thread_lock = threading.Lock()
        self._closing = False
        self._game_stops: dict[str, threading.Event] = {}
        self._final_sync_game_ids: set[str] = set()
        self._event_lock = threading.Lock()
        self._restore_lock = threading.Lock()
        self._restore_running = False
        self._event_stream_started = False
        self.username: str | None = None
        self.puzzle_username: str | None = None
        self._callback: OAuthCallbackServer | None = None
        self._username_challenge_stop: threading.Event | None = None
        self._username_challenge_id: str | None = None

    def sync_archive(self, *, full: bool = False, background: bool = False) -> None:
        """Sync archive in a worker; background runs never show modal dialogs."""
        if self.repository is None:
            if not background:
                self.archive_finished.emit(0, "Хранилище архива не подключено.")
            return
        if self._archive_stop is not None:
            if not background:
                self.archive_finished.emit(0, "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop
        def work():
            count = 0
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                sync = ArchiveSync(self.client, self.repository)
                count = sync.run(username, token, stop, full=full,
                                 progress=(None if background else
                                           lambda n, game_id: self.archive_progress.emit(n, game_id)))
                message = "Отменено." if stop.is_set() else f"Завершено, новых партий: {count}"
                if background:
                    self.background_sync_finished.emit("партий", message)
                else:
                    self.archive_finished.emit(count, message)
            except Exception as exc:
                if background:
                    self.background_sync_finished.emit("партий", f"Ошибка: {exc}")
                else:
                    self.archive_finished.emit(count, str(exc))
            finally:
                self._archive_stop = None
        self._thread(work, "chess-coach-archive-sync")

    def pilot_import_archive(self, *, max_games: int = 5) -> None:
        """Run one explicitly requested 5–10 game sample, separate from backfill."""
        if self.repository is None:
            self.archive_pilot_finished.emit(0, 0, "Локальное хранилище архива недоступно.")
            return
        if self._archive_stop is not None and not self._archive_stop.is_set():
            self.archive_pilot_finished.emit(0, 0, "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                result = ArchiveSync(self.client, self.repository).run_pilot(
                    username, token, stop, max_games=max_games,
                    progress=lambda received, new, game_id:
                    self.archive_pilot_progress.emit(received, new, game_id))
                message = ("Пробный импорт отменён; уже записанные партии сохранены."
                           if result["status"] == "interrupted" else "Пробный импорт завершён.")
                self.archive_pilot_finished.emit(int(result["received"]), int(result["new"]), message)
            except Exception as exc:
                received = new = 0
                if self.username:
                    account_id = f"lichess:{self.username.strip().lower()}"
                    runs = self.repository.list_archive_pilot_runs(account_id, limit=1)
                    if runs:
                        received, new = int(runs[0]["received_count"]), int(runs[0]["new_count"])
                self.archive_pilot_finished.emit(received, new, f"Пробный импорт завершился ошибкой: {exc}")
            finally:
                self._archive_stop = None
        self._thread(work, "chess-coach-archive-pilot")

    def cancel_archive_sync(self) -> None:
        if self._archive_stop is not None:
            self._archive_stop.set()

    def refresh_archive_game(self, game_id: str) -> None:
        """Explicitly refresh one completed, already-linked game; never fetch the archive."""
        if self.repository is None:
            self.archive_game_refresh_finished.emit(game_id, "Локальное хранилище недоступно.")
            return
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                ArchiveSync(self.client, self.repository).refresh_game(username, token, game_id)
                self.archive_game_refresh_finished.emit(
                    game_id, "История этой партии обновлена из Lichess.")
            except Exception as exc:
                self.archive_game_refresh_finished.emit(game_id, str(exc))
        self._thread(work, "chess-coach-archive-game-refresh")

    def refresh_missing_archive_evaluations(self, *, limit: int = 25) -> None:
        """Explicit, bounded refresh of missing Lichess PGN evals using saved OAuth only."""
        if self.repository is None:
            self.archive_eval_refresh_finished.emit(0, 0, "Архив недоступен.")
            return
        if self._archive_stop is not None and not self._archive_stop.is_set():
            self.archive_eval_refresh_finished.emit(0, 0, "Синхронизация архива уже выполняется.")
            return
        stop = threading.Event()
        self._archive_stop = stop

        def work():
            refreshed = selected = 0
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                username = self.username or str(self.client.get_account(token)["username"])
                result = ArchiveSync(self.client, self.repository).refresh_missing_evaluations(
                    username, token, stop, limit=limit,
                    progress=lambda done, total, game_id:
                    self.archive_eval_refresh_progress.emit(done, total, game_id))
                refreshed = int(result["refreshed"])
                selected = int(result["selected"])
                message = ("Обновление оценок отменено." if result["cancelled"] else
                           "Оценки Lichess обновлены.")
                self.archive_eval_refresh_finished.emit(refreshed, selected, message)
            except Exception as exc:
                self.archive_eval_refresh_finished.emit(refreshed, selected, str(exc))
            finally:
                self._archive_stop = None

        self._thread(work, "chess-coach-archive-eval-refresh")

    def connect_and_sync_puzzle_activity(self, repository=None,
                                         puzzle_token_store=None) -> None:
        """Explicitly request puzzle:read in a separate credential slot, then sync history."""
        repo = repository or self.repository
        if repo is None:
            self.puzzles_finished.emit(0, "Хранилище архива не подключено.")
            return
        if self._puzzle_stop is not None and not self._puzzle_stop.is_set():
            self.puzzles_finished.emit(0, "Загрузка истории задач уже выполняется.")
            return
        stop = threading.Event()
        self._puzzle_stop = stop
        def work():
            callback = None
            try:
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
                    self.puzzles_finished.emit(0, "Синхронизация отменена.")
                    return
                self.puzzle_username = username
                sync = PuzzleActivitySync(self.client, repo)
                count = sync.run(username, puzzle_token, stop,
                                 progress=lambda n: self.puzzles_progress.emit(n),
                                 scanned=lambda n: self.puzzles_scanned.emit(n))
                status = "Синхронизация отменена." if stop.is_set() else "История задач синхронизирована."
                if not stop.is_set():
                    try:
                        account_id = repo.ensure_archive_account(username)
                        dashboard = self.client.get_puzzle_dashboard(puzzle_token, 30)
                        repo.save_puzzle_dashboard(account_id, 30, dashboard)
                    except Exception:
                        status += " Сводка Lichess недоступна; локальная статистика сохранена."
                self.puzzles_finished.emit(count, status)
            except Exception as exc:
                self.puzzles_finished.emit(0, str(exc))
            finally:
                self._puzzle_stop = None
                self._puzzle_callback = None
                if callback:
                    callback.close()
        self._thread(work, "chess-coach-puzzle-sync")

    def sync_puzzle_activity_background(self, repository=None) -> None:
        """Use an existing puzzle:read token only; never start OAuth automatically."""
        repo = repository or self.repository
        if repo is None or self._puzzle_stop is not None or not self.username:
            return
        stop = threading.Event()
        self._puzzle_stop = stop
        def work():
            try:
                store = WindowsCredentialStore(
                    service="ChessCoach.Lichess.Puzzles", user="puzzle-activity-token")
                token = store.get()
                if not token:
                    return  # First-time authorization requires a user click.
                username = str(self.client.get_account(token)["username"])
                if username.casefold() != (self.username or "").casefold():
                    self.background_sync_finished.emit(
                        "задач", "Аккаунты не совпадают; обновление пропущено")
                    return
                count = PuzzleActivitySync(self.client, repo).run(
                    username, token, stop)
                if not stop.is_set():
                    self.background_sync_finished.emit(
                        "задач", f"Завершено, новых попыток: {count}")
            except Exception as exc:
                self.background_sync_finished.emit("задач", f"Ошибка: {exc}")
            finally:
                self._puzzle_stop = None
        self._thread(work, "chess-coach-puzzle-background")

    def cancel_puzzle_sync(self) -> None:
        if self._puzzle_stop is not None:
            self._puzzle_stop.set()
        if self._puzzle_callback is not None:
            self._puzzle_callback.cancel()

    def pilot_puzzle_activity(self, limit: int, repository=None,
                              puzzle_token_store=None) -> None:
        """Explicitly import a bounded sample using the separate puzzle:read token."""
        repo = repository or self.repository
        if repo is None:
            self.puzzle_pilot_finished.emit(0, 0, "", "Хранилище архива не подключено.")
            return
        if limit not in (5, 10):
            self.puzzle_pilot_finished.emit(0, 0, "", "Можно загрузить только 5 или 10 попыток.")
            return
        if self._puzzle_stop is not None and not self._puzzle_stop.is_set():
            self.puzzle_pilot_finished.emit(0, 0, "", "Загрузка истории задач уже выполняется.")
            return
        stop = threading.Event()
        self._puzzle_stop = stop

        def work():
            callback = None
            received = new_count = 0
            run_id = ""
            try:
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
                    self.puzzle_pilot_finished.emit(0, 0, "", "Пробный импорт отменён.")
                    return
                self.puzzle_username = username
                sync = PuzzleActivitySync(self.client, repo)
                result = sync.run_pilot(
                    username, puzzle_token, stop, limit=limit,
                    progress=lambda count, added: self.puzzle_pilot_progress.emit(count, added))
                received = int(result["received_count"])
                new_count = int(result["new_count"])
                run_id = str(result["run_id"])
                status = str(result["status"])
                message = {"complete": "Пробная загрузка задач завершена.",
                           "cancelled": "Пробная загрузка задач отменена.",
                           "error": "Пробная загрузка завершилась ошибкой."}.get(status, status)
                if result.get("error"):
                    message += " " + str(result["error"])
                self.puzzle_pilot_finished.emit(received, new_count, run_id, message)
            except Exception as exc:
                self.puzzle_pilot_finished.emit(received, new_count, run_id, str(exc))
            finally:
                self._puzzle_stop = None
                self._puzzle_callback = None
                if callback:
                    callback.close()
        self._thread(work, "chess-coach-puzzle-pilot")

    @property
    def token(self) -> str | None:
        return self.token_store.get()

    def _thread(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        with self._thread_lock:
            if self._closing:
                return
            self._threads.append(thread)
            thread.start()

    def restore(self) -> None:
        with self._restore_lock:
            if self._restore_running:
                return
            self._restore_running = True
        self.status_changed.emit("Проверяем подключение…")
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    self.status_changed.emit("Не подключено")
                    return
                account = self.client.get_account(token)
                self.username = str(account["username"])
                self.authenticated.emit(self.username)
                self._start_event_stream(token)
            except Exception as exc:
                if isinstance(exc, LichessError) and exc.status == 401:
                    self.token_store.delete()
                self.error.emit(str(exc))
                if isinstance(exc, RateLimitError) or (
                    isinstance(exc, LichessError) and exc.status == 429
                ):
                    self.status_changed.emit(
                        f"Ограничение Lichess (429): повторная попытка не раньше чем через {max(60, getattr(exc, 'retry_after', 60))} с"
                    )
                else:
                    self.status_changed.emit("Ошибка подключения")
            finally:
                with self._restore_lock:
                    self._restore_running = False
        self._thread(work, "chess-coach-restore-auth")

    def fetch_active_games(self) -> None:
        """Fetch a single account/playing snapshot on explicit user request."""
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                games = self.client.get_playing_games(token)
                self.active_games_received.emit(games)
            except Exception as exc:
                self.active_games_error.emit(str(exc))

        self._thread(work, "chess-coach-active-games")

    def check_active_games_for_analysis(self, request_id: str) -> None:
        """Take one explicit server snapshot before a user-started local analysis."""
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Токен Lichess недоступен; безопасность анализа не подтверждена.")
                games = self.client.get_playing_games(token)
                self.analysis_safety_games_received.emit(request_id, games)
            except Exception as exc:
                self.analysis_safety_error.emit(request_id, str(exc))

        self._thread(work, "chess-coach-analysis-safety")

    def connect_account(self) -> None:
        def work():
            callback = None
            try:
                callback = OAuthCallbackServer()
                self._callback = callback
                request = create_authorization_request(callback.redirect_uri, CLIENT_ID)
                if not webbrowser.open(request.url):
                    raise LichessError("Не удалось открыть браузер для авторизации Lichess.")
                self.status_changed.emit("Ожидание авторизации в браузере…")
                _, username = authorize_and_store(self.client, self.token_store, callback, request)
                self.username = username
                self._connection_stop = threading.Event()
                self.authenticated.emit(username)
                self._start_event_stream(self.token_store.get())
                self._callback = None
            except Exception as exc:
                if callback:
                    callback.close()
                    self._callback = None
                self.error.emit(str(exc))
                self.status_changed.emit("Не подключено")
        self._thread(work, "chess-coach-oauth")

    def disconnect_account(self) -> None:
        # A disconnect must cancel ongoing background requests immediately,
        # before the token is revoked by the worker.
        if self._archive_stop is not None:
            self._archive_stop.set()
        if self._puzzle_stop is not None:
            self._puzzle_stop.set()
        def work():
            self._connection_stop.set()
            if self._username_challenge_stop:
                self._username_challenge_stop.set()
            for stop in self._game_stops.values():
                stop.set()
            try:
                token = self.token_store.get()
                if token:
                    try:
                        self.client.revoke_token(token)
                    finally:
                        self.token_store.delete()
                self.username = None
                self.status_changed.emit("Не подключено")
            except Exception as exc:
                self.error.emit(str(exc))
        self._thread(work, "chess-coach-disconnect")

    def _start_event_stream(self, token: str | None) -> None:
        if not token:
            return
        connection_stop = self._connection_stop
        with self._event_lock:
            if self._event_stream_started:
                return
            self._event_stream_started = True
        self.status_changed.emit(f"Подключено: {self.username or 'Lichess'}")

        def work():
            delay = 1
            try:
                while not self._stop.is_set() and not connection_stop.is_set():
                    try:
                        for event in self.client.stream_events(token, connection_stop):
                            delay = 1
                            if self._stop.is_set() or connection_stop.is_set():
                                break
                            self.event_received.emit(event)
                            if event.get("type") == "gameStart":
                                game_id = event.get("game", {}).get("gameId")
                                if game_id:
                                    self.start_game(str(game_id), token)
                        if not self._stop.is_set() and not connection_stop.is_set():
                            raise LichessError("Поток событий Lichess завершился.")
                    except RateLimitError as exc:
                        self.status_changed.emit(
                            f"Ограничение Lichess (429): переподключение через {max(60, exc.retry_after)} с"
                        )
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if exc.status == 401:
                            self.token_store.delete()
                            connection_stop.set()
                            self.error.emit(str(exc))
                            break
                        self.status_changed.emit("Переподключение…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
                self.status_changed.emit("Не подключено")
            finally:
                with self._event_lock:
                    self._event_stream_started = False
                # A fast account switch may have installed a new connection stop
                # event while this worker was still unwinding. Start exactly one
                # stream for that current connection after releasing the old one.
                current_connection_stop = self._connection_stop
                if not self._stop.is_set() and not current_connection_stop.is_set():
                    try:
                        self._start_event_stream(self.token_store.get())
                    except Exception:
                        pass
        self._thread(work, "chess-coach-lichess-events")

    def start_game(self, game_id: str, token: str | None = None) -> None:
        token = token or self.token
        if not token:
            return
        stop = threading.Event()
        connection_stop = self._connection_stop
        with self._event_lock:
            if game_id in self._game_stops:
                return
            self._game_stops[game_id] = stop

        def work():
            delay = 1
            final_attempts = 0
            try:
                while not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                    try:
                        for event in self.client.stream_game(token, game_id, stop):
                            if self._stop.is_set() or connection_stop.is_set() or stop.is_set():
                                break
                            delay = 1
                            self.game_event_received.emit(game_id, event)
                            state = event.get("state", event)
                            is_terminal = event.get("type") in {"gameFull", "gameState"} and state.get("status") not in {None, "started", "created", "started_soon", "playing"}
                            if is_terminal:
                                with self._event_lock:
                                    self._final_sync_game_ids.discard(game_id)
                            if event.get("type") == "gameFull" and is_terminal:
                                stop.set()
                            if event.get("type") == "gameState" and is_terminal:
                                stop.set()
                        if not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                            raise LichessError("Поток партии завершился.")
                    except RateLimitError as exc:
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self.status_changed.emit(f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self.status_changed.emit(
                            f"Ограничение Lichess (429): синхронизация партии через {max(60, exc.retry_after)} с"
                        )
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if exc.status == 401:
                            self.token_store.delete()
                            connection_stop.set()
                            self.error.emit(str(exc))
                            break
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self.status_changed.emit(f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self.status_changed.emit("Потеряно соединение с партией; синхронизация…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
            finally:
                with self._event_lock:
                    if self._game_stops.get(game_id) is stop:
                        self._game_stops.pop(game_id, None)
        self._thread(work, f"chess-coach-game-{game_id}")

    def mark_game_finished(self, game_id: str) -> None:
        """Allow a bounded stream resync to fetch the final authoritative line."""
        with self._event_lock:
            self._final_sync_game_ids.add(game_id)

    def open_friend_game(self, color: str, clock_limit: int | None, increment: int = 0, rated: bool = False):
        return self._challenge(lambda token: self._create_open(token, color, clock_limit, increment, rated))

    def _create_open(self, token, color, clock_limit, increment, rated):
        if color == "random":
            color = random.choice(("white", "black"))
        result = self.client.create_open_challenge(token, color, rated, clock_limit, increment)
        challenge_id = result.get("id")
        time_control = result.get("timeControl") or {}
        expected_type = "unlimited" if clock_limit is None else "clock"
        valid_control = time_control.get("type") == expected_type
        if clock_limit is not None:
            try:
                valid_control = (valid_control and int(time_control.get("limit", -1)) == clock_limit
                                 and int(time_control.get("increment", -1)) == increment)
            except (TypeError, ValueError):
                valid_control = False
        valid_control = bool(valid_control and challenge_id)
        if not valid_control or (rated and not result.get("rated", False)):
            if challenge_id:
                try:
                    self.client.cancel_challenge(token, challenge_id)
                except LichessError:
                    pass
            raise LichessError(
                "Lichess не подтвердил запрошенный режим часов или rated для приглашения. "
                "Вызов не принят и не будет передан другу. Проверьте ответ API/ограничения аккаунта."
            )
        game_color = color
        friend_url = (result.get("blackUrl") or result.get("urlBlack")) if game_color == "white" else (result.get("whiteUrl") or result.get("urlWhite"))
        if not friend_url:
            if challenge_id:
                try:
                    self.client.cancel_challenge(token, challenge_id)
                except LichessError:
                    pass
            raise LichessError("Ответ Lichess не содержит ссылку для второго игрока; приглашение отменено.")
        accepted = self.client.accept_open_challenge(token, challenge_id, game_color)
        result["accept"] = accepted
        result["friendUrl"] = friend_url
        return result

    def create_ai_game(self, level: int, color: str, clock_limit: int | None, increment: int = 0):
        return self._challenge(lambda token: self.client.create_ai_challenge(token, level, color, clock_limit, increment))

    def cancel_open_invitation(self, challenge_id: str):
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Аккаунт Lichess не подключён.")
                self.client.cancel_challenge(token, challenge_id)
                self.invitation_cancelled.emit(challenge_id)
            except Exception as exc:
                self.error.emit(str(exc))
        self._thread(work, "chess-coach-cancel-invitation")

    def challenge_username(self, username: str, color: str, clock_limit: int | None, increment: int = 0, rated: bool = False):
        stop = threading.Event()
        self._username_challenge_stop = stop
        self._username_challenge_id = None
        def action(token):
            form = {"rated": str(rated).lower(), "color": color, "keepAliveStream": "true"}
            if clock_limit is not None:
                form.update({"clock.limit": clock_limit, "clock.increment": increment})
            self.challenge_status_changed.emit(f"Ожидаем ответ от {username}…", True)
            try:
                for event in self.client.username_challenge_events(token, username, form, stop):
                    self.event_received.emit(event)
                    challenge_id = event.get("challenge", {}).get("id")
                    if challenge_id:
                        self._username_challenge_id = challenge_id
                    kind = event.get("type")
                    if kind == "gameStart":
                        self.challenge_status_changed.emit("Вызов принят; партия запускается…", False)
                        break
                    if kind == "challengeDeclined":
                        self.challenge_status_changed.emit("Игрок отклонил вызов.", False)
                        break
                    if kind == "challengeCanceled":
                        self.challenge_status_changed.emit("Вызов отменён.", False)
                        break
                    done = event.get("done")
                    if done:
                        message = {
                            "accepted": "Игрок принял вызов; партия запускается…",
                            "declined": "Игрок отклонил вызов.",
                            "canceled": "Вызов отменён.",
                        }.get(done, f"Ожидание завершено: {done}.")
                        self.challenge_status_changed.emit(message, False)
                        break
            finally:
                if stop.is_set():
                    self.challenge_status_changed.emit("Ожидание вызова отменено.", False)
                self._username_challenge_stop = None
                self._username_challenge_id = None
        return self._challenge(action)

    def cancel_username_challenge(self):
        stop = self._username_challenge_stop
        challenge_id = self._username_challenge_id
        if stop:
            stop.set()
        if challenge_id:
            self._action(lambda token: self.client.cancel_challenge(token, challenge_id))

    def _challenge(self, action):
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Сначала подключите аккаунт Lichess.")
                result = action(token)
                if isinstance(result, dict):
                    self.challenge_ready.emit(result)
            except Exception as exc:
                self.error.emit(str(exc))
        self._thread(work, "chess-coach-challenge")

    def send_move(self, game_id: str, uci: str, request_id: str):
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Аккаунт Lichess не подключён.")
                self.client.send_move(token, game_id, uci)
            except Exception as exc:
                if isinstance(exc, LichessError) and exc.status == 401:
                    self.token_store.delete()
                    self._connection_stop.set()
                status = exc.status if isinstance(exc, LichessError) and exc.status else 0
                self.move_failed.emit(game_id, uci, request_id, status)
        self._thread(work, "chess-coach-send-move")

    def resign(self, game_id: str):
        self._action(lambda token: self.client.resign(token, game_id))

    def abort(self, game_id: str):
        self._action(lambda token: self.client.abort(token, game_id))

    def draw(self, game_id: str, accept: bool):
        self._action(lambda token: self.client.handle_draw(token, game_id, accept))

    def claim_draw(self, game_id: str):
        self._action(lambda token: self.client.claim_draw(token, game_id))

    def claim_victory(self, game_id: str):
        self._action(lambda token: self.client.claim_victory(token, game_id))

    def takeback(self, game_id: str, action: str, request_id: str):
        accept = action != "decline"

        def work():
            status = 0
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Аккаунт Lichess не подключён.")
                self.client.handle_takeback(token, game_id, accept)
            except Exception as exc:
                status = exc.status if isinstance(exc, LichessError) and exc.status else -1
            self.takeback_action_completed.emit(game_id, request_id, status)

        self._thread(work, "chess-coach-takeback")

    def _action(self, action):
        def work():
            try:
                token = self.token_store.get()
                if not token:
                    raise LichessError("Аккаунт Lichess не подключён.")
                action(token)
            except Exception as exc:
                self.error.emit(str(exc))
        self._thread(work, "chess-coach-lichess-action")

    def close(self, timeout: float = 1.0) -> bool:
        """Stop workers cooperatively. Return False while any worker still owns resources."""
        with self._thread_lock:
            self._closing = True
        self._stop.set()
        self._connection_stop.set()
        if self._archive_stop is not None:
            self._archive_stop.set()
        if self._puzzle_stop is not None:
            self._puzzle_stop.set()
        if self._callback:
            self._callback.cancel()
            self._callback = None
        if self._puzzle_callback:
            self._puzzle_callback.cancel()
            self._puzzle_callback = None
        if self._username_challenge_stop:
            self._username_challenge_stop.set()
        for stop in self._game_stops.values():
            stop.set()
        deadline = time.monotonic() + max(0.0, timeout)
        with self._thread_lock:
            threads = list(self._threads)
        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = [thread for thread in threads if thread.is_alive()
                 and thread is not threading.current_thread()]
        with self._thread_lock:
            self._threads = alive
        return not alive
