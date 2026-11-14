import contextlib
import io
import json
import os
import re
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
    policy_receipt_dir,
    _pins_table,
)
from vps_ssh_launcher.maintenance.adapters import (
    MaintenanceBusy,
    build_docker_upgrade_command,
    build_xray_upgrade_command,
    execute_action,
)
from vps_ssh_launcher.maintenance.inventory import (
    _ALLOWED_FACT_KEYS,
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
    record_remote_start_and_mark_applied,
    refund_automation_attempt,
    restore_unattended_busy_plan,
    save_plan,
    save_plan_if_absent,
)
from vps_ssh_launcher.maintenance.apply import (
    _apply_outcome,
    _execute_remote_plan,
    _record_unverified_receipt,
)
from vps_ssh_launcher.maintenance_cli import (
    build_parser,
    main,
)


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

    def test_plan_profile_scope_matches_file_and_live_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            with policy_path.open("a", encoding="utf-8") as stream:
                stream.write('\n[profiles.other.resources]\nxray = "upgrade"\n')
            record = InventoryRecord("bwg", True, {"xray_version": "26.3.26"})
            records = (record, replace(record, profile="other"))
            snapshot = InventorySnapshot("now", records, inventory_fingerprint(records))
            inventory_path = root / "inventory.json"
            write_inventory(inventory_path, snapshot)
            expected_fingerprint = inventory_fingerprint((record,))
            for mode in ("file", "live"):
                with (
                    self.subTest(mode=mode),
                    mock.patch.dict(
                        os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}
                    ),
                    mock.patch(
                        "vps_ssh_launcher.maintenance_cli.collect_inventory",
                        return_value=snapshot,
                    ),
                    mock.patch(
                        "vps_ssh_launcher.maintenance_cli.target_config.assert_private_target_config"
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    source_args = (
                        ["--inventory-file", str(inventory_path)]
                        if mode == "file"
                        else [
                            "--live-inventory",
                            "--run-integration",
                            "--target-config",
                            str(root / "target.json"),
                        ]
                    )
                    code = main(
                        [
                            "--config",
                            str(policy_path),
                            "--json",
                            "plan",
                            "--profile",
                            "bwg",
                            *source_args,
                        ]
                    )
                self.assertEqual(code, 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(
                    {action["profile"] for action in plan["actions"]}, {"bwg"}
                )
                self.assertEqual(plan["inventory_fingerprint"], expected_fingerprint)

    def test_live_plan_output_persists_review_inventory_without_reconnecting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            record = InventoryRecord("bwg", True, {"xray_version": "26.3.26"})
            snapshot = InventorySnapshot(
                "now", (record,), inventory_fingerprint((record,))
            )
            output = root / "review-plan.json"
            with (
                mock.patch.dict(os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory",
                    return_value=snapshot,
                ) as collect,
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.target_config.assert_private_target_config"
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "plan",
                            "--live-inventory",
                            "--run-integration",
                            "--target-config",
                            str(root / "target.json"),
                            "--output",
                            str(output),
                        ]
                    ),
                    0,
                )
            collect.assert_called_once()
            review_inventory = root / "review-plan-inventory.json"
            self.assertTrue(review_inventory.is_file())
            self.assertEqual(
                json.loads(review_inventory.read_text(encoding="utf-8"))["fingerprint"],
                snapshot.fingerprint,
            )
            self.assertTrue(output.is_file())
            if os.name != "nt":
                self.assertEqual(review_inventory.stat().st_mode & 0o777, 0o600)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_plan_publication_refuses_both_existing_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            record = InventoryRecord("bwg", True, {"xray_version": "26.3.26"})
            snapshot = InventorySnapshot(
                "now", (record,), inventory_fingerprint((record,))
            )
            for existing_kind in ("plan", "inventory"):
                with self.subTest(existing_kind=existing_kind):
                    output = root / f"{existing_kind}-collision.json"
                    inventory_path = output.with_name(output.stem + "-inventory.json")
                    target = output if existing_kind == "plan" else inventory_path
                    target.write_text("{}\n", encoding="utf-8")
                    with (
                        mock.patch.dict(
                            os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}
                        ),
                        mock.patch(
                            "vps_ssh_launcher.maintenance_cli.collect_inventory",
                            return_value=snapshot,
                        ) as collect,
                        mock.patch(
                            "vps_ssh_launcher.maintenance_cli.target_config.assert_private_target_config"
                        ),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        self.assertEqual(
                            main(
                                [
                                    "--config",
                                    str(policy_path),
                                    "plan",
                                    "--live-inventory",
                                    "--run-integration",
                                    "--target-config",
                                    str(root / "target.json"),
                                    "--output",
                                    str(output),
                                ]
                            ),
                            2,
                        )
                    collect.assert_called_once()
                    self.assertFalse((root / "state.db").exists())
                    self.assertEqual(
                        target.read_text(encoding="utf-8"),
                        "{}\n",
                    )
                    if existing_kind == "plan":
                        self.assertFalse(inventory_path.exists())
                    else:
                        self.assertFalse(output.exists())

    def test_plan_publication_failure_withdraws_the_new_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            record = InventoryRecord("bwg", True, {"xray_version": "26.3.26"})
            snapshot = InventorySnapshot(
                "now", (record,), inventory_fingerprint((record,))
            )
            output = root / "failed-publication.json"
            with (
                mock.patch.dict(os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory",
                    return_value=snapshot,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli._atomic_local_json",
                    side_effect=OSError("simulated publication failure"),
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "plan",
                            "--live-inventory",
                            "--run-integration",
                            "--target-config",
                            str(root / "target.json"),
                            "--output",
                            str(output),
                        ]
                    ),
                    2,
                )
            self.assertEqual(list_plans(root / "state.db"), [])

    def test_unknown_profile_is_rejected_before_live_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = self._policy(Path(directory))
            with (
                mock.patch.dict(os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
                mock.patch(
                    "vps_ssh_launcher.maintenance_cli.collect_inventory"
                ) as collect,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "plan",
                            "--live-inventory",
                            "--run-integration",
                            "--target-config",
                            str(Path(directory) / "target.json"),
                            "--profile",
                            "outside-policy",
                        ]
                    ),
                    2,
                )
            collect.assert_not_called()

    def test_apply_rejects_unknown_persisted_status_before_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            record = InventoryRecord("bwg", True, {"xray_version": "old"})
            snapshot = InventorySnapshot(
                "now", (record,), inventory_fingerprint((record,))
            )
            plan = build_plan(load_policy(policy_path), snapshot)
            for target in ("plan", "action"):
                with self.subTest(target=target):
                    stem = f"maintenance-{target}"
                    policy_path.write_text(
                        self._xray_upgrade_policy(root)
                        .read_text(encoding="utf-8")
                        .replace(
                            'state_path = "state.db"',
                            f'state_path = "{stem}.db"',
                        ),
                        encoding="utf-8",
                    )
                    state_path = root / f"{stem}.db"
                    save_plan(state_path, plan)
                    payload = plan.to_dict()
                    if target == "plan":
                        payload["status"] = "future_status"
                    else:
                        payload["actions"].append(
                            {**payload["actions"][0], "status": "future_status"}
                        )
                    with sqlite3.connect(state_path) as database:
                        database.execute(
                            "UPDATE plans SET plan_json = ?", (json.dumps(payload),)
                        )
                    with (
                        mock.patch.dict(
                            os.environ,
                            {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"},
                        ),
                        mock.patch(
                            "vps_ssh_launcher.maintenance.workflow.collect_inventory"
                        ) as collect,
                        mock.patch(
                            "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
                        ) as connect,
                        contextlib.redirect_stderr(io.StringIO()) as errors,
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
                                ]
                            ),
                            2,
                        )
                    self.assertIn("status is invalid", errors.getvalue())
                    collect.assert_not_called()
                    connect.assert_not_called()

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

    def test_policy_rejects_unknown_keys_at_every_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._policy(root).read_text(encoding="utf-8")
            variants = (
                (
                    base.replace(
                        "command_timeout = 10",
                        "command_timeout = 10\ncommand_timeot = 5",
                    ),
                    "Unsupported settings fields: command_timeot",
                ),
                (
                    base.replace(
                        "[profiles.bwg]\nenabled = true",
                        "[profiles.bwg]\nenabled = true\nenbled = true",
                    ),
                    "Unsupported profiles.bwg fields: enbled",
                ),
                (
                    base + "\n[dead]\nkey = 1\n",
                    "Unsupported maintenance policy sections: dead",
                ),
            )
            for text, message in variants:
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    path = root / "variant.toml"
                    path.write_text(text, encoding="utf-8")
                    load_policy(path)

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

    def test_inventory_emit_keys_and_allowlist_do_not_drift(self) -> None:
        # parse_probe_output silently drops any emitted key that is missing
        # from _ALLOWED_FACT_KEYS, so a new probe fact would vanish from every
        # fingerprint with no failing test. Equality guards both directions:
        # emitted-without-allowed (silent fact loss) and allowed-without-
        # emitted (dead allowlist entries masking a removed probe).
        emit_keys = set(re.findall(r"printf '([A-Za-z0-9_]+)=", INVENTORY_COMMAND))
        self.assertTrue(emit_keys)
        self.assertEqual(emit_keys, set(_ALLOWED_FACT_KEYS))

    def test_resource_observed_fact_keys_resolve_in_inventory(self) -> None:
        # Planner reads facts[observed_fact_key] (falling back to the generic
        # facts[<resource>] key); a key that never reaches the allowlist makes
        # the planner permanently read "unknown" and block every plan.
        for name, spec in RESOURCES.items():
            with self.subTest(resource=name):
                fact_key = spec.observed_fact_key or name
                self.assertIn(fact_key, set(_ALLOWED_FACT_KEYS))

    def test_inventory_detects_service_managed_xray_when_binary_is_not_on_path(
        self,
    ) -> None:
        self.assertIn('systemctl is-active --quiet "$1"', INVENTORY_COMMAND)
        self.assertIn("unit_state xray", INVENTORY_COMMAND)
        self.assertIn("unit_state sing-box", INVENTORY_COMMAND)
        self.assertIn("ss -lunH", INVENTORY_COMMAND)
        self.assertIn("udp_listeners", INVENTORY_COMMAND)
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

    def test_long_fact_truncation_stays_fingerprint_tail_sensitive(self) -> None:
        # List-valued identity facts can exceed the 256-char storage cap; two
        # different sets sharing a prefix must still produce different stored
        # facts, or plan/apply revalidation accepts stale plans.
        prefix = "udp_listeners=" + "/0.0.0.0:443," * 40
        first = parse_probe_output("bwg", prefix + "10.0.0.1:1\n")
        second = parse_probe_output("bwg", prefix + "10.0.0.2:2\n")
        self.assertLessEqual(len(first.facts["udp_listeners"]), 270)
        self.assertNotEqual(first.facts["udp_listeners"], second.facts["udp_listeners"])

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

    def test_plan_blocks_absent_required_resources_without_installing(
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
        self.assertEqual(statuses["docker"], "blocked")
        self.assertEqual(statuses["xray"], "blocked")
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
                    iteration_path = root / f"state-{status}.db"
                    iteration_policy = policy_path.with_name(
                        f"maintenance-{status}.toml"
                    )
                    iteration_policy.write_text(
                        policy_path.read_text(encoding="utf-8").replace(
                            'state_path = "state.db"',
                            f'state_path = "{iteration_path.name}"',
                        ),
                        encoding="utf-8",
                    )
                    state_path = iteration_path
                    original = build_plan(policy, inventory)
                    save_plan_if_absent(state_path, original)
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
                "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
            ) as connect:
                # A disabled ack is a structured guard refusal (exit 1), not
                # an unexpected local error (exit 2); the receipt contract is
                # pinned by test_unattended_ack_refusal_is_structured_exit_one_with_receipt.
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
                    1,
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
        plan = save_plan_if_absent(
            state_path,
            replace(plan, may_refund_unattended_attempt=True),
        )
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
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
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
                    1,
                )
                connect.assert_not_called()

            # The missing integration opt-in is a local guard refusal: it is
            # reported as a structured refused outcome with a receipt, the
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
                                "strict_host_key_checking": True,
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
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
                    return_value=client,
                ) as connect,
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.execution.exec_remote",
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
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
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
                        profile="bwg",
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
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
                    return_value=client,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.execute_action",
                    side_effect=RuntimeError("simulated adapter crash"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated adapter crash"):
                    _execute_remote_plan(
                        args,
                        policy,
                        plan,
                        state_path=state_path,
                        profile="bwg",
                    )
            stored = load_plan(state_path)
            self.assertEqual(stored.actions[0].status, "applied")
            self.assertEqual(stored.status, "applied")
            client.close.assert_called_once_with()

    def test_interrupted_busy_attempt_is_durably_retryable(self) -> None:
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
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "VPS_MAINT_TEST_PASSWORD": "unused-in-tests",
                        "VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1",
                    },
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
                    return_value=mock.MagicMock(),
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.execute_action",
                    side_effect=MaintenanceBusy("Host busy; no remote write."),
                ),
            ):
                with self.assertRaises(MaintenanceBusy):
                    _execute_remote_plan(
                        Namespace(
                            run_integration=True,
                            target_config=str(target_config),
                            profile=None,
                        ),
                        policy,
                        plan,
                        state_path=state_path,
                        profile="bwg",
                    )
            # Bind a fresh local attempt to the stored intent, then simulate
            # the adapter's busy response. The durable action must not remain
            # permanently "applied" merely because the caller's old in-memory
            # plan is replaced during cleanup.
            stored = load_plan(state_path)
            self.assertEqual(stored.actions[0].status, "applied")
            record_remote_start_and_mark_applied(
                state_path,
                profile="bwg",
                resource="xray",
                pin_digest=pin_fingerprint(policy, plan.actions[0]),
                plan_id=plan.plan_id,
            )
            restored = restore_unattended_busy_plan(
                state_path,
                plan_id=plan.plan_id,
                action_profile="bwg",
                action_resource="xray",
                preserve_refund=True,
            )
            self.assertTrue(restored.may_refund_unattended_attempt)
            refund_automation_attempt(
                state_path,
                profile="bwg",
                resource="xray",
                pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
                plan_id=plan.plan_id,
            )
            restored = load_plan(state_path)
            target_after_refund = load_automation_target(
                state_path,
                profile="bwg",
                resource="xray",
                pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
            )
            assert target_after_refund is not None
            self.assertEqual(target_after_refund["attempt_count"], 0)
            self.assertEqual(target_after_refund["last_outcome"], "busy")
            self.assertEqual(restored.actions[0].status, "planned")
            self.assertEqual(restored.status, plan.status)

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
        # The adapter records the pre-upgrade service state and restores it:
        # a host intentionally running xray inactive must not be started by
        # an upgrade round or its rollback.
        self.assertIn("service_was_active=1", xray_command)
        # Keep the URL shell-quoted through the second command line; unwrapped
        # whitespace in a line-continuation would split the URL into $2/$3.
        self.assertIn('"$archive_url" -o "$tmp_dir/xray.zip"', xray_command)
        self.assertIn('"$expected_artifact_sha256" "$tmp_dir/xray.zip"', xray_command)
        self.assertIn('if [ "$service_was_active" -eq 1 ]; then', xray_command)
        capture_idx = xray_command.index("service_was_active=1")
        first_mutation_idx = xray_command.index('cp -a "$binary" "$backup_dir/xray"')
        self.assertLess(capture_idx, first_mutation_idx)
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
        self.assertIn('docker image inspect "$image_ref"', docker_command)
        self.assertIn("PULL_SKIPPED service=$service", docker_command)
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

        for scenario in (
            "unchanged",
            "changed",
            "unchanged_after_restart",
            "inactive_changed",
        ):
            with self.subTest(scenario=scenario):
                changed_binary = (
                    "printf 'replacement\\n' > \"$binary\""
                    if scenario in ("changed", "inactive_changed")
                    else ":"
                )
                service_was_active = "0" if scenario == "inactive_changed" else "1"
                payload = f"""set -Eeuo pipefail
scenario='{scenario}'
fixture_root="$(mktemp -d)"
backup_dir="$fixture_root/backup"
tmp_dir="$fixture_root/tmp"
binary="$fixture_root/xray"
confdir="$fixture_root/conf"
restart_count=0
service_restart_attempted=0
service_was_active={service_was_active}
mkdir -p "$backup_dir" "$tmp_dir" "$confdir"
printf '#!/bin/sh\\nexit 0\\n' > "$binary"
chmod 0755 "$binary"
cp -a "$binary" "$backup_dir/xray"
backup_ready=1
if [ "$scenario" = unchanged_after_restart ]; then service_restart_attempted=1; fi
{changed_binary}
systemctl() {{
  if [ "$scenario" = inactive_changed ]; then
    # The host runs xray inactive; restart would wrongly start it.
    return 1
  fi
  if [ "$1" = restart ]; then
    restart_count=$((restart_count + 1))
    echo RESTART_INVOKED >&2
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
                if scenario == "inactive_changed":
                    # Restoring the pre-upgrade state means staying inactive:
                    # the rollback must not have started the service.
                    self.assertNotIn("RESTART_INVOKED", completed.stderr)

    def test_xray_backup_failure_does_not_restart_unmodified_service(self) -> None:
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
        payload = f"""set -Eeuo pipefail
fixture_root="$(mktemp -d)"
backup_dir="$fixture_root/backup"
tmp_dir="$fixture_root/tmp"
binary="$fixture_root/xray"
confdir="$fixture_root/conf"
mkdir -p "$backup_dir" "$tmp_dir" "$confdir"
printf '#!/bin/sh\\nexit 0\\n' > "$binary"
chmod 0755 "$binary"
cp -a "$binary" "$backup_dir/xray"
service_restart_attempted=0
systemctl() {{
  if [ "${{1:-}}" = restart ]; then echo RESTART_ATTEMPTED >> "$fixture_root/calls"; fi
  return 0
}}
rollback() {{
{rollback_body}
}}
trap rollback ERR EXIT
false
"""
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [bash, "-c", payload],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("ROLLBACK_VERIFIED", completed.stdout)
            calls = Path(directory) / "calls"
            self.assertFalse(
                calls.exists() and "RESTART_ATTEMPTED" in calls.read_text()
            )
            # The embedded command copy must run before backup_ready=1.
            self.assertLess(
                command.index('cmp -s "$binary" "$backup_dir/xray"'),
                command.index("backup_ready=1"),
            )

    def test_docker_rollback_snapshot_pins_preexisting_image_digest(self) -> None:
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app",),
            digests={"app": self.DockerDigest},
        )
        marker = (
            'python3 - "$backup_dir/rollback-compose.json" '
            '"$old_image_pairs" "$backup_dir/rollback-compose.yml" '
            "\"$expected_pairs\" <<'PY'\n"
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
                    "app|sha256:" + "b" * 64,
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

    def test_docker_digest_pins_are_bound_to_compose_services_before_pull(self) -> None:
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256=self.XRaySha256,
            services=("app", "db"),
            digests={"app": self.DockerDigest, "db": "sha256:" + "c" * 64},
        )
        marker = (
            'python3 - "$backup_dir/rollback-compose.json" '
            '"$old_image_pairs" "$backup_dir/rollback-compose.yml" '
            "\"$expected_pairs\" <<'PY'\n"
        )
        program = command.split(marker, 1)[1].split("\nPY\n", 1)[0]
        self.assertLess(
            command.index('rollback-compose.yml" "$expected_pairs"'),
            command.index("ADAPTER_STAGE=pull"),
        )
        expected_pairs = "app|" + self.DockerDigest + " db|sha256:" + "c" * 64
        for app_digest, db_digest, expected_success in (
            ("b" * 64, "c" * 64, True),
            ("c" * 64, "b" * 64, False),
        ):
            with (
                self.subTest(expected_success=expected_success),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = root / "compose.json"
                destination = root / "rollback-compose.yml"
                source.write_text(
                    json.dumps(
                        {
                            "services": {
                                "app": {"image": "example/app@sha256:" + app_digest},
                                "db": {"image": "example/db@sha256:" + db_digest},
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                old_argv = sys.argv
                sys.argv = [
                    "docker-preflight",
                    str(source),
                    "",
                    str(destination),
                    expected_pairs,
                ]
                try:
                    if expected_success:
                        exec(compile(program, "<docker-preflight>", "exec"), {})
                    else:
                        with self.assertRaisesRegex(
                            SystemExit, "DIGEST_PIN_MISMATCH service=app"
                        ):
                            exec(compile(program, "<docker-preflight>", "exec"), {})
                finally:
                    sys.argv = old_argv
                self.assertEqual(destination.exists(), expected_success)

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
            (1, "exit 75", 75),
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
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (
                0,
                "APPLY_VERIFIED\necho APPLY_VERIFIED\n",
                "",
            ),
        )
        self.assertEqual(result.status, "unverified")
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (0, "APPLY_VERIFIED\n", "ROLLBACK_VERIFIED\n"),
        )
        self.assertEqual(result.status, "verified")

    def test_adapter_raises_busy_for_reserved_exit_with_marker(self) -> None:
        # exit 75 + MAINTENANCE_BUSY is emitted by the adapters' flock guard
        # before any mutation: it must raise the typed busy error instead of
        # being folded into "unverified" (which would burn an unattended
        # attempt slot on a transient collision).
        action = mock.Mock(resource="xray")
        action.resource = "xray"
        action.status = "planned"
        with self.assertRaises(MaintenanceBusy):
            execute_action(
                action,
                pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
                executor=lambda command: (75, "", "MAINTENANCE_BUSY\n"),
            )
        # The reserved exit code without the marker stays fail-closed.
        result = execute_action(
            action,
            pins={"xray": {"version": "26.3.27", "sha256": self.XRaySha256}},
            executor=lambda command: (75, "", ""),
        )
        self.assertEqual(result.status, "unverified")

    def test_adapter_refusal_marker_is_distinct_from_unverified(self) -> None:
        """A pre-mutation refusal must not collapse into "unverified".

        The adapters print APPLY_REFUSED_BEFORE_MUTATION as their final
        receipt when a pin/precondition refuses before any write (the xray
        adapter on stdout, the docker adapter on stderr). Folding that into
        "unverified" made the receipt claim the host state was unknown when
        it factually was untouched, hiding the pin mismatch from the plan
        and the receipt.
        """
        action = mock.Mock(resource="xray")
        action.resource = "xray"
        action.status = "planned"
        pins = {"xray": {"version": "26.3.27", "sha256": self.XRaySha256}}
        # xray adapter: refusal marker is the final stdout line.
        result = execute_action(
            action,
            pins=pins,
            executor=lambda command: (47, "APPLY_REFUSED_BEFORE_MUTATION\n", ""),
        )
        self.assertEqual(result.status, "refused_before_mutation")
        # docker adapter: refusal marker is the final stderr line.
        result = execute_action(
            action,
            pins=pins,
            executor=lambda command: (52, "", "APPLY_REFUSED_BEFORE_MUTATION\n"),
        )
        self.assertEqual(result.status, "refused_before_mutation")
        # The refusal is the strongest claim and outranks a stray success.
        result = execute_action(
            action,
            pins=pins,
            executor=lambda command: (47, "APPLY_REFUSED_BEFORE_MUTATION\n", ""),
        )
        self.assertNotEqual(result.status, "verified")
        # A refusal does not satisfy the stdout-only apply receipt contract:
        # the marker on stderr must not verify anything.
        result = execute_action(
            action,
            pins=pins,
            executor=lambda command: (0, "", "APPLY_REFUSED_BEFORE_MUTATION\n"),
        )
        self.assertEqual(result.status, "refused_before_mutation")

    def test_unattended_busy_host_refunds_attempt_and_stays_retryable(self) -> None:
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
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
                    return_value=mock.MagicMock(),
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.execution.exec_remote",
                    return_value=(75, "", "MAINTENANCE_BUSY\n"),
                ),
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
            policy = load_policy(policy_path)
            target = load_automation_target(
                state_path,
                profile="bwg",
                resource="xray",
                pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
            )
            self.assertIsNotNone(target)
            assert target is not None  # unittest asserts do not narrow for mypy
            # The busy collision must not consume the attempt budget.
            self.assertEqual(target["attempt_count"], 0)
            self.assertEqual(target["last_outcome"], "busy")
            # The action stays planned so the next window can retry it.
            persisted = load_plan(state_path)
            self.assertEqual(persisted.plan_id, plan.plan_id)
            self.assertEqual(persisted.actions[0].status, "planned")
            receipts = list((root / "receipts").glob("*.json"))
            self.assertTrue(receipts)
            payload = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "deferred")
            self.assertIn("Host busy", payload["reason"])

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

    def test_receipt_and_terminal_errors_redact_unexpected_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._policy(root))
            record = InventoryRecord(
                "bwg", True, {"xray": "present", "xray_version": "26.3.26"}
            )
            plan = build_plan(
                policy,
                InventorySnapshot(
                    "now",
                    (record,),
                    inventory_fingerprint((record,)),
                ),
            )
            secret_text = (
                'password="sentinel secret" token: sentinel-token '
                "https://user:sentinel-password@example.invalid "
                "password_env=SECRET_ENV_NAME"
            )
            path = write_receipt(
                root / "receipts",
                plan,
                outcome="refused",
                reason=secret_text,
            )
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sentinel-password", text)
            self.assertNotIn("sentinel-token", text)
            self.assertNotIn("SECRET_ENV_NAME", text)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.apply.load_plan",
                    side_effect=RuntimeError("connect failed token=sentinel-token"),
                ),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(self._xray_upgrade_policy(root)),
                            "apply",
                            "--yes",
                        ]
                    ),
                    2,
                )
            self.assertNotIn("sentinel-token", stderr.getvalue())
            self.assertIn("<redacted>", stderr.getvalue())

    def test_receipts_preserve_same_plan_events_at_the_same_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._policy(root))
            records = (InventoryRecord(profile="bwg", reachable=True, facts={}),)
            plan = build_plan(
                policy,
                InventorySnapshot(
                    created_at="now",
                    records=records,
                    fingerprint=inventory_fingerprint(records),
                ),
            )
            with mock.patch("vps_ssh_launcher.maintenance.receipt.datetime") as clock:
                clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
                first = write_receipt(
                    root / "receipts", plan, outcome="dry_run", reason="review"
                )
                second = write_receipt(
                    root / "receipts", plan, outcome="refused", reason="guard"
                )
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text())["outcome"], "dry_run")
            self.assertEqual(json.loads(second.read_text())["outcome"], "refused")

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
                "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
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
                "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
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

    def test_apply_interrupted_mid_remote_writes_unverified_receipt(self) -> None:
        # A transport drop or hard timeout after the adapter started must leave
        # a receipt for THIS event instead of relying on the next apply round's
        # refused receipt to represent it. The reason records the exception
        # type only — never its message, which can carry remote output.
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
            with (
                mock.patch.dict(os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
                mock.patch(
                    "vps_ssh_launcher.maintenance.apply._execute_remote_plan",
                    side_effect=RuntimeError("channel dropped"),
                ),
            ):
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
            receipts = list((root / "receipts").glob("*.json"))
            self.assertTrue(receipts)
            payload = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "unverified")
            self.assertIn("RuntimeError", payload["reason"])
            self.assertNotIn("channel dropped", payload["reason"])

    def test_unattended_ack_refusal_is_structured_exit_one_with_receipt(self) -> None:
        # The automation layer owns the acknowledgement guard: a disabled ack
        # must surface as a structured not-OK refusal (exit 1 + refused
        # receipt, per the CLI docstring's exit-code contract), not as an
        # unexpected local error (exit 2, no receipt).
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            self.assertFalse(policy.automation.unattended_apply)
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
            self.assertEqual(
                main(
                    [
                        "--config",
                        str(policy_path),
                        "apply",
                        "--yes",
                        "--unattended",
                        "--remote-write",
                    ]
                ),
                1,
            )
            receipts = list((root / "receipts").glob("*.json"))
            self.assertTrue(receipts)
            payload = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "refused")
            self.assertIn("Unattended apply is disabled", payload["reason"])

    def test_apply_refuses_unresolved_terminal_action_without_retrying_remote(
        self,
    ) -> None:
        # All three persisted non-retryable terminal states refuse the apply
        # without touching the remote and keep their stored status untouched.
        for status in ("applied", "unverified", "rolled_back"):
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
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
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
                self.assertEqual(stored.actions[0].status, status)
                self.assertEqual(stored.status, status)

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
                "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
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
                    1,
                )
                connect.assert_not_called()
            receipts = list(policy_receipt_dir(policy).glob("*.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(receipt["outcome"], "refused")
            self.assertIn("--run-integration", receipt["reason"])

    def test_remote_apply_fingerprint_drift_is_structured_refusal(self) -> None:
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
            drifted_records = (
                InventoryRecord(
                    profile="bwg",
                    reachable=True,
                    facts={"xray": "present", "xray_version": "26.3.27"},
                ),
            )
            drifted = InventorySnapshot(
                created_at="later",
                records=drifted_records,
                fingerprint=inventory_fingerprint(drifted_records),
            )
            self.assertNotEqual(drifted.fingerprint, plan.inventory_fingerprint)
            with (
                mock.patch.dict(os.environ, {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=drifted,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.load_config",
                    return_value={
                        "profiles": {
                            "bwg": {
                                "host": "203.0.113.10",
                                "user": "root",
                                "password": "fixture",
                            }
                        }
                    },
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
                ) as connect,
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            # Pin the target config explicitly so the refusal
                            # under test (fingerprint drift) does not depend on
                            # a user-local target.json existing on the runner.
                            "--target-config",
                            str(root / "target.json"),
                            "--remote-write",
                            "--run-integration",
                        ]
                    ),
                    1,
                )
                connect.assert_not_called()
            receipts = list(policy_receipt_dir(policy).glob("*.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(receipt["outcome"], "refused")
            self.assertIn("fingerprint", receipt["reason"])

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
                "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
            ) as connect:
                # A stale policy fingerprint is a structured refusal (exit 1
                # + refused receipt), not an unexpected local error.
                self.assertEqual(
                    main(["--config", str(policy_path), "apply", "--yes"]),
                    1,
                )
                receipts = list((root / "receipts").glob("*.json"))
                self.assertTrue(receipts)
                payload = json.loads(receipts[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["outcome"], "refused")
                self.assertIn("fingerprint does not match", payload["reason"])
                connect.assert_not_called()

    def test_apply_profile_mismatch_is_enforced_on_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
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
            save_plan(
                root / "state.db", build_plan(load_policy(policy_path), inventory)
            )
            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
                ) as connect,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--profile",
                            "zz",
                        ]
                    ),
                    1,
                )
                self.assertIn(
                    "vps-maint refused: --profile does not match the stored plan profile",
                    stderr.getvalue(),
                )
                # The refusal is a structured not-OK outcome with a receipt,
                # same bucket as the other guard refusals (12b5292 contract).
                receipts = list((root / "receipts").glob("*.json"))
                self.assertTrue(receipts)
                payload = json.loads(receipts[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["outcome"], "refused")
                self.assertIn(
                    "--profile does not match the stored plan profile",
                    payload["reason"],
                )
                self.assertEqual(
                    main(
                        [
                            "--config",
                            str(policy_path),
                            "apply",
                            "--yes",
                            "--profile",
                            "bwg",
                        ]
                    ),
                    0,
                )
                connect.assert_not_called()

    def test_unattended_guard_refusal_is_a_structured_not_ok_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, _, _ = self._unattended_plan(policy_path, state_path)
            refusal = (
                "Current local time is outside the unattended maintenance window; "
                "no remote write was attempted."
            )
            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.apply.authorize_unattended_apply",
                    side_effect=ValueError(refusal),
                ) as authorize,
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
                ) as connect,
                contextlib.redirect_stdout(io.StringIO()) as stdout,
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
                    1,
                )
            authorize.assert_called_once()
            connect.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("outcome: refused", output)
            self.assertIn(refusal, output)
            stored = load_plan(state_path)
            self.assertEqual(stored.plan_id, plan.plan_id)

    def test_unattended_apply_outside_window_refuses_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            policy_path.write_text(
                policy_path.read_text(encoding="utf-8")
                .replace('window_start = "00:00"', 'window_start = "20:00"')
                .replace('window_end = "23:59"', 'window_end = "22:00"'),
                encoding="utf-8",
            )
            state_path = root / "state.db"
            plan, fixed_now, _ = self._unattended_plan(policy_path, state_path)

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
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry"
                ) as connect,
                contextlib.redirect_stdout(io.StringIO()) as stdout,
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
                    1,
                )
                connect.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("outcome: refused", output)
            self.assertIn("outside the unattended maintenance window", output)
            self.assertEqual(
                load_automation_target(
                    state_path,
                    profile=plan.actions[0].profile,
                    resource=plan.actions[0].resource,
                    pin_fingerprint=pin_fingerprint(
                        load_policy(policy_path), plan.actions[0]
                    ),
                ),
                None,
            )

    def test_unexpected_apply_failure_still_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            self._unattended_plan(policy_path, state_path)
            with (
                mock.patch(
                    "vps_ssh_launcher.maintenance.apply.authorize_unattended_apply",
                    side_effect=RuntimeError("simulated unexpected failure"),
                ),
                contextlib.redirect_stderr(io.StringIO()),
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

    def test_unattended_interruption_after_remote_start_burns_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._unattended_xray_policy(root)
            state_path = root / "state.db"
            plan, fixed_now, inventory = self._unattended_plan(policy_path, state_path)
            policy = load_policy(policy_path)
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
                    "vps_ssh_launcher.maintenance.workflow.collect_inventory",
                    return_value=inventory,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.target_config.assert_private_target_config"
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.connection.connect_with_retry",
                    return_value=client,
                ),
                mock.patch(
                    "vps_ssh_launcher.maintenance.workflow.execute_action",
                    side_effect=RuntimeError("simulated transport drop"),
                ),
                contextlib.redirect_stderr(io.StringIO()),
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
                    2,
                )
            client.close.assert_called_once_with()
            target = load_automation_target(
                state_path,
                profile="bwg",
                resource="xray",
                pin_fingerprint=pin_fingerprint(policy, plan.actions[0]),
            )
            self.assertIsNotNone(target)
            assert target is not None
            self.assertEqual(target["attempt_count"], 1)
            self.assertEqual(target["last_outcome"], "unverified")
            receipts = list((root / "receipts").glob("*.json"))
            self.assertTrue(receipts)
            payload = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["outcome"], "unverified")
            self.assertIn("RuntimeError", payload["reason"])
            self.assertNotIn("simulated transport drop", payload["reason"])

    def test_apply_reconciles_durable_busy_refusal_after_prewrite_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._xray_upgrade_policy(root)
            policy = load_policy(policy_path)
            action = MaintenanceAction(
                "bwg", "xray", "upgrade", "26.3.26", "planned", "fixture upgrade"
            )
            plan = MaintenancePlan(
                plan_id="plan-" + "b" * 16,
                created_at="now",
                policy_fingerprint=policy.fingerprint,
                inventory_fingerprint="fixture",
                actions=(action,),
                status="planned",
            )
            state_path = root / "state.db"
            save_plan(state_path, plan)

            def busy_after_durable_apply(
                *args: Any, **kwargs: Any
            ) -> tuple[MaintenancePlan, str, str, int]:
                on_start = kwargs.get("on_remote_start")
                self.assertIsNotNone(on_start)
                assert callable(on_start)
                on_start()
                raise MaintenanceBusy("Host busy; no remote write.")

            args = Namespace(remote_write=True, unattended=False)
            with mock.patch(
                "vps_ssh_launcher.maintenance.apply._execute_remote_plan",
                side_effect=busy_after_durable_apply,
            ):
                outcome, reason, code = _apply_outcome(
                    args,
                    policy,
                    plan,
                    "bwg",
                    state_path,
                    policy_receipt_dir(policy),
                )
            self.assertEqual((outcome, code), ("deferred", 0))
            self.assertIn("Host busy", reason)
            restored = load_plan(state_path)
            self.assertEqual(restored.actions[0].status, "planned")

    def test_unverified_receipt_records_error_type_not_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(self._xray_upgrade_policy(root))
            plan = MaintenancePlan(
                plan_id="plan-" + "c" * 16,
                created_at="now",
                policy_fingerprint=policy.fingerprint,
                inventory_fingerprint="fixture",
                actions=(
                    MaintenanceAction(
                        "bwg", "xray", "upgrade", "26.3.26", "planned", "fixture"
                    ),
                ),
                status="planned",
            )
            state_path = root / "state.db"
            save_plan(state_path, plan)
            receipt_dir = root / "receipts"
            _record_unverified_receipt(
                plan,
                state_path,
                receipt_dir,
                RuntimeError("sentinel-secret"),
            )
            payload = json.loads(
                next(receipt_dir.glob("*.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["reason"], "apply interrupted: RuntimeError")

    def test_apply_parser_accepts_each_subcommand_after_refactor(self) -> None:
        parser = build_parser()
        commands = (
            ("inventory", {"target_config", "profile", "output", "run_integration"}),
            (
                "plan",
                {
                    "inventory_file",
                    "live_inventory",
                    "target_config",
                    "profile",
                    "run_integration",
                    "output",
                },
            ),
            (
                "apply",
                {
                    "plan_id",
                    "yes",
                    "remote_write",
                    "unattended",
                    "run_integration",
                    "target_config",
                    "profile",
                },
            ),
            ("history", {"limit"}),
        )
        for action, options in commands:
            with self.subTest(action=action):
                parsed = parser.parse_args(
                    ["apply", "--yes"] if action == "apply" else [action]
                )
                self.assertEqual(parsed.action, action)
                self.assertTrue(options <= set(vars(parsed)))


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
            owner["created_at"] = (
                datetime.now().astimezone() - timedelta(minutes=181)
            ).isoformat()
            lock.write_text(json.dumps(owner), encoding="utf-8")
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
            owner = json.loads(lock.read_text(encoding="utf-8"))
            owner["token"] = "c" * 32
            lock.write_text(json.dumps(owner), encoding="utf-8")
            with unattended_lock(lock):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_recovery_never_deletes_a_replaced_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            old_owner = {
                "pid": 999_999_999,
                "created_at": "2000-01-01T00:00:00+00:00",
                "token": "a" * 32,
            }
            successor = {
                "pid": os.getpid(),
                "created_at": datetime.now().astimezone().isoformat(),
                "token": "b" * 32,
            }
            self._write_lock(lock, 999_999_999, minutes_ago=181)
            payload = json.loads(lock.read_text(encoding="utf-8"))
            payload["token"] = old_owner["token"]
            lock.write_text(json.dumps(payload), encoding="utf-8")
            real_replace = os.replace

            def replace_then_compete(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                destination.write_text(json.dumps(successor), encoding="utf-8")

            with (
                mock.patch.object(os, "replace", side_effect=replace_then_compete),
                mock.patch(
                    "vps_ssh_launcher.maintenance.automation._pid_alive",
                    side_effect=lambda pid: pid == os.getpid(),
                ),
                self.assertRaisesRegex(ValueError, "owns the lock"),
            ):
                with unattended_lock(lock):
                    self.fail("A stale recovery must not delete its successor.")
            self.assertEqual(
                json.loads(lock.read_text(encoding="utf-8"))["token"],
                successor["token"],
            )
            self.assertTrue(lock.with_name(lock.name + ".guard").exists())
            for candidate in Path(tmp).glob(f".{lock.name}.recovery-*"):
                candidate.unlink()

    def test_legacy_dead_owner_requires_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "unattended.lock"
            self._write_lock(lock, 999999999, minutes_ago=181)
            with self.assertRaisesRegex(ValueError, "legacy dead lock owner"):
                with unattended_lock(lock):
                    self.fail(
                        "An unfenced legacy owner cannot be deleted automatically."
                    )
            self.assertTrue(lock.exists())

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
