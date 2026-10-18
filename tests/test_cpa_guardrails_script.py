"""test_cpa_guardrails_script.py - split from test_scripts.py (domain: guardrails)."""

import base64
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from cpa_catalog_expectations import (
    ANTIGRAVITY_OAUTH_ROUTE_ALIASES,
    OAUTH_ROUTE_ALIASES,
)

from script_validation_support import (
    ScriptValidationMixin,
    read_guardrail_source,
)


class CpaGuardrailsScriptTests(ScriptValidationMixin, unittest.TestCase):
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
                "Oct 17 cpa-admission[1]: upstream_result lane=claude-oauth "
                "model=claude-opus-5-5 status=503 capacity=false "
                "retry_after=30 waited_ms=3000 request_id=req-secret "
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
        self.assertIn("admission_capacity_events_24h=1", result.stdout)
        self.assertIn("admission_capacity_statuses=429:1", result.stdout)
        self.assertIn(
            "admission_upstream_429_503_details="
            "claude-oauth/claude-haiku-5-5/HTTP429/capacity:1,"
            "claude-oauth/claude-opus-5-5/HTTP503/noncapacity:1",
            result.stdout,
        )
        self.assertNotIn("req-secret", result.stdout)
        self.assertNotIn("prompt-secret", result.stdout)

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
                expected.setdefault("oauth", {}).setdefault("providers", {}).setdefault(
                    "antigravity", {}
                )["sensitive-words"] = policy["EXPECTED_CONFIG_PATHS"][
                    ("oauth", "providers", "antigravity", "sensitive-words")
                ]
                self.assertEqual(generated, expected)

        for fixture in (
            {"oauth": None},
            {"oauth": {"providers": []}},
            {"oauth": {"providers": {"antigravity": "invalid"}}},
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
        # Execute the real preparation/loader code, stopping before Git or SSH.
        prefix = source.split("function Assert-ProjectionSourcesUnchanged", 1)[0]
        trailer = """
$result = [ordered]@{}
foreach ($entry in $guardrailTemplates.GetEnumerator()) {
  $result[$entry.Key] = @{
    payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-CpaGuardrailTemplate -Name $entry.Key)))
    hash = $projectionSourceHashes[$entry.Value]
    tracked = $projectionSourcePaths -contains $entry.Value
  }
}
$result | ConvertTo-Json -Depth 4 -Compress
"""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            scripts = fixture / "scripts"
            shutil.copytree(root / "scripts/remote", scripts / "remote")
            (fixture / "connect.ps1").write_text(
                "throw 'SSH MUST NOT RUN'\n", encoding="utf-8"
            )
            driver = fixture / "prepare.ps1"
            prefix = prefix.replace(
                "$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path",
                "$scriptDir = '" + str(scripts).replace("'", "''") + "'",
            )
            driver.write_text(prefix + trailer, encoding="utf-8")

            def run() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [pwsh, "-NoProfile", "-File", str(driver)],
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
            self.assertEqual(len(loaded), 5)
            for name, record in loaded.items():
                payload = (scripts / "remote" / f"cpa-guardrail-{name}.sh").read_bytes()
                self.assertEqual(
                    base64.b64decode(record["payload"]), payload.removesuffix(b"\n")
                )
                self.assertEqual(record["hash"], hashlib.sha256(payload).hexdigest())
                self.assertTrue(record["tracked"])

            missing = scripts / "remote/cpa-guardrail-doctor.sh"
            missing.unlink()
            self.assertNotEqual(run().returncode, 0)
            missing.write_text("\n", encoding="utf-8")
            empty = run()
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("template is empty", empty.stderr)

    def test_strict_doctor_entrypoint_uses_chunked_transport_without_network(
        self,
    ) -> None:
        """Exercise the real PowerShell entrypoint while replacing connect.ps1."""
        pwsh = shutil.which("pwsh")
        bash = self._resolve_bash()
        if pwsh is None or bash is None:
            self.skipTest("PowerShell 7 and Bash are required for transport simulation")

        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8")
        connect_assignment = '$connectScript = Join-Path $repoRoot "connect.ps1"'
        self.assertIn(connect_assignment, source)
        fake_connect = r"""
param([string]$Command)
$statePath = $env:CPA_TEST_TRANSPORT_STATE
$logPath = $env:CPA_TEST_TRANSPORT_LOG
$operation = ""
if ($Command -match "^umask 077; : > '([^']+)'; chmod 600 '") {
  $state = @{ path = $matches[1]; encoded = "" }
  $operation = "create"
} elseif ($Command -match "^printf %s '([A-Za-z0-9+/=]+)' >> '([^']+)'$") {
  $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -AsHashtable
  if ($matches[2] -ne $state.path) { throw "Temporary path changed during upload." }
  $state.encoded += $matches[1]
  $operation = "chunk"
} elseif ($Command.StartsWith("set -o pipefail; base64 -d -- ")) {
  $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -AsHashtable
  $expectedFinal = "set -o pipefail; base64 -d -- '$($state.path)' | bash; rc=`$?; rm -f -- '$($state.path)'; exit `$rc"
  if ($Command -ne $expectedFinal) {
    throw "Final decoder command did not preserve failure and cleanup handling."
  }
  $script = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($state.encoded))
  $process = [Diagnostics.Process]::new()
  $process.StartInfo.FileName = $env:CPA_TEST_BASH
  $process.StartInfo.Arguments = "-n"
  $process.StartInfo.UseShellExecute = $false
  $process.StartInfo.RedirectStandardInput = $true
  $process.StartInfo.RedirectStandardError = $true
  $process.StartInfo.StandardInputEncoding = [Text.Encoding]::UTF8
  $process.StartInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
  [void]$process.Start()
  $process.StandardInput.Write($script)
  $process.StandardInput.Close()
  if (-not $process.WaitForExit(10000)) {
    $process.Kill($true)
    throw "Local Bash syntax check exceeded its time limit."
  }
  $stderr = $process.StandardError.ReadToEnd()
  if ($process.ExitCode -ne 0) { throw "Bash rejected decoded doctor: $stderr" }
  Remove-Item -LiteralPath $statePath -Force
  $operation = "execute"
  Write-Output "SIMULATED_BASH_SYNTAX=PASS"
} elseif ($Command -match "^printf %s ([A-Za-z0-9+/=]+) \| base64 -d \| bash$") {
  $script = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($matches[1]))
  $process = [Diagnostics.Process]::new()
  $process.StartInfo.FileName = $env:CPA_TEST_BASH
  $process.StartInfo.Arguments = "-n"
  $process.StartInfo.UseShellExecute = $false
  $process.StartInfo.RedirectStandardInput = $true
  $process.StartInfo.RedirectStandardError = $true
  $process.StartInfo.StandardInputEncoding = [Text.Encoding]::UTF8
  [void]$process.Start()
  $process.StandardInput.Write($script)
  $process.StandardInput.Close()
  if (-not $process.WaitForExit(10000)) { $process.Kill($true); throw "Bash check timed out." }
  $stderr = $process.StandardError.ReadToEnd()
  if ($process.ExitCode -ne 0) { throw "Bash rejected decoded doctor: $stderr" }
  $operation = "single"
  Write-Output "SIMULATED_BASH_SYNTAX=PASS"
} else {
  throw "Unexpected remote command shape in transport simulation."
}
if ($operation -eq "create") {
  $state | ConvertTo-Json -Compress | Set-Content -LiteralPath $statePath -Encoding utf8
}
Add-Content -LiteralPath $logPath -Value $operation -Encoding utf8
if ($operation -eq "chunk") {
  $state | ConvertTo-Json -Compress | Set-Content -LiteralPath $statePath -Encoding utf8
}
exit 0
"""

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            stub = fixture / "connect-mock.ps1"
            stub.write_text(fake_connect, encoding="utf-8")
            driver = fixture / "guardrails-simulated.ps1"
            driver.write_text(
                source.replace(
                    "$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path",
                    "$scriptDir = '" + str(root / "scripts").replace("'", "''") + "'",
                    1,
                )
                .replace(
                    "$repoRoot = Split-Path -Parent $scriptDir",
                    "$repoRoot = '" + str(root).replace("'", "''") + "'",
                    1,
                )
                .replace(
                    connect_assignment,
                    "$connectScript = '" + str(stub).replace("'", "''") + "'",
                    1,
                ),
                encoding="utf-8",
            )
            state = fixture / "remote-temp-state.json"
            operations = fixture / "transport-operations.txt"
            process_env = os.environ.copy()
            process_env.update(
                {
                    "CPA_TEST_TRANSPORT_STATE": str(state),
                    "CPA_TEST_TRANSPORT_LOG": str(operations),
                    "CPA_TEST_BASH": bash,
                }
            )
            result = subprocess.run(
                [pwsh, "-NoProfile", "-File", str(driver), "-Profile", "bwg"],
                cwd=root,
                env=process_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SIMULATED_BASH_SYNTAX=PASS", result.stdout)
            self.assertFalse(state.exists(), "remote temporary payload must be cleaned")
            recorded = operations.read_text(encoding="utf-8-sig").splitlines()
            self.assertGreater(recorded.count("chunk"), 1)
            self.assertEqual(recorded[0], "create")
            self.assertEqual(recorded[-1], "execute")

    def test_cpa_guardrails_freezes_public_data_plane_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = read_guardrail_source()

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
        self.assertIn("success:0|running:0|busy:75|unverified:10|deferred:76", text)
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
            [1, 2, 3, 4],
        )
        self.assertEqual(
            route_manifest["retired_hosts"], ["codex.ciii.club", "api.deepseek.com"]
        )
        retired_aliases = {
            "codex-auto-review",
            "gpt-5.5",
            "gpt-5.6",
            "gpt-reserve",
            "gpt-image-2.5",
        }
        self.assertTrue(retired_aliases <= set(route_manifest["oauth_exclusions"]))
        self.assertTrue(
            retired_aliases <= set(route_manifest["codex_api_key_exclusions"])
        )
        self.assertFalse(
            retired_aliases
            & {
                model["alias"]
                for provider in route_manifest["providers"]
                for model in provider["models"]
            }
        )
        slot1_route = next(
            route for route in route_manifest["providers"] if route["slot"] == 1
        )
        self.assertEqual(slot1_route["host"], "ai.input.im")
        self.assertEqual(
            [model["alias"] for model in slot1_route["models"]],
            [
                "gpt-6-astra",
                "gpt-6.1-sol-input",
            ],
        )
        self.assertEqual(set(slot1_route["optional_models"]), {"gpt-6.1-sol-input"})
        # gpt-image-2.5 retired 2026-10-04: no image route is declared, so the
        # whole image_models key disappears instead of staying an empty list.
        self.assertNotIn("image_models", slot1_route)
        zen_route = next(
            route for route in route_manifest["providers"] if route["slot"] == 2
        )
        self.assertEqual(zen_route["host"], "opencode.ai")
        self.assertEqual(zen_route["path"], "/zen/go/v1")
        # muse-spark-1.3-contributor retired from the slot 2026-10-11 and
        # claude-haiku-5-5 moved off it on 2026-10-16: zen answers
        # chat/completions for both with 400 ModelProtocolUnsupported
        # (upstream error string, not in CPA source). They live in the managed
        # family routes below — muse on the codex family (native /responses),
        # haiku on the claude family (native Anthropic Messages).
        self.assertEqual(
            zen_route["models"],
            [
                {"name": "deepseek-v4.1-flash", "alias": "deepseek-v4.1-flash"},
            ],
        )
        # The opencode zen go endpoint requires a session header; the exact
        # static value is pinned here and pushed verbatim by -Apply (the only
        # reviewed transport-header carve-out).
        self.assertEqual(
            zen_route["headers"], {"x-opencode-session": "cpa-bwg-gateway-01"}
        )
        # muse returns via the managed codex-family route: the upstream serves
        # it only through the Responses API, which the codex-api-key executor
        # speaks natively ({base}/responses) with the same pinned session
        # header; the API key borrows slot 2's credential.
        codex_route = route_manifest["codex_api_key_routes"][0]
        self.assertEqual(codex_route["name"], "opencode-zen-muse")
        self.assertEqual(codex_route["base-url"], "https://opencode.ai/zen/go/v1")
        self.assertEqual(codex_route["api_key_slot"], 2)
        self.assertEqual(
            codex_route["models"],
            [
                {
                    "name": "muse-spark-1.3-contributor",
                    "alias": "muse-spark-1.3-contributor",
                }
            ],
        )
        # The zen claude-family route (added 2026-10-16 for the same
        # slot-2 credential) was retired on 2026-10-17: the Claude
        # subscription OAuth lane now claims claude-haiku-5-5 (with
        # claude-opus-5-5 / claude-sonnet-5-5) directly from the account's
        # native Anthropic catalog, so the family list stays empty and the
        # projected api-keys.claude group is removed by the apply.
        self.assertEqual(route_manifest["claude_api_key_routes"], [])
        for slot, expected in {
            4: {
                "glm-5.3",
                "glm-5.3-flash",
            },
        }.items():
            provider = next(
                route for route in route_manifest["providers"] if route["slot"] == slot
            )
            self.assertEqual({model["name"] for model in provider["models"]}, expected)
            self.assertEqual({model["alias"] for model in provider["models"]}, expected)
        http_route = next(
            provider
            for provider in route_manifest["providers"]
            if provider["slot"] == 3
        )
        self.assertEqual(
            (http_route["scheme"], http_route["host"], http_route["port"]),
            ("http", "35.213.82.91", 8003),
        )
        self.assertEqual(
            http_route["models"],
            [
                {"name": "gpt-5.6-terra", "alias": "gpt-5.6-terra"},
            ],
        )
        self.assertIs(http_route["allow_insecure_http"], True)
        self.assertIn(
            'legacy_hosts = set(route_manifest.get("retired_hosts", []))',
            text,
        )
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
        # redaction-safe (no log line text echoed), capability-aware, and
        # observation-grade. Two severity tiers: >5 events/7d is ELEVATED
        # (warrants quality-canary review); 1-5 is OBSERVED; 0 is OK.
        self.assertIn("==model-substitution==", text)
        self.assertIn("model_substitution_warnings_7d=", text)
        self.assertIn("grep -c 'upstream served model'", text)
        self.assertIn("WARN_SUBSTITUTION_ELEVATED", text)
        self.assertIn("WARN_SUBSTITUTION_OBSERVED", text)
        self.assertIn("model_substitution=UNAVAILABLE_VERSION", text)
        self.assertIn("sort -V", text)
        # Coverage annotation must mention the elevated threshold so operators
        # understand what triggers the higher-severity label.
        self.assertIn(
            "WARN_SUBSTITUTION_ELEVATED (>5 in 7d) warrants quality-canary review", text
        )
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
            (repo_root / "scripts" / "remote" / "cpa-health.py").read_text(),
        )
        self.assertIn('chmod 700 "$DIR/auth/logs"', text)
        self.assertIn("-name 'error-*.log' -exec chmod 600 -- {} +", text)
        # Random-path rotation must prove old path dead and new path live.
        self.assertIn("OLD_PATH_REVOKED=yes", text)
        self.assertIn("NEW_PATH_ACTIVE=yes", text)
        # Chunked payload execution must fail on EITHER pipeline stage: a
        # corrupted payload fails the base64 decoder, and a failing remote
        # script must keep its own exit code (pipefail $? captures both).
        self.assertIn("set -o pipefail; base64 -d -- '$remoteTemp' | bash; ", text)
        self.assertIn("rc=`$?; rm -f -- '$remoteTemp'; exit `$rc", text)
        # Apply/RotatePath/DeactivateOAuthLuna/QuarantineOAuthLuna share the
        # updater flock so the daily timer cannot interleave with a guardrail
        # transaction.
        self.assertEqual(text.count("exec 9>/run/vps-ssh-launcher-maintenance.lock"), 4)
        self.assertEqual(text.count("flock -n 9"), 4)
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

    def test_cpa_guardrails_normalizes_crlf_in_remote_payloads(self) -> None:
        text = read_guardrail_source()
        function = text.split("function Invoke-BwgRemoteScript", 1)[1].split(
            "$doctorScript = @'", 1
        )[0]
        # gitattributes checks *.ps1 out as CRLF; real Linux bash rejects CR
        # in the projected payload (e.g. "func() {<CR>" is a syntax error), so
        # the payload must be normalized before it is base64-projected.
        self.assertIn(
            '$Script = $Script.Replace("`r`n", "`n").Replace("`r", "`n")',
            function,
        )
        self.assertLess(
            function.index("$Script = $Script.Replace"),
            function.index("UTF8.GetBytes($Script)"),
        )
        # Chunked uploads must pace their SSH channels instead of bursting.
        self.assertIn("Start-Sleep -Milliseconds 100", function)

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
        self.assertIn('Get-HeadBlobSha256 "scripts/remote/cpa-auto-update.sh"', source)
        self.assertNotIn("/opt/cliproxyapi/auto-update.sh=$updaterSha256", source)
        self.assertIn("Assert-ProjectionSourcesUnchanged", source)
        self.assertIn(
            "git -C $repoRoot status --porcelain=v1 --untracked-files=all",
            source,
        )
        self.assertIn('"connect.ps1"', source)
        self.assertIn('"scripts/cpa_bwg_guardrails.ps1"', source)

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
            "ADMISSION_SERVICE_USER=$(systemctl show cpa-admission.service -p User --value)",
            doctor,
        )
        self.assertIn(
            '[ -n "$ADMISSION_SERVICE_USER" ] || ADMISSION_SERVICE_USER=root', doctor
        )
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

    def test_cpa_guardrails_doctor_bounds_access_log_scan(self) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        self.assertIn("scan_cap_bytes = 64 * 1024 * 1024", doctor)
        self.assertIn("log_scan_truncated", doctor)
        # The partial line at the truncation boundary must be dropped.
        self.assertIn("log_handle.readline()", doctor)

    def test_cpa_guardrails_doctor_observes_updater_timer_freshness(self) -> None:
        source = read_guardrail_source()
        doctor = source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0]

        self.assertIn("LastTriggerUSec", doctor)
        self.assertIn("timer_last_trigger_age_hours", doctor)
        self.assertIn("timer_last_trigger=STALE", doctor)

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
        # `client_body_buffer_size 128k;` assertion lives elsewhere in the
        # doctor and must stay a literal, while the desktop lane's copy has to
        # follow the canonical value.
        lane_block = doctor.split(
            "LANE443_CONF=/etc/nginx/conf.d/00-cpa-443-http.conf", 1
        )[1].split("echo gateway-443-lane=ABSENT", 1)[0]
        self.assertNotIn("client_body_buffer_size 128k;", lane_block)
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
        self.assertIn("lane443_valid=$(curl", doctor)
        self.assertIn('--resolve "$lane443_server:33387:127.0.0.1"', doctor)
        self.assertIn(
            '[ "$lane443_valid" = "401" ] && [ "$lane443_bare" = "404" ]', doctor
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
        # the repo root holds no private env file.
        self.assertIn(
            'Join-Path $env:APPDATA "vps-ssh-launcher\\providers.env"',
            source,
        )
        self.assertNotIn("副本", source)

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
        block = payload.split(
            "python3 - /tmp/cpa-oauth-retire-catalog.json <<'PY'\n", 1
        )[1].split("\nPY\n", 1)[0]
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

    def test_cpa_oauth_quarantine_state_change_is_reversible_and_policy_clean(
        self,
    ) -> None:
        for provider in ("codex", "antigravity", "claude"):
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
            for provider in ("codex", "antigravity", "claude"):
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
            for provider in ("codex", "antigravity", "claude"):
                with self.subTest(provider=provider):
                    result = subprocess.run(
                        [sys.executable, "-c", block, directory],
                        capture_output=True,
                        env={**os.environ, "CPA_OAUTH_QUARANTINE_PROVIDER": provider},
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), b"ACTIVE_OAUTH_FILES=1")
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
            candidate["oauth"]["excluded-models"]["antigravity"].append(
                "gemini-3.8-flash-high"
            )
            self.assertNotEqual(run().returncode, 0)
            marker = {
                "version": 1,
                "provider": "antigravity",
                "state": "quarantined",
                "aliases": ["gemini-3.8-flash-high"],
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

    def test_claude_doctor_executes_active_and_drift_states(self) -> None:
        import runpy

        import yaml

        repo = Path(__file__).parents[1]
        payload = (repo / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        block = (
            payload.split('echo "==claude-lane=="', 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        policy = runpy.run_path(str(repo / "scripts/remote/cpa_policy.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(
                repo / "scripts/remote/cpa_provider_routes.json",
                root / "cpa_provider_routes.json",
            )
            shutil.copyfile(
                repo / "scripts/remote/cpa_policy.py",
                root / "cpa_policy.py",
            )
            candidate = self._valid_cpa_policy_config(policy)
            config_path = root / "config.yaml"
            marker_path = root / "claude-oauth-quarantine.json"

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
            self.assertIn(b"claude_lane=OK", result.stdout)
            self.assertIn(
                b"allowed=claude-haiku-5-5,claude-opus-5-5,claude-sonnet-5-5",
                result.stdout,
            )
            deployed = list(candidate["oauth"]["excluded-models"]["claude"])
            applied = deployed + ["claude-*"]
            marker_aliases = [
                "claude-haiku-5-5",
                "claude-opus-5-5",
                "claude-sonnet-5-5",
            ]
            marker: dict[str, Any] = {
                "version": 1,
                "state": "quarantined",
                "provider": "claude",
                "aliases": marker_aliases,
                "previous_claude_exclusions": deployed,
                "applied_claude_exclusions": applied,
            }
            candidate["oauth"]["excluded-models"]["claude"] = applied
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"claude_lane=QUARANTINED", result.stdout)
            marker["aliases"] = marker_aliases[:-1]
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
            self.assertNotEqual(run().returncode, 0)
            marker_path.unlink()
            candidate["oauth"]["excluded-models"]["claude"] = deployed
            # The deliberate fail-close teardown state must surface as drift,
            # not pass silently.
            deployed = list(candidate["oauth"]["excluded-models"]["claude"])
            candidate["oauth"]["excluded-models"]["claude"] = ["claude-*"]
            self.assertNotEqual(run().returncode, 0)
            candidate["oauth"]["excluded-models"]["claude"] = deployed[:-1]
            self.assertNotEqual(run().returncode, 0)
            # A tombstone that covers a declared alias is a manifest bug and
            # must fail on the blocked-alias branch, not just set equality.
            candidate["oauth"]["excluded-models"]["claude"] = deployed + [
                "claude-haiku-5-5"
            ]
            self.assertNotEqual(run().returncode, 0)
            manifest_path = root / "cpa_provider_routes.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["claude_oauth_exclusions"].append("claude-haiku-5-5")
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"claude_lane=BLOCKED_DECLARED_ALIAS", result.stdout)

    def test_claude_lane_quarantine_requires_user_directive(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/cpa_bwg_guardrails.ps1"
        ).read_text(encoding="utf-8")
        # 2026-10-17 an agent session quarantined the claude lane on its own
        # ToS reading; the switch must refuse to run without an explicit user
        # directive so lane shutdowns carry authorization, not judgment.
        self.assertIn("[string]$QuarantineUserDirective", source)
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
            'python3 - "$DIR" "$MODE" /tmp/cpa-quarantine-catalog.json <<\'PY\'\n', 1
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
        wrapper = source.split("$wrapperText = @'\n", 1)[1].split("\n'@", 1)[0]
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


if __name__ == "__main__":
    unittest.main()
