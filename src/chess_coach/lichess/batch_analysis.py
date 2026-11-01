"""Persistent, bounded batch analysis for explicitly selected completed games."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import threading
import time
import uuid
from typing import Callable

import chess.engine

from .analysis import AnalysisOptions, CLASSIFIER_VERSION, CompletedGameAnalyzer, _binary_fingerprint
from .repository import GameRepository
from ..diagnostics import log_event, reset_context, set_context


@dataclass(frozen=True)
class FastPassOptions:
    nodes: int = 20_000
    hash_mb: int = 64
    threads: int = 1

    def normalized(self) -> "FastPassOptions":
        return FastPassOptions(
            nodes=max(1_000, min(30_000, int(self.nodes))),
            hash_mb=max(16, min(128, int(self.hash_mb))),
            threads=1,
        )

    def analysis_options(self) -> AnalysisOptions:
        value = self.normalized()
        return AnalysisOptions(limit_kind="nodes", limit_value=value.nodes,
                               threads=value.threads, hash_mb=value.hash_mb)

    def parameters(self) -> dict:
        options = self.analysis_options().normalized()
        return {"limit_kind": "nodes", "limit_value": options.limit_value,
                "threads": 1, "hash_mb": options.hash_mb, "multipv": 1,
                "classifier_version": CLASSIFIER_VERSION, "analysis_pass": "fast"}


class BatchAnalysisRunner:
    """Run queued games serially through one explicitly chosen local UCI process.

    The caller must provide a current fail-closed Lichess safety check for every
    claimed job. `True` means a complete snapshot proves the run is safe; False,
    None, or an exception pauses the queue without starting UCI.
    """

    def __init__(self, repository: GameRepository, engine_path: str | Path,
                 *, options: FastPassOptions = FastPassOptions(),
                 analyzer: CompletedGameAnalyzer | None = None,
                 engine_factory: Callable[[str], chess.engine.SimpleEngine] | None = None,
                 lease_seconds: int = 300,
                 safety_refresh_seconds: float = 10.0) -> None:
        self.repository = repository
        self.engine_path = Path(engine_path)
        if not self.engine_path.is_file():
            raise FileNotFoundError("Укажите установленный локальный Stockfish UCI executable.")
        self.options = options.normalized()
        self.analysis_options = self.options.analysis_options()
        self.parameters = self.options.parameters()
        self.binary_sha256 = _binary_fingerprint(self.engine_path)
        self.engine_key = "stockfish-sha256:" + self.binary_sha256
        self.review_engine_key = "stockfish-review-sha256:" + self.binary_sha256
        self.lease_seconds = max(30, min(3600, int(lease_seconds)))
        self.safety_refresh_seconds = max(0.01, min(60.0, float(safety_refresh_seconds)))
        self.analyzer = analyzer or CompletedGameAnalyzer(repository, engine_factory)
        self.worker_id = uuid.uuid4().hex

    def enqueue_missing(self, *, account_id: str | None = None,
                        limit: int = 1000, retry_failed: bool = False) -> dict[str, int]:
        candidates = self.repository.list_games_needing_fast_analysis(
            account_id=account_id, limit=max(1, min(10_000, int(limit))),
            engine_version=self.engine_key, parameters=self.parameters,
            retry_failed=retry_failed)
        return self.repository.enqueue_analysis_jobs(
            candidates, analysis_pass="fast", engine_version=self.engine_key,
            parameters=self.parameters, retry_failed=retry_failed)

    def run(self, *, stop_event: threading.Event, safety_check: Callable[[dict], bool | None],
            pause_event: threading.Event | None = None,
            progress: Callable[[str, int, int], None] | None = None,
            include_selective_review: bool = False,
            account_id: str | None = None,
            max_fast_jobs: int | None = None,
            review_budget_per_game: int = 12,
            review_budget_total: int = 100) -> dict[str, int | bool | str | None]:
        if safety_check is None:
            raise ValueError("A fail-closed per-game safety check is required.")
        engine: chess.engine.SimpleEngine | None = None
        processed = failed = 0
        last_failed_job_id: str | None = None
        last_failure: str | None = None
        attempted_fast = 0
        paused = cancelled = False
        stop_reason: str | None = None
        analysis_stop = threading.Event()
        monitor_stop = threading.Event()
        active_job_lock = threading.Lock()
        safety_call_lock = threading.Lock()
        active_job: dict | None = None
        safety_lost = threading.Event()
        safety_failure: list[str] = []
        analysis_pass = "fast"
        review_scheduled = False
        review_queued = 0
        run_id = uuid.uuid4().hex
        run_started = time.monotonic()
        run_context = set_context(run_id=run_id)
        log_event("batch_run_started", "batch_analysis", run_id=run_id,
                  mode="selective" if include_selective_review else "fast")

        def check_active_safety(job: dict) -> bool:
            try:
                with safety_call_lock:
                    result = safety_check(job)
            except Exception as exc:
                result = None
                safety_failure[:] = [f"safety check failed: {type(exc).__name__}"]
            if result is not True:
                if not safety_failure:
                    safety_failure[:] = ["Fair-play snapshot is unsafe, stale, or unknown."]
                safety_lost.set()
                analysis_stop.set()
                return False
            return True

        def monitor_controls() -> None:
            next_refresh = time.monotonic() + self.safety_refresh_seconds
            while not monitor_stop.wait(0.05):
                if stop_event.is_set() or (pause_event is not None and pause_event.is_set()):
                    analysis_stop.set()
                    return
                with active_job_lock:
                    job = active_job
                if job is not None and time.monotonic() >= next_refresh:
                    if not check_active_safety(job):
                        return
                    next_refresh = time.monotonic() + self.safety_refresh_seconds

        monitor = threading.Thread(target=monitor_controls,
                                   name="chess-coach-batch-control", daemon=True)
        monitor.start()
        try:
            while True:
                if stop_event.is_set():
                    cancelled = True
                    stop_reason = "cancelled"
                    break
                if pause_event is not None and pause_event.is_set():
                    paused = True
                    stop_reason = "paused"
                    break
                if analysis_pass == "fast":
                    if max_fast_jobs is not None and attempted_fast >= max(0, int(max_fast_jobs)):
                        if include_selective_review and not review_scheduled:
                            review_queued = self.enqueue_selected_reviews(
                                account_id=account_id, budget_per_game=review_budget_per_game,
                                budget_total=review_budget_total)
                            review_scheduled = True
                            analysis_pass = "review"
                            continue
                        break
                    job = self.repository.claim_next_analysis_job(
                        self.worker_id, engine_version=self.engine_key,
                        analysis_pass="fast", parameters=self.parameters,
                        lease_seconds=self.lease_seconds)
                    if job is None and include_selective_review and not review_scheduled:
                        review_queued = self.enqueue_selected_reviews(
                            account_id=account_id, budget_per_game=review_budget_per_game,
                            budget_total=review_budget_total)
                        review_scheduled = True
                        analysis_pass = "review"
                        continue
                else:
                    job = self.repository.claim_next_analysis_job(
                        self.worker_id, engine_version=self.review_engine_key,
                        analysis_pass="review", parameters=None,
                        lease_seconds=self.lease_seconds)
                if job is None:
                    break
                log_event("batch_job_claimed", "batch_analysis", run_id=run_id,
                          job_id=job.get("job_id"), mode=str(job.get("analysis_pass", "fast")))
                if analysis_pass == "fast":
                    attempted_fast += 1
                safety_failure.clear()
                safety_lost.clear()
                if not check_active_safety(job):
                    cancelled = stop_event.is_set()
                    paused = not cancelled
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id,
                        status="cancelled" if cancelled else "paused",
                        error=stop_reason or safety_failure[0])
                    stop_reason = stop_reason or ("cancelled during safety check" if cancelled
                                                   else safety_failure[0])
                    break
                if not self._job_has_eligible_legal_line(job):
                    last_failed_job_id = str(job["job_id"])
                    last_failure = "The game is no longer a completed standard game with a legal current line."
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status="failed",
                        error=last_failure)
                    failed += 1
                    log_event("batch_job_failed", "batch_analysis", run_id=run_id,
                              job_id=job.get("job_id"), error_code="ineligible_game_line")
                    continue
                job_context = set_context(job_id=str(job.get("job_id", "")))
                with active_job_lock:
                    active_job = job

                def on_progress(done: int, total: int) -> None:
                    if not self.repository.renew_analysis_job_lease(
                            job["job_id"], self.worker_id,
                            lease_seconds=self.lease_seconds):
                        analysis_stop.set()
                        raise InterruptedError("Очередь анализа потеряла lease задачи.")
                    if progress:
                        progress(str(job["game_id"]), done, total)

                try:
                    if engine is None:
                        engine = self.analyzer.engine_factory(str(self.engine_path))
                        log_event("uci_engine_opened", "batch_analysis", run_id=run_id)
                    if job["analysis_pass"] == "fast":
                        result = self.analyzer.analyze(
                            str(job["game_id"]), self.engine_path, self.analysis_options,
                            progress=on_progress, stop_event=analysis_stop,
                            analysis_pass="fast", engine=engine)
                    else:
                        review_params = json.loads(job["parameters_json"])
                        result = self.analyzer.review_selected_positions(
                            str(job["game_id"]), self.engine_path,
                            review_params["review_move_plies"],
                            # The worker performed an authoritative preflight and its
                            # independent TTL monitor continues while UCI runs. This
                            # callback only reads the thread-safe monitor state; it
                            # must not issue one Lichess request per selected ply.
                            safety_check=lambda: not safety_lost.is_set() and not analysis_stop.is_set(),
                            nodes=int(review_params["limit_value"]),
                            multipv=int(review_params["multipv"]),
                            stop_event=analysis_stop,
                            progress=lambda done, total: on_progress(done, total),
                            engine=engine)
                except InterruptedError as exc:
                    if stop_event.is_set():
                        status = "cancelled"
                        cancelled = True
                        stop_reason = str(exc)
                    elif pause_event is not None and pause_event.is_set():
                        status = "paused"
                        paused = True
                        stop_reason = "paused"
                    elif safety_lost.is_set():
                        status = "paused"
                        paused = True
                        stop_reason = safety_failure[0]
                    else:
                        status = "cancelled"
                        cancelled = True
                        stop_reason = str(exc)
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status=status, error=str(exc))
                    with active_job_lock:
                        active_job = None
                    break
                except Exception as exc:
                    log_event("batch_job_failed", "batch_analysis", run_id=run_id,
                              job_id=job.get("job_id"), error=exc)
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status="failed", error=str(exc))
                    with active_job_lock:
                        active_job = None
                    failed += 1
                    stop_reason = f"{type(exc).__name__}: {exc}"
                    last_failed_job_id = str(job["job_id"])
                    last_failure = stop_reason
                    break
                else:
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status="complete",
                        result_run_id=result.get("run_id"))
                    with active_job_lock:
                        active_job = None
                    processed += 1
                    log_event("batch_job_completed", "batch_analysis", run_id=run_id,
                              job_id=job.get("job_id"), status="complete")
                finally:
                    reset_context(job_context)
        finally:
            monitor_stop.set()
            monitor.join()
            if engine is not None:
                engine.quit()
            if cancelled:
                self.repository.cancel_queued_analysis_jobs(
                    engine_version=self.engine_key, parameters=self.parameters)
                self.repository.cancel_queued_analysis_jobs(engine_version=self.review_engine_key)
            reset_context(run_context)
        log_event("batch_run_finished", "batch_analysis", run_id=run_id,
                  duration_ms=(time.monotonic() - run_started) * 1000,
                  count=processed, new_count=failed,
                  status="cancelled" if cancelled else "paused" if paused else "complete")
        return {"processed": processed, "failed": failed, "paused": paused,
                "cancelled": cancelled, "reason": stop_reason,
                "last_failed_job_id": last_failed_job_id,
                "last_failure": last_failure,
                "attempted_fast": attempted_fast,
                "queue": self.repository.analysis_job_counts(engine_version=self.engine_key,
                                                               parameters=self.parameters),
                "review_queue": self.repository.analysis_job_counts(
                    engine_version=self.review_engine_key),
                "review_queued": review_queued}

    def enqueue_selected_reviews(self, *, account_id: str | None = None,
                                 budget_per_game: int = 12,
                                 budget_total: int = 100,
                                 nodes: int = 150_000, multipv: int = 2) -> int:
        """Persist bounded review jobs from one current fast or Lichess source run per game."""
        cap = max(0, min(500, int(budget_total)))
        if cap == 0:
            return 0
        selected_total = 0
        queued_jobs = 0
        page_size = 100
        offset = 0
        while selected_total < cap:
            runs = self.repository.list_reviewable_analysis_runs(
                engine_version=self.engine_key, parameters=self.parameters,
                account_id=account_id, limit=page_size, offset=offset)
            if not runs:
                break
            offset += len(runs)
            for run in runs:
                remaining = cap - selected_total
                if remaining <= 0:
                    break
                plan = self.repository.plan_review_positions(
                    run["run_id"], budget_per_game=max(0, min(50, int(budget_per_game))),
                    budget_total=remaining)
                move_plies = [int(item["ply"]) for item in plan]
                if not move_plies:
                    continue
                review_params = {
                    "review_policy_version": "cc-selective-review-v1",
                    "parent_run_id": run["run_id"], "source": run["source"],
                    "review_move_plies": move_plies,
                    "limit_kind": "nodes", "limit_value": max(10_000, min(300_000, int(nodes))),
                    "multipv": max(1, min(3, int(multipv))), "threads": 1, "hash_mb": 128,
                }
                result = self.repository.enqueue_analysis_jobs(
                    [{"game_id": run["game_id"], "line_revision": run["line_revision"],
                      "line_hash": run["line_hash"], "started_at": run["started_at"]}],
                    analysis_pass="review", engine_version=self.review_engine_key,
                    parameters=review_params)
                queued_jobs += result["queued"]
                # Duplicate queued/complete/paused jobs do not consume this invocation's quota;
                # keep scanning older pages until newly requested work fills the cap.
                if result["queued"]:
                    selected_total += len(move_plies)
        return queued_jobs

    def _job_has_eligible_legal_line(self, job: dict) -> bool:
        game = self.repository.get_game(str(job["game_id"]))
        revision = self.repository.current_line(str(job["game_id"]))
        if not game or not revision:
            return False
        if (game.get("status") not in {"mate", "resign", "stalemate", "timeout", "draw",
                                       "outoftime", "cheat", "noStart", "variantEnd", "finished"}
                or bool(game.get("final_sync_pending")) or game.get("import_error")
                or game.get("variant") != "standard"
                or revision.get("line_revision") != job.get("line_revision")
                or revision.get("line_hash") != job.get("line_hash")):
            return False
        pgn = str(game.get("pgn") or "")
        if not any(f'[Result "{result}"]' in pgn
                   for result in ("1-0", "0-1", "1/2-1/2")):
            return False
        try:
            board = chess.Board() if revision["initial_fen"] == "startpos" else chess.Board(
                revision["initial_fen"])
            for uci in str(revision["moves"]).split():
                board.push_uci(uci)
        except (KeyError, ValueError, chess.InvalidMoveError, chess.IllegalMoveError,
                chess.AmbiguousMoveError):
            return False
        return True
