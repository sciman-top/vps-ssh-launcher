"""In-memory simulation of the multi-action apply state machine.

A live host is the only place these sequences used to be observable: the
terminal status never reached SQLite, and a busy refusal on the second action
reconciled the first. Both are driven here through the real `run_apply_locked`
path with a fake SSH client, so no socket is opened and no -Apply is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from launcher_fakes import patch_attr
from vps_ssh_launcher.maintenance import adapters, apply as apply_module, workflow
from vps_ssh_launcher.maintenance.config import load_policy, policy_receipt_dir
from vps_ssh_launcher.maintenance.fingerprint import inventory_fingerprint
from vps_ssh_launcher.maintenance.models import (
    InventoryRecord,
    InventorySnapshot,
    MaintenancePolicy,
)
from vps_ssh_launcher.maintenance.planner import build_plan
from vps_ssh_launcher.maintenance.state import list_plans, load_plan, save_plan


def _policy(root: Path) -> MaintenancePolicy:
    quote = chr(34)
    lines = [
        "[settings]",
        "strict_host_key_checking = true",
        "command_timeout = 5",
        f"state_path = {quote}{root.as_posix()}/state.db{quote}",
        f"receipt_dir = {quote}{root.as_posix()}/receipts{quote}",
        "",
        "[profiles.bwg]",
        "enabled = true",
        "",
        "[profiles.bwg.resources]",
        f"docker = {quote}upgrade{quote}",
        f"xray = {quote}upgrade{quote}",
        "",
        "[pins.xray]",
        f"version = {quote}26.3.27{quote}",
        f"sha256 = {quote}{chr(48) * 64}{quote}",
        "",
        "[pins.docker]",
        f"compose_file = {quote}/srv/app/compose.yml{quote}",
        f"compose_sha256 = {quote}{chr(48) * 64}{quote}",
        f"services = [{quote}web{quote}]",
        f"digests = {{ web = {quote}sha256:{chr(49) * 64}{quote} }}",
    ]
    path = root / "maintenance.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_policy(path)


def _inventory() -> InventorySnapshot:
    records = (
        InventoryRecord(
            profile="bwg",
            reachable=True,
            facts={"xray": "present", "xray_version": "26.3.26", "docker": "present"},
        ),
    )
    return InventorySnapshot(
        "2026-11-14T00:00:00+00:00", records, inventory_fingerprint(records)
    )


def _target(root: Path) -> Path:
    path = root / "target.json"
    path.write_text(
        json.dumps(
            {
                "profiles": {
                    "bwg": {
                        "host": "203.0.113.10",
                        "user": "root",
                        "password": "fixture",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return path


class MultiActionApplySimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.policy = _policy(self.root)
        self.snapshot = _inventory()
        save_plan(self.root / "state.db", build_plan(self.policy, self.snapshot))
        self.target = _target(self.root)

    def _run(self, behaviour: Any) -> tuple[dict[str, Any], int]:
        """Run one apply; a structured refusal (None payload) is a test failure."""
        client = mock.Mock()
        with (
            mock.patch.dict("os.environ", {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
            patch_attr(workflow, "collect_inventory", return_value=self.snapshot),
            patch_attr(workflow.connection, "connect_with_retry", return_value=client),
            patch_attr(workflow, "execute_action", side_effect=behaviour),
        ):
            result, code = apply_module.run_apply_locked(
                argparse.Namespace(
                    remote_write=True,
                    unattended=False,
                    target_config=str(self.target),
                    run_integration=True,
                    plan_id=None,
                    profile=None,
                ),
                self.policy,
            )
        assert result is not None, "unexpected structured refusal"
        return result, code

    @staticmethod
    def _busy_on(index: int) -> Any:
        calls = {"n": 0}

        def behaviour(*_args: Any, **_kwargs: Any) -> adapters.AdapterResult:
            current = calls["n"]
            calls["n"] += 1
            if current == index:
                raise adapters.MaintenanceBusy("Host busy; no remote write.")
            return adapters.AdapterResult("verified", f"action {current} verified")

        return behaviour

    def test_a_verified_plan_persists_its_terminal_state_and_receipt(self) -> None:
        result, code = self._run(self._busy_on(-1))
        self.assertEqual((result["outcome"], code), ("verified", 0))
        stored = load_plan(self.root / "state.db")
        self.assertEqual(stored.status, "verified")
        self.assertEqual({a.status for a in stored.actions}, {"verified"})
        entries = list_plans(self.root / "state.db")
        self.assertTrue(entries[0]["receipt_path"], entries)
        self.assertEqual(len(list(policy_receipt_dir(self.policy).glob("*.json"))), 1)

    def test_a_busy_second_action_keeps_the_first_verified_and_retries(self) -> None:
        result, code = self._run(self._busy_on(1))
        self.assertEqual((result["outcome"], code), ("deferred", 0))
        self.assertFalse(result["remote_write"])
        statuses = {
            a.resource: a.status for a in load_plan(self.root / "state.db").actions
        }
        first = sorted(statuses)[0]
        second = sorted(statuses)[1]
        self.assertEqual(statuses[first], "verified", statuses)
        self.assertEqual(statuses[second], "planned", statuses)

        retry, retry_code = self._run(self._busy_on(-1))
        self.assertEqual((retry["outcome"], retry_code), ("verified", 0))
        final = load_plan(self.root / "state.db")
        self.assertEqual(final.status, "verified")
        self.assertEqual({a.status for a in final.actions}, {"verified"})


if __name__ == "__main__":
    unittest.main()
