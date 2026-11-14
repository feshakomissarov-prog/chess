"""Print aggregate-only Lichess coverage for an explicitly selected DB snapshot.

The input must be a stable SQLite snapshot made with SQLite's backup API. The
script opens it with mode=ro, enables query_only, and never performs migrations,
network requests, or engine work. It is deliberately not run by CI against user
data; tests use temporary synthetic snapshots.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

from chess_coach.application.lichess_coverage_audit import audit_lichess_coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path,
                        help="stable SQLite backup/snapshot; do not pass an active database")
    parser.add_argument("account_id", help="selected account scope; never printed in the report")
    parser.add_argument("--snapshot-time", help="timezone-aware ISO timestamp; defaults to now UTC")
    args = parser.parse_args(argv)
    try:
        snapshot_time = (datetime.fromisoformat(args.snapshot_time.replace("Z", "+00:00"))
                         if args.snapshot_time else None)
        uri = f"{args.snapshot.resolve(strict=True).as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=3.0)
        connection.row_factory = sqlite3.Row
        try:
            result = audit_lichess_coverage(
                connection, args.account_id, snapshot_time=snapshot_time)
        finally:
            connection.close()
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Exception messages from SQLite can contain a private absolute path.
        print(f"Lichess coverage audit failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
