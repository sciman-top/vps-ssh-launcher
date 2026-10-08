"""SSH connection lifecycle, host trust and transient network retries."""

from __future__ import annotations

import errno
import logging
import os
import socket
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import allow_agent_arg, cli_key_arg, coerce_port, user_config_path
from .contracts import (
    CONNECT_TIMEOUT,
    CONNECT_RETRIES,
    KEEPALIVE_INTERVAL,
    EXIT_CONFIG_ERROR,
    EXIT_TIMEOUT,
    EXIT_NETWORK_ERROR,
    EXIT_SSH_ERROR,
)

if TYPE_CHECKING:
    import paramiko

logger = logging.getLogger("ssh_tool")
APP_KNOWN_HOSTS_FILE = "known_hosts"
OPENSSH_KNOWN_HOSTS_FILE = Path(".ssh") / "known_hosts"
_paramiko_module: Any | None = None
_host_keys_lock = threading.Lock()
RETRYABLE_SOCKET_ERROR_CODES = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ETIMEDOUT,
        10053,  # WSAECONNABORTED
        10054,  # WSAECONNRESET
        10060,  # WSAETIMEDOUT
        10061,  # WSAECONNREFUSED
    }
)


@dataclass(frozen=True)
class ConnectionErrorClassification:
    code: int
    category: str


def _load_paramiko() -> Any:
    """Import Paramiko only when a real SSH operation needs it."""
    global _paramiko_module
    if _paramiko_module is not None:
        return _paramiko_module
    try:
        import paramiko as loaded_paramiko
    except Exception as exc:
        raise RuntimeError(
            "Unable to load paramiko. Verify the Python environment, installed "
            "dependencies, and Windows network provider/Winsock health."
        ) from exc
    _paramiko_module = loaded_paramiko
    return loaded_paramiko


def _is_paramiko_auth_error(exc: BaseException) -> bool:
    try:
        paramiko_module = _load_paramiko()
    except RuntimeError:
        return False
    return isinstance(exc, paramiko_module.AuthenticationException)


def classify_connection_error(
    exc: BaseException,
    *,
    target_known: bool,
) -> ConnectionErrorClassification:
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return ConnectionErrorClassification(EXIT_CONFIG_ERROR, "config_error")
    if isinstance(exc, TimeoutError):
        return ConnectionErrorClassification(EXIT_TIMEOUT, "connect_timeout")
    if isinstance(exc, OSError):
        if target_known:
            return ConnectionErrorClassification(EXIT_NETWORK_ERROR, "network_error")
        return ConnectionErrorClassification(EXIT_CONFIG_ERROR, "config_error")
    if _is_paramiko_auth_error(exc):
        return ConnectionErrorClassification(EXIT_SSH_ERROR, "auth_error")
    return ConnectionErrorClassification(EXIT_SSH_ERROR, "connect_error")


def _user_known_hosts_path() -> Path:
    return user_config_path().with_name(APP_KNOWN_HOSTS_FILE)


def _windows_openssh_known_hosts_path() -> Path | None:
    """Return the current Windows user's OpenSSH trust store when applicable."""
    if os.name != "nt":
        return None
    profile = os.environ.get("USERPROFILE")
    if not profile:
        return None
    return Path(profile) / OPENSSH_KNOWN_HOSTS_FILE


def _connection_endpoint(args: Any) -> tuple[str, str, int]:
    host = args.host.strip() if isinstance(args.host, str) else args.host
    user = args.user.strip() if isinstance(args.user, str) else args.user
    if not host or not user:
        raise ValueError("Missing host or user. Provide via CLI or target.json.")
    port = coerce_port(args.port if args.port is not None else 22, context="Connection")
    return host, user, port


_SOCKS5_REPLY_MESSAGES = {
    0: "succeeded",
    1: "general SOCKS server failure",
    2: "connection not allowed by ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "TTL expired",
    7: "command not supported",
    8: "address type not supported",
}


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes; a short read means the peer hung up."""
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _socks5_tunnel(
    proxy: tuple[str, int],
    dst_host: str,
    dst_port: int,
    timeout: float,
) -> socket.socket:
    """Open a TCP connection to ``dst_host`` through a SOCKS5 proxy.

    Hand-rolled no-auth SOCKS5 CONNECT so no additional dependency is needed;
    failures raise ``OSError`` so callers reuse the transient-network retry
    and error classification paths.
    """
    proxy_host, proxy_port = proxy
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        greeting = _recv_exact(sock, 2)
        if len(greeting) < 2 or greeting[0] != 0x05 or greeting[1] != 0x00:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} rejected the "
                f"no-auth handshake: {greeting!r}"
            )

        try:
            addr_bytes = socket.inet_pton(socket.AF_INET, dst_host)
            atyp, addr_field = 0x01, addr_bytes
        except OSError:
            try:
                addr_bytes = socket.inet_pton(socket.AF_INET6, dst_host)
                atyp, addr_field = 0x04, addr_bytes
            except OSError:
                atyp = 0x03
                try:
                    addr_field = dst_host.encode("idna")
                except UnicodeError as exc:
                    # Preserve the OSError contract so a malformed hostname is
                    # classified with every other network failure, not as an
                    # unhandled config-shaped error.
                    raise OSError(
                        f"cannot represent destination host {dst_host!r} as "
                        f"a SOCKS5 domain address: {exc}"
                    ) from exc

        if atyp == 0x03:
            encoded_addr = bytes([len(addr_field)]) + addr_field
        else:
            encoded_addr = addr_field
        request = (
            b"\x05\x01\x00" + bytes([atyp]) + encoded_addr + dst_port.to_bytes(2, "big")
        )
        sock.sendall(request)

        reply = _recv_exact(sock, 4)
        if len(reply) < 4 or reply[0] != 0x05:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} sent a malformed "
                f"CONNECT reply: {reply!r}"
            )
        code = reply[1]
        if code != 0x00:
            detail = _SOCKS5_REPLY_MESSAGES.get(code, f"reply code {code}")
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} failed to reach "
                f"{dst_host}:{dst_port}: {detail}"
            )
        # Drain the bound address so the stream stays aligned for SSH.
        truncated = (
            f"SOCKS5 proxy {proxy_host}:{proxy_port} closed the connection "
            "inside the CONNECT reply."
        )
        atyp_reply = reply[3]
        if atyp_reply == 0x01:
            remainder = 4 + 2
        elif atyp_reply == 0x04:
            remainder = 16 + 2
        elif atyp_reply == 0x03:
            # BND.ADDR for a domain is a length-prefixed variable-length
            # field; the length byte itself is part of the reply.
            length_field = _recv_exact(sock, 1)
            if not length_field:
                raise OSError(truncated)
            remainder = length_field[0] + 2
        else:
            raise OSError(
                f"SOCKS5 proxy {proxy_host}:{proxy_port} returned an unknown "
                f"address type {atyp_reply}."
            )
        if len(_recv_exact(sock, remainder)) < remainder:
            raise OSError(truncated)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock
    except Exception:
        with suppress(OSError):
            sock.close()
        raise


def _key_path_from_arg(key: str | None) -> Path | None:
    if not key:
        return None
    key_path = Path(key).expanduser()
    if not key_path.exists():
        raise FileNotFoundError(f"SSH key file not found: {key_path}")
    return key_path


class _PersistentAutoAddPolicy:
    """Persist first-use host keys while preserving compatibility mode."""

    def __init__(self, paramiko_module: Any, known_hosts_path: Path) -> None:
        self._paramiko = paramiko_module
        self._known_hosts_path = known_hosts_path

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        with _host_keys_lock:
            try:
                self._known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
                self._known_hosts_path.touch(mode=0o600, exist_ok=True)
                client.load_host_keys(str(self._known_hosts_path))

                known_for_host = client.get_host_keys().lookup(hostname)
                if known_for_host is not None:
                    expected_key = known_for_host.get(key.get_name())
                    # A known host with a different algorithm is still a
                    # changed host identity. Accepting it would let an
                    # attacker add an alternate key beside a pinned key.
                    if expected_key is None or expected_key != key:
                        raise self._paramiko.BadHostKeyException(
                            hostname,
                            key,
                            expected_key,
                        )
                    return

                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(self._known_hosts_path))
            except self._paramiko.BadHostKeyException:
                raise
            except Exception as exc:
                raise ValueError(
                    f"Unable to persist SSH host key in {self._known_hosts_path}."
                ) from exc


def _load_user_host_keys(client: Any, known_hosts_path: Path) -> None:
    if not known_hosts_path.exists():
        return
    if not known_hosts_path.is_file():
        raise ValueError(f"Launcher known_hosts is not a file: {known_hosts_path}")
    try:
        client.load_host_keys(str(known_hosts_path))
    except Exception as exc:
        raise ValueError(
            f"Unable to load launcher known_hosts: {known_hosts_path}"
        ) from exc


def _load_windows_openssh_host_keys(client: Any) -> None:
    """Load Windows OpenSSH user keys without weakening strict verification.

    Paramiko's system lookup is platform-dependent.  Explicitly loading the
    current user's OpenSSH store lets strict launcher calls reuse a host key
    that the user has already verified in Windows.  A present-but-unreadable
    store is a local trust-store permission error, not permission to accept a
    new key.
    """
    known_hosts_path = _windows_openssh_known_hosts_path()
    if known_hosts_path is None or not known_hosts_path.exists():
        return
    if not known_hosts_path.is_file():
        raise ValueError(
            f"Windows OpenSSH known_hosts is not a file: {known_hosts_path}"
        )
    try:
        client.load_host_keys(str(known_hosts_path))
    except Exception as exc:
        raise ValueError(
            "Unable to load Windows OpenSSH known_hosts; restore read access "
            f"for the current user: {known_hosts_path}"
        ) from exc


def connect_client(args: Any) -> paramiko.SSHClient:
    host, user, port = _connection_endpoint(args)
    use_agent = allow_agent_arg(args)
    password = args.password if isinstance(args.password, str) else None
    key = cli_key_arg(args)
    if not password and not key and not use_agent:
        raise ValueError(
            "No auth method. Use --password, --key, --allow-agent, "
            "or set credentials in target.json."
        )

    logger.debug("Connecting to %s@%s:%d", user, host, port)

    key_path = _key_path_from_arg(key)
    paramiko_module = _load_paramiko()

    # Optional profile-level SOCKS5 tunnel; without it the socket is a direct
    # TCP connection, so existing profiles keep their exact previous behavior.
    proxy = getattr(args, "socks5", None)
    if proxy is not None:
        sock = _socks5_tunnel(proxy, host, port, CONNECT_TIMEOUT)
    else:
        # socket.create_connection supports both IPv4 and IPv6
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)

    client: Any | None = None
    try:
        client = paramiko_module.SSHClient()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Known hosts are always checked. Strict verification is the default;
        # compatibility TOFU must be an explicit opt-out at the CLI boundary.
        client.load_system_host_keys()
        # Compatibility mode has its own persistence store.  Do not make an
        # unreadable Windows OpenSSH store break that first-use workflow; the
        # store is required for the default strict verification path.
        if getattr(args, "strict_host_key_checking", True):
            _load_windows_openssh_host_keys(client)
        known_hosts_path = _user_known_hosts_path()
        _load_user_host_keys(client, known_hosts_path)
        if getattr(args, "strict_host_key_checking", True):
            client.set_missing_host_key_policy(paramiko_module.RejectPolicy())
        else:
            client.set_missing_host_key_policy(  # nosec B507
                _PersistentAutoAddPolicy(paramiko_module, known_hosts_path)
            )

        connect_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": user,
            "sock": sock,
            "timeout": CONNECT_TIMEOUT,
            "banner_timeout": CONNECT_TIMEOUT,
            "auth_timeout": CONNECT_TIMEOUT,
            "allow_agent": use_agent,
            "look_for_keys": False,
        }
        if key_path:
            connect_kwargs["key_filename"] = str(key_path)
        elif password:
            connect_kwargs["password"] = password
        client.connect(**connect_kwargs)
        # Keep-alive prevents idle disconnects on long-running commands.
        if KEEPALIVE_INTERVAL > 0:
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(KEEPALIVE_INTERVAL)
    except Exception:
        if client is not None:
            with suppress(Exception):
                client.close()
        with suppress(OSError):
            sock.close()
        raise

    logger.debug("Connected to %s@%s:%d", user, host, port)
    return client


def _is_retryable_connection_error(exc: OSError) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    error_code = exc.errno
    if error_code is None:
        error_code = getattr(exc, "winerror", None)
    return error_code in RETRYABLE_SOCKET_ERROR_CODES


def connect_with_retry(args: Any) -> paramiko.SSHClient:
    """Connect with optional retry for transient network errors."""
    for attempt in range(1 + CONNECT_RETRIES):
        try:
            return connect_client(args)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if not _is_retryable_connection_error(exc):
                raise
            if attempt < CONNECT_RETRIES:
                # Short exponential spacing: transient blips still get spaced
                # retries, while an interactive check against a dead target
                # fails ~1.5s sooner than a flat 1s-per-retry delay.
                delay = 0.25 * (2**attempt)
                logger.debug(
                    "Retry %d/%d after %.2fs: %s",
                    attempt + 1,
                    CONNECT_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("CONNECT_RETRIES loop exhausted unexpectedly.")
