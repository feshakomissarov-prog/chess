"""Fake-transport rated result safety; never contacts Lichess or a real keyring."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

import chess_coach.window as window_module
from chess_coach.lichess.api import HttpResponse, LichessClient
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
from chess_coach.window import MainWindow


class FakeTokenStore:
    def __init__(self, *, service, user):
        assert service == "ChessCoach.Lichess.Puzzles"
        assert user == "puzzle-rated-token"

    def get(self):
        return "synthetic-rated-token"


def test_ambiguous_post_is_durable_unknown_and_never_retried(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeTokenStore)
    monkeypatch.setattr(MainWindow, "_refresh_home_dashboard", lambda _self: None)
    window = MainWindow(
        QSettings(str(tmp_path / "rated.ini"), QSettings.Format.IniFormat),
        offline_mode=True,
    )
    qtbot.addWidget(window)
    window.offline_mode = False

    class Client:
        def __init__(self):
            self.posts = []

        def get_account(self, _token):
            return {"username": "fixture-user"}

        def submit_puzzle_round(self, token, angle, puzzle_id, win, *, rated, next_count):
            self.posts.append((token, angle, puzzle_id, win, rated, next_count))
            # Model a server that applied the result but whose response was lost.
            raise TimeoutError("response lost after possible commit")

    client = Client()
    window.lichess = SimpleNamespace(
        client=client, is_auth_generation_current=lambda _generation: True)
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.account_id = "lichess:fixture-user"
    page._session_account_id = page.account_id
    page._session_id = "session-unknown"
    page._session_puzzle_id = "puzzle-unknown"
    window._puzzle_training_page = page
    window._puzzle_page = page
    window.screen_stack.addWidget(page)
    window.screen_stack.setCurrentWidget(page)
    payload = {
        "attempt_id": page._session_id,
        "session_id": page._session_id,
        "account_id": page._session_account_id,
        "puzzle_id": page._session_puzzle_id,
        "mode": "LICHESS_RATED", "rated": True, "win": True,
        "angle": "mix", "difficulty": "normal",
    }

    window._submit_rated_puzzle_result(page, payload)
    qtbot.waitUntil(lambda: page.rated_status.text().startswith("Результат Lichess неизвестен"),
                    timeout=5000)
    row = window.game_repository.rated_puzzle_attempt(page._session_id)
    assert row["status"] == "unknown"
    assert row["server_rating_diff"] is None
    assert not page.reconcile_rated_button.isHidden()

    # A repeated UI/controller event for the same attempt reads the durable row;
    # it does not repeat the non-idempotent POST.
    window._submit_rated_puzzle_result(page, payload)
    qtbot.wait(100)
    assert len(client.posts) == 1
    assert client.posts[0][4:] == (True, 1)
    window.game_repository.shutdown()
    window.close()


def test_post_response_with_wrong_round_id_is_not_an_ack():
    class Transport:
        def request(self, method, url, headers, body, timeout):
            return HttpResponse(200, {},
                                b'{"rounds":[{"id":"another","win":true,"ratingDiff":9}]}')

        def stream(self, *args, **kwargs):
            raise AssertionError("stream not expected")

    client = LichessClient(transport=Transport(), base_url="https://lichess.test")
    with pytest.raises(Exception, match="не подтвердил результат"):
        client.submit_puzzle_round("synthetic", "mix", "expected", True)
