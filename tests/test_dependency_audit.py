"""Contract tests for scripts/dependency_audit.py.

The dependency gate used to audit requirements.txt only: the single runtime pin.
Everything the dev extra installs -- and every transitive package -- stayed
outside the audit while the gate still reported success. These tests pin the
two decisions that make the wider audit usable: what is excluded, and that the
gate actually calls this tool instead of the requirements file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from scripts import dependency_audit

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstalledPinsTests(unittest.TestCase):
    def test_bootstrap_tooling_is_excluded(self) -> None:
        # Failing on the venv's own pip/setuptools would be a gate the project
        # cannot repair; the exclusion is the reason the audit is actionable.
        pins = dependency_audit.installed_pins()
        names = {pin.split("==", 1)[0].lower() for pin in pins}
        for excluded in ("pip", "setuptools", "wheel", "vps-ssh-launcher"):
            self.assertNotIn(excluded, names)

    def test_every_pin_is_a_parseable_requirement(self) -> None:
        pins = dependency_audit.installed_pins()
        self.assertTrue(pins)
        for pin in pins:
            with self.subTest(pin=pin):
                name, _, version = pin.partition("==")
                self.assertTrue(name)
                self.assertTrue(version)
                self.assertNotIn(" ", pin)

    def test_dev_and_transitive_packages_are_in_scope(self) -> None:
        # pytest is a dev extra, not a runtime dependency, so its presence is
        # the proof that the audit widened past requirements.txt.
        names = {
            pin.split("==", 1)[0].lower() for pin in dependency_audit.installed_pins()
        }
        self.assertIn("pytest", names)


class AuditInvocationTests(unittest.TestCase):
    def test_audit_reads_the_snapshot_instead_of_the_requirements_file(self) -> None:
        argv = dependency_audit.audit_argv(Path("installed.txt"))
        self.assertIn("-r", argv)
        self.assertIn("installed.txt", argv)
        self.assertNotIn("requirements.txt", argv)

    def test_gate_runs_this_tool_for_the_dependency_audit(self) -> None:
        text = (REPO_ROOT / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")
        self.assertIn("scripts/dependency_audit.py", text)
        self.assertNotIn('"pip_audit", "-r", "requirements.txt"', text)

    def test_list_mode_prints_pins_without_network(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "dependency_audit.py"),
                "--list",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("==", completed.stdout)

    def test_a_failing_audit_propagates_the_exit_code(self) -> None:
        # The old inline invocation surfaced pip-audit's own exit code; a
        # refactor must not turn a vulnerable environment into a passing gate.
        completed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=1
        )
        with mock.patch.object(
            dependency_audit.subprocess, "run", return_value=completed
        ):
            self.assertEqual(dependency_audit.main([]), 1)

    def test_a_passing_audit_reports_pass(self) -> None:
        passed: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
        with mock.patch.object(dependency_audit.subprocess, "run", return_value=passed):
            self.assertEqual(dependency_audit.main([]), 0)


class EnvironmentTests(unittest.TestCase):
    def test_snapshot_reflects_the_running_interpreter(self) -> None:
        # Guard against reading a different environment (for example a global
        # interpreter) while claiming to audit the gate's own.
        pins = dependency_audit.installed_pins()
        self.assertTrue(any(pin.lower().startswith("pytest==") for pin in pins))
        self.assertTrue(os.path.exists(sys.executable))


if __name__ == "__main__":
    unittest.main()
