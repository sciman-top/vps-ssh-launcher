"""Refusal-before-write contract for one host's remote plan execution.

`execute_remote_plan` is the boundary that decides whether an already reviewed
plan may still touch the host. Every refusal below happens before the first
remote write, so each one is pinned by asserting that no client was opened or
that the refusal type is the structured `RemotePlanRefusal` the CLI maps to a
receipt rather than a crash.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from launcher_fakes import patch_attr
from vps_ssh_launcher.maintenance import workflow
from vps_ssh_launcher.maintenance.config import load_policy
from vps_ssh_launcher.maintenance.models import (
    InventoryRecord,
    InventorySnapshot,
    MaintenanceAction,
    MaintenancePlan,
    MaintenancePolicy,
)


def _policy(root: Path) -> MaintenancePolicy:
    path = root / "maintenance.toml"
    path.write_text(
        "[settings]\n"
        "strict_host_key_checking = true\n"
        "command_timeout = 10\n"
        "\n"
        "[profiles.bwg]\n"
        "enabled = true\n"
        "\n"
        "[profiles.bwg.resources]\n"
        'xray = "upgrade"\n',
        encoding="utf-8",
    )
    return load_policy(path)


def _target(root: Path, *, profiles: dict[str, Any] | None = None) -> Path:
    path = root / "target.json"
    path.write_text(
        json.dumps(
            {
                "profiles": (
                    {
                        "bwg": {
                            "host": "203.0.113.10",
                            "user": "root",
                            "password": "fixture",
                        }
                    }
                    if profiles is None
                    else profiles
                )
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _plan(*, fingerprint: str) -> MaintenancePlan:
    return MaintenancePlan(
        plan_id="plan-0123456789abcdef",
        created_at="2026-11-14T00:00:00+00:00",
        policy_fingerprint="policy",
        inventory_fingerprint=fingerprint,
        actions=(
            MaintenanceAction(
                profile="bwg",
                resource="xray",
                desired="upgrade",
                observed="25.0.0",
                status="planned",
                reason="planned",
            ),
        ),
        status="planned",
    )


def _snapshot(*, fingerprint: str, reachable: bool = True) -> InventorySnapshot:
    return InventorySnapshot(
        "2026-11-14T00:00:00+00:00",
        (InventoryRecord(profile="bwg", reachable=reachable, facts={}),),
        fingerprint,
    )


class RemotePlanRefusalTests(unittest.TestCase):
    def _run(
        self,
        *,
        target: Path,
        policy: MaintenancePolicy,
        plan: MaintenancePlan,
        fresh: InventorySnapshot,
        state: Path,
    ) -> tuple[MaintenancePlan, str, str, int]:
        with patch_attr(workflow, "collect_inventory", return_value=fresh):
            return workflow.execute_remote_plan(
                policy,
                plan,
                target=target,
                state_path=state,
                profile="bwg",
            )

    def test_a_drifted_inventory_fingerprint_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy(root)
            target = _target(root)
            plan = _plan(fingerprint="stored")
            with self.assertRaises(workflow.RemotePlanRefusal) as caught:
                self._run(
                    target=target,
                    policy=policy,
                    plan=plan,
                    fresh=_snapshot(fingerprint="changed"),
                    state=root / "state.db",
                )
        self.assertIn("fingerprint", str(caught.exception))

    def test_an_unreachable_fresh_probe_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy(root)
            target = _target(root)
            plan = _plan(fingerprint="same")
            with self.assertRaisesRegex(workflow.RemotePlanRefusal, "not reachable"):
                self._run(
                    target=target,
                    policy=policy,
                    plan=plan,
                    fresh=_snapshot(fingerprint="same", reachable=False),
                    state=root / "state.db",
                )

    def test_a_profile_missing_from_the_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy(root)
            target = _target(
                root,
                profiles={
                    "other": {"host": "203.0.113.11", "user": "root", "password": "x"}
                },
            )
            plan = _plan(fingerprint="same")
            with self.assertRaisesRegex(workflow.RemotePlanRefusal, "missing"):
                self._run(
                    target=target,
                    policy=policy,
                    plan=plan,
                    fresh=_snapshot(fingerprint="same"),
                    state=root / "state.db",
                )

    def test_a_non_object_profile_entry_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy(root)
            target = _target(root, profiles={"bwg": ["not-an-object"]})
            plan = _plan(fingerprint="same")
            with self.assertRaisesRegex(
                workflow.RemotePlanRefusal, "must be an object"
            ):
                self._run(
                    target=target,
                    policy=policy,
                    plan=plan,
                    fresh=_snapshot(fingerprint="same"),
                    state=root / "state.db",
                )

    def test_no_refusal_opens_a_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = _policy(root)
            target = _target(root)
            plan = _plan(fingerprint="stored")
            with (
                patch_attr(
                    workflow,
                    "collect_inventory",
                    return_value=_snapshot(fingerprint="x"),
                ),
                patch_attr(workflow.connection, "connect_with_retry") as connect,
            ):
                with self.assertRaises(workflow.RemotePlanRefusal):
                    workflow.execute_remote_plan(
                        policy,
                        plan,
                        target=target,
                        state_path=root / "state.db",
                        profile="bwg",
                    )
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
