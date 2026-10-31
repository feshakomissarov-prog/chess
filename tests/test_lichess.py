import base64
import hashlib
import json
import threading
from urllib.request import urlopen
from urllib.parse import parse_qs, urlparse

import chess
import chess.pgn

from chess_coach.lichess.api import HttpResponse, LichessClient, LichessError, RateLimitError
from chess_coach.lichess.auth import OAuthCallbackServer, create_authorization_request
from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.session import LichessGameSession
from chess_coach.lichess.service import LichessService


class FakeTransport:
    def __init__(self, responses=None, lines=None):
        self.responses = list(responses or [])
        self.lines = list(lines or [])
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)

    def stream(self, method, url, headers, body, timeout, stop_event):
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
    fake = FakeTransport([response(200, {"ok": True}) for _ in range(4)])
    client = LichessClient(fake)
    client.send_move("token", "game1234", "e2e4")
    client.handle_draw("token", "game1234", True)
    client.resign("token", "game1234")
    client.abort("token", "game1234")
    assert fake.calls[0][1].endswith("/api/board/game/game1234/move/e2e4")
    assert fake.calls[1][1].endswith("/api/board/game/game1234/draw/yes")
    assert fake.calls[2][1].endswith("/api/board/game/game1234/resign")
    assert fake.calls[3][1].endswith("/api/board/game/game1234/abort")


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
