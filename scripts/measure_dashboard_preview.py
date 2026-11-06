"""Measure the bounded Home preview against synthetic temporary SQLite archives."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from chess_coach.application.dashboard import dashboard_preview_snapshot
from chess_coach.lichess.repository import GameRepository


def measure(size: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="cc-dashboard-preview-") as root:
        repo = GameRepository(Path(root) / "synthetic.sqlite3")
        try:
            account_id = repo.ensure_archive_account("preview-benchmark")
            now = "2026-11-06T12:00:00+00:00"
            with repo._connection() as db:
                db.executemany(
                    "INSERT INTO games(game_id,initial_fen,moves,color,opponent,rated,clock,status,"
                    "winner,updated_at,pgn,started_at,game_time_precision) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(f"synthetic-{index}", "startpos", "", "white", f"Opponent {index}",
                      0, "3+2", "resign", "white", now, "", now, "timestamp")
                     for index in range(size)])
                db.executemany(
                    "INSERT INTO account_games(account_id,game_id,remote_id,imported_at,color,opponent) "
                    "VALUES(?,?,?,?,?,?)",
                    [(account_id, f"synthetic-{index}", f"remote-{index}", now, "white",
                      f"Opponent {index}") for index in range(size)])
            elapsed_ms = []
            for _ in range(5):
                start = time.perf_counter()
                snapshot = dashboard_preview_snapshot(repo, account_id=account_id)
                elapsed_ms.append((time.perf_counter() - start) * 1000)
            assert snapshot["metrics"][0]["value"] == str(size)
            with repo._connection() as db:
                plan = [str(tuple(row)) for row in db.execute(
                    "EXPLAIN QUERY PLAN SELECT COUNT(DISTINCT g.game_id) FROM games g "
                    "JOIN account_games ag ON ag.game_id=g.game_id "
                    "WHERE ag.account_id=?", (account_id,))]
            return {"games": size, "samples_ms": [round(value, 2) for value in elapsed_ms],
                    "median_ms": round(sorted(elapsed_ms)[len(elapsed_ms) // 2], 2),
                    "query_plan": plan}
        finally:
            repo.shutdown()


def main() -> None:
    print(json.dumps({"fixture": "temporary synthetic SQLite; no user data",
                      "runs": [measure(100), measure(3500)]}, indent=2))


if __name__ == "__main__":
    main()
