"""Native MainWindow integration using public Lichess GETs and isolated data.

This is an explicit Windows L2 scenario, not pytest and not an authorized
OAuth test. A fake credential is used only to enter the authenticated code
path; the adapter never sends it and fetches public /api/puzzle/{id} responses.
MainWindow runs in offline-runtime mode first, so its SQLite is temporary and
outside the owner's profile.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen

import chess
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from chess_coach.diagnostics import shutdown_logging
from chess_coach.lichess.puzzle_training import normalize_api_puzzle
from chess_coach.ui.theme import VISUAL_TOKENS, stylesheet as desktop_stylesheet
from chess_coach.window import MainWindow
from puz_ux_03_live_public_smoke import capture, click_move, solve_public_line, wait_until


PUBLIC_PUZZLE_IDS = ("0haqU", "00008", "0000D")


class FakeCredentialStore:
    """An inert placeholder; the string is never included in an HTTP request."""

    token = "not-a-real-oauth-token-public-read-only-test"

    def __init__(self, **_kwargs) -> None:
        pass

    def get(self) -> str:
        return type(self).token

    def set(self, token: str) -> None:
        type(self).token = str(token)


class FakeOAuthCallback:
    redirect_uri = "http://127.0.0.1:0/fake-callback"

    def close(self) -> None:
        pass


class PublicPuzzleAdapter:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.request_ids: list[str] = []
        self.trace: list[dict] = []
        self.username = "cc-puz-ux03-isolated"
        self.account_resolution_calls = 0
        self.fail_next = False
        self.block_next = False
        self.block_started = threading.Event()
        self.release_blocked = threading.Event()
        # The second item deliberately repeats the just-solved public puzzle.
        # This gives L2 a deterministic prefetch-before-ACK duplicate race.
        self.puzzle_sequence = ["0haqU", "0haqU", "00008", "0000D"]
        self.batch_arguments: list[dict] = []

    def get_account(self, _token) -> dict:
        """Resolve the fake profile locally; never send the credential."""
        self.account_resolution_calls += 1
        return {"username": self.username}

    def get_puzzle_batch(self, _token, *, angle: str, difficulty: str, count: int):
        self.batch_arguments.append({"angle": angle, "difficulty": difficulty,
                                     "count": count, "token_sent": False})
        if self.block_next:
            self.block_next = False
            self.block_started.set()
            if not self.release_blocked.wait(20):
                raise TimeoutError("Controlled L2 puzzle GET was not released")
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("controlled L2 network failure")
        puzzle_id = self.puzzle_sequence[len(self.request_ids) % len(self.puzzle_sequence)]
        self.request_ids.append(puzzle_id)
        url = f"https://lichess.org/api/puzzle/{puzzle_id}"
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "ChessCoach-PUZ-UX-03-mainwindow-integration",
        })
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        with urlopen(request, timeout=20) as response:
            status = int(response.status)
            raw = response.read()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        payload = json.loads(raw.decode("utf-8"))
        playable = normalize_api_puzzle(payload)
        row = {
            "timestamp_utc": timestamp, "url": url, "status": status,
            "puzzle_id": playable.puzzle_id, "get_ms": elapsed_ms,
            "angle": angle, "difficulty": difficulty, "count": count,
            "authorization_header_sent": False,
            "fake_credential_sent": False,
        }
        self.trace.append(row)
        print("MAINWINDOW_PUBLIC_GET " + json.dumps(row, sort_keys=True))
        if status != 200:
            raise RuntimeError(f"Public Lichess GET returned HTTP {status}")
        return {"puzzles": [payload]}


class FakeLichessService:
    puzzle_username = "cc-puz-ux03-isolated"
    # Main game OAuth may be A while the separate puzzle:read token is B.
    username = "cc-puz-ux03-game-only"

    def __init__(self, client: PublicPuzzleAdapter) -> None:
        self.client = client


def run(output: Path) -> None:
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(desktop_stylesheet(VISUAL_TOKENS))

    # Offline runtime guarantees a temporary root and SQLite file. Only the
    # connection mode and service adapter are replaced after construction.
    settings = QSettings(str(output / "isolated-settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, offline_mode=True)
    if not window.runtime.offline_data_dir or window.lichess is not None:
        raise AssertionError("MainWindow did not start in an isolated offline runtime")
    window.offline_mode = False
    client = PublicPuzzleAdapter(output)
    window.lichess = FakeLichessService(client)

    import chess_coach.window as window_module
    window_module.WindowsCredentialStore = FakeCredentialStore

    window.resize(1672, 941)
    window.show()
    window.raise_()
    window.activateWindow()
    window.open_puzzle_training()
    page = window._puzzle_training_page
    if page is None:
        raise AssertionError("MainWindow did not create the production puzzle page")
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU", timeout_ms=30000)
    wait_until(app, lambda: page._prepared_online_puzzle is not None, timeout_ms=30000)
    frames: list[dict] = []
    capture(page, output, len(frames), "mainwindow-first-public-ready", frames)
    account_id = page.account_id
    if not account_id.startswith("lichess:cc-puz-ux03-isolated"):
        raise AssertionError(f"Unexpected account scope: {account_id}")

    # Exercise the real background credential recheck while a public puzzle is
    # active. A stale/missing credential must only report that speculative next
    # is unavailable; it must leave the current position actionable.
    active_before_recheck = {
        "puzzle_id": page.state.puzzle.puzzle_id,
        "fen": page.state.board.fen(),
        "status": page.state.status.value,
        "account_id": page._session_account_id,
    }
    generation_before_recheck = window._puzzle_training_generation
    invalid_messages: list[str] = []

    def report_prefetch_error(error) -> None:
        message = "Следующая задача недоступна · проверьте разрешение"
        page.network_status.setText(message)
        page.set_authorization_needed(bool(error))
        invalid_messages.append(message)

    window._validate_puzzle_credential_before_apply(
        page, generation_before_recheck, "deliberately-stale-fingerprint",
        lambda: (_ for _ in ()).throw(AssertionError("stale credential applied")),
        on_invalid=report_prefetch_error)
    wait_until(app, lambda: bool(invalid_messages), timeout_ms=10000)
    if (page.state.puzzle.puzzle_id != active_before_recheck["puzzle_id"]
            or page.state.board.fen() != active_before_recheck["fen"]
            or page.state.status.value != active_before_recheck["status"]
            or page._session_account_id != active_before_recheck["account_id"]
            or not page._allow_submit or not page.board._interaction_enabled):
        raise AssertionError("Background credential error changed the active puzzle state")
    capture(page, output, len(frames), "mainwindow-prefetch-error-current-task-actionable", frames)
    prefetch_error_trace = {
        **active_before_recheck,
        "generation": generation_before_recheck,
        "board_enabled": bool(page.board._interaction_enabled),
        "allow_submit": bool(page._allow_submit),
        "attempt_id": None,
        "status_text": page.network_status.text(),
        "credential_material_recorded": False,
    }
    page.network_status.clear()
    page.set_authorization_needed(False)

    # Hold the first durable enqueue while submitting the same immutable ID a
    # second time. The second signal must coalesce with the active worker.
    duplicate_attempt_id = "l2-prefetch-save-single-flight"
    duplicate_attempt = {
        "attempt_id": duplicate_attempt_id,
        "account_id": account_id,
        "session_id": page._session_id,
        "puzzle_id": "l2-prefetch-save-puzzle",
        "result": "success",
        "solved": True,
        "mistakes": 0,
        "hints": 0,
        "duration_ms": 25,
        "puzzle_json": json.dumps({"puzzle": {
            "id": "l2-prefetch-save-puzzle", "themes": ["fork"], "rating": 1600}}),
        "selected_theme": "fork",
        "rating_eligible": False,
    }
    enqueue_started = threading.Event()
    release_enqueue = threading.Event()
    enqueue_calls = 0
    original_enqueue = window.game_repository.enqueue_puzzle_attempt

    def gated_enqueue(attempt_payload):
        nonlocal enqueue_calls
        enqueue_calls += 1
        enqueue_started.set()
        if not release_enqueue.wait(10):
            raise TimeoutError("L2 duplicate-save barrier was not released")
        return original_enqueue(attempt_payload)

    window.game_repository.enqueue_puzzle_attempt = gated_enqueue
    attempts_before_duplicate_save = window.game_repository.local_puzzle_attempt_count(account_id)
    page.attach_pending_attempts([duplicate_attempt])
    window._save_training_attempt(page, duplicate_attempt)
    wait_until(app, enqueue_started.is_set, timeout_ms=10000)
    window._save_training_attempt(page, duplicate_attempt)
    release_enqueue.set()
    try:
        wait_until(app, lambda: window.game_repository.local_puzzle_attempt_count(account_id)
                   == attempts_before_duplicate_save + 1
                   and duplicate_attempt_id not in page._pending_attempt_saves,
                   timeout_ms=15000)
    finally:
        release_enqueue.set()
        window.game_repository.enqueue_puzzle_attempt = original_enqueue
    duplicate_save_event = window.game_repository.local_puzzle_rating_event(
        account_id, "l2-prefetch-save-puzzle")
    if enqueue_calls != 1 or duplicate_save_event is not None:
        raise AssertionError("Duplicate attempt save did not coalesce to one committed worker")
    duplicate_save_trace = {
        "attempt_id": duplicate_attempt_id,
        "account_id": account_id,
        "generation": window._puzzle_training_generation,
        "enqueue_workers": enqueue_calls,
        "attempt_rows_added": (window.game_repository.local_puzzle_attempt_count(account_id)
                               - attempts_before_duplicate_save),
        "rating_events_added": int(duplicate_save_event is not None),
        "status": page.persistence_status.text(),
        "credential_material_recorded": False,
    }
    capture(page, output, len(frames), "mainwindow-duplicate-save-coalesced", frames)
    second_account = window.game_repository.ensure_archive_account("cc-puz-ux03-second")

    solve_public_line(app, page, output, frames, "mainwindow-first")
    if page.state.status.value != "success":
        raise AssertionError("MainWindow public puzzle did not reach success")
    wait_until(app, lambda: page.persistence_status.text() == "✓ Сохранено в SQLite",
               timeout_ms=10000)
    before_auto_next = int(page.practice_rating.text().split(":", 1)[1].split()[0])
    stored_rating = int(window.game_repository.local_puzzle_rating(account_id)["rating"])
    if before_auto_next != stored_rating:
        raise AssertionError("SQLite save ACK rating does not match the visible practice rating")
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU", timeout_ms=10000)
    after_auto_next = int(page.practice_rating.text().split(":", 1)[1].split()[0])
    if after_auto_next != stored_rating:
        raise AssertionError(
            f"Prefetch changed the acknowledged rating: {stored_rating} → {after_auto_next}")
    capture(page, output, len(frames), "mainwindow-save-ack-auto-next-rating", frames)

    # The prefetched duplicate may have been prepared before the first task's
    # DB ACK. The ACK must invalidate it for this account before it is shown.
    if (page._prepared_online_puzzle is None
            or page._prepared_online_puzzle["puzzle"].puzzle_id != "0haqU"):
        raise AssertionError("Controlled duplicate puzzle was not prefetched before save ACK")
    duplicate_payload = page._prepared_online_puzzle["payload"]
    duplicate_rating_before = int(window.game_repository.local_puzzle_rating(account_id)["rating"])
    duplicate_attempt_count_before = window.game_repository.local_puzzle_attempt_count(account_id)
    QTimer.singleShot(0, lambda: _accept_question(app))
    page.fetch.click()
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU" and not page._is_replay,
               timeout_ms=10000)
    if page._rating_eligible:
        raise AssertionError("Duplicate online puzzle retained stale rating eligibility")
    if page._rating_preview is not None:
        raise AssertionError("Duplicate online puzzle advertised a second rating delta")
    capture(page, output, len(frames), "mainwindow-duplicate-unrated-after-ack", frames)
    solve_public_line(app, page, output, frames, "mainwindow-duplicate-unrated")
    wait_until(app, lambda: page.persistence_status.text() == "✓ Сохранено в SQLite",
               timeout_ms=10000)
    if (window.game_repository.local_puzzle_attempt_count(account_id)
            != duplicate_attempt_count_before + 1
            or window.game_repository.local_puzzle_rating(account_id)["rated_attempts"] != 1
            or window.game_repository.local_puzzle_rating_event(account_id, "0haqU") is None):
        raise AssertionError("Duplicate task created a second rating event or failed to persist")

    # Prepare a second isolated account with the same saved public position,
    # then open that account's actual history row while its account-list worker
    # is deterministically held. The stale callback must not replace replay.
    replay_account = window.game_repository.ensure_archive_account("cc-puz-ux03-replay")
    serialized_duplicate = json.dumps(duplicate_payload, ensure_ascii=False)
    window.game_repository.cache_training_puzzle(
        replay_account, "0haqU", serialized_duplicate)
    window.game_repository.save_local_puzzle_attempt(
        replay_account, "0haqU", result="success", solved=True, mistakes=0,
        hints=0, duration_ms=100, puzzle_json=serialized_duplicate,
        attempt_id="cross-account-history-seed", rating_eligible=False)
    replay_before_attempts = window.game_repository.local_puzzle_attempt_count(replay_account)
    replay_before_stats = window.game_repository.local_puzzle_statistics(replay_account)
    replay_before_rating = int(window.game_repository.local_puzzle_rating(replay_account)["rating"])
    accounts = [{"account_id": page.account_selector.itemData(index),
                 "username": page.account_selector.itemText(index), "attempts": 0}
                for index in range(page.account_selector.count())]
    accounts.append({"account_id": replay_account, "username": "cc-puz-ux03-replay",
                     "attempts": replay_before_attempts})
    page.set_accounts(accounts, page.account_id)
    original_local_attempts = window.game_repository.local_puzzle_attempts
    delayed_entered, release_delayed = threading.Event(), threading.Event()

    def delayed_local_attempts(selected, *, limit=8):
        if str(selected) == replay_account:
            delayed_entered.set()
            if not release_delayed.wait(20):
                raise TimeoutError("L2 account callback release timed out")
        return original_local_attempts(selected, limit=limit)

    window.game_repository.local_puzzle_attempts = delayed_local_attempts
    page.account_selector.setCurrentIndex(page.account_selector.findData(replay_account))
    wait_until(app, delayed_entered.is_set, timeout_ms=10000)
    page.set_recent_history([{"account_id": replay_account, "puzzle_id": "0haqU",
                              "source": "local", "result": "success",
                              "themes": ["fork"]}], local_count=replay_before_attempts,
                            local_stats=replay_before_stats,
                            practice_rating=window.game_repository.local_puzzle_rating(
                                replay_account))
    history_item = page.history.item(0)
    if history_item is None:
        raise AssertionError("Cross-account saved history row was not rendered")
    page._open_history_item(history_item)
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU" and page._is_replay,
               timeout_ms=15000)
    release_delayed.set()
    window.game_repository.local_puzzle_attempts = original_local_attempts
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU" and page._is_replay,
               timeout_ms=10000)
    first = chess.Move.from_uci(page.state.puzzle.expected_user_moves[0])
    click_move(app, page, first)
    if page.state.status.value == "mistake_retry":
        raise AssertionError("History replay rejected its known correct first move")
    if not page._is_replay or not page._allow_submit:
        raise AssertionError("History replay was not interactive/unrated")
    solve_public_line(app, page, output, frames, "mainwindow-replay")
    replay_after_rating = int(window.game_repository.local_puzzle_rating(replay_account)["rating"])
    replay_after_attempts = window.game_repository.local_puzzle_attempt_count(replay_account)
    replay_after_stats = window.game_repository.local_puzzle_statistics(replay_account)
    if (replay_after_rating != replay_before_rating
            or replay_after_attempts != replay_before_attempts
            or replay_after_stats != replay_before_stats
            or page.persistence_status.text() != "Повтор не записывается"):
        raise AssertionError("Replay changed attempt statistics/rating or claimed to save")
    capture(page, output, len(frames), "mainwindow-interactive-unrated-history-replay", frames)

    # Return to the first account, go to its next task and exercise wrong→skip.
    window.lichess.puzzle_username = "cc-puz-ux03-isolated"
    window.lichess.username = "cc-puz-ux03-isolated"
    client.username = "cc-puz-ux03-isolated"
    page.account_selector.setCurrentIndex(page.account_selector.findData(account_id))
    wait_until(app, lambda: page.account_id == account_id and page.state.puzzle is not None
               and not page._is_replay and page.state.status.value == "ready",
               timeout_ms=15000)
    legal_wrong = next(move for move in page.state.puzzle.board().legal_moves
                       if move.uci() not in page.state.puzzle.expected_user_moves)
    click_move(app, page, legal_wrong)
    wait_until(app, lambda: page.state.status.value == "mistake_retry", timeout_ms=5000)
    wait_until(app, lambda: not page._wrong_timer_pending, timeout_ms=5000)
    skipped_puzzle_id = page.state.puzzle.puzzle_id
    page._prepared_online_puzzle = None
    QTimer.singleShot(0, lambda: _accept_question(app))
    page._next()
    if page.state.status.value != "skipped":
        raise AssertionError("Skip confirmation did not produce the SKIPPED state")
    if page._session_results[-1]["mark"] != "✕":
        raise AssertionError("First wrong move disappeared from the result tape after skip")
    capture(page, output, len(frames), "mainwindow-skip-visible", frames)
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id != skipped_puzzle_id
               and not page._is_replay and page._active_source == "lichess"
               and page.state.status.value == "ready", timeout_ms=15000)
    wait_until(app, lambda: page._prepared_online_puzzle is not None, timeout_ms=30000)
    capture(page, output, len(frames), "mainwindow-after-skip-next-public", frames)

    # Force a controlled GET failure with no prepared puzzle. Confirm the skip
    # dialog, then assert the UI remains an explicit online error with no demo.
    page._prepared_online_puzzle = None
    client.fail_next = True
    QTimer.singleShot(0, lambda: _accept_question(app))
    page._next()
    wait_until(app, lambda: "Не удалось загрузить задачу Lichess" in
               page.network_status.text(), timeout_ms=15000)
    if page._active_source == "demo":
        raise AssertionError("Network failure silently entered a demo puzzle")
    network_failure_message = page.network_status.text()
    capture(page, output, len(frames), "mainwindow-public-network-failure-no-fallback", frames)

    # Single-flight: a delayed real public GET accepts one Next request only.
    client.block_next = True
    requests_before_single_flight = len(client.batch_arguments)
    page._next()
    wait_until(app, client.block_started.is_set, timeout_ms=5000)
    page._next()
    if len(client.batch_arguments) != requests_before_single_flight + 1:
        raise AssertionError("Double Next launched more than one in-flight request")
    if page.next.isEnabled() or page.fetch.isEnabled():
        raise AssertionError("Next controls remained enabled during the active GET")
    capture(page, output, len(frames), "mainwindow-single-flight-fetching", frames)
    client.release_blocked.set()
    wait_until(app, lambda: page.state.puzzle is not None
               and page.state.status.value == "ready" and not page._fetch_inflight,
               timeout_ms=20000)
    if page.state.history:
        raise AssertionError("Reducer history leaked from the prior task into the new puzzle")
    capture(page, output, len(frames), "mainwindow-single-flight-complete", frames)

    # An optional read-model failure after COMMIT must still deliver success.
    malformed_attempt = {
        "attempt_id": "l2-malformed-post-commit", "account_id": account_id,
        "puzzle_id": "l2-malformed-payload", "session_id": "l2-read-model",
        "result": "success", "solved": True, "mistakes": 0, "hints": 0,
        "duration_ms": 1, "puzzle_json": "{broken", "rating_eligible": True,
    }
    save_ack = []
    window.puzzle_attempt_save_finished.connect(
        lambda payload, error: save_ack.append((payload, error))
        if payload.get("attempt_id") == malformed_attempt["attempt_id"] else None)
    original_attempt_read = window.game_repository.local_puzzle_attempts
    window.game_repository.local_puzzle_attempts = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("controlled post-COMMIT read-model failure"))
    try:
        window._save_training_attempt(page, malformed_attempt)
        wait_until(app, lambda: bool(save_ack), timeout_ms=10000)
    finally:
        window.game_repository.local_puzzle_attempts = original_attempt_read
    if save_ack[0][1] is not None:
        raise AssertionError(f"Committed attempt received a false failure ACK: {save_ack[0][1]}")
    if window.game_repository.local_puzzle_attempt_count(account_id) < 1:
        raise AssertionError("The post-COMMIT scenario did not persist its local attempt")
    if window.game_repository.local_puzzle_rating_event(account_id,
                                                        "l2-malformed-payload") is not None:
        raise AssertionError("Malformed payload created an official/local rating event")

    # Seed an official rating in isolated SQLite, then prove an account with no
    # rating history does not inherit the prior profile's number or date.
    window.game_repository.save_puzzle_player_rating(account_id, [[2026, 10, 19, 1850]])
    page.account_id = account_id
    page.set_recent_history(
        [], local_count=window.game_repository.local_puzzle_attempt_count(account_id),
        official_rating=1850, rating_date="2026-11-19",
        official_rating_loaded=True)
    if "1850" not in page.metrics.text():
        raise AssertionError("Isolated account A's official rating was not displayed")
    page.account_id = second_account
    page.set_recent_history([], local_count=0, official_rating=None, rating_date=None,
                            official_rating_loaded=True)
    if "1850" in page.metrics.text() or "нет данных" not in page.metrics.text():
        raise AssertionError("Account B inherited account A's official puzzle rating")
    capture(page, output, len(frames), "mainwindow-official-rating-cleared-account-switch", frames)

    # Durable failed attempts are recovered only in their account scope. Reopen
    # the page model for A/B/A without touching any persistent owner database.
    if page.state.puzzle is None:
        raise AssertionError("Expected the current live puzzle before outbox recovery")
    previous_puzzle_id = page.state.puzzle.puzzle_id
    outbox_attempt = {
        "attempt_id": "l2-outbox-account-a", "account_id": account_id,
        "puzzle_id": "l2-outbox-puzzle", "session_id": "closed-l2-session",
        "result": "mistake", "solved": False, "mistakes": 1, "hints": 0,
        "duration_ms": 20, "puzzle_json": "{}", "rating_eligible": False,
    }
    window.game_repository.enqueue_puzzle_attempt(outbox_attempt, error="controlled failure")
    outbox_attempts_before_retry = window.game_repository.local_puzzle_attempt_count(account_id)
    window._puzzle_attempt_save_result_ready(outbox_attempt, "controlled late failure")
    if "l2-outbox-account-a" in page._pending_attempt_saves:
        raise AssertionError("A's late failure was attached while account B was selected")
    page.reset_for_account_switch()
    page.account_id = account_id
    window._load_puzzle_training_accounts(page, account_id,
                                          default_to_available=False, request_puzzle=False)
    wait_until(app, lambda: "l2-outbox-account-a" in page._pending_attempt_saves,
               timeout_ms=10000)
    capture(page, output, len(frames), "mainwindow-outbox-recovered-account-a", frames)
    page.reset_for_account_switch()
    page.account_id = second_account
    window._load_puzzle_training_accounts(page, second_account,
                                          default_to_available=False, request_puzzle=False)
    wait_until(app, lambda: page.account_selector.currentData() == second_account,
               timeout_ms=10000)
    if "l2-outbox-account-a" in page._pending_attempt_saves:
        raise AssertionError("A's failed attempt was exposed in account B's retry queue")
    page.reset_for_account_switch()
    page.account_id = account_id
    window._load_puzzle_training_accounts(page, account_id,
                                          default_to_available=False, request_puzzle=False)
    wait_until(app, lambda: "l2-outbox-account-a" in page._pending_attempt_saves,
               timeout_ms=10000)
    page._retry_attempt_save()
    wait_until(app, lambda: window.game_repository.local_puzzle_attempt_count(account_id)
               == outbox_attempts_before_retry + 1, timeout_ms=10000)
    wait_until(app, lambda: not window.game_repository.pending_puzzle_attempts(account_id),
               timeout_ms=10000)
    if window.game_repository.pending_puzzle_attempts(account_id):
        raise AssertionError("Acknowledged outbox entry was not removed")

    # Change to a second synthetic profile and confirm the MainWindow account
    # callback clears the previous position and starts a scoped request.
    rows = [{"account_id": page.account_selector.itemData(index),
             "username": page.account_selector.itemText(index), "attempts": 0}
            for index in range(page.account_selector.count())]
    rows.append({"account_id": second_account, "username": "cc-puz-ux03-second",
                 "attempts": 0})
    page.set_accounts(rows, page.account_id)
    window.lichess.puzzle_username = "cc-puz-ux03-second"
    window.lichess.username = "cc-puz-ux03-second"
    client.username = "cc-puz-ux03-second"
    page.account_selector.setCurrentIndex(page.account_selector.findData(second_account))
    wait_until(app, lambda: page.account_id == second_account
               and page.state.puzzle is not None
               and page.state.puzzle.puzzle_id != previous_puzzle_id,
               timeout_ms=20000)
    if not page.account_id.endswith("cc-puz-ux03-second"):
        raise AssertionError("Account switch loaded a task into the wrong scope")
    capture(page, output, len(frames), "mainwindow-account-switch-restarted", frames)

    # Advanced difficulty is selected through the production controls and the
    # resulting prepared puzzle must retain the exact selected key.
    page.more_difficulty.setChecked(True)
    hardest_index = page.advanced_difficulty.findData("hardest")
    if hardest_index < 0:
        raise AssertionError("Advanced hardest option is unavailable")
    page.advanced_difficulty.setCurrentIndex(hardest_index)
    import chess_coach.window as window_module
    window_module.OAuthCallbackServer = FakeOAuthCallback
    window_module.create_authorization_request = lambda *_args, **_kwargs: type(
        "FakeRequest", (), {"url": "https://lichess.org/oauth/fake"})()
    def fake_authorize_and_store(client_adapter, store, _callback, _request, **_kw):
        token = "fake-oauth-callback-token"
        store.set(token)
        return token, client_adapter.username

    window_module.authorize_and_store = fake_authorize_and_store
    QDesktopServices.openUrl = lambda _url: True
    page.set_authorization_needed(True)
    before_authorized_session = page._session_id
    previous_batch_count = len(client.batch_arguments)
    page.authorize.click()
    wait_until(app, lambda: len(client.batch_arguments) > previous_batch_count
               and client.batch_arguments[-1]["difficulty"] == "hardest",
               timeout_ms=20000)
    wait_until(app, lambda: page._session_id != before_authorized_session
               and page.state.puzzle is not None and page.state.status.value == "ready",
               timeout_ms=20000)
    if client.batch_arguments[-1]["token_sent"]:
        raise AssertionError("Fake OAuth credential was sent in a public GET header")
    capture(page, output, len(frames), "mainwindow-advanced-hardest-prefetch", frames)

    trace = {
        "scenario": "MainWindow public live integration",
        "oauth": "FAKE credential only; no authorized profile tested",
        "database": "MainWindow isolated offline runtime temporary SQLite",
        "owner_database_accessed": False,
        "requests": client.trace,
        "batch_arguments": client.batch_arguments,
        "request_ids": client.request_ids,
        "fake_profile_resolutions": client.account_resolution_calls,
        "frames": frames,
        "rating_before_auto_next": stored_rating,
        "rating_after_auto_next": after_auto_next,
        "rating_before_replay": replay_before_rating,
        "rating_after_replay": replay_after_rating,
        "attempts_before_replay": replay_before_attempts,
        "attempts_after_replay": replay_after_attempts,
        "statistics_before_replay": replay_before_stats,
        "statistics_after_replay": replay_after_stats,
        "replay_account": replay_account,
        "duplicate_id_unrated": True,
        "wrong_then_skip_kept_red": True,
        "skip_status": "skipped",
        "network_failure": network_failure_message,
        "second_account_scope": second_account,
        "account_switch_restarted": page.account_id == second_account,
        "advanced_difficulty": page.selected_difficulty(),
        "oauth_get_difficulty": client.batch_arguments[-1]["difficulty"],
        "single_flight_batch_requests": len(client.batch_arguments) - requests_before_single_flight,
        "post_commit_read_model_ack": save_ack[0][1] is None,
        "malformed_rating_events": 0,
        "official_rating_cleared_for_account_without_data": True,
        "outbox_a_to_b_to_a_recovery": True,
        "reducer_history_cleared_on_next_puzzle": True,
        "gaming_account_a": "cc-puz-ux03-game-only",
        "puzzle_read_profile_b": "cc-puz-ux03-isolated",
        "prefetch_error_kept_current_task_actionable": prefetch_error_trace,
        "duplicate_attempt_save_coalesced": duplicate_save_trace,
    }
    (output / "mainwindow-public-integration.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MAINWINDOW_INTEGRATION " + json.dumps({
        "public_gets": len(client.trace), "frames": len(frames),
        "rating": [stored_rating, after_auto_next, replay_after_rating],
        "attempts": [replay_before_attempts, replay_after_attempts],
        "skip": "passed", "network_failure": "no fallback",
        "account_switch": page.account_id == second_account,
        "advanced_difficulty": page.selected_difficulty(),
        "oauth_get_difficulty": client.batch_arguments[-1]["difficulty"],
    }, ensure_ascii=False))
    window.close()
    app.processEvents()
    shutdown_logging()


def _accept_question(app: QApplication, attempts: int = 0) -> None:
    dialog = next((widget for widget in app.topLevelWidgets()
                   if isinstance(widget, QMessageBox) and widget.isVisible()), None)
    if dialog is not None:
        yes = dialog.button(QMessageBox.StandardButton.Yes)
        if yes is not None:
            QTest.mouseClick(yes, Qt.MouseButton.LeftButton)
            return
    if attempts < 100:
        QTimer.singleShot(10, lambda: _accept_question(app, attempts + 1))


def main() -> None:
    output = Path(os.environ.get(
        "CC_PUZ_UX_03_MAINWINDOW_OUTPUT", "live-evidence/mainwindow"))
    output.mkdir(parents=True, exist_ok=True)
    run(output)


if __name__ == "__main__":
    main()
