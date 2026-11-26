"""test_cpa_guardrails_script.py - split from test_scripts.py (domain: guardrails)."""

import ast
import base64
import datetime
import http.server
import json
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, cast
from unittest import mock
from cpa_catalog_expectations import (
    ANTIGRAVITY_OAUTH_ROUTE_ALIASES,
    OAUTH_ROUTE_ALIASES,
)

from script_validation_support import (
    ScriptValidationMixin,
    read_guardrail_source,
    server_thread,
)


def _fail2ban_failregex(source: str) -> str:
    """Return the single failregex line from a fail2ban filter file."""
    for line in source.splitlines():
        if line.startswith("failregex = "):
            return line.split("failregex = ", 1)[1].strip()
    raise AssertionError("filter declares no failregex")


def _guardrail_preparation_fixture(
    source: str, *, include_updater_timeout: bool = True
) -> str:
    """Keep a syntactically whole preparation prefix for focused execution."""

    source = source.replace("\r\n", "\n")
    header_end = source.index('$ErrorActionPreference = "Stop"')
    body = source[header_end:]
    if not include_updater_timeout:
        body = body.replace(
            "$updaterTimeoutConfPath = Join-Path $scriptDir "
            '"remote\\cpa-update-timeout.conf"',
            "$updaterTimeoutConfPath = $env:VPS_TEST_UPDATER_TIMEOUT",
            1,
        )
        body += "\nfunction Assert-ProjectionSourcesUnchanged { }"
        body += "\n$projectionHashPairs = ''"
    body = body.replace(
        "if ($QuarantineClaudeOAuth -and [string]::IsNullOrWhiteSpace($QuarantineUserDirective)) {",
        "if ($false) {",
        1,
    )
    body = body.replace(
        '$updaterTimeoutConfText = Read-ProjLeafText -Path $updaterTimeoutConfPath -Description "CPA updater timeout drop-in"',
        '$updaterTimeoutConfText = "TimeoutStartSec=40min`nTimeoutStopSec=4min`n"',
        1,
    )
    body = body.replace('if ($Profile -ne "bwg") {', "if ($false) {", 1)
    command_start = body.find("function Invoke-BwgRemoteScript {")
    if command_start < 0:
        raise AssertionError("guardrail fixture could not find the command boundary")
    body = body[:command_start].rstrip()
    if ". (Join-Path $scriptDir" not in body:
        raise AssertionError("guardrail fixture did not retain the shared bootstrap")
    for marker in (
        "$guardrailTemplateTexts = @{}",
        "function Assert-ProjectionSourcesUnchanged",
    ):
        if body.find(marker) < 0:
            raise AssertionError(f"guardrail fixture stopped before {marker}")
    lines = [
        line
        for line in body.splitlines()
        if "scriptDir = Split-Path -Parent" not in line
        and "repoRoot = Split-Path -Parent" not in line
        and "ensure_repo_imports()" not in line
        and not line.startswith('"{')
        and not line.startswith('Write-Host "AUDIT_RESULT=PASS"')
        and not line.startswith("if ($QuarantineClaudeOAuth -and")
        and '. (Join-Path $scriptDir "lib\\project_environment.ps1")' not in line
    ]
    guarded: list[str] = ["#requires -Version 7"]
    guarded.append("$scriptDir = $env:VPS_TEST_SCRIPTS")
    guarded.append("$repoRoot = $env:VPS_TEST_ROOT")
    guarded.append('$connectScript = Join-Path $repoRoot "connect.ps1"')
    guarded.append(
        "$project_environment_path = Join-Path $env:VPS_TEST_SCRIPTS "
        '"lib/project_environment.ps1"'
    )
    guarded.append(". $project_environment_path")
    for line in lines:
        guarded.append(line)
    return "\n".join(guarded)


class CpaGuardrailsScriptTests(ScriptValidationMixin, unittest.TestCase):
    def test_doctor_oauth_egress_scan_is_precise_and_redacted(self) -> None:
        repo = Path(__file__).parents[1]
        doctor = (repo / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        block = (
            'echo "==cpa-doctor=="'
            + doctor.split('echo "==cpa-doctor=="', 1)[1].split(
                'echo "==container=="', 1
            )[0]
        )
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for OAuth egress scan")
        baseline_cases = {
            "OAI_FIXTURE_BASE_URL": "http://127.0.0.1:8317",
            "OPENAI_FIXTURE_BASE_URL": "http://127.0.0.1:8317",
        }
        harness_prefix = (
            "set -u\n"
            "DOCTOR_FAILED=0\n"
            'mark_fail() { echo "$1=FAIL"; DOCTOR_FAILED=1; }\n'
            "env() {\n"
            "  printf '%s\\n' OAI_SENSITIVE_VALUE\n"
            "}\n"
        )
        harness_suffix = block + '\necho "DOCTOR_FAILED=$DOCTOR_FAILED"\n'

        def probe(
            extra_environment: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            visible_environment = {
                **baseline_cases,
                "https_proxy": "http://ordinary-proxy.invalid:8443",
                **extra_environment,
            }
            case_environment = {
                "PATH": os.environ.get("PATH", ""),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                "WINDIR": os.environ.get("WINDIR", ""),
                "COMSPEC": os.environ.get("COMSPEC", ""),
                "TMP": os.environ.get("TMP", ""),
                "TEMP": os.environ.get("TEMP", ""),
                "HOME": os.environ.get("HOME", ""),
                "LANG": os.environ.get("LANG", ""),
                "MSYSTEM": os.environ.get("MSYSTEM", ""),
                "NO_PROXY": "oauth.proxy.invalid",
            }
            case_prefix = harness_prefix.replace(
                "  printf '%s\\n' OAI_SENSITIVE_VALUE",
                "  printf '%s\\n' "
                + " ".join(
                    shlex.quote(f"{key}={value}")
                    for key, value in visible_environment.items()
                ),
                1,
            )
            self.assertNotEqual(case_prefix, harness_prefix)
            case_environment = {
                **case_environment,
                **{
                    key: value
                    for key, value in extra_environment.items()
                    if key in {"NO_PROXY", "no_proxy"}
                },
            }
            result = subprocess.run(
                self._bash_command(
                    bash,
                    "-c",
                    case_prefix + harness_suffix,
                ),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=case_environment,
            )
            if result.returncode != 0 or "DOCTOR_FAILED" not in result.stdout:
                raise AssertionError(
                    f"harness output={result.stdout!r} stderr={result.stderr!r} "
                    f"environment={sorted(extra_environment)!r}"
                )
            return result

        generic = probe({})
        self.assertEqual(generic.returncode, 0, generic.stderr)
        self.assertIn("DOCTOR_FAILED=0", generic.stdout)
        self.assertNotIn("accounts.google.com", generic.stdout)
        lookalike = probe({"CODEX_FOO_BASE_URL": "https://fixture.invalid/v1"})
        self.assertIn("DOCTOR_FAILED=0", lookalike.stdout)

        cases = (
            (
                "OPENAI_OAUTH_BASE_URL",
                "https://user:secret@proxy.invalid/v1",
                "secret",
                "oauth_egress_override_kind=OAUTH_AUTHENTICATION",
            ),
            (
                "CHATGPT_AUTH_BASE_URL",
                "https://user:secret@proxy.invalid/v1",
                "CHATGPT_AUTH_BASE_URL",
                "oauth_egress_override_kind=OAUTH_AUTHENTICATION",
            ),
            (
                "CODEX_AUTH_ENDPOINT",
                "https://oauth.proxy.invalid/v1",
                "CODEX_AUTH_ENDPOINT",
                "oauth_egress_override_kind=OAUTH_AUTHENTICATION",
            ),
            (
                "https_proxy",
                "http://user:secret@accounts.google.com:8443",
                "accounts.google.com:8443",
                "oauth_egress_proxy_var=https_proxy",
            ),
        )
        for name, value, redacted_key, expected_marker in cases:
            with self.subTest(variable=name):
                result = probe({name: value})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(generic.returncode, 0, generic.stderr)
                self.assertIn("DOCTOR_FAILED=0", generic.stdout)
                lines = result.stdout.replace("\r\n", "\n").splitlines()
                self.assertIn(expected_marker, lines)
                self.assertIn("oauth_upstream_egress_override=PRESENT", lines)
                self.assertIn("oauth-upstream-egress=FAIL", lines)
                self.assertIn("DOCTOR_FAILED=1", lines)
                self.assertNotIn(redacted_key, result.stdout)
        for name, value in (
            ("CODEX_LUNA_BASE_URL", "https://fixture.example.invalid/v1"),
            ("CODEX_FOO_BASE_URL", "https://fixture.example.invalid/v1"),
            ("CODEX_BASE_URL_URL", "https://fixture.example.invalid/v1"),
        ):
            with self.subTest(lookalike=name):
                result = probe({name: value})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("DOCTOR_FAILED=0", result.stdout)
        bypassed = probe(
            {
                "https_proxy": "http://oauth.proxy.invalid:8443",
                "NO_PROXY": "oauth.proxy.invalid",
            }
        )
        self.assertEqual(bypassed.returncode, 0, bypassed.stderr)
        self.assertIn("DOCTOR_FAILED=0", bypassed.stdout)

    def test_diagnostics_do_not_share_predictable_tmp_files(self) -> None:
        root = Path(__file__).parents[1] / "scripts/remote"
        bash = self._resolve_bash()
        for name in (
            "apply",
            "deactivate-oauth-luna",
            "doctor",
            "quarantine-oauth-luna",
            "rotate-path",
        ):
            source = (root / f"cpa-guardrail-{name}.sh").read_text(encoding="utf-8")
            self.assertNotRegex(source, r"/tmp/cpa-[A-Za-z0-9_.-]+\.(?:log|json)")
            if bash is not None:
                result = subprocess.run(
                    self._bash_command(bash, "-n"),
                    input=source,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        if bash is None:
            self.skipTest("bash unavailable for diagnostic cleanup check")
        source = (root / "cpa-guardrail-doctor.sh").read_text(encoding="utf-8")
        prelude = source[: source.index("STRICT=1")]
        harness = (
            'rm() { command rm "$@"; [[ ! -e "${@: -1}" ]] || exit 99; '
            'printf "CLEANED\\n"; };\n'
            + prelude
            + 'stat -c %a "$DIAGNOSTIC_DIR"; exit 7\n'
        )
        result = subprocess.run(
            self._bash_command(bash, "-c", harness),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        # Git Bash on NTFS reports synthetic directory modes (typically 755);
        # Linux CI checks the real mktemp mode. Cleanup runs on both hosts.
        if os.name != "nt":
            self.assertIn("700", result.stdout)
        self.assertIn("CLEANED", result.stdout)

    def test_model_substitution_classifies_known_and_unknown_pairs(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for substitution classification harness")
        source = read_guardrail_source()
        section = source.split('echo "==model-substitution=="', 1)[1].split(
            'echo "==tls-cert=="', 1
        )[0]

        def run(logs: str) -> str:
            with tempfile.TemporaryDirectory() as temp_dir:
                harness = f"""
set -o pipefail
DIAGNOSTIC_DIR={temp_dir!r}
mkdir -p "$DIAGNOSTIC_DIR"
docker() {{
  if [ "$1" = inspect ]; then
    printf '%s\\n' 'eceasy/cli-proxy-api:v8.0.47'
  elif [ "$1" = logs ]; then
    printf '%s\\n' "$FAKE_LOGS"
  fi
}}
{section}
"""
                result = subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env={**os.environ, "FAKE_LOGS": logs},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

        known = 'upstream served model "gemini-3.8-flash-n" for requested model "gemini-3.8-flash-high"'
        known_output = run("\n".join([known] * 6))
        self.assertIn("model_substitution_known_name_variant_pairs=1", known_output)
        self.assertIn("model_substitution_unknown_pairs=0", known_output)
        self.assertIn(
            "model_substitution=WARN_SUBSTITUTION_REVIEWED_PAIR", known_output
        )

        known_single_output = run(known)
        self.assertIn(
            "model_substitution=WARN_SUBSTITUTION_REVIEWED_PAIR",
            known_single_output,
        )

        control = 'upstream served model "gemini-3.8-flash-control" for requested model "gemini-3.8-flash-high"'
        control_output = run("\n".join([control] * 6))
        self.assertIn("model_substitution_known_name_variant_pairs=1", control_output)
        self.assertIn("model_substitution_unknown_pairs=0", control_output)
        self.assertIn(
            "model_substitution=WARN_SUBSTITUTION_REVIEWED_PAIR", control_output
        )

        gemini4_known = "\n".join(
            [
                'upstream served model "gemini-4-neon" for requested model "gemini-4-neon-xhigh"',
                'upstream served model "gemini-4-neon" for requested model "gemini-4-neon-medium"',
                'upstream served model "gemini-4-neon" for requested model "gemini-4-neon-low"',
                'upstream served model "gemini-4-argon-trial" for requested model "gemini-4-argon-high"',
                'upstream served model "gemini-4-argon-trial" for requested model "gemini-4-argon-low"',
            ]
            * 4
        )
        gemini4_output = run(gemini4_known)
        self.assertIn("model_substitution_known_name_variant_pairs=5", gemini4_output)
        self.assertIn("model_substitution_unknown_pairs=0", gemini4_output)
        self.assertIn(
            "model_substitution=WARN_SUBSTITUTION_REVIEWED_PAIR", gemini4_output
        )

        unknown = 'upstream served model "gemini-3.8-flash-lite" for requested model "gemini-3.8-flash-high"'
        unknown_output = run("\n".join([unknown] * 6))
        self.assertIn("model_substitution_known_name_variant_pairs=0", unknown_output)
        self.assertIn("model_substitution_unknown_pairs=1", unknown_output)
        self.assertIn("model_substitution=WARN_SUBSTITUTION_ELEVATED", unknown_output)

    def test_doctor_parses_fork_image_tags(self) -> None:
        """The fork image carries a suffix; the version must still parse.

        2026-11-09: the running image became cli-proxy-api-fork:v8.0.61-gemini-raw.
        The old sed anchored vX.Y.Z at the END of the tag, so it parsed nothing
        and the whole model-substitution section was skipped -- the silent
        downgrade observation went blind exactly while the fork was serving, and
        the doctor still reported a passing contract.
        """

        source = read_guardrail_source()
        section = source.split('echo "==model-substitution=="', 1)[1].split(
            'echo "==tls-cert=="', 1
        )[0]
        # The version must be matched with a trailing wildcard, not $.
        self.assertIn(r"s#.*:(v[0-9]+\.[0-9]+\.[0-9]+).*$#", section)
        self.assertNotIn(r"s#.*:(v[0-9]+\.[0-9]+\.[0-9]+)(@sha256", section)
        # An unparsable tag must be reported, never silently skipped.
        self.assertIn("cpa_image_tag_unparsed=", section)

    def test_doctor_warns_about_orphan_projection_copies(self) -> None:
        """A historical copy of a streamed payload must be reported, not hidden.

        2026-10-08 modularization moved the doctor from a projected file to an
        inline streamed script but left /opt/cliproxyapi/cpa-guardrail-doctor.sh
        on the host. Nothing reads it, so no drift check ever covers it, and it
        sat there for a month keeping a stale copy of the whole guard pipeline.
        The check must WARN (an inert copy cannot break a projection) and must
        state the coverage boundary so nobody reads silence as proof of absence.
        """

        source = read_guardrail_source()
        self.assertIn("projection-copy-orphan=", source)
        self.assertIn("projection-copy-coverage=", source)
        # The orphan section ends at the next header. The updater-schedule
        # gate that follows is a separate, hard gate and must not be read as
        # part of this observation.
        section = source.split("projection-copy-orphan=", 1)[1].split(
            'echo "==updater-schedule=="', 1
        )[0]
        self.assertIn("==updater-schedule==", source)
        # Inert, so it must not fail the contract.
        self.assertNotIn("mark_fail", section)

    def test_doctor_attributes_ban_and_substitution_signals_to_credentials(
        self,
    ) -> None:
        """Per-credential attribution must survive as an observation.

        The oauth-monitor section reads the Codex expiry file and container-level
        refresh failures, so a single credential returning 401/403 or "account
        restricted" was invisible unless the whole provider was down. CPA labels
        every failed upstream attempt with auth_file=, which is the only
        per-account attribution available without reading credentials. The
        doctor must surface that attribution and must never turn an external ban
        wave into a failing gate.
        """

        source = read_guardrail_source()
        for marker in (
            'echo "==credential-ban-signals=="',
            'echo "==model-substitution-by-credential=="',
        ):
            self.assertIn(marker, source)
        # Observation only: neither new section may gate the contract.
        ban_section = source.split('echo "==credential-ban-signals=="', 1)[1].split(
            'echo "==model-substitution-by-credential=="', 1
        )[0]
        self.assertNotIn("mark_fail", ban_section)
        self.assertIn("this is an observation, not a gate", ban_section)

        # Behavioural probes (21e1ac5 sentinel pattern) instead of pins on the
        # hashing/pipeline spelling: the real credential label pipeline is
        # extracted and executed on fixtures.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for the ban-signal pipeline probe")
        pipeline_head = next(
            line
            for line in ban_section.splitlines()
            if line.startswith("python3 - \"$BAN_TMP\" <<'PY'")
        )
        python_body = ban_section.split(pipeline_head + "\n", 1)[1].split("\nPY\n", 1)[
            0
        ]

        def run_label_pipeline(labels: list[str]) -> str:
            """Run the real credential_ban_signal pipeline over a fixture log."""
            with tempfile.TemporaryDirectory() as directory:
                ban_log = Path(directory) / "ban.log"
                ban_log.write_text(
                    "".join(
                        f"invalid_grant account restricted auth_file=oauth/{label}\n"
                        for label in labels
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
                executable = str(Path(sys.executable).as_posix())
                harness = (
                    "set -euo pipefail\n"
                    f'PYTHON_BIN="{executable}"\n'
                    'python3() { "$PYTHON_BIN" "$@"; }\n'
                    f'BAN_TMP="{ban_log.as_posix()}"\n'
                    + pipeline_head
                    + "\n"
                    + python_body
                    + "\nPY\n"
                )
                result = subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

        # Credential labels must be hashed like the oauth-monitor section:
        # auth files are named after accounts, so a raw filename would put
        # account emails into the doctor output while the attribution
        # itself survives the hash.
        labels = [f"user-{index}@gmail.com.json" for index in range(10)]
        output = run_label_pipeline(["repeat-me@gmail.com.json"] * 3 + labels)
        signal_lines = [
            line
            for line in output.splitlines()
            if line.startswith("credential_ban_signal=")
        ]
        # The pipeline aggregates inside the signal print itself (bounded
        # top-8, descending counts), not in a side branch.
        self.assertLessEqual(len(signal_lines), 8)
        self.assertGreaterEqual(len(signal_lines), 8)
        self.assertTrue(
            all(
                re.fullmatch(r"credential_ban_signal=\d+ x [0-9a-f]{8}", line)
                for line in signal_lines
            ),
            signal_lines,
        )
        counts = [int(line.split("=", 1)[1].split(" ", 1)[0]) for line in signal_lines]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts[0], 3)
        # The account email itself never reaches the doctor output.
        self.assertNotIn("gmail.com", output)
        self.assertNotIn("auth_file=", output)

        # The auth_index token sits inside parentheses; a plain $i never matches,
        # which is how the first draft reported zero credentials. The real awk
        # aggregation must strip them and print the attributed pair counts.
        subst_section = source.split('echo "==model-substitution-by-credential=="', 1)[
            1
        ].split('echo "==syntax=="', 1)[0]
        pipeline_tail = '"$SUBST_ALL_TMP" | sort | uniq -c | sort -rn | head -n 6 \\\n'
        awk_start = subst_section.rindex(
            "awk '{", 0, subst_section.index(pipeline_tail)
        )
        printf_index = subst_section.index('printf "model_substitution_credential=')
        awk_pipeline = subst_section[
            awk_start : subst_section.index("\n", printf_index)
        ]

        def run_credential_pipeline(lines: list[str]) -> list[str]:
            with tempfile.TemporaryDirectory() as directory:
                subst_log = Path(directory) / "subst.log"
                subst_log.write_text(
                    "".join(line + "\n" for line in lines),
                    encoding="utf-8",
                    newline="\n",
                )
                harness = (
                    "set -euo pipefail\n"
                    f'SUBST_ALL_TMP="{subst_log.as_posix()}"\n' + awk_pipeline + "\n"
                )
                result = subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return [
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                ]

        warn = (
            'upstream served model "{served}" for requested model "{requested}" '
            "(auth_index={idx})"
        )
        attributed = run_credential_pipeline(
            [
                warn.format(
                    served="gemini-3.8-flash-n",
                    requested="gemini-3.8-flash-high",
                    idx="a64956265d0f4506",
                ),
                warn.format(
                    served="gemini-3.8-flash-n",
                    requested="gemini-3.8-flash-high",
                    idx="a64956265d0f4506",
                ),
                warn.format(
                    served="gpt-5.6-terra",
                    requested="gpt-5.6-terra",
                    idx="0123abcdef",
                ),
            ]
        )
        self.assertEqual(
            attributed,
            [
                "model_substitution_credential=2 x a64956265d0f4506 "
                "gemini-3.8-flash-n for requested model gemini-3.8-flash-high",
                "model_substitution_credential=1 x 0123abcdef "
                "gpt-5.6-terra for requested model gpt-5.6-terra",
            ],
        )
        # Coverage must be scoped to the lane it describes, not every provider.
        self.assertIn("grep -c 'auth=antigravity-'", source)

    def test_doctor_admission_journal_aggregates_upstream_429_503(self) -> None:
        source = read_guardrail_source()
        section = source.split('echo "==admission-journal-24h=="', 1)[1].split(
            'echo "==model-substitution=="', 1
        )[0]
        script = section.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        fixture = "\n".join(
            (
                "Oct 17 cpa-admission[1]: upstream_result lane=claude-oauth "
                "model=claude-haiku-5-5 status=429 capacity=true "
                "retry_after=30 waited_ms=3000 request_id=req-secret "
                "client_text=prompt-secret",
                "upstream_result lane=chatgpt-oauth model=gpt-6.1-sol "
                "status=200 capacity=true retry_after=absent waited_ms=0 "
                "successful=false protocol_result=capacity request_id=req-secret",
                "upstream_result lane=chatgpt-oauth model=gpt-6.1-sol "
                "status=200 capacity=false retry_after=absent waited_ms=0 "
                "successful=true protocol_result=completed request_id=req-secret",
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "status=200 capacity=false retry_after=absent waited_ms=0 "
                "successful=false protocol_result=client_disconnect request_id=req-secret",
                "upstream_result lane=chatgpt-oauth model=gpt-5.6-luna "
                "status=200 capacity=false retry_after=absent waited_ms=0 "
                "successful=false protocol_result=missing_terminal request_id=req-secret",
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "status=200 capacity=false retry_after=absent waited_ms=0 "
                "successful=false protocol_result=inspection_limit "
                "sse_inspection_complete=false sse_malformed_json=false "
                "sse_terminal_seen=true",
                "Oct 17 cpa-admission[1]: upstream_result lane=claude-oauth "
                "model=claude-opus-5-5 status=503 capacity=false "
                "retry_after=30 waited_ms=3000 protocol_result=prompt-secret request_id=req-secret "
                "client_text=prompt-secret",
                "Oct 17 cpa-admission[1]: lane_reject lane=claude-oauth "
                "model=claude-haiku-5-5 reason=shared_upstream_capacity "
                "retry_after=30 request_id=req-secret",
            )
        )
        harness = (
            "import subprocess, sys, types\n"
            "fixture = sys.stdin.read()\n"
            "def fake_run(args, **kwargs):\n"
            "    assert args == ['journalctl', '-u', 'cpa-admission', "
            "'--since', '24 hours ago', '--no-pager']\n"
            "    return types.SimpleNamespace(returncode=0, stdout=fixture, stderr='')\n"
            "subprocess.run = fake_run\n" + script
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            input=fixture,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("admission_capacity_events_24h=2", result.stdout)
        self.assertIn("admission_capacity_statuses=200:1,429:1", result.stdout)
        self.assertIn(
            "admission_protocol_results=completed:1,not_completed:4,unknown:2",
            result.stdout,
        )
        self.assertIn(
            "chatgpt-oauth/gpt-6.1-sol/HTTP200/not_completed:1", result.stdout
        )
        self.assertIn("chatgpt-oauth/gpt-6-luna/HTTP200/not_completed:2", result.stdout)
        self.assertIn(
            "admission_protocol_reasons=capacity:1,client_disconnect:1,completed:1,"
            "inspection_limit:1,missing_terminal:1,unknown:2",
            result.stdout,
        )
        self.assertIn(
            "chatgpt-oauth/gpt-6-luna/HTTP200/client_disconnect:1", result.stdout
        )
        self.assertIn(
            "chatgpt-oauth/gpt-5.6-luna/HTTP200/missing_terminal:1", result.stdout
        )
        self.assertIn(
            "admission_upstream_429_503_details="
            "claude-oauth/claude-haiku-5-5/HTTP429/capacity:1,"
            "claude-oauth/claude-opus-5-5/HTTP503/noncapacity:1",
            result.stdout,
        )
        self.assertNotIn("req-secret", result.stdout)
        self.assertNotIn("prompt-secret", result.stdout)

    def test_doctor_admission_journal_aggregates_request_guard_refusals(self) -> None:
        source = read_guardrail_source()
        section = source.split('echo "==admission-journal-24h=="', 1)[1].split(
            'echo "==model-substitution=="', 1
        )[0]
        script = section.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        fixture = "\n".join(
            (
                "Oct 23 cpa-admission[1]: request_guard_refused stage=flock "
                "reason=maintenance_busy errno=11 request_id=req-secret",
                "Oct 23 cpa-admission[1]: request_guard_refused stage=flock "
                "reason=maintenance_busy errno=11 request_id=req-secret",
                "Oct 23 cpa-admission[1]: request_guard_refused stage=open "
                "reason=lock_unavailable errno=2",
                "Oct 23 cpa-admission[1]: request_guard_refused stage=flock "
                "reason=lock_error errno=9",
                "Oct 23 cpa-admission[1]: upstream_result lane=chatgpt-oauth "
                "model=gpt-6.1-sol status=503 capacity=true retry_after=30 "
                "waited_ms=1 successful=false request_id=req-secret",
            )
        )
        harness = (
            "import subprocess, sys, types\n"
            "fixture = sys.stdin.read()\n"
            "def fake_run(args, **kwargs):\n"
            "    return types.SimpleNamespace(returncode=0, stdout=fixture, stderr='')\n"
            "subprocess.run = fake_run\n" + script
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            input=fixture,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("admission_request_guard_refusals_24h=4", result.stdout)
        self.assertIn(
            "admission_request_guard_refusal_reasons="
            "flock/lock_error:1,flock/maintenance_busy:2,open/lock_unavailable:1",
            result.stdout,
        )
        # A refusal is not an upstream call: it must not inflate capacity counts.
        self.assertIn("admission_capacity_events_24h=1", result.stdout)
        self.assertNotIn("req-secret", result.stdout)

    def test_doctor_separates_text_no_text_and_unknown_first_text_turns(self) -> None:
        source = read_guardrail_source()
        section = source.split('echo "==admission-latency=="', 1)[1].split(
            'echo "==lane-hold=="', 1
        )[0]
        script = section.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        fixture = "\n".join(
            (
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=1000 successful=true protocol_result=completed "
                "sse_text_finish=stop sse_inspection_complete=true",
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=na successful=true protocol_result=completed "
                "sse_text_finish=reasoning_only "
                "sse_inspection_complete=true",
                "upstream_result lane=workbuddy-oauth model=deepseek-v4.1-flash "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=na successful=true protocol_result=completed "
                "sse_text_finish=tool_call "
                "sse_inspection_complete=true",
                "upstream_result lane=passthrough model=other "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=na successful=true protocol_result=completed "
                "sse_text_finish=na sse_inspection_complete=na",
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=na successful=true protocol_result=completed",
                "upstream_result lane=chatgpt-oauth model=gpt-6-luna "
                "waited_ms=0 upstream_header_ms=10 upstream_first_byte_ms=11 "
                "first_text_ms=na successful=false protocol_result=capacity "
                "sse_text_finish=na sse_inspection_complete=na",
            )
        )
        harness = (
            "import subprocess, sys, types\n"
            "fixture = sys.stdin.read()\n"
            "def fake_run(args, **kwargs):\n"
            "    return types.SimpleNamespace(returncode=0, stdout=fixture, stderr='')\n"
            "subprocess.run = fake_run\n" + script
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            input=fixture,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("first_text_ms_text_turns n=1", result.stdout)
        self.assertIn("first_text_ms_no_text_turns=3", result.stdout)
        self.assertIn("first_text_ms_unknown_turns=1", result.stdout)
        self.assertIn("first_text_ms_measurable_share_pct=20", result.stdout)

    def test_doctor_gateway_statuses_separates_maintenance_503(self) -> None:
        source = read_guardrail_source()
        section = source.split('echo "==gateway-statuses-current-log-24h=="', 1)[
            1
        ].split('echo "==admission-journal-24h=="', 1)[0]
        script = section.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        now = datetime.datetime.now(datetime.timezone.utc)

        def access_line(
            client: str,
            offset_s: int,
            body: str,
            request_time: str,
            upstream_time: str,
        ) -> str:
            stamp = (now - datetime.timedelta(seconds=offset_s)).strftime(
                "%d/%b/%Y:%H:%M:%S +0000"
            )
            return (
                f"{client} method=POST route=responses status=503 "
                f"request_time={request_time} upstream_status=503 "
                f"upstream_time={upstream_time} bytes={body} "
                f"limit_req=PASSED limit_conn=PASSED retry_after=absent "
                f"time=[{stamp}] auth_status=200 "
                f"upstream_header_time={upstream_time}"
            )

        lines = [
            access_line("144.34.229.116", 60 - index, "44", "0.002", "0.002")
            for index in range(6)
        ]
        # One observed refusal spent 0.646s client-visible wall time on a 2ms
        # admission reply; keying on upstream_time keeps it classified.
        lines.append(access_line("144.34.229.116", 61, "44", "0.646", "0.002"))
        lines += [
            access_line("203.0.113.9", 300 - index, "120", "0.010", "0.010")
            for index in range(5)
        ]
        # Authentication backend failures happen before the model upstream is
        # contacted. Keep them separate from provider 5xx and ordinary local 500.
        auth_failure = access_line("203.0.113.9", 20, "177", "0.522", "-")
        auth_failure = auth_failure.replace("status=503 ", "status=500 ", 1)
        auth_failure = auth_failure.replace("upstream_status=503", "upstream_status=-")
        auth_failure = auth_failure.replace("auth_status=200", "auth_status=502")
        lines.append(auth_failure)
        lines.append(auth_failure.replace("auth_status=502", "auth_status=200"))
        fixture = "\n".join(lines) + "\n"
        harness = (
            "import io, pathlib, sys\n"
            "fixture = sys.stdin.read()\n"
            "_orig_open = pathlib.Path.open\n"
            "def _fake_open(self, *args, **kwargs):\n"
            "    if self.name == 'cpa_gateway.access.log':\n"
            "        return io.StringIO(fixture)\n"
            "    return _orig_open(self, *args, **kwargs)\n"
            "pathlib.Path.open = _fake_open\n" + script
        )
        result = subprocess.run(
            [sys.executable, "-c", harness],
            input=fixture,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # The section emits the gateway-status JSON and then the retained
        # error-dump JSON; select the one under test rather than the last line.
        payload = next(
            json.loads(line)
            for line in result.stdout.splitlines()
            if "five_xx_local_vs_upstream" in line
        )
        self.assertEqual(
            payload["five_xx_local_vs_upstream"][
                "503/admission_maintenance_44B_upstream_lt_0_5s"
            ],
            7,
        )
        self.assertEqual(
            payload["five_xx_local_vs_upstream"]["503/fast_upstream_lt_0_5s"], 5
        )
        self.assertEqual(payload["client_503_maintenance_shape_total"], 7)
        self.assertEqual(
            payload["auth_subrequest_failures"], {"external/500/auth_502": 1}
        )
        self.assertEqual(
            payload["five_xx_local_vs_upstream"]["500/auth_subrequest_502"], 1
        )
        self.assertEqual(
            payload["five_xx_local_vs_upstream"]["500/mid_local_0_5_to_3s"], 1
        )
        self.assertEqual(
            payload["client_503_maintenance_shape_by_client_masked"],
            {"144.34.x.x": 7},
        )
        # The maintenance burst must not be reported as a client retry storm,
        # while the genuine cooldown burst keeps its cadence stats.
        self.assertNotIn("144.34.x.x", payload["client_503_retry_pattern"])
        self.assertEqual(payload["client_503_retry_pattern"]["203.0.x.x"]["n503"], 5)
        self.assertNotIn("144.34.229.116", result.stdout)

    def test_doctor_lane_hold_gates_configured_lanes_only(self) -> None:
        source = read_guardrail_source()
        self.assertIn('echo "==lane-hold=="', source)
        self.assertIn("mark_fail lane-hold", source)
        section = source.split('echo "==lane-hold=="', 1)[1].split(
            'echo "==model-substitution=="', 1
        )[0]
        script = section.split(
            'python3 - "$DIR" "/var/lib/cpa-admission" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]

        def state_body(hold: Any, not_before: float) -> str:
            return json.dumps(
                {"version": 1, "not_before": not_before, "hold_reason": hold}
            )

        def run_case(
            config_lanes: list[str],
            states: dict[str, str],
            config_text: str | None = None,
            healthz: dict[str, Any] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if config_text is None:
                    config_text = json.dumps(
                        {"lanes": [{"name": name} for name in config_lanes]}
                    )
                (root / "cpa-admission.json").write_text(config_text, encoding="utf-8")
                state_dir = root / "state"
                state_dir.mkdir()
                for filename, body in states.items():
                    (state_dir / filename).write_text(body, encoding="utf-8")
                if healthz is None:
                    healthz_url = (root / "no-healthz.json").as_uri()
                else:
                    healthz_path = root / "healthz.json"
                    healthz_path.write_text(json.dumps(healthz), encoding="utf-8")
                    healthz_url = healthz_path.as_uri()
                harness = (
                    "import sys\n"
                    f"sys.argv = ['lane-hold', {str(root)!r}, "
                    f"{str(state_dir)!r}, {healthz_url!r}]\n" + script
                )
                return subprocess.run(
                    [sys.executable, "-c", harness],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )

        healthy = run_case(
            [
                "chatgpt-oauth",
                "zhipu-coding-plan",
                "antigravity-oauth",
                "workbuddy-oauth",
            ],
            {
                "chatgpt-risk.json": state_body(None, 4102444800.0),
                "workbuddy-risk.json": state_body(None, 0.0),
                "antigravity-risk.json": state_body(None, 0.0),
            },
        )
        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        self.assertIn("lane-hold=OK", healthy.stdout)
        self.assertIn("lane-hold-chatgpt-oauth=OPEN cooldown_mirror_s=", healthy.stdout)
        self.assertIn("lane-hold-workbuddy-oauth=OPEN", healthy.stdout)

        held = run_case(
            ["chatgpt-oauth", "workbuddy-oauth"],
            {
                "chatgpt-risk.json": state_body(None, 0.0),
                "workbuddy-risk.json": state_body("retry_window_review_required", 0.0),
            },
        )
        self.assertEqual(held.returncode, 1, held.stdout)
        self.assertIn(
            "lane-hold-workbuddy-oauth=HELD reason=retry_window_review_required",
            held.stdout,
        )
        self.assertIn(
            "lane-hold=HELD workbuddy-oauth:retry_window_review_required",
            held.stdout,
        )

        tombstone = run_case(
            ["chatgpt-oauth", "workbuddy-oauth"],
            {
                "chatgpt-risk.json": state_body(None, 0.0),
                "workbuddy-risk.json": state_body(None, 0.0),
                "claude-risk.json": state_body("authentication_required", 0.0),
            },
        )
        self.assertEqual(tombstone.returncode, 0, tombstone.stderr)
        self.assertIn(
            "lane-hold-claude-oauth=HELD reason=authentication_required",
            tombstone.stdout,
        )
        self.assertIn(
            "lane-hold-claude-oauth-note=unconfigured-tombstone-inert",
            tombstone.stdout,
        )
        self.assertIn("lane-hold=OK", tombstone.stdout)

        unreadable = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": "{not json"},
        )
        self.assertEqual(unreadable.returncode, 1, unreadable.stdout)
        self.assertIn("lane-hold-chatgpt-oauth=UNREADABLE", unreadable.stdout)

        config_broken = run_case(["chatgpt-oauth"], {}, config_text="{not json")
        self.assertEqual(config_broken.returncode, 1, config_broken.stdout)
        self.assertIn("lane-hold-config=UNREADABLE", config_broken.stdout)

        # The risk file is written ahead of every upstream request, so a live
        # lane legitimately shows `unverified_request` on disk while inflight>0.
        # The running snapshot must decide, otherwise ordinary traffic fails the
        # strict gate (2026-10-22 false HELD).
        write_ahead = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": state_body("unverified_request", 0.0)},
            healthz={
                "lanes": {
                    "chatgpt-oauth": {
                        "state": {
                            "hold_reason": None,
                            "inflight": 1,
                            "cooldown_remaining": 0,
                        }
                    }
                }
            },
        )
        self.assertEqual(write_ahead.returncode, 0, write_ahead.stdout)
        self.assertIn(
            "lane-hold-chatgpt-oauth=OPEN note=write_ahead_inflight=1",
            write_ahead.stdout,
        )
        self.assertIn("lane-hold=OK", write_ahead.stdout)

        stale_marker = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": state_body("unverified_request", 0.0)},
            healthz={
                "lanes": {
                    "chatgpt-oauth": {
                        "state": {
                            "hold_reason": None,
                            "inflight": 0,
                            "cooldown_remaining": 0,
                        }
                    }
                }
            },
        )
        self.assertEqual(stale_marker.returncode, 0, stale_marker.stdout)
        self.assertIn(
            "lane-hold-chatgpt-oauth=OPEN note=stale_write_ahead_marker",
            stale_marker.stdout,
        )

        live_hold = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": state_body("unverified_request", 0.0)},
            healthz={
                "lanes": {
                    "chatgpt-oauth": {
                        "state": {
                            "hold_reason": "unverified_request",
                            "inflight": 0,
                            "cooldown_remaining": 0,
                        }
                    }
                }
            },
        )
        self.assertEqual(live_hold.returncode, 1, live_hold.stdout)
        self.assertIn(
            "lane-hold-chatgpt-oauth=HELD reason=unverified_request",
            live_hold.stdout,
        )

        disk_only = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": state_body("authentication_required", 0.0)},
            healthz={
                "lanes": {
                    "chatgpt-oauth": {
                        "state": {
                            "hold_reason": None,
                            "inflight": 0,
                            "cooldown_remaining": 0,
                        }
                    }
                }
            },
        )
        self.assertEqual(disk_only.returncode, 1, disk_only.stdout)
        self.assertIn(
            "lane-hold-chatgpt-oauth=HELD reason=authentication_required note=disk_only",
            disk_only.stdout,
        )

        # healthz unreachable keeps the crash-latch fail-closed behavior.
        unreachable = run_case(
            ["chatgpt-oauth"],
            {"chatgpt-risk.json": state_body("unverified_request", 0.0)},
        )
        self.assertEqual(unreachable.returncode, 1, unreachable.stdout)
        self.assertIn(
            "lane-hold-chatgpt-oauth=HELD reason=unverified_request",
            unreachable.stdout,
        )

    def test_doctor_workbuddy_config_gate_pins_deployed_policy(self) -> None:
        import yaml

        source = read_guardrail_source()
        self.assertIn('echo "==workbuddy-config=="', source)
        self.assertIn("mark_fail workbuddy-config", source)
        section = source.split('echo "==workbuddy-config=="', 1)[1].split(
            'echo "==model-substitution=="', 1
        )[0]
        script = section.split(
            'python3 - "$DIR/config.yaml" "$DIR/auth" "$WORKBUDDY_INTL_PREFIX" '
            "\"$INTL_DISABLED_EXPECTED\" <<'PY'\n",
            1,
        )[1].split("\nPY\n", 1)[0]
        # 2026-11-20: the same heredoc also pins the CN-lane rule that every
        # INTL WorkBuddy credential stays disabled while its plugin is closed
        # dark. The count comes from cpa_policy, so the harness passes it the
        # same way the doctor does instead of hard-coding an expectation.
        self.assertIn("count-intl-disabled", section)
        self.assertIn("WORKBUDDY_INTL_PREFIX=workbuddyai-", source)

        def run_case(
            config: dict[str, Any] | str,
            intl_disabled_expected: int = 0,
            auth_files: dict[str, Any] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.yaml"
                if isinstance(config, str):
                    path.write_text(config, encoding="utf-8")
                else:
                    path.write_text(yaml.safe_dump(config), encoding="utf-8")
                auth_dir = Path(tmp) / "auth"
                auth_dir.mkdir()
                for name, payload in (auth_files or {}).items():
                    target = auth_dir / name
                    if isinstance(payload, str):
                        target.write_text(payload, encoding="utf-8")
                    else:
                        target.write_text(json.dumps(payload), encoding="utf-8")
                harness = (
                    "import sys\n"
                    "sys.argv = ['workbuddy-config', "
                    + repr(str(path))
                    + ", "
                    + repr(str(auth_dir))
                    + ", 'workbuddyai-', "
                    + repr(str(intl_disabled_expected))
                    + "]\n"
                    + script
                )
                return subprocess.run(
                    [sys.executable, "-c", harness],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )

        def bridge_entries() -> list[dict[str, Any]]:
            return [
                {
                    "name": "deepseek-v4.1-flash-sg",
                    "alias": "deepseek-v4.1-flash-intl",
                    "fork": True,
                },
            ]

        def healthy_config(**overrides: Any) -> dict[str, Any]:
            cn_section = {
                "enabled": True,
                "checkin_auto": True,
                "growth_auto": False,
                "lifecycle_auto": False,
                "token_keepalive": False,
                "rotation_enabled": False,
                "scheduler_mode": "credits",
                "retry_on_4xx": 0,
            }
            cn_section.update(overrides)
            return {
                "plugins": {
                    "configs": {
                        "workbuddy-provider": cn_section,
                    }
                },
                "oauth-excluded-models": {
                    "workbuddy-provider": ["glm-5.3"],
                },
            }

        healthy = run_case(healthy_config())
        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        self.assertIn("workbuddy-config=OK", healthy.stdout)
        self.assertIn("workbuddy-checkin_auto=true", healthy.stdout)
        self.assertIn("workbuddy-growth_auto=false", healthy.stdout)
        self.assertIn("workbuddy-rotation_enabled=false", healthy.stdout)
        self.assertIn("workbuddy-scheduler_mode=credits", healthy.stdout)
        self.assertIn("workbuddy-retry_on_4xx=0", healthy.stdout)
        self.assertIn(
            "workbuddy-intl-plugin=absent note=uninstalled_20261126", healthy.stdout
        )
        self.assertIn("workbuddy-intl-alias=absent", healthy.stdout)
        self.assertIn(
            "workbuddy-excluded-workbuddy-provider=PRESENT n=1", healthy.stdout
        )
        self.assertIn("workbuddy-excluded-workbuddy-ai-provider=absent", healthy.stdout)
        self.assertIn("workbuddy-intl-settings=absent", healthy.stdout)

        legacy = run_case(
            {
                "plugins": {
                    "configs": {
                        "workbuddy": {"enabled": True},
                        "workbuddy-provider": {"enabled": True},
                    }
                }
            }
        )
        self.assertEqual(legacy.returncode, 1, legacy.stdout)
        self.assertIn(
            "workbuddy-config=DRIFT note=legacy_workbuddy_section_present",
            legacy.stdout,
        )

        wiped = run_case(
            {"plugins": {"configs": {"workbuddy-provider": {"enabled": True}}}}
        )
        self.assertEqual(wiped.returncode, 1, wiped.stdout)
        self.assertIn("workbuddy-checkin_auto=ABSENT expected=true", wiped.stdout)
        self.assertIn("workbuddy-growth_auto=ABSENT expected=false", wiped.stdout)
        self.assertIn("workbuddy-config=DRIFT", wiped.stdout)
        self.assertIn("scheduler_mode:absent", wiped.stdout)
        self.assertIn("retry_on_4xx:absent", wiped.stdout)
        self.assertIn("oauth_excluded_models:workbuddy-provider", wiped.stdout)

        for mode in (None, "off", "selected", True, ["credits"]):
            with self.subTest(scheduler_mode=mode):
                config = healthy_config()
                section_config = config["plugins"]["configs"]["workbuddy-provider"]
                if mode is None:
                    section_config.pop("scheduler_mode")
                else:
                    section_config["scheduler_mode"] = mode
                result = run_case(config)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("scheduler_mode:", result.stdout)
                self.assertIn("workbuddy-config=DRIFT", result.stdout)

        for retry_budget in (10, False, "0"):
            with self.subTest(retry_on_4xx=retry_budget):
                result = run_case(healthy_config(retry_on_4xx=retry_budget))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(
                    "workbuddy-retry_on_4xx=UNEXPECTED expected=0", result.stdout
                )

        growth_flip = run_case(healthy_config(growth_auto=True))
        self.assertEqual(growth_flip.returncode, 1, growth_flip.stdout)
        self.assertIn("workbuddy-growth_auto=true", growth_flip.stdout)
        self.assertIn("workbuddy-config=DRIFT growth_auto:true", growth_flip.stdout)

        rotation_flip = run_case(healthy_config(rotation_enabled=True))
        self.assertEqual(rotation_flip.returncode, 1, rotation_flip.stdout)
        self.assertIn(
            "workbuddy-config=DRIFT rotation_enabled:true", rotation_flip.stdout
        )

        # Uninstalled INTL contract: any leftover workbuddy-ai-provider section
        # under plugins.configs, oauth.model-alias, or oauth.excluded-models is drift.
        intl_present = healthy_config()
        intl_present["plugins"]["configs"]["workbuddy-ai-provider"] = {
            "enabled": False,
            "token_keepalive": False,
        }
        result = run_case(intl_present)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("intl_plugin:config_present", result.stdout)

        intl_alias = healthy_config()
        intl_alias["oauth-model-alias"] = {"workbuddy-ai-provider": bridge_entries()}
        result = run_case(intl_alias)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("oauth_model_alias:workbuddy-ai-provider_present", result.stdout)

        legacy_channel = healthy_config()
        legacy_channel["oauth-model-alias"] = {
            "workbuddy": [
                {"name": "deepseek-v4.1-flash", "alias": "deepseek-v4.1-flash-intl"}
            ]
        }
        result = run_case(legacy_channel)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "workbuddy-alias-legacy-channel=PRESENT note=remove", result.stdout
        )
        self.assertIn(
            "oauth_model_alias:legacy_workbuddy_channel_present", result.stdout
        )

        no_exclusions = healthy_config()
        del no_exclusions["oauth-excluded-models"]
        result = run_case(no_exclusions)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "workbuddy-excluded-workbuddy-provider=ABSENT note=realm_discovery_reopened",
            result.stdout,
        )

        disabled = run_case(
            {
                "plugins": {
                    "configs": {"workbuddy-provider": {"enabled": False}},
                }
            }
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertIn("workbuddy-config=DISABLED", disabled.stdout)

        unreadable = run_case("{not yaml: [")
        self.assertEqual(unreadable.returncode, 1, unreadable.stdout)
        self.assertIn("workbuddy-config=UNREADABLE", unreadable.stdout)

        # 2026-11-20 CN-lane protection. The INTL plugin is closed dark, but
        # its auth files stayed enabled and the plugin's credit sweep kept
        # reading them (an exhausted balance answers a bare 429). The guard is
        # armed only once the projection actually disabled a credential, so the
        # first deploy degrades to a readout instead of failing itself.
        intl_auth = {
            "workbuddyai-aaa.json": {"type": "workbuddy", "disabled": True},
            "workbuddyai-bbb.json": {"type": "workbuddy", "disabled": True},
            "workbuddy-ccc.json": {"type": "workbuddy", "disabled": True},
        }
        armed = run_case(healthy_config(), 1, intl_auth)
        self.assertEqual(armed.returncode, 0, armed.stdout)
        # Only the INTL prefix is counted; the CN-named file stays invisible.
        self.assertIn("workbuddy-intl-auth-files=2 disabled=2 enabled=0", armed.stdout)
        self.assertIn(
            "workbuddy-intl-auth-disabled-guard=armed expected=1", armed.stdout
        )
        self.assertIn("workbuddy-intl-auth-enabled=none", armed.stdout)

        # A re-enabled INTL credential is the regression the guard exists for.
        reopened = dict(intl_auth)
        reopened["workbuddyai-bbb.json"] = {"type": "workbuddy", "disabled": False}
        result = run_case(healthy_config(), 1, reopened)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("workbuddy-intl-auth-files=2 disabled=1 enabled=1", result.stdout)
        self.assertIn(
            "workbuddy-intl-auth-enabled=PRESENT workbuddyai-bbb.json", result.stdout
        )
        self.assertIn("intl_auth_enabled:workbuddyai-bbb.json", result.stdout)

        # A missing `disabled` key is not evidence of a closed credential.
        absent_flag = dict(intl_auth)
        absent_flag["workbuddyai-ccc.json"] = {"type": "workbuddy"}
        absent_flag["workbuddy-ccc.json"] = {"type": "workbuddy", "disabled": True}
        result = run_case(healthy_config(), 1, absent_flag)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("intl_auth_enabled:workbuddyai-ccc.json", result.stdout)

        # CN-named credentials are outside this rule: the guard counts only the
        # INTL prefix, so a CN account that happens to be enabled is not drift
        # for a closed INTL plugin.
        result = run_case(
            healthy_config(),
            1,
            {
                "workbuddyai-aaa.json": {"disabled": True},
                "workbuddyai-bbb.json": {"disabled": False},
                "workbuddy-ccc.json": {"disabled": False},
            },
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            "workbuddy-intl-auth-enabled=PRESENT workbuddyai-bbb.json", result.stdout
        )
        self.assertNotIn("workbuddy-ccc.json", result.stdout)

        # Before the projection lands the expectation is not armed: a healthy
        # closed-dark host reads as READOUT_ONLY and the reviewed deploy
        # transaction (whose precondition is a clean strict doctor) can run.
        readout = run_case(
            healthy_config(),
            1,
            {
                "workbuddyai-aaa.json": {"disabled": False},
                "workbuddyai-bbb.json": {"disabled": False},
            },
        )
        self.assertEqual(readout.returncode, 0, readout.stdout)
        self.assertIn(
            "workbuddy-intl-auth-disabled-guard=READOUT_ONLY expected=1 disabled=0",
            readout.stdout,
        )

        # Readout-only is distinguishable from armed drift, so a doctor reading
        # an unprojected host never reports a green guard it has not proven.
        self.assertIn("note=expectation_not_projected_yet", readout.stdout)
        self.assertNotIn("workbuddy-intl-auth-disabled-guard=armed", readout.stdout)

    def test_policy_counter_verb_is_reachable_before_projection(self) -> None:
        """The apply's precondition and the doctor's expectation are one number.

        The counter must answer as a CLI verb on the *previous* generation:
        the bootstrap apply runs before its own payload is deployed, so a
        helper defined after the entry point would raise NameError exactly then.
        """
        root = Path(__file__).parents[1]
        auth_dir = root / "scripts" / "remote"
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp)
            (auth / "workbuddyai-a.json").write_text(
                '{"disabled":true}', encoding="utf-8"
            )
            (auth / "workbuddyai-b.json").write_text(
                '{"disabled":false}', encoding="utf-8"
            )
            (auth / "workbuddyai-c.json").write_text(
                '{"type":"workbuddy"}', encoding="utf-8"
            )
            (auth / "workbuddy-d.json").write_text(
                '{"disabled":true}', encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(auth_dir / "cpa_policy.py"),
                    "count-intl-disabled",
                    str(auth),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1")
        # The definition has to precede the __main__ block for the same reason.
        source = (auth_dir / "cpa_policy.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("def count_workbuddy_intl_disabled"),
            source.index('if __name__ == "__main__":'),
        )

    def test_apply_projects_intl_disabled_with_readback(self) -> None:
        source = read_guardrail_source()
        apply_source = (
            Path(__file__).parents[1] / "scripts" / "remote" / "cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        # The apply refuses to guess: without the counter it cannot tell which
        # credentials must stay disabled, and rewriting auth bytes blind is
        # exactly the mistake the refresh rotation owns.
        self.assertIn("REFUSE intl_disabled_counter_unavailable", apply_source)
        # A baseline below the file total is legitimate (six of seven were
        # already disabled by the 2026-11-17 cutover); completeness is proven
        # by the readback, not by assuming the expectation starts at zero.
        self.assertNotIn("REFUSE intl_disabled_expectation_partial", apply_source)
        self.assertIn(
            'echo "INTL_AUTH_BASELINE files=$INTL_AUTH_TOTAL disabled_before=$INTL_EXPECTED_DISABLED"',
            apply_source,
        )
        # The precondition counts through THIS payload's module: a transaction
        # always runs one generation behind its own projection, so asking the
        # deployed generation for a verb it never had refuses the rollout that
        # would introduce it (observed on the first apply of this control).
        self.assertIn("python3 - \"$INTL_COUNTER_DIR\" <<'PY'", apply_source)
        self.assertIn('base64.b64decode("__CPA_POLICY_B64__")', apply_source)
        self.assertIn(
            'INTL_EXPECTED_DISABLED=$(python3 "$INTL_COUNTER_DIR/cpa_policy.py" count-intl-disabled "$DIR/auth"',
            apply_source,
        )
        # The readback also runs the payload's module copy (the deployed one is
        # still the previous generation on this rollout) and requires the whole
        # set to be disabled, not merely more than the baseline.
        self.assertIn("mktemp -d /tmp/cpa-intl-verify", apply_source)
        self.assertIn(
            'INTL_AFTER_DISABLED=$(python3 "$INTL_COUNTER_DIR/cpa_policy.py" count-intl-disabled "$DIR/auth"',
            apply_source,
        )
        self.assertIn(
            'if [ "$INTL_AFTER_DISABLED" != "$INTL_AUTH_TOTAL_AFTER" ]; then',
            apply_source,
        )
        self.assertIn("ROLLBACK intl_auth_disabled_readback", apply_source)
        # Auth files are snapshotted before the first rewrite and restored to
        # their original names, so rollback also covers this new write surface.
        self.assertIn(
            'cp -a "$intl_auth" "$BK/intl-auth/$intl_backup_index.json"', apply_source
        )
        self.assertIn(
            'cp -a "$BK/intl-auth/$intl_restore_index.json" "$DIR/auth/$intl_restore_name"',
            apply_source,
        )
        # The projection only ever lowers disabled to true and never touches
        # refresh material.
        projection = apply_source.split("# CN-lane protection (2026-11-20).", 1)[
            1
        ].split('updater_path = Path("/opt/cliproxyapi/auto-update.sh")', 1)[0]
        self.assertIn("re.subn(", projection)
        self.assertIn("(false|true|null|[0-9]+)", projection)
        self.assertIn("intl_text,", projection)
        self.assertNotIn("refreshToken", projection)
        self.assertNotIn("accessToken", projection)
        self.assertIn("atomic_write(intl_path, intl_new, 0o600)", projection)
        self.assertIn('"disabled":true}', projection)
        # Observability usage values are CPA defaults, pinned so an absent key
        # cannot silently return after a management reserialization.
        self.assertIn(
            'usage_after["usage-statistics-enabled"] = True',
            source,
        )
        self.assertIn(
            'usage_after["redis-usage-queue-retention-seconds"] = 3600',
            source,
        )

    def test_updater_schedule_split_is_projected_and_gated(self) -> None:
        root = Path(__file__).parents[1]
        source = read_guardrail_source()
        apply_source = (root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        wrapper = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        schedule = (root / "scripts/remote/cpa-updater-schedule-check.sh").read_text(
            encoding="utf-8"
        )
        # The gate ships inside the doctor payload and the apply transaction
        # owns the schedule, so both templates must carry the marker and the
        # wrapper must substitute it at both call sites.
        self.assertIn('echo "==updater-schedule=="', source)
        # Projected-then-reloaded: the doctor runs BEFORE -Apply, so a hard
        # gate here would block the very transaction that installs the split.
        # The section is a decision readout; the drift hashes stay strict.
        self.assertNotIn("mark_fail updater-schedule", source)
        self.assertIn('"$UPDATER_SCHEDULE_CHECK --status"', source)
        self.assertIn("'__CPA_UPDATER_SCHEDULE_CHECK_B64__'", source)
        self.assertEqual(
            wrapper.count('.Replace("__CPA_UPDATER_SCHEDULE_CHECK_B64__"'), 2
        )
        self.assertIn('"scripts/remote/cpa-updater-schedule-check.sh"', wrapper)
        self.assertIn(
            '"scripts/remote/cpa-updater-schedule-check.sh" = '
            "$updaterScheduleCheckSourceText",
            wrapper,
        )
        # The declared budget is injected into the embedded checker. The
        # source registry must retain the pre-substitution bytes so its hash
        # assertion still describes the file on disk.
        self.assertIn(
            "$updaterScheduleCheckText = $updaterScheduleCheckSourceText.Replace(",
            wrapper,
        )
        # Four anchors, one per background transaction, each owned by exactly
        # one job; a randomized delay that could spill into the next slot and
        # a start budget that could overrun it are both refused.
        for anchor in ("04:00", "04:10", "04:20", "04:30"):
            self.assertIn(anchor, schedule)
        self.assertIn("updater-schedule=OK anchors=4", schedule)
        self.assertIn("updater-schedule-jitter", schedule)
        self.assertIn("updater-schedule-budget", schedule)
        self.assertIn("--status", schedule)
        self.assertIn(
            "updater-schedule-compose=UNVERIFIED reason=no_docker_or_compose", schedule
        )
        # Read the loaded per-unit property before the global default. systemd
        # can render infinity or a duration differently between versions, so
        # both coordinate orders are queried and their results normalised.
        self.assertIn(
            "systemctl show -p TimeoutStartUSec --value cliproxyapi-update.service",
            schedule,
        )
        self.assertIn("systemctl show -p DefaultTimeoutStartUSec --value", schedule)
        self.assertIn("existing_default_seconds", schedule)
        self.assertIn("property_seconds", schedule)
        self.assertIn(
            "systemctl show -p TimeoutStartUSec --value cliproxyapi-update.service",
            schedule,
        )

        # Apply projects the timer between the timeout drop-in and the
        # daemon-reload that activates it, and backs up the original unit.
        # 2026-11-20: this assertion used to embed the mangled literal and so
        # "passed" while the apply projected OnCalendar=-*-*-* — systemd then
        # loaded the unit with no trigger at all. Assert the exact schedule the
        # tool must write, not a copy of whatever the script currently says.
        SIX_FIELD_CALENDAR = "*-*-* 04:00:00"
        render_timer = (
            'python3 - "$UPDATER_TIMER_UNIT" '
            "'__UPDATER_TIMER_ONCALENDAR__' "
            "'__UPDATER_TIMER_RANDOM_DELAY__' <<'PY' || {"
        )
        self.assertIn(render_timer, apply_source)
        timer_projection = apply_source.split(
            render_timer,
            1,
        )[1].split("\nPY\n", 1)[0]
        self.assertIn('stripped == "[Timer]"', timer_projection)
        self.assertIn('"OnCalendar=" + target_oncalendar', timer_projection)
        self.assertIn('"RandomizedDelaySec=" + target_randomized', timer_projection)
        self.assertIn("updater timer unit has no [Timer] section", timer_projection)
        self.assertIn("ROLLBACK updater_timer_slot_split", apply_source)
        self.assertIn(
            'cp -a "$UPDATER_TIMER_UNIT" "$BK/cliproxyapi-update.timer"', apply_source
        )
        self.assertIn(
            'cp -a "$BK/cliproxyapi-update.timer" "$UPDATER_TIMER_UNIT"', apply_source
        )
        apply_projection_at = apply_source.index("ROLLBACK updater_timer_slot_split")
        daemon_reload_at = apply_source.index("ROLLBACK admission_daemon_reload")
        self.assertLess(apply_projection_at, daemon_reload_at)
        effective_readback_at = apply_source.index(
            "ROLLBACK updater_timer_effective_readback delay="
        )
        self.assertLess(daemon_reload_at, effective_readback_at)
        self.assertIn(
            'systemctl show "$UPDATER_TIMER_UNIT" -p TimersCalendar --value',
            apply_source,
        )
        self.assertIn(
            'systemctl show "$UPDATER_TIMER_UNIT" -p RandomizedDelayUSec --value',
            apply_source,
        )
        self.assertIn(
            "__UPDATER_TIMER_RANDOM_DELAY_USEC__|__UPDATER_TIMER_RANDOM_DELAY_SHORT__)",
            apply_source,
        )

        # The rewrite is key-order preserving and idempotent: run the embedded
        # block against a stock unit and against an already-projected one.
        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / "cliproxyapi-update.timer"
            blocked = (
                "[Timer]\nOnCalendar="
                + SIX_FIELD_CALENDAR
                + "\nRandomizedDelaySec=30min\nPersistent=true\n"
            )
            for payload in (
                "[Unit]\nDescription=t\n\n"
                + blocked
                + "\n[Install]\nWantedBy=timers.target\n",
                "[Unit]\nDescription=t\n\n"
                + blocked.replace("30min", "300")
                + "\n[Install]\nWantedBy=timers.target\n",
            ):
                unit.write_text(payload, encoding="utf-8")
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        timer_projection.strip(),
                        str(unit),
                        "*-*-* 04:00:00",
                        "300",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = unit.read_text(encoding="utf-8")
                self.assertIn("OnCalendar=*-*-* 04:00:00", rendered)
                self.assertIn("RandomizedDelaySec=300", rendered)
                self.assertEqual(rendered.count("RandomizedDelaySec="), 1)
                self.assertEqual(rendered.count("OnCalendar="), 1)
                self.assertIn("Persistent=true", rendered)
                self.assertIn("WantedBy=timers.target", rendered)

            unit.write_text("[Unit]\nDescription=t\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    timer_projection.strip(),
                    str(unit),
                    "*-*-* 04:00:00",
                    "300",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("no [Timer] section", result.stderr)

    def test_apply_regenerates_antigravity_sensitive_words(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        source = read_guardrail_source()
        payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = 'oauth_block_after = config_after.setdefault("oauth", {})\n'
        block += payload.split(block, 1)[1].split(
            'oauth_exclusions = oauth_block_after.get("excluded-models")', 1
        )[0]
        script = (
            "import json, sys\nconfig_after = json.loads(sys.stdin.read())\n"
            + block
            + "print(json.dumps(config_after))\n"
        )
        fixtures: list[dict[str, Any]] = [
            {},
            {"oauth": {"providers": {}}},
            {
                "oauth": {
                    "providers": {
                        "antigravity": {
                            "antigravity-credits": False,
                            "sensitive-words": ["obsolete"],
                        },
                        "codex": {"preserve": True},
                    },
                    "excluded-models": {"antigravity": ["other-model"]},
                },
                "unrelated": {"preserve": True},
            },
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    input=json.dumps(fixture),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                generated = json.loads(result.stdout)
                expected = json.loads(json.dumps(fixture))
                expected.setdefault("oauth", {})["auth-auto-refresh-workers"] = policy[
                    "EXPECTED_CONFIG_PATHS"
                ][("oauth", "auth-auto-refresh-workers")]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["sensitive-words"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "sensitive-words")
                ]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["sensitive-words-replace"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "sensitive-words-replace")
                ]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["sensitive-patterns-replace"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "sensitive-patterns-replace")
                ]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["tos-pause-window"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "tos-pause-window")
                ]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["per-credential-daily-cap"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "per-credential-daily-cap")
                ]
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["connection-pool"] = {
                    "enabled": policy["EXPECTED_CONFIG_PATHS"][
                        (
                            "oauth",
                            "providers",
                            "antigravity",
                            "connection-pool",
                            "enabled",
                        )
                    ],
                    "idle-conn-timeout": policy["EXPECTED_CONFIG_PATHS"][
                        (
                            "oauth",
                            "providers",
                            "antigravity",
                            "connection-pool",
                            "idle-conn-timeout",
                        )
                    ],
                    "max-idle-conns-per-host": policy["EXPECTED_CONFIG_PATHS"][
                        (
                            "oauth",
                            "providers",
                            "antigravity",
                            "connection-pool",
                            "max-idle-conns-per-host",
                        )
                    ],
                }
                self.assertEqual(generated, expected)

        for fixture in (
            {"oauth": None},
            {"oauth": {"providers": []}},
            {"oauth": {"providers": {"antigravity": "invalid"}}},
            {"oauth": {"providers": {"antigravity": {"connection-pool": "invalid"}}}},
        ):
            with self.subTest(invalid=fixture):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    input=json.dumps(fixture),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("REFUSE", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_apply_regenerates_oauth_request_scoped_errors(self) -> None:
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        source = read_guardrail_source()
        payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        start_marker = 'oauth_request_scoped_after = oauth_block_after.get("request-scoped-errors")\n'
        end_marker = "\n# The requests.payload block is owned by cpa_policy:"
        block = start_marker + payload.split(start_marker, 1)[1].split(end_marker, 1)[0]
        script = (
            "import json, sys\noauth_block_after = json.loads(sys.stdin.read())\n"
            + block
            + "print(json.dumps(oauth_block_after))\n"
        )
        fixtures: list[dict[str, Any]] = [
            {},
            {"request-scoped-errors": None},
            {
                "request-scoped-errors": {
                    "antigravity": [
                        {"status": 429, "match": ["stale"], "action": "continue"}
                    ],
                    "vertex": [
                        {
                            "status": 400,
                            "match": ["maximum_context_length"],
                            "action": "stop",
                        }
                    ],
                }
            },
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    input=json.dumps(fixture),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                generated = json.loads(result.stdout)
                expected = json.loads(json.dumps(fixture))
                expected_rse = expected.get("request-scoped-errors") or {}
                expected_rse["antigravity"] = policy[
                    "EXPECTED_OAUTH_REQUEST_SCOPED_ERRORS"
                ]["antigravity"]
                expected_rse["codex"] = policy["EXPECTED_OAUTH_REQUEST_SCOPED_ERRORS"][
                    "codex"
                ]
                expected["request-scoped-errors"] = expected_rse
                self.assertEqual(generated, expected)

        invalid = {"request-scoped-errors": ["not-a-mapping"]}
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(invalid),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REFUSE", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_doctor_policy_summary_never_selects_management_secret(self) -> None:
        source = read_guardrail_source()
        block = source.split('echo "==cpa-policy=="', 1)[1].split(
            'echo "==models-configured=="', 1
        )[0]
        self.assertNotIn("secret-key:", block)

    def test_template_loader_preserves_payloads_and_tracks_all_drift_sources(
        self,
    ) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell 7 is unavailable")
        root = Path(__file__).parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        # Execute the real preparation/loader code in an isolated fixture.
        # The shared transport dependency must remain importable.
        prefix = _guardrail_preparation_fixture(source)
        prefix = (
            "$project_environment_path = '"
            + str(root / "scripts/lib/project_environment.ps1").replace("'", "''")
            + "'\n$scriptDir = '"
            + str(root / "scripts/remote").replace("'", "''")
            + "'\n$repoRoot = '"
            + str(root).replace("'", "''")
            + "'\n"
            + prefix
        )
        trailer = """
$result = [ordered]@{}
foreach ($entry in $guardrailTemplates.GetEnumerator()) {
  $result[$entry.Key] = @{
    payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-CpaGuardrailTemplate -Name $entry.Key)))
    hash = $projectionSourceHashes[$entry.Value]
    tracked = $projectionSourcePaths -contains $entry.Value
  }
}
$doctorTargets = [ordered]@{}
$doctorHashes = [ordered]@{}
foreach ($entry in $projectionDoctorTargets.GetEnumerator()) {
  $doctorTargets[$entry.Key] = $entry.Value
  $doctorHashes[$entry.Key] = $projectionHeadSha256[$entry.Key]
}
$result["projection"] = @{
  source_paths = @($projectionSourcePaths)
  doctor_targets = $doctorTargets
  doctor_hashes = $doctorHashes
  hash_pairs = $projectionHashPairs
}
$result | ConvertTo-Json -Depth 6 -Compress
"""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            scripts = fixture / "scripts"
            shutil.copytree(root / ".git", fixture / ".git")
            scripts.mkdir(parents=True)
            shutil.copy2(
                root / "scripts/cpa-updater-timer-contract.ps1",
                scripts / "cpa-updater-timer-contract.ps1",
            )
            shutil.copytree(root / "scripts/remote", scripts / "remote")
            shutil.copytree(root / "scripts/lib", scripts / "lib")
            driver_prefix = prefix
            (fixture / "connect.ps1").write_text(
                "throw 'SSH MUST NOT RUN'\n", encoding="utf-8"
            )
            driver = fixture / "prepare.ps1"
            driver.write_text(driver_prefix + trailer, encoding="utf-8")
            driver_env = os.environ.copy()
            driver_env["VPS_TEST_SCRIPTS"] = str(scripts)
            driver_env["VPS_TEST_ROOT"] = str(fixture)

            def run() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [pwsh, "-NoProfile", "-File", str(driver)],
                    env=driver_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    # pwsh writes stderr in the console codepage (GBK on
                    # zh-CN Windows); a strict decode would kill the reader
                    # thread mid-test.
                    errors="replace",
                    timeout=20,
                    check=False,
                )

            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = json.loads(result.stdout)
            projection = loaded.pop("projection")
            self.assertEqual(len(loaded), 5)
            helper = (
                (root / "scripts/lib/proxy_tls_contract.ps1")
                .read_text(encoding="utf-8")
                .split("return @'\n", 1)[1]
                .split("\n'@", 1)[0]
            )
            for name, record in loaded.items():
                payload = (scripts / "remote" / f"cpa-guardrail-{name}.sh").read_bytes()
                source_bytes = payload.replace(b"\r\n", b"\n")
                expected_bytes = source_bytes.replace(
                    b"__VERIFIED_TLS_HELPER__", helper.encode()
                )
                self.assertEqual(
                    base64.b64decode(record["payload"]),
                    expected_bytes.removesuffix(b"\n"),
                )
                self.assertEqual(
                    record["hash"],
                    hashlib.sha256(source_bytes).hexdigest(),
                )
                self.assertTrue(record["tracked"])

            expected_targets = {
                "scripts/remote/cpa-auto-update.sh": "/opt/cliproxyapi/auto-update.sh",
                "scripts/remote/cpa-health.py": "/opt/cliproxyapi/cpa-health.py",
                "scripts/remote/cpa_health_runtime.py": "/opt/cliproxyapi/cpa_health_runtime.py",
                "scripts/remote/cpa_policy.py": "/opt/cliproxyapi/cpa_policy.py",
                "scripts/remote/cpa_provider_routes.json": "/opt/cliproxyapi/cpa_provider_routes.json",
                "scripts/remote/cpa-admission.py": "/opt/cliproxyapi/cpa-admission.py",
                "scripts/remote/cpa-admission-launch.sh": "/opt/cliproxyapi/cpa-admission-launch.sh",
                "scripts/remote/cpa_admission_bootstrap.py": "/usr/local/libexec/cpa_admission_bootstrap.py",
                "scripts/remote/cpa_request_guard.py": "/usr/local/libexec/cpa_request_guard.py",
                "scripts/remote/cpa-admission.json": "/opt/cliproxyapi/cpa-admission.json",
                "scripts/remote/cpa-admission.service": "/etc/systemd/system/cpa-admission.service",
                "scripts/remote/cpa-admission-integrity-check.sh": "/usr/local/libexec/cpa-admission-integrity-check",
                "scripts/remote/cpa-admission-integrity.conf": "/etc/systemd/system/cpa-admission.service.d/10-integrity.conf",
                "scripts/remote/cpa-admission-integrity-pin.txt": "/etc/vps-ssh-launcher/cpa-admission.sha256",
                "scripts/remote/cpa-update-timeout.conf": "/etc/systemd/system/cliproxyapi-update.service.d/cpa-update-timeout.conf",
                "scripts/remote/cpa-fail2ban-filter.conf": "/etc/fail2ban/filter.d/cpa-gateway.conf",
                "scripts/remote/cpa-fail2ban-jail.conf": "/etc/fail2ban/jail.d/cpa-gateway.conf",
            }
            self.assertEqual(projection["doctor_targets"], expected_targets)
            self.assertEqual(set(projection["doctor_hashes"]), set(expected_targets))
            pairs = dict(
                pair.split("=", 1) for pair in projection["hash_pairs"].split()
            )
            self.assertEqual(
                pairs,
                {
                    destination: projection["doctor_hashes"][source]
                    for source, destination in expected_targets.items()
                },
            )
            self.assertTrue(
                set(expected_targets).issubset(set(projection["source_paths"]))
            )
            admission_hash = projection["doctor_hashes"][
                "scripts/remote/cpa-admission.py"
            ]
            raw_guard = (root / "scripts/remote/cpa_request_guard.py").read_text(
                encoding="utf-8"
            )
            expected_guard_hash = hashlib.sha256(
                raw_guard.replace("__SOURCE_SHA256__", admission_hash).encode()
            ).hexdigest()
            self.assertEqual(
                projection["doctor_hashes"]["scripts/remote/cpa_request_guard.py"],
                expected_guard_hash,
            )

            missing = scripts / "remote/cpa-guardrail-doctor.sh"
            missing.unlink()
            self.assertNotEqual(run().returncode, 0)
            missing.write_text("\n", encoding="utf-8")
            empty = run()
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("CPA guardrail template doctor is empty", empty.stderr)

    def test_updater_timeout_dropin_is_a_tracked_projection_with_clean_tree_actions(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        apply_script = (root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        # The drop-in is repo-owned: it must be projected, hashed, HEAD-anchored
        # in the doctor drift list, and recoverable from the apply backup.
        self.assertIn('"scripts/remote/cpa-update-timeout.conf"', source)
        self.assertIn(
            '"scripts/remote/cpa-update-timeout.conf" = '
            '"/etc/systemd/system/cliproxyapi-update.service.d/'
            'cpa-update-timeout.conf"',
            source,
        )
        self.assertIn(
            '"$($entry.Value)=$($projectionHeadSha256[$entry.Key])"',
            source,
        )
        self.assertIn("__CPA_UPDATER_TIMEOUT_CONF_B64__", source)
        self.assertIn("__CPA_UPDATER_TIMEOUT_CONF_SHA256__", source)
        self.assertIn(
            'write_base64_file "__CPA_UPDATER_TIMEOUT_CONF_B64__" "$UPDATER_TIMEOUT_DROPIN" 644',
            apply_script,
        )
        self.assertIn("$BK/cpa-update-timeout.conf.missing", apply_script)
        self.assertIn(
            'cp -a "$BK/cpa-update-timeout.conf" "$UPDATER_TIMEOUT_DROPIN"',
            apply_script,
        )
        self.assertIn('PROJECTION_COMMIT="__CPA_PROJECTION_COMMIT__"', apply_script)
        self.assertIn(
            '"__CPA_PROJECTION_COMMIT__",\n  $projectionCommit',
            source,
        )
        self.assertIn("$BK/projected-sources/SHA256SUMS", apply_script)
        self.assertIn("$BK/projection-sources.txt", apply_script)
        self.assertIn("projection_source_scan_dir=", apply_script)
        self.assertIn("projection_source_scan=", apply_script)
        self.assertIn('if [ ! -f "$projection_source_scan" ]; then', apply_script)
        self.assertIn('rm -rf -- "$projection_source_scan_dir"', apply_script)
        source_scan = apply_script[
            apply_script.index("projection_source_scan_dir=") : apply_script.index(
                "ROLLBACK_DONE=0"
            )
        ]
        self.assertIn('if [ -L "$projection_source_scan_dir" ]', source_scan)
        self.assertIn(
            "PATH= && export PATH &&\n"
            '  /usr/bin/grep -Eqi "api[-_]?key" SHA256SUMS\n'
            ") || scan_status=$?\n",
            source_scan,
        )
        self.assertIn('if [ "$scan_status" = 0 ]; then', source_scan)
        self.assertIn("API-key material", source_scan)
        self.assertIn('if [ "$scan_status" != 1 ]; then', source_scan)
        self.assertEqual(source_scan.count("exit 4"), 4)
        self.assertNotIn('rm -rf -- "$BK"', source_scan)
        self.assertNotIn('rm -rf -- "$BK"', apply_script)
        self.assertIn(
            "printf '%s' '__CPA_PROVIDER_ROUTES_B64__' | base64 -d",
            apply_script,
        )
        # Every action that embeds repo-derived bytes (route manifest, policy,
        # projections) must require a clean tree, not only -Apply and the
        # claude quarantine pair.
        actions_block = source.split("$payloadEmbeddingActions = @(", 1)[1].split(
            ")", 1
        )[0]
        for action in (
            "$Apply",
            "$DeactivateOAuthLuna",
            "$QuarantineOAuthLuna",
            "$RestoreOAuthLuna",
            "$QuarantineAntigravity",
            "$RestoreAntigravity",
            "$QuarantineClaudeOAuth",
            "$RestoreClaudeOAuth",
        ):
            self.assertIn(action, actions_block)

    def test_admission_integrity_check_refuses_on_every_broken_branch(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("Bash unavailable")
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/remote/cpa-admission-integrity-check.sh").read_text(
            encoding="utf-8"
        )
        script = (
            source.replace(
                "TARGET=/opt/cliproxyapi/cpa-admission.py", "TARGET=./target"
            )
            .replace("PIN=/etc/vps-ssh-launcher/cpa-admission.sha256", "PIN=./pin")
            .replace("UNIT=/etc/systemd/system/cpa-admission.service", "UNIT=./unit")
        )
        body = "payload for the pinned admission generation\n"
        digest = hashlib.sha256(body.encode()).hexdigest()
        for name, target_body, pin_body, expected_rc, marker in (
            ("ok", body, digest, 0, "ADMISSION_INTEGRITY_OK"),
            ("missing_target", None, digest, 1, "missing_target_or_pin_or_unit"),
            ("missing_launcher", body, digest, 1, "broker_compatible_launcher_missing"),
            ("invalid_pin", body, "not-a-sha", 1, "invalid_pin"),
            ("hash_mismatch", body, "f" * 64, 1, "hash_mismatch"),
            ("guard_generation_mismatch", body, digest, 1, "guard_generation_mismatch"),
        ):
            with self.subTest(branch=name), tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                if target_body is not None:
                    # write_bytes: text mode would translate \n to \r\n on
                    # Windows and break the digest the pin carries.
                    (workdir / "target").write_bytes(target_body.encode())
                if pin_body is not None:
                    (workdir / "pin").write_bytes((pin_body + "\n").encode())
                unit_body = (
                    "Environment=VPS_SSH_LAUNCHER_ADMISSION_PROCESS_MARKER="
                    "/run/cpa-admission/process\n"
                )
                if name != "missing_launcher":
                    unit_body += "ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh\n"
                (workdir / "unit").write_text(unit_body, encoding="utf-8")
                if name == "guard_generation_mismatch":
                    guard_dir = workdir / "libexec"
                    guard_dir.mkdir()
                    (guard_dir / "cpa_request_guard.py").write_text(
                        'SOURCE_SHA256 = "0" * 64\n', encoding="utf-8"
                    )
                else:
                    guard_dir = workdir / "libexec"
                    guard_dir.mkdir(exist_ok=True)
                    (guard_dir / "cpa_request_guard.py").write_text(
                        f'SOURCE_SHA256 = "{digest}"\n', encoding="utf-8"
                    )
                completed = subprocess.run(
                    self._bash_command(
                        bash, "-c", script.replace("/usr/local/libexec", "./libexec")
                    ),
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, expected_rc, completed.stderr)
                # REFUSE markers go to stderr, the OK marker to stdout.
                self.assertIn(marker, completed.stdout + completed.stderr)

    def test_strict_doctor_entrypoint_uses_chunked_transport_without_network(
        self,
    ) -> None:
        """Exercise the real PowerShell entrypoint while isolating its SSH child."""
        pwsh = shutil.which("pwsh")
        bash = self._resolve_bash()
        if pwsh is None or bash is None:
            self.skipTest("PowerShell 7 and Bash are required for transport simulation")

        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        # The transport is the shared lib's; the projector must ride it with
        # the historical remote temp prefix and the idle-timeout mapping, and
        # must not keep a private copy of the chunking algorithm.
        self.assertIn("Invoke-LauncherRemoteCommand", source)
        self.assertIn('RemoteTempPrefix = "cpa-guardrails"', source)
        self.assertIn("IdleTimeoutSeconds = $CommandTimeout", source)
        self.assertIn('[string]$LauncherEntryPointOverride = ""', source)
        self.assertNotIn("$chunkSize = 12000", source)
        # The remote side is emulated by a disposable launcher next to the
        # project configuration. The shared transport still launches a real
        # Python process with the production argv; only that explicit child
        # entrypoint is replaced. Clear inherited startup hooks so an
        # unexpected child cannot follow this fixture to a real host.
        fake_remote = r"""
import base64
import os
import re
import subprocess
import sys
with open(os.environ["CPA_TEST_TRANSPORT_RAW"], "a", encoding="utf-8") as raw:
    raw.write("START=" + repr(sys.argv) + "\n")
try:
  if len(sys.argv) > 1 and "run" in sys.argv and "--command" in sys.argv:
    open(os.environ["CPA_TEST_DEBUG_MARKER"], "w").close()
    # Keep the simulation hook in this launcher child only. The real launcher
    # may start unrelated Python children that must not inherit the hook.
    os.environ.pop("PYTHONPATH", None)
    command_index = sys.argv.index("--command") + 1
    command = sys.argv[command_index]
    log_path = os.environ["CPA_TEST_TRANSPORT_LOG"]
    state_path = os.environ["CPA_TEST_TRANSPORT_STATE"]
    # A chunk append can contain a quoted "| base64 -d | bash" payload;
    # only a complete outer expression is executable single-shot syntax.
    if re.fullmatch(
        r"printf %s [A-Za-z0-9+/=]+ \| base64 -d \| bash",
        command,
    ):
        completed = subprocess.run(
            [os.environ["CPA_TEST_BASH"], "-c", command],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            print("SIMULATED_BASH_SYNTAX=PASS")
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("single\n")
            raise SystemExit(0)
    operation = ""
    script = ""
    command = command.rstrip("\r\n")
    m = re.fullmatch(r"umask 077; : > '([^']+)'; chmod 600 '([^']+)'", command)
    if m:
        open(state_path, "w").close()
        operation = "create"
    else:
        m = re.fullmatch(r"printf %s '([A-Za-z0-9+/=]+)' >> '([^']+)'", command)
        if m:
            with open(state_path, "a", encoding="ascii") as handle:
                handle.write(m.group(1))
            operation = "chunk"
        else:
            m = re.fullmatch(
                r".*; set -o pipefail; base64 -d -- '([^']+)' \| bash; "
                r"rc=\$\?; rm -f -- \'[^\']+\'; exit \$rc",
                command,
            )
            if m:
                with open(state_path, encoding="ascii") as handle:
                    script = base64.b64decode(handle.read()).decode("utf-8")
                operation = "commit"
            else:
                m = re.fullmatch(
                    r"printf %s ([A-Za-z0-9+/=]+) \| base64 -d "
                    r"\| (?:env -u BASH_ENV )?bash",
                    command,
                )
                if m:
                    outer = base64.b64decode(m.group(1)).decode("utf-8")
                    if outer.endswith("\nREMOTE_COMMIT"):
                        framed = outer[: -len("\nREMOTE_COMMIT")].split("\n")
                        counts = [
                            line
                            for line in framed
                            if line.startswith("# chunks=")
                        ]
                        count = int(counts[0].split("=", 1)[1])
                        script = base64.b64decode(
                            "".join(framed[-count:])
                        ).decode("utf-8")
                    else:
                        script = outer
                    operation = "single"
    if not operation:
        print("Unexpected remote command shape in transport simulation.",
              file=sys.stderr)
        os._exit(3)
    if operation in ("commit", "single"):
        probe = subprocess.run(
            [os.environ["CPA_TEST_BASH"], "-n"],
            input=script,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if probe.returncode != 0:
            print("Bash rejected decoded doctor: " + probe.stderr,
                  file=sys.stderr)
            os._exit(4)
        print("SIMULATED_BASH_SYNTAX=PASS")
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(operation + "\n")
    sys.stdout.flush()
    # Report a normal completion to the shared capture. os._exit here can
    # discard unflushed .NET pipe data on Windows.
except Exception as exc:
    print("TRANSPORT_SIMULATION_EXCEPTION: " + type(exc).__name__, file=sys.stderr)
    os._exit(5)
"""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            (fixture / "ssh_tool.py").write_text(fake_remote, encoding="utf-8")
            remote = root / "scripts/remote/cpa-guardrail-doctor.sh"
            payload = remote.read_text(encoding="utf-8")
            if len(payload.encode("utf-8")) <= 12000:
                self.fail("The doctor payload must exercise the staged transport.")
            driver = root / "scripts/cpa_bwg_guardrails.ps1"
            operations = fixture / "transport-operations.txt"
            process_env = os.environ.copy()
            process_env.update(
                {
                    "CPA_TEST_TRANSPORT_LOG": str(operations),
                    "CPA_TEST_TRANSPORT_STATE": str(fixture / "transport-state.b64"),
                    "CPA_TEST_TRANSPORT_RAW": str(fixture / "transport-raw.txt"),
                    "CPA_TEST_BASH": bash,
                    "CPA_TEST_DEBUG_MARKER": str(fixture / "launcher-started.txt"),
                }
            )
            # The shared transport must work without a local config. Clear
            # inherited selectors that could choose a real host or interpreter.
            process_env.pop("VPS_SSH_LAUNCHER_PYTHON", None)
            process_env.pop("VPS_SSH_LAUNCHER_CONFIG", None)
            process_env.pop("PYTHONPATH", None)
            try:
                result = subprocess.run(
                    [
                        pwsh,
                        "-NoProfile",
                        "-File",
                        str(driver),
                        "-Profile",
                        "bwg",
                        "-LauncherEntryPointOverride",
                        str(fixture / "ssh_tool.py"),
                    ],
                    cwd=root,
                    env=process_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                diagnostics = (
                    operations.read_text(encoding="utf-8-sig")
                    if operations.exists()
                    else "NO_TRANSPORT_OPERATIONS"
                )
                self.fail(
                    f"transport simulation timed out; OPERATIONS={diagnostics!r}\n{exc}"
                )
            if result.returncode:
                diagnostics = (
                    operations.read_text(encoding="utf-8-sig")
                    if operations.exists()
                    else "NO_TRANSPORT_OPERATIONS"
                )
                raw = (
                    (fixture / "transport-raw.txt").read_text(encoding="utf-8")
                    if (fixture / "transport-raw.txt").exists()
                    else "NO_SITECUSTOMIZE_ARGV"
                )
                self.fail(
                    f"{result.stdout}\n{result.stderr}\n"
                    f"OPERATIONS={diagnostics}\nRAW={raw}"
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            recorded = operations.read_text(encoding="utf-8-sig").splitlines()
            # The doctor payload far exceeds the Win32 single-invocation
            # budget, so the driver must take the staged path: one create,
            # one chunk per bounded slice, one commit.
            self.assertEqual(recorded[0], "create")
            self.assertEqual(recorded[-1], "commit")
            self.assertTrue(all(op == "chunk" for op in recorded[1:-1]))
            self.assertGreaterEqual(len(recorded), 4)

    def test_cpa_guardrails_freezes_public_data_plane_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dispatcher = read_guardrail_source()
        # This contract belongs to the templates. Keep its occurrence counts
        # tied to those payloads when the local projection dispatcher grows.
        templates = "\n".join(
            (repo_root / f"scripts/remote/cpa-guardrail-{name}.sh").read_text(
                encoding="utf-8"
            )
            for name in (
                "doctor",
                "rotate-path",
                "deactivate-oauth-luna",
                "quarantine-oauth-luna",
                "apply",
            )
        )
        text = dispatcher + "\n" + templates

        # Authorization gates: every remote write is behind an explicit
        # switch. Five switches write remote state: -Apply (projection),
        # -RotatePath (capability path), -QuarantineOAuthLuna and
        # -RestoreOAuthLuna (oauth-excluded-models + marker), and
        # -DeactivateOAuthLuna (irreversible credential destruction). The
        # doctor is a blocking contract, not an observation.
        self.assertIn("[switch]$Observe", text)
        self.assertIn("[switch]$RotatePath", text)
        self.assertIn("[switch]$QuarantineOAuthLuna", text)
        self.assertIn("[switch]$RestoreOAuthLuna", text)
        self.assertIn("[switch]$DeactivateOAuthLuna", text)
        self.assertIn("STRICT=1", text)
        self.assertIn("DOCTOR_CONTRACT_FAILED", text)
        self.assertIn("==maintenance-heartbeats==", text)
        self.assertIn("heartbeat_limit_hours", text)
        self.assertIn("heartbeat-$heartbeat", text)
        self.assertIn("status=MISSING required=true", text)
        self.assertIn("status=NOT_REQUIRED required=false", text)
        self.assertIn("status=INVALID required=$heartbeat_required", text)
        self.assertIn(
            "success:0|running:0|busy:75|unverified:10|deferred:30|deferred:76",
            text,
        )
        self.assertIn("cpa-update.pending", text)
        self.assertIn("cpa-update-pending-verification", text)
        # Observe mode keeps exit 0 but must not masquerade a failing run as a
        # clean contract.
        self.assertIn("DOCTOR_CONTRACT_OBSERVE_FAILED", text)
        self.assertIn("DOCTOR_CONTRACT_OBSERVE_OK", text)
        # Data plane shape: loopback-only container port and no SSH tunnel
        # data plane; the public entry stays nginx 8443 with random path.
        self.assertIn(
            'expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}',
            text,
        )
        self.assertNotIn("ssh -L", text)
        self.assertNotIn("ssh -R", text)
        self.assertNotIn("ssh -D", text)
        # Host hygiene readout: a pending reboot (the monthly job never reboots)
        # and the bounded maintenance backup roots must be visible from the
        # doctor instead of only living in remote logs.
        self.assertIn("==host-hygiene==", text)
        self.assertIn("reboot_required=present age_days=", text)
        self.assertIn("reboot_required_advisory=STALE_REBOOT_PENDING", text)
        self.assertIn("reboot_required=absent", text)
        self.assertIn("maintenance_backups root=", text)
        for scope, prefix in (
            ("cpa_guardrails_backups", "cpa-guardrails-backup-*"),
            ("cpa_path_backups", "cpa-guardrails-path-backup-*"),
            ("cpa_oauth_quarantine_backups", "cpa-oauth-quarantine-backup-*"),
        ):
            with self.subTest(scope=scope):
                self.assertIn(f"-name '{prefix}'", text)
                self.assertIn(f"PRUNE scope={scope}", text)
        # Projection integrity: each embedded payload placeholder must have a
        # matching remote write, or the remote side silently keeps stale code.
        for placeholder in ("__CPA_HEALTH_B64__", "__CPA_UPDATER_B64__"):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, text)
                self.assertIn(f'write_base64_file "{placeholder}"', text)
        self.assertIn("__CPA_PROVIDER_ENV_B64__", text)
        self.assertIn("__CPA_PROVIDER_ROUTES_B64__", text)
        self.assertIn(
            'write_base64_file "__CPA_PROVIDER_ROUTES_B64__" '
            '"$DIR/cpa_provider_routes.json" 644',
            text,
        )
        route_manifest = json.loads(
            (repo_root / "scripts" / "remote" / "cpa_provider_routes.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [provider["slot"] for provider in route_manifest["providers"]],
            # Slot 3 (http-bridge-8003) deleted 2026-11-18 (user directive).
            # Slot 2 (opencode) retired 2026-11-20 with the user-cancelled
            # OpenCode Go subscription; the number stays unused so the
            # providers.env BASE_URL_2/API_KEY_2 mapping survives a renewal
            # re-add.
            [1, 4, 6],
        )
        self.assertEqual(
            route_manifest["retired_hosts"],
            [
                "codex.ciii.club",
                "api.deepseek.com",
                "ai.input.im",
                "35.213.82.91",
                # 20540ac lesson: the retired host must be registered so the
                # apply prunes the orphaned live provider instead of rolling
                # back on POLICY_FAILED.
                "opencode.ai",
            ],
        )
        retired_aliases = {
            "codex-auto-review",
            "gpt-5.5",
            "gpt-6.1-sol-input",
            "gpt-image-2.5",
        }
        self.assertTrue(retired_aliases <= set(route_manifest["oauth_exclusions"]))
        self.assertTrue(
            retired_aliases <= set(route_manifest["codex_api_key_exclusions"])
        )
        # Slot-2 aliases tombstoned with the 2026-11-20 retirement stay in
        # the OAuth-side exclusion list so neither lane can re-claim them.
        oc_tombstones = {
            "deepseek-v4.1-flash-oc",
            "glm-5.3-oc",
            "glm-5.3-flash-oc",
        }
        self.assertTrue(oc_tombstones <= set(route_manifest["oauth_exclusions"]))
        self.assertFalse(
            oc_tombstones
            & {
                model["alias"]
                for provider in route_manifest["providers"]
                for model in provider["models"]
            }
        )
        oauth_route_aliases = {
            model["alias"]
            for route in route_manifest["oauth_routes"]
            for model in route["models"]
        }
        # gpt-reserve retired 2026-11-04 (user directive): the OAuth route and
        # its gpt-5.6-luna rename are gone.
        # 2026-11-14 (user directive: never use): with the upstream catalog
        # re-advertising the name, both exclusion lists tombstone it so
        # neither the OAuth nor the API-key lane can ever claim it.
        self.assertNotIn("gpt-reserve", oauth_route_aliases)
        self.assertIn("gpt-reserve", route_manifest["oauth_exclusions"])
        self.assertIn("gpt-reserve", route_manifest["codex_api_key_exclusions"])
        self.assertIn("gpt-5.6", route_manifest["codex_api_key_exclusions"])
        self.assertEqual(
            route_manifest["oauth_model_aliases"],
            {
                "workbuddy-provider": [
                    {"name": "glm-5.3-flash", "alias": "glm-5.3-flash-wb"},
                    {"name": "glm-5.4", "alias": "glm-5.4-wb"},
                ],
            },
        )
        slot1_route = next(
            route for route in route_manifest["providers"] if route["slot"] == 1
        )
        self.assertEqual(slot1_route["host"], "input.codes")
        self.assertEqual(
            [model["alias"] for model in slot1_route["models"]],
            [
                "gpt-6.2-astra",
            ],
        )
        self.assertEqual(slot1_route["optional_models"], [])
        self.assertEqual(
            slot1_route["retired_models"],
            ["gpt-6.1-sol-input", "gpt-6-astra"],
        )
        # gpt-image-2.5 retired 2026-10-04: no image route is declared, so the
        # whole image_models key disappears instead of staying an empty list.
        self.assertNotIn("image_models", slot1_route)
        # Slot 2 was retired 2026-10-18, re-activated 2026-10-24, retired again
        # 2026-11-11 (lapsed subscription), re-admitted 2026-11-14 after the
        # renewal (single-homed deepseek-v4.1-flash-oc, GLM-5.3 family joined
        # 2026-11-20 morning), and retired for good the same day when the
        # user cancelled the OpenCode Go subscription. No slot-2 route may
        # exist in the manifest; opencode.ai sits in retired_hosts so the
        # apply prunes the live leg instead of rolling back on
        # POLICY_FAILED (20540ac). A renewal re-add restores the slot from
        # git history plus -Apply; the providers.env BASE_URL_2/API_KEY_2
        # mapping stays unused meanwhile.
        self.assertFalse(
            [route for route in route_manifest["providers"] if route["slot"] == 2]
        )
        self.assertEqual(route_manifest["claude_api_key_routes"], [])
        for slot, expected in {
            4: {
                "glm-5.3-flash",
                "glm-5.4",
            },
        }.items():
            provider = next(
                route for route in route_manifest["providers"] if route["slot"] == slot
            )
            self.assertEqual({model["name"] for model in provider["models"]}, expected)
            self.assertEqual({model["alias"] for model in provider["models"]}, expected)
            glm_flash = next(
                model
                for model in provider["models"]
                if model["name"] == "glm-5.3-flash"
            )
            self.assertEqual(
                glm_flash,
                {
                    "name": "glm-5.3-flash",
                    "alias": "glm-5.3-flash",
                    "max-context-length": 1000000,
                    "thinking-levels": ["low", "medium", "high", "xhigh"],
                },
            )
            glm_54 = next(
                model for model in provider["models"] if model["name"] == "glm-5.4"
            )
            self.assertEqual(
                glm_54,
                {
                    "name": "glm-5.4",
                    "alias": "glm-5.4",
                    "max-context-length": 1000000,
                    "thinking-levels": ["low", "medium", "high", "xhigh"],
                },
            )
        self.assertFalse(
            any(provider["slot"] == 3 for provider in route_manifest["providers"])
        )
        self.assertIn(
            'legacy_hosts = set(route_manifest.get("retired_hosts", []))',
            text,
        )
        self.assertEqual(
            route_manifest["apply_patch_filter_models"],
            ["glm-5.3-flash", "glm-5.4"],
        )
        import runpy

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        self.assertEqual(
            policy["EXPECTED_PAYLOAD_TOOL_RULES"]["filter"][-2:],
            [
                {
                    "models": [{"name": "glm-5.3-flash"}],
                    "params": [
                        'tools.#(type=="custom")',
                        'input.#(type=="additional_tools")#.tools.#(name=="functions")#'
                        '.tools.#(name=="apply_patch")#',
                    ],
                },
                {
                    "models": [{"name": "glm-5.4"}],
                    "params": [
                        'tools.#(type=="custom")',
                        'input.#(type=="additional_tools")#.tools.#(name=="functions")#'
                        '.tools.#(name=="apply_patch")#',
                    ],
                },
            ],
        )
        apply_source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("apply_patch_filter_models = route_manifest.get(", apply_source)
        function_source = apply_source.split("def model_entry(", 1)[1].split(
            "\n\ndef build_provider", 1
        )[0]
        namespace: dict[str, Any] = {}
        exec("def model_entry(" + function_source, namespace)
        model_entry = namespace["model_entry"]
        self.assertEqual(
            model_entry(
                {
                    "name": "glm-5.3",
                    "alias": "glm-5.3",
                    "input-modalities": ["text"],
                    "max-context-length": 1000000,
                }
            ),
            {
                "name": "glm-5.3",
                "alias": "glm-5.3",
                "input-modalities": ["text"],
                "max-context-length": 1000000,
            },
        )
        for invalid in (
            [],
            "",
            ["text", "text"],
            ["patch"],
            [42],
            ["text", "image", "audio"],
        ):
            with self.subTest(modalities=invalid):
                with self.assertRaises(SystemExit):
                    model_entry(
                        {
                            "name": "glm-5.3",
                            "alias": "glm-5.3",
                            "input-modalities": invalid,
                        }
                    )
        with self.assertRaises(SystemExit):
            model_entry({"name": "x", "alias": "x", "unexpected": True})
        # A truncated foreign write must be refused before yaml folds it into a
        # scalar. The 2026-11-06 live file ended with a partial re-write of the
        # previous key at zero indent; that parses cleanly, so only this scan
        # catches it.
        detector = (
            "def tail_defects(config_text):\n"
            "    tail_start = max(0, len(config_text.splitlines()) - 12)\n"
            "    defects = []\n"
            "    previous_key = ''\n"
            "    for number, raw in enumerate(\n"
            "        config_text.splitlines()[tail_start:], start=tail_start + 1\n"
            "    ):\n"
            "        if not raw.strip() or raw.lstrip().startswith(('---', '...', '#')):\n"
            "            continue\n"
            "        key = raw.split(':', 1)[0].strip() if ':' in raw else ''\n"
            "        if raw[0].isspace():\n"
            "            previous_key = key\n"
            "            continue\n"
            "        if (\n"
            "            key\n"
            "            and previous_key\n"
            "            and len(key) < len(previous_key)\n"
            "            and previous_key.endswith(key)\n"
            "        ):\n"
            "            defects.append(f'line {number}: {raw.rstrip()!r}')\n"
            "        previous_key = key\n"
            "    return defects\n"
        )
        defect_scope: dict[str, Any] = {}
        exec(detector, defect_scope)
        detect = cast(Callable[[str], list[str]], defect_scope["tail_defects"])
        self.assertEqual(
            detect("      scheduler_priority_reclaim: true\nity_reclaim: true\n"),
            ["line 2: 'ity_reclaim: true'"],
        )
        # Two distinct top-level keys, a nested list, and a nested mapping are
        # all legitimate and must not trip the scan.
        for clean in (
            "routing:\n  strategy: round-robin\nserver:\n  port: 8317\n",
            "    antigravity:\n    - gemini-3-flash\n    claude:\n    - fable-5\n",
            "  plugins:\n    configs:\n      workbuddy:\n        token_keepalive: false\n"
            "        scheduler_priority_reclaim: true\n",
        ):
            with self.subTest(clean=clean):
                self.assertEqual(detect(clean), [])
        for invalid_length in (0, -1, "500000", 500000.0, True):
            with self.subTest(max_context_length=invalid_length):
                with self.assertRaises(SystemExit):
                    model_entry(
                        {
                            "name": "x",
                            "alias": "x",
                            "max-context-length": invalid_length,
                        }
                    )
        setting_source = apply_source.split("def oauth_model_setting(", 1)[1].split(
            "\n\ndef build_provider", 1
        )[0]
        exec("def oauth_model_setting(" + setting_source, namespace)
        oauth_model_setting = namespace["oauth_model_setting"]
        self.assertEqual(
            oauth_model_setting({"name": "gpt-6.1-sol", "max-context-length": 500000}),
            {"name": "gpt-6.1-sol", "max-context-length": 500000},
        )
        for invalid_setting in (
            {},
            {"name": "x"},
            {"name": "", "max-context-length": 1},
            {"name": "x", "max-context-length": 0},
            {"name": "x", "max-context-length": "1"},
            {"name": "x", "max-context-length": 1, "unexpected": True},
        ):
            with self.subTest(setting=invalid_setting):
                with self.assertRaises(SystemExit):
                    oauth_model_setting(invalid_setting)

        doctor = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-doctor.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('echo "model-input-modalities=OK"', doctor)
        self.assertIn("mark_fail model-input-modalities", doctor)
        checker = doctor.split(
            'if python3 - "$DIR/config.yaml" "$DIR/cpa_provider_routes.json" <<\'PY\'\n',
            1,
        )[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            config_path = root / "config.yaml"
            matching = {
                "providers": [
                    {
                        "models": [
                            {
                                "name": "glm-5.3",
                                "alias": "glm-5.3",
                                "input-modalities": ["text"],
                            }
                        ]
                    }
                ]
            }
            manifest_path.write_text(json.dumps(matching), encoding="utf-8")
            config_path.write_text(
                "api-keys:\n"
                "  openai-compatibility:\n"
                "  - models:\n"
                "    - name: glm-5.3\n"
                "      alias: glm-5.3\n"
                "      input-modalities:\n"
                "      - text\n",
                encoding="utf-8",
            )
            harness = "import sys\nsys.argv = ['checker', %r, %r]\n" % (
                str(config_path),
                str(manifest_path),
            )
            passed = subprocess.run(
                [sys.executable, "-c", harness + checker],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            config_path.write_text(
                "api-keys:\n"
                "  openai-compatibility:\n"
                "  - models:\n"
                "    - name: glm-5.3\n"
                "      alias: glm-5.3\n",
                encoding="utf-8",
            )
            failed = subprocess.run(
                [sys.executable, "-c", harness + checker],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
        self.assertIn('echo "==models-max-context=="', doctor)
        self.assertIn('echo "==client-model-context=="', doctor)
        self.assertIn("MODEL_CONTEXT_DRIFT=", doctor)
        self.assertIn("mark_fail model-context-catalog", doctor)
        context_checker = doctor.split(
            "if printf '%s' \"$CLIENT_MODEL_CATALOG\" | python3 -c '", 1
        )[1].split('\' "$DIR/cpa_provider_routes.json"', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "models": [
                                    {
                                        "name": "gpt-6-astra",
                                        "alias": "gpt-6-astra",
                                        "max-context-length": 272000,
                                    },
                                    {
                                        "name": "gpt-6.1-sol",
                                        "alias": "gpt-6.1-sol-input",
                                        "max-context-length": 500000,
                                    },
                                ],
                                "optional_models": ["gpt-6.1-sol-input"],
                            }
                        ],
                        "oauth_settings": {
                            "codex": [
                                {
                                    "name": "gpt-6.1-sol",
                                    "max-context-length": 500000,
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            def run_context(
                catalog: dict[str, Any],
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-c", context_checker, str(manifest_path)],
                    input=json.dumps(catalog),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )

            passed = run_context(
                {
                    "models": [
                        {
                            "slug": "gpt-6-astra",
                            "context_window": 272000,
                            "max_context_window": 272000,
                        },
                        {
                            "slug": "gpt-6.1-sol",
                            "context_window": 500000,
                            "max_context_window": 500000,
                        },
                    ]
                }
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("MODEL_CONTEXT_DRIFT=none", passed.stdout)
            stale = run_context(
                {
                    "models": [
                        {
                            "slug": "gpt-6.1-sol",
                            "context_window": 500000,
                            "max_context_window": 872000,
                        }
                    ]
                }
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("gpt-6.1-sol:want=500000", stale.stdout)

            optional_absent = run_context(
                {
                    "models": [
                        {
                            "slug": "gpt-6-astra",
                            "context_window": 272000,
                            "max_context_window": 272000,
                        },
                        {
                            "slug": "gpt-6.1-sol",
                            "context_window": 500000,
                            "max_context_window": 500000,
                        },
                    ]
                }
            )
            self.assertEqual(
                optional_absent.returncode,
                0,
                optional_absent.stdout + optional_absent.stderr,
            )
            self.assertIn(
                "MODEL_CONTEXT_OPTIONAL_MISSING=gpt-6.1-sol-input",
                optional_absent.stdout,
            )
            self.assertIn("MODEL_CONTEXT_MISSING=none", optional_absent.stdout)
        self.assertIn("error-dump-permissions=OK", text)
        self.assertIn("compose-umask=OK", text)
        self.assertIn("logs-max-total-size-mb=32", text)
        self.assertIn("limit_req zone=cpa_rl burst=10;", text)
        self.assertIn(
            'legacy_limit_req = "limit_req zone=cpa_rl burst=20 nodelay;"', text
        )
        # Local throttling must answer 429, not nginx's 503 default, and the
        # limiter directive plus its statuses must be asserted by the read-only
        # doctor - not only repaired by -Apply. Without this the 2026-09-09
        # hardening silently regressed out of the versioned contract.
        self.assertIn("limit_req_status 429;", text)
        self.assertIn("limit_conn_status 429;", text)
        self.assertIn("gateway-per-ip-rate-limit", text)
        self.assertIn("gateway-throttle-status=429", text)
        # The per-IP connection budget must cover all three admission lanes'
        # held-connection budgets (6 + 7 + 7 = 20); 12 was still reached by
        # four concurrent desktop sessions with auxiliary connections.
        self.assertIn("limit_conn cpa_cc 20;", text)
        self.assertIn("gateway-per-ip-concurrency=20", text)
        self.assertIn("ensure_nginx_directive", text)
        self.assertIn("ROLLBACK gateway_throttle_contract", text)
        # The catalog segment is manifest-derived and fails closed on any
        # unregistered ID instead of only printing MODEL_IDS for a human.
        self.assertIn("MODEL_IDS_UNKNOWN=", text)
        self.assertIn('"$DIR/cpa_provider_routes.json"; then', text)
        # config.yaml carries cleartext provider keys; its permissions are gated.
        self.assertIn("config-permissions=owner-only", text)
        self.assertIn("oauth_days_left=", text)
        self.assertIn("oauth_hours_left=", text)
        self.assertIn("oauth_refresh_policy=lead24h_grace2h", text)
        self.assertIn("refresh_token_reused", text)
        self.assertIn("oauth_refresh_failures_7d=", text)
        self.assertIn("oauth_monitor=FAIL_REFRESH_SIGNAL", text)
        # OAuth refresh signals come only from response-side dump sections
        # and the container log: request-body keywords must never trip the
        # gate (2026-09-21 false positive), one file is one event, and a
        # later successful refresh resolves retained signals.
        self.assertIn("=== api error response ===", text)
        self.assertIn("oauth_refresh_signals_resolved", text)
        self.assertIn("incomplete_bounded_sample", text)
        # Error dump inventory must retain each path alongside its metadata;
        # otherwise the loop repeatedly reads the last path from discovery.
        self.assertIn("candidates.append((path, st.st_mtime, st.st_size))", text)
        self.assertIn("for path, mtime, size in candidates:", text)
        # Retained-dump observations are bounded samples (CPA keeps only the
        # newest error-logs-max-files dumps), never full-window counts.
        self.assertIn("auth_unavailable_retained_sample_count", text)
        self.assertIn("incomplete_bounded_error_dumps", text)
        # Management plane: either fully disabled or keyed-behind-loopback.
        # allow-remote=true is only acceptable with a >=32 char secret-key,
        # because docker-proxy forwards non-loopback source IPs and the panel
        # is reached through an SSH tunnel; nginx must never carry a
        # management route.
        self.assertIn("management-remote=DISABLED", text)
        self.assertIn("management-remote=LOOPBACK_KEYED", text)
        self.assertIn("-ge 32", text)
        self.assertIn("nginx-no-management-route", text)
        self.assertIn("REFUSE remote management enabled without a strong", text)
        # Gateway status telemetry: 499 client aborts carry request_time stats
        # so a fixed client-side total timeout signature is provable from
        # doctor output alone.
        self.assertIn("client_abort_request_time", text)
        self.assertIn("abort_request_times", text)
        self.assertIn("'p50_s':", text)
        self.assertIn("admission_429_shape", text)
        self.assertIn("likely_admission_fast_203", text)
        self.assertIn("upstream_time", text)
        # The Nginx shape only identifies the producing hop. Doctor must also
        # expose the bounded admission journal aggregates so a local 429 can
        # be separated from an upstream 429 without grepping raw request ids.
        self.assertIn("==admission-journal-24h==", text)
        self.assertIn("admission_lane_reject_reasons=", text)
        self.assertIn("admission_capacity_statuses=", text)
        self.assertIn("admission_upstream_429_503_details=", text)
        self.assertIn('status in {"429", "503"}', text)
        self.assertIn("upstream_result_is_authoritative_for_capacity", text)
        # 5xx attribution: request_time buckets separate CPA cooldown
        # fast-fails (<0.5s) from upstream passthrough (>=3s) so a client 503
        # storm is attributable from doctor output alone; client IPs stay
        # masked to /16 and retry cadence is reported as aggregate gaps only.
        self.assertIn("five_xx_local_vs_upstream", text)
        self.assertIn("fast_local_lt_0_5s", text)
        self.assertIn("fast_upstream_lt_0_5s", text)
        self.assertIn("slow_upstream_ge_3s", text)
        self.assertIn("five_xx_by_client_masked", text)
        self.assertIn("client_503_retry_pattern", text)
        self.assertIn("client_503_retry_pattern_by_plane", text)
        # A planned upgrade window returns the 44-byte maintenance refusal in
        # ~2ms, which is shaped like a cooldown fast-fail. Name it so its 1s
        # retries are not reported as a client retry storm, and expose the
        # authoritative journal refusal aggregate beside it.
        self.assertIn("admission_maintenance_44B_upstream_lt_0_5s", text)
        self.assertIn("client_503_maintenance_shape_total", text)
        self.assertIn("client_503_maintenance_shape_by_client_masked", text)
        self.assertIn("admission_request_guard_refusals_24h", text)
        self.assertIn("admission_request_guard_refusal_reasons", text)
        self.assertIn("retry_after_classes", text)
        self.assertIn("last_1h_statuses", text)
        # Production vs. test-residue split: the bare 24h total once read as a
        # clean gateway while 20 of its 429s were loopback load-test residue and
        # every real-client 429 sat inside one two-hour window. Every status is
        # now classed by client plane, and per-hour buckets locate a burst in
        # time. Only the class is emitted, never an address.
        self.assertIn("statuses_by_client_class", text)
        self.assertIn("statuses_by_hour", text)
        self.assertIn("limit_rejected", text)
        self.assertIn("startswith('127.')", text)
        self.assertIn("route_classes", text)
        self.assertIn("safe-route-class=LEGACY_UNPROJECTED", text)
        self.assertIn("map $uri $cpa_route_class", text)
        self.assertIn("route=$cpa_route_class", text)
        self.assertIn("retry_after=$cpa_retry_after_class", text)
        self.assertIn("map $upstream_http_retry_after $cpa_retry_after_class", text)
        # TTFB observability: $upstream_response_time only reports the completed
        # response, which for SSE is the whole turn, so a gateway that held the
        # response headers for ten seconds was indistinguishable from a slow
        # generation. The header-time field is the only one that separates them,
        # and it is appended last so no positional parser shifts.
        self.assertIn("upstream_header_time=$upstream_header_time", text)
        self.assertIn("pre_ttfb_log_format = log_format.replace(", text)
        self.assertGreaterEqual(text.count("pre_ttfb_log_format,"), 2)
        self.assertGreaterEqual(text.count("without_retry_log_format,"), 2)
        self.assertGreaterEqual(text.count("legacy_route_log_format,"), 2)
        # Silent model substitution telemetry (upstream >= v7.3.8): counted,
        # redaction-safe (no log line text or auth_index echoed),
        # capability-aware, and observation-grade. Two severity tiers: >5
        # events/7d is ELEVATED (warrants quality-canary review); 1-5 is
        # OBSERVED; 0 is OK. v8.0.3x emits one warn per substituted request,
        # so the doctor also breaks the count down per (served, requested)
        # model pair — the raw count tracks volume, the pairs show spread.
        self.assertIn("==model-substitution==", text)
        self.assertIn("model_substitution_warnings_7d=", text)
        self.assertIn("| grep 'upstream served model' >", text)
        self.assertIn("model_substitution_distinct_pairs=", text)
        self.assertIn("model_substitution_pair=", text)
        self.assertIn("model_substitution_known_name_variant_pairs=", text)
        self.assertIn("model_substitution_unknown_pairs=", text)
        self.assertIn("KNOWN_NAME_VARIANT", text)
        self.assertIn("WARN_SUBSTITUTION_ELEVATED", text)
        self.assertIn("WARN_SUBSTITUTION_OBSERVED", text)
        self.assertIn("model_substitution=UNAVAILABLE_VERSION", text)
        self.assertIn("sort -V", text)
        # Coverage must not turn a retained-log observation into OAuth probes
        # or proof of a complete seven-day window.
        self.assertIn("zero warnings do not prove absence over a full 7d window", text)
        self.assertIn("OAuth quality probes require separate admission", text)
        self.assertNotIn(
            "not a strict gate",
            text.split("WARN_SUBSTITUTION_ELEVATED")[0],
            msg="coverage annotation must appear after the ELEVATED label",
        )
        # P2-C: HTTP (cleartext) provider slots from the deployed route manifest
        # must be surfaced each doctor run so operators never rely on memory.
        # The check reads cpa_provider_routes.json and emits a reminder line
        # without mark_fail (slot-3 is a user-authorised exception).
        self.assertIn("insecure_http_providers=", text)
        self.assertIn("no mark_fail", text)
        self.assertIn("user-authorised", text)
        self.assertIn("assert_public_route_contract()", text)
        self.assertIn("assert_path_route_contract()", text)
        self.assertIn(
            "PROBE_ALREADY_RUNNING",
            (repo_root / "scripts" / "remote" / "cpa-health.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn('chmod 700 "$DIR/auth/logs"', text)
        self.assertIn("-name 'error-*.log' -exec chmod 600 -- {} +", text)
        # Random-path rotation must prove old path dead and new path live.
        self.assertIn("OLD_PATH_REVOKED=yes", text)
        self.assertIn("NEW_PATH_ACTIVE=yes", text)
        # A long payload rides the shared lib transport: bounded chunks stage
        # a mode-600 temp file, byte-count validation gates the decode, and
        # cleanup is owned by the remote finally block. The staged path lives
        # in project_environment.ps1; the dispatcher must feed it the
        # historical cpa-guardrails namespace.
        self.assertIn('RemoteTempPrefix = "cpa-guardrails"', text)
        lib_text = (
            repo_root / "scripts" / "lib" / "project_environment.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "/tmp/$RemoteTempPrefix-$([guid]::NewGuid().ToString('N')).b64",
            lib_text,
        )
        self.assertIn("umask 077; : > '$remoteTemp'; chmod 600 '$remoteTemp'", lib_text)
        self.assertIn(
            "test `\"`$(wc -c < '$remoteTemp')`\" -eq $($payload.Length) ||",
            lib_text,
        )
        self.assertIn("rm -f -- '$remoteTemp'", lib_text)
        # Apply/RotatePath/DeactivateOAuthLuna/QuarantineOAuthLuna share the
        # updater flock so the daily timer cannot interleave with a guardrail
        # transaction.
        self.assertEqual(
            templates.count("exec 9>/run/vps-ssh-launcher-maintenance.lock"), 4
        )
        self.assertEqual(templates.count("flock -n 9"), 4)
        # OAuth lane availability is a property of the whole route, not of the
        # bare `gpt-6-luna` name; the doctor must expose the derived alias set
        # and the partial state so a dropped bare name is not read as an outage.
        self.assertIn("catalog_oauth_aliases=", text)
        self.assertIn("catalog_oauth_missing=", text)
        self.assertIn("available_partial", text)
        self.assertIn("unknown_route_manifest", text)
        # P2-D: local throttle rejections must carry a back-off signal, and the
        # header must stay scoped to those rejections. The merged count is
        # lane-aware since the 2026-09-30 443-fallback lane mirrors the
        # canonical shapes, so the doctor reports the count and the lane
        # marker instead of a pinned literal.
        self.assertIn("safe-throttle-retry-after=OK", text)
        self.assertIn("throttle-retry-after-map-count=", text)
        self.assertIn("gateway-443-fallback-lane=", text)
        self.assertIn("add_header Retry-After $cpa_throttle_retry_after always;", text)
        self.assertIn(
            'map "$limit_req_status:$limit_conn_status" $cpa_throttle_retry_after {',
            text,
        )
        # nginx reaches CPA over loopback, so the jail must never be able to ban
        # 127.0.0.1: the doctor's own unauthenticated probe would otherwise feed
        # it 401s until the gateway lost its upstream. Guard the source of truth
        # and the deployed reading.
        self.assertIn("fail2ban-ban-scope=loopback_exempt_incremental", text)
        self.assertIn("ignoreip = 127.0.0.1/8 ::1", text)
        jail_source = (
            repo_root / "scripts" / "remote" / "cpa-fail2ban-jail.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("ignoreip = 127.0.0.1/8 ::1", jail_source)
        self.assertIn("bantime = 3600", jail_source)
        self.assertIn("bantime.increment = true", jail_source)
        self.assertIn("bantime.factor = 2", jail_source)
        self.assertIn("bantime.maxtime = 604800", jail_source)
        self.assertIn("maxretry = 20", jail_source)
        self.assertIn("findtime = 600", jail_source)

    def test_fail2ban_filter_matches_the_format_nginx_actually_writes(self) -> None:
        """The jail regex must match the live record shape, not just its source.

        2026-11-09 (bwg): the deployed filter was byte-identical to the pinned
        literal the doctor grepped for, and matched 0 of 1702 access-log lines.
        cpa_safe had appended ``route=`` before ``status=`` and
        ``upstream_header_time=`` after ``auth_status=``, so the old trailing
        end-of-line anchor could never match. The jail was inert for weeks while
        ``fail2ban-contract=OK`` was reported, because that check compared the
        file to a pinned string instead of to a real log line.

        This is the behavioural half, and it runs anywhere: the fail2ban
        substitution rules are applied in pure Python, so no daemon or bash is
        needed. The sample record is built from the field order the apply
        script projects, so a future field insertion fails here instead of
        silently disarming the jail.
        """

        repo = Path(__file__).parents[1]
        filter_source = (repo / "scripts/remote/cpa-fail2ban-filter.conf").read_text(
            encoding="utf-8"
        )
        apply_source = (repo / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        regex = _fail2ban_failregex(filter_source)
        # The expression must terminate on whitespace-or-end, never on EOL:
        # cpa_safe appends observable fields at the end of the record.
        pattern = re.compile(regex)
        self.assertIsNone(re.search(r"\s\*\$$", regex.rsplit("auth_status=", 1)[1]))

        # Model the projected field order rather than hardcoding it, so a new
        # field inserted between auth_status and the tail is caught here.
        format_block = apply_source.split("log_format = (", 1)[1].split(");", 1)[0]
        self.assertLess(
            format_block.index("auth_status=$cpa_auth_status"),
            format_block.index("upstream_header_time=$upstream_header_time"),
        )
        tail = "upstream_header_time=-"

        def record(status: str, auth_status: str) -> str:
            return (
                "8.163.41.198 method=POST route=chat "
                f"status={status} request_time=0.161 upstream_status=- "
                "upstream_time=- bytes=172 limit_req=PASSED limit_conn=PASSED "
                "retry_after=absent "
                "time=[09/Nov/2026:13:11:25 +0000] "
                f"auth_status={auth_status} {tail}"
            )

        # fail2ban replaces <HOST> with the host-matching expression.
        pattern = re.compile(regex.replace("<HOST>", r"\S+"))
        # A credential failure must match; a served request and a local
        # throttle (no CPA auth_status) must not.
        self.assertIsNotNone(pattern.search(record("401", "401")))
        self.assertIsNotNone(pattern.search(record("403", "403")))
        self.assertIsNone(pattern.search(record("200", "200")))
        self.assertIsNone(
            pattern.search(
                record("429", "-").replace("limit_req=PASSED", "limit_req=REJECTED")
            )
        )

    def test_cpa_guardrails_normalizes_crlf_in_remote_payloads(self) -> None:
        text = read_guardrail_source()
        function = text.split("function Invoke-BwgRemoteScript", 1)[1].split(
            "$doctorScript = @'", 1
        )[0]
        # CRLF normalization and chunking belong to the shared launcher
        # transport. Keep this wrapper free of a divergent private algorithm
        # while preserving its established prefix and timeout mapping.
        self.assertIn("Invoke-LauncherRemoteCommand", function)
        self.assertIn('RemoteTempPrefix = "cpa-guardrails"', function)
        self.assertIn("IdleTimeoutSeconds = $CommandTimeout", function)
        self.assertNotIn("$chunkSize = 12000", function)
        transport = (
            Path(__file__).resolve().parents[1] / "scripts/lib/project_environment.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '$normalizedCommand = $Command.Replace("`r`n", "`n").Replace("`r", "`n")',
            transport,
        )
        self.assertIn("chmod 600 '$remoteTemp'", transport)

    def test_cpa_guardrails_projection_hash_contract(self) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # Apply must verify every projected payload byte-for-byte at write
        # time; a mismatch rolls back instead of surviving as silent drift.
        self.assertIn("PROJECTION_HASH_MISMATCH", apply_payload)
        self.assertIn("PROJECTION_HASH_VERIFIED", apply_payload)
        for token in (
            "__CPA_UPDATER_SHA256__",
            "__CPA_HEALTH_SHA256__",
            "__CPA_POLICY_SHA256__",
            "__CPA_PROVIDER_ROUTES_SHA256__",
            "__CPA_FAIL2BAN_FILTER_SHA256__",
            "__CPA_FAIL2BAN_JAIL_SHA256__",
        ):
            self.assertIn(token, apply_payload)

        # Doctor compares the deployed copies against the same repo-side
        # hashes, so repo-ahead drift fails the contract instead of waiting
        # for a human to eyeball the sha listing.
        self.assertIn("==projection-drift==", doctor)
        self.assertIn("__CPA_PROJECTION_HASH_PAIRS__", doctor)
        self.assertIn('mark_fail "projection-drift-$drift_name"', doctor)
        self.assertIn("LIVE_MISSING", doctor)
        self.assertIn("MISMATCH", doctor)

        # The injected pair list must pass a strict format check before it
        # ever reaches the remote shell, and doctor hashes must anchor to the
        # committed source of truth (HEAD blob), not the working tree: with a
        # parallel session holding uncommitted edits in this worktree, a
        # working-tree anchor would report phantom drift.
        self.assertIn("^/[A-Za-z0-9._/-]+=[0-9a-f]{64}$", source)
        self.assertIn("Get-LfNormalizedSha256", source)
        self.assertIn("Get-HeadBlobSha256", source)
        self.assertIn("$projectionDoctorTargets = [ordered]@{", source)
        self.assertIn(
            "if (-not $projectionSources.Contains($entry.Key))",
            source,
        )
        self.assertIn("$admissionRequestGuardHeadDeployedText", source)
        # The HEAD anchor itself is the mechanism (the helpers are probed
        # behaviourally by test_head_blob_helpers_anchor_to_committed_bytes:
        # committed generation, argument-list invocation, fail-closed miss).
        self.assertIn('"HEAD:$(', source)
        self.assertIn("function Get-HeadBlobBytes", source)
        self.assertIn(
            "$projectionHeadSha256[$entry.Key] = Get-HeadBlobSha256 $entry.Key",
            source,
        )
        self.assertIn(
            '"scripts/remote/cpa-admission-launch.sh" = "/opt/cliproxyapi/cpa-admission-launch.sh"',
            source,
        )
        self.assertIn(
            '"scripts/remote/cpa_admission_bootstrap.py" = "/usr/local/libexec/cpa_admission_bootstrap.py"',
            source,
        )
        self.assertIn(
            '"scripts/remote/cpa_request_guard.py" = "/usr/local/libexec/cpa_request_guard.py"',
            source,
        )
        self.assertIn(
            "$projectionDoctorTargets.GetEnumerator()",
            source,
        )
        self.assertIn(
            '"/etc/vps-ssh-launcher/cpa-admission.sha256"',
            source,
        )
        self.assertIn(
            '$admissionIntegrityPinHeadValue = Get-HeadBlobText "scripts/remote/cpa-admission-integrity-pin.txt"',
            source,
        )
        self.assertIn(
            "$admissionIntegrityPinHeadValue -ne $admissionHeadSha256", source
        )
        self.assertNotIn("/opt/cliproxyapi/auto-update.sh=$updaterSha256", source)
        # Source metadata and the actual working bytes must describe one
        # generation. Otherwise Apply would deploy a payload that the next
        # HEAD-anchored doctor reports as drift.
        self.assertIn("if ($Apply -and $updaterSha256 -ne $updaterHeadSha256)", source)
        self.assertIn("Assert-ProjectionSourcesUnchanged", source)
        self.assertIn(
            "git -C $repoRoot status --porcelain=v1 --untracked-files=all",
            source,
        )
        self.assertIn("__CPA_WORKBUDDY_VERSION_CHECK_B64__", doctor)
        root = Path(__file__).parents[1]
        version_check = (
            root / "scripts/remote/cpa-guardrail-workbuddy-version-check.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("EXPECTED_CN=0.21.32", version_check)
        self.assertIn("INTL_SO_PRESENT", version_check)
        self.assertIn("LEGACY_SO_PRESENT", version_check)
        self.assertIn("current_container_process_only", version_check)
        self.assertIn("loading does not prove request behavior", version_check)
        self.assertIn('"connect.ps1"', source)
        self.assertIn('"scripts/cpa_bwg_guardrails.ps1"', source)

    def test_workbuddy_version_gate_fails_closed_without_retained_logs(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for plugin version gate")
        root = Path(__file__).parents[1]
        source = (
            root / "scripts/remote/cpa-guardrail-workbuddy-version-check.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('docker logs --since "$since" cli-proxy-api', source)
        self.assertIn(
            "docker inspect --format '{{.State.StartedAt}}' cli-proxy-api", source
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "opt"
            plugins = fixture / "cliproxyapi" / "plugins"
            plugins.mkdir(parents=True)
            (plugins / "workbuddy-provider.so").write_bytes(b"fixture")
            payload = (
                "docker() {\n"
                '  case "$1" in\n'
                "    inspect) printf '%s\\n' '2026-11-05T00:00:00.000000000Z' ;;\n"
                "    logs) printf '%s\\n' \"$MOCK_LOG\" ;;\n"
                "    *) return 1 ;;\n"
                "  esac\n"
                "}\n" + source.split("\n", 2)[2]
            )
            for mock_log, expected_code, marker in (
                ("", 1, "current_process_log=none"),
                (
                    "plugin registered plugin_id=workbuddy-provider version=0.21.26",
                    1,
                    "got=0.21.26",
                ),
                # Uninstalled healthy state: only the CN plugin registers.
                (
                    "plugin registered plugin_id=workbuddy-provider version=0.21.32",
                    0,
                    "workbuddy-version=cn:0.21.32 intl:uninstalled",
                ),
                # An INTL registration after uninstall = drift.
                (
                    "plugin registered plugin_id=workbuddy-provider version=0.21.32\n"
                    "plugin registered plugin_id=workbuddy-ai-provider version=0.1.40",
                    1,
                    "workbuddy-version=UNEXPECTED_REGISTRATION plugin=workbuddy-ai-provider got=0.1.40",
                ),
            ):
                with self.subTest(mock_log=mock_log):
                    env = dict(
                        os.environ,
                        MOCK_LOG=mock_log,
                        CPA_DIR=str(fixture / "cliproxyapi"),
                    )
                    result = subprocess.run(
                        self._bash_command(bash, "-c", payload),
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, expected_code, result.stderr)
                    self.assertIn(marker, result.stdout)

    def test_cpa_guardrails_doctor_proves_integrity_guard_is_loaded(
        self,
    ) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # A drop-in file on disk is not a loaded unit. The base unit declares no
        # ExecStartPre of its own, so requiring it in systemd's loaded view is
        # what proves the guard actually runs before admission starts.
        self.assertIn(
            "systemctl show cpa-admission.service -p ExecStartPre --value", doctor
        )
        self.assertIn('grep -Fq "$ADMISSION_INTEGRITY_CHECK"', doctor)
        # ExecStartPre runs as the unit's own User=, so the probe has to run
        # there too: a pin that only root can read would pass a root-only check
        # and still block every future service start.
        self.assertIn(
            "ADMISSION_SERVICE_USER=$(systemctl show",
            doctor,
        )
        self.assertIn("-p User --value 2>/dev/null)", doctor)
        # A fallback to root is the false green this probe exists to prevent: a
        # pin only root can read passes a root-only check and still blocks every
        # service start. An unanswerable systemd must fail the check instead.
        self.assertNotIn("|| ADMISSION_SERVICE_USER=root", doctor)
        self.assertIn("admission-integrity=UNREADABLE", doctor)
        self.assertIn(
            'runuser -u "$ADMISSION_SERVICE_USER" -- "$ADMISSION_INTEGRITY_CHECK"',
            doctor,
        )
        self.assertIn(
            "admission_integrity_service_user=$ADMISSION_SERVICE_USER", doctor
        )
        self.assertIn("__CPA_ADMISSION_PIN_VALUE__", doctor)
        self.assertIn("admission-integrity=OK", doctor)
        self.assertIn("mark_fail admission-integrity", doctor)

    def test_cpa_guardrails_pin_is_head_anchored_and_matches_admission_script(
        self,
    ) -> None:
        root = Path(__file__).parents[1]
        pin = (root / "scripts/remote/cpa-admission-integrity-pin.txt").read_text(
            encoding="utf-8"
        )
        admission = (
            (root / "scripts/remote/cpa-admission.py")
            .read_bytes()
            .replace(b"\r\n", b"\n")
        )
        # The pin file is the only source for the remote integrity pin, so a
        # stale or hand-edited value would project a generation whose every
        # future start fails ExecStartPre.
        self.assertTrue(pin.endswith("\n"))
        self.assertEqual(len(pin.strip()), 64)
        self.assertEqual(hashlib.sha256(admission).hexdigest(), pin.strip())
        apply_script = (root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("__CPA_ADMISSION_SHA256__", apply_script)
        self.assertIn("admission_pin_transition_pending", apply_script)
        self.assertIn(
            'write_base64_file "__CPA_REQUEST_GUARD_B64__" "$ADMISSION_REQUEST_GUARD" 644',
            apply_script,
        )
        self.assertIn(
            "ExecStart=/opt/cliproxyapi/cpa-admission-launch.sh",
            (root / "scripts/remote/cpa-admission.service").read_text(encoding="utf-8"),
        )

        source = read_guardrail_source()
        self.assertIn(
            'Get-HeadBlobText "scripts/remote/cpa-admission-integrity-pin.txt"', source
        )
        self.assertIn(
            "$admissionIntegrityPinHeadValue -ne $admissionHeadSha256", source
        )
        # Doctor compares the deployed pin against the committed generation, not
        # the working tree: a parallel session editing that file must not turn
        # into a phantom contract failure.
        self.assertNotIn("$admissionIntegrityPinText.Trim()", source)

    def test_head_blob_helpers_anchor_to_committed_bytes(self) -> None:
        """Run the real HEAD-blob helpers against a fixture git repository.

        The projection drift gate is only as trustworthy as these helpers:
        they must return the committed generation (never the working tree,
        where a parallel session may hold uncommitted edits), pass repo
        paths to git as argv elements so quoting can never reinterpret a
        path with spaces, and fail closed on a path absent from HEAD.
        """
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell 7 is unavailable")
        source = read_guardrail_source()
        helpers = source[
            source.index("function Get-HeadBlobBytes") : source.index(
                "$admissionIntegrityPinHeadValue = Get-HeadBlobText"
            )
        ]
        committed = "committed generation\n"
        space_committed = "space path generation\n"
        expected_hash = hashlib.sha256(committed.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "fixture-repo"
            notes = repo / "notes"
            notes.mkdir(parents=True)
            (notes / "plain.txt").write_text(committed, encoding="utf-8", newline="\n")
            (notes / "with space.txt").write_text(
                space_committed, encoding="utf-8", newline="\n"
            )
            env = {**os.environ, "GIT_CONFIG_GLOBAL": str(root / "gitconfig")}

            def git(*args: str) -> None:
                subprocess.run(
                    ["git", "-C", repo.as_posix(), *args],
                    capture_output=True,
                    check=True,
                    env=env,
                )

            git("init")
            git("add", ".")
            git(
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture generation",
            )
            # A parallel session holds uncommitted working-tree edits.
            (notes / "plain.txt").write_text(
                "dirty working tree\n", encoding="utf-8", newline="\n"
            )
            script = (
                f"$repoRoot = '{repo.as_posix()}'\n" + helpers + "\n"
                'Write-Output ("TEXT=" + (Get-HeadBlobText '
                '-RepoRelativePath "notes/plain.txt"))\n'
                'Write-Output ("HASH=" + (Get-HeadBlobSha256 '
                '-RepoRelativePath "notes/plain.txt"))\n'
                "$spaceBytes = Get-HeadBlobBytes "
                '-RepoRelativePath "notes/with space.txt"\n'
                'Write-Output ("SPACE=" + '
                "[Text.Encoding]::UTF8.GetString($spaceBytes))\n"
                "try { $null = Get-HeadBlobText "
                '-RepoRelativePath "notes/absent.txt" } '
                "catch { Write-Output 'MISSING=THREW' }\n"
            )
            completed = subprocess.run(
                [pwsh, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                env=env,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(f"TEXT={committed}", completed.stdout)
            self.assertNotIn("dirty working tree", completed.stdout)
            self.assertIn(f"HASH={expected_hash}", completed.stdout)
            self.assertIn(f"SPACE={space_committed}", completed.stdout)
            self.assertIn("MISSING=THREW", completed.stdout)

    def test_fork_binary_ledger_is_a_bare_sha256(self) -> None:
        root = Path(__file__).parents[1]
        ledger = (root / "scripts/remote/fork-binary.sha256").read_text(
            encoding="utf-8"
        )
        # A bare digest keeps the wrapper injection format-checked and the
        # doctor comparison exact; prose here would silently break both.
        self.assertTrue(ledger.endswith("\n"))
        self.assertRegex(ledger.strip(), r"^[0-9a-f]{64}$")

    def test_doctor_asserts_runtime_binary_against_fork_ledger(self) -> None:
        """The executed binary must equal the committed ledger in either shape.

        2026-11-08 exit-126 proved the entrypoint binary is load-bearing and
        that an image tag alone says nothing about which binary serves
        requests. The fork-image derivation moves the binary into the image
        (docs/runbooks/cpa-fork-image-derivation-rfc.md), but a rollback
        legitimately restores the bind-mount shape, so the source is reported
        and only an unledgered or unmatchable binary fails.
        """

        source = read_guardrail_source()
        self.assertIn('Get-HeadBlobText "scripts/remote/fork-binary.sha256"', source)
        self.assertIn("notmatch '^[0-9a-f]{64}$'", source)
        self.assertIn('Replace("__CPA_FORK_BINARY_SHA_VALUE__"', source)
        section = source.split("==runtime-binary==", 1)[1].split(
            'echo "==listeners=="', 1
        )[0]
        self.assertIn("__CPA_FORK_BINARY_SHA_VALUE__", section)
        self.assertIn("runtime_binary_source=", section)
        self.assertIn("runtime_binary_hash=MATCH", section)
        self.assertIn("runtime_binary_hash=MISMATCH", section)
        self.assertIn("mark_fail runtime-binary-source", section)
        self.assertIn("mark_fail runtime-binary-hash", section)
        self.assertIn("mark_fail runtime-binary-ledger", section)
        self.assertIn("/CLIProxyAPI/CLIProxyAPI", section)

    def test_cpa_guardrails_apply_restarts_admission_service(self) -> None:
        source = read_guardrail_source()
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # enable --now is a no-op on an already enabled+active service, so a
        # re-apply would leave the previous generation's process running the
        # old projected code (2026-09-26: the SSE stream fix shipped without
        # ever being loaded). The forward path must restart the unit.
        self.assertNotIn("enable --now cpa-admission.service", apply_payload)
        self.assertIn("systemctl restart cpa-admission.service", apply_payload)
        self.assertIn("ROLLBACK admission_restart", apply_payload)

    def test_cpa_guardrails_apply_requires_idle_lanes_before_mutation(self) -> None:
        """A busy lane must defer Apply without truncating its config backup.

        The preflight gate is probed, not spell-checked: the real
        PY_PREFLIGHT block runs against a fixture healthz server (the pinned
        URL and deadline are sentinel-replaced, 21e1ac5 pattern) and must
        accept an idle snapshot while refusing inflight, pending, and
        degraded lane states before any mutation happens.
        """

        source = read_guardrail_source()
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        contract = "Apply requires an idle lane snapshot"
        preflight = apply_payload.index("REFUSE cpa_apply_requires_idle_lanes")
        first_backup = apply_payload.index('cp -a "$DIR/config.yaml" "$BK/config.yaml"')
        first_mutation = apply_payload.index("\nwrite_base64_file() {")
        drain = apply_payload.index("exec 8< /run/cpa-admission/requests.lock")
        self.assertIn(contract, apply_payload)
        self.assertLess(preflight, first_backup)
        self.assertLess(preflight, first_mutation)
        self.assertLess(preflight, drain)
        preflight_start = apply_payload.rindex("<<'PY_PREFLIGHT'", 0, preflight + 1)
        preflight_end = apply_payload.rindex("PY_PREFLIGHT\n", 0, preflight + 1)
        preflight_block = apply_payload[preflight_start:preflight_end]
        # Drop the heredoc opener so the block is directly executable Python.
        preflight_block = preflight_block.split("<<'PY_PREFLIGHT'\n", 1)[1]
        # The mechanism stays: a bounded polling deadline against the live
        # healthz endpoint, not a one-shot read.
        self.assertIn("urllib.request.urlopen(", preflight_block)
        self.assertIn("time.monotonic()", preflight_block)
        self.assertIn("time.sleep(", preflight_block)

        class FixtureHealthz(http.server.BaseHTTPRequestHandler):
            """Loopback healthz fixture; state swapped per refusal case."""

            state: dict[str, Any] = {"status": "ok", "lanes": {}}

            def do_GET(self) -> None:  # noqa: N802 - stdlib signature
                body = json.dumps(self.state).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), FixtureHealthz)
        poller = server_thread(server, "apply-preflight-healthz")
        poller.start()
        try:
            block = preflight_block.replace(
                "http://127.0.0.1:8318/healthz",
                f"http://127.0.0.1:{server.server_port}/healthz",
            ).replace(
                # Bound the drain window the way the probe bounds the target
                # path: the mechanism under test is the deadline, not its
                # production value.
                "time.monotonic() + 45",
                "time.monotonic() + 2",
            )

            def run_preflight() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-c", block],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )

            FixtureHealthz.state = {
                "status": "ok",
                "lanes": {"chatgpt-oauth": {"state": {"inflight": 0, "pending": 0}}},
            }
            idle = run_preflight()
            self.assertEqual(idle.returncode, 0, idle.stderr)

            FixtureHealthz.state = {
                "status": "ok",
                "lanes": {"chatgpt-oauth": {"state": {"inflight": 1, "pending": 0}}},
            }
            inflight = run_preflight()
            self.assertNotEqual(inflight.returncode, 0)
            self.assertIn(contract, inflight.stderr)

            FixtureHealthz.state = {
                "status": "ok",
                "lanes": {"chatgpt-oauth": {"state": {"inflight": 0, "pending": 2}}},
            }
            pending = run_preflight()
            self.assertNotEqual(pending.returncode, 0)
            self.assertIn(contract, pending.stderr)

            FixtureHealthz.state = {"status": "degraded", "lanes": {}}
            degraded = run_preflight()
            self.assertNotEqual(degraded.returncode, 0)
            self.assertIn(contract, degraded.stderr)
        finally:
            server.shutdown()
            poller.join(timeout=5)
            server.server_close()

    def test_cpa_guardrails_doctor_bounds_access_log_scan(self) -> None:
        """Run the real scan preamble against fixture logs over a small cap.

        The 64 MiB production cap is sentinel-scaled to 64 KiB (21e1ac5
        pattern) and the pinned log path to a fixture file. Truncation must
        report its byte accounting, drop the partial boundary line, and scan
        only whole lines; an under-cap log must scan everything unannounced.
        """
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        scan_start = doctor.index("scan_cap_bytes = ")
        scan_end = doctor.index("for line in log_handle:", scan_start)
        scan_block = doctor[scan_start:scan_end]

        def run_scan(log_path: Path) -> subprocess.CompletedProcess[str]:
            probe = (
                "from pathlib import Path\n"
                + scan_block.replace("64 * 1024 * 1024", "64 * 1024").replace(
                    "Path('/var/log/nginx/cpa_gateway.access.log')",
                    f"Path({log_path.as_posix()!r})",
                )
                + "scanned = 0\n"
                "first_line = None\n"
                "for line in log_handle:\n"
                "    scanned += 1\n"
                "    if first_line is None:\n"
                "        first_line = line\n"
                "print('SCAN_PROBE scanned=%d first=%r' % (scanned, first_line))\n"
            )
            return subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small-access.log"
            small.write_text(
                "".join(f"L{i:04d}: {'x' * 93}\n" for i in range(5)),
                encoding="utf-8",
                newline="\n",
            )
            small_run = run_scan(small)
            self.assertEqual(small_run.returncode, 0, small_run.stderr)
            self.assertNotIn("log_scan_truncated", small_run.stdout)
            self.assertIn("SCAN_PROBE scanned=5", small_run.stdout)

            oversized = root / "oversized-access.log"
            # Each record is exactly 100 bytes (7-char prefix + 92 x + LF) so
            # the cap boundary lands at a computable record offset.
            oversized.write_text(
                "".join(f"L{i:04d}: {'x' * 92}\n" for i in range(1000)),
                encoding="utf-8",
                newline="\n",
            )
            total = oversized.stat().st_size
            self.assertEqual(total, 100_000)
            cap = 64 * 1024
            boundary = total - cap
            truncated = run_scan(oversized)
            self.assertEqual(truncated.returncode, 0, truncated.stderr)
            self.assertIn(
                f"log_scan_truncated total_bytes={total} scanned_bytes={cap}",
                truncated.stdout,
            )
            # The boundary lands mid-line (offset 34464 is inside L0344), so
            # the partial remnant must be dropped and the first scanned line
            # is the complete L0345 record.
            self.assertGreaterEqual(boundary % 100, 1)
            self.assertIn(
                "SCAN_PROBE scanned=655 first='L0345: ",
                truncated.stdout,
            )

    def test_cpa_guardrails_doctor_observes_updater_timer_freshness(self) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        self.assertIn("LastTriggerUSec", doctor)
        self.assertIn("timer_last_trigger_age_hours", doctor)
        self.assertIn("timer_last_trigger=STALE", doctor)

    def test_doctor_timer_readout_parses_systemd_durations_and_calendar_triggers(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for doctor timer readout check")
        doctor = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-doctor.sh"
        ).read_text(encoding="utf-8")
        timer_block = doctor.split('echo "==timer=="\n', 1)[1].split(
            'echo "==timer-result=="', 1
        )[0]
        cases = [
            (
                "5min-with-journal",
                "5min",
                "Fri 2026-11-20 04:02:11 UTC",
                "2026-11-18T04:01:02+0000 bwg systemd[1]: Starting cliproxyapi-update.service...\n"
                "2026-11-19T04:03:45+0000 bwg systemd[1]: Starting cliproxyapi-update.service...\n"
                "2026-11-20T04:02:11+0000 bwg systemd[1]: Starting cliproxyapi-update.service...\n",
                "timer_randomized_delay_seconds=300",
                "timer_randomized_delay=5min",
                "timer_last_triggers=2026-11-18T04:01:02+0000,2026-11-19T04:03:45+0000,2026-11-20T04:02:11+0000",
            ),
            (
                "legacy-usec-fallback-last-trigger",
                "300000000",
                "Fri 2026-11-20 04:02:11 UTC",
                "-- No entries --\n",
                "timer_randomized_delay_seconds=300",
                "timer_randomized_delay=5min",
                "timer_last_triggers=Fri 2026-11-20 04:02:11 UTC",
            ),
            (
                "non-minute-seconds",
                "90s",
                "0",
                "",
                "timer_randomized_delay_seconds=90",
                "timer_randomized_delay=90s",
                "timer_last_triggers=UNKNOWN",
            ),
            (
                "compound-duration",
                "1h 30min",
                "n/a",
                "",
                "timer_randomized_delay_seconds=5400",
                "timer_randomized_delay=90min",
                "timer_last_triggers=UNKNOWN",
            ),
            (
                "infinity-unknown",
                "infinity",
                "",
                "",
                "timer_randomized_delay_seconds=UNKNOWN",
                "timer_randomized_delay=UNKNOWN",
                "timer_last_triggers=UNKNOWN",
            ),
        ]
        for (
            label,
            delay_raw,
            last_trigger,
            journal_out,
            want_sec,
            want_dur,
            want_triggers,
        ) in cases:
            with self.subTest(case=label):
                harness = (
                    "set -u\n"
                    "systemctl() {\n"
                    '  case "$1" in\n'
                    "    is-enabled) echo enabled ;;\n"
                    "    is-active) echo active ;;\n"
                    "    show)\n"
                    '      case "$*" in\n'
                    '        *NextElapseUSecRealtime*) echo "Sat 2026-11-21 04:03:00 UTC" ;;\n'
                    '        *TimersCalendar*) echo "{ OnCalendar=*-*-* 04:00:00 ; next_elapse=Sat 2026-11-21 04:03:00 UTC }" ;;\n'
                    '        *RandomizedDelayUSec*) printf "%s\\n" "$STUB_DELAY" ;;\n'
                    '        *LastTriggerUSec*) printf "%s\\n" "$STUB_LAST_TRIGGER" ;;\n'
                    "      esac\n"
                    "      ;;\n"
                    "  esac\n"
                    "}\n"
                    "journalctl() {\n"
                    '  printf "%s" "$STUB_JOURNAL"\n'
                    "}\n" + timer_block
                )
                completed = subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    capture_output=True,
                    text=True,
                    env={
                        **os.environ,
                        "STUB_DELAY": delay_raw,
                        "STUB_LAST_TRIGGER": last_trigger,
                        "STUB_JOURNAL": journal_out,
                    },
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                lines = completed.stdout.splitlines()
                self.assertIn(want_sec, lines, completed.stdout)
                self.assertIn(want_dur, lines, completed.stdout)
                self.assertIn(want_triggers, lines, completed.stdout)

    def test_apply_timer_readback_falls_back_from_path_form_to_unit_name(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for updater timer readback check")
        root = Path(__file__).parents[1]
        apply_source = (root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        fragment_start = apply_source.index("# A daemon-reload that succeeds")
        timer_slot_echo = apply_source.index(
            '  echo "UPDATER_TIMER_SLOT=', fragment_start
        )
        fragment_end = apply_source.index("\nfi\n", timer_slot_echo) + len("\nfi\n")
        fragment = apply_source[fragment_start:fragment_end]
        fragment = (
            fragment.replace(
                "__UPDATER_TIMER_RANDOM_DELAY_USEC__|__UPDATER_TIMER_RANDOM_DELAY_SHORT__",
                "300000000|5min",
            )
            .replace(
                "__UPDATER_TIMEOUT_START_USEC__|__UPDATER_TIMEOUT_START_SHORT__",
                "40min|40min",
            )
            .replace(
                '*"OnCalendar=__UPDATER_TIMER_ONCALENDAR__"*',
                '*"OnCalendar=*-*-* 04:00:00"*',
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            timer = Path(directory) / "cliproxyapi-update.timer"
            timer.write_text("[Timer]\nOnCalendar=*-*-* 04:00:00\n", encoding="utf-8")
            (Path(directory) / "bk").mkdir()
            # systemd 255 returns rc=0 but an empty unit property for a path
            # argument. The basename retry is the deployed compatibility path.
            harness = (
                "set -u\n"
                f'UPDATER_TIMER_UNIT="{timer.as_posix()}"\n'
                f'BK="{(Path(directory) / "bk").as_posix()}"\n'
                "systemctl() {\n"
                '  case "$1" in\n'
                "    start) return 0 ;;\n"
                "    show)\n"
                '      case "$2" in\n'
                '        "$UPDATER_TIMER_UNIT") return 0 ;;\n'
                "        cliproxyapi-update.timer)\n"
                '          case "$4" in\n'
                '            TimersCalendar) echo "{ OnCalendar=*-*-* 04:00:00 ; }" ;;\n'
                "            RandomizedDelayUSec) echo 300000000 ;;\n"
                "          esac\n"
                "          ;;\n"
                "        cliproxyapi-update.service) echo 40min ;;\n"
                "        *) return 1 ;;\n"
                "      esac\n"
                "      ;;\n"
                "    *) return 1 ;;\n"
                "  esac\n"
                "}\n" + fragment
            )
            completed = subprocess.run(
                self._bash_command(bash, "-c", harness),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "UPDATER_TIMER_EFFECTIVE on_calendar=0400 randomized_delay_usec=300000000",
                completed.stdout,
            )
            self.assertIn(
                "UPDATER_TIMEOUT_EFFECTIVE timeout_start=40min", completed.stdout
            )
            self.assertNotIn("ROLLBACK", completed.stdout)

    def test_cpa_guardrails_doctor_asserts_443_lane_limiter_contract(self) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # The 443-fallback lane carries the desktop's own entry path
        # (fq.sciman.top:443 -> xray -> VLESS REALITY -> target :33387 ->
        # subscribe.conf), so its local limiters and transport shape cannot
        # stay outside the read-only contract: on 2026-10-10 the lane was
        # found still holding the pre-2026-10-02 connection budget of 6 while
        # the canonical lane had been raised to 20, it never declared the 429
        # rejection statuses, and it was missing the 128k request-body buffer
        # the canonical lane received on 2026-09-15 (measured: a 40708-byte
        # body written to a temporary file on this lane only).
        self.assertIn("LANE443_CONF=/etc/nginx/conf.d/00-cpa-443-http.conf", doctor)
        self.assertIn("LANE443_SERVER=/etc/nginx/conf.d/subscribe.conf", doctor)
        # The budget invariant is "same as the canonical lane", not a pinned
        # literal, so a future canonical change cannot leave the desktop lane
        # behind again.
        self.assertIn(
            "canonical_conn_budget=$(grep -oE 'limit_conn cpa_cc [0-9]+;'", doctor
        )
        self.assertIn(
            "lane443_conn_budget=$(grep -oE 'limit_conn cpa_cc443 [0-9]+;'", doctor
        )
        self.assertIn('[ "$lane443_conn_budget" = "$canonical_conn_budget" ]', doctor)
        self.assertNotIn("limit_conn cpa_cc443 20;", doctor)
        self.assertIn("limit_req zone=cpa_rl443 burst=10;", doctor)
        self.assertIn("limit_req_status 429;", doctor)
        self.assertIn("limit_conn_status 429;", doctor)
        self.assertIn(
            "add_header Retry-After $cpa_throttle_retry_after443 always;", doctor
        )
        self.assertIn(
            "limit_req_zone $binary_remote_addr zone=cpa_rl443:1m rate=10r/s;", doctor
        )
        self.assertIn("limit_conn_zone $binary_remote_addr zone=cpa_cc443:1m;", doctor)
        # Transport parity. The buffer size is read from the canonical lane
        # for the same reason as the connection budget, and the SSE shape is
        # asserted by directive: a dropped `proxy_buffering off` stalls token
        # delivery and a default 60s read timeout truncates long streams.
        self.assertIn(
            "canonical_body_buffer=$(grep -oE 'client_body_buffer_size [0-9]+[a-z]?;'",
            doctor,
        )
        self.assertIn(
            "lane443_body_buffer=$(grep -oE 'client_body_buffer_size [0-9]+[a-z]?;'",
            doctor,
        )
        self.assertIn('[ "$lane443_body_buffer" = "$canonical_body_buffer" ]', doctor)
        # Scoped to the lane block: the canonical lane's own pinned
        # `client_body_buffer_size 2m;` assertion lives elsewhere in the
        # doctor and must stay a literal, while the desktop lane's copy has to
        # follow the canonical value.
        lane_block = doctor.split(
            "LANE443_CONF=/etc/nginx/conf.d/00-cpa-443-http.conf", 1
        )[1].split("echo gateway-443-lane=ABSENT", 1)[0]
        self.assertNotIn("client_body_buffer_size 128k;", lane_block)
        self.assertNotIn("client_body_buffer_size 2m;", lane_block)
        self.assertIn("grep -Fq 'proxy_buffering off;' \"$LANE443_SERVER\"", doctor)
        self.assertIn("grep -Fq 'proxy_read_timeout 300s;' \"$LANE443_SERVER\"", doctor)
        self.assertIn("grep -Fq 'proxy_send_timeout 300s;' \"$LANE443_SERVER\"", doctor)
        self.assertIn("body-buffer=$lane443_body_buffer", doctor)
        self.assertIn("gateway_443_body_buffer=canonical:", doctor)
        # Presence-conditional: an unprojected lane must not turn the
        # canonical lane's fail-closed counts into a failure.
        self.assertIn("gateway-443-lane=OK", doctor)
        self.assertIn("gateway-443-lane=ABSENT", doctor)
        self.assertIn("mark_fail gateway-443-lane", doctor)
        # The lane's route contract is probed over the desktop entry itself,
        # from loopback (fail2ban ignoreip set) and without a key, so a broken
        # mirror location or a lost auth_request fails the doctor instead of
        # surfacing as a user-visible error on the desktop.
        self.assertIn("gateway-443-lane-route=OK", doctor)
        self.assertIn("mark_fail gateway-443-lane-route", doctor)
        self.assertIn('tls_curl_args "$lane443_server" 33387', doctor)
        self.assertIn("$lane443_valid", doctor)
        self.assertIn('--resolve "$lane443_server:33387:127.0.0.1"', doctor)
        self.assertIn(
            '[ "$lane443_valid" = "401" ] && [ "$lane443_bare" = "404" ]', doctor
        )
        self.assertNotIn("curl --noproxy '*' -k", doctor)

    def test_guardrail_tls_checks_consume_the_shared_verified_helper(self) -> None:
        repo = Path(__file__).parents[1]
        wrapper = (repo / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-VerifiedLocalTlsCaPython", wrapper)
        self.assertIn('.Replace(\n    "__VERIFIED_TLS_HELPER__"', wrapper)
        for name in ("doctor", "apply", "rotate-path"):
            with self.subTest(name=name):
                text = (repo / "scripts/remote" / f"cpa-guardrail-{name}.sh").read_text(
                    encoding="utf-8"
                )
                self.assertIn("__VERIFIED_TLS_HELPER__", text)
                self.assertIn("verified_live_tls_curl_args(host, port)", text)

        contract = repo / "scripts/lib/proxy_tls_contract.ps1"
        source = contract.read_text(encoding="utf-8")
        helper = source.split("return @'\n", 1)[1].split("\n'@", 1)[0]
        namespace: dict[str, Any] = {}
        exec(compile(helper, str(contract), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative_bundle = root / "relative.pem"
            relative_bundle.write_text(
                "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n",
                encoding="ascii",
            )
            previous = os.getcwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    os.environ,
                    {
                        "VPS_LOCAL_TLS_VERIFY_DIR": root.as_posix(),
                        "VPS_SSH_LAUNCHER_TLS_CA_BUNDLE": relative_bundle.name,
                    },
                ):
                    with self.assertRaisesRegex(RuntimeError, "not shell-safe"):
                        namespace["verified_live_tls_curl_args"]("localhost", 8443)
            finally:
                os.chdir(previous)

    def test_path_rotation_reverts_after_signal_during_post_write_probe(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-rotate-path.sh"
        ).read_text(encoding="utf-8")
        for required in (
            "trap cleanup_path_rotation EXIT",
            "trap 'exit 129' HUP",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            "MUTATED=1",
            "ROLLBACK_DONE=1",
            "restore_path() {",
        ):
            self.assertIn(required, source)
        self.assertGreater(
            source.index("MUTATED=1"), source.index("os.replace(temp, path)")
        )
        self.assertLess(
            source.index("MUTATED=1"),
            source.index("assert_path_route_contract_settled"),
        )

    def test_guardrail_apply_forwards_natural_prompts_without_replacement(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("antigravity_fingerprint_swap", source)
        replace = next(
            line.strip() for line in source.splitlines() if '"replace":' in line
        )
        self.assertEqual(replace, '"replace": [],')
        self.assertIn(
            'antigravity_provider_after["sensitive-words-replace"]',
            source,
        )

    def test_cpa_guardrails_requires_the_model_scoped_breaker_generation(
        self,
    ) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        # The per-model breaker is only real where the deployment is: the
        # healthz contract must reject a snapshot without `cooldown_scope` /
        # `model_cooldowns`, i.e. an admission build that parks the whole lane
        # when the upstream names one capped model.
        self.assertIn(
            'snapshot.get("cooldown_scope") not in {"lane", "model", "none"}',
            doctor,
        )
        self.assertIn('not isinstance(snapshot.get("model_cooldowns"), dict)', doctor)
        healthz_block = doctor.split("http://127.0.0.1:8318/healthz", 1)[1]
        self.assertIn("model_cooldowns", healthz_block)

    def test_cpa_guardrails_provider_env_defaults_to_appdata(self) -> None:
        source = read_guardrail_source()

        # Provider credentials live in the user profile next to target.json;
        # the repo root holds no private env file. The root comes from the
        # shared helper so this path cannot drift from the launcher config,
        # and the old inline join must not come back.
        self.assertIn('Resolve-LauncherUserDataPath -Leaf "providers.env"', source)
        self.assertNotIn('$env:APPDATA "vps-ssh-launcher', source)
        self.assertNotIn("副本", source)
        read = source.index("Get-Content -LiteralPath $ProviderEnvPath")
        privacy = source.index(
            "Assert-LauncherPrivateSecretFile `\n  -Path $ProviderEnvPath", 0, read
        )
        self.assertLess(privacy, read, "providers.env must be private before read")

    def test_cpa_guardrails_uses_the_shared_user_data_root(self) -> None:
        # Behaviour, not text: drive the real helper the guardrail resolves
        # providers.env with, under a controlled APPDATA.
        library = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "lib"
            / "project_environment.ps1"
        )
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell 7 is not available")
        with tempfile.TemporaryDirectory() as appdata:
            env = os.environ.copy()
            # The shared root is %APPDATA%\vps-ssh-launcher on Windows and
            # ~/.config/vps-ssh-launcher elsewhere; the fixture has to set the
            # variable that this host actually reads.
            if os.name == "nt":
                env["APPDATA"] = appdata
                expected_root = Path(appdata) / "vps-ssh-launcher"
            else:
                env["HOME"] = appdata
                expected_root = Path(appdata) / ".config" / "vps-ssh-launcher"
            completed = subprocess.run(
                [
                    pwsh,
                    "-NoProfile",
                    "-Command",
                    f". '{library}'; Resolve-LauncherUserDataPath -Leaf 'providers.env'",
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                Path(completed.stdout.strip()).parent,
                expected_root,
            )

    def test_local_projection_action_is_isolated_from_all_remote_writes(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$AuditProjection", source)
        self.assertIn('throw "REFUSE -AuditProjection must be used alone."', source)
        self.assertIn("function Read-ProjLeafText", source)
        self.assertIn("[IO.FileAttributes]::ReparsePoint", source)
        self.assertIn("[Text.UTF8Encoding]::new($false, $true)", source)
        self.assertIn("[char]0", source)
        self.assertIn("Unsubstituted projection placeholder", source)
        self.assertIn("Projected source byte round-trip failed", source)
        self.assertIn("Projected Python/JSON syntax validation failed", source)
        self.assertIn(
            '"scripts/remote/cpa-guardrail-workbuddy-version-check.sh"', source
        )
        self.assertIn(
            "Invoke-BwgRemoteScript -Script $applyScript -CommandTimeout 240",
            source,
        )
        self.assertIn(
            r"(?:A|OA|XA|ZA);[^;]*;([^;]*);[^;]*;([^;]*);([^;]*;[^;]*)", source
        )
        self.assertNotIn("Get-Acl -LiteralPath $auditRoot", source)
        self.assertIn("icacls $auditRoot /save", source)
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell 7 is unavailable")
        root = Path(__file__).resolve().parents[1]
        audit = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-File",
                str(root / "scripts/cpa_bwg_guardrails.ps1"),
                "-Profile",
                "bwg",
                "-AuditProjection",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertIn(
            "CPA_PROJECTION_TEMPLATE=apply:READY",
            audit.stdout,
        )
        self.assertIn(
            "CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection",
            audit.stdout,
        )
        conflict = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-File",
                str(root / "scripts/cpa_bwg_guardrails.ps1"),
                "-Profile",
                "bwg",
                "-AuditProjection",
                "-Apply",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("REFUSE -AuditProjection must be used alone.", conflict.stderr)

    def test_local_projection_audit_rejects_untrusted_source_text(self) -> None:
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell 7 is unavailable")
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        prefix = _guardrail_preparation_fixture(source, include_updater_timeout=False)
        prefix = (
            "$project_environment_path = '"
            + str(root / "scripts/lib/project_environment.ps1").replace("'", "''")
            + "'\n$scriptDir = '"
            + str(root / "scripts/remote").replace("'", "''")
            + "'\n$repoRoot = '"
            + str(root).replace("'", "''")
            + "'\n"
            + prefix
        )
        trailer = """
foreach ($case in @(
  @("blank", [Text.UTF8Encoding]::new($false), ""),
  @("nul", [Text.UTF8Encoding]::new($false), "source`0value"),
  @("unterminated", [Text.UTF8Encoding]::new($false, $true), $null),
  @("oversize", [Text.UTF8Encoding]::new($false), ("x" * (8MB + 1)))
)) {
  $path = Join-Path ([IO.Path]::GetTempPath()) ("projection-source-" + [guid]::NewGuid().ToString("N"))
  try {
    if ($case[2] -is [string]) {
      [IO.File]::WriteAllText($path, [string]$case[2], $case[1])
    } else {
      [IO.File]::WriteAllBytes($path, [byte[]](0x41, 0xc3, 0x28))
    }
    try {
      $null = Read-ProjLeafText -Path $path -Description "fixture"
    } catch {
      Write-Output ($case[0] + "=REJECTED:" + $_.Exception.Message)
      continue
    }
    throw "untrusted projection source was accepted: $($case[0])"
  } finally {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  }
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            scripts = fixture / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(
                root / "scripts/cpa-updater-timer-contract.ps1",
                scripts / "cpa-updater-timer-contract.ps1",
            )
            shutil.copytree(root / "scripts/remote", scripts / "remote")
            shutil.copytree(root / "scripts/lib", scripts / "lib")
            shutil.copytree(root / ".git", fixture / ".git")
            driver_prefix = prefix
            (fixture / "connect.ps1").write_text(
                "throw 'SSH MUST NOT RUN'\n", encoding="utf-8"
            )
            driver = fixture / "prepare.ps1"
            driver.write_text(driver_prefix + trailer, encoding="utf-8")
            driver_env = os.environ.copy()
            driver_env["VPS_TEST_SCRIPTS"] = str(scripts)
            driver_env["VPS_TEST_ROOT"] = str(fixture)
            result = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(driver)],
                env=driver_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        for case in ("blank", "nul", "unterminated", "oversize"):
            self.assertIn(f"{case}=REJECTED:", result.stdout)

    def test_cpa_guardrails_payloads_are_valid_bash(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = read_guardrail_source()
        payloads = {
            "doctor": source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "rotate": source.split("$rotateScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "deactivate_oauth_luna": source.split(
                "$deactivateOAuthLunaScript = @'\n", 1
            )[1].split("\n'@", 1)[0],
            "quarantine_oauth_luna": source.split("$quarantineScript = @'\n", 1)[
                1
            ].split("\n'@", 1)[0],
            "apply": source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0],
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                completed = subprocess.run(
                    self._bash_command(bash, "-n"),
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                )
                self.assertEqual(completed.returncode, 0, output)

    def test_rotate_path_route_assertion_settles_after_reload(self) -> None:
        source = read_guardrail_source()
        payload = source.split("$rotateScript = @'\n", 1)[1].split("\n'@", 1)[0]
        # Real rotation 2026-11-02: both the forward and the rollback route
        # assertion tripped on probes fired within milliseconds of
        # `systemctl reload nginx` while the conf state itself was correct.
        # The wrapper must settle, then retry exactly once, at both call
        # sites (forward check and rollback verification).
        self.assertIn("assert_path_route_contract_settled", payload)
        self.assertEqual(payload.count("assert_path_route_contract_settled"), 3)
        self.assertIn(
            "|| rollback_failed=1",
            payload.split("assert_path_route_contract_settled", 1)[1],
        )
        # The raw assertion stays a pure single-shot probe.
        self.assertIn("assert_path_route_contract() {", payload)

        # Behavioural probe: the real wrapper runs against stubbed curl/ss.
        # The settle windows stay mechanism-level (bounded sleeps before the
        # first attempt and before the single retry) without pinning the
        # durations; the probe proves the retry happens at most once.
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for the settle-wrapper probe")
        raw_fn = (
            "assert_path_route_contract() {"
            + payload.split("assert_path_route_contract() {", 1)[1].split("\n}\n", 1)[0]
            + "\n}\n"
        )
        settled_body = payload.split("assert_path_route_contract_settled() {", 1)[
            1
        ].split("\n}\n", 1)[0]
        self.assertIn("sleep ", settled_body)
        settled_fn = (
            "assert_path_route_contract_settled() {"
            + settled_body.replace("sleep 2", "sleep 0").replace("sleep 4", "sleep 0")
            + "\n}\n"
        )
        active_path = "abcdef0123456789"
        old_path = "0000000000000000"

        def run_settled(
            active_first: str, active_second: str
        ) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as directory:
                conf = Path(directory) / "cpa-site.conf"
                conf.write_text(
                    "server {\n  server_name example.com;\n}\n",
                    encoding="utf-8",
                )
                calls_file = Path(directory) / "probe-calls"
                stub = (
                    "curl() {\n"
                    '  local url="${*: -1}"\n'
                    '  local calls=$(( $(cat "$PROBE_CALLS") + 1 ))\n'
                    '  printf %s "$calls" > "$PROBE_CALLS"\n'
                    "  local code\n"
                    '  case "$url" in\n'
                    f'    *"/{active_path}/v1/models")\n'
                    f'      if [ "$calls" -le 4 ]; then code={active_first!r}; '
                    f"else code={active_second!r}; fi ;;\n"
                    "    *) code=404 ;;\n"
                    "  esac\n"
                    '  printf %s "$code"\n'
                    "}\n"
                    # Mimic the real ss column layout: the doctor greps for
                    # the listen address followed by whitespace, and GNU grep
                    # can never match the line's own trailing newline.
                    'ss() { printf "LISTEN 0 128 0.0.0.0:8443 0.0.0.0:*\\n"; }\n'
                )
                script_text = (
                    "set -u\n"
                    f'PROBE_CALLS="{self._bash_path(bash, calls_file)}"\n'
                    ': > "$PROBE_CALLS"\n'
                    f'NGINX_CONF="{self._bash_path(bash, conf)}"\n'
                    f'DIAGNOSTIC_DIR="{self._bash_path(bash, Path(directory))}"\n'
                    "tls_curl_args() {\n"
                    "  printf '%s\\n%s' '--cacert' '/tmp/private-stub.pem'\n"
                    "}\n" + stub + raw_fn + settled_fn + "\n"
                    f'if assert_path_route_contract_settled "{active_path}" '
                    f'"{old_path}"; then\n'
                    '  echo "SETTLED_RC=0"\n'
                    "else\n"
                    '  echo "SETTLED_RC=$?"\n'
                    "fi\n"
                    'echo "PROBE_CALLS=$(cat "$PROBE_CALLS")"\n'
                )
                return subprocess.run(
                    self._bash_command(bash, "-c", script_text),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )

        # A settled reload passes on the first round: exactly one probe round.
        steady = run_settled("401", "401")
        self.assertEqual(steady.returncode, 0, steady.stderr)
        self.assertIn("SETTLED_RC=0", steady.stdout)
        self.assertIn("PROBE_CALLS=4", steady.stdout)

        # A probe that lands during worker turnover fails once, then the
        # single retry passes: exactly two rounds, no third.
        flaky = run_settled("000", "401")
        self.assertEqual(flaky.returncode, 0, flaky.stderr)
        self.assertIn("SETTLED_RC=0", flaky.stdout)
        self.assertIn("PROBE_CALLS=8", flaky.stdout)

        # A genuine outage fails closed, still after exactly one retry.
        outage = run_settled("000", "000")
        self.assertIn("SETTLED_RC=1", outage.stdout)
        self.assertIn("PROBE_CALLS=8", outage.stdout)

    def test_cpa_oauth_luna_deactivation_is_explicit_and_credential_destructive(
        self,
    ) -> None:
        source = read_guardrail_source()
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        # Destructive OAuth removal must exist only behind the explicit
        # switch; the payload itself must never archive the auth directory
        # (credentials would survive in backups).
        self.assertIn("-DeactivateOAuthLuna", source)
        self.assertIn("-not $DeactivateOAuthLuna", source)
        self.assertNotIn('cp -a "$AUTH_DIR"', payload)

    def test_cpa_oauth_luna_deactivation_survival_contract_is_manifest_derived(
        self,
    ) -> None:
        manifest = json.loads(
            (
                Path(__file__).parents[1] / "scripts/remote/cpa_provider_routes.json"
            ).read_text(encoding="utf-8")
        )
        provider_aliases = {
            model["alias"]
            for provider in manifest["providers"]
            for model in provider["models"]
        }
        optional = {
            model
            for provider in manifest["providers"]
            for model in provider.get("optional_models", [])
        }
        oauth_aliases = {
            model["alias"]
            for route in manifest["oauth_routes"]
            for model in route["models"]
        }
        antigravity_aliases = {
            model["alias"]
            for route in manifest["oauth_routes"]
            if route["name"] == "antigravity-oauth"
            for model in route["models"]
        }

        def catalog(*ids: str) -> dict[str, Any]:
            return {"data": [{"id": model} for model in ids]}

        # Full post-removal catalog: every provider alias survives, no OAuth
        # alias remains.
        completed = self._run_oauth_retire_catalog_contract(catalog(*provider_aliases))
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # Optional aliases may be absent (availability-filtered catalog).
        required_only = provider_aliases - optional
        completed = self._run_oauth_retire_catalog_contract(catalog(*required_only))
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # Antigravity (the other OAuth route) keeps serving after Luna is
        # destroyed: its aliases surviving is success, not a contract breach.
        completed = self._run_oauth_retire_catalog_contract(
            catalog(*(required_only | antigravity_aliases))
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        # A cooled-down channel hides a required alias: informational marker,
        # not a failure — the doctor gate owns persistent absence.
        incomplete = required_only - {"glm-5.3-flash"}
        completed = self._run_oauth_retire_catalog_contract(catalog(*incomplete))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "CATALOG_INCOMPLETE missing=glm-5.3-flash", completed.stdout.decode()
        )

        # A surviving OAuth alias means the removal failed its own goal.
        completed = self._run_oauth_retire_catalog_contract(
            catalog(*(required_only | oauth_aliases))
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("CPA_ROUTE_VERIFICATION_FAILED", completed.stderr.decode())

        # Unknown IDs are not an alternate namespace; they fail closed.
        completed = self._run_oauth_retire_catalog_contract(
            catalog(*required_only, "ghost-model")
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unknown=ghost-model", completed.stderr.decode())

    def test_cpa_oauth_luna_deactivation_refuses_empty_manifest_contract(
        self,
    ) -> None:
        source = read_guardrail_source()
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        block = payload.split("python3 - \"$RETIRE_CATALOG\" <<'PY'\n", 1)[1].split(
            "\nPY\n", 1
        )[0]
        empty_b64 = base64.b64encode(
            json.dumps({"providers": [], "oauth_routes": []}).encode("utf-8")
        ).decode("ascii")
        script = block.replace("__CPA_OAUTH_RETIRE_MANIFEST_B64__", empty_b64)
        completed = subprocess.run(
            [sys.executable, "-c", script, os.devnull],
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("REFUSE invalid route manifest", completed.stderr.decode())

    def test_cpa_oauth_luna_deactivation_client_key_reads_v8_layout(self) -> None:
        """Execute the KEY extraction block against a v8 fixture config.

        RecoverAfterReset (7807211) and the muse apply rollback were one
        failure class: a legacy top-level api-keys read that only text
        assertions covered. Run the real block so a v8 config yields the
        client key and a legacy-only config fails closed.
        """
        import yaml

        source = read_guardrail_source()
        payload = source.split("$deactivateOAuthLunaScript = @'\n", 1)[1].split(
            "\n'@", 1
        )[0]
        block = payload.split("KEY=$(python3 - \"$CONFIG\" <<'PY'\n", 1)[1].split(
            "\nPY\n", 1
        )[0]

        def run_block(config: dict[str, Any]) -> subprocess.CompletedProcess:
            with tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "config.yaml"
                config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
                return subprocess.run(
                    [sys.executable, "-c", block, str(config_path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

        # v8 file layout: client key at access.api-keys, channel-family
        # mapping at the top-level api-keys remnant.
        completed = run_block(
            {
                "access": {"api-keys": ["client-key-fixture"]},
                "api-keys": {"openai-compatibility": []},
            }
        )
        self.assertEqual(
            completed.returncode, 0, completed.stderr.decode("utf-8", "replace")
        )
        self.assertEqual(completed.stdout.decode().strip(), "client-key-fixture")

        # A legacy-only config (pre-v8 key list at the top level) must fail
        # closed instead of feeding an empty bearer token to the readiness
        # loop after the irreversible OAuth removal.
        completed = run_block({"api-keys": ["client-key-fixture"]})
        self.assertNotEqual(completed.returncode, 0)

    def test_cpa_key_read_guards_are_reachable_under_set_e(self) -> None:
        """Run the KEY read + guard shell fragments under set -e.

        A failing command substitution aborts a set -e script before any
        following guard, so ``) || true`` is what makes the named
        refusal/rollback messages real. Quarantine's EXIT trap rolls back
        either way but only reports the generic transaction_failed reason;
        deactivate has no trap and would die silently after the
        irreversible removal.
        """
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        import yaml

        executable = str(Path(sys.executable).as_posix())
        cases = {
            "deactivate-oauth-luna": (
                "deactivateOAuthLunaScript",
                "KEY=$(python3 - \"$CONFIG\" <<'PY'",
                "CONFIG",
                "OAUTH_DEACTIVATE_KEY_READ_FAILED",
            ),
            "quarantine-oauth-luna": (
                "quarantineScript",
                "KEY=$(python3 - \"$CONFIG\" <<'PY'",
                "CONFIG",
                "REFUSE missing_client_key",
            ),
            "apply": (
                "applyScript",
                "KEY=$(python3 - \"$DIR/config.yaml\" <<'PY'",
                "DIR",
                "REFUSE missing_client_key",
            ),
        }
        for name, (variable, marker, path_var, expected_message) in cases.items():
            with self.subTest(script=name):
                source = read_guardrail_source()
                payload = source.split(f"${variable} = @'\n", 1)[1].split("\n'@", 1)[0]
                fragment = (
                    marker
                    + payload.split(marker, 1)[1].split("\nfi\n", 1)[0]
                    + "\nfi\n"
                ).replace("$(python3 - ", f'$("{executable}" - ', 1)

                def run_fragment(
                    config: dict[str, Any],
                    *,
                    fragment: str = fragment,
                    path_var: str = path_var,
                ) -> subprocess.CompletedProcess:
                    with tempfile.TemporaryDirectory() as directory:
                        config_path = Path(directory) / "config.yaml"
                        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
                        # A forward-slash Windows path is unambiguous to both
                        # Git Bash and the Windows interpreter; MSYS2 argument
                        # translation of /c/... forms is heuristic.
                        location = config_path.as_posix()
                        if path_var == "DIR":
                            location = str(Path(location).parent)
                        script_text = (
                            "set -euo pipefail\n"
                            f'{path_var}="{location}"\n'
                            "restore_all() { echo STUB_RESTORE_ALL; }\n"
                            + fragment
                            + '\necho "CAPTURED:$KEY"\n'
                        )
                        return subprocess.run(
                            self._bash_command(bash, "-c", script_text),
                            capture_output=True,
                            timeout=30,
                            check=False,
                        )

                # v8 layout: the guard stays silent and KEY captures the key.
                completed = run_fragment(
                    {
                        "access": {"api-keys": ["client-key-fixture"]},
                        "api-keys": {"openai-compatibility": []},
                        "routing": {"force-model-prefix": True},
                    }
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode("utf-8", "replace"),
                )
                self.assertEqual(
                    completed.stdout.decode().strip(), "CAPTURED:client-key-fixture"
                )

                # Legacy-only layout: the named guard fires, not a bare abort.
                completed = run_fragment({"api-keys": ["client-key-fixture"]})
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected_message, completed.stdout.decode())

    def test_cpa_management_read_guards_are_reachable_under_pipefail(self) -> None:
        """Run the apply MGMT grep reads under set -euo pipefail.

        A key-less config.yaml (a realistic panel-write drift state) makes
        grep exit 1; without ``|| true`` the assignment aborts the script
        before both the pass-through and the weak-key REFUSE become
        reachable, and the operator sees a bare exit 1.
        """
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        import yaml

        source = read_guardrail_source()
        payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        marker = "MGMT_APPLY_ALLOW=$(grep"
        fragment = marker + payload.split(marker, 1)[1].split("\nfi\n", 1)[0] + "\nfi\n"

        def run_fragment(config: dict[str, Any]) -> subprocess.CompletedProcess:
            with tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "config.yaml"
                config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
                script_text = (
                    "set -euo pipefail\n"
                    f'DIR="{config_path.parent.as_posix()}"\n'
                    "restore_all() { echo STUB_RESTORE_ALL; }\n"
                    + fragment
                    + '\necho "CAPTURED:$MGMT_APPLY_ALLOW:$MGMT_APPLY_KEY"\n'
                )
                return subprocess.run(
                    self._bash_command(bash, "-c", script_text),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

        # Key-less config: the guard's pass-through branch must be reachable
        # (both reads empty), not a silent pre-mutation abort.
        completed = run_fragment({"access": {"api-keys": ["client-key-fixture"]}})
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout.decode("utf-8", "replace")
            + completed.stderr.decode("utf-8", "replace"),
        )
        self.assertIn("CAPTURED::", completed.stdout.decode())

        # allow-remote with a weak key: the named REFUSE must fire, which
        # also proves the KEY read did not abort the script first.
        completed = run_fragment(
            {"management": {"allow-remote": True, "secret-key": "short"}}
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "REFUSE remote management enabled without a strong",
            completed.stdout.decode(),
        )

    def test_apply_rolls_back_on_hup_when_no_exit_status_is_set(self) -> None:
        # A dropped SSH channel Hangups the remote payload. bash still runs the
        # EXIT trap on an untrapped fatal signal, but with $? == 0, so an
        # EXIT-only rollback silently keeps a half-applied host AND reports
        # success. The trap set must therefore cover HUP itself, and the
        # rollback decision must key off the transaction phase.
        source = read_guardrail_source()
        self.assertIn("trap 'exit 129' HUP", source)
        self.assertLess(
            source.index("MUTATED=0"),
            source.index("trap cleanup_guardrail_preflight EXIT"),
        )
        self.assertLess(
            source.index("trap cleanup_guardrail_preflight EXIT"),
            source.index("INTL_COUNTER_DIR=$(mktemp"),
        )
        # The later EXIT handler is defined after its diagnostic directory
        # exists, but is installed after set -u has already been active.
        self.assertLess(
            source.index("MUTATED=0"),
            source.index("cleanup_preflight_and_mutated()"),
        )
        self.assertIn('if [ "${MUTATED:-0}" = 1 ] && [ "$status" -ne 0 ]; then', source)
        self.assertIn('if [ "$rc" -ne 0 ] || [ "$MUTATED" -eq 1 ]; then', source)
        self.assertIn("MUTATED=1", source)
        self.assertGreaterEqual(source.count("trap - EXIT HUP INT TERM"), 2)

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        payload = source[
            source.index("rollback_on_exit() {") : source.index("\nif ! grep -Eq")
        ]
        prelude = (
            "set -Eeuo pipefail\n"
            "MUTATED=0\n"
            "ROLLBACK_DONE=0\n"
            "restore_all() { echo RESTORE_RAN; }\n" + payload
        )
        with tempfile.TemporaryDirectory() as tmp:
            # 1. The real signal path. The probe signals ITSELF from a
            # subshell: Python on Windows has no SIGHUP to deliver, and the
            # contract under test is bash-side. `sleep & wait` keeps the trap
            # dispatch immediate instead of deferring it to the end of the
            # foreground command.
            harness = Path(tmp) / "hup_probe.sh"
            harness.write_text(
                prelude + "trap rollback_on_exit EXIT\n"
                "trap 'exit 129' HUP\n"
                "trap 'exit 130' INT\n"
                "trap 'exit 143' TERM\n"
                "MUTATED=1\n"
                "echo MUTATED_AND_RUNNING\n"
                "( sleep 1; kill -HUP $$ ) &\n"
                # A builtin loop, not `sleep 30 & wait`: bash defers a trapped
                # signal until the running foreground command returns, and a
                # blocked `wait` on this shell never returns early, so the
                # probe would burn the full timeout before the trap fired.
                "for _ in $(seq 1 400); do sleep 0.05; done\n"
                "echo UNEXPECTED_CONTINUATION\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                self._bash_command(bash, str(harness)),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertNotIn("UNEXPECTED_CONTINUATION", completed.stdout)
            self.assertIn("RESTORE_RAN", completed.stdout, completed.stderr)
            self.assertIn(
                "ROLLBACK transaction_failed", completed.stdout, completed.stderr
            )
            self.assertNotEqual(completed.returncode, 0)

            # 2. The phase guard on its own: an untrapped fatal signal runs the
            # EXIT trap with $? == 0. Restoring must not depend on the status.
            bare = self._run_rollback_probe(bash, tmp, "zero_status", "MUTATED=1")
            self.assertEqual(bare.returncode, 0, bare.stderr)
            self.assertIn("RESTORE_RAN", bare.stdout)
            self.assertIn("ROLLBACK transaction_failed", bare.stdout)

            # 3. Before the mutation phase nothing is restored on a clean exit.
            untouched = self._run_rollback_probe(bash, tmp, "clean", "MUTATED=0")
            self.assertEqual(untouched.returncode, 0, untouched.stderr)
            self.assertNotIn("RESTORE_RAN", untouched.stdout)

    def _run_rollback_probe(
        self, bash: str, directory: str, name: str, mutated: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the real rollback_on_exit() around one no-op body."""
        source = read_guardrail_source()
        payload = source[
            source.index("rollback_on_exit() {") : source.index("\nif ! grep -Eq")
        ]
        script = Path(directory) / f"{name}_probe.sh"
        script.write_text(
            "set -Eeuo pipefail\n"
            "ROLLBACK_DONE=0\n"
            "restore_all() { echo RESTORE_RAN; }\n"
            + payload
            + "trap rollback_on_exit EXIT\n"
            f"{mutated}\n"
            "true\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            self._bash_command(bash, str(script)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        return completed

    def test_cpa_apply_public_route_reads_keep_named_rollback_reasons(self) -> None:
        """The post-mutation public-route greps keep their named ROLLBACK
        reasons reachable: the EXIT trap restores either way, but only a
        ``|| true`` guard lets the specific reason print instead of dying
        silently at the assignment.

        Behavioural probe: the real read lines extracted from the apply
        payload run under ``set -euo pipefail`` against fixture nginx confs.
        A dropped ``|| true`` (grep exits 1 on a conf without the path) would
        abort before the named reason and turn this red.
        """
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for the route-read probe")
        source = read_guardrail_source()
        apply_payload = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        readers = [
            line
            for line in apply_payload.splitlines()
            if line.startswith(("PREFIX=$(grep", "RANDOM_PATH_AFTER=$(grep"))
        ]
        self.assertEqual(len(readers), 2)
        for reader in readers:
            # The read itself must stay failure-tolerant: the conf may lack
            # the pattern entirely, and the guard reason must stay reachable.
            self.assertIn("|| true)", reader)

        def run_reads(conf_text: str) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as directory:
                conf = Path(directory) / "cpa-site.conf"
                conf.write_text(conf_text, encoding="utf-8", newline="\n")
                script_text = (
                    "set -euo pipefail\n"
                    f'NGINX_CONF="{conf.as_posix()}"\n'
                    + "\n".join(readers)
                    + "\necho 'ROLLBACK gateway_throttle_contract'\n"
                    'echo "CAPTURED:$PREFIX:$RANDOM_PATH_AFTER"\n'
                )
                return subprocess.run(
                    self._bash_command(bash, "-c", script_text),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )

        projected = run_reads(
            "# managed by cpa-guardrail-apply\n"
            "location ~ ^/0123456789abcdef/v1/ { return 401; }\n"
        )
        self.assertEqual(projected.returncode, 0, projected.stderr)
        self.assertIn("ROLLBACK gateway_throttle_contract", projected.stdout)
        self.assertIn("CAPTURED:0123456789abcdef:0123456789abcdef", projected.stdout)

        pathless = run_reads("server {\n  listen 443 ssl;\n}\n")
        self.assertEqual(pathless.returncode, 0, pathless.stderr)
        self.assertIn("ROLLBACK gateway_throttle_contract", pathless.stdout)
        self.assertIn("CAPTURED::", pathless.stdout)

    def test_cpa_oauth_quarantine_is_reversible_and_non_destructive(self) -> None:
        source = read_guardrail_source()
        payload = source.split("$quarantineScript = @'\n", 1)[1].split("\n'@", 1)[0]
        # Two explicit switches, exclusive with every other transaction mode.
        self.assertIn("[switch]$QuarantineOAuthLuna", source)
        self.assertIn("[switch]$RestoreOAuthLuna", source)
        self.assertIn("-not $QuarantineOAuthLuna", source)
        self.assertIn("-not $RestoreOAuthLuna", source)
        self.assertIn("$quarantineScript.Replace(", source)
        self.assertIn("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__", source)
        # Reversibility: the transaction only rewrites the OAuth exclusion list
        # plus a marker file. It must never read, copy, delete or replay
        # credential material, and never clear quota/cooldown state.
        self.assertIn("exclusions_after[provider] = after", payload)
        self.assertIn("[switch]$QuarantineAntigravity", source)
        self.assertIn("[switch]$RestoreAntigravity", source)
        self.assertIn("-not $QuarantineAntigravity", source)
        self.assertIn("-not $RestoreAntigravity", source)
        self.assertIn("[switch]$QuarantineClaudeOAuth", source)
        self.assertIn("[switch]$RestoreClaudeOAuth", source)
        self.assertIn("write_config_in_place(config_path, candidate, 0o600)", payload)
        claude_catalog_read = payload.index("before_status=$(curl")
        claude_idle_samples = payload.index("for sample in 1 2 3")
        config_transaction = payload.index(
            'if ! python3 - "$CONFIG" "$MARKER" "$MODE" <<\'PY\''
        )
        self.assertLess(claude_catalog_read, claude_idle_samples)
        self.assertLess(claude_idle_samples, config_transaction)
        self.assertIn(
            'if [ "$CPA_OAUTH_QUARANTINE_PROVIDER" != "claude" ]; then', payload
        )
        self.assertIn("marker_path.unlink()", payload)
        self.assertNotIn("reset-quota", payload)
        self.assertNotIn('cp -a "$AUTH_DIR"', payload)
        self.assertNotIn("--codex-device-login", payload)
        self.assertIn("QUOTA_STATE_RESET=no", payload)
        self.assertIn("OAUTH_CREDENTIAL_RETAINED=yes", payload)
        # Both directions verify the routed catalog and roll back on failure.
        self.assertIn("QUARANTINE_APPLIED", payload)
        self.assertIn("QUARANTINE_RELEASED", payload)
        self.assertIn("ROLLBACK_VERIFIED", payload)
        # A refusal must not cost a container restart. When config.yaml is
        # byte-identical to the backup nothing was mutated, so the rollback
        # restores files, skips the restart and reports the skip instead of
        # claiming a verified rollback it never performed.
        self.assertIn('cmp -s "$BK/config.yaml" "$CONFIG"', payload)
        self.assertIn("ROLLBACK_SKIPPED no_mutation", payload)
        restore_all = payload.split("restore_all() {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("ROLLBACK_SKIPPED no_mutation", restore_all)
        skipped_branch, verified_branch = restore_all.split(
            "ROLLBACK_SKIPPED no_mutation", 1
        )
        self.assertNotIn(
            "docker restart cli-proxy-api",
            skipped_branch,
            "the no-mutation path must return before any container restart",
        )
        self.assertIn("docker restart cli-proxy-api", verified_branch)
        self.assertIn("survived", payload)
        self.assertIn("still_blocked", payload)
        # -Apply recomputes the exclusion list, so it must not silently undo an
        # explicit quarantine decision.
        apply_script = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.assertIn("REFUSE OAuth lane quarantine is active", apply_script)
        # The doctor owns the state readback, including the unmarked-block case.
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.assertIn("==oauth-quarantine==", doctor)
        self.assertIn("oauth_quarantine=UNMARKED_OAUTH_BLOCK", doctor)
        self.assertIn("mark_fail oauth-quarantine", doctor)

    def test_restore_all_reports_first_failed_step(self) -> None:
        root = Path(__file__).parents[1]
        source = (root / "scripts/remote/cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        restore_all = source.split("restore_all() {\n", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("mark_rollback_failed() {", restore_all)
        self.assertEqual(restore_all.count("rollback_failed=1"), 1)
        self.assertIn(
            'echo "ROLLBACK_FAILED first_step=${ROLLBACK_FIRST_FAILED_STEP:-unknown}"',
            restore_all,
        )

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable for rollback first-failure probe")
        helper_start = source.index("  mark_rollback_failed() {")
        helper_end = source.index("\n  # Both generations", helper_start)
        helper = source[helper_start:helper_end]
        harness = (
            "set -u\n"
            "rollback_failed=0\n"
            "ROLLBACK_FIRST_FAILED_STEP=\n"
            + helper
            + "\nfalse || mark_rollback_failed\n"
            "first_step=$ROLLBACK_FIRST_FAILED_STEP\n"
            "false || mark_rollback_failed\n"
            "second_step=$ROLLBACK_FIRST_FAILED_STEP\n"
            'printf "FIRST=%s\\nSECOND=%s\\n" "$first_step" "$second_step"\n'
        )
        completed = subprocess.run(
            self._bash_command(bash, "-c", harness),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertRegex(completed.stdout, r"FIRST=restore_all:[0-9]+")
        output_lines = completed.stdout.splitlines()
        self.assertEqual(
            output_lines[0].split("=", 1)[1], output_lines[1].split("=", 1)[1]
        )

    def test_cpa_oauth_quarantine_state_change_is_reversible_and_policy_clean(
        self,
    ) -> None:
        # claude-oauth retired 2026-10-20: the real manifest no longer
        # carries the route, so the quarantine payload correctly refuses it;
        # the shared machinery stays covered by the codex/antigravity cases.
        # Re-attach must reintroduce this case together with the route.
        for provider in ("codex", "antigravity"):
            with self.subTest(provider=provider):
                self._exercise_provider_quarantine(provider)

    def test_quarantine_topology_accepts_mixed_providers_without_leaking_auth(
        self,
    ) -> None:
        source = read_guardrail_source()
        payload = source.split("$quarantineScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = payload.split("python3 - \"$AUTH_DIR\" <<'PY'\n", 1)[1].split(
            "\nPY\n", 1
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # claude-oauth retired 2026-10-20: the real manifest no longer
            # carries the route, so the quarantine payload correctly refuses
            # it; the shared machinery stays covered by the codex/antigravity
            # cases. Re-attach must reintroduce this case with the route.
            for provider in ("codex", "antigravity"):
                (root / f"{provider}.json").write_text(
                    json.dumps(
                        {
                            "type": provider,
                            "access_token": "fixture-secret",
                            "refresh_token": "fixture-refresh",
                        }
                    ),
                    encoding="utf-8",
                )
            (root / "antigravity-second.json").write_text(
                json.dumps(
                    {
                        "type": "antigravity",
                        "access_token": "fixture-secret-2",
                        "refresh_token": "fixture-refresh-2",
                    }
                ),
                encoding="utf-8",
            )
            for provider in ("codex", "antigravity"):
                with self.subTest(provider=provider):
                    result = subprocess.run(
                        [sys.executable, "-c", block, directory],
                        capture_output=True,
                        env={**os.environ, "CPA_OAUTH_QUARANTINE_PROVIDER": provider},
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = (
                        b"ACTIVE_OAUTH_FILES=2"
                        if provider == "antigravity"
                        else b"ACTIVE_OAUTH_FILES=1"
                    )
                    self.assertEqual(result.stdout.strip(), expected)
                    self.assertNotIn(b"fixture-secret", result.stdout + result.stderr)

    def test_antigravity_doctor_executes_active_and_quarantined_states(self) -> None:
        import runpy
        import yaml

        repo = Path(__file__).parents[1]
        payload = (repo / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        block = (
            payload.split('echo "==antigravity-lane=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        policy = runpy.run_path(str(repo / "scripts/remote/cpa_policy.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "cpa_policy.py",
                "cpa_provider_routes.json",
                "cpa-admission.json",
            ):
                shutil.copyfile(repo / "scripts/remote" / name, root / name)
            candidate = self._valid_cpa_policy_config(policy)
            config_path = root / "config.yaml"

            def run() -> subprocess.CompletedProcess[bytes]:
                config_path.write_text(yaml.safe_dump(candidate), encoding="utf-8")
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        block,
                        str(config_path),
                        str(root / "cpa_provider_routes.json"),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"antigravity_lane=OK", result.stdout)
            before = list(candidate["oauth"]["excluded-models"]["antigravity"])
            first_ag = ANTIGRAVITY_OAUTH_ROUTE_ALIASES[0]
            candidate["oauth"]["excluded-models"]["antigravity"].append(first_ag)
            self.assertNotEqual(run().returncode, 0)
            # The quarantine marker must name every configured lane alias
            # (cpa_policy.load_quarantine_aliases), and a quarantined apply
            # projects all of them into the provider exclusions.
            aliases = list(ANTIGRAVITY_OAUTH_ROUTE_ALIASES)
            candidate["oauth"]["excluded-models"]["antigravity"].extend(
                alias for alias in aliases if alias != first_ag
            )
            marker = {
                "version": 1,
                "provider": "antigravity",
                "state": "quarantined",
                "aliases": aliases,
                "previous_provider_exclusions": before,
                "applied_provider_exclusions": candidate["oauth"]["excluded-models"][
                    "antigravity"
                ],
            }
            (root / "antigravity-quarantine.json").write_text(
                json.dumps(marker), encoding="utf-8"
            )
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"antigravity_lane=QUARANTINED allowed=none", result.stdout)

    def test_claude_doctor_executes_resurrection_guard(self) -> None:
        """The claude-oauth lane retired 2026-10-20; the doctor's claude
        section must pass on the retired manifest and fail closed the moment
        a claude-oauth route reappears without a reviewed re-attach."""
        import json

        repo = Path(__file__).parents[1]
        payload = (repo / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        block = (
            payload.split('echo "==claude-lane=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(
                (repo / "scripts/remote/cpa_provider_routes.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest_path = root / "cpa_provider_routes.json"

            def run() -> subprocess.CompletedProcess[bytes]:
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                return subprocess.run(
                    [sys.executable, "-c", block, str(manifest_path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )

            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"claude_lane=RETIRED", result.stdout)

            manifest["oauth_routes"].append(
                {
                    "name": "claude-oauth",
                    "shared_consumption": True,
                    "workload_class": "subscription-oauth",
                    "admission_lane": "claude-oauth",
                    "models": [
                        {"name": "claude-haiku-5-5", "alias": "claude-haiku-5-5"}
                    ],
                }
            )
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"claude_lane=RESURRECTED", result.stdout)

    def test_claude_lane_quarantine_requires_user_directive(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/cpa_bwg_guardrails.ps1"
        ).read_text(encoding="utf-8")
        # 2026-10-17 an agent session quarantined the claude lane on its own
        # ToS reading; the switch must refuse to run without an explicit user
        # directive so lane shutdowns carry authorization, not judgment.
        self.assertIn("[string]$QuarantineUserDirective", source)
        self.assertIn(
            "QUARANTINE_USER_DIRECTIVE=present length=$($QuarantineUserDirective.Length)",
            source,
        )
        self.assertNotIn("QUARANTINE_USER_DIRECTIVE=$QuarantineUserDirective", source)
        self.assertIn(
            "REFUSE -QuarantineClaudeOAuth without -QuarantineUserDirective",
            source,
        )

    def _exercise_provider_quarantine(self, provider: str) -> None:
        import runpy

        import yaml

        source = read_guardrail_source()
        payload = source.split("$quarantineScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = payload.split('python3 - "$CONFIG" "$MARKER" "$MODE" <<\'PY\'\n', 1)[
            1
        ].split("\nPY\n", 1)[0]
        manifest_b64 = base64.b64encode(
            (
                Path(__file__).parents[1] / "scripts/remote/cpa_provider_routes.json"
            ).read_bytes()
        ).decode("ascii")
        script = block.replace("__CPA_OAUTH_QUARANTINE_MANIFEST_B64__", manifest_b64)
        if os.name == "nt":
            # This extracted payload runs on Linux in production. Windows lacks
            # fchmod on Python 3.11; POSIX permission checks remain in Linux CI.
            script = "import os\nos.fchmod = lambda fd, mode: None\n" + script
        catalog_block = payload.split(
            'python3 - "$DIR" "$MODE" "$BK/cpa-quarantine-catalog.json" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        previous_field = {
            "codex": "previous_codex_exclusions",
            "antigravity": "previous_provider_exclusions",
            "claude": "previous_claude_exclusions",
        }[provider]
        applied_field = {
            "codex": "applied_codex_exclusions",
            "antigravity": "applied_provider_exclusions",
            "claude": "applied_claude_exclusions",
        }[provider]
        expected_aliases = {
            "codex": OAUTH_ROUTE_ALIASES,
            "antigravity": ANTIGRAVITY_OAUTH_ROUTE_ALIASES,
            "claude": cast(frozenset[str], policy["EXPECTED_CLAUDE_ROUTE_ALIASES"]),
        }[provider]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            luna_marker_path = Path(directory) / "oauth-quarantine.json"
            antigravity_marker_path = Path(directory) / "antigravity-quarantine.json"
            claude_marker_path = Path(directory) / "claude-oauth-quarantine.json"
            marker_path = {
                "codex": luna_marker_path,
                "antigravity": antigravity_marker_path,
                "claude": claude_marker_path,
            }[provider]
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

            def validate(candidate: dict[str, Any]) -> list[str]:
                return cast(
                    list[str],
                    policy["validate_config"](
                        candidate,
                        marker_path=luna_marker_path,
                        antigravity_marker_path=antigravity_marker_path,
                        claude_marker_path=claude_marker_path,
                    ),
                )

            def read_config() -> dict[str, Any]:
                return cast(
                    dict[str, Any],
                    yaml.safe_load(config_path.read_text(encoding="utf-8")),
                )

            def run(mode: str) -> "subprocess.CompletedProcess[bytes]":
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(config_path),
                        str(marker_path),
                        mode,
                    ],
                    capture_output=True,
                    env={**os.environ, "CPA_OAUTH_QUARANTINE_PROVIDER": provider},
                    timeout=30,
                    check=False,
                )

            self.assertEqual(validate(read_config()), [])

            config_inode = config_path.stat().st_ino
            quarantined = run("quarantine")
            self.assertEqual(quarantined.returncode, 0, quarantined.stderr)
            self.assertIn(b"QUARANTINE_APPLIED", quarantined.stdout)
            self.assertTrue(marker_path.exists())
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(
                marker[previous_field],
                config["oauth"]["excluded-models"][provider],
            )
            after = read_config()
            manifest_path = Path(directory) / "cpa_provider_routes.json"
            manifest_path.write_bytes(
                (
                    Path(__file__).parents[1]
                    / "scripts/remote/cpa_provider_routes.json"
                ).read_bytes()
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            all_aliases = {
                model["alias"]
                for route in (
                    manifest["providers"]
                    + manifest["oauth_routes"]
                    + manifest["codex_api_key_routes"]
                    + manifest["claude_api_key_routes"]
                )
                for model in route["models"]
            }
            catalog_path = Path(directory) / "catalog.json"

            def verify_catalog(
                mode: str, aliases: set[str]
            ) -> subprocess.CompletedProcess[bytes]:
                catalog_path.write_text(
                    json.dumps({"data": [{"id": alias} for alias in aliases]}),
                    encoding="utf-8",
                )
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        catalog_block,
                        directory,
                        mode,
                        str(catalog_path),
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                    env={**os.environ, "CPA_OAUTH_QUARANTINE_PROVIDER": provider},
                )

            survivors = all_aliases - set(expected_aliases)
            result = verify_catalog("quarantine", survivors)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(verify_catalog("quarantine", all_aliases).returncode, 0)
            self.assertNotEqual(
                verify_catalog(
                    "quarantine", survivors | {"unreviewed-model"}
                ).returncode,
                0,
            )
            self.assertEqual(
                marker[applied_field],
                after["oauth"]["excluded-models"][provider],
            )
            # The quarantine state must satisfy the semantic policy, and it must
            # change nothing outside the OAuth exclusion list.
            self.assertEqual(validate(after), [])
            self.assertEqual(
                {key: value for key, value in after.items() if key != "oauth"},
                {key: value for key, value in config.items() if key != "oauth"},
            )
            self.assertEqual(
                sorted(
                    set(after["oauth"]["excluded-models"][provider])
                    - set(config["oauth"]["excluded-models"][provider])
                ),
                sorted({"claude-*"} if provider == "claude" else expected_aliases),
            )
            for other_provider in {"codex", "antigravity", "claude"} - {provider}:
                self.assertEqual(
                    after["oauth"]["excluded-models"][other_provider],
                    config["oauth"]["excluded-models"][other_provider],
                )
            if os.name != "nt":
                if provider == "claude":
                    self.assertEqual(config_path.stat().st_ino, config_inode)
                self.assertEqual(int(config_path.stat().st_mode) & 0o777, 0o600)
                self.assertEqual(int(marker_path.stat().st_mode) & 0o777, 0o600)

            # Re-quarantining is refused instead of silently no-op.
            self.assertNotEqual(run("quarantine").returncode, 0)

            # A manual edit during the quarantine window must not be silently
            # overwritten by restore; the operator must resolve the drift.
            drifted = read_config()
            drifted["oauth"]["excluded-models"][provider].append("manual-drift")
            config_path.write_text(yaml.safe_dump(drifted), encoding="utf-8")
            refused_restore = run("restore")
            self.assertNotEqual(refused_restore.returncode, 0)
            self.assertTrue(marker_path.exists())
            config_path.write_text(yaml.safe_dump(after), encoding="utf-8")

            released = run("restore")
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertIn(b"QUARANTINE_RELEASED", released.stdout)
            self.assertFalse(marker_path.exists())
            self.assertEqual(read_config(), config)
            if os.name != "nt" and provider == "claude":
                self.assertEqual(config_path.stat().st_ino, config_inode)
            self.assertEqual(validate(read_config()), [])
            result = verify_catalog("restore", all_aliases)
            self.assertEqual(result.returncode, 0, result.stderr)

            # Restoring without an active quarantine is refused.
            self.assertNotEqual(run("restore").returncode, 0)

    def test_doctor_gates_gateway_tls_cert_validity(self) -> None:
        doctor = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-doctor.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("==tls-cert==", doctor)
        block = doctor.split("==tls-cert==", 1)[1].split("==syntax==", 1)[0]
        # The fork's RenewTLS exits 0 while the cert ages toward expiry, so the
        # doctor must gate on the served cert's remaining days, not on renewal
        # exit codes (2026-10-13 outage: cert expired with green heartbeats).
        self.assertIn("gateway_tls_cert_days_left=", block)
        self.assertLess(block.index("-ge 7"), block.index("mark_fail tls-cert"))
        self.assertIn("mark_fail tls-cert", block.rsplit("else", 1)[-1])

    def test_renewtls_wrapper_renews_before_fork_last_day(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts" / "v2ray_agent_renewtls_cron.ps1"
        ).read_text(encoding="utf-8")
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
        # The fork renews on the literal day of expiry, after the cert has
        # already died (00:32 UTC) relative to the daily 01:30 UTC check. The
        # wrapper must run acme.sh first, preserve real errors, and verify
        # the certificate loaded by nginx before reporting success.
        self.assertIn("--renew -d '*.sciman.top' --ecc", wrapper)
        self.assertIn("nginx -s reload", wrapper)
        self.assertIn("RENEWTLS_LOADED_CERT_VERIFIED", wrapper)
        self.assertLess(
            wrapper.index("--renew -d '*.sciman.top' --ecc"),
            wrapper.index("install.sh RenewTLS"),
        )
        self.assertIn(
            "|| renew_rc=$?", wrapper.split('acme.sh" --renew', 1)[1].split("\n", 1)[0]
        )


def _extract_flat_py_dict(text: str, varname: str) -> dict[str, Any]:
    """Extract a one-level Python dict literal assigned to ``varname``.

    The guardrail scripts embed admission tables as Python dict literals inside
    heredocs. Flat dicts have no nested braces, so the literal ends at the
    first ``}`` that follows the opening one.
    """
    match = re.search(rf"^\s*{varname}\s*=\s*\{{(.*?)\}}", text, re.S | re.M)
    if match is None:
        raise AssertionError(f"{varname} table not found")
    return cast(dict[str, Any], ast.literal_eval("{" + match.group(1) + "}"))


def _extract_lanes_models_table(text: str) -> dict[str, Any]:
    """Extract the lane models table (the ``expected = {`` block listing lanes)."""
    for match in re.finditer(r"^expected = \{(.*?)^\}", text, re.S | re.M):
        body = match.group(1)
        if '"chatgpt-oauth"' in body and "deepseek-v4.1-flash" in body:
            return cast(dict[str, Any], ast.literal_eval("{" + body + "}"))
    raise AssertionError("lane models table not found")


class CpaAdmissionContractParityTests(unittest.TestCase):
    """cpa-admission.json vs apply.sh vs doctor.sh embedded admission tables.

    The 2026-11-01 workbuddy RPM change hit exactly this gap: the json and the
    call-site grep surface were updated, one script kept the old constant, and
    the drift only surfaced as a remote ROLLBACK admission_config_contract
    during a real -Apply. These tables must move together or not at all.
    """

    def setUp(self) -> None:
        root = Path(__file__).parents[1] / "scripts" / "remote"
        self.json_lanes = {
            lane["name"]: lane
            for lane in json.loads(
                (root / "cpa-admission.json").read_text(encoding="utf-8")
            )["lanes"]
        }
        self.apply_source = (root / "cpa-guardrail-apply.sh").read_text(
            encoding="utf-8"
        )
        self.doctor_source = (root / "cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )

    def test_lane_name_sets_match_across_all_three_sources(self) -> None:
        tables = {
            "apply.sh expected_rpm": _extract_flat_py_dict(
                self.apply_source, "expected_rpm"
            ),
            "doctor.sh expected_rpm": _extract_flat_py_dict(
                self.doctor_source, "expected_rpm"
            ),
            "apply.sh lane models": _extract_lanes_models_table(self.apply_source),
            "doctor.sh lane models": _extract_lanes_models_table(self.doctor_source),
        }
        for label, table in tables.items():
            with self.subTest(table=label):
                self.assertEqual(set(table), set(self.json_lanes))

    def test_rpm_and_inflight_and_models_match_json(self) -> None:
        apply_rpm = _extract_flat_py_dict(self.apply_source, "expected_rpm")
        doctor_rpm = _extract_flat_py_dict(self.doctor_source, "expected_rpm")
        apply_inflight = _extract_flat_py_dict(
            self.apply_source, "expected_max_inflight"
        )
        doctor_inflight = _extract_flat_py_dict(
            self.doctor_source, "expected_max_inflight"
        )
        apply_models = _extract_lanes_models_table(self.apply_source)
        doctor_models = _extract_lanes_models_table(self.doctor_source)
        for name, lane in self.json_lanes.items():
            for label, actual, expected in (
                ("apply.sh rpm", apply_rpm.get(name), lane["requests_per_minute"]),
                ("doctor.sh rpm", doctor_rpm.get(name), lane["requests_per_minute"]),
                (
                    "apply.sh max_inflight",
                    apply_inflight.get(name),
                    lane["max_inflight"],
                ),
                (
                    "doctor.sh max_inflight",
                    doctor_inflight.get(name),
                    lane["max_inflight"],
                ),
                ("apply.sh models", apply_models.get(name), lane["models"]),
                ("doctor.sh models", doctor_models.get(name), lane["models"]),
            ):
                with self.subTest(lane=name, field=label):
                    self.assertEqual(actual, expected)

    def test_shutdown_timeout_stream_probe_and_doctor_observability_wired(self) -> None:
        self.assertIn('server_after["shutdown-timeout"] = 15', self.apply_source)
        self.assertIn('server_block.pop("shutdown-timeout", None)', self.apply_source)
        self.assertIn(
            'new_body_buffer = "client_body_buffer_size 2m;"', self.apply_source
        )
        self.assertIn("client_body_buffer_size 2m;", self.doctor_source)
        self.assertIn('config.get("stream_probe_bytes") != 2097152', self.apply_source)
        self.assertIn("invalidated oauth token", self.doctor_source)
        self.assertIn("workbuddy_failover_probe_401_7d=", self.doctor_source)
        self.assertIn("refresh_cluster_max_10s=", self.doctor_source)

    def test_rollback_admission_generation_check_line_continuation(self) -> None:
        self.assertIn(
            '[ "$(sha256sum "$ADMISSION_SCRIPT" | awk \'{print $1}\')" = \\',
            self.apply_source,
        )
        self.assertNotIn(
            '[ "$(sha256sum "$ADMISSION_SCRIPT" | awk \'{print $1}\')" =\n',
            self.apply_source,
        )


if __name__ == "__main__":
    unittest.main()
