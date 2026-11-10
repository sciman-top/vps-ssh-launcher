"""Contract tests for scripts/cockpit_request_log_audit.py.

The audit reads the local Cockpit request-log sqlite read-only and buckets
429/latency signals; the repo convention pairs every scripts/<域>_<名>.py with
tests/test_<域>_<名>.py, which this file restores.
"""

from __future__ import annotations

import io
import json
import runpy
import sqlite3
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cockpit_request_log_audit.py")
)
main = cast(Any, MODULE["main"])


def _seed_db(path: Path, now_ms: int) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            create table request_logs (
                id integer primary key,
                timestamp integer,
                http_status integer,
                error_category text,
                latency_ms integer,
                model_id text,
                gateway_mode text
            )
            """
        )
        rows = [
            # quota 429 inside the 40-60s local-gate bucket
            (1, now_ms - 60_000, 429, "quota_or_rate_limit", 45_000, "m1", "gw"),
            # quota 429 beyond the 115s remote-queue bucket
            (2, now_ms - 120_000, 429, "quota_or_rate_limit", 120_000, "m1", "gw"),
            # quota row outside both buckets
            (3, now_ms - 30_000, 429, "quota_or_rate_limit", 10_000, "m2", "gw"),
            # slow success at the >=60s boundary
            (4, now_ms - 90_000, 200, None, 61_000, "m1", "gw"),
            # ordinary success, never bucketed
            (5, now_ms - 10_000, 200, None, 900, "m1", "gw"),
        ]
        db.executemany("insert into request_logs values (?, ?, ?, ?, ?, ?, ?)", rows)


class CockpitRequestLogAuditTests(unittest.TestCase):
    def test_buckets_and_counts_from_seeded_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "logs.sqlite"
            now_ms = int(time.time() * 1000)
            _seed_db(db_path, now_ms)
            argv = [
                "cockpit_request_log_audit",
                "--db",
                str(db_path),
                "--since-minutes",
                "60",
            ]
            output = io.StringIO()
            with self._patched_argv(argv):
                with redirect_stdout(output):
                    code = main()
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["statuses"], {"429": 3, "200": 2})
        attribution = result["attribution"]
        self.assertEqual(attribution["method"], "timing_bucket_only")
        self.assertEqual(attribution["local_gate_429_40_to_60s"], 1)
        self.assertEqual(attribution["remote_queue_or_long_429_ge_115s"], 1)
        self.assertEqual(attribution["other_quota_or_rate_limit"], 1)
        self.assertEqual(attribution["slow_success_ge_60s"], 1)
        # The event view must stay attribution-only: exactly these fields,
        # no request payloads, URLs or credential-shaped material.
        for event_result in result["recent_events"]:
            self.assertEqual(
                set(event_result),
                {"time", "status", "category", "latency_ms", "model", "gateway"},
            )

    def test_missing_database_reports_unavailable_without_failure(self) -> None:
        argv = [
            "cockpit_request_log_audit",
            "--db",
            "Z:/definitely/not/here/logs.sqlite",
        ]
        output = io.StringIO()
        with self._patched_argv(argv):
            with redirect_stdout(output):
                code = main()
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "unavailable", "reason": "database_missing"},
        )

    def test_non_positive_since_minutes_is_a_usage_error(self) -> None:
        argv = [
            "cockpit_request_log_audit",
            "--db",
            "Z:/unused.sqlite",
            "--since-minutes",
            "0",
        ]
        with self._patched_argv(argv):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)

    def _patched_argv(self, argv: list[str]) -> AbstractContextManager[None]:
        @contextmanager
        def manager() -> Iterator[None]:
            original = sys.argv
            sys.argv = argv
            try:
                yield
            finally:
                sys.argv = original

        return manager()
