"""SSH connection lifecycle, host trust, retries and error classification."""

from __future__ import annotations

import argparse
import errno
import os
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

    def test_persistent_policy_uses_standard_known_hosts_name_for_custom_port(
        self,
    ) -> None:
        paramiko_module = connection._load_paramiko()
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            key = paramiko_module.Ed25519Key(
                data=bytes.fromhex("0000000b7373682d6564323535313900000020" + "01" * 32)
            )
            client = paramiko_module.SSHClient()
            policy = connection._PersistentAutoAddPolicy(
                paramiko_module,
                known_hosts,
                connect_port=2222,
            )
            policy.missing_host_key(client, "[example.test]:2222", key)
            self.assertIsNotNone(client.get_host_keys().lookup("[example.test]:2222"))
            self.assertIsNone(client.get_host_keys().lookup("example.test"))

            # The standard bracketed form is what Paramiko's real SSHClient
            # uses for a custom port. A second process must reuse that key.
            reloaded = paramiko_module.SSHClient()
            reloaded.load_host_keys(str(known_hosts))
            found = reloaded.get_host_keys().lookup("[example.test]:2222")
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found["ssh-ed25519"], key)
            if os.name != "nt":
                self.assertEqual(known_hosts.stat().st_mode & 0o777, 0o600)

    def test_persistent_policy_accepts_legacy_unqualified_key_but_adds_port(
        self,
    ) -> None:
        paramiko_module = connection._load_paramiko()
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            old_key = paramiko_module.Ed25519Key(
                data=bytes.fromhex("0000000b7373682d6564323535313900000020" + "01" * 32)
            )
            changed_key = paramiko_module.Ed25519Key(
                data=bytes.fromhex("0000000b7373682d6564323535313900000020" + "02" * 32)
            )
            legacy = paramiko_module.SSHClient()
            legacy.get_host_keys().add("example.test", "ssh-ed25519", old_key)
            legacy.save_host_keys(str(known_hosts))

            accepted = paramiko_module.SSHClient()
            policy = connection._PersistentAutoAddPolicy(
                paramiko_module,
                known_hosts,
                connect_port=2222,
            )
            policy.missing_host_key(accepted, "example.test", old_key)

            rejected = paramiko_module.SSHClient()
            with self.assertRaises(paramiko_module.BadHostKeyException):
                policy.missing_host_key(rejected, "example.test", changed_key)

    def test_persistent_policy_new_port_uses_port_scoped_hostname(self) -> None:
        paramiko_module = connection._load_paramiko()
        with tempfile.TemporaryDirectory() as tmpdir:
            known_hosts = Path(tmpdir) / "known_hosts"
            key = paramiko_module.Ed25519Key(
                data=bytes.fromhex("0000000b7373682d6564323535313900000020" + "01" * 32)
            )
            client = paramiko_module.SSHClient()
            client.get_host_keys().add("example.test", "ssh-ed25519", key)
            policy = connection._PersistentAutoAddPolicy(
                paramiko_module,
                known_hosts,
                connect_port=2222,
            )
            policy.missing_host_key(client, "example.test", key)
            self.assertIn("example.test", client.get_host_keys())

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

    def test_invalid_trust_store_or_key_fails_before_network(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            user="root",
            password="test",
            key=None,
            allow_agent=False,
            strict_host_key_checking=True,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            known_hosts = root / "known_hosts"
            known_hosts.write_text(
                "example.test ssh-ed25519 not-a-public-key\n",
                encoding="utf-8",
            )
            with (
                patch_attr(
                    connection, "_user_known_hosts_path", return_value=known_hosts
                ),
                patch_attr(
                    connection, "_windows_openssh_known_hosts_path", return_value=None
                ),
                patch_attr(connection.socket, "create_connection") as connect,
            ):
                with self.assertRaisesRegex(ValueError, "malformed entry"):
                    connection.connect_client(args)
            connect.assert_not_called()

            key = root / "id_ed25519"
            key.write_text("key bytes only for a shape test\n", encoding="utf-8")
            if os.name != "nt":
                key.chmod(0o600)
            args.key = str(key)
            link = root / "key-link"
            if os.name != "nt":
                link.symlink_to(key)
                args.key = str(link)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    connection.connect_client(args)
            args.key = str(key)
            oversized = root / "oversized-key"
            oversized.write_bytes(b"x" * (connection._MAX_SSH_KEY_BYTES + 1))
            args.key = str(oversized)
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                connection.connect_client(args)

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


class TrustStoreShapeTests(unittest.TestCase):
    """Malformed local trust stores must fail before any socket is opened."""

    def _args(self, **overrides: object) -> SimpleNamespace:
        base: dict[str, object] = {
            "host": "127.0.0.1",
            "port": 22,
            "user": "root",
            "password": "test",
            "key": None,
            "allow_agent": False,
            "strict_host_key_checking": False,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_a_symlinked_known_hosts_is_refused(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX symlink creation only")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "real"
            target.write_text("", encoding="utf-8")
            link = Path(directory) / "known_hosts"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                connection._validate_known_hosts_file(link, "Launcher known_hosts")

    def test_a_directory_where_a_key_file_belongs_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not a file"):
                connection._validate_known_hosts_file(
                    Path(directory), "Launcher known_hosts"
                )

    def test_an_oversized_known_hosts_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            path.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "16 MiB"):
                connection._validate_known_hosts_file(path, "Launcher known_hosts")

    def test_a_missing_known_hosts_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(
                connection._validate_known_hosts_file(
                    Path(directory) / "absent", "Launcher known_hosts"
                )
            )

    def test_a_malformed_known_hosts_stops_the_connect_before_the_socket(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            path.write_text("this is not a known_hosts record\n", encoding="utf-8")
            with (
                patch_attr(connection, "_user_known_hosts_path", return_value=path),
                patch_attr(connection.socket, "create_connection") as create_socket,
            ):
                with self.assertRaisesRegex(ValueError, "malformed"):
                    connection.connect_client(self._args())
        create_socket.assert_not_called()


class EndpointAndKeyTests(unittest.TestCase):
    def _args(self, **overrides: object) -> SimpleNamespace:
        base: dict[str, object] = {
            "host": "127.0.0.1",
            "port": 22,
            "user": "root",
            "password": "test",
            "key": None,
            "allow_agent": False,
            "strict_host_key_checking": False,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_a_blank_host_is_a_configuration_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing host or user"):
            connection._connection_endpoint(self._args(host="   "))

    def test_the_stripped_endpoint_is_what_gets_used(self) -> None:
        host, user, port = connection._connection_endpoint(
            self._args(host=" 10.0.0.1 ", user=" root ", port=None)
        )
        self.assertEqual((host, user, port), ("10.0.0.1", "root", 22))

    def test_no_auth_method_is_refused_before_connecting(self) -> None:
        with patch_attr(connection.socket, "create_connection") as create_socket:
            with self.assertRaisesRegex(ValueError, "No auth method"):
                connection.connect_client(
                    self._args(password=None, key=None, allow_agent=False)
                )
        create_socket.assert_not_called()

    def test_an_unreadable_key_path_reports_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(b"k" * 8)
            with patch_attr(Path, "stat", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, "Unable to inspect"):
                    connection._key_path_from_arg(str(path))

    def test_an_oversized_key_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(b"k" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                connection._key_path_from_arg(str(path))

    def test_a_zero_byte_key_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "1 byte"):
                connection._key_path_from_arg(str(path))

    def test_a_key_file_with_broad_local_permissions_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_bytes(b"k" * 8)
            if os.name == "nt":
                with patch_attr(
                    connection.windows_acl,
                    "_ensure_windows_private_dacl",
                    side_effect=ValueError("foreign access"),
                ):
                    with self.assertRaisesRegex(ValueError, "must be private"):
                        connection._key_path_from_arg(str(path))
            else:
                path.chmod(0o644)
                with self.assertRaisesRegex(ValueError, "accessible to other users"):
                    connection._key_path_from_arg(str(path))

    def test_a_directory_as_key_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                connection._key_path_from_arg(directory)

    def test_the_key_path_is_passed_to_paramiko_instead_of_the_password(
        self,
    ) -> None:
        recorded: dict[str, object] = {}

        def fake_connect(**kwargs: object) -> None:
            recorded.update(kwargs)

        fake_sock = SimpleNamespace(close=lambda: None, setsockopt=lambda *a: None)
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "id_ed25519"
            key.write_bytes(b"k" * 8)
            if os.name != "nt":
                key.chmod(0o600)
            with (
                patch_attr(
                    connection,
                    "_user_known_hosts_path",
                    return_value=Path(directory) / "known_hosts",
                ),
                patch_attr(
                    connection.socket, "create_connection", return_value=fake_sock
                ),
                patch_attr(
                    connection, "_load_paramiko", return_value=FakeParamikoModule
                ),
                patch_attr(FakeSSHClient, "connect", side_effect=fake_connect),
                patch_attr(FakeSSHClient, "get_transport", return_value=None),
            ):
                connection.connect_client(
                    self._args(password=None, key=str(key), allow_agent=False)
                )
        self.assertEqual(recorded.get("key_filename"), str(key))
        self.assertNotIn("password", recorded)


class RetryClassificationTests(unittest.TestCase):
    def test_a_timeout_is_retryable(self) -> None:
        self.assertTrue(connection._is_retryable_connection_error(TimeoutError()))

    def test_a_dns_temporary_failure_is_retryable_but_not_permanent(self) -> None:
        transient = socket.gaierror(socket.EAI_AGAIN, "temporarily unavailable")
        permanent = socket.gaierror(socket.EAI_NONAME, "unknown host")
        self.assertTrue(connection._is_retryable_connection_error(transient))
        self.assertFalse(connection._is_retryable_connection_error(permanent))

    def test_a_windows_error_code_is_read_when_errno_is_missing(self) -> None:
        exc = OSError("closing")
        exc.errno = None
        # OSError.winerror only exists on Windows, so a direct attribute
        # assignment fails mypy on the Linux static leg. setattr() is the one
        # form both checkers accept; B010 is about constant-value setattr in
        # production code, which this deliberately is not.
        setattr(exc, "winerror", 10054)  # noqa: B010  # WSAECONNRESET
        self.assertTrue(connection._is_retryable_connection_error(exc))

    def test_an_unlisted_error_code_is_not_retried(self) -> None:
        self.assertFalse(
            connection._is_retryable_connection_error(OSError(errno.EACCES, "denied"))
        )

    def test_the_retry_loop_exhausts_into_the_final_error(self) -> None:
        calls: list[int] = []

        def always_fail(_args: object) -> object:
            calls.append(1)
            raise TimeoutError("still down")

        with (
            patch_attr(connection, "connect_client", side_effect=always_fail),
            patch_attr(connection.time, "sleep", return_value=None),
        ):
            with self.assertRaises(TimeoutError):
                connection.connect_with_retry(SimpleNamespace())
        # One initial attempt plus CONNECT_RETRIES retries.
        self.assertEqual(len(calls), 1 + contracts.CONNECT_RETRIES)

    def test_a_missing_key_file_is_never_retried(self) -> None:
        calls: list[int] = []

        def missing(_args: object) -> object:
            calls.append(1)
            raise FileNotFoundError("no key")

        with patch_attr(connection, "connect_client", side_effect=missing):
            with self.assertRaises(FileNotFoundError):
                connection.connect_with_retry(SimpleNamespace())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
