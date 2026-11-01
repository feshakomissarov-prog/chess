"""OAuth 2.0 Authorization Code + PKCE and secure token storage."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

from .api import LichessClient, LichessError
from ..diagnostics import log_event


CLIENT_ID = "chess-coach-feshakomissarov-prog-windows"
OAUTH_SCOPES = "board:play challenge:write"


class TokenStore(Protocol):
    def get(self) -> str | None: ...

    def set(self, token: str) -> None: ...

    def delete(self) -> None: ...


class CredentialStorageError(RuntimeError):
    pass


class WindowsCredentialStore:
    """Store only the bearer token using the OS credential backend."""

    SERVICE = "ChessCoach.Lichess"
    USER = "oauth-access-token"
    PERSONAL_TOKEN_USER = "personal-access-token"

    def __init__(self, keyring_module=None, *, service: str | None = None,
                 user: str | None = None) -> None:
        self._service = service or self.SERVICE
        self._user = user or self.USER
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise CredentialStorageError(
                    "Не установлен защищённый Windows Credential Manager (keyring)."
                ) from exc
        self._keyring = keyring_module

    def get(self) -> str | None:
        try:
            return self._keyring.get_password(self._service, self._user)
        except Exception as exc:
            raise CredentialStorageError(
                "Не удалось прочитать токен из Windows Credential Manager."
            ) from exc

    def set(self, token: str) -> None:
        try:
            self._keyring.set_password(self._service, self._user, token)
        except Exception as exc:
            raise CredentialStorageError(
                "Не удалось сохранить токен в Windows Credential Manager."
            ) from exc

    def delete(self) -> None:
        try:
            self._keyring.delete_password(self._service, self._user)
        except self._keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:
            raise CredentialStorageError(
                "Не удалось удалить токен из Windows Credential Manager."
            ) from exc


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    verifier: str
    redirect_uri: str


def create_authorization_request(
    redirect_uri: str, client_id: str = CLIENT_ID, scopes: str = OAUTH_SCOPES
) -> AuthorizationRequest:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": scopes,
            "state": state,
        }
    )
    return AuthorizationRequest(
        url=f"https://lichess.org/oauth?{query}",
        state=state,
        verifier=verifier,
        redirect_uri=redirect_uri,
    )


@dataclass(frozen=True)
class CallbackResult:
    code: str | None
    state: str | None
    error: str | None


class OAuthCallbackServer:
    """Short-lived loopback receiver; binds only to 127.0.0.1."""

    def __init__(self) -> None:
        self._received = threading.Event()
        self._result: CallbackResult | None = None
        self._close_lock = threading.Lock()
        self._closed = False

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path != "/oauth/callback":
                    self.send_error(404)
                    return
                params = parse_qs(parsed.query, keep_blank_values=True)
                owner._result = CallbackResult(
                    code=(params.get("code") or [None])[0],
                    state=(params.get("state") or [None])[0],
                    error=(params.get("error") or [None])[0],
                )
                owner._received.set()
                payload = (
                    "Код передан Chess Coach. Проверку аккаунта завершит приложение."
                    if owner._result.code
                    else "Авторизация не завершена. Вернитесь в Chess Coach."
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args) -> None:
                return

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError as exc:
            raise LichessError("Не удалось открыть локальный OAuth callback-порт.") from exc
        self._server.daemon_threads = True
        self.redirect_uri = (
            f"http://127.0.0.1:{self._server.server_port}/oauth/callback"
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="chess-coach-oauth-callback",
            daemon=True,
        )
        self._thread.start()

    def wait(self, state: str, timeout: float = 300.0) -> str:
        try:
            if not self._received.wait(timeout):
                raise LichessError("Время ожидания авторизации Lichess истекло.")
            result = self._result
            if result is None:
                raise LichessError("OAuth callback не передал результат авторизации.")
            if result.error:
                raise LichessError("Авторизация Lichess отменена или отклонена.")
            if not result.code or not result.state:
                raise LichessError("OAuth callback не содержит код или state.")
            if not secrets.compare_digest(state, result.state):
                raise LichessError("Проверка OAuth state не пройдена.")
            return result.code
        finally:
            self.close()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._received.set()
            self._server.shutdown()
            self._server.server_close()
            if self._thread.is_alive():
                self._thread.join(timeout=2)

    def cancel(self) -> None:
        """Wake a waiting OAuth worker during app shutdown."""
        if self._result is None:
            self._result = CallbackResult(None, None, "cancelled")
        self._received.set()
        self.close()


def authorize_and_store(
    client: LichessClient,
    token_store: TokenStore,
    callback: OAuthCallbackServer,
    request: AuthorizationRequest,
    timeout: float = 300.0,
    is_current: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    """Exchange a checked callback, validate the token, then persist it."""
    correlation_id = uuid.uuid4().hex[:12]
    stage_started = time.monotonic()
    code = callback.wait(request.state, timeout)
    log_event("oauth_callback_received", "lichess_auth", correlation_id=correlation_id)
    if is_current is not None and not is_current():
        raise LichessError("Запрос авторизации устарел; подключение не сохранено.")
    try:
        token = client.exchange_code(
            code, request.verifier, request.redirect_uri, CLIENT_ID
        )
    except Exception as exc:
        log_event("oauth_exchange_failed", "lichess_auth", correlation_id=correlation_id,
                  stage="oauth", http_status=getattr(exc, "status", None), error=exc)
        raise
    log_event("oauth_exchange_success", "lichess_auth", correlation_id=correlation_id,
              duration_ms=(time.monotonic() - stage_started) * 1000)
    account_started = time.monotonic()
    log_event("account_check_started", "lichess_auth", correlation_id=correlation_id,
              stage="oauth")
    try:
        account = client.get_account(token)
    except Exception as exc:
        log_event("account_check_failed", "lichess_auth", correlation_id=correlation_id,
                  stage="oauth", http_status=getattr(exc, "status", None),
                  response_type=getattr(exc, "response_type", None), error=exc)
        raise
    username = account.get("username")
    if not isinstance(username, str) or not username.strip():
        raise LichessError("Lichess не вернул имя аккаунта.")
    if is_current is not None and not is_current():
        raise LichessError("Проверка аккаунта устарела; подключение не сохранено.")
    log_event("account_check_succeeded", "lichess_auth", correlation_id=correlation_id,
              stage="oauth", duration_ms=(time.monotonic() - account_started) * 1000)
    token_store.set(token)
    log_event("credential_store_success", "lichess_auth", correlation_id=correlation_id,
              stage="oauth")
    log_event("connection_ready", "lichess_auth", correlation_id=correlation_id,
              stage="oauth")
    return token, username.strip()
