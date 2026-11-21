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
import unittest
import uuid
from unittest import mock
from pathlib import Path
from typing import Any, cast

from script_validation_support import (
    HEALTH_FIXTURE_CATALOG_IDS,
    SLOT_API_KEY_HEADER_TOKEN,
    ScriptValidationMixin,
    claude_route_headers,
    read_guardrail_source,
    server_thread,
)

WSL_BASH_TIMEOUT_SECONDS = 60


def _read_bash_source(path: Path) -> str:
    """Read the immutable projection source on Windows and Linux."""

    repo_root = Path(__file__).parents[1]
    relative = path.relative_to(repo_root).as_posix()
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"HEAD:{relative}"],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot read committed projection source {relative}")
    return completed.stdout.decode("utf-8").replace("\r\n", "\n")


def _extract_bash_function(source: str, name: str, *, start: int = 0) -> str:
    start = source.index(f"{name}() {{", start)
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1] + "\n"
    raise AssertionError(f"unterminated Bash function: {name}")


def _bash_source_and_prefix(source: str, marker: str) -> tuple[str, str]:
    """Return a runnable prefix plus the source minus heredoc test bodies."""
    definitions: list[str] = []
    lines = source.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line == marker:
            end = index
            break
        match = re.fullmatch(r"[a-z_]+\(\) \{", line)
        if match is not None:
            definitions.append(f"source <(sed -n '{start + 1},{index}p' \"$UPDATER\")")
            start = index
            depth = 1
            index += 1
            while index < len(lines) and depth:
                depth += lines[index].count("{") - lines[index].count("}")
                index += 1
            if depth:
                raise AssertionError(f"unterminated Bash function: {line}")
            start = index
    else:
        raise AssertionError(f"marker not found: {marker}")
    definitions.append(f"source <(sed -n '{start + 1},{end}p' \"$UPDATER\")")
    quiet = re.sub(
        r"(?ms)^<<'([A-Z][A-Z0-9_]*)'\n.*?^\\1$",
        lambda match: f"<<'{match.group(1)}'\ntrue\n{match.group(1)}",
        source,
    )
    return "\n".join(definitions), quiet


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
            ({"plugins": {"configs": {}}}, 0),
            (
                {
                    "plugins": {
                        "configs": {
                            "custom": {"enabled": False},
                            "other": {"enabled": False},
                        }
                    }
                },
                0,
            ),
            (
                {
                    "plugins": {
                        "configs": {
                            "custom": {"enabled": True},
                            "other": {"enabled": False},
                        }
                    }
                },
                77,
            ),
            (
                {
                    "plugins": {
                        "enabled": True,
                        "configs": {"custom": {"enabled": False}},
                    }
                },
                77,
            ),
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
            ({"plugins": {"configs": []}}, 77),
            ({"plugins": {"configs": {"custom": "enabled"}}}, 77),
            ({"plugins": {"configs": {"custom": {"enabled": "false"}}}}, 77),
            (None, 77),
        ):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.yaml"
                path.write_text(json.dumps(config), encoding="utf-8")
                with (
                    mock.patch.object(sys, "argv", ["guard", str(path)]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    output = io.StringIO()
                    if expected:
                        with (
                            contextlib.redirect_stdout(output),
                            self.assertRaises(SystemExit) as error,
                        ):
                            exec(compile(program, "plugin-guard", "exec"), {})
                        self.assertEqual(error.exception.code, expected)
                        if config == {
                            "plugins": {
                                "configs": {
                                    "custom": {"enabled": True},
                                    "other": {"enabled": False},
                                }
                            }
                        }:
                            self.assertIn(
                                "enabled_plugin_configs=custom", output.getvalue()
                            )
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

    def test_update_signals_defer_to_the_verified_rollback_boundary(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("trap 'defer_updater_signal 130' INT", source)
        self.assertIn("trap 'defer_updater_signal 143' TERM", source)
        self.assertNotIn("trap 'exit 130' INT", source)
        self.assertNotIn("trap 'exit 143' TERM", source)
        self.assertIn("compose_start_pid=$!", source)
        self.assertIn("MUTATION_STARTED=1", source)
        self.assertIn("pid_alive() {", source)
        helper = _extract_bash_function(source, "stop_compose_start")
        self.assertIn('kill -TERM -- "-$start_pid"', helper)
        self.assertIn("stop_compose_start", source)
        self.assertIn(
            'cp -a "$BK/compose.yml" "$DIR/compose.yml" || restore_failed=1',
            source,
        )
        self.assertIn("restore_verified", source)
        self.assertIn(
            "ROLLBACK_VERIFIED interrupted=${ROLLBACK_INTERRUPTED:-0} restored=$CUR",
            source,
        )
        self.assertIn("trap 'rollback_after_write 143' TERM", source)
        self.assertIn("DOCKER_START_PENDING=1", source)
        self.assertIn('rollback "$start_status"', source)
        self.assertIn("ROLLBACK_INTERRUPTED=1", source)
        self.assertIn("rollback_after_write 143", source)
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        status = _extract_bash_function(source, "on_exit")
        defer = next(
            function
            for function in re.findall(
                r"(?ms)^defer_updater_signal\(\) \{\n.*?^\}\n",
                source,
            )
            if 'UPDATER_SIGNAL="$code"' in function and "rollback" not in function
        )
        harness = "\n".join(
            [
                "set -e",
                "set -u",
                "UPDATER_SIGNAL=0",
                "DOCKER_START_PENDING=0",
                status,
                defer,
                "trap 'defer_updater_signal 143' TERM",
                "trap 'defer_updater_signal 130' INT",
                "defer_updater_signal 143",
                'printf "TRAP_REGISTERED=%s\\n" "$(trap -p TERM)"',
                'printf "TERM_DEFERRED code=%s\\n" "$UPDATER_SIGNAL"',
                "defer_updater_signal 130",
                'printf "TERM_PRESERVED code=%s\\n" "$UPDATER_SIGNAL"',
                'write_status() { printf \'STATUS result=%s code=%s reason=%s\\n\' "$1" "$2" "${3:-}"; }',
                "DOCKER_START_PENDING=0",
                "( exit 0 )",
                'printf "AFTER_CLEAN_SUCCESS\\n"',
                "DOCKER_START_PENDING=1",
                "trap 'status_code=$?; on_exit \"$status_code\"' EXIT",
                "( exit 0 )",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            harness_path = Path(directory) / "start-shape.sh"
            harness_path.write_text(harness + "\n", encoding="utf-8", newline="\n")
            completed = subprocess.run(
                self._bash_command(bash, self._bash_path(bash, harness_path)),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
        output = completed.stdout.decode()
        outcome = output + completed.stderr.decode()
        self.assertEqual(completed.returncode, 32, outcome)
        self.assertIn(
            "TRAP_REGISTERED=trap -- 'defer_updater_signal 143' SIGTERM", output
        )
        self.assertIn("TERM_DEFERRED code=143", output)
        self.assertIn("TERM_PRESERVED code=143", output)
        self.assertIn("AFTER_CLEAN_SUCCESS", output)
        self.assertIn("STATUS result=unverified code=32", output)

    def test_post_write_signal_reports_the_verified_restore_as_deferred(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        restore = _extract_bash_function(source, "restore_verified")
        rollback_after_write = _extract_bash_function(source, "rollback_after_write")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup"
            cpa = root / "cpa"
            log = root / "updater.log"
            backup.mkdir()
            cpa.mkdir()
            backup_host = backup.as_posix()
            cpa_host = cpa.as_posix()
            (backup / "compose.yml").write_text(
                "old-image\n", encoding="utf-8", newline="\n"
            )
            (cpa / "compose.yml").write_text(
                "new-image\n", encoding="utf-8", newline="\n"
            )
            harness = "\n".join(
                [
                    "set -Eeuo pipefail",
                    "DOCKER_START_PENDING=1",
                    "MUTATED=1",
                    "ROLLBACK_INTERRUPTED=0",
                    "ROLLBACK_IMAGE_ID=sha256:old",
                    "ROLLBACK_IMAGE_REF=backup-image",
                    "ROLLBACK_STAGE=prepare",
                    "ROLLBACK_VERIFIED=0",
                    "umask 077",
                    f"BK='{backup_host}'",
                    f"DIR='{cpa_host}'",
                    f"LOG='{log.as_posix()}'",
                    "CUR=v8.0.79",
                    'cp() { [[ "$1" == -a ]] || return 1; printf \'old-image\\n\' >"$DIR/compose.yml"; }',
                    "docker() {",
                    '  case "$*" in',
                    '    "compose up -d --pull never") return 0 ;;',
                    '    "inspect --format {{.Image}} cli-proxy-api") printf "sha256:old\\n" ;;',
                    '    "image inspect --format {{.ID}} backup-image") printf "sha256:old\\n" ;;',
                    "  esac",
                    "}",
                    "write_status() { :; }",
                    "on_exit() {",
                    '  printf "RECEIPT result=deferred code=%s\\n" "$1"',
                    '  exit "$1"',
                    "}",
                    "trap 'status_code=$?; on_exit \"$status_code\"' EXIT",
                    "health() { return 0; }",
                    'log() { printf "%s\\n" "$*"; }',
                    "image() { printf 'sha256:old\\n'; }",
                    "cd() { :; }",
                    "cmp() { return 0; }",
                    restore,
                    rollback_after_write,
                    "rollback_after_write 143",
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 31, output)
            self.assertIn("ROLLBACK_VERIFIED interrupted=1 restored=v8.0.79", output)
            self.assertIn("RECEIPT result=deferred code=31", output)
            self.assertEqual((cpa / "compose.yml").read_text(), "old-image\n")

    def test_interrupted_docker_start_restores_backup_with_bounded_recovery(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        starts = [
            match.start()
            for match in re.finditer(
                r"DOCKER_START_PENDING=1\n.*?^\(\n  cd",
                source,
                re.MULTILINE | re.DOTALL,
            )
        ]
        self.assertEqual(len(starts), 1, "start recovery must have one definition")
        start = starts[0]
        end = source.index("\nRESULT=0\n", start)
        block = source[start:end] + "\n:"
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        if os.name == "nt":
            # Git Bash cannot reproduce the deployed Linux /proc and process
            # group boundary. Run the behavioural fixture in native WSL; the
            # source assertions remain active when WSL is unavailable.
            if shutil.which("wsl.exe") is None or not self._has_native_linux_wsl():
                self.skipTest("POSIX start-recovery fixture requires native WSL")
            bash = "wsl"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup"
            cpa = root / "cpa"
            backup.mkdir()
            cpa.mkdir()
            (backup / "compose.yml").write_text(
                "old-image\n", encoding="utf-8", newline="\n"
            )
            (cpa / "compose.yml").write_text(
                "new-image\n", encoding="utf-8", newline="\n"
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\nexit 0\n", encoding="ascii", newline="\n"
            )
            fake_docker.chmod(0o755)
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    "set -m",
                    f"export PATH='{self._bash_path(bash, fake_bin)}:/usr/bin:/bin'",
                    f"BK='{self._bash_path(bash, backup)}'",
                    f"DIR='{self._bash_path(bash, cpa)}'",
                    f"LOG='{self._bash_path(bash, root / 'updater.log')}'",
                    "CUR='v8.0.79'",
                    "TARGET='v8.0.80'",
                    "UPDATER_SIGNAL=0",
                    "start_status=0",
                    "ROLLBACK_IMAGE_ID='sha256:old'",
                    "MUTATION_STARTED=1",
                    "is_within_tree() { return 0; }",
                    "seq() { printf '1\\n'; }",
                    "sleep() { :; }",
                    "ROLLBACK_IMAGE_REF='cliproxyapi-backup'",
                    "ROLLBACK_STAGE=prepare",
                    "stop_compose_start() { return 0; }",
                    'cp() { if [[ "$1" == -a && "$2" == "$BK/compose.yml" ]]; then printf \'old-image\\n\' >"$DIR/compose.yml"; else command cp "$@"; fi; }',
                    'log() { printf "%s\\n" "$*" | tee -a "$LOG"; }',
                    "kill() {",
                    '  if [[ "$1" == -0 ]]; then return 1; fi',
                    '  if [[ "$*" == "-TERM -- -"* ]]; then return 1; fi',
                    '  command kill "$@"',
                    "}",
                    "docker() {",
                    '  case "$*" in',
                    '    "compose config --quiet") printf "new-image\\n" >"$DIR/compose.yml"; return 0 ;;',
                    '    "compose up -d --pull never") printf "restored\\n" >>"$LOG"; return 0 ;;',
                    "    \"inspect --format {{.Image}} cli-proxy-api\") printf 'sha256:old\\n' ;;",
                    "    \"image inspect --format {{.ID}} cliproxyapi-backup\") printf 'sha256:old\\n' ;;",
                    "  esac",
                    "}",
                    '( command sleep 0.2; command kill -TERM "$$"; sleep 1 ) &',
                    "trap 'UPDATER_SIGNAL=143' TERM",
                    'sleep() { [[ "$UPDATER_SIGNAL" == 0 ]] || return 1; command sleep "$@"; }',
                    'rollback() { printf "INTERRUPTED_ROLLBACK code=%s\\n" "$1"; exit "$1"; }',
                    block,
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 31, output)
            self.assertIn("ROLLBACK_VERIFIED interrupted=start_container", output)
            self.assertEqual((cpa / "compose.yml").read_text(), "old-image\n")
            self.assertIn("restored", (root / "updater.log").read_text())
            self.assertNotIn("UNEXPECTED_SUCCESS", output)

    def test_start_recovery_requires_the_original_running_image(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        start = source.index(
            "DOCKER_START_PENDING=1\n", source.index("MUTATION_STARTED=1\n")
        )
        end = source.index("\nRESULT=0\n", start)
        model = source[start:end]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup"
            cpa = root / "cpa"
            backup.mkdir()
            cpa.mkdir()
            (backup / "compose.yml").write_text(
                "old-image\n", encoding="utf-8", newline="\n"
            )
            (cpa / "compose.yml").write_text(
                "new-image\n", encoding="utf-8", newline="\n"
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\nexit 0\n", encoding="ascii", newline="\n"
            )
            fake_docker.chmod(0o755)
            harness = "\n".join(
                [
                    "set -u",
                    f"export PATH='{self._bash_path(bash, fake_bin)}:/usr/bin:/bin'",
                    f"BK='{self._bash_path(bash, backup)}'",
                    f"DIR='{self._bash_path(bash, cpa)}'",
                    f"LOG='{self._bash_path(bash, root / 'updater.log')}'",
                    "CUR=v8.0.79",
                    "TARGET=v8.0.80",
                    "UPDATER_SIGNAL=0",
                    "start_status=0",
                    "MUTATION_STARTED=1",
                    "is_within_tree() { return 0; }",
                    "ROLLBACK_IMAGE_REF=old",
                    "ROLLBACK_IMAGE_ID=sha256:old",
                    "ROLLBACK_STAGE=prepare",
                    "readlink() { printf '%s' \"$DIR\"; }",
                    "ps() { return 1; }",
                    "wait() { UPDATER_SIGNAL=143; return 0; }",
                    "log() { printf '%s\\n' \"$*\"; }",
                    "cp() { printf 'old-image\\n' >\"$DIR/compose.yml\"; }",
                    "docker() {",
                    '  case "$*" in',
                    '    "inspect --format {{.Image}} cli-proxy-api") printf "sha256:old\\n" ;;',
                    '    "image inspect --format {{.ID}} old") printf "sha256:old\\n" ;;',
                    "  esac",
                    "}",
                    'rollback() { printf "NORMAL_ROLLBACK rc=%s\\n" "$1"; exit "$1"; }',
                    "stop_compose_start() { return 0; }",
                    model,
                    'printf "UNEXPECTED_SUCCESS\\n"',
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 31, output)
            self.assertIn("ROLLBACK_VERIFIED interrupted=start_container", output)

    def test_deferred_signal_has_a_post_start_transaction_boundary(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        start = source.index(
            "DOCKER_START_PENDING=1\n", source.index("MUTATION_STARTED=1\n")
        )
        end = source.index("\nRESULT=0\n", start)
        block = source[start:end]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "updater.log"
            backup = root / "test-backup"
            cpa = root / "test-cpa"
            backup.mkdir()
            cpa.mkdir()
            (backup / "compose.yml").write_text("old-image\n", encoding="utf-8")
            harness = (
                "set -u\n"
                'sleep() { command sleep "$@"; }\n'
                'kill() { if [[ "$1" == -0 ]]; then return 1; fi; command kill "$@"; }\n'
                "UPDATER_SIGNAL=0\n"
                "DOCKER_START_PENDING=0\n"
                f"LOG='{self._bash_path(bash, log)}'\n"
                "trap 'UPDATER_SIGNAL=143' TERM\n"
                "signal_pid=$$\n"
                '( sleep 0.01; command kill -TERM "$signal_pid" ) &\n'
                'rollback() { printf "DEFERRED_ROLLBACK code=%s\\n" "$1"; exit "$1"; }\n'
                "stop_compose_start() { UPDATER_SIGNAL=143; return 0; }\n"
                f"BK='{self._bash_path(bash, backup)}'\n"
                f"DIR='{self._bash_path(bash, cpa)}'\n"
                "CUR=v8.0.79\n"
                "TARGET=v8.0.80\n"
                "ROLLBACK_IMAGE_REF=test-image\n"
                "ROLLBACK_IMAGE_ID=sha256:test-old\n"
                "restore_wait=30\n"
                "restore_pid=999999\n"
                'log() { printf "%s\\n" "$*"; }\n'
                "cp() { printf 'old-image\\n' >\"$DIR/compose.yml\"; }\n"
                "docker() { return 0; }\n"
                'rollback() { printf "DEFERRED_ROLLBACK code=%s\\n" "$1"; exit "$1"; }\n'
                + block
                + '\nprintf "UNEXPECTED_SUCCESS\\n"\n'
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertIn(completed.returncode, (31, 32), output)
            self.assertIn("ROLLBACK_FAILED interrupted=start_container", output)
            self.assertNotIn("UNEXPECTED_SUCCESS", output)

    def test_compose_start_wait_parses_linux_proc_state_and_waits_for_exit(
        self,
    ) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        start = source.index("DOCKER_START_PENDING=1\n")
        end = source.index('\nif [[ "$UPDATER_SIGNAL" != 0 ]]; then', start)
        block = source[start:end]
        self.assertIn("start_status=0", block)
        self.assertIn(
            'read -r start_state _ start_pgid _ <<< "${proc_state##*) }"',
            block,
        )
        self.assertIn('wait "$compose_start_pid" || start_status=$?', block)
        self.assertNotIn("set +e", block)
        self.assertNotIn("read -r _ start_pid start_state", block)
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        if os.name == "nt":
            if shutil.which("wsl.exe") is None or not self._has_native_linux_wsl():
                self.skipTest("Linux /proc fixture requires native WSL")
            bash = "wsl"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "compose-helper.sh"
            helper.write_text(
                "sleep 0.1\nprintf 'completed\\n' > \"$1\"\nexit 7\n",
                encoding="utf-8",
                newline="\n",
            )
            result = root / "result"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\nexec bash "$HELPER" "$RESULT"\n',
                encoding="ascii",
                newline="\n",
            )
            fake_docker.chmod(0o755)
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    "set -m",
                    f"export PATH='{self._bash_path(bash, fake_bin)}:/usr/bin:/bin'",
                    f"HELPER='{self._bash_path(bash, helper)}'",
                    f"RESULT='{self._bash_path(bash, result)}'",
                    "export HELPER RESULT",
                    "UPDATER_SIGNAL=0",
                    f"LOG='{self._bash_path(bash, root / 'updater.log')}'",
                    '(\n  exec /usr/bin/bash "$HELPER" "$RESULT"\n) &',
                    "compose_start_pid=$!",
                    'start_pid="$compose_start_pid"',
                    "start_pgid=''",
                    "start_state=''",
                    "start_status=0",
                    f"DIR='{self._bash_path(bash, root)}'",
                    "docker() { return 0; }",
                    'ls -l "/proc/$compose_start_pid/stat" >/dev/null',
                    block,
                    'printf "STATUS %s PID %s\\n" "$start_status" "$start_pid"',
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            output = completed.stdout.decode() + completed.stderr.decode()
            self.assertEqual(completed.returncode, 0, output)
            self.assertIn("STATUS 7", output)
            self.assertEqual(result.read_text(), "completed\n")

    def test_process_identity_arming_accepts_precreated_group_leader(
        self,
    ) -> None:
        source = _read_bash_source(
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        )
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        if os.name == "nt":
            if shutil.which("wsl.exe") is None or not self._has_native_linux_wsl():
                self.skipTest("Linux /proc fixture requires native WSL")
            bash = "wsl"
        helper = _extract_bash_function(source, "stop_compose_start")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "test-backup"
            cpa = root / "test-cpa"
            backup.mkdir()
            cpa.mkdir()
            harness = "\n".join(
                [
                    "set -u",
                    "python3() { return 0; }",
                    "is_within_tree() { return 0; }",
                    helper,
                    f"DIR='{self._bash_path(bash, cpa)}'",
                    f"BK='{self._bash_path(bash, backup)}'",
                    "compose_start_pid=$$",
                    'ps() { printf "$compose_start_pid\\n"; }',
                    "pid_alive() { return 1; }",
                    "readlink() { printf '%s\\n' \"$DIR\"; }",
                    "kill() {",
                    '  if [[ "$1" == -TERM && "$2" == -- && "$3" == "-$$" ]]; then',
                    '    printf "GROUP_TERM %s\\n" "$3"; return 0;',
                    "  fi",
                    '  printf "UNEXPECTED_KILL %s %s\\n" "$1" "$2"; return 1',
                    "}",
                    "stop_compose_start || exit $?",
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash, "-c", harness),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            output = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 0, output)
            self.assertIn("GROUP_TERM", output)
            self.assertNotIn("UNEXPECTED_KILL", output)

    def test_start_recovery_rejects_backup_outside_cpa_directory(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        start = source.index("is_within_tree() {")
        end = source.index("\ndefer_updater_signal() {", start)
        definitions = source[start:end]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        harness = "\n".join(
            [
                "set -u",
                definitions,
                f"DIR='{(Path(__file__).parents[1]).as_posix()}'",
                f"BK='{(Path(__file__).parents[2] / 'cpa-backup-outside').as_posix()}'",
                "python3() { return 1; }",
                "compose_start_pid=999999",
                f"start_pid='{os.getpid()}'",
                "start_state=''",
                "readlink() { printf 'C:/fixture/cpa\\n'; }",
                "stop_compose_start || exit $?",
                'printf "UNEXPECTED_CONTINUATION\\n"',
            ]
        )
        completed = subprocess.run(
            self._bash_command(bash, "-c", harness),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("REFUSE backup is outside the CPA directory", output)

    def test_docker_start_group_is_inspected_before_negative_group_signal(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        helper = _extract_bash_function(source, "stop_compose_start")
        self.assertLess(
            helper.index('start_cwd=$(readlink -f -- "/proc/$start_pid/cwd"'),
            helper.index('kill -TERM -- "-$start_pid"'),
        )
        self.assertLess(
            helper.index("start_group=${start_pgid:-"),
            helper.index('kill -TERM -- "-$start_pid"'),
        )
        self.assertIn("set -m", source)
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "fixture" / "cpa" / "backups" / "run"
            backup.mkdir(parents=True)
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    'PID_FIXTURE="${PID_FIXTURE:?native pid required}"',
                    f"DIR='{self._bash_path(bash, backup.parents[2])}'",
                    f"BK='{self._bash_path(bash, backup)}'",
                    "compose_start_pid=$PID_FIXTURE",
                    "start_pid=$PID_FIXTURE",
                    "start_pgid=''",
                    "start_state='S'",
                    'readlink() { printf "%s" "$DIR"; }',
                    'tr() { if [[ "$1" == -d ]]; then command tr "$@"; else printf \'compose up -d --pull never\'; fi; }',
                    "kill() {",
                    '  [[ "$*" == "-TERM -- -$PID_FIXTURE" || '
                    '"$*" == "-TERM $PID_FIXTURE" ]] || {',
                    "    printf 'UNEXPECTED_KILL %s\\n' \"$*\"; return 1",
                    "  }",
                    "  printf 'GROUP_TERM %s\\n' \"$PID_FIXTURE\"",
                    "}",
                    "ps() { printf '%s\\n' \"$START_PGID\"; }",
                    "pid_alive() {",
                    '  [[ "$1" == "$PID_FIXTURE" ]] || { printf \'UNEXPECTED_PROBE %s\\n\' "$1"; return 1; }',
                    '  if [[ ! -f "$PROBES" ]]; then',
                    "    printf 'probe\\n' >>\"$PROBES\"; printf 'PROBE %s\\n' \"$1\"; return 0",
                    "  fi",
                    "  return 1",
                    "}",
                    "wait() { printf 'WAIT_BEFORE_SIGNAL %s\\n' \"$1\"; }",
                    f"PROBES='{self._bash_path(bash, root / 'probe-count')}'",
                    'tr() { if [[ "$1" == -d ]]; then command tr "$@"; else printf \'compose up -d --pull never\'; fi; }',
                    "is_within_tree() { return 0; }",
                    helper,
                    '[[ -e "/proc/$PID_FIXTURE" ]] || exit 90',
                    "stop_compose_start; stop_status=$?; "
                    'printf "STOP_STATUS=%s\\n" "$stop_status"; '
                    '[[ "$stop_status" == 0 ]] || exit 91',
                ]
            )
            harness_path = root / "stop-start.sh"
            if os.name == "nt":
                # Git Bash has no Linux /proc. WSL cannot read a Windows temp
                # path, so stage a disposable script and retain a real PID.
                staged = f"/tmp/cpa-process-fixture-{uuid.uuid4().hex}.sh"
                stdin = "\n".join(
                    [
                        "set -euo pipefail",
                        "set -m",
                        f"cat >'{staged}' <<'FIXTURE_EOF'",
                        harness,
                        "FIXTURE_EOF",
                        f"bash -n '{staged}'",
                        'trap \'command kill "$PID_FIXTURE" 2>/dev/null || true; '
                        f'rm -f -- "{staged}"\' EXIT',
                        "command sleep 60 & PID_FIXTURE=$!",
                        "START_PGID=$PID_FIXTURE",
                        "export PID_FIXTURE START_PGID",
                        f"source '{staged}'",
                    ]
                )
            else:
                native = subprocess.Popen(
                    ["sleep", "60"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                os.environ["PID_FIXTURE"] = str(native.pid)
                os.environ["START_PGID"] = str(native.pid)
                harness_path.write_text(harness + "\n", encoding="utf-8", newline="\n")
            completed = subprocess.run(
                (
                    self._bash_command(bash)
                    if os.name == "nt"
                    else self._bash_command(bash, self._bash_path(bash, harness_path))
                ),
                input=(stdin.encode() if os.name == "nt" else None),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                check=False,
            )
            if os.name != "nt":
                native.terminate()
                native.wait(timeout=10)
        output = completed.stdout.decode() + completed.stderr.decode()
        self.assertNotIn("unexpected EOF", output)
        self.assertEqual(completed.returncode, 0, output)
        helper_start = helper.index("stop_compose_start() {")
        self.assertLess(
            helper.index('wait "$start_pid"', helper_start),
            helper.index(
                'pid_alive "$start_pid" || return 0',
                helper.index('wait "$start_pid"', helper_start),
            ),
        )
        self.assertIn("GROUP_TERM", output)

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
        server_thread(server).start()
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
        checker = (root / "scripts/remote/cpa-updater-schedule-check.sh").read_text(
            encoding="utf-8"
        )
        doctor = (root / "scripts/remote/cpa-guardrail-doctor.sh").read_text(
            encoding="utf-8"
        )
        declaration = re.search(
            r"^AUTO_UPDATE_BUDGETS='(?P<value>[^']+)'\r?$",
            updater,
            re.MULTILINE,
        )
        self.assertIsNotNone(declaration)
        assert declaration is not None
        budgets = {
            key: int(value)
            for key, value in (
                pair.split("=", 1) for pair in declaration.group("value").split()
            )
        }
        self.assertEqual(
            set(budgets),
            {
                "pull_seconds",
                "compose_seconds",
                "start_stop_seconds",
                "restore_seconds",
                "docker_kill_after_seconds",
                "health_request_seconds",
            },
        )
        # A single call must not consume the outer fail-stop, and the cold path
        # (one pull, two Compose clients, recovery and one health bound) must
        # still leave time to roll back instead of being cut off midway.
        start_match = re.search(r"TimeoutStartSec=(\d+)min", conf)
        self.assertIsNotNone(start_match)
        assert start_match is not None
        dropin_start = int(start_match.group(1))
        self.assertGreater(min(budgets.values()), 0)
        self.assertLess(max(budgets.values()), dropin_start * 60)
        cold_path = (
            budgets["pull_seconds"]
            + 2 * budgets["compose_seconds"]
            + budgets["start_stop_seconds"]
            + budgets["restore_seconds"]
            + budgets["docker_kill_after_seconds"]
            + budgets["health_request_seconds"]
        )
        self.assertLessEqual(cold_path, dropin_start * 60 - 600)
        stop_match = re.search(r"TimeoutStopSec=(\d+)min", conf)
        self.assertIsNotNone(stop_match)
        assert stop_match is not None
        stop_min = int(stop_match.group(1))
        self.assertIn("TimeoutStopSec=4min", conf)
        self.assertIn("trap 'defer_updater_signal 143' TERM", updater)
        # The checker must preserve one-call and cold-path safety against the
        # exact outer fail-stop.
        self.assertIn("__CPA_UPDATER_BUDGETS__", checker)
        self.assertIn("budget_declaration_exact", checker)
        self.assertIn('"$start_seconds" -eq "$expected_start_seconds"', checker)
        self.assertIn('"$largest_budget" -lt "$start_seconds"', checker)
        self.assertIn("full_path_budget", checker)
        self.assertIn("2 * compose_budget", checker)
        self.assertIn("recovery_reserve=600", checker)
        self.assertIn(f"-lt {stop_min * 60}", doctor)
        self.assertIn("systemd_timeout_seconds", doctor)

    def test_updater_schedule_budget_uses_the_slot_and_effective_timeout(self) -> None:
        source = _read_bash_source(
            Path(__file__).parents[1] / "scripts/remote/cpa-updater-schedule-check.sh"
        )
        updater = _read_bash_source(
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        )
        dropin = (
            Path(__file__).parents[1] / "scripts/remote/cpa-update-timeout.conf"
        ).read_text(encoding="utf-8")
        dropin_start = re.search(r"TimeoutStartSec=(\d+)min", dropin)
        self.assertIsNotNone(dropin_start)
        assert dropin_start is not None
        dropin_start_seconds = int(dropin_start.group(1)) * 60
        budget_declaration = re.search(
            r"^AUTO_UPDATE_BUDGETS='(?P<value>[^']+)'\r?$",
            updater,
            re.MULTILINE,
        )
        self.assertIsNotNone(budget_declaration)
        assert budget_declaration is not None
        budget_literal = budget_declaration.group("value")
        declared_budgets = [
            int(value)
            for _, value in (pair.split("=", 1) for pair in budget_literal.split())
        ]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        normalize = (
            "\n".join(
                [
                    "MODE=verify",
                    "fail=0",
                    "flag() {",
                    '  if [ "$1" = OK ]; then printf "%s=OK\\n" "$2";',
                    '  else printf "%s=FAIL reason=%s\\n" "$2" "$1"; fi; }',
                ]
            )
            + "\n"
        )
        normalize = normalize.replace("\r\n", "\n")
        normalize += _extract_bash_function(source, "normalize_seconds")
        normalize += _extract_bash_function(source, "budget_seconds")
        declaration = source[
            source.index("budget_declaration_exact() {") : source.index(
                "\nbudgets_every() {"
            )
        ]
        declaration = declaration.replace(
            "  for token in ${AUTO_UPDATE_BUDGETS:-}; do\n",
            "  for token in $AUTO_UPDATE_BUDGETS; do\n",
            1,
        )
        normalize += declaration
        normalize += _extract_bash_function(source, "budgets_every")
        start = source.index("pull_budget=")
        budget = source[start : source.index("\n# Readiness precondition", start)]
        budget = budget.replace("\r\n", "\n")
        outputs = []
        for prop, default, effective, expected, budget_status in (
            ("2400s", "2400s", "2400s", 2400, "OK"),
            ("2400s", "90s", "90s", "UNVERIFIED", "UNVERIFIED"),
            # An empty Effective from an older systemd does not override the
            # explicit service property; that property remains authoritative.
            ("2400s", "90s", "", 2400, "OK"),
            # When Effective agrees with the explicit ceiling, the per-unit
            # value is the allocated timeout. An inconsistent pair is rejected
            # before reaching this table.
            ("2400s", "infinity", "2400s", 2400, "OK"),
            ("2400s", "infinity", "40min", 2400, "OK"),
            # An explicit property without Effective remains a visible,
            # non-passing fallback; it does not inherit the manager default.
            ("2400s", "infinity", "", 2400, "OK"),
            ("1200s", "infinity", "", 1200, "full_path_budget=1170s"),
            ("infinity", "infinity", "", "UNVERIFIED", "UNVERIFIED"),
            ("30min", "infinity", "", 1800, "timeout_start=1800s"),
        ):
            with self.subTest(prop=prop, default=default, effective=effective):
                harness = "\n".join(
                    [
                        normalize,
                        "systemctl() {",
                        '  local args="$*"',
                        '  case "$args" in',
                        '    *EffectiveTimeoutStartUSec*) printf "%s\\n" "$EFFECTIVE" ;;',
                        '    *DefaultTimeoutStartUSec*) printf "%s\\n" "$DEFAULT" ;;',
                        '    *TimeoutStartUSec*) printf "%s\\n" "$PROP" ;;',
                        "  esac",
                        "}",
                        f"AUTO_UPDATE_BUDGETS='{budget_literal}'",
                        budget,
                    ]
                )
                harness += '\nexit "$fail"\n'
                # Extracted production lines were read as UTF-8, whose default
                # universal-newline conversion leaves LF. Normalising again is
                # harmless even if a Windows writer introduces CRLF.
                harness = harness.replace("\r\n", "\n")
                result = subprocess.run(
                    self._bash_command(bash, "-c", harness),
                    env={
                        **os.environ,
                        "PROP": prop,
                        "DEFAULT": default,
                        "EFFECTIVE": effective,
                    },
                    input=harness.encode(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = result.stdout.decode() + result.stderr.decode()
                outputs.append(output)
                self.assertEqual(result.returncode, 0, output)
                if expected == "UNVERIFIED":
                    self.assertIn("updater-schedule-timeout-start=UNVERIFIED", output)
                    self.assertIn(
                        "updater-schedule-budget=FAIL reason=timeout_start_unreadable",
                        output,
                    )
                    continue
                self.assertIn(f"updater-schedule-timeout-start={expected}s", output)
                self.assertIn(f"updater-schedule-timeout-raw={prop}/{default}", output)
                if budget_status == "OK":
                    self.assertIn("updater-schedule-budget=OK", output)
                elif budget_status == "FAIL_BEFORE_BUDGET_OPTIONAL":
                    self.assertIn("updater-schedule-budget=", output)
                else:
                    self.assertIn(
                        f"updater-schedule-budget=FAIL reason={budget_status}",
                        output,
                    )
        self.assertLess(max(declared_budgets), dropin_start_seconds)
        self.assertIn(
            f"updater-schedule-budget-largest={max(declared_budgets)}s",
            "\n".join(outputs),
        )
        self.assertIn("updater-schedule-budget-declared=exact", "\n".join(outputs))
        self.assertIn(
            '[[ "${AUTO_UPDATE_BUDGETS_PRINT:-0}" != 1 ]]',
            updater,
        )
        self.assertLess(
            updater.index("AUTO_UPDATE_BUDGETS_PRINT:-0"),
            updater.index("DIR=/opt/cliproxyapi"),
        )

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
            "workbuddy_developer_role_override": {
                "from_protocol": "responses",
                "models": ["ds-test"],
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
                    {
                        "models": [{"name": "ds-test", "from-protocol": "responses"}],
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
                "replace": [],
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
        source = _read_bash_source(repo_root / "scripts/remote/cpa-auto-update.sh")
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
            "workbuddy-intl-risk.json",
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
                "workbuddy-intl-risk.json": '{"version":1,"not_before":0,"hold_reason":null}',
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

    def test_check_mode_status_split_runs_after_mode_is_assigned(self) -> None:
        # The script starts with 'set -Eeuo pipefail', so referencing $MODE
        # before MODE=... aborts every invocation with "MODE: unbound
        # variable". The --check status split was once placed beside the EXIT
        # trap, i.e. ~130 lines above the assignment, which broke --apply,
        # --check and --confirm-pending alike.
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        self.assertLess(
            source.index("MODE=${1:---apply}"),
            source.index('if [[ "$MODE" == --check ]]; then'),
            "the --check status split must follow the MODE assignment",
        )
        # The trap must still be armed before the first status write.
        self.assertLess(
            source.index("trap 'status_code=$?; on_exit \"$status_code\"' EXIT"),
            source.index("write_status running 0"),
        )
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.sh"
            probe.write_text(
                "set -Eeuo pipefail\n"
                'STATUS_FILE="/tmp/x.status"\n'
                + source[
                    source.index("MODE=${1:---apply}") : source.index("MIN_FREE_KIB=")
                ]
                + 'echo "STATUS_FILE=$STATUS_FILE"\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [bash, str(probe), "--check"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("STATUS_FILE=/tmp/x-check.status", completed.stdout)

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
            windows_base = base
            native_linux = os.name != "nt"
            for name in (
                "chatgpt-risk.json",
                "workbuddy-risk.json",
                "workbuddy-intl-risk.json",
                "antigravity-risk.json",
            ):
                (windows_base / name).write_text(
                    '{"version":1,"not_before":0,"hold_reason":null}'
                )

            def run(pre_holds: str) -> subprocess.CompletedProcess[str]:
                # Point the extracted prelude's env-overridable state dir at
                # the fixture before its production-literal export line runs,
                # and shim python3 to this interpreter (Windows Store python3
                # stubs resolve but never run the payload).
                python_shim = ""
                if os.name == "nt" and not native_linux:
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
                    + "UPDATER_SIGNAL=0\n"
                    + 'rollback() { printf \'ROLLBACK_SIGNAL=%s\\n\' "$1"; exit "$1"; }\n'
                    + _extract_bash_function(
                        source,
                        "persist_pending_or_rollback",
                    )
                    + "TARGET=TEST BK=TESTBK\n"
                    + f"PRE_HOLDS='{pre_holds}'\n"
                    + block
                )
                if native_linux and os.name == "nt":
                    # A script file avoids launcher quoting changes and lets
                    # the native shell report the production exit code.
                    harness_path = windows_base / "hold-delta.sh"
                    harness_path.write_text(harness, encoding="utf-8")
                    command = self._bash_command(bash, harness_path.as_posix())
                    return subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
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

            (windows_base / "chatgpt-risk.json").write_text(
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

            (windows_base / "workbuddy-risk.json").write_text('{"version":1,')
            broken = run("")
            broken_output = broken.stdout + broken.stderr
            if native_linux:
                self.assertEqual(broken.returncode, 10, broken_output)
            # Git Bash can lose an explicit exit status from a function's
            # process substitution and return 0. Its stderr marker remains
            # required, and the outer script must never issue success output.
            self.assertNotIn("OK: updated", broken.stdout)
            self.assertIn("durable state unreadable after update", broken.stdout)
            self.assertIn("PENDING_WRITTEN", broken.stdout)
            self.assertIn("HOLD_STATE_UNREADABLE", broken_output)

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
                    completed.returncode,
                    expected_code,
                    completed.stderr.decode() + " " + output,
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
        branch_start = source.index("\nRESULT=0\n", source.index("UPDATER_SIGNAL"))
        post_signal = source.index('[[ "$UPDATER_SIGNAL" == 0 ]]', branch_start)
        start = post_signal
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
                        "CUR=v7.3.7; TARGET=v7.3.8; BK=/tmp/cpa-test-backup; LOG=/tmp/cpa-test.log",
                        "DIGEST='sha256:test'; UPDATER_SIGNAL=0; MUTATED=1",
                        "PRE_UPDATE_HOLDS=''",
                        'lane_hold_state() { printf ""; }',
                        'health() { if [[ "$1" == generation ]]; then return 10; fi; return '
                        f"{readiness}; }}",
                        'log() { printf "%s\\n" "$*"; }',
                        'rollback() { printf "ROLLBACK_CALLED result=%s\\n" "$1"; exit "$1"; }',
                        'write_pending_verification() { printf "PENDING_WRITTEN\\n"; }',
                        _extract_bash_function(
                            source,
                            "persist_pending_or_rollback",
                        ),
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
                    completed.returncode,
                    expected_code,
                    completed.stderr.decode() + " " + output,
                )
                self.assertIn(marker, output)
                if readiness == 10:
                    self.assertIn("PENDING_WRITTEN", output)

    def test_pending_write_failure_exits_unverified_not_success(self) -> None:
        source = _read_bash_source(
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        )
        mode = source.index('if [[ "$MODE" == --confirm-pending ]]')
        apply_branch = source.index('if [[ "$MODE" != --apply ]]', mode)
        start = source.index("RESULT=0\n", apply_branch)
        start = source.index("health generation", start)
        end = source.index('\n[[ "$RESULT" == 0 ]]', start)
        branch = source[start:end]
        helper_start = source.index("persist_pending_or_rollback() {")
        helper_end = source.index("\n}\n", helper_start) + 3
        helper = source[helper_start:helper_end]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        harness = "\n".join(
            [
                "set -u",
                "CUR=v7.3.7; TARGET=v7.3.8; BK=/tmp/cpa-test-backup; DIGEST='sha256:test'",
                "MUTATED=1",
                "PRE_UPDATE_HOLDS=''",
                'lane_hold_state() { printf ""; }',
                'health() { printf "HEALTH %s\\n" "$1"; [[ "$1" == generation ]] && return 10; return 0; }',
                'write_pending_verification() { printf "PENDING_WRITE_FAILED\\n"; return 1; }',
                'log() { printf "LOG %s\\n" "$*"; }',
                "rollback() { printf 'ROLLBACK_CALLED code=%s\\n' \"$1\"; exit 1; }",
                helper,
                branch,
                'printf "UNEXPECTED_SUCCESS\\n"',
            ]
        )
        completed = subprocess.run(
            self._bash_command(bash),
            input=harness.encode(),
            capture_output=True,
            timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            check=False,
        )
        output = completed.stdout.decode() + completed.stderr.decode()
        self.assertEqual(completed.returncode, 1, output)
        self.assertIn("PENDING_WRITE_FAILED", output)
        self.assertIn("ROLLBACK_CALLED code=1", output)
        self.assertNotIn("readiness=HEALTH_OK", output)
        self.assertNotIn("UNEXPECTED_SUCCESS", output)

    def test_pending_write_failure_reverts_candidate_or_fails_closed(self) -> None:
        source = _read_bash_source(
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        )
        start = source.index("persist_pending_or_rollback() {")
        end = source.index("\n}\n", start) + 3
        helper = source[start:end]
        mode = source.index('if [[ "$MODE" == --confirm-pending ]]')
        apply_branch = source.index('if [[ "$MODE" != --apply ]]', mode)
        branch_start = source.index("RESULT=0\n", apply_branch)
        branch_start = source.index("health generation", branch_start)
        branch_end = source.index('\n[[ "$RESULT" == 0 ]]', branch_start)
        branch = source[branch_start:branch_end]
        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")
        for rollback_succeeds, expected_code, expected_marker in (
            (True, 1, "ROLLBACK_VERIFIED"),
            (False, 9, "ROLLBACK_FAILED"),
        ):
            with self.subTest(rollback_succeeds=rollback_succeeds):
                harness = "\n".join(
                    [
                        "set -u",
                        "CUR=v8.0.79; TARGET=v8.0.80; DIGEST='sha256:test'; BK=/tmp/cpa-test-backup; MUTATED=1",
                        "PRE_UPDATE_HOLDS=''; ROLLBACK_STAGE=prepare",
                        'lane_hold_state() { printf ""; }',
                        'health() { printf "HEALTH %s\\n" "$1"; [[ "$1" == generation ]] && return 10; return 0; }',
                        'write_pending_verification() { printf "PENDING_WRITE_FAILED\\n"; return 1; }',
                        'log() { printf "%s\\n" "$*"; }',
                        "rollback() {",
                        '  printf "ROLLBACK_CALLED code=%s\\n" "$1"',
                        (
                            '  printf "ROLLBACK_VERIFIED pending receipt failed\\n"; '
                            "exit 1"
                            if rollback_succeeds
                            else "  return 0"
                        ),
                        "}",
                        helper,
                        branch,
                        'printf "UNEXPECTED_SUCCESS\\n"',
                    ]
                )
                completed = subprocess.run(
                    self._bash_command(bash),
                    input=harness.encode(),
                    capture_output=True,
                    timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                output = completed.stdout.decode() + completed.stderr.decode()
                self.assertEqual(completed.returncode, expected_code, output)
                self.assertIn("PENDING_WRITE_FAILED", output)
                self.assertIn("ROLLBACK_CALLED code=1", output)
                self.assertIn(expected_marker, output)
                if rollback_succeeds:
                    self.assertNotIn("manual_recovery_required=true", output)
                else:
                    self.assertIn("manual_recovery_required=true", output)
                self.assertNotIn("UNEXPECTED_SUCCESS", output)
        on_exit = _extract_bash_function(source, "on_exit")
        completed = subprocess.run(
            self._bash_command(bash),
            input=(
                "set -u\n"
                + on_exit
                + '\nwrite_status() { printf \'STATUS result=%s code=%s reason=%s\\n\' "$1" "$2" "${3:-}"; }\n'
                + "DOCKER_START_PENDING=0\n"
                + "on_exit 9\n"
            ).encode(),
            capture_output=True,
            timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            check=False,
        )
        output = completed.stdout.decode() + completed.stderr.decode()
        self.assertEqual(completed.returncode, 9, output)
        self.assertIn("STATUS result=unverified code=9", output)

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
        alternate_digest = "sha256:" + "b" * 64
        for pending_digest, compose_digest in (
            (expected_digest, expected_digest),
            (alternate_digest, alternate_digest),
            (expected_digest, alternate_digest),
        ):
            with tempfile.TemporaryDirectory() as directory:
                pending = Path(directory) / "cpa-update.pending"
                pending.write_text(
                    f"version=v8.0.16\ndigest={pending_digest}\nhold_lanes=unknown\n",
                    encoding="utf-8",
                )
                harness = "\n".join(
                    [
                        "set -u",
                        "MODE=--confirm-pending; CUR=v8.0.16",
                        f"PENDING_FILE='{self._bash_path(bash, pending)}'",
                        "CURRENT_HOLDS=''",
                        "HOLD_STATE_RESULT=0",
                        'lane_hold_state() { printf "%s" "$CURRENT_HOLDS"; return "$HOLD_STATE_RESULT"; }',
                        'health() { printf "HEALTH_CALL %s\\n" "$1"; return 0; }',
                        "compose_official_image_digest() {",
                        '  printf "%s\\n" "$COMPOSE_DIGEST"',
                        "}",
                        f"COMPOSE_DIGEST='{compose_digest}'",
                        'log() { printf "%s\\n" "$*"; }',
                        "docker() {",
                        '  if [[ "$*" == *".Image"* ]]; then printf "sha256:running\\n";',
                        '  elif [[ "$*" == *"RepoDigests"* ]]; then printf "eceasy/cli-proxy-api@%s\\n" "$PENDING_DIGEST";',
                        "  fi; }",
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
                if pending_digest != compose_digest:
                    self.assertEqual(completed.returncode, 10, output)
                    self.assertIn("compose image digest does not match", output)
                    self.assertTrue(pending.exists())
                else:
                    self.assertEqual(completed.returncode, 0, output)
                    self.assertIn("PENDING_VERIFIED version=v8.0.16", output)
                    self.assertIn("HEALTH_CALL generation", output)
                    self.assertFalse(pending.exists())
                if pending_digest != compose_digest:
                    self.assertNotIn("HEALTH_CALL", output)
        pending_writer = source.split("write_pending_verification() {", 1)[1].split(
            "\n}\n", 1
        )[0]
        self.assertLess(
            pending_writer.index('chmod 600 "$tmp"'),
            pending_writer.index("printf 'version=%s"),
        )
        self.assertIn(
            'chmod 600 "$tmp" || { rm -f -- "$tmp"; return 1; }', pending_writer
        )
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
                if pending_lanes not in ("__missing__", "__compose-mismatch__"):
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
                        "compose_official_image_digest() {",
                        '  printf "%s\\n" "$COMPOSE_DIGEST"',
                        "}",
                        "COMPOSE_DIGEST="
                        + (
                            "sha256:" + "b" * 64
                            if pending_lanes == "__compose-mismatch__"
                            else expected_digest
                        ),
                        'log() { printf "%s\\n" "$*"; }',
                        f'docker() {{ if [[ "$*" == *".Image"* ]]; then printf "sha256:running\\n"; elif [[ "$*" == *"RepoDigests"* ]]; then printf "eceasy/cli-proxy-api@{runtime_digest}\\n"; fi; }}',
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
                self.assertEqual(
                    completed.returncode,
                    expected_code,
                    completed.stderr.decode() + " " + output,
                )
                self.assertIn(marker, output)
                if expected_code == 0:
                    self.assertIn("PENDING_VERIFIED version=v8.0.16", output)
                    self.assertIn("HEALTH_CALL generation", output)
                    self.assertFalse(pending.exists())
                else:
                    self.assertNotIn("HEALTH_CALL", output)
                    self.assertTrue(pending.exists())

    def test_pending_acceptance_binds_the_compose_digest(self) -> None:
        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text(encoding="utf-8")
        parser = (
            source.split("compose_official_image_digest() {", 1)[1]
            .split("<<'PY'\n", 1)[1]
            .split("\nPY\n", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            for image, expected in (
                (
                    "eceasy/cli-proxy-api:v8.0.16@sha256:" + "a" * 64,
                    "sha256:" + "a" * 64,
                ),
                ("cli-proxy-api-fork:v8.0.16-gemini-raw", "REFUSE"),
                ("eceasy/cli-proxy-api:latest", "REFUSE"),
            ):
                with self.subTest(image=image):
                    compose.write_text(
                        f"services:\n  cli-proxy-api:\n    image: {image} # current\n",
                        encoding="utf-8",
                    )
                    result = subprocess.run(
                        [sys.executable, "-c", parser, str(compose)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    if expected == "REFUSE":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("REFUSE", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.strip(), expected)

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
        self.assertEqual(transformed["oauth"]["auth-auto-refresh-workers"], 1)
        self.assertEqual(transformed["requests"]["streaming"]["keepalive-seconds"], 15)
        self.assertIs(
            transformed["requests"]["streaming"]["send-keepalive-before-ft"], True
        )
        self.assertEqual(
            transformed["oauth"]["providers"]["antigravity"]["connection-pool"],
            {
                "enabled": True,
                "idle-conn-timeout": "30s",
                "max-idle-conns-per-host": 2,
            },
        )
        self.assertEqual(
            transformed["oauth"]["request-scoped-errors"],
            policy["EXPECTED_OAUTH_REQUEST_SCOPED_ERRORS"],
        )
        # Slot 2 (opencode) retired 2026-11-20: the manifest no longer
        # declares it, so the transform neither declares nor rebuilds a
        # provider for the retired host.
        self.assertFalse(
            [
                provider
                for provider in transformed["api-keys"]["openai-compatibility"]
                if provider["name"] == "opencode"
            ]
        )
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
            # A declared thinking-levels list is projected as the model's
            # thinking.levels block (the gateway serves capabilities, not the
            # manifest's own vocabulary).
            expected_models = json.loads(json.dumps(provider["models"]))
            for model in expected_models:
                levels = model.pop("thinking-levels", None)
                if levels is not None:
                    model["thinking"] = {"levels": levels}
            self.assertEqual(
                compatibility[provider["name"]],
                expected_models,
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

    def test_cpa_apply_upgrades_opencode_subset_headers_and_refuses_tampered(
        self,
    ) -> None:
        text = read_guardrail_source()
        apply_script = text.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0]
        embedded_python = apply_script.split("if ! python3 - <<'PY'\n", 1)[1].split(
            "\nPY\nthen", 1
        )[0]
        start = embedded_python.index("def build_provider(")
        end = embedded_python.index("\ntarget_hosts = ")
        build_src = embedded_python[start:end]
        ns: dict[str, Any] = {
            "deepcopy": __import__("copy").deepcopy,
            "forbidden_provider_keys": {"headers", "proxy-url"},
            "model_entry": lambda m: dict(m),
        }
        exec(build_src, ns)  # nosec B102
        build_provider = ns["build_provider"]
        # Synthetic carrier: slot 2 (opencode) retired 2026-11-20, so no real
        # manifest provider carries pinned headers or request-scoped errors
        # any more. build_provider is manifest-driven and generic; these
        # constants reproduce the retired slot's shape to keep the
        # strict-subset upgrade mechanism covered.
        carrier_headers = {
            "x-opencode-session": "cpa-bwg-gateway-01",
            "user-agent": "cpa-bwg-gateway/1.0",
        }
        carrier_rse = [{"status": 403, "match": ["1010"], "action": "stop"}]
        carrier_models: list[dict[str, Any]] = []
        # Live host pre-upgrade shape: only x-opencode-session, no user-agent,
        # and missing request-scoped-errors.
        existing_subset = {
            "name": "opencode",
            "base-url": "https://opencode.ai/zen/go/v1",
            "keys": [{"api-key": "OLD"}],
            "headers": {"x-opencode-session": "cpa-bwg-gateway-01"},
            "models": [],
        }
        upgraded = build_provider(
            existing_subset,
            "opencode",
            "https://opencode.ai/zen/go/v1",
            "NEW",
            carrier_models,
            carrier_headers,
            carrier_rse,
        )
        self.assertEqual(upgraded["headers"], carrier_headers)
        self.assertEqual(upgraded["request-scoped-errors"], carrier_rse)
        # Any undeclared or tampered existing header must still fail closed.
        for bad_headers in (
            {"x-opencode-session": "tampered"},
            {"x-opencode-session": "cpa-bwg-gateway-01", "x-evil": "1"},
        ):
            with self.subTest(bad_headers=bad_headers):
                tampered = dict(existing_subset, headers=bad_headers)
                with self.assertRaises(SystemExit) as ctx:
                    build_provider(
                        tampered,
                        "opencode",
                        "https://opencode.ai/zen/go/v1",
                        "NEW",
                        carrier_models,
                        carrier_headers,
                        carrier_rse,
                    )
                self.assertIn("REFUSE", str(ctx.exception))

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
        self.assertIn(
            'log "ROLLBACK_VERIFIED interrupted=${ROLLBACK_INTERRUPTED:-0} '
            'restored=$CUR backup=$BK"',
            updater,
        )
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
        self.assertIn('old_digest = "sha256:" + "a" * 64', acceptance)

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
