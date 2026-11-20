import json
from urllib.parse import parse_qs, urlparse

import pytest

from chess_coach.lichess.api import HttpResponse, LichessClient


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return HttpResponse(200, {}, json.dumps(self.payload).encode())


def test_training_adapter_uses_bounded_authenticated_read_only_get():
    transport = FakeTransport({"puzzles": [{"game": {}, "puzzle": {"id": "p"}}],
                               "glicko": {"rating": 1750}})
    client = LichessClient(transport, base_url="https://lichess.test")
    payload = client.get_puzzle_batch("secret-token", angle="pin", difficulty="harder", count=50)
    method, url, headers, body = transport.calls[0]
    parsed = urlparse(url)
    assert method == "GET"
    assert parsed.path == "/api/puzzle/batch/pin"
    assert parse_qs(parsed.query) == {"difficulty": ["harder"], "nb": ["5"]}
    assert headers["Authorization"] == "Bearer secret-token"
    assert body is None
    assert len(payload["puzzles"]) == 1


@pytest.mark.parametrize("angle,difficulty", [("", "normal"), ("bad theme!", "normal"),
                                                ("pin", "1900")])
def test_training_adapter_rejects_untrusted_filter_values(angle, difficulty):
    client = LichessClient(FakeTransport({"puzzles": []}), base_url="https://lichess.test")
    with pytest.raises(ValueError):
        client.get_puzzle_batch("token", angle=angle, difficulty=difficulty)
