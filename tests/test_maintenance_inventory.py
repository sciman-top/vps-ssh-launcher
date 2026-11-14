"""Behaviour contract for the read-only inventory collector.

`collect_inventory` decides reachability, honours the per-profile enable flag,
and turns any connector failure into a structured record instead of aborting
the whole run. None of those paths had behavioural coverage: the plan/execute
tests stub the collector itself, so a regression here would only surface as a
wrong plan on a real host.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from launcher_fakes import patch_attr
from vps_ssh_launcher.maintenance import inventory
from vps_ssh_launcher.maintenance.config import load_policy

PROBE_OUTPUT = "hostname=bwg\nos=Linux\nlisteners=0.0.0.0:22\n"


def _policy_file(root: Path, *, enabled: bool = True) -> Path:
    path = root / "maintenance.toml"
    state = "true" if enabled else "false"
    path.write_text(
        (
            "[settings]\n"
            "strict_host_key_checking = true\n"
            "command_timeout = 10\n"
            'state_path = "state.db"\n'
            'receipt_dir = "receipts"\n'
            "\n"
            "[profiles.bwg]\n"
            f"enabled = {state}\n"
            "\n"
            "[profiles.bwg.resources]\n"
            'cpa = "deferred"\n'
        ),
        encoding="utf-8",
    )
    return path


DEFAULT_PROFILES: dict[str, Any] = {
    "bwg": {"host": "203.0.113.10", "user": "root", "password": "fixture"}
}


def _target_file(root: Path, profiles: dict[str, Any] | None = None) -> Path:
    # `profiles or DEFAULT` would silently replace an intentional empty object,
    # which is exactly the case one of these tests needs to send.
    path = root / "target.json"
    path.write_text(
        json.dumps({"profiles": DEFAULT_PROFILES if profiles is None else profiles}),
        encoding="utf-8",
    )
    if __import__("os").name != "nt":
        path.chmod(0o600)
    return path


class CollectInventoryTests(unittest.TestCase):
    def test_a_reachable_probe_becomes_a_record_with_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root)
            snapshot = inventory.collect_inventory(
                policy,
                target,
                connector=lambda name, entry: (0, PROBE_OUTPUT, ""),
            )
        self.assertEqual([record.profile for record in snapshot.records], ["bwg"])
        record = snapshot.records[0]
        self.assertTrue(record.reachable)
        self.assertEqual(record.facts["hostname"], "bwg")

    def test_a_non_zero_probe_exit_is_an_unreachable_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root)
            snapshot = inventory.collect_inventory(
                policy,
                target,
                connector=lambda name, entry: (255, "", "denied"),
            )
        record = snapshot.records[0]
        self.assertFalse(record.reachable)
        self.assertEqual(record.error_class, "remote_command_error")

    def test_a_connector_exception_becomes_a_classified_record(self) -> None:
        def boom(name: str, entry: dict[str, Any]) -> tuple[int, str, str]:
            raise TimeoutError("probe timed out")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root)
            snapshot = inventory.collect_inventory(policy, target, connector=boom)
        record = snapshot.records[0]
        self.assertFalse(record.reachable)
        # The class name is lowercased so the record stays greppable.
        self.assertEqual(record.error_class, "timeouterror")

    def test_a_disabled_profile_is_skipped_without_connecting(self) -> None:
        calls: list[str] = []

        def connector(name: str, entry: dict[str, Any]) -> tuple[int, str, str]:
            calls.append(name)
            return 0, PROBE_OUTPUT, ""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root, enabled=False))
            target = _target_file(root)
            snapshot = inventory.collect_inventory(policy, target, connector=connector)
        self.assertEqual(calls, [])
        self.assertEqual(snapshot.records[0].error_class, "disabled_by_policy")

    def test_a_profile_missing_from_target_config_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(
                root,
                {
                    "other": {
                        "host": "203.0.113.11",
                        "user": "root",
                        "password": "fixture",
                    }
                },
            )
            with self.assertRaisesRegex(ValueError, "Profiles missing"):
                inventory.collect_inventory(
                    policy, target, connector=lambda name, entry: (0, "", "")
                )

    def test_an_empty_profiles_object_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root, {})
            with self.assertRaisesRegex(ValueError, "non-empty"):
                inventory.collect_inventory(
                    policy, target, connector=lambda name, entry: (0, "", "")
                )

    def test_the_default_connector_closes_its_client(self) -> None:
        closed: list[bool] = []

        class FakeClient:
            def close(self) -> None:
                closed.append(True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root)
            with (
                patch_attr(
                    inventory.connection,
                    "connect_with_retry",
                    return_value=FakeClient(),
                ),
                patch_attr(
                    inventory.execution,
                    "exec_remote",
                    return_value=(0, PROBE_OUTPUT, ""),
                ),
            ):
                snapshot = inventory.collect_inventory(policy, target)
        self.assertEqual(closed, [True])
        self.assertTrue(snapshot.records[0].reachable)

    def test_the_default_connector_closes_its_client_on_failure(self) -> None:
        closed: list[bool] = []

        class FakeClient:
            def close(self) -> None:
                closed.append(True)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = load_policy(_policy_file(root))
            target = _target_file(root)
            with (
                patch_attr(
                    inventory.connection,
                    "connect_with_retry",
                    return_value=FakeClient(),
                ),
                patch_attr(
                    inventory.execution,
                    "exec_remote",
                    side_effect=OSError("channel closed"),
                ),
            ):
                snapshot = inventory.collect_inventory(policy, target)
        # A failed probe must not leak the SSH client.
        self.assertEqual(closed, [True])
        self.assertFalse(snapshot.records[0].reachable)


class InventoryFileTests(unittest.TestCase):
    def _snapshot(self) -> inventory.InventorySnapshot:
        return inventory.InventorySnapshot(
            "2026-11-14T00:00:00+00:00",
            (inventory.InventoryRecord(profile="bwg", reachable=True, facts={}),),
            inventory.inventory_fingerprint(
                (inventory.InventoryRecord(profile="bwg", reachable=True, facts={}),)
            ),
        )

    def test_write_then_load_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            snapshot = self._snapshot()
            inventory.write_inventory(path, snapshot)
            loaded = inventory.load_inventory(path)
        self.assertEqual(loaded.fingerprint, snapshot.fingerprint)
        self.assertEqual(loaded.records, snapshot.records)

    def test_write_refuses_to_overwrite_an_existing_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            inventory.write_inventory(path, self._snapshot())
            with self.assertRaises(FileExistsError):
                inventory.write_inventory(path, self._snapshot())

    def test_load_rejects_a_fingerprint_that_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            inventory.write_inventory(path, self._snapshot())
            document = json.loads(path.read_text(encoding="utf-8"))
            document["fingerprint"] = "0" * 64
            path.unlink()
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                inventory.load_inventory(path)

    def test_load_rejects_an_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            with self.assertRaisesRegex(ValueError, "Unable to read"):
                inventory.load_inventory(path)


if __name__ == "__main__":
    unittest.main()
