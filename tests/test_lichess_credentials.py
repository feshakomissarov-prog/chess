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

    def stream_events(self, token, stop):
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
