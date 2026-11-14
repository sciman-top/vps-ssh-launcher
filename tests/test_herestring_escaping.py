"""test_herestring_escaping.py - split from test_scripts.py (herestring-escaping contracts)."""

import re
import unittest
from pathlib import Path


def live_dollar_hits(line: str) -> list[str]:
    """PowerShell double-quoted here-string live ``$`` markers on one line.

    A ``$`` is escaped only when the run of backticks immediately before it
    has odd length (`` `$ `` = literal dollar). An even run - including zero -
    means the dollar expands locally: `` ``$ `` is an escaped backtick
    followed by a live expansion, which a naive single-backtick lookbehind
    misses entirely.
    """
    hits: list[str] = []
    for match in re.finditer(r"\$(?=[A-Za-z_{$(])", line):
        backticks = 0
        index = match.start() - 1
        while index >= 0 and line[index] == "`":
            backticks += 1
            index -= 1
        if backticks % 2 == 0:
            hits.append(match.group(0))
    return hits


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
            # Dual-lane projection: a PowerShell-computed 0/1 flag. It is a
            # bare value, never a shell expansion, so interpolating it here is
            # the reviewed intent (bwg keeps both kernel wrappers).
            "keep_other_wrapper='$keepOtherWrapper'",
            # Manually-managed-core gate: a PowerShell-computed 0/1 flag (zz
            # xray only), same reviewed bare-value injection class as
            # keep_other_wrapper.
            "menu_update_allowed='$menuUpdateAllowed'",
            # Per-profile Google/Gemini IPv4 domain list, pre-rendered as a bare
            # JSON array of quoted literals by PowerShell. Interpolated into a
            # single-quoted jq filter and a bash heredoc; the values are fixed
            # domain names from the reviewed profile table, never shell syntax.
            "== [$sbGoogleDomainsJson]",
            "target_version='$targetVersion'",
            "expected_sha256='$expectedSha256'",
            "expected_vasma_sha256='$expectedVasmaSha256'",
            # The per-profile route domain list is serialized by PowerShell
            # into the remote jq expression; it is a reviewed data injection,
            # not a bash command substitution.
            'jq -e \'([.route.rules[] | select(.action == "resolve" and .strategy == "ipv4_only")] | length) == 1 and ([.route.rules[] | select(.action == "resolve" and .strategy == "ipv4_only")] | .[0].domain_suffix) == [$sbGoogleDomainsJson]\' "`$path" >/dev/null',
            'if jq -e \'([.route.rules[] | select(.action == "resolve" and .strategy == "ipv4_only")] | length) == 1 and ([.route.rules[] | select(.action == "resolve" and .strategy == "ipv4_only")] | .[0].domain_suffix) == [$sbGoogleDomainsJson]\' "`$source_file" >/dev/null 2>&1; then',
            '"domain_suffix": [$sbGoogleDomainsJson],',
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
            '[ "`$(grep -Ec \'^[[:space:]]*"domain:\' "`$generator_stage")" -ne $expectedDomainCount ]; then',
            'echo "GENERATOR_ROUTE_CONTRACT=${expectedDomainCount}_DOMAINS"',
            # cpa_status_deploy.ps1 bakes PowerShell-computed values into its
            # remote payloads: $remoteDir/$remoteFile/$servicePort are script
            # variables expanded at deploy time, while the backtick-dollar in
            # the backup name deliberately escapes ONLY the remote bash
            # command substitution (reviewed 2026-11-14; the deployed status
            # daemon is the live acceptance of this expansion semantics).
            "umask 022; mkdir -p '$remoteDir';",
            # 2026-11-14: the previous one-line backup was the defect, not the
            # contract. Its single quotes made the stamp a literal in the backup
            # NAME, so every deploy reused one file and the second destroyed the
            # rollback point. The backup is now a multi-line block whose stamp
            # expands remotely; these are its reviewed injections ($remoteFile
            # is deploy-time, `$stamp is remote bash).
            "if [ -f '$remoteFile' ]; then",
            "cp -a '$remoteFile' '$remoteFile.bak.'\"`$stamp\" || exit 1",
            "test -f '$remoteFile.bak.'\"`$stamp\" || exit 1",
            "ExecStart=/usr/bin/python3 $remoteFile --remote-serve --port $servicePort",
        }
    )

    def test_double_quoted_here_strings_escape_every_bash_dollar(self) -> None:
        violations: list[str] = []
        # rglob covers scripts/lib/ too: a helper heredoc that expands local
        # variables is exactly as dangerous as one in a top-level lane script.
        # Anchor at the repo root and require a non-empty script set: a
        # relative "scripts" glob silently matches nothing when pytest runs
        # from another cwd, and an empty scan must fail loudly, not pass.
        scripts = sorted((Path(__file__).parents[1] / "scripts").rglob("*.ps1"))
        self.assertGreater(len(scripts), 0)
        for script in scripts:
            lines = script.read_text(encoding="utf-8").splitlines()
            in_double_here_string = False
            for number, line in enumerate(lines, 1):
                stripped = line.strip()
                if not in_double_here_string:
                    # Any line ending in ``@"`` opens a double here-string in
                    # PowerShell, including the argument form ``-Command @"``;
                    # matching only ``name = @"`` left that parameter form
                    # unscanned (cdn_origin_cert_sync.ps1).
                    if re.fullmatch(r'.*@"$', stripped):
                        in_double_here_string = True
                    continue
                if stripped == '"@':
                    in_double_here_string = False
                    continue
                if stripped in self.INTENTIONAL_INJECTIONS:
                    continue
                for hit in live_dollar_hits(line):
                    violations.append(
                        f"{script}:{number}: unescaped '{hit}' in "
                        f"double-quoted here-string: {stripped[:100]}"
                    )
        self.assertEqual(violations, [])

    def test_backtick_parity_distinguishes_escaped_from_live(self) -> None:
        # A naive (?<!`) lookbehind only blocks a single backtick. PowerShell
        # escapes run pairwise: ``$ is an escaped backtick followed by a LIVE
        # expansion (and must be flagged), while `$ stays a literal dollar.
        self.assertEqual(live_dollar_hits('echo "$x"'), ["$"])
        self.assertEqual(live_dollar_hits('echo "`$x"'), [])
        self.assertEqual(live_dollar_hits('echo "``$x"'), ["$"])
        self.assertEqual(live_dollar_hits('pwd "`$HOME" "``$(id)"'), ["$"])
        self.assertEqual(live_dollar_hits("no dollar here"), [])

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
