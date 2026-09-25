import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from vps_ssh_launcher.maintenance.automation import (
    _pid_alive,
    authorize_unattended_apply,
    pin_fingerprint,
    unattended_lock,
)
from vps_ssh_launcher.maintenance.config import load_policy
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
)
from vps_ssh_launcher.maintenance.planner import build_plan
from vps_ssh_launcher.maintenance.receipt import write_receipt
from vps_ssh_launcher.maintenance.state import (
    list_plans,
    load_automation_target,
    load_plan,
    record_automation_attempt,
    record_automation_outcome,
    save_plan,
)
from vps_ssh_launcher.maintenance_cli import main


class MaintenanceControlPlaneTests(unittest.TestCase):
    XRaySha256 = "a" * 64
    DockerDigest = "sha256:" + "b" * 64

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
        self.assertIn("systemctl is-active --quiet xray", INVENTORY_COMMAND)
        self.assertIn("systemctl is-active --quiet sing-box", INVENTORY_COMMAND)

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
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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
    ) -> tuple[Any, Any, InventorySnapshot]:
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
        plan = replace(
            build_plan(policy, inventory), created_at=fixed_now.isoformat()
        )
        save_plan(state_path, plan)
        return plan, fixed_now, inventory

    def test_unattended_local_refusal_does_not_burn_pin_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, fixed_now, _ = self._unattended_plan(policy_path, state_path)

            class _FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed_now

            with mock.patch(
                "vps_ssh_launcher.maintenance.automation.datetime", _FixedDatetime
            ), mock.patch(
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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

            class _FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed_now

            client = mock.MagicMock()
            with mock.patch(
                "vps_ssh_launcher.maintenance.automation.datetime", _FixedDatetime
            ), mock.patch.dict(
                os.environ,
                {
                    "VPS_MAINT_TEST_PASSWORD": "unused-in-tests",
                    "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                },
            ), mock.patch(
                "vps_ssh_launcher.maintenance_cli.collect_inventory",
                return_value=inventory,
            ), mock.patch(
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry",
                return_value=client,
            ) as connect, mock.patch(
                "vps_ssh_launcher.maintenance_cli.cli.exec_remote",
                return_value=(0, "APPLY_VERIFIED\n", ""),
            ) as exec_remote:
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
            self.assertEqual(target["attempt_count"], 1)
            self.assertEqual(target["last_outcome"], "verified")

    def test_remote_adapter_commands_are_pinned_and_cpa_scoped(self) -> None:
        xray_command = build_xray_upgrade_command(
            version="26.3.27",
            sha256=self.XRaySha256,
        )
        self.assertIn("sha256sum --check", xray_command)
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
        self.assertIn(
            "docker inspect --format '{{.Image}}' \"$container_id\"", docker_command
        )
        self.assertIn('!= "$old_image_id"', docker_command)
        with self.assertRaisesRegex(ValueError, "must not target CPA"):
            build_docker_upgrade_command(
                compose_file="/opt/cliproxyapi/compose.yml",
                compose_sha256=self.XRaySha256,
                services=("app",),
                digests={"app": self.DockerDigest},
            )

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
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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
                "vps_ssh_launcher.maintenance_cli.cli.connect_with_retry"
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

    def test_pid_alive_liveness_semantics(self) -> None:
        self.assertTrue(_pid_alive(os.getpid()))
        dead = self._confirmed_dead_pid()
        if dead is None:
            self.skipTest("could not observe a dead pid")
        self.assertFalse(_pid_alive(dead))


if __name__ == "__main__":
    unittest.main()
