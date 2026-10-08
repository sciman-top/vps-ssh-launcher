"""test_cpa_updater_apply.py - split from test_scripts.py (domain: apply)."""

import ast
import builtins
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, cast

from script_validation_support import (
    HEALTH_FIXTURE_CATALOG_IDS,
    ScriptValidationMixin,
    read_guardrail_source,
)


class CpaUpdaterApplyTests(ScriptValidationMixin, unittest.TestCase):
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

        # Deletion is bounded: exactly one rm -rf restricted to backup-dir
        # entries collected by find, and one docker rmi restricted to the
        # pinned repo. Anything broader is an unbounded-delete hazard.
        self.assertEqual(source.count("rm -rf"), 1)
        self.assertEqual(source.count("docker rmi"), 1)
        # Pruning runs only on the verified success path; the UNVERIFIED exit-10
        # and rollback paths keep every backup and image.
        ok_log = source.index('log "OK: updated')
        unverified = source.index("UNVERIFIED: upstream unavailable")
        between = source[unverified:ok_log]
        self.assertNotIn("prune_backups", between)
        self.assertNotIn("prune_images", between)
        self.assertIn("\nprune_backups\n", source[ok_log:])
        self.assertIn("\nprune_images\n", source[ok_log:])
        # Error-request dumps are age-bounded hygiene: swept on the daily
        # timer path too, restricted to auth/logs/error-*.log older than 48
        # hours (2026-09-21 review: plaintext bodies stay at rest too long).
        self.assertIn("prune_error_dumps", source[:unverified])
        self.assertIn("\nprune_error_dumps\n", source[ok_log:])
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
        branch = source[start:end]
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
        for runtime_digest, pending_digest, expected_code, marker in (
            (expected_digest, expected_digest, 0, "PENDING_VERIFIED"),
            (
                "sha256:" + "b" * 64,
                expected_digest,
                10,
                "running CPA image does not match",
            ),
            ("sha256:" + "b" * 64, "invalid", 10, "record is malformed"),
        ):
            with (
                self.subTest(marker=marker),
                tempfile.TemporaryDirectory() as directory,
            ):
                pending = Path(directory) / "cpa-update.pending"
                pending.write_text(
                    f"version=v8.0.16\ndigest={pending_digest}\n",
                    encoding="utf-8",
                )
                harness = "\n".join(
                    [
                        "set -u",
                        "MODE=--confirm-pending; CUR=v8.0.16",
                        f"PENDING_FILE='{self._bash_path(bash, pending)}'",
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
                (root / f"202609{day:02d}T000000Z-from-v0.0.{day}").mkdir()
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
                        for day in range(3, 11)
                    ),
                    "foreign.txt",
                ],
            )
            self.assertIn(
                "LOG PRUNE scope=backups kept=8 removed=2",
                completed.stdout.decode(),
            )

    def test_cpa_prune_images_normalizes_docker_image_ids(self) -> None:
        import tempfile

        bash = self._resolve_bash()
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parents[1] / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_images() {") : source.index(
                '\nif [[ "$CUR" == "$TARGET" ]]'
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            log_path = self._bash_path(bash, Path(directory) / "prune.log")
            backup_root = Path(directory) / "backups"
            retained = backup_root / "20260901T000000Z-from-v0.0.1"
            retained.mkdir(parents=True)
            (retained / "compose.yml").write_text(
                "services:\n  cli-proxy-api:\n    image: rollback-image\n"
            )
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    f"BK='{self._bash_path(bash, retained)}'",
                    f"BACKUP_ROOT='{self._bash_path(bash, backup_root)}'",
                    "CPA_IMAGE_REPO=eceasy/cli-proxy-api",
                    f"LOG='{log_path}'",
                    "log() { printf 'LOG %s\\n' \"$*\"; }",
                    "secure_error_dumps() { :; }",
                    "prune_error_dumps() { :; }",
                    "compose_image_ref() { printf 'rollback-image\\n'; }",
                    "docker() {",
                    '  if [[ "$1" == inspect && "$2" == --format ]]; then',
                    "    printf 'sha256:%064d\\n' 0 | tr '0' 'a'",
                    '  elif [[ "$1" == image && "$2" == inspect && "$4" == \'{{.ID}}\' ]]; then',
                    "    printf 'sha256:%064d\\n' 0 | tr '0' 'b'",
                    '  elif [[ "$1" == image && "$2" == inspect && "$4" == \'{{.Size}}\' ]]; then',
                    "    printf '123\\n'",
                    '  elif [[ "$1" == images ]]; then',
                    "    printf '%s\\n' 'aaaaaaaaaaaa eceasy/cli-proxy-api:<none>' 'bbbbbbbbbbbb eceasy/cli-proxy-api:<none>' 'cccccccccccc eceasy/cli-proxy-api:<none>'",
                    '  elif [[ "$1" == rmi ]]; then',
                    "    printf 'removed=%s\\n' \"$2\"",
                    "  else",
                    "    return 1",
                    "  fi",
                    "}",
                    function,
                    "prune_images",
                    'cat "$LOG"',
                ]
            )
            completed = subprocess.run(
                self._bash_command(bash),
                input=harness.encode(),
                capture_output=True,
                timeout=self.BASH_HARNESS_TIMEOUT_SECONDS,
            )
            output = completed.stdout.decode()
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertIn("removed=cccccccccccc", output)
            self.assertNotIn("removed=aaaaaaaaaaaa", output)
            self.assertNotIn("removed=bbbbbbbbbbbb", output)
            self.assertIn(
                "PRUNE scope=images kept=2 removed=1 freed_bytes=123",
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
                yaml.safe_dump({"codex": {"stream-bootstrap-buffering": False}}),
                encoding="utf-8",
            )
            # Headers are committed before the upstream failure arrives, so CPA
            # relays the upstream's 200 with the capacity marker in the body.
            self.assertEqual(expected(root), 200)
            config_path.write_text(
                yaml.safe_dump({"codex": {"stream-bootstrap-buffering": True}}),
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
