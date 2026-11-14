"""Read-only, pinned check for the reviewed frps/ftp BWG relay.

The caller is the existing launcher. This script never changes either host.
It does not read environment variables, credential files, or arbitrary paths.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

EXPECTED_FRP_VERSION = "0.72.0"
PROTECTED_PATH = "/opt/frp-bwg/frpc.toml"


class RelayCheckError(RuntimeError):
    """A fixed, redacted relay check failure."""


def _parse_proxy(text: str) -> dict[str, str]:
    lines = text.splitlines()
    roots = [
        line.strip() for line in lines if line and not line.startswith((" ", "\t"))
    ]
    expected_roots = {
        'serverAddr = "8.163.41.198"',
        "serverPort = 27000",
        'auth.method = "token"',
        "transport.tls.enable = true",
        "loginFailExit = false",
        "[[proxies]]",
    }
    if not expected_roots.issubset(roots):
        raise RelayCheckError("FRP_CONFIG_ROOT=UNEXPECTED")
    tokens = [line for line in lines if line.startswith("auth.token = ")]
    if len(tokens) != 1:
        raise RelayCheckError("FRP_CONFIG_TOKEN_SHAPE=UNEXPECTED")
    token_value = tokens[0].removeprefix("auth.token = ").strip()
    if not re.fullmatch(r'"[^"\r\n]+"', token_value):
        raise RelayCheckError("FRP_CONFIG_TOKEN_SHAPE=UNEXPECTED")
    proxy = {}
    for line in lines:
        for key in ("name", "type", "localIP", "localPort", "remotePort"):
            prefix = f"{key} = "
            if line.startswith(prefix):
                proxy[key] = line.removeprefix(prefix).strip().strip('"')
    expected_proxy = {
        "name": "relay-52443",
        "type": "tcp",
        "localIP": "127.0.0.1",
        "localPort": "443",
        "remotePort": "52443",
    }
    if proxy != expected_proxy:
        raise RelayCheckError("FRP_PROXY_SCOPE=UNEXPECTED")
    return proxy


def _count_established(run: Any, peer: str) -> int:
    # `grep -c` exits 1 on "no match", which is a legitimate count of zero, so
    # count in Python from the raw listing instead of depending on grep status.
    listing = run("ss -Htn state established")
    return sum(1 for line in listing.splitlines() if line.rstrip().endswith(peer))


def _count_listening(run: Any, port: str) -> int:
    listing = run("ss -Hltn")
    return sum(
        1
        for line in listing.splitlines()
        if len(line.split()) > 3 and line.split()[3].endswith(f":{port}")
    )


def _recent_log_summary(run: Any) -> str:
    """Return the count, or an explicit unavailable marker.

    ``journalctl`` missing, an unknown unit and an empty window must not print
    the same "0".
    """
    try:
        log = run(
            "journalctl -u frps -n 30 --no-pager -o cat "
            "| grep -E 'login|proxy' | tail -4"
        )
    except RelayCheckError as exc:
        return f"UNAVAILABLE:{exc}"
    lines = len(log.splitlines())
    return f"LINES={lines}"


def check_bwg(client: Any) -> None:
    def run(command: str) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=30)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RelayCheckError(f"BWG_REMOTE_CHECK=FAILED_EXIT_{exit_code}")
        return str((stdout.read() + stderr.read()).decode("utf-8", "replace"))

    active = run("systemctl is-active frpc-relay.service").strip()
    control = _count_established(run, "8.163.41.198:27000")
    reality = _count_listening(run, "443")
    print(f"BWG_FRPC_RELAY_ACTIVE={active}")
    print(f"BWG_FRPS_CONTROL_ESTABLISHED={control}")
    print(f"BWG_LOCAL_REALITY_LISTENER={reality}")
    # ">= 1", not "== 1": a retried connection legitimately overlaps for a few
    # seconds, and "grep -c ... || true" used to report a failed ss and a real
    # zero identically. A zero is the only fatal condition.
    if active != "active" or control < 1 or reality < 1:
        raise RelayCheckError("BWG_RELAY_HEALTH=FAILED")
    version = run("/opt/frp-bwg/frpc --version").strip()
    print(f"BWG_FRPC_VERSION={version}")
    if EXPECTED_FRP_VERSION not in version:
        raise RelayCheckError("BWG_FRPC_VERSION=UNEXPECTED")
    digest = run(f"sha256sum {PROTECTED_PATH} | cut -d' ' -f1").strip()
    print(f"BWG_CONFIG_SHA256={digest}")
    print("BWG_CONFIG_MODE=" + run(f"stat -c '%a:%U:%G' {PROTECTED_PATH}").strip())
    print(
        "BWG_FRPC_BINARY_SHA256="
        + run("sha256sum /opt/frp-bwg/frpc | cut -d' ' -f1").strip()
    )
    restart = run("systemctl show frpc-relay.service -p Restart --value").strip()
    restart_sec = run("systemctl show frpc-relay.service -p RestartSec --value").strip()
    enabled = run("systemctl is-enabled frpc-relay.service").strip()
    print(f"BWG_FRPC_RESTART={restart}")
    print(f"BWG_FRPC_RESTART_SEC={restart_sec}")
    print(f"BWG_FRPC_ENABLED={enabled}")


def check_aliyun(client: Any) -> None:
    def run(command: str) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=30)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RelayCheckError(f"ALIYUN_REMOTE_CHECK=FAILED_EXIT_{exit_code}")
        return str((stdout.read() + stderr.read()).decode("utf-8", "replace"))

    active = run("systemctl is-active frps.service").strip()
    print(f"ALIYUN_FRPS_ACTIVE={active}")
    listeners = run("ss -Hltn | awk '$4 ~ /:(27000|52443)$/ {print $4}'").splitlines()
    print(f"ALIYUN_EXPECTED_LISTENERS={len(listeners)}")
    if len(listeners) != 2:
        raise RelayCheckError("ALIYUN_LISTENER_SCOPE=UNEXPECTED")
    if active != "active":
        raise RelayCheckError("ALIYUN_FRPS_HEALTH=FAILED")
    version = run("/opt/frp/frps --version").strip()
    restart = run("systemctl show frps.service -p Restart --value").strip()
    restart_sec = run("systemctl show frps.service -p RestartSec --value").strip()
    enabled = run("systemctl is-enabled frps.service").strip()
    digest = run("sha256sum /opt/frp/frps.toml | cut -d' ' -f1").strip()
    control = _count_established(run, ":27000")
    print(f"ALIYUN_FRPS_VERSION={version}")
    print(f"ALIYUN_FRPS_RESTART={restart}")
    print(f"ALIYUN_FRPS_RESTART_SEC={restart_sec}")
    print(f"ALIYUN_FRPS_ENABLED={enabled}")
    print(f"ALIYUN_CONFIG_SHA256={digest}")
    print(f"ALIYUN_CONTROL_ESTABLISHED={control}")
    # Report failure and emptiness differently. With "|| true" a missing
    # journalctl produced ALIYUN_FRP_LOG_LINES=0, which reads exactly like
    # "the window had no login/proxy lines" and hid the real problem.
    print("ALIYUN_FRP_LOG=" + _recent_log_summary(run))


def config_contract(path: Path | None = None) -> dict[str, str]:
    """Validate the pinned frpc.toml shape; return the reviewed proxy mapping.

    The previous implementation printed PASS unconditionally, so a token-shape
    drift or a widened proxy scope (a second remotePort, a non-loopback
    landing) still reported a green contract. The parser existed and was only
    exercised by its unit test, never by the entry point.
    """
    target = path or Path(PROTECTED_PATH)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RelayCheckError("FRP_CONFIG=UNREADABLE") from exc
    proxy = _parse_proxy(text)
    return proxy


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "config-contract":
        try:
            config_contract()
        except RelayCheckError as exc:
            print(f"FRP_CONFIG_CONTRACT=FAIL:{exc}")
            raise SystemExit(1) from None
        print("FRP_CONFIG_CONTRACT=PASS")
        return
    if mode in {"bwg", "aliyun"}:
        from scripts._remote_ops import (  # type: ignore[import-not-found]
            connect_ssh,
            describe_ssh_connection_error,
            load_target_profile,
        )

        target = Path(tempfile.gettempdir()) / "frp-relay-review-target.json"
        if not target.is_file():
            raise RelayCheckError("PRIVATE_RELAY_TARGET=MISSING")
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            source = json.loads(raw["target"].read_text(encoding="utf-8"))
            selected = source["profiles"][mode]
            target.write_text(
                json.dumps({"profiles": {mode: selected}}), encoding="utf-8"
            )
            profile = load_target_profile(target, mode)
            client = connect_ssh(profile, require_host_key_pin=True)
        except (OSError, ValueError, KeyError) as exc:
            raise RelayCheckError("PRIVATE_RELAY_TARGET=INVALID") from exc
        except Exception as exc:
            reason = describe_ssh_connection_error(exc)
            raise RelayCheckError(f"RELAY_HOST_CONNECTION=FAILED:{reason}") from exc
        try:
            if mode == "bwg":
                check_bwg(client)
            else:
                check_aliyun(client)
        finally:
            client.close()
        return
    raise SystemExit("usage: frp_bwg_relay.py config-contract|bwg|aliyun")


if __name__ == "__main__":
    main()
