from dataclasses import dataclass

import pytest

from chess_coach.application.game_sessions import GameSessionCoordinator


@dataclass
class FakeSession:
    game_id: str
    owner_username: str | None = None
    auth_generation: int | None = None
    detached: bool = False

    def belongs_to(self, username, *, generation=None):
        return (not self.detached and self.owner_username == username
                and (generation is None or self.auth_generation == generation))


def test_background_registration_never_changes_selected_game():
    coordinator = GameSessionCoordinator[FakeSession]()
    first = FakeSession("visible-game")
    background = FakeSession("background-game")
    coordinator.register(first, select=True)
    coordinator.register(background)
    assert coordinator.selected_game_id == "visible-game"
    assert coordinator.get("background-game") is background
    assert not coordinator.is_selected("background-game")


def test_selection_requires_a_registered_session_and_clear_keeps_registry():
    coordinator = GameSessionCoordinator[FakeSession]()
    session = FakeSession("game")
    assert coordinator.select("missing") is None
    assert coordinator.selected_game_id is None
    coordinator.register(session)
    assert coordinator.select("game") is session
    coordinator.clear_selection()
    assert coordinator.selected_game_id is None
    assert coordinator.get("game") is session


def test_game_id_cannot_be_owned_by_two_session_instances():
    coordinator = GameSessionCoordinator[FakeSession]()
    coordinator.register(FakeSession("game"))
    with pytest.raises(ValueError):
        coordinator.register(FakeSession("game"), select=True)


def test_empty_game_id_is_rejected():
    with pytest.raises(ValueError):
        GameSessionCoordinator[FakeSession]().register(FakeSession(""))


def test_session_registry_isolates_account_and_auth_generation():
    coordinator = GameSessionCoordinator[FakeSession]()
    account_a = FakeSession("same-id", "account-a", 4)
    account_b = FakeSession("other-id", "account-b", 5)
    coordinator.register(account_a)
    coordinator.register(account_b)
    assert coordinator.for_owner("account-a", generation=4) == (account_a,)
    assert coordinator.for_owner("account-a", generation=5) == ()
    assert coordinator.for_owner("account-b", generation=5) == (account_b,)


def test_detached_game_id_can_be_replaced_without_reusing_old_state():
    coordinator = GameSessionCoordinator[FakeSession]()
    old = FakeSession("same-id", "account-a", 4)
    coordinator.register(old)
    old.detached = True
    replacement = FakeSession("same-id", "account-b", 5)
    coordinator.register(replacement)
    assert coordinator.get("same-id") is replacement
    assert coordinator.for_owner("account-a", generation=4) == ()
