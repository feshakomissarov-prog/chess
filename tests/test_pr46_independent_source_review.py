from __future__ import annotations

import threading

from types import SimpleNamespace

import chess
import pytest
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QMessageBox

from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.api import LichessError, RateLimitError
from chess_coach.lichess.game_status import TERMINAL_GAME_STATUSES
from chess_coach.lichess.service import LichessService
from chess_coach.lichess.session import LichessGameSession
from chess_coach.window import MainWindow


@pytest.fixture(autouse=True)
def disable_credential_restore_and_isolate_storage(monkeypatch, tmp_path):
    # These fake-account tests never read credentials or contact Lichess.
    monkeypatch.setattr("chess_coach.lichess.service.LichessService.restore", lambda self: None)
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda _location: str(tmp_path / "ChessCoach"))


class MemoryStore:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = None


class FakeGameClient:
    def __init__(self):
        self.posts = []

    def resign(self, token, game_id):
        self.posts.append(("resign", game_id))

    def claim_victory(self, token, game_id):
        self.posts.append(("claim_victory", game_id))

    def handle_takeback(self, token, game_id, accept):
        self.posts.append(("takeback", game_id, accept))

    def handle_draw(self, token, game_id, accept):
        self.posts.append(("draw", game_id, accept))

    def send_move(self, token, game_id, uci):
        self.posts.append(("move", game_id, uci))


def _service(client=None):
    return LichessService(client=client or FakeGameClient(),
                          token_store=MemoryStore("oauth-token"),
                          personal_token_store=MemoryStore("pat-token"))


def _event(kind="gameState", *, moves="e2e4", status="playing", **state):
    return {"type": kind, "initialFen": "startpos",
            "state": {"moves": moves, "status": status, **state}}


def _accept_snapshot(service, game_id="live", *, generation=None, color="white",
                     snapshot_sequence=1):
    generation = service.auth_generation if generation is None else generation
    service.reconcile_game_actions(
        game_id, _event("gameFull", wdraw=False, bdraw=False), generation,
        color=color, snapshot_sequence=snapshot_sequence)
    return service.authorize_game_actions(
        game_id, generation, snapshot_sequence=snapshot_sequence, color=color)


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("stale_kind", ["gameState", "gameFull"])
@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK])
def test_session_latches_confirmed_draw_offer_against_old_or_missing_flags(
        tmp_path, missing, stale_kind, color):
    session = LichessGameSession("draw-latch", GameRepository(tmp_path / "draw.sqlite3"))
    session.color = color
    my_flag = "wdraw" if color == chess.WHITE else "bdraw"
    opponent_flag = "bdraw" if color == chess.WHITE else "wdraw"
    session.apply_event(_event("gameFull", wdraw=False, bdraw=False))
    session.apply_event(_event(**{my_flag: True, opponent_flag: False}))
    assert session.draw_offered_by_me

    stale = _event(stale_kind)
    if not missing:
        stale["state"]["wdraw"] = False
        stale["state"]["bdraw"] = False
    session.apply_event(stale)
    assert session.draw_offered_by_me

    # Only an explicitly cleared flag on a later confirmed line clears the
    # latch; that line also allows a later, genuinely new opponent offer.
    session.apply_event(_event(moves="e2e4 e7e5", wdraw=False, bdraw=False))
    assert not session.draw_offered_by_me
    session.apply_event(_event(moves="e2e4 e7e5",
                               **{my_flag: False, opponent_flag: True}))
    assert session.draw_offered_by_opponent


def test_session_latches_incoming_draw_offer_against_same_line_false_replay(tmp_path):
    session = LichessGameSession("incoming-draw-latch", GameRepository(
        tmp_path / "incoming-draw.sqlite3"))
    session.color = chess.WHITE
    session.apply_event(_event("gameFull", wdraw=False, bdraw=False))
    session.apply_event(_event(bdraw=True, wdraw=False))
    assert session.draw_offered_by_opponent
    session.apply_event(_event(bdraw=False, wdraw=False))
    assert session.draw_offered_by_opponent


def test_confirmed_takeback_branch_can_clear_draw_offer_with_explicit_false(tmp_path):
    session = LichessGameSession("draw-latch-takeback", GameRepository(
        tmp_path / "draw-latch-takeback.sqlite3"))
    session.color = chess.WHITE
    session.apply_event(_event("gameFull", moves="e2e4 e7e5",
                               wdraw=False, bdraw=False))
    session.apply_event(_event(moves="e2e4 e7e5", wdraw=True, bdraw=False))
    assert session.draw_offered_by_me

    session.takeback_requested_by_me = True
    session.apply_event(_event(moves="e2e4", wdraw=False, bdraw=False))
    assert session.moves == "e2e4"
    assert not session.draw_offered_by_me


@pytest.mark.parametrize("color", ["white", "black"])
def test_service_latches_confirmed_offer_and_rejects_duplicate_posts(qtbot, color):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "draw", generation=generation, color=color)

    service.draw("draw", True)
    qtbot.waitUntil(lambda: client.posts == [("draw", "draw", True)], timeout=1000)
    own_flag = "wdraw" if color == "white" else "bdraw"
    opponent_flag = "bdraw" if color == "white" else "wdraw"
    service.reconcile_game_actions(
        "draw", _event(**{own_flag: True, opponent_flag: False}), generation,
        color=color)
    for stale in (_event(**{own_flag: False, opponent_flag: False}),
                  _event(**{own_flag: False, opponent_flag: False}),
                  _event(**{opponent_flag: False})):
        service.reconcile_game_actions("draw", stale, generation, color=color)
    assert service._game_authoritative_state[(generation, "draw")][own_flag] is True
    service.draw("draw", True)
    qtbot.wait(20)
    assert client.posts == [("draw", "draw", True)]

    # A false flag on a progressed confirmed line clears the latch and permits
    # a genuinely later offer. No retry is inferred from the stale replay.
    service.reconcile_game_actions(
        "draw", _event(moves="e2e4 e7e5", wdraw=False, bdraw=False),
        generation, color=color)
    service.draw("draw", True)
    qtbot.waitUntil(lambda: client.posts == [
        ("draw", "draw", True), ("draw", "draw", True)], timeout=1000)
    # Explicit false on an accepted shorter takeback branch is newer state.
    service.reconcile_game_actions(
        "draw", _event(moves="e2e4", wdraw=False, bdraw=False),
        generation, color=color, accepted_line_change=True)
    assert service._game_authoritative_state[(generation, "draw")][own_flag] is False
    service.close(timeout=1.0)


def test_service_requires_accepted_fresh_game_snapshot_for_every_live_post(qtbot):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)

    assert service.resign("not-yet-snapshotted") is None
    qtbot.wait(20)
    assert client.posts == []

    assert _accept_snapshot(service, "live", generation=generation)
    request_id = service.resign("live")
    qtbot.waitUntil(lambda: client.posts == [("resign", "live")], timeout=1000)
    service.invalidate_game_action_admission(generation, "live")
    assert service.claim_victory("live") is None
    assert client.posts == [("resign", "live")]
    service.close(timeout=1.0)


def test_global_disconnect_invalidates_admission_until_new_accepted_snapshot(qtbot):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "live", generation=generation)
    service.invalidate_game_action_admission(generation)
    service.mark_event_stream_ready(generation)

    assert not service.authorize_game_actions("live", generation, 1, color="white")
    assert service.resign("live") is None
    assert client.posts == []
    assert _accept_snapshot(service, "live", generation=generation,
                            snapshot_sequence=2)
    service.resign("live")
    qtbot.waitUntil(lambda: client.posts == [("resign", "live")], timeout=1000)
    service.close(timeout=1.0)


def test_queued_game_post_is_rechecked_after_game_stream_loss(qtbot, monkeypatch):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "live", generation=generation)
    queued, completed = [], []
    monkeypatch.setattr(service, "_thread", lambda work, _name: queued.append(work))
    service.session_action_completed.connect(lambda *args: completed.append(args))

    service.resign("live")
    assert len(queued) == 1
    service.invalidate_game_action_admission(generation, "live")
    queued.pop()()
    qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    assert completed[-1][-1] == -2
    assert client.posts == []
    assert not service.has_pending_action("live", "resign")
    assert service.resign("old-game-id") is None
    service.close(timeout=1.0)


def test_game_action_429_requires_new_snapshot_after_cooldown(qtbot):
    class RateLimitedClient(FakeGameClient):
        rate_limit_remaining = 0

        def resign(self, token, game_id):
            self.posts.append(("resign", game_id))
            self.rate_limit_remaining = 45
            raise RateLimitError(45)

    client = RateLimitedClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "live", generation=generation)
    statuses, completed = [], []
    service.session_status_changed.connect(lambda *args: statuses.append(args))
    service.session_action_completed.connect(lambda *args: completed.append(args))

    service.resign("live")
    qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    assert completed[-1][-1] == 429
    assert statuses and statuses[-1][1].startswith("Ограничение Lichess (429)")
    client.rate_limit_remaining = 0
    assert service.resign("other-game") is None
    assert client.posts == [("resign", "live")]

    assert service._accepted_game_full_sequence == {}
    assert not service.authorize_game_actions("live", generation, 1, color="white")
    service.close(timeout=1.0)


@pytest.mark.parametrize("pending_command", ["resign", "abort", "claim_draw", "takeback"])
@pytest.mark.parametrize("first", ["claim_victory", "other"])
def test_claim_victory_conflicts_atomically_with_terminal_service_commands(
        monkeypatch, pending_command, first):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "live", generation=generation)
    queued = []
    monkeypatch.setattr(service, "_thread", lambda work, _name: queued.append(work))

    def invoke(command):
        if command == "claim_victory":
            service.claim_victory("live")
        elif command == "takeback":
            service.takeback("live", "offer", f"{command}-request")
        else:
            service._action(lambda token: None, game_id="live", command=command)

    first_command = "claim_victory" if first == "claim_victory" else pending_command
    competing_command = pending_command if first == "claim_victory" else "claim_victory"
    invoke(first_command)
    assert len(queued) == 1
    invoke(competing_command)
    assert len(queued) == 1
    assert service.has_pending_action("live", first_command)
    assert not service.has_pending_action("live", competing_command)
    assert client.posts == []
    service.close(timeout=1.0)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, -1])
def test_takeback_definitive_vs_ambiguous_http_failures_are_one_shot(qtbot, status):
    class FailingTakebackClient(FakeGameClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def handle_takeback(self, token, game_id, accept):
            self.calls += 1
            if status == -1:
                raise TimeoutError("synthetic timeout")
            raise LichessError("synthetic takeback failure", status)

    client = FailingTakebackClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "live", generation=generation)
    completed = []
    service.session_takeback_action_completed.connect(lambda *args: completed.append(args))

    service.takeback("live", "offer", "request")
    qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    assert completed[-1][-1] == status
    definitive = status in {400, 401, 403, 404, 422}
    assert service.has_pending_action("live", "takeback") is (not definitive)
    if not definitive:
        service.takeback("live", "offer", "retry")
        qtbot.wait(20)
    assert client.calls == 1
    service.close(timeout=1.0)


def test_claim_victory_conflicts_symmetrically_with_terminal_commands():
    from chess_coach.lichess.service_games import GAME_ACTION_CONFLICTS

    actions = {"offer_draw", "accept_draw", "decline_draw", "resign", "abort",
               "claim_draw", "claim_victory", "takeback"}
    assert set(GAME_ACTION_CONFLICTS) == actions
    terminal_commands = actions - {"offer_draw"}
    for pending in terminal_commands:
        assert GAME_ACTION_CONFLICTS[pending] == actions
    assert "claim_victory" not in GAME_ACTION_CONFLICTS["offer_draw"]
    assert "offer_draw" in GAME_ACTION_CONFLICTS["claim_victory"]


def test_takeback_without_credentials_releases_pending_and_completes(qtbot, monkeypatch):
    client = FakeGameClient()
    token_store = MemoryStore("temporary")
    service = LichessService(client=client, token_store=token_store,
                             personal_token_store=MemoryStore("pat"))
    scheduled = []
    monkeypatch.setattr(service, "_thread", lambda target, _name: scheduled.append(target))
    completed = []
    service.session_takeback_action_completed.connect(lambda *args: completed.append(args))

    assert service.takeback("live", "offer", "request") is None  # no stream admission
    assert not service.has_pending_action("live", "takeback")
    service.mark_event_stream_ready(service.auth_generation)
    assert _accept_snapshot(service)
    assert service.takeback("live", "offer", "request") is None  # worker is queued
    assert service.has_pending_action("live", "takeback")
    token_store.value = None
    scheduled[-1]()

    assert completed and completed[-1][-1] == -2
    assert not service.has_pending_action("live", "takeback")
    assert client.posts == []
    service.close(timeout=1.0)


def test_takeback_missing_credential_completes_through_qt_session(qtbot, tmp_path,
                                                                  monkeypatch):
    window = MainWindow(QSettings(str(tmp_path / "takeback-ui.ini"),
                                  QSettings.Format.IniFormat), offline_mode=False)
    qtbot.addWidget(window)
    service = window.lichess
    service.username = "TakebackTest"
    client = FakeGameClient()
    service.client = client
    token_store = MemoryStore("will-expire-before-worker")
    service.oauth_token_store = token_store
    service.personal_token_store = MemoryStore("unused-pat")
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    window._lichess_account_authenticated = True
    window._lichess_session_selected = True
    window._lichess_connected = True
    window._lichess_stream_ready_generation = generation
    window._enforce_live_human_game_safety = lambda *a, **k: None

    session = LichessGameSession("takeback-ui", window.game_repository)
    session.color = chess.WHITE
    session.bind_owner("TakebackTest", generation)
    window.game_sessions.register(session)
    window.online_sessions[session.game_id] = session
    window.online_session = session
    window._selected_live_game_id = session.game_id
    window._screen_mode = "online"
    window.screen_stack.setCurrentWidget(window.game_page)
    window._lichess_game_event(session.game_id, _event("gameFull"), generation=generation)

    scheduled = []
    monkeypatch.setattr(service, "_thread", lambda work, _name: scheduled.append(work))
    window._start_takeback_action("offer")
    assert session.takeback_action_pending == "offer"
    assert service.has_pending_action(session.game_id, "takeback")
    token_store.value = None
    scheduled.pop()()
    qtbot.waitUntil(lambda: session.takeback_action_pending is None, timeout=1000)
    qtbot.waitUntil(lambda: (session.game_id, "takeback")
                    not in window._pending_game_actions, timeout=1000)

    assert not service.has_pending_action(session.game_id, "takeback")
    assert client.posts == []
    assert window.takeback_button.isEnabled()
    window.close()


def test_two_fake_accounts_route_draw_stream_through_sessions_and_qt_controls(
        qtbot, tmp_path, monkeypatch):
    game_id = "qt-draw-reconciliation"
    clients = {"Alice": FakeGameClient(), "Bob": FakeGameClient()}
    windows = {}
    sessions = {}
    generation_by_name = {}
    for index, (username, color) in enumerate((
            ("Alice", chess.WHITE), ("Bob", chess.BLACK))):
        window = MainWindow(QSettings(str(tmp_path / f"{username}.ini"),
                                      QSettings.Format.IniFormat), offline_mode=False)
        qtbot.addWidget(window)
        service = window.lichess
        assert service is not None
        service.client = clients[username]
        service.oauth_token_store = MemoryStore(f"fake-{username}")
        service.personal_token_store = MemoryStore(f"fake-pat-{username}")
        service.username = username
        generation = service.auth_generation
        generation_by_name[username] = generation
        service.mark_event_stream_ready(generation)
        window.offline_mode = False
        window._lichess_account_authenticated = True
        window._lichess_session_selected = True
        window._lichess_connected = True
        window._lichess_stream_ready_generation = generation
        window._event_stream_recovery_pending = False
        monkeypatch.setattr(window, "_enforce_live_human_game_safety", lambda *a, **k: None)
        session = LichessGameSession(game_id, window.game_repository)
        session.color = color
        session.bind_owner(username, generation)
        window.game_sessions.register(session)
        window.online_sessions[game_id] = session
        window.online_session = session
        window._selected_live_game_id = game_id
        window._screen_mode = "online"
        window.screen_stack.setCurrentWidget(window.game_page)
        windows[username] = window
        sessions[username] = session

        window._lichess_game_event(game_id, {
            "type": "gameFull", "initialFen": "startpos",
            "color": "white" if color == chess.WHITE else "black",
            "white": {"name": "Alice"}, "black": {"name": "Bob"},
            "state": {"moves": "e2e4 e7e5", "status": "playing",
                      "wdraw": False, "bdraw": False},
        }, generation=generation)
        assert service._game_action_admission_ready_locked(generation, game_id)
        assert window.draw_button.isEnabled()

    alice = windows["Alice"]
    bob = windows["Bob"]
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k:
                        QMessageBox.StandardButton.Yes)

    # Alice's single HTTP fake POST is followed by stream delivery to both
    # real session reducers and their visible Qt controls.
    alice.draw_button.click()
    qtbot.waitUntil(lambda: len(clients["Alice"].posts) == 1, timeout=1000)
    offered = _event(moves="e2e4 e7e5", wdraw=True, bdraw=False)
    for username in ("Alice", "Bob"):
        windows[username]._lichess_game_event(
            game_id, offered, generation=generation_by_name[username])
    assert sessions["Alice"].draw_offered_by_me
    assert sessions["Bob"].draw_offered_by_opponent

    # One or more stale copies cannot re-enable a repeated offer or hide the
    # incoming offer. Exercise both the button state and direct UI handler.
    stale = _event(moves="e2e4 e7e5", wdraw=False, bdraw=False)
    for _ in range(2):
        for username in ("Alice", "Bob"):
            windows[username]._lichess_game_event(
                game_id, stale, generation=generation_by_name[username])
    assert sessions["Alice"].draw_offered_by_me
    assert sessions["Bob"].draw_offered_by_opponent
    assert not alice.draw_button.isEnabled()
    assert bob.draw_button.isEnabled()
    alice.handle_draw()
    assert clients["Alice"].posts == [("draw", game_id, True)]

    bob.decline_draw_button.click()
    qtbot.waitUntil(lambda: clients["Bob"].posts == [("draw", game_id, False)], timeout=1000)
    qtbot.waitUntil(lambda: sessions["Bob"].awaiting_authoritative_snapshot, timeout=1000)

    # A later confirmed line and explicit false can clear the old offer; a new
    # independent offer remains reachable, preserving the normal game flow.
    # A successful decline requires a fresh authoritative full snapshot; a
    # same-line gameState false could be an old replay and is not sufficient.
    continued = _event("gameFull", moves="e2e4 e7e5", wdraw=False, bdraw=False)
    # Alice has an outgoing offer whose acknowledgement is only known to Bob.
    # Confirm line progress for Alice and a fresh full snapshot for Bob after
    # Bob's successful decline; one account's response is not another's ACK.
    alice_progress = _event("gameState", moves="e2e4 e7e5 g1f3", wdraw=False, bdraw=False)
    windows["Alice"]._lichess_game_event(
        game_id, alice_progress, generation=generation_by_name["Alice"])
    windows["Bob"]._lichess_game_event(
        game_id, continued, generation=generation_by_name["Bob"])
    assert not sessions["Alice"].draw_offered_by_me
    assert not sessions["Bob"].draw_offered_by_opponent
    bob.draw_button.click()
    qtbot.waitUntil(lambda: len(clients["Bob"].posts) == 2, timeout=1000)

    for window in windows.values():
        window.close()


def test_authorize_and_auth_status_use_one_lock_order_and_finish_concurrently():
    service = _service()
    service.mark_event_stream_ready(service.auth_generation)
    generation = service.auth_generation
    assert _accept_snapshot(service, "lock-order", generation=generation)
    underlying = service._pending_action_lock

    class OrderedPendingLock:
        def __enter__(self):
            if threading.current_thread().name == "authorize-test":
                assert service._auth_lock._is_owned(), "auth lock must precede pending lock"
            underlying.acquire()
            return self
        def __exit__(self, *_args):
            underlying.release()

    service._pending_action_lock = OrderedPendingLock()
    completed = []
    authorize = threading.Thread(
        name="authorize-test",
        target=lambda: completed.append(service.authorize_game_actions(
            "lock-order", generation, 1, color="white")))
    status = threading.Thread(
        name="status-test",
        target=lambda: completed.append(service._emit_session_status(
            generation, "Переподключение…")))
    authorize.start()
    status.start()
    authorize.join(timeout=1.0)
    status.join(timeout=1.0)
    assert not authorize.is_alive() and not status.is_alive()
    assert completed == [True, True]
    assert not service._game_action_admissions


def test_service_send_move_requires_fresh_snapshot_and_rechecks_queued_work(qtbot, monkeypatch):
    client = FakeGameClient()
    service = _service(client)
    failures = []
    service.session_move_failed.connect(lambda *args: failures.append(args))
    generation = service.auth_generation

    service.send_move("g", "e2e4", "before-snapshot")
    qtbot.waitUntil(lambda: len(failures) == 1, timeout=1000)
    assert failures[0][-1] == -2
    assert client.posts == []

    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "g", generation=generation)
    entered, release = threading.Event(), threading.Event()
    original = service.operation_token
    def blocked_credential(context):
        entered.set()
        assert release.wait(1.0)
        return original(context)
    monkeypatch.setattr(service, "operation_token", blocked_credential)
    service.send_move("g", "e2e4", "queued-before-loss")
    assert entered.wait(1.0)
    service.invalidate_game_action_admission(generation, "g")
    release.set()
    qtbot.waitUntil(lambda: len(failures) == 2, timeout=1000)
    assert failures[-1][-1] == -2
    assert client.posts == []


def test_service_accept_and_decline_draw_require_an_authoritative_incoming_offer(qtbot):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "incoming", generation=generation)

    assert service.draw("incoming", True, accepting_offer=True) is None
    assert service.draw("incoming", False) is None
    qtbot.wait(20)
    assert client.posts == []

    service.reconcile_game_actions(
        "incoming", _event("gameState", bdraw=True, wdraw=False), generation)
    service.draw("incoming", True, accepting_offer=True)
    qtbot.waitUntil(lambda: client.posts == [("draw", "incoming", True)], timeout=1000)
    # A second response, including the opposite action, is blocked while the
    # first accepted response awaits authoritative state.
    service.draw("incoming", False)
    qtbot.wait(20)
    assert client.posts == [("draw", "incoming", True)]


def test_successful_same_line_draw_decline_requires_fresh_full_snapshot(qtbot, tmp_path):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "decline", generation=generation, color="black")
    session = LichessGameSession("decline", GameRepository(tmp_path / "decline.sqlite3"))
    session.color = chess.BLACK
    initial = _event("gameFull", moves="e2e4 e7e5", wdraw=False, bdraw=False)
    incoming = _event("gameState", moves="e2e4 e7e5", wdraw=True, bdraw=False)
    session.apply_event(initial)
    service.reconcile_game_actions("decline", incoming, generation, color="black")
    session.apply_event(incoming)
    assert session.draw_offered_by_opponent

    service.draw("decline", False)
    qtbot.waitUntil(lambda: client.posts == [("draw", "decline", False)], timeout=1000)
    service.draw("decline", False)
    qtbot.wait(20)
    assert client.posts == [("draw", "decline", False)]

    # A status-only replay after ACK remains latched. A newly accepted full
    # snapshot is the causal resync that may clear the declined offer.
    stale = _event("gameState", moves="e2e4 e7e5", wdraw=False, bdraw=False)
    service.reconcile_game_actions("decline", stale, generation, color="black")
    session.apply_event(stale)
    assert session.draw_offered_by_opponent
    session.awaiting_authoritative_snapshot = True
    fresh = _event("gameFull", moves="e2e4 e7e5", wdraw=False, bdraw=False)
    session.apply_event(fresh)
    service.reconcile_game_actions(
        "decline", fresh, generation, color="black", resync=True,
        snapshot_sequence=2)
    assert not session.draw_offered_by_opponent
    assert service._game_authoritative_state[(generation, "decline")]["wdraw"] is False


def test_game_stream_restart_reopens_known_games_for_fresh_full(qtbot):
    class StreamingClient(FakeGameClient):
        def __init__(self):
            super().__init__()
            self.stream_calls = []
        def stream_game(self, token, game_id, stop):
            self.stream_calls.append(game_id)
            yield _event("gameFull", moves="", status="playing", wdraw=False, bdraw=False)
            while not stop.wait(0.01):
                yield _event("gameState", moves="", status="playing", wdraw=False, bdraw=False)

    client = StreamingClient()
    service = _service(client)
    generation = service.auth_generation
    service.start_game("stream-g")
    qtbot.waitUntil(lambda: client.stream_calls == ["stream-g"], timeout=1000)
    service.restart_live_game_streams(generation)
    qtbot.waitUntil(lambda: client.stream_calls == ["stream-g", "stream-g"], timeout=1000)
    assert service._game_stops.get("stream-g") is not None
    service.close(timeout=0.2)


def test_game_specific_429_stales_only_affected_window_session(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "scoped-stream-loss.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    affected = LichessGameSession("affected-stream", window.game_repository)
    other = LichessGameSession("healthy-stream", window.game_repository)
    for session in (affected, other):
        session.status = "playing"
        session.awaiting_authoritative_snapshot = False
        window.online_sessions[session.game_id] = session
    window._lichess_status(
        "Ограничение Lichess (429): синхронизация партии affected-stream через 60 с")
    assert affected.awaiting_authoritative_snapshot
    assert not other.awaiting_authoritative_snapshot
    window.close()


def test_not_sent_move_failure_clears_matching_local_submission(qtbot, tmp_path):
    window = MainWindow(QSettings(str(tmp_path / "not-sent-move.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    session = LichessGameSession("not-sent", window.game_repository)
    session.status = "playing"
    session.awaiting_authoritative_snapshot = False
    assert session.begin_submission(chess.Move.from_uci("e2e4"))
    request_id = session.pending_request_id
    window.online_sessions[session.game_id] = session
    window._lichess_move_failed(session.game_id, "e2e4", request_id, -2)
    assert session.pending_uci is None
    window.close()


@pytest.mark.parametrize("status", ["mate", "resign", "stalemate", "timeout",
                                    "outoftime", "draw", "cheat", "noStart",
                                    "variantEnd", "unknownFinish", "aborted",
                                    "repetition", "50moves", "insufficientMaterial",
                                    "finished"])
@pytest.mark.parametrize("kind", ["gameFull", "gameState"])
def test_all_terminal_server_statuses_open_only_the_postgame_review_context(
        qtbot, tmp_path, status, kind):
    window = MainWindow(QSettings(str(tmp_path / f"terminal-{status}.ini"),
                                  QSettings.Format.IniFormat), offline_mode=True)
    qtbot.addWidget(window)
    session = LichessGameSession(f"terminal-{status}", window.game_repository)
    if kind == "gameState":
        session.apply_event(_event("gameFull", moves="", status="playing"))
    session.apply_event(_event(kind, moves="", status=status))
    window.online_session = session
    window._screen_mode = "review"
    window._refresh_online()

    assert not window._is_live_online_session()
    assert window._position_eval_context_available()
    assert window.finished_review_button.isHidden() is False
    window._update_clock()
    session.status = "unrecognizedFutureStatus"
    assert window._is_live_online_session()  # Unknown status stays fail-closed.
    assert not window._position_eval_context_available()
    window.close()


@pytest.mark.parametrize("status", sorted(TERMINAL_GAME_STATUSES))
def test_terminal_status_resolves_pending_service_action(qtbot, status):
    client = FakeGameClient()
    service = _service(client)
    generation = service.auth_generation
    service.mark_event_stream_ready(generation)
    assert _accept_snapshot(service, "terminal-action", generation=generation)

    service.resign("terminal-action")
    qtbot.waitUntil(lambda: client.posts == [("resign", "terminal-action")], timeout=1000)
    service.reconcile_game_actions(
        "terminal-action", _event(status=status), generation, color="white")
    assert not service.has_pending_action("terminal-action", "resign")
    service.close(timeout=1.0)
