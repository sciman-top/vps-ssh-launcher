"""Tests for scripts/cpa_failure_triage.py.

The classifier is the contract that keeps failure attribution from depending on
whoever reads the logs, so every layer rule and every boundary is pinned here.
"""

from __future__ import annotations

import datetime
import json
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

MODULE = runpy.run_path(
    str(Path(__file__).parent / "scripts" / "cpa_failure_triage.py")
)
Budgets = cast(Any, MODULE["Budgets"])
Row = cast(Any, MODULE["Row"])
LayerReport = cast(Any, MODULE["LayerReport"])
LAYER_ORDER = cast(tuple, MODULE["LAYER_ORDER"])
render = cast(Any, MODULE["render"])
classify = cast(Any, MODULE["classify"])
load_budgets = cast(Any, MODULE["load_budgets"])
parse_doctor = cast(Any, MODULE["parse_doctor"])
read_rows = cast(Any, MODULE["read_rows"])
summarize = cast(Any, MODULE["summarize"])
main = cast(Any, MODULE["main"])
LOCAL_GATE = cast(str, MODULE["LOCAL_GATE"])
ADMISSION_QUEUE = cast(str, MODULE["ADMISSION_QUEUE"])
ADMISSION_FAST = cast(str, MODULE["ADMISSION_FAST"])
UPSTREAM_CAPACITY = cast(str, MODULE["UPSTREAM_CAPACITY"])
DEAD_ROUTE = cast(str, MODULE["DEAD_ROUTE"])
CLIENT_ABORT = cast(str, MODULE["CLIENT_ABORT"])
CLIENT_ERROR = cast(str, MODULE["CLIENT_ERROR"])
SLOW_SUCCESS = cast(str, MODULE["SLOW_SUCCESS"])
HEALTHY = cast(str, MODULE["HEALTHY"])
UNCLASSIFIED = cast(str, MODULE["UNCLASSIFIED"])

BUDGETS = Budgets()


def classify_row(
    status: int | None,
    category: str = "",
    latency_ms: int = 0,
    model: str = "gpt-6.1-sol",
    budgets: Any = BUDGETS,
) -> str:
    return cast(str, classify(status, category, latency_ms, model, budgets))


class ClassifierTests(unittest.TestCase):
    def test_healthy_and_slow_success(self) -> None:
        self.assertEqual(classify_row(200, latency_ms=2000), HEALTHY)
        self.assertEqual(classify_row(200, latency_ms=59999), HEALTHY)
        self.assertEqual(classify_row(200, latency_ms=60000), SLOW_SUCCESS)
        self.assertEqual(classify_row(200, latency_ms=331566), SLOW_SUCCESS)

    def test_local_gate_matches_its_budget(self) -> None:
        # Observed fingerprints were 45026/45029/45045/45060 ms.
        for latency in (45026, 45029, 45045, 45060):
            with self.subTest(latency=latency):
                self.assertEqual(
                    classify_row(429, "quota_or_rate_limit", latency), LOCAL_GATE
                )

    def test_admission_queue_timeout_matches_its_budget(self) -> None:
        # Observed fingerprints were 121863 and 155009 ms; the queue budget is
        # 120000 ms and the local side adds its own overhead.
        for latency in (120005, 121863):
            with self.subTest(latency=latency):
                self.assertEqual(
                    classify_row(429, "quota_or_rate_limit", latency), ADMISSION_QUEUE
                )

    def test_fast_reject_is_admission_side(self) -> None:
        for latency in (169, 874, 4020):
            with self.subTest(latency=latency):
                self.assertEqual(
                    classify_row(429, "quota_or_rate_limit", latency), ADMISSION_FAST
                )

    def test_unexplained_429_is_surfaced_not_guessed(self) -> None:
        # 20 s matches neither budget and is not a fast reject.
        self.assertEqual(classify_row(429, "quota_or_rate_limit", 20000), UNCLASSIFIED)

    def test_dead_route_precedes_generic_upstream_capacity(self) -> None:
        for status in (500, 502, 503):
            with self.subTest(status=status):
                self.assertEqual(
                    classify_row(status, "upstream_error", 4202, "gpt-5.6-terra"),
                    DEAD_ROUTE,
                )
        # The same status on a healthy alias stays upstream capacity.
        self.assertEqual(
            classify_row(502, "upstream_error", 4202, "gpt-6.1-sol"), UPSTREAM_CAPACITY
        )

    def test_upstream_capacity_and_client_errors(self) -> None:
        self.assertEqual(
            classify_row(503, "upstream_error", 543, "gpt-6.1-sol"), UPSTREAM_CAPACITY
        )
        self.assertEqual(classify_row(499, "", 21153), CLIENT_ABORT)
        self.assertEqual(classify_row(401, "auth_failed"), CLIENT_ERROR)
        self.assertEqual(classify_row(404, "model_not_available"), CLIENT_ERROR)
        self.assertEqual(classify_row(400, "request_failed"), CLIENT_ERROR)

    def test_status_alone_can_identify_a_rate_limit(self) -> None:
        # A missing category must not hide a 429 whose latency matches a budget.
        self.assertEqual(classify_row(429, "", 45000), LOCAL_GATE)

    def test_tolerance_boundaries(self) -> None:
        # The gate fingerprint is a window: the gate refuses before contacting
        # upstream, so its overhead is tens of milliseconds.
        self.assertEqual(classify_row(429, "", 45000), LOCAL_GATE)
        self.assertEqual(classify_row(429, "", 45000 + 1500), LOCAL_GATE)
        self.assertEqual(classify_row(429, "", 45000 + 1501), UNCLASSIFIED)
        # The admission fingerprint is a lower bound: the client waits the whole
        # queue budget before the refusal is written.
        self.assertEqual(classify_row(429, "", 120000 - 1500), ADMISSION_QUEUE)
        self.assertEqual(classify_row(429, "", 120000 - 1501), UNCLASSIFIED)
        self.assertEqual(classify_row(429, "", 155009), ADMISSION_QUEUE)
        # Fast rejects are anything answered in milliseconds.
        self.assertEqual(classify_row(429, "", 4999), ADMISSION_FAST)
        self.assertEqual(classify_row(429, "", 5000), UNCLASSIFIED)


class BudgetTests(unittest.TestCase):
    def test_collection_value_drives_the_gate_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collection = Path(tmp) / "codex_local_access.json"
            collection.write_text(
                json.dumps({"accountConcurrencyWaitMs": 30000}), encoding="utf-8"
            )
            budgets = load_budgets(collection, {})
            self.assertEqual(budgets.gate_wait_ms, 30000)
            # The classifier follows the configured budget, not the old constant.
            self.assertEqual(
                classify_row(429, "quota_or_rate_limit", 30010, budgets=budgets),
                LOCAL_GATE,
            )
            self.assertEqual(
                classify_row(429, "quota_or_rate_limit", 45000, budgets=budgets),
                UNCLASSIFIED,
            )

    def test_missing_collection_falls_back_to_default(self) -> None:
        budgets = load_budgets(Path("does-not-exist.json"), {})
        self.assertEqual(budgets.gate_wait_ms, BUDGETS.gate_wait_ms)

    def test_explicit_override_wins(self) -> None:
        budgets = load_budgets(None, {"gate_wait_ms": 90000, "slow_ms": 30000})
        self.assertEqual(budgets.gate_wait_ms, 90000)
        self.assertEqual(budgets.slow_ms, 30000)


class SummaryTests(unittest.TestCase):
    def test_counts_examples_and_latency_span(self) -> None:
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        rows = [
            Row(now_ms, 429, "quota_or_rate_limit", 45045, "gpt-6.1-sol"),
            Row(now_ms, 429, "quota_or_rate_limit", 45026, "gpt-6.1-sol"),
            Row(now_ms, 503, "upstream_error", 543, "gpt-6.1-sol"),
            Row(now_ms, 200, "", 2000, "glm-5.3"),
        ]
        reports = summarize(rows, BUDGETS)
        self.assertEqual(reports[LOCAL_GATE].count, 2)
        self.assertEqual(min(reports[LOCAL_GATE].latency_ms), 45026)
        self.assertEqual(max(reports[LOCAL_GATE].latency_ms), 45045)
        self.assertEqual(len(reports[LOCAL_GATE].examples), 2)
        self.assertEqual(reports[UPSTREAM_CAPACITY].count, 1)
        self.assertEqual(reports[HEALTHY].count, 1)

    def test_example_limit_is_respected(self) -> None:
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        rows = [
            Row(now_ms, 503, "upstream_error", 500, "gpt-6.1-sol") for _ in range(10)
        ]
        reports = summarize(rows, BUDGETS, example_limit=3)
        self.assertEqual(reports[UPSTREAM_CAPACITY].count, 10)
        self.assertEqual(len(reports[UPSTREAM_CAPACITY].examples), 3)


class DoctorParsingTests(unittest.TestCase):
    def test_extracts_contract_cooldown_and_blocks(self) -> None:
        statuses = {
            "statuses": {"200": 902, "429": 37, "503": 13},
            "statuses_by_client_class": {"external/429": 17, "loopback/429": 20},
            "statuses_by_hour": {"2026-10-02T15": {"total": 12, "429": 2}},
        }
        text = "\n".join(
            [
                "==cooldown-state==",
                "cooldown_state=none",
                "luna_state=available",
                "==oauth-monitor==",
                "oauth_days_left=3",
                # The doctor prints one JSON line and the nested keys live inside
                # it, exactly as the real output does.
                json.dumps(statuses),
                '{"retained_overload_request_files": 3, "overload_markers": 6}',
                "DOCTOR_CONTRACT_OK",
            ]
        )
        facts = parse_doctor(text)
        self.assertEqual(facts["contract"], "DOCTOR_CONTRACT_OK")
        self.assertEqual(facts["cooldown_state"], "none")
        self.assertEqual(facts["luna_state"], "available")
        self.assertEqual(facts["oauth_days_left"], "3")
        self.assertEqual(facts["gateway_statuses"]["statuses"]["429"], 37)
        self.assertEqual(facts["statuses_by_client_class"]["external/429"], 17)
        self.assertEqual(facts["statuses_by_hour"]["2026-10-02T15"]["429"], 2)
        self.assertEqual(facts["overload"]["retained_overload_request_files"], 3)

    def test_missing_keys_are_absent_not_invented(self) -> None:
        facts = parse_doctor("nothing to see here")
        self.assertNotIn("contract", facts)
        self.assertNotIn("cooldown_state", facts)


class DatabaseTests(unittest.TestCase):
    @staticmethod
    def _make_db(path: Path, rows: list[tuple[int, int | None, str, int, str]]) -> None:
        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "create table request_logs (timestamp integer, http_status integer, "
                "error_category text, latency_ms integer, requested_model text)"
            )
            connection.executemany(
                "insert into request_logs values (?, ?, ?, ?, ?)", rows
            )
            connection.commit()
        finally:
            connection.close()

    def test_read_rows_filters_by_time_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "logs.sqlite"
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            old_ms = now_ms - 10 * 3600 * 1000
            self._make_db(
                db,
                [
                    (now_ms, 429, "quota_or_rate_limit", 45045, "gpt-6.1-sol"),
                    (old_ms, 429, "quota_or_rate_limit", 45045, "gpt-6.1-sol"),
                ],
            )
            rows = read_rows(db, now_ms - 3600 * 1000)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, 429)
            self.assertEqual(rows[0].model, "gpt-6.1-sol")

    def test_missing_database_yields_no_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_rows(Path(tmp) / "absent.sqlite", 0), [])

    def test_main_reports_json_for_a_synthetic_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "logs.sqlite"
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            self._make_db(
                db,
                [
                    (now_ms, 429, "quota_or_rate_limit", 45045, "gpt-6.1-sol"),
                    (now_ms, 502, "upstream_error", 4202, "gpt-5.6-terra"),
                    (now_ms, 200, "", 2000, "glm-5.3"),
                ],
            )
            collection = Path(tmp) / "codex_local_access.json"
            collection.write_text(
                json.dumps({"accountConcurrencyWaitMs": 45000}), encoding="utf-8"
            )
            exit_code = main(
                [
                    "--db",
                    str(db),
                    "--collection",
                    str(collection),
                    "--hours",
                    "1",
                    "--json",
                ]
            )
            self.assertEqual(exit_code, 0)

    def test_main_signals_a_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exit_code = main(["--db", str(Path(tmp) / "absent.sqlite"), "--hours", "1"])
            self.assertEqual(exit_code, 2)


class RenderTests(unittest.TestCase):
    def test_remote_section_is_curated_not_a_raw_dump(self) -> None:
        facts = {
            "contract": "DOCTOR_CONTRACT_OK",
            "cooldown_state": "none",
            "luna_state": "available",
            "oauth_days_left": "3",
            # A raw gateway-statuses block is present in the facts and must not
            # be echoed: the tool has to stay readable under pressure.
            "gateway_statuses": {"limit_markers": {"PASSED/PASSED": 945}},
            "statuses_by_client_class": {"external/429": 17, "loopback/429": 20},
            "statuses_by_hour": {"2026-10-02T15": {"total": 12, "429": 2}},
            "overload": {"retained_overload_request_files": 3, "overload_markers": 6},
        }
        reports = {name: LayerReport(name) for name in LAYER_ORDER}
        text = cast(str, render(reports, facts, BUDGETS, 4))
        self.assertIn("contract=DOCTOR_CONTRACT_OK", text)
        self.assertIn("external statuses={'429': 17}", text)
        self.assertIn("loopback statuses={'429': 20}", text)
        self.assertIn("retained_overload_request_files=3", text)
        self.assertIn("2026-10-02T15 total=12 429=2", text)
        self.assertNotIn("limit_markers", text)

    def test_empty_facts_render_without_a_remote_section(self) -> None:
        reports = {name: LayerReport(name) for name in LAYER_ORDER}
        text = cast(str, render(reports, {}, BUDGETS, 1))
        self.assertNotIn("-- remote facts --", text)
        self.assertIn("No attributed failures in the window.", text)


if __name__ == "__main__":
    unittest.main()
