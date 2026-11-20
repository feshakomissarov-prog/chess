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
import time
from urllib.request import Request, urlopen

import chess
from PySide6.QtCore import QSettings, Qt, QTimer
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

    def __init__(self, **_kwargs) -> None:
        pass

    def get(self) -> str:
        return "not-a-real-oauth-token-public-read-only-test"


class PublicPuzzleAdapter:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.request_ids: list[str] = []
        self.trace: list[dict] = []
        self.username = "cc-puz-ux03-isolated"
        self.account_resolution_calls = 0
        self.fail_next = False

    def get_account(self, _token) -> dict:
        """Resolve the fake profile locally; never send the credential."""
        self.account_resolution_calls += 1
        return {"username": self.username}

    def get_puzzle_batch(self, _token, *, angle: str, difficulty: str, count: int):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("controlled L2 network failure")
        puzzle_id = PUBLIC_PUZZLE_IDS[len(self.request_ids) % len(PUBLIC_PUZZLE_IDS)]
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
    username = puzzle_username

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
               page.state.puzzle.puzzle_id == "00008", timeout_ms=10000)
    after_auto_next = int(page.practice_rating.text().split(":", 1)[1].split()[0])
    if after_auto_next != stored_rating:
        raise AssertionError(
            f"Prefetch changed the acknowledged rating: {stored_rating} → {after_auto_next}")
    capture(page, output, len(frames), "mainwindow-save-ack-auto-next-rating", frames)

    # Open the just-saved history row through its actual page callback and
    # replay one move. The recorded repetition is interactive and unrated.
    history_item = next((page.history.item(i) for i in range(page.history.count())
                         if (page.history.item(i).data(Qt.ItemDataRole.UserRole) or {}).get(
                             "puzzle_id") == "0haqU"), None)
    if history_item is None:
        raise AssertionError("Saved online puzzle was not visible in MainWindow history")
    page._open_history_item(history_item)
    wait_until(app, lambda: page.state.puzzle is not None and
               page.state.puzzle.puzzle_id == "0haqU" and page._is_replay,
               timeout_ms=15000)
    replay_before_attempts = window.game_repository.local_puzzle_attempt_count(account_id)
    replay_before_rating = int(window.game_repository.local_puzzle_rating(account_id)["rating"])
    first = chess.Move.from_uci(page.state.puzzle.expected_user_moves[0])
    click_move(app, page, first)
    if page.state.status.value == "mistake_retry":
        raise AssertionError("History replay rejected its known correct first move")
    if not page._is_replay or not page._allow_submit:
        raise AssertionError("History replay was not interactive/unrated")
    solve_public_line(app, page, output, frames, "mainwindow-replay")
    wait_until(app, lambda: page.persistence_status.text() == "✓ Сохранено в SQLite",
               timeout_ms=10000)
    replay_after_rating = int(window.game_repository.local_puzzle_rating(account_id)["rating"])
    replay_after_attempts = window.game_repository.local_puzzle_attempt_count(account_id)
    if replay_after_rating != replay_before_rating or replay_after_attempts <= replay_before_attempts:
        raise AssertionError("Replay must save a local attempt without changing CC rating")
    capture(page, output, len(frames), "mainwindow-interactive-unrated-history-replay", frames)

    # Go to the second task and exercise a real confirmation/skip. The answer
    # is accepted automatically in the nested modal event loop by QtTest.
    page.fetch.click()
    wait_until(app, lambda: page.state.puzzle is not None and
               not page._is_replay and page._active_source == "lichess"
               and page.state.status.value == "ready",
               timeout_ms=15000)
    skipped_puzzle_id = page.state.puzzle.puzzle_id
    page._prepared_online_puzzle = None
    QTimer.singleShot(0, lambda: _accept_question(app))
    page._next()
    if page.state.status.value != "skipped":
        raise AssertionError("Skip confirmation did not produce the SKIPPED state")
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
    capture(page, output, len(frames), "mainwindow-public-network-failure-no-fallback", frames)

    # Change to a second synthetic profile and confirm the MainWindow account
    # callback clears the previous position and starts a scoped request.
    second_account = window.game_repository.ensure_archive_account("cc-puz-ux03-second")
    rows = [{"account_id": page.account_selector.itemData(index),
             "username": page.account_selector.itemText(index), "attempts": 0}
            for index in range(page.account_selector.count())]
    rows.append({"account_id": second_account, "username": "cc-puz-ux03-second",
                 "attempts": 0})
    previous_puzzle_id = page.state.puzzle.puzzle_id
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
    wait_until(app, lambda: page._prepared_online_puzzle is not None, timeout_ms=30000)
    if page._prepared_online_puzzle.get("difficulty") != "hardest":
        raise AssertionError("MainWindow discarded the selected advanced prefetch key")
    capture(page, output, len(frames), "mainwindow-advanced-hardest-prefetch", frames)

    trace = {
        "scenario": "MainWindow public live integration",
        "oauth": "FAKE credential only; no authorized profile tested",
        "database": "MainWindow isolated offline runtime temporary SQLite",
        "owner_database_accessed": False,
        "requests": client.trace,
        "request_ids": client.request_ids,
        "fake_profile_resolutions": client.account_resolution_calls,
        "frames": frames,
        "rating_before_auto_next": stored_rating,
        "rating_after_auto_next": after_auto_next,
        "rating_before_replay": replay_before_rating,
        "rating_after_replay": replay_after_rating,
        "attempts_before_replay": replay_before_attempts,
        "attempts_after_replay": replay_after_attempts,
        "skip_status": "skipped",
        "network_failure": page.network_status.text(),
        "second_account_scope": second_account,
        "account_switch_restarted": page.account_id == second_account,
        "advanced_difficulty": page._prepared_online_puzzle.get("difficulty"),
    }
    (output / "mainwindow-public-integration.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MAINWINDOW_INTEGRATION " + json.dumps({
        "public_gets": len(client.trace), "frames": len(frames),
        "rating": [stored_rating, after_auto_next, replay_after_rating],
        "attempts": [replay_before_attempts, replay_after_attempts],
        "skip": "passed", "network_failure": "no fallback",
        "account_switch": page.account_id == second_account,
        "advanced_difficulty": page._prepared_online_puzzle.get("difficulty"),
    }, ensure_ascii=False))
    window.close()
    app.processEvents()
    shutdown_logging()


def _accept_question(app: QApplication) -> None:
    dialog = app.activeModalWidget()
    if isinstance(dialog, QMessageBox):
        dialog.done(int(QMessageBox.StandardButton.Yes))


def main() -> None:
    output = Path(os.environ.get(
        "CC_PUZ_UX_03_MAINWINDOW_OUTPUT", "live-evidence/mainwindow"))
    output.mkdir(parents=True, exist_ok=True)
    run(output)


if __name__ == "__main__":
    main()
