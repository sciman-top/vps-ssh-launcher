"""SSH connection lifecycle, host trust and transient network retries."""

from __future__ import annotations

import errno
import logging
import os
import socket
import stat
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import windows_acl
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
from .socks5 import open_tunnel as _socks5_tunnel

if TYPE_CHECKING:
    import paramiko

logger = logging.getLogger("ssh_tool")
APP_KNOWN_HOSTS_FILE = "known_hosts"
OPENSSH_KNOWN_HOSTS_FILE = Path(".ssh") / "known_hosts"
_paramiko_module: Any | None = None
_host_keys_lock = threading.Lock()
# Paramiko passes the hostname without the port only when the caller omitted
# a socket. connect_client always supplies its own socket for SOCKS/direct
# control, so the policy cannot reliably infer an unqualified host key there.
_HOST_KEY_PORT_KEY = "[{host}]:{port}"

# This is a private trust store, not OpenSSH's user file. It never carries
# credentials, but making it owner-only prevents another local account or a
# repository copy from silently supplying a host identity.
_PRIVATE_FILE_MODE = 0o600
_MAX_KNOWN_HOSTS_BYTES = 16 * 1024 * 1024
_MAX_SSH_KEY_BYTES = 1024 * 1024
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
# Paramiko wraps every mid-handshake transport failure (banner read timeout,
# server-side pre-auth drop such as MaxStartups, SOCKS5 relay hiccup) as
# SSHException instead of an OSError, so the retry loop would otherwise give
# up on the transient blips a staged multi-connection transfer hits most.
# Only these observed handshake messages retry; auth and host-key verdicts
# stay terminal so a retry can never mask a wrong secret or an untrusted key.
TRANSIENT_HANDSHAKE_MESSAGES = (
    "error reading ssh protocol banner",
    "no existing session",
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


def _key_path_from_arg(key: str | None) -> Path | None:
    if not key:
        return None
    key_path = Path(key).expanduser()
    try:
        key_stat = key_path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"SSH key file not found: {key_path}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to inspect SSH key file: {key_path}") from exc
    if stat.S_ISLNK(key_stat.st_mode):
        raise ValueError(f"SSH key file must not be a symlink: {key_path}")
    if not stat.S_ISREG(key_stat.st_mode):
        raise ValueError(f"SSH key path is not a regular file: {key_path}")
    if key_stat.st_size <= 0 or key_stat.st_size > _MAX_SSH_KEY_BYTES:
        raise ValueError(f"SSH key file must be between 1 byte and 1 MiB: {key_path}")
    permission_denied = False
    try:
        if os.name == "nt":
            # Reuse the same DACL parser as target.json, but refuse rather than
            # mutating a user's private key behind their back.
            windows_acl._ensure_windows_private_dacl(key_path, lambda _path: 1)
        elif stat.S_IMODE(key_stat.st_mode) & 0o077:
            permission_denied = True
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"SSH key file must be private and inaccessible to other users: {key_path}"
        ) from exc
    if permission_denied:
        raise ValueError(
            f"SSH key file is accessible to other users; repair its permissions: {key_path}"
        )
    return key_path


class _PersistentAutoAddPolicy:
    """Persist first-use host keys while preserving compatibility mode."""

    def __init__(
        self,
        paramiko_module: Any,
        known_hosts_path: Path,
        *,
        connect_port: int | None = None,
    ) -> None:
        self._paramiko = paramiko_module
        self._known_hosts_path = known_hosts_path
        self._connect_port = connect_port

    def _lookup_keys(
        self,
        client: Any,
        hostname: str,
        *,
        connect_port: int | None,
    ) -> tuple[Any | None, str | None]:
        """Find both standard port keys and legacy unqualified host keys."""
        host_keys = client.get_host_keys()
        if connect_port is None:
            return host_keys.lookup(hostname), hostname
        naked_name = hostname
        if naked_name.startswith("["):
            closing = naked_name.find("]")
            if closing != -1:
                naked_name = naked_name[1:closing]
        qualified = _HOST_KEY_PORT_KEY.format(host=naked_name, port=connect_port)
        found = host_keys.lookup(qualified)
        if found is not None:
            return found, qualified

        # A legacy unqualified key resolves the same physical endpoint at a
        # non-default port, so reuse it. Do not copy it into a new port scope:
        # the first explicit trust belongs in the standard bracket form.
        found = host_keys.lookup(naked_name)
        if found is not None:
            return found, naked_name
        return None, qualified

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        with _host_keys_lock:
            try:
                self._known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
                if os.name == "nt":
                    self._known_hosts_path.touch(exist_ok=True)
                    # ``touch`` inherits the parent directory DACL.  The
                    # launcher trust store is user controlled state, so
                    # refuse to persist a host key until that inherited ACL
                    # has been checked/tightened through the same seam used
                    # for target.json and private keys.
                    windows_acl._ensure_windows_private_dacl(
                        self._known_hosts_path,
                        windows_acl._tighten_windows_dacl,
                    )
                else:
                    descriptor = os.open(
                        self._known_hosts_path,
                        os.O_CREAT | os.O_RDWR,
                        _PRIVATE_FILE_MODE,
                    )
                    os.close(descriptor)
                    os.chmod(self._known_hosts_path, _PRIVATE_FILE_MODE)
                client.load_host_keys(str(self._known_hosts_path))

                known_for_host, storage_name = self._lookup_keys(
                    client,
                    hostname,
                    connect_port=self._connect_port,
                )
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

                if storage_name is None:
                    raise ValueError("SSH host key storage name is unavailable.")
                client.get_host_keys().add(storage_name, key.get_name(), key)
                client.save_host_keys(str(self._known_hosts_path))
            except self._paramiko.BadHostKeyException:
                raise
            except Exception as exc:
                raise ValueError(
                    f"Unable to persist SSH host key in {self._known_hosts_path}."
                ) from exc


def _validate_known_hosts_file(path: Path, label: str) -> bool:
    """Check trust-store shape without changing it or opening a network link."""
    try:
        path_stat = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError(f"Unable to inspect {label}: {path}") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError(f"{label} is not a file: {path}")
    if path_stat.st_size > _MAX_KNOWN_HOSTS_BYTES:
        raise ValueError(f"{label} exceeds the 16 MiB limit.")
    return True


def _load_user_host_keys(client: Any, known_hosts_path: Path) -> None:
    if not _validate_known_hosts_file(known_hosts_path, "Launcher known_hosts"):
        return
    if os.name != "nt":
        try:
            os.chmod(known_hosts_path, _PRIVATE_FILE_MODE)
        except OSError as exc:
            raise ValueError(
                f"Unable to restrict launcher known_hosts permissions: {known_hosts_path}"
            ) from exc
        try:
            if stat.S_IMODE(known_hosts_path.stat().st_mode) != _PRIVATE_FILE_MODE:
                raise ValueError(
                    f"Launcher known_hosts remains accessible to other users: {known_hosts_path}"
                )
        except FileNotFoundError as exc:
            raise ValueError(
                f"Launcher known_hosts disappeared during verification: {known_hosts_path}"
            ) from exc
    else:
        # Existing stores may predate the private trust-store policy, and a
        # file created by another process can inherit broad directory ACLs.
        # Validate/tighten before Paramiko reads any host identity.
        windows_acl._ensure_windows_private_dacl(
            known_hosts_path,
            windows_acl._tighten_windows_dacl,
        )
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
    if known_hosts_path is None or not _validate_known_hosts_file(
        known_hosts_path,
        "Windows OpenSSH known_hosts",
    ):
        return
    try:
        client.load_host_keys(str(known_hosts_path))
    except Exception as exc:
        raise ValueError(
            "Unable to load Windows OpenSSH known_hosts; restore read access "
            f"for the current user: {known_hosts_path}"
        ) from exc


def _verify_host_key_stores(
    *,
    strict_host_key_checking: bool,
    known_hosts_path: Path,
) -> None:
    """Fail on malformed local trust stores before opening any network socket."""
    # Load paramiko FIRST. Importing it inside the per-file parser below meant a
    # missing dependency surfaced as "known_hosts contains a malformed entry"
    # and was classified as a configuration error (exit 2), sending the operator
    # to repair a trust store that was fine.
    paramiko_module = _load_paramiko()

    host_keys_type = getattr(paramiko_module, "HostKeys", None)
    if not callable(host_keys_type):
        # A partial runtime (or a client-only double) exposes no host-key
        # parser. There is nothing to parse with, and the same runtime will
        # fail later on its own missing attribute; do not invent a trust-store
        # verdict here.
        return

    def parse(label: str, path: Path) -> None:
        # Parsing every record through the real HostKeys constructor rejects a
        # malformed or empty file without enrolling a missing host, sending
        # network traffic, or constructing an SSHClient (that constructor is
        # exercised later, after socket creation).
        try:
            host_keys_type(str(path))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"{label} contains a malformed entry.") from exc

    if _validate_known_hosts_file(known_hosts_path, "Launcher known_hosts"):
        parse("Launcher known_hosts", known_hosts_path)
    if strict_host_key_checking and os.name == "nt":
        windows_path = _windows_openssh_known_hosts_path()
        if windows_path is not None and _validate_known_hosts_file(
            windows_path,
            "Windows OpenSSH known_hosts",
        ):
            parse("Windows OpenSSH known_hosts", windows_path)


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
    known_hosts_path = _user_known_hosts_path()
    _verify_host_key_stores(
        strict_host_key_checking=bool(getattr(args, "strict_host_key_checking", True)),
        known_hosts_path=known_hosts_path,
    )
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
        _load_user_host_keys(client, known_hosts_path)
        if getattr(args, "strict_host_key_checking", True):
            client.set_missing_host_key_policy(paramiko_module.RejectPolicy())
        else:
            client.set_missing_host_key_policy(
                _PersistentAutoAddPolicy(
                    paramiko_module,
                    known_hosts_path,
                    connect_port=port,
                )
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


def _is_transient_handshake_error(exc: BaseException) -> bool:
    """Classify the Paramiko handshake failures that surface outside OSError.

    A bare EOFError is how Paramiko reports a TCP close before the banner;
    unknown exception types and a partial client-only Paramiko double behave
    exactly as before this classifier existed: no retry.
    """
    if isinstance(exc, EOFError):
        return True
    try:
        paramiko_module = _load_paramiko()
    except RuntimeError:
        return False
    ssh_exception = getattr(paramiko_module, "SSHException", None)
    if ssh_exception is None or not isinstance(exc, ssh_exception):
        return False
    terminal_types = tuple(
        exception_type
        for exception_type in (
            getattr(paramiko_module, "AuthenticationException", None),
            getattr(paramiko_module, "HostKeyException", None),
        )
        if exception_type is not None
    )
    if terminal_types and isinstance(exc, terminal_types):
        return False
    message = str(exc).lower()
    return any(pattern in message for pattern in TRANSIENT_HANDSHAKE_MESSAGES)


def connect_with_retry(args: Any) -> paramiko.SSHClient:
    """Connect with optional retry for transient network errors."""
    for attempt in range(1 + CONNECT_RETRIES):
        try:
            return connect_client(args)
        except FileNotFoundError:
            raise
        except Exception as exc:
            if isinstance(exc, OSError):
                if not _is_retryable_connection_error(exc):
                    raise
            elif not _is_transient_handshake_error(exc):
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
