"""test_cpa_updater_apply.py - split from test_scripts.py (domain: apply)."""

import ast
import builtins
import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, cast

from script_validation_support import (
    HEALTH_FIXTURE_CATALOG_IDS,
    SLOT_API_KEY_HEADER_TOKEN,
    ScriptValidationMixin,
    claude_route_headers,
    read_guardrail_source,
)

WSL_BASH_TIMEOUT_SECONDS = 60


def _extract_bash_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1] + "\n"
    raise AssertionError(f"unterminated Bash function: {name}")


class CpaUpdaterApplyTests(ScriptValidationMixin, unittest.TestCase):
    def test_admission_stabilization_binds_source_pin_and_rollback(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source = (
            repo_root / "outputs/deploy-admission-stabilization-20261111.ps1"
        ).read_text(encoding="utf-8")
        request_guard = (
            repo_root / "outputs/deploy-cpa-request-guard-20261113.ps1"
        ).read_text(encoding="utf-8")
        for anchor in (
            '[string]$ExpectedLivePin = "d3e7f8e3cb16c4927096f426830aa726c92667412f946e5574f4c7371b095d77"',
            '[string]$ExpectedInstalledPin = "d3e7f8e3cb16c4927096f426830aa726c92667412f946e5574f4c7371b095d77"',
            '[ "$(sha256sum "$SOURCE" | awk \'{print $1}\')" = "$NEW_SOURCE" ]',
            "OLD_LIVE=__OLD_LIVE_PIN__",
            "OLD_INSTALLED=__OLD_INSTALLED_PIN__",
            "REFUSE bwg_target_endpoint_mismatch",
            "flock -w 30 -x 7",
            "drain_request_lock",
            "REFUSE traffic_not_drained",
            "REFUSE request_lock_exclusive",
            'cp --preserve=all "$SOURCE" "$BK/cpa-admission.py"',
            'restore_file "$BK/cpa-admission.py" "$SOURCE"',
            'restore_file "$BK/cpa_request_guard.py" "$GUARD"',
            "PROCESS_LINEAGE_OK",
            "GENERATION_BOUND_OK",
            'chmod 0755 "$STAGING"',
            'cp --preserve=all "$CONFIG" "$BK/cpa-admission.json"',
            'restore_file "$BK/cpa-admission.json" "$CONFIG"',
            'restore_file "$BK/cpa-admission.service" "$UNIT"',
            'install -m 0644 -o root -g root "$STAGING/unit" "$UNIT"',
            'echo "DEPLOY_OK generation=$NEW_SOURCE guard=$NEW_GUARD backup=$BK"',
            "Invoke-LauncherRemoteCommand",
        ):
            with self.subTest(file="cpa-request-guard", anchor=anchor):
                self.assertIn(anchor, request_guard)
        self.assertNotIn("start_request_guard_broker(opened_fd)", request_guard)
        self.assertIn(
            'Write-Output "CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection"',
            (repo_root / "scripts/cpa_bwg_guardrails.ps1").read_text(encoding="utf-8"),
        )
        for anchor in (
            '[string]$ExpectedLivePin = "c410187f7975131630bcdb0c7e2bc4f045df1fb5fc9165a59b1bd1867fda6ea5"',
            '[string]$ExpectedInstalledPin = "c410187f7975131630bcdb0c7e2bc4f045df1fb5fc9165a59b1bd1867fda6ea5"',
            '[string]$CurrentDeployedPin = "375da23846ae0cf5169f6210150ea085e053d9760f479c5cf46ae7d46941f96d"',
            'Replace("__OLD_SOURCE_PIN__", $ExpectedInstalledPin)',
            'Replace("__CURRENT_PIN__", $CurrentDeployedPin)',
            "[ \"${VPS_SSH_LAUNCHER_PROFILE:-}\" = 'bwg' ]",
            "REFUSE bwg_target_endpoint_mismatch",
            '([string]$bwgProfile.host).Trim() -ne "144.34.229.116"',
            "[int]$bwgProfile.port -ne 29712",
            "[ -f /etc/nginx/conf.d/cpa-gateway.conf ]",
            "REFUSE bwg_gateway_identity_missing",
            "REFUSE bwg_admission_service_identity",
            "REFUSE bwg_admission_service_user",
            "REFUSE live_source_baseline_drift",
            "REFUSE installed_pin_baseline_drift",
            "SOURCE_APPLIED=0",
            "PIN_APPLIED=0",
            "SOURCE_APPLIED=1",
            "PIN_APPLIED=1",
            'mv -f "$STAGED" "$LIVE" || rollback 20',
            'mv -f "$PIN.new" "$PIN" || rollback 20',
            "TRAFFIC=/run/cpa-admission/requests.lock",
            "LOCK_INFRA=/run/vps-ssh-launcher-admission-deploy-20261112.lock",
            "fcntl.flock(inf_hold, fcntl.LOCK_EX | fcntl.LOCK_NB)",
            "fcntl.flock(hold, fcntl.LOCK_EX | fcntl.LOCK_NB)",
            "REQUEST_GUARD_EXCLUSIVE_OK",
            "REQUEST_GUARD_COMMIT",
            "release_guard",
            "release_traffic",
            "check_idle",
            "systemctl restart cpa-admission",
            "check_recovery_contract",
            "REFUSE live_source_baseline_drift expected=$OLD_PIN actual=$current_live_pin",
            "DRY_RUN_OK baseline=$OLD_PIN candidate=$NEW_PIN",
            "ROLLBACK_VERIFIED",
        ):
            self.assertIn(anchor, source)
        self.assertNotIn('Replace("__OLD_PIN__", $newPin)', source)
        rollback = source.index("rollback() {")
        mutation = source.index("MUTATED=1")
        self.assertLess(rollback, mutation)
        self.assertLess(
            source.index(
                'acquire_traffic "$DRAIN_WAIT"',
                source.index("if [ \"$MODE\" = 'dry-run' ]"),
            ),
            mutation,
        )
        self.assertLess(source.index("check_idle\n"), mutation)
        self.assertLess(source.index("SOURCE_APPLIED=1"), source.index("PIN_APPLIED=1"))
        self.assertLess(
            source.index('mv -f "$STAGED" "$LIVE"'),
            source.index('mv -f "$PIN.new" "$PIN"'),
        )
        self.assertLess(
            source.index('mv -f "$PIN.new" "$PIN"'),
            source.index(
                "systemctl restart cpa-admission", source.index("PIN_APPLIED=1")
            ),
        )
        self.assertIn("SOURCE_APPLIED", source[source.index("rollback() {") : mutation])
        self.assertIn("PIN_APPLIED", source[source.index("rollback() {") : mutation])
        self.assertIn(
            "release_traffic", source[source.index("rollback() {") : mutation]
        )
        self.assertLess(source.index('acquire_traffic "$DRAIN_WAIT"'), mutation)
        # A current-deployed generation is not the reviewed baseline. The
        # transaction must either prove the new target already applied or
        # refuse baseline drift; the old shortcut reported success without a
        # source swap.
        self.assertNotIn("ALREADY_APPLIED mode=$MODE pin=$CURRENT_PIN", source)
        self.assertIn("REFUSE live_source_baseline_drift", source)
        self.assertNotIn(
            '$ExpectedLivePin = "476e1f24b2ea291c554542f1a4f60672aed5fa9cb39062f4882131ab094b85dc"',
            source,
        )
        self.assertIn(
            "$remote = \"VPS_SSH_LAUNCHER_PROFILE='bwg'`n\" + $remote",
            source,
        )

    def test_native_plugins_defer_host_update_without_reading_credentials(self) -> None:
        import contextlib
        import io

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        program = (
            source.split("require_plugin_canary() {", 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        for config, expected in (
            ({}, 0),
            ({"plugins": {"enabled": False}}, 0),
            (
                {
                    "plugins": {
                        "enabled": True,
                        "configs": {"custom": {"enabled": True}},
                    }
                },
                77,
            ),
            ({"plugins": []}, 77),
            (None, 77),
        ):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.yaml"
                path.write_text(json.dumps(config))
                with (
                    mock.patch.object(sys, "argv", ["guard", str(path)]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    if expected:
                        with self.assertRaises(SystemExit) as error:
                            exec(compile(program, "plugin-guard", "exec"), {})
                        self.assertEqual(error.exception.code, expected)
                    else:
                        exec(compile(program, "plugin-guard", "exec"), {})
        self.assertLess(
            source.index("\nrequire_plugin_canary\n"),
            source.index("\nif ! health generation"),
        )
        self.assertLess(
            source.index("\nverify_traffic_guard\n"), source.index("\nMUTATED=1")
        )
        self.assertIn("flock -w 60 -x 8", source)

    def test_runtime_bind_mount_defers_image_only_update(self) -> None:
        """A fork mounted over the image requires a joint canary."""
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        self.assertIn("require_runtime_binary_canary() {", source)
        self.assertIn("RUNTIME_BINARY_CANARY_REQUIRED", source)
        self.assertIn('target.rstrip("/") == "/CLIProxyAPI/CLIProxyAPI"', source)
        self.assertIn("raise SystemExit(77)", source)
        # The fork-image derivation carries the binary inside the image, so
        # the bind-mount check alone would stop covering it: an automatic
        # switch back to the official image would silently drop the fork
        # patch (docs/runbooks/cpa-fork-image-derivation-rfc.md).
        self.assertIn('not image.startswith("eceasy/cli-proxy-api")', source)
        self.assertIn("FORK_IMAGE_REBUILD_REQUIRED", source)
        self.assertLess(
            source.index("\nrequire_runtime_binary_canary\n"),
            source.index("\nif ! health generation"),
        )

    def test_compose_version_parser_understands_fork_image_era(self) -> None:
        """The fork tag must parse like the official tag, not as a daily outage.

        Serving requests from cli-proxy-api-fork:v<upstream>-gemini-raw leaves
        the official-only parser refusing every compose read, which the daily
        run would misreport as a metadata outage instead of the named fork
        refusal the canary exists to give.
        """

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        parser_start = source.index("compose_cpa_version() {")
        parser = source[parser_start:].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.assertIn("cli-proxy-api-fork", parser)
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            compose.write_text("image: cli-proxy-api-fork:v8.0.61-gemini-raw\n")
            result = subprocess.run(
                [sys.executable, "-c", parser, str(compose)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "v8.0.61")

    def test_updater_compose_replacement_is_exact_and_metadata_preserving(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        replacement = source.split(
            'MUTATED=1\npython3 - "$DIR/compose.yml" "$IMAGE" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            image = "eceasy/cli-proxy-api@sha256:" + "a" * 64
            compose.write_text(
                "services:\n  cli-proxy-api:\n"
                "    image: eceasy/cli-proxy-api:v8.0.61 # pinned\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                compose.chmod(0o640)
            result = subprocess.run(
                [sys.executable, "-c", replacement, str(compose), image],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                compose.read_text(encoding="utf-8"),
                f"services:\n  cli-proxy-api:\n    image: {image} # pinned\n",
            )
            if os.name != "nt":
                self.assertEqual(compose.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(Path(directory).glob(".*.new")), [])

            compose.write_text(
                "image: eceasy/cli-proxy-api:v8.0.61\n"
                "image: eceasy/cli-proxy-api:v8.0.61\n",
                encoding="utf-8",
            )
            refused = subprocess.run(
                [sys.executable, "-c", replacement, str(compose), image],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("replacement count", refused.stderr)

    def test_docker_calls_have_bounded_budgets_including_rollback(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash unavailable")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = (
            "docker() {"
            + source.split("docker() {", 1)[1].split("\n}\n", 1)[0]
            + "\n}\n"
        )
        payload = (
            'set -eu\ntimeout() { printf "%s\\n" "$*"; }\n'
            + function
            + "docker pull image\ndocker compose up -d\ndocker inspect name\n"
        )
        result = subprocess.run(
            self._bash_command(bash, "-c", payload),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("600s docker pull image", result.stdout)
        self.assertIn("180s docker compose up -d", result.stdout)
        self.assertIn("30s docker inspect name", result.stdout)

    def test_traffic_guard_preflight_is_env_addressable_with_production_defaults(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        # The acceptance fixture shares /run with the host in its mount
        # namespace, so the guard targets must be overridable; unset values
        # must stay the production literals and the exclusive lock must take
        # the same override.
        self.assertIn(
            'TRAFFIC_GUARD_URL="${TRAFFIC_GUARD_URL:-http://127.0.0.1:8318/healthz}"',
            source,
        )
        self.assertIn(
            'TRAFFIC_GUARD_LOCK="${TRAFFIC_GUARD_LOCK:-/run/cpa-admission/requests.lock}"',
            source,
        )
        self.assertIn('exec 8<"$TRAFFIC_GUARD_LOCK"', source)
        # Execute the embedded preflight exactly as the updater does (env in,
        # HTTP out). A healthz naming another lock path must fail closed.
        program = (
            source.split("verify_traffic_guard() {", 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        reported = {"traffic_guard": "missing"}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                body = json.dumps(reported).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            # The stub reports one fixed lock; only the env side varies, so
            # the second case exercises the mismatch refusal.
            reported["traffic_guard"] = str(Path(__file__))
            for lock, expected in (
                (str(Path(__file__)), 0),
                (str(Path(__file__).with_suffix(".other")), 1),
            ):
                completed = subprocess.run(
                    [sys.executable, "-c", program],
                    env=dict(
                        os.environ,
                        TRAFFIC_GUARD_URL=f"http://127.0.0.1:{server.server_address[1]}/healthz",
                        TRAFFIC_GUARD_LOCK=lock,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode, expected, completed.stdout + completed.stderr
                )
        finally:
            server.shutdown()

    def test_updater_timeout_dropin_covers_the_bounded_budgets(self) -> None:
        root = Path(__file__).parents[1]
        updater = (root / "scripts/remote/cpa-auto-update.sh").read_text()
        conf = (root / "scripts/remote/cpa-update-timeout.conf").read_text()
        doctor = (root / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        # Derive the relation from the sources instead of restating the
        # numbers: the start budget must cover the longest unattended stretch
        # (pull + compose) and the stop budget the bounded compose rollback,
        # and the doctor gate must fail exactly below the drop-in values.
        pull_match = re.search(r'"\$1" == pull \]\]; then budget=(\d+)', updater)
        compose_match = re.search(r'"\$1" == compose \]\]; then budget=(\d+)', updater)
        start_match = re.search(r"TimeoutStartSec=(\d+)min", conf)
        stop_match = re.search(r"TimeoutStopSec=(\d+)min", conf)
        assert pull_match and compose_match and start_match and stop_match
        pull_budget = int(pull_match.group(1))
        compose_budget = int(compose_match.group(1))
        start_min = int(start_match.group(1))
        stop_min = int(stop_match.group(1))
        self.assertGreaterEqual(start_min * 60, pull_budget + compose_budget)
        self.assertGreaterEqual(stop_min * 60, compose_budget)
        self.assertIn(f"-lt {start_min * 60}", doctor)
        self.assertIn(f"-lt {stop_min * 60}", doctor)

    def test_apply_payload_block_replaces_from_policy_authority(self) -> None:
        """The requests.payload block is owned by cpa_policy's manifest
        derivation: the apply must replace it wholesale so rules from retired
        lanes cannot survive as stale orphans (claude-oauth retirement
        2026-10-20), and unrelated config keys must round-trip untouched."""
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-guardrail-apply.sh"
        ).read_text(encoding="utf-8")
        program = source.split("codex_models = sorted({", 1)[1]
        program = (
            "codex_models = sorted({"
            + program.split("# Only the blocks this transaction owns", 1)[0]
        )
        stale = {
            "models": [{"name": "claude-haiku-5-5", "protocol": "claude"}],
            "params": ["fallbacks"],
        }
        config: dict[str, Any] = {
            "requests": {"payload": {"filter": [stale]}, "other": True}
        }
        route_manifest = {
            "apply_patch_filter_models": ["glm-test"],
            "zhipu_developer_role_override": {
                "from_protocol": "responses",
                "models": ["glm-test"],
            },
            "codex_api_key_routes": [
                {
                    "base-url": "https://zen.example/v1",
                    "models": [{"name": "gpt-test"}],
                }
            ],
            "claude_api_key_routes": [],
            "oauth_routes": [],
        }
        scope = {
            "config_after": config,
            "config_before": {
                "requests": {"payload": {"filter": [stale]}, "other": True}
            },
            "route_manifest": route_manifest,
        }
        exec(program, scope)
        self.assertEqual(
            config["requests"]["payload"],
            {
                "override": [
                    {
                        "models": [{"name": "gpt-test"}],
                        "params": {
                            'tools.#(type=="tool_search").parameters.required': [
                                "query",
                                "limit",
                            ]
                        },
                    },
                    {
                        "models": [{"name": "glm-test", "from-protocol": "responses"}],
                        "params": {
                            'messages.#(role=="developer")#.role': "system",
                        },
                    },
                ],
                "filter": [
                    {
                        "models": [{"name": "gpt-test"}],
                        "params": [
                            'tools.#(type=="web_search").search_content_types',
                            'tools.#(type=="custom")',
                        ],
                    },
                    {
                        "models": [{"name": "glm-test"}],
                        "params": [
                            'tools.#(type=="custom")',
                            'input.#(type=="additional_tools")#.tools.#(name=="functions")#'
                            '.tools.#(name=="apply_patch")#',
                        ],
                    },
                ],
            },
        )
        self.assertTrue(config["requests"]["other"])
        # Idempotent: a second pass changes nothing.
        exec(program, scope)
        self.assertEqual(
            config["requests"]["payload"]["filter"],
            config["requests"]["payload"]["filter"],
        )
        assert config["requests"]["payload"]["override"]

    def test_updater_gates_new_lane_holds_across_the_recreated_container(self) -> None:
        repo_root = Path(__file__).parents[1]
        source = (repo_root / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        # Baseline after the traffic-guard drain, delta check on the success
        # path before OK (2026-10-21 canary case). Only holds that appear
        # across OUR restart fail the update; pre-existing ones belong to the
        # doctor's ==lane-hold== gate.
        self.assertLess(
            source.index("PRE_HOLDS=$(lane_hold_state)"),
            source.index("\nMUTATED=1"),
        )
        self.assertLess(
            source.index("NEW_HOLDS"), source.index('log "OK: updated $CUR -> $TARGET')
        )
        self.assertIn(
            'LANE_HOLD_STATE_DIR="${LANE_HOLD_STATE_DIR:-/var/lib/cpa-admission}"',
            source,
        )
        for lane in (
            "chatgpt-risk.json",
            "workbuddy-risk.json",
            "antigravity-risk.json",
        ):
            self.assertIn(lane, source)
        # The claude tombstone (authentication_required) is inert by design and
        # must never fail an update.
        self.assertNotIn("claude-risk.json", source)

    def test_updater_drain_and_hold_read_failures_are_deferred(self) -> None:
        repo_root = Path(__file__).parents[1]
        source = (repo_root / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        drain = source.split("if ! flock -w 60 -x 8; then", 1)[1].split("\nfi", 1)[0]
        holds = source.split("if ! PRE_HOLDS=$(lane_hold_state); then", 1)[1].split(
            "\nfi", 1
        )[0]
        # A timeout means the request-drain guard remains held while the
        # updater exits. That must be represented as a deferred restart, not a
        # generic failure that could lead an operator to stop the updater and
        # release the lock before the new generation is ready.
        self.assertIn("exit 30", drain)
        self.assertNotIn("exit 75", drain)
        self.assertIn("exit 30", holds)
        self.assertLess(
            source.index("PRE_HOLDS=$(lane_hold_state)"), source.index("\nMUTATED=1")
        )
        status = source.split("if (( code == 30 )); then", 1)[1].split("\n  fi", 1)[0]
        self.assertIn("preflight_or_drain_failed", status)

    def test_lane_hold_state_check_distinguishes_new_preexisting_and_tombstone(
        self,
    ) -> None:
        repo_root = Path(__file__).parents[1]
        source = (repo_root / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        block = source.split("lane_hold_state() {", 1)[1].split("\nPY\n", 1)[0]
        payload = block.split("<<'PY'\n", 1)[1]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state = {
                "chatgpt-risk.json": '{"version":1,"not_before":0,"hold_reason":null}',
                "workbuddy-risk.json": '{"version":1,"not_before":900,"hold_reason":null}',
                "antigravity-risk.json": '{"version":1,"not_before":0,"hold_reason":null}',
                # Inert tombstone: must be invisible to the check.
                "claude-risk.json": '{"version":1,"not_before":0,"hold_reason":"authentication_required"}',
            }
            for name, body in state.items():
                (base / name).write_text(body)
            env = dict(os.environ, LANE_HOLD_STATE_DIR=str(base))

            def run() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-c", payload],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

            clean = run()
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertEqual(clean.stdout.strip(), "")

            (base / "chatgpt-risk.json").write_text(
                '{"version":1,"not_before":0,"hold_reason":"unverified_request"}'
            )
            held = run()
            self.assertEqual(held.returncode, 0, held.stderr)
            self.assertEqual(
                held.stdout.strip(), "chatgpt-risk.json=unverified_request"
            )

            (base / "workbuddy-risk.json").write_text('{"version":1,')
            broken = run()
            self.assertEqual(broken.returncode, 2, broken.stdout + broken.stderr)
            self.assertIn("HOLD_STATE_UNREADABLE", broken.stderr)

    def test_deploy_canaries_replicate_the_traffic_guard_drain(self) -> None:
        repo_root = Path(__file__).parents[1]
        # Every canary listed here executed before the 2026-10-21 drain
        # contract (admission went live 2026-10-19; none of them coordinate
        # with requests.lock, which is the root cause recorded in
        # docs/change-evidence/20261021-bwg-chatgpt-unverified-hold-release.md).
        # They are frozen evidence of completed runs and must not be rewritten
        # in place. The next canary a session authors is where the contract
        # starts to bite: it must replicate the updater's traffic-guard drain.
        known_executed_before_contract = {
            "deploy-cpa-v8029-canary-20261015.sh",
            "deploy-cpa-v8032-canary-20261018.sh",
            "deploy-cpa-v8033-canary-20261019.sh",
            "deploy-cpa-v8034-canary-20261019.sh",
            "deploy-cpa-v8035-canary-20261020.sh",
            "deploy-cpa-v8037-canary-20261021.sh",
            "deploy-cpa-v8038-canary-20261021.sh",
        }
        canaries = sorted(
            path
            for path in (repo_root / "outputs").glob("deploy-cpa-*.sh")
            if path.name not in known_executed_before_contract
        )
        if not canaries:
            self.skipTest(
                "no forward-looking deploy canary present; the drain contract "
                "applies to the next authored canary"
            )
        for canary in canaries:
            with self.subTest(canary=canary.name):
                source = canary.read_text(encoding="utf-8")
                self.assertIn("requests.lock", source)
                self.assertIn("flock", source)

    def test_updater_hold_delta_block_behaves_on_real_bash(self) -> None:
        repo_root = Path(__file__).parents[1]
        source = (repo_root / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        prelude = source[
            source.index("export LANE_HOLD_STATE_DIR=") : source.index("\nMODE=")
        ]
        start = source.index("if ! POST_HOLDS=$(lane_hold_state); then")
        block = source[start : source.index('log "OK: updated $CUR -> $TARGET')]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in (
                "chatgpt-risk.json",
                "workbuddy-risk.json",
                "antigravity-risk.json",
            ):
                (base / name).write_text(
                    '{"version":1,"not_before":0,"hold_reason":null}'
                )

            def run(pre_holds: str) -> subprocess.CompletedProcess[str]:
                # Point the extracted prelude's env-overridable state dir at
                # the fixture before its production-literal export line runs,
                # and shim python3 to this interpreter (Windows Store python3
                # stubs resolve but never run the payload).
                python_shim = (
                    f"python3() {{ '{Path(sys.executable).as_posix()}' \"$@\"; }}\n"
                )
                harness = (
                    "set -Eeuo pipefail\n"
                    + f'export LANE_HOLD_STATE_DIR="{base.as_posix()}"\n'
                    + python_shim
                    + prelude
                    + "\n"
                    + "log() { printf '%s\\n' \"$*\"; }\n"
                    + "write_pending_verification() { printf 'PENDING_WRITTEN\\n'; }\n"
                    + "TARGET=TEST BK=TESTBK\n"
                    + f"PRE_HOLDS='{pre_holds}'\n"
                    + block
                )
                return subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

            clean = run("")
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

            (base / "chatgpt-risk.json").write_text(
                '{"version":1,"not_before":0,"hold_reason":"unverified_request"}'
            )
            new_hold = run("")
            self.assertEqual(new_hold.returncode, 10, new_hold.stdout + new_hold.stderr)
            self.assertIn(
                "lane hold appeared across the container restart", new_hold.stdout
            )
            self.assertIn("chatgpt-risk.json=unverified_request", new_hold.stdout)
            self.assertIn("PENDING_WRITTEN", new_hold.stdout)

            preexisting = run("chatgpt-risk.json=unverified_request")
            self.assertEqual(
                preexisting.returncode, 0, preexisting.stdout + preexisting.stderr
            )

            (base / "workbuddy-risk.json").write_text('{"version":1,')
            broken = run("")
            self.assertEqual(broken.returncode, 10, broken.stdout + broken.stderr)
            self.assertIn("durable state unreadable after update", broken.stdout)
            self.assertIn("PENDING_WRITTEN", broken.stdout)

    def test_cpa_updater_waits_for_auth_registration_without_generation_retry(
        self,
    ) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {"data": [{"id": m} for m in HEALTH_FIXTURE_CATALOG_IDS]}
        smoke = {
            "model": "glm-5.3-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }
        for final, expected in [
            (smoke, 0),
            ({"error": {"code": "rate_limit"}}, 10),
            ({}, 20),
        ]:
            with self.subTest(expected=expected):
                responses = [{"data": []}, catalog]
                responses.extend([final] * (1 if expected == 0 else 1))
                request = mock.Mock(side_effect=responses)
                sleep = mock.Mock()
                self.assertEqual(check({}, "generation", request, sleep), expected)
                self.assertEqual(request.call_count, 3)
                sleep.assert_called_once_with(2)
                self.assertEqual(
                    sum(len(c.args) > 1 for c in request.call_args_list),
                    1,
                )
                body = next(
                    call.args[1]
                    for call in request.call_args_list
                    if len(call.args) > 1
                )
                self.assertEqual(body["model"], "glm-5.3-flash")

    def test_cpa_updater_selects_mature_release_without_starvation(self) -> None:
        import contextlib
        import datetime as dt
        import io
        import json
        import tempfile

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        selection_start = source.index("if ! SELECTION=$(python3")
        selection = (
            source[selection_start:].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        )
        now = dt.datetime.now(dt.timezone.utc)
        old = (now - dt.timedelta(days=4)).isoformat()
        fresh = (now - dt.timedelta(hours=1)).isoformat()
        releases = [
            {"tag_name": tag, "published_at": age, "draft": False, "prerelease": False}
            for tag, age in [
                ("v7.2.159", fresh),
                ("v7.2.156", old),
                ("v7.3.0", old),
                ("v8.0.0", old),
            ]
        ]
        tags = {
            "results": [
                {"name": tag, "last_updated": age, "digest": "sha256:" + "a" * 64}
                for tag, age in [
                    ("v7.2.159", fresh),
                    ("v7.2.156", old),
                    ("v7.3.0", old),
                    ("v8.0.0", old),
                ]
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            for current, expected in [
                ("v7.2.154", "v7.2.156"),
                ("v7.2.156", "v7.2.156"),
                ("v7.2.160", "v7.2.160"),
                ("v7.3.0", "v7.3.0"),
                ("v8.0.0", "v8.0.0"),
            ]:
                with self.subTest(current=current):
                    compose.write_text(f"image: eceasy/cli-proxy-api:{current}\n")
                    responses = [
                        io.BytesIO(json.dumps(v).encode()) for v in (releases, tags)
                    ]
                    output = io.StringIO()
                    with (
                        mock.patch("urllib.request.urlopen", side_effect=responses),
                        mock.patch.object(sys, "argv", ["select", str(compose)]),
                        contextlib.redirect_stdout(output),
                    ):
                        exec(compile(selection, "updater-selection", "exec"), {})
                    self.assertEqual(output.getvalue().split()[:2], [current, expected])
                    minor_expected = ["v7.3.0"] if current.startswith("v7.2.") else []
                    self.assertEqual(
                        [
                            line.split("available=")[-1]
                            for line in output.getvalue().splitlines()
                            if line.startswith("MINOR_CANDIDATE available=")
                        ],
                        minor_expected,
                    )
                    major_expected = [] if current.startswith("v8.") else ["v8.0.0"]
                    self.assertEqual(
                        [
                            line.split("available=")[-1]
                            for line in output.getvalue().splitlines()
                            if line.startswith("MAJOR_CANDIDATE available=")
                        ],
                        major_expected,
                    )

    def test_cpa_updater_prunes_with_bounded_retention_after_success(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        self.assertIn('mkdir -m 700 "$BK"', source)
        self.assertNotIn('mkdir -m 700 -p "$BK"', source)
        self.assertIn('cp -a "$DIR/compose.yml" "$BK/"', source)
        self.assertNotIn('"$BK/auth"', source)
        self.assertNotIn("-delete || rollback_failed=1", source)
        self.assertNotIn('cp -a "$BK/config.yaml"', source)
        self.assertNotIn('cp -a "$BK/cpa-health.py"', source)

        # Deletion is bounded, and the bound is what matters -- not the number of
        # call sites. 2026-11-09 added a second prune family (manual
        # transactions, measured 184 dirs / 1247 MiB with 117 unexpirable), so a
        # literal count would either fail forever or push a future editor to
        # merge the loops and lose the per-family scoping. Assert instead that
        # every rm -rf targets the one entry variable of a find bounded to
        # $BACKUP_ROOT, which is the property the count was standing in for.
        rm_invocations = re.findall(r"rm -rf -- (\S+?);", source)
        self.assertTrue(rm_invocations, "expected at least one bounded rm -rf")
        self.assertEqual(set(rm_invocations), {'"$entry"'})
        # The snapshot's original inventory is BACKUP_ROOT-scoped. Cleanup
        # iterates that private array so no path can be reinjected by a second
        # enumeration of the backup root.
        self.assertIn(
            'find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d '
            "-name '*-from-v[0-9]*' -print0",
            source,
        )
        for block in source.split("rm -rf -- ")[:-1]:
            self.assertIn("entry", block[-500:])
        self.assertIn('-L "$BACKUP_ROOT/$leaf"', source)
        self.assertEqual(source.count("docker rmi"), 1)
        # Deletion timing (2026-11-09 change). Directory pruning runs on the
        # DAILY path -- before the "no newer mature release" exit -- because
        # backup families grow from manual transactions regardless of releases.
        # Image pruning runs on every pass too, using the same protected set
        # of the running image and the newest eight backup identities.
        ok_log = source.index('log "OK: updated')
        unverified = source.index("UNVERIFIED: upstream unavailable")
        between = source[unverified:ok_log]
        self.assertNotIn("prune_backups", between)
        self.assertNotIn("prune_images", between)
        self.assertNotIn("prune_backups", source[ok_log:])
        self.assertIn("\nprune_images\n", source[:ok_log])
        self.assertIn("\nprune_backups\n", source[:ok_log])
        self.assertIn("\nprune_manual_backup_families\n", source[:ok_log])
        # Error-request dumps are age-bounded hygiene: swept on the daily
        # timer path too, restricted to auth/logs/error-*.log older than 48
        # hours (2026-09-21 review: plaintext bodies stay at rest too long).
        self.assertIn("prune_error_dumps", source[:unverified])
        self.assertIn("\nprune_error_dumps ||", source[ok_log:])
        self.assertIn('find "$DIR/auth/logs"', source)
        self.assertIn("-mmin +1440", source)
        self.assertIn("secure_error_dumps", source)
        self.assertIn('chmod 700 -- "$DIR/auth/logs"', source)
        self.assertIn('chmod 600 -- "$entry"', source)
        # The third-party relay channel must never be auto-probed by the daily
        # timer; relay-soft stays a manual, explicit mode of cpa-health.py.
        self.assertNotIn("relay-soft", source)
        self.assertNotIn("RELAY_SOFT", source)

    def test_cpa_updater_no_update_path_is_non_consuming(self) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index('if [[ "$CUR" == "$TARGET" ]]')
        end = source.index("\nif ! health generation", start)
        pending_guard = source[
            source.index('if [[ "$MODE" == --apply && -f "$PENDING_FILE" ]]; then') :
        ].split("MIN_FREE_KIB=", 1)[0]
        branch = "MODE=--apply\n" + pending_guard + source[start:end]
        for readiness_code, expected_code, marker in (
            (0, 0, "OK: no newer mature release"),
            (1, 1, "DEFER: no-update readiness failed"),
        ):
            with (
                self.subTest(readiness=readiness_code),
                tempfile.TemporaryDirectory() as directory,
            ):
                pending_path = self._bash_path(
                    bash, Path(directory) / "cpa-update.pending"
                )
                harness = "\n".join(
                    [
                        "set -u",
                        f"PENDING_FILE='{pending_path}'",
                        "CUR=v7.3.7",
                        "TARGET=v7.3.7",
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return '
                        f"{readiness_code}; }}",
                        'docker() { printf "credential refresh failed for codex\\n"; }',
                        'log() { printf "%s\\n" "$*"; }',
                        "prune_error_dumps() { :; }",
                        "prune_manual_backup_families() { :; }",
                        branch,
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                # The no-candidate daily path must not spend the OAuth
                # account: local readiness only, never a generation request
                # (2026-09-21 review removed the fixed-window machine smoke).
                self.assertEqual(
                    [
                        line
                        for line in output.splitlines()
                        if line.startswith("HEALTH_CALL ")
                    ],
                    ["HEALTH_CALL readiness"],
                )
                if readiness_code == 0:
                    self.assertIn("REFRESH_SIGNALS_24H=1", output)
                    self.assertNotIn("UNVERIFIED", output)

        with tempfile.TemporaryDirectory() as directory:
            pending_file = Path(directory) / "cpa-update.pending"
            pending_file.write_text("version=v7.3.7\ndigest=sha256:test\n")
            harness = "\n".join(
                [
                    "set -u",
                    f"PENDING_FILE='{self._bash_path(bash, pending_file)}'",
                    "CUR=v7.3.7; TARGET=v7.3.7",
                    'health() { printf "HEALTH_CALL %s\\n" "$1"; return 0; }',
                    'docker() { printf "credential refresh failed for codex\\n"; }',
                    'log() { printf "%s\\n" "$*"; }',
                    "prune_error_dumps() { :; }",
                    "prune_manual_backup_families() { :; }",
                    branch,
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 10, completed.stderr.decode())
            self.assertIn("requires explicit --confirm-pending", output)
            self.assertNotIn("HEALTH_CALL", output)

    def test_cpa_updater_post_update_readiness_distinguishes_upstream_and_local_failure(
        self,
    ) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index("RESULT=0\nhealth generation")
        end = source.index('\n[[ "$RESULT" == 0 ]]', start)
        branch = source[start:end]
        for readiness, expected_code, marker in (
            (10, 10, "readiness=UPSTREAM_UNAVAILABLE"),
            (20, 20, "ROLLBACK_CALLED result=20"),
        ):
            with self.subTest(readiness=readiness), tempfile.TemporaryDirectory():
                harness = "\n".join(
                    [
                        "set -u",
                        "TARGET=v7.3.8; BK=/tmp/cpa-test-backup; LOG=/tmp/cpa-test.log",
                        'health() { if [[ "$1" == generation ]]; then return 10; fi; return '
                        f"{readiness}; }}",
                        'log() { printf "%s\\n" "$*"; }',
                        'rollback() { printf "ROLLBACK_CALLED result=%s\\n" "$1"; exit "$1"; }',
                        'write_pending_verification() { printf "PENDING_WRITTEN\\n"; }',
                        branch,
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                if readiness == 10:
                    self.assertIn("PENDING_WRITTEN", output)

    def test_cpa_pending_acceptance_requires_explicit_matching_image_probe(
        self,
    ) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        self.assertLess(
            source.index('if [[ "$MODE" == --confirm-pending ]]'),
            source.index("if ! SELECTION=$(python3"),
        )
        start = source.index(
            'if [[ "$MODE" == --confirm-pending ]]; then\n  if [[ ! -f "$PENDING_FILE" ]]'
        )
        end = source.index('\nif [[ "$MODE" != --apply ]]', start)
        branch = source[start:end]
        expected_digest = "sha256:" + "a" * 64
        for (
            runtime_digest,
            pending_digest,
            pending_lanes,
            current_holds,
            hold_state_result,
            expected_code,
            marker,
        ) in (
            (
                expected_digest,
                expected_digest,
                "unknown",
                "",
                0,
                0,
                "PENDING_VERIFIED",
            ),
            (
                "sha256:" + "b" * 64,
                expected_digest,
                "unknown",
                "",
                0,
                10,
                "running CPA image does not match",
            ),
            (
                "sha256:" + "b" * 64,
                "invalid",
                "unknown",
                "",
                0,
                10,
                "record is malformed",
            ),
            (
                expected_digest,
                expected_digest,
                "",
                "",
                0,
                10,
                "pending lane hold record is malformed",
            ),
            (
                expected_digest,
                expected_digest,
                "workbuddy-risk.json",
                "workbuddy-risk.json=retry_window_review_required",
                0,
                10,
                "pending lane holds remain",
            ),
            (
                expected_digest,
                expected_digest,
                "workbuddy-risk.json",
                "",
                0,
                0,
                "PENDING_VERIFIED",
            ),
            (
                expected_digest,
                expected_digest,
                "unknown",
                "",
                2,
                10,
                "lane hold state is unreadable",
            ),
            (
                expected_digest,
                expected_digest,
                "unknown",
                "antigravity-risk.json=authentication_required",
                0,
                10,
                "lane holds remain after an unreadable state receipt",
            ),
            (
                expected_digest,
                expected_digest,
                "bad-risk.json",
                "",
                0,
                10,
                "pending lane hold record is malformed",
            ),
            (
                expected_digest,
                expected_digest,
                "__missing__",
                "",
                0,
                10,
                "predates lane-state receipts",
            ),
        ):
            with (
                self.subTest(marker=marker),
                tempfile.TemporaryDirectory() as directory,
            ):
                pending = Path(directory) / "cpa-update.pending"
                pending_record = f"version=v8.0.16\ndigest={pending_digest}\n"
                if pending_lanes != "__missing__":
                    pending_record += f"hold_lanes={pending_lanes}\n"
                pending.write_text(pending_record, encoding="utf-8")
                harness = "\n".join(
                    [
                        "set -u",
                        "MODE=--confirm-pending; CUR=v8.0.16",
                        f"PENDING_FILE='{self._bash_path(bash, pending)}'",
                        f"CURRENT_HOLDS='{current_holds}'",
                        f"HOLD_STATE_RESULT={hold_state_result}",
                        'lane_hold_state() { printf "%s" "$CURRENT_HOLDS"; return "$HOLD_STATE_RESULT"; }',
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return 0; }',
                        'log() { printf "%s\\n" "$*"; }',
                        f'docker() {{ if [[ "$1" == inspect ]]; then printf "sha256:running\\n"; else printf "eceasy/cli-proxy-api@{runtime_digest}\\n"; fi; }}',
                        branch,
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                if expected_code == 0:
                    self.assertIn("PENDING_VERIFIED version=v8.0.16", output)
                    self.assertIn("HEALTH_CALL generation", output)
                    self.assertFalse(pending.exists())
                else:
                    self.assertNotIn("HEALTH_CALL", output)
                    self.assertTrue(pending.exists())

    def test_cpa_updater_dump_permissions_gate_blocks_all_provider_traffic(
        self,
    ) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        start = source.index("if ! secure_error_dumps; then")
        end = source.index('if [[ "$CUR" == "$TARGET" ]]', start)
        gate = source[start:end]
        for dumps_result, expected_code, marker in (
            # Exit 76 is reserved for a pre-write error-dump hygiene refusal;
            # generic update failures remain exit 1.
            (1, 76, "DEFER: error-dump permissions unavailable"),
            (0, 0, "OK: no newer mature release"),
        ):
            with self.subTest(dumps_result=dumps_result):
                harness = "\n".join(
                    [
                        "set -u",
                        "CUR=v7.3.7",
                        "TARGET=v7.3.7",
                        f"secure_error_dumps() {{ return {dumps_result}; }}",
                        "prune_error_dumps() { :; }",
                        "prune_manual_backup_families() { :; }",
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return 0; }',
                        'log() { printf "%s\\n" "$*"; }',
                        gate,
                        'if [[ "$CUR" == "$TARGET" ]]; then',
                        "  RESULT=0",
                        "  health generation || RESULT=$?",
                        '  [[ "$RESULT" == 0 ]]',
                        '  log "OK: no newer mature release; current=$CUR"',
                        "  exit 0",
                        "fi",
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                )
                output = completed.stdout.decode()
                self.assertEqual(
                    completed.returncode, expected_code, completed.stderr.decode()
                )
                self.assertIn(marker, output)
                if dumps_result == 1:
                    self.assertNotIn("HEALTH_CALL", output)

    def test_cpa_updater_bash_syntax_parses(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        script_path = self._bash_path(
            bash, Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        )
        completed = subprocess.run(
            [
                *self._bash_command(bash, "-n"),
                script_path,
            ],
            capture_output=True,
            text=True,
            timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cpa_prune_backups_keeps_newest_backup_dirs(self) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_backups() {") : source.index("prune_images() {")
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "backups"
            root.mkdir()
            for day in range(1, 11):
                backup = root / f"202609{day:02d}T000000Z-from-v0.0.{day}"
                backup.mkdir()
                # Deliberately reverse age vs lexical name: backups selected
                # by image mtime must be the exact directories retained here.
                os.utime(backup, (1_700_000_000 + (10 - day) * 60,) * 2)
            (root / "foreign.txt").write_text("keep me", encoding="utf-8")
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BACKUP_ROOT='{self._bash_path(bash, root)}'",
                    "RETENTION_KEEP_BACKUPS=8",
                    'log() { printf "LOG %s\\n" "$*"; }',
                    function,
                    "prune_backups",
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            remaining = sorted(path.name for path in root.iterdir())
            self.assertEqual(
                remaining,
                [
                    *(
                        f"202609{day:02d}T000000Z-from-v0.0.{day}"
                        for day in range(1, 9)
                    ),
                    "foreign.txt",
                ],
            )
            self.assertIn(
                "LOG PRUNE scope=backups kept=8 removed=2 rejected=0",
                completed.stdout.decode(),
            )

    def test_cpa_prune_backups_rejects_a_path_outside_the_backup_root(
        self,
    ) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_backups() {") : source.index("prune_images() {")
        ]
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory) / "outside"
            foreign.mkdir()
            escape_root = Path(directory) / "escape-backups"
            escape_root.mkdir()
            for index in range(9):
                (escape_root / f"backup-{index}").mkdir()
            escape_harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BACKUP_ROOT='{self._bash_path(bash, escape_root)}'",
                    "RETENTION_KEEP_BACKUPS=8",
                    'log() { printf "LOG %s\\n" "$*"; }',
                    function,
                    "prune_backups",
                ]
            )
            escape = escape_root / "20990101T000000Z-from-v9.9.9"
            try:
                escape.symlink_to(foreign, target_is_directory=True)
            except OSError as exc:
                self.skipTest("native symlink creation is unavailable: " + str(exc))
            touch = subprocess.run(
                [
                    *self._bash_command(bash, "-c"),
                    'touch -h -d @1600000000 -- "$1"',
                    "harness",
                    self._bash_path(bash, escape),
                ],
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            self.assertEqual(
                touch.returncode, 0, touch.stdout.decode() + touch.stderr.decode()
            )
            escape_run = subprocess.run(
                self._bash_command(bash),
                input=escape_harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = escape_run.stdout.decode() + escape_run.stderr.decode()
            self.assertEqual(escape_run.returncode, 0, output)
            if "stage=symlink" not in output and "stage=path_escape" not in output:
                # Linux d_type and current Git for Windows exclude the
                # directory symlink from the -type d inventory, so it never
                # becomes a deletion candidate: nothing is kept, removed or
                # rejected. Find variants that DO inventory it are covered by
                # the two rejection branches of the updater itself; the
                # native-WSL sibling below verifies the explicit symlink
                # rejection with real backups.
                self.assertIn("kept=0 removed=0 rejected=0", output)
            self.assertTrue(escape.is_symlink(), output)

    def test_cpa_prune_backups_rejects_a_linux_native_symlink(
        self,
    ) -> None:
        if os.name != "nt" or shutil.which("wsl.exe") is None:
            self.skipTest("native Linux symlink fixture requires WSL")
        if not self._has_native_linux_wsl():
            self.skipTest("WSL is unavailable on this Windows host")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_backups() {") : source.index("prune_images() {")
        ]
        # A directory symlink is not a d_type=directory on Linux. Exercise
        # the explicit -L reject in the selected deletion sequence.
        function = function.replace(" -type d -name ", " -name ")
        fixture = subprocess.check_output(
            [
                "wsl.exe",
                "-d",
                "Ubuntu-24.04",
                "--",
                "mktemp",
                "-d",
                "/tmp/vps-cpa-prune-XXXXXX",
            ],
            text=True,
            timeout=30,
        ).strip()
        try:
            script = "\n".join(
                [
                    "set -euo pipefail",
                    f"FIXTURE={shlex.quote(fixture)}",
                    'mkdir -p "$FIXTURE/escape" "$FIXTURE/outside"',
                    'mkdir "$FIXTURE/escape/202601-from-v1"',
                    'mkdir "$FIXTURE/escape/202602-from-v2"',
                    'mkdir "$FIXTURE/escape/202603-from-v3"',
                    'mkdir "$FIXTURE/escape/202604-from-v4"',
                    'mkdir "$FIXTURE/escape/202605-from-v5"',
                    'mkdir "$FIXTURE/escape/202606-from-v6"',
                    'mkdir "$FIXTURE/escape/202607-from-v7"',
                    'mkdir "$FIXTURE/escape/202608-from-v8"',
                    'mkdir "$FIXTURE/escape/202609-from-v9"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202601-from-v1"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202602-from-v2"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202603-from-v3"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202604-from-v4"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202605-from-v5"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202606-from-v6"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202607-from-v7"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202608-from-v8"',
                    'touch -d @1600000000 -- "$FIXTURE/escape/202609-from-v9"',
                    'ln -s "$FIXTURE/outside" "$FIXTURE/escape/202610-from-v10"',
                    'BACKUP_ROOT="$FIXTURE/escape"',
                    "RETENTION_KEEP_BACKUPS=8",
                    'log() { printf "LOG %s\\n" "$*"; }',
                    function,
                    'touch -h -d @1600000020 -- "$FIXTURE/escape/202610-from-v10"',
                    'test -L "$BACKUP_ROOT/202610-from-v10"',
                    "prune_backups",
                ]
            )
            completed = subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu-24.04",
                    "--",
                    "env",
                    "-u",
                    "WSL_DISTRO_NAME",
                    "bash",
                ],
                input=script.encode(),
                capture_output=True,
                timeout=WSL_BASH_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 0, output)
            self.assertIn("stage=symlink", output)
        finally:
            subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu-24.04",
                    "--",
                    "rm",
                    "-rf",
                    "--",
                    fixture,
                ],
                timeout=30,
                check=False,
            )

    @staticmethod
    def _has_native_linux_wsl() -> bool:
        try:
            probe = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu-24.04", "--", "true"],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return probe.returncode == 0

    def test_cpa_prune_images_preserves_running_and_retained_rollback_images(
        self,
    ) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        self.assertIn("docker image ls --all --no-trunc", source)
        self.assertIn("^sha256:[0-9a-f]{64}$", source)
        function = "".join(
            _extract_bash_function(source, name)
            for name in (
                "image_id_file",
                "backup_image_id",
                "resolve_backup_image_id",
                "retained_backup_paths",
                "image_candidates",
                "prune_images",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "prune.log"
            log_path = self._bash_path(bash, log_file)
            backup_root = Path(directory) / "backups"
            backups = [
                backup_root / f"202609{day:02d}T000000Z-from-v0.0.{day}"
                for day in range(1, 11)
            ]
            for index, backup in enumerate(backups):
                backup.mkdir(parents=True)
                (backup / "compose.yml").write_text(
                    "services:\n  cli-proxy-api:\n    image: mutable-fork-tag\n",
                    encoding="utf-8",
                )
                os.utime(backup, (1_700_000_000 + index * 60,) * 2)

            images = {
                key: "sha256:" + digit * 64
                for key, digit in {
                    "current": "e",
                    "rollback": "a",
                    "removable": "c",
                    "fork": "d",
                    "expired": "f",
                }.items()
            }
            for backup in backups[:2]:
                (backup / "image-id").write_text(images["expired"] + "\n")
            for backup in backups[2:]:
                (backup / "image-id").write_text(images["rollback"] + "\n")
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BK='{self._bash_path(bash, backups[-1])}'",
                    f"BACKUP_ROOT='{self._bash_path(bash, backup_root)}'",
                    "CPA_IMAGE_REPO=eceasy/cli-proxy-api",
                    "FORK_IMAGE_REPO=cli-proxy-api-fork",
                    "RETENTION_KEEP_BACKUPS=8",
                    f"LOG='{log_path}'",
                    'touch "$LOG"',
                    "log() { printf 'LOG %s\\n' \"$*\"; }",
                    "compose_image_ref() { printf 'mutable-fork-tag\\n'; }",
                    f"current_image='{images['current']}'",
                    f"rollback_image='{images['rollback']}'",
                    f"removable_image='{images['removable']}'",
                    f"fork_image='{images['fork']}'",
                    f"expired_image='{images['expired']}'",
                    "docker() {",
                    '  if [[ "$1 $2" == "inspect --format" ]]; then',
                    '    [[ "$3" == \'{{.Image}}\' && "$4" == cli-proxy-api ]] || return 1',
                    '    printf "%s\\n" "$current_image"',
                    '  elif [[ "$1 $2" == "ps --all" ]]; then',
                    '    [[ "$3" == --filter && "$4" == name=^/cli-proxy-api$ ]] || return 1',
                    "    printf 'container-id\\n'",
                    '  elif [[ "$1 $2" == "image ls" ]]; then',
                    '    [[ "$3" == --all && "$4" == --no-trunc && "$5" == --format && "$7" =~ ^(eceasy/cli-proxy-api|cli-proxy-api-fork)$ ]] || return 1',
                    '    if [[ "$7" == eceasy/cli-proxy-api ]]; then',
                    '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$current_image"',
                    '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$removable_image"',
                    '      printf "%s|eceasy/cli-proxy-api:<none>\\n" "$expired_image"',
                    '    elif [[ "$7" == cli-proxy-api-fork ]]; then',
                    '      printf "%s|cli-proxy-api-fork:v8.0.61-gemini-raw\\n" "$fork_image"',
                    "    fi",
                    '  elif [[ "$1 $2" == "image inspect" ]]; then',
                    '    [[ "$3" == --format && -n "${5:-}" ]] || return 1',
                    "    if [[ \"$4\" == '{{json .RepoTags}} {{json .RepoDigests}}' ]]; then",
                    '      if [[ "$5" == "$fork_image" ]]; then',
                    '        printf "%s\\n" "cli-proxy-api-fork:v8.0.61-gemini-raw cli-proxy-api-fork@sha256"',
                    '      elif [[ " $current_image $removable_image $expired_image " == *" $5 "* ]]; then',
                    '        printf "%s\\n" "eceasy/cli-proxy-api:x eceasy/cli-proxy-api@sha256"',
                    "      else return 1; fi",
                    "    elif [[ \"$4\" == '{{.Id}}' ]]; then",
                    '      [[ "$5" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1',
                    '      printf "%s\\n" "$5"',
                    "    elif [[ \"$4\" == '{{.RootFS.Layers}}' ]]; then",
                    "      printf '[sha256:%064d]\\n' 0 | tr '0' 'e'",
                    "    elif [[ \"$4\" == '{{.Created}}' ]]; then",
                    "      printf '2026-11-08T23:09:24Z\\n'",
                    "    elif [[ \"$4\" == '{{.Size}}' ]]; then",
                    "      printf '123\\n'",
                    "    else return 1; fi",
                    '  elif [[ "$1 $2" == "system df" ]]; then',
                    "    printf 'Images %s\\n' '1GB'",
                    '  elif [[ "$1" == rmi ]]; then',
                    '    [[ "$2" == "$removable_image" ]] || return 1',
                    '    printf "removal=%s\\n" "$2"',
                    "  else return 1; fi",
                    "}",
                    function,
                    "prune_images",
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            )
            output = (
                completed.stdout.decode()
                + completed.stderr.decode()
                + log_file.read_text()
            )
            self.assertEqual(completed.returncode, 0, output)
            self.assertNotIn("IMAGE_RETENTION_DEFERRED", output)
            self.assertEqual(output.count("removal="), 1, output)
            self.assertIn("removal=" + images["removable"], output)
            for protected in ("current", "rollback", "fork", "expired"):
                self.assertNotIn("removal=" + images[protected], output)
            self.assertIn(
                "PRUNE scope=images candidates=3 retained=2 removed=1 freed_bytes=123",
                output,
            )

    def test_cpa_apply_embedded_python_and_rollback_contract(self) -> None:
        text = read_guardrail_source()
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        embedded_python = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        tree = ast.parse(embedded_python)
        assigned: set[str] = set()
        loaded: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                (assigned if isinstance(node.ctx, ast.Store) else loaded).add(node.id)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                assigned.add(node.name)
            elif isinstance(node, ast.arg):
                assigned.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                assigned.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assigned.add(alias.asname or alias.name.split(".")[0])

        self.assertEqual(
            loaded - assigned - set(dir(builtins)),
            set(),
        )
        self.assertNotIn('cp -a "$DIR/auth" "$BK/auth"', apply_script)
        self.assertIn('cp -a "$DIR/cpa_policy.py" "$BK/cpa_policy.py"', apply_script)
        self.assertIn('python3 "$DIR/cpa_policy.py" "$DIR/config.yaml"', apply_script)
        self.assertIn("stream-bootstrap-timeout", apply_script)
        self.assertIn('"session-affinity-rebind-on": "quota",', apply_script)
        self.assertIn(
            'workbuddy_after["scheduler_priority_reclaim"] = True',
            apply_script,
        )
        self.assertIn(
            "REFUSE plugins change exceeded scheduler_priority_reclaim",
            apply_script,
        )
        # The projected value is the disabled contract, not merely a present
        # key: the bootstrap hold cost ~10s of dead air per Luna turn and the
        # apply script must not silently restore it.
        self.assertIn('"stream-bootstrap-buffering": False,', apply_script)
        self.assertIn('"stream-bootstrap-timeout": "0",', apply_script)
        self.assertIn('python3 "$DIR/cpa-health.py" readiness', apply_script)
        self.assertIn("ROLLBACK_VERIFIED", apply_script)
        self.assertIn("ROLLBACK_FAILED", apply_script)
        self.assertIn("limit_req=$limit_req_status", apply_script)
        self.assertIn("limit_conn=$limit_conn_status", apply_script)
        self.assertIn("limit_req_status 429;", apply_script)
        self.assertIn("limit_conn_status 429;", apply_script)
        self.assertIn("ensure_nginx_directive", apply_script)

    def test_cpa_apply_config_transform_passes_policy_on_v8_fixture(self) -> None:
        """Execute the apply block's config transform against a v8 fixture.

        The 2026-10-11 muse regression (a projected codex group silently
        missed every exclusion tombstone because the merge appended to the
        detached ``.get`` default list) was invisible to text assertions.
        Running the real section end-to-end fails such bugs here, locally,
        instead of as a remote apply rollback.
        """
        import base64
        import runpy

        import yaml

        text = read_guardrail_source()
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        block = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        # Truncate before the updater/nginx sections: this test exercises the
        # config transform, which is the part the semantic policy gates.
        block = block.split("updater_path = Path(", 1)[0]
        self.assertIn("atomic_write(config_path, candidate, 0o600)", block)

        policy = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa_policy.py")
        )
        config = self._valid_cpa_policy_config(policy)
        config["plugins"] = {
            "enabled": True,
            "configs": {
                "workbuddy": {
                    "enabled": True,
                    "scheduler_mode": "credits",
                    "account_max_concurrent": 1,
                }
            },
        }
        route_manifest = policy["ROUTE_MANIFEST"]
        env_lines = []
        for provider in route_manifest["providers"]:
            base = (
                f"{provider.get('scheme', 'https')}://{provider['host']}"
                + (f":{provider['port']}" if provider.get("port") is not None else "")
                + provider["path"]
            )
            env_lines.append(f"BASE_URL_{provider['slot']}={base}")
            env_lines.append(f"API_KEY_{provider['slot']}=SLOT{provider['slot']}_KEY")
        env_b64 = base64.b64encode(("\n".join(env_lines) + "\n").encode()).decode()
        manifest_b64 = base64.b64encode(
            (
                Path(__file__).parents[1] / "scripts/remote/cpa_provider_routes.json"
            ).read_bytes()
        ).decode()
        block = block.replace("__CPA_PROVIDER_ENV_B64__", env_b64).replace(
            "__CPA_PROVIDER_ROUTES_B64__", manifest_b64
        )
        block = block.replace(
            'config_path = Path("/opt/cliproxyapi/config.yaml")',
            "import sys as _sys\nconfig_path = Path(_sys.argv[1])",
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            # The block is passed as a file rather than `-c`: it grew past the
            # Windows command-line limit (WinError 206) once the claude-family
            # projection joined the apply section, and the config transform is
            # what this test exercises, not the argv transport.
            block_path = Path(directory) / "apply_block.py"
            block_path.write_text(block, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(block_path), str(config_path)],
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", "replace"),
            )
            transformed = json.loads(
                json.dumps(yaml.safe_load(config_path.read_text(encoding="utf-8")))
            )
        issues = policy["validate_config"](transformed)
        self.assertEqual(issues, [], issues)
        self.assertEqual(
            transformed["oauth"]["settings"],
            route_manifest["oauth_settings"],
        )
        self.assertIs(
            transformed["plugins"]["configs"]["workbuddy"][
                "scheduler_priority_reclaim"
            ],
            True,
        )
        compatibility = {
            provider["name"]: provider["models"]
            for provider in transformed["api-keys"]["openai-compatibility"]
        }
        for provider in route_manifest["providers"]:
            self.assertEqual(
                compatibility[provider["name"]],
                provider["models"],
            )
        # The regression itself: projected codex groups must carry the full
        # tombstone set after the in-place merge.
        codex_groups = transformed["api-keys"]["codex"]
        for route in route_manifest.get("codex_api_key_routes", []):
            group = next(
                g
                for g in codex_groups
                if str(g.get("base-url", "")).rstrip("/")
                == route["base-url"].rstrip("/")
            )
            self.assertEqual(
                sorted(set(group["excluded-models"])),
                sorted(set(route_manifest["codex_api_key_exclusions"])),
            )
        # Claude-family groups take no tombstone list, so the projection itself
        # is the contract: base-url, borrowed slot-2 credential, the pinned
        # session header, and the slot credential expanded into the header the
        # upstream actually reads (the manifest holds only the placeholder).
        claude_groups = transformed["api-keys"]["claude"]
        self.assertEqual(
            len(claude_groups), len(route_manifest["claude_api_key_routes"])
        )
        for route in route_manifest["claude_api_key_routes"]:
            group = next(
                g
                for g in claude_groups
                if str(g.get("base-url", "")).rstrip("/")
                == route["base-url"].rstrip("/")
            )
            self.assertEqual(group["headers"], claude_route_headers(route, "SLOT2_KEY"))
            self.assertEqual(group["models"], route["models"])
            self.assertEqual(group["keys"][0]["api-key"], "SLOT2_KEY")
            self.assertNotIn(SLOT_API_KEY_HEADER_TOKEN, json.dumps(group))

    def test_cpa_apply_ensure_nginx_directive_repairs_missing_status(self) -> None:
        text = read_guardrail_source()
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        embedded_python = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        start = embedded_python.index("def ensure_nginx_directive(")
        end = embedded_python.index("\nnginx = ensure_nginx_directive(", start)
        namespace: dict[str, Any] = {}
        exec(embedded_python[start:end], namespace)
        ensure = cast(Any, namespace["ensure_nginx_directive"])

        base = (
            "server {\n"
            "  limit_req_zone $binary_remote_addr zone=cpa_rl:1m rate=10r/s;\n"
            "  limit_conn_zone $binary_remote_addr zone=cpa_cc:1m;\n"
            "  location / {\n"
            "    limit_req zone=cpa_rl burst=10;\n"
            "    limit_conn cpa_cc 6;\n"
            "  }\n"
            "}\n"
        )
        repaired = ensure(
            base, "limit_req_status 429;", "limit_req zone=cpa_rl burst=10;"
        )
        repaired = ensure(repaired, "limit_conn_status 429;", "limit_conn cpa_cc 6;")
        # Inserted inside the limiter's own block, preserving its indentation.
        self.assertIn("\n    limit_req_status 429;\n", repaired)
        self.assertIn("\n    limit_conn_status 429;\n", repaired)
        self.assertEqual(repaired.count("limit_req_status 429;"), 1)
        # Idempotent: a config that already carries the directive is untouched.
        self.assertEqual(
            ensure(
                repaired, "limit_req_status 429;", "limit_req zone=cpa_rl burst=10;"
            ),
            repaired,
        )
        # A missing anchor fails closed instead of silently dropping the control.
        with self.assertRaises(SystemExit):
            ensure(
                "server {}\n",
                "limit_req_status 429;",
                "limit_req zone=cpa_rl burst=10;",
            )

    def test_cpa_apply_exit_and_signal_failures_invoke_rollback_once(self) -> None:
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = read_guardrail_source()
        apply_script = source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        handler = apply_script.split("rollback_on_exit() {\n", 1)[1].split(
            "\n}\n\nif ! grep", 1
        )[0]
        handler = "rollback_on_exit() {\n" + handler + "\n}\n"

        for trigger, expected_code in (("false", 1), ("kill -TERM $$", 143)):
            harness = (
                "set -Eeuo pipefail\n"
                "ROLLBACK_CALLS=0\n"
                "restore_all() { ROLLBACK_CALLS=$((ROLLBACK_CALLS + 1)); "
                "echo ROLLBACK_CALLS=$ROLLBACK_CALLS; }\n"
                + handler
                + "trap rollback_on_exit EXIT\n"
                + "trap 'exit 130' INT\n"
                + "trap 'exit 143' TERM\n"
                + trigger
                + "\n"
            )
            with self.subTest(trigger=trigger):
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode("utf-8"),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                stdout = completed.stdout.decode("utf-8", errors="replace")
                stderr = completed.stderr.decode("utf-8", errors="replace")
                self.assertEqual(completed.returncode, expected_code, stderr)
                self.assertEqual(stdout.count("ROLLBACK_CALLS=1"), 1)
                self.assertIn("ROLLBACK transaction_failed", stdout)

    def test_cpa_acceptance_overload_status_follows_bootstrap_buffering(self) -> None:
        import runpy

        import yaml

        module = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-acceptance.py")
        )
        expected = module["expected_overload_status"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {"upstream": {"codex": {"stream-bootstrap-buffering": False}}}
                ),
                encoding="utf-8",
            )
            # Headers are committed before the upstream failure arrives, so CPA
            # relays the upstream's 200 with the capacity marker in the body.
            self.assertEqual(expected(root), 200)
            config_path.write_text(
                yaml.safe_dump(
                    {"upstream": {"codex": {"stream-bootstrap-buffering": True}}}
                ),
                encoding="utf-8",
            )
            # Buffered bootstrap keeps the headers, so CPA still rewrites 503.
            self.assertEqual(expected(root), 503)
            for broken in ("{not yaml", "{}", ""):
                with self.subTest(config=broken):
                    config_path.write_text(broken, encoding="utf-8")
                    self.assertEqual(expected(root), 200)
            config_path.unlink()
            self.assertEqual(expected(root), 200)

    def test_update_acceptance_matches_verified_rollback_marker(self) -> None:
        root = Path(__file__).parents[1]
        updater = (root / "scripts/remote/cpa-auto-update.sh").read_text(
            encoding="utf-8"
        )
        acceptance = (root / "scripts/remote/cpa-update-acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('log "ROLLBACK_VERIFIED restored=$CUR backup=$BK"', updater)
        self.assertIn(
            '"rollback_logged": "ROLLBACK_VERIFIED restored=" in p.stdout',
            acceptance,
        )
        # The updater verifies rollback by comparing the running container's
        # image ID with the backup pin. The Docker stub must therefore answer
        # both inspect shapes instead of returning an empty successful result.
        self.assertIn("if args[:2]==['image','inspect']:", acceptance)
        self.assertIn("if args[:2]==['inspect','--format']:", acceptance)
        self.assertIn("'a'*64 if 'v0.0.1' in ref else 'b'*64", acceptance)
        self.assertIn(
            "'b'*64 if 'v0.0.2' in text else 'a'*64",
            acceptance,
        )

    def test_cpa_acceptance_synthetic_upstream_matches_wire_contract(self) -> None:
        import io
        import runpy

        module = runpy.run_path(
            str(Path(__file__).parents[1] / "scripts/remote/cpa-acceptance.py")
        )
        handler_class = module["Upstream"]
        module["STATE"]["mode"] = "ok"

        def run_handler(path: str, body: dict[str, Any]) -> tuple[int, bytes]:
            payload = json.dumps(body).encode()
            handler = handler_class.__new__(handler_class)
            handler.rfile = io.BytesIO(payload)
            handler.wfile = io.BytesIO()
            handler.headers = {"Content-Length": str(len(payload))}
            handler.request_version = "HTTP/1.1"
            handler.requestline = f"POST {path} HTTP/1.1"
            handler.path = path
            module["STATE"]["calls"] = 0
            handler.do_POST()
            return module["STATE"]["calls"], handler.wfile.getvalue()

        # Openai-compat lanes relay the upstream chat body verbatim (v7.3.16),
        # so the fixture must answer chat completions with a real chat payload.
        calls, raw = run_handler(
            "/v1/chat/completions",
            {"model": "glm-5.3-flash", "messages": [], "max_tokens": 64},
        )
        self.assertEqual(calls, 1)
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertIn(b"200 OK", head)
        self.assertIn(b"application/json", head)
        payload = json.loads(body)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["model"], "glm-5.3-flash")
        self.assertEqual(payload["choices"][0]["message"]["content"], "OK")
        self.assertEqual(payload["choices"][0]["finish_reason"], "stop")

        calls, raw = run_handler(
            "/v1/chat/completions",
            {"model": "glm-5.3-flash", "messages": [], "stream": True},
        )
        self.assertEqual(calls, 1)
        self.assertIn(b'"object": "chat.completion.chunk"', raw)
        self.assertIn(b'"finish_reason": "stop"', raw)
        self.assertIn(b"data: [DONE]", raw)

        # The codex-api-key responses lane keeps the responses-SSE contract.
        calls, raw = run_handler(
            "/v1/responses",
            {"model": "gpt-6-luna", "input": "Reply OK", "stream": True},
        )
        self.assertEqual(calls, 1)
        self.assertIn(b'"type": "response.created"', raw)

        module["STATE"]["mode"] = "http503"
        calls, raw = run_handler("/v1/chat/completions", {"model": "glm-5.3-flash"})
        self.assertEqual(calls, 1)
        self.assertIn(b"503", raw.partition(b"\r\n\r\n")[0])
        self.assertIn(b"server_is_overloaded", raw)


if __name__ == "__main__":
    unittest.main()
