"""Read-only --all admission, parallel fan-out and ordered reporting."""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from vps_ssh_launcher import batch, connection, contracts
from launcher_fakes import (
    FakeAuthenticationException,
    FakeParamikoModule,
    patch_attr,
    patch_env,
)


class BatchTests(unittest.TestCase):
    def test_completed_host_output_batches_flushes_without_changing_lines(self) -> None:
        class Output(io.StringIO):
            flushes = 0

            def flush(self) -> None:
                self.flushes += 1
                super().flush()

        stream = Output()
        text = "中文\r\n" * 3000 + "tail"
        batch._print_prefixed_lines("alpha", text, stream=stream)
        self.assertEqual(
            stream.getvalue(), "[alpha] 中文\r\n" * 3000 + "[alpha] tail\n"
        )
        self.assertEqual(stream.flushes, 1)

    def test_run_all_rejects_mutations_before_loading_targets(self) -> None:
        commands = [
            "ip link set eth0 down",
            "git branch -D important",
            "sed -i.bak s/a/b/ file",
            "sed --in-place=.bak s/a/b/ file",
            "curl --request=DELETE https://example.invalid/resource",
            "curl -XDELETE https://example.invalid/resource",
            "hostname changed-name",
            "find . -delete",
            "awk 'system(\"reboot\")'",
            "/tmp/uptime",
            "systemctl status --now xray",
            "systemctl status *",
            "docker ps --help",
            "uname -a; reboot",
        ]
        for command in commands:
            with (
                self.subTest(command=command),
                mock.patch.object(batch, "_run_all_config_file") as load,
            ):
                with self.assertRaises(ValueError):
                    batch.run_on_all(argparse.Namespace(), command)
                load.assert_not_called()

    def test_run_all_allows_documented_diagnostics(self) -> None:
        for command in [
            "uptime",
            "uname -a",
            "df -h",
            "free -m",
            "ss -ltnp",
            "systemctl is-active xray",
            "systemctl status sing-box.service",
        ]:
            with self.subTest(command=command):
                batch._validate_run_all_command(command)

    def test_run_on_all_returns_remote_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                                "password": "secret",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_connect(ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None, host=ns.host)

            def fake_exec(
                client: Any,
                command: str,
                **_kwargs: Any,
            ) -> tuple[int, str, str, bool, bool]:
                if client.host == "10.0.0.1":
                    return 0, "ok\n", "", False, False
                return 9, "", "oops\n", False, False

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(batch, "execute_remote_capture", side_effect=fake_exec):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 9)
            self.assertIn("[alpha] ok", stdout.getvalue())
            self.assertIn("[beta] exit code: 9", stdout.getvalue())
            self.assertIn("[beta] oops", stderr.getvalue())

    def test_run_on_all_passes_command_timeout_to_exec_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                command_timeout=300,
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_connect(_ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None)

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(
                    batch,
                    "execute_remote_capture",
                    return_value=(0, "ok\n", "", False, False),
                ) as execute_remote:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertEqual(execute_remote.call_args.kwargs["command_timeout"], 300)

    def test_run_on_all_passes_command_hard_timeout_to_exec_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                command_timeout=300,
                command_hard_timeout=900,
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_connect(_ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None)

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(
                    batch,
                    "execute_remote_capture",
                    return_value=(0, "ok\n", "", False, False),
                ) as execute_remote:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertEqual(
                execute_remote.call_args.kwargs["command_hard_timeout"], 900
            )

    def test_run_on_all_rejects_invalid_command_timeout_before_thread_fanout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                                "password": "secret",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                command_timeout=-1,
                allow_agent=False,
                strict_host_key_checking=False,
            )

        with self.assertRaisesRegex(ValueError, "Command timeout"):
            batch.run_on_all(args, "uptime")

    def test_run_on_all_rejects_mutating_command_before_thread_fanout(self) -> None:
        args = argparse.Namespace(command_timeout=60)

        with self.assertRaisesRegex(ValueError, "read-only systemctl"):
            batch.run_on_all(args, "systemctl restart xray")

    def test_run_on_all_rejects_shell_composition_before_thread_fanout(self) -> None:
        args = argparse.Namespace(command_timeout=60)

        with self.assertRaisesRegex(ValueError, "shell operators"):
            batch.run_on_all(args, "uptime && reboot")

    def test_run_on_all_rejects_invalid_command_hard_timeout_before_thread_fanout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                command_timeout=60,
                command_hard_timeout=-1,
                allow_agent=False,
                strict_host_key_checking=False,
            )

            with self.assertRaisesRegex(ValueError, "hard timeout"):
                batch.run_on_all(args, "uptime")

    def test_run_on_all_allows_agent_only_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=True,
                strict_host_key_checking=False,
            )

            def fake_connect(_ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None)

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(
                    batch,
                    "execute_remote_capture",
                    return_value=(0, "ok\n", "", False, False),
                ):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertIn("[alpha] ok", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_run_on_all_uses_cli_key_for_profiles_without_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                password=None,
                key="  shared-key.pem  ",
                allow_agent=False,
                strict_host_key_checking=False,
            )
            seen: list[argparse.Namespace] = []

            def fake_connect(ns: argparse.Namespace) -> SimpleNamespace:
                seen.append(ns)
                return SimpleNamespace(close=lambda: None)

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(
                    batch,
                    "execute_remote_capture",
                    return_value=(0, "ok\n", "", False, False),
                ):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual({ns.key for ns in seen}, {"shared-key.pem"})
            self.assertEqual({ns.password for ns in seen}, {None})

    def test_run_on_all_allow_agent_skips_missing_password_env(self) -> None:
        env_name = "VPS_SSH_TOOL_TEST_MISSING_PASSWORD"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password_env": env_name,
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                                "password_env": env_name,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                password=None,
                key=None,
                allow_agent=True,
                strict_host_key_checking=False,
            )
            seen: list[argparse.Namespace] = []

            def fake_connect(ns: argparse.Namespace) -> SimpleNamespace:
                seen.append(ns)
                return SimpleNamespace(close=lambda: None)

            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                with patch_attr(
                    batch,
                    "connect_with_retry",
                    side_effect=fake_connect,
                ):
                    with patch_attr(
                        batch,
                        "execute_remote_capture",
                        return_value=(0, "ok\n", "", False, False),
                    ):
                        with (
                            redirect_stdout(io.StringIO()),
                            redirect_stderr(io.StringIO()),
                        ):
                            code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual({ns.password for ns in seen}, {None})
            self.assertEqual({ns.key for ns in seen}, {None})
            self.assertEqual({ns.allow_agent for ns in seen}, {True})

    def test_run_on_all_prints_summary_with_failure_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                                "password": "secret",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_connect(ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None, host=ns.host)

            def fake_exec(
                client: Any,
                _command: str,
                **_kwargs: Any,
            ) -> tuple[int, str, str, bool, bool]:
                if client.host == "10.0.0.1":
                    return 0, "ok\n", "", False, False
                return 7, "", "failed\n", False, False

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(batch, "execute_remote_capture", side_effect=fake_exec):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 7)
            text = stdout.getvalue()
            self.assertIn("[summary] profiles=2 ok=1 failed=1", text)
            self.assertIn("[summary] remote_nonzero: 1", text)
            self.assertIn("[summary] max_exit_code: 7", text)
            self.assertIn("[summary] exit_code_histogram: 0=1, 7=1", text)
            self.assertIn("[summary] failed_profiles: beta", text)
            self.assertIn("[alpha] elapsed:", text)
            self.assertIn("[beta] elapsed:", text)
            self.assertIn("[beta] exit code: 7", text)
            self.assertIn("[beta] failed", stderr.getvalue())

    def test_run_on_all_marks_truncated_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_connect(_ns: argparse.Namespace) -> SimpleNamespace:
                return SimpleNamespace(close=lambda: None)

            with patch_attr(batch, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(
                    batch,
                    "execute_remote_capture",
                    return_value=(
                        0,
                        "x" * (contracts.RUN_ALL_OUTPUT_LIMIT - 64),
                        "",
                        True,
                        False,
                    ),
                ):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            text = stdout.getvalue()
            self.assertIn("[alpha] stdout truncated", text)

    def test_run_on_all_keeps_summary_when_worker_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            },
                            "beta": {
                                "host": "10.0.0.2",
                                "user": "root",
                                "password": "secret",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            def fake_run_profile_command(
                name: str,
                _entry: dict[str, Any],
                _context: batch.ProfileRunContext,
            ) -> batch.ProfileRunResult:
                if name == "beta":
                    raise RuntimeError("worker exploded")
                return batch.ProfileRunResult(
                    name=name,
                    code=0,
                    stdout="ok",
                    stderr="",
                    category="ok",
                    elapsed=0.01,
                )

            with patch_attr(
                batch,
                "_run_profile_command",
                side_effect=fake_run_profile_command,
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, contracts.EXIT_CMD_ERROR)
            text = stdout.getvalue()
            self.assertIn("[alpha] ok\n[alpha] elapsed:", text)
            self.assertIn("[summary] profiles=2 ok=1 failed=1", text)
            self.assertIn("[summary] internal_error: 1", text)
            self.assertIn("[summary] failed_profiles: beta", text)
            self.assertIn("worker exploded", stderr.getvalue())

    def test_run_on_all_closes_client_on_unexpected_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "s",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )
            closed = []
            fake_client = SimpleNamespace(close=lambda: closed.append(True))

            with patch_attr(batch, "connect_with_retry", return_value=fake_client):
                with patch_attr(
                    batch, "execute_remote_capture", side_effect=RuntimeError("boom")
                ):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        batch.run_on_all(args, "test")

            self.assertTrue(closed, "client should be closed even on unexpected errors")

    def test_run_on_all_classifies_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            with patch_attr(
                batch,
                "connect_with_retry",
                side_effect=socket.timeout("connect timeout"),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, contracts.EXIT_TIMEOUT)
            self.assertIn("[alpha] exit code: 3", stdout.getvalue())

    def test_run_on_all_classifies_paramiko_auth_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            with patch_attr(
                batch,
                "connect_with_retry",
                side_effect=FakeAuthenticationException("denied"),
            ):
                with patch_attr(
                    connection, "_load_paramiko", return_value=FakeParamikoModule
                ):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, contracts.EXIT_SSH_ERROR)
            self.assertIn("[summary] auth_error: 1", stdout.getvalue())
            self.assertIn("denied", stderr.getvalue())

    def test_run_on_all_classifies_missing_password_env_as_config_error(self) -> None:
        env_name = "VPS_SSH_TOOL_TEST_MISSING_PASSWORD"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "password_env": env_name,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                allow_agent=False,
                strict_host_key_checking=False,
            )

            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = batch.run_on_all(args, "uptime")

            self.assertEqual(code, contracts.EXIT_CONFIG_ERROR)
            self.assertIn("[alpha] exit code: 2", stdout.getvalue())
            self.assertIn(env_name, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
