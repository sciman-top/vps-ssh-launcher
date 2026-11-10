"""test_maintenance_cron_family.py - split from test_scripts.py (domain: cron_family)."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import (
    ScriptValidationMixin,
)


class MaintenanceCronFamilyTests(ScriptValidationMixin, unittest.TestCase):
    def test_cron_projection_signals_always_rollback_and_fail(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        for name, handler in (
            ("system_maintenance_cron.ps1", "rollback_apply"),
            ("vasma_kernel_update_cron.ps1", "rollback_apply"),
            ("v2ray_agent_script_update_cron.ps1", "rollback_on_exit"),
            ("v2ray_agent_renewtls_cron.ps1", "rollback_on_exit"),
        ):
            source = (
                (Path(__file__).parents[1] / "scripts" / name)
                .read_text(encoding="utf-8")
                .replace("`", "")
            )
            function = (
                handler
                + "() {"
                + source.split(handler + "() {", 1)[1].split("\n}\n", 1)[0]
                + "\n}\n"
            )
            traps = re.search(
                rf"(?m)^\s*trap {handler} [^\n]+(?:\n\s*trap [^\n]+)*", source
            )
            self.assertIsNotNone(traps)
            assert traps is not None
            for signal, code in (("INT", 130), ("TERM", 143), ("HUP", 129)):
                with self.subTest(script=name, signal=signal):
                    payload = f"""set -Eeuo pipefail
rollback() {{ echo ROLLBACK_CALLED; }}
restore_apply_state() {{ echo ROLLBACK_CALLED; }}
{function}
{traps.group()}
kill -{signal} $$
echo UNEXPECTED_CONTINUATION
"""
                    result = subprocess.run(
                        self._bash_command(bash, "-c", payload),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode, code, result.stdout + result.stderr
                    )
                    self.assertEqual(result.stdout.count("ROLLBACK_CALLED"), 1)
                    self.assertNotIn("UNEXPECTED_CONTINUATION", result.stdout)

    def test_script_updater_signals_record_failure_and_nonzero_exit(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/v2ray-agent-script-update.sh"
        ).read_text(encoding="utf-8")
        handlers = (
            "cleanup() {" + source.split("cleanup() {", 1)[1].split("usage() {", 1)[0]
        )
        for signal, code in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=signal):
                payload = f"""set -Eeuo pipefail
CANDIDATE=''
REPLACED=0
BACKUP_DIR=''
log() {{ echo "$*"; }}
write_status() {{ echo "STATUS=$1/$2"; }}
{handlers}
kill -{signal} $$
echo UNEXPECTED_CONTINUATION
"""
                result = subprocess.run(
                    self._bash_command(bash, "-c", payload),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(f"STATUS=failed/{code}", result.stdout)
                self.assertNotIn("UNEXPECTED_CONTINUATION", result.stdout)

    def test_cockpit_gate_wait_cap_check_is_self_contained(self) -> None:
        # Promoted from a dated one-off in outputs/: the maintained tool must
        # discover the install-specific provider-gateway config dir by glob
        # (no hardcoded hash dir), run fully isolated on scratch ports, and
        # print the same PASS marker the runbooks judge by. It complements
        # cockpit_sidecar_guardrails.ps1 -Mode Verify (state) with behaviour
        # proof after projecting a freshly built binary.
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "cockpit_gate_wait_cap_check.py").read_text(
            encoding="utf-8"
        )
        for token in (
            'GATEWAY_ROOT.glob("*/config.json")',
            "DEFAULT_SCRATCH_PORT = 19109",
            "DEFAULT_STUB_PORT = 19110",
            "ACCEPTANCE_PASS",
            "value not printed",
            "--expect-cap-s",
            "--control",
        ):
            self.assertIn(token, text)
        self.assertNotIn("36218dcc", text, "must not hardcode the install hash dir")
        self.assertNotIn("gate-r3-20261002.exe", text)

    def test_google_ipv4_routing_script_is_opt_in_for_apply(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")

        # Default mode is read-only diagnosis; every remote write must go
        # through the -Apply gate and the shared apply-safety guard.
        self.assertIn("[switch]$Apply", text)
        self.assertIn("Assert-SafeRemoteApplyScript", text)
        self.assertIn("[string]$RemoteApplySha256", text)
        self.assertIn("-Apply requires a 64-hex -RemoteApplySha256", text)
        self.assertIn("REMOTE_APPLY_SCRIPT_HASH_MISMATCH", text)
        self.assertIn("POST_APPLY_VERIFICATION_FAILED", text)
        self.assertIn("restore_known_state", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("ROLLBACK_FAILED", text)
        self.assertIn("[switch]$PinClaudeWebIPv4", text)
        self.assertIn("[string]$GeneratorSha256", text)
        self.assertIn("/etc/v2ray-agent/apply-google-ipv4-routing-config.sh", text)
        self.assertIn("domain:platform.claude.com", text)
        self.assertIn("domain:claude.com", text)

        apply_command = text.split('$applyCommand = @"', 1)[1].split('"@', 1)[0]
        self.assertIn('"`$generator_script"', apply_command)
        self.assertIn("set -o pipefail", apply_command)
        restore_body = apply_command.split("restore_known_state() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertLess(
            restore_body.index("generator_backup="),
            restore_body.index("systemctl restart xray"),
            "restore the generator before restarting Xray, whose ExecStartPre replays it",
        )
        self.assertIn("GENERATOR_BACKUP_SHA256=", apply_command)
        self.assertIn("GENERATOR_BACKUP_SHA256_MISMATCH", apply_command)
        self.assertIn(
            "GENERATOR_ROUTE_CONTRACT=${expectedDomainCount}_DOMAINS", apply_command
        )
        self.assertIn("GENERATOR_APPLIED_SHA256=", apply_command)
        self.assertIn("GENERATOR_POST_REPLACE_SHA256_MISMATCH", apply_command)
        self.assertIn("if ! python3 - \"`$generator_stage\" <<'PY'", apply_command)
        self.assertIn("if ! python3 - <<'PY'", apply_command)
        self.assertIn("GENERATOR_STAGE_DISCARDED no_live_change", apply_command)
        self.assertIn(
            "POST_APPLY_ROUTE_CONTRACT=GOOGLE_AND_CLAUDE_IPV4_ONLY_DOMAINS",
            apply_command,
        )

        check_command = text.split("$checkCommand = @'", 1)[1].split("'@", 1)[0]
        # Cross-prove the count instead of trusting one literal: both remote
        # payloads receive the shared per-profile list from
        # scripts/lib/proxy_google_ipv4_domains.ps1 as a pre-rendered literal
        # (exactly one token each), and the generator stage guard counts with
        # the same dynamic value, so a future add/drop cannot fork the two
        # cores into an inscrutable remote count mismatch.
        self.assertEqual(check_command.count("__EXPECTED_DOMAIN_LIST__"), 1)
        self.assertEqual(apply_command.count("__EXPECTED_DOMAIN_LIST__"), 1)
        self.assertIn("$expectedDomainListBlock", text)
        self.assertIn("-ne $expectedDomainCount", apply_command)
        # Regression guard: single-quoted here-strings pass backticks to bash
        # verbatim, which once broke the read-only check silently.
        self.assertNotIn(
            "`",
            check_command,
            "single-quoted here-strings pass backticks to bash verbatim; "
            "escape $ only inside double-quoted here-strings",
        )

    def test_google_ipv4_route_contract_accepts_legacy_and_sockopt_strategy(
        self,
    ) -> None:
        # Xray 26 deprecates freedom `settings.domainStrategy` and reads the
        # same value from `streamSettings.sockopt.domainStrategy`. vasma still
        # emits the legacy shape today, but a strict legacy-only assertion would
        # turn a future migration into a false apply failure + rollback. Both
        # embedded probes (the read-only check and the post-apply gate) must
        # accept either equivalent shape while still rejecting a wrong
        # strategy, so a silent drop back to AS_IS cannot slip through.
        import json
        import subprocess
        import sys

        text = (
            Path(__file__).resolve().parents[1] / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")
        blocks = re.findall(r"python3 - <<'PY'\r?\n(.*?)\r?\nPY", text, re.DOTALL)
        self.assertEqual(len(blocks), 2, "expected the check and post-apply probes")

        route = {"rules": [{"outboundTag": "google_ipv4_out", "domain": ["domain:x"]}]}
        legacy = {
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "google_ipv4_out",
                    "settings": {"domainStrategy": "ForceIPv4"},
                }
            ]
        }
        sockopt = {
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "google_ipv4_out",
                    "settings": {},
                    "streamSettings": {"sockopt": {"domainStrategy": "ForceIPv4"}},
                }
            ]
        }
        wrong = {
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "google_ipv4_out",
                    "settings": {"domainStrategy": "UseIP"},
                }
            ]
        }

        for block in blocks:
            code = block.replace("__EXPECTED_DOMAIN_LIST__", '"domain:x",')
            with tempfile.TemporaryDirectory() as tmp:
                conf = Path(tmp)
                # The embedded probe reads the deployed absolute conf paths;
                # redirect them at the temp fixtures so the predicate is
                # exercised without touching a real /etc tree. Use posix-style
                # separators so Windows backslashes are not read as Python
                # string escapes inside the embedded literals.
                routing_path = (conf / "09_routing.json").as_posix()
                outbound_path = (conf / "98_google_ipv4_outbound.json").as_posix()
                code = code.replace(
                    "/etc/v2ray-agent/xray/conf/09_routing.json",
                    routing_path,
                ).replace(
                    "/etc/v2ray-agent/xray/conf/98_google_ipv4_outbound.json",
                    outbound_path,
                )
                (conf / "09_routing.json").write_text(
                    json.dumps(route), encoding="utf-8"
                )
                script = conf / "probe.py"
                script.write_text(code, encoding="utf-8")

                def run(directory: Path, probe: Path, outbound: dict) -> int:
                    (directory / "98_google_ipv4_outbound.json").write_text(
                        json.dumps(outbound), encoding="utf-8"
                    )
                    return subprocess.run(
                        [sys.executable, str(probe)],
                        cwd=str(directory),
                        capture_output=True,
                    ).returncode

                self.assertEqual(
                    run(conf, script, legacy), 0, "legacy settings shape must pass"
                )
                self.assertEqual(
                    run(conf, script, sockopt),
                    0,
                    "migrated streamSettings.sockopt shape must pass",
                )
                self.assertNotEqual(
                    run(conf, script, wrong),
                    0,
                    "a non-ForceIPv4 strategy must fail the contract",
                )

    def test_vps_maintenance_wrapper_is_fresh_and_dry_run_by_default(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "scripts" / "vps_maintenance.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Fresh maintenance runs require -RunIntegration", text)
        self.assertIn("-live-inventory", text)
        self.assertIn("-run-integration", text)
        self.assertIn("-Apply requires -RemoteWrite", text)
        self.assertIn("-AutoApply", text)
        self.assertIn('"--unattended"', text)
        self.assertIn('"--inventory-file", $reviewInventoryPath', text)
        self.assertIn("VPS_SSH_LAUNCHER_RUN_INTEGRATION", text)
        self.assertNotIn("Register-ScheduledTask", text)

    def test_maintenance_wrapper_runs_the_proxy_lane_checks(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "scripts" / "vps_maintenance.ps1"
        ).read_text(encoding="utf-8")
        # A vasma reinstall reverts the hardening lanes without breaking any
        # service, so the daily observe pass has to run the read-only gate.
        self.assertIn("[switch]$LaneAcceptance", text)
        self.assertIn("function Invoke-ProxyLaneChecks {", text)
        self.assertIn("proxy_hardening_drift.ps1", text)
        self.assertIn("proxy_lane_acceptance.ps1", text)
        self.assertIn("-LaneAcceptance is restricted to bwg.", text)
        self.assertIn('"$Profile-hysteria2-obfs-password-v2.txt"', text)
        self.assertIn('"$Profile-hysteria2-obfs-password.txt"', text)
        self.assertIn('"-ObfsPasswordFile", $obfsPasswordFile', text)
        self.assertIn(
            '-WithAcceptance:($LaneAcceptance -or ($Profile -eq "bwg" -and -not ($Apply -or $AutoApply)))',
            text,
        )

    def test_maintenance_wrapper_applies_the_plan_it_just_generated(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is unavailable")
        source = (Path(__file__).parents[1] / "scripts/vps_maintenance.ps1").read_text(
            encoding="utf-8"
        )
        start = source.index("function Invoke-VpsMaintenanceCli {")
        end = source.index("if ($Apply -and $AutoApply)", start)
        source = (
            source[:start]
            + """
function Invoke-VpsMaintenanceCli {
  param($Python, [string[]]$Arguments, $LogPath)
  if ($Arguments -contains 'plan') {
    $outputIndex = [array]::IndexOf($Arguments, '--output')
    if ($outputIndex -ge 0) {
      $destination = $Arguments[$outputIndex + 1]
      $inventory = $destination -replace '\\.json$', '-inventory.json'
      '{"plan_id":"plan-0123456789abcdef","inventory_fingerprint":"sha256:fixture"}' |
        Set-Content -LiteralPath $destination
      '{"created_at":"fixture","records":[],"fingerprint":"sha256:fixture"}' |
        Set-Content -LiteralPath $inventory
    }
  } else {
    $index = [array]::IndexOf($Arguments, '--plan-id')
    if ($index -lt 0 -or $Arguments[$index + 1] -ne 'plan-0123456789abcdef') {
      throw 'Apply did not select the generated plan.'
    }
    Write-Host 'GENERATED_PLAN_SELECTED'
  }
  return 0
}
"""
            + source[end:]
        )
        source = source.replace(
            '. (Join-Path $PSScriptRoot "lib\\project_environment.ps1")',
            "function Initialize-WindowsProcessEnvironment {}\n"
            'function Resolve-ProjectPython { return @{Exe="unused";Args=@()} }',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "wrapper.ps1"
            script.write_text(source, encoding="utf-8")
            policy = root / "policy.toml"
            policy.write_text("", encoding="utf-8")
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    str(script),
                    "-Config",
                    str(policy),
                    "-TargetConfig",
                    str(target),
                    "-OutputDirectory",
                    str(root / "runs"),
                    "-RunIntegration",
                    "-Apply",
                    "-RemoteWrite",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            self.assertIn("GENERATED_PLAN_SELECTED", completed.stdout)

    def test_full_maintenance_refuses_conflicting_target_files_before_ssh(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell 7 is unavailable")
        source = Path(__file__).parents[1] / "scripts/bwg_full_maintenance.ps1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.json", root / "second.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            # The Windows-first wrapper requires APPDATA even when validating
            # local arguments. Supply it explicitly on Linux CI as well.
            env = os.environ.copy()
            env["APPDATA"] = str(root)
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-File",
                    str(source),
                    "-Config",
                    str(first),
                    "-TargetConfig",
                    str(second),
                    "-OutputDirectory",
                    str(root / "runs"),
                    "-RunIntegration",
                ],
                env=env,
                capture_output=True,
                text=True,
                # pwsh renders its own diagnostics (e.g. the ConciseView
                # truncation ellipsis) in the console code page, which is not
                # UTF-8 on Windows. A strict decode would crash the reader
                # thread and leave stdout=None, masking the real output.
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("same target file", completed.stdout + completed.stderr)
            self.assertFalse((root / "runs").exists())

    def test_vps_maintenance_task_is_observe_only(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install_vps_maintenance_task.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("SupportsShouldProcess = $true", text)
        self.assertIn('[string]$At = "20:00"', text)
        self.assertIn("-RunIntegration", text)
        self.assertIn("[switch]$AutoApply", text)
        self.assertIn("-AutoApply", text)
        self.assertIn('"-WindowStyle", "Hidden"', text)
        self.assertIn("-Hidden `", text)
        # S4U keeps the daily run alive with no interactive logon; the two-hour
        # limit avoids killing a remote transaction mid-flight.
        self.assertIn("-LogonType S4U", text)
        self.assertIn("New-TimeSpan -Hours 2", text)
        # S4U needs elevation and Unregister-then-Register can lose the task
        # when Register fails: both must be guarded.
        self.assertIn("requires an elevated pwsh", text)
        self.assertIn(
            "A failed update must not leave the host without its daily task.", text
        )
        self.assertIn("mode=observe-only", text)
        self.assertIn("silent=true", text)
        self.assertNotIn('"-Apply"', text)
        self.assertNotIn('"-RemoteWrite"', text)

    def test_system_maintenance_cron_safety_contracts(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")

        # bwg keeps the no-reboot policy. zz may migrate its reviewed
        # AUTO_REBOOT_ON_MAINT=1 delay=15 exception only through the explicit
        # -PreserveAutoReboot path after the old cron matches exactly.
        self.assertIn("[switch]$PreserveAutoReboot", text)
        self.assertIn("-PreserveAutoReboot is restricted to profile zz", text)
        self.assertIn("automatic reboot disabled", text)
        self.assertIn('shutdown -r "+`$AUTO_REBOOT_DELAY_MIN"', text)
        self.assertIn("AUTO_REBOOT_DELAY_MIN must be an integer", text)
        self.assertNotIn("systemctl reboot", text)
        self.assertNotIn("reboot -f", text)
        self.assertIn(
            'if ! shutdown -r "+`$AUTO_REBOOT_DELAY_MIN" '
            '"System maintenance: reboot required"',
            text,
        )
        self.assertIn(
            "automatic reboot suppressed because maintenance checks failed", text
        )
        # A controlled auto-reboot line may only survive when its reviewed
        # policy is preserved. Every guard must precede the cron write.
        self.assertIn(
            "REFUSED: existing AUTO_REBOOT policy requires zz with -PreserveAutoReboot",
            text,
        )
        self.assertIn(
            "REFUSED: existing AUTO_REBOOT cron is not the reviewed delay=15 policy",
            text,
        )
        self.assertIn(
            'grep -Fq "root AUTO_REBOOT_ON_MAINT=1 '
            'AUTO_REBOOT_DELAY_MIN=15 /bin/bash `$maintenance_script"',
            text,
        )
        self.assertIn(
            "REFUSED: -PreserveAutoReboot requires the reviewed existing zz AUTO_REBOOT cron",
            text,
        )
        reboot_guard_idx = text.index(
            "REFUSED: existing AUTO_REBOOT cron is not the reviewed delay=15 policy"
        )
        cron_write_idx = text.index("printf 'SHELL=/bin/bash")
        self.assertLess(reboot_guard_idx, cron_write_idx)
        self.assertLess(
            text.rfind("if ! verify_proxy_services; then"),
            text.index('if ! shutdown -r "+`$AUTO_REBOOT_DELAY_MIN"'),
        )

        # Unattended apt runs must be non-interactive and must keep local
        # config files instead of blocking the cron job on a conffile prompt.
        self.assertIn("DEBIAN_FRONTEND=noninteractive", text)
        self.assertIn("--force-confdef", text)
        self.assertIn("--force-confold", text)
        # Allow dependency-only package transitions (for example Ubuntu's
        # split linux-firmware packages) without enabling removals as
        # full-upgrade would.
        self.assertIn("apt-get upgrade --with-new-pkgs", text)
        # A kernel installed as linux-image-<abi> does not pull a later ABI
        # through `apt-get upgrade`. Install the distro meta package first so
        # monthly maintenance can stage the latest generic/amd64 kernel under
        # the profile's explicit reboot policy.
        self.assertIn("kernel_meta_package", text)
        self.assertIn("linux-image-generic", text)
        self.assertIn("linux-image-amd64", text)
        self.assertIn('step "apt-get install kernel meta package"', text)
        self.assertIn("KERNEL_REBOOT_REQUIRED=pending running=", text)
        self.assertIn("==kernel-update==", text)
        self.assertIn("kernel_meta_tracking=missing", text)
        self.assertIn("dpkg --audit", text)
        self.assertIn("apt-get check", text)
        self.assertIn("apt-get -s -o Debug::NoLocking=1 upgrade", text)
        self.assertIn("package-state-sha256-before=", text)
        self.assertIn("package-state-sha256-after=", text)
        self.assertIn("FAILURE_SUMMARY=", text)
        self.assertIn("/var/lib/vps-ssh-launcher/maintenance-status", text)
        self.assertIn("monthly-maintenance.status", text)
        self.assertIn("write_status running 0", text)
        self.assertIn("docker-daemon-preflight", text)
        self.assertIn("FAILURE_REASONS", text)
        docker_preflight = text.index('record_failure "docker-daemon-preflight"')
        docker_fail_exit = text.index("FAILURE_SUMMARY=", docker_preflight)
        package_preflight = text.index("dpkg_audit_dirty()")
        self.assertLess(docker_fail_exit, package_preflight)
        preflight_block = text.index(
            "Never start a package mutation", text.index("apt-simulation-pre")
        )
        preflight_exit = text.index("exit 1", preflight_block)
        upgrade_step = text.index('step "apt-get upgrade --with-new-pkgs"')
        self.assertLess(preflight_exit, upgrade_step)
        self.assertGreater(
            text.index("apt-get -s -o Debug::NoLocking=1 autoremove --purge"),
            upgrade_step,
        )

        # The purge phase deletes configuration files too, so its removal set
        # must be simulated and recorded before it runs; the earlier run only
        # previewed the upgrade, and a summary line cannot name a package that
        # unexpectedly disappears. The simulation follows upgrade so it uses
        # the dependency and auto/manual state after that package change.
        self.assertIn("apt-get -s -o Debug::NoLocking=1 autoremove --purge", text)
        self.assertIn("apt-simulation-before-autoremove-start", text)
        self.assertIn("autoremove-sim: ", text)
        # A half-configured dpkg state (interrupted apt) must be recovered once
        # instead of chaining every later apt phase into failure.
        self.assertIn("dpkg --configure -a", text)
        self.assertIn("dpkg-audit-pre=recovered", text)
        # The shared flock only serialises this repo's own entries; it cannot
        # exclude the host's unattended-upgrades timer, so apt must wait for
        # the dpkg lock instead of failing the whole run immediately.
        self.assertIn("DPkg::Lock::Timeout=600", text)
        # A pending reboot is an operator action item, so it must be visible as
        # a greppable marker rather than only a prose warning in a remote log.
        self.assertIn("REBOOT_REQUIRED=pending age_days=", text)
        self.assertIn("REBOOT_REQUIRED=none", text)
        # A failed monthly run must not age silently in a remote log no gate
        # reads: the read-only probe surfaces the wrapper's own markers only.
        self.assertIn("==monthly-log==", text)
        self.assertIn("/var/log/monthly-maintenance.log", text)
        self.assertIn("FAILURE_SUMMARY=|REBOOT_REQUIRED=", text)
        self.assertNotIn("cat /var/log/monthly-maintenance.log", text)

        # Kernel updates share the same lock, so a monthly run must never
        # overlap an in-flight vasma kernel update.
        self.assertIn('LOCK_FILE="/run/vps-ssh-launcher-maintenance.lock"', text)
        self.assertIn("lock_file='/run/vps-ssh-launcher-maintenance.lock'", text)
        self.assertIn('exec 9>"`$lock_file"', text)
        self.assertIn("apply requires root", text)

        # Every apply path must be wrapped in the rollback trap with verified
        # backup/restore state, and cron install may only drop its own line.
        self.assertIn("backup_apply_state", text)
        self.assertIn("restore_apply_state", text)
        self.assertIn("trap rollback_apply ERR", text)
        self.assertIn("trap 'rollback_apply 130' INT", text)
        self.assertIn("trap 'rollback_apply 143' TERM", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("read_crontab_or_empty", text)
        self.assertIn(
            "sed -E '/\\/usr\\/local\\/sbin\\/monthly-maintenance\\.sh/d'", text
        )
        self.assertNotIn("crontab -l 2>/dev/null | grep", text)

        # An apt phase that bounces dockerd must be followed by an explicit
        # container recovery check: snapshot before, re-verify after, one
        # explicit start attempt per straggler, and a recorded failure if
        # anything stays down. The wrapper must never restart the daemon.
        self.assertIn("DOCKER_SNAPSHOT", text)
        self.assertIn("re-verifying docker containers", text)
        self.assertIn("docker-daemon-post-maintenance", text)
        self.assertIn("docker-daemon-recovery", text)
        self.assertIn("attempting explicit start", text)
        self.assertIn("containers still down after recovery attempt", text)
        self.assertNotIn("systemctl restart docker", text)

        # Proxy checks are collected in one pass so one broken service does
        # not hide the state of the remaining services from the maintenance log.
        self.assertIn("local checked=0 failed=0", text)
        self.assertIn('return "`$failed"', text)
        self.assertIn("verify_proxy_services", text)

        # Scheduling must live in /etc/cron.d, not root's crontab: vasma's
        # installCronTLS rewrites `crontab -l` with `sed '/v2ray-agent/d'`,
        # which silently deleted the crontab line on bwg on 2026-09-24.
        self.assertIn("/etc/cron.d/vps-launcher-monthly-maintenance", text)
        self.assertIn("root %s/bin/bash %s", text)

    def test_rendered_maintenance_wrapper_is_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_maintenance_wrapper")
        completed = subprocess.run(
            self._bash_command(bash, "-n"),
            input=wrapper.encode("utf-8"),
            capture_output=True,
            timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            check=False,
        )
        output = (completed.stdout + completed.stderr).decode(
            "utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, output)

    def test_monthly_wrapper_selects_supported_kernel_meta_package(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts" / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_maintenance_wrapper")
        start = wrapper.index("kernel_meta_package() (")
        end = wrapper.index("\n)\n", start) + len("\n)\n")
        function_text = wrapper[start:end]
        for distro, kernel, expected in (
            ("ubuntu", "6.8.0-107-generic", "linux-image-generic"),
            ("debian", "6.1.0-40-amd64", "linux-image-amd64"),
            ("ubuntu", "6.8.0-107-aws", ""),
        ):
            with (
                self.subTest(distro=distro, kernel=kernel),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                os_release = root / "os-release"
                os_release.write_text(f'ID="{distro}"\n', encoding="ascii")
                script = root / "probe.sh"
                script.write_text(
                    "uname() { printf '%s\\n' "
                    + kernel
                    + "; }\n"
                    + function_text.replace("/etc/os-release", os_release.as_posix())
                    + "kernel_meta_package || true\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    self._bash_command(bash, str(script)),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(
                    result.stdout.strip(), expected, result.stdout + result.stderr
                )

    def test_monthly_wrapper_logs_lane_hold_state_after_docker_recovery(self) -> None:
        # A daemon bounce mid-request latches the same fail-closed hold a
        # canary restart does (2026-10-21 case). The wrapper only reports the
        # state: enforcement stays with doctor ==lane-hold== and release is
        # the operator's manual flow, so the scan must not record_failure nor
        # attempt an automated release.
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        scan = source.split("HOLD_SCAN=", 1)[1].split("\nPY\n", 1)[0]
        for lane in (
            "chatgpt-risk.json",
            "workbuddy-risk.json",
            "antigravity-risk.json",
        ):
            self.assertIn(lane, scan)
        self.assertNotIn("record_failure", scan)
        # The inert claude tombstone must never surface as a maintenance WARN.
        self.assertNotIn("claude-risk.json", source)
        self.assertIn("lane-hold-state-after-maintenance", source)

    def test_partial_index_failure_prevents_package_mutation(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash unavailable")
        source = (
            Path(__file__).parents[1] / "scripts/system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_maintenance_wrapper")
        step = (
            "step() {" + wrapper.split("step() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
        )
        block = wrapper[
            wrapper.index('step "apt-get update"') : wrapper.index(
                'step "apt-get upgrade --with-new-pkgs"'
            )
        ]
        payload = (
            """set -uo pipefail
fail=0
APT_LOCK_OPTS=(-o DPkg::Lock::Timeout=600)
LOG=/dev/null
log() { echo "$*"; }
    record_failure() { fail=1; FAILURE_REASONS=$1; echo "FAILED=$1"; }
apt-get() {
  if [ "$1" = update ]; then
    case " $* " in
      *" APT::Update::Error-Mode=any "*) return 100 ;;
      *) return 0 ;;
    esac
  fi
  echo '0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.'
}
"""
            + step
            + block
            + "\necho UNEXPECTED_PACKAGE_MUTATION\n"
        )
        completed = subprocess.run(
            self._bash_command(bash, "-c", payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertIn("FAILED=apt-get update", completed.stdout)
        self.assertNotIn("UNEXPECTED_PACKAGE_MUTATION", completed.stdout)

    def test_monthly_wrapper_fails_when_docker_readback_stays_unavailable(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "system_maintenance_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_maintenance_wrapper")
        start = wrapper.index('if [ "$DOCKER_SNAPSHOT_VALID" -eq 1 ]; then')
        end = wrapper.index("\nif [ -f /run/reboot-required ]; then", start)
        docker_check = wrapper[start:end]
        harness = "\n".join(
            [
                "set -uo pipefail",
                "DOCKER_SNAPSHOT_VALID=1; DOCKER_SNAPSHOT=cli-proxy-api; fail=0",
                'log() { printf "%s\\n" "$*"; }',
                'record_failure() { fail=1; log "FAIL=$1"; }',
                "docker() { return 1; }",
                "sleep() { :; }",
                docker_check,
                'printf "FINAL_FAIL=%s\\n" "$fail"',
            ]
        )
        completed = subprocess.run(
            self._bash_command(bash),
            input=harness.encode("utf-8"),
            capture_output=True,
            timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
        )
        output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("FAIL=docker-daemon-post-maintenance", output)
        self.assertIn("FINAL_FAIL=1", output)
        self.assertNotIn("docker containers verified running", output)

    def test_bwg_full_maintenance_is_scoped_and_serial(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts/bwg_full_maintenance.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[ValidateSet("Observe", "RunNow")]', text)
        self.assertIn('"bwg"', text)
        self.assertIn("-RunIntegration", text)
        self.assertIn("monthly-maintenance.sh", text)
        self.assertIn("vps-launcher-v2ray-agent-update.sh --apply", text)
        # Remote steps travel through the shared transport (never a hand-rolled
        # ssh_tool invocation) with exit-code-faithful capture.
        self.assertIn("Invoke-LauncherRemoteCommand", text)
        self.assertIn("-CaptureOutput", text)
        self.assertNotIn("ssh_tool.py", text)
        self.assertIn(
            "--strict-host-key-checking",
            (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn("REMOTE_OUTPUT_LINES=", text)
        self.assertIn("cpa-doctor-pre", text)
        self.assertIn("cpa-doctor-post", text)
        self.assertIn("proxy_hardening_drift.ps1", text)
        self.assertIn("proxy-hardening-read", text)
        self.assertNotIn("DeactivateOAuthLuna", text)
        self.assertNotIn("RotatePath", text)


if __name__ == "__main__":
    unittest.main()
