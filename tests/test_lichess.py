import base64
import hashlib
import json
import sqlite3
import threading
import time
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

import chess
import chess.pgn
import pytest

from chess_coach.lichess.api import HttpResponse, LichessClient, LichessError, RateLimitError
from chess_coach.lichess.auth import OAuthCallbackServer, create_authorization_request
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.session import LichessGameSession
from chess_coach.lichess.service import LichessService


class FakeTransport:
    def __init__(self, responses=None, lines=None, stream_error=None):
        self.responses = list(responses or [])
        self.lines = list(lines or [])
        self.stream_error = stream_error
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)

    def stream(self, method, url, headers, body, timeout, stop_event):
        if self.stream_error:
            raise self.stream_error
        yield from self.lines


class FakeChallengeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create_open_challenge(self, *args):
        self.calls.append(("create", args))
        return dict(self.result)

    def accept_open_challenge(self, *args):
        self.calls.append(("accept", args))
        return {"ok": True}

    def cancel_challenge(self, *args):
        self.calls.append(("cancel", args))


def response(status, data):
    return HttpResponse(status, {}, json.dumps(data).encode())


def test_pkce_request_uses_s256_and_minimal_scopes():
    auth = create_authorization_request("http://127.0.0.1:1234/oauth/callback")
    params = parse_qs(urlparse(auth.url).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(auth.verifier.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["board:play challenge:write"]
    assert params["state"] == [auth.state]


def test_loopback_oauth_callback_checks_state_without_lichess_network():
    callback = OAuthCallbackServer()
    auth = create_authorization_request(callback.redirect_uri)
    try:
        with urlopen(f"{callback.redirect_uri}?code=local-code&state={auth.state}", timeout=2) as response:
            assert response.status == 200
        assert callback.wait(auth.state, timeout=2) == "local-code"
    finally:
        callback.close()


def test_loopback_oauth_callback_rejects_state_mismatch():
    callback = OAuthCallbackServer()
    try:
        with urlopen(f"{callback.redirect_uri}?code=local-code&state=wrong-state", timeout=2):
            pass
        with __import__("pytest").raises(LichessError, match="state"):
            callback.wait("expected-state", timeout=2)
    finally:
        callback.close()


def test_open_challenge_then_creator_accept_with_chosen_color():
    fake = FakeTransport([response(201, {"id": "abc", "urlWhite": "w", "urlBlack": "b"}), response(200, {"ok": True})])
    client = LichessClient(fake, base_url="https://lichess.test")
    created = client.create_open_challenge("token", "black", False, 180, 2)
    accepted = client.accept_open_challenge("token", created["id"], "black")
    assert accepted["ok"]
    assert fake.calls[0][1].endswith("/api/challenge/open")
    assert b"clock.limit=180" in fake.calls[0][3]
    assert "color=black" in fake.calls[1][1]


def test_unlimited_open_challenge_omits_clock_and_premoves_never_change_server_state():
    fake = FakeTransport([response(201, {"id": "unlimited", "timeControl": {"type": "unlimited"}})])
    client = LichessClient(fake)
    client.create_open_challenge("token", "white", False)
    assert b"clock.limit" not in (fake.calls[0][3] or b"")


def test_open_link_is_accepted_only_after_server_confirms_unlimited_and_color_url():
    service = LichessService(client=FakeChallengeClient({
        "id": "invite", "timeControl": {"type": "unlimited"},
        "whiteUrl": "https://lichess.org/white", "blackUrl": "https://lichess.org/black",
        "rated": False,
    }), token_store=object())
    result = service._create_open("token", "white", None, 0, False)
    assert result["friendUrl"] == "https://lichess.org/black"
    assert [call[0] for call in service.client.calls] == ["create", "accept"]


def test_open_link_cancels_instead_of_silently_creating_correspondence():
    service = LichessService(client=FakeChallengeClient({
        "id": "invite", "timeControl": {"type": "correspondence"},
    }), token_store=object())
    with __import__("pytest").raises(LichessError, match="часов"):
        service._create_open("token", "white", None, 0, False)
    assert [call[0] for call in service.client.calls] == ["create", "cancel"]


def test_username_challenge_uses_keepalive_ndjson_stream():
    fake = FakeTransport(lines=[b'{"type":"challenge","challenge":{"id":"wait1"}}\n', b'{"type":"challengeDeclined"}\n'])
    client = LichessClient(fake)
    events = list(client.username_challenge_events("token", "Friend", {
        "rated": "false", "keepAliveStream": "true", "color": "white"
    }, threading.Event()))
    assert [event["type"] for event in events] == ["challenge", "challengeDeclined"]


def test_move_draw_resign_and_abort_use_documented_board_api_routes():
    fake = FakeTransport([response(200, {"ok": True}) for _ in range(6)])
    client = LichessClient(fake)
    client.send_move("token", "game1234", "e2e4")
    client.handle_draw("token", "game1234", True)
    client.resign("token", "game1234")
    client.abort("token", "game1234")
    client.handle_takeback("token", "game1234", True)
    client.handle_takeback("token", "game1234", False)
    assert fake.calls[0][1].endswith("/api/board/game/game1234/move/e2e4")
    assert fake.calls[1][1].endswith("/api/board/game/game1234/draw/yes")
    assert fake.calls[2][1].endswith("/api/board/game/game1234/resign")
    assert fake.calls[3][1].endswith("/api/board/game/game1234/abort")
    assert fake.calls[4][1].endswith("/api/board/game/game1234/takeback/yes")
    assert fake.calls[5][1].endswith("/api/board/game/game1234/takeback/no")


def test_api_errors_and_429_cooldown(monkeypatch):
    fake = FakeTransport([HttpResponse(401, {}, b"{}"), HttpResponse(429, {}, b"{}")])
    client = LichessClient(fake)
    try:
        client.get_account("token")
    except LichessError as exc:
        assert exc.status == 401 and "отозвана" in str(exc)
    else:
        raise AssertionError("401 must fail")
    try:
        client.get_account("token")
    except RateLimitError as exc:
        assert exc.retry_after >= 60
    else:
        raise AssertionError("429 must fail")
    with __import__("pytest").raises(RateLimitError):
        client.get_account("token")


def test_get_active_games_uses_account_playing_and_discards_malformed_entries():
    payload = {"nowPlaying": [
        {"gameId": "game1234", "color": "white", "opponent": {"username": "Friend"}},
        None,
        {"color": "black"},
    ]}
    fake = FakeTransport([response(200, payload)])
    client = LichessClient(fake, base_url="https://lichess.test")

    games = client.get_playing_games("token")

    assert [game["gameId"] for game in games] == ["game1234"]
    assert fake.calls[0][0] == "GET"
    assert fake.calls[0][1].endswith("/api/account/playing?nb=50")
    assert fake.calls[0][2]["Authorization"] == "Bearer token"


def test_active_game_list_service_uses_one_explicit_request_without_retry(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    class Client:
        def __init__(self):
            self.calls = 0

        def get_playing_games(self, token):
            self.calls += 1
            assert token == "fake-token"
            return [{"gameId": "game1234"}]

    client = Client()
    service = LichessService(client=client, token_store=TokenStore())
    results = []
    service.active_games_received.connect(results.append)
    workers = []
    monkeypatch.setattr(service, "_thread", lambda target, name: (workers.append(name), target()))

    service.fetch_active_games()

    assert results == [[{"gameId": "game1234"}]]
    assert client.calls == 1
    assert workers == ["chess-coach-active-games"]


def test_active_game_list_429_is_reported_without_automatic_retry(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    class Client:
        def __init__(self):
            self.calls = 0

        def get_playing_games(self, _token):
            self.calls += 1
            raise RateLimitError(90)

    client = Client()
    service = LichessService(client=client, token_store=TokenStore())
    errors = []
    service.active_games_error.connect(errors.append)
    monkeypatch.setattr(service, "_thread", lambda target, _name: target())

    service.fetch_active_games()

    assert client.calls == 1
    assert errors and "90" in errors[0]


def test_retry_after_header_is_honored_and_stream_429_blocks_regular_requests():
    fake = FakeTransport([HttpResponse(429, {"Retry-After": "125"}, b"{}")])
    client = LichessClient(fake)
    with __import__("pytest").raises(RateLimitError) as caught:
        client.get_account("token")
    assert caught.value.retry_after >= 125
    assert len(fake.calls) == 1

    streaming = FakeTransport(stream_error=RateLimitError(75))
    client = LichessClient(streaming)
    with __import__("pytest").raises(RateLimitError) as caught:
        list(client.stream_events("token", threading.Event()))
    assert caught.value.retry_after >= 75
    with __import__("pytest").raises(RateLimitError):
        client.get_account("token")
    assert streaming.calls == []


def test_lichess_service_reports_429_without_starting_retry_worker(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

        def delete(self):
            raise AssertionError("429 must not delete a valid token")

    class Client:
        def get_account(self, token):
            raise RateLimitError(90)

    service = LichessService(client=Client(), token_store=TokenStore())
    statuses = []
    errors = []
    service.status_changed.connect(statuses.append)
    service.error.connect(errors.append)
    workers = []
    monkeypatch.setattr(service, "_thread", lambda target, name: (workers.append(name), target()))
    service.restore()
    assert statuses[-1].startswith("Ограничение Lichess (429)")
    assert "90" in statuses[-1]
    assert len(workers) == 1
    assert len(errors) == 1


def test_game_stream_start_is_deduplicated_per_game(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    service = LichessService(client=object(), token_store=TokenStore())
    workers = []
    monkeypatch.setattr(service, "_thread", lambda target, name: workers.append((target, name)))
    service.start_game("same-game", "fake-token")
    service.start_game("same-game", "fake-token")
    assert len(workers) == 1
    assert "same-game" in workers[0][1]


def test_global_event_stream_is_started_once_per_connection(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    service = LichessService(client=object(), token_store=TokenStore())
    workers = []
    monkeypatch.setattr(service, "_thread", lambda target, name: workers.append((target, name)))
    service._start_event_stream("fake-token")
    service._start_event_stream("fake-token")
    assert len(workers) == 1
    assert workers[0][1] == "chess-coach-lichess-events"


def test_event_stream_429_waits_before_retry_and_does_not_spin(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    class Client:
        def __init__(self):
            self.calls = 0

        def stream_events(self, token, stop_event):
            self.calls += 1
            raise RateLimitError(70)
            yield  # pragma: no cover

    class StopEvent:
        def __init__(self):
            self.stopped = False
            self.waits = []

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            self.waits.append(seconds)
            self.stopped = True
            return True

    client = Client()
    service = LichessService(client=client, token_store=TokenStore())
    service._connection_stop = StopEvent()
    statuses = []
    service.status_changed.connect(statuses.append)
    monkeypatch.setattr(service, "_thread", lambda target, name: target())
    service._start_event_stream("fake-token")
    assert client.calls == 1
    assert service._connection_stop.waits == [70]
    assert any(message.startswith("Ограничение Lichess (429)") for message in statuses)


def test_finished_game_stream_interrupt_has_bounded_retry_and_keeps_sync_pending(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    class Client:
        def __init__(self):
            self.calls = 0

        def stream_game(self, token, game_id, stop_event):
            self.calls += 1
            raise LichessError("offline disconnect")
            yield  # pragma: no cover

    class WaitControl:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, seconds):
            self.waits.append(seconds)
            return False

    client = Client()
    service = LichessService(client=client, token_store=TokenStore())
    control = WaitControl()
    service._connection_stop = control
    statuses = []
    service.status_changed.connect(statuses.append)
    service.mark_game_finished("disconnected-final")
    monkeypatch.setattr(service, "_thread", lambda target, name: target())

    service.start_game("disconnected-final", "fake-token")

    assert client.calls == 2
    assert control.waits == [1]
    assert any("Финальная синхронизация" in status for status in statuses)
    assert "disconnected-final" in service._final_sync_game_ids


def test_game_stream_rebuilds_confirmed_state_and_saves_pgn(tmp_path):
    repo = GameRepository(tmp_path / "games.sqlite3")
    session = LichessGameSession("abc123", repo)
    session.color = chess.WHITE
    full = {"type": "gameFull", "id": "abc123", "initialFen": "startpos", "rated": False,
            "clock": {"initial": 180000, "increment": 2000},
            "state": {"moves": "e2e4", "wtime": 180000, "btime": 180000,
                      "winc": 2000, "binc": 2000, "status": "started"}}
    snapshot = session.apply_event(full)
    assert snapshot.board.peek() == chess.Move.from_uci("e2e4")
    assert session.begin_submission(chess.Move.from_uci("g1f3")) is False
    state = {"type": "gameState", "moves": "e2e4 e7e5", "wtime": 178000,
             "btime": 179000, "winc": 2000, "binc": 2000, "status": "started"}
    snapshot = session.apply_event(state)
    assert snapshot.board.peek() == chess.Move.from_uci("e7e5")
    assert snapshot.is_my_turn
    assert session.begin_submission(chess.Move.from_uci("g1f3"))
    row = repo.get_game("abc123")
    assert row["moves"] == "e2e4 e7e5"
    assert "1. e4 e5" in repo.export_pgn("abc123")
    # Duplicate stream payload is idempotent in SQLite.
    session.apply_event(state)
    assert len(repo.list_games(completed_only=False)) == 1


def test_repository_closes_every_connection_including_exception_and_async_writer(
    tmp_path, monkeypatch
):
    import chess_coach.lichess.repository as repository_module

    real_connect = sqlite3.connect
    connections = []

    class TrackedConnection:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def __setattr__(self, name, value):
            if name in {"connection", "closed"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self.connection, name, value)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self.connection.__exit__(exc_type, exc_value, traceback)

        def close(self):
            self.closed = True
            self.connection.close()

    def tracked_connect(*args, **kwargs):
        connection = TrackedConnection(real_connect(*args, **kwargs))
        connections.append(connection)
        return connection

    monkeypatch.setattr(repository_module.sqlite3, "connect", tracked_connect)
    path = tmp_path / "connections.sqlite3"
    repository = GameRepository(path, async_writes=True)
    repository.save_snapshot("tracked", "startpos", "e2e4", "white", "Opponent",
                             False, "180+2", "started")
    repository.wait_for_writes()
    repository.list_games(completed_only=False)
    repository.get_game("tracked")
    repository.list_pending_final_sync()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with repository._connection() as db:
            db.execute("SELECT * FROM missing_table")

    assert connections
    assert all(connection.closed for connection in connections)
    repository.shutdown()
    renamed = path.with_name("connections-renamed.sqlite3")
    path.rename(renamed)
    renamed.unlink()


def test_m3_migration_preserves_legacy_game_creates_revision_and_backup(tmp_path):
    path = tmp_path / "games.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE games (
        game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, moves TEXT NOT NULL,
        color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER NOT NULL,
        clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT,
        updated_at TEXT NOT NULL, pgn TEXT NOT NULL,
        white_player TEXT NOT NULL DEFAULT '?', black_player TEXT NOT NULL DEFAULT '?',
        started_at TEXT, final_sync_pending INTEGER NOT NULL DEFAULT 0
    )""")
    legacy_pgn = '[Event "Lichess casual"]\n\n1. e4 e5 *\n'
    db.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("legacy", "startpos", "e2e4 e7e5", "white", "Friend", 0,
                "180+2", "resign", "black", "2026-01-01T00:00:00+00:00",
                legacy_pgn, "Me", "Friend", "2026-01-01T00:00:00+00:00", 0))
    db.commit()
    db.close()

    repo = GameRepository(path)
    row = repo.get_game("legacy")
    assert row["pgn"] == legacy_pgn
    assert row["moves"] == "e2e4 e7e5"
    assert row["white_player"] == "Me"
    assert row["line_revision"] == 1
    assert row["import_error"] is None
    with repo._connection() as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 1
        assert migrated.execute("SELECT COUNT(*) FROM game_positions").fetchone()[0] == 3
        assert migrated.execute("SELECT san FROM game_moves ORDER BY ply").fetchall()[0][0] == "e4"
    backup = sqlite3.connect(str(path) + ".pre-m3-v1.bak")
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT pgn FROM games WHERE game_id='legacy'").fetchone()[0] == legacy_pgn
    finally:
        backup.close()

    # Reopening at the current version is idempotent and never replays legacy rows.
    GameRepository(path)
    with repo._connection() as migrated:
        assert migrated.execute("SELECT COUNT(*) FROM game_revisions").fetchone()[0] == 1


def test_m3_invalid_legacy_line_is_retained_and_marked_for_repair(tmp_path):
    path = tmp_path / "invalid.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE games (game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, "
               "moves TEXT NOT NULL, color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER "
               "NOT NULL, clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT, updated_at "
               "TEXT NOT NULL, pgn TEXT NOT NULL)")
    db.execute("INSERT INTO games VALUES ('legacy','startpos','e2e4','white','Friend',0,'-',"
               "'mate','white','2026-01-01','raw')")
    raw_pgn = "retained raw PGN"
    db.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               ("bad", "startpos", "not-a-move", "white", "Opponent", 0, "-", "mate",
                "white", "2026-01-01", raw_pgn))
    db.commit()
    db.close()
    repo = GameRepository(path)
    row = repo.get_game("bad")
    assert row["pgn"] == raw_pgn
    assert row["moves"] == "not-a-move"
    assert row["import_error"]
    assert repo.current_line("bad") is None


def test_m3_takeback_keeps_revision_and_position_notes(tmp_path):
    repo = GameRepository(tmp_path / "revisions.sqlite3")
    repo.save_snapshot("game", "startpos", "e2e4 e7e5 g1f3", "white", "AI",
                       False, "180+2", "started")
    note_id = repo.save_note("game", "I considered this plan", line_revision=1, ply=2)
    repo.save_snapshot("game", "startpos", "e2e4 e7e5", "white", "AI",
                       False, "180+2", "started")
    assert repo.get_game("game")["line_revision"] == 2
    assert repo.current_line("game")["line_revision"] == 2
    assert repo.list_notes("game")[0]["note_id"] == note_id
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM game_revisions WHERE game_id='game'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM game_positions WHERE game_id='game' "
                          "AND line_revision=1").fetchone()[0] == 4


def test_m3_paginated_search_and_note_crud(tmp_path):
    repo = GameRepository(tmp_path / "library.sqlite3")
    for index in range(25):
        repo.save_snapshot(f"g{index:02}", "startpos", "e2e4", "white",
                           "Friend" if index % 2 else "AI", False, "-", "mate")
    page = repo.search_games("Friend", page=1, page_size=4)
    assert page["total"] == 12
    assert page["pages"] == 3
    assert len(page["items"]) == 4
    note = repo.save_note("g01", "Opening thought", line_revision=1, ply=1)
    repo.update_note(note, "Updated thought")
    assert repo.list_notes("g01")[0]["body"] == "Updated thought"
    repo.delete_note(note)
    assert repo.list_notes("g01") == []


def test_m3_migration_rolls_back_schema_and_version_on_backfill_failure(tmp_path, monkeypatch):
    import chess_coach.lichess.repository as repository_module

    path = tmp_path / "rollback.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE games (game_id TEXT PRIMARY KEY, initial_fen TEXT NOT NULL, "
               "moves TEXT NOT NULL, color TEXT NOT NULL, opponent TEXT NOT NULL, rated INTEGER "
               "NOT NULL, clock TEXT NOT NULL, status TEXT NOT NULL, winner TEXT, updated_at "
               "TEXT NOT NULL, pgn TEXT NOT NULL)")
    db.execute("INSERT INTO games VALUES ('legacy','startpos','e2e4','white','Friend',0,'-',"
               "'mate','white','2026-01-01','raw')")
    db.commit()
    db.close()
    monkeypatch.setattr(repository_module.GameRepository, "_write_revision",
                        staticmethod(lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected"))))
    with pytest.raises(RuntimeError, match="injected"):
        GameRepository(path)
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
        assert "source" not in columns
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='game_revisions'").fetchone()
    finally:
        db.close()


def test_m3_library_search_handles_ten_thousand_games_with_paging(tmp_path):
    repo = GameRepository(tmp_path / "large-library.sqlite3")
    with repo._connection() as db:
        db.executemany("""INSERT INTO games (
            game_id, initial_fen, moves, color, opponent, rated, clock, status,
            winner, updated_at, pgn, white_player, black_player, started_at,
            final_sync_pending, source, source_game_id, variant, line_revision
        ) VALUES (?, 'startpos', '', 'white', ?, 0, '-', 'mate', 'white', ?, '',
                  'Player', 'Opponent', NULL, 0, 'lichess', ?, 'standard', 1)""",
            ((f"game-{i:05}", "Classical friend" if i % 2 else "AI",
              f"2026-01-{i % 28 + 1:02}T00:00:00+00:00", f"game-{i:05}")
             for i in range(10_000)))
    page = repo.search_games("Classical", page=9, page_size=500)
    assert page["total"] == 5_000
    assert len(page["items"]) == 500
    assert page["page"] == 9
    assert page["pages"] == 10


def test_premove_is_an_intent_and_revalidated_after_opponent_move(tmp_path):
    session = LichessGameSession("premove", GameRepository(tmp_path / "games.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos", "state":
        {"moves": "e2e4", "status": "started"}})
    premove = chess.Move.from_uci("g1f3")
    assert session.queue_premove(premove)
    assert session.board.peek() == chess.Move.from_uci("e2e4")
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
    assert session.take_ready_premove() == premove
    assert session.board.peek() == chess.Move.from_uci("e7e5")
    assert session.take_ready_premove() is None


def test_premove_is_discarded_if_opponent_move_makes_it_illegal(tmp_path):
    session = LichessGameSession("check", GameRepository(tmp_path / "games.sqlite3"))
    session.color = chess.WHITE
    fen = "4k3/8/8/2b5/8/8/8/4K1N1 b - - 0 1"
    session.apply_event({"type": "gameFull", "initialFen": fen, "state": {"moves": "", "status": "started"}})
    move = chess.Move.from_uci("g1f3")
    assert session.queue_premove(move)
    session.apply_event({"type": "gameState", "moves": "c5f2", "status": "started"})
    assert session.board.is_check()
    assert session.take_ready_premove() is None
    assert session.board.move_stack == [chess.Move.from_uci("c5f2")]


def test_premove_waits_for_opponent_reply_without_being_consumed(tmp_path):
    session = LichessGameSession("wait", GameRepository(tmp_path / "games.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started"}})
    move = chess.Move.from_uci("b1c3")
    assert session.queue_premove(move)
    assert session.take_ready_premove() is None
    assert session.premove == move
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
    assert session.take_ready_premove() == move


def test_online_move_is_pending_until_stream_confirmation(tmp_path):
    session = LichessGameSession("pending", GameRepository(tmp_path / "games.sqlite3"))
    session.apply_event({"type": "gameFull", "initialFen": "startpos", "state": {"moves": "", "status": "started"}})
    move = chess.Move.from_uci("e2e4")
    assert session.begin_submission(move)
    assert session.pending_uci == "e2e4"
    assert not session.begin_submission(move)  # prevents a duplicate POST
    assert session.board.move_stack == []  # never optimistically commits
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started"})
    assert session.pending_uci is None
    assert session.board.move_stack == [move]


def test_pending_move_has_render_only_position_until_server_confirmation(tmp_path):
    session = LichessGameSession("optimistic", GameRepository(tmp_path / "optimistic.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    move = chess.Move.from_uci("e2e4")
    assert session.begin_submission(move)
    assert session.board.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert session.board.piece_at(chess.E4) is None
    assert session.render_board.piece_at(chess.E2) is None
    assert session.render_board.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert session.moves == ""
    assert repo_moves(session.repository, "optimistic") == ""
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started"})
    assert session.pending_uci is None
    assert session.board.fen() == session.render_board.fen()
    assert repo_moves(session.repository, "optimistic") == "e2e4"


def repo_moves(repository, game_id):
    return repository.get_game(game_id)["moves"]


def test_definitive_rejection_removes_render_only_move_but_timeout_keeps_it(tmp_path):
    session = LichessGameSession("optimistic-failure", GameRepository(tmp_path / "optimistic-failure.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    assert session.begin_submission(chess.Move.from_uci("e2e4"))
    request_id = session.pending_request_id
    assert not session.fail_submission(request_id, definitive=False)
    assert session.render_board.piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert session.fail_submission(request_id, definitive=True)
    assert session.render_board.piece_at(chess.E2) == chess.Piece(chess.PAWN, chess.WHITE)
    assert repo_moves(session.repository, "optimistic-failure") == ""


def test_incoming_takeback_flag_is_visible_but_does_not_itself_rewind(tmp_path):
    session = LichessGameSession("offer", GameRepository(tmp_path / "offer.sqlite3"))
    session.color = chess.WHITE
    line = "e2e4 e7e5 g1f3 b8c6"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    # A delayed shorter state containing only an offer flag is not an acceptance.
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5",
                         "btakeback": True, "status": "started"})
    assert session.moves == line
    assert session.takeback_requested_by_opponent
    assert session.board.ply() == 4
    request_id = session.begin_takeback_action("accept")
    assert request_id
    assert session.finish_takeback_action(request_id, 0)
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5",
                         "wtime": 170000, "btime": 175000, "status": "started"})
    assert session.moves == "e2e4 e7e5"
    assert session.board.ply() == 2
    assert not session.takeback_requested_by_opponent


def test_takeback_flags_are_mapped_to_the_opponent_color(tmp_path):
    white = LichessGameSession("white-offer", GameRepository(tmp_path / "white.sqlite3"))
    white.color = chess.WHITE
    black = LichessGameSession("black-offer", GameRepository(tmp_path / "black.sqlite3"))
    black.color = chess.BLACK
    for session, flag in ((white, "btakeback"), (black, "wtakeback")):
        session.apply_event({"type": "gameFull", "initialFen": "startpos",
                             "state": {"moves": "e2e4", "status": "started"}})
        session.apply_event({"type": "gameState", "moves": "e2e4", flag: True,
                             "status": "started"})
        assert session.takeback_requested_by_opponent
        clear = {"type": "gameState", "moves": "e2e4", "status": "started"}
        session.apply_event(clear)
        assert not session.takeback_requested_by_opponent


def test_accepted_takeback_rebuilds_board_pgn_and_database_from_shorter_history(tmp_path):
    repo = GameRepository(tmp_path / "takeback.sqlite3")
    session = LichessGameSession("takeback123", repo)
    session.configure_players({"name": "WhitePlayer"}, {"name": "BlackPlayer"}, "white")
    session.apply_event({
        "type": "gameFull", "initialFen": "startpos", "rated": False,
        "createdAt": 1793404800000, "clock": {"initial": 180000, "increment": 2000},
        "state": {"moves": "e2e4 e7e5 g1f3 b8c6", "wtime": 170000,
                  "btime": 175000, "status": "started"},
    })
    before_pgn = repo.export_pgn("takeback123")
    assert "2. Nf3 Nc6" in before_pgn
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5 g1f3 b8c6",
                         "btakeback": True, "status": "started"})
    request_id = session.begin_takeback_action("accept")
    assert request_id
    assert session.begin_submission(chess.Move.from_uci("e2e4")) is False
    assert session.finish_takeback_action(request_id, 0)
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5",
                         "wtime": 178000, "btime": 179000, "status": "started"})
    row = repo.get_game("takeback123")
    pgn = chess.pgn.read_game(__import__("io").StringIO(row["pgn"]))
    assert row["moves"] == "e2e4 e7e5"
    assert session.board.ply() == 2
    assert [move.uci() for move in pgn.mainline_moves()] == ["e2e4", "e7e5"]
    assert pgn.headers["White"] == "WhitePlayer"
    assert pgn.headers["Black"] == "BlackPlayer"
    assert pgn.headers["Date"] == "2026.10.31"
    assert "Nf3" not in row["pgn"] and "Nc6" not in row["pgn"]


def test_accepted_takeback_clears_premove_tied_to_superseded_turn(tmp_path):
    session = LichessGameSession("takeback-premove", GameRepository(tmp_path / "takeback-premove.sqlite3"))
    session.color = chess.BLACK
    line = "e2e4 e7e5 g1f3 b8c6"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    move = chess.Move.from_uci("g8f6")
    assert session.queue_premove(move)
    session.apply_event({"type": "gameState", "moves": line, "wtakeback": True,
                         "status": "started"})
    request_id = session.begin_takeback_action("accept")
    assert request_id
    session.finish_takeback_action(request_id, 0)
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
    assert session.premove is None
    assert session.take_ready_premove() is None


def test_fresh_game_full_after_reconnect_can_authoritatively_shorten_line(tmp_path):
    session = LichessGameSession("reconnect-takeback", GameRepository(tmp_path / "reconnect.sqlite3"))
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5 g1f3", "status": "started"}})
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                        "state": {"moves": "e2e4 e7e5", "status": "started"}})
    assert session.moves == "e2e4 e7e5"
    assert session.board.ply() == 2


def test_own_takeback_offer_is_pending_until_the_server_rewinds(tmp_path):
    session = LichessGameSession("own-takeback", GameRepository(tmp_path / "own-takeback.sqlite3"))
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    request_id = session.begin_takeback_action("offer")
    assert request_id and session.takeback_requested_by_me and session.input_locked
    assert session.finish_takeback_action(request_id, 0)
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5",
                         "wtakeback": True, "status": "started"})
    assert session.takeback_requested_by_me
    assert session.moves == "e2e4 e7e5"
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started"})
    assert session.moves == "e2e4"
    assert not session.input_locked


def test_takeback_decline_and_server_error_do_not_rewind_board(tmp_path):
    session = LichessGameSession("decline-takeback", GameRepository(tmp_path / "decline-takeback.sqlite3"))
    session.color = chess.WHITE
    line = "e2e4 e7e5"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    session.apply_event({"type": "gameState", "moves": line, "btakeback": True,
                         "status": "started"})
    original_fen = session.board.fen()
    request_id = session.begin_takeback_action("decline")
    assert request_id
    assert session.finish_takeback_action(request_id, 0)
    assert session.board.fen() == original_fen
    session.apply_event({"type": "gameState", "moves": line, "status": "started"})
    assert not session.takeback_requested_by_opponent
    assert not session.input_locked

    second_id = session.begin_takeback_action("offer")
    assert second_id
    assert session.finish_takeback_action(second_id, 403)
    assert session.board.fen() == original_fen
    assert session.moves == line
    assert not session.takeback_requested_by_me


def test_pending_move_serializes_takeback_response_until_move_state_arrives(tmp_path):
    session = LichessGameSession("pending-takeback", GameRepository(tmp_path / "pending-takeback.sqlite3"))
    session.color = chess.WHITE
    line = "e2e4 e7e5"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": line, "status": "started"}})
    move = chess.Move.from_uci("g1f3")
    assert session.begin_submission(move)
    pending_id = session.pending_request_id
    session.apply_event({"type": "gameState", "moves": line, "btakeback": True,
                         "status": "started"})
    assert session.takeback_requested_by_opponent
    assert session.begin_takeback_action("accept") is None
    assert not session.queue_premove(chess.Move.from_uci("b1c3"))
    session.apply_event({"type": "gameState", "moves": line + " g1f3",
                         "btakeback": True, "status": "started"})
    assert session.pending_uci is None
    assert session.pending_request_id is None
    assert pending_id
    assert session.begin_takeback_action("accept")


def test_pending_move_confirmation_uses_expected_ply_not_old_repeated_uci(tmp_path):
    session = LichessGameSession("repeat", GameRepository(tmp_path / "repeat.sqlite3"))
    session.color = chess.WHITE
    history = "e2e4 g8f6 g1f3 f6g8 f3g1 g8f6"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": history, "status": "started"}})
    move = chess.Move.from_uci("g1f3")
    assert session.begin_submission(move)
    request_id = session.pending_request_id
    session.apply_event({"type": "gameState", "moves": history, "status": "started"})
    assert session.pending_uci == "g1f3"
    session.apply_event({"type": "gameState", "moves": history + " g1f3", "status": "started"})
    assert session.pending_uci is None
    assert session.pending_request_id is None
    assert request_id


def test_stale_and_divergent_stream_states_do_not_roll_back_position(tmp_path):
    session = LichessGameSession("stale", GameRepository(tmp_path / "stale.sqlite3"))
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4 e7e5", "status": "started"}})
    current = session.board.fen()
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started"})
    assert session.moves == "e2e4 e7e5"
    assert session.board.fen() == current
    session.apply_event({"type": "gameState", "moves": "e2e4 c7c5", "status": "started"})
    assert session.moves == "e2e4 e7e5"
    assert session.board.fen() == current


def test_late_failure_only_clears_matching_definitive_submission(tmp_path):
    session = LichessGameSession("late", GameRepository(tmp_path / "late.sqlite3"))
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    move = chess.Move.from_uci("e2e4")
    assert session.begin_submission(move)
    old_request = session.pending_request_id
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started"})
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
    assert session.begin_submission(chess.Move.from_uci("g1f3"))
    new_request = session.pending_request_id
    assert old_request != new_request
    assert not session.fail_submission(old_request, definitive=True)
    assert session.pending_uci == "g1f3"
    assert not session.fail_submission(new_request, definitive=False)
    assert session.pending_uci == "g1f3"  # ambiguous timeout/disconnect awaits stream sync
    assert session.fail_submission(new_request, definitive=True)
    assert session.pending_uci is None


def test_pgn_keeps_real_players_and_original_game_date_on_resave(tmp_path):
    repo = GameRepository(tmp_path / "pgn.sqlite3")
    session = LichessGameSession("pgn123", repo)
    session.color = chess.WHITE
    game_full = {
        "type": "gameFull", "initialFen": "startpos", "rated": False,
        "createdAt": 1793404800000,
        "clock": {"initial": 180000, "increment": 2000},
        "white": {"name": "OwnerName", "id": "ownername"},
        "black": {"name": "Lichess AI level 1", "id": "lichess-ai-level1"},
        "state": {"moves": "e2e4 e7e5", "status": "started"},
    }
    session.configure_players(game_full["white"], game_full["black"], "white")
    session.apply_event(game_full)
    session.finish("mate", "white")
    original = repo.export_pgn("pgn123")
    parsed = chess.pgn.read_game(__import__("io").StringIO(original))
    assert parsed.headers["White"] == "OwnerName"
    assert parsed.headers["Black"] == "Lichess AI level 1"
    assert parsed.headers["Date"] == "2026.10.31"
    assert parsed.headers["TimeControl"] == "180+2"
    assert parsed.headers["Result"] == "*"
    assert repo.get_game("pgn123")["final_sync_pending"] == 1
    session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "mate", "winner": "white"})
    original = repo.export_pgn("pgn123")
    parsed = chess.pgn.read_game(__import__("io").StringIO(original))
    assert parsed.headers["Result"] == "1-0"
    repo.save_snapshot("pgn123", "startpos", "e2e4 e7e5", "white", "Lichess AI level 1",
                       False, "180+2", "mate", "white")
    assert repo.export_pgn("pgn123") == original


def test_pgn_missing_start_time_uses_unknown_date_not_resave_date(tmp_path):
    repo = GameRepository(tmp_path / "unknown-date.sqlite3")
    repo.save_snapshot("unknown", "startpos", "", "black", "RealOpponent", False, "-", "started")
    pgn = repo.export_pgn("unknown")
    assert '[Date "????.??.??"]' in pgn
    assert '"Chess Coach"' not in pgn


def test_special_moves_are_accepted_from_authoritative_uci_stream(tmp_path):
    cases = [
        ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8q"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"),
        ("rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3", "e5d6"),
    ]
    for index, (fen, uci) in enumerate(cases):
        session = LichessGameSession(f"special-{index}", GameRepository(tmp_path / f"{index}.sqlite3"))
        session.apply_event({"type": "gameFull", "initialFen": fen,
                             "state": {"moves": "", "status": "started"}})
        move = chess.Move.from_uci(uci)
        assert move in session.board.legal_moves
        session.apply_event({"type": "gameState", "moves": uci, "status": "started"})
        assert session.board.peek() == move


def test_finish_before_terminal_state_persists_pending_then_syncs_final_ply(tmp_path):
    repo = GameRepository(tmp_path / "finish-before-state.sqlite3")
    session = LichessGameSession("finish-before-state", repo)
    session.color = chess.WHITE
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "f2f3 e7e5 g2g4", "status": "started"}})
    session.finish("mate", "black")
    pending = repo.get_game(session.game_id)
    assert session.status == "mate" and session.final_sync_pending
    assert pending["moves"] == "f2f3 e7e5 g2g4"
    assert pending["final_sync_pending"] == 1
    assert '[Result "*"]' in pending["pgn"]

    final_line = "f2f3 e7e5 g2g4 d8h4"
    final = {"type": "gameState", "moves": final_line, "status": "mate", "winner": "black"}
    session.apply_event(final)
    saved = repo.get_game(session.game_id)
    assert session.status == "mate" and not session.final_sync_pending
    assert session.moves == final_line and len(session.board.move_stack) == 4
    assert saved["moves"] == final_line and saved["final_sync_pending"] == 0
    assert '[Result "0-1"]' in saved["pgn"] and "Qh4#" in saved["pgn"]


def test_terminal_state_before_finish_is_idempotent_on_duplicate_events(tmp_path):
    repo = GameRepository(tmp_path / "state-before-finish.sqlite3")
    session = LichessGameSession("state-before-finish", repo)
    line = "f2f3 e7e5 g2g4 d8h4"
    terminal = {"type": "gameState", "moves": line, "status": "mate", "winner": "black"}
    session.apply_event(terminal)
    session.finish("mate", "black")
    session.apply_event(terminal)
    saved = repo.get_game(session.game_id)
    assert session.status == "mate" and not session.final_sync_pending
    assert session.moves == line and len(session.board.move_stack) == 4
    assert saved["final_sync_pending"] == 0 and saved["moves"] == line


def test_delayed_final_state_survives_stale_truncated_and_duplicate_events(tmp_path):
    repo = GameRepository(tmp_path / "delayed-final.sqlite3")
    session = LichessGameSession("delayed-final", repo)
    prefix = "f2f3 e7e5 g2g4"
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": prefix, "status": "started"}})
    session.finish("mate", "black")
    session.apply_event({"type": "gameState", "moves": prefix, "status": "started"})
    session.apply_event({"type": "gameState", "moves": "f2f3 e7e5", "status": "mate", "winner": "black"})
    assert session.final_sync_pending and session.status == "mate" and session.moves == prefix

    final_line = prefix + " d8h4"
    final = {"type": "gameState", "moves": final_line, "status": "mate", "winner": "black"}
    session.apply_event(final)
    session.apply_event(final)
    saved = repo.get_game(session.game_id)
    assert not session.final_sync_pending
    assert session.status == "mate" and session.moves == final_line
    assert saved["moves"] == final_line and len(session.board.move_stack) == 4


def test_async_repository_keeps_gui_event_fast_and_commits_ordered_snapshots(tmp_path, monkeypatch):
    repo = GameRepository(tmp_path / "async-writes.sqlite3", async_writes=True)
    entered = threading.Event()
    release = threading.Event()
    sync_save = repo._save_snapshot_sync

    def delayed_save(*args, **kwargs):
        entered.set()
        release.wait(2)
        return sync_save(*args, **kwargs)

    monkeypatch.setattr(repo, "_save_snapshot_sync", delayed_save)
    session = LichessGameSession("async-write", repo)
    try:
        started = time.perf_counter()
        session.apply_event({"type": "gameFull", "initialFen": "startpos",
                             "state": {"moves": "e2e4", "status": "started"}})
        elapsed = time.perf_counter() - started
        assert entered.wait(1)
        assert elapsed < 0.25

        session.apply_event({"type": "gameState", "moves": "e2e4 e7e5", "status": "started"})
        release.set()
        repo.wait_for_writes()
        saved = repo.get_game(session.game_id)
        assert saved["moves"] == "e2e4 e7e5"
        assert not repo.has_pending_write(session.game_id)
    finally:
        release.set()
        repo.shutdown()


def test_async_write_failure_is_visible_and_same_snapshot_can_be_retried(tmp_path, monkeypatch):
    repo = GameRepository(tmp_path / "async-retry.sqlite3", async_writes=True)
    sync_save = repo._save_snapshot_sync
    failures = [1]

    def fail_once(*args, **kwargs):
        if failures:
            failures.pop()
            raise OSError("database is locked")
        return sync_save(*args, **kwargs)

    monkeypatch.setattr(repo, "_save_snapshot_sync", fail_once)
    session = LichessGameSession("async-retry", repo)
    event = {"type": "gameFull", "initialFen": "startpos",
             "state": {"moves": "e2e4", "status": "started"}}
    try:
        session.apply_event(event)
        repo.wait_for_writes()
        deadline = time.monotonic() + 1
        while not repo.has_write_error(session.game_id) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert "database is locked" in (repo.write_error(session.game_id) or "")

        session.apply_event(event)
        repo.wait_for_writes()
        saved = repo.get_game(session.game_id)
        assert saved["moves"] == "e2e4"
        assert repo.write_error(session.game_id) is None
    finally:
        repo.shutdown()


def test_clock_only_duplicate_does_not_reserialize_or_resave_full_pgn(tmp_path, monkeypatch):
    repo = GameRepository(tmp_path / "clock-dedup.sqlite3")
    saves = []
    save = repo.save_snapshot
    monkeypatch.setattr(repo, "save_snapshot", lambda *args, **kwargs: (saves.append(args), save(*args, **kwargs)))
    session = LichessGameSession("clock-dedup", repo)
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "e2e4", "status": "started", "wtime": 10_000}})
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started", "wtime": 9_000})
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started", "wtime": 8_000})
    assert len(saves) == 1
