import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from chess_coach.lichess.api import HttpResponse, LichessClient, RateLimitError
from chess_coach.lichess.archive import ArchiveSync
from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION


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
    return {
        "id": game_id, "status": "mate", "pgn": _pgn(game_id, evals=evals),
        "players": {
            "white": {"user": {"id": "owner-id", "name": "Me"}, "analysis": {"inaccuracy": 0}},
            "black": {"user": {"id": "opponent-id", "name": "Opponent"}},
        },
        **({"accuracy": {"white": 91.5, "black": 87.25}} if accuracy else {}),
        "opening": {"eco": "C20", "name": "King's Pawn Game"},
        "division": {"middle": "2", "end": "4"},
    }


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
    assert repository.get_game(first_game_id)["lichess_accuracy_white"] == 91.5
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
    assert repository.get_game(first_game_id)["lichess_accuracy_black"] == 87.25
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
    manual = sync.refresh_ready_analytics(
        "Me", "fake", threading.Event(), limit=10, force_retry=True)
    assert manual["checked"] == 1 and len(fake.calls) == 2
    repository.shutdown()


def test_preexisting_partial_pgn_evaluations_still_get_metadata_backfill(tmp_path):
    repository = GameRepository(tmp_path / "partial-old.sqlite3")
    game_id = _import_games(repository, 1)[0]
    partial_export = _export(game_id, accuracy=False, evals=True)
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
