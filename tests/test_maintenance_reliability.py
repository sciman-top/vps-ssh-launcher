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
        source = (ROOT / "scripts/vasma_kernel_update_cron.ps1").read_text(
            encoding="utf-8"
        )
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
        source = (ROOT / "scripts/vasma_kernel_update_cron.ps1").read_text(
            encoding="utf-8"
        )
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

    def test_auto_update_rolls_back_on_hup(self) -> None:
        source = (ROOT / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        # A SIGHUP mid-transaction (SSH channel drop during a manual run) must
        # hit the same verified rollback as ERR/INT/TERM; otherwise a half-
        # applied image swap is silently absorbed by the next daily run.
        self.assertIn("trap 'rollback 129' HUP", source)
        self.assertEqual(source.count("trap - ERR INT TERM HUP"), 3)

    def test_auto_update_hup_signal_hits_verified_rollback(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        source = (ROOT / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        rollback = (
            "rollback() {"
            + source.split("rollback() {", 1)[1].split("\n}\ntrap rollback ERR", 1)[0]
            + "\n}"
        )
        trap_block = source.split("\ntrap rollback ERR\n", 1)[1].split("\nIMAGE=", 1)[0]
        traps = "trap rollback ERR\n" + trap_block
        payload = f"""set -Eeuo pipefail
MUTATED=0
{rollback}
{traps}
true
kill -HUP $$
echo UNEXPECTED_CONTINUATION
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_shell(payload, Path(tmp))
        self.assertEqual(result.returncode, 129, result.stdout + result.stderr)
        self.assertNotIn("UNEXPECTED_CONTINUATION", result.stdout)

    def test_pending_acceptance_blocks_even_a_newer_candidate(self) -> None:
        source = (ROOT / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
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
        source = (ROOT / "scripts/cpa_recovery_workflow.ps1").read_text(
            encoding="utf-8"
        )
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
        source = (ROOT / "scripts/cpa_recovery_workflow.ps1").read_text(
            encoding="utf-8"
        )
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
        source = (ROOT / "scripts/v2ray_agent_renewtls_cron.ps1").read_text(
            encoding="utf-8"
        )
        # The template is parameterized per profile; reconstruct the effective
        # bwg wrapper text (common body plus the bwg reload section).
        wrapper = source.split("$commonText = @'\n", 1)[1].split("\n'@", 1)[0]
        wrapper = wrapper.replace(
            "__RELOAD_SECTION__",
            source.split("$bwgReloadSection = @'\n", 1)[1].split("\n'@", 1)[0],
        )
        wrapper = wrapper.replace(
            "__CERT_PATH__", "/etc/v2ray-agent/tls/fq.sciman.top.crt"
        ).replace("__ACME_DOMAIN__", "*.sciman.top")
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
        body = body.replace(
            'state_file="/var/lib/vps-ssh-launcher/maintenance-status/renewtls-applied.sha"',
            "state_file=./status/renewtls-applied.sha",
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
    mkdir -p ./status
if [ -f ./acme.sh ]; then chmod +x ./acme.sh; fi
openssl() {{ return 0; }}
legacy_renew() {{ echo LEGACY >> calls; }}
nginx() {{ if [ "$1" = -s ]; then return {reload}; fi; }}
    systemctl() {{
        case "$1" in
            is-active|restart) return 0 ;;
            show) echo "$$"; return 0 ;;
            *) return 1 ;;
        esac
    }}
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

    def test_zz_reload_section_branch_contract(self) -> None:
        source = (ROOT / "scripts/v2ray_agent_renewtls_cron.ps1").read_text(
            encoding="utf-8"
        )
        section = source.split("$zzReloadSection = @'\n", 1)[1].split("\n'@", 1)[0]
        # The applied-sha state file must make a renewed certificate trigger
        # exactly one nginx reload plus sing-box restart, while a missing
        # baseline on an already-healthy host records state without any
        # restart, a dead sing-box is restarted even without a renewal, and a
        # certificate file newer than the running core takes the reload path
        # instead of being frozen in as a baseline. Half-failure paths must
        # abort loudly and leave the previous state file intact so the next
        # cron run retries the drift resolution.
        scenarios = (
            # (name, state_sha, verify_rc, post_verify_rc, core_active,
            #  core_fresh, nginx_test_rc, expect_restart, expect_ok)
            ("baseline", None, 0, 0, True, True, 0, False, True),
            ("steady", "sha-new", 0, 0, True, True, 0, False, True),
            ("renewal", "sha-old", 0, 0, True, True, 0, True, True),
            ("stale_loaded", "sha-new", 1, 0, True, True, 0, True, True),
            ("core_down", "sha-new", 0, 0, False, True, 0, True, True),
            ("baseline_stale_core", None, 0, 0, True, False, 0, True, True),
            ("empty_state_baselines", "", 0, 0, True, True, 0, False, True),
            ("nginx_test_failure", "sha-old", 0, 0, True, True, 9, False, False),
            (
                "post_restart_verify_failure",
                "sha-old",
                0,
                1,
                True,
                True,
                0,
                True,
                False,
            ),
        )
        for (
            scenario,
            state_sha,
            verify_rc,
            post_verify_rc,
            core_active,
            core_fresh,
            nginx_test_rc,
            expect_restart,
            expect_ok,
        ) in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "cert.pem").write_text("cert", encoding="ascii")
                status_dir = root / "maintenance-status"
                status_dir.mkdir()
                if state_sha is not None:
                    (status_dir / "renewtls-applied.sha").write_text(
                        state_sha + "\n", encoding="ascii"
                    )
                core_is_active = "return 0" if core_active else "return 1"
                # Replace the section's real handshake probe (it would need a
                # live TLS listener) with a stateful stub: the first call
                # returns the scenario's verdict and later calls return the
                # post-reload verdict so restart-path failures stay exercisable
                # under set -e.
                probe_start = section.index("verify_loaded_certificate() {")
                probe_end = section.index("# nginx and sing-box", probe_start)
                scenario_section = (
                    section[:probe_start]
                    + "verify_loaded_certificate() {\n"
                    + "    if [ -f verify_called ]; then return "
                    + str(post_verify_rc)
                    + "; fi\n"
                    + "    touch verify_called\n"
                    + f"    return {verify_rc}\n"
                    + "}\n"
                    + section[probe_end:]
                )
                # The deployed section writes the absolute state path; aim it
                # at the sandbox like the other environment substitutions.
                scenario_section = scenario_section.replace(
                    "/var/lib/vps-ssh-launcher/maintenance-status/renewtls-applied.sha",
                    "./maintenance-status/renewtls-applied.sha",
                )
                core_started = "500" if core_fresh else "100"
                script = f"""set -Eeuo pipefail
LOG_FILE=./log
systemctl() {{
    if [ "$1" = restart ]; then echo restart >> calls; touch restarted; return 0; fi
    if [ "$1" = is-active ]; then
        if [ -f restarted ]; then return 0; fi
        {core_is_active}
    fi
    if [ "$1" = show ]; then echo 4242; return 0; fi
    return 0
}}
stat() {{
    case "$3" in
        /proc/*) echo {core_started} ;;
        *) echo 200 ;;
    esac
}}
nginx() {{
    echo "nginx $1" >> calls
    if [ "$1" = -t ]; then return {nginx_test_rc}; fi
    return 0
}}
sha256sum() {{ echo "sha-new  cert"; }}
GATEWAY_CERT=./cert.pem
{scenario_section}
echo DONE
"""
                result = self.run_shell(script, root)
                if expect_ok:
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertIn("DONE", result.stdout)
                else:
                    self.assertNotEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertNotIn("DONE", result.stdout)
                calls = ""
                calls_path = root / "calls"
                if calls_path.exists():
                    calls = calls_path.read_text(encoding="ascii")
                self.assertEqual("restart" in calls, expect_restart, calls)
                if expect_restart:
                    self.assertIn("nginx -s", calls, calls)
                    self.assertIn("nginx -t", calls, calls)
                state_path = status_dir / "renewtls-applied.sha"
                if expect_ok:
                    applied = state_path.read_text(encoding="ascii").strip()
                    self.assertEqual(applied, "sha-new")
                elif state_sha in (None, ""):
                    self.assertFalse(state_path.exists())
                else:
                    self.assertEqual(
                        state_path.read_text(encoding="ascii").strip(), state_sha
                    )

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
        source = (ROOT / "scripts/cpa_reset_event_bridge.ps1").read_text(
            encoding="utf-8"
        )
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
        installer = (ROOT / "scripts/install_cpa_reset_event_bridge.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", installer)
        self.assertIn("-RestartCount 3", installer)
        self.assertNotIn(
            "Unregister-ScheduledTask", installer[installer.index("TASK_BACKUP=") :]
        )

    def test_daily_health_failure_reaches_task_exit_and_existing_log(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell unavailable")
        source = (ROOT / "scripts/vps_maintenance.ps1").read_text(encoding="utf-8")
        body = source.split(
            '    if ($VerifyHealth -or ($Profile -eq "bwg" -and -not ($Apply -or $AutoApply))) {\n',
            1,
        )[1].split("\n    }\n", 1)[0]
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
                    encoding="utf-8",
                    errors="replace",
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
