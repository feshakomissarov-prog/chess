import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from chess_coach.lichess.api import HttpResponse, LichessClient, RateLimitError
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.analysis_json import normalize_game_analytics
from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION
from chess_coach.lichess.repository import StaleGameLineError


def _pgn(game_id: str, *, evals: bool = False) -> str:
    comments = " { [%eval 0.20] }" if evals else ""
    end_comment = " { [%eval 0.10] }" if evals else ""
    return (f'[Event "Synthetic {game_id}"]\n[White "Me"]\n[Black "Opponent"]\n'
            '[Result "1-0"]\n\n' + f"1. e4{comments} e5{end_comment} 1-0\n")


def _import_games(repository: GameRepository, count: int = 30) -> list[str]:
    ids = []
    for index in range(count):
        game_id = f"synthetic-{index:02}"
        ids.append(game_id)
        repository.import_archive_record("Me", {"id": game_id, "pgn": _pgn(game_id)},
                                         checkpoint_ms=None, backfill=False)
    return ids


def _export(game_id: str, *, accuracy=True, evals=True) -> dict:
    white_analysis = {"inaccuracy": 1, "mistake": 2, "blunder": 0, "acpl": 24}
    black_analysis = {"inaccuracy": 2, "mistake": 1, "blunder": 1, "acpl": 31}
    if accuracy:
        white_analysis["accuracy"] = 91
        white_analysis["phases"] = {"opening": 95, "middlegame": 87, "endgame": 90}
        black_analysis["accuracy"] = 87
    payload = {
        "id": game_id, "status": "mate", "pgn": _pgn(game_id, evals=False),
        "players": {
            "white": {"user": {"id": "owner-id", "name": "Me"}, "rating": 1500,
                      "analysis": white_analysis},
            "black": {"user": {"id": "opponent-id", "name": "Opponent"}, "rating": 1520,
                      "analysis": black_analysis},
        },
        "opening": {"eco": "C20", "name": "King's Pawn Game"},
        "division": {"middle": 1, "end": 2},
    }
    if evals:
        payload["analysis"] = [{"eval": 20}, {"eval": 0}]
    return payload


class FakeJSONClient:
    rate_limit_remaining = 0

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def export_game_json(self, token, game_id):
        self.calls.append((token, game_id))
        value = self.responses[game_id]
        if isinstance(value, Exception):
            raise value
        return value


def test_documented_json_export_uses_game_export_parameters_and_never_puts_token_in_url():
    payload = _export("testgame")

    class Transport:
        def __init__(self): self.calls = []
        def request(self, method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body))
            return HttpResponse(200, {"Content-Type": "application/json"},
                                json.dumps(payload).encode())
        def stream(self, *args, **kwargs): return iter(())

    transport = Transport()
    client = LichessClient(transport, base_url="https://lichess.test")
    assert client.export_game_json("synthetic-secret", "testgame")["id"] == "testgame"
    method, url, headers, _body = transport.calls[0]
    assert method == "GET"
    assert urlparse(url).path == "/game/export/testgame"
    query = parse_qs(urlparse(url).query)
    assert query == {"accuracy": ["true"], "evals": ["true"],
                     "pgnInJson": ["true"], "opening": ["true"], "division": ["true"]}
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer synthetic-secret"
    assert "synthetic-secret" not in url


def test_old_games_auto_queue_in_small_pages_merge_server_data_and_resume_after_restart(tmp_path):
    path = tmp_path / "old-archive.sqlite3"
    repository = GameRepository(path)
    ids = _import_games(repository, 30)
    before = repository.archive_sync_state("lichess:me")
    fake = FakeJSONClient({game_id: _export(game_id) for game_id in ids})
    sync = ArchiveSync(fake, repository)

    first = sync.refresh_ready_analytics("Me", "fake-token", threading.Event(), limit=10)
    assert first["selected"] == first["checked"] == 10
    assert first["accuracy"] == first["evals"] == 10
    assert len(fake.calls) == 10
    assert all(token == "fake-token" for token, _game in fake.calls)
    first_game_id = fake.calls[0][1]
    assert repository.get_game(first_game_id)["lichess_accuracy_white"] == 91
    assert repository.lichess_eval_score_count(first_game_id, 1) == 2
    after_first = repository.archive_sync_state("lichess:me")
    # The UI freshness timestamp may change, but synchronization cursors and full-pass
    # completion markers must remain untouched by metadata enrichment.
    for cursor in ("last_game_at", "last_game_id", "backfill_started_at",
                   "full_sync_completed_at", "last_successful_sync_at"):
        if cursor in before:
            assert after_first[cursor] == before[cursor]
    repository.shutdown()
    repository = GameRepository(path)  # A real SQLite reopen, not an in-memory fake.
    assert repository.get_game(first_game_id)["lichess_accuracy_black"] == 87
    fake2 = FakeJSONClient({game_id: _export(game_id) for game_id in ids})
    second = ArchiveSync(fake2, repository).refresh_ready_analytics(
        "Me", "fake-token", threading.Event(), limit=10)
    assert second["selected"] == second["checked"] == 10
    assert len({game_id for _token, game_id in fake.calls + fake2.calls}) == 20
    assert set(game_id for _token, game_id in fake.calls).isdisjoint(
        game_id for _token, game_id in fake2.calls)
    assert repository.lichess_analysis_backfill_summary("lichess:me")["checked"] == 20
    with repository._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 30
        assert db.execute("SELECT COUNT(*) FROM analysis_runs WHERE source='stockfish'").fetchone()[0] == 0
    repository.shutdown()


def test_concurrent_backfill_read_does_not_duplicate_a_live_export(tmp_path):
    path = tmp_path / "single-live-export.sqlite3"
    repository = GameRepository(path)
    game_id = _import_games(repository, 1)[0]
    entered = threading.Event()
    release = threading.Event()

    class BlockingFake(FakeJSONClient):
        def export_game_json(self, token, requested_game_id):
            self.calls.append((token, requested_game_id))
            entered.set()
            assert release.wait(3)
            return self.responses[requested_game_id]

    fake = BlockingFake({game_id: _export(game_id)})
    result = []
    errors = []

    def first_worker():
        try:
            result.append(ArchiveSync(fake, repository).refresh_ready_analytics(
                "Me", "synthetic-token", threading.Event(), limit=1))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=first_worker, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        account_id = repository.ensure_archive_account("Me")
        state = repository.lichess_analysis_backfill_state(account_id, game_id)
        assert state["lease_owner"]
        assert repository.heartbeat_lichess_analysis_backfill(
            account_id, game_id, lease_owner=state["lease_owner"])
        with repository._connection() as db:
            stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            db.execute("UPDATE lichess_analysis_backfill SET updated_at=? "
                       "WHERE account_id=? AND game_id=?", (stale, account_id, game_id))
        repository.enqueue_lichess_analysis_backfill(account_id)
        state = repository.lichess_analysis_backfill_state(account_id, game_id)
        assert state["accuracy_status"] == "running"
        assert state["lease_owner"]
        duplicate = ArchiveSync(fake, repository).refresh_ready_analytics(
            "Me", "synthetic-token", threading.Event(), limit=1)
        assert duplicate["selected"] == 0
        assert len(fake.calls) == 1
    finally:
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert not errors
        assert len(fake.calls) == 1
        repository.shutdown()


def test_expired_worker_response_cannot_merge_after_new_owner_claims_line(tmp_path):
    repository = GameRepository(tmp_path / "stale-lease-response.sqlite3")
    game_id = _import_games(repository, 1)[0]
    account_id = repository.ensure_archive_account("Me")
    repository.enqueue_lichess_analysis_backfill(account_id)
    item = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    try:
        assert repository.claim_lichess_analysis_backfill(
            account_id, item["game_id"], lease_owner="old-worker",
            line_revision=item["line_revision"], line_hash=item["line_hash"])
        with repository._connection() as db:
            expired = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
            db.execute("UPDATE lichess_analysis_backfill SET lease_heartbeat_at=?,updated_at=? "
                       "WHERE account_id=? AND game_id=?",
                       (expired, expired, account_id, game_id))
        repository.enqueue_lichess_analysis_backfill(account_id)
        assert repository.claim_lichess_analysis_backfill(
            account_id, item["game_id"], lease_owner="new-worker",
            line_revision=item["line_revision"], line_hash=item["line_hash"])
        with pytest.raises(StaleGameLineError, match="stale_lease"):
            repository.merge_lichess_ready_analysis(
                "Me", game_id, source_game_id=game_id, account_id=account_id,
                expected_line_revision=item["line_revision"],
                expected_line_hash=item["line_hash"], expected_moves=item["moves"],
                accuracy={"white": 99.0, "black": 99.0}, player_analysis={},
                assessments=[], move_annotations=[], validation_issues=[],
                lease_owner="old-worker")
        assert repository.get_game(game_id)["lichess_accuracy_white"] is None
    finally:
        repository.shutdown()

def test_no_server_data_is_not_repolled_before_seven_day_ttl(tmp_path):
    repository = GameRepository(tmp_path / "no-data.sqlite3")
    game_id = _import_games(repository, 1)[0]
    fake = FakeJSONClient({game_id: _export(game_id, accuracy=False, evals=False)})
    sync = ArchiveSync(fake, repository)
    first = sync.refresh_ready_analytics("Me", "fake", threading.Event(), limit=10)
    assert first["checked"] == 1 and first["no_data"] == 1
    second = sync.refresh_ready_analytics("Me", "fake", threading.Event(), limit=10)
    assert second["selected"] == 0 and second["checked"] == 0
    assert len(fake.calls) == 1
    global_check = sync.refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10, force_retry=True)
    assert global_check["selected"] == 0 and global_check["checked"] == 0
    assert len(fake.calls) == 1
    state = repository.lichess_analysis_backfill_state("lichess:me", game_id)
    assert not repository.retry_lichess_analysis_game(
        "lichess:me", game_id, line_revision=state["line_revision"],
        source_game_id=game_id)
    not_due = sync.refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10, selected_game_id=game_id,
        expected_line_revision=int(state["line_revision"]),
        expected_source_game_id=game_id)
    assert not_due["selected"] == 0 and len(fake.calls) == 1
    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET accuracy_next_retry_at=?,"
                   "evals_next_retry_at=? WHERE account_id=? AND game_id=?",
                   ("2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00",
                    "lichess:me", game_id))
    assert repository.retry_lichess_analysis_game(
        "lichess:me", game_id, line_revision=state["line_revision"],
        source_game_id=game_id)
    manual = sync.refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10, selected_game_id=game_id,
        expected_line_revision=int(state["line_revision"]),
        expected_source_game_id=game_id)
    assert manual["checked"] == 1 and len(fake.calls) == 2
    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET accuracy_next_retry_at=?,"
                   "evals_next_retry_at=? WHERE account_id=? AND game_id=?",
                   ("2025-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00",
                    "lichess:me", game_id))
    fake.responses[game_id] = _export(game_id)
    delayed = sync.refresh_ready_analytics("Me", "fake", threading.Event(), limit=10)
    assert delayed["checked"] == delayed["accuracy"] == delayed["evals"] == 1
    assert repository.get_game(game_id)["lichess_accuracy_white"] == 91
    assert len(fake.calls) == 3
    assert sync.refresh_ready_analytics("Me", "fake", threading.Event(), limit=10)["selected"] == 0
    repository.shutdown()


def test_backfill_due_facets_are_independent_and_cooldown_is_preserved(tmp_path):
    from datetime import datetime, timedelta, timezone

    repository = GameRepository(tmp_path / "independent-due.sqlite3")
    game_id = _import_games(repository, 1)[0]
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET accuracy_status='queued',"
                   "evals_status='no_server_data',evals_next_retry_at=? "
                   "WHERE account_id='lichess:me' AND game_id=?", (future, game_id))
    accuracy_only = repository.list_lichess_analysis_backfill("lichess:me", limit=1)[0]
    assert accuracy_only["check_accuracy"] == 1
    assert accuracy_only["check_evals"] == 0
    repository.mark_lichess_analysis_backfill_running(
        "lichess:me", game_id, check_accuracy=True, check_evals=False)
    repository.record_lichess_analysis_backfill(
        "lichess:me", game_id, accuracy_status="has_data", evals_status="queued",
        check_accuracy=True, check_evals=False)
    state = repository.lichess_analysis_backfill_state("lichess:me", game_id)
    assert state["accuracy_status"] == "has_data"
    assert state["evals_status"] == "no_server_data"
    assert state["evals_next_retry_at"] == future

    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET accuracy_status='no_server_data',"
                   "accuracy_next_retry_at=?,evals_status='queued',evals_next_retry_at=NULL "
                   "WHERE account_id='lichess:me' AND game_id=?", (future, game_id))
    evals_only = repository.list_lichess_analysis_backfill("lichess:me", limit=1)[0]
    assert evals_only["check_accuracy"] == 0
    assert evals_only["check_evals"] == 1
    repository.shutdown()


def test_backfill_summary_exposes_attempt_success_retry_and_daily_budget(tmp_path):
    from datetime import datetime, timedelta, timezone

    repository = GameRepository(tmp_path / "backfill-visible-status.sqlite3")
    game_id = _import_games(repository, 1)[0]
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    row = repository.list_lichess_analysis_backfill("lichess:me", limit=1)[0]
    repository.mark_lichess_analysis_backfill_running(
        "lichess:me", game_id, line_revision=row["line_revision"],
        line_hash=row["line_hash"])
    assert repository.reserve_lichess_analysis_request("lichess:me", daily_limit=4)
    repository.record_lichess_analysis_backfill(
        "lichess:me", game_id, accuracy_status="has_data", evals_status="partial",
        retry_after_seconds=1800, line_revision=row["line_revision"], line_hash=row["line_hash"])
    with repository._connection() as db:
        db.execute("UPDATE games SET lichess_accuracy_white=91 "
                   "WHERE game_id=?", (game_id,))
    summary = repository.lichess_analysis_backfill_summary("lichess:me")
    assert summary["last_attempt_at"]
    assert summary["last_success_at"]
    assert summary["checked"] == 1
    assert summary["accuracy"] == 1
    # A partial status alone is not proof that any server evaluations were saved.
    assert summary["evals"] == 0
    assert summary["retry_waiting"] == 1
    assert summary["requests_today"] == 1
    assert summary["retry_at"]
    due = repository.lichess_analysis_backfill_next_eligible_at(
        "lichess:me", daily_limit=4, enqueue=False)
    assert due and datetime.fromisoformat(due) > datetime.now(timezone.utc)
    last_attempt = summary["last_attempt_at"]
    repository.set_lichess_analysis_backfill_paused("lichess:me", True)
    paused_summary = repository.lichess_analysis_backfill_summary("lichess:me")
    assert paused_summary["last_attempt_at"] == last_attempt
    assert paused_summary["paused"] is True
    repository.shutdown()


def test_backfill_next_eligibility_tracks_due_queue_retry_after_and_daily_reset(tmp_path):
    from datetime import datetime, timedelta, timezone

    repository = GameRepository(tmp_path / "durable-next-eligibility.sqlite3")
    game_id = _import_games(repository, 1)[0]
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    now = datetime.now(timezone.utc)
    due = repository.lichess_analysis_backfill_next_eligible_at("lichess:me")
    assert due is not None and datetime.fromisoformat(due) <= now + timedelta(seconds=1)

    retry_at = (now + timedelta(hours=3)).isoformat()
    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET accuracy_status='no_server_data',"
                   "accuracy_next_retry_at=?,evals_status='has_data',evals_next_retry_at=NULL "
                   "WHERE account_id='lichess:me' AND game_id=?", (retry_at, game_id))
    assert repository.lichess_analysis_backfill_next_eligible_at("lichess:me") == retry_at
    repository.shutdown()

    # Retry eligibility survives closing and reopening the real SQLite file.
    repository = GameRepository(tmp_path / "durable-next-eligibility.sqlite3")
    assert repository.lichess_analysis_backfill_next_eligible_at("lichess:me") == retry_at
    repository.shutdown()


def test_backfill_daily_limit_persists_midnight_wakeup_at_last_allowed_request(tmp_path):
    from datetime import datetime, timezone

    repository = GameRepository(tmp_path / "daily-wakeup.sqlite3")
    _import_games(repository, 1)
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    assert repository.reserve_lichess_analysis_request("lichess:me", daily_limit=1)
    next_at = repository.lichess_analysis_backfill_next_eligible_at(
        "lichess:me", daily_limit=1)
    assert next_at is not None
    next_dt = datetime.fromisoformat(next_at)
    assert next_dt.hour == next_dt.minute == next_dt.second == 0
    assert next_dt.date() > datetime.now(timezone.utc).date()
    assert not repository.lichess_analysis_backfill_has_due_work(
        "lichess:me", daily_limit=1)
    assert repository.lichess_analysis_retry_after("lichess:me") == next_at
    repository.shutdown()


def test_backfill_scheduler_has_no_wakeup_for_empty_or_paused_queue(tmp_path):
    repository = GameRepository(tmp_path / "empty-pause-wakeup.sqlite3")
    assert repository.lichess_analysis_backfill_next_eligible_at("lichess:me") is None
    _import_games(repository, 1)
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    repository.set_lichess_analysis_backfill_paused("lichess:me", True)
    assert repository.lichess_analysis_backfill_next_eligible_at("lichess:me") is None
    repository.shutdown()


def test_preexisting_partial_pgn_evaluations_still_get_metadata_backfill(tmp_path):
    repository = GameRepository(tmp_path / "partial-old.sqlite3")
    game_id = _import_games(repository, 1)[0]
    partial_export = _export(game_id, accuracy=False, evals=True)
    partial_export["analysis"] = [{"eval": 20}, None]
    partial_export["pgn"] = (
        f'[Event "Synthetic {game_id}"]\n[White "Me"]\n[Black "Opponent"]\n'
        '[Result "1-0"]\n\n1. e4 { [%eval 0.20] } e5 1-0\n')
    repository.import_archive_record(
        "Me", partial_export,
        checkpoint_ms=None, backfill=False)
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    rows = repository.list_lichess_analysis_backfill("lichess:me", limit=10)
    assert len(rows) == 1 and rows[0]["evals_status"] == "partial"
    repository.shutdown()


def test_ready_analytics_queue_prioritizes_recent_games_without_starving_old_backlog(tmp_path):
    repository = GameRepository(tmp_path / "ready-priority-fairness.sqlite3")
    game_ids = _import_games(repository, 25)
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    with repository._connection() as db:
        for index, game_id in enumerate(game_ids):
            db.execute("UPDATE games SET started_at=? WHERE game_id=?",
                       (recent if index >= 10 else old, game_id))
    try:
        first = repository.list_lichess_analysis_backfill("lichess:me", limit=10)
        first_ids = {row["game_id"] for row in first}
        assert len(first) == 10
        assert len(first_ids.intersection(game_ids[10:])) == 8
        assert len(first_ids.intersection(game_ids[:10])) == 2

        # Mark just the first page as temporarily deferred, as happens after a
        # no-data/partial response. The next page must continue selecting the
        # remaining durable backlog rather than requiring a full-archive scan.
        with repository._connection() as db:
            for game_id in first_ids:
                db.execute("UPDATE lichess_analysis_backfill SET accuracy_status='no_server_data',"
                           "evals_status='no_server_data',accuracy_next_retry_at=?,"
                           "evals_next_retry_at=? WHERE account_id='lichess:me' AND game_id=?",
                           ("2999-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", game_id))
        second = repository.list_lichess_analysis_backfill("lichess:me", limit=10)
        assert len(second) == 10
        assert not first_ids.intersection(row["game_id"] for row in second)
        assert len({row["game_id"] for row in second}) == 10
    finally:
        repository.shutdown()


def test_official_game_json_accuracy_and_move_analysis_are_saved_without_pgn_eval_comments(tmp_path):
    repository = GameRepository(tmp_path / "official-schema.sqlite3")
    game_id = _import_games(repository, 1)[0]
    export = _export(game_id)
    report = ArchiveSync(FakeJSONClient({game_id: export}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)

    assert report["checked"] == report["accuracy"] == report["evals"] == 1
    game = repository.get_game(game_id)
    assert game["lichess_accuracy_white"] == 91
    assert game["lichess_accuracy_black"] == 87
    assert game["white_analysis"] == {
        "accuracy": 91, "inaccuracy": 1, "mistake": 2, "blunder": 0, "acpl": 24,
        "phases": {"opening": 95, "middlegame": 87, "endgame": 90}}
    assert game["black_analysis"]["accuracy"] == 87
    assert game["lichess_analysis_source"] == "lichess-json"
    assert game["lichess_analysis_schema_version"] == "lichess-game-json-v1"
    assert game["opening_eco"] == "C20" and game["opening_name"] == "King's Pawn Game"
    assert game["white_rating"] == 1500 and game["black_rating"] == 1520
    assert repository.phase_for_ply(game_id, 1, 1) == "middlegame"
    assert repository.phase_for_ply(game_id, 1, 2) == "endgame"
    detail = repository.latest_analysis_detail(game_id, 1)
    by_ply = {row["ply"]: row for row in detail["assessments"]}
    assert by_ply[1]["white_cp"] == 20
    assert by_ply[2]["white_cp"] == 0
    assert repository.lichess_analysis_backfill_state("lichess:me", game_id)["evals_status"] == "has_data"
    assert repository.lichess_analysis_backfill_state("lichess:me", game_id)["accuracy_status"] == "has_data"
    assert not any(row["source"] == "stockfish" for row in repository.list_analyses(game_id))
    with repository._connection() as db:
        metadata = db.execute("SELECT source,schema_version,white_analysis_json,black_analysis_json "
                              "FROM lichess_ready_analysis_metadata WHERE game_id=?", (game_id,)).fetchone()
        assert tuple(metadata[:2]) == ("lichess-json", "lichess-game-json-v1")
    repository.shutdown()


def test_metadata_refresh_preserves_the_original_raw_pgn_snapshot(tmp_path):
    repository = GameRepository(tmp_path / "raw-pgn-preserve.sqlite3")
    game_id = _import_games(repository, 1)[0]
    before = repository.get_game(game_id)
    export = _export(game_id)
    export["pgn"] = _pgn(game_id, evals=True)  # The server response differs only by eval comments.
    ArchiveSync(FakeJSONClient({game_id: export}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    after = repository.get_game(game_id)
    assert after["raw_pgn"] == before["raw_pgn"]
    assert after["raw_checksum"] == before["raw_checksum"]
    assert after["moves"] == before["moves"]
    repository.shutdown()


def test_deeper_saved_pgn_score_is_not_replaced_by_depthless_json_score(tmp_path):
    repository = GameRepository(tmp_path / "score-provenance.sqlite3")
    game_id = "deep-score"
    pgn = ('[Event "Synthetic"]\n[White "Me"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'
           '1. e4 { [%eval 0.20,20] } e5 1-0\n')
    repository.import_archive_record("Me", {"id": game_id, "pgn": pgn},
                                      checkpoint_ms=None, backfill=False)
    export = _export(game_id)
    export["analysis"] = [{"eval": 900}, {"eval": 0}]
    ArchiveSync(FakeJSONClient({game_id: export}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    detail = repository.latest_analysis_detail(game_id, 1)
    by_ply = {item["ply"]: item for item in detail["assessments"]}
    assert by_ply[1]["white_cp"] == 20 and by_ply[1]["depth"] == 20
    assert by_ply[2]["white_cp"] == 0
    repository.shutdown()


def test_concurrent_line_replacement_rolls_back_old_json_metadata_and_marks_stale_line(tmp_path):
    repository = GameRepository(tmp_path / "line-cas.sqlite3")
    game_id = _import_games(repository, 1)[0]
    original = repository.get_game(game_id)

    class RacingClient(FakeJSONClient):
        def export_game_json(self, token, requested_id):
            payload = super().export_game_json(token, requested_id)
            # Simulate another legitimate writer replacing the current line after
            # the backfill page read but before its metadata transaction.
            repository.save_snapshot(game_id, "startpos", "d2d4 d7d5", "white", "Opponent",
                                     False, "5+0", "finished", "white",
                                     white_player="Me", black_player="Opponent")
            return payload

    fake = RacingClient({game_id: _export(game_id)})
    report = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    latest = repository.current_line(game_id)
    game = repository.get_game(game_id)
    queue = repository.lichess_analysis_backfill_state("lichess:me", game_id)
    assert report["errors"] == 1
    assert latest["line_revision"] == 2 and latest["moves"] == "d2d4 d7d5"
    assert game["lichess_accuracy_white"] is None
    assert game["raw_pgn"] == original["raw_pgn"]
    assert queue["accuracy_status"] == queue["evals_status"] == "stale_line"
    assert repository.list_analyses(game_id, 2) == []
    repository.shutdown()


def test_auth_generation_change_before_commit_rolls_back_all_metadata(tmp_path):
    repository = GameRepository(tmp_path / "generation-before-commit.sqlite3")
    game_id = _import_games(repository, 1)[0]
    line = repository.current_line(game_id)
    original = repository.get_game(game_id)
    checks = {"count": 0}

    def generation_is_current():
        checks["count"] += 1
        return checks["count"] == 1

    with pytest.raises(StaleGameLineError, match="stale_session"):
        repository.merge_lichess_ready_analysis(
            "Me", game_id, source_game_id=game_id, account_id="lichess:me",
            expected_line_revision=line["line_revision"], expected_line_hash=line["line_hash"],
            expected_moves=line["moves"], accuracy={"white": 99, "black": 98},
            player_analysis={"white": {"accuracy": 99}, "black": {"accuracy": 98}},
            assessments=[{"ply": 1, "white_cp": 75, "mate_white": None, "depth": None}],
            move_annotations=[], validation_issues=[], generation_is_current=generation_is_current)
    after = repository.get_game(game_id)
    assert after["lichess_accuracy_white"] == original["lichess_accuracy_white"] is None
    assert after["white_analysis"] == {}
    assert repository.list_analyses(game_id, line["line_revision"]) == []
    repository.shutdown()


def test_v18_queue_migration_preserves_rows_and_allows_stale_line_status(tmp_path):
    path = tmp_path / "backfill-v18-migration.sqlite3"
    repository = GameRepository(path)
    game_id = _import_games(repository, 1)[0]
    account_id = repository.ensure_archive_account("Me")
    repository.enqueue_lichess_analysis_backfill(account_id)
    queued = repository.list_lichess_analysis_backfill(account_id, limit=1)[0]
    repository.shutdown()

    with __import__("sqlite3").connect(path) as db:
        db.execute("DROP INDEX IF EXISTS idx_lichess_analysis_backfill_due")
        db.execute("DROP TABLE lichess_analysis_backfill")
        db.execute("""CREATE TABLE lichess_analysis_backfill (
            account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
            game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
            line_revision INTEGER NOT NULL,line_hash TEXT NOT NULL,
            accuracy_status TEXT NOT NULL DEFAULT 'queued'
              CHECK(accuracy_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error')),
            evals_status TEXT NOT NULL DEFAULT 'queued'
              CHECK(evals_status IN ('queued','running','has_data','no_server_data','partial','rate_limited','error')),
            accuracy_checked_at TEXT,evals_checked_at TEXT,accuracy_next_retry_at TEXT,
            evals_next_retry_at TEXT,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,
            updated_at TEXT NOT NULL,PRIMARY KEY(account_id,game_id))""")
        now = "2026-11-03T00:00:00+00:00"
        db.execute("INSERT INTO lichess_analysis_backfill(account_id,game_id,line_revision,line_hash,"
                   "accuracy_status,evals_status,accuracy_checked_at,evals_checked_at,"
                   "accuracy_next_retry_at,evals_next_retry_at,attempts,last_error,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (account_id, game_id, queued["line_revision"], queued["line_hash"],
                    "partial", "partial", None, None, now, now, 1, "previous", now))
        db.execute("CREATE INDEX idx_lichess_analysis_backfill_due ON "
                   "lichess_analysis_backfill(account_id,accuracy_next_retry_at,"
                   "evals_next_retry_at,updated_at)")
        db.execute("PRAGMA user_version=18")

    migrated = GameRepository(path)
    row = migrated.lichess_analysis_backfill_state(account_id, game_id)
    assert row["accuracy_status"] == row["evals_status"] == "partial"
    migrated.record_lichess_analysis_backfill(account_id, game_id,
                                               accuracy_status="stale_line",
                                               evals_status="stale_line",
                                               retry_after_seconds=60,
                                               error="stale_line: synthetic concurrent edit")
    assert migrated.lichess_analysis_backfill_state(account_id, game_id)["accuracy_status"] == "stale_line"
    with migrated._connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    migrated.shutdown()


def test_ai_player_accuracy_and_signed_mate_zero_are_normalized(tmp_path):
    repository = GameRepository(tmp_path / "ai-json-schema.sqlite3")
    game_id = _import_games(repository, 1)[0]
    export = _export(game_id)
    export["players"]["black"] = {
        "aiLevel": 3,
        "analysis": {"accuracy": 82, "inaccuracy": 2, "mistake": 1, "blunder": 0, "acpl": 18},
    }
    export["analysis"] = [{"eval": 35}, {"mate": 0}]
    report = ArchiveSync(FakeJSONClient({game_id: export}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    assert report["accuracy"] == report["evals"] == 1
    game = repository.get_game(game_id)
    assert game["lichess_accuracy_black"] == 82
    detail = repository.latest_analysis_detail(game_id, 1)
    by_ply = {row["ply"]: row for row in detail["assessments"]}
    assert by_ply[1]["white_cp"] == 35
    assert by_ply[2]["white_cp"] is None and by_ply[2]["mate_white"] == 0
    repository.shutdown()


def test_normalizer_rejects_nonfinite_accuracy_booleans_and_malformed_counts():
    normalized = normalize_game_analytics({
        "players": {
            "white": {"analysis": {"accuracy": float("nan"), "blunder": True,
                                    "acpl": -1}},
            "black": {"aiLevel": 2, "analysis": {"accuracy": 101, "mistake": "1"}},
        },
        "analysis": [{"eval": True}],
    }, expected_plies=1)
    assert normalized.accuracy == {"white": None, "black": None}
    assert normalized.player_analysis == {"white": {}, "black": {}}
    assert normalized.assessments == ()
    assert {"invalid_white_accuracy", "invalid_white_blunder", "invalid_white_acpl",
            "invalid_black_accuracy", "invalid_black_mistake", "invalid_eval_ply_1"}.issubset(
                normalized.issues)


def test_invalid_official_analytics_is_reported_partial_not_no_server_data(tmp_path):
    repository = GameRepository(tmp_path / "invalid-not-absent.sqlite3")
    game_id = _import_games(repository, 1)[0]
    payload = _export(game_id)
    payload["players"]["white"]["analysis"]["accuracy"] = float("nan")
    payload["players"]["black"]["analysis"].pop("accuracy")
    payload["analysis"] = [{"eval": True}, None]
    ArchiveSync(FakeJSONClient({game_id: payload}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    state = repository.lichess_analysis_backfill_state("lichess:me", game_id)
    assert state["accuracy_status"] == "partial"
    assert state["evals_status"] == "partial"
    assert state["accuracy_status"] != "no_server_data"
    assert state["evals_status"] != "no_server_data"
    repository.shutdown()


@pytest.mark.parametrize("analysis", [
    [{"eval": 20}],                 # Truncated array must not shift the first score.
    [{"eval": 20}, None],           # Empty item preserves its ply index.
    [{"eval": 20}, {"eval": 0}, {"eval": 30}],  # Extra score is not silently accepted.
    [{"eval": 20}, {"eval": 0, "mate": 3}],     # Conflicting score representations.
    [{"eval": 20}, {"eval": True}],             # bool is not a CP integer.
])
def test_incomplete_or_invalid_json_analysis_is_partial_and_keeps_ply_indices(tmp_path, analysis):
    repository = GameRepository(tmp_path / f"invalid-json-{len(analysis)}-{abs(hash(str(analysis)))}.sqlite3")
    game_id = _import_games(repository, 1)[0]
    export = _export(game_id)
    export["analysis"] = analysis
    ArchiveSync(FakeJSONClient({game_id: export}), repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    detail = repository.latest_analysis_detail(game_id, 1)
    by_ply = {row["ply"]: row for row in detail["assessments"]}
    assert by_ply[1]["white_cp"] == 20
    assert repository.lichess_analysis_backfill_state("lichess:me", game_id)["evals_status"] == "partial"
    repository.shutdown()


def test_partial_data_keeps_previous_verified_accuracy_and_local_stockfish_source(tmp_path):
    repository = GameRepository(tmp_path / "partial.sqlite3")
    game_id = _import_games(repository, 1)[0]
    with repository._connection() as db:
        db.execute("UPDATE games SET lichess_accuracy_white=88.0 WHERE game_id=?", (game_id,))
    # The JSON has evals but no accuracy: importer COALESCE must retain known data.
    fake = FakeJSONClient({game_id: _export(game_id, accuracy=False, evals=True)})
    result = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    assert result["checked"] == 1 and result["evals"] == 1
    game = repository.get_game(game_id)
    assert game["lichess_accuracy_white"] == 88.0
    assert repository.list_games_missing_lichess_evaluations("lichess:me") == []
    repository.shutdown()


def test_backfill_stops_on_rate_limit_and_respects_global_retry_after(tmp_path):
    repository = GameRepository(tmp_path / "rate-limit.sqlite3")
    ids = _import_games(repository, 3)

    class LimitedClient:
        rate_limit_remaining = 0
        calls = []
        def export_game_json(self, _token, game_id):
            self.calls.append(game_id)
            self.rate_limit_remaining = 85
            raise RateLimitError(85)

    fake = LimitedClient()
    report = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10)
    assert report["errors"] == 1 and report["fatal_status"] == 429
    assert len(fake.calls) == 1
    again = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10, force_retry=True)
    assert again["selected"] == 0
    assert len(fake.calls) == 1
    repository.shutdown()


def test_stale_account_generation_and_mismatched_line_do_not_merge(tmp_path):
    repository = GameRepository(tmp_path / "stale.sqlite3")
    game_id = _import_games(repository, 1)[0]
    current = {"value": True}

    class StaleClient(FakeJSONClient):
        def export_game_json(self, token, game_id):
            result = super().export_game_json(token, game_id)
            current["value"] = False
            return result

    fake = StaleClient({game_id: _export(game_id)})
    report = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), generation_is_current=lambda: current["value"])
    assert report["checked"] == 0
    assert repository.get_game(game_id)["lichess_accuracy_white"] is None
    assert repository.lichess_eval_score_count(game_id, 1) == 0
    # Interrupted `running` rows are recovered by the next process/queue scan.
    with repository._connection() as db:
        db.execute("UPDATE lichess_analysis_backfill SET updated_at=? WHERE game_id=?",
                   ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(), game_id))
    repository.enqueue_lichess_analysis_backfill("lichess:me")
    assert repository.list_lichess_analysis_backfill("lichess:me", limit=1)[0]["accuracy_status"] == "queued"
    repository.shutdown()


def test_mismatched_game_id_or_line_is_rejected_without_rewriting_saved_moves(tmp_path):
    repository = GameRepository(tmp_path / "wrong-line.sqlite3")
    game_id = _import_games(repository, 1)[0]
    before = repository.current_line(game_id)
    wrong = _export(game_id)
    wrong["pgn"] = _pgn("different-line", evals=True).replace("1. e4", "1. d4")
    fake = FakeJSONClient({game_id: wrong})
    report = ArchiveSync(fake, repository).refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=1)
    assert report["errors"] == 1
    assert repository.current_line(game_id)["line_hash"] == before["line_hash"]
    assert repository.get_game(game_id)["lichess_accuracy_white"] is None
    repository.shutdown()
