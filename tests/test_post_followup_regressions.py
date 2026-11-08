from __future__ import annotations

import threading
from types import SimpleNamespace

import chess
import random
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest

from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.repository import GameRepository
from chess_coach.ui.game_review import GameReviewWorkspace
from chess_coach.window import MainWindow


def _make_archive(repository: GameRepository, username: str, source_id: str) -> str:
    pgn = ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
           '1. e4 e5 2. Nf3 Nc6 1-0')
    repository.import_archive_record(username, {"id": source_id, "pgn": pgn},
                                     checkpoint_ms=None, backfill=False)
    with repository._connection() as db:
        return str(db.execute("SELECT game_id FROM games WHERE source_game_id=?",
                              (source_id,)).fetchone()[0])


class _ExportClient:
    rate_limit_remaining = 0

    def __init__(self):
        self.calls: list[str] = []

    def export_game_json(self, token: str, source_game_id: str):
        self.calls.append(source_game_id)
        return {"id": source_game_id, "status": "mate",
                "pgn": ('[White "Owner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
                        '1. e4 e5 2. Nf3 Nc6 1-0'),
                "players": {"white": {"user": {"name": "Owner"}},
                            "black": {"user": {"name": "Opponent"}}},
                "analysis": []}


def test_selected_analytics_retry_fetches_only_the_requested_game_even_when_batch_is_ten(
        tmp_path):
    repository = GameRepository(tmp_path / "strict-selected-backfill.sqlite3")
    client = _ExportClient()
    try:
        account_id = repository.ensure_archive_account("Owner")
        game_ids = [_make_archive(repository, "Owner", f"game-{index}")
                    for index in range(20)]
        repository.enqueue_lichess_analysis_backfill(account_id)
        selected = repository.get_game(game_ids[13])
        line = repository.current_line(game_ids[13])
        assert line is not None
        repository.record_lichess_analysis_backfill(
            account_id, game_ids[13], accuracy_status="no_server_data",
            evals_status="partial", line_revision=line["line_revision"],
            line_hash=line["line_hash"], retry_after_seconds=0)
        assert repository.retry_lichess_analysis_game(
            account_id, game_ids[13], line_revision=int(line["line_revision"]))

        report = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), limit=10,
            selected_game_id=game_ids[13],
            expected_line_revision=int(line["line_revision"]),
            expected_source_game_id=str(selected["source_game_id"]))

        assert report["checked"] == 1
        assert client.calls == [str(selected["source_game_id"])]
    finally:
        repository.shutdown()

def test_selected_analytics_retry_never_falls_back_to_unrelated_due_games(tmp_path):
    repository = GameRepository(tmp_path / "not-due-selected-backfill.sqlite3")
    client = _ExportClient()
    try:
        account_id = repository.ensure_archive_account("Owner")
        game_ids = [_make_archive(repository, "Owner", f"due-{index}") for index in range(3)]
        repository.enqueue_lichess_analysis_backfill(account_id)
        selected = game_ids[0]
        row = repository.lichess_analysis_backfill_state(account_id, selected)
        repository.record_lichess_analysis_backfill(
            account_id, selected, accuracy_status="has_data", evals_status="has_data",
            line_revision=row["line_revision"], line_hash=row["line_hash"])

        report = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), limit=10,
            selected_game_id=selected,
            expected_line_revision=int(row["line_revision"]),
            expected_source_game_id="due-0")

        assert report["selected"] == 0
        assert client.calls == []
    finally:
        repository.shutdown()


def test_selected_analytics_retry_preserves_future_ttl_and_does_not_spend_budget(tmp_path):
    repository = GameRepository(tmp_path / "selected-backfill-ttl.sqlite3")
    client = _ExportClient()
    try:
        account_id = repository.ensure_archive_account("Owner")
        selected = _make_archive(repository, "Owner", "not-due")
        unrelated = _make_archive(repository, "Owner", "other-due")
        repository.enqueue_lichess_analysis_backfill(account_id)
        row = repository.lichess_analysis_backfill_state(account_id, selected)
        repository.record_lichess_analysis_backfill(
            account_id, selected, accuracy_status="no_server_data", evals_status="partial",
            line_revision=row["line_revision"], line_hash=row["line_hash"],
            retry_after_seconds=86_400)
        before = repository.lichess_analysis_backfill_state(account_id, selected)

        assert not repository.retry_lichess_analysis_game(
            account_id, selected, line_revision=int(row["line_revision"]),
            source_game_id="not-due")
        after = repository.lichess_analysis_backfill_state(account_id, selected)
        assert after["accuracy_next_retry_at"] == before["accuracy_next_retry_at"]
        assert after["evals_next_retry_at"] == before["evals_next_retry_at"]
        assert after["accuracy_status"] == "no_server_data"
        assert after["evals_status"] == "partial"

        report = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), limit=10,
            selected_game_id=selected,
            expected_line_revision=int(row["line_revision"]),
            expected_source_game_id="not-due")
        assert report["selected"] == 0
        assert client.calls == []
        assert unrelated != selected
    finally:
        repository.shutdown()


def test_selected_retry_respects_global_retry_after_daily_quota_and_429(tmp_path):
    from chess_coach.lichess.api import RateLimitError

    repository = GameRepository(tmp_path / "selected-retry-limits.sqlite3")
    game_id = _make_archive(repository, "Owner", "rate-limited-selected")
    account_id = repository.ensure_archive_account("Owner")
    repository.enqueue_lichess_analysis_backfill(account_id)
    line = repository.current_line(game_id)
    source_id = "rate-limited-selected"

    class LimitedClient(_ExportClient):
        def export_game_json(self, token, requested_id):
            self.calls.append(requested_id)
            raise RateLimitError(3600)

    client = LimitedClient()
    try:
        repository.set_lichess_analysis_retry_after(account_id, 3600)
        during_retry_after = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), selected_game_id=game_id,
            expected_line_revision=int(line["line_revision"]),
            expected_source_game_id=source_id)
        assert during_retry_after["selected"] == 0 and client.calls == []

        with repository._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill_budget SET retry_after_at=NULL,requests=0 "
                       "WHERE account_id=?", (account_id,))
        for _ in range(25):
            assert repository.reserve_lichess_analysis_request(account_id, daily_limit=25)
        quota = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), daily_limit=25,
            selected_game_id=game_id,
            expected_line_revision=int(line["line_revision"]),
            expected_source_game_id=source_id)
        assert quota["selected"] == 0 and client.calls == []

        # A fresh UTC day gives one request; a server 429 creates a durable
        # account cooldown which a repeated selected click cannot bypass.
        with repository._connection() as db:
            db.execute("UPDATE lichess_analysis_backfill_budget SET utc_day='2000-01-01',requests=0,"
                       "retry_after_at=NULL "
                       "WHERE account_id=?", (account_id,))
        first = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), selected_game_id=game_id,
            expected_line_revision=int(line["line_revision"]),
            expected_source_game_id=source_id)
        assert first["fatal_status"] == 429
        assert client.calls == [source_id]
        second = ArchiveSync(client, repository).refresh_ready_analytics(
            "Owner", "synthetic-token", threading.Event(), selected_game_id=game_id,
            expected_line_revision=int(line["line_revision"]),
            expected_source_game_id=source_id)
        assert second["selected"] == 0 and client.calls == [source_id]
    finally:
        repository.shutdown()


def test_library_retry_rejects_archive_account_not_matching_authenticated_identity(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "retry-account-isolation.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    repository = window.game_repository
    account_a_game_id = _make_archive(repository, "AccountA", "same-game")
    game = window.library_queries.search({"account_id": "lichess:accounta"})["items"][0]
    assert game["game_id"] == account_a_game_id
    assert game["account_id"] == "lichess:accounta"
    account_b = repository.ensure_archive_account("AccountB")
    with repository._connection() as db:
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                   "VALUES(?,?,?,?,?,?)", (account_b, account_a_game_id, "same-game",
                   "synthetic", "white", "Synthetic"))
        db.commit()
    state_before = repository.lichess_analysis_backfill_state(
        "lichess:accounta", account_a_game_id)
    calls: list[tuple] = []
    messages: list[str] = []
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_sessions_for_safety", lambda: [])
    monkeypatch.setattr(window, "_load_library_game_detail", lambda *args: None)
    monkeypatch.setattr(window, "_notify_inline", messages.append)
    monkeypatch.setattr(repository, "enqueue_lichess_analysis_backfill",
                        lambda *args, **kwargs: calls.append(("enqueue", args, kwargs)))
    monkeypatch.setattr(repository, "retry_lichess_analysis_game",
                        lambda *args, **kwargs: calls.append(("retry", args, kwargs)) or True)
    monkeypatch.setattr(window, "_start_lichess_analysis_backfill",
                        lambda *args, **kwargs: calls.append(("network", args, kwargs)))
    window.lichess = SimpleNamespace(username="AccountB", auth_generation=9,
                                     is_auth_generation_current=lambda generation: generation == 9)
    window._lichess_connected = True
    try:
        window.offline_mode = False
        window.show_saved_games()
        page = window.library_page
        page.set_results([(game, "Synthetic game", "Lichess")], page=0, pages=1, total=1)
        page.show_game_detail(game)
        QTest.mouseClick(page.retry_analysis_button, Qt.MouseButton.LeftButton)
        assert calls == []
        assert repository.lichess_analysis_backfill_state(
            "lichess:accounta", account_a_game_id) == state_before
        assert repository.lichess_analysis_backfill_state(
            account_b, account_a_game_id) is None
        assert any("AccountA" in message or "accounta" in message.casefold()
                   for message in messages)
    finally:
        repository.shutdown()
        window.close()


def test_library_retry_for_matching_authenticated_account_starts_only_selected_game(
        qtbot, tmp_path, monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "retry-matching-account.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    repository = window.game_repository
    game_id = _make_archive(repository, "AccountA", "selected-only")
    game = window.library_queries.search({"account_id": "lichess:accounta"})["items"][0]
    line = repository.current_line(game_id)
    assert game["game_id"] == game_id
    assert game["account_id"] == "lichess:accounta"
    calls = []
    monkeypatch.setattr(window, "_online_actions_allowed", lambda: True)
    monkeypatch.setattr(window, "_sessions_for_safety", lambda: [])
    monkeypatch.setattr(window, "_load_library_game_detail", lambda *args: None)
    monkeypatch.setattr(window, "_current_lichess_generation", lambda generation: generation == 9)
    monkeypatch.setattr(window, "_notify_inline", lambda message: calls.append(("status", message)))
    monkeypatch.setattr(window, "_start_lichess_analysis_backfill",
                        lambda *args, **kwargs: calls.append(("worker", args, kwargs)))
    window.offline_mode = False
    window._lichess_stream_ready_generation = 9
    window.lichess = SimpleNamespace(
        username="AccountA", auth_generation=9,
        is_auth_generation_current=lambda generation: generation == 9)
    window._lichess_connected = True
    try:
        window.show_saved_games()
        page = window.library_page
        page.set_results([(game, "Synthetic game", "Lichess")], page=0, pages=1, total=1)
        page.show_game_detail(game)
        QTest.mouseClick(page.retry_analysis_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: any(call[0] == "worker" for call in calls), timeout=5000)
        worker = [call for call in calls if call[0] == "worker"]
        assert len(worker) == 1
        assert worker[0][2]["selected_game_id"] == game_id
        assert worker[0][2]["expected_source_game_id"] == "selected-only"
    finally:
        repository.shutdown()
        window.close()


def test_finished_timed_game_with_missing_clock_snapshot_does_not_claim_untimed(
        qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "finished-clock-missing.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    from chess_coach.lichess.session import LichessGameSession
    session = LichessGameSession("synthetic-missing-clock", window.game_repository)
    session.speed = "blitz"
    session.clock_control = "180+2"
    session.clock_state_valid = None
    session.white_ms = session.black_ms = None
    session.status = "mate"
    window.online_session = session
    window._screen_mode = "online"
    try:
        window._update_clock()
        assert "без часов" not in window.clock_label.text().casefold()
        assert "180+2" in window.clock_label.text()
        assert "заверш" in window.clock_label.text().casefold()
    finally:
        window.game_repository.shutdown()
        window.close()


def test_review_initial_position_scrolls_first_san_row_into_view_after_late_ply(
        qtbot, tmp_path):
    repository = GameRepository(tmp_path / "review-start-scroll.sqlite3")
    game_id = "scroll-review"
    board = chess.Board()
    moves: list[str] = []
    rng = random.Random(64)
    for _ in range(60):
        move = rng.choice(list(board.legal_moves))
        moves.append(move.uci())
        board.push(move)
        if board.is_game_over():
            break
    repository.save_snapshot(game_id, "startpos", " ".join(moves), "white", "Synthetic",
                             False, "5+0", "mate", "white")
    workspace = GameReviewWorkspace(repository, training_allowed=True)
    qtbot.addWidget(workspace)
    workspace.resize(1120, 640)
    workspace.show()
    try:
        workspace.load_game(repository.get_game(game_id))
        qtbot.wait(30)
        workspace.select_ply(len(workspace.review["moves"]))
        qtbot.wait(30)
        assert workspace.moves_list.table.verticalScrollBar().value() > 0
        workspace.select_ply(0)
        qtbot.wait(30)
        first = workspace.moves_list.item_by_ply(1)
        assert first is not None
        assert workspace.moves_list.table.visualItemRect(first).isValid()
        assert workspace.moves_list.table.visualItemRect(first).intersects(
            workspace.moves_list.table.viewport().rect())
        assert workspace.review_board._board.fen() == workspace.review["positions"][0].fen()
    finally:
        repository.shutdown()
