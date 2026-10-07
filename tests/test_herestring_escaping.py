"""test_herestring_escaping.py - split from test_scripts.py (herestring-escaping contracts)."""

import re
import unittest
from pathlib import Path


class PowerShellHereStringEscapingTests(unittest.TestCase):
    """Double-quoted PS here-strings must escape every bash ``$`` they carry.

    A PowerShell ``@"..."@`` here-string expands ``$(command)`` and ``$var``
    locally before the payload ever reaches bash. Unescaped bash substitutions
    are therefore executed on the operator's Windows machine (2026-10-05: the
    monthly and renewtls wrappers computed their age arithmetic against local
    ``date``/``stat`` and injected the garbage into the deployed script) or
    silently expanded to empty strings (the google_ipv4 backup prune compared
    ``""`` to ``""`` and skipped every entry while logging success). The
    intentional PowerShell injections below are the reviewed allowlist; a new
    one must be added here explicitly, which is the review point.
    """

    INTENTIONAL_INJECTIONS = frozenset(
        {
            "schedule='$Schedule'",
            "apply='$applyValue'",
            "kernel='$Kernel'",
            "target_version='$targetVersion'",
            "expected_sha256='$expectedSha256'",
            "expected_vasma_sha256='$expectedVasmaSha256'",
            "lock_file='$MaintenanceLockFile'",
            "apply='$([int]$Apply.IsPresent)'",
            "schedule='$scheduleLiteral'",
            "payload='$wrapperBase64'",
            "payload_sha='$wrapperSha256'",
            "wrapper='$wrapperPath'",
            "cron_file='$cronPath'",
            "lock_file='$lockPath'",
            "legacy_pattern='$legacyPattern'",
            "expected_install_sha='$installShaLiteral'",
            "payload='$sourceBase64'",
            "payload_sha='$sourceSha256'",
            "remote_script='$remoteScriptPath'",
            "'$sourceRefAnchor' \\",
            "'$sourceShaAnchor' \\",
            'apply_script="$safeRemoteApplyScript"',
            'expected_apply_sha256="$expectedRemoteApplySha256"',
        }
    )
    UNESCAPED_DOLLAR = re.compile(r"(?<!`)\$[A-Za-z_{(]")

    def test_double_quoted_here_strings_escape_every_bash_dollar(self) -> None:
        violations: list[str] = []
        for script in sorted(Path("scripts").glob("*.ps1")):
            lines = script.read_text(encoding="utf-8").splitlines()
            in_double_here_string = False
            for number, line in enumerate(lines, 1):
                stripped = line.strip()
                if not in_double_here_string:
                    if re.fullmatch(r"[\w$\.]+\s*=\s*@\"$", stripped):
                        in_double_here_string = True
                    continue
                if stripped == '"@':
                    in_double_here_string = False
                    continue
                if stripped in self.INTENTIONAL_INJECTIONS:
                    continue
                for match in self.UNESCAPED_DOLLAR.finditer(line):
                    violations.append(
                        f"{script}:{number}: unescaped '{match.group(0)}' in "
                        f"double-quoted here-string: {stripped[:100]}"
                    )
        self.assertEqual(violations, [])

    def test_monthly_wrapper_reboot_age_is_bash_evaluated(self) -> None:
        text = Path("scripts/system_maintenance_cron.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "REBOOT_AGE_DAYS=`$(( ( `$(date +%s) - "
            "`$(stat -c %Y /run/reboot-required) ) / 86400 ))",
            text,
        )
        # The escaped form still contains "$(date" as a substring; only an
        # occurrence *not* preceded by a backtick is a live PS subexpression.
        self.assertIsNone(re.search(r"(?<!`)\$\(date \+%s\)", text))

    def test_renewtls_probe_log_age_is_bash_evaluated(self) -> None:
        text = Path("scripts/v2ray_agent_renewtls_cron.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "renewtls_log_age_days=`$(( ( `$(date +%s) - "
            "`$(stat -c %Y /etc/v2ray-agent/crontab_tls.log) ) / 86400 ))",
            text,
        )

    def test_google_ipv4_prune_compares_remote_variables(self) -> None:
        text = Path("scripts/google_ipv4_routing.ps1").read_text(encoding="utf-8")
        self.assertIn('[ "`$old_backup" = "`$BK" ] && continue', text)
        self.assertIn('rm -rf -- "`$old_backup"', text)
        self.assertNotIn('[ "$old_backup" = "$BK" ]', text)


if __name__ == "__main__":
    unittest.main()
