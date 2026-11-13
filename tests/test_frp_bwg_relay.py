"""Focused contract checks for the reviewed BWG-hosted frp relay."""

from __future__ import annotations

import pytest

from scripts.remote import frp_bwg_relay


def _valid_config() -> str:
    return (
        'serverAddr = "8.163.41.198"\n'
        "serverPort = 27000\n"
        'auth.method = "token"\n'
        'auth.token = "fixture-value-not-live"\n'
        "transport.tls.enable = true\n"
        "loginFailExit = false\n"
        "\n[[proxies]]\n"
        'name = "relay-52443"\n'
        'type = "tcp"\n'
        'localIP = "127.0.0.1"\n'
        "localPort = 443\n"
        "remotePort = 52443\n"
    )


def test_reviewed_config_is_one_exact_tcp_mapping() -> None:
    assert frp_bwg_relay._parse_proxy(_valid_config()) == {  # noqa: SLF001
        "name": "relay-52443",
        "type": "tcp",
        "localIP": "127.0.0.1",
        "localPort": "443",
        "remotePort": "52443",
    }


def test_nonloopback_landing_is_rejected() -> None:
    with pytest.raises(frp_bwg_relay.RelayCheckError):
        frp_bwg_relay._parse_proxy(  # noqa: SLF001
            _valid_config().replace(
                'localIP = "127.0.0.1"', 'localIP = "144.34.229.116"'
            )
        )


def test_health_check_fails_closed_on_inactive_service() -> None:
    class Branch:
        def recv_exit_status(self) -> int:
            return 0

    class Stream:
        channel = Branch()

        def read(self) -> bytes:
            return b""

    class Stdout(Stream):
        def read(self) -> bytes:
            return b"inactive\n"

    class Client:
        def exec_command(
            self, command: str, timeout: int
        ) -> tuple[object, Stream, object]:
            del command, timeout
            return object(), Stdout(), Stream()

    with pytest.raises(frp_bwg_relay.RelayCheckError):
        frp_bwg_relay.check_bwg(Client())
