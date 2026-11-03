"""Interactive Lichess game, challenge and move commands."""

from __future__ import annotations

import random
import threading
import time
import uuid

from .api import LichessError, RateLimitError

class LichessGameActionsMixin:
    def claim_draw(self, game_id: str):
        self._action(lambda token: self.client.claim_draw(token, game_id))

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

    def resign(self, game_id: str):
        self._action(lambda token: self.client.resign(token, game_id))

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

    def open_friend_game(self, color: str, clock_limit: int | None, increment: int = 0, rated: bool = False):
        return self._challenge(lambda token: self._create_open(token, color, clock_limit, increment, rated))

    def mark_game_finished(self, game_id: str) -> None:
        """Allow a bounded stream resync to fetch the final authoritative line."""
        with self._event_lock:
            self._final_sync_game_ids.add(game_id)

    def cancel_username_challenge(self):
        stop = self._username_challenge_stop
        challenge_id = self._username_challenge_id
        if stop:
            stop.set()
        if challenge_id:
            self._action(lambda token: self.client.cancel_challenge(token, challenge_id))

    def start_game(self, game_id: str, token: str | None = None,
                   expected_generation: int | None = None) -> None:
        token = token or self.token
        if not token:
            return
        stop = threading.Event()
        with self._auth_lock:
            auth_generation = self._auth_generation
            if expected_generation is not None and expected_generation != auth_generation:
                return
            connection_stop = self._connection_stop
        with self._auth_lock:
            if (auth_generation != self._auth_generation
                    or connection_stop is not self._connection_stop):
                return
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
                            if (self._stop.is_set() or connection_stop.is_set() or stop.is_set()
                                    or not self.is_auth_generation_current(auth_generation)):
                                break
                            delay = 1
                            if not self._emit_session_game_event(auth_generation, game_id, event):
                                break
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
                        if not self.is_auth_generation_current(auth_generation):
                            break
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self._emit_session_status(auth_generation, f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self._emit_session_status(
                            auth_generation,
                            f"Ограничение Lichess (429): синхронизация партии через {max(60, exc.retry_after)} с"
                        )
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if not self.is_auth_generation_current(auth_generation):
                            break
                        if exc.status == 401:
                            connection_stop.set()
                            self._emit_session_error(auth_generation, str(exc))
                            break
                        with self._event_lock:
                            final_sync = game_id in self._final_sync_game_ids
                        if final_sync and final_attempts >= 1:
                            self._emit_session_status(auth_generation, f"Финальная синхронизация партии {game_id} ожидает повторного подключения.")
                            break
                        if final_sync:
                            final_attempts += 1
                        self._emit_session_status(auth_generation, "Потеряно соединение с партией; синхронизация…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
            finally:
                with self._event_lock:
                    if self._game_stops.get(game_id) is stop:
                        self._game_stops.pop(game_id, None)
        self._thread(work, f"chess-coach-game-{game_id}")

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

    def claim_victory(self, game_id: str):
        self._action(lambda token: self.client.claim_victory(token, game_id))

    def create_ai_game(self, level: int, color: str, clock_limit: int | None, increment: int = 0):
        return self._challenge(lambda token: self.client.create_ai_challenge(token, level, color, clock_limit, increment))

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

    def abort(self, game_id: str):
        self._action(lambda token: self.client.abort(token, game_id))

    def draw(self, game_id: str, accept: bool):
        self._action(lambda token: self.client.handle_draw(token, game_id, accept))

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
