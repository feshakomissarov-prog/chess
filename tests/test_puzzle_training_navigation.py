from chess_coach.lichess import puzzle_training


def test_next_local_candidate_visits_three_ids_before_repeat():
    rows = [("p1", {}, "local"), ("p2", {}, "local"), ("p3", {}, "local")]
    selector = getattr(puzzle_training, "select_next_local_candidate", None)
    assert callable(selector), "local training selection needs a seen-aware candidate selector"
    seen = set()
    selected = []
    for _ in rows:
        candidate = selector(rows, seen)
        assert candidate is not None
        selected.append(candidate[0])
        seen.add(candidate[0])
    assert selected == ["p1", "p2", "p3"]
    assert select_next_local_candidate(rows, seen) is None


def test_next_local_candidate_skips_seen_ids_and_preserves_candidate_order():
    rows = [("p1", {}, "local"), ("p2", {}, "local"), ("p3", {}, "local")]
    selector = getattr(puzzle_training, "select_next_local_candidate", None)
    assert callable(selector), "local training selection needs a seen-aware candidate selector"
    assert selector(rows, {"p1", "p2"}) == rows[2]
