import threading

from chess_coach.application import background_work
from chess_coach.application.background_work import BackgroundWorkCoordinator
from chess_coach.domain.work_context import reset_work_priority, set_work_priority
from chess_coach.lichess.api import HttpResponse, LichessClient
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
