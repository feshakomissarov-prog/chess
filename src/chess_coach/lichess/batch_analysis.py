"""Persistent, bounded batch analysis for explicitly selected completed games."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import uuid
from typing import Callable

import chess.engine

from .analysis import AnalysisOptions, CLASSIFIER_VERSION, CompletedGameAnalyzer, _binary_fingerprint
from .repository import GameRepository


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
                 lease_seconds: int = 300) -> None:
        self.repository = repository
        self.engine_path = Path(engine_path)
        if not self.engine_path.is_file():
            raise FileNotFoundError("Укажите установленный локальный Stockfish UCI executable.")
        self.options = options.normalized()
        self.analysis_options = self.options.analysis_options()
        self.parameters = self.options.parameters()
        self.binary_sha256 = _binary_fingerprint(self.engine_path)
        self.engine_key = "stockfish-sha256:" + self.binary_sha256
        self.lease_seconds = max(30, min(3600, int(lease_seconds)))
        self.analyzer = analyzer or CompletedGameAnalyzer(repository, engine_factory)
        self.worker_id = uuid.uuid4().hex

    def enqueue_missing(self, *, account_id: str | None = None,
                        limit: int = 1000) -> dict[str, int]:
        candidates = self.repository.list_games_needing_fast_analysis(
            account_id=account_id, limit=max(1, min(10_000, int(limit))))
        return self.repository.enqueue_analysis_jobs(
            candidates, analysis_pass="fast", engine_version=self.engine_key,
            parameters=self.parameters)

    def run(self, *, stop_event: threading.Event, safety_check: Callable[[dict], bool | None],
            pause_event: threading.Event | None = None,
            progress: Callable[[str, int, int], None] | None = None) -> dict[str, int | bool | str | None]:
        if safety_check is None:
            raise ValueError("A fail-closed per-game safety check is required.")
        engine: chess.engine.SimpleEngine | None = None
        processed = failed = 0
        paused = cancelled = False
        stop_reason: str | None = None
        analysis_stop = threading.Event()
        monitor_stop = threading.Event()

        def monitor_controls() -> None:
            while not monitor_stop.wait(0.05):
                if stop_event.is_set() or (pause_event is not None and pause_event.is_set()):
                    analysis_stop.set()
                    return

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
                job = self.repository.claim_next_analysis_job(
                    self.worker_id, engine_version=self.engine_key,
                    analysis_pass="fast", parameters=self.parameters,
                    lease_seconds=self.lease_seconds)
                if job is None:
                    break
                try:
                    safety = safety_check(job)
                except Exception as exc:
                    safety = None
                    stop_reason = f"safety check failed: {type(exc).__name__}"
                if safety is not True:
                    cancelled = stop_event.is_set()
                    paused = not cancelled
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id,
                        status="cancelled" if cancelled else "paused",
                        error=stop_reason or "Safety state unknown or active human game detected.")
                    stop_reason = stop_reason or ("cancelled during safety check" if cancelled
                                                   else "safety blocked")
                    break
                if engine is None:
                    engine = self.analyzer.engine_factory(str(self.engine_path))

                def on_progress(done: int, total: int) -> None:
                    if not self.repository.renew_analysis_job_lease(
                            job["job_id"], self.worker_id,
                            lease_seconds=self.lease_seconds):
                        analysis_stop.set()
                        raise InterruptedError("Очередь анализа потеряла lease задачи.")
                    if progress:
                        progress(str(job["game_id"]), done, total)

                try:
                    result = self.analyzer.analyze(
                        str(job["game_id"]), self.engine_path, self.analysis_options,
                        progress=on_progress, stop_event=analysis_stop,
                        analysis_pass="fast", engine=engine)
                except InterruptedError as exc:
                    if stop_event.is_set():
                        status = "cancelled"
                        cancelled = True
                        stop_reason = str(exc)
                    elif pause_event is not None and pause_event.is_set():
                        status = "paused"
                        paused = True
                        stop_reason = "paused"
                    else:
                        status = "cancelled"
                        cancelled = True
                        stop_reason = str(exc)
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status=status, error=str(exc))
                    break
                except Exception as exc:
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status="failed", error=str(exc))
                    failed += 1
                    stop_reason = f"{type(exc).__name__}: {exc}"
                    break
                else:
                    self.repository.finish_analysis_job(
                        job["job_id"], self.worker_id, status="complete",
                        result_run_id=result.get("run_id"))
                    processed += 1
        finally:
            monitor_stop.set()
            monitor.join()
            if engine is not None:
                engine.quit()
            if cancelled:
                self.repository.cancel_queued_analysis_jobs(
                    engine_version=self.engine_key, parameters=self.parameters)
        return {"processed": processed, "failed": failed, "paused": paused,
                "cancelled": cancelled, "reason": stop_reason,
                "queue": self.repository.analysis_job_counts(engine_version=self.engine_key)}
