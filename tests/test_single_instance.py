from chess_coach.single_instance import acquire_single_instance


def test_same_profile_second_process_activates_existing_window(qtbot, tmp_path):
    activations = []
    primary = acquire_single_instance(tmp_path / "profile")
    assert primary is not None
    primary.set_activation_callback(lambda: activations.append("activated"))

    assert acquire_single_instance(tmp_path / "profile") is None
    qtbot.waitUntil(lambda: activations == ["activated"], timeout=1000)
    primary.close()


def test_distinct_profiles_can_run_independently(qtbot, tmp_path):
    first = acquire_single_instance(tmp_path / "one")
    second = acquire_single_instance(tmp_path / "two")
    assert first is not None
    assert second is not None
    second.close()
    first.close()
