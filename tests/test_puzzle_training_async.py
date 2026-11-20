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
    def get_account(self, _token):
        return {"username": "fixture-user"}

    def get_puzzle_batch(self, _token, **_kwargs):
        return {"puzzles": [_official_example()],
                "glicko": {"rating": 1600, "deviation": 75}}


@pytest.mark.parametrize("cancel_after_open", [False, True])
def test_oauth_browser_open_is_dispatched_on_qt_main_thread(
        qtbot, tmp_path, monkeypatch, cancel_after_open):
    app = QApplication.instance()
    window = MainWindow(QSettings(str(tmp_path / "oauth-thread.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    window.offline_mode = False
    service = SimpleNamespace(client=_FakePuzzleClient(),
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
    window._request_puzzle_training(page, "mix", "normal", authorize=True)
    qtbot.waitUntil(opened.is_set, timeout=5000)
    if cancel_after_open:
        page.session_hidden.emit()
    qtbot.waitUntil(lambda: page.state.puzzle is not None or not window._puzzle_training_handle.is_alive(),
                    timeout=5000)
    assert calls and calls[0][0].startswith("https://lichess.org/oauth/")
    assert calls[0][1], "QDesktopServices.openUrl ran outside QApplication's Qt thread"
    window.close()


def test_delayed_save_ack_refreshes_reopened_page_after_navigation(qtbot, tmp_path):
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
            assert self.release.wait(5)
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

        def has_pending_writes(self):
            return False

    repository = DelayedRepository()
    window.game_repository = repository
    window._puzzle_training_page = old_page
    window._save_training_attempt(old_page, attempt)
    qtbot.waitUntil(repository.started.is_set, timeout=5000)

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
