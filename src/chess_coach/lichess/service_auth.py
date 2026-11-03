"""OAuth, personal-token, session generation and event-stream lifecycle."""

from __future__ import annotations

import threading
import time
import uuid
import webbrowser

from .api import LichessError, RateLimitError
from .auth import (CLIENT_ID, CredentialStorageError, OAuthCallbackServer,
                   WindowsCredentialStore, authorize_and_store, create_authorization_request)
from ..diagnostics import log_event

class LichessAuthMixin:
    def set_auth_method(self, method: str) -> None:
        """Switch credentials without deleting either slot or revoking PATs."""
        if method not in {"oauth", "personal_token"}:
            raise ValueError("Неизвестный способ подключения Lichess.")
        with self._auth_lock:
            if method == self.auth_method:
                return
            generation = self._cancel_active_session()
            self.auth_method = method
        self._emit_session_status(generation, "Не подключено")

    def _emit_session_error(self, generation: int, message: str) -> bool:
        with self._auth_lock:
            if generation != self._auth_generation:
                return False
            self.session_error.emit(generation, message)
            self.error.emit(message)
            return True

    def _thread(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        with self._thread_lock:
            if self._closing:
                return
            self._threads.append(thread)
            thread.start()

    def disconnect_account(self) -> None:
        """Stop the active session but retain its selected credential for restore."""
        generation = self._cancel_active_session()
        self._emit_session_status(generation, "Не подключено")

    @property
    def selected_token_store(self):
        return (self.personal_token_store if self.auth_method == "personal_token"
                else self.oauth_token_store)

    @property
    def token(self) -> str | None:
        return self.selected_token_store.get()

    def _emit_session_status(self, generation: int, status: str) -> bool:
        with self._auth_lock:
            if generation != self._auth_generation:
                return False
            self.session_status_changed.emit(generation, status)
            # Retain the legacy signal for external callers. The application
            # itself consumes only the generation-tagged version.
            self.status_changed.emit(status)
            return True

    def delete_saved_personal_token(self) -> None:
        """Delete only the local PAT copy; never call the Lichess revoke API."""
        if self.auth_method == "personal_token":
            self._cancel_active_session()
            self.username = None
            self.status_changed.emit("Не подключено")
        self.personal_token_store.delete()

    def restore(self) -> None:
        with self._auth_lock:
            selected_store = self.selected_token_store
            selected_method = self.auth_method
            auth_generation = self._auth_generation
        with self._restore_lock:
            if auth_generation in self._restore_generations:
                return
            self._restore_generations.add(auth_generation)
            self._restore_running = True
        self._emit_session_status(auth_generation, "Проверяем подключение…")
        def work():
            correlation_id = uuid.uuid4().hex[:12]
            started = time.monotonic()
            log_event("restore_started", "lichess_auth", correlation_id=correlation_id,
                      stage=selected_method)
            try:
                if not self.is_auth_generation_current(auth_generation):
                    return
                token = selected_store.get()
                if not token:
                    self._emit_session_status(auth_generation, "Не подключено")
                    return
                account = self.client.get_account(token)
                if (auth_generation != self._auth_generation
                        or selected_method != self.auth_method):
                    return
                username = account.get("username")
                if not isinstance(username, str) or not username.strip():
                    raise LichessError("Lichess не вернул корректный аккаунт.")
                with self._auth_lock:
                    if (auth_generation != self._auth_generation
                            or selected_method != self.auth_method):
                        return
                    self.username = username.strip()
                log_event("connection_ready", "lichess_auth", correlation_id=correlation_id,
                          stage=selected_method,
                          duration_ms=(time.monotonic() - started) * 1000)
                self.session_authenticated.emit(auth_generation, self.username)
                self.authenticated.emit(self.username)
                self._start_event_stream(token, expected_generation=auth_generation)
            except Exception as exc:
                log_event("restore_failed", "lichess_auth", correlation_id=correlation_id,
                          stage=selected_method, http_status=getattr(exc, "status", None),
                          response_type=getattr(exc, "response_type", None), error=exc)
                self._emit_session_error(auth_generation, str(exc))
                if isinstance(exc, RateLimitError) or (
                    isinstance(exc, LichessError) and exc.status == 429
                ):
                    self._emit_session_status(
                        auth_generation,
                        f"Ограничение Lichess (429): повторная попытка не раньше чем через {max(60, getattr(exc, 'retry_after', 60))} с"
                    )
                elif isinstance(exc, LichessError) and exc.status == 403:
                    self._emit_session_status(
                        auth_generation, "Проверка аккаунта отклонена Lichess (HTTP 403)")
                elif isinstance(exc, LichessError) and exc.status == 401:
                    self._emit_session_status(
                        auth_generation, "Требуется повторное подключение Lichess (HTTP 401)")
                else:
                    self._emit_session_status(auth_generation, "Ошибка подключения")
            finally:
                with self._restore_lock:
                    self._restore_generations.discard(auth_generation)
                    self._restore_running = bool(self._restore_generations)
        self._thread(work, "chess-coach-restore-auth")

    def revoke_oauth_credential(self) -> None:
        """Explicitly revoke the OAuth credential captured for this operation only."""
        with self._auth_lock:
            revoke_generation = (self._cancel_active_session()
                                 if self.auth_method == "oauth" else self._auth_generation)
            oauth_store = self.oauth_token_store
        if self.auth_method == "oauth":
            self._emit_session_status(revoke_generation, "Отзываем сохранённый OAuth-токен…")

        def work():
            try:
                with self._auth_lock:
                    if revoke_generation != self._auth_generation:
                        return
                token = oauth_store.get()
                if not token:
                    self._emit_session_status(revoke_generation,
                                              "Сохранённый OAuth-токен не найден.")
                    return
                self.client.revoke_token(token)
                # A new OAuth login may have replaced the slot during the
                # network request. Delete only the exact credential revoked.
                if oauth_store.get() == token:
                    oauth_store.delete()
                self._emit_session_status(revoke_generation,
                                          "OAuth-токен отозван и удалён локально.")
            except Exception as exc:
                self._emit_session_error(revoke_generation,
                                         "Не удалось отозвать OAuth-токен.")
                log_event("oauth_revoke_failed", "lichess_auth", error=exc)

        self._thread(work, "chess-coach-oauth-revoke")

    @property
    def auth_generation(self) -> int:
        with self._auth_lock:
            return self._auth_generation

    def connect_account(self) -> None:
        with self._auth_lock:
            auth_generation = self._cancel_active_session()
            self.auth_method = "oauth"
        def work():
            callback = None
            correlation_id = uuid.uuid4().hex[:12]

            class GuardedOAuthStore:
                def get(inner_self):
                    return self.oauth_token_store.get()

                def set(inner_self, token):
                    with self._auth_lock:
                        if (auth_generation != self._auth_generation
                                or self.auth_method != "oauth"):
                            raise LichessError("Авторизация устарела; секрет не сохранён.")
                        self.oauth_token_store.set(token)

                def delete(inner_self):
                    return self.oauth_token_store.delete()
            try:
                if not self.is_auth_generation_current(auth_generation):
                    return
                callback = OAuthCallbackServer()
                if not self._install_callback_if_current(auth_generation, callback):
                    callback.close()
                    return
                request = create_authorization_request(callback.redirect_uri, CLIENT_ID)
                if not webbrowser.open(request.url):
                    raise LichessError("Не удалось открыть браузер для авторизации Lichess.")
                self._emit_session_status(auth_generation,
                                          "Ожидание авторизации в браузере…")
                token, username = authorize_and_store(
                    self.client, GuardedOAuthStore(), callback, request,
                    is_current=lambda: (auth_generation == self._auth_generation
                                        and self.auth_method == "oauth"))
                with self._auth_lock:
                    if auth_generation != self._auth_generation or self.auth_method != "oauth":
                        return
                    self.username = username
                    self._connection_stop = threading.Event()
                self.session_authenticated.emit(auth_generation, username)
                self.authenticated.emit(username)
                self._start_event_stream(token, expected_generation=auth_generation)
                self._clear_callback_if_owned(callback)
            except Exception as exc:
                if callback:
                    callback.close()
                    self._clear_callback_if_owned(callback)
                log_event("oauth_exchange_or_validation_failed", "lichess_auth",
                          correlation_id=correlation_id,
                          http_status=getattr(exc, "status", None),
                          response_type=getattr(exc, "response_type", None), error=exc)
                self._emit_session_error(auth_generation, str(exc))
                if getattr(exc, "status", None) == 403:
                    self._emit_session_status(
                        auth_generation, "Проверка аккаунта отклонена Lichess (HTTP 403)")
                elif getattr(exc, "status", None) == 429:
                    self._emit_session_status(
                        auth_generation,
                        f"Ограничение Lichess (429): повторите через {max(60, getattr(exc, 'retry_after', 60))} с")
                else:
                    self._emit_session_status(auth_generation, "Не подключено")
        self._thread(work, "chess-coach-oauth")

    def _emit_session_game_event(self, generation: int, game_id: str, event: dict) -> bool:
        with self._auth_lock:
            if generation != self._auth_generation:
                return False
            self.session_game_event_received.emit(generation, game_id, event)
            self.game_event_received.emit(game_id, event)
            return True

    def _start_event_stream(self, token: str | None,
                            expected_generation: int | None = None) -> None:
        if not token:
            return
        with self._auth_lock:
            auth_generation = self._auth_generation
            if expected_generation is not None and expected_generation != auth_generation:
                return
            connection_stop = self._connection_stop
            username = self.username
            with self._event_lock:
                if self._event_stream_started:
                    return
                if connection_stop is not self._connection_stop:
                    return
                self._event_stream_started = True
        self._emit_session_status(auth_generation,
                                  f"Подключено: {username or 'Lichess'}")

        def work():
            delay = 1
            recovering_stream = False

            def stream_connected():
                nonlocal recovering_stream
                if (self._stop.is_set()
                        or connection_stop.is_set()
                        or self._connection_stop is not connection_stop
                        or not self.is_auth_generation_current(auth_generation)):
                    return
                self.session_event_stream_ready.emit(auth_generation, username or "")
                if recovering_stream and self._emit_session_status(
                        auth_generation,
                        f"Поток восстановлен: {username or 'Lichess'}"):
                    recovering_stream = False

            try:
                while not self._stop.is_set() and not connection_stop.is_set():
                    try:
                        for event in self.client.stream_events(
                                token, connection_stop, on_connected=stream_connected):
                            delay = 1
                            if (self._stop.is_set() or connection_stop.is_set()
                                    or not self.is_auth_generation_current(auth_generation)):
                                break
                            if not self._emit_session_event(auth_generation, event):
                                break
                            if event.get("type") == "gameStart":
                                game_id = event.get("game", {}).get("gameId")
                                if game_id:
                                    self.start_game(str(game_id), token,
                                                    expected_generation=auth_generation)
                        if not self._stop.is_set() and not connection_stop.is_set():
                            raise LichessError("Поток событий Lichess завершился.")
                    except RateLimitError as exc:
                        recovering_stream = True
                        self._emit_session_status(
                            auth_generation,
                            f"Ограничение Lichess (429): переподключение через {max(60, exc.retry_after)} с"
                        )
                        connection_stop.wait(max(60, exc.retry_after))
                    except LichessError as exc:
                        if not self.is_auth_generation_current(auth_generation):
                            break
                        if exc.status == 401:
                            connection_stop.set()
                            self._emit_session_error(auth_generation, str(exc))
                            break
                        if exc.status == 403:
                            connection_stop.set()
                            self._emit_session_error(auth_generation, str(exc))
                            break
                        recovering_stream = True
                        self._emit_session_status(auth_generation, "Переподключение…")
                        connection_stop.wait(delay)
                        delay = min(delay * 2, 30)
                self._emit_session_status(auth_generation, "Не подключено")
            finally:
                with self._event_lock:
                    if (auth_generation == self._auth_generation
                            and self._connection_stop is connection_stop):
                        self._event_stream_started = False
        self._thread(work, "chess-coach-lichess-events")

    def _cancel_active_session(self) -> int:
        with self._auth_lock:
            self._auth_generation += 1
            generation = self._auth_generation
            previous_connection_stop = self._connection_stop
            self._connection_stop = threading.Event()
            self.username = None
            previous_connection_stop.set()
            callback, self._callback = self._callback, None
            if self._archive_stop is not None:
                self._archive_stop.set()
            if self._analysis_backfill_stop is not None:
                self._analysis_backfill_stop.set()
            if self._puzzle_stop is not None:
                self._puzzle_stop.set()
            if self._username_challenge_stop:
                self._username_challenge_stop.set()
            with self._event_lock:
                self._event_stream_started = False
                for stop in self._game_stops.values():
                    stop.set()
                self._game_stops.clear()
                self._final_sync_game_ids.clear()
        if callback is not None:
            callback.cancel()
        return generation

    def is_auth_generation_current(self, generation: int) -> bool:
        with self._auth_lock:
            return generation == self._auth_generation

    def _emit_session_event(self, generation: int, event: dict) -> bool:
        with self._auth_lock:
            if generation != self._auth_generation:
                return False
            self.session_event_received.emit(generation, event)
            self.event_received.emit(event)
            return True

    @property
    def token_store(self):
        """Compatibility alias for code paths that use the selected account."""
        return self.selected_token_store

    def _install_callback_if_current(self, generation: int,
                                     callback: OAuthCallbackServer) -> bool:
        with self._auth_lock:
            if generation != self._auth_generation or self.auth_method != "oauth":
                return False
            self._callback = callback
            return True

    def _clear_callback_if_owned(self, callback: OAuthCallbackServer) -> None:
        with self._auth_lock:
            if self._callback is callback:
                self._callback = None

    def connect_personal_token(self, token: str) -> None:
        """Validate a manually entered PAT before persisting it to its own slot."""
        candidate = token.strip()
        if not candidate:
            self.error.emit("Введите персональный токен Lichess.")
            return
        with self._auth_lock:
            auth_generation = self._cancel_active_session()
            self.auth_method = "personal_token"
        correlation_id = uuid.uuid4().hex[:12]
        def work():
            started = time.monotonic()
            log_event("account_check_started", "lichess_auth", correlation_id=correlation_id,
                      stage="personal_token")
            self._emit_session_status(auth_generation,
                                      "Проверяем аккаунт по персональному токену…")
            try:
                if not self.is_auth_generation_current(auth_generation):
                    return
                account = self.client.get_account(candidate)
                username = account.get("username")
                if not isinstance(username, str) or not username.strip():
                    raise LichessError("Lichess не вернул корректный аккаунт.")
                with self._auth_lock:
                    if auth_generation != self._auth_generation or self.auth_method != "personal_token":
                        raise LichessError("Проверка токена устарела; подключение не сохранено.")
                    self.personal_token_store.set(candidate)
                    self.auth_method = "personal_token"
                    self.username = username.strip()
                    self._connection_stop = threading.Event()
                log_event("credential_store_success", "lichess_auth",
                          correlation_id=correlation_id, stage="personal_token")
                log_event("connection_ready", "lichess_auth", correlation_id=correlation_id,
                          stage="personal_token", duration_ms=(time.monotonic() - started) * 1000)
                self.session_authenticated.emit(auth_generation, self.username)
                self.authenticated.emit(self.username)
                self._start_event_stream(candidate, expected_generation=auth_generation)
            except Exception as exc:
                log_event("account_check_failed", "lichess_auth", correlation_id=correlation_id,
                          stage="personal_token", http_status=getattr(exc, "status", None),
                          response_type=getattr(exc, "response_type", None), error=exc)
                message = (str(exc) if isinstance(exc, (LichessError, CredentialStorageError))
                           else "Не удалось проверить и сохранить токен.")
                self._emit_session_error(auth_generation, message)
                if isinstance(exc, RateLimitError) or getattr(exc, "status", None) == 429:
                    delay = max(60, getattr(exc, "retry_after", 60))
                    self._emit_session_status(
                        auth_generation, f"Ограничение Lichess (429): повторите через {delay} с")
                elif getattr(exc, "status", None) == 403:
                    self._emit_session_status(
                        auth_generation, "Проверка аккаунта отклонена Lichess (HTTP 403)")
                elif getattr(exc, "status", None) == 401:
                    self._emit_session_status(
                        auth_generation, "Требуется повторное подключение Lichess (HTTP 401)")
                else:
                    self._emit_session_status(auth_generation, "Ошибка проверки аккаунта")
        self._thread(work, "chess-coach-personal-token-check")
