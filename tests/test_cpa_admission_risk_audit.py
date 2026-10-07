"""Tests for scripts/cpa_admission_risk_audit.py.

The audit is the only place that turns the admission *contract* and the
advertised-vs-observed join into a machine-checked verdict, so every rule and
every severity boundary is pinned here. The severity ladder matters as much as
the rule itself: a route that fails 77% of the time is a defect, while the OAuth
lane answering 503 whenever its single subscription account is busy is expected
behaviour and must stay informational.
"""

from __future__ import annotations

import io
import json
import runpy
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Sequence, cast

MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "cpa_admission_risk_audit.py")
)
Finding = cast(Any, MODULE["Finding"])
advertised_aliases = cast(Any, MODULE["advertised_aliases"])
audit_contract = cast(Any, MODULE["audit_contract"])
audit_posture = cast(Any, MODULE["audit_posture"])
observed_by_model = cast(Any, MODULE["observed_by_model"])
observed_totals = cast(Any, MODULE["observed_totals"])
observed_late_stream_failures = cast(Any, MODULE["observed_late_stream_failures"])
observed_last_failure_age_minutes = cast(
    Any, MODULE["observed_last_failure_age_minutes"]
)
observed_live_failure_by_model = cast(Any, MODULE["observed_live_failure_by_model"])
render = cast(Any, MODULE["render"])
main = cast(Any, MODULE["main"])
SEVERITY_FAIL = cast(str, MODULE["SEVERITY_FAIL"])
SEVERITY_WARN = cast(str, MODULE["SEVERITY_WARN"])
SEVERITY_INFO = cast(str, MODULE["SEVERITY_INFO"])


def codes(findings: list[Any], severity: str | None = None) -> list[str]:
    return [
        finding.code
        for finding in findings
        if severity is None or finding.severity == severity
    ]


def admission_config(
    *,
    schedule: list[int] | None = None,
    ladder_cap: int = 900,
    server_cap: int = 900,
    statuses: list[int] | None = None,
    markers: list[str] | None = None,
    probe_bytes: int = 262144,
    lanes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if lanes is None:
        lanes = [
            {
                "name": "chatgpt-oauth",
                "models": ["gpt-6-luna"],
                "max_inflight": 2,
                "max_pending": 4,
                "queue_timeout_seconds": 120,
                "cooldown_schedule_seconds": schedule or [60, 120, 240, 480, 900],
                "cooldown_cap_seconds": ladder_cap,
                "capacity_statuses": statuses or [429, 503],
                "capacity_markers": markers
                or ["server_is_overloaded", "usage_limit_reached"],
            }
        ]
    return {
        "version": 1,
        "probe_bytes": probe_bytes,
        "retry_after_max_seconds": server_cap,
        "lanes": lanes,
    }


def routes_manifest(
    *,
    providers: list[dict[str, Any]] | None = None,
    oauth_routes: list[dict[str, Any]] | None = None,
    oauth_exclusions: list[str] | None = None,
    key_exclusions: list[str] | None = None,
) -> dict[str, Any]:
    if providers is None:
        providers = [
            {
                "slot": 1,
                "name": "ai.input.im",
                "models": [{"name": "gpt-6.1-sol", "alias": "gpt-6.1-sol-input"}],
            }
        ]
    if oauth_routes is None:
        oauth_routes = [
            {
                "name": "chatgpt-plus-oauth",
                "models": [{"name": "gpt-6-luna", "alias": "gpt-6-luna"}],
            }
        ]
    if oauth_exclusions is None:
        oauth_exclusions = ["gpt-6.1-sol-input"]
    if key_exclusions is None:
        key_exclusions = ["gpt-6.1-sol-input", "gpt-6-luna"]
    return {
        "version": 1,
        "providers": providers,
        "oauth_routes": oauth_routes,
        "oauth_exclusions": oauth_exclusions,
        "codex_api_key_exclusions": key_exclusions,
    }


def write_db(path: Path, rows: Sequence[tuple[int, int | None, str]]) -> None:
    """Synthetic client log with the app's own ``success`` verdict.

    ``success`` is derived the way the real app writes it: a 2xx is a success
    *unless* the stream itself reported an error, which is exactly the
    late-stream shape the audit has to count as a failure.
    """

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "create table request_logs ("
            "timestamp integer, http_status integer, requested_model text, success integer)"
        )
        connection.executemany(
            "insert into request_logs values (?, ?, ?, ?)",
            [
                (ts, status, model, 1 if status is not None and status < 300 else 0)
                for ts, status, model in rows
            ],
        )
        connection.commit()
    finally:
        connection.close()


def write_db_raw(path: Path, rows: Sequence[tuple[int, int | None, str, int]]) -> None:
    """Same table, but with ``success`` spelled out (to pin the authority)."""

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "create table request_logs ("
            "timestamp integer, http_status integer, requested_model text, success integer)"
        )
        connection.executemany("insert into request_logs values (?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()


class AdvertisedAliasesTests(unittest.TestCase):
    def test_provider_and_oauth_aliases_are_both_advertised(self) -> None:
        owners = advertised_aliases(routes_manifest())
        self.assertEqual(owners["gpt-6.1-sol-input"], "slot1:ai.input.im")
        self.assertEqual(owners["gpt-6-luna"], "oauth:chatgpt-plus-oauth")

    def test_first_slot_wins_on_a_collision_but_collision_is_reported(self) -> None:
        routes = routes_manifest(
            providers=[
                {"slot": 1, "name": "a", "models": [{"name": "x", "alias": "dup"}]},
                {"slot": 2, "name": "b", "models": [{"name": "x", "alias": "dup"}]},
            ]
        )
        owners = advertised_aliases(routes)
        self.assertEqual(owners["dup"], "slot1:a")
        findings = audit_contract(admission_config(), routes)
        self.assertIn("alias-multi-slot", codes(findings, SEVERITY_FAIL))


class ContractTests(unittest.TestCase):
    def test_clean_configuration_has_no_findings(self) -> None:
        findings = audit_contract(admission_config(), routes_manifest())
        self.assertEqual(findings, [])

    def test_ladder_must_end_at_the_declared_cap(self) -> None:
        config = admission_config(schedule=[60, 120, 240, 480], ladder_cap=900)
        findings = audit_contract(config, routes_manifest())
        self.assertIn("lane-cooldown-ladder-mismatch", codes(findings, SEVERITY_FAIL))

    def test_missing_ladder_is_a_failure(self) -> None:
        config = admission_config(schedule=[])
        config["lanes"][0]["cooldown_schedule_seconds"] = []
        findings = audit_contract(config, routes_manifest())
        self.assertIn("lane-cooldown-ladder-missing", codes(findings, SEVERITY_FAIL))

    def test_server_cap_above_ladder_cap_is_one_aggregated_warning(self) -> None:
        lanes = [
            {
                "name": name,
                "models": ["gpt-6-luna"],
                "cooldown_schedule_seconds": [60, 900],
                "cooldown_cap_seconds": 900,
                "capacity_statuses": [429, 503],
                "capacity_markers": ["server_is_overloaded", "usage_limit_reached"],
            }
            for name in ("lane-a", "lane-b")
        ]
        config = admission_config(lanes=lanes, server_cap=86400)
        findings = audit_contract(config, routes_manifest())
        warns = [f for f in findings if f.code == "lane-cooldown-server-cap"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0].severity, SEVERITY_WARN)
        self.assertIn("lane-a", warns[0].message)
        self.assertIn("lane-b", warns[0].message)

    def test_server_cap_equal_to_ladder_cap_is_not_reported(self) -> None:
        config = admission_config(server_cap=900)
        findings = audit_contract(config, routes_manifest())
        self.assertNotIn("lane-cooldown-server-cap", codes(findings))

    def test_capacity_statuses_must_cover_429_and_503(self) -> None:
        config = admission_config(statuses=[503])
        findings = audit_contract(config, routes_manifest())
        self.assertIn("lane-capacity-status-missing", codes(findings, SEVERITY_FAIL))

    def test_capacity_markers_must_cover_both_upstream_signals(self) -> None:
        config = admission_config(markers=["server_is_overloaded"])
        findings = audit_contract(config, routes_manifest())
        self.assertIn("lane-capacity-marker-missing", codes(findings, SEVERITY_FAIL))

    def test_probe_window_below_baseline_warns(self) -> None:
        config = admission_config(probe_bytes=4096)
        findings = audit_contract(config, routes_manifest())
        self.assertIn("capacity-probe-window", codes(findings, SEVERITY_WARN))

    def test_lane_member_that_no_route_advertises_fails(self) -> None:
        config = admission_config()
        config["lanes"][0]["models"] = ["gpt-6-luna", "retired-model"]
        findings = audit_contract(config, routes_manifest())
        self.assertIn("lane-model-not-advertised", codes(findings, SEVERITY_FAIL))

    def test_model_in_two_lanes_fails(self) -> None:
        lanes = [
            {
                "name": "lane-a",
                "models": ["gpt-6-luna"],
                "cooldown_schedule_seconds": [60, 900],
                "cooldown_cap_seconds": 900,
                "capacity_statuses": [429, 503],
                "capacity_markers": ["server_is_overloaded", "usage_limit_reached"],
            },
            {
                "name": "lane-b",
                "models": ["gpt-6-luna"],
                "cooldown_schedule_seconds": [60, 900],
                "cooldown_cap_seconds": 900,
                "capacity_statuses": [429, 503],
                "capacity_markers": ["server_is_overloaded", "usage_limit_reached"],
            },
        ]
        findings = audit_contract(admission_config(lanes=lanes), routes_manifest())
        self.assertIn("lane-model-in-multiple-lanes", codes(findings, SEVERITY_FAIL))

    def test_gpt_route_missing_from_oauth_exclusions_fails(self) -> None:
        routes = routes_manifest(oauth_exclusions=[])
        findings = audit_contract(admission_config(), routes)
        self.assertIn("oauth-exclusion-violation", codes(findings, SEVERITY_FAIL))

    def test_alias_missing_from_codex_key_exclusions_fails(self) -> None:
        routes = routes_manifest(key_exclusions=["gpt-6.1-sol-input"])
        findings = audit_contract(admission_config(), routes)
        self.assertIn("codex-key-exclusion-violation", codes(findings, SEVERITY_FAIL))

    def test_no_lanes_fails(self) -> None:
        findings = audit_contract(admission_config(lanes=[]), routes_manifest())
        self.assertIn("admission-no-lanes", codes(findings, SEVERITY_FAIL))


class LaneRouteCoverageTests(unittest.TestCase):
    """A route naming a shared-account lane must be fully gated by that lane.

    The manifest declares the intent (``admission_lane``) and the admission config
    implements it (``lanes[].models``). Nothing else joins the two, so a model
    added to the route without being added to the lane reaches the shared account
    with no concurrency bound and no capacity breaker.
    """

    def _gated_routes(self, *, lane: str, aliases: list[str]) -> dict[str, Any]:
        return routes_manifest(
            oauth_routes=[
                {
                    "name": "chatgpt-plus-oauth",
                    "admission_lane": lane,
                    "models": [{"name": a, "alias": a} for a in aliases],
                }
            ],
            oauth_exclusions=[],
            key_exclusions=[],
        )

    def test_matching_lane_and_route_has_no_coverage_finding(self) -> None:
        routes = self._gated_routes(lane="chatgpt-oauth", aliases=["gpt-6-luna"])
        findings = audit_contract(admission_config(), routes)
        self.assertNotIn("lane-route-coverage-missing", codes(findings))

    def test_route_alias_the_lane_does_not_gate_fails(self) -> None:
        routes = self._gated_routes(
            lane="chatgpt-oauth", aliases=["gpt-6-luna", "gpt-9-ungated"]
        )
        findings = audit_contract(admission_config(), routes)
        failed = [
            finding
            for finding in findings
            if finding.code == "lane-route-coverage-missing"
            and finding.severity == SEVERITY_FAIL
        ]
        self.assertEqual(len(failed), 1)
        self.assertIn("gpt-9-ungated", failed[0].message)

    def test_route_naming_an_undefined_lane_fails(self) -> None:
        routes = self._gated_routes(lane="lane-that-does-not-exist", aliases=["x"])
        findings = audit_contract(admission_config(), routes)
        self.assertIn("lane-route-coverage-missing", codes(findings, SEVERITY_FAIL))

    def test_provider_slot_lane_declaration_is_covered_too(self) -> None:
        routes = routes_manifest(
            providers=[
                {
                    "slot": 4,
                    "name": "zhipu-plan",
                    "admission_lane": "zhipu-coding-plan",
                    "models": [{"name": "glm-5.3", "alias": "glm-5.3"}],
                }
            ],
            oauth_exclusions=[],
            key_exclusions=["glm-5.3"],
        )
        findings = audit_contract(admission_config(), routes)
        self.assertIn("lane-route-coverage-missing", codes(findings, SEVERITY_FAIL))

    def test_route_without_a_lane_declaration_is_not_checked(self) -> None:
        findings = audit_contract(admission_config(), routes_manifest())
        self.assertNotIn("lane-route-coverage-missing", codes(findings))


class PostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "logs.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_database_is_empty_not_an_error(self) -> None:
        self.assertEqual(observed_by_model(self.db, 24), {})
        self.assertEqual(observed_totals(self.db, 24), (0, 0))
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertEqual(codes(findings), ["posture-no-traffic"])

    def test_empty_database_is_no_traffic(self) -> None:
        write_db(self.db, [])
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertEqual(codes(findings), ["posture-no-traffic"])

    def test_majority_failure_of_an_advertised_route_is_a_failure(self) -> None:
        # A FAIL verdict requires the model's *newest* row to have failed. A route
        # that answered successfully afterwards is reported as stale instead --
        # otherwise a burst that ended hours ago keeps the gate red forever.
        base = 1_800_000_000_000
        rows = [(base + i, 200, "gpt-6.1-sol-input") for i in range(1)]
        rows += [(base + 100 + i, 502, "gpt-6.1-sol-input") for i in range(9)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))

    def test_flagged_route_also_reports_the_attribution_boundary(self) -> None:
        # The name->slot mapping is a manifest projection. Measured case: the
        # client asked for one alias and the gateway journal recorded a
        # different model for the same requests, so the finding must not be read
        # as "this slot is broken".
        base = 1_800_000_000_000
        rows = [(base + i, 502, "gpt-6.1-sol-input") for i in range(9)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("attribution-boundary", codes(findings, SEVERITY_INFO))

    def test_clean_traffic_does_not_report_the_attribution_boundary(self) -> None:
        base = 1_800_000_000_000
        write_db(self.db, [(base + i, 200, "gpt-6.1-sol-input") for i in range(9)])
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertNotIn("attribution-boundary", codes(findings))


class DataStalenessTests(unittest.TestCase):
    """A posture window anchored at a log that stopped updating proves nothing live."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "logs.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _failing_rows(self, newest_ms: int) -> list[tuple[int, int, str]]:
        return [(newest_ms - i * 60_000, 502, "gpt-6.1-sol-input") for i in range(9)]

    def test_stale_database_downgrades_live_failure_to_warning(self) -> None:
        # Measured case (2026-10-07): the desktop went direct on 10/5 01:04,
        # the local gate database went silent, and the audit kept reporting
        # two-day-old 31/31 failures as a live FAIL (exit 1) even though the
        # upstream had recovered. A stale log must not produce a live verdict.
        newest = int(time.time() * 1000) - int(30 * 3600 * 1000)
        write_db(self.db, self._failing_rows(newest))
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertNotIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))
        self.assertIn("posture-data-stale", codes(findings, SEVERITY_WARN))
        downgraded = [
            f for f in findings if f.code == "advertised-failing-route-stale-data"
        ]
        self.assertEqual(len(downgraded), 1)
        self.assertEqual(downgraded[0].severity, SEVERITY_WARN)
        self.assertIn(
            "stopped updating",
            [f for f in findings if f.code == "posture-data-stale"][0].message,
        )

    def test_fresh_database_still_reports_live_failure(self) -> None:
        newest = int(time.time() * 1000) - int(3600 * 1000)
        write_db(self.db, self._failing_rows(newest))
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))
        self.assertNotIn("posture-data-stale", codes(findings, SEVERITY_WARN))
        self.assertNotIn("advertised-failing-route-stale-data", codes(findings))

    def test_future_timestamps_do_not_count_as_stale(self) -> None:
        # The legacy fixtures pin rows at a fixed future epoch; clock skew or
        # fixture data must never downgrade a live verdict.
        newest = int(time.time() * 1000) + int(3600 * 1000)
        write_db(self.db, self._failing_rows(newest))
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))


class LastFailureAgeTests(unittest.TestCase):
    """A window can span a config change, so a failure count alone is ambiguous."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "logs.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_age_is_measured_from_the_newest_row(self) -> None:
        base = 1_800_000_000_000
        rows = [(base, 502, "stale"), (base + 60 * 60 * 1000, 200, "ok")]
        write_db(self.db, rows)
        self.assertEqual(observed_last_failure_age_minutes(self.db, 24), {"stale": 60})

    def test_a_model_that_recovered_reports_a_large_age(self) -> None:
        base = 1_800_000_000_000
        rows = [(base, 502, "m") for _ in range(9)]
        rows += [(base + 300 * 60 * 1000, 200, "m")]
        write_db(self.db, rows)
        self.assertEqual(observed_last_failure_age_minutes(self.db, 24), {"m": 300})

    def test_age_is_absent_for_a_model_with_no_failures(self) -> None:
        base = 1_800_000_000_000
        write_db(self.db, [(base + i, 200, "m") for i in range(5)])
        self.assertEqual(observed_last_failure_age_minutes(self.db, 24), {})

    def test_finding_message_carries_the_age(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 502, "gpt-6.1-sol-input") for i in range(9)]
        rows += [(base + 120 * 60 * 1000, 200, "gpt-6.1-sol-input")]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        flagged = [f for f in findings if f.code == "advertised-failing-route-stale"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].severity, SEVERITY_WARN)
        self.assertIn("last failure 120 min before the newest row", flagged[0].message)
        self.assertIn("historical outage", flagged[0].message)

    def test_live_failure_by_model_reports_the_newest_row_only(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 502, "a") for i in range(9)]
        rows += [(base + 1000, 200, "a")]
        rows += [(base + i, 200, "b") for i in range(9)]
        rows += [(base + 1000, 502, "b")]
        write_db(self.db, rows)
        self.assertEqual(
            observed_live_failure_by_model(self.db, 24), {"a": False, "b": True}
        )

    def test_live_failure_is_absent_for_a_model_with_no_rows(self) -> None:
        self.assertEqual(observed_live_failure_by_model(self.db, 24), {})

    def test_recovered_route_is_stale_not_failing(self) -> None:
        # The measured shape (2026-10-04): 90 failures over 24h, last one 656 min
        # before the newest row, and a successful request 79 min before it. The
        # route was healthy; only the window still remembered the outage.
        base = 1_800_000_000_000
        rows = [(base + i, 502, "gpt-6.1-sol-input") for i in range(9)]
        rows += [(base + 600 * 60 * 1000, 200, "gpt-6.1-sol-input")]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertNotIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))
        stale = [f for f in findings if f.code == "advertised-failing-route-stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].severity, SEVERITY_WARN)
        self.assertIn("600 min before the newest row", stale[0].message)

    def test_failure_after_a_recovery_is_still_live(self) -> None:
        base = 1_800_000_000_000
        rows = [(base, 502, "gpt-6.1-sol-input")]
        rows += [(base + 1000, 200, "gpt-6.1-sol-input")]
        rows += [(base + 2000 + i, 502, "gpt-6.1-sol-input") for i in range(10)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))
        self.assertNotIn("advertised-failing-route-stale", codes(findings))

    def test_occasional_failure_of_an_advertised_route_is_informational(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 503, "gpt-6-luna") for i in range(6)]
        rows += [(base + 100 + i, 200, "gpt-6-luna") for i in range(200)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-degraded-route", codes(findings, SEVERITY_INFO))
        self.assertNotIn("advertised-failing-route", codes(findings, SEVERITY_FAIL))

    def test_mid_range_failure_rate_warns(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 503, "gpt-6-luna") for i in range(30)]
        rows += [(base + 100 + i, 200, "gpt-6-luna") for i in range(70)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("advertised-degraded-route", codes(findings, SEVERITY_WARN))

    def test_failing_model_no_route_advertises_warns(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 502, "ghost-model") for i in range(20)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertIn("failing-unadvertised-model", codes(findings, SEVERITY_WARN))

    def test_below_threshold_counts_are_not_reported(self) -> None:
        base = 1_800_000_000_000
        rows = [(base + i, 502, "gpt-6.1-sol-input") for i in range(4)]
        rows += [(base + 100 + i, 200, "gpt-6.1-sol-input") for i in range(1)]
        write_db(self.db, rows)
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertNotIn("advertised-failing-route", codes(findings))
        self.assertNotIn("advertised-degraded-route", codes(findings))

    def test_window_is_anchored_to_the_newest_row(self) -> None:
        base = 1_800_000_000_000
        rows = [(base, 502, "old-model"), (base + 48 * 3600 * 1000, 200, "new-model")]
        write_db(self.db, rows)
        per_model = observed_by_model(self.db, 24)
        self.assertEqual(set(per_model), {"new-model"})
        total, errors = observed_totals(self.db, 24)
        self.assertEqual((total, errors), (1, 0))


class FailureSemanticsTests(unittest.TestCase):
    """`success` is the authority, not the HTTP status band.

    Measured on the live database: 37,150 rows carry no `http_status` and 36,206
    of them are successful. Counting a missing status as a failure inflates the
    error rate on any window that reaches back far enough.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "logs.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_status_but_successful_is_not_a_failure(self) -> None:
        base = 1_800_000_000_000
        write_db_raw(self.db, [(base + i, None, "m", 1) for i in range(9)])
        total, errors = observed_totals(self.db, 24)
        self.assertEqual((total, errors), (9, 0))

    def test_missing_status_and_failed_is_a_failure(self) -> None:
        base = 1_800_000_000_000
        write_db_raw(self.db, [(base + i, None, "m", 0) for i in range(9)])
        total, errors = observed_totals(self.db, 24)
        self.assertEqual((total, errors), (9, 9))

    def test_late_stream_failure_is_counted_and_surfaced(self) -> None:
        base = 1_800_000_000_000
        write_db_raw(
            self.db,
            [(base, 200, "m", 0), (base + 1, 200, "m", 1), (base + 2, 200, "m", 1)],
        )
        total, errors = observed_totals(self.db, 24)
        self.assertEqual((total, errors), (3, 1))
        self.assertEqual(observed_late_stream_failures(self.db, 24), 1)

    def test_late_stream_counter_is_zero_without_the_success_column(self) -> None:
        base = 1_800_000_000_000
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "create table request_logs ("
                "timestamp integer, http_status integer, requested_model text)"
            )
            connection.execute(
                "insert into request_logs values (?, ?, ?)", (base, 200, "m")
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(observed_late_stream_failures(self.db, 24), 0)

    def test_schema_without_success_falls_back_to_the_status_band(self) -> None:
        base = 1_800_000_000_000
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "create table request_logs ("
                "timestamp integer, http_status integer, requested_model text)"
            )
            connection.executemany(
                "insert into request_logs values (?, ?, ?)",
                [(base, 502, "m"), (base + 1, 200, "m")],
            )
            connection.commit()
        finally:
            connection.close()
        total, errors = observed_totals(self.db, 24)
        self.assertEqual((total, errors), (2, 1))

    def test_unparsed_bucket_is_not_reported_as_a_model(self) -> None:
        base = 1_800_000_000_000
        write_db_raw(self.db, [(base + i, None, "", 0) for i in range(50)])
        findings = audit_posture(routes_manifest(), self.db, 24)
        self.assertNotIn("failing-unadvertised-model", codes(findings))
        self.assertIn("other", observed_by_model(self.db, 24))


class RenderAndMainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _inputs(
        self, admission: dict[str, Any], routes: dict[str, Any]
    ) -> tuple[Path, Path]:
        admission_path = self.tmp / "admission.json"
        routes_path = self.tmp / "routes.json"
        admission_path.write_text(json.dumps(admission), encoding="utf-8")
        routes_path.write_text(json.dumps(routes), encoding="utf-8")
        return admission_path, routes_path

    def test_render_reports_pass_and_fail_verdicts(self) -> None:
        clean = render([Finding("x", SEVERITY_INFO, "note")], 24.0, False)
        self.assertIn("verdict: PASS", clean[-1])
        failing = render([Finding("x", SEVERITY_FAIL, "bad")], 24.0, False)
        self.assertIn("verdict: FAIL", failing[-1])

    def test_strict_promotes_warnings_to_failure(self) -> None:
        warnings = [Finding("x", SEVERITY_WARN, "careful")]
        self.assertIn("verdict: PASS with warnings", render(warnings, 24.0, False)[-1])
        self.assertIn("verdict: FAIL (strict)", render(warnings, 24.0, True)[-1])

    def test_main_exits_zero_on_a_clean_contract_only_run(self) -> None:
        admission_path, routes_path = self._inputs(
            admission_config(), routes_manifest()
        )
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(
                [
                    "--admission-config",
                    str(admission_path),
                    "--routes",
                    str(routes_path),
                    "--skip-posture",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("verdict: PASS", buffer.getvalue())

    def test_main_exits_one_when_a_failure_is_present(self) -> None:
        admission_path, routes_path = self._inputs(
            admission_config(lanes=[]), routes_manifest()
        )
        with redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--admission-config",
                    str(admission_path),
                    "--routes",
                    str(routes_path),
                    "--skip-posture",
                ]
            )
        self.assertEqual(code, 1)

    def test_main_exits_two_on_unreadable_input(self) -> None:
        routes_path = self.tmp / "routes.json"
        routes_path.write_text(json.dumps(routes_manifest()), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--admission-config",
                    str(self.tmp / "missing.json"),
                    "--routes",
                    str(routes_path),
                    "--skip-posture",
                ]
            )
        self.assertEqual(code, 2)

    def test_unreadable_database_skips_posture_with_a_visible_warning(self) -> None:
        # The sidecar writes the client log while this audit may read it, so a
        # locked or corrupt database is a live possibility. The contract half
        # must still run, and the skipped posture has to stay visible instead
        # of reading as a clean "no traffic" bill of health.
        admission_path, routes_path = self._inputs(
            admission_config(), routes_manifest()
        )
        db = self.tmp / "corrupt.sqlite"
        db.write_bytes(b"not a sqlite database")
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(
                [
                    "--admission-config",
                    str(admission_path),
                    "--routes",
                    str(routes_path),
                    "--db",
                    str(db),
                    "--hours",
                    "24",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("posture-unavailable", buffer.getvalue())
        self.assertIn("PASS with warnings", buffer.getvalue())

    def test_main_exits_zero_when_the_only_failure_is_stale(self) -> None:
        # Regression for the measured steady state: a burst that ended hours ago
        # used to make the standard 24h verification command exit 1 forever.
        base = 1_800_000_000_000
        db = self.tmp / "logs.sqlite"
        rows = [(base + i, 502, "gpt-6.1-sol-input") for i in range(9)]
        rows += [(base + 3600 * 1000, 200, "gpt-6.1-sol-input")]
        write_db(db, rows)
        admission_path, routes_path = self._inputs(
            admission_config(), routes_manifest()
        )
        with redirect_stdout(io.StringIO()) as buffer:
            code = main(
                [
                    "--admission-config",
                    str(admission_path),
                    "--routes",
                    str(routes_path),
                    "--db",
                    str(db),
                    "--hours",
                    "24",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("advertised-failing-route-stale", buffer.getvalue())

    def test_json_output_is_machine_readable(self) -> None:
        admission_path, routes_path = self._inputs(
            admission_config(), routes_manifest()
        )
        with redirect_stdout(io.StringIO()) as buffer:
            main(
                [
                    "--admission-config",
                    str(admission_path),
                    "--routes",
                    str(routes_path),
                    "--skip-posture",
                    "--json",
                ]
            )
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["hours"], 24.0)


if __name__ == "__main__":
    unittest.main()
