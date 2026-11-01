from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading

import chess
import chess.engine
import pytest

from chess_coach.lichess.analysis import AnalysisOptions, CompletedGameAnalyzer, _binary_fingerprint
from chess_coach.lichess.batch_analysis import BatchAnalysisRunner, FastPassOptions
from chess_coach.lichess.repository import GameRepository


class FakeResult:
    def __init__(self, board: chess.Board):
        self.info = {
            "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
            "depth": 1,
            "nodes": 1,
            "pv": [next(iter(board.legal_moves))] if board.legal_moves else [],
        }
        self.stop_called = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        yield self.info

    def stop(self):
        self.stop_called = True


class FakeEngine:
    def __init__(self):
        self.id = {"name": "Synthetic UCI", "author": "tests"}
        self.configure_calls = []
        self.analysis_calls = 0
        self.quit_calls = 0

    def configure(self, options):
        self.configure_calls.append(dict(options))

    def analysis(self, board, _limit):
        self.analysis_calls += 1
        return FakeResult(board)

    def quit(self):
        self.quit_calls += 1


def _save_game(repository: GameRepository, game_id: str, started_at: str | None) -> None:
    repository.save_snapshot(
        game_id, "startpos", "e2e4 e7e5", "white", "Synthetic opponent", False,
        "blitz", "finished", "white", white_player="Synthetic user",
        black_player="Synthetic opponent", started_at=started_at,
    )


def _runner(repository, tmp_path, engines, *, options=FastPassOptions()):
    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_bytes(b"not-a-real-engine-used-by-fake-factory")

    def factory(_path):
        engine = FakeEngine()
        engines.append(engine)
        return engine

    return BatchAnalysisRunner(repository, engine_path, options=options,
                               analyzer=CompletedGameAnalyzer(repository, factory))


def test_batch_queue_is_idempotent_recent_first_and_reuses_one_fake_engine(tmp_path):
    repository = GameRepository(tmp_path / "batch.sqlite3")
    engines = []
    try:
        _save_game(repository, "older", "2026-01-01T00:00:00+00:00")
        _save_game(repository, "newer", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines,
                         options=FastPassOptions(nodes=99_000, hash_mb=512, threads=7))
        assert runner.options.nodes == 30_000
        assert runner.options.hash_mb == 128
        assert runner.options.threads == 1
        assert runner.enqueue_missing(limit=10) == {"queued": 2, "already_present": 0}
        assert runner.enqueue_missing(limit=10) == {"queued": 0, "already_present": 2}
        safety_calls = []
        result = runner.run(
            stop_event=threading.Event(), safety_check=lambda job: safety_calls.append(job["game_id"]) or True)
        assert result["processed"] == 2
        assert safety_calls == ["newer", "older"]
        assert len(engines) == 1
        assert engines[0].analysis_calls == 6
        assert engines[0].quit_calls == 1
        assert len(repository.list_analyses("newer")) == 1
        assert len(repository.list_analyses("older")) == 1
        jobs = repository.analysis_job_counts(engine_version=runner.engine_key)
        assert jobs == {"complete": 2}
        assert runner.enqueue_missing(limit=10) == {"queued": 0, "already_present": 2}
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {"complete": 2}
        with repository._connection() as db:
            result_links = db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE result_run_id IS NOT NULL").fetchone()[0]
        assert result_links == 2
    finally:
        repository.shutdown()


def test_standard_analyzer_reuses_legacy_cache_without_analysis_pass_key(tmp_path):
    repository = GameRepository(tmp_path / "legacy-analysis-cache.sqlite3")
    engine_path = tmp_path / "fake-stockfish.exe"
    engine_path.write_bytes(b"fake-engine-for-cache-key")
    engines = []
    try:
        _save_game(repository, "cached", "2026-10-01T00:00:00+00:00")
        revision = repository.current_line("cached")
        options = AnalysisOptions().normalized()
        legacy_parameters = {"limit_kind": options.limit_kind,
                             "limit_value": options.limit_value,
                             "threads": options.threads, "hash_mb": options.hash_mb,
                             "multipv": 1, "classifier_version": "cc-cpl-thresholds-v1"}
        engine_version = json.dumps({
            "identity_version": 2, "uci_name": "Synthetic UCI", "uci_author": "tests",
            "binary_sha256": _binary_fingerprint(engine_path),
        }, sort_keys=True, separators=(",", ":"))
        repository.save_analysis(
            "cached", line_revision=1, line_hash=revision["line_hash"], source="stockfish",
            engine_name="Synthetic UCI", engine_version=engine_version,
            parameters=legacy_parameters,
            assessments=[{"ply": 0, "white_cp": 0}, {"ply": 1, "white_cp": 0},
                         {"ply": 2, "white_cp": 0}],
            classifications=[{"ply": 1, "mover": "white", "eval_loss_cp": 0},
                             {"ply": 2, "mover": "black", "eval_loss_cp": 0}],
        )

        def factory(_path):
            engine = FakeEngine()
            engines.append(engine)
            return engine

        result = CompletedGameAnalyzer(repository, factory).analyze("cached", engine_path)
        assert result["cached"] is True
        assert engines[0].analysis_calls == 0
        assert engines[0].quit_calls == 1
    finally:
        repository.shutdown()


@pytest.mark.parametrize("unsafe_state", [False, None, "exception"])
def test_batch_analysis_fails_closed_before_starting_engine(tmp_path, unsafe_state):
    repository = GameRepository(tmp_path / f"safety-{unsafe_state}.sqlite3")
    engines = []
    try:
        _save_game(repository, "game", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        runner.enqueue_missing()

        def safety(_job):
            if unsafe_state == "exception":
                raise RuntimeError("snapshot unavailable")
            return unsafe_state

        result = runner.run(stop_event=threading.Event(), safety_check=safety)
        assert result["paused"] is True
        assert not engines
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {"paused": 1}
        assert repository.resume_paused_analysis_jobs(engine_version=runner.engine_key) == 1
    finally:
        repository.shutdown()


def test_cancel_during_safety_check_cancels_claimed_and_remaining_jobs(tmp_path):
    repository = GameRepository(tmp_path / "cancel-during-preflight.sqlite3")
    engines = []
    try:
        _save_game(repository, "first", "2026-10-02T00:00:00+00:00")
        _save_game(repository, "second", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        runner.enqueue_missing()
        stop = threading.Event()

        def cancel_during_safety(_job):
            stop.set()
            return None

        result = runner.run(stop_event=stop, safety_check=cancel_during_safety)
        assert result["cancelled"] is True
        assert result["paused"] is False
        assert result["processed"] == 0
        assert not engines
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {
            "cancelled": 2}
        assert repository.list_analyses("first") == []
        assert repository.list_analyses("second") == []
    finally:
        repository.shutdown()


def test_resume_and_cancel_are_scoped_to_exact_engine_parameters(tmp_path):
    repository = GameRepository(tmp_path / "parameter-scoped-queue.sqlite3")
    engines = []
    try:
        _save_game(repository, "game", "2026-10-02T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        candidate = repository.list_games_needing_fast_analysis()[0]
        other_parameters = FastPassOptions(nodes=10_000, hash_mb=32).parameters()
        repository.enqueue_analysis_jobs([candidate], analysis_pass="fast",
                                         engine_version=runner.engine_key,
                                         parameters=runner.parameters)
        repository.enqueue_analysis_jobs([candidate], analysis_pass="fast",
                                         engine_version=runner.engine_key,
                                         parameters=other_parameters)
        claimed_default = repository.claim_next_analysis_job(
            "worker-default", engine_version=runner.engine_key, analysis_pass="fast",
            parameters=runner.parameters)
        claimed_other = repository.claim_next_analysis_job(
            "worker-other", engine_version=runner.engine_key, analysis_pass="fast",
            parameters=other_parameters)
        repository.finish_analysis_job(claimed_default["job_id"], "worker-default", status="paused")
        repository.finish_analysis_job(claimed_other["job_id"], "worker-other", status="paused")
        assert repository.resume_paused_analysis_jobs(
            engine_version=runner.engine_key, parameters=runner.parameters) == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {
            "paused": 1, "queued": 1}
        assert repository.cancel_queued_analysis_jobs(
            engine_version=runner.engine_key, parameters=runner.parameters) == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {
            "cancelled": 1, "paused": 1}
        assert repository.analysis_job_counts(engine_version=runner.engine_key,
                                              parameters=runner.parameters) == {"cancelled": 1}
        assert repository.analysis_job_counts(engine_version=runner.engine_key,
                                              parameters=other_parameters) == {"paused": 1}
        assert not engines
    finally:
        repository.shutdown()


def test_batch_detects_new_human_game_before_next_job_and_stops_engine(tmp_path):
    repository = GameRepository(tmp_path / "appearing-human.sqlite3")
    engines = []
    try:
        _save_game(repository, "first", "2026-10-02T00:00:00+00:00")
        _save_game(repository, "second", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        runner.enqueue_missing()
        safety_calls = []

        def safety(job):
            safety_calls.append(job["game_id"])
            return job["game_id"] == "first"

        result = runner.run(stop_event=threading.Event(), safety_check=safety)
        assert result["processed"] == 1 and result["paused"] is True
        assert safety_calls == ["first", "second"]
        assert len(engines) == 1 and engines[0].quit_calls == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {
            "complete": 1, "paused": 1}
        assert len(repository.list_analyses("first")) == 1
        assert repository.list_analyses("second") == []
    finally:
        repository.shutdown()


def test_pause_and_cancel_stop_current_job_and_persist_remaining_state(tmp_path):
    repository = GameRepository(tmp_path / "stop-batch.sqlite3")
    engines = []
    try:
        _save_game(repository, "first", "2026-10-02T00:00:00+00:00")
        _save_game(repository, "second", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        runner.enqueue_missing()
        stop = threading.Event()

        def on_progress(game_id, done, _total):
            if game_id == "first" and done == 1:
                stop.set()

        result = runner.run(stop_event=stop, safety_check=lambda _job: True, progress=on_progress)
        assert result["cancelled"] is True
        assert len(engines) == 1 and engines[0].quit_calls == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {"cancelled": 2}
        assert repository.list_analyses("first") == []
    finally:
        repository.shutdown()


def test_pause_event_interrupts_current_search_and_resume_keeps_remaining_jobs(tmp_path):
    repository = GameRepository(tmp_path / "pause-batch.sqlite3")
    engines = []
    try:
        _save_game(repository, "first", "2026-10-02T00:00:00+00:00")
        _save_game(repository, "second", "2026-10-01T00:00:00+00:00")
        runner = _runner(repository, tmp_path, engines)
        runner.enqueue_missing()
        pause = threading.Event()

        def on_progress(game_id, done, _total):
            if game_id == "first" and done == 1:
                pause.set()

        result = runner.run(stop_event=threading.Event(), pause_event=pause,
                            safety_check=lambda _job: True, progress=on_progress)
        assert result["paused"] is True and result["cancelled"] is False
        assert len(engines) == 1 and engines[0].quit_calls == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {
            "paused": 1, "queued": 1}
        assert repository.list_analyses("first") == []
        assert repository.resume_paused_analysis_jobs(engine_version=runner.engine_key) == 1
        assert repository.analysis_job_counts(engine_version=runner.engine_key) == {"queued": 2}
    finally:
        repository.shutdown()


def test_expired_lease_is_reclaimed_and_stale_revision_is_never_analyzed(tmp_path):
    repository = GameRepository(tmp_path / "lease.sqlite3")
    try:
        _save_game(repository, "recover", "2026-10-01T00:00:00+00:00")
        candidate = repository.list_games_needing_fast_analysis()[0]
        params = FastPassOptions().parameters()
        key = "fake-engine-v1"
        repository.enqueue_analysis_jobs([candidate], analysis_pass="fast",
                                         engine_version=key, parameters=params)
        first = repository.claim_next_analysis_job(
            "worker-one", engine_version=key, analysis_pass="fast", parameters=params)
        assert first["attempts"] == 1
        with repository._connection() as db:
            db.execute("UPDATE analysis_jobs SET lease_until=? WHERE job_id=?",
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                        first["job_id"]))
        reclaimed = repository.claim_next_analysis_job(
            "worker-two", engine_version=key, analysis_pass="fast", parameters=params)
        assert reclaimed["job_id"] == first["job_id"]
        assert reclaimed["attempts"] == 2
        assert reclaimed["lease_owner"] == "worker-two"
        repository.finish_analysis_job(reclaimed["job_id"], "worker-two", status="paused")

        candidate = repository.list_games_needing_fast_analysis()[0]
        repository.enqueue_analysis_jobs([candidate], analysis_pass="review",
                                         engine_version=key, parameters={"pass": "review"})
        with repository._connection() as db:
            db.execute("UPDATE game_revisions SET is_current=0 WHERE game_id='recover'")
            db.execute("UPDATE games SET line_revision=line_revision+1 WHERE game_id='recover'")
        # The current-line lookup detects a missing current revision and marks queued work stale.
        assert repository.claim_next_analysis_job(
            "worker-three", engine_version=key, analysis_pass="review",
            parameters={"pass": "review"}) is None
        assert repository.analysis_job_counts(engine_version=key) == {"paused": 1, "stale": 1}
    finally:
        repository.shutdown()
