import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vps_ssh_launcher.maintenance.config import load_policy
from vps_ssh_launcher.maintenance.fingerprint import fingerprint
from vps_ssh_launcher.maintenance.inventory import (
    INVENTORY_COMMAND,
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
    load_plan,
    save_plan,
)
from vps_ssh_launcher.maintenance_cli import main


class MaintenanceControlPlaneTests(unittest.TestCase):
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
            fingerprint=fingerprint([record.to_dict() for record in snapshot.records]),
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
            fingerprint=fingerprint([record.to_dict() for record in records]),
        )
        plan = build_plan(policy, inventory)
        statuses = {action.resource: action.status for action in plan.actions}
        self.assertEqual(statuses["cpa"], "deferred")
        self.assertEqual(statuses["proxy_core"], "blocked")
        self.assertEqual(statuses["docker"], "noop")
        self.assertEqual(statuses["xray"], "noop")
        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.plan_id, build_plan(policy, inventory).plan_id)

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
                fingerprint=fingerprint([record.to_dict() for record in records]),
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
                fingerprint=fingerprint([record.to_dict() for record in records]),
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


if __name__ == "__main__":
    unittest.main()
