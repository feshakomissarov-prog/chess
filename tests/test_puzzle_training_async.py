import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from PySide6.QtCore import QSettings, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from chess_coach import window as window_module
from chess_coach.window import MainWindow
from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
import chess


def _official_example():
    fixture = Path(__file__).parent / "fixtures" / "lichess_puzzle_batch_official_example.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["puzzle"]


class _FakeCallback:
    redirect_uri = "http://127.0.0.1/callback"

    def cancel(self):
        return None

    def close(self):
        return None


class _FakePuzzleClient:
    def __init__(self):
        self.batch_arguments = []

    def get_account(self, _token):
        return {"username": "fixture-user"}

    def get_puzzle_batch(self, _token, **kwargs):
        self.batch_arguments.append(dict(kwargs))
        return {"puzzles": [_official_example()],
                "glicko": {"rating": 1600, "deviation": 75}}


@pytest.mark.parametrize("cancel_after_open", [False, True])
@pytest.mark.parametrize("difficulty", ["easiest", "normal", "hardest"])
def test_oauth_browser_open_is_dispatched_on_qt_main_thread(
        qtbot, tmp_path, monkeypatch, cancel_after_open, difficulty):
    app = QApplication.instance()
    window = MainWindow(QSettings(str(tmp_path / "oauth-thread.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    client = _FakePuzzleClient()
    service = SimpleNamespace(client=client,
                              capture_operation_context=lambda **_kwargs: None)
    window.lichess = service

    class FakeStore:
        def __init__(self, **_kwargs):
            pass

        def get(self):
            return None

        def set(self, _token):
            return None

    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeStore)
    monkeypatch.setattr(window_module, "OAuthCallbackServer", _FakeCallback)
    monkeypatch.setattr(window_module, "create_authorization_request",
                        lambda *_args, **_kwargs: SimpleNamespace(
                            url="https://lichess.org/oauth/test", redirect_uri="http://127.0.0.1/callback"))
    monkeypatch.setattr(window_module, "authorize_and_store",
                        lambda *_args, **_kwargs: ("fake-read-token", "fixture-user"))
    calls = []
    opened = threading.Event()

    def open_url(url: QUrl) -> bool:
        calls.append((str(url.toString()), QThread.currentThread() == app.thread()))
        opened.set()
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    window.show_puzzle_training("local")
    page = window._puzzle_training_page
    page.more_difficulty.setChecked(difficulty != "normal")
    if difficulty != "normal":
        page.advanced_difficulty.setCurrentIndex(page.advanced_difficulty.findData(difficulty))
    window._request_puzzle_training(page, page.selected_theme(),
                                    page.selected_difficulty(), authorize=True)
    qtbot.waitUntil(opened.is_set, timeout=5000)
    if cancel_after_open:
        page.session_hidden.emit()
    qtbot.waitUntil(lambda: page.state.puzzle is not None or not window._puzzle_training_handle.is_alive(),
                    timeout=5000)
    assert calls and calls[0][0].startswith("https://lichess.org/oauth/")
    assert calls[0][1], "QDesktopServices.openUrl ran outside QApplication's Qt thread"
    if not cancel_after_open:
        assert client.batch_arguments == [{
            "angle": page.selected_theme(), "difficulty": difficulty, "count": 1}]
    window.close()


@pytest.mark.parametrize("delay_ms", (50, 500, 3000))
def test_delayed_save_ack_refreshes_reopened_page_after_navigation(qtbot, tmp_path, delay_ms):
    window = MainWindow(QSettings(str(tmp_path / "save-navigation.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    old_page = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(old_page)
    old_page.account_id = "local"
    old_page.load_puzzle(old_page.state.puzzle, old_page._puzzle_payload, source="demo")
    attempts = []
    old_page.attempt_saved.connect(attempts.append)
    old_page._submit_move(chess.Move.from_uci("d7d5"))
    attempt = attempts[-1]

    class DelayedRepository:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.saved = {}

        def save_local_puzzle_attempt(self, account_id, puzzle_id, **payload):
            self.started.set()
            assert self.release.wait(delay_ms / 1000 + 5)
            attempt_id = payload["attempt_id"]
            self.saved.setdefault(attempt_id, (account_id, puzzle_id, payload))
            return attempt_id, len(self.saved) == 1

        def local_puzzle_attempts(self, _account_id, *, limit):
            return [{"puzzle_id": attempt["puzzle_id"], "result": "success",
                     "solved": True, "created_at_ms": 1_700_000_000_000,
                     "source": "local", "themes": ["fork"]}]

        def local_puzzle_attempt_count(self, _account_id):
            return 1

        def local_puzzle_statistics(self, _account_id, _theme=None):
            return {"attempts": 1, "solved": 1, "success_percent": 100.0}

        def local_puzzle_rating(self, _account_id):
            return {"rating": 1508, "delta": 8, "rated_attempts": 1}

        def local_puzzle_rating_event(self, _account_id, _puzzle_id):
            return {"delta": 8}

        def has_pending_writes(self):
            return False

    repository = DelayedRepository()
    window.game_repository = repository
    window._puzzle_training_page = old_page
    window._save_training_attempt(old_page, attempt)
    qtbot.waitUntil(repository.started.is_set, timeout=5000)
    ui_tick = threading.Event()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(20, ui_tick.set)
    qtbot.waitUntil(ui_tick.is_set, timeout=500)
    qtbot.wait(delay_ms)
    assert old_page.status.text().startswith("✓ Задача решена")

    reopened = PuzzleTrainingPage(demo=True)
    qtbot.addWidget(reopened)
    reopened.account_id = "local"
    window._puzzle_training_page = reopened
    repository.release.set()
    qtbot.waitUntil(lambda: reopened.history.count() == 1, timeout=5000)
    assert reopened._local_count == 1
    assert len(repository.saved) == 1
    duplicate_ack = {**attempt, "local_count": 1,
                     "recent": [{"puzzle_id": attempt["puzzle_id"], "result": "success",
                                 "solved": True, "created_at_ms": 1_700_000_000_000,
                                 "source": "local", "themes": ["fork"]}],
                     "local_stats": {"attempts": 1, "solved": 1,
                                     "success_percent": 100.0},
                     "theme_stats": {"attempts": 1, "solved": 1,
                                     "success_percent": 100.0}}
    window.puzzle_attempt_save_finished.emit(duplicate_ack, None)
    assert reopened.history.count() == 1
    window.close()
    old_page.close()
    reopened.close()
    qtbot.wait(100)


@pytest.mark.parametrize("puzzle_json", ["{broken", "[]", "null",
                                        '{"puzzle":{"themes":["fork"],"rating":1500}}'])
def test_committed_attempt_is_success_even_when_optional_read_model_fails(
        qtbot, tmp_path, monkeypatch, puzzle_json):
    window = MainWindow(QSettings(str(tmp_path / "post-commit-ack.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    page = PuzzleTrainingPage(demo=True)
    account_id = window.game_repository.ensure_archive_account("post-commit-ack")
    page.account_id = account_id
    window._puzzle_training_page = page
    attempt = {"attempt_id": f"ack-{len(puzzle_json)}", "account_id": account_id,
               "puzzle_id": "legacy-ack", "session_id": "session", "result": "success",
               "solved": True, "mistakes": 0, "hints": 0, "duration_ms": 10,
               "puzzle_json": puzzle_json, "rating_eligible": True}
    original_read = window.game_repository.local_puzzle_attempts
    monkeypatch.setattr(window.game_repository, "local_puzzle_attempts",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("read-model unavailable")))
    acknowledgements = []
    window.puzzle_attempt_save_finished.connect(
        lambda payload, error: acknowledgements.append((payload, error)))
    window._save_training_attempt(page, attempt)
    qtbot.waitUntil(lambda: bool(acknowledgements), timeout=5000)
    stored = window.game_repository.local_puzzle_attempt_count(account_id)
    assert stored == 1
    assert acknowledgements[0][1] is None
    assert acknowledgements[0][0]["attempt_id"] == attempt["attempt_id"]
    assert window.game_repository.local_puzzle_rating(account_id)["rated_attempts"] == int(
        puzzle_json.startswith('{"puzzle"'))
    monkeypatch.setattr(window.game_repository, "local_puzzle_attempts", original_read)
    window.close()


def test_late_failed_ack_attaches_durable_retry_to_reopened_same_account_page(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "late-failed-save.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    account = window.game_repository.ensure_archive_account("late-save-account")
    old_page = PuzzleTrainingPage(demo=True)
    old_page.account_id = account
    new_page = PuzzleTrainingPage(demo=True)
    new_page.account_id = account
    window._puzzle_training_page = old_page
    attempt = {"attempt_id": "late-failed-stable-id", "account_id": account,
               "puzzle_id": "late-failed-puzzle", "session_id": "old-page",
               "result": "success", "solved": True, "mistakes": 0, "hints": 0,
               "duration_ms": 20, "puzzle_json": "{}", "rating_eligible": False}
    original_save = window.game_repository.save_local_puzzle_attempt
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("controlled SQLite write failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(window.game_repository, "save_local_puzzle_attempt", fail_once)
    old_page.attempt_saved.connect(lambda retry: window._save_training_attempt(old_page, retry))
    window._save_training_attempt(old_page, attempt)
    window._puzzle_training_page = new_page
    new_page.attempt_saved.connect(lambda retry: window._save_training_attempt(new_page, retry))
    qtbot.waitUntil(lambda: "late-failed-stable-id" in new_page._pending_attempt_saves,
                    timeout=5000)
    assert not new_page.retry_save.isHidden() and new_page.retry_save.isEnabled()
    assert window.game_repository.pending_puzzle_attempts(account)[0]["attempt_id"] == (
        "late-failed-stable-id")
    new_page._retry_attempt_save()
    qtbot.waitUntil(lambda: window.game_repository.local_puzzle_attempt_count(account) == 1,
                    timeout=5000)
    assert window.game_repository.local_puzzle_rating_event(
        account, "late-failed-puzzle") is None
    assert window.game_repository.pending_puzzle_attempts(account) == []
    window.close()


@pytest.mark.parametrize("delay_ms", (100, 1200, 3000))
def test_delayed_live_get_keeps_qt_event_loop_responsive(
        qtbot, tmp_path, monkeypatch, delay_ms):
    window = MainWindow(QSettings(str(tmp_path / "puzzle-get-delay.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    get_started = threading.Event()
    release_get = threading.Event()

    class DelayedClient(_FakePuzzleClient):
        def get_puzzle_batch(self, _token, **_kwargs):
            get_started.set()
            assert release_get.wait(delay_ms / 1000 + 5), \
                "test releases GET after proving Qt responsiveness"
            return {"puzzles": [_official_example()],
                    "glicko": {"rating": 1600, "deviation": 75}}

    service = SimpleNamespace(
        client=DelayedClient(), username="fixture-user", puzzle_username="fixture-user",
        capture_operation_context=lambda **_kwargs: None,
        close=lambda *_args, **_kwargs: True,
    )
    window.lichess = service

    class FakeStore:
        def __init__(self, **_kwargs):
            pass

        def get(self):
            return "isolated-fake-puzzle-read-token"

    monkeypatch.setattr(window_module, "WindowsCredentialStore", FakeStore)
    window.show_puzzle_training("lichess:fixture-user")
    page = window._puzzle_training_page
    assert page is not None
    assert get_started.wait(2)
    ui_tick = threading.Event()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(20, ui_tick.set)
    try:
        qtbot.waitUntil(ui_tick.is_set, timeout=500)
        assert window.screen_stack.currentWidget() is page
        assert page.state.puzzle is None
        assert page.status.text().startswith("Получаю")
        qtbot.wait(delay_ms)
        assert get_started.is_set()
        assert page.state.puzzle is None
    finally:
        release_get.set()
    qtbot.waitUntil(lambda: page.state.puzzle is not None, timeout=5000)
    assert page.state.puzzle.puzzle_id == _official_example()["puzzle"]["id"]
    window.close()
