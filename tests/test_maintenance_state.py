"""Attempt accounting and refund semantics for the unattended state store.

The attempt counter is what stops an unattended run from retrying the same pin
forever, and the refund is what stops a transient MAINTENANCE_BUSY collision
from consuming a slot. Both live behind SQLite transactions that no flow test
exercises directly, so the arithmetic is pinned here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vps_ssh_launcher.maintenance import state
from vps_ssh_launcher.maintenance.models import MaintenanceAction, MaintenancePlan


def _plan(*, refundable: bool = True) -> MaintenancePlan:
    return MaintenancePlan(
        plan_id="plan-0123456789abcdef",
        created_at="2026-11-14T00:00:00+00:00",
        policy_fingerprint="policy",
        inventory_fingerprint="inventory",
        actions=(
            MaintenanceAction(
                profile="bwg",
                resource="xray",
                desired="upgrade",
                observed="25.0.0",
                status="applied",
                reason="started",
            ),
        ),
        status="applied",
        may_refund_unattended_attempt=refundable,
    )


class AutomationAttemptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "state.db"
        state.save_plan_if_absent(self.path, _plan())  # (plan, newly_stored)

    def _book(self, *, at: str = "2026-11-14T00:00:00+00:00") -> int:
        return state.record_automation_attempt(
            self.path,
            profile="bwg",
            resource="xray",
            pin_fingerprint="pin",
            plan_id="plan-0123456789abcdef",
            attempted_at=at,
        )

    def test_the_first_attempt_books_one(self) -> None:
        self.assertEqual(self._book(), 1)

    def test_attempts_accumulate(self) -> None:
        self._book()
        self.assertEqual(self._book(), 2)

    def test_a_missing_target_reads_as_none(self) -> None:
        self.assertIsNone(
            state.load_automation_target(
                self.path, profile="bwg", resource="xray", pin_fingerprint="other"
            )
        )

    def test_the_stored_target_reflects_the_latest_booking(self) -> None:
        self._book(at="2026-11-14T01:00:00+00:00")
        target = state.load_automation_target(
            self.path, profile="bwg", resource="xray", pin_fingerprint="pin"
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target["attempt_count"], 1)
        self.assertEqual(target["last_attempt_at"], "2026-11-14T01:00:00+00:00")
        self.assertEqual(target["last_plan_id"], "plan-0123456789abcdef")

    def test_a_refund_returns_the_slot_and_marks_the_outcome_busy(self) -> None:
        self._book()
        state.refund_automation_attempt(
            self.path,
            profile="bwg",
            resource="xray",
            pin_fingerprint="pin",
            plan_id="plan-0123456789abcdef",
            attempted_at="2026-11-14T02:00:00+00:00",
        )
        target = state.load_automation_target(
            self.path, profile="bwg", resource="xray", pin_fingerprint="pin"
        )
        assert target is not None
        self.assertEqual(target["attempt_count"], 0)
        self.assertEqual(target["last_outcome"], "busy")

    def test_a_second_refund_of_the_same_attempt_is_refused(self) -> None:
        # The refund clears the plan's refund authority in the same transaction,
        # so a double refund cannot push the counter below zero.
        self._book()
        state.refund_automation_attempt(
            self.path,
            profile="bwg",
            resource="xray",
            pin_fingerprint="pin",
            plan_id="plan-0123456789abcdef",
        )
        with self.assertRaisesRegex(ValueError, "no refundable"):
            state.refund_automation_attempt(
                self.path,
                profile="bwg",
                resource="xray",
                pin_fingerprint="pin",
                plan_id="plan-0123456789abcdef",
            )
        target = state.load_automation_target(
            self.path, profile="bwg", resource="xray", pin_fingerprint="pin"
        )
        assert target is not None
        self.assertEqual(target["attempt_count"], 0)

    def test_a_plan_without_a_refundable_attempt_is_refused(self) -> None:
        # A stale plan must not be able to refund an attempt it never booked.
        # save_plan_if_absent() forces the flag on a fresh plan, so a
        # non-refundable row is written the way the workflow writes it: the
        # fresh plan, then the same plan with the authority withdrawn.
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            state.save_plan_if_absent(path, _plan(refundable=True))
            state.save_plan(
                path,
                replace(_plan(refundable=True), may_refund_unattended_attempt=False),
            )
            with self.assertRaisesRegex(ValueError, "no refundable"):
                state.refund_automation_attempt(
                    path,
                    profile="bwg",
                    resource="xray",
                    pin_fingerprint="pin",
                    plan_id="plan-0123456789abcdef",
                )

    def test_a_missing_plan_cannot_refund(self) -> None:
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            state.refund_automation_attempt(
                self.path,
                profile="bwg",
                resource="xray",
                pin_fingerprint="pin",
                plan_id="plan-absent",
            )

    def test_busy_restore_requires_a_mappable_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be safely mapped"):
            state.restore_unattended_busy_plan(
                self.path,
                plan_id="plan-0123456789abcdef",
                action_profile="bwg",
                action_resource="docker",
            )

    def test_a_path_like_plan_id_never_matches_a_row(self) -> None:
        # The id is bound as a parameter, so a path-like value cannot escape;
        # the format check runs on the row that comes back, not on the input.
        # Assert the reachable outcome rather than an imagined pre-SQL refusal.
        with self.assertRaisesRegex(ValueError, "No stored maintenance plan"):
            state.load_plan(self.path, "../../etc/passwd")

    def test_a_well_formed_but_absent_plan_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "No stored maintenance plan"):
            state.load_plan(self.path, "plan-ffffffffffffffff")

    def test_the_latest_plan_is_the_default_read(self) -> None:
        plan = state.load_plan(self.path)
        self.assertEqual(plan.plan_id, "plan-0123456789abcdef")

    def test_a_finished_plan_is_not_overwritten_by_a_later_save(self) -> None:
        # save_plan() refuses to touch a plan past planned/ready/blocked; the
        # stored execution must survive a stale in-memory copy.
        from dataclasses import replace

        finished = replace(state.load_plan(self.path), status="verified")
        state.save_plan(self.path, finished)
        state.save_plan(self.path, _plan())
        self.assertEqual(state.load_plan(self.path).status, "verified")

    def test_listing_plans_returns_most_recent_first(self) -> None:
        entries = state.list_plans(self.path)
        self.assertEqual(entries[0]["plan_id"], "plan-0123456789abcdef")
        self.assertIn("status", entries[0])


if __name__ == "__main__":
    unittest.main()
