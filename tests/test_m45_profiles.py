from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chess_coach.lichess.repository import GameRepository
from chess_coach.lichess.statistics import StatisticsService


def _add_account_game(repo, account_id, index, color, started_at, *, accuracy=85.0,
                      complete=True, time_control="300+0", label="good"):
    game_id = f"profile-{index}"
    moves = "e2e4" if color == "white" else "e2e4 e7e5"
    repo.save_snapshot(game_id, "startpos", moves, color, "Synthetic", False,
                       time_control, "finished", color, started_at=started_at)
    with repo._connection() as db:
        db.execute("UPDATE games SET source='lichess_archive',"
                   "lichess_accuracy_white=?,lichess_accuracy_black=? WHERE game_id=?",
                   (accuracy, accuracy + 2, game_id))
        db.execute("INSERT INTO game_phase_boundaries(game_id,line_revision,middle_start_ply,"
                   "end_start_ply,source,method_version) VALUES (?,1,3,NULL,'synthetic','test')",
                   (game_id,))
        db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,"
                   "player_rating,opponent_rating,opponent,player_name) VALUES (?,?,?,?,?,?,?,?,?)",
                   (account_id, game_id, game_id, datetime.now(timezone.utc).isoformat(),
                    color, 1500, 1500, "Synthetic", "Owner"))
    if complete:
        line = repo.current_line(game_id)
        count = len(moves.split())
        scores = [0]
        for ply in range(1, count + 1):
            mover = "white" if ply % 2 else "black"
            scores.append(scores[-1] + (-120 if label != "good" and mover == "white" else
                                        120 if label != "good" and mover == "black" else 0))
        assessments = [{"ply": ply, "white_cp": score, "depth": 1}
                       for ply, score in enumerate(scores)]
        classes = [{"ply": ply, "mover": "white" if ply % 2 else "black",
                    "eval_loss_cp": 120 if label != "good" else 0, "label": label, "phase": "opening",
                    "algorithm_version": "synthetic"}
                   for ply in range(1, count + 1)]
        repo.save_analysis(game_id, line_revision=line["line_revision"],
            line_hash=line["line_hash"], source="stockfish", engine_name="FakeFish",
            engine_version="fixture", parameters={"nodes": 1},
            assessments=assessments, classifications=classes, status="complete")
    return game_id


def test_profile_uses_90_day_complete_sample_per_color_and_keeps_denominators(tmp_path):
    repo = GameRepository(tmp_path / "profiles.sqlite3")
    try:
        account = repo.ensure_archive_account("ProfileOwner")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        for index in range(30):
            _add_account_game(repo, account, index, "white",
                (now - timedelta(days=index + 1)).isoformat(), accuracy=80.0)
        _add_account_game(repo, account, 40, "black", (now - timedelta(days=15)).isoformat(),
                          accuracy=90.0)
        # Undated games are retained in history but never silently enter recent windows.
        _add_account_game(repo, account, 41, "white", None, accuracy=70.0, complete=False)

        profile = StatisticsService(repo).personal_profile(
            account_id=account, clock="300+0", now=now)
        white = profile["color_segments"]["white"]
        black = profile["color_segments"]["black"]
        assert white["current_window"] == "90 дней"
        assert white["complete_games"] == 30
        assert not white["provisional"]
        assert white["current"]["games"] == 30
        assert white["current_official_accuracy"] == {
            "games": 30, "n": 30, "accuracy": 80.0, "source": "lichess"}
        assert white["current"]["avg_mistakes_per_analyzed_game"] == 0.0
        diagnostics = white["opening_phase_diagnostics"]
        assert diagnostics["focus_is_evidence_not_cause"]
        assert diagnostics["phases"][0]["phase"] == "opening"
        assert diagnostics["current_focus"] == []
        assert black["current_window"] == "12 месяцев (расширено)"
        assert black["provisional"]
        assert black["current"]["games"] == 1
        unknown = [row for row in profile["historical_timeline"] if row["period"] == "unknown"]
        assert len(unknown) == 1
        assert unknown[0]["games"] == 1
        assert unknown[0]["accuracy_n"] == 1
        assert unknown[0]["my_color"] == "white"
        assert all(row["opportunity_conversion"] is None for row in profile["historical_timeline"])
        assert all(row["opportunity_conversion_status"] == "no eligible denominator"
                   for row in profile["historical_timeline"])
    finally:
        repo.shutdown()


def test_current_focus_is_backed_by_opening_phase_and_move_evidence(tmp_path):
    repo = GameRepository(tmp_path / "focus.sqlite3")
    try:
        account = repo.ensure_archive_account("FocusOwner")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        for index in range(3):
            game_id = _add_account_game(repo, account, index, "white",
                (now - timedelta(days=index + 1)).isoformat(), label="mistake")
            with repo._connection() as db:
                db.execute("UPDATE games SET opening_eco='C20',opening_name='King Pawn' WHERE game_id=?",
                           (game_id,))
        profile = StatisticsService(repo).personal_profile(account_id=account, clock="300+0", now=now)
        diagnostics = profile["color_segments"]["white"]["opening_phase_diagnostics"]
        assert diagnostics["openings"][0]["eco"] == "C20"
        assert diagnostics["openings"][0]["games"] == 3
        assert diagnostics["openings"][0]["errors"] == 3
        # Three games with only three assessed own opening moves are evidence,
        # but too small to promote an opening-weakness hypothesis.
        assert not diagnostics["openings"][0]["hypothesis_eligible"]
        assert diagnostics["phases"][0]["errors"] == 3
        assert {(row["game_id"], row["ply"], row["label"]) for row in diagnostics["current_focus"]} == {
            (f"profile-{index}", 1, "mistake") for index in range(3)}
    finally:
        repo.shutdown()


def test_opportunity_conversion_stays_unknown_without_candidate_action_link(tmp_path):
    repo = GameRepository(tmp_path / "opportunity-conversion.sqlite3")
    try:
        account = repo.ensure_archive_account("OpportunityOwner")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        game_id = _add_account_game(repo, account, 1, "white",
            (now - timedelta(days=1)).isoformat(), label="mistake")
        with repo._connection() as db:
            run_id = db.execute("SELECT run_id FROM analysis_runs WHERE game_id=?", (game_id,)).fetchone()[0]
            db.execute("INSERT INTO analysis_events(event_key,run_id,game_id,line_revision,ply,event_type,"
                "confidence_state,source,detector_version,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("opportunity-candidate", run_id, game_id, 1, 1, "missed_opportunity", "candidate",
                 "stockfish", "synthetic", "{}"))
        row = StatisticsService(repo).historical_timeline(account_id=account)[0]
        assert row["opportunity_candidates"] == 1
        assert row["opportunity_conversion"] is None
        assert "no verified candidate-to-action linkage" in row["opportunity_conversion_status"]
    finally:
        repo.shutdown()


def test_period_comparison_requires_large_samples_and_similar_analysis_coverage(tmp_path):
    repo = GameRepository(tmp_path / "period-comparison.sqlite3")
    try:
        account = repo.ensure_archive_account("Comparator")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        for index in range(3):
            _add_account_game(repo, account, index,
                "white", (now - timedelta(days=index)).isoformat())
        service = StatisticsService(repo)
        result = service.compare_periods(
            account_id=account,
            current_since=(now - timedelta(days=30)).isoformat(), current_until=now.isoformat(),
            previous_since=(now - timedelta(days=60)).isoformat(),
            previous_until=(now - timedelta(days=30)).isoformat(), clock="300+0", side="white")
        assert not result["eligible_for_comparison"]
        assert result["differences"] is None
        assert "предварительное" in result["explanation"]
        mixed = service.compare_periods(
            account_id=account,
            current_since=(now - timedelta(days=30)).isoformat(), current_until=now.isoformat(),
            previous_since=(now - timedelta(days=60)).isoformat(),
            previous_until=(now - timedelta(days=30)).isoformat(), side="white")
        assert not mixed["eligible_for_comparison"]
        assert "контроль времени" in mixed["explanation"]
    finally:
        repo.shutdown()


def test_archive_import_records_date_precision_and_unknown_date_is_not_recent(tmp_path):
    repo = GameRepository(tmp_path / "date-precision.sqlite3")
    try:
        date_pgn = ('[Event "date-only"]\n[White "Me"]\n[Black "Friend"]\n'
                    '[Date "2026.10.01"]\n[Result "1-0"]\n\n1. e4 1-0\n')
        repo.import_archive_record("Me", {"id": "date-only", "pgn": date_pgn},
                                   checkpoint_ms=None, backfill=False)
        time_pgn = ('[Event "timestamp"]\n[White "Me"]\n[Black "Friend"]\n'
                    '[Result "1-0"]\n\n1. e4 1-0\n')
        repo.import_archive_record("Me", {"id": "timestamp", "pgn": time_pgn,
                                   "createdAt": 1790841600000},
                                   checkpoint_ms=None, backfill=False)
        repo.import_archive_record("Me", {"id": "unknown", "pgn": time_pgn},
                                   checkpoint_ms=None, backfill=False)
        assert repo.get_game("date-only")["game_time_precision"] == "date_only"
        assert repo.get_game("timestamp")["game_time_precision"] == "timestamp"
        assert repo.get_game("unknown")["game_time_precision"] == "unknown"
        with repo._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 16
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(ValueError, match="selected account"):
            StatisticsService(repo).personal_profile(account_id=None)
    finally:
        repo.shutdown()


def test_personal_profile_and_page_remain_bounded_on_10000_synthetic_games(tmp_path):
    repo = GameRepository(tmp_path / "profile-10000.sqlite3")
    try:
        account = repo.ensure_archive_account("ScaleOwner")
        now = datetime.now(timezone.utc)
        started = (now - timedelta(days=10)).isoformat()
        created = now.isoformat()
        games = []
        revisions = []
        links = []
        for index in range(10_000):
            game_id = f"scale-{index:05}"
            color = "white" if index % 2 == 0 else "black"
            games.append((game_id, "startpos", "", color, "Fixture", 0, "300+0",
                          "finished", color, created, "*", "Owner", "Fixture", started,
                          "timestamp", "lichess_archive", game_id, "standard", 1,
                          None, 1500, 1500, 80.0, 82.0))
            revisions.append((game_id, 1, "startpos", "", "*", f"scale-hash-{index}", created, 1))
            links.append((account, game_id, game_id, created, "Fixture", "Owner", color, 1500, 1500))
        with repo._connection() as db:
            db.executemany("INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                "winner,updated_at,pgn,white_player,black_player,started_at,game_time_precision,source,"
                "source_game_id,variant,line_revision,import_error,white_rating,black_rating,"
                "lichess_accuracy_white,lichess_accuracy_black) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                games)
            db.executemany("INSERT INTO game_revisions VALUES (?,?,?,?,?,?,?,?)", revisions)
            db.executemany("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,opponent,"
                "player_name,color,player_rating,opponent_rating) VALUES (?,?,?,?,?,?,?,?,?)", links)
        service = StatisticsService(repo)
        profile = service.personal_profile(account_id=account, now=now)
        assert profile["color_segments"]["white"]["current"]["games"] == 5_000
        assert profile["color_segments"]["black"]["current"]["games"] == 5_000
        page = service.game_page(account_id=account, page=0, page_size=50)
        assert page["total"] == 10_000 and len(page["items"]) == 50
        with repo._connection() as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        repo.shutdown()


def test_mixed_time_control_profile_is_provisional_and_reports_separate_buckets(tmp_path):
    repo = GameRepository(tmp_path / "mixed-time-controls.sqlite3")
    try:
        account = repo.ensure_archive_account("MixedControls")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        _add_account_game(repo, account, 1, "white", (now - timedelta(days=1)).isoformat(),
                          time_control="300+0")
        _add_account_game(repo, account, 2, "white", (now - timedelta(days=2)).isoformat(),
                          time_control="600+5")
        profile = StatisticsService(repo).personal_profile(account_id=account, now=now)
        white = profile["color_segments"]["white"]
        assert white["provisional"]
        assert "разные контроли" in white["provisional_reason"]
        assert {row["clock"] for row in profile["time_control_buckets"]} == {"300+0", "600+5"}
    finally:
        repo.shutdown()


def test_missing_comparison_position_is_unknown_not_complete_own_move_coverage(tmp_path):
    repo = GameRepository(tmp_path / "incomplete-comparison.sqlite3")
    try:
        account = repo.ensure_archive_account("CoverageOwner")
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        game_id = "coverage-missing-root"
        repo.save_snapshot(game_id, "startpos", "e2e4", "white", "Synthetic", False,
                           "300+0", "finished", "white", started_at=(now - timedelta(days=1)).isoformat())
        with repo._connection() as db:
            db.execute("INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,"
                       "player_rating,opponent_rating,opponent,player_name) VALUES (?,?,?,?,?,?,?,?,?)",
                       (account, game_id, game_id, now.isoformat(), "white", 1500, 1500, "Synthetic", "Owner"))
        line = repo.current_line(game_id)
        assert line
        repo.save_analysis(game_id, line_revision=1, line_hash=line["line_hash"],
            source="stockfish", engine_name="Fake", engine_version="fake-v1",
            parameters={"nodes": 1}, assessments=[{"ply": 1, "white_cp": 50}],
            classifications=[], status="complete")
        run_id = repo.list_analyses(game_id)[0]["run_id"]
        repo.materialize_analysis_metrics(run_id)
        service = StatisticsService(repo)
        summary = service.summary(account_id=account, clock="300+0", analysis_source="stockfish")
        assert summary["scored_positions"] == 1
        assert summary["evaluated_moves"] == 0
        assert summary["total_own_moves"] == 1
        assert summary["move_coverage_percent"] == 0.0
        with repo._connection() as db:
            metric = db.execute("SELECT cp_loss,severity FROM move_metrics WHERE game_id=?",
                                (game_id,)).fetchone()
            unknown_event = db.execute("SELECT confidence_state FROM analysis_events WHERE game_id=? "
                                       "AND event_type='outcome_unknown'", (game_id,)).fetchone()
        assert tuple(metric) == (None, "unknown")
        assert unknown_event[0] == "unknown"
        assert service._fully_covered_games(account, since="2026-10-01T00:00:00+00:00",
            until=now.isoformat(), clock="300+0", side="white", analysis_source="stockfish") == 0
        game = service.game_page(account_id=account, clock="300+0", analysis_source="stockfish")["items"][0]
        assert game["own_assessed_moves"] == 0 and game["total_own_moves"] == 1
    finally:
        repo.shutdown()
