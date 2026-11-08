from chess_coach.application.app_state import (
    ReviewResumeTarget,
    WorkspaceSnapshot,
    WorkspaceStateStore,
)


class FakeSettings:
    def __init__(self):
        self.values = {}
        self.synced = 0

    def value(self, key, defaultValue=None, *, type=None):
        value = self.values.get(key, defaultValue)
        return type(value) if type is not None and value is not None else value

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def sync(self):
        self.synced += 1


def test_workspace_store_round_trips_review_identity_and_ui_preferences():
    settings = FakeSettings()
    store = WorkspaceStateStore(settings)
    expected_review = ReviewResumeTarget("game-1", 4, "line-hash", 7, 2)
    store.save(WorkspaceSnapshot(
        route="games", geometry=b"geometry", sidebar_collapsed=True,
        sidebar_explicit=True, board_target_size=560, review=expected_review))

    assert store.last_route() == "games"
    assert store.load_review_target() == expected_review
    assert settings.values["workspace/window_geometry"] == b"geometry"
    assert settings.values["workspace/sidebar_collapsed"] is True
    assert settings.values["workspace/sidebar_explicit"] is True
    assert settings.values["workspace/board_target_size"] == 560
    assert settings.synced == 1


def test_workspace_store_does_not_erase_saved_review_when_saving_other_route():
    settings = FakeSettings()
    store = WorkspaceStateStore(settings)
    review = ReviewResumeTarget("game-2", 1, "hash-2", 3, 0)
    store.save(WorkspaceSnapshot("games", review=review))
    store.save(WorkspaceSnapshot("home"))
    assert store.load_review_target() == review


def test_workspace_store_removes_only_resume_game_key():
    settings = FakeSettings()
    store = WorkspaceStateStore(settings)
    store.save(WorkspaceSnapshot("games", review=ReviewResumeTarget("game-3", 2, "h", 1, 0)))
    store.remove_review_target()
    assert store.load_review_target() is None
    assert settings.values["workspace/review_line_revision"] == 2
