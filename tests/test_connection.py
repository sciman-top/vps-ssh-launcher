"""SSH connection lifecycle, host trust, retries and error classification."""

from __future__ import annotations

import argparse
import errno
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from vps_ssh_launcher import connection, contracts, execution
from launcher_fakes import (
    FakeAuthenticationException,
    FakeParamikoModule,
    FakeRejectPolicy,
    FakeSSHClient,
    StubCallable,
    patch_attr,
)


class ConnectionTests(unittest.TestCase):
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

        with patch_attr(connection.socket, "create_connection") as create_connection:
            with self.assertRaises(FileNotFoundError):
                connection.connect_client(args)

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
            connection, "_load_paramiko", side_effect=RuntimeError("paramiko broken")
        ):
            with patch_attr(
                connection.socket, "create_connection"
            ) as create_connection:
                with self.assertRaisesRegex(RuntimeError, "paramiko broken"):
                    connection.connect_client(args)

        create_connection.assert_not_called()

    def test_connect_client_normalizes_connection_inputs(self) -> None:
        fake_sock = SimpleNamespace(
            close=lambda: None,
            setsockopt=lambda *_args: None,
        )
        cases = {
            "string port coerced to int": SimpleNamespace(
                host="127.0.0.1",
                port="2222",
                user="root",
                password="test",
                key=None,
                allow_agent=False,
                strict_host_key_checking=False,
            ),
            "host and user whitespace stripped": SimpleNamespace(
                host=" 127.0.0.1 ",
                port=22,
                user=" root ",
                password="test",
                key=None,
                allow_agent=False,
                strict_host_key_checking=False,
            ),
        }
        for label, args in cases.items():
            with self.subTest(case=label):
                with patch_attr(
                    connection.socket, "create_connection", return_value=fake_sock
                ) as create_connection:
                    with patch_attr(
                        connection, "_load_paramiko", return_value=FakeParamikoModule
                    ):
                        with patch_attr(
                            FakeSSHClient, "connect", return_value=None
                        ) as connect:
                            with patch_attr(
                                FakeSSHClient, "get_transport", return_value=None
                            ):
                                client = cast(
                                    FakeSSHClient, connection.connect_client(args)
                                )

                self.assertIsInstance(client, FakeSSHClient)
                self.assertEqual(
                    create_connection.call_args.args[0],
                    ("127.0.0.1", int(args.port)),
                )
                self.assertEqual(connect.call_args.kwargs["hostname"], "127.0.0.1")
                self.assertEqual(connect.call_args.kwargs["username"], "root")

    def test_connect_client_uses_persistent_auto_add_policy_by_default(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
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

        with patch_attr(connection.socket, "create_connection", return_value=fake_sock):
            with patch_attr(
                connection, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(FakeSSHClient, "connect", return_value=None):
                    with patch_attr(FakeSSHClient, "get_transport", return_value=None):
                        client = cast(FakeSSHClient, connection.connect_client(args))

        self.assertIsInstance(
            client.missing_host_key_policy,
            connection._PersistentAutoAddPolicy,
        )
        self.assertTrue(client.loaded_system_host_keys)

    def test_compatibility_mode_does_not_require_windows_openssh_store(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
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
        with patch_attr(connection.socket, "create_connection", return_value=fake_sock):
            with patch_attr(
                connection, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(
                    connection,
                    "_load_windows_openssh_host_keys",
                    side_effect=AssertionError("should not load in compatibility mode"),
                ):
                    with patch_attr(FakeSSHClient, "connect", return_value=None):
                        with patch_attr(
                            FakeSSHClient, "get_transport", return_value=None
                        ):
                            client = cast(
                                FakeSSHClient, connection.connect_client(args)
                            )
        self.assertIsInstance(
            client.missing_host_key_policy, connection._PersistentAutoAddPolicy
        )

    def test_persistent_auto_add_policy_saves_and_rejects_changed_key(self) -> None:
        paramiko_module = connection._load_paramiko()
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            first_key = paramiko_module.RSAKey.generate(1024)
            changed_key = paramiko_module.RSAKey.generate(1024)

            first_client = paramiko_module.SSHClient()
            policy = connection._PersistentAutoAddPolicy(paramiko_module, known_hosts)
            policy.missing_host_key(first_client, "example.test", first_key)

            reloaded_client = paramiko_module.SSHClient()
            reloaded_client.load_host_keys(str(known_hosts))
            persisted = reloaded_client.get_host_keys().lookup("example.test")
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted[first_key.get_name()], first_key)

            changed_client = paramiko_module.SSHClient()
            changed_policy = connection._PersistentAutoAddPolicy(
                paramiko_module,
                known_hosts,
            )
            with self.assertRaises(paramiko_module.BadHostKeyException):
                changed_policy.missing_host_key(
                    changed_client,
                    "example.test",
                    changed_key,
                )

    def test_persistent_auto_add_policy_rejects_new_algorithm_for_known_host(
        self,
    ) -> None:
        paramiko_module = connection._load_paramiko()
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            first_key = paramiko_module.RSAKey.generate(1024)
            alternate_key = paramiko_module.ECDSAKey.generate()

            first_client = paramiko_module.SSHClient()
            policy = connection._PersistentAutoAddPolicy(paramiko_module, known_hosts)
            policy.missing_host_key(first_client, "example.test", first_key)

            changed_client = paramiko_module.SSHClient()
            changed_policy = connection._PersistentAutoAddPolicy(
                paramiko_module,
                known_hosts,
            )
            with self.assertRaises(paramiko_module.BadHostKeyException):
                changed_policy.missing_host_key(
                    changed_client,
                    "example.test",
                    alternate_key,
                )

    def test_connect_client_rejects_unknown_hosts_when_strict(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=True,
        )
        fake_sock = SimpleNamespace(
            close=lambda: None,
            setsockopt=lambda *_args: None,
        )

        with patch_attr(connection.socket, "create_connection", return_value=fake_sock):
            with patch_attr(
                connection, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(FakeSSHClient, "connect", return_value=None):
                    with patch_attr(FakeSSHClient, "get_transport", return_value=None):
                        client = cast(FakeSSHClient, connection.connect_client(args))

        self.assertTrue(client.loaded_system_host_keys)
        self.assertIsInstance(client.missing_host_key_policy, FakeRejectPolicy)

    def test_connect_client_loads_windows_openssh_keys_before_strict_policy(
        self,
    ) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=True,
        )
        fake_sock = SimpleNamespace(
            close=lambda: None,
            setsockopt=lambda *_args: None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            openssh_known_hosts = Path(tmpdir) / "known_hosts"
            openssh_known_hosts.write_text(
                "example.test ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA==\n"
            )
            with patch_attr(
                connection.socket, "create_connection", return_value=fake_sock
            ):
                with patch_attr(
                    connection, "_load_paramiko", return_value=FakeParamikoModule
                ):
                    with patch_attr(
                        connection,
                        "_windows_openssh_known_hosts_path",
                        return_value=openssh_known_hosts,
                    ):
                        with patch_attr(FakeSSHClient, "connect", return_value=None):
                            with patch_attr(
                                FakeSSHClient, "get_transport", return_value=None
                            ):
                                client = cast(
                                    FakeSSHClient, connection.connect_client(args)
                                )
        self.assertIn(str(openssh_known_hosts), client.loaded_host_key_files)
        self.assertIsInstance(client.missing_host_key_policy, FakeRejectPolicy)

    def test_windows_openssh_known_hosts_read_failure_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            known_hosts.write_text("not a valid known_hosts record\n")
            client = FakeSSHClient()
            with patch_attr(
                connection,
                "_windows_openssh_known_hosts_path",
                return_value=known_hosts,
            ):
                with patch_attr(
                    FakeSSHClient,
                    "load_host_keys",
                    side_effect=OSError("denied"),
                ):
                    with self.assertRaisesRegex(
                        ValueError, "Windows OpenSSH known_hosts"
                    ):
                        connection._load_windows_openssh_host_keys(client)

    def test_connect_with_retry_does_not_retry_missing_key_file(self) -> None:
        args = argparse.Namespace()
        with patch_attr(
            connection,
            "connect_client",
            side_effect=FileNotFoundError("missing key"),
        ) as connect_client:
            with patch_attr(execution.time, "sleep") as sleep:
                with self.assertRaises(FileNotFoundError):
                    connection.connect_with_retry(args)

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

        with patch_attr(connection.socket, "create_connection", return_value=fake_sock):
            with patch_attr(
                connection, "_load_paramiko", return_value=FakeParamikoModule
            ):
                with patch_attr(
                    FakeSSHClient, "connect", side_effect=RuntimeError("boom")
                ):
                    with self.assertRaises(RuntimeError):
                        connection.connect_client(args)

        self.assertTrue(
            fake_sock_close_called, "sock should be closed when connect fails"
        )

    def test_connection_error_classifier_matches_shared_categories(self) -> None:
        cases = [
            (
                ValueError("bad config"),
                False,
                contracts.EXIT_CONFIG_ERROR,
                "config_error",
            ),
            (
                FileNotFoundError("missing key"),
                True,
                contracts.EXIT_CONFIG_ERROR,
                "config_error",
            ),
            (
                TimeoutError("slow"),
                True,
                contracts.EXIT_TIMEOUT,
                "connect_timeout",
            ),
            (
                OSError("network down"),
                True,
                contracts.EXIT_NETWORK_ERROR,
                "network_error",
            ),
            (
                OSError("pre-target denied"),
                False,
                contracts.EXIT_CONFIG_ERROR,
                "config_error",
            ),
        ]

        for exc, target_known, expected_code, expected_category in cases:
            with self.subTest(exc=type(exc).__name__, target_known=target_known):
                classified = connection.classify_connection_error(
                    exc,
                    target_known=target_known,
                )

            self.assertEqual(classified.code, expected_code)
            self.assertEqual(classified.category, expected_category)

    def test_connection_error_classifier_detects_paramiko_auth_error(self) -> None:
        with patch_attr(connection, "_load_paramiko", return_value=FakeParamikoModule):
            classified = connection.classify_connection_error(
                FakeAuthenticationException("denied"),
                target_known=True,
            )

        self.assertEqual(classified.code, contracts.EXIT_SSH_ERROR)
        self.assertEqual(classified.category, "auth_error")

    def test_connect_with_retry_skips_permanent_dns_error(self) -> None:
        args = argparse.Namespace()
        permanent_error = socket.gaierror(socket.EAI_NONAME, "name not known")
        with patch_attr(
            connection,
            "connect_client",
            side_effect=permanent_error,
        ) as connect_client:
            with patch_attr(execution.time, "sleep") as sleep:
                with self.assertRaises(socket.gaierror):
                    connection.connect_with_retry(args)

        connect_client.assert_called_once_with(args)
        sleep.assert_not_called()

    def test_connect_with_retry_spaces_transient_retries_with_backoff(self) -> None:
        args = argparse.Namespace()
        refused = ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
        with patch_attr(
            connection,
            "connect_client",
            side_effect=refused,
        ) as connect_client:
            with patch_attr(execution.time, "sleep") as sleep:
                with self.assertRaises(ConnectionRefusedError):
                    connection.connect_with_retry(args)

        self.assertEqual(len(connect_client.calls), 1 + connection.CONNECT_RETRIES)
        self.assertEqual(
            [call.args[0] for call in sleep.calls],
            [0.25, 0.5],
        )

    def test_connect_client_closes_socket_when_client_construction_fails(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=False,
        )
        socket_closed: list[bool] = []
        fake_socket = SimpleNamespace(
            close=lambda: socket_closed.append(True),
            setsockopt=lambda *_args: None,
        )
        broken_paramiko = SimpleNamespace(
            SSHClient=StubCallable(side_effect=RuntimeError("constructor failed"))
        )

        with patch_attr(
            connection.socket, "create_connection", return_value=fake_socket
        ):
            with patch_attr(connection, "_load_paramiko", return_value=broken_paramiko):
                with self.assertRaisesRegex(RuntimeError, "constructor failed"):
                    connection.connect_client(args)

        self.assertTrue(socket_closed)


if __name__ == "__main__":
    unittest.main()
