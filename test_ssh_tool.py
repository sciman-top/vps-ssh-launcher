import argparse
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import ssh_tool
import auto_install


class FakeStdin:
    def close(self) -> None:
        pass


class FakeChannel:
    def __init__(
        self,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        exit_status: int = 0,
    ) -> None:
        self._stdout_chunks = list(stdout_chunks or [])
        self._stderr_chunks = list(stderr_chunks or [])
        self._exit_status = exit_status
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv_ready(self) -> bool:
        return bool(self._stdout_chunks)

    def recv(self, _size: int) -> bytes:
        return self._stdout_chunks.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self._stderr_chunks)

    def recv_stderr(self, _size: int) -> bytes:
        return self._stderr_chunks.pop(0)

    def exit_status_ready(self) -> bool:
        return not self._stdout_chunks and not self._stderr_chunks

    def recv_exit_status(self) -> int:
        return self._exit_status


class FakeFile:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    def exec_command(self, command: str) -> tuple[FakeStdin, FakeFile, FakeFile]:
        return FakeStdin(), FakeFile(self._channel), FakeFile(self._channel)

    def close(self) -> None:
        self.closed = True


class SSHToolTests(unittest.TestCase):
    def test_exec_remote_reads_both_streams(self) -> None:
        channel = FakeChannel(
            stdout_chunks=[b"hello ", b"world\n"],
            stderr_chunks=[b"warn\n"],
            exit_status=7,
        )
        client = FakeClient(channel)

        code, out, err = ssh_tool.exec_remote(client, "echo test")

        self.assertEqual(code, 7)
        self.assertEqual(out, "hello world\n")
        self.assertEqual(err, "warn\n")
        self.assertFalse(client.closed)

    def test_connect_client_rejects_missing_key_file_before_network(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password=None,
            key=str(Path(tempfile.gettempdir()) / "definitely-missing-key"),
            allow_agent=False,
            strict_host_key_checking=False,
        )

        with patch.object(ssh_tool.socket, "create_connection") as create_connection:
            with self.assertRaises(FileNotFoundError):
                ssh_tool.connect_client(args)

        create_connection.assert_not_called()

    def test_apply_config_loads_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "port": 2222,
                                "user": "root",
                                "password": "secret",
                            }
                        },
                        "default": "alpha",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config=str(config_path),
                profile=None,
                host=None,
                port=None,
                user=None,
                password=None,
                key=None,
            )

            ssh_tool.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.port, 2222)
            self.assertEqual(args.user, "root")
            self.assertEqual(args.password, "secret")

    def test_load_config_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "target.json"
            config_path.write_text("{invalid json", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                ssh_tool.load_config(config_path)

            self.assertIn("Invalid JSON", str(ctx.exception))

    def test_connect_client_coerces_string_port(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port="2222",
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )
        fake_sock = SimpleNamespace(
            close=lambda: None,
            setsockopt=lambda *_args: None,
        )

        with patch.object(
            ssh_tool.socket, "create_connection", return_value=fake_sock
        ) as create_connection:
            with patch.object(
                ssh_tool.paramiko.SSHClient, "connect", return_value=None
            ):
                with patch.object(
                    ssh_tool.paramiko.SSHClient, "get_transport", return_value=None
                ):
                    client = ssh_tool.connect_client(args)

        self.assertIsInstance(client, ssh_tool.paramiko.SSHClient)
        create_connection.assert_called_once()
        self.assertEqual(create_connection.call_args.args[0][1], 2222)

    def test_coerce_port_rejects_float_like_value(self) -> None:
        with self.assertRaises(ValueError):
            ssh_tool._coerce_port(22.7, context="test")

    def test_resolve_default_config_prefers_local_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_dir = Path(tmpdir)
            (script_dir / "target.json").write_text("{}", encoding="utf-8")
            with patch.object(
                ssh_tool,
                "_user_config_path",
                return_value=Path(tmpdir) / "missing-target.json",
            ):
                self.assertEqual(
                    ssh_tool.resolve_default_config_path(script_dir),
                    script_dir / "target.json",
                )

    def test_resolve_default_config_prefers_user_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_dir = Path(tmpdir) / "repo"
            script_dir.mkdir()
            (script_dir / "target.json").write_text("{}", encoding="utf-8")

            user_config = Path(tmpdir) / "target.json"
            user_config.write_text("{}", encoding="utf-8")

            with patch.object(ssh_tool, "_user_config_path", return_value=user_config):
                self.assertEqual(
                    ssh_tool.resolve_default_config_path(script_dir),
                    user_config,
                )

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

            def fake_exec(client: Any, command: str) -> tuple[int, str, str]:
                if client.host == "10.0.0.1":
                    return 0, "ok\n", ""
                return 9, "", "oops\n"

            with patch.object(ssh_tool, "connect_with_retry", side_effect=fake_connect):
                with patch.object(ssh_tool, "exec_remote", side_effect=fake_exec):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, 9)
            self.assertIn("[alpha] ok", stdout.getvalue())
            self.assertIn("[beta] exit code: 9", stdout.getvalue())
            self.assertIn("[beta] oops", stderr.getvalue())

    def test_validate_profile_rejects_empty_password(self) -> None:
        entry = {"host": "10.0.0.1", "user": "root", "password": ""}
        with self.assertRaises(ValueError) as ctx:
            ssh_tool.validate_profile(entry, "test")
        self.assertIn("password", str(ctx.exception))

    def test_validate_profile_rejects_empty_key(self) -> None:
        entry = {"host": "10.0.0.1", "user": "root", "key": ""}
        with self.assertRaises(ValueError) as ctx:
            ssh_tool.validate_profile(entry, "test")
        self.assertIn("key", str(ctx.exception))

    def test_validate_profile_rejects_invalid_password_env(self) -> None:
        entry = {"host": "10.0.0.1", "user": "root", "password_env": 123}
        with self.assertRaises(ValueError) as ctx:
            ssh_tool.validate_profile(entry, "test")
        self.assertIn("password_env", str(ctx.exception))

    def test_validate_profile_rejects_blank_host(self) -> None:
        entry = {"host": " ", "user": "root", "password": "secret"}
        with self.assertRaises(ValueError) as ctx:
            ssh_tool.validate_profile(entry, "test")
        self.assertIn("host", str(ctx.exception))

    def test_connect_with_retry_does_not_retry_missing_key_file(self) -> None:
        args = argparse.Namespace()
        with patch.object(
            ssh_tool,
            "connect_client",
            side_effect=FileNotFoundError("missing key"),
        ) as connect_client:
            with patch.object(ssh_tool.time, "sleep") as sleep:
                with self.assertRaises(FileNotFoundError):
                    ssh_tool.connect_with_retry(args)

        connect_client.assert_called_once_with(args)
        sleep.assert_not_called()

    def test_connect_client_closes_sock_on_connect_failure(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )
        fake_sock_close_called = []
        fake_sock = SimpleNamespace(
            close=lambda: fake_sock_close_called.append(True),
            setsockopt=lambda *_args: None,
        )

        with patch.object(ssh_tool.socket, "create_connection", return_value=fake_sock):
            with patch.object(
                ssh_tool.paramiko.SSHClient, "connect", side_effect=Exception("boom")
            ):
                with self.assertRaises(Exception):
                    ssh_tool.connect_client(args)

        self.assertTrue(
            fake_sock_close_called, "sock should be closed when connect fails"
        )

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

            with patch.object(ssh_tool, "connect_with_retry", return_value=fake_client):
                with patch.object(
                    ssh_tool, "exec_remote", side_effect=RuntimeError("boom")
                ):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        ssh_tool.run_on_all(args, "test")

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

            with patch.object(
                ssh_tool,
                "connect_with_retry",
                side_effect=socket.timeout("connect timeout"),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, ssh_tool.EXIT_TIMEOUT)
            self.assertIn("[alpha] exit code: 3", stdout.getvalue())

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

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, ssh_tool.EXIT_CONFIG_ERROR)
            self.assertIn("[alpha] exit code: 2", stdout.getvalue())
            self.assertIn(env_name, stderr.getvalue())

    def test_main_handles_pre_target_oserror_without_unboundlocal(self) -> None:
        argv = ["ssh_tool", "--config", "target.json", "check"]

        with patch.object(sys, "argv", argv):
            with patch.object(ssh_tool, "apply_config", side_effect=OSError("denied")):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    code = ssh_tool.main()

        self.assertEqual(code, ssh_tool.EXIT_CONFIG_ERROR)
        self.assertIn("Config error: denied", stderr.getvalue())

    def test_auto_install_generic_select_response(self) -> None:
        self.assertEqual(auto_install._generic_select_response(1), "2")
        self.assertEqual(auto_install._generic_select_response(2), "1")
        self.assertEqual(auto_install._generic_select_response(3), "")

    def test_exec_remote_closes_stream_handles(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], stderr_chunks=[], exit_status=0)
        stdin = FakeStdin()
        stdout = FakeFile(channel)
        stderr = FakeFile(channel)

        class FakeHandleClient:
            def exec_command(
                self, command: str
            ) -> tuple[FakeStdin, FakeFile, FakeFile]:
                return stdin, stdout, stderr

        client = FakeHandleClient()

        code, out, err = ssh_tool.exec_remote(client, "echo ok")

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertTrue(stdout.closed)
        self.assertTrue(stderr.closed)


if __name__ == "__main__":
    unittest.main()
