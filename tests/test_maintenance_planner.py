"""Status precedence for plan transitions and the plan-build refusals.

`updated_plan` decides what the stored plan reports after one action moves,
and that status is what a later `apply` revalidates against. Precedence is not
obvious from reading the if-chain, so each combination is pinned here.
"""

from __future__ import annotations

import unittest

from vps_ssh_launcher.maintenance.models import (
    InventoryRecord,
    InventorySnapshot,
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
)
from vps_ssh_launcher.maintenance.planner import build_plan, updated_plan


def _action(status: str, *, resource: str = "xray") -> MaintenanceAction:
    return MaintenanceAction(
        profile="bwg",
        resource=resource,
        desired="upgrade",
        observed="present",
        status=status,  # type: ignore[arg-type]
        reason="fixture",
    )


def _plan(*statuses: str) -> MaintenancePlan:
    return MaintenancePlan(
        plan_id="plan-fixture",
        created_at="2026-11-14T00:00:00+00:00",
        policy_fingerprint="policy",
        inventory_fingerprint="inventory",
        actions=tuple(_action(status) for status in statuses),
        status="planned",
    )


class StatusPrecedenceTests(unittest.TestCase):
    def test_applied_outranks_every_other_terminal_status(self) -> None:
        plan = _plan("verified", "rolled_back")
        result = updated_plan(plan, action_index=0, status="applied", reason="r")
        self.assertEqual(result.status, "applied")

    def test_refused_before_mutation_outranks_unverified(self) -> None:
        plan = _plan("unverified", "verified")
        result = updated_plan(
            plan, action_index=0, status="refused_before_mutation", reason="r"
        )
        self.assertEqual(result.status, "refused_before_mutation")

    def test_unverified_outranks_rolled_back(self) -> None:
        plan = _plan("rolled_back", "verified")
        result = updated_plan(plan, action_index=0, status="unverified", reason="r")
        self.assertEqual(result.status, "unverified")

    def test_rolled_back_outranks_verified(self) -> None:
        plan = _plan("verified", "verified")
        result = updated_plan(plan, action_index=0, status="rolled_back", reason="r")
        self.assertEqual(result.status, "rolled_back")

    def test_any_remaining_planned_action_keeps_the_plan_planned(self) -> None:
        plan = _plan("verified", "verified")
        result = updated_plan(plan, action_index=0, status="verified", reason="r")
        self.assertEqual(result.status, "verified")

    def test_a_deferred_plan_stays_planned(self) -> None:
        plan = _plan("deferred", "noop")
        result = updated_plan(plan, action_index=0, status="deferred", reason="r")
        self.assertEqual(result.status, "planned")

    def test_an_unknown_end_state_keeps_the_previous_plan_status(self) -> None:
        plan = _plan("noop")
        result = updated_plan(plan, action_index=0, status="noop", reason="r")
        self.assertEqual(result.status, "planned")

    def test_only_the_targeted_action_changes(self) -> None:
        plan = _plan("planned", "planned")
        result = updated_plan(plan, action_index=1, status="verified", reason="done")
        self.assertEqual(result.actions[0].status, "planned")
        self.assertEqual(result.actions[1].status, "verified")
        self.assertEqual(result.actions[1].reason, "done")


class BuildPlanRefusalTests(unittest.TestCase):
    def _policy(self) -> MaintenancePolicy:
        from vps_ssh_launcher.maintenance.config import load_policy

        import tempfile
        from pathlib import Path

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "maintenance.toml"
        path.write_text(
            "[settings]\n"
            "strict_host_key_checking = true\n"
            "command_timeout = 10\n"
            "\n"
            "[profiles.bwg]\n"
            "enabled = true\n"
            "\n"
            "[profiles.bwg.resources]\n"
            'xray = "upgrade"\n'
            "\n"
            "[profiles.disabled]\n"
            "enabled = false\n"
            "\n"
            "[profiles.disabled.resources]\n"
            'xray = "upgrade"\n',
            encoding="utf-8",
        )
        return load_policy(path)

    def _inventory(self, *records: InventoryRecord) -> InventorySnapshot:
        from vps_ssh_launcher.maintenance.fingerprint import inventory_fingerprint

        return InventorySnapshot(
            "2026-11-14T00:00:00+00:00",
            tuple(records),
            inventory_fingerprint(tuple(records)),
        )

    def test_a_disabled_profile_contributes_no_actions(self) -> None:
        policy = self._policy()
        inventory = self._inventory(
            InventoryRecord(profile="bwg", reachable=True, facts={}),
            InventoryRecord(profile="disabled", reachable=True, facts={}),
        )
        plan = build_plan(policy, inventory)
        self.assertEqual({action.profile for action in plan.actions}, {"bwg"})

    def test_an_unreachable_profile_blocks_every_action(self) -> None:
        policy = self._policy()
        inventory = self._inventory(
            InventoryRecord(profile="bwg", reachable=False, facts={}),
        )
        plan = build_plan(policy, inventory)
        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.actions[0].observed, "unreachable")

    def test_the_fingerprint_refuses_a_blank_profile_name(self) -> None:
        from vps_ssh_launcher.maintenance.fingerprint import inventory_fingerprint

        # The fingerprint is the gate that runs first; build_plan repeats the
        # check defensively, so this is the reachable refusal.
        with self.assertRaisesRegex(ValueError, "non-empty profile"):
            inventory_fingerprint(
                (InventoryRecord(profile="  ", reachable=True, facts={}),)
            )

    def test_the_fingerprint_refuses_a_duplicate_profile(self) -> None:
        from vps_ssh_launcher.maintenance.fingerprint import inventory_fingerprint

        records = (
            InventoryRecord(profile="bwg", reachable=True, facts={}),
            InventoryRecord(profile="bwg", reachable=True, facts={}),
        )
        with self.assertRaisesRegex(ValueError, "duplicate profile"):
            inventory_fingerprint(records)

    def test_build_plan_repeats_the_duplicate_check_defensively(self) -> None:
        # A snapshot carrying a fingerprint that was not derived from its
        # records must still not plan twice against the same host.
        policy = self._policy()
        duplicate = (
            InventoryRecord(profile="bwg", reachable=True, facts={}),
            InventoryRecord(profile="bwg", reachable=True, facts={}),
        )
        snapshot = InventorySnapshot("2026-11-14T00:00:00+00:00", duplicate, "x")
        with self.assertRaisesRegex(ValueError, "duplicate profile"):
            build_plan(policy, snapshot)

    def test_the_plan_id_is_deterministic_for_the_same_inputs(self) -> None:
        policy = self._policy()
        inventory = self._inventory(
            InventoryRecord(profile="bwg", reachable=True, facts={}),
        )
        first = build_plan(policy, inventory)
        second = build_plan(policy, inventory)
        self.assertEqual(first.plan_id, second.plan_id)


if __name__ == "__main__":
    unittest.main()
