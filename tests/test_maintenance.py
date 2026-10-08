import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from script_validation_support import ScriptValidationMixin

from vps_ssh_launcher.maintenance.automation import (
    _pid_alive,
    authorize_unattended_apply,
    pin_fingerprint,
    unattended_lock,
)
from vps_ssh_launcher.maintenance.config import (
    load_policy,
    policy_lock_path,
    _pins_table,
)
from vps_ssh_launcher.maintenance.adapters import (
    build_docker_upgrade_command,
    build_xray_upgrade_command,
    execute_action,
)
from vps_ssh_launcher.maintenance.inventory import (
    INVENTORY_COMMAND,
    inventory_fingerprint,
    load_inventory,
    parse_probe_output,
    write_inventory,
)
from vps_ssh_launcher.maintenance.models import (
    InventoryRecord,
    InventorySnapshot,
    MaintenancePlan,
    MaintenanceAction,
)
from vps_ssh_launcher.maintenance.planner import build_plan
from vps_ssh_launcher.maintenance.resources import RESOURCES, ResourceSpec
from vps_ssh_launcher.maintenance.receipt import write_receipt
from vps_ssh_launcher.maintenance.state import (
    list_plans,
    load_automation_target,
    load_plan,
    record_automation_attempt,
    record_automation_outcome,
    save_plan,
    save_plan_if_absent,
)
from vps_ssh_launcher.maintenance_cli import _execute_remote_plan, main


class MaintenanceControlPlaneTests(unittest.TestCase):
    XRaySha256 = "a" * 64
    DockerDigest = "sha256:" + "b" * 64

    def test_resource_registration_connects_policy_plan_and_execution(self) -> None:
        """An added/removed resource must not need three independent allowlists."""
        normalize = mock.Mock(return_value={"version": "1"})
        action = MaintenanceAction(
            "bwg", "fixture", "upgrade", "0", "planned", "fixture upgrade"
        )
        plan_upgrade = mock.Mock(return_value=action)
        builder = mock.Mock(return_value="fixture command")
        executor = mock.Mock(return_value=(0, "APPLY_VERIFIED\n", ""))
        spec = ResourceSpec(
            "fixture", normalize, builder, plan_upgrade, "fixture pin required"
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(RESOURCES, {"fixture": spec}),
        ):
            policy = replace(
                load_policy(self._policy(Path(tmp))),
                profiles={"bwg": {"resources": {"fixture": "upgrade"}}},
                pins=_pins_table({"fixture": {"version": "1"}}),
            )
            inventory = InventorySnapshot(
                "2026-10-08T00:00:00+00:00",
                (InventoryRecord("bwg", True, {"fixture": "0"}),),
                "fixture fingerprint",
            )
            plan = build_plan(policy, inventory)
            self.assertEqual(plan.actions, (action,))
            self.assertEqual(
                execute_action(action, pins=policy.pins, executor=executor).status,
                "verified",
            )
            normalize.assert_called_once_with({"version": "1"})
            plan_upgrade.assert_called_once_with(
                "bwg", "upgrade", "0", {"version": "1"}
            )
            builder.assert_called_once_with({"version": "1"})
            executor.assert_called_once_with("fixture command")

        with self.assertRaisesRegex(ValueError, "Unsupported maintenance pin"):
            _pins_table({"fixture": {"version": "1"}})
        with self.assertRaisesRegex(ValueError, "No remote adapter"):
            execute_action(action, pins=policy.pins, executor=executor)
        self.assertEqual(build_plan(policy, inventory).actions[0].status, "blocked")
        executor.assert_called_once()

    def test_windows_process_query_failure_preserves_live_lock(self) -> None:
        import ctypes

        kernel = mock.MagicMock()
        kernel.OpenProcess.return_value = 0
        for error, expected_alive in ((5, True), (87, False), (0, True)):
            with (
                self.subTest(error=error),
                mock.patch("sys.platform", "win32"),
                mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
                mock.patch.object(
                    ctypes, "get_last_error", return_value=error, create=True
                ),
            ):
                self.assertEqual(_pid_alive(12345), expected_alive)

    def _policy(self, root: Path) -> Path:
        policy_path = root / "maintenance.toml"
        policy_path.write_text(
            """
[settings]
strict_host_key_checking = true
command_timeout = 10
state_path = "state.db"
receipt_dir = "receipts"

[profiles.bwg]
enabled = true

[profiles.bwg.resources]
cpa = "deferred"
proxy_core = "managed"
docker = "present"
xray = "present"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return policy_path

    def _xray_upgrade_policy(self, root: Path) -> Path:
        policy_path = root / "maintenance-upgrade.toml"
        policy_path.write_text(
            f"""
[settings]
strict_host_key_checking = true
state_path = "state.db"
receipt_dir = "receipts"

[pins.xray]
version = "26.3.27"
sha256 = "{self.XRaySha256}"

[profiles.bwg]
enabled = true

[profiles.bwg.resources]
xray = "upgrade"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return policy_path

    def _unattended_xray_policy(self, root: Path) -> Path:
        policy_path = root / "maintenance-unattended.toml"
        policy_path.write_text(
            f"""
[settings]
strict_host_key_checking = true
state_path = "state.db"
receipt_dir = "receipts"

[automation]
mode = "unattended_apply"
acknowledge = "I_ACKNOWLEDGE_BWG_SINGLE_HOST_AUTOMATION"
profiles = ["bwg"]
resources = ["xray"]
window_start = "00:00"
window_end = "23:59"
max_attempts_per_pin = 1
cooldown_minutes = 1440
max_plan_age_minutes = 15

[pins.xray]
version = "26.3.27"
sha256 = "{self.XRaySha256}"

[profiles.bwg]
enabled = true

[profiles.bwg.resources]
xray = "upgrade"
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return policy_path

    def test_policy_schema_and_fingerprint_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._policy(root)
            first = load_policy(policy_path)
            second = load_policy(policy_path)
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertTrue(first.strict_host_key_checking)
            self.assertEqual(first.profiles["bwg"]["resources"]["cpa"], "deferred")

    def test_policy_rejects_non_strict_host_key_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._policy(root)
            policy_path.write_text(
                policy_path.read_text(encoding="utf-8").replace(
                    "strict_host_key_checking = true",
                    "strict_host_key_checking = false",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must remain true"):
                load_policy(policy_path)

    def test_policy_loads_normalized_xray_and_docker_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "pins.toml"
            path.write_text(
                f"""
[pins.xray]
version = "v26.3.27"
sha256 = "sha256:{self.XRaySha256}"

[pins.docker]
compose_file = "/srv/app/compose.yml"
compose_sha256 = "{self.XRaySha256}"
services = ["app"]

[pins.docker.digests]
app = "{self.DockerDigest}"

[profiles.bwg.resources]
xray = "present"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            policy = load_policy(path)
            self.assertEqual(policy.pins["xray"]["version"], "26.3.27")
            self.assertEqual(policy.pins["xray"]["sha256"], self.XRaySha256)
            self.assertEqual(policy.pins["docker"]["digests"]["app"], self.DockerDigest)
            self.assertEqual(policy.pins["docker"]["compose_sha256"], self.XRaySha256)

    def test_unattended_policy_requires_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._unattended_xray_policy(root)
            policy = load_policy(path)
            self.assertTrue(policy.automation.unattended_apply)
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "I_ACKNOWLEDGE_BWG_SINGLE_HOST_AUTOMATION",
                    "wrong",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "explicit acknowledgement"):
                load_policy(path)

    def test_policy_rejects_cpa_docker_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "pins.toml"
            path.write_text(
                f"""
[pins.docker]
compose_file = "/opt/cliproxyapi/compose.yml"
compose_sha256 = "{self.XRaySha256}"
services = ["app"]

[pins.docker.digests]
app = "{self.DockerDigest}"

[profiles.bwg.resources]
docker = "upgrade"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must not target CPA"):
                load_policy(path)

    def test_inventory_parser_is_allowlisted_and_redaction_friendly(self) -> None:
        record = parse_probe_output(
            "bwg",
            "hostname=fixture-host\n"
            "docker=absent\n"
            "xray=absent\n"
            "secret=DO_NOT_RETAIN\n"
            "garbage line\n",
        )
        self.assertTrue(record.reachable)
        self.assertEqual(record.facts["hostname"], "fixture-host")
        self.assertNotIn("secret", record.facts)

    def test_inventory_detects_service_managed_xray_when_binary_is_not_on_path(
        self,
    ) -> None:
        self.assertIn('systemctl is-active --quiet "$1"', INVENTORY_COMMAND)
        self.assertIn("unit_state xray", INVENTORY_COMMAND)
        self.assertIn("unit_state sing-box", INVENTORY_COMMAND)
        self.assertIn("vasma_sha256", INVENTORY_COMMAND)
        self.assertIn("xray_geodata_sha256", INVENTORY_COMMAND)
        self.assertIn("sing_box_source_config_sha256", INVENTORY_COMMAND)
        self.assertIn("service_backend", INVENTORY_COMMAND)
        self.assertIn('rc-service "$1" status', INVENTORY_COMMAND)

    def test_inventory_parser_retains_new_maintenance_identity_facts(self) -> None:
        record = parse_probe_output(
            "bwg",
            "service_backend=openrc\n"
            "vasma_path=/usr/sbin/vasma\n"
            "vasma_sha256=" + ("a" * 64) + "\n"
            "xray_geodata_sha256=" + ("b" * 64) + "\n"
            "sing_box_source_config_sha256=" + ("c" * 64) + "\n"
            "reboot_required=absent\n",
        )
        self.assertEqual(record.facts["service_backend"], "openrc")
        self.assertEqual(record.facts["vasma_path"], "/usr/sbin/vasma")
        self.assertEqual(record.facts["reboot_required"], "absent")

    def test_inventory_round_trip_checks_fingerprint(self) -> None:
        snapshot = InventorySnapshot(
            created_at="2026-09-22T00:00:00+00:00",
            records=(
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"docker": "absent", "xray": "absent"},
                ),
            ),
            fingerprint="",
        )
        snapshot = InventorySnapshot(
            created_at=snapshot.created_at,
            records=snapshot.records,
            fingerprint=inventory_fingerprint(snapshot.records),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            write_inventory(path, snapshot)
            loaded = load_inventory(path)
            self.assertEqual(loaded.fingerprint, snapshot.fingerprint)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["records"][0]["facts"]["docker"] = "present"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                load_inventory(path)

    def test_inventory_rejects_duplicate_or_empty_profiles(self) -> None:
        for profiles in (("bwg", "bwg"), ("", "bwg")):
            with self.subTest(profiles=profiles):
                records: list[dict[str, Any]] = [
                    {
                        "profile": profile,
                        "reachable": True,
                        "facts": {},
                        "error_class": None,
                    }
                    for profile in profiles
                ]
                payload = {
                    "created_at": "2026-09-22T00:00:00+00:00",
                    "records": records,
                    "fingerprint": "sha256:" + ("0" * 64),
                }
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "inventory.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        ValueError, "(invalid fields|duplicate)"
                    ):
                        load_inventory(path)

    def test_planner_rejects_duplicate_profiles_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = load_policy(self._policy(Path(directory)))
        records = (
            InventoryRecord(profile="bwg", reachable=True, facts={}),
            InventoryRecord(profile="bwg", reachable=False, facts={}),
        )
        inventory = InventorySnapshot(
            created_at="now",
            records=records,
            fingerprint="sha256:" + ("0" * 64),
        )
        with self.assertRaisesRegex(ValueError, "duplicate profile"):
            build_plan(policy, inventory)

    def test_inventory_fingerprint_ignores_volatile_telemetry(self) -> None:
        stable = {"hostname": "host-1", "xray_sha256": "a" * 64, "docker": "present"}
        before = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={
                    **stable,
                    "root_disk_used_percent": "41",
                    "memory_mb": "938",
                    "listeners": "22,8443",
                },
            ),
        )
        after = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={
                    **stable,
                    "root_disk_used_percent": "43",
                    "memory_mb": "1024",
                    "listeners": "22,8443",
                },
            ),
        )
        self.assertEqual(
            inventory_fingerprint(before),
            inventory_fingerprint(after),
        )

    def test_inventory_fingerprint_changes_with_identity_facts(self) -> None:
        base = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={"xray_sha256": "a" * 64, "cpa_image_digest": "sha256:b"},
            ),
        )
        rotated = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={"xray_sha256": "c" * 64, "cpa_image_digest": "sha256:b"},
            ),
        )
        self.assertNotEqual(
            inventory_fingerprint(base),
            inventory_fingerprint(rotated),
        )
        listener_rotated = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={
                    "xray_sha256": "a" * 64,
                    "cpa_image_digest": "sha256:b",
                    "listeners": "22,9443",
                },
            ),
        )
        self.assertNotEqual(
            inventory_fingerprint(base),
            inventory_fingerprint(listener_rotated),
        )

    def test_plan_marks_cpa_deferred_proxy_blocked_and_absent_optional_resources_noop(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy = load_policy(self._policy(Path(directory)))
        records = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={
                    "cpa": "active",
                    "docker": "absent",
                    "xray": "absent",
                },
            ),
        )
        inventory = InventorySnapshot(
            created_at="now",
            records=records,
            fingerprint=inventory_fingerprint(records),
        )
        plan = build_plan(policy, inventory)
        statuses = {action.resource: action.status for action in plan.actions}
        self.assertEqual(statuses["cpa"], "deferred")
        self.assertEqual(statuses["proxy_core"], "blocked")
        self.assertEqual(statuses["docker"], "noop")
        self.assertEqual(statuses["xray"], "noop")
        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.plan_id, build_plan(policy, inventory).plan_id)

    def test_state_rejects_plan_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._policy(root))
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"cpa": "active", "docker": "absent", "xray": "absent"},
                ),
            )
            plan = build_plan(
                policy,
                InventorySnapshot(
                    created_at="now",
                    records=records,
                    fingerprint=inventory_fingerprint(records),
                ),
            )
            state_path = root / "state.db"
            save_plan(state_path, plan)
            with sqlite3.connect(state_path) as connection:
                payload = json.loads(
                    connection.execute(
                        "SELECT plan_json FROM plans WHERE plan_id = ?",
                        (plan.plan_id,),
                    ).fetchone()[0]
                )
                payload["plan_id"] = "../../outside"
                connection.execute(
                    "UPDATE plans SET plan_json = ? WHERE plan_id = ?",
                    (json.dumps(payload), plan.plan_id),
                )
                connection.commit()
            with self.assertRaisesRegex(ValueError, "invalid format"):
                load_plan(state_path, plan.plan_id)

            with sqlite3.connect(state_path) as connection:
                payload["plan_id"] = "plan-0000000000000000"
                connection.execute(
                    "UPDATE plans SET plan_json = ? WHERE plan_id = ?",
                    (json.dumps(payload), plan.plan_id),
                )
                connection.commit()
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_plan(state_path, plan.plan_id)

    def test_replanning_identical_snapshot_preserves_remote_execution_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            original = build_plan(policy, inventory)
            state_path = root / "state.db"
            created = save_plan_if_absent(state_path, original)
            self.assertEqual(load_plan(state_path, original.plan_id), created)
            conflicting = replace(
                original,
                actions=(replace(original.actions[0], desired="26.3.27"),),
            )
            with self.assertRaisesRegex(ValueError, "different maintenance intent"):
                save_plan_if_absent(state_path, conflicting)

            for status in ("applied", "unverified", "rolled_back", "verified"):
                with self.subTest(status=status):
                    action = replace(
                        original.actions[0],
                        status=status,
                        reason="Remote outcome read back.",
                    )
                    save_plan(
                        state_path,
                        replace(original, actions=(action,), status=status),
                    )
                    saved = save_plan_if_absent(
                        state_path, build_plan(policy, inventory)
                    )
                    self.assertEqual(saved.actions[0].status, status)
                    self.assertEqual(saved.status, status)
                    self.assertEqual(saved.created_at, original.created_at)
                    self.assertEqual(saved.actions[0].reason, action.reason)

    def test_replanning_unexecuted_plan_renews_its_review_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._xray_upgrade_policy(root))
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            original = replace(
                build_plan(policy, inventory), created_at="2026-10-01T00:00:00+00:00"
            )
            state_path = root / "state.db"
            save_plan(state_path, original)
            fresh = replace(original, created_at="2026-10-07T00:00:00+00:00")
            renewed = save_plan_if_absent(state_path, fresh)
            self.assertEqual(renewed.created_at, fresh.created_at)
            self.assertEqual(load_plan(state_path), renewed)

    def test_xray_upgrade_plans_only_when_pinned_version_differs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._xray_upgrade_policy(root))
            old_records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            old_inventory = InventorySnapshot(
                created_at="now",
                records=old_records,
                fingerprint=inventory_fingerprint(old_records),
            )
            old_plan = build_plan(policy, old_inventory)
            self.assertEqual(old_plan.actions[0].status, "planned")
            self.assertEqual(old_plan.actions[0].target, "26.3.27")

            same_records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.27"},
                ),
            )
            same_inventory = InventorySnapshot(
                created_at="now",
                records=same_records,
                fingerprint=inventory_fingerprint(same_records),
            )
            self.assertEqual(
                build_plan(policy, same_inventory).actions[0].status, "noop"
            )

    def test_upgrade_is_blocked_when_host_reboot_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._xray_upgrade_policy(root))
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={
                        "xray": "present",
                        "xray_version": "26.3.26",
                        "reboot_required": "present",
                    },
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            action = plan.actions[0]
            self.assertEqual(action.status, "blocked")
            self.assertEqual(action.resource, "xray")
            self.assertIn("reboot-required", action.reason)
            self.assertEqual(plan.status, "blocked")

    def test_unattended_authorization_is_one_new_pin_inside_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._unattended_xray_policy(root))
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            created = datetime(2026, 9, 22, 20, 5, tzinfo=timezone.utc)
            inventory = InventorySnapshot(
                created_at=created.isoformat(),
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = replace(
                build_plan(policy, inventory), created_at=created.isoformat()
            )
            state_path = root / "state.db"
            authorization = authorize_unattended_apply(
                policy,
                plan,
                state_path,
                now=created,
            )
            self.assertEqual(authorization.profile, "bwg")
            self.assertEqual(authorization.resource, "xray")
            self.assertEqual(
                authorization.pin_fingerprint,
                pin_fingerprint(policy, plan.actions[0]),
            )
            record_automation_attempt(
                state_path,
                profile=authorization.profile,
                resource=authorization.resource,
                pin_fingerprint=authorization.pin_fingerprint,
                plan_id=plan.plan_id,
                attempted_at=created.isoformat(),
            )
            record_automation_outcome(
                state_path,
                profile=authorization.profile,
                resource=authorization.resource,
                pin_fingerprint=authorization.pin_fingerprint,
                outcome="verified",
                plan_id=plan.plan_id,
                attempted_at=created.isoformat(),
            )
            with self.assertRaisesRegex(ValueError, "already verified"):
                authorize_unattended_apply(
                    policy,
                    plan,
                    state_path,
                    now=created,
                )

    def test_unattended_apply_rejects_observe_policy_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="2026-09-22T20:05:00+00:00",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            save_plan(root / "state.db", build_plan(policy, inventory))
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                            "--unattended",
                        ]
                    ),
                    2,
                )
                connect.assert_not_called()

    def _unattended_plan(
        self,
        policy_path: Path,
        state_path: Path,
    ) -> tuple[MaintenancePlan, datetime, InventorySnapshot]:
        policy = load_policy(policy_path)
        records = (
            InventoryRecord(
                profile="bwg",
                reachable=True,
                facts={"xray": "present", "xray_version": "26.3.26"},
            ),
        )
        inventory = InventorySnapshot(
            created_at="2026-09-22T20:05:00+00:00",
            records=records,
            fingerprint=inventory_fingerprint(records),
        )
        # Pin the authorization clock so the all-day window and the 15-minute
        # plan-age guard hold regardless of the host's wall-clock time.
        fixed_now = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)
        plan = replace(build_plan(policy, inventory), created_at=fixed_now.isoformat())
        save_plan(state_path, plan)
        return plan, fixed_now, inventory

    def test_unattended_local_refusal_does_not_burn_pin_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, fixed_now, _ = self._unattended_plan(policy_path, state_path)

            class _FixedClock:
                # automation.py only needs now() and fromisoformat(); a stub
                # avoids fighting mypy over datetime subclass overrides.
                @staticmethod
                def now(tz: Any = None) -> datetime:
                    return fixed_now

                @staticmethod
                def fromisoformat(value: str) -> datetime:
                    return datetime.fromisoformat(value)

            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.automation.datetime", _FixedClock
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
                ) as connect,
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                            "--unattended",
                        ]
                    ),
                    2,
                )
                connect.assert_not_called()

            # The missing integration opt-in is a local guard refusal: the
            # pin attempt quota must be untouched and the next (correct)
            # invocation must still be authorizable.
            policy = load_policy(policy_path)
            authorization = authorize_unattended_apply(
                policy, plan, state_path, now=fixed_now
            )
            self.assertIsNone(
                load_automation_target(
                    state_path,
                    profile=authorization.profile,
                    resource=authorization.resource,
                    pin_fingerprint=authorization.pin_fingerprint,
                )
            )

    def test_unattended_apply_records_attempt_for_real_remote_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, fixed_now, inventory = self._unattended_plan(policy_path, state_path)

            target_config = root / "target.json"
            target_config.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "bwg": {
                                "host": "203.0.113.10",
                                "user": "root",
                                "password_env": "VPS_MAINT_TEST_PASSWORD",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            class _FixedClock:
                # automation.py only needs now() and fromisoformat(); a stub
                # avoids fighting mypy over datetime subclass overrides.
                @staticmethod
                def now(tz: Any = None) -> datetime:
                    return fixed_now

                @staticmethod
                def fromisoformat(value: str) -> datetime:
                    return datetime.fromisoformat(value)

            client = mock.MagicMock()
            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.automation.datetime", _FixedClock
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "VPS_MAINT_TEST_PASSWORD": "unused-in-tests",
                        "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                    },
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry",
                    return_value=client,
                ) as connect,
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.execution.exec_remote",
                    return_value=(0, "APPLY_VERIFIED\n", ""),
                ) as exec_remote,
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                            "--run-integration",
                            "--unattended",
                            "--target-config",
                            str(target_config),
                        ]
                    ),
                    0,
                )
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(exec_remote.call_count, 1)
            policy = load_policy(policy_path)
            target = load_automation_target(
                state_path,
                profile="bwg",
                resource="xray",
                pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
            )
            self.assertIsNotNone(target)
            assert target is not None  # unittest asserts do not narrow for mypy
            self.assertEqual(target["attempt_count"], 1)
            self.assertEqual(target["last_outcome"], "verified")

    def test_unattended_connection_failure_does_not_burn_pin_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, fixed_now, inventory = self._unattended_plan(policy_path, state_path)
            target_config = root / "target.json"
            target_config.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "bwg": {
                                "host": "203.0.113.10",
                                "user": "root",
                                "password_env": "VPS_MAINT_TEST_PASSWORD",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            class _FixedClock:
                @staticmethod
                def now(tz: Any = None) -> datetime:
                    return fixed_now

                @staticmethod
                def fromisoformat(value: str) -> datetime:
                    return datetime.fromisoformat(value)

            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.automation.datetime", _FixedClock
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "VPS_MAINT_TEST_PASSWORD": "unused-in-tests",
                        "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                    },
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry",
                    side_effect=OSError("simulated connection refusal"),
                ),
            ):

                def _record_start() -> None:
                    record_automation_attempt(
                        state_path,
                        profile="bwg",
                        resource="xray",
                        pin_fingerprint=pin_fingerprint(
                            load_policy(policy_path), plan.actions[0]
                        ),
                        plan_id=plan.plan_id,
                    )

                with self.assertRaisesRegex(OSError, "connection refusal"):
                    _execute_remote_plan(
                        Namespace(
                            run_integration=True,
                            target_config=str(target_config),
                            profile=None,
                        ),
                        load_policy(policy_path),
                        plan,
                        state_path=state_path,
                        on_remote_start=_record_start,
                    )

            policy = load_policy(policy_path)
            self.assertIsNone(
                load_automation_target(
                    state_path,
                    profile="bwg",
                    resource="xray",
                    pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
                )
            )

    def test_remote_adapter_failure_leaves_durable_applied_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            state_path = root / "state.db"
            save_plan(state_path, plan)
            target_config = root / "target.json"
            target_config.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "bwg": {
                                "host": "203.0.113.10",
                                "user": "root",
                                "password_env": "VPS_MAINT_TEST_PASSWORD",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                run_integration=True,
                target_config=str(target_config),
                profile=None,
            )
            client = mock.MagicMock()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "VPS_MAINT_TEST_PASSWORD": "unused-in-tests",
                        "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                    },
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry",
                    return_value=client,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.execute_action",
                    side_effect=RuntimeError("simulated adapter crash"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated adapter crash"):
                    _execute_remote_plan(
                        args,
                        policy,
                        plan,
                        state_path=state_path,
                    )
            stored = load_plan(state_path)
            self.assertEqual(stored.actions[0].status, "applied")
            self.assertEqual(stored.status, "applied")
            client.close.assert_called_once_with()

    def test_remote_adapter_commands_are_pinned_and_cpa_scoped(self) -> None:
        xray_command = build_xray_upgrade_command(
            version="26.3.27",
            sha256=self.XRaySha256,
        )
        self.assertIn("sha256sum --check", xray_command)
        self.assertIn("expected_artifact_sha256", xray_command)
        self.assertIn("awk '/^Xray / {print $2; exit}'", xray_command)
        self.assertIn('grep -Fx "$version"', xray_command)
        self.assertIn("UNSUPPORTED_XRAY_ARCH", xray_command)
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", xray_command)
        self.assertIn("ROLLBACK_VERIFIED", xray_command)
        with self.assertRaises(ValueError):
            build_xray_upgrade_command(
                version="26.3.27; touch /tmp/pwned",
                sha256=self.XRaySha256,
            )

        docker_command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        self.assertIn("docker compose", docker_command)
        self.assertIn("DIGEST_READBACK_MISMATCH", docker_command)
        self.assertIn("CPA_IMAGE_REFUSED", docker_command)
        self.assertIn(
            'docker compose --project-directory "$compose_project_dir"', docker_command
        )
        self.assertIn("APPLY_REFUSED_BEFORE_MUTATION", docker_command)
        self.assertIn("OLD_IMAGE_READBACK_FAILED", docker_command)
        self.assertIn("old_image_pairs=", docker_command)
        self.assertIn("rollback-compose.yml", docker_command)
        self.assertIn("ROLLBACK_OLD_IMAGE_DIGEST_UNAVAILABLE", docker_command)
        self.assertIn(
            "docker inspect --format '{{.Image}}' \"$container_id\"", docker_command
        )
        self.assertIn(
            '"{{range .RepoDigests}}{{println .}}{{end}}"',
            docker_command,
        )
        self.assertIn('!= "$old_image_id"', docker_command)
        with self.assertRaisesRegex(ValueError, "must not target CPA"):
            build_docker_upgrade_command(
                compose_file="/opt/cliproxyapi/compose.yml",
                compose_sha256=self.XRaySha256,
                services=("app",),
                digests={"app": self.DockerDigest},
            )

    def test_xray_rollback_restarts_only_after_restoring_a_changed_binary(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        command = build_xray_upgrade_command(
            version="26.3.27",
            sha256=self.XRaySha256,
        )
        rollback_body = command.split("rollback() {\n", 1)[1].split(
            "\n}\ntrap rollback ERR EXIT", 1
        )[0]
        rollback_function = "rollback() {\n" + rollback_body + "\n}"

        for scenario in ("unchanged", "changed", "unchanged_after_restart"):
            with self.subTest(scenario=scenario):
                changed_binary = (
                    "printf 'replacement\\n' > \"$binary\""
                    if scenario == "changed"
                    else ":"
                )
                payload = f"""set -Eeuo pipefail
scenario='{scenario}'
fixture_root="$(mktemp -d)"
backup_dir="$fixture_root/backup"
tmp_dir="$fixture_root/tmp"
binary="$fixture_root/xray"
confdir="$fixture_root/conf"
restart_count=0
service_restart_attempted=0
mkdir -p "$backup_dir" "$tmp_dir" "$confdir"
printf '#!/bin/sh\\nexit 0\\n' > "$binary"
chmod 0755 "$binary"
cp -a "$binary" "$backup_dir/xray"
backup_ready=1
if [ "$scenario" = unchanged_after_restart ]; then service_restart_attempted=1; fi
{changed_binary}
systemctl() {{
  if [ "$1" = restart ]; then
    restart_count=$((restart_count + 1))
    if [ "$scenario" = unchanged ] || [ "$restart_count" -gt 1 ]; then
      return 1
    fi
    return 0
  fi
  if [ "$1" = is-active ]; then
    if [ "$scenario" = changed ] && [ "$restart_count" -ne 1 ]; then
      return 1
    fi
    if [ "$scenario" = unchanged_after_restart ] && [ "$restart_count" -ne 1 ]; then
      return 1
    fi
    if [ "$scenario" = unchanged ] && [ "$restart_count" -ne 0 ]; then
      return 1
    fi
    return 0
  fi
  return 1
}}
{rollback_function}
set +e
false
rollback
"""
                completed = subprocess.run(
                    [bash, "-c", payload],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn("ROLLBACK_VERIFIED", completed.stdout)

    def test_docker_rollback_snapshot_pins_preexisting_image_digest(self) -> None:
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        marker = (
            'python3 - "$backup_dir/rollback-compose.json" '
            '"$old_image_pairs" "$backup_dir/rollback-compose.yml" <<\'PY\'\n'
        )
        program = command.split(marker, 1)[1].split("\nPY\n", 1)[0]
        image_template = "app/repo:v2@sha256:" + "b" * 64
        old_digest = "app/repo@sha256:" + "c" * 64
        for available_refs, expected_success in (
            (old_digest, True),
            ("unrelated/repo@sha256:" + "d" * 64, False),
        ):
            with (
                self.subTest(expected_success=expected_success),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = root / "compose.json"
                destination = root / "rollback-compose.yml"
                source.write_text(
                    json.dumps({"services": {"app": {"image": image_template}}}),
                    encoding="utf-8",
                )
                old_argv = sys.argv
                sys.argv = [
                    "rollback-snapshot",
                    str(source),
                    "app|sha256:" + "a" * 64,
                    str(destination),
                ]
                try:
                    with mock.patch(
                        "subprocess.check_output", return_value=available_refs
                    ):
                        if expected_success:
                            exec(compile(program, "<rollback-snapshot>", "exec"), {})
                        else:
                            with self.assertRaisesRegex(
                                SystemExit, "ROLLBACK_OLD_IMAGE_DIGEST_UNAVAILABLE"
                            ):
                                exec(
                                    compile(program, "<rollback-snapshot>", "exec"), {}
                                )
                finally:
                    sys.argv = old_argv
                if expected_success:
                    snapshot = json.loads(destination.read_text(encoding="utf-8"))
                    self.assertEqual(snapshot["services"]["app"]["image"], old_digest)
                    self.assertIn("destination.chmod(0o600)", program)
                    if os.name != "nt":
                        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                else:
                    self.assertFalse(destination.exists())

    @staticmethod
    def _resolve_bash() -> str | None:
        """Prefer Git Bash on Windows before retaining the PATH fallback."""
        if os.name == "nt":
            for root in filter(
                None,
                (
                    os.environ.get("ProgramW6432"),
                    os.environ.get("ProgramFiles"),
                    os.environ.get("ProgramFiles(x86)"),
                ),
            ):
                for relative_path in (
                    Path("Git") / "bin" / "bash.exe",
                    Path("Git") / "usr" / "bin" / "bash.exe",
                ):
                    candidate = Path(root) / relative_path
                    if candidate.is_file():
                        return str(candidate)
        return shutil.which("bash")

    def test_adapter_payloads_are_valid_bash(self) -> None:
        # The adapters synthesize high-risk remote payloads with Python
        # f-strings; string assertions alone cannot catch a broken escape, and
        # the failure would otherwise first surface as a remote "unverified".
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        login = os.name == "nt" and "git" in {
            part.lower() for part in Path(bash).resolve().parts
        }
        payloads = {
            "xray": build_xray_upgrade_command(
                version="26.3.27",
                sha256=self.XRaySha256,
            ),
            "docker": build_docker_upgrade_command(
                compose_file="/srv/app/compose.yml",
                compose_sha256=self.XRaySha256,
                services=("app",),
                digests={"app": self.DockerDigest},
            ),
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                command = [bash, "-l", "-n"] if login else [bash, "-n"]
                completed = subprocess.run(
                    command,
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                )
                self.assertEqual(completed.returncode, 0, output)

    def test_docker_multiline_service_list_is_accepted(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app", "db"),
            digests={"app": self.DockerDigest, "db": self.DockerDigest},
        )
        checks = (
            "services_output="
            + command.split("services_output=", 1)[1].split(
                "for pair in $expected_pairs; do", 1
            )[0]
        )
        payload = (
            """set -Eeuo pipefail
compose_file=unused
expected_services='app db'
docker() {
  if [ "$5" = '--services' ]; then printf 'app\\ndb\\n';
  else printf 'example/repo@sha256:aaaa\\n'; fi
}
"""
            + checks
            + "\nprintf 'SERVICES_VALIDATED\\n'\n"
        )
        result = subprocess.run(
            ScriptValidationMixin._bash_command(bash, "-c", payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SERVICES_VALIDATED", result.stdout)

    def test_docker_preflight_preserves_stopped_and_scaled_services(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        preflight = (
            "for service in $expected_services; do\n  # Stopped"
            + command.split("for service in $expected_services; do\n  # Stopped", 1)[
                1
            ].split("command -v python3", 1)[0]
        )
        for state, ids, expected_code in (
            ("running", "container", 0),
            ("exited", "container", 52),
            ("running", "one\\ntwo", 52),
            ("absent", "", 0),
        ):
            with self.subTest(state=state, ids=ids):
                payload = f"""set -Eeuo pipefail
compose_file=unused
expected_services=app
old_image_pairs=''
old_existing_services=''
old_absent_services=''
docker() {{
  case " $* " in
    *' ps -aq '*) printf '{ids}' ;;
    *'.State.Status'*) echo {state} ;;
    *'.Image'*) echo old-image ;;
    *) return 99 ;;
  esac
}}
{preflight}
echo PREFLIGHT_PASSED
"""
                result = subprocess.run(
                    ScriptValidationMixin._bash_command(bash, "-c", payload),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    result.returncode, expected_code, result.stdout + result.stderr
                )
                self.assertEqual(
                    "PREFLIGHT_PASSED" in result.stdout, expected_code == 0
                )

    def test_docker_failure_traps_cover_explicit_exit_signals_and_preflight(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        traps = (
            "rollback() {"
            + command.split("rollback() {", 1)[1].split('\ntest -f "$compose_file"', 1)[
                0
            ]
        )
        for mutation, failure, expected_code in (
            (1, "exit 45", 45),
            (1, "exit 46", 46),
            (1, "false", 1),
            (1, "kill -TERM $$", 143),
            (1, "kill -INT $$", 130),
            (0, "false", 1),
        ):
            with self.subTest(mutation=mutation, failure=failure):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "rollback-compose.yml").write_text("{}", encoding="utf-8")
                    payload = f"""set -Eeuo pipefail
backup_dir=.
backup_ready=1
mutation_started={mutation}
compose_file=unused
compose_project_dir=.
old_existing_services=app
old_absent_services=''
old_image_pairs='app|old-image'
docker() {{
  printf '%s\\n' "$*" >> calls
  case " $* " in
    *' ps '*) echo container ;;
    *'.State.Status'*) echo running ;;
    *'.Image'*) echo old-image ;;
  esac
  return 0
}}
{traps}
{failure}
"""
                    result = subprocess.run(
                        ScriptValidationMixin._bash_command(bash, "-c", payload),
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode, expected_code, result.stdout + result.stderr
                    )
                    if mutation:
                        self.assertIn("ROLLBACK_VERIFIED", result.stdout)
                        self.assertEqual((root / "calls").read_text().count(" up "), 1)
                    else:
                        self.assertIn("APPLY_REFUSED_BEFORE_MUTATION", result.stderr)
                        self.assertFalse((root / "calls").exists())

    def test_docker_rollback_verifies_previously_absent_service_is_removed(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        rollback = command.split("rollback() {", 1)[1].split(
            "\n}\ntrap rollback ERR EXIT", 1
        )[0]
        for removed, expected_marker in (
            (True, "ROLLBACK_VERIFIED"),
            (False, "ROLLBACK_FAILED"),
        ):
            with (
                self.subTest(removed=removed),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "rollback-compose.yml").write_text("{}", encoding="utf-8")
                payload = (
                    """set -Eeuo pipefail
backup_dir=.
backup_ready=1
mutation_started=1
compose_file=unused
compose_project_dir=.
expected_services=app
old_existing_services=''
old_absent_services=app
old_image_pairs=''
removed=0
docker() {
  case " $* " in
    *' rm '*) removed=__REMOVED__ ;;
    *' ps '*) if [ "$removed" = 0 ]; then printf 'remaining-container\\n'; fi ;;
    *' inspect '*) printf 'running\\n' ;;
  esac
  return 0
}
rollback() {""".replace("__REMOVED__", "1" if removed else "0")
                    + rollback
                    + "\n}\ntrap rollback ERR\nfalse\n"
                )
                result = subprocess.run(
                    ScriptValidationMixin._bash_command(bash, "-c", payload),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected_marker, result.stdout + result.stderr)

    def test_inventory_probe_is_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        login = os.name == "nt" and "git" in {
            part.lower() for part in Path(bash).resolve().parts
        }
        command = [bash, "-l", "-n"] if login else [bash, "-n"]
        completed = subprocess.run(
            command,
            input=INVENTORY_COMMAND.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
        output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        self.assertEqual(completed.returncode, 0, output)

    def test_adapter_result_uses_success_and_rollback_markers(self) -> None:
        action = mock.Mock(resource="xray")
        action.resource = "xray"
        action.status = "planned"
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (0, "APPLY_VERIFIED\n", ""),
        )
        self.assertEqual(result.status, "verified")
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (1, "ROLLBACK_VERIFIED\n", ""),
        )
        self.assertEqual(result.status, "rolled_back")
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (0, "echo APPLY_VERIFIED\n", ""),
        )
        self.assertEqual(result.status, "unverified")
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (0, "  APPLY_VERIFIED  \n", ""),
        )
        self.assertEqual(result.status, "verified")

    def test_state_and_receipt_store_no_sensitive_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._policy(root))
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"hostname": "secret-host", "docker": "absent"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            state_path = root / "state.db"
            save_plan(state_path, plan)
            self.assertEqual(load_plan(state_path).plan_id, plan.plan_id)
            self.assertEqual(list_plans(state_path)[0]["plan_id"], plan.plan_id)
            receipt_path = write_receipt(
                root / "receipts",
                plan,
                outcome="refused",
                reason="No adapter",
            )
            receipt_text = receipt_path.read_text(encoding="utf-8")
            self.assertNotIn("secret-host", receipt_text)
            self.assertNotIn("password", receipt_text.lower())
            self.assertIn("sensitive_values_omitted", receipt_text)

    def test_apply_requires_yes_and_refuses_without_remote_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"docker": "present", "xray": "present"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            save_plan(root / "state.db", plan)
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(["--config", str(policy_path), "apply"]),
                    2,
                )
                self.assertEqual(
                    main(["--config", str(policy_path), "apply", "--yes"]),
                    1,
                )
                connect.assert_not_called()

    def test_apply_planned_is_dry_run_without_remote_write_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            save_plan(root / "state.db", plan)
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                        ]
                    ),
                    0,
                )
                connect.assert_not_called()

    def test_apply_refuses_unresolved_applied_action_without_retrying_remote(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            plan = build_plan(policy, inventory)
            action = replace(
                plan.actions[0],
                status="applied",
                reason="Remote adapter started; outcome is unresolved.",
            )
            save_plan(
                root / "state.db",
                replace(plan, actions=(action,), status="applied"),
            )
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                            "--run-integration",
                        ]
                    ),
                    1,
                )
                connect.assert_not_called()
            stored = load_plan(root / "state.db")
            self.assertEqual(stored.actions[0].status, "applied")
            self.assertEqual(stored.status, "applied")

    def test_apply_refuses_unverified_or_rolled_back_action_without_retrying_remote(
        self,
    ) -> None:
        for status in ("unverified", "rolled_back"):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                policy_path = self._xray_upgrade_policy(root)
                policy = load_policy(policy_path)
                records = (
                    InventoryRecord(
                        profile="bwg",
                        reachable=True,
                        facts={"xray": "present", "xray_version": "26.3.26"},
                    ),
                )
                inventory = InventorySnapshot(
                    created_at="now",
                    records=records,
                    fingerprint=inventory_fingerprint(records),
                )
                plan = build_plan(policy, inventory)
                action = replace(
                    plan.actions[0],
                    status=status,
                    reason="Remote adapter did not leave a retryable plan.",
                )
                save_plan(
                    root / "state.db",
                    replace(plan, actions=(action,), status=status),
                )
                with mock.patch(
                    "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
                ) as connect:
                    self.assertEqual(
                        main(
                            [
                                "--config",
                                str(policy_path),
                                "apply",
                                "--yes",
                                "--remote-write",
                                "--run-integration",
                            ]
                        ),
                        1,
                    )
                    connect.assert_not_called()

    def test_remote_apply_requires_integration_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            save_plan(root / "state.db", build_plan(policy, inventory))
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                        ]
                    ),
                    2,
                )
                connect.assert_not_called()

    def test_manual_remote_apply_acquires_the_local_maintenance_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            save_plan(root / "state.db", build_plan(policy, inventory))
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.unattended_lock",
                side_effect=ValueError("local maintenance lock observed"),
            ) as lock:
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--remote-write",
                        ]
                    ),
                    2,
                )
                lock.assert_called_once_with(policy_lock_path(policy))

    def test_apply_rejects_policy_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.26"},
                ),
            )
            inventory = InventorySnapshot(
                created_at="now",
                records=records,
                fingerprint=inventory_fingerprint(records),
            )
            save_plan(root / "state.db", build_plan(policy, inventory))
            policy_path.write_text(
                policy_path.read_text(encoding="utf-8").replace(
                    self.XRaySha256,
                    "c" * 64,
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "vps_ssh_launcher.maintenance_cli.connection.connect_with_retry"
            ) as connect:
                self.assertEqual(
                    main(["--config", str(policy_path), "apply", "--yes"]),
                    2,
                )
                connect.assert_not_called()


class UnattendedLockTest(unittest.TestCase):
    """unattended_lock is the last fail-closed gate before remote writes; the
    stale-recovery path in particular silently depended on os.kill(pid, 0)
    semantics that do not hold on Windows, so every branch gets a test."""

    @staticmethod
    def _confirmed_dead_pid() -> int | None:
        for _ in range(5):
            proc = subprocess.Popen([sys.executable, "-c", "pass"])
            proc.wait()
            if not _pid_alive(proc.pid):
                return proc.pid
        return None

    @staticmethod
    def _write_lock(path: Path, pid: int, *, minutes_ago: float) -> None:
        created = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
        path.write_text(
            json.dumps({"pid": pid, "created_at": created.isoformat()}),
            encoding="utf-8",
        )

    def test_acquires_and_releases_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            with unattended_lock(lock):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_refuses_second_holder_while_owner_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            with unattended_lock(lock):
                with self.assertRaises(ValueError):
                    with unattended_lock(lock):
                        pass

    def test_native_guard_blocks_a_process_after_owner_file_disappears(self) -> None:
        child = (
            "from pathlib import Path; import sys; "
            "from vps_ssh_launcher.maintenance.automation import unattended_lock; "
            "\nwith unattended_lock(Path(sys.argv[1])): print('WRITE_ADMITTED')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            with unattended_lock(lock):
                lock.unlink()
                result = subprocess.run(
                    [sys.executable, "-c", child, str(lock)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("WRITE_ADMITTED", result.stdout)
                self.assertIn("owns the lock", result.stderr)
                self.assertFalse(lock.exists())
            # An unlocked persistent guard is reusable, rather than a stale
            # second owner file needing manual recovery.
            with unattended_lock(lock):
                self.assertTrue(lock.exists())

    def test_native_guard_is_released_on_abrupt_process_exit(self) -> None:
        child = (
            "from pathlib import Path; import os, sys; "
            "from vps_ssh_launcher.maintenance.automation import unattended_lock; "
            "\nwith unattended_lock(Path(sys.argv[1])): os._exit(0)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            result = subprocess.run(
                [sys.executable, "-c", child, str(lock)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(lock.exists())
            owner = json.loads(lock.read_text(encoding="utf-8"))
            if _pid_alive(owner["pid"]):
                self.skipTest("exited child's process id was reused")
            self._write_lock(lock, owner["pid"], minutes_ago=181)
            with unattended_lock(lock):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_recovers_stale_lock_from_dead_owner(self) -> None:
        dead = self._confirmed_dead_pid()
        if dead is None:
            self.skipTest("could not observe a dead pid")
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            self._write_lock(lock, dead, minutes_ago=181)
            with unattended_lock(lock):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_refuses_stale_lock_with_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            self._write_lock(lock, os.getpid(), minutes_ago=181)
            with self.assertRaises(ValueError):
                with unattended_lock(lock):
                    pass

    def test_refuses_fresh_lock_from_dead_owner(self) -> None:
        dead = self._confirmed_dead_pid()
        if dead is None:
            self.skipTest("could not observe a dead pid")
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            self._write_lock(lock, dead, minutes_ago=0)
            with self.assertRaises(ValueError):
                with unattended_lock(lock):
                    pass

    def test_unreadable_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            lock.write_bytes(b"\x00\x01not-json")
            with self.assertRaises(ValueError):
                with unattended_lock(lock):
                    pass

    def test_invalid_lock_owner_fails_closed_without_deleting_lock(self) -> None:
        owners: tuple[Any, ...] = ([], {}, {"pid": 0}, {"pid": True}, {"pid": 123})
        for owner in owners:
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as tmp:
                lock = Path(tmp) / "unattended.lock"
                lock.write_text(json.dumps(owner), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unreadable"):
                    with unattended_lock(lock):
                        self.fail("Invalid lock owner must prevent remote writes.")
                self.assertTrue(lock.exists())

    def test_pid_alive_liveness_semantics(self) -> None:
        self.assertTrue(_pid_alive(os.getpid()))
        dead = self._confirmed_dead_pid()
        if dead is None:
            self.skipTest("could not observe a dead pid")
        self.assertFalse(_pid_alive(dead))


if __name__ == "__main__":
    unittest.main()
