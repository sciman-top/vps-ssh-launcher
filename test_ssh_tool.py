import argparse
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import ssh_tool
import auto_install


_MISSING = object()


class RecordedCall:
    def __init__(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.args = args
        self.kwargs = kwargs


class StubCallable:
    def __init__(
        self,
        *,
        return_value: Any = None,
        side_effect: Any = None,
    ) -> None:
        self.return_value = return_value
        self.side_effect = side_effect
        self.calls: list[RecordedCall] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(RecordedCall(args, kwargs))
        if self.side_effect is not None:
            if isinstance(self.side_effect, BaseException):
                raise self.side_effect
            return self.side_effect(*args, **kwargs)
        return self.return_value

    @property
    def call_args(self) -> RecordedCall:
        if not self.calls:
            raise AssertionError("stub was not called")
        return self.calls[-1]

    def assert_not_called(self) -> None:
        if self.calls:
            raise AssertionError(f"expected no calls, got {len(self.calls)}")

    def assert_called_once(self) -> None:
        if len(self.calls) != 1:
            raise AssertionError(f"expected one call, got {len(self.calls)}")

    def assert_called_once_with(self, *args: Any, **kwargs: Any) -> None:
        self.assert_called_once()
        call = self.call_args
        if call.args != args or call.kwargs != kwargs:
            raise AssertionError(
                f"expected call args={args!r} kwargs={kwargs!r}, "
                f"got args={call.args!r} kwargs={call.kwargs!r}"
            )


@contextmanager
def patch_attr(
    target: Any,
    name: str,
    value: Any = _MISSING,
    *,
    return_value: Any = _MISSING,
    side_effect: Any = _MISSING,
) -> Iterator[Any]:
    original = getattr(target, name)
    replacement = value
    if replacement is _MISSING:
        replacement = StubCallable(
            return_value=None if return_value is _MISSING else return_value,
            side_effect=None if side_effect is _MISSING else side_effect,
        )
    setattr(target, name, replacement)
    try:
        yield replacement
    finally:
        setattr(target, name, original)


@contextmanager
def patch_env(
    mapping: MutableMapping[str, str],
    values: dict[str, str],
    *,
    clear: bool = False,
) -> Iterator[None]:
    original = dict(mapping)
    if clear:
        mapping.clear()
    mapping.update(values)
    try:
        yield
    finally:
        mapping.clear()
        mapping.update(original)


class FakeAuthenticationException(Exception):
    pass


class FakeRejectPolicy:
    pass


class FakeAutoAddPolicy:
    pass


class FakeSSHClient:
    def __init__(self) -> None:
        self.closed = False
        self.missing_host_key_policy: Any = None

    def load_system_host_keys(self) -> None:
        pass

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.missing_host_key_policy = policy

    def connect(self, **_kwargs: Any) -> None:
        pass

    def get_transport(self) -> Any:
        return None

    def close(self) -> None:
        self.closed = True


class FakeParamikoModule:
    SSHClient: type[FakeSSHClient] = FakeSSHClient
    RejectPolicy: type[FakeRejectPolicy] = FakeRejectPolicy
    AutoAddPolicy: type[FakeAutoAddPolicy] = FakeAutoAddPolicy
    AuthenticationException: type[FakeAuthenticationException] = (
        FakeAuthenticationException
    )


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

        with patch_attr(ssh_tool.socket, "create_connection") as create_connection:
            with self.assertRaises(FileNotFoundError):
                ssh_tool.connect_client(args)

        create_connection.assert_not_called()

    def test_connect_client_loads_paramiko_before_opening_socket(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )

        with patch_attr(
            ssh_tool, "_load_paramiko", side_effect=RuntimeError("paramiko broken")
        ):
            with patch_attr(ssh_tool.socket, "create_connection") as create_connection:
                with self.assertRaisesRegex(RuntimeError, "paramiko broken"):
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

    def test_apply_config_allows_agent_only_profile(self) -> None:
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
                allow_agent=True,
            )

            ssh_tool.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.port, 22)
            self.assertEqual(args.user, "root")
            self.assertIsNone(args.password)
            self.assertIsNone(args.key)

    def test_apply_config_cli_key_skips_missing_password_env(self) -> None:
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
                key="  shared-key.pem  ",
                allow_agent=False,
            )

            with patch_env(os.environ, {}, clear=False):
                os.environ.pop(env_name, None)
                ssh_tool.apply_config(args)

            self.assertEqual(args.host, "10.0.0.1")
            self.assertEqual(args.key, "shared-key.pem")
            self.assertIsNone(args.password)

    def test_apply_config_resolves_relative_key_from_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "conf"
            config_dir.mkdir()
            config_path = config_dir / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "alpha": {
                                "host": "10.0.0.1",
                                "user": "root",
                                "key": "keys/id_rsa",
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

            self.assertEqual(args.key, str(config_dir / "keys" / "id_rsa"))

    def test_resolve_key_falls_back_to_legacy_cwd_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "conf"
            config_dir.mkdir()
            legacy_cwd = Path(tmpdir) / "legacy"
            (legacy_cwd / "keys").mkdir(parents=True)
            legacy_key = legacy_cwd / "keys" / "id_rsa"
            legacy_key.write_text("dummy", encoding="utf-8")

            with patch_attr(ssh_tool.Path, "cwd", return_value=legacy_cwd):
                resolved = ssh_tool._resolve_key(
                    {"key": "keys/id_rsa"},
                    config_dir=config_dir,
                )

            self.assertEqual(resolved, str(legacy_key))

    def test_resolve_key_prefers_config_relative_path_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "conf"
            (config_dir / "keys").mkdir(parents=True)
            config_key = config_dir / "keys" / "id_rsa"
            config_key.write_text("dummy", encoding="utf-8")
            legacy_cwd = Path(tmpdir) / "legacy"
            (legacy_cwd / "keys").mkdir(parents=True)
            (legacy_cwd / "keys" / "id_rsa").write_text("legacy", encoding="utf-8")

            with patch_attr(ssh_tool.Path, "cwd", return_value=legacy_cwd):
                resolved = ssh_tool._resolve_key(
                    {"key": "keys/id_rsa"},
                    config_dir=config_dir,
                )

            self.assertEqual(resolved, str(config_key))

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

        with patch_attr(
            ssh_tool.socket, "create_connection", return_value=fake_sock
        ) as create_connection:
            with patch_attr(
                ssh_tool, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(FakeSSHClient, "connect", return_value=None):
                    with patch_attr(FakeSSHClient, "get_transport", return_value=None):
                        client = ssh_tool.connect_client(args)

        self.assertIsInstance(client, FakeSSHClient)
        create_connection.assert_called_once()
        self.assertEqual(create_connection.call_args.args[0][1], 2222)

    def test_connect_client_strips_host_and_user(self) -> None:
        args = SimpleNamespace(
            host=" 127.0.0.1 ",
            port=22,
            user=" root ",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )
        fake_sock = SimpleNamespace(
            close=lambda: None,
            setsockopt=lambda *_args: None,
        )

        with patch_attr(
            ssh_tool.socket, "create_connection", return_value=fake_sock
        ) as create_connection:
            with patch_attr(
                ssh_tool, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(FakeSSHClient, "connect", return_value=None) as connect:
                    with patch_attr(FakeSSHClient, "get_transport", return_value=None):
                        ssh_tool.connect_client(args)

        self.assertEqual(create_connection.call_args.args[0], ("127.0.0.1", 22))
        self.assertEqual(connect.call_args.kwargs["hostname"], "127.0.0.1")
        self.assertEqual(connect.call_args.kwargs["username"], "root")

    def test_coerce_port_rejects_float_like_value(self) -> None:
        with self.assertRaises(ValueError):
            ssh_tool._coerce_port(22.7, context="test")

    def test_resolve_default_config_prefers_local_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_dir = Path(tmpdir)
            (script_dir / "target.json").write_text("{}", encoding="utf-8")
            with patch_attr(
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

            with patch_attr(ssh_tool, "_user_config_path", return_value=user_config):
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

            with patch_attr(ssh_tool, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(ssh_tool, "exec_remote", side_effect=fake_exec):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, 9)
            self.assertIn("[alpha] ok", stdout.getvalue())
            self.assertIn("[beta] exit code: 9", stdout.getvalue())
            self.assertIn("[beta] oops", stderr.getvalue())

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

            with patch_attr(ssh_tool, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(ssh_tool, "exec_remote", return_value=(0, "ok\n", "")):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = ssh_tool.run_on_all(args, "uptime")

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

            with patch_attr(ssh_tool, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(ssh_tool, "exec_remote", return_value=(0, "ok\n", "")):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual({ns.key for ns in seen}, {"shared-key.pem"})
            self.assertEqual({ns.password for ns in seen}, {None})

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

            def fake_exec(client: Any, _command: str) -> tuple[int, str, str]:
                if client.host == "10.0.0.1":
                    return 0, "ok\n", ""
                return 7, "", "failed\n"

            with patch_attr(ssh_tool, "connect_with_retry", side_effect=fake_connect):
                with patch_attr(ssh_tool, "exec_remote", side_effect=fake_exec):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, 7)
            text = stdout.getvalue()
            self.assertIn("[summary] profiles=2 ok=1 failed=1", text)
            self.assertIn("[summary] remote_nonzero: 1", text)
            self.assertIn("[alpha] elapsed:", text)
            self.assertIn("[beta] elapsed:", text)
            self.assertIn("[beta] exit code: 7", text)
            self.assertIn("[beta] failed", stderr.getvalue())

    def test_validate_profile_rejects_empty_password(self) -> None:
        entry = {"host": "10.0.0.1", "user": "root", "password": ""}
        with self.assertRaises(ValueError) as ctx:
            ssh_tool.validate_profile(entry, "test")
        self.assertIn("password", str(ctx.exception))

    def test_validate_profile_rejects_empty_key(self) -> None:
        entry = {"host": "10.0.0.1", "user": "root", "key": "  "}
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
        with patch_attr(
            ssh_tool,
            "connect_client",
            side_effect=FileNotFoundError("missing key"),
        ) as connect_client:
            with patch_attr(ssh_tool.time, "sleep") as sleep:
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

        with patch_attr(ssh_tool.socket, "create_connection", return_value=fake_sock):
            with patch_attr(
                ssh_tool, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(
                    FakeSSHClient, "connect", side_effect=Exception("boom")
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

            with patch_attr(ssh_tool, "connect_with_retry", return_value=fake_client):
                with patch_attr(
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

            with patch_attr(
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
                ssh_tool,
                "connect_with_retry",
                side_effect=FakeAuthenticationException("denied"),
            ):
                with patch_attr(
                    ssh_tool, "_load_paramiko", return_value=FakeParamikoModule
                ):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, ssh_tool.EXIT_SSH_ERROR)
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
                    code = ssh_tool.run_on_all(args, "uptime")

            self.assertEqual(code, ssh_tool.EXIT_CONFIG_ERROR)
            self.assertIn("[alpha] exit code: 2", stdout.getvalue())
            self.assertIn(env_name, stderr.getvalue())

    def test_main_handles_pre_target_oserror_without_unboundlocal(self) -> None:
        argv = ["ssh_tool", "--config", "target.json", "check"]

        with patch_attr(sys, "argv", argv):
            with patch_attr(ssh_tool, "apply_config", side_effect=OSError("denied")):
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

    def test_exec_remote_redacts_sensitive_command_in_debug_log(self) -> None:
        channel = FakeChannel(stdout_chunks=[b"ok\n"], stderr_chunks=[], exit_status=0)
        client = FakeClient(channel)

        with self.assertLogs("ssh_tool", level="DEBUG") as captured:
            code, out, err = ssh_tool.exec_remote(client, "printf token=secret-value")

        self.assertEqual(code, 0)
        self.assertEqual(out, "ok\n")
        self.assertEqual(err, "")
        self.assertTrue(
            any(
                "<redacted command containing sensitive marker>" in line
                for line in captured.output
            )
        )
        self.assertFalse(any("secret-value" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
