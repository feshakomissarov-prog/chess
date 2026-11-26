"""Fake-transport rated result safety; never contacts Lichess or a real keyring."""

import json
import time
from types import SimpleNamespace

import chess
import pytest
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

import chess_coach.window as window_module
from chess_coach.lichess.api import HttpResponse, LichessClient
from chess_coach.lichess.puzzle_training import PuzzleStatus, normalize_api_puzzle
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
from chess_coach.window import MainWindow
from chess_coach.bootstrap import create_runtime


class FakeTokenStore:
    def __init__(self, *, service, user):
        assert service == "ChessCoach.Lichess.Puzzles"
        assert user == "puzzle-rated-token"

    def get(self):
        return "synthetic-rated-token"


RATED_PUZZLE = {
    "game": {"id": "rated-fixture-game", "pgn": "1. e4 e5"},
    "puzzle": {"id": "puzzle-frozen", "initialPly": 1,
               "lastMove": "e7e5", "solution": ["d1h5", "b8c6", "f1c4"],
               "themes": ["short"], "rating": 1500, "plays": 100},
}


def isolated_rated_window(tmp_path, name="rated-window"):
    """Use a repository under tmp_path, never the owner profile or database."""
    database = tmp_path / f"{name}.sqlite3"
    initial = GameRepository(database)
    initial.shutdown()
    settings = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/database_file", str(database))
    settings.sync()
    runtime = create_runtime(settings=settings, manual_connect_mode=True)
    return MainWindow(runtime=runtime), settings, database


def mount_rated_page(window, *, puzzle_payload=RATED_PUZZLE):
    page = PuzzleTrainingPage(parent=window)
    page.account_id = "lichess:fixture-user"
    page.set_mode("LICHESS_RATED")
    puzzle = normalize_api_puzzle(puzzle_payload)
    page.load_puzzle(puzzle, puzzle_payload, source="lichess")
    page.account_id = "lichess:fixture-user"
    page._session_account_id = page.account_id
    window._puzzle_training_page = page
    window._puzzle_page = page
    window.screen_stack.addWidget(page)
    window.screen_stack.setCurrentWidget(page)
    return page


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
    page.set_mode("LICHESS_RATED")
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
    deadline = time.monotonic() + 2.5
    while (not page.rated_status.text().startswith("Результат Lichess неизвестен")
           and time.monotonic() < deadline):
        QTest.qWait(25)
    assert page.rated_status.text().startswith("Результат Lichess неизвестен"), (
        f"Rated result UI did not update: text={page.rated_status.text()!r}; "
        f"stored={window.game_repository.rated_puzzle_attempt(page._session_id)!r}; "
        f"current_page={window.screen_stack.currentWidget() is page}; "
        f"session={page._session_id!r}; account={page.account_id!r}")
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


def test_unknown_reconcile_uses_official_nested_activity(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeTokenStore)
    window, _settings, _database = isolated_rated_window(tmp_path, "nested-activity")
    qtbot.addWidget(window)
    page = mount_rated_page(window)
    attempt = {"attempt_id": "nested-activity-attempt", "session_id": "nested-activity-attempt",
               "account_id": page.account_id, "puzzle_id": "puzzle-frozen",
               "mode": "LICHESS_RATED", "rated": True, "win": True,
               "angle": "mix", "difficulty": "normal"}
    row, _ = window.game_repository.prepare_rated_puzzle_attempt(attempt)
    window.game_repository.update_rated_puzzle_attempt(
        attempt["attempt_id"], "unknown", error="response lost after possible commit")

    class Client:
        def __init__(self):
            self.activity_calls = 0

        def get_account(self, _token):
            return {"username": "fixture-user"}

        def stream_puzzle_activity(self, _token, _stop, *, since, max_items):
            self.activity_calls += 1
            assert since <= row["created_at_ms"]
            assert max_items <= 100
            yield {"date": row["created_at_ms"] + 2_000, "win": True,
                   "puzzle": {"id": "puzzle-frozen"}}

    client = Client()
    window.lichess = SimpleNamespace(client=client)
    page.rated_reconcile_requested.connect(
        lambda attempt_id: window._reconcile_rated_puzzle_result(page, attempt_id))
    page.apply_rated_result("unknown", win=True, attempt_id=attempt["attempt_id"],
                            attempt_payload=attempt)
    page.reconcile_rated_button.show()
    page.reconcile_rated_button.setEnabled(True)

    QTest.mouseClick(page.reconcile_rated_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.game_repository.rated_puzzle_attempt(
        attempt["attempt_id"])["status"] == "confirmed", timeout=3000)

    confirmed = window.game_repository.rated_puzzle_attempt(attempt["attempt_id"])
    assert client.activity_calls == 1
    assert confirmed["server_rating_diff"] is None and confirmed["server_rating"] is None
    assert "изменение рейтинга недоступно" in page.rated_status.text().casefold()
    assert not page.reconcile_rated_button.isVisible()
    assert window.game_repository.unresolved_rated_puzzle_attempts(page.account_id) == []
    page.state.status = PuzzleStatus.SUCCESS
    window.close()


def test_unknown_reconcile_rejects_stale_malformed_and_foreign_activity(
        qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeTokenStore)
    window, _settings, _database = isolated_rated_window(tmp_path, "invalid-activity")
    qtbot.addWidget(window)
    page = mount_rated_page(window)
    attempt = {"attempt_id": "invalid-activity-attempt", "session_id": "invalid-activity-attempt",
               "account_id": page.account_id, "puzzle_id": "puzzle-frozen",
               "mode": "LICHESS_RATED", "rated": True, "win": True,
               "angle": "mix", "difficulty": "normal"}
    row, _ = window.game_repository.prepare_rated_puzzle_attempt(attempt)
    window.game_repository.update_rated_puzzle_attempt(attempt["attempt_id"], "unknown")

    class Client:
        username = "another-user"

        def get_account(self, _token):
            return {"username": self.username}

        def stream_puzzle_activity(self, *_args, **_kwargs):
            yield {"date": row["created_at_ms"] - 120_000, "win": True,
                   "puzzle": {"id": "puzzle-frozen"}}
            yield {"date": row["created_at_ms"] + 2_000, "win": "true",
                   "puzzle": {"id": "puzzle-frozen"}}
            yield {"date": row["created_at_ms"] + 2_000, "win": True,
                   "puzzle": {"id": "another-puzzle"}}

    window.lichess = SimpleNamespace(client=Client())
    page.rated_reconcile_requested.connect(
        lambda attempt_id: window._reconcile_rated_puzzle_result(page, attempt_id))
    page.apply_rated_result("unknown", win=True, attempt_id=attempt["attempt_id"],
                            attempt_payload=attempt)
    QTest.mouseClick(page.reconcile_rated_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "другому аккаунту" in page.rated_status.text().casefold(),
                    timeout=3000)
    assert window.game_repository.rated_puzzle_attempt(
        attempt["attempt_id"])["status"] == "unknown"
    client = window.lichess.client
    client.username = "fixture-user"
    QTest.mouseClick(page.reconcile_rated_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.rated_status.text().startswith(
        "Активность не позволила однозначно"), timeout=3000)
    assert window.game_repository.rated_puzzle_attempt(attempt["attempt_id"])["status"] == "unknown"
    assert "Повторный POST не выполнялся" in page.rated_status.text()
    page.state.status = PuzzleStatus.SUCCESS
    window.close()


def test_manual_next_cannot_bypass_pending_rated_ack(qtbot):
    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.set_mode("LICHESS_RATED")
    page.load_puzzle(normalize_api_puzzle(RATED_PUZZLE), RATED_PUZZLE, source="lichess")
    original_session = page._session_id
    second_payload = {"game": {"id": "next", "pgn": "1. e4 e5"},
                      "puzzle": {**RATED_PUZZLE["puzzle"], "id": "puzzle-next"}}
    page.set_prepared_puzzles(page.account_id, "mix", "normal", [
        (normalize_api_puzzle(second_payload), second_payload),
        (normalize_api_puzzle({**second_payload, "puzzle": {
            **second_payload["puzzle"], "id": "puzzle-next-2"}}),
         {**second_payload, "puzzle": {**second_payload["puzzle"], "id": "puzzle-next-2"}}),
    ])
    requests = []
    page.new_puzzle_requested.connect(lambda *args: requests.append(args))
    page.apply_rated_result("unknown", win=True, attempt_id=original_session,
                            attempt_payload={"account_id": page.account_id,
                                             "attempt_id": original_session})

    assert not page.fetch.isEnabled() and not page.next.isEnabled()
    QTest.mouseClick(page.next, Qt.MouseButton.LeftButton)
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    assert page._session_id == original_session and requests == []
    assert len(page._prepared_online_queue) == 2

    page.apply_rated_result("confirmed", win=True, attempt_id=original_session,
                            attempt_payload={"account_id": page.account_id,
                                             "attempt_id": original_session})
    assert page.fetch.isEnabled()
    QTest.mouseClick(page.fetch, Qt.MouseButton.LeftButton)
    assert page._session_puzzle_id == "puzzle-next"
    page.close()


def test_prepost_failure_and_prepared_restart_recovery(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeTokenStore)
    window, settings, database = isolated_rated_window(tmp_path, "prepost-recovery")
    qtbot.addWidget(window)
    page = mount_rated_page(window)

    class Client:
        def __init__(self):
            self.account_failures = 1
            self.account_calls = 0
            self.posts = []

        def get_account(self, _token):
            self.account_calls += 1
            if self.account_failures:
                self.account_failures -= 1
                raise TimeoutError("account lookup timed out before POST")
            return {"username": "fixture-user"}

        def submit_puzzle_round(self, _token, angle, puzzle_id, win, *, rated, next_count):
            self.posts.append((angle, puzzle_id, win, rated, next_count))
            return {"rounds": [{"id": puzzle_id, "win": win, "ratingDiff": 4}],
                    "glicko": {"rating": 1704}}

    client = Client()
    window.lichess = SimpleNamespace(client=client)
    page.rated_retry_requested.connect(
        lambda frozen: window._submit_rated_puzzle_result(page, frozen, retry=True))
    frozen = {"attempt_id": page._session_id, "session_id": page._session_id,
              "account_id": page.account_id, "puzzle_id": page._session_puzzle_id,
              "mode": "LICHESS_RATED", "rated": True, "win": True,
              "angle": "mix", "difficulty": "normal",
              "puzzle_payload": RATED_PUZZLE}

    window._submit_rated_puzzle_result(page, frozen)
    qtbot.waitUntil(lambda: window.game_repository.rated_puzzle_attempt(
        frozen["attempt_id"])["status"] == "not_sent", timeout=3000)
    failed = window.game_repository.rated_puzzle_attempt(frozen["attempt_id"])
    assert client.posts == []
    assert json.loads(failed["payload_json"])["puzzle_id"] == "puzzle-frozen"
    assert page.reconcile_rated_button.isVisible()
    QTest.mouseClick(page.reconcile_rated_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.game_repository.rated_puzzle_attempt(
        frozen["attempt_id"])["status"] == "confirmed", timeout=3000)
    assert client.posts == [("mix", "puzzle-frozen", True, True, 1)]

    # A persisted `prepared` result after restart is shown for an explicit send;
    # it must not submit during startup or fetch a different ranked task.
    page.state.status = PuzzleStatus.SUCCESS
    window.close()
    repo = GameRepository(database)
    prepared_payload = {**frozen, "attempt_id": "prepared-after-restart",
                        "session_id": "prepared-after-restart", "puzzle_id": "puzzle-restart"}
    repo.prepare_rated_puzzle_attempt(prepared_payload)
    repo.shutdown()
    settings.setValue(f"puzzles/rated_consent/{page.account_id}", True)
    settings.sync()
    runtime = create_runtime(settings=settings, manual_connect_mode=True)
    restarted = MainWindow(runtime=runtime)
    qtbot.addWidget(restarted)
    restart_client = Client()
    restart_client.account_failures = 0
    restarted.lichess = SimpleNamespace(client=restart_client)
    restarted.show_puzzle_training(account_id="lichess:fixture-user", mode="LICHESS_RATED")
    recovered_page = restarted._puzzle_training_page
    assert restart_client.posts == [] and restart_client.account_calls == 0
    assert recovered_page.reconcile_rated_button.isVisible()
    assert recovered_page.reconcile_rated_button.text() == "Отправить сохранённый результат"
    QTest.mouseClick(recovered_page.reconcile_rated_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: restarted.game_repository.rated_puzzle_attempt(
        "prepared-after-restart")["status"] == "confirmed", timeout=3000)
    assert restart_client.posts == [("mix", "puzzle-restart", True, True, 1)]
    recovered_page.state.status = PuzzleStatus.SUCCESS
    restarted.close()
