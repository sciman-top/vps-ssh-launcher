"""Profile-level SOCKS5 tunnel: config resolution, validation and handshake."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import unittest
import unittest.mock
from types import SimpleNamespace
from typing import Any

from vps_ssh_launcher import connection
from vps_ssh_launcher import config as target_config


def _recv_exact(conn: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            break
        data += chunk
    return data


class FakeSocks5Server:
    """Minimal SOCKS5 no-auth server recording the CONNECT request."""

    def __init__(
        self,
        reply_code: int = 0,
        greeting: bytes = b"\x05\x00",
        reply_tail: bytes | None = None,
        trailing: bytes = b"",
    ) -> None:
        self.reply_code = reply_code
        self.greeting = greeting
        self.reply_tail = reply_tail
        self.trailing = trailing
        self.requests: list[bytes] = []
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port: int = self._socket.getsockname()[1]
        self._thread: threading.Thread | None = None

    def serve_one(self) -> None:
        conn, _ = self._socket.accept()
        with conn:
            try:
                self._serve(conn)
            except OSError:
                pass  # client gave up mid-handshake; nothing left to assert

    def _serve(self, conn: socket.socket) -> None:
        _recv_exact(conn, 3)  # VER NMETHODS METHODS
        conn.sendall(self.greeting)
        request = _recv_exact(conn, 4)
        if len(request) < 4:
            return
        atyp = request[3]
        addr_len = {0x01: 4, 0x03: None, 0x04: 16}[atyp]
        if addr_len is None:
            addr_len = _recv_exact(conn, 1)[0]
            request += bytes([addr_len])
        request += _recv_exact(conn, addr_len + 2)
        self.requests.append(request)
        # VER REP RSV ATYP BND.ADDR BND.PORT (default: 0.0.0.0:0 IPv4)
        tail = self.reply_tail
        if tail is None:
            tail = b"\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00"
        conn.sendall(b"\x05" + bytes([self.reply_code]) + tail + self.trailing)

    def __enter__(self) -> "FakeSocks5Server":
        self._thread = threading.Thread(target=self.serve_one, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._socket.close()


class ResolveSocks5ProxyTests(unittest.TestCase):
    def test_absent_field_returns_none(self) -> None:
        self.assertIsNone(target_config.resolve_socks5_proxy({}, profile_name="p"))

    def test_string_host_port_pair(self) -> None:
        entry = {"socks5": "127.0.0.1:10808"}
        self.assertEqual(
            target_config.resolve_socks5_proxy(entry, profile_name="p"),
            ("127.0.0.1", 10808),
        )

    def test_bare_host_defaults_to_1080(self) -> None:
        entry = {"socks5": "proxy.local"}
        self.assertEqual(
            target_config.resolve_socks5_proxy(entry, profile_name="p"),
            ("proxy.local", 1080),
        )

    def test_object_form_with_explicit_port(self) -> None:
        entry = {"socks5": {"host": "127.0.0.1", "port": "2080"}}
        self.assertEqual(
            target_config.resolve_socks5_proxy(entry, profile_name="p"),
            ("127.0.0.1", 2080),
        )

    def test_object_form_defaults_to_1080(self) -> None:
        entry = {"socks5": {"host": "proxy.local"}}
        self.assertEqual(
            target_config.resolve_socks5_proxy(entry, profile_name="p"),
            ("proxy.local", 1080),
        )

    def test_empty_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy({"socks5": "  "}, profile_name="p")

    def test_empty_host_in_string_pair_rejected(self) -> None:
        # ":1080" must not silently resolve to the loopback default host.
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy({"socks5": ":1080"}, profile_name="p")

    def test_bad_port_rejected(self) -> None:
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy(
                {"socks5": "127.0.0.1:99999"}, profile_name="p"
            )

    def test_ipv6_string_form_rejected(self) -> None:
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy({"socks5": "::1:1080"}, profile_name="p")

    def test_object_without_host_rejected(self) -> None:
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy(
                {"socks5": {"port": 1080}}, profile_name="p"
            )

    def test_unsupported_type_rejected(self) -> None:
        with self.assertRaises(ValueError):
            target_config.resolve_socks5_proxy({"socks5": 1080}, profile_name="p")

    def test_validate_profile_accepts_proxy_forms(self) -> None:
        target_config.validate_profile(
            {"host": "h", "user": "u", "password": "p", "socks5": "127.0.0.1:10808"},
            "p",
        )
        target_config.validate_profile(
            {
                "host": "h",
                "user": "u",
                "password": "p",
                "socks5": {"host": "127.0.0.1", "port": 10808},
            },
            "p",
        )

    def test_validate_profile_rejects_invalid_proxy(self) -> None:
        with self.assertRaises(ValueError):
            target_config.validate_profile(
                {"host": "h", "user": "u", "password": "p", "socks5": "127.0.0.1:x"},
                "p",
            )


class NamespaceCarryingTests(unittest.TestCase):
    def test_build_connect_namespace_carries_proxy(self) -> None:
        entry = {
            "host": "203.0.113.10",
            "port": 29712,
            "user": "root",
            "password": "pw",
            "socks5": "127.0.0.1:10808",
        }
        ns = target_config.build_connect_namespace(
            entry,
            profile_name="p",
            base_args=argparse.Namespace(password=None, key=None, allow_agent=False),
            config_dir=target_config.SOURCE_ROOT,
            strict_host_key_checking=True,
        )
        self.assertEqual(ns.socks5, ("127.0.0.1", 10808))

    def test_build_connect_namespace_defaults_to_no_proxy(self) -> None:
        entry = {
            "host": "203.0.113.10",
            "port": 22,
            "user": "root",
            "password": "pw",
        }
        ns = target_config.build_connect_namespace(
            entry,
            profile_name="p",
            base_args=argparse.Namespace(password=None, key=None, allow_agent=False),
            config_dir=target_config.SOURCE_ROOT,
            strict_host_key_checking=True,
        )
        self.assertIsNone(ns.socks5)

    def test_apply_config_sets_proxy_from_profile(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "target.json"
            config_path.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "proxied": {
                                "host": "203.0.113.10",
                                "user": "root",
                                "password": "pw",
                                "socks5": "127.0.0.1:10808",
                            }
                        },
                        "default": "proxied",
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                host=None,
                port=None,
                user=None,
                config=str(config_path),
                profile=None,
                password=None,
                key=None,
                allow_agent=False,
            )
            target_config.apply_config(args)
            self.assertEqual(args.socks5, ("127.0.0.1", 10808))


class Socks5TunnelTests(unittest.TestCase):
    def test_tunnel_success_sends_connect_request(self) -> None:
        with FakeSocks5Server() as server:
            sock = connection._socks5_tunnel(
                ("127.0.0.1", server.port), "203.0.113.10", 29712, 5.0
            )
            try:
                self.assertIsInstance(sock, socket.socket)
            finally:
                sock.close()
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual(request[0], 0x05)  # VER
        self.assertEqual(request[1], 0x01)  # CMD=CONNECT
        self.assertEqual(request[2], 0x00)  # RSV
        self.assertEqual(request[3], 0x01)  # ATYP=IPv4
        self.assertEqual(request[4:8], socket.inet_aton("203.0.113.10"))
        self.assertEqual(request[8:10], (29712).to_bytes(2, "big"))

    def test_tunnel_supports_domain_names(self) -> None:
        with FakeSocks5Server() as server:
            sock = connection._socks5_tunnel(
                ("127.0.0.1", server.port), "ssh.example.com", 22, 5.0
            )
            sock.close()
        request = server.requests[0]
        self.assertEqual(request[3], 0x03)  # ATYP=domain
        self.assertEqual(request[4], len("ssh.example.com"))
        self.assertEqual(request[5 : 5 + len("ssh.example.com")], b"ssh.example.com")

    def test_tunnel_raises_oserror_on_proxy_refusal(self) -> None:
        with FakeSocks5Server(reply_code=5) as server:
            with self.assertRaises(OSError):
                connection._socks5_tunnel(
                    ("127.0.0.1", server.port), "203.0.113.10", 22, 5.0
                )

    def test_tunnel_raises_oserror_on_bad_greeting(self) -> None:
        with FakeSocks5Server(greeting=b"\x05\x02") as server:
            with self.assertRaises(OSError):
                connection._socks5_tunnel(
                    ("127.0.0.1", server.port), "203.0.113.10", 22, 5.0
                )

    def test_unreachable_proxy_raises_oserror(self) -> None:
        with self.assertRaises(OSError):
            connection._socks5_tunnel(("127.0.0.1", 1), "203.0.113.10", 22, 2.0)

    def test_tunnel_drains_variable_length_domain_bound_address(self) -> None:
        bnd = bytes([len("bound.example")]) + b"bound.example" + b"\x16\x2e"
        banner = b"SSH-2.0-fake\r\n"
        with FakeSocks5Server(reply_tail=b"\x00\x03" + bnd, trailing=banner) as server:
            sock = connection._socks5_tunnel(
                ("127.0.0.1", server.port), "203.0.113.10", 29712, 5.0
            )
            try:
                # The stream must come back aligned: the first SSH-facing
                # bytes are exactly the next payload, with no BND.ADDR residue.
                self.assertEqual(_recv_exact(sock, len(banner)), banner)
            finally:
                sock.close()

    def test_tunnel_reports_truncated_domain_bound_address(self) -> None:
        # A length-prefixed domain BND.ADDR cut short must surface as an
        # OSError, not as a misaligned stream handed to paramiko.
        bnd = bytes([len("bound.example")]) + b"bound.example" + b"\x16\x2e"
        with FakeSocks5Server(reply_tail=b"\x00\x03" + bnd[:6]) as server:
            with self.assertRaises(OSError):
                connection._socks5_tunnel(
                    ("127.0.0.1", server.port), "203.0.113.10", 22, 5.0
                )

    def test_tunnel_host_encoding_failure_raises_oserror(self) -> None:
        # "label..invalid" trips the idna codec (UnicodeError); the tunnel
        # must translate that into OSError for the retry/classification paths.
        with FakeSocks5Server() as server:
            with self.assertRaises(OSError):
                connection._socks5_tunnel(
                    ("127.0.0.1", server.port), "label..invalid", 22, 5.0
                )

    def test_tunnel_rejects_domain_name_over_255_encoded_bytes(self) -> None:
        # Each DNS label fits, but the full IDNA name cannot fit the SOCKS5
        # one-byte domain-length field.
        host = ".".join(["a" * 63] * 4 + ["a"])
        with FakeSocks5Server() as server:
            with self.assertRaisesRegex(OSError, "255 bytes"):
                connection._socks5_tunnel(("127.0.0.1", server.port), host, 22, 5.0)


class ConnectClientProxyDispatchTests(unittest.TestCase):
    def test_connect_client_uses_tunnel_when_proxy_configured(self) -> None:
        tunnel_calls: list[tuple[Any, ...]] = []

        def fake_tunnel(
            proxy: tuple[str, int], host: str, port: int, timeout: float
        ) -> socket.socket:
            tunnel_calls.append((proxy, host, port, timeout))
            raise OSError("stop before SSH handshake")

        args = SimpleNamespace(
            host="203.0.113.10",
            port=29712,
            user="root",
            password="pw",
            key=None,
            allow_agent=False,
            strict_host_key_checking=True,
            socks5=("127.0.0.1", 10808),
        )
        with unittest.mock.patch.object(
            connection, "_socks5_tunnel", side_effect=fake_tunnel
        ):
            with self.assertRaises(OSError):
                connection.connect_client(args)
        self.assertEqual(
            tunnel_calls,
            [(("127.0.0.1", 10808), "203.0.113.10", 29712, connection.CONNECT_TIMEOUT)],
        )

    def test_connect_client_without_proxy_keeps_direct_socket(self) -> None:
        args = SimpleNamespace(
            host="203.0.113.10",
            port=22,
            user="root",
            password="pw",
            key=None,
            allow_agent=False,
            strict_host_key_checking=True,
            socks5=None,
        )
        with unittest.mock.patch.object(
            connection.socket, "create_connection", side_effect=OSError("stop")
        ) as create_connection:
            with self.assertRaises(OSError):
                connection.connect_client(args)
        create_connection.assert_called_once_with(
            ("203.0.113.10", 22), timeout=connection.CONNECT_TIMEOUT
        )


if __name__ == "__main__":
    unittest.main()
