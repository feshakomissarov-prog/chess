"""Targeted regressions for the consolidated two-mode puzzle workflow."""

import json
import time

import chess
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from chess_coach.lichess.puzzle_training import (
    normalize_activity_puzzle,
    normalize_legacy_csv_puzzle,
    select_api_puzzle_queue,
)


ACTIVITY = {
    "id": "0haqU",
    "fen": "6k1/1q3p2/5prp/2b5/3pN3/1PP2P1P/2Q1R1P1/6K1 b - - 0 1",
    "lastMove": "f2f3",
    "solution": ["d4d3", "e4c5", "d3c2", "c5b7", "c2c1q"],
    "themes": ["advantage", "crushing"],
    "rating": 1638,
    "plays": 314,
}


def test_official_activity_fen_is_already_playable_and_first_move_is_preserved():
    playable = normalize_activity_puzzle(
        ACTIVITY["id"], ACTIVITY["fen"], ACTIVITY["solution"],
        themes=ACTIVITY["themes"], rating=ACTIVITY["rating"],
        plays=ACTIVITY["plays"], last_move=ACTIVITY["lastMove"],
    )
    board = chess.Board(ACTIVITY["fen"])
    assert playable.player_to_move_fen == board.fen()
    assert playable.board().turn == chess.BLACK
    assert playable.last_move == "f2f3"
    assert playable.expected_user_moves == tuple(ACTIVITY["solution"])
    assert chess.Move.from_uci("d4d3") in playable.board().legal_moves


def test_legacy_csv_prep_format_remains_explicit_and_separate():
    before_prep = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    playable = normalize_legacy_csv_puzzle(
        "csv-fixture", before_prep, ["e2e4", "e7e5"])
    assert playable.board().turn == chess.BLACK
    assert playable.expected_user_moves == ("e7e5",)


def test_api_batch_queue_keeps_distinct_valid_candidates_and_skips_bad_members():
    def payload(pid, pgn, move):
        return {
            "game": {"id": f"game-{pid}", "pgn": pgn},
            "puzzle": {"id": pid, "initialPly": 2, "solution": [move],
                       "themes": ["fork"], "rating": 1500},
        }

    batch = [payload("a", "1. e4 e5 2. Nf3", "d7d5"),
             {"game": None, "puzzle": {}},
             payload("b", "1. e4 c5 2. Nf3", "d7d6"),
             payload("a", "1. e4 e5 2. Nf3", "d7d5"),
             payload("c", "1. d4 Nf6 2. c4", "e7e6")]
    queue = select_api_puzzle_queue(batch, set(), theme="mix", max_count=5)
    assert [item[0].puzzle_id for item in queue] == ["a", "b", "c"]
    assert len({item[0].player_to_move_fen for item in queue}) == 3


def test_training_mode_is_unrated_and_does_not_emit_rating_request(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from chess_coach.lichess.puzzle_training import normalize_api_puzzle
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    rated_requests = []
    page.rated_result_requested.connect(rated_requests.append)
    payload = {"game": {"id": "game", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "train", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["fork"]}}
    page.set_mode("TRAINING")
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    monkeypatch.setattr(
        "chess_coach.ui.pages.puzzle_training.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    page._hint()
    assert page.attempt_mode == "TRAINING"
    assert rated_requests == []
    assert "Тренировка" in page.mode_status.text()
    page.rated_mode_button.click()
    assert page.mode == "LICHESS_RATED"
    assert page.rated_mode_button.isChecked() and not page.training_mode_button.isChecked()
    page.training_mode_button.click()
    assert page.mode == "TRAINING"


def test_first_rated_loss_is_emitted_once_and_filter_is_frozen(qtbot):
    from chess_coach.lichess.puzzle_training import normalize_api_puzzle
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page.set_mode("LICHESS_RATED")
    mate2 = page.theme.findData("mateIn2")
    page.theme.setCurrentIndex(mate2)
    payload = {"game": {"id": "game", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "ranked-one", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["mateIn2"]}}
    page.account_id = "lichess:fixture"
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    requests = []
    page.rated_result_requested.connect(requests.append)
    page.theme.setCurrentIndex(page.theme.findData("mateIn3"))
    page._record_first_failure()
    page._hint()
    assert len(requests) == 1
    assert requests[0]["win"] is False and requests[0]["rated"] is True
    assert requests[0]["angle"] == "mateIn2"
    assert page.attempt_mode == "LICHESS_RATED"


def test_declined_rated_consent_keeps_current_training_attempt(qtbot, monkeypatch):
    from chess_coach.lichess.puzzle_training import normalize_api_puzzle
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    payload = {"game": {"id": "game", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "kept-on-consent-cancel", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["fork"]}}
    page.set_mode("TRAINING")
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    page.mode_changed.connect(lambda _mode: page.set_mode("TRAINING"))
    monkeypatch.setattr(
        "chess_coach.ui.pages.puzzle_training.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    page.rated_mode_button.click()

    assert page.mode == "TRAINING"
    assert page._session_puzzle_id == "kept-on-consent-cancel"
    assert page._allow_submit
    assert page.state.status.value == "ready"
    assert not page._session_outcome_ids


def test_prepared_queue_is_cleared_on_mode_switch_and_seen_ids_cross_filters(qtbot):
    from chess_coach.lichess.puzzle_training import normalize_api_puzzle
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    first = {"game": {"id": "one", "pgn": "1. e4 e5 2. Nf3"},
             "puzzle": {"id": "seen-training", "initialPly": 2,
                        "solution": ["d7d5"], "themes": ["fork"]}}
    second = {"game": {"id": "two", "pgn": "1. d4 d5 2. c4"},
              "puzzle": {"id": "queued-training", "initialPly": 2,
                         "solution": ["e7e6"], "themes": ["fork"]}}
    page.load_puzzle(normalize_api_puzzle(first), first, source="lichess")
    page.set_prepared_puzzle("local", "mix", "normal",
                             normalize_api_puzzle(second), second)
    assert page.online_seen_ids("local", "mateIn3", "hardest") == {"seen-training"}

    page.set_mode("LICHESS_RATED")

    assert page.take_prepared_puzzle("local", "mix", "normal") is None
    assert not page._prepared_online_queue


def test_visible_results_stay_scoped_to_the_attempt_mode(qtbot, monkeypatch):
    from chess_coach.lichess.puzzle_training import normalize_api_puzzle
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    payload = {"game": {"id": "scope-game", "pgn": "1. e4 e5 2. Nf3"},
               "puzzle": {"id": "scope-training", "initialPly": 2,
                          "solution": ["d7d5"], "themes": ["fork"]}}
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    monkeypatch.setattr(
        "chess_coach.ui.pages.puzzle_training.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    page.rated_mode_button.click()

    assert page.mode == "LICHESS_RATED"
    assert page._session_puzzle_id == "scope-training"
    assert page._session_results == []
    training_results = page._session_results_by_scope[("local", "TRAINING")]
    assert len(training_results) == 1
    assert training_results[0]["puzzle_id"] == "scope-training"
    assert training_results[0]["completed"]
    page.training_mode_button.click()
    assert page._session_results is training_results


def test_rated_not_sent_offers_explicit_retry_after_cooldown(qtbot):
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page.resize(1200, 800)
    page.show()
    page._session_id = "attempt-retry"
    page._session_account_id = "lichess:fixture"
    page._session_puzzle_id = "puzzle-retry"
    page._attempt_theme = "mateIn2"
    page._attempt_difficulty = "normal"
    page.attempt_mode = "LICHESS_RATED"
    retry_payload = {
        "attempt_id": "attempt-retry", "session_id": "attempt-retry",
        "account_id": "lichess:fixture", "puzzle_id": "puzzle-retry",
        "mode": "LICHESS_RATED", "rated": True, "win": False,
        "angle": "mateIn2", "difficulty": "normal",
    }
    retries = []
    page.rated_retry_requested.connect(retries.append)
    page.apply_rated_result("not_sent", win=False, message="429",
                            attempt_id="attempt-retry", attempt_payload=retry_payload)
    assert page.reconcile_rated_button.text() == "Повторить отправку"

    page.set_rate_limit_cooldown(2)
    assert not page.reconcile_rated_button.isEnabled()
    page._cooldown_seconds = 0
    page._cooldown_tick()
    page.reconcile_rated_button.click()

    assert retries == [retry_payload]


def test_rated_auto_next_starts_only_after_confirmed_result(qtbot):
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
    from chess_coach.lichess.puzzle_training import PuzzleStatus

    page = PuzzleTrainingPage()
    qtbot.addWidget(page)
    page._session_id = "rated-auto-next"
    page.attempt_mode = "LICHESS_RATED"
    page.state.status = PuzzleStatus.SUCCESS
    page.auto_next.setChecked(True)

    page.apply_rated_result("confirmed", win=True)

    assert page._auto_next_timer.isActive()
    assert page._auto_next_session_id == "rated-auto-next"


def test_close_partial_rated_attempt_preserves_unfinished_outcome(qtbot, tmp_path):
    from chess_coach.bootstrap import create_runtime
    from chess_coach.lichess.puzzle_training import PuzzleStatus, normalize_api_puzzle
    from chess_coach.lichess.repository import GameRepository
    from chess_coach.ui.pages.puzzle_training import PuzzleTrainingPage
    from chess_coach.window import MainWindow

    database = tmp_path / "partial-close.sqlite3"
    repository = GameRepository(database)
    repository.shutdown()
    settings = QSettings(str(tmp_path / "partial-close.ini"), QSettings.Format.IniFormat)
    settings.setValue("workspace/database_file", str(database))
    settings.sync()
    runtime = create_runtime(settings=settings, manual_connect_mode=True)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    payload = {"game": {"id": "close-game", "pgn": "1. e4 e5"},
               "puzzle": {"id": "close-puzzle", "initialPly": 1, "lastMove": "e7e5",
                          "solution": ["d1h5", "b8c6", "f1c4"],
                          "themes": ["short"], "rating": 1500}}
    page = PuzzleTrainingPage(parent=window)
    page.account_id = "lichess:close-user"
    page.set_mode("LICHESS_RATED")
    page.load_puzzle(normalize_api_puzzle(payload), payload, source="lichess")
    page.account_id = "lichess:close-user"
    page._session_account_id = page.account_id
    assert page.state.submit(chess.Move.from_uci("d1h5")) == "correct"
    page.board.set_position(page.state.board)
    window._puzzle_training_page = page
    window._puzzle_page = page
    window.screen_stack.addWidget(page)
    window.screen_stack.setCurrentWidget(page)
    posts = []
    window.lichess = type("Service", (), {"client": type("Client", (), {
        "submit_puzzle_round": lambda _self, *args, **kwargs: posts.append((args, kwargs))})()})()

    clicked = []

    def click_confirmed_close():
        dialogs = [widget for widget in QApplication.topLevelWidgets()
                   if isinstance(widget, QMessageBox) and widget.isVisible()]
        if not dialogs:
            QTimer.singleShot(10, click_confirmed_close)
            return
        dialog = dialogs[-1]
        button = next((item for item in dialog.findChildren(QPushButton)
                       if item.text() == "Закрыть без отправки"), None)
        assert button is not None, f"Close dialog buttons: {[b.text() for b in dialog.findChildren(QPushButton)]}"
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        clicked.append(button.text())

    QTimer.singleShot(0, click_confirmed_close)
    assert window.close()
    assert clicked == ["Закрыть без отправки"]
    assert posts == []

    reopened = GameRepository(database)
    try:
        row = reopened.rated_puzzle_attempt(page._session_id)
        assert row["status"] == "not_sent"
        assert row["last_error"].startswith("ABORTED_NO_SEND:")
        progress = json.loads(row["progress_json"])
        assert progress["moves"] == ["d1h5"]
        assert progress["position_fen"] == page.state.board.fen()
        assert row["win"] == 0
        assert reopened.unresolved_rated_puzzle_attempts(page.account_id) == []
    finally:
        reopened.shutdown()

    settings.setValue(f"puzzles/rated_consent/{page.account_id}", True)
    settings.sync()
    restarted = MainWindow(runtime=create_runtime(settings=settings, manual_connect_mode=True))
    qtbot.addWidget(restarted)
    restarted.show_puzzle_training(account_id=page.account_id, mode="LICHESS_RATED")
    resumed_page = restarted._puzzle_training_page
    assert "прервана" in resumed_page.rated_status.text().casefold()
    assert "не отправлялся" in resumed_page.rated_status.text().casefold()
    resumed_page.state.status = PuzzleStatus.SUCCESS
    restarted.close()


def test_post_round_uses_official_json_contract_and_matching_id_only():
    from chess_coach.lichess.api import HttpResponse, LichessClient

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body))
            response = {"rounds": [{"id": "puzzle-1", "win": True,
                                    "ratingDiff": 12}],
                        "glicko": {"rating": 1812, "deviation": 60,
                                   "provisional": False}, "puzzles": []}
            return HttpResponse(200, {}, json.dumps(response).encode())

        def stream(self, *args, **kwargs):
            raise AssertionError("not used")

    transport = Transport()
    client = LichessClient(transport=transport, base_url="https://lichess.test")
    result = client.submit_puzzle_round("rated-token", "mix", "puzzle-1", True)
    assert result["rounds"][0]["ratingDiff"] == 12
    method, url, headers, body = transport.calls[0]
    assert method == "POST"
    assert url == "https://lichess.test/api/puzzle/batch/mix?nb=1"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "solutions": [{"id": "puzzle-1", "win": True, "rated": True}]
    }


def test_rated_outbox_is_durable_and_unknown_survives_repository_restart(tmp_path):
    from chess_coach.lichess.repository import GameRepository

    path = tmp_path / "rated-attempt.sqlite3"
    attempt = {"attempt_id": "attempt-1", "session_id": "session-1",
               "account_id": "lichess:fixture", "puzzle_id": "puzzle-1",
               "mode": "LICHESS_RATED", "rated": True, "win": True,
               "angle": "mateIn2", "difficulty": "normal"}
    repository = GameRepository(path)
    row, inserted = repository.prepare_rated_puzzle_attempt(attempt)
    assert inserted and row["status"] == "prepared"
    repository.update_rated_puzzle_attempt("attempt-1", "sending")
    repository.update_rated_puzzle_attempt("attempt-1", "unknown",
                                           error="transport ended after possible send")
    repository.shutdown()

    reopened = GameRepository(path)
    try:
        rows = reopened.unresolved_rated_puzzle_attempts("lichess:fixture")
        assert len(rows) == 1
        assert rows[0]["status"] == "unknown"
        duplicate, created = reopened.prepare_rated_puzzle_attempt(attempt)
        assert not created and duplicate["status"] == "unknown"
    finally:
        reopened.shutdown()


def test_training_attempt_is_saved_without_local_cc_elo_even_if_legacy_flag_is_set(tmp_path):
    from chess_coach.lichess.repository import GameRepository

    repository = GameRepository(tmp_path / "training-mode.sqlite3")
    try:
        account = repository.ensure_archive_account("training-mode-user")
        payload = json.dumps({"puzzle": {"id": "training-only", "rating": 2100}})
        repository.save_local_puzzle_attempt(
            account, "training-only", result="success", solved=True, mistakes=0,
            hints=0, duration_ms=10, puzzle_json=payload,
            attempt_id="training-attempt", rating_eligible=True, mode="TRAINING")
        assert repository.local_puzzle_rating(account)["rated_attempts"] == 0
        attempt = repository.local_puzzle_attempts(account)[0]
        assert attempt["mode"] == "TRAINING"
        with repository._connection() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM local_puzzle_rating_events WHERE account_id=?",
                (account,)).fetchone()[0] == 0
    finally:
        repository.shutdown()
