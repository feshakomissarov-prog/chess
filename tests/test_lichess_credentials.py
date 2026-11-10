from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLineEdit
from PySide6.QtTest import QSignalSpy

from chess_coach.lichess.api import HttpResponse, LichessClient, LichessError
from chess_coach.lichess.auth import (
    AuthorizationRequest,
    CredentialStorageError,
    WindowsCredentialStore,
    authorize_and_store,
)
from chess_coach.lichess.service import LichessService
from chess_coach.lichess.service_games import GAME_ACTION_CONFLICTS
from chess_coach.window import LichessConnectionDialog
from chess_coach import diagnostics


class MemoryStore:
    def __init__(self, value=None):
        self.value = value
        self.writes = []
        self.deletes = 0

    def get(self):
        return self.value

    def set(self, value):
        self.writes.append(value)
        self.value = value

    def delete(self):
        self.deletes += 1
        self.value = None


def _admit_live_game_actions(service, game_id, *, color="white", state=None):
    """Seed the service gate with a synthetic accepted gameFull snapshot."""
    generation = service.auth_generation
    assert service.mark_event_stream_ready(generation)
    snapshot_state = {"status": "started", "moves": "e2e4",
                      "wdraw": False, "bdraw": False}
    if state:
        snapshot_state.update(state)
    service.reconcile_game_actions(
        game_id, {"type": "gameFull", "state": snapshot_state}, generation,
        color=color, snapshot_sequence=1)
    sequence = service._game_authoritative_sequence[(generation, str(game_id))]
    assert service.authorize_game_actions(
        game_id, generation, sequence, color=color)


class AccountClient:
    def __init__(self, result=None, error=None):
        self.result = result or {"username": "TestUser"}
        self.error = error
        self.calls = []

    def get_account(self, token):
        self.calls.append(token)
        if self.error:
            raise self.error
        return self.result

    def stream_events(self, token, stop, on_connected=None):
        return iter(())


def join_worker(service):
    for worker in list(service._threads):
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_credential_store_keeps_oauth_and_personal_token_in_distinct_slots():
    class FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        def __init__(self):
            self.values = {}

        def get_password(self, service, user):
            return self.values.get((service, user))

        def set_password(self, service, user, value):
            self.values[(service, user)] = value

        def delete_password(self, service, user):
            if (service, user) not in self.values:
                raise self.errors.PasswordDeleteError()
            del self.values[(service, user)]

    keyring = FakeKeyring()
    oauth = WindowsCredentialStore(keyring)
    personal = WindowsCredentialStore(keyring, user=WindowsCredentialStore.PERSONAL_TOKEN_USER)
    oauth.set("fake-oauth-value")
    personal.set("fake-personal-value")
    assert oauth.get() == "fake-oauth-value"
    assert personal.get() == "fake-personal-value"
    personal.delete()
    assert oauth.get() == "fake-oauth-value"
    assert personal.get() is None


def test_oauth_account_403_does_not_persist_new_credential():
    class Callback:
        def wait(self, state, timeout):
            return "synthetic-code"

    class Client(AccountClient):
        def exchange_code(self, *args):
            return "synthetic-oauth-token"

    store = MemoryStore("older-token")
    client = Client(error=LichessError("denied", 403, "html"))
    request = AuthorizationRequest("https://lichess.test/oauth", "state", "verifier", "http://127.0.0.1/cb")
    with pytest.raises(LichessError) as error:
        authorize_and_store(client, store, Callback(), request)
    assert error.value.status == 403
    assert store.value == "older-token"
    assert store.writes == []


def test_oauth_requires_nonblank_account_identity_before_persisting():
    class Callback:
        def wait(self, state, timeout): return "synthetic-code"

    class Client(AccountClient):
        def exchange_code(self, *args): return "synthetic-oauth-token"

    store = MemoryStore()
    request = AuthorizationRequest("https://lichess.test/oauth", "state", "verifier", "http://127.0.0.1/cb")
    with pytest.raises(LichessError, match="имя аккаунта"):
        authorize_and_store(Client(result={"username": "   "}), store, Callback(), request)
    assert store.value is None
    assert store.writes == []


@pytest.mark.parametrize(("status", "body", "content_type", "expected"), [
    (403, b"<html>edge denied</html>", "text/html", "html"),
    (403, b'{"error":"denied"}', "application/json", "json"),
])
def test_account_403_is_classified_without_logging_response_content(
        status, body, content_type, expected):
    class Transport:
        calls = []

        def request(self, method, url, headers, payload, timeout):
            self.calls.append((method, url, dict(headers)))
            return HttpResponse(status, {"Content-Type": content_type}, body)

    transport = Transport()
    client = LichessClient(transport)
    with pytest.raises(LichessError) as error:
        client.get_account("fake-bearer-value")
    assert error.value.status == 403
    assert error.value.response_type == expected
    method, url, headers = transport.calls[0]
    assert method == "GET" and url == "https://lichess.org/api/account"
    assert headers["Authorization"] == "Bearer fake-bearer-value"
    assert "edge denied" not in str(error.value)


def test_personal_token_success_saves_only_after_account_validation_and_restores(qtbot, monkeypatch):
    oauth = MemoryStore("old-oauth-token")
    personal = MemoryStore()
    client = AccountClient()
    service = LichessService(client=client, token_store=oauth,
                             personal_token_store=personal, auth_method="personal_token")
    monkeypatch.setattr(service, "_start_event_stream", lambda _token: None)
    service.connect_personal_token("  synthetic-new-personal-token  ")
    join_worker(service)
    assert personal.value == "synthetic-new-personal-token"
    assert oauth.value == "old-oauth-token"
    assert service.username == "TestUser"
    assert client.calls == ["synthetic-new-personal-token"]

    restarted = LichessService(client=client, token_store=oauth,
                               personal_token_store=personal, auth_method="personal_token")
    monkeypatch.setattr(restarted, "_start_event_stream", lambda _token: None)
    restarted.restore()
    join_worker(restarted)
    assert restarted.username == "TestUser"
    assert client.calls[-1] == "synthetic-new-personal-token"
    service.close()
    restarted.close()


def test_restore_without_selected_credential_stays_local_and_makes_no_api_request(qtbot):
    class TrackingEmptyStore(MemoryStore):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def get(self):
            self.reads += 1
            return None

    store = TrackingEmptyStore()
    client = AccountClient()
    service = LichessService(client=client, token_store=store,
                             personal_token_store=MemoryStore(), auth_method="oauth")
    statuses = []
    service.session_status_changed.connect(lambda generation, value: statuses.append(value))
    service.restore()
    join_worker(service)
    qtbot.waitUntil(lambda: bool(statuses) and statuses[-1] == "Не подключено", timeout=1000)
    assert store.reads == 1
    assert client.calls == []
    assert service.username is None
    assert statuses[-1] == "Не подключено"
    service.close()


def test_restore_reads_credentials_off_the_calling_thread():
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(MemoryStore):
        def get(self):
            entered.set()
            assert release.wait(2)
            return None

    service = LichessService(client=AccountClient(), token_store=BlockingStore(),
                             personal_token_store=MemoryStore(), auth_method="oauth")
    service.restore()
    assert entered.wait(1)
    assert any(worker.name == "chess-coach-restore-auth" and worker.is_alive()
               for worker in service._threads)
    release.set()
    join_worker(service)
    service.close()


@pytest.mark.parametrize("status", [401, 403])
def test_invalid_personal_token_is_not_saved_and_never_connects(qtbot, status, monkeypatch):
    oauth = MemoryStore("untouched-oauth")
    personal = MemoryStore("previous-personal")
    service = LichessService(client=AccountClient(error=LichessError("denied", status, "json")),
                             token_store=oauth, personal_token_store=personal,
                             auth_method="personal_token")
    states = []
    service.status_changed.connect(states.append)
    service.connect_personal_token("synthetic-replacement")
    join_worker(service)
    qtbot.waitUntil(lambda: bool(states), timeout=1000)
    assert personal.value == "previous-personal"
    assert oauth.value == "untouched-oauth"
    assert service.username is None
    assert any(f"HTTP {status}" in value for value in states)
    service.close()


def test_personal_token_429_uses_client_cooldown_and_does_not_store():
    from chess_coach.lichess.api import RateLimitError

    personal = MemoryStore()
    client = AccountClient(error=RateLimitError(73))
    service = LichessService(client=client, token_store=MemoryStore(),
                             personal_token_store=personal, auth_method="personal_token")
    states = []
    service.status_changed.connect(states.append)
    service.connect_personal_token("synthetic-rate-limited-token")
    join_worker(service)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert personal.value is None
    assert states[-1].startswith("Ограничение Lichess (429)")
    service.close()


def test_connection_dialog_masks_pat_and_qsettings_never_contains_it(qtbot, tmp_path):
    dialog = LichessConnectionDialog("personal_token", personal_token_saved=True)
    qtbot.addWidget(dialog)
    assert dialog.personal_token_input.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.personal_token_input.text() == ""
    assert dialog.personal_saved_badge.text() == "Сохранён в Windows Credential Manager"
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("lichess/selected_auth_method", "personal_token")
    settings.sync()
    assert "synthetic" not in (tmp_path / "settings.ini").read_text(encoding="utf-8")
    submitted = QSignalSpy(dialog.personal_token_requested)
    dialog.personal_token_input.setText("synthetic-ui-token")
    dialog.personal_check_button.click()
    assert submitted.count() == 1
    assert submitted.at(0)[0] == "synthetic-ui-token"
    assert dialog.personal_token_input.text() == ""
    assert "synthetic-ui-token" not in (tmp_path / "settings.ini").read_text(encoding="utf-8")
    dialog.close()


def test_keyring_failure_has_no_plaintext_fallback():
    class BrokenKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        def set_password(self, *args):
            raise OSError("credential manager unavailable")

        def get_password(self, *args):
            raise OSError("credential manager unavailable")

    store = WindowsCredentialStore(BrokenKeyring())
    with pytest.raises(CredentialStorageError):
        store.set("synthetic-token")
    with pytest.raises(CredentialStorageError):
        store.get()


def test_late_pat_validation_after_auth_switch_does_not_save_or_connect():
    entered = threading.Event()
    release = threading.Event()

    class DelayedClient(AccountClient):
        def get_account(self, token):
            self.calls.append(token)
            entered.set()
            assert release.wait(2)
            return {"username": "WrongAccount"}

    personal = MemoryStore()
    service = LichessService(client=DelayedClient(), token_store=MemoryStore("oauth"),
                             personal_token_store=personal, auth_method="personal_token")
    service.connect_personal_token("synthetic-delayed-token")
    assert entered.wait(1)
    service.set_auth_method("oauth")
    release.set()
    join_worker(service)
    assert personal.value is None
    assert service.username is None
    service.close()


def test_late_restore_response_after_auth_switch_is_ignored():
    entered = threading.Event()
    release = threading.Event()

    class DelayedClient(AccountClient):
        def get_account(self, token):
            self.calls.append(token)
            entered.set()
            assert release.wait(2)
            return {"username": "OldAccount"}

    oauth = MemoryStore("old-oauth")
    service = LichessService(client=DelayedClient(), token_store=oauth,
                             personal_token_store=MemoryStore(), auth_method="oauth")
    service.restore()
    assert entered.wait(1)
    service.set_auth_method("personal_token")
    release.set()
    join_worker(service)
    assert service.username is None
    assert oauth.value == "old-oauth"
    service.close()


def test_pat_disconnect_and_local_delete_never_revoke_remote_token():
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.revocations = 0

        def revoke_token(self, token):
            self.revocations += 1

    client = Client()
    personal = MemoryStore("synthetic-pat")
    service = LichessService(client=client, token_store=MemoryStore("oauth"),
                             personal_token_store=personal, auth_method="personal_token")
    service.username = "TestUser"
    service.disconnect_account()
    join_worker(service)
    assert client.revocations == 0
    assert personal.value == "synthetic-pat"
    service.delete_saved_personal_token()
    assert client.revocations == 0
    assert personal.value is None
    service.close()


def test_disconnect_is_local_and_generation_switch_cannot_stop_new_same_name_session():
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.revocations = []

        def revoke_token(self, token):
            self.revocations.append(token)

    oauth = MemoryStore("oauth-b")
    personal = MemoryStore("pat-a")
    client = Client()
    service = LichessService(client=client, token_store=oauth,
                             personal_token_store=personal,
                             auth_method="personal_token")
    statuses = []
    service.session_status_changed.connect(
        lambda generation, status: statuses.append((generation, status)))
    service.username = "SameName"
    stale_generation = service.auth_generation
    old_stop = service._connection_stop

    service.disconnect_account()
    assert old_stop.is_set()
    assert personal.value == "pat-a"
    assert client.revocations == []

    service.set_auth_method("oauth")
    current_generation = service.auth_generation
    new_stop = service._connection_stop
    service.username = "SameName"
    assert not service._emit_session_status(stale_generation, "Не подключено")
    assert not new_stop.is_set()
    assert service.username == "SameName"
    assert statuses[-1] == (current_generation, "Не подключено")
    assert oauth.value == "oauth-b"
    service.close()


def test_stale_send_move_401_cannot_delete_or_stop_replacement_auth_session():
    entered = threading.Event()
    release = threading.Event()

    class DelayedMoveClient(AccountClient):
        def send_move(self, token, game_id, uci):
            self.calls.append((token, game_id, uci))
            entered.set()
            assert release.wait(2)
            raise LichessError("old credential rejected", 401, "json")

    oauth = MemoryStore("old-oauth")
    personal = MemoryStore("new-pat")
    client = DelayedMoveClient()
    service = LichessService(client=client, token_store=oauth,
                             personal_token_store=personal, auth_method="oauth")
    service.username = "AccountA"
    _admit_live_game_actions(service, "game-a")
    service.send_move("game-a", "e2e4", "move-request-a")
    old_worker = service._threads[-1]
    assert entered.wait(1)

    service.set_auth_method("personal_token")
    service.username = "AccountB"
    replacement_stop = service._connection_stop
    release.set()
    old_worker.join(timeout=2)
    assert not old_worker.is_alive()

    assert oauth.value == "old-oauth"
    assert personal.value == "new-pat"
    assert not replacement_stop.is_set()
    service.close()


def test_queued_game_action_does_not_read_replacement_account_token():
    queued = []
    calls = []

    class Client(AccountClient):
        def abort(self, token, game_id):
            calls.append((token, game_id))

    oauth = MemoryStore("account-a-token")
    personal = MemoryStore("account-b-token")
    service = LichessService(client=Client(), token_store=oauth,
                             personal_token_store=personal, auth_method="oauth")
    service.username = "AccountA"
    service._thread = lambda target, _name: queued.append(target)
    _admit_live_game_actions(service, "game-a")
    service.abort("game-a")
    service.set_auth_method("personal_token")
    service.username = "AccountB"

    queued.pop()()

    assert calls == []
    service.close()


@pytest.mark.parametrize("status", [403, 429])
def test_old_stream_error_after_switch_cannot_replace_new_connected_status(qtbot, status):
    entered = threading.Event()
    release = threading.Event()

    class Client(AccountClient):
        def stream_events(self, token, stop, on_connected=None):
            if token == "old-session":
                entered.set()
                assert release.wait(2)
                if status == 429:
                    from chess_coach.lichess.api import RateLimitError
                    raise RateLimitError(90)
                raise LichessError("old response", status, "html")
            entered_new.set()
            stop.wait(2)
            return
            yield  # pragma: no cover

    entered_new = threading.Event()
    service = LichessService(client=Client(), token_store=MemoryStore("oauth"),
                             personal_token_store=MemoryStore("pat"), auth_method="oauth")
    service.username = "SameName"
    statuses = []
    errors = []
    service.session_status_changed.connect(lambda gen, message: statuses.append((gen, message)))
    service.session_error.connect(lambda gen, message: errors.append((gen, message)))
    service._start_event_stream("old-session")
    old_worker = service._threads[-1]
    assert entered.wait(1)

    service.set_auth_method("personal_token")
    generation_b = service.auth_generation
    service.username = "SameName"
    service._start_event_stream("new-session", expected_generation=generation_b)
    assert entered_new.wait(1)
    assert statuses[-1] == (generation_b, "Подключено: SameName")

    release.set()
    qtbot.waitUntil(lambda: not old_worker.is_alive(), timeout=1000)
    assert service.is_auth_generation_current(generation_b)
    assert statuses[-1] == (generation_b, "Подключено: SameName")
    assert errors == []
    service.disconnect_account()
    join_worker(service)
    service.close()


def test_new_generation_can_restore_while_old_account_check_is_still_blocked(qtbot, monkeypatch):
    old_entered = threading.Event()
    release_old = threading.Event()
    authenticated = []

    class Client(AccountClient):
        def get_account(self, token):
            self.calls.append(token)
            if token == "old-oauth":
                old_entered.set()
                assert release_old.wait(2)
            return {"username": "SameName"}

    service = LichessService(client=Client(), token_store=MemoryStore("old-oauth"),
                             personal_token_store=MemoryStore("new-pat"), auth_method="oauth")
    monkeypatch.setattr(service, "_start_event_stream", lambda *_args, **_kwargs: None)
    service.session_authenticated.connect(
        lambda generation, username: authenticated.append((generation, username)))
    service.restore()
    assert old_entered.wait(1)

    service.set_auth_method("personal_token")
    new_generation = service.auth_generation
    service.restore()
    qtbot.waitUntil(lambda: authenticated == [(new_generation, "SameName")], timeout=1000)

    release_old.set()
    join_worker(service)
    assert authenticated == [(new_generation, "SameName")]
    assert service.username == "SameName"
    service.close()


def test_old_game_full_event_after_account_switch_is_discarded(qtbot):
    entered = threading.Event()
    release = threading.Event()
    new_stop_ref = []

    class Client(AccountClient):
        def stream_game(self, token, game_id, stop):
            entered.set()
            assert release.wait(2)
            yield {"type": "gameFull", "state": {"moves": "e2e4", "status": "started"}}

        def stream_events(self, token, stop, on_connected=None):
            new_stop_ref.append(stop)
            stop.wait(2)
            return
            yield  # pragma: no cover

    service = LichessService(client=Client(), token_store=MemoryStore("oauth"),
                             personal_token_store=MemoryStore("pat"), auth_method="oauth")
    received = []
    service.session_game_event_received.connect(
        lambda generation, game_id, event: received.append((generation, game_id, event)))
    service.start_game("same-game", "old-session")
    old_worker = service._threads[-1]
    assert entered.wait(1)
    service.set_auth_method("personal_token")
    generation_b = service.auth_generation
    service.username = "SameName"
    current_stop = service._connection_stop
    service._start_event_stream("new-session", expected_generation=generation_b)
    qtbot.waitUntil(lambda: bool(new_stop_ref), timeout=1000)

    release.set()
    qtbot.waitUntil(lambda: not old_worker.is_alive(), timeout=1000)
    assert received == []
    assert service._connection_stop is current_stop
    assert not current_stop.is_set()
    service.disconnect_account()
    join_worker(service)
    service.close()


def test_duplicate_game_action_is_posted_once_while_first_request_is_pending(qtbot):
    entered = threading.Event()
    release = threading.Event()

    class Client(AccountClient):
        def abort(self, token, game_id):
            self.calls.append((token, game_id))
            entered.set()
            assert release.wait(2)
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    started = []
    completed = []
    service.session_action_started.connect(lambda *args: started.append(args))
    service.session_action_completed.connect(lambda *args: completed.append(args))
    _admit_live_game_actions(service, "game-1")
    first_id = service.abort("game-1")
    second_id = service.abort("game-1")
    assert second_id == first_id
    assert entered.wait(1)
    assert client.calls == [("token", "game-1")]
    release.set()
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)
    assert len(started) == 1
    assert completed[0][2:] == ("abort", "game-1", 0)
    service.close(timeout=1.0)


def test_successful_game_action_stays_one_shot_until_authoritative_reconciliation(qtbot):
    class Client(AccountClient):
        def abort(self, token, game_id):
            self.calls.append((token, game_id))
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    _admit_live_game_actions(service, "game-uncertain")
    first_id = service.abort("game-uncertain")
    qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    second_id = service.abort("game-uncertain")
    qtbot.wait(40)
    assert second_id == first_id
    assert client.calls == [("token", "game-uncertain")]
    assert len(completed) == 1
    assert service.has_pending_action("game-uncertain", "abort")
    service.reconcile_game_actions("game-uncertain", {
        "type": "gameState", "status": "started", "moves": "e2e4"})
    assert service.has_pending_action("game-uncertain", "abort")
    service.reconcile_game_actions("game-uncertain", {
        "type": "gameState", "status": "aborted", "moves": ""})
    assert not service.has_pending_action("game-uncertain", "abort")
    service.close(timeout=1.0)


def test_friend_invitation_cancel_before_post_response_cancels_exact_server_id(qtbot):
    entered = threading.Event()
    release = threading.Event()

    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.cancelled = []
            self.accepted = []
        def create_open_challenge(self, token, color, rated, limit, increment):
            entered.set()
            assert release.wait(2)
            return {"id": "challenge-1", "timeControl": {
                "type": "clock", "limit": limit, "increment": increment},
                "rated": rated, "blackUrl": "https://lichess.org/join/challenge-1"}
        def cancel_challenge(self, token, challenge_id):
            self.cancelled.append((token, challenge_id))
        def accept_open_challenge(self, token, challenge_id, color):
            self.accepted.append((token, challenge_id, color))
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    ready = []
    service.session_challenge_ready.connect(lambda *args: ready.append(args))
    request_id = service.open_friend_game("white", 300, 2, False)
    assert entered.wait(1)
    assert service.cancel_pending_friend_challenge(request_id)
    release.set()
    qtbot.waitUntil(lambda: bool(ready), timeout=1500)
    assert ready[0][2] == {"id": "challenge-1", "cancelled": True}
    assert client.cancelled == [("token", "challenge-1")]
    assert client.accepted == []
    service.close(timeout=1.0)


def test_friend_invitation_cancel_during_accept_never_cancels_accepted_game(qtbot):
    accept_entered = threading.Event()
    release_accept = threading.Event()

    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.cancelled = []
        def create_open_challenge(self, token, color, rated, limit, increment):
            return {"id": "challenge-accepted", "timeControl": {
                "type": "clock", "limit": limit, "increment": increment},
                "rated": rated, "blackUrl": "https://lichess.org/join/challenge-accepted"}
        def accept_open_challenge(self, token, challenge_id, color):
            accept_entered.set()
            assert release_accept.wait(2)
            return {"id": "game-accepted"}
        def cancel_challenge(self, token, challenge_id):
            self.cancelled.append((token, challenge_id))

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    ready = []
    service.session_challenge_ready.connect(lambda *args: ready.append(args))
    request_id = service.open_friend_game("white", 300, 2, False)
    assert accept_entered.wait(1)
    assert service.cancel_pending_friend_challenge(request_id)
    release_accept.set()
    qtbot.waitUntil(lambda: bool(ready), timeout=1500)
    assert ready[0][2]["cancel_too_late"] is True
    assert client.cancelled == []
    service.close(timeout=1.0)


def test_friend_pending_cancel_429_is_retryable_and_never_claimed_success(qtbot):
    from chess_coach.lichess.api import RateLimitError
    create_entered, release_create = threading.Event(), threading.Event()

    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.create_count = 0
            self.cancel_count = 0
        def create_open_challenge(self, token, color, rated, limit, increment):
            self.create_count += 1
            create_entered.set()
            assert release_create.wait(2)
            return {"id": "challenge-429", "timeControl": {
                "type": "clock", "limit": limit, "increment": increment},
                "rated": rated, "blackUrl": "https://lichess.org/join/challenge-429"}
        def cancel_challenge(self, token, challenge_id):
            self.cancel_count += 1
            if self.cancel_count == 1:
                raise RateLimitError(60)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    failures, statuses = [], []
    service.session_operation_failed.connect(lambda *args: failures.append(args))
    service.session_challenge_status_changed.connect(lambda *args: statuses.append(args))
    request_id = service.open_friend_game("white", 300, 2, False)
    assert create_entered.wait(1)
    assert service.cancel_pending_friend_challenge(request_id)
    release_create.set()
    qtbot.waitUntil(lambda: bool(failures), timeout=1000)
    assert failures[0][4] == 429
    assert client.create_count == 1 and client.cancel_count == 1
    assert service.cancel_pending_friend_challenge(request_id)
    qtbot.waitUntil(lambda: any("отменено Lichess" in row[2] for row in statuses), timeout=1000)
    assert client.create_count == 1 and client.cancel_count == 2
    service.close(timeout=1.0)


def test_429_game_action_is_not_reposted_until_authoritative_effect(qtbot):
    from chess_coach.lichess.api import RateLimitError

    class Client(AccountClient):
        def __init__(self): super().__init__(); self.post_count = 0
        def resign(self, token, game_id):
            self.post_count += 1
            raise RateLimitError(60)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    reconciled = []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    service.session_action_reconciled.connect(lambda *args: reconciled.append(args))
    _admit_live_game_actions(service, "game-429")
    first_id = service.resign("game-429")
    qtbot.waitUntil(lambda: bool(completed), timeout=1000)
    for _ in range(3):
        assert service.resign("game-429") == first_id
    qtbot.wait(30)
    assert client.post_count == 1
    assert service.has_pending_action("game-429", "resign")
    service.reconcile_game_actions("game-429", {
        "type": "gameState", "status": "started", "moves": "e2e4"})
    assert service.has_pending_action("game-429", "resign")
    service.reconcile_game_actions("game-429", {
        "type": "gameState", "status": "resign", "moves": "e2e4"})
    assert not service.has_pending_action("game-429", "resign")
    qtbot.waitUntil(lambda: bool(reconciled), timeout=1000)
    service.close(timeout=1.0)


def test_draw_offer_and_acceptance_have_distinct_authoritative_effects(qtbot):
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.draw_posts = []
        def handle_draw(self, token, game_id, accept):
            self.draw_posts.append((token, game_id, accept))
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_action_completed.connect(lambda *args: completed.append(args))

    _admit_live_game_actions(service, "draw-game")
    offer_id = service.draw("draw-game", True)
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)
    assert client.draw_posts == [("token", "draw-game", True)]
    assert service.has_pending_action("draw-game", "offer_draw")
    service.reconcile_game_actions("draw-game", {
        "type": "gameState", "status": "started", "moves": "e2e4 e7e5", "wdraw": False,
        "bdraw": True})
    assert not service.has_pending_action("draw-game", "offer_draw")

    accept_id = service.draw("draw-game", True, accepting_offer=True)
    qtbot.waitUntil(lambda: len(completed) == 2, timeout=1000)
    assert accept_id != offer_id
    assert client.draw_posts == [("token", "draw-game", True), ("token", "draw-game", True)]
    assert service.has_pending_action("draw-game", "accept_draw")
    service.reconcile_game_actions("draw-game", {
        "type": "gameState", "status": "draw", "moves": "e2e4", "wdraw": True,
        "bdraw": True})
    assert not service.has_pending_action("draw-game", "accept_draw")
    service.close(timeout=1.0)


@pytest.mark.parametrize("outcome", ["http_ack", "rate_limited", "timeout"])
def test_draw_offer_pending_lifecycle_never_reposts_and_resolves_on_fresh_absence(
        qtbot, outcome):
    from chess_coach.lichess.api import LichessError

    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.draw_posts = []
        def handle_draw(self, token, game_id, accept):
            self.draw_posts.append((game_id, accept))
            if outcome == "rate_limited":
                raise LichessError("rate limited", 429)
            if outcome == "timeout":
                raise TimeoutError("synthetic timeout")
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    baseline = {"type": "gameState", "status": "started", "moves": "e2e4",
                "wdraw": False, "bdraw": False}
    _admit_live_game_actions(service, "draw-game", state=baseline)

    request_id = service.draw("draw-game", True)
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)
    assert service.has_pending_action("draw-game", "offer_draw")
    assert service.draw("draw-game", True) == request_id
    assert client.draw_posts == [("draw-game", True)]

    # A later active authoritative snapshot with no own-side offer clears the
    # unknown/acknowledged lifecycle. No timer or automatic POST retries it.
    service.reconcile_game_actions("draw-game", {
        "type": "gameState", "status": "started", "moves": "e2e4 e7e5",
        "wdraw": False, "bdraw": False})
    assert not service.has_pending_action("draw-game", "offer_draw")
    assert client.draw_posts == [("draw-game", True)]
    service.close(timeout=1.0)


@pytest.mark.parametrize("replays", [0, 1, 2])
def test_draw_offer_same_authoritative_fingerprint_replay_cannot_clear_pending(
        qtbot, replays):
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.posts = []
        def handle_draw(self, token, game_id, accept):
            self.posts.append((game_id, accept))
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    baseline = {"type": "gameState", "status": "started", "moves": "e2e4",
                "wdraw": False, "bdraw": False, "turns": 1}
    _admit_live_game_actions(service, "draw-game", state=baseline)
    request_id = service.draw("draw-game", True)
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)
    for _ in range(replays):
        service.reconcile_game_actions("draw-game", dict(baseline))
    assert service.has_pending_action("draw-game", "offer_draw")
    assert service.draw("draw-game", True) == request_id
    assert client.posts == [("draw-game", True)]
    service.close(timeout=1.0)


def test_two_fake_clients_exchange_draw_offer_continue_then_receive_later_offer(qtbot):
    class Client(AccountClient):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.posts = []
        def handle_draw(self, token, game_id, accept):
            self.posts.append((game_id, accept))

    client_a, client_b = Client("A"), Client("B")
    account_a = LichessService(client=client_a, token_store=MemoryStore("token-a"),
                               personal_token_store=MemoryStore("pat-a"))
    account_b = LichessService(client=client_b, token_store=MemoryStore("token-b"),
                               personal_token_store=MemoryStore("pat-b"))
    completed_a, completed_b = [], []
    account_a.session_action_completed.connect(lambda *args: completed_a.append(args))
    account_b.session_action_completed.connect(lambda *args: completed_b.append(args))
    game_id = "two-fake-clients"
    baseline = {"type": "gameFull", "state": {"status": "started", "moves": "e2e4",
                "wdraw": False, "bdraw": False, "turns": 1}}
    _admit_live_game_actions(account_a, game_id, color="white",
                             state=baseline["state"])
    _admit_live_game_actions(account_b, game_id, color="black",
                             state=baseline["state"])

    account_a.draw(game_id, True)
    qtbot.waitUntil(lambda: len(completed_a) == 1, timeout=1000)
    offered_by_a = {"type": "gameState", "status": "started", "moves": "e2e4",
                    "wdraw": True, "bdraw": False, "turns": 1}
    account_a.reconcile_game_actions(game_id, offered_by_a, color="white")
    account_b.reconcile_game_actions(game_id, offered_by_a, color="black")
    assert not account_a.has_pending_action(game_id, "offer_draw")
    assert offered_by_a["wdraw"] is True  # B's incoming offer is authoritative.

    continued = {"type": "gameState", "status": "started", "moves": "e2e4 e7e5",
                 "wdraw": False, "bdraw": False, "turns": 2}
    account_a.reconcile_game_actions(game_id, continued, color="white")
    account_b.reconcile_game_actions(game_id, continued, color="black")
    assert not account_a.has_pending_action(game_id, "offer_draw")

    account_b.draw(game_id, True)
    qtbot.waitUntil(lambda: len(completed_b) == 1, timeout=1000)
    offered_by_b = {"type": "gameState", "status": "started", "moves": "e2e4 e7e5",
                    "wdraw": False, "bdraw": True, "turns": 2}
    account_b.reconcile_game_actions(game_id, offered_by_b, color="black")
    account_a.reconcile_game_actions(game_id, offered_by_b, color="white")
    account_a.draw(game_id, True, accepting_offer=True)
    qtbot.waitUntil(lambda: len(completed_a) == 2, timeout=1000)
    accepted = {"type": "gameState", "status": "draw", "moves": "e2e4 e7e5",
                "wdraw": False, "bdraw": True, "turns": 2}
    account_a.reconcile_game_actions(game_id, accepted, color="white")

    assert client_a.posts == [(game_id, True), (game_id, True)]
    assert client_b.posts == [(game_id, True)]
    assert not account_a.has_pending_action(game_id, "accept_draw")
    account_a.close(timeout=1.0)
    account_b.close(timeout=1.0)


def test_incoming_draw_response_is_independent_of_stale_offer_but_not_opposite_response(
        qtbot):
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.draw_posts = []
        def handle_draw(self, token, game_id, accept):
            self.draw_posts.append((game_id, accept))
            return {}

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_action_completed.connect(lambda *args: completed.append(args))
    _admit_live_game_actions(service, "draw-game", color="white", state={
        "status": "started", "moves": "e2e4 e7e5",
        "wdraw": False, "bdraw": True})

    # A server-confirmed incoming offer must never turn POST /draw/yes into an
    # accidental acceptance. Only the explicit accept action may respond.
    offer_id = service.draw("draw-game", True)
    assert offer_id is None
    qtbot.wait(20)
    assert completed == []
    assert not service.has_pending_action("draw-game", "offer_draw")
    accept_id = service.draw("draw-game", True, accepting_offer=True)
    qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)
    assert accept_id
    assert client.draw_posts == [("draw-game", True)]
    assert service.draw("draw-game", False) is None
    assert client.draw_posts == [("draw-game", True)]
    service.close(timeout=1.0)


def test_game_action_conflict_table_preserves_only_explicit_safe_exceptions():
    all_actions = {"offer_draw", "accept_draw", "decline_draw", "resign", "abort",
                   "claim_draw", "claim_victory", "takeback"}
    assert set(GAME_ACTION_CONFLICTS) == all_actions
    assert "resign" not in GAME_ACTION_CONFLICTS["offer_draw"]
    assert {"accept_draw", "decline_draw"}.isdisjoint(
        GAME_ACTION_CONFLICTS["offer_draw"])
    for pending in all_actions - {"offer_draw"}:
        assert GAME_ACTION_CONFLICTS[pending] == all_actions


def test_pending_resign_atomically_rejects_conflicting_game_posts(qtbot):
    entered = threading.Event()
    release = threading.Event()

    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.posts = []
        def resign(self, token, game_id):
            self.posts.append(("resign", game_id))
            entered.set()
            assert release.wait(2)
        def handle_draw(self, token, game_id, accept):
            self.posts.append(("draw", game_id, accept))
        def claim_draw(self, token, game_id):
            self.posts.append(("claim_draw", game_id))
        def handle_takeback(self, token, game_id, accept):
            self.posts.append(("takeback", game_id, accept))

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    _admit_live_game_actions(service, "game")
    service.resign("game")
    assert entered.wait(1)
    assert service.draw("game", True) is None
    assert service.draw("game", True, accepting_offer=True) is None
    assert service.draw("game", False) is None
    assert service.claim_draw("game") is None
    completed = []
    service.session_takeback_action_completed.connect(lambda *args: completed.append(args))
    assert service.takeback("game", "offer", "takeback-1") is None
    assert client.posts == [("resign", "game")]
    assert completed and completed[-1][-1] == -2
    release.set()
    qtbot.waitUntil(lambda: len(client.posts) == 1, timeout=1000)
    assert service.has_pending_action("game", "resign")
    service.close(timeout=1.0)


def test_terminal_authoritative_state_rejects_takeback_without_post(qtbot):
    class Client(AccountClient):
        def __init__(self):
            super().__init__()
            self.posts = []
        def handle_takeback(self, token, game_id, accept):
            self.posts.append((game_id, accept))

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("token"),
                             personal_token_store=MemoryStore("pat"))
    completed = []
    service.session_takeback_action_completed.connect(lambda *args: completed.append(args))
    service.reconcile_game_actions("finished", {
        "type": "gameFull", "state": {"status": "draw", "moves": "e2e4 e7e5"}})

    assert service.takeback("finished", "offer", "late-takeback") is None
    assert completed and completed[-1][-1] == -2
    assert client.posts == []
    assert not service.has_pending_action("finished", "takeback")
    service.close(timeout=1.0)


def test_late_old_game_start_is_not_delivered_or_started(qtbot):
    entered = threading.Event()
    release = threading.Event()
    new_started = threading.Event()

    class Client(AccountClient):
        def stream_events(self, token, stop, on_connected=None):
            if token == "old-session":
                entered.set()
                assert release.wait(2)
                yield {"type": "gameStart", "game": {"gameId": "old-game"}}
                return
            new_started.set()
            stop.wait(2)
            return
            yield  # pragma: no cover

    service = LichessService(client=Client(), token_store=MemoryStore("oauth"),
                             personal_token_store=MemoryStore("pat"), auth_method="oauth")
    started_games = []
    events = []
    service.start_game = lambda *args, **kwargs: started_games.append((args, kwargs))
    service.session_event_received.connect(
        lambda generation, event: events.append((generation, event)))
    service._start_event_stream("old-session")
    old_worker = service._threads[-1]
    assert entered.wait(1)

    service.set_auth_method("personal_token")
    generation_b = service.auth_generation
    service.username = "SameName"
    service._start_event_stream("new-session", expected_generation=generation_b)
    assert new_started.wait(1)
    release.set()
    qtbot.waitUntil(lambda: not old_worker.is_alive(), timeout=1000)

    assert events == []
    assert started_games == []
    assert service.is_auth_generation_current(generation_b)
    service.disconnect_account()
    join_worker(service)
    service.close()


def test_explicit_oauth_revoke_does_not_delete_replacement_credential():
    entered = threading.Event()
    release = threading.Event()

    class DelayedClient(AccountClient):
        def __init__(self):
            super().__init__()
            self.revoked = []

        def revoke_token(self, token):
            self.revoked.append(token)
            entered.set()
            assert release.wait(2)

    oauth = MemoryStore("oauth-old")
    client = DelayedClient()
    service = LichessService(client=client, token_store=oauth,
                             personal_token_store=MemoryStore("pat"), auth_method="oauth")
    service.revoke_oauth_credential()
    assert entered.wait(1)
    oauth.set("oauth-new")
    with service._auth_lock:
        service._cancel_active_session()
    release.set()
    join_worker(service)
    assert client.revoked == ["oauth-old"]
    assert oauth.value == "oauth-new"
    service.close()


def test_account_denial_logs_no_token_body_or_account_data(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_HANDLER", None)
    diagnostics._LOGGER.handlers.clear()
    assert diagnostics.configure_logging(tmp_path)

    class Transport:
        def request(self, method, url, headers, payload, timeout):
            assert method == "GET"
            assert headers["Authorization"] == "Bearer synthetic-hidden-token"
            return HttpResponse(403, {"Content-Type": "application/json"},
                                b'{"error":"private-server-detail@example.test synthetic-hidden-token"}')

    with pytest.raises(LichessError):
        LichessClient(Transport()).get_account("synthetic-hidden-token")
    for handler in diagnostics._LOGGER.handlers:
        handler.flush()
    contents = (tmp_path / "chess-coach.log").read_text(encoding="utf-8")
    assert '"response_type":"json"' in contents
    assert '"method":"GET"' in contents
    assert '"route":"account"' in contents
    assert "synthetic-hidden-token" not in contents
    assert "private-server-detail" not in contents
    assert "example.test" not in contents
    diagnostics._LOGGER.handlers.clear()
    diagnostics._HANDLER = None


def test_rv2_transport_event_cannot_reconcile_unvalidated_position(qtbot):
    class Client(AccountClient):
        def resign(self, token, game_id):
            self.calls.append(game_id)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("fake"))
    done = []
    service.session_action_completed.connect(lambda *args: done.append(args))
    try:
        _admit_live_game_actions(service, "game")
        service.resign("game")
        qtbot.waitUntil(lambda: bool(done))
        service._emit_session_game_event(service.auth_generation, "game", {
            "type": "gameState", "status": "resign", "moves": "e2e5"})
        assert service.has_pending_action("game", "resign")
    finally:
        service.close(timeout=1)


def test_rv2_successful_friend_request_does_not_retain_credentials(qtbot):
    class Client(AccountClient):
        def create_open_challenge(self, *args):
            return {"id": "invite", "blackUrl": "https://lichess.org/invite",
                    "timeControl": {"type": "unlimited"}}
        def accept_open_challenge(self, *args):
            return {}

    service = LichessService(client=Client(), token_store=MemoryStore("fake"))
    ready = []
    service.session_challenge_ready.connect(lambda *args: ready.append(args))
    try:
        service.open_friend_game("white", None)
        qtbot.waitUntil(lambda: bool(ready))
        assert not service._pending_friend_challenges
        service._thread = lambda work, _name: work()
        for _ in range(100):
            service.open_friend_game("white", None)
        assert len(ready) == 101
        assert not service._pending_friend_challenges
    finally:
        service.close(timeout=1)


def test_rv2_stream_and_cancel_finish_when_both_threads_cross_barrier(qtbot):
    from concurrent.futures import ThreadPoolExecutor
    service = LichessService(client=AccountClient(), token_store=MemoryStore("fake"))
    context = service.capture_operation_context(command="create_friend_game")
    key = (context.generation, context.request_id)
    service._pending_friend_challenges[key] = {
        "context": context, "phase": "creating", "cancel_event": threading.Event(),
    }
    barrier = threading.Barrier(2)

    class BoundedLock:
        def __init__(self, lock, first_thread):
            self.lock = lock
            self.first_thread = first_thread

        def __enter__(self):
            if not self.lock.acquire(timeout=1):
                raise TimeoutError("auth/pending lock inversion")
            if threading.current_thread().name == self.first_thread:
                self.first_thread = None
                try:
                    barrier.wait(2)
                except BaseException:
                    self.lock.release()
                    raise
            return self

        def __exit__(self, *_):
            self.lock.release()

    service._auth_lock = BoundedLock(threading.RLock(), "rv2-stream")
    service._pending_action_lock = BoundedLock(threading.Lock(), "rv2-cancel")

    def stream():
        threading.current_thread().name = "rv2-stream"
        return service._emit_session_game_event(
            context.generation, "synthetic",
            {"type": "gameState", "status": "started", "moves": ""})

    def cancel_pending():
        threading.current_thread().name = "rv2-cancel"
        return service.cancel_pending_friend_challenge(context.request_id, context.generation)

    heartbeat = []
    from PySide6.QtCore import QTimer
    with ThreadPoolExecutor(max_workers=2) as pool:
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        cancel = pool.submit(cancel_pending)
        event = pool.submit(stream)
        qtbot.waitUntil(lambda: bool(heartbeat) and cancel.done() and event.done(), timeout=2500)
        assert cancel.result() and event.result()
    service.close(timeout=1)


def test_rv2_partial_friend_accept_429_does_not_repeat_create_or_cancel(qtbot):
    from chess_coach.lichess.api import RateLimitError
    class Client(AccountClient):
        def create_open_challenge(self, *args):
            self.calls.append("create")
            return {"id": "invite", "blackUrl": "https://lichess.org/invite",
                    "timeControl": {"type": "unlimited"}}
        def accept_open_challenge(self, *args):
            self.calls.append("accept")
            raise RateLimitError(60)
        def cancel_challenge(self, *args):
            self.calls.append("cancel")

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("fake"))
    try:
        with qtbot.waitSignal(service.session_operation_failed):
            request_id = service.open_friend_game("white", None)
        generation = service.auth_generation
        assert service.open_friend_game("white", None) == request_id
        assert service.friend_request_phase(request_id, generation) == "accept_unknown"
        assert not service.cancel_pending_friend_challenge(request_id, generation)
        assert client.calls == ["create", "accept"]
        service._cancel_active_session()
        assert not service._pending_friend_challenges
    finally:
        service.close(timeout=1)


def test_rv2_pre_id_unknown_is_retained_until_explicit_session_reset(qtbot):
    from chess_coach.lichess.api import RateLimitError
    class Client(AccountClient):
        def create_open_challenge(self, *args):
            self.calls.append("create")
            raise RateLimitError(60)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("fake"))
    try:
        with qtbot.waitSignal(service.session_operation_failed):
            request_id = service.open_friend_game("white", None)
        assert service.open_friend_game("white", None) == request_id
        assert service.friend_request_phase(request_id, service.auth_generation) == "unknown"
        assert not service.cancel_pending_friend_challenge(request_id)
        assert client.calls == ["create"]
        service._cancel_active_session()
        assert not service._pending_friend_challenges
    finally:
        service.close(timeout=1)


def test_rv2_friend_partial_success_rolls_back_only_after_definitive_rejection(qtbot):
    from chess_coach.lichess.api import LichessError, RateLimitError

    class Client(AccountClient):
        def create_open_challenge(self, *args):
            self.calls.append("create")
            return {"id": "exact-id", "blackUrl": "https://lichess.org/invite",
                    "timeControl": {"type": "unlimited"}}
        def accept_open_challenge(self, *args):
            self.calls.append("accept")
            raise LichessError("synthetic forbidden", status=403)
        def cancel_challenge(self, token, challenge_id):
            self.calls.append(("cancel", challenge_id))
            if self.calls.count(("cancel", challenge_id)) == 1:
                raise RateLimitError(60)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("fake"))
    try:
        with qtbot.waitSignal(service.session_operation_failed) as failure:
            request_id = service.open_friend_game("white", None)
        assert failure.args[4] == 429
        assert service.open_friend_game("white", None) == request_id
        with qtbot.waitSignal(service.session_challenge_status_changed) as status:
            assert service.cancel_pending_friend_challenge(request_id)
        assert status.args[3] is False
        assert client.calls == ["create", "accept", ("cancel", "exact-id"), ("cancel", "exact-id")]
        assert not service._pending_friend_challenges
    finally:
        service.close(timeout=1)


def test_rv2_authoritative_offer_during_http_is_retained_until_completion(qtbot):
    entered, release = threading.Event(), threading.Event()

    class Client(AccountClient):
        def handle_draw(self, token, game_id, accept):
            self.calls.append(game_id)
            entered.set()
            assert release.wait(3)

    client = Client()
    service = LichessService(client=client, token_store=MemoryStore("fake"))
    try:
        _admit_live_game_actions(service, "draw")
        request_id = service.draw("draw", True)
        assert entered.wait(1)
        service.reconcile_game_actions("draw", {
            "type": "gameState", "status": "started", "moves": "", "bdraw": True},
            color="black")
        service.reconcile_game_actions("draw", {
            "type": "gameState", "status": "started", "moves": "e2e4"}, color="black")
        assert service.draw("draw", True) == request_id
        with qtbot.waitSignal(service.session_action_reconciled):
            release.set()
        assert client.calls == ["draw"]
        assert not service.has_pending_action("draw", "offer_draw")
    finally:
        release.set()
        service.close(timeout=1)
