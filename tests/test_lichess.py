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
from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION
from chess_coach.lichess.session import LichessGameSession, classify_lichess_opponent
from chess_coach.lichess.service import LichessService

LICHESS_MIN_MS = 1_356_998_400_070


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


def test_get_active_games_rejects_malformed_entries_instead_of_failing_open():
    payload = {"nowPlaying": [
        {"gameId": "game1234", "color": "white", "opponent": {"username": "Friend"}},
        None,
        {"color": "black"},
    ]}
    fake = FakeTransport([response(200, payload)])
    client = LichessClient(fake, base_url="https://lichess.test")

    with pytest.raises(LichessError, match="Некорректная запись"):
        client.get_playing_games("token")
    assert fake.calls[0][0] == "GET"
    assert fake.calls[0][1].endswith("/api/account/playing?nb=50")
    assert fake.calls[0][2]["Authorization"] == "Bearer token"


def test_get_active_games_fails_closed_when_response_reaches_requested_limit():
    payload = {"nowPlaying": [
        {"gameId": f"game{i}", "opponent": {"username": f"BOT{i}", "title": "BOT"}}
        for i in range(49)
    ]}
    payload["nowPlaying"].append(
        {"gameId": "hidden-human", "opponent": {"username": "Friend"}})
    client = LichessClient(FakeTransport([response(200, payload)]),
                           base_url="https://lichess.test")
    with pytest.raises(LichessError, match="может быть неполным"):
        client.get_playing_games("token")


def test_get_active_games_preserves_official_builtin_ai_identity():
    ai_game = {"gameId": "ai123456", "source": "ai", "opponent": {
        "id": None, "username": "Stockfish level 1", "ai": 1}}
    client = LichessClient(FakeTransport([response(200, {"nowPlaying": [ai_game]})]),
                           base_url="https://lichess.test")
    games = client.get_playing_games("token")
    assert games == [ai_game]
    assert classify_lichess_opponent(games[0]) == "official_ai"


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


def test_analysis_safety_snapshot_is_one_explicit_fail_closed_request(monkeypatch):
    class TokenStore:
        def get(self):
            return "fake-token"

    class Client:
        def __init__(self): self.calls = []
        def get_playing_games(self, token):
            self.calls.append(token)
            return [{"gameId": "unopened-human-game", "opponent": {"username": "Friend"}}]

    client = Client()
    service = LichessService(client=client, token_store=TokenStore())
    results, errors, workers = [], [], []
    service.analysis_safety_games_received.connect(lambda request, games: results.append((request, games)))
    service.analysis_safety_error.connect(lambda request, message: errors.append((request, message)))
    monkeypatch.setattr(service, "_thread", lambda target, name: (workers.append(name), target()))
    service.check_active_games_for_analysis("req-1")
    assert client.calls == ["fake-token"]
    assert results[0][0] == "req-1" and results[0][1][0]["gameId"] == "unopened-human-game"
    assert not errors and workers == ["chess-coach-analysis-safety"]

    class BrokenClient:
        def get_playing_games(self, _token): raise RateLimitError(90)
    failed = LichessService(client=BrokenClient(), token_store=TokenStore())
    failures = []
    failed.analysis_safety_error.connect(lambda request, message: failures.append((request, message)))
    monkeypatch.setattr(failed, "_thread", lambda target, _name: target())
    failed.check_active_games_for_analysis("req-429")
    assert failures and failures[0][0] == "req-429" and "90" in failures[0][1]


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


def test_restore_is_single_flight_until_the_current_attempt_finishes(monkeypatch):
    class TokenStore:
        def get(self): return "token"
    service = LichessService(client=object(), token_store=TokenStore())
    workers = []
    monkeypatch.setattr(service, "_thread", lambda target, name: workers.append((target, name)))
    service.restore()
    service.restore()
    assert len(workers) == 1
    workers[0][0]()
    service.restore()
    assert len(workers) == 2


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


def test_lichess_opponent_classifier_requires_the_official_ai_signature():
    official_ai = {"source": "ai", "opponent": {
        "id": None, "username": "Stockfish level 1", "ai": 1}}
    bot_account = {"source": "friend", "opponent": {
        "id": "somebot", "username": "SomeBot", "title": "BOT"}}
    assert classify_lichess_opponent(official_ai) == "official_ai"
    assert classify_lichess_opponent(bot_account) == "bot_account"
    assert classify_lichess_opponent({"source": "future-source",
                                      "opponent": official_ai["opponent"]}) == "unknown"
    assert classify_lichess_opponent({"source": [],
                                      "opponent": official_ai["opponent"]}) == "unknown"
    assert classify_lichess_opponent({"source": "friend", "opponent": {
        "id": None, "username": "Stockfish level 1", "ai": 1}}) == "unknown"
    assert classify_lichess_opponent({"source": "ai", "opponent": {
        "id": None, "username": "Stockfish", "ai": 9}}) == "unknown"
    assert classify_lichess_opponent({"source": "ai", "opponent": {
        "username": "Stockfish", "ai": 1}}) == "unknown"
    assert classify_lichess_opponent({"source": "ai", "opponent": {
        "id": "somebot", "username": "Stockfish", "ai": 1}}) == "unknown"
    assert classify_lichess_opponent({"source": "ai", "opponent": {
        "id": None, "username": "Stockfish", "ai": 1, "title": "BOT"}}) == "unknown"
    assert classify_lichess_opponent({"source": "friend", "opponent": {
        "id": "somebot", "username": "SomeBot", "title": []}}) == "unknown"


def test_session_preserves_authoritative_ai_identity_across_game_state(tmp_path):
    session = LichessGameSession("ai-session", GameRepository(tmp_path / "ai-session.sqlite3"))
    session.set_server_game_identity({"source": "ai", "opponent": {
        "id": None, "username": "Stockfish level 1", "ai": 1}})
    assert session.is_ai_opponent
    session.apply_event({"type": "gameFull", "initialFen": "startpos",
                         "state": {"moves": "", "status": "started"}})
    session.apply_event({"type": "gameState", "moves": "e2e4", "status": "started",
                         "source": "friend", "opponent": {
                             "id": "somebot", "username": "SomeBot", "title": "BOT"}})
    assert session.is_ai_opponent
    assert session.game_source == "ai"


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
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert migrated.execute("SELECT COUNT(*) FROM game_positions").fetchone()[0] == 3
        assert migrated.execute("SELECT san FROM game_moves ORDER BY ply").fetchall()[0][0] == "e4"
    backup = sqlite3.connect(str(path) + f".pre-m3-v{SCHEMA_VERSION}.bak")
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
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


def test_targeted_game_export_requests_only_the_validated_game_id():
    pgn = '[Event "Old correspondence"]\n[White "Me"]\n[Black "Friend"]\n\n1. e4 *\n'
    fake = FakeTransport([HttpResponse(200, {}, pgn.encode("utf-8"))])
    client = LichessClient(fake, base_url="https://lichess.test")
    assert client.export_game_pgn("token", "game-123") == pgn
    assert fake.calls[0][0:2] == ("GET", "https://lichess.test/game/export/game-123")
    assert fake.calls[0][2]["Authorization"] == "Bearer token"
    with pytest.raises(ValueError, match="Invalid Lichess game ID"):
        client.export_game_pgn("token", "../private")
    assert len(fake.calls) == 1


def test_archive_endpoint_streams_json_page_with_cursor_parameters():
    stop = threading.Event()
    class StreamTransport(FakeTransport):
        def stream(self, method, url, headers, body, timeout, stop_event):
            self.calls.append((method, url, headers, body))
            yield from self.lines
    fake = StreamTransport(lines=[b'{"id":"game1","pgn":"1. e4 *"}\n'])
    client = LichessClient(fake, base_url="https://lichess.test")
    records = list(client.stream_user_games("token", "My User", stop, since=123,
                                             max_games=50))
    assert records == [{"id": "game1", "pgn": "1. e4 *"}]
    method, url, headers, _body = fake.calls[0]
    assert method == "GET"
    assert "/api/games/user/My%20User?" in url
    assert "since=123" in url and "max=50" in url and "pgnInJson=true" in url
    assert "evals=true" in url and "accuracy=true" in url
    assert "division=true" in url and "finished=true" in url and "ongoing=false" in url
    assert headers["Authorization"] == "Bearer token"


def test_archive_sync_resumes_backfill_deduplicates_and_advances_checkpoint(tmp_path):
    from chess_coach.lichess.archive import ArchiveSync

    pgns = {}
    for index, moves in enumerate(("1. e4 *", "1. d4 d5 *", "1. c4 e5 *"), 1):
        pgns[f"g{index}"] = {
            "id": f"g{index}", "pgn": f'[Event "Archive"]\n[Site "https://lichess.org/g{index}"]\n'
            f'[Date "2025.01.0{index}"]\n[White "Me"]\n[Black "Friend"]\n'
            f'[Result "*"]\n\n{moves}\n', "lastMoveAt": LICHESS_MIN_MS + index * 1000,
            "rated": False, "variant": "standard",
        }

    class Client:
        def __init__(self):
            self.requests = []
        def stream_user_games(self, token, username, stop_event, *, since=None,
                              until_ms=None, max_games=1000):
            self.requests.append((since, until_ms, max_games))
            if until_ms is not None:
                candidates = [r for r in pgns.values() if r["lastMoveAt"] <= until_ms]
                yield from sorted(candidates, key=lambda r: r["lastMoveAt"], reverse=True)[:max_games]
            else:
                return

    repo = GameRepository(tmp_path / "archive.sqlite3")
    client = Client()
    stop = threading.Event()
    sync = ArchiveSync(client, repo)
    assert sync.run("Me", "fake-token", stop, full=True, page_size=2) == 3
    assert len(repo.list_games(completed_only=False)) == 3
    account = repo.ensure_archive_account("Me")
    state = repo.archive_sync_state(account)
    assert state["backfill_complete"] == 1
    assert state["imported_count"] == 3
    assert state["backfill_before"] == LICHESS_MIN_MS + 1001
    # Incremental query is bounded by the persisted time cursor, not a second full scan.
    assert client.requests[-1][0] is not None
    assert sync.run("Me", "fake-token", stop, full=False, page_size=2) == 0
    assert len(repo.list_games(completed_only=False)) == 3


def test_completed_analyzer_is_gated_caches_by_engine_and_closes_engine(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "analysis.sqlite3")
    repo.save_snapshot("done", "startpos", "e2e4 e7e5", "white", "Friend",
                       False, "-", "mate", "black")
    engine_path = tmp_path / "stockfish.exe"
    engine_path.write_text("fake UCI engine", encoding="utf-8")

    class FakeEngine:
        id = {"name": "FakeFish", "version": "1"}
        def __init__(self):
            self.analyzed = 0
            self.closed = False
            self.protocol_has_stop = False
        def configure(self, options):
            assert options == {"MultiPV": 1, "Threads": 1, "Hash": 128}
        def analysis(self, board, limit):
            self.analyzed += 1
            score = chess.engine.PovScore(chess.engine.Cp(20 if board.ply() else 0), chess.WHITE)
            info = {"score": score, "depth": 12, "nodes": 100,
                    "pv": list(board.legal_moves)[:1]}
            class Analysis:
                def __init__(self):
                    self._items = iter([info])
                    self.stop_calls = 0
                @property
                def info(self): return info
                def __enter__(self): return self
                def __exit__(self, *_args): self.stop()
                def __iter__(self): return self
                def __next__(self): return next(self._items)
                def stop(self): self.stop_calls += 1
            return Analysis()
        def quit(self):
            self.closed = True

    engines = []
    def factory(_path):
        engine = FakeEngine()
        engines.append(engine)
        return engine
    analyzer = CompletedGameAnalyzer(repo, factory)
    with pytest.raises(ValueError, match="онлайн-партии"):
        analyzer.analyze("done", engine_path, active_human_game_ids={"done"})
    first = analyzer.analyze("done", engine_path)
    assert len(first["assessments"]) == 3
    assert len(first["classifications"]) == 2
    assert first["classifications"][0]["phase"] == "unknown"
    assert engines[0].closed and engines[0].analyzed == 3
    second = analyzer.analyze("done", engine_path)
    assert second["cached"] is True
    assert engines[1].closed and engines[1].analyzed == 0
    assert len(repo.list_analyses("done")) == 1


def test_analysis_options_normalize_bounds_and_build_uci_limits():
    from chess_coach.lichess.analysis import AnalysisOptions

    nodes = AnalysisOptions(limit_kind="nodes", limit_value=42_000,
                            threads=99, hash_mb=0).normalized()
    assert nodes.threads == 8 and nodes.hash_mb == 16
    assert nodes.engine_limit().nodes == 42_000
    depth = AnalysisOptions(limit_kind="depth", limit_value=19).normalized()
    assert depth.engine_limit().depth == 19
    timed = AnalysisOptions(limit_kind="time", limit_value=2_500).normalized()
    assert timed.engine_limit().time == 2.5


def test_completed_analyzer_applies_settings_and_separates_cache_keys(tmp_path):
    from chess_coach.lichess.analysis import AnalysisOptions, CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "engine-options.sqlite3")
    repo.save_snapshot("options", "startpos", "", "white", "Friend",
                       False, "-", "finished", "white")
    engine_path = tmp_path / "fake-engine.exe"
    engine_path.write_text("fake", encoding="utf-8")
    configurations = []

    class FakeEngine:
        id = {"name": "OptionsFish", "author": "test"}
        def configure(self, options): configurations.append(options)
        def analysis(self, board, limit):
            assert limit.depth == 14
            info = {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                    "depth": 14, "nodes": 10, "pv": []}
            class Result:
                def __init__(self): self.info = info
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def __iter__(self): return iter([info])
            return Result()
        def quit(self): pass

    result = CompletedGameAnalyzer(repo, lambda _path: FakeEngine()).analyze(
        "options", engine_path,
        AnalysisOptions(limit_kind="depth", limit_value=14, threads=3, hash_mb=256))
    assert configurations == [{"MultiPV": 1, "Threads": 3, "Hash": 256}]
    import json
    stored = repo.list_analyses("options")[0]
    stored_parameters = json.loads(stored["parameters_json"])
    assert stored_parameters["limit_kind"] == "depth"
    assert stored_parameters["limit_value"] == 14
    assert stored_parameters["threads"] == 3
    assert repo.find_analysis_cache(
        "options", 1, stored["line_hash"], "stockfish", stored["engine_version"],
        {**stored_parameters, "hash_mb": 128}) is None
    assert result["cached"] is False


def test_completed_analyzer_rejects_live_game_before_engine_start(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "live-engine.sqlite3")
    repo.save_snapshot("live", "startpos", "e2e4", "white", "Friend",
                       False, "3+2", "playing")
    with pytest.raises(ValueError, match="только после завершения"):
        CompletedGameAnalyzer(repo, lambda _path: pytest.fail("engine must not launch")).analyze(
            "live", tmp_path / "stockfish.exe")


def test_completed_game_is_blocked_while_another_human_game_is_live(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer
    repo = GameRepository(tmp_path / "fair-play-global.sqlite3")
    repo.save_snapshot("finished-a", "startpos", "e2e4", "white", "Friend",
                       False, "3+2", "finished", "white")
    repo.save_snapshot("live-b", "startpos", "d2d4", "white", "Human",
                       False, "3+2", "playing")
    engine = tmp_path / "stockfish.exe"
    engine.write_text("fake", encoding="utf-8")
    analyzer = CompletedGameAnalyzer(repo, lambda _path: pytest.fail("engine must not launch"))
    with pytest.raises(ValueError, match="онлайн-партии"):
        analyzer.analyze("finished-a", engine, active_human_game_ids={"live-b"})


def test_engine_cancellation_uses_simple_analysis_result_stop_and_skips_cache(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "analysis-cancel.sqlite3")
    repo.save_snapshot("cancel", "startpos", "", "white", "Friend",
                       False, "-", "finished", "white")
    engine_path = tmp_path / "fake-engine.exe"
    engine_path.write_text("fake UCI engine", encoding="utf-8")
    engines = []
    for _attempt in range(2):
        stop_event = threading.Event()
        analysis_started = threading.Event()
        result_stopped = threading.Event()

        class FakeAnalysisResult:
            def __init__(self): self.stop_calls = 0
            @property
            def info(self):
                return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                        "depth": 1, "nodes": 1, "pv": []}
            def __enter__(self): return self
            def __exit__(self, *_args): self.stop()
            def __iter__(self): return self
            def __next__(self):
                analysis_started.set()
                time.sleep(0.12)  # Cancellation must remain watched after the first poll.
                stop_event.set()
                if not result_stopped.wait(2):
                    raise RuntimeError("analysis wasn't stopped")
                raise StopIteration
            def stop(self):
                self.stop_calls += 1
                result_stopped.set()

        result = FakeAnalysisResult()

        class FakeEngine:
            id = {"name": "FakeFish", "author": "Test"}
            def __init__(self): self.closed = False
            def configure(self, _options): pass
            def analysis(self, _board, _limit): return result
            def quit(self): self.closed = True

        engine = FakeEngine()
        engines.append((engine, result, analysis_started))
        with pytest.raises(InterruptedError, match="Анализ отменён"):
            CompletedGameAnalyzer(repo, lambda _path: engine).analyze(
                "cancel", engine_path, stop_event=stop_event)

    assert len(engines) == 2
    for engine, result, analysis_started in engines:
        assert analysis_started.is_set()
        assert result.stop_calls >= 1
        assert not hasattr(engine, "stop")  # SimpleEngine.stop() does not exist.
        assert engine.closed
    assert repo.list_analyses("cancel") == []
    repo.shutdown()


def test_engine_startup_cancellation_quits_engine_without_analysis(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "analysis-startup-cancel.sqlite3")
    repo.save_snapshot("startup", "startpos", "", "white", "Friend",
                       False, "-", "finished", "white")
    engine_path = tmp_path / "fake-engine.exe"
    engine_path.write_text("fake UCI engine", encoding="utf-8")
    stop_event = threading.Event()

    class FakeEngine:
        id = {"name": "FakeFish"}
        def __init__(self): self.closed = False
        def configure(self, _options): pytest.fail("cancelled startup must skip configure")
        def analysis(self, *_args, **_kwargs): pytest.fail("cancelled startup must skip analysis")
        def quit(self): self.closed = True

    engine = FakeEngine()
    def factory(_path):
        stop_event.set()
        return engine

    with pytest.raises(InterruptedError, match="Анализ отменён"):
        CompletedGameAnalyzer(repo, factory).analyze(
            "startup", engine_path, stop_event=stop_event)
    assert engine.closed
    assert repo.list_analyses("startup") == []
    repo.shutdown()


def test_engine_stream_error_stops_result_quits_engine_and_saves_nothing(tmp_path):
    from chess_coach.lichess.analysis import CompletedGameAnalyzer

    repo = GameRepository(tmp_path / "analysis-stream-error.sqlite3")
    repo.save_snapshot("stream-error", "startpos", "", "white", "Friend",
                       False, "-", "finished", "white")
    engine_path = tmp_path / "fake-engine.exe"
    engine_path.write_text("fake UCI engine", encoding="utf-8")
    stream_stopped = threading.Event()

    class FailingAnalysis:
        @property
        def info(self): return {}
        def __enter__(self): return self
        def __exit__(self, *_args): self.stop()
        def __iter__(self): return self
        def __next__(self): raise RuntimeError("fake info stream failure")
        def stop(self): stream_stopped.set()

    class FakeEngine:
        id = {"name": "FakeFish"}
        def __init__(self): self.closed = False
        def configure(self, _options): pass
        def analysis(self, _board, _limit): return FailingAnalysis()
        def quit(self): self.closed = True

    engine = FakeEngine()
    with pytest.raises(RuntimeError, match="fake info stream failure"):
        CompletedGameAnalyzer(repo, lambda _path: engine).analyze(
            "stream-error", engine_path)
    assert stream_stopped.is_set()
    assert engine.closed
    assert repo.list_analyses("stream-error") == []
    repo.shutdown()


def test_archive_import_keeps_lichess_pgn_evaluations_as_separate_source(tmp_path):
    repo = GameRepository(tmp_path / "lichess-eval.sqlite3")
    repo.import_archive_record("Me", {
        "id": "eval-game",
        "pgn": '[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "1/2-1/2"]\n\n'
               '1. e4 { [%eval 0.25] } e5 1/2-1/2\n',
        "lastMoveAt": 1000, "accuracy": {"white": 91.2, "black": 88.0},
        "division": {"middle": {"begin": 1}, "end": {"begin": 2}},
    }, checkpoint_ms=1000, backfill=True)
    game = repo.get_game("eval-game")
    assert game["raw_pgn"].find("[%eval 0.25]") >= 0
    assert game["lichess_accuracy_white"] == 91.2
    runs = repo.list_analyses("eval-game")
    assert len(runs) == 1 and runs[0]["source"] == "lichess-pgn"
    cached = repo.find_analysis_cache("eval-game", 1, runs[0]["line_hash"],
                                      "lichess-pgn", None, {"format": "pgn-eval-v1"})
    assert cached["assessments"][0]["white_cp"] == 25
    assert repo.phase_for_ply("eval-game", 1, 0) == "opening"
    assert repo.phase_for_ply("eval-game", 1, 1) == "middlegame"
    assert repo.phase_for_ply("eval-game", 1, 2) == "endgame"
    from chess_coach.lichess.statistics import StatisticsService
    summary = StatisticsService(repo).summary(analysis_source="lichess-pgn")
    assert summary["games"] == 1
    assert summary["evaluated_moves"] == 0
    assert summary["move_coverage_percent"] == 0.0


def test_statistics_use_move_denominators_partial_coverage_and_keep_accuracy_separate(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    repo = GameRepository(tmp_path / "stats.sqlite3")
    games = [
        ("w", "white", "white", "finished"),
        ("d", None, "black", "draw"),
        ("l", "white", "black", "finished"),
    ]
    for game_id, winner, color, status in games:
        repo.save_snapshot(game_id, "startpos", "e2e4 e7e5", color, "Opponent",
                               False, "180+2", status, winner,
                               started_at="2026-10-01T12:00:00+00:00" if game_id == "w" else None)
    with repo._connection() as db:
        db.execute("UPDATE games SET lichess_accuracy_white=90,lichess_accuracy_black=80 "
                   "WHERE game_id='w'")
        db.execute("UPDATE games SET white_rating=1500,opening_eco='C20',opening_name='King Pawn' "
                   "WHERE game_id='w'")
    line = repo.current_line("w")
    repo.save_analysis("w", line_revision=1, line_hash=line["line_hash"],
        source="stockfish", engine_name="Fake", engine_version="1", parameters={"nodes": 1},
        assessments=[{"ply": i, "white_cp": i * 10} for i in range(3)],
        classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 100, "label": "mistake",
             "phase": "opening", "algorithm_version": "test"},
            {"ply": 2, "mover": "black", "eval_loss_cp": 10, "label": "good",
             "phase": "opening", "algorithm_version": "test"},
        ])
    line = repo.current_line("d")
    repo.save_analysis("d", line_revision=1, line_hash=line["line_hash"],
        source="stockfish", engine_name="Fake", engine_version="1", parameters={"nodes": 1},
        assessments=[{"ply": 1, "white_cp": 10}],
        classifications=[{"ply": 1, "mover": "black", "eval_loss_cp": 250,
                          "label": "blunder", "phase": "unknown",
                          "algorithm_version": "test"}])
    summary = StatisticsService(repo).summary(analysis_source="stockfish")
    assert (summary["games"], summary["wins"], summary["draws"], summary["losses"]) == (2, 1, 1, 0)
    assert summary["confirmed_moves"] == 4
    assert summary["evaluated_moves"] == 1
    assert summary["move_coverage_percent"] == 50.0
    assert summary["own_assessed_moves"] == 1
    assert summary["own_error_rate_per_100"] == 0.0
    assert summary["avg_cpl_cp"] == 0.0
    assert summary["swings_200cp"] == 0
    assert summary["lichess_accuracy"] == {"white": 90.0, "black": 80.0}
    assert summary["rating_trend"] == [{"date": repo.get_game("w")["started_at"],
                                         "game_time_precision": "timestamp",
                                         "rating": 1500, "game_id": "w"}]
    filtered = StatisticsService(repo).summary(rating_min=1400, rating_max=1600, opening="C20")
    assert filtered["games"] == 1


def test_statistics_result_analysis_source_and_game_page_filters_match(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    repo = GameRepository(tmp_path / "statistics-filters.sqlite3")
    rows = (("win", "white", "finished", "white"),
            ("loss", "white", "finished", "black"),
            ("draw", "black", "draw", None),
            ("unknown", "white", "unknownFinish", None))
    for game_id, color, status, winner in rows:
        repo.save_snapshot(game_id, "startpos", "e2e4", color, "Opponent",
                           False, "180+2", status, winner)
    line = repo.current_line("win")
    repo.save_analysis("win", line_revision=1, line_hash=line["line_hash"],
        source="stockfish", engine_name="Fake", engine_version="fixture", parameters={"nodes": 1},
        assessments=[{"ply": 1, "white_cp": 25}], classifications=[
            {"ply": 1, "mover": "white", "eval_loss_cp": 0, "label": "good",
             "phase": "opening", "algorithm_version": "test"}])
    service = StatisticsService(repo)
    assert service.summary(result="win")["games"] == 1
    assert service.summary(result="loss")["games"] == 1
    assert service.summary(result="draw")["games"] == 1
    unknown = service.summary(result="unknown")
    assert unknown["games"] == unknown["unknown_or_aborted"] == 1
    assert service.summary(analyzed="yes", analysis_source="stockfish")["games"] == 1
    assert service.summary(analyzed="no", analysis_source="stockfish")["games"] == 3
    page = service.game_page(result="draw", clock="180+2", page_size=1)
    assert page["total"] == 1 and [game["game_id"] for game in page["items"]] == ["draw"]
    assert service.game_page(analyzed="yes", analysis_source="stockfish")["total"] == 1


def test_statistics_page_is_bounded_for_large_archive(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    repo = GameRepository(tmp_path / "statistics-large.sqlite3")
    for index in range(1_005):
        repo.save_snapshot(f"large-{index:04}", "startpos", "e2e4", "white", "Fixture",
                           False, "-", "finished", "white")
    service = StatisticsService(repo)
    page = service.game_page(page=10, page_size=100)
    assert page["total"] == 1_005
    assert len(page["items"]) == 5
    assert service.summary(result="win")["games"] == 1_005


def test_statistics_summary_scales_to_35000_file_backed_games_without_id_bind_lists(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    path = tmp_path / "statistics-35000.sqlite3"
    repo = GameRepository(path)
    count = 35_000
    now = "2026-10-31T00:00:00+00:00"
    games = []
    revisions = []
    moves = []
    for index in range(count):
        game_id = f"stress-{index:05}"
        winner = "white" if index % 2 == 0 else "black"
        pgn = f'[Result "{ "1-0" if winner == "white" else "0-1" }"]'
        games.append((game_id, "startpos", "", "white", "Fixture", 0, "-", "mate",
                      winner, now, pgn, "White", "Black", now, 0, "local", game_id,
                      "standard", 1, None, None, None, None, 1500, 1500, None, None, None, None))
        revisions.append((game_id, 1, "startpos", "", pgn, f"hash-{index}", now, 1))
        if index < 1375:
            moves.append((game_id, 1, 1, "e2e4", "e4", "white", None, None))
    with repo._connection() as db:
        db.executemany("INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                       "winner,updated_at,pgn,white_player,black_player,started_at,final_sync_pending,"
                       "source,source_game_id,variant,line_revision,import_error,raw_checksum,raw_pgn,"
                       "last_move_at,white_rating,black_rating,opening_eco,opening_name,"
                       "lichess_accuracy_white,lichess_accuracy_black) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       games)
        db.executemany("INSERT INTO game_revisions VALUES (?,?,?,?,?,?,?,?)", revisions)
        db.executemany("INSERT INTO game_moves VALUES (?,?,?,?,?,?,?,?)", moves)
    service = StatisticsService(repo)
    summary = service.summary()
    page = service.game_page(page=0, page_size=100)
    assert summary["games"] == count
    assert summary["wins"] == count // 2
    assert summary["losses"] == count // 2
    assert summary["confirmed_moves"] == 1375
    assert len(summary["rating_trend"]) == 500
    assert page["total"] == count and len(page["items"]) == 100
    repo.save_snapshot("stress-after-cache", "startpos", "e2e4", "white", "Fixture",
                       False, "-", "mate", "white")
    assert service.summary()["games"] == count + 1


def test_analysis_source_filter_constrains_default_summary_and_page_selection(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    repo = GameRepository(tmp_path / "statistics-analysis-source.sqlite3")
    for game_id in ("stockfish-only", "lichess-only", "both", "neither"):
        repo.save_snapshot(game_id, "startpos", "e2e4", "white", "Opponent",
                           False, "180+2", "finished", "white")
    for game_id, sources in (("stockfish-only", ("stockfish",)),
                             ("lichess-only", ("lichess-pgn",)),
                             ("both", ("stockfish", "lichess-pgn"))):
        line = repo.current_line(game_id)
        for source in sources:
            repo.save_analysis(game_id, line_revision=1, line_hash=line["line_hash"],
                source=source, engine_name="Fixture", engine_version="1", parameters={},
                assessments=[{"ply": 1, "white_cp": 10}], classifications=[])
    service = StatisticsService(repo)
    for source in ("stockfish", "lichess-pgn"):
        default = service.summary(analysis_source=source)
        assert default["games"] == default["analysis_games"] == 2
        assert default["wins"] == 2 and default["losses"] == 0 and default["draws"] == 0
        assert service.game_page(analysis_source=source)["total"] == 2
        assert service.summary(analysis_source=source, analyzed="yes")["games"] == 2
        assert service.game_page(analysis_source=source, analyzed="yes")["total"] == 2
        assert service.summary(analysis_source=source, analyzed="no")["games"] == 2
        assert service.game_page(analysis_source=source, analyzed="no")["total"] == 2
    assert service.summary()["games"] == 4
    assert service.game_page()["total"] == 4


def test_statistics_query_cache_invalidates_after_game_and_analysis_writes(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService

    repo = GameRepository(tmp_path / "statistics-cache-invalidation.sqlite3")
    service = StatisticsService(repo)
    assert service.summary(result="win")["games"] == 0
    repo.save_snapshot("cache-game", "startpos", "e2e4", "white", "Fixture",
                       False, "-", "finished", "white")
    assert service.summary(result="win")["games"] == 1
    assert service.summary(analyzed="yes", analysis_source="stockfish")["games"] == 0
    line = repo.current_line("cache-game")
    repo.save_analysis("cache-game", line_revision=line["line_revision"],
        line_hash=line["line_hash"], source="stockfish", engine_name="Fake",
        engine_version="fixture", parameters={}, assessments=[{"ply": 1, "white_cp": 1}],
        classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 0,
                          "label": "good", "phase": "opening", "algorithm_version": "test"}])
    assert service.summary(analyzed="yes", analysis_source="stockfish")["games"] == 1


def test_statistics_use_account_color_and_do_not_count_unknown_finish_as_draw(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService
    repo = GameRepository(tmp_path / "stats-accounts.sqlite3")
    pgn = '[Event "Archive"]\n[White "Alice"]\n[Black "Bob"]\n[Result "0-1"]\n\n1. e4 0-1\n'
    record = {"id": "shared", "pgn": pgn, "players": {
        "white": {"rating": 1500}, "black": {"rating": 1800}}, "rated": True}
    repo.import_archive_record("Alice", record, checkpoint_ms=None, backfill=False)
    repo.import_archive_record("Bob", record, checkpoint_ms=None, backfill=False)
    repo.save_snapshot("unknown", "startpos", "e2e4", "white", "Unknown",
                       False, "-", "unknownFinish", None)
    bob = repo.ensure_archive_account("Bob")
    with repo._connection() as db:
        rows = db.execute("SELECT account_id,color,player_rating FROM account_games WHERE game_id='shared'").fetchall()
        assert {r["color"] for r in rows} == {"white", "black"}
        assert {r["player_rating"] for r in rows} == {1500, 1800}
    assert repo.get_game("shared")["color"] == "white"
    summary = StatisticsService(repo).summary(account_id=bob)
    assert (summary["wins"], summary["losses"], summary["draws"]) == (1, 0, 0)
    assert summary["account_scope"] == "selected account"
    all_games = StatisticsService(repo).summary()
    assert all_games["unknown_or_aborted"] == 1
    assert all_games["account_scope"].startswith("mixed")


def test_statistics_choose_one_run_when_finished_at_ties(tmp_path):
    from chess_coach.lichess.statistics import StatisticsService
    repo = GameRepository(tmp_path / "stats-run-tie.sqlite3")
    repo.save_snapshot("g", "startpos", "e2e4", "white", "Friend", False, "-", "finished", "white")
    line = repo.current_line("g")
    run_ids = []
    for loss in (50, 250):
        run_ids.append(repo.save_analysis("g", line_revision=1, line_hash=line["line_hash"],
            source="stockfish", engine_name="Fake", engine_version="1", parameters={"nodes": loss},
            assessments=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": -loss}], classifications=[
                {"ply": 1, "mover": "white", "eval_loss_cp": loss,
                 "label": "mistake" if loss == 50 else "blunder", "phase": "unknown",
                 "algorithm_version": "test"}]))
    with repo._connection() as db:
        db.execute("UPDATE analysis_runs SET finished_at='same-time' WHERE run_id IN (?,?)", run_ids)
    summary = StatisticsService(repo).summary(analysis_source="stockfish")
    latest = max(run_ids)
    expected = 250 if latest == run_ids[1] else 50
    assert summary["own_assessed_moves"] == 1
    assert summary["avg_cpl_cp"] == expected


def test_puzzle_activity_oauth_scope_and_activity_endpoint_are_explicit():
    auth = create_authorization_request("http://127.0.0.1/callback", scopes="puzzle:read")
    assert parse_qs(urlparse(auth.url).query)["scope"] == ["puzzle:read"]
    class StreamTransport(FakeTransport):
        def stream(self, method, url, headers, body, timeout, stop_event):
            self.calls.append((method, url, headers, body))
            yield b'{"date":100,"win":true,"puzzle":{"id":"p1"}}\n'
    fake = StreamTransport()
    client = LichessClient(fake, base_url="https://lichess.test")
    assert list(client.stream_puzzle_activity("puzzle-token", threading.Event(),
                                               before=200, max_items=10))[0]["puzzle"]["id"] == "p1"
    assert fake.calls[0][1].endswith("/api/puzzle/activity?max=10&before=200")
    assert fake.calls[0][2]["Authorization"] == "Bearer puzzle-token"
    dashboard = FakeTransport([response(200, {"global": {"nb": 12}})])
    result = LichessClient(dashboard, base_url="https://lichess.test").get_puzzle_dashboard("token", 30)
    assert result["global"]["nb"] == 12
    assert dashboard.calls[0][1].endswith("/api/puzzle/dashboard/30")


def test_puzzle_oauth_credential_slot_does_not_replace_board_token():
    from chess_coach.lichess.auth import WindowsCredentialStore
    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass
        values = {("ChessCoach.Lichess", "oauth-access-token"): "board-token"}
        def get_password(self, service, user):
            return self.values.get((service, user))
        def set_password(self, service, user, token):
            self.values[(service, user)] = token
        def delete_password(self, service, user):
            if (service, user) not in self.values:
                raise self.errors.PasswordDeleteError()
            del self.values[(service, user)]
    keyring = FakeKeyring()
    store = WindowsCredentialStore(keyring, service="ChessCoach.Lichess.Puzzles",
                                   user="puzzle-activity-token")
    store.set("puzzle-token")
    assert store.get() == "puzzle-token"
    assert keyring.get_password("ChessCoach.Lichess", "oauth-access-token") == "board-token"


def test_puzzle_import_retains_repeated_attempts_and_is_idempotent(tmp_path):
    repo = GameRepository(tmp_path / "puzzle-history.sqlite3")
    first = {"date": 1000, "win": True, "puzzle": {"id": "p1", "fen": chess.STARTING_FEN,
             "solution": ["e2e4"], "themes": ["fork"], "rating": 1450, "plays": 300}}
    second = {"date": 1000, "win": False, "puzzle": {**first["puzzle"], "plays": 301}}
    uid1 = repo.import_puzzle_activity("Me", first, backfill=True)
    repo.import_puzzle_activity("Me", first, backfill=True)
    uid2 = repo.import_puzzle_activity("Me", second, backfill=True)
    account_id = repo.ensure_archive_account("Me")
    history = repo.puzzle_history(account_id)
    assert uid1 != uid2
    assert len(history) == 2
    stats = repo.puzzle_statistics(account_id)
    assert stats["attempts"] == 2 and stats["wins"] == 1 and stats["success_percent"] == 50.0
    assert stats["themes"]["fork"]["attempts"] == 2


def test_puzzle_sync_backfills_repeated_puzzle_ids_and_empty_archive(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    items = [
        {"date": LICHESS_MIN_MS + 500, "win": True, "puzzle": {"id": "same", "fen": chess.STARTING_FEN,
         "solution": ["e2e4"], "themes": ["fork"], "rating": 1500, "plays": 10}},
        {"date": LICHESS_MIN_MS + 500, "win": False, "puzzle": {"id": "same", "fen": chess.STARTING_FEN,
         "solution": ["e2e4"], "themes": ["fork"], "rating": 1500, "plays": 10}},
        {"date": LICHESS_MIN_MS + 400, "win": True, "puzzle": {"id": "other", "fen": chess.STARTING_FEN,
         "solution": ["d2d4"], "themes": ["pin"], "rating": 1600, "plays": 20}},
    ]
    class FakePuzzleClient:
        def stream_puzzle_activity(self, token, stop, *, since=None, before=None, max_items=1000):
            if before is not None:
                yield from sorted((item for item in items if item["date"] < before),
                                  key=lambda item: item["date"], reverse=True)[:max_items]
    repo = GameRepository(tmp_path / "puzzle-sync.sqlite3")
    sync = PuzzleActivitySync(FakePuzzleClient(), repo)
    assert sync.run("Me", "token", threading.Event(), page_size=2) == 3
    account_id = repo.ensure_archive_account("Me")
    assert len(repo.puzzle_history(account_id)) == 3
    assert repo.puzzle_sync_state(account_id)["backfill_complete"] == 1
    class EmptyClient:
        def stream_puzzle_activity(self, *args, **kwargs):
            return iter(())
    empty_repo = GameRepository(tmp_path / "puzzle-empty.sqlite3")
    assert PuzzleActivitySync(EmptyClient(), empty_repo).run(
        "Empty", "token", threading.Event()) == 0
    empty_account = empty_repo.ensure_archive_account("Empty")
    assert empty_repo.puzzle_statistics(empty_account)["attempts"] == 0


def test_puzzle_pilot_is_bounded_idempotent_and_does_not_move_full_sync_cursor(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    records = [{"date": LICHESS_MIN_MS + i, "win": i % 2 == 0,
                "puzzle": {"id": f"pilot-{i % 3}", "fen": chess.STARTING_FEN,
                           "solution": ["e2e4"], "themes": ["fork"],
                           "rating": 1400, "plays": i}}
               for i in range(1, 14)]

    class Client:
        calls = []
        def stream_puzzle_activity(self, token, stop, *, since=None, before=None, max_items=1000):
            self.calls.append((since, before, max_items))
            yield from sorted(records, key=lambda item: item["date"], reverse=True)[:max_items]

    path = tmp_path / "puzzle-pilot.sqlite3"
    repo = GameRepository(path)
    account_id = repo.ensure_archive_account("PilotUser")
    repo.finish_puzzle_backfill(account_id)
    repo.set_puzzle_incremental_progress(account_id, 1_357_000_000_000,
                                         1_357_000_100_000)
    before = repo.puzzle_sync_state(account_id)
    client = Client()
    sync = PuzzleActivitySync(client, repo)

    first = sync.run_pilot("PilotUser", "fake", threading.Event(), limit=5)
    assert first["status"] == "complete"
    assert first["received_count"] == 5 and first["new_count"] == 5
    assert client.calls[0][0] is None and client.calls[0][2] == 5
    assert repo.puzzle_sync_state(account_id) == before
    first_history = repo.puzzle_pilot_history(account_id, run_id=first["run_id"], limit=20)
    assert len(first_history) == 5
    assert len({entry["attempt_uid"] for entry in first_history}) == 5

    second = sync.run_pilot("PilotUser", "fake", threading.Event(), limit=10)
    assert second["received_count"] == 10 and second["new_count"] == 5
    assert repo.puzzle_sync_state(account_id) == before
    assert repo.puzzle_statistics(account_id)["attempts"] == 10
    assert len(repo.puzzle_pilot_history(account_id, run_id=second["run_id"], limit=20)) == 10
    assert repo.puzzle_pilot_history(account_id, limit=30)[0]["run_id"] == second["run_id"]
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 8
        assert db.execute("SELECT COUNT(*) FROM puzzle_pilot_runs").fetchone()[0] == 2
    repo.shutdown()


def test_puzzle_attempt_identity_ignores_mutable_metadata_but_keeps_events_distinct(tmp_path):
    from chess_coach.lichess.puzzles import _key

    repo = GameRepository(tmp_path / "puzzle-stable-event.sqlite3")
    first = {"date": 123456, "win": True,
             "puzzle": {"id": "stable-p", "fen": chess.STARTING_FEN,
                        "solution": ["e2e4"], "themes": ["fork", "pin"],
                        "rating": 1400, "plays": 100}}
    refreshed = {"date": 123456, "win": True,
                 "puzzle": {**first["puzzle"], "themes": ["pin", "fork"],
                            "rating": 1430, "plays": 101}}
    assert _key(first, "lichess:alice") == _key(refreshed, "lichess:alice")
    uid, inserted = repo.import_puzzle_activities("Alice", [first])[0]
    same_uid, inserted_again = repo.import_puzzle_activities("Alice", [refreshed])[0]
    assert uid == same_uid and inserted and not inserted_again
    assert repo.puzzle_statistics(repo.ensure_archive_account("Alice"))["attempts"] == 1
    assert repo.has_puzzle_attempt("Alice", refreshed)
    with repo._connection() as db:
        puzzle = db.execute("SELECT puzzle_rating,plays,themes_json FROM puzzles WHERE puzzle_id='stable-p'").fetchone()
        assert tuple(puzzle) == (1430, 101, '["pin", "fork"]')

    later = {**refreshed, "date": 123457}
    _uid_later, inserted_later = repo.import_puzzle_activities("Alice", [later])[0]
    _uid_other_account, inserted_other_account = repo.import_puzzle_activities("Bob", [refreshed])[0]
    assert inserted_later and inserted_other_account
    assert repo.puzzle_statistics(repo.ensure_archive_account("Alice"))["attempts"] == 2
    assert repo.puzzle_statistics(repo.ensure_archive_account("Bob"))["attempts"] == 1
    repo.shutdown()


def test_v8_puzzle_attempt_migration_deduplicates_legacy_uids_and_preserves_pilot_links(tmp_path):
    path = tmp_path / "legacy-puzzle-v8.sqlite3"
    repo = GameRepository(path)
    account_id = repo.ensure_archive_account("LegacyUser")
    run_id = repo.start_puzzle_pilot(account_id, 5)
    record_a = {"date": 789, "win": True, "puzzle": {"id": "legacy-p",
                 "fen": chess.STARTING_FEN, "solution": ["e2e4"], "themes": ["fork"],
                 "rating": 1200, "plays": 10}}
    record_b = {"date": 789, "win": True, "puzzle": {**record_a["puzzle"],
                 "rating": 1220, "plays": 11, "themes": ["fork", "pin"]}}
    repo.import_puzzle_activities("LegacyUser", [record_a, record_b], pilot_run_id=run_id)
    with repo._connection() as db:
        db.execute("DROP INDEX idx_puzzle_attempt_event")
        duplicate_uid = hashlib.sha256(b"legacy-v8-duplicate-metadata-hash").hexdigest()
        db.execute("INSERT INTO puzzle_attempts SELECT ?,account_id,puzzle_id,attempt_date_ms,"
                   "win,source,metadata_json,imported_at FROM puzzle_attempts LIMIT 1",
                   (duplicate_uid,))
        db.execute("INSERT INTO puzzle_pilot_run_attempts VALUES (?,?)", (run_id, duplicate_uid))
        db.execute("UPDATE puzzle_pilot_runs SET new_count=2,received_count=2 WHERE run_id=?", (run_id,))
        db.execute("PRAGMA user_version=8")
    repo.shutdown()

    migrated = GameRepository(path)
    with migrated._connection() as db:
        attempts = db.execute("SELECT attempt_uid,metadata_json FROM puzzle_attempts").fetchall()
        links = db.execute("SELECT run_id,attempt_uid FROM puzzle_pilot_run_attempts").fetchall()
        run = db.execute("SELECT received_count,new_count FROM puzzle_pilot_runs WHERE run_id=?", (run_id,)).fetchone()
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(attempts) == 1
    assert len(links) == 1 and links[0][0] == run_id and links[0][1] == attempts[0][0]
    assert tuple(run) == (2, 2)  # historical run counters are not rewritten
    assert json.loads(attempts[0][1])["puzzle"]["rating"] in {1200, 1220}
    backup = sqlite3.connect(str(path) + f".pre-m3-v{SCHEMA_VERSION}.bak")
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 8
        assert backup.execute("SELECT COUNT(*) FROM puzzle_attempts").fetchone()[0] == 2
        assert backup.execute("SELECT COUNT(*) FROM puzzle_pilot_run_attempts").fetchone()[0] == 2
    finally:
        backup.close()
    migrated.shutdown()


def test_puzzle_pilot_rejects_unbounded_limits_and_records_cancelled_run(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    repo = GameRepository(tmp_path / "puzzle-pilot-cancel.sqlite3")
    account_id = repo.ensure_archive_account("CancelUser")
    before = repo.puzzle_sync_state(account_id)
    with pytest.raises(ValueError):
        repo.start_puzzle_pilot(account_id, 1000)

    class Client:
        def stream_puzzle_activity(self, token, stop, **kwargs):
            yield {"date": LICHESS_MIN_MS + 1, "win": True,
                   "puzzle": {"id": "cancel-p", "fen": chess.STARTING_FEN,
                              "solution": ["e2e4"], "themes": [], "rating": 1200}}
            stop.set()

    result = PuzzleActivitySync(Client(), repo).run_pilot(
        "CancelUser", "fake", threading.Event(), limit=5)
    assert result["status"] == "cancelled"
    assert result["received_count"] == 1 and result["new_count"] == 1
    assert repo.puzzle_sync_state(account_id) == before
    assert len(repo.puzzle_pilot_history(account_id, run_id=result["run_id"])) == 1
    repo.shutdown()


def test_puzzle_pilot_service_uses_separate_stored_credential_and_never_runs_full_sync(tmp_path):
    repo = GameRepository(tmp_path / "puzzle-pilot-service.sqlite3")
    record = {"date": LICHESS_MIN_MS + 50, "win": True,
              "puzzle": {"id": "service-p", "fen": chess.STARTING_FEN,
                         "solution": ["e2e4"], "themes": ["fork"], "rating": 1300}}

    class Client:
        def __init__(self): self.calls = []
        def get_account(self, token):
            self.calls.append(("account", token))
            return {"username": "ServicePilot"}
        def stream_puzzle_activity(self, token, stop, **kwargs):
            self.calls.append(("activity", token, kwargs["max_items"]))
            yield record
        def get_puzzle_dashboard(self, *args):
            raise AssertionError("Pilot must not request a full-sync dashboard")

    class TokenStore:
        def get(self): return "separate-puzzle-token"
        def set(self, _token): raise AssertionError("Existing token must not be replaced")

    client = Client()
    service = LichessService(client=client, token_store=TokenStore(), repository=repo)
    # Keep the orchestration deterministic; network methods above are local fakes.
    service._thread = lambda target, name: target()
    service.pilot_puzzle_activity(5, repo, TokenStore())
    account_id = repo.ensure_archive_account("ServicePilot")
    assert service.puzzle_username == "ServicePilot"
    assert client.calls == [("account", "separate-puzzle-token"),
                            ("activity", "separate-puzzle-token", 5)]
    assert repo.puzzle_statistics(account_id)["attempts"] == 1
    assert repo.puzzle_sync_state(account_id) == {}
    service.close()
    repo.shutdown()


def test_puzzle_pilot_api_failure_is_journaled_without_advancing_full_sync(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    repo = GameRepository(tmp_path / "puzzle-pilot-error.sqlite3")
    account_id = repo.ensure_archive_account("ErrorUser")
    before = repo.puzzle_sync_state(account_id)

    class BrokenClient:
        def stream_puzzle_activity(self, *args, **kwargs):
            raise RuntimeError("fixture transport failed")

    result = PuzzleActivitySync(BrokenClient(), repo).run_pilot(
        "ErrorUser", "fake", threading.Event(), limit=10)
    assert result["status"] == "error"
    assert result["last_error"] == "fixture transport failed"
    assert result["received_count"] == 0
    assert repo.puzzle_sync_state(account_id) == before
    repo.shutdown()


def test_puzzle_incremental_sync_pages_more_than_one_page(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    records = [{"date": date, "win": date % 2 == 0,
                "puzzle": {"id": f"p{date}", "fen": chess.STARTING_FEN,
                           "solution": ["e2e4"], "themes": [], "rating": 1000, "plays": 1}}
               for date in (LICHESS_MIN_MS + 100, LICHESS_MIN_MS + 200, LICHESS_MIN_MS + 300)]
    class Client:
        def stream_puzzle_activity(self, token, stop, *, since=None, before=None, max_items=1000):
            if before is None:
                return iter(())
            return iter(sorted((record for record in records if record["date"] < before),
                               key=lambda record: record["date"], reverse=True)[:max_items])
    repo = GameRepository(tmp_path / "puzzle-incremental.sqlite3")
    account_id = repo.ensure_archive_account("Me")
    repo.finish_puzzle_backfill(account_id)
    assert PuzzleActivitySync(Client(), repo).run(
        "Me", "token", threading.Event(), page_size=2) == 3
    assert repo.puzzle_statistics(account_id)["attempts"] == 3


def test_archive_games_endpoint_uses_until_and_rejects_before_parameter():
    class StrictTransport(FakeTransport):
        def stream(self, method, url, headers, body, timeout, stop_event):
            query = parse_qs(urlparse(url).query)
            allowed = {"max", "sort", "pgnInJson", "clocks", "evals", "accuracy",
                       "opening", "division", "finished", "ongoing", "since", "until"}
            assert not (set(query) - allowed)
            assert "before" not in query
            self.calls.append((method, url, headers, body))
            yield b'{"id":"g1","pgn":"1. e4 *"}\n'
    fake = StrictTransport()
    list(LichessClient(fake, base_url="https://lichess.test").stream_user_games(
        "token", "Me", threading.Event(), until_ms=1234))
    query = parse_qs(urlparse(fake.calls[0][1]).query)
    assert query["until"] == ["1234"] and query["sort"] == ["dateDesc"]
    assert query["division"] == ["true"] and query["finished"] == ["true"]
    assert query["ongoing"] == ["false"]


def test_archive_2501_games_paginates_three_pages_and_keeps_raw_pgn(tmp_path):
    from chess_coach.lichess.archive import ArchiveSync

    total = 2501
    records = [{"id": f"g{i}", "createdAt": LICHESS_MIN_MS + i, "lastMoveAt": LICHESS_MIN_MS + i,
                "pgn": f'[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n'
                       f'[Result "*"]\n\n1. e4 {{ [%clk 0:05:00] [%eval 0.25] }} *\n'}
               for i in range(1, total + 1)]
    class Client:
        def __init__(self): self.page_sizes = []
        def stream_user_games(self, token, username, stop, *, since=None, until_ms=None, max_games=1000):
            self.page_sizes.append((since, until_ms, max_games))
            matches = [r for r in records
                       if (since is None or r["createdAt"] >= since)
                       and (until_ms is None or r["createdAt"] <= until_ms)]
            matches.sort(key=lambda r: r["createdAt"], reverse=since is None)
            yield from matches[:max_games]
    class MemoryRepository:
        def __init__(self):
            self.state = {"backfill_complete": 0, "backfill_before": None, "incremental_since": None}
            self.items = {}
        def ensure_archive_account(self, _username): return "lichess:me"
        def archive_sync_state(self, _account): return dict(self.state)
        def import_archive_record(self, username, record, *, checkpoint_ms, backfill,
                                  _return_inserted=False):
            inserted = record["id"] not in self.items
            self.items[record["id"]] = record
            if backfill and checkpoint_ms is not None:
                self.state["backfill_before"] = checkpoint_ms
            return (record["id"], inserted) if _return_inserted else record["id"]
        def finish_archive_backfill(self, _account):
            self.state.update(backfill_complete=1, incremental_since=int(time.time() * 1000))
        def finish_archive_incremental(self, _account, cursor):
            self.state["incremental_since"] = cursor
        def record_archive_error(self, _account, error): self.state["last_error"] = error
        def list_games(self, completed_only=False): return list(self.items.values())
        def export_pgn(self, game_id): return self.items[game_id]["pgn"]
    repo = MemoryRepository()
    client = Client()
    imported = ArchiveSync(client, repo).run("Me", "fake", threading.Event(),
                                              full=True, page_size=1000)
    assert imported == total
    assert len(repo.list_games(completed_only=False)) == total
    assert len([p for p in client.page_sizes if p[2] == 1000 and p[1] is not None]) == 3
    assert repo.export_pgn("g1").find("[%clk 0:05:00]") >= 0
    assert repo.export_pgn("g1").find("[%eval 0.25]") >= 0
    assert len(repo.items) == total


def test_archive_drains_large_same_timestamp_boundary_without_losing_records():
    from chess_coach.lichess.archive import ArchiveSync
    total = 1005
    records = [{"id": f"same-{i}", "createdAt": LICHESS_MIN_MS + 5000, "lastMoveAt": LICHESS_MIN_MS + 5000,
                "pgn": f'[Event "Archive"]\n[White "Me"]\n[Black "F"]\n[Result "*"]\n\n1. e4 *\n'}
               for i in range(total)]
    class Client:
        def stream_user_games(self, token, username, stop, *, since=None, until_ms=None, max_games=1000):
            selected = [r for r in records
                        if (since is None or r["createdAt"] >= since)
                        and (until_ms is None or r["createdAt"] <= until_ms)]
            return iter(selected[:max_games])
    class Repository:
        def __init__(self):
            self.state = {"backfill_complete": 0, "backfill_before": None,
                          "incremental_since": None}
            self.ids = set()
        def ensure_archive_account(self, _): return "a"
        def archive_sync_state(self, _): return dict(self.state)
        def import_archive_record(self, _user, record, *, checkpoint_ms, backfill,
                                  _return_inserted=False):
            inserted = record["id"] not in self.ids
            self.ids.add(record["id"])
            self.state["backfill_before"] = checkpoint_ms
            return (record["id"], inserted) if _return_inserted else record["id"]
        def finish_archive_backfill(self, _):
            self.state.update(backfill_complete=1, incremental_since=int(time.time()*1000))
        def finish_archive_incremental(self, _, cursor): self.state["incremental_since"] = cursor
        def record_archive_error(self, _, error): self.state["last_error"] = error
    repo = Repository()
    assert ArchiveSync(Client(), repo).run("Me", "fake", threading.Event(),
                                            full=True, page_size=1000) == total
    assert len(repo.ids) == total
    assert repo.state["backfill_complete"] == 1


def test_archive_429_on_page_two_keeps_checkpoint_and_recovers_on_retry(tmp_path):
    from chess_coach.lichess.archive import ArchiveSync
    records = [{"id": f"g{i}", "createdAt": LICHESS_MIN_MS + i * 1000,
                "lastMoveAt": LICHESS_MIN_MS + i * 1000,
                "pgn": f'[Event "Archive"]\n[White "Me"]\n[Black "F"]\n[Result "*"]\n\n1. e4 *\n'}
               for i in range(1, 4)]
    class Client:
        fail_once = True
        calls = 0
        def stream_user_games(self, token, username, stop, *, since=None, until_ms=None, max_games=1000):
            if since is None and max_games == 2:
                self.calls += 1
                if self.calls == 2 and self.fail_once:
                    self.fail_once = False
                    raise RateLimitError(60)
            selected = [r for r in records
                        if (since is None or r["createdAt"] >= since)
                        and (until_ms is None or r["createdAt"] <= until_ms)]
            selected.sort(key=lambda r: r["createdAt"], reverse=since is None)
            return iter(selected[:max_games])
    repo = GameRepository(tmp_path / "archive-429.sqlite3")
    client = Client()
    sync = ArchiveSync(client, repo)
    with pytest.raises(RateLimitError):
        sync.run("Me", "fake", threading.Event(), full=True, page_size=2)
    account = repo.ensure_archive_account("Me")
    state = repo.archive_sync_state(account)
    assert state["backfill_complete"] == 0 and state["last_error"]
    assert len(repo.list_games(completed_only=False)) == 2
    assert sync.run("Me", "fake", threading.Event(), full=True, page_size=2) >= 1
    assert len(repo.list_games(completed_only=False)) == 3
    assert repo.archive_sync_state(account)["backfill_complete"] == 1


def test_async_archive_import_waits_for_atomic_writer_and_failure_keeps_cursor(tmp_path, monkeypatch):
    repo = GameRepository(tmp_path / "archive-atomic.sqlite3", async_writes=True)
    account = repo.ensure_archive_account("Me")
    record = {"id": "async-new", "createdAt": 1234, "pgn":
              '[Event "Archive"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n'
              '1. e4 { [%clk 0:05:00] [%eval 0.25] } *\n'}
    release = threading.Event()
    started = threading.Event()
    original = repo._save_snapshot_sync
    def slow(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, "_save_snapshot_sync", slow)
    done = threading.Event()
    errors = []
    def import_one():
        try:
            repo.import_archive_record("Me", record, checkpoint_ms=1234, backfill=True)
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=import_one)
    worker.start()
    assert started.wait(2)
    assert not done.is_set()
    with repo._connection() as db:
        assert db.execute("SELECT 1 FROM games WHERE game_id='async-new'").fetchone() is None
    release.set()
    worker.join(5)
    assert not errors and done.is_set()
    assert repo.get_game("async-new")["raw_pgn"].find("[%eval 0.25]") >= 0
    assert repo.export_pgn("async-new").find("[%clk 0:05:00]") >= 0
    with repo._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM account_games WHERE game_id='async-new'").fetchone()[0] == 1
    assert repo.archive_sync_state(account)["backfill_before"] == 1235

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("simulated disk write failure")
    monkeypatch.setattr(repo, "_save_snapshot_sync", fail)
    failing = {**record, "id": "must-not-commit"}
    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        repo.import_archive_record("Me", failing, checkpoint_ms=9999, backfill=True)
    assert repo.get_game("must-not-commit") is None
    assert repo.archive_sync_state(account)["backfill_before"] == 1235
    repo.shutdown()


def test_puzzle_sync_handles_2501_descending_attempts_and_restarts_after_page_two(tmp_path):
    from chess_coach.lichess.puzzles import PuzzleActivitySync

    records = [{"date": LICHESS_MIN_MS + i, "win": i % 2 == 0,
                "puzzle": {"id": f"p{i % 17}", "fen": chess.STARTING_FEN,
                           "solution": ["e2e4"], "themes": ["fork"], "rating": 1200, "plays": i}}
               for i in range(1, 2502)]
    class Client:
        def __init__(self): self.full_pages = 0; self.cancel_page_two = True
        def stream_puzzle_activity(self, token, stop, *, since=None, before=None, max_items=1000):
            if before is None: return iter(())
            if max_items == 1000:
                self.full_pages += 1
                if self.cancel_page_two and self.full_pages == 2:
                    stop.set()
                    return iter(())
            items = [r for r in records if (before is None or r["date"] < before)
                     and (since is None or r["date"] >= since)]
            return iter(sorted(items, key=lambda r: r["date"], reverse=True)[:max_items])
    class MemoryRepository:
        def __init__(self):
            self.state = {"backfill_complete": 0, "backfill_before": None,
                          "incremental_since": None, "incremental_before": None,
                          "incremental_target": None}
            self.attempts = {}
        def ensure_archive_account(self, _username): return "lichess:me"
        def puzzle_sync_state(self, _account): return dict(self.state)
        def import_puzzle_activity(self, _username, record, *, backfill=False, _return_inserted=False):
            uid = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False).encode()).hexdigest()
            inserted = uid not in self.attempts
            self.attempts[uid] = record
            if backfill: self.state["backfill_before"] = int(record["date"]) + 1
            return (uid, inserted) if _return_inserted else uid
        def finish_puzzle_backfill(self, _account):
            self.state.update(backfill_complete=1,
                              incremental_since=max((r["date"] for r in self.attempts.values()), default=0))
        def set_puzzle_incremental_progress(self, _account, before, target):
            self.state.update(incremental_before=before, incremental_target=target)
        def finish_puzzle_incremental(self, _account, cursor, new_count=0):
            self.state.update(incremental_since=cursor, incremental_before=None,
                              incremental_target=None, last_sync_new_count=new_count)
        def record_puzzle_error(self, _account, error): self.state["last_error"] = error
        def puzzle_statistics(self, _account): return {"attempts": len(self.attempts)}
        def puzzle_history(self, _account, limit=100, offset=0):
            return list(self.attempts.values())[offset:offset + limit]
    repo = MemoryRepository()
    client = Client()
    sync = PuzzleActivitySync(client, repo)
    stop = threading.Event()
    assert sync.run("Me", "fake", stop, page_size=1000) == 1000
    account = repo.ensure_archive_account("Me")
    assert repo.puzzle_sync_state(account)["backfill_complete"] == 0
    assert repo.puzzle_statistics(account)["attempts"] == 1000
    stop.clear(); client.cancel_page_two = False
    assert sync.run("Me", "fake", stop, page_size=1000) == 1501
    assert repo.puzzle_statistics(account)["attempts"] == 2501
    assert len(repo.puzzle_history(account, limit=500)) == 500
    assert repo.puzzle_sync_state(account)["backfill_complete"] == 1


def test_archive_unsupported_variant_preserves_pgn_without_fake_standard_line(tmp_path):
    repo = GameRepository(tmp_path / "variant.sqlite3", async_writes=True)
    pgn = '[Event "Variant"]\n[Variant "Crazyhouse"]\n[White "Me"]\n[Black "Friend"]\n[Result "*"]\n\n1. e4 *\n'
    repo.import_archive_record("Me", {"id": "variant", "variant": "crazyhouse", "pgn": pgn},
                               checkpoint_ms=42, backfill=True)
    game = repo.get_game("variant")
    assert game["variant"] == "crazyhouse"
    assert "analysis disabled" in game["import_error"]
    assert game["raw_pgn"] == pgn and repo.export_pgn("variant") == pgn
    assert game["initial_fen"] == "" and repo.current_line("variant") is None
    repo.shutdown()


def test_archive_malformed_pgn_is_quarantined_and_checkpointed(tmp_path):
    path = tmp_path / "quarantine.sqlite3"
    repo = GameRepository(path)
    pgn = '[Event "Broken"]\n[White "Me"]\n[Black "Friend"]\n\n1. e4 e5 2. e5 *\n'
    game_id = repo.import_archive_record(
        "Me", {"id": "broken", "variant": "standard", "pgn": pgn},
        checkpoint_ms=1234, backfill=True)
    assert game_id == "broken"
    game = repo.get_game(game_id)
    assert game["import_error"].startswith("Malformed PGN quarantined")
    assert game["raw_pgn"] == pgn
    assert game["initial_fen"] == "" and repo.current_line(game_id) is None
    account = repo.ensure_archive_account("Me")
    assert repo.archive_sync_state(account)["backfill_before"] == 1235
    assert repo.export_pgn(game_id) == pgn
    repo.shutdown()
    reopened = GameRepository(path)
    assert reopened.get_game(game_id)["raw_pgn"] == pgn
    assert reopened.export_pgn(game_id) == pgn
    reopened.shutdown()


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


def test_disconnect_cancels_archive_and_puzzle_background_workers(monkeypatch):
    service = LichessService(client=object(), token_store=object())
    service._archive_stop = threading.Event()
    service._puzzle_stop = threading.Event()
    monkeypatch.setattr(service, "_thread", lambda target, name: None)

    service.disconnect_account()

    assert service._archive_stop.is_set()
    assert service._puzzle_stop.is_set()


def test_automatic_puzzle_sync_never_opens_oauth_without_saved_token(monkeypatch):
    import chess_coach.lichess.service as service_module

    class EmptyCredentialStore:
        def __init__(self, **kwargs):
            pass

        def get(self):
            return None

    class NoNetworkClient:
        def get_account(self, token):
            raise AssertionError("No account requests without an existing puzzle token")

    monkeypatch.setattr(service_module, "WindowsCredentialStore", EmptyCredentialStore)
    service = LichessService(client=NoNetworkClient(), token_store=object())
    service.username = "Owner"
    monkeypatch.setattr(service, "_thread", lambda target, name: target())
    results = []
    service.background_sync_finished.connect(lambda kind, status: results.append((kind, status)))

    service.sync_puzzle_activity_background(repository=object())

    assert service._puzzle_stop is None
    assert results == []
