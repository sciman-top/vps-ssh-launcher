"""Failure-path regressions for unattended maintenance; no real host access."""

import datetime as dt
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import ScriptValidationMixin
from vps_ssh_launcher.maintenance.docker import build_docker_upgrade_command


ROOT = Path(__file__).resolve().parents[1]


class MaintenanceReliabilityTests(ScriptValidationMixin, unittest.TestCase):
    def run_shell(self, payload: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash unavailable")
        return subprocess.run(
            self._bash_command(bash, "-c", payload),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_kernel_interrupt_restores_once_and_reports_failure(self) -> None:
        source = (ROOT / "scripts/vasma_kernel_update_cron.ps1").read_text()
        for name, core, verify in (
            ("write_xray_wrapper", "xray", "verify_current_xray"),
            ("write_singbox_wrapper", "sing-box", "verify_current_singbox"),
        ):
            wrapper = self._render_embedded_wrapper(source, name)
            status = wrapper[wrapper.index("status_on_exit() {") :].split(
                "write_status running 0", 1
            )[0]
            recovery = wrapper[wrapper.index("restore_binary() {") :].split(
                'exec 9>"$LOCK_FILE"', 1
            )[0]
            for signal, code in (("INT", 130), ("TERM", 143)):
                with (
                    self.subTest(core=core, signal=signal),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    directory = Path(tmp)
                    (directory / core).write_text("old")
                    (directory / "conf").mkdir()
                    payload = f"""set -Eeuo pipefail
UPDATE_STARTED=1
BACKUP_DIR=.
XRAY_BINARY=./live
SINGBOX_BINARY=./live
SINGBOX_CONF_DIR=./live-conf
ROUTE_CHANGED=0
CONFIG_CHANGED=0
ROUTE_BACKUP_DIR=''
LOG=/dev/null
write_status() {{ echo STATUS_RESULT=$1 EXIT_CODE=$2; }}
log() {{ echo "$*"; }}
service_restart() {{ echo RESTART; }}
service_is_active() {{ return 0; }}
{verify}() {{ return 0; }}
{status}
{recovery}
true
kill -{signal} $$
echo UNREACHABLE
"""
                    result = self.run_shell(payload, directory)
                    self.assertEqual(
                        result.returncode, code, result.stdout + result.stderr
                    )
                    self.assertEqual(result.stdout.count("ROLLBACK_VERIFIED"), 1)
                    self.assertIn(
                        f"STATUS_RESULT=failed EXIT_CODE={code}", result.stdout
                    )
                    self.assertNotIn("UNREACHABLE", result.stdout)
                    self.assertEqual((directory / "live").read_text(), "old")

    def test_kernel_restore_stages_copy_before_replacing_destination(self) -> None:
        source = (ROOT / "scripts/vasma_kernel_update_cron.ps1").read_text()
        for name in ("write_xray_wrapper", "write_singbox_wrapper"):
            wrapper = self._render_embedded_wrapper(source, name)
            helper = wrapper[wrapper.index("restore_binary() {") :].split(
                "recover_on_error() {", 1
            )[0]
            with self.subTest(wrapper=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "old").write_text("original")
                (root / "live").write_text("running-new-binary")
                payload = f"""set -Eeuo pipefail
cp() {{
  if [ "${{@: -1}}" = ./live ]; then echo ETXTBSY >&2; return 1; fi
  command cp "$@"
}}
{helper}
restore_binary ./old ./live
"""
                result = self.run_shell(payload, root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((root / "live").read_text(), "original")
                self.assertEqual(list(root.glob("*.rollback.*")), [])

    def test_pending_acceptance_blocks_even_a_newer_candidate(self) -> None:
        source = (ROOT / "scripts/remote/cpa-auto-update.sh").read_text()
        block = source[
            source.index('if [[ "$MODE" == --apply && -f "$PENDING_FILE" ]]; then') :
        ].split("MIN_FREE_KIB=", 1)[0]
        for target in ("v8.0.29", "v8.0.30"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "pending").write_text("version=v8.0.29\n")
                result = self.run_shell(
                    f"""set -Eeuo pipefail
MODE=--apply
CUR=v8.0.29
TARGET={target}
PENDING_FILE=./pending
log() {{ echo "$*"; }}
health() {{ echo UNEXPECTED_GENERATION; }}
{block}
echo UNEXPECTED_BACKUP
""",
                    root,
                )
                self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
                self.assertNotIn("UNEXPECTED", result.stdout)
                self.assertTrue((root / "pending").exists())

    def test_restart_refuses_shared_lock_before_any_probe(self) -> None:
        source = (ROOT / "scripts/cpa_recovery_workflow.ps1").read_text()
        block = source.split("$restartRemote = @'\n", 1)[1].split("\n'@", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            block = block.replace("/run/vps-ssh-launcher-maintenance.lock", "./lock")
            result = self.run_shell(
                "flock() { return 1; }\npython3() { echo UNEXPECTED_PROBE; }\n"
                "docker() { echo UNEXPECTED_RESTART; }\n" + block,
                Path(tmp),
            )
            self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
            self.assertNotIn("UNEXPECTED", result.stdout)

    def test_restart_checks_every_lane(self) -> None:
        source = (ROOT / "scripts/cpa_recovery_workflow.ps1").read_text()
        probe = (
            source.split("$restartRemote = @'\n", 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY", 1)[0]
        )
        for busy in (False, True):
            idle = {"inflight": 0, "pending": 0, "half_open_probe": False}
            health = {
                "lanes": {
                    "chatgpt-oauth": {"state": idle},
                    "other": {"state": {**idle, "inflight": int(busy)}},
                }
            }
            fixture = (
                "import io, urllib.request\nurllib.request.build_opener = lambda *a: type('Opener', (), {'open': lambda *a, **k: io.StringIO("
                + repr(json.dumps(health))
                + ")})()\n"
            )
            with self.subTest(busy=busy):
                result = subprocess.run(
                    [shutil.which("python") or "python", "-c", fixture + probe],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    result.returncode, 14 if busy else 0, result.stdout + result.stderr
                )

    def test_tls_errors_do_not_become_success_and_skip_is_normal(self) -> None:
        source = (ROOT / "scripts/v2ray_agent_renewtls_cron.ps1").read_text()
        wrapper = source.split("$wrapperText = @'\n", 1)[1].split("\n'@", 1)[0]
        body = wrapper[wrapper.index("ACME_HOME=") :]
        a = body.index("verify_loaded_certificate() {")
        b = body.index("# Retry a previously failed reload", a)
        body = body[:a] + 'verify_loaded_certificate() { [ "$1" != 0 ]; }\n' + body[b:]
        body = body.replace('ACME_HOME="/root/.acme.sh"', "ACME_HOME=.")
        body = body.replace(
            'GATEWAY_CERT="/etc/v2ray-agent/tls/fq.sciman.top.crt"',
            "GATEWAY_CERT=./cert",
        )
        body = body.replace(
            "/bin/bash /etc/v2ray-agent/install.sh RenewTLS", "legacy_renew"
        )
        for renew, reload, expected in ((1, 0, 1), (2, 0, 0), (0, 9, 9), (None, 0, 4)):
            with (
                self.subTest(renew=renew, reload=reload),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                (root / "cert").touch()
                if renew is not None:
                    (root / "acme.sh").write_text(f"#!/bin/bash\nexit {renew}\n")
                result = self.run_shell(
                    f"""set -Eeuo pipefail
LOG_FILE=./log
if [ -f ./acme.sh ]; then chmod +x ./acme.sh; fi
openssl() {{ return 0; }}
legacy_renew() {{ echo LEGACY >> calls; }}
nginx() {{ if [ "$1" = -s ]; then return {reload}; fi; }}
{body}
echo SUCCESS
""",
                    root,
                )
                self.assertEqual(
                    result.returncode, expected, result.stdout + result.stderr
                )
                self.assertEqual("SUCCESS" in result.stdout, expected == 0)
                if renew in (1, None):
                    self.assertFalse((root / "calls").exists())

    def test_docker_waits_for_health_for_upgrade_and_rollback(self) -> None:
        command = build_docker_upgrade_command(
            compose_file="/srv/app/compose.yml",
            compose_sha256="a" * 64,
            services=("app",),
            digests={"app": "sha256:" + "b" * 64},
        )
        self.assertEqual(command.count("--wait --wait-timeout 120"), 2)

    def test_bridge_cursor_survives_reload_and_task_has_no_runtime_limit(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell unavailable")
        source = (ROOT / "scripts/cpa_reset_event_bridge.ps1").read_text()
        function = source[
            source.index("function Load-State {") : source.index(
                "function Get-ActiveLogs {"
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "schema": 1,
                "last_event_id": "local",
                "last_event_time": "2026-10-15T00:00:00Z",
                "last_event_log": "local.log",
                "last_management_event_id": "management",
                "last_management_event_time": "2026-10-15T01:00:00Z",
                "last_management_event_log": "remote:cli-proxy-api",
            }
            (root / "state.json").write_text(json.dumps(state))
            script = root / "probe.ps1"
            script.write_text(
                "$ErrorActionPreference='Stop'\n$StatePath=Join-Path $PSScriptRoot state.json\nfunction Write-BridgeLog { throw 'invalid state' }\n"
                + function
                + "\nLoad-State | ConvertTo-Json -Compress\n"
            )
            result = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(script)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            restored = json.loads(result.stdout)
            self.assertEqual(restored["last_management_event_id"], "management")
            self.assertEqual(
                dt.datetime.fromisoformat(restored["last_management_event_time"]),
                dt.datetime.fromisoformat(str(state["last_management_event_time"])),
            )
        installer = (ROOT / "scripts/install_cpa_reset_event_bridge.ps1").read_text()
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", installer)
        self.assertIn("-RestartCount 3", installer)
        self.assertNotIn(
            "Unregister-ScheduledTask", installer[installer.index("TASK_BACKUP=") :]
        )

    def test_daily_health_failure_reaches_task_exit_and_existing_log(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell unavailable")
        source = (ROOT / "scripts/vps_maintenance.ps1").read_text()
        body = source.split("    if ($VerifyHealth) {\n", 1)[1].split("\n    }\n", 1)[0]
        for code in (0, 5):
            with self.subTest(doctor_exit=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                script = root / "health.ps1"
                script.write_text(
                    "$ErrorActionPreference='Stop'\n$Profile='bwg'\n$targetPath='unused'\n"
                    "$logPath=Join-Path $PSScriptRoot run.log\n"
                    f"function pwsh {{ $global:LASTEXITCODE={code}; 'DOCTOR_RESULT={code}' }}\n"
                    + body,
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [pwsh, "-NoProfile", "-File", str(script)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0 if code == 0 else 1,
                    result.stdout + result.stderr,
                )
                self.assertIn(
                    f"DOCTOR_RESULT={code}",
                    (root / "run.log").read_text(encoding="utf-8"),
                )
