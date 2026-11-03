from dataclasses import dataclass

import pytest

from chess_coach.application.game_sessions import GameSessionCoordinator


@dataclass
class FakeSession:
    game_id: str


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
