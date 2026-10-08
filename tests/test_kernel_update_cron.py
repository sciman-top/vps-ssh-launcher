"""test_kernel_update_cron.py - split from test_scripts.py (domain: kernel)."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from script_validation_support import (
    ScriptValidationMixin,
)


class KernelUpdateCronTests(ScriptValidationMixin, unittest.TestCase):
    def test_vasma_kernel_cron_uses_vasma_menu_not_direct_downloads(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")

        # Kernel upgrades go through the vasma menu; direct GitHub downloads
        # are forbidden so version pinning and menu handling stay in one place.
        self.assertNotIn("releases?per_page", text)
        self.assertNotIn("releases/download", text)

        # Every apply path must be wrapped in the rollback trap with verified
        # backup/restore state before and after the remote write.
        self.assertIn("backup_apply_state", text)
        self.assertIn("restore_apply_state", text)
        self.assertIn("trap rollback_apply ERR", text)
        self.assertIn("trap 'rollback_apply 130' INT", text)
        self.assertIn("trap 'rollback_apply 143' TERM", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("-Apply requires -Version", text)
        self.assertIn("-Apply requires a 64-hex -InstalledSha256", text)
        self.assertIn("-Apply requires a 64-hex -VasmaSha256", text)
        self.assertIn("TARGET_VERSION", text)
        self.assertIn("EXPECTED_SHA256", text)
        self.assertIn("EXPECTED_VASMA_SHA256", text)
        self.assertIn("verify_target_xray", text)
        self.assertIn("verify_target_singbox", text)
        self.assertIn("pinned Xray version and hash already match", text)
        self.assertIn("pinned sing-box version and hash already match", text)
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", text)
        self.assertIn(
            '[string]$MaintenanceLockFile = "/run/vps-ssh-launcher-maintenance.lock"',
            text,
        )
        self.assertIn("lock_file='$MaintenanceLockFile'", text)
        self.assertIn('LOCK_FILE="__LOCK_FILE__"', text)
        self.assertIn("s|__LOCK_FILE__|", text)
        self.assertIn("CONFIG_CHANGED=0", text)
        self.assertIn("config_hash_before", text)
        self.assertIn("merged sing-box config changed; runtime restart required", text)
        self.assertIn("kernel-xray.status", text)
        self.assertIn("kernel-sing-box.status", text)
        self.assertIn("write_status running 0", text)

        # The menu pipeline is position-coupled to the deployed vasma prompts;
        # both wrappers must verify the expected menu anchors before driving it.
        self.assertEqual(text.count("verify_vasma_anchors"), 5)
        self.assertIn("16.core管理", text)
        self.assertIn("xrayVersionManageMenu", text)
        self.assertIn("singBoxVersionManageMenu", text)
        self.assertIn("1.升级Xray-core", text)
        self.assertIn("1.升级 sing-box", text)
        self.assertIn("1.Upgrade Xray-core", text)
        self.assertIn("1. Upgrade sing-box", text)
        self.assertIn("menu anchors missing", text)
        self.assertIn("exit 12", text)
        self.assertIn("exit 13", text)
        self.assertIn("read_crontab_or_empty", text)
        self.assertIn("SINGBOX_ROUTE_FRAGMENT", text)
        self.assertIn("99_vps_ssh_launcher_google_ipv4.json", text)
        self.assertIn("99_vps_ssh_launcher_ipv4_only.json", text)
        self.assertIn("assert_google_ipv4_route", text)
        self.assertIn("domain_suffix", text)
        self.assertNotIn("crontab -l 2>/dev/null | grep", text)

        # Scheduling must live in /etc/cron.d, not root's crontab: vasma's
        # installCronTLS rewrites `crontab -l` with `sed '/v2ray-agent/d'`,
        # which silently deleted the crontab line on bwg on 2026-09-24.
        self.assertIn("/etc/cron.d/vps-launcher-kernel-update", text)
        self.assertIn("root /bin/bash", text)

        # The read-only probe must surface the weekly cron's structured
        # outcome markers (UNVERIFIED pin backlog, rollback verdicts) so they
        # stop aging silently in a remote log no gate reads. Only the
        # wrappers' own marker lines may be shown; the raw vasma transcript
        # can carry node and subscription details and must never be echoed.
        # `skip reinstall` is the already-pinned fast path -- the *common*
        # healthy weekly outcome -- and without it the probe shows an
        # `update start` with no terminal state for that run.
        self.assertIn("==kernel-update-log==", text)
        self.assertIn("/etc/v2ray-agent/crontab_xray_update.log", text)
        self.assertIn("/etc/v2ray-agent/crontab_singbox_update.log", text)
        self.assertIn(
            "grep -E 'UNVERIFIED|ROLLBACK_VERIFIED|ROLLBACK_FAILED|"
            "DEFERRED_BUSY|update start|update done|update skipped|"
            "skip reinstall|ERROR: |WARN: '",
            text,
        )
        self.assertIn("tail -n 8", text)
        self.assertNotIn('cat "`$log_file"', text)

    def test_rendered_vasma_wrappers_are_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        for function_name in ("write_xray_wrapper", "write_singbox_wrapper"):
            with self.subTest(wrapper=function_name):
                wrapper = self._render_embedded_wrapper(source, function_name)
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
                self.assertEqual(
                    completed.returncode,
                    0,
                    output,
                )

    def test_vasma_query_failure_verifies_current_installation_and_skips(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        cases = (
            (
                "write_xray_wrapper",
                "vasma_visible_stable_xray_version",
                "verify_current_xray",
            ),
            (
                "write_singbox_wrapper",
                "vasma_visible_stable_singbox_version",
                "verify_current_singbox",
            ),
        )
        for wrapper_name, query_function, verify_function in cases:
            with self.subTest(wrapper=wrapper_name):
                wrapper = self._render_embedded_wrapper(source, wrapper_name)
                branch_start = wrapper.index(
                    f'if ! latest_version="$({query_function})"; then'
                )
                branch_end = wrapper.index(
                    'if [ "$current_version" = "$latest_version" ]; then',
                    branch_start,
                )
                branch = wrapper[branch_start:branch_end]
                probe = f"""
set -Eeuo pipefail
log() {{ printf '%s\\n' "$*"; }}
{query_function}() {{ return 22; }}
{verify_function}() {{ echo VERIFIED_CURRENT; }}
{branch}
echo UNREACHABLE
"""
                completed = subprocess.run(
                    self._bash_command(bash, "-s"),
                    input=probe.encode("utf-8"),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8",
                    errors="replace",
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    output,
                )
                self.assertIn("VERIFIED_CURRENT", output)
                self.assertIn("unable to query latest stable", output)
                self.assertNotIn("UNREACHABLE", output)

    def test_vasma_target_verification_rejects_version_and_hash_drift(self) -> None:
        # Regression for the 2026-10-04 full-chain audit. Both target
        # verifiers were invoked as `if ! verify_target_*` while their bodies
        # were bare `[ ... ]` lines. Bash ignores `set -e` inside a function
        # executed in a condition context, so the function's status collapsed
        # to its last command and the pinned version/SHA-256 assertions became
        # dead code: a wrong binary was accepted and the wrapper logged a
        # successful update. Every assertion must short-circuit explicitly.
        # Static text assertions cannot catch this, hence the behavioural
        # probe below (stubbed dependencies + real function bodies).
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        wrappers = (
            (
                "write_xray_wrapper",
                "current_xray_version",
                "XRAY_BINARY",
                "XRAY_CONFDIR",
                "verify_target_xray",
                "verify_current_xray() {",
                "restore_xray() {",
            ),
            (
                "write_singbox_wrapper",
                "current_singbox_version",
                "SINGBOX_BINARY",
                "SINGBOX_CONFIG",
                "verify_target_singbox",
                "verify_current_singbox() {",
                "restore_singbox() {",
            ),
        )
        scenarios = (
            # name, stub version, hash mode, binary, service rc, expected verdict
            ("version-drift", "v0.0.1", "match", "true", "0", "REJECT"),
            ("hash-drift", "v9.9.9", "zero", "true", "0", "REJECT"),
            ("binary-not-runnable", "v9.9.9", "match", "false", "0", "REJECT"),
            ("service-inactive", "v9.9.9", "match", "true", "1", "REJECT"),
            ("all-match", "v9.9.9", "match", "true", "0", "ACCEPT"),
        )
        for (
            wrapper_name,
            version_function,
            binary_var,
            config_var,
            verify_target,
            start_marker,
            end_marker,
        ) in wrappers:
            wrapper = self._render_embedded_wrapper(source, wrapper_name)
            body_start = wrapper.index(start_marker)
            functions = wrapper[body_start : wrapper.index(end_marker, body_start)]
            for name, stub_version, hash_mode, binary, service_rc, verdict in scenarios:
                with self.subTest(wrapper=wrapper_name, scenario=name):
                    if hash_mode == "match":
                        hash_line = (
                            'EXPECTED_SHA256="$(sha256sum "$%s" '
                            "| awk '{print $1}')\"" % binary_var
                        )
                    else:
                        hash_line = 'EXPECTED_SHA256="%s"' % ("0" * 64)
                    probe = f"""
set -Eeuo pipefail
log() {{ printf '%s\\n' "$*"; }}
LOG="/tmp/vasma-verify-probe.log"
{binary_var}="/bin/{binary}"
{config_var}="/tmp/vasma-verify-probe"
TARGET_VERSION="v9.9.9"
ROUTE_CHANGED=0
CONFIG_CHANGED=0
{version_function}() {{ printf '%s\\n' "{stub_version}"; }}
service_is_active() {{ return {service_rc}; }}
service_restart() {{ return 0; }}
ensure_google_ipv4_route() {{ return 0; }}
assert_google_ipv4_route() {{ return 0; }}
{functions}
{hash_line}
if {verify_target}; then echo VERDICT_ACCEPT; else echo VERDICT_REJECT; fi
"""
                    completed = subprocess.run(
                        self._bash_command(bash, "-s"),
                        input=probe.encode("utf-8"),
                        capture_output=True,
                        timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                        check=False,
                    )
                    output = (completed.stdout + completed.stderr).decode(
                        "utf-8",
                        errors="replace",
                    )
                    self.assertEqual(completed.returncode, 0, output)
                    self.assertIn(f"VERDICT_{verdict}", output)

    def test_vasma_core_backup_prune_keeps_newest_and_protects_current(self) -> None:
        # The weekly lane used to accumulate one binary+conf snapshot per
        # release forever. Retention must keep the newest N, drop the rest, and
        # never delete the backup the current run just created (it is the
        # rollback source until the next verified success).
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "backups"
            root.mkdir()
            for index in range(10):
                entry = root / f"v2ray-agent-core-update.{index:06d}"
                entry.mkdir()
                stamp = 1_700_000_000 + index * 3600
                os.utime(entry, (stamp, stamp))
            protected = root / "v2ray-agent-core-update.zzzzzz"
            protected.mkdir()
            os.utime(protected, (1_600_000_000, 1_600_000_000))  # oldest by mtime
            foreign = root / "unrelated-dir"
            foreign.mkdir()
            for wrapper_name, end_marker in (
                ("write_xray_wrapper", "restore_xray() {"),
                ("write_singbox_wrapper", "restore_singbox() {"),
            ):
                with self.subTest(wrapper=wrapper_name):
                    wrapper = self._render_embedded_wrapper(source, wrapper_name)
                    start = wrapper.index("prune_core_backups() {")
                    function = wrapper[start : wrapper.index(end_marker, start)]
                    # Best effort: under `set -o pipefail` a prune failure must
                    # not turn a successful upgrade into a non-zero cron result.
                    self.assertIn("done || true", function)
                    harness = "\n".join(
                        [
                            "set -uo pipefail",
                            'log() { printf "LOG %s\\n" "$*"; }',
                            f"CORE_BACKUP_ROOT='{self._bash_path(bash, root)}'",
                            f"BACKUP_DIR='{self._bash_path(bash, protected)}'",
                            function,
                            "prune_core_backups",
                        ]
                    )
                    completed = subprocess.run(
                        self._bash_command(bash),
                        input=harness.encode("utf-8"),
                        capture_output=True,
                        timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr.decode())
                    remaining = sorted(path.name for path in root.iterdir())
                    self.assertEqual(
                        remaining,
                        [
                            foreign.name,
                            *(
                                f"v2ray-agent-core-update.{index:06d}"
                                for index in range(2, 10)
                            ),
                            protected.name,
                        ],
                    )
                    self.assertIn(
                        "LOG PRUNE scope=core_backups policy=keep_8",
                        completed.stdout.decode(),
                    )

    def test_v2ray_agent_script_updater_only_replaces_management_script(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        updater = repo_root / "scripts" / "remote" / "v2ray-agent-script-update.sh"
        text = updater.read_text(encoding="utf-8")
        self.assertIn("SOURCE_REPOSITORY=", text)
        self.assertIn("SOURCE_REF=", text)
        self.assertIn("EXPECTED_CANDIDATE_SHA=", text)
        self.assertIn("candidate_sha_unpinned", text)
        self.assertIn("--check", text)
        self.assertIn("--apply", text)
        self.assertIn("flock -n", text)
        self.assertIn("v2ray-agent-update.status", text)
        self.assertIn("write_status running 0", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("coreVersionManageMenu", text)
        self.assertIn("xrayVersionManageMenu", text)
        self.assertIn('systemctl cat "$1" >/dev/null 2>&1 || return 1', text)
        self.assertIn('service_manager "$service" >/dev/null 2>&1 || {', text)
        self.assertNotIn("/usr/bin/vasma", text)
        self.assertNotIn("/usr/sbin/vasma", text)
        self.assertNotIn("printf '16", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("rc-service .* restart", text)

        # Route through the same launcher resolution the other bash-driven
        # assertions use: a bare "bash" resolves to the WSL stub on some
        # Windows hosts and then the run produces no readable streams at all.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        completed = subprocess.run(
            [
                *self._bash_command(bash, "-n"),
                updater.relative_to(repo_root).as_posix(),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout or "") + (completed.stderr or ""),
        )

    def test_v2ray_agent_script_projection_is_pinned_and_backup_first(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / "scripts" / "v2ray_agent_script_update_cron.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[string]$InstallSha256", text)
        self.assertIn("-Apply requires -InstallSha256", text)
        # Remote transport moved into the shared lib helper; the strict-host
        # invariant now lives there and the wrapper must ride that helper.
        self.assertIn("Invoke-LauncherRemoteCommand", text)
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("--strict-host-key-checking", helper)
        self.assertIn("/var/backups/v2ray-agent-script-update-deploy", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("RUNTIME_VERIFY_OK", text)
        self.assertIn("/etc/cron.d/vps-launcher-v2ray-agent-update", text)
        self.assertIn("v2ray-agent-source-pin.json", text)
        self.assertIn("SOURCE_REF=", text)
        self.assertIn('systemctl cat "`$1" >/dev/null 2>&1 || return 1', text)
        self.assertIn("return 1\n  fi\n}\n\nverify_runtime", text)
        # A failed weekly run must not age silently in a remote log no gate
        # reads: the read-only probe surfaces the updater's own markers only.
        self.assertIn("==update-log==", text)
        self.assertIn("/var/log/vps-launcher-v2ray-agent-update.log", text)
        self.assertIn("ROLLBACK_|ERROR '", text)
        self.assertNotIn("cat /var/log/vps-launcher-v2ray-agent-update.log", text)
        # A read-only probe must not fail on a host that simply lacks vasma or
        # has an inactive proxy service.
        self.assertIn(
            "verify_runtime || echo 'RUNTIME_VERIFY_NONFATAL_READ_ONLY'", text
        )
        self.assertIn("RUNTIME_VERIFY_NONFATAL_READ_ONLY", text)

    def test_v2ray_agent_source_pin_and_renewtls_lock_contracts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        pin = json.loads(
            (repo_root / "scripts/remote/v2ray-agent-source-pin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pin["repository"], "https://github.com/mack-a/v2ray-agent")
        self.assertRegex(pin["ref"], r"^[0-9a-f]{40}$")
        self.assertRegex(pin["install_sha256"], r"^[0-9a-f]{64}$")

        updater = (repo_root / "scripts/remote/v2ray-agent-script-update.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("SOURCE_REF=", updater)
        self.assertIn("EXPECTED_CANDIDATE_SHA=", updater)
        self.assertIn("candidate_sha_unpinned", updater)
        self.assertNotIn(
            'SOURCE_URL="https://raw.githubusercontent.com/mack-a/v2ray-agent/master',
            updater,
        )

        renewtls = (repo_root / "scripts/v2ray_agent_renewtls_cron.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("/run/vps-ssh-launcher-maintenance.lock", renewtls)
        self.assertIn("/etc/cron.d/vps-launcher-v2ray-agent-renewtls", renewtls)
        self.assertIn("/etc/v2ray-agent/install.sh RenewTLS", renewtls)
        self.assertIn("verify_rollback_state", renewtls)
        self.assertIn("ROLLBACK_VERIFIED", renewtls)
        self.assertIn(
            '/bin/bash /etc/v2ray-agent/install.sh RenewTLS >> "$LOG_FILE" 2>&1',
            renewtls,
        )
        self.assertNotIn(
            "exec /bin/bash /etc/v2ray-agent/install.sh RenewTLS", renewtls
        )
        # vasma's RenewTLS transcript can carry domain details, so only the
        # log's freshness is reported; no content is echoed.
        self.assertIn("==renewtls-log==", renewtls)
        self.assertIn("renewtls_log_age_days=", renewtls)
        self.assertIn("renewtls.status", renewtls)
        self.assertIn("write_status running 0", renewtls)
        self.assertNotIn("cat /etc/v2ray-agent/crontab_tls.log", renewtls)


if __name__ == "__main__":
    unittest.main()
