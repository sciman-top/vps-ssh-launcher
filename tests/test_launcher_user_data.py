"""Behaviour contract for the shared launcher user-data path rule.

Every wrapper derives its target config, maintenance policy, known_hosts and
run logs from one platform root. The rule was previously restated in
``run_gates.ps1`` with a ``%APPDATA%\\vps-ssh-launcher\\<platform>\\`` layout the
launcher never writes, which made an implicit integration config silently fall
through to the repository file. These tests drive the real PowerShell helpers
and compare them with the Python resolver they must mirror.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from vps_ssh_launcher import config as target_config

from launcher_fakes import patch_env

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = REPO_ROOT / "scripts" / "lib" / "project_environment.ps1"
# Last gate generation that still resolved the %APPDATA%\vps-ssh-launcher\<platform>
# layout; the control test below reads it from history rather than from HEAD.
RETIRED_GATE_REV = "785c750"


def _powershell() -> str:
    found = shutil.which("pwsh")
    if found is None:
        raise unittest.SkipTest("PowerShell 7 is not available")
    return found


class UserDataRootTests(unittest.TestCase):
    def _run(self, body: str, *, env: dict[str, str]) -> str:
        command = f". '{LIBRARY}'; " + body
        completed = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", command],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"{(completed.stdout or '')}{(completed.stderr or '')}",
        )
        return completed.stdout.strip()

    def test_roaming_root_matches_the_python_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as appdata:
            env = os.environ.copy()
            env["APPDATA"] = appdata
            env.pop("HOME", None)
            root = self._run("Get-LauncherUserDataRoot", env=env)
            self.assertEqual(Path(root), Path(appdata) / "vps-ssh-launcher")

    def test_local_scope_stays_out_of_the_roaming_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as appdata,
            tempfile.TemporaryDirectory() as local,
        ):
            env = os.environ.copy()
            env["APPDATA"] = appdata
            env["LOCALAPPDATA"] = local
            roaming = self._run("Get-LauncherUserDataRoot", env=env)
            local_root = self._run("Get-LauncherUserDataRoot -Scope Local", env=env)
            self.assertNotEqual(Path(roaming), Path(local_root))
            self.assertEqual(Path(roaming).parent, Path(appdata))
            self.assertEqual(Path(local_root).parent, Path(local))

    def test_named_leaf_resolves_under_the_same_root(self) -> None:
        with tempfile.TemporaryDirectory() as appdata:
            env = os.environ.copy()
            env["APPDATA"] = appdata
            path = self._run(
                'Resolve-LauncherUserDataPath -Leaf "maintenance.toml"', env=env
            )
            self.assertEqual(
                Path(path), Path(appdata) / "vps-ssh-launcher" / "maintenance.toml"
            )

    def test_config_resolution_prefers_the_user_file_over_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            user_dir = root_path / "roaming" / "vps-ssh-launcher"
            user_dir.mkdir(parents=True)
            (user_dir / "target.json").write_text("{}\n", encoding="utf-8")
            repo_dir = root_path / "repo"
            repo_dir.mkdir()
            (repo_dir / "target.json").write_text("{}\n", encoding="utf-8")
            env = os.environ.copy()
            env["APPDATA"] = str(root_path / "roaming")
            resolved = self._run(
                f"Resolve-LauncherConfigPath -ProjectRoot '{repo_dir}'", env=env
            )
            self.assertEqual(Path(resolved), user_dir / "target.json")

    def test_config_resolution_falls_back_to_the_repository_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            repo_dir = root_path / "repo"
            repo_dir.mkdir()
            repo_file = repo_dir / "target.json"
            repo_file.write_text("{}\n", encoding="utf-8")
            env = os.environ.copy()
            env["APPDATA"] = str(root_path / "empty-roaming")
            resolved = self._run(
                f"Resolve-LauncherConfigPath -ProjectRoot '{repo_dir}'", env=env
            )
            self.assertEqual(Path(resolved), repo_file)


class GateConfigResolutionTests(unittest.TestCase):
    """The gate must resolve its integration config exactly like the launcher."""

    def test_gate_delegates_to_the_shared_resolver(self) -> None:
        # .gitattributes checks *.ps1 out as CRLF, so a multi-line pattern must
        # normalize first; unnormalized, the negative assertion can never match
        # and the guard silently passes on Windows.
        text = (
            (REPO_ROOT / "scripts" / "run_gates.ps1")
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
        self.assertIn("Resolve-LauncherConfigPath", text)
        # The platform-suffixed layout this gate used to invent is never
        # written by the launcher; its return would silently skip the
        # non-interactive profile guard.
        self.assertNotIn('"vps-ssh-launcher",\n        $platform', text)

    def test_gate_resolver_assertion_rejects_the_retired_layout(self) -> None:
        # Control for the negative assertion above: the pattern must match the
        # text the gate actually carried, otherwise "not in" proves nothing.
        # Pin the revision instead of reading HEAD -- HEAD moves past the fix and
        # the control then asserts against text that no longer contains it,
        # which is exactly how this control first rotted.
        retired = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "show",
                f"{RETIRED_GATE_REV}:scripts/run_gates.ps1",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if retired.returncode != 0:
            self.skipTest(f"git cannot read {RETIRED_GATE_REV}; shallow checkout?")
        normalized = retired.stdout.replace("\r\n", "\n")
        self.assertIn('"vps-ssh-launcher",\n        $platform', normalized)

    def test_gate_no_longer_carries_the_platform_suffixed_layout(self) -> None:
        # Same rule expressed behaviourally: the retired body above must not
        # come back, no matter how the surrounding code is reformatted.
        text = (REPO_ROOT / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n")
        self.assertNotIn("$env:APPDATA,", normalized)
        self.assertNotIn('[Environment]::GetFolderPath("UserProfile")', normalized)

    def test_gate_resolves_the_same_file_the_launcher_does(self) -> None:
        _powershell()
        with tempfile.TemporaryDirectory() as appdata:
            user_dir = Path(appdata) / "vps-ssh-launcher"
            user_dir.mkdir(parents=True)
            (user_dir / "target.json").write_text("{}\n", encoding="utf-8")
            env = os.environ.copy()
            env["APPDATA"] = appdata
            command = (
                f". '{LIBRARY}'; "
                f"$c = Resolve-LauncherConfigPath -ProjectRoot '{REPO_ROOT}' -Config ''; "
                "$c"
            )
            completed = subprocess.run(
                [_powershell(), "-NoProfile", "-Command", command],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            gate_path = Path(completed.stdout.strip().splitlines()[-1])
        expected = (user_dir / "target.json").as_posix()
        self.assertEqual(gate_path.as_posix(), expected)
        # Compare against the Python resolver for the same environment instead
        # of restating "target.json": the file name is fixed, the directory is
        # the thing that can drift.
        with patch_env(os.environ, {"APPDATA": appdata}):
            self.assertEqual(target_config.user_config_path().as_posix(), expected)


if __name__ == "__main__":
    unittest.main()
