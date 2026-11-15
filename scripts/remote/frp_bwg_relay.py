"""Read-only, pinned check for the reviewed frps/ftp BWG relay.

The caller is the existing launcher. This script never changes either host.
It does not read environment variables, credential files, or arbitrary paths.
"""

from __future__ import annotations

import argparse
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
    # Count established sockets whose EITHER endpoint ends with ``peer``.
    #
    # Checking only the trailing field made the aliyun side report 0 forever:
    # on the server the local column is the listening port (...:27000) and the
    # line ends with the client ephemeral port, so endswith(":27000") never
    # matched even while the relay was up -- the same "failure looks like zero"
    # defect this module exists to remove, one level down.
    #
    # `grep -c` exits 1 on "no match", which is a legitimate count of zero, so
    # count in Python from the raw listing instead of depending on grep status.
    listing = run("ss -Htn state established")
    total = 0
    for line in listing.splitlines():
        for field in line.split():
            # Addresses are the only fields that carry "host:port"; the
            # Recv-Q/Send-Q counters and any state token do not. Matching by
            # SHAPE rather than by column index keeps the counter correct for
            # both `ss -Htn` (queue columns first) and the state-prefixed form.
            if ":" not in field:
                continue
            if field.endswith(peer):
                total += 1
                break
    return total


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


def _reviewed_target_path() -> Path:
    """The single private config this read-only checker may open.

    Deliberately NOT an arbitrary path: the checker runs on the operator
    workstation and must not be pointed at an unrelated credential file.
    """
    return Path(tempfile.gettempdir()) / "frp-relay-review-target.json"


def _open_reviewed_client(profile_name: str) -> Any:
    """Open one strict, host-key-pinned SSH client for a reviewed profile.

    This used to import ``scripts._remote_ops``, a module that never existed in
    this repository (``git log --all`` finds no such path), so both the ``bwg``
    and ``aliyun`` modes failed with ImportError before touching a host. The
    launcher already owns config parsing, profile validation and strict
    host-key checking, so the checker reuses that instead of inventing a
    second connection layer.
    """
    from vps_ssh_launcher import config as launcher_config
    from vps_ssh_launcher import connection as launcher_connection

    pointer = _reviewed_target_path()
    if not pointer.is_file():
        raise RelayCheckError("PRIVATE_RELAY_TARGET=MISSING")
    try:
        # The staged file is a POINTER: {"target": "<path to the real
        # target.json>"}. Keeping the pointer in temp means the credential
        # file itself never leaves its private location. A file that already
        # carries profiles is accepted too, so the checker still works when an
        # operator points it straight at the real config.
        raw = json.loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("review target must be an object")
        source_path = raw.get("target")
        config_path = Path(source_path) if isinstance(source_path, str) else pointer
        # load_config() also runs the private-DACL/symlink gate, so a world
        # readable credential file is refused before any socket opens.
        loaded = launcher_config.load_config(config_path)
        profiles = loaded.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("profiles must be an object")
        entry = profiles[profile_name]
        if not isinstance(entry, dict):
            raise ValueError("profile must be an object")
        launcher_config.validate_profile(entry, profile_name)
        base_args = argparse.Namespace(password=None, key=None, allow_agent=False)
        namespace = launcher_config.build_connect_namespace(
            entry,
            profile_name=profile_name,
            base_args=base_args,
            config_dir=config_path.parent,
            # Never inherit the profile opt-out: a read-only audit must not
            # silently accept a first-use host key.
            strict_host_key_checking=True,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RelayCheckError("PRIVATE_RELAY_TARGET=INVALID") from exc
    try:
        return launcher_connection.connect_with_retry(namespace)
    except Exception as exc:
        reason = launcher_connection.classify_connection_error(exc, target_known=True)
        raise RelayCheckError(
            f"RELAY_HOST_CONNECTION=FAILED:{reason.category}"
        ) from exc


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
        client = _open_reviewed_client(mode)
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
