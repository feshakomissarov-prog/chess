import threading

from chess_coach.application import background_work
from chess_coach.application.background_work import (
    BackgroundWorkCoordinator,
    OperationLane,
    OperationStatus,
)


def test_background_network_task_does_not_block_interactive_lane(monkeypatch):
    monkeypatch.setattr(background_work, "log_event", lambda *_args, **_kwargs: None)
    coordinator = BackgroundWorkCoordinator()
    background_started = threading.Event()
    allow_background_finish = threading.Event()
    interactive_started = threading.Event()

    background = coordinator.start(
        lambda: (background_started.set(), allow_background_finish.wait(3)),
        kind="archive_sync", lane=OperationLane.NETWORK_BACKGROUND)
    assert background_started.wait(1)
    interactive = coordinator.start(
        interactive_started.set,
        kind="send_move", lane=OperationLane.INTERACTIVE)
    try:
        assert interactive_started.wait(1)
        assert background.is_alive()
        assert interactive.snapshot.lane is OperationLane.INTERACTIVE
        assert interactive.snapshot.context.priority > background.snapshot.context.priority
    finally:
        allow_background_finish.set()
        background.join(2)
        interactive.join(2)
        assert coordinator.close(1)
    assert background.snapshot.status is OperationStatus.COMPLETED
    assert interactive.snapshot.status is OperationStatus.COMPLETED


def test_operation_failure_and_progress_are_visible_in_registry(monkeypatch):
    monkeypatch.setattr(background_work, "log_event", lambda *_args, **_kwargs: None)
    coordinator = BackgroundWorkCoordinator()
    started = threading.Event()

    def fail():
        started.set()
        raise ValueError("synthetic failure")

    handle = coordinator.start(fail, kind="fixture", lane=OperationLane.SAFETY)
    handle.update_progress(3, 5)
    assert started.wait(1)
    handle.join(1)
    snapshot = handle.snapshot
    assert snapshot.status is OperationStatus.FAILED
    assert snapshot.failure_code == "ValueError"
    assert (snapshot.progress_done, snapshot.progress_total) == (3, 5)
    assert coordinator.snapshots() == ()
    assert coordinator.close(1)


def test_cancel_sets_supplied_cooperative_event_and_records_reason(monkeypatch):
    monkeypatch.setattr(background_work, "log_event", lambda *_args, **_kwargs: None)
    coordinator = BackgroundWorkCoordinator()
    stop = threading.Event()
    started = threading.Event()

    def cancellable():
        started.set()
        stop.wait(2)

    handle = coordinator.start(cancellable, kind="sync", lane=OperationLane.NETWORK_BACKGROUND,
                               stop_event=stop)
    assert started.wait(1)
    assert handle.cancel("account_switched")
    handle.join(1)
    assert stop.is_set()
    assert handle.snapshot.status is OperationStatus.CANCELLED
    assert handle.snapshot.cancellation_reason == "account_switched"
    assert coordinator.close(1)


def test_structured_operation_log_uses_random_correlation_id_and_omits_account(monkeypatch):
    recorded = []
    monkeypatch.setattr(background_work, "log_event",
                        lambda event, component, **fields:
                        recorded.append((event, component, fields)))
    coordinator = BackgroundWorkCoordinator()
    handle = coordinator.start(lambda: None, kind="safe_sync",
                               lane=OperationLane.NETWORK_BACKGROUND,
                               account_id="private-account-name")
    handle.join(1)
    assert coordinator.close(1)
    assert recorded
    for _event, component, fields in recorded:
        assert component == "background_work"
        assert fields["operation_id"] == handle.operation_id
        assert "account_id" not in fields
        assert "private-account-name" not in repr(fields)


def test_legacy_operation_names_are_mapped_to_safe_static_categories():
    name = "chess-coach-game-private-game-id-123"
    assert BackgroundWorkCoordinator.lane_for_legacy_name(name) is OperationLane.STREAM
    assert BackgroundWorkCoordinator.kind_for_legacy_name(name) == "lichess_game_stream"
    assert "private-game-id" not in BackgroundWorkCoordinator.kind_for_legacy_name(name)
