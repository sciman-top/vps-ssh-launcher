import ast
import builtins
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path


class ScriptValidationTests(unittest.TestCase):
    def test_cpa_health_classifies_overload_and_model_exposure(self) -> None:
        import runpy
        import urllib.error
        from email.message import Message

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    "glm-5.3-flash",
                    "gpt-5.5",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "gpt-6-astra",
                ]
            ]
        }
        for code, expected in [
            (401, 20),
            (403, 20),
            (429, 10),
            (503, 10),
            (520, 10),
            (526, 10),
            (400, 20),
        ]:
            with self.subTest(code=code):
                req = mock.Mock(
                    side_effect=[
                        catalog,
                        urllib.error.HTTPError("", code, "", Message(), None),
                    ]
                )
                self.assertEqual(check({}, "generation", req, mock.Mock()), expected)
        req = mock.Mock(
            return_value={"data": catalog["data"] + [{"id": "gpt-unexpected"}]}
        )
        self.assertEqual(check({}, "readiness", req, mock.Mock()), 20)
        self.assertEqual(req.call_count, 1)
        for mode, expected in [("readiness", 0), ("generation", 10)]:
            request = mock.Mock(return_value={"data": []})
            self.assertEqual(check({}, mode, request, mock.Mock()), expected)
        request = mock.Mock(return_value={"error": "invalid response"})
        self.assertEqual(check({}, "readiness", request, mock.Mock()), 20)

    def test_cpa_health_loopback_request_bypasses_ambient_proxy(self) -> None:
        import io
        import json
        import runpy
        import urllib.request

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": model}
                for model in [
                    "glm-5.3-flash",
                    "gpt-5.5",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "gpt-6-astra",
                ]
            ]
        }
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(catalog).encode())
        response.__exit__.return_value = False
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(
                check({"api-keys": ["SECRET"]}, "readiness"),
                0,
            )
        build.assert_called_once()
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})
        opener.open.assert_called_once()

    def test_cpa_updater_waits_for_auth_registration_without_generation_retry(
        self,
    ) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        catalog = {
            "data": [
                {"id": m}
                for m in [
                    "glm-5.3-flash",
                    "gpt-5.5",
                    "gpt-5.6-luna",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "gpt-6-astra",
                ]
            ]
        }
        smoke = {
            "model": "gpt-5.6-luna",
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

    def test_cpa_health_all_routes_is_explicit_and_budgeted(self) -> None:
        import runpy

        check = runpy.run_path(
            str(Path(__file__).parent / "scripts/remote/cpa-health.py")
        )["check"]
        models = [
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-6-astra",
            "glm-5.3-flash",
        ]
        catalog = {"data": [{"id": m} for m in [*models, "gpt-5.5"]]}
        responses: list[object] = [catalog]
        responses.extend(
            {
                "model": model,
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            }
            for model in models
        )
        request = mock.Mock(side_effect=responses)
        self.assertEqual(check({}, "generation-all", request, mock.Mock()), 0)
        self.assertEqual(request.call_count, 6)
        self.assertEqual(
            request.call_args_list[-1].args[1]["max_tokens"],
            1024,
        )

    def test_cpa_updater_selects_mature_release_without_starvation(self) -> None:
        import contextlib
        import datetime as dt
        import io
        import json
        import tempfile

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        selection = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        now = dt.datetime.now(dt.timezone.utc)
        old = (now - dt.timedelta(days=4)).isoformat()
        fresh = (now - dt.timedelta(hours=1)).isoformat()
        releases = [
            {"tag_name": tag, "published_at": age, "draft": False, "prerelease": False}
            for tag, age in [
                ("v7.2.159", fresh),
                ("v7.2.156", old),
                ("v7.3.0", old),
            ]
        ]
        tags = {
            "results": [
                {"name": tag, "last_updated": age, "digest": "sha256:" + "a" * 64}
                for tag, age in [
                    ("v7.2.159", fresh),
                    ("v7.2.156", old),
                    ("v7.3.0", old),
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

    def test_cpa_updater_prunes_with_bounded_retention_after_success(self) -> None:
        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()

        self.assertIn("MIN_FREE_KIB=2097152", source)
        self.assertIn('BACKUP_ROOT="$DIR/backups"', source)
        self.assertIn("BACKUP_HEALTH status=ok", source)
        self.assertIn("BACKUP_HEALTH status=insufficient_free_space", source)
        self.assertIn("BACKUP_HEALTH status=invalid_root", source)
        self.assertIn('-L "$BACKUP_ROOT"', source)
        self.assertIn('[[ "$backup_mode" != 700 ]]', source)
        self.assertNotIn("rm -d", source)
        self.assertIn("RETENTION_KEEP_BACKUPS=8", source)
        self.assertIn("version(t['name'])[:2] == current_version[:2]", source)
        self.assertIn("-name '*-from-v[0-9]*'", source)
        self.assertIn("CPA_IMAGE_REPO=eceasy/cli-proxy-api", source)
        # Deletion is bounded: one rm -rf restricted to backup-dir entries
        # collected by find, and one docker rmi restricted to the pinned repo.
        self.assertEqual(source.count("rm -rf"), 1)
        self.assertIn('rm -rf -- "$entry"', source)
        self.assertEqual(source.count("docker rmi"), 1)
        self.assertIn(
            "docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}'", source
        )
        self.assertIn("'$2 ~ \"^\"repo {print}'", source)
        # Digest-pinned pulls leave no version tag, so the rollback image is
        # protected by ID resolved from the backup compose next to the running
        # image ID, and untagged repository entries are removed by ID.
        self.assertIn(
            'docker image inspect "$(compose_image_ref "$DIR/compose.yml")"', source
        )
        self.assertIn('compose_image_ref "$BK/compose.yml"', source)
        self.assertIn("short_image_id() {", source)
        self.assertIn("local image_id=${1#sha256:}", source)
        self.assertIn('running_id=$(short_image_id "$(docker inspect --format', source)
        self.assertIn(
            'protected_id=$(short_image_id "$(docker image inspect --format', source
        )
        self.assertIn('"$id" == "$running_id"', source)
        self.assertIn('"$id" == "$protected_id"', source)
        # Pruning runs only on the verified success path; the UNVERIFIED exit-10
        # and rollback paths keep every backup and image.
        ok_log = source.index('log "OK: updated')
        unverified = source.index("UNVERIFIED: upstream unavailable")
        between = source[unverified:ok_log]
        self.assertNotIn("prune_backups", between)
        self.assertNotIn("prune_images", between)
        self.assertIn("\nprune_backups\n", source[ok_log:])
        self.assertGreater(
            source.index("\nprune_images\n", ok_log),
            source.index("\nprune_backups\n", ok_log),
        )

    def test_cpa_updater_bash_syntax_parses(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        script_path = self._bash_path(
            bash, Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        )
        completed = subprocess.run(
            [
                bash,
                "-n",
                script_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_cpa_prune_backups_keeps_newest_backup_dirs(self) -> None:
        import tempfile

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
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
                [bash],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
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

        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (
            Path(__file__).parent / "scripts/remote/cpa-auto-update.sh"
        ).read_text()
        function = source[
            source.index("prune_images() {") : source.index(
                '\nif [[ "$CUR" == "$TARGET" ]]'
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            log_path = self._bash_path(bash, Path(directory) / "prune.log")
            harness = "\n".join(
                [
                    "set -euo pipefail",
                    "BK=/backup",
                    "CPA_IMAGE_REPO=eceasy/cli-proxy-api",
                    f"LOG='{log_path}'",
                    "log() { printf 'LOG %s\\n' \"$*\"; }",
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
                [bash],
                input=harness.encode(),
                capture_output=True,
                timeout=30,
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

    @staticmethod
    def _bash_path(bash: str, path: Path) -> str:
        """Use a path understood by the selected Bash implementation."""
        if os.name != "nt" or not path.drive:
            return str(path)

        # Windows ships a WSL bash launcher that cannot consume Win32 paths.
        # Probe the selected executable instead of assuming every bash.exe is
        # WSL; Git Bash accepts the original path form.
        try:
            probe = subprocess.run(
                [bash, "-c", "test -d /mnt/c"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return str(path)
        if probe.returncode != 0:
            return str(path)

        posix = path.as_posix()
        return f"/mnt/{path.drive[0].lower()}{posix[2:]}"

    def test_cpa_doctor_reports_timer_result_and_inventory(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        section = text[
            text.index('echo "==timer-result=="') : text.index('echo "==auth-modes=="')
        ]
        for anchor in (
            "systemctl show cliproxyapi-update.service -p Result --value",
            "systemctl show cliproxyapi-update.service -p ExecMainStatus --value",
            "systemctl show cliproxyapi-update.service -p ExecMainExitTimestamp --value",
            "auto-update.log",
            'echo "==inventory=="',
            "df -h /",
            "update_backups=",
            "update_backups_kib=",
            "cpa_image_tags=",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, section)
        # Reporting only: update failures surface through this output, and a
        # designed exit 10 (upstream unavailable) must not fail the doctor.
        self.assertNotIn("mark_fail", section)

    @staticmethod
    def _render_embedded_wrapper(source: str, function_name: str) -> str:
        function_start = source.index(f"{function_name}()")
        body_start = source.index("#!/usr/bin/env bash", function_start)
        body_end = source.index("\nEOF", body_start)
        rendered = source[body_start:body_end].replace("`", "")
        return rendered.replace("\r\n", "\n").replace("\r", "\n")

    def test_ssh_tool_direct_execution_invokes_cli(self) -> None:
        repo_root = Path(__file__).resolve().parent
        completed = subprocess.run(
            [sys.executable, str(repo_root / "ssh_tool.py"), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())

    def test_powershell_scripts_parse(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                self._assert_powershell_script_parses(powershell, script_path)

    def test_cpa_guardrails_freezes_public_data_plane_contract(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$Observe", text)
        self.assertIn("[switch]$RotatePath", text)
        self.assertIn("STRICT=1", text)
        self.assertIn("DOCTOR_CONTRACT_FAILED", text)
        self.assertIn("mark_fail fail2ban-file-monitor", text)
        self.assertIn("mark_fail safe-log-timestamp", text)
        self.assertIn("mark_fail safe-limit-status", text)
        self.assertIn("mark_fail gateway-transport", text)
        self.assertIn("mark_fail container-log-rotation", text)
        self.assertIn("mark_fail client-body-buffer", text)
        for anchor in (
            "client_max_body_size 32m;",
            "client_body_buffer_size 128k;",
            "proxy_buffering off;",
            "proxy_read_timeout 300s;",
            "proxy_send_timeout 300s;",
        ):
            self.assertIn(anchor, text)
        self.assertIn("legacy_log_format", text)
        self.assertIn("$chunkSize = 12000", text)
        self.assertIn("base64 -d -- '$remoteTemp' | bash", text)
        self.assertIn("chmod 600 '$remoteTemp'", text)
        self.assertIn("rm -f -- '$remoteTemp'", text)
        self.assertIn(
            '$updaterPath = Join-Path $scriptDir "remote\\cpa-auto-update.sh"', text
        )
        self.assertIn(
            '$healthPath = Join-Path $scriptDir "remote\\cpa-health.py"', text
        )
        self.assertIn("$healthBase64", text)
        self.assertIn("__CPA_HEALTH_B64__", text)
        self.assertIn("$updaterBase64", text)
        self.assertIn("__CPA_UPDATER_B64__", text)
        self.assertIn(
            'write_base64_file "__CPA_UPDATER_B64__" "$DIR/auto-update.sh" 700',
            text,
        )
        self.assertIn(
            'write_base64_file "__CPA_HEALTH_B64__" "$DIR/cpa-health.py" 644',
            text,
        )
        self.assertIn('cp -a "$DIR/cpa-health.py" "$BK/cpa-health.py"', text)
        self.assertIn('cp -a "$BK/cpa-health.py" "$DIR/cpa-health.py"', text)
        self.assertNotIn('config_after["codex-api-key"] =', text)
        self.assertNotIn('config_after["openai-compatibility"] =', text)
        self.assertIn("nginx -T", text)
        self.assertIn("cpa-port-binding=exact-loopback-only", text)
        self.assertIn(
            'expected = {"8317/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8317"}]}',
            text,
        )
        self.assertIn("public_authenticated_probe", text)
        self.assertIn('python3 -m py_compile "$DIR/cpa-health.py"', text)
        self.assertIn("valid_path_unauth", text)
        self.assertIn("bare_path", text)
        self.assertIn("wrong_path", text)
        self.assertIn("OLD_PATH_REVOKED=yes", text)
        self.assertIn("NEW_PATH_ACTIVE=yes", text)
        self.assertGreaterEqual(text.count("--noproxy '*'"), 5)
        self.assertNotIn("ssh -L", text)
        self.assertNotIn("ssh -R", text)
        self.assertNotIn("ssh -D", text)

    def test_cpa_guardrails_normalizes_crlf_in_remote_payloads(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
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

    def test_cpa_guardrails_payloads_are_valid_bash(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")

        source = (Path(__file__).parent / "scripts/cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
        payloads = {
            "doctor": source.split("$doctorScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "rotate": source.split("$rotateScript = @'\n", 1)[1].split("\n'@", 1)[0],
            "apply": source.split("$applyScript = @'\n", 1)[1].split("\n'@", 1)[0],
        }
        for name, payload in payloads.items():
            with self.subTest(payload=name):
                completed = subprocess.run(
                    ["bash", "-n"],
                    input=payload.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                output = (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                )
                self.assertEqual(completed.returncode, 0, output)

    def test_cpa_apply_embedded_python_and_rollback_contract(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )
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
        self.assertIn('python3 "$DIR/cpa-health.py" readiness', apply_script)
        self.assertIn("ROLLBACK_VERIFIED", apply_script)
        self.assertIn("ROLLBACK_FAILED", apply_script)
        self.assertIn("limit_req=$limit_req_status", apply_script)
        self.assertIn("limit_conn=$limit_conn_status", apply_script)

    def test_cpa_fail2ban_policy_has_versioned_source_and_is_projected(self) -> None:
        repo_root = Path(__file__).resolve().parent
        filter_source = (
            repo_root / "scripts" / "remote" / "cpa-fail2ban-filter.conf"
        ).read_text(encoding="utf-8")
        jail_source = (
            repo_root / "scripts" / "remote" / "cpa-fail2ban-jail.conf"
        ).read_text(encoding="utf-8")
        guardrails = (repo_root / "scripts" / "cpa_bwg_guardrails.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("auth_status=(401|403)", filter_source)
        self.assertIn("backend = polling", jail_source)
        self.assertIn(
            "logpath = /var/log/nginx/cpa_gateway.access.log tail", jail_source
        )
        self.assertIn("$fail2banFilterPath", guardrails)
        self.assertIn("$fail2banJailPath", guardrails)
        self.assertIn("__CPA_FAIL2BAN_FILTER_B64__", guardrails)
        self.assertIn("__CPA_FAIL2BAN_JAIL_B64__", guardrails)
        self.assertIn("fail2ban-client reload --restart cpa-gateway", guardrails)

    def _assert_powershell_script_parses(
        self, powershell: str, script_path: Path
    ) -> None:
        command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path -LiteralPath $env:VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST),
  [ref]$tokens,
  [ref]$errors
) | Out-Null

if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 1
}
"""
        args = [powershell, "-NoProfile"]
        if Path(powershell).name.lower() == "powershell.exe":
            args += ["-ExecutionPolicy", "Bypass"]
        args += ["-Command", command]
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_SCRIPT_UNDER_TEST"] = str(script_path)

        completed = subprocess.run(
            args,
            cwd=script_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_google_ipv4_routing_script_is_opt_in_for_apply(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "google_ipv4_routing.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("[switch]$Apply", text)
        self.assertIn("reapply-google-ipv4-routing.sh", text)
        self.assertIn("google_ipv4_out", text)
        self.assertIn("ForceIPv4", text)
        self.assertIn("xray-missing", text)
        self.assertIn("Assert-SafeRemoteApplyScript", text)
        self.assertIn("config_test_output=", text)
        self.assertIn('"--strict-host-key-checking"', text)
        self.assertNotIn("/tmp/xray-google-ipv4-test.out", text)

        check_command = text.split("$checkCommand = @'", 1)[1].split("'@", 1)[0]
        self.assertNotIn(
            "`",
            check_command,
            "single-quoted here-strings pass backticks to bash verbatim; "
            "escape $ only inside double-quoted here-strings",
        )

    def test_google_ipv4_routing_reuses_project_python_resolution(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "google_ipv4_routing.ps1").read_text(
            encoding="utf-8"
        )
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("Resolve-ProjectPython", helper)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", helper)
        self.assertIn(".venv\\Scripts\\python.exe", helper)
        self.assertIn("Invoke-LauncherPython", helper)
        self.assertIn("Invoke-LauncherPython -Python $py", text)
        self.assertNotIn("& python $sshTool", text)

    def test_vasma_kernel_cron_uses_vasma_menu_not_direct_downloads(self) -> None:
        text = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("16.core管理 -> 1.Xray-core -> 1.升级Xray-core", text)
        self.assertIn("16.core管理 -> 2.sing-box -> 1.升级 sing-box", text)
        self.assertIn("printf '16\\n1\\n1\\ny\\n' | /usr/bin/vasma", text)
        self.assertIn("printf '16\\n2\\n1\\ny\\n' | /usr/bin/vasma", text)
        self.assertIn("vasma_visible_stable_xray_version", text)
        self.assertIn("vasma_visible_stable_singbox_version", text)
        self.assertIn("XTLS/Xray-core/releases/latest", text)
        self.assertIn("SagerNet/sing-box/releases/latest", text)
        self.assertIn("skip update to avoid empty download URL", text)
        self.assertIn("skip reinstall", text)
        self.assertIn("ensure_ipv4_only_route", text)
        self.assertIn('"strategy":"ipv4_only"', text)
        self.assertIn("pre-ipv4-only", text)
        self.assertIn("systemctl restart sing-box", text)
        self.assertIn("--connect-timeout 10 --max-time 30", text)
        self.assertIn('"--strict-host-key-checking"', text)
        self.assertIn('mv -f "`$candidate" "`$SINGBOX_CONFIG"', text)
        self.assertNotIn('cat "`$candidate" > "`$SINGBOX_CONFIG"', text)
        self.assertIn("auto_update_xray.sh", text)
        self.assertIn("auto_update_singbox.sh", text)
        self.assertIn("grep -v -E '/etc/v2ray-agent/auto_update_", text)
        self.assertIn("backup_apply_state", text)
        self.assertIn("restore_apply_state", text)
        self.assertIn("trap rollback_apply ERR INT TERM", text)
        self.assertIn("ROLLBACK_VERIFIED", text)
        self.assertIn("APPLY_BACKUP_DIR", text)
        self.assertNotIn("releases?per_page", text)
        self.assertNotIn("github.com/XTLS/Xray-core/releases/download", text)
        self.assertNotIn("github.com/SagerNet/sing-box/releases/download", text)
        self.assertNotIn('REPO="XTLS/Xray-core"', text)
        self.assertNotIn('REPO="SagerNet/sing-box"', text)

    def test_rendered_vasma_wrappers_are_valid_bash(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
        ).read_text(encoding="utf-8")
        for function_name in ("write_xray_wrapper", "write_singbox_wrapper"):
            with self.subTest(wrapper=function_name):
                wrapper = self._render_embedded_wrapper(source, function_name)
                completed = subprocess.run(
                    [bash, "-n"],
                    input=wrapper.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
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
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is not available")

        source = (
            Path(__file__).resolve().parent / "scripts" / "vasma_kernel_update_cron.ps1"
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
                    [bash, "-s"],
                    input=probe.encode("utf-8"),
                    capture_output=True,
                    timeout=30,
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

    def test_connect_ps1_template_uses_password_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"password_env": "VPS_EXAMPLE_PASSWORD"', text)
        self.assertNotIn('"password": "YOUR_PASSWORD"', text)

    def test_connect_ps1_prefers_project_python_over_path(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "connect.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("VPS_SSH_LAUNCHER_PYTHON", helper)
        self.assertIn(".venv\\Scripts\\python.exe", helper)

    def test_shared_launcher_normalizes_remote_command_line_endings(self) -> None:
        helper = (
            Path(__file__).resolve().parent
            / "scripts"
            / "lib"
            / "project_environment.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("$normalizedLauncherArgs", helper)
        self.assertIn('$_ -replace "`r`n", "`n"', helper)
        self.assertIn("| Out-Host", helper)
        self.assertIn("$exitCode = $LASTEXITCODE", helper)

    def test_connect_cmd_requires_powershell_7(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.cmd").read_text(
            encoding="utf-8"
        )

        self.assertIn("VPS_SSH_LAUNCHER_POWERSHELL", text)
        self.assertIn("pwsh.exe", text)
        self.assertNotIn('set "POWERSHELL_EXE=powershell.exe"', text)
        self.assertIn("PowerShell 7", text)

    def test_connect_ps1_initializes_windows_process_environment(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "connect.ps1").read_text(encoding="utf-8")
        helper = (repo_root / "scripts" / "lib" / "project_environment.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("project_environment.ps1", text)
        self.assertIn("Initialize-WindowsProcessEnvironment", text)
        self.assertIn("SYSTEMROOT", helper)
        self.assertIn("COMSPEC", helper)
        self.assertIn("APPDATA", helper)
        self.assertIn("LOCALAPPDATA", helper)
        self.assertIn("PROGRAMDATA", helper)

    def test_connect_ps1_forwards_run_options(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[int]$CommandTimeout = 60", text)
        self.assertIn("--command-timeout", text)
        self.assertIn("[int]$CommandHardTimeout = 0", text)
        self.assertIn("--command-hard-timeout", text)
        self.assertIn("[ValidateRange(1, 128)]", text)
        self.assertIn('PSBoundParameters.ContainsKey("MaxWorkers")', text)
        self.assertIn("--max-workers", text)

    def test_connect_ps1_requires_explicit_allow_global_bootstrap(self) -> None:
        text = (Path(__file__).resolve().parent / "connect.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$AllowGlobalBootstrap", text)
        self.assertIn("AllowGlobalBootstrap", text)
        self.assertIn(
            "Refusing to install or upgrade dependencies in non-isolated Python",
            text,
        )
        self.assertIn('importlib.metadata.version("paramiko")', text)
        self.assertIn("major == 5", text)

    def test_run_gates_covers_package_without_duplicate_tools(self) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        self.assertIn("project_environment.ps1", text)
        self.assertIn('"vps_ssh_launcher"', text)
        self.assertIn('"pytest"', text)
        self.assertIn("[switch]$RunDependencyAudit", text)
        self.assertNotIn('"unittest"', text)
        self.assertNotIn('"pyright"', text)
        self.assertNotIn('"vulture"', text)

    def test_run_gates_resolves_effective_integration_config_for_guard(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        text = (repo_root / "scripts" / "run_gates.ps1").read_text(encoding="utf-8")

        self.assertIn("function Resolve-IntegrationConfigPath", text)
        self.assertIn("vps-ssh-launcher\\target.json", text)
        self.assertIn("$effectiveIntegrationConfig", text)
        self.assertIn("-ConfigPath $effectiveIntegrationConfig", text)
        self.assertIn(
            "$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG = $effectiveIntegrationConfig",
            text,
        )

    def test_powershell_entrypoints_fail_fast_on_invalid_python_env(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "scripts" / "lib" / "project_environment.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn(
                    "VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist",
                    text,
                )

    def test_powershell_entrypoints_reuse_shared_environment_helper(self) -> None:
        repo_root = Path(__file__).resolve().parent
        script_paths = [
            repo_root / "connect.ps1",
            repo_root / "scripts" / "run_gates.ps1",
            repo_root / "scripts" / "google_ipv4_routing.ps1",
            repo_root / "scripts" / "vasma_kernel_update_cron.ps1",
        ]

        for script_path in script_paths:
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                self.assertIn("project_environment.ps1", text)
                self.assertNotIn("function Resolve-ProjectPython", text)

    def test_explicit_python_environment_is_probed_for_isolation(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        repo_root = Path(__file__).resolve().parent
        helper = repo_root / "scripts" / "lib" / "project_environment.ps1"
        command = r"""
. $env:VPS_SSH_LAUNCHER_HELPER_UNDER_TEST
$resolved = Resolve-ProjectPython -ProjectRoot $env:VPS_SSH_LAUNCHER_ROOT
$resolved.IsIsolated.ToString().ToLowerInvariant()
"""
        env = os.environ.copy()
        env["VPS_SSH_LAUNCHER_HELPER_UNDER_TEST"] = str(helper)
        env["VPS_SSH_LAUNCHER_ROOT"] = str(repo_root)
        env["VPS_SSH_LAUNCHER_PYTHON"] = sys.executable
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        expected = str(sys.prefix != sys.base_prefix).lower()
        self.assertEqual(completed.stdout.strip().splitlines()[-1], expected)

    def test_integration_workflow_is_fixed_strict_and_environment_protected(
        self,
    ) -> None:
        workflow = (
            Path(__file__).resolve().parent
            / ".github"
            / "workflows"
            / "integration-real-ssh.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("integration_command:", workflow)
        self.assertNotIn("integration_expected:", workflow)
        self.assertNotIn("-IntegrationCommand", workflow)
        self.assertNotIn("-IntegrationExpected", workflow)
        self.assertIn("environment: vps-production", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("group: vps-real-ssh-integration", workflow)
        self.assertIn("VPS_SSH_LAUNCHER_INTEGRATION_KNOWN_HOSTS", workflow)
        self.assertIn(
            'VPS_SSH_LAUNCHER_INTEGRATION_STRICT_HOST_KEY_CHECKING: "1"',
            workflow,
        )
        self.assertNotIn('"${{ inputs.integration_profile }}"', workflow)

    def test_repository_markdown_uses_lf_without_embedded_carriage_returns(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        markdown_files = [
            *repo_root.glob("*.md"),
            *(repo_root / "docs").rglob("*.md"),
        ]
        for path in markdown_files:
            with self.subTest(path=str(path.relative_to(repo_root))):
                self.assertNotIn(b"\r", path.read_bytes())

    def test_shared_environment_helper_is_the_only_inline_environment_definition(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parent
        all_scripts = [
            repo_root / "connect.ps1",
            *sorted((repo_root / "scripts").rglob("*.ps1")),
        ]

        for script_path in all_scripts:
            text = script_path.read_text(encoding="utf-8")
            is_helper = script_path.name == "project_environment.ps1"
            with self.subTest(script=str(script_path.relative_to(repo_root))):
                if is_helper:
                    self.assertIn("function Initialize-WindowsProcessEnvironment", text)
                    self.assertIn("function Resolve-ProjectPython", text)
                else:
                    self.assertNotIn(
                        "function Initialize-WindowsProcessEnvironment",
                        text,
                    )
                    self.assertNotIn("function Resolve-ProjectPython", text)


if __name__ == "__main__":
    unittest.main()
