"""Small, testable Lichess Board API client using only the standard library."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..diagnostics import log_event
from ..domain.work_context import current_work_priority


class LichessError(RuntimeError):
    """Safe, user-facing API error; never contains request headers or tokens."""

    def __init__(self, message: str, status: int | None = None,
                 response_type: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.response_type = response_type


class RateLimitError(LichessError):
    def __init__(self, retry_after: int = 60) -> None:
        super().__init__(f"Lichess ограничил частоту запросов. Повторите через {retry_after} с.", 429)
        self.retry_after = retry_after


class RequestNotSentError(LichessError):
    """A queued request was cancelled or became stale before transport started."""

    def __init__(self) -> None:
        super().__init__("Сессия изменилась до отправки запроса Lichess.", -2)


_REQUEST_ADMISSION: ContextVar[tuple[Callable[[], bool], threading.Event | None] | None] = (
    ContextVar("lichess_request_admission", default=None))


@contextmanager
def authenticated_request_admission(guard: Callable[[], bool],
                                    stop_event: threading.Event | None = None):
    """Bind a captured session guard to the next synchronous client request.

    The guard is evaluated only after the shared request gate is acquired and
    immediately before invoking the physical transport. It is context-local,
    so concurrent workers cannot replace one another's admission check.
    """
    token = _REQUEST_ADMISSION.set((guard, stop_event))
    try:
        yield
    finally:
        _REQUEST_ADMISSION.reset(token)


class _PriorityRequestGate:
    """Admit one bounded HTTP request at a time; choose the highest priority next."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiters: list[tuple[int, int]] = []
        self._next_ticket = 0
        self._active = False

    @contextmanager
    def request(self, priority: int, stop_event: threading.Event | None = None):
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            waiter = (max(0, int(priority)), ticket)
            self._waiters.append(waiter)
            self._condition.notify_all()
            try:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        raise InterruptedError("Запрос Lichess отменён до допуска.")
                    if (not self._active and waiter == max(
                            self._waiters, key=lambda item: (item[0], -item[1]))):
                        self._waiters.remove(waiter)
                        self._active = True
                        break
                    self._condition.wait(timeout=0.05)
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                    self._condition.notify_all()
                raise
        try:
            yield
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()

    @property
    def waiting_count(self) -> int:
        with self._condition:
            return len(self._waiters)


class _FiniteStreamGate:
    """Bound concurrent archive/puzzle exports without holding the HTTP gate."""

    def __init__(self, limit: int = 1) -> None:
        self._condition = threading.Condition()
        self._limit = max(1, int(limit))
        self._active = 0
        self._waiters = 0

    @contextmanager
    def admit(self, stop_event: threading.Event):
        acquired = False
        with self._condition:
            self._waiters += 1
            try:
                while self._active >= self._limit:
                    if stop_event.is_set():
                        raise InterruptedError("Фоновая выгрузка отменена до допуска.")
                    self._condition.wait(timeout=0.05)
                if stop_event.is_set():
                    raise InterruptedError("Фоновая выгрузка отменена до допуска.")
                self._active += 1
                acquired = True
            finally:
                self._waiters -= 1
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    self._active -= 1
                    self._condition.notify_all()

    @property
    def active_count(self) -> int:
        with self._condition:
            return self._active

    @property
    def waiting_count(self) -> int:
        with self._condition:
            return self._waiters


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> object:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LichessError("Lichess вернул некорректный JSON.", self.status) from exc


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        stop_event: threading.Event,
        on_connected: Callable[[], None] | None = None,
    ) -> Iterator[bytes]: ...


class UrlLibTransport:
    """Production transport. Streaming responses close when stop is requested."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    response.status,
                    dict(response.headers.items()),
                    response.read(),
                )
        except HTTPError as response:
            return HttpResponse(
                response.code,
                dict(response.headers.items()),
                response.read(),
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise LichessError("Не удалось подключиться к Lichess. Проверьте интернет.") from exc

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        stop_event: threading.Event,
        on_connected: Callable[[], None] | None = None,
    ) -> Iterator[bytes]:
        request = Request(url, data=body, headers=dict(headers), method=method)
        response = None
        try:
            response = urlopen(request, timeout=timeout)
            if on_connected is not None:
                on_connected()
            while not stop_event.is_set():
                line = response.readline()
                if not line:
                    break
                yield line
        except HTTPError as exc:
            payload = exc.read(2048).decode("utf-8", "replace")
            if exc.code == 429:
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                raise RateLimitError(retry_after) from exc
            message = _error_message(exc.code, payload)
            raise LichessError(message, exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if not stop_event.is_set():
                raise LichessError("Поток Lichess прерван.") from exc
        finally:
            if response is not None:
                response.close()


def _error_message(status: int, body: str) -> str:
    if status == 401:
        return "Авторизация Lichess недействительна или отозвана. Подключите аккаунт снова."
    if status == 403:
        return "Lichess отклонил запрос (HTTP 403)."
    if status == 404:
        return "Партия или вызов Lichess больше не найдены."
    if status == 429:
        return "Lichess ограничил частоту запросов. Автоматические повторы приостановлены на 60 секунд."
    try:
        payload = json.loads(body)
        detail = payload.get("error") or payload.get("message")
    except (json.JSONDecodeError, AttributeError):
        detail = None
    if status >= 500:
        return "Сервис Lichess временно недоступен. Попробуйте позже."
    if detail:
        return f"Lichess отклонил запрос: {str(detail)[:300]}"
    return f"Lichess отклонил запрос (HTTP {status})."


def _retry_after_seconds(value: str | None) -> int:
    if value:
        try:
            return max(60, int(float(value)))
        except (ValueError, OverflowError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(60, int((retry_at - datetime.now(timezone.utc)).total_seconds() + 0.999))
            except (TypeError, ValueError, OverflowError):
                pass
    return 60


class LichessClient:
    BASE_URL = "https://lichess.org"
    MAX_FINITE_STREAM_PAGE = 250

    def __init__(
        self,
        transport: HttpTransport | None = None,
        base_url: str = BASE_URL,
        request_timeout: float = 20.0,
    ) -> None:
        self.transport = transport or UrlLibTransport()
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        # Serialize one transport request and shared Retry-After state only.
        # Workflow-level scans must yield between requests and never own this lock.
        self._request_gate = _PriorityRequestGate()
        self._finite_stream_gate = _FiniteStreamGate(limit=1)
        self._cooldown_lock = threading.Lock()
        self._rate_limited_until = 0.0

    def _call(
        self,
        method: str,
        path: str,
        token: str | None = None,
        form: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        accept: str = "application/json",
    ) -> HttpResponse:
        started = time.monotonic()
        correlation_id = uuid.uuid4().hex[:12]
        diagnostic_route = ("puzzle_dashboard" if path.startswith("/api/puzzle/dashboard/")
                           else "account" if path == "/api/account" else "api")
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": accept, "User-Agent": "ChessCoach/0.1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = None
        if form is not None:
            body = urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            admission = _REQUEST_ADMISSION.get()
            guard, stop_event = admission if admission is not None else (None, None)
            with self._request_gate.request(current_work_priority(), stop_event):
                if ((stop_event is not None and stop_event.is_set())
                        or (guard is not None and not guard())):
                    raise RequestNotSentError()
                with self._cooldown_lock:
                    self._check_cooldown_locked()
                response = self.transport.request(method, url, headers, body, self.request_timeout)
                if response.status == 429:
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                    with self._cooldown_lock:
                        self._rate_limited_until = max(
                            self._rate_limited_until, time.monotonic() + retry_after)
                    log_event("rate_limit_429", "lichess_api",
                              duration_ms=(time.monotonic() - started) * 1000,
                              http_status=429, mode=diagnostic_route,
                              method=method, route=diagnostic_route,
                              correlation_id=correlation_id,
                              retry_after=retry_after)
                    raise RateLimitError(retry_after)
        except Exception as exc:
            if not isinstance(exc, RateLimitError):
                log_event("api_request_failed", "lichess_api",
                          duration_ms=(time.monotonic() - started) * 1000,
                          mode=diagnostic_route, method=method, route=diagnostic_route,
                          correlation_id=correlation_id, error=exc)
            raise
        if response.status >= 400:
            response_type = _classify_error_response(response.headers, response.body)
            log_event("api_request_failed", "lichess_api",
                      duration_ms=(time.monotonic() - started) * 1000,
                      http_status=response.status, mode=diagnostic_route,
                      method=method, route=diagnostic_route,
                      response_type=response_type, correlation_id=correlation_id,
                      error=LichessError("HTTP status error", response.status))
            detail = response.body[:2048].decode("utf-8", "replace")
            message = _error_message(response.status, detail)
            if response.status == 403 and path == "/api/account":
                message = ("Проверка аккаунта отклонена Lichess (HTTP 403). "
                           "Сохранённые локальные данные доступны; автоматическая повторная авторизация не запускается.")
            raise LichessError(message, response.status,
                               response_type)
        if diagnostic_route == "puzzle_dashboard":
            log_event("puzzle_sync_dashboard_received", "lichess_api",
                      duration_ms=(time.monotonic() - started) * 1000,
                      bytes=len(response.body), http_status=response.status)
        return response


    def _check_cooldown_locked(self) -> None:
        deadline = self._rate_limited_until - time.monotonic()
        if deadline > 0:
            raise RateLimitError(max(1, int(deadline + 0.999)))

    @property
    def rate_limit_remaining(self) -> int:
        with self._cooldown_lock:
            return max(0, int(self._rate_limited_until - time.monotonic() + 0.999))

    def _json_call(self, *args, **kwargs) -> dict:
        data = self._call(*args, **kwargs).json()
        if not isinstance(data, dict):
            raise LichessError("Lichess вернул неожиданный ответ.")
        return data

    def get_puzzle_rating_history(self, username: str, token: str) -> list:
        response = self._call("GET", f"/api/user/{quote(username, safe='')}/rating-history", token)
        payload = response.json()
        if not isinstance(payload, list):
            raise LichessError("Некорректный ответ Lichess об истории рейтинга.")
        puzzles = next((entry for entry in payload
                        if isinstance(entry, dict) and entry.get("name") == "puzzle"), None)
        points = puzzles.get("points") if puzzles is not None else []
        if not isinstance(points, list):
            raise LichessError("Некорректные точки рейтинга задач Lichess.")
        return points

    def get_account(self, token: str) -> dict:
        return self._json_call("GET", "/api/account", token=token)

    def get_playing_games(self, token: str, limit: int = 50) -> list[dict]:
        """Return one validated snapshot of active games or fail closed.

        The endpoint has a bounded response. Reaching the requested limit does
        not prove the snapshot is complete, so callers must not use it as
        evidence that no human game is active.
        """
        if not 1 <= limit <= 50:
            raise ValueError("Количество активных партий должно быть от 1 до 50.")
        payload = self._json_call(
            "GET", "/api/account/playing", token=token, query={"nb": limit}
        )
        games = payload.get("nowPlaying")
        if not isinstance(games, list):
            raise LichessError("Lichess вернул неожиданный список активных партий.")
        if len(games) >= limit:
            raise LichessError(
                "Список активных партий достиг лимита и может быть неполным."
            )
        validated = []
        for index, game in enumerate(games):
            if not isinstance(game, dict):
                raise LichessError(
                    f"Некорректная запись активной партии Lichess (элемент {index + 1})."
                )
            game_id = game.get("gameId")
            opponent = game.get("opponent")
            if (not isinstance(game_id, str) or not game_id.strip()
                    or not isinstance(opponent, dict)
                    or not isinstance(opponent.get("username"), str)
                    or not opponent["username"].strip()
                    or ("title" in opponent and not isinstance(opponent["title"], str))):
                raise LichessError(
                    f"Неполная запись активной партии Lichess (элемент {index + 1})."
                )
            validated.append(game)
        return validated

    def export_game_pgn(self, token: str, game_id: str) -> str:
        """Fetch one explicitly selected game without requesting the user's archive."""
        if not game_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in game_id):
            raise ValueError("Invalid Lichess game ID")
        response = self._call("GET", f"/game/export/{quote(game_id, safe='')}", token=token,
                              accept="application/x-chess-pgn")
        try:
            return response.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LichessError("Lichess returned a PGN that is not valid UTF-8.") from exc

    def export_game_json(self, token: str, game_id: str) -> dict:
        """Fetch documented finished-game JSON, including only ready server metadata."""
        if not game_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in game_id):
            raise ValueError("Invalid Lichess game ID")
        response = self._call(
            "GET", f"/game/export/{quote(game_id, safe='')}", token=token,
            query={"accuracy": "true", "evals": "true", "pgnInJson": "true",
                   "opening": "true", "division": "true"},
            accept="application/json",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise LichessError("Lichess вернул неожиданный JSON партии.")
        return payload

    def stream_user_games(self, token: str, username: str, stop_event: threading.Event,
                          *, since: int | None = None, until_ms: int | None = None,
                          max_games: int = 1000) -> Iterator[dict]:
        """Stream a bounded export page; callers persist each record before checkpointing."""
        if not 1 <= max_games <= 10_000:
            raise ValueError("Размер страницы архива должен быть от 1 до 10000.")
        query: dict[str, object] = {
            "max": max_games, "sort": "dateAsc" if since is not None else "dateDesc",
            "pgnInJson": "true", "clocks": "true", "evals": "true",
            "accuracy": "true", "opening": "true", "division": "true",
            "finished": "true", "ongoing": "false",
        }
        if since is not None:
            query["since"] = since
        if until_ms is not None:
            query["until"] = until_ms
        path = f"/api/games/user/{quote(username, safe='')}?{urlencode(query)}"
        yield from self.stream_ndjson(path, token, stop_event, stream_kind="finite")

    def stream_puzzle_activity(self, token: str, stop_event: threading.Event, *,
                               since: int | None = None, before: int | None = None,
                               max_items: int = 1000) -> Iterator[dict]:
        if not 1 <= max_items <= 10_000:
            raise ValueError("Размер страницы задач должен быть от 1 до 10000.")
        query: dict[str, object] = {"max": max_items}
        if since is not None:
            query["since"] = since
        if before is not None:
            query["before"] = before
        yield from self.stream_ndjson("/api/puzzle/activity?" + urlencode(query), token, stop_event,
                                      stream_kind="finite")

    def get_puzzle_dashboard(self, token: str, days: int = 30) -> dict:
        if not 1 <= int(days) <= 365:
            raise ValueError("Период статистики задач должен быть от 1 до 365 дней.")
        return self._json_call("GET", f"/api/puzzle/dashboard/{int(days)}", token=token)

    def exchange_code(
        self,
        code: str,
        verifier: str,
        redirect_uri: str,
        client_id: str,
    ) -> str:
        response = self._json_call(
            "POST",
            "/api/token",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
            },
        )
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise LichessError("Ответ авторизации не содержит токен.")
        return token

    def revoke_token(self, token: str) -> None:
        self._call("DELETE", "/api/token", token=token)

    def create_open_challenge(
        self,
        token: str,
        color: str,
        rated: bool = False,
        clock_limit: int | None = None,
        clock_increment: int | None = None,
    ) -> dict:
        form: dict[str, object] = {"rated": str(rated).lower()}
        if clock_limit is not None:
            form["clock.limit"] = clock_limit
            form["clock.increment"] = clock_increment or 0
        if color not in {"white", "black", "random"}:
            raise ValueError("Цвет должен быть white, black или random.")
        return self._json_call("POST", "/api/challenge/open", token=token, form=form)

    def accept_open_challenge(self, token: str, challenge_id: str, color: str) -> dict:
        return self._json_call(
            "POST",
            f"/api/challenge/{challenge_id}/accept",
            token=token,
            query={"color": color},
        )

    def create_username_challenge(
        self,
        token: str,
        username: str,
        color: str,
        rated: bool = False,
        clock_limit: int | None = None,
        clock_increment: int | None = None,
    ) -> dict:
        if color not in {"white", "black", "random"}:
            raise ValueError("Цвет должен быть white, black или random.")
        form: dict[str, object] = {
            "rated": str(rated).lower(),
            "color": color,
        }
        if clock_limit is not None:
            form["clock.limit"] = clock_limit
            form["clock.increment"] = clock_increment or 0
        return self._json_call(
            "POST", f"/api/challenge/{quote(username, safe='')}", token=token, form=form
        )

    def username_challenge_events(
        self, token: str, username: str, form: Mapping[str, object], stop_event: threading.Event
    ) -> Iterator[dict]:
        """Await challenge lifecycle updates from Lichess' keep-alive NDJSON stream."""
        if color := form.get("color"):
            if color not in {"white", "black", "random"}:
                raise ValueError("Цвет должен быть white, black или random.")
        yield from self.stream_ndjson(
            f"/api/challenge/{quote(username, safe='')}", token, stop_event,
            method="POST", form=form, stream_kind="persistent"
        )

    def create_ai_challenge(
        self,
        token: str,
        level: int,
        color: str,
        clock_limit: int | None = None,
        clock_increment: int | None = None,
    ) -> dict:
        if level < 1 or level > 8:
            raise ValueError("Уровень Lichess AI должен быть от 1 до 8.")
        if color not in {"white", "black", "random"}:
            raise ValueError("Цвет должен быть white, black или random.")
        form: dict[str, object] = {"level": level, "color": color}
        if clock_limit is not None:
            form["clock.limit"] = clock_limit
            form["clock.increment"] = clock_increment or 0
        return self._json_call("POST", "/api/challenge/ai", token=token, form=form)

    def cancel_challenge(self, token: str, challenge_id: str) -> dict:
        return self._json_call(
            "POST", f"/api/challenge/{challenge_id}/cancel", token=token
        )

    def send_move(self, token: str, game_id: str, uci: str) -> dict:
        return self._json_call(
            "POST", f"/api/board/game/{game_id}/move/{uci}", token=token
        )

    def resign(self, token: str, game_id: str) -> dict:
        return self._json_call(
            "POST", f"/api/board/game/{game_id}/resign", token=token
        )

    def abort(self, token: str, game_id: str) -> dict:
        return self._json_call(
            "POST", f"/api/board/game/{game_id}/abort", token=token
        )

    def handle_draw(self, token: str, game_id: str, accept: bool) -> dict:
        value = "yes" if accept else "no"
        return self._json_call(
            "POST", f"/api/board/game/{game_id}/draw/{value}", token=token
        )

    def claim_draw(self, token: str, game_id: str) -> dict:
        return self._json_call("POST", f"/api/board/game/{game_id}/claim-draw", token=token)

    def claim_victory(self, token: str, game_id: str) -> dict:
        return self._json_call("POST", f"/api/board/game/{game_id}/claim-victory", token=token)

    def handle_takeback(self, token: str, game_id: str, accept: bool) -> dict:
        value = "yes" if accept else "no"
        return self._json_call("POST", f"/api/board/game/{game_id}/takeback/{value}", token=token)

    def stream_ndjson(
        self,
        path: str,
        token: str,
        stop_event: threading.Event,
        method: str = "GET",
        form: Mapping[str, object] | None = None,
        on_connected: Callable[[], None] | None = None,
        stream_kind: str = "persistent",
    ) -> Iterator[dict]:
        if stream_kind not in {"persistent", "finite"}:
            raise ValueError("Unknown NDJSON stream kind")
        route = path.split("?", 1)[0]
        diagnostic_mode = ("puzzle_activity" if route == "/api/puzzle/activity"
                           else "archive" if route.startswith("/api/games/user/")
                           else "persistent_stream" if stream_kind == "persistent" else "finite_stream")
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/x-ndjson",
            "User-Agent": "ChessCoach/0.1",
        }
        body = None
        if form is not None:
            body = urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        started = time.monotonic()
        byte_count = record_count = 0
        log_event("ndjson_stream_started", "lichess_api", mode=diagnostic_mode)
        finite_admission = (self._finite_stream_gate.admit(stop_event)
                            if stream_kind == "finite" else nullcontext())
        try:
            with finite_admission:
                with self._cooldown_lock:
                    self._check_cooldown_locked()
                # The gate orders only connection establishment. Once a response is
                # open, regular short requests can proceed beside the bounded stream.
                priority = (current_work_priority() if stream_kind == "finite"
                            else max(80, current_work_priority()))
                request_admission = self._request_gate.request(priority, stop_event)
                request_admission.__enter__()
                gate_lock = threading.Lock()
                gate_held = True

                def release_request_admission() -> None:
                    nonlocal gate_held
                    with gate_lock:
                        if gate_held:
                            gate_held = False
                            request_admission.__exit__(None, None, None)

                def connected() -> None:
                    release_request_admission()
                    if on_connected is not None:
                        on_connected()

                try:
                    with self._cooldown_lock:
                        self._check_cooldown_locked()
                    response = self.transport.stream(
                        method, url, headers, body, 35.0, stop_event,
                        on_connected=connected,
                    )
                    for raw in response:
                        # Simple fake transports may not implement on_connected.
                        release_request_admission()
                        byte_count += len(raw)
                        if stop_event.is_set():
                            break
                        line = raw.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise LichessError("Поток Lichess прислал некорректную строку NDJSON.") from exc
                        if isinstance(value, dict):
                            record_count += 1
                            yield value
                finally:
                    release_request_admission()
            log_event("puzzle_sync_cancelled" if stop_event.is_set()
                      else "ndjson_stream_completed", "lichess_api",
                      duration_ms=(time.monotonic() - started) * 1000,
                      bytes=byte_count, records=record_count, mode=diagnostic_mode)
        except RateLimitError as exc:
            retry_after = max(60, exc.retry_after)
            with self._cooldown_lock:
                self._rate_limited_until = max(
                    self._rate_limited_until, time.monotonic() + retry_after
                )
            log_event("rate_limit_429", "lichess_api",
                      duration_ms=(time.monotonic() - started) * 1000,
                      http_status=429, error=exc)
            raise RateLimitError(retry_after) from exc
        except Exception as exc:
            log_event("ndjson_stream_failed", "lichess_api",
                      duration_ms=(time.monotonic() - started) * 1000,
                      bytes=byte_count, records=record_count, error=exc)
            raise

    def stream_events(self, token: str, stop_event: threading.Event,
                      on_connected: Callable[[], None] | None = None) -> Iterator[dict]:
        yield from self.stream_ndjson("/api/stream/event", token, stop_event,
                                      on_connected=on_connected, stream_kind="persistent")

    def stream_game(self, token: str, game_id: str, stop_event: threading.Event) -> Iterator[dict]:
        yield from self.stream_ndjson(f"/api/board/game/stream/{game_id}", token, stop_event,
                                      stream_kind="persistent")


def _classify_error_response(headers: Mapping[str, str], body: bytes) -> str:
    """Classify a denial without retaining or logging its contents."""
    content_type = next((str(value).casefold() for key, value in headers.items()
                         if str(key).casefold() == "content-type"), "")
    if "json" in content_type:
        return "json"
    if "html" in content_type:
        return "html"
    prefix = body.lstrip()[:32].lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "html"
    if prefix.startswith((b"{", b"[")):
        return "json"
    return "other"
