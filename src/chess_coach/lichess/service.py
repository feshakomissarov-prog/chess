"""Background OAuth, challenge, and NDJSON orchestration for the Qt UI."""

from __future__ import annotations

import threading
import random
import webbrowser

from PySide6.QtCore import QObject, Signal

from .api import LichessClient, LichessError, RateLimitError
from .auth import (CLIENT_ID, OAuthCallbackServer, WindowsCredentialStore,
                   authorize_and_store, create_authorization_request)


class LichessService(QObject):
    status_changed = Signal(str)
    event_received = Signal(dict)
    game_event_received = Signal(str, dict)
    challenge_ready = Signal(dict)
    challenge_status_changed = Signal(str, bool)
    invitation_cancelled = Signal(str)
    error = Signal(str)
    move_failed = Signal(str, str, str, int)
    authenticated = Signal(str)

    def __init__(self, client: LichessClient | None = None, token_store=None, parent=None):
        super().__init__(parent)
        self.client = client or LichessClient()
        self.token_store = token_store or WindowsCredentialStore()
        self._stop = threading.Event()
        self._connection_stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._game_stops: dict[str, threading.Event] = {}
        self._event_lock = threading.Lock()
        self._event_stream_started = False
        self.username: str | None = None
        self._callback: OAuthCallbackServer | None = None
        self._username_challenge_stop: threading.Event | None = None
        self._username_challenge_id: str | None = None

    @property
    def token(self) -> str | None:
        return self.token_store.get()

    def _thread(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def restore(self) -> None:
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
                self.status_changed.emit("Ошибка подключения")
        self._thread(work, "chess-coach-restore-auth")

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
                if not self._stop.is_set() and not connection_stop.is_set():
                    try:
                        self._start_event_stream(self.token_store.get())
                    except Exception:
                        pass
        self._thread(work, "chess-coach-lichess-events")

    def start_game(self, game_id: str, token: str | None = None) -> None:
        token = token or self.token
        if not token or game_id in self._game_stops:
            return
        stop = threading.Event()
        connection_stop = self._connection_stop
        self._game_stops[game_id] = stop

        def work():
            delay = 1
            try:
                while not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                    try:
                        for event in self.client.stream_game(token, game_id, stop):
                            if self._stop.is_set() or connection_stop.is_set() or stop.is_set():
                                break
                            delay = 1
                            self.game_event_received.emit(game_id, event)
                            if event.get("type") == "gameFull" and event.get("state", {}).get("status") not in {"started", "created"}:
                                stop.set()
                            if event.get("type") == "gameState" and event.get("status") not in {"started", "created"}:
                                stop.set()
                        if not self._stop.is_set() and not connection_stop.is_set() and not stop.is_set():
                            raise LichessError("Поток партии завершился.")
                    except RateLimitError as exc:
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if exc.status == 401:
                            self.token_store.delete()
                            connection_stop.set()
                            self.error.emit(str(exc))
                            break
                        self.status_changed.emit("Потеряно соединение с партией; синхронизация…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
            finally:
                self._game_stops.pop(game_id, None)
        self._thread(work, f"chess-coach-game-{game_id}")

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

    def close(self) -> None:
        self._stop.set()
        self._connection_stop.set()
        if self._callback:
            self._callback.cancel()
            self._callback = None
        for stop in self._game_stops.values():
            stop.set()
        for thread in self._threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=1.5)
