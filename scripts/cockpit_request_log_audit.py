"""Redacted local Cockpit request-log attribution for capacity, 429, and slow paths."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def default_db() -> Path:
    return (
        Path(os.environ.get("USERPROFILE", "~")).expanduser()
        / ".antigravity_cockpit"
        / "codex_local_access_logs.sqlite"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--since-minutes", type=int, default=180)
    args = parser.parse_args()
    if args.since_minutes <= 0:
        parser.error("--since-minutes must be positive")
    if not args.db.is_file():
        print(json.dumps({"status": "unavailable", "reason": "database_missing"}))
        return 0

    cutoff = int((time.time() - args.since_minutes * 60) * 1000)
    # Read-only URI prevents an audit from creating journal/WAL state or
    # blocking the running sidecar's writer.  A short timeout keeps Audit from
    # becoming a hidden dependency on the request path.
    uri = f"file:{args.db.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
            rows = list(
                db.execute(
                    """
                    select timestamp, http_status, error_category, latency_ms,
                           model_id, gateway_mode
                    from request_logs
                    where timestamp >= ?
                    order by id desc
                    """,
                    (cutoff,),
                )
            )
    except sqlite3.Error as exc:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "reason": "sqlite_read_failed",
                    "error": type(exc).__name__,
                }
            )
        )
        return 0

    status_counts = Counter(str(row[1]) for row in rows)
    category_counts = Counter(row[2] or "none" for row in rows)
    quota = [row for row in rows if row[2] == "quota_or_rate_limit"]
    slow_success = [row for row in rows if row[1] == 200 and row[3] >= 60_000]
    local_gate = [row for row in quota if 40_000 <= row[3] <= 60_000]
    remote_queue = [row for row in quota if row[3] >= 115_000]

    def event(row: tuple[object, ...]) -> dict[str, object]:
        timestamp = int(row[0])
        return {
            "time": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
            "status": row[1],
            "category": row[2] or "",
            "latency_ms": row[3],
            "model": row[4],
            "gateway": row[5],
        }

    result = {
        "status": "ok",
        "since_minutes": args.since_minutes,
        "total": len(rows),
        "statuses": dict(status_counts),
        "error_categories": dict(category_counts),
        "attribution": {
            "method": "timing_bucket_only",
            "certainty": "suspected_until_correlated_with_cpa_request_id_and_admission_journal",
            "local_gate_429_40_to_60s": len(local_gate),
            "remote_queue_or_long_429_ge_115s": len(remote_queue),
            "other_quota_or_rate_limit": len(quota)
            - len(local_gate)
            - len(remote_queue),
            "slow_success_ge_60s": len(slow_success),
        },
        "recent_events": [event(row) for row in rows[:20]],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
