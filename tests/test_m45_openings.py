from __future__ import annotations

import sqlite3

import chess

from chess_coach.lichess.opening_matcher import (
    DATASET_REVISION, OpeningMatcher, _dataset_digest, epd_key,
)
from chess_coach.lichess.phases import PHASE_FALLBACK_VERSION, infer_phase_boundaries, phase_at_ply
from chess_coach.lichess.repository import GameRepository, SCHEMA_VERSION
from chess_coach.lichess.statistics import StatisticsService


def test_bundled_cc0_dataset_matches_last_known_position_and_keeps_provenance(tmp_path):
    repo = GameRepository(tmp_path / "openings.sqlite3")
    try:
        # Main line includes 1.e4, after which the local CC0 database has a named match.
        repo.save_snapshot("opening-game", "startpos", "e2e4 e7e5 g1f3 b8c6 f1b5", "white",
                           "Friend", False, "300+0", "finished", "white")
        matcher = OpeningMatcher()
        match = matcher.match_game(repo, "opening-game")
        assert matcher.position_count > 3000
        assert match is not None
        assert match["dataset_revision"] == DATASET_REVISION
        assert match["matched_ply"] == 5
        saved = repo.opening_match("opening-game")
        assert saved["eco"] == match["eco"]
        assert saved["lichess_conflict"] == 0
        # Matching is idempotent and leaves the imported Lichess opening fields alone.
        repo.save_snapshot("opening-game", "startpos", "e2e4 e7e5 g1f3 b8c6 f1b5", "white",
                           "Friend", False, "300+0", "finished", "white")
        assert repo.get_game("opening-game")["opening_eco"] is None
        assert matcher.match_game(repo, "opening-game")["matched_ply"] == 5
        assert StatisticsService(repo).summary(opening=match["eco"])["games"] == 1
    finally:
        repo.shutdown()


def test_opening_dataset_checksum_is_stable_across_windows_line_endings():
    files_lf = [("a.tsv", b"eco\tname\tpgn\nA00\tFixture\t1. e4\n"),
                ("b.tsv", b"eco\tname\tpgn\nB00\tFixture\t1. d4\n")]
    files_crlf = [(name, content.replace(b"\n", b"\r\n")) for name, content in files_lf]
    assert _dataset_digest(files_lf) == _dataset_digest(files_crlf)


def test_epd_key_supports_transposed_lines_and_legal_en_passant_identity():
    first = chess.Board()
    for san in ("Nf3", "d5", "d4", "Nf6", "c4", "e6"):
        first.push_san(san)
    second = chess.Board()
    for san in ("d4", "Nf6", "Nf3", "e6", "c4", "d5"):
        second.push_san(san)
    assert epd_key(first) == epd_key(second)
    # The matcher sees both histories as one transposition because its reference
    # index is keyed by normalized EPD rather than the move sequence.
    matcher = OpeningMatcher.__new__(OpeningMatcher)
    matcher._index = {epd_key(first): [("A00", "Synthetic transposition", 6,
                                       "Synthetic", None)]}
    left = matcher.match_boards("left", 1, [first])
    right = matcher.match_boards("right", 1, [second])
    assert left["eco"] == right["eco"] == "A00"
    # A non-capturable FEN en-passant marker is normalized away as required by EPD.
    no_ep = chess.Board("4k3/8/8/P2p4/8/8/8/4K3 w - - 0 1")
    ghost_ep = chess.Board("4k3/8/8/P2p4/8/8/8/4K3 w - d6 0 1")
    assert epd_key(no_ep) == epd_key(ghost_ep)


def test_opening_conflict_is_visible_and_statistics_are_grouped_by_owner_color(tmp_path):
    repo = GameRepository(tmp_path / "opening-conflict.sqlite3")
    try:
        repo.save_snapshot("conflict", "startpos", "e2e4", "white", "Friend", False,
                           "300+0", "finished", "white")
        with repo._connection() as db:
            db.execute("UPDATE games SET opening_eco='Z99',opening_name='Lichess label' "
                       "WHERE game_id='conflict'")
        matcher = OpeningMatcher()
        result = matcher.match_game(repo, "conflict")
        assert result and result["eco"] != "Z99"
        assert repo.opening_match("conflict")["lichess_conflict"] == 1
        rows = StatisticsService(repo).opening_statistics()
        assert len(rows) == 1
        assert rows[0]["my_color"] == "white"
        assert rows[0]["opening_provenance"] == "chess-coach-cc0"
        assert rows[0]["opening_conflict"] == 1
        assert rows[0]["lichess_opening_eco"] == "Z99"
        repo.save_snapshot("lichess-only", "startpos", "d2d4", "white", "Friend", False,
                           "300+0", "finished", "white")
        with repo._connection() as db:
            db.execute("UPDATE games SET opening_eco='D00',opening_name='Lichess Queen Pawn' "
                       "WHERE game_id='lichess-only'")
        lichess_only = next(row for row in StatisticsService(repo).opening_statistics()
                            if row["eco"] == "D00")
        assert lichess_only["opening_provenance"] == "lichess"
        assert lichess_only["opening_dataset_revision"] is None
        line = repo.current_line("conflict")
        repo.save_analysis("conflict", line_revision=1, line_hash=line["line_hash"],
            source="stockfish-fast", engine_name="fake", engine_version="1",
            parameters={"nodes": 1}, assessments=[{"ply": 0, "white_cp": 0},
                                                   {"ply": 1, "white_cp": -120}],
            classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 80,
                              "label": "mistake", "phase": "opening",
                              "algorithm_version": "test"}])
        with repo._connection() as db:
            db.execute("INSERT INTO game_phase_boundaries(game_id,line_revision,middle_start_ply,"
                       "end_start_ply,source,method_version) VALUES ('conflict',1,2,NULL,'synthetic','test')")
        by_source = StatisticsService(repo).opening_statistics(analysis_source="stockfish-fast")
        assert by_source[0]["analysis_source"] == "stockfish-fast"
        assert by_source[0]["evaluated_own_moves"] == 1
        assert by_source[0]["own_errors_per_100_evaluated"] == 100.0
        assert by_source[0]["mean_opening_cpl_cp"] == 120
    finally:
        repo.shutdown()


def test_phase_fallback_uses_board_facts_and_lichess_division_wins(tmp_path):
    # Construct positions by replaying a legal, synthetic line with queen exchange.
    board = chess.Board("4k3/8/8/8/8/8/4K3/8 w - - 0 1")
    boards = [board.copy()]
    board.push_san("Ke3")
    boards.append(board.copy())
    middle, end = infer_phase_boundaries(boards)
    assert middle is not None and middle <= len(boards) - 1

    repo = GameRepository(tmp_path / "phase-fallback.sqlite3")
    try:
        pgn = ('[Event "synthetic"]\n[White "Me"]\n[Black "Friend"]\n'
               '[Result "1-0"]\n[SetUp "1"]\n'
               '[FEN "4k3/8/8/8/8/8/4K3/8 w - - 0 1"]\n\n1. Ke3 1-0\n')
        repo.import_archive_record("Me", {"id": "fallback", "pgn": pgn},
                                   checkpoint_ms=None, backfill=False)
        with repo._connection() as db:
            phase = db.execute("SELECT * FROM game_phase_boundaries WHERE game_id='fallback'").fetchone()
        assert phase["source"] == "material-fallback"
        assert phase["method_version"] == PHASE_FALLBACK_VERSION
        assert repo.phase_boundaries("fallback", 1)["source"] == "material-fallback"
        line = repo.current_line("fallback")
        repo.save_analysis("fallback", line_revision=1, line_hash=line["line_hash"],
            source="stockfish", engine_name="fake", engine_version="1",
            parameters={"nodes": 1}, assessments=[{"ply": 1, "white_cp": 0}],
            classifications=[{"ply": 1, "mover": "white", "label": "good",
                              "phase": "unknown", "algorithm_version": "test"}])
        # Explicit server division replaces the fallback for a subsequent import.
        repo.import_archive_record("Me", {"id": "fallback", "pgn": pgn,
            "division": {"middle": {"begin": 5}, "end": {"begin": 6}}},
            checkpoint_ms=None, backfill=False)
        with repo._connection() as db:
            phase = db.execute("SELECT * FROM game_phase_boundaries WHERE game_id='fallback'").fetchone()
        assert phase["source"] == "lichess"
        assert phase["middle_start_ply"] == 5
        with repo._connection() as db:
            label = db.execute("SELECT phase FROM move_classifications WHERE game_id='fallback' ").fetchone()[0]
        assert label == "opening"
    finally:
        repo.shutdown()


def test_phase_boundaries_handle_unknown_end_and_late_hydration(tmp_path):
    assert phase_at_ply(4, None, 3) == "opening"
    assert phase_at_ply(4, None, 4) == "middlegame"
    assert phase_at_ply(4, None, 99) == "middlegame"
    assert phase_at_ply(None, 6, 5) == "unknown"
    assert phase_at_ply(None, 6, 6) == "endgame"
    assert phase_at_ply(None, None, 4) == "unknown"
    assert phase_at_ply(1, 1, 1) == "endgame"

    repo = GameRepository(tmp_path / "late-phase-hydration.sqlite3")
    try:
        repo.save_snapshot("phase-late", "startpos", "e2e4 e7e5", "white", "Friend",
                           False, "300+0", "finished", "white")
        line = repo.current_line("phase-late")
        repo.save_analysis("phase-late", line_revision=1, line_hash=line["line_hash"],
            source="stockfish", engine_name="synthetic", engine_version="1",
            parameters={"nodes": 100}, assessments=[{"ply": 0, "white_cp": 0},
                                                   {"ply": 1, "white_cp": 100},
                                                   {"ply": 2, "white_cp": 100}],
            classifications=[{"ply": 1, "mover": "white", "label": "good",
                              "algorithm_version": "synthetic"},
                             {"ply": 2, "mover": "black", "label": "good",
                              "algorithm_version": "synthetic"}])
        assert repo.phase_for_ply("phase-late", 1, 2) == "unknown"
        with repo._connection() as db:
            db.execute("INSERT INTO game_phase_boundaries VALUES (?,?,?,?,?,?)",
                       ("phase-late", 1, 2, None, "synthetic", "test"))
            repo._apply_phase_labels(db, "phase-late", 1, 2, None)
        detail = repo.latest_analysis_detail("phase-late", 1)
        phases = {row["ply"]: row["phase"] for row in detail["move_metrics"]}
        assert repo.phase_for_ply("phase-late", 1, 2) == "middlegame"
        assert phases == {1: "opening", 2: "middlegame"}
        with repo._connection() as db:
            legacy_phases = {row["ply"]: row["phase"] for row in db.execute(
                "SELECT ply,phase FROM move_classifications WHERE game_id='phase-late'")}
        assert legacy_phases == {1: "opening", 2: "middlegame"}
    finally:
        repo.shutdown()


def test_schema_v16_upgrade_preserves_existing_facts_and_backup(tmp_path):
    path = tmp_path / "upgrade-v15.sqlite3"
    repo = GameRepository(path)
    repo.save_snapshot("keep", "startpos", "e2e4 e7e5", "white", "Friend", False,
                       "300+0", "finished", "white")
    repo.shutdown()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=15")
    upgraded = GameRepository(path)
    try:
        assert SCHEMA_VERSION == 17
        assert upgraded.get_game("keep")["moves"] == "e2e4 e7e5"
        with upgraded._connection() as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 17
            assert db.execute("SELECT COUNT(*) FROM opening_matches").fetchone()[0] == 0
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with sqlite3.connect(str(path) + ".pre-m3-v17.bak") as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 15
            assert backup.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    finally:
        upgraded.shutdown()
