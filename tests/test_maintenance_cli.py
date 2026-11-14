"""Exit-code and admission contract for the vps-maint CLI shell.

`maintenance_cli` owns the documented exit buckets and the integration opt-in.
Those branches were only reached by a real SSH run, so they are driven here
with the collector and policy loader stubbed at the module boundary.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from launcher_fakes import patch_attr
from vps_ssh_launcher import maintenance_cli
from vps_ssh_launcher.maintenance.models import InventoryRecord, InventorySnapshot

POLICY = """
[settings]
strict_host_key_checking = true
command_timeout = 10

[profiles.bwg]
enabled = true

[profiles.bwg.resources]
cpa = "deferred"
"""


def _snapshot(*, reachable: bool) -> InventorySnapshot:
    record = InventoryRecord(
        profile="bwg", reachable=reachable, facts={"hostname": "b"}
    )
    return InventorySnapshot("2026-11-14T00:00:00+00:00", (record,), "fingerprint")


class CliFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        root = Path(self._directory.name)
        self.policy_path = root / "maintenance.toml"
        self.policy_path.write_text(POLICY, encoding="utf-8")
        self.target_path = root / "target.json"
        self.target_path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "bwg": {
                            "host": "203.0.113.10",
                            "user": "root",
                            "password": "x",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        if __import__("os").name != "nt":
            self.target_path.chmod(0o600)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        # The opt-in is two-part by design: the flag and the environment. Every
        # run below sets the variable so only the flag is under test.
        with (
            patch_attr(sys, "argv", ["vps-maint", *argv]),
            mock.patch.dict("os.environ", {"VPS_SSH_LAUNCHER_RUN_INTEGRATION": "1"}),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            try:
                code = maintenance_cli.main()
            except SystemExit as exc:  # argparse errors
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()


class InventoryAdmissionTests(CliFixture):
    def test_inventory_without_the_opt_in_is_a_local_error(self) -> None:
        code, _out, err = self._run(
            ["--config", str(self.policy_path), "inventory", "--profile", "bwg"]
        )
        self.assertEqual(code, maintenance_cli.EXIT_LOCAL_ERROR)
        self.assertIn("run-integration", err)

    def test_inventory_reports_a_reachable_probe_as_ok(self) -> None:
        with mock.patch.object(
            maintenance_cli, "collect_inventory", return_value=_snapshot(reachable=True)
        ):
            code, out, _err = self._run(
                [
                    "--json",
                    "--config",
                    str(self.policy_path),
                    "inventory",
                    "--profile",
                    "bwg",
                    "--target-config",
                    str(self.target_path),
                    "--run-integration",
                ]
            )
        self.assertEqual(code, maintenance_cli.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["kind"], "inventory")
        self.assertEqual(payload["profiles"][0]["profile"], "bwg")

    def test_inventory_reports_an_unreachable_probe_as_not_ok(self) -> None:
        with mock.patch.object(
            maintenance_cli,
            "collect_inventory",
            return_value=_snapshot(reachable=False),
        ):
            code, _out, _err = self._run(
                [
                    "--config",
                    str(self.policy_path),
                    "inventory",
                    "--profile",
                    "bwg",
                    "--target-config",
                    str(self.target_path),
                    "--run-integration",
                ]
            )
        self.assertEqual(code, maintenance_cli.EXIT_NOT_OK)

    def test_a_profile_outside_the_policy_is_refused(self) -> None:
        code, _out, err = self._run(
            [
                "--config",
                str(self.policy_path),
                "inventory",
                "--profile",
                "zz",
                "--target-config",
                str(self.target_path),
                "--run-integration",
            ]
        )
        self.assertEqual(code, maintenance_cli.EXIT_LOCAL_ERROR)
        self.assertIn("not configured in maintenance policy", err)


class RenderTests(unittest.TestCase):
    def test_a_mapping_prints_one_line_per_key(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            maintenance_cli._print({"a": 1, "b": 2}, as_json=False)
        self.assertEqual(stdout.getvalue().splitlines(), ["a: 1", "b: 2"])

    def test_a_scalar_prints_verbatim(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            maintenance_cli._print("done", as_json=False)
        self.assertEqual(stdout.getvalue().strip(), "done")

    def test_json_mode_is_sorted_and_ascii_safe(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            maintenance_cli._print({"b": 1, "a": "中"}, as_json=True)
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered, {"a": "中", "b": 1})
        escaped = chr(92) + "u4e2d"
        self.assertIn(escaped, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
