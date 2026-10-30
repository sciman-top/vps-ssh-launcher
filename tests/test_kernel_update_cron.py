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
        self.assertIn("trap 'rollback_apply 129' HUP", text)
        # The embedded wrappers must also roll back on SIGHUP (SSH channel
        # drop): an untrapped HUP would bypass the EXIT-trap status write and
        # leave result=running stuck.
        self.assertIn("trap 'recover_on_error 129' HUP", text)
        # vasma refreshes geodata during the Xray-core menu update; the
        # rollback must restore it or a geodata-caused failure survives.
        self.assertIn("restoring pre-update Xray geodata failed", text)
        self.assertIn("cp -a /etc/v2ray-agent/xray/geoip.dat", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("-Apply requires -Version", text)
        self.assertIn("-Apply requires a 64-hex -InstalledSha256", text)
        self.assertIn("-Apply requires a 64-hex -VasmaSha256", text)
        # bwg is dual-lane (xray + sing-box); zz stays sing-box-only. On a
        # dual-lane profile -Apply keeps the sibling wrapper and the cron file
        # projects one line per deployed wrapper with per-lane schedules.
        self.assertIn('"bwg" = @("xray", "sing-box")', text)
        self.assertIn("Profile $Profile is pinned to the active", text)
        self.assertIn("keep_other_wrapper='", text)
        self.assertIn("xray_lane_schedule='20 14 * * 5'", text)
        self.assertIn("singbox_lane_schedule='30 14 * * 5'", text)
        # The sing-box Google/Gemini IPv4 pin list is per-profile: bwg mirrors
        # its xray lane's 14-domain scope (Anthropic domains included so
        # proxy traffic cannot leak through the IPv6 SIT tunnel); zz keeps
        # the 10-domain design (v4-only host).
        self.assertIn("$singboxGoogleIpv4DomainsByProfile", text)
        self.assertIn(
            '"youtube.com", "ytimg.com", "anthropic.com", "claude.ai",\n'
            '    "claude.com", "platform.claude.com"',
            text,
        )
        self.assertIn(
            '"gstatic.com", "googleusercontent.com", "googlevideo.com", "ggpht.com",\n    "youtube.com", "ytimg.com"\n  )',
            text,
        )
        # An empty -Schedule resolves to the selected lane's canonical slot,
        # and a dual-lane profile refuses the sibling lane's canonical slot:
        # same-minute cron lines would share the flock and starve one lane.
        self.assertIn('[string]$Schedule = ""', text)
        self.assertIn("$canonicalScheduleByKernel", text)
        self.assertIn("is the sibling $siblingKernel lane's canonical slot", text)
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

        # A vasma script update rewrites /usr/bin/vasma bytes and silently
        # invalidates the wrapper's embedded EXPECTED_VASMA_SHA256; the next
        # weekly run then fails closed with exit 13 before any gate saw it
        # coming (recurred 2026-10-23 and 2026-10-29). The read lane must
        # compare both embedded pins against live state so the staleness is
        # visible between the two -Apply transactions. Verdicts only: the
        # probe must not echo the raw vasma transcript or imply trust by
        # printing embedded and live hashes as a matched pair.
        self.assertIn("==kernel-pin==", text)
        self.assertIn("vasma_live_sha256=", text)
        self.assertIn("pin_freshness script=", text)
        self.assertIn("vasma_pin=", text)
        self.assertIn("binary_pin=", text)
        self.assertIn("pinned_version=", text)
        self.assertIn('s/^EXPECTED_VASMA_SHA256="\\([0-9a-fA-F]*\\)"/\\1/p', text)
        self.assertIn("state=missing", text)
        # A missing vasma binary must not read as hash drift, and a missing
        # wrapper must still carry the full verdict token set.
        self.assertIn("vasma_state=absent", text)
        self.assertIn("state=missing vasma_pin=missing binary_pin=missing", text)
        # Pin shape is validated whenever a value is provided: the pins are
        # interpolated into the remote bash payload in read mode too, so an
        # unvalidated value must never reach the projection.
        self.assertIn(
            "-Version must look like 26.3.27 so vasma output can be verified.",
            text,
        )
        self.assertIn(
            "-InstalledSha256 (or legacy -Sha256) must be a 64-hex pin when provided.",
            text,
        )
        self.assertIn(
            "-VasmaSha256 must be a 64-hex pin when provided.",
            text,
        )
        # ensure_google_ipv4_route runs in a condition context where errexit
        # is suppressed: the merge and the route assertion must fail
        # explicitly, and the route backup may only be discarded after the
        # merged config is proven to satisfy the contract.
        self.assertIn("sing-box config merge failed; keeping pre-merge state", text)
        self.assertEqual(
            text.count('assert_google_ipv4_route "`$SINGBOX_CONFIG" || return 1'),
            2,
        )
        self.assertIn("==xray-domain-strategy==", text)
        self.assertIn("z_direct_domain_strategy=legacy_settings", text)
        self.assertIn("z_direct_domain_strategy=sockopt", text)
        self.assertIn("z_direct_domain_strategy=invalid", text)

        # GFW-style active probing separates a REALITY/Hysteria2 endpoint from a
        # genuine site using camouflage identity and protocol-shape signals. The
        # read lane surfaces them so drift is visible between -Apply rounds, but
        # the generated config is vasma-owned: the section is report-only and
        # must never echo key material, UUIDs or Hysteria2 passwords.
        self.assertIn("==proxy-camouflage==", text)
        self.assertIn("reality inbound=", text)
        self.assertIn("empty_short_id=", text)
        self.assertIn("min_client_ver=", text)
        self.assertIn("hysteria2 listen_port=", text)
        self.assertIn("obfs=", text)
        self.assertIn("singbox_reality listen_port=", text)
        self.assertIn("handshake_server=", text)
        self.assertIn("camouflage_dest ", text)
        self.assertIn("camouflage_root=", text)
        self.assertIn("camouflage_root=stock-default", text)
        self.assertIn("obfs_lane=", text)
        # Report-only: the camouflage section must not gate or stop the run.
        camouflage = text.split("==proxy-camouflage==", 1)[1]
        camouflage = camouflage.split("if [ \"`$apply\" = '1' ]; then", 1)[0]
        self.assertNotIn("exit 1", camouflage)
        self.assertNotIn("private_key", camouflage)
        self.assertNotIn("password", camouflage)

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

    def test_vasma_query_failure_is_unverified_after_current_installation_check(
        self,
    ) -> None:
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
                    10,
                    output,
                )
                self.assertIn("VERIFIED_CURRENT", output)
                self.assertIn("UNVERIFIED: unable to query latest stable", output)
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

    def test_ensure_google_ipv4_route_fails_closed_in_condition_context(self) -> None:
        # Regression (2026-10-29 review): verify_current_singbox calls
        # ensure_google_ipv4_route via `|| return 1`, so errexit is suppressed
        # for the whole callee. A failed merge or a failed route assertion
        # must return non-zero explicitly, and the route backup may only be
        # deleted after the merged config has been proven to satisfy the
        # contract.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash is not available")
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        wrapper = self._render_embedded_wrapper(source, "write_singbox_wrapper")
        start = wrapper.index("ensure_google_ipv4_route() {")
        function = wrapper[start : wrapper.index("verify_current_singbox() {", start)]
        scenarios = (
            # name, merge_binary, assert_rc, expected_call, backup_gone
            ("merge-fails", "/usr/bin/false", 0, "FAIL", "no"),
            ("assert-fails", "/bin/true", 1, "FAIL", "no"),
            ("all-pass-no-change", "/bin/true", 0, "OK", "yes"),
        )
        for name, merge_binary, assert_rc, expected_call, backup_gone in scenarios:
            with self.subTest(scenario=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = f"""set -uo pipefail
log() {{ printf 'LOG %s\\n' "$*"; }}
LOG='{self._bash_path(bash, root / "wrapper.log")}'
SINGBOX_BINARY={merge_binary}
SINGBOX_CONF_DIR='{self._bash_path(bash, root / "conf")}'
SINGBOX_SOURCE_DIR='{self._bash_path(bash, root / "source")}'
SINGBOX_ROUTE_FRAGMENT='{self._bash_path(bash, root / "source" / "99_route.json")}'
SINGBOX_OLD_GOOGLE_FRAGMENT='{self._bash_path(bash, root / "source" / "99_old.json")}'
SINGBOX_LEGACY_FRAGMENT='{self._bash_path(bash, root / "source" / "99_legacy.json")}'
SINGBOX_CONFIG='{self._bash_path(bash, root / "conf" / "config.json")}'
ROUTE_CHANGED=0
CONFIG_CHANGED=0
ROUTE_BACKUP_DIR='{self._bash_path(bash, root / "backup")}'
source_has_google_ipv4_route() {{ return 0; }}
assert_google_ipv4_route() {{ return {assert_rc}; }}
backup_route_state() {{ mkdir -p "$ROUTE_BACKUP_DIR"; : > "$ROUTE_BACKUP_DIR/keep"; }}
{function}
mkdir -p "$SINGBOX_CONF_DIR" "$SINGBOX_SOURCE_DIR"
: > "$SINGBOX_CONFIG"
backup_route_state
if ensure_google_ipv4_route; then echo CALL_OK; else echo CALL_FAIL; fi
echo "BACKUP_GONE=$([ -e "$ROUTE_BACKUP_DIR" ] && echo no || echo yes)"
"""
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                )
                self.assertEqual(completed.returncode, 0, output)
                self.assertIn(f"CALL_{expected_call}", output)
                self.assertIn(f"BACKUP_GONE={backup_gone}", output)

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
        # Presence-aware, core-any-of verification: the proxy plane needs one
        # active core (xray or sing-box); dormant units for the other core are
        # ignored, and nginx/fail2ban are only required when their unit exists.
        self.assertIn("for service in xray sing-box; do", text)
        self.assertIn("VERIFY_FAILED service=proxy-core", text)
        self.assertIn('service_manager "$service" >/dev/null 2>&1 || continue', text)
        # Presence-gated config validation mirrors the xray run -test branch
        # for the sing-box layout, so a corrupted sing-box config on the
        # dual-core host also fails the updater before any script swap.
        self.assertIn("VERIFY_FAILED singbox_config", text)
        self.assertIn("/etc/v2ray-agent/sing-box/sing-box check", text)
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
        self.assertEqual(pin["version"], "v3.5.33")

        updater = (repo_root / "scripts/remote/v2ray-agent-script-update.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'SOURCE_REF="{pin["ref"]}"', updater)
        self.assertIn(f'EXPECTED_CANDIDATE_SHA="{pin["install_sha256"]}"', updater)
        self.assertIn("candidate_sha_unpinned", updater)
        self.assertIn("trap 'on_error 129' HUP", updater)
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
        # Both profiles share the projected transaction and the reload
        # contract: nginx reload plus a sing-box restart guarded by the
        # applied-sha state file (bwg gained the restart when sing-box
        # Hysteria2 landed on the dual-core host).
        self.assertIn('ValidateSet("bwg", "zz")', renewtls)
        self.assertIn("/etc/v2ray-agent/tls/zz.sciman.top.crt", renewtls)
        self.assertIn("--renew -d '__ACME_DOMAIN__' --ecc", renewtls)
        self.assertIn("renewtls-applied.sha", renewtls)
        self.assertIn("systemctl restart sing-box", renewtls)
        self.assertIn('server_hostname="zz.sciman.top"', renewtls)
        # The bwg reload section mirrors the zz contract against its own
        # probe (8443 / fq.sciman.top). The earlier byte-stability no-op
        # contract was retired when bwg became dual-core: the new section
        # must be re-projected to the host exactly once.
        self.assertIn('server_hostname="fq.sciman.top"', renewtls)
        self.assertNotIn(
            'GATEWAY_CERT="/etc/v2ray-agent/tls/fq.sciman.top.crt"\nACME_HOME', renewtls
        )
        self.assertGreaterEqual(renewtls.count("renewtls-applied.sha"), 2)
        self.assertIn("RENEWTLS_BASELINED", renewtls)


if __name__ == "__main__":
    unittest.main()
