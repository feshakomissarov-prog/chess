import threading
import json

from chess_coach.application import background_work
from chess_coach.application.background_work import BackgroundWorkCoordinator
from chess_coach.domain.work_context import reset_work_priority, set_work_priority
from chess_coach.lichess.api import HttpResponse, LichessClient, RateLimitError
from chess_coach.lichess import service_sync
from chess_coach.lichess.service import LichessService


class TokenStore:
    def get(self):
        return "synthetic-token"


def test_background_lichess_work_yields_between_requests(monkeypatch):
    monkeypatch.setattr(background_work, "log_event", lambda *_args, **_kwargs: None)
    between_archive_requests = threading.Event()
    allow_archive_second_request = threading.Event()
    puzzle_request_finished = threading.Event()
    request_lock = threading.Lock()

    class Client:
        username = "owner"

        def get_account(self, _token):
            return {"username": self.username}

        def request(self, request_name):
            with request_lock:
                if request_name == "archive-1":
                    between_archive_requests.set()
                elif request_name == "puzzle":
                    puzzle_request_finished.set()

    class FakeArchiveSync:
        def __init__(self, client, _repository):
            self.client = client

        def run(self, *_args, **_kwargs):
            self.client.request("archive-1")
            assert allow_archive_second_request.wait(3)
            self.client.request("archive-2")
            return 0

    class FakePuzzleSync:
        def __init__(self, client, _repository):
            self.client = client

        def run(self, *_args, **_kwargs):
            self.client.request("puzzle")
            return 0

    monkeypatch.setattr(service_sync, "ArchiveSync", FakeArchiveSync)
    monkeypatch.setattr(service_sync, "PuzzleActivitySync", FakePuzzleSync)
    monkeypatch.setattr(service_sync, "WindowsCredentialStore", lambda **_kwargs: TokenStore())
    coordinator = BackgroundWorkCoordinator()
    service = LichessService(client=Client(), token_store=TokenStore(),
                             personal_token_store=TokenStore(), repository=object(),
                             work_coordinator=coordinator)
    service.username = "owner"
    service.sync_archive(background=True)
    assert between_archive_requests.wait(1)

    service.sync_puzzle_activity_background()
    try:
        assert puzzle_request_finished.wait(1), (
            "Puzzle backfill waited behind the whole archive scan instead of one request")
    finally:
        allow_archive_second_request.set()
        assert service.close(timeout=2)
        assert coordinator.close(timeout=2)
    assert not hasattr(service, "_background_api_lock")


def test_interactive_http_request_is_admitted_before_waiting_background_request():
    first_request_entered = threading.Event()
    release_first_request = threading.Event()
    request_order = []

    class Transport:
        def request(self, _method, url, _headers, _body, _timeout):
            path = url.rsplit("/", 1)[-1]
            request_order.append(path)
            if path == "in-flight":
                first_request_entered.set()
                assert release_first_request.wait(2)
            return HttpResponse(200, {}, b"{}")

    client = LichessClient(Transport(), base_url="https://lichess.test")
    errors = []

    def request(path, priority):
        token = set_work_priority(priority)
        try:
            client._call("GET", f"/{path}")
        except Exception as exc:  # surfaced in the main test thread
            errors.append(exc)
        finally:
            reset_work_priority(token)

    in_flight = threading.Thread(target=request, args=("in-flight", 0))
    background = threading.Thread(target=request, args=("background", 20))
    interactive = threading.Thread(target=request, args=("interactive", 100))
    in_flight.start()
    assert first_request_entered.wait(1)
    background.start()
    interactive.start()
    try:
        with client._request_gate._condition:
            assert client._request_gate._condition.wait_for(
                lambda: client._request_gate.waiting_count == 2, timeout=1)
        release_first_request.set()
        for thread in (in_flight, background, interactive):
            thread.join(2)
            assert not thread.is_alive()
    finally:
        release_first_request.set()
    assert not errors
    assert request_order == ["in-flight", "interactive", "background"]


def test_finite_ndjson_exports_are_bounded_but_live_streams_and_moves_are_independent():
    archive_connected = threading.Event()
    puzzle_connected = threading.Event()
    event_connected = threading.Event()
    game_connected = threading.Event()
    release_archive = threading.Event()
    release_live = threading.Event()
    move_finished = threading.Event()
    lock = threading.Lock()
    active_finite = max_finite = active_streams = 0
    errors = []

    class Transport:
        def request(self, _method, url, _headers, _body, _timeout):
            if "/move/" in url:
                move_finished.set()
            return HttpResponse(200, {}, b'{"ok":true}')

        def stream(self, _method, url, _headers, _body, _timeout, stop_event,
                   on_connected=None):
            nonlocal active_finite, max_finite, active_streams
            route = url.split("?", 1)[0]
            finite = ("/api/games/user/" in route or route == "https://lichess.test/api/puzzle/activity")
            with lock:
                active_streams += 1
                if finite:
                    active_finite += 1
                    max_finite = max(max_finite, active_finite)
            try:
                if on_connected:
                    on_connected()
                if route.endswith("/api/stream/event"):
                    event_connected.set()
                    assert release_live.wait(3)
                    yield b'{"type":"event"}\n'
                elif "/api/board/game/stream/" in route:
                    game_connected.set()
                    assert release_live.wait(3)
                    yield b'{"type":"gameState"}\n'
                elif "/api/games/user/" in route:
                    archive_connected.set()
                    assert release_archive.wait(3)
                    yield b'{"id":"synthetic-game"}\n'
                else:
                    puzzle_connected.set()
                    yield b'{"date":100,"win":true,"puzzle":{"id":"synthetic"}}\n'
            finally:
                with lock:
                    active_streams -= 1
                    if finite:
                        active_finite -= 1

    client = LichessClient(Transport(), base_url="https://lichess.test")

    def consume(iterator):
        try:
            list(iterator)
        except Exception as exc:
            errors.append(exc)

    event_thread = threading.Thread(
        target=consume, args=(client.stream_events("fake", threading.Event()),))
    game_thread = threading.Thread(
        target=consume, args=(client.stream_game("fake", "g1", threading.Event()),))
    archive_thread = threading.Thread(
        target=consume, args=(client.stream_user_games("fake", "owner", threading.Event()),))
    puzzle_thread = threading.Thread(
        target=consume, args=(client.stream_puzzle_activity("fake", threading.Event()),))
    event_thread.start()
    game_thread.start()
    assert event_connected.wait(1) and game_connected.wait(1)
    archive_thread.start()
    assert archive_connected.wait(1)
    puzzle_thread.start()
    assert client._finite_stream_gate._condition.acquire(timeout=1)
    try:
        assert client._finite_stream_gate._condition.wait_for(
            lambda: client._finite_stream_gate.waiting_count == 1, timeout=1)
    finally:
        client._finite_stream_gate._condition.release()

    priority = set_work_priority(100)
    try:
        client.send_move("fake", "g1", "e2e4")
    finally:
        reset_work_priority(priority)
    assert move_finished.wait(1), "a game action must not wait for a finite export"
    assert not puzzle_connected.is_set()
    release_archive.set()
    assert puzzle_connected.wait(1), "the next finite export should get the released slot"
    release_live.set()
    for thread in (event_thread, game_thread, archive_thread, puzzle_thread):
        thread.join(2)
        assert not thread.is_alive()
    assert not errors
    assert max_finite == 1
    assert client._finite_stream_gate.active_count == 0


def test_finite_stream_wait_cancels_promptly_and_stream_429_sets_shared_cooldown():
    archive_connected = threading.Event()
    release_archive = threading.Event()
    waiting_stop = threading.Event()
    request_calls = []

    class BlockingTransport:
        def request(self, *args):
            request_calls.append(args)
            return HttpResponse(200, {}, b'{"username":"owner"}')

        def stream(self, _method, url, _headers, _body, _timeout, _stop, on_connected=None):
            if on_connected:
                on_connected()
            if "/api/games/user/" in url:
                archive_connected.set()
                assert release_archive.wait(2)
                yield b'{"id":"g1"}\n'
            else:
                yield b'{"date":1,"win":true,"puzzle":{"id":"p1"}}\n'

    client = LichessClient(BlockingTransport(), base_url="https://lichess.test")
    errors = []

    def archive():
        try:
            list(client.stream_user_games("fake", "owner", threading.Event()))
        except Exception as exc:
            errors.append(exc)

    def puzzle():
        try:
            list(client.stream_puzzle_activity("fake", waiting_stop))
        except Exception as exc:
            errors.append(exc)

    archive_thread = threading.Thread(target=archive)
    puzzle_thread = threading.Thread(target=puzzle)
    archive_thread.start()
    assert archive_connected.wait(1)
    puzzle_thread.start()
    assert client._finite_stream_gate._condition.acquire(timeout=1)
    try:
        assert client._finite_stream_gate._condition.wait_for(
            lambda: client._finite_stream_gate.waiting_count == 1, timeout=1)
    finally:
        client._finite_stream_gate._condition.release()
    waiting_stop.set()
    puzzle_thread.join(1)
    assert not puzzle_thread.is_alive()
    assert any(isinstance(exc, InterruptedError) for exc in errors)
    release_archive.set()
    archive_thread.join(1)
    assert not archive_thread.is_alive()

    class RateLimitedTransport:
        def __init__(self):
            self.request_calls = 0

        def request(self, *args):
            self.request_calls += 1
            return HttpResponse(200, {}, b'{"username":"owner"}')

        def stream(self, *_args, **_kwargs):
            if False:
                yield b""
            raise RateLimitError(125)

    rate_limited = RateLimitedTransport()
    client = LichessClient(rate_limited, base_url="https://lichess.test")
    with __import__("pytest").raises(RateLimitError) as caught:
        list(client.stream_user_games("fake", "owner", threading.Event()))
    assert caught.value.retry_after >= 125
    with __import__("pytest").raises(RateLimitError):
        client.get_account("fake")
    assert rate_limited.request_calls == 0
