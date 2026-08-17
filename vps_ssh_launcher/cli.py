"""Minimal multi-VPS SSH launcher.

Exit codes:
  0  Success (or remote command exited 0)
  1  SSH / authentication error
  2  Configuration error
  3  Connection timeout
  4  Network error
  5  Remote command error
  For the 'run' action, remote exit codes (0-255) are returned as-is.
"""

from __future__ import annotations

import argparse
import codecs
import errno
import json
import logging
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

if TYPE_CHECKING:
    import paramiko

# --- Tunables ---
CONNECT_TIMEOUT = 8  # TCP + SSH handshake + auth (seconds)
CMD_TIMEOUT = 60  # Remote command timeout (seconds)
KEEPALIVE_INTERVAL = 30  # SSH keep-alive (seconds), 0 = disabled
CONNECT_RETRIES = 2  # Extra retries for transient errors
MIN_PORT = 1
MAX_PORT = 65535
MAX_REMOTE_EXIT_CODE = 255
DEFAULT_RUN_ALL_MAX_WORKERS = 32
RUN_ALL_OUTPUT_LIMIT = 64 * 1024
CHANNEL_POLL_INTERVAL = 0.01
CHANNEL_READ_BURST = 16

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

# --- Exit codes ---
EXIT_OK = 0
EXIT_SSH_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_TIMEOUT = 3
EXIT_NETWORK_ERROR = 4
EXIT_CMD_ERROR = 5

__version__ = "1.1.1"

logger = logging.getLogger("ssh_tool")

APP_CONFIG_DIR = "vps-ssh-launcher"
APP_CONFIG_FILE = "target.json"
APP_KNOWN_HOSTS_FILE = "known_hosts"
SOURCE_ROOT = Path(__file__).resolve().parents[1]

_paramiko_module: Any | None = None
_host_keys_lock = threading.Lock()


class RemoteCommandClient(Protocol):
    def exec_command(self, command: str) -> tuple[Any, Any, Any]: ...


class ClosableRemoteCommandClient(RemoteCommandClient, Protocol):
    def close(self) -> None: ...


@dataclass(frozen=True)
class ProfileRunResult:
    name: str
    code: int
    stdout: str
    stderr: str
    category: str
    elapsed: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class ProfileRunContext:
    args: argparse.Namespace
    config_dir: Path
    command: str
    command_timeout: int
    command_hard_timeout: int


@dataclass(frozen=True)
class MainConnectionResult:
    client: ClosableRemoteCommandClient | None
    exit_code: int | None


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


def _classify_connection_error(
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


def _coerce_port(value: Any, *, context: str) -> int:
    """Normalize a port value and fail fast on invalid input."""
    if isinstance(value, bool):
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {value!r}."
        )
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdigit():
        port = int(value.strip())
    else:
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {value!r}."
        )
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(
            f"{context}: port must be an integer {MIN_PORT}-{MAX_PORT}, got {port!r}."
        )
    return port


def _cli_password_arg(args: Any) -> str | None:
    password = getattr(args, "password", None)
    return password if isinstance(password, str) and password else None


def _cli_key_arg(args: Any) -> str | None:
    key = getattr(args, "key", None)
    return key.strip() if isinstance(key, str) and key.strip() else None


def _allow_agent_arg(args: Any) -> bool:
    return bool(getattr(args, "allow_agent", False))


def _has_cli_auth_override(args: Any) -> bool:
    return bool(
        _allow_agent_arg(args)
        or _cli_password_arg(args) is not None
        or _cli_key_arg(args) is not None
    )


def _resolve_auth_for_entry(
    entry: dict[str, Any],
    args: Any,
    *,
    config_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    """Resolve password/key after applying CLI authentication overrides."""
    cli_key = _cli_key_arg(args)
    if cli_key is not None:
        return None, cli_key

    cli_password = _cli_password_arg(args)
    if cli_password is not None:
        return cli_password, None

    if _allow_agent_arg(args):
        return None, None

    profile_key = _resolve_key(entry, config_dir=config_dir)
    if profile_key is not None:
        return None, profile_key
    return _resolve_password(entry), None


def _redact_command_for_log(command: str) -> str:
    return f"<redacted remote command; length={len(command)}>"


def _coerce_timeout(value: Any, *, context: str) -> int:
    """Normalize command timeout seconds; 0 disables command timeout."""
    if isinstance(value, bool):
        raise ValueError(f"{context}: timeout must be an integer >= 0, got {value!r}.")
    if isinstance(value, int):
        timeout = value
    elif isinstance(value, str) and value.strip().isdigit():
        timeout = int(value.strip())
    else:
        raise ValueError(f"{context}: timeout must be an integer >= 0, got {value!r}.")
    if timeout < 0:
        raise ValueError(
            f"{context}: timeout must be an integer >= 0, got {timeout!r}."
        )
    return timeout


def _command_timeout_arg(args: Any) -> int:
    raw_timeout = getattr(args, "command_timeout", None)
    if raw_timeout is None:
        return CMD_TIMEOUT
    return _coerce_timeout(raw_timeout, context="Command timeout")


def _command_hard_timeout_arg(args: Any) -> int:
    raw_timeout = getattr(args, "command_hard_timeout", None)
    if raw_timeout is None:
        return 0
    return _coerce_timeout(raw_timeout, context="Command hard timeout")


def _run_all_max_workers_arg(args: Any, profile_count: int) -> int:
    if profile_count < 1:
        raise ValueError("Profile count must be at least 1.")

    raw_max_workers = getattr(args, "max_workers", None)
    if raw_max_workers is None:
        return min(profile_count, DEFAULT_RUN_ALL_MAX_WORKERS)
    if not isinstance(raw_max_workers, int) or isinstance(raw_max_workers, bool):
        raise ValueError("--max-workers must be an integer >= 1.")
    if raw_max_workers < 1:
        raise ValueError("--max-workers must be an integer >= 1.")
    return min(profile_count, raw_max_workers)


class _DecodedOutput:
    """Decode one remote stream and optionally emit/capture it with a hard cap."""

    def __init__(
        self,
        stream_name: str,
        *,
        writer: Callable[[str], Any] | None,
        capture_limit: int | None,
    ) -> None:
        self._stream_name = stream_name
        self._writer = writer
        self._capture_limit = capture_limit
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._chunks: list[str] = []
        self._captured_chars = 0
        self.truncated = False

    def feed(self, data: bytes) -> None:
        self._emit(self._decoder.decode(data))

    def finish(self) -> None:
        self._emit(self._decoder.decode(b"", final=True))

    def _emit(self, text: str) -> None:
        if not text:
            return
        if self._writer is not None:
            self._writer(text)
        if self._capture_limit == 0:
            return
        if self._capture_limit is None:
            self._chunks.append(text)
            self._captured_chars += len(text)
            return

        remaining = self._capture_limit - self._captured_chars
        if remaining > 0:
            captured = text[:remaining]
            self._chunks.append(captured)
            self._captured_chars += len(captured)
        if len(text) > max(remaining, 0):
            self.truncated = True

    def render(self) -> str:
        text = "".join(self._chunks)
        if not self.truncated or self._capture_limit is None:
            return text
        marker = (
            f"\n[{self._stream_name} truncated at {self._capture_limit} chars; "
            "showing prefix only]\n"
        )
        prefix_limit = max(self._capture_limit - len(marker), 0)
        return text[:prefix_limit] + marker


def _read_available_channel_data(
    channel: Any,
    stdout_output: _DecodedOutput,
    stderr_output: _DecodedOutput,
) -> bool:
    progressed = False
    for _ in range(CHANNEL_READ_BURST):
        if not channel.recv_ready():
            break
        data = channel.recv(32768)
        if not data:
            break
        stdout_output.feed(data)
        progressed = True

    for _ in range(CHANNEL_READ_BURST):
        if not channel.recv_stderr_ready():
            break
        data = channel.recv_stderr(32768)
        if not data:
            break
        stderr_output.feed(data)
        progressed = True

    return progressed


def _drain_channel(
    channel: Any,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
    stdout_writer: Callable[[str], Any] | None = None,
    stderr_writer: Callable[[str], Any] | None = None,
    capture_limit: int | None = None,
) -> tuple[str, str, int, bool, bool]:
    """Read stdout and stderr without deadlocking the SSH channel.

    Uses a two-tier timeout:
    - Idle timeout: resets on every data received.
    - Hard total timeout: optional absolute upper bound regardless of activity.
    """
    stdout_output = _DecodedOutput(
        "stdout", writer=stdout_writer, capture_limit=capture_limit
    )
    stderr_output = _DecodedOutput(
        "stderr", writer=stderr_writer, capture_limit=capture_limit
    )
    idle_deadline: float | None = None
    hard_deadline: float | None = None
    if command_timeout > 0:
        now = time.monotonic()
        idle_deadline = now + command_timeout
    if command_hard_timeout > 0:
        hard_deadline = time.monotonic() + command_hard_timeout

    while True:
        progressed = _read_available_channel_data(
            channel,
            stdout_output,
            stderr_output,
        )
        if progressed and idle_deadline is not None:
            idle_deadline = time.monotonic() + command_timeout

        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break

        now = time.monotonic()
        if hard_deadline is not None and now > hard_deadline:
            raise TimeoutError(
                f"Remote command exceeded hard timeout of {command_hard_timeout}s."
            )
        if idle_deadline is not None and now > idle_deadline:
            raise TimeoutError(
                f"Remote command timed out after {command_timeout}s idle."
            )

        if not progressed:
            time.sleep(CHANNEL_POLL_INTERVAL)

    stdout_output.finish()
    stderr_output.finish()
    return (
        stdout_output.render(),
        stderr_output.render(),
        channel.recv_exit_status(),
        stdout_output.truncated,
        stderr_output.truncated,
    )


# ── CLI ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssh_tool",
        description="Minimal multi-VPS remote shell helper",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--config", help="JSON config file path")
    parser.add_argument("--profile", help="Profile name from config")
    parser.add_argument("--password")
    parser.add_argument("--key", help="SSH private key path")
    parser.add_argument(
        "--allow-agent",
        action="store_true",
        help="Use SSH agent for authentication",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--strict-host-key-checking",
        action="store_true",
        help="Reject unknown host keys",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    run = sub.add_parser("run", help="Run command on remote host(s)")
    run.add_argument("--command", required=True)
    run.add_argument(
        "--command-timeout",
        type=int,
        default=CMD_TIMEOUT,
        help=(
            "Remote command idle timeout in seconds; 0 disables command timeout "
            f"(default: {CMD_TIMEOUT})."
        ),
    )
    run.add_argument(
        "--command-hard-timeout",
        type=int,
        default=0,
        help="Absolute remote command timeout in seconds; 0 disables it.",
    )
    run.add_argument(
        "--all",
        action="store_true",
        dest="run_all",
        help="Run on all profiles in parallel",
    )
    run.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help=(
            "Maximum parallel profiles for --all; defaults to "
            f"min(profile count, {DEFAULT_RUN_ALL_MAX_WORKERS})."
        ),
    )

    sub.add_parser("check", help="Test connectivity")

    return parser


# ── Config ──────────────────────────────────────────────────


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Unable to read config file: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file: {path}") from exc
    if not isinstance(config, dict):
        raise ValueError("Config root must be a JSON object.")
    return cast(dict[str, Any], config)


def _user_config_path() -> Path:
    if os.name == "nt":
        base_dir = os.environ.get("APPDATA")
        if base_dir:
            return Path(base_dir) / APP_CONFIG_DIR / APP_CONFIG_FILE
    return Path.home() / ".config" / APP_CONFIG_DIR / APP_CONFIG_FILE


def _user_known_hosts_path() -> Path:
    return _user_config_path().with_name(APP_KNOWN_HOSTS_FILE)


def resolve_default_config_path(script_dir: Path) -> Path | None:
    """Prefer user-local override, then legacy repo-local config."""
    candidates = (
        _user_config_path(),
        script_dir / "target.json",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_password(entry: dict[str, Any]) -> str | None:
    """Resolve password: prefer password_env, then plaintext password."""
    env_name = entry.get("password_env")
    if env_name:
        env_name = cast(str, env_name).strip()
        pw = os.environ.get(env_name)
        if not pw:
            raise ValueError(f"Environment variable '{env_name}' is not set or empty.")
        return pw
    password = entry.get("password")
    return password if isinstance(password, str) else None


def _resolve_key(
    entry: dict[str, Any], *, config_dir: Path | None = None
) -> str | None:
    """Resolve SSH key path, optionally relative to config directory."""
    key = entry.get("key")
    if not isinstance(key, str):
        return None
    key_text = key.strip()
    if not key_text:
        return None
    key_path = Path(key_text).expanduser()
    if config_dir is not None and not key_path.is_absolute():
        return str(config_dir / key_path)
    return str(key_path)


def validate_profile(
    entry: dict[str, Any],
    name: str,
    *,
    require_auth: bool = True,
) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"Profile '{name}' must be an object.")
    host = entry.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"Profile '{name}': 'host' is required and must be a string.")
    _coerce_port(entry.get("port", 22), context=f"Profile '{name}'")
    user = entry.get("user")
    if not isinstance(user, str) or not user.strip():
        raise ValueError(f"Profile '{name}': 'user' is required and must be a string.")
    password = entry.get("password")
    password_env = entry.get("password_env")
    key = entry.get("key")

    if password is not None and (not isinstance(password, str) or not password):
        raise ValueError(f"Profile '{name}': 'password' must be a non-empty string.")
    if password_env is not None and (
        not isinstance(password_env, str) or not password_env.strip()
    ):
        raise ValueError(
            f"Profile '{name}': 'password_env' must be a non-empty string."
        )
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise ValueError(f"Profile '{name}': 'key' must be a non-empty string.")

    if require_auth:
        has_auth = password is not None or password_env is not None or key is not None
        if not has_auth:
            raise ValueError(
                f"Profile '{name}': no auth method. Set 'password', 'password_env', or 'key'."
            )


def _print_available_profiles(profiles: dict[str, Any], names: list[str]) -> None:
    print("Available VPS profiles:")
    for i, n in enumerate(names, 1):
        p = profiles[n]
        print(
            f"  {i}. {n} "
            f"({p.get('user', '?')}@{p.get('host', '?')}:{p.get('port', 22)})"
        )


def _read_profile_choice() -> str:
    try:
        return input("Select VPS (number or name): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        raise ValueError("Cancelled.") from None


def _resolve_profile_choice(
    choice: str,
    profiles: dict[str, Any],
    names: list[str],
) -> str:
    if not choice:
        raise ValueError("No profile selected.")
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(names):
            return names[idx - 1]
        raise ValueError(f"Number out of range: {idx}")
    if choice in profiles:
        return choice
    raise ValueError(f"Unknown profile: {choice}")


def _select_interactive_profile(profiles: dict[str, Any]) -> str:
    names = list(profiles)
    _print_available_profiles(profiles, names)
    return _resolve_profile_choice(_read_profile_choice(), profiles, names)


def select_profile(
    profiles: dict[str, Any],
    default_name: str | None,
    requested_name: str | None,
) -> str:
    if requested_name:
        if requested_name not in profiles:
            raise ValueError(
                f"Unknown profile '{requested_name}'. Available: {', '.join(profiles)}"
            )
        return requested_name

    if default_name:
        if default_name not in profiles:
            raise ValueError(
                f"Default profile '{default_name}' not found. "
                f"Available: {', '.join(profiles)}"
            )
        return default_name

    if len(profiles) == 1:
        return next(iter(profiles))

    stdin_is_interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not stdin_is_interactive:
        raise ValueError(
            "Multiple profiles found and no default/profile selected. "
            "Pass --profile or set 'default' in target.json. "
            f"Available: {', '.join(profiles)}"
        )

    return _select_interactive_profile(profiles)


def _select_config_entry(
    config: dict[str, Any],
    requested_profile: str | None,
) -> tuple[str, dict[str, Any]]:
    if "profiles" not in config:
        return "(root)", config

    profiles = config["profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("'profiles' must be a non-empty object.")
    profiles = cast(dict[str, Any], profiles)
    default_name = config.get("default")
    if default_name is not None and not isinstance(default_name, str):
        raise ValueError("'default' must be a string when set.")
    name = select_profile(profiles, default_name, requested_profile)
    return name, cast(dict[str, Any], profiles[name])


def apply_config(args: argparse.Namespace) -> None:
    """Merge config file into CLI args (CLI takes precedence)."""
    direct_target_complete = bool(
        isinstance(args.host, str)
        and args.host.strip()
        and isinstance(args.user, str)
        and args.user.strip()
        and _has_cli_auth_override(args)
    )
    if args.config is None and args.profile is None and direct_target_complete:
        return

    default_config = resolve_default_config_path(SOURCE_ROOT)
    config_path = args.config or (str(default_config) if default_config else None)
    if not config_path:
        return

    config_file = Path(config_path).expanduser()
    config = load_config(config_file)
    name, entry = _select_config_entry(config, args.profile)

    cli_key = _cli_key_arg(args)
    cli_has_auth_override = _has_cli_auth_override(args)

    validate_profile(entry, name, require_auth=not cli_has_auth_override)

    if args.host is None:
        args.host = cast(str, entry["host"]).strip()

    if args.port is None:
        args.port = _coerce_port(entry.get("port", 22), context=f"Profile '{name}'")
    else:
        args.port = _coerce_port(args.port, context="CLI --port")

    if args.user is None:
        args.user = cast(str, entry["user"]).strip()
    profile_password, profile_key = _resolve_auth_for_entry(
        entry,
        args,
        config_dir=config_file.parent,
    )
    if args.password is None:
        args.password = profile_password
    if cli_key is not None:
        args.key = cli_key
    elif args.key is None:
        args.key = profile_key


# ── Connection ──────────────────────────────────────────────


def _connection_endpoint(args: Any) -> tuple[str, str, int]:
    host = args.host.strip() if isinstance(args.host, str) else args.host
    user = args.user.strip() if isinstance(args.user, str) else args.user
    if not host or not user:
        raise ValueError("Missing host or user. Provide via CLI or target.json.")
    port = _coerce_port(
        args.port if args.port is not None else 22, context="Connection"
    )
    return host, user, port


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
                    if expected_key is not None:
                        if expected_key != key:
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


def connect_client(args: Any) -> paramiko.SSHClient:
    host, user, port = _connection_endpoint(args)
    use_agent = _allow_agent_arg(args)
    password = args.password if isinstance(args.password, str) else None
    key = _cli_key_arg(args)
    if not password and not key and not use_agent:
        raise ValueError(
            "No auth method. Use --password, --key, --allow-agent, "
            "or set credentials in target.json."
        )

    logger.debug("Connecting to %s@%s:%d", user, host, port)

    key_path = _key_path_from_arg(key)
    paramiko_module = _load_paramiko()

    # socket.create_connection supports both IPv4 and IPv6
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)

    client: Any | None = None
    try:
        client = paramiko_module.SSHClient()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Known hosts are always checked. Compatibility mode accepts only hosts
        # that are not yet known and persists first-use keys for future checks.
        client.load_system_host_keys()
        known_hosts_path = _user_known_hosts_path()
        _load_user_host_keys(client, known_hosts_path)
        if getattr(args, "strict_host_key_checking", False):
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
                delay = 1
                logger.debug(
                    "Retry %d/%d after %ds: %s",
                    attempt + 1,
                    CONNECT_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("CONNECT_RETRIES loop exhausted unexpectedly.")


# ── Execution ───────────────────────────────────────────────


def _execute_remote(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
    stdout_writer: Callable[[str], Any] | None = None,
    stderr_writer: Callable[[str], Any] | None = None,
    capture_limit: int | None = None,
) -> tuple[int, str, str, bool, bool]:
    """Execute one command through the shared streaming/capture seam."""
    command_timeout = _coerce_timeout(command_timeout, context="Command timeout")
    command_hard_timeout = _coerce_timeout(
        command_hard_timeout,
        context="Command hard timeout",
    )
    logger.debug("Running: %s", _redact_command_for_log(command))
    # This tool intentionally executes the explicit command supplied by the user.
    stdin, stdout, _stderr = client.exec_command(command)  # nosec
    try:
        stdin.close()  # Prevent hangs on commands that read stdin
        if command_timeout > 0:
            stdout.channel.settimeout(command_timeout)

        # Drain both streams incrementally to avoid filling one buffer while
        # waiting on the other. This keeps stderr-heavy commands safe.
        out, err, code, stdout_truncated, stderr_truncated = _drain_channel(
            stdout.channel,
            command_timeout=command_timeout,
            command_hard_timeout=command_hard_timeout,
            stdout_writer=stdout_writer,
            stderr_writer=stderr_writer,
            capture_limit=capture_limit,
        )
        if not EXIT_OK <= code <= MAX_REMOTE_EXIT_CODE:
            raise RuntimeError(f"Remote command returned invalid exit status: {code}.")
        return code, out, err, stdout_truncated, stderr_truncated
    finally:
        try:
            stdout.close()
        finally:
            _stderr.close()


def exec_remote(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
) -> tuple[int, str, str]:
    """Execute command and return its complete stdout/stderr for programmatic use."""
    code, out, err, _stdout_truncated, _stderr_truncated = _execute_remote(
        client,
        command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
    )
    return code, out, err


def _write_stream(stream: Any, text: str) -> None:
    stream.write(text)
    stream.flush()


def run_command(
    client: RemoteCommandClient,
    command: str,
    *,
    command_timeout: int = CMD_TIMEOUT,
    command_hard_timeout: int = 0,
) -> int:
    """Execute command, stream output. Returns exit code."""
    code, _out, _err, _stdout_truncated, _stderr_truncated = _execute_remote(
        client,
        command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
        stdout_writer=lambda text: _write_stream(sys.stdout, text),
        stderr_writer=lambda text: _write_stream(sys.stderr, text),
        capture_limit=0,
    )
    logger.debug("Exit code: %d", code)
    return code


def _run_all_config_file(args: argparse.Namespace) -> Path:
    config_path = (
        Path(args.config) if args.config else resolve_default_config_path(SOURCE_ROOT)
    )
    if not config_path:
        raise FileNotFoundError("Config file not found. Create target.json.")
    return config_path.expanduser()


def _load_profiles_for_run_all(config_file: Path) -> dict[str, Any]:
    config = load_config(config_file)
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("No profiles found in config.")
    return cast(dict[str, Any], profiles)


def _profile_error_result(
    name: str,
    code: int,
    category: str,
    error: str,
    started_at: float,
) -> ProfileRunResult:
    return ProfileRunResult(
        name=name,
        code=code,
        stdout="",
        stderr=error,
        category=category,
        elapsed=time.monotonic() - started_at,
    )


def _profile_namespace(
    name: str,
    entry: dict[str, Any],
    context: ProfileRunContext,
) -> argparse.Namespace:
    profile_password, profile_key = _resolve_auth_for_entry(
        entry,
        context.args,
        config_dir=context.config_dir,
    )
    return argparse.Namespace(
        host=cast(str, entry["host"]).strip(),
        port=_coerce_port(entry.get("port", 22), context=f"Profile '{name}'"),
        user=cast(str, entry["user"]).strip(),
        password=profile_password,
        key=profile_key,
        allow_agent=_allow_agent_arg(context.args),
        strict_host_key_checking=getattr(
            context.args, "strict_host_key_checking", False
        ),
    )


def _run_profile_command(
    name: str,
    entry: dict[str, Any],
    context: ProfileRunContext,
) -> ProfileRunResult:
    run_started = time.monotonic()
    client: ClosableRemoteCommandClient | None = None
    connection_error: tuple[int, str, str] | None = None
    try:
        ns = _profile_namespace(name, entry, context)
        client = connect_with_retry(ns)
    except Exception as exc:
        classified = _classify_connection_error(exc, target_known=True)
        if classified.category == "connect_error":
            logger.debug("Unexpected error connecting to '%s'", name, exc_info=True)
        connection_error = (classified.code, classified.category, str(exc))

    if connection_error is not None:
        code, category, error = connection_error
        return _profile_error_result(
            name=name,
            code=code,
            category=category,
            error=error,
            started_at=run_started,
        )

    if client is None:
        return _profile_error_result(
            name=name,
            code=EXIT_SSH_ERROR,
            category="connect_error",
            error="Internal error: SSH client was not created.",
            started_at=run_started,
        )
    try:
        try:
            code, out, err, stdout_truncated, stderr_truncated = _execute_remote(
                client,
                context.command,
                command_timeout=context.command_timeout,
                command_hard_timeout=context.command_hard_timeout,
                capture_limit=RUN_ALL_OUTPUT_LIMIT,
            )
            category = "ok" if code == EXIT_OK else "remote_nonzero"
            return ProfileRunResult(
                name=name,
                code=code,
                stdout=out,
                stderr=err,
                category=category,
                elapsed=time.monotonic() - run_started,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except TimeoutError as exc:
            error_result = _profile_error_result(
                name=name,
                code=EXIT_CMD_ERROR,
                category="command_timeout",
                error=str(exc),
                started_at=run_started,
            )
        except Exception as exc:
            logger.debug("Unexpected error executing on '%s'", name, exc_info=True)
            error_result = _profile_error_result(
                name=name,
                code=EXIT_CMD_ERROR,
                category="command_error",
                error=str(exc),
                started_at=run_started,
            )
        return error_result
    finally:
        if client is not None:
            client.close()


def _print_prefixed_lines(name: str, text: str, *, stream: Any | None = None) -> None:
    if not text:
        return
    if stream is None:
        stream = sys.stdout
    for line in text.splitlines(keepends=True):
        print(f"[{name}] {line}", end="", file=stream, flush=True)
    if not text.endswith(("\n", "\r")):
        print(file=stream, flush=True)


def _print_profile_result(result: ProfileRunResult) -> None:
    _print_prefixed_lines(result.name, result.stdout)
    _print_prefixed_lines(result.name, result.stderr, stream=sys.stderr)
    if result.stdout_truncated:
        print(f"[{result.name}] stdout truncated", flush=True)
    if result.stderr_truncated:
        print(f"[{result.name}] stderr truncated", flush=True)
    if result.code != EXIT_OK:
        print(f"[{result.name}] exit code: {result.code}", flush=True)
    print(f"[{result.name}] elapsed: {result.elapsed:.2f}s", flush=True)


def _summarize_run_on_all_results(
    results: list[ProfileRunResult],
) -> tuple[int, dict[str, int], dict[int, int], list[str]]:
    max_code = EXIT_OK
    category_counts: dict[str, int] = {}
    exit_code_counts: dict[int, int] = {}
    failed_profiles: list[str] = []

    for result in results:
        category_counts[result.category] = category_counts.get(result.category, 0) + 1
        exit_code_counts[result.code] = exit_code_counts.get(result.code, 0) + 1
        if result.code != EXIT_OK:
            failed_profiles.append(result.name)
        max_code = max(max_code, result.code)

    return max_code, category_counts, exit_code_counts, failed_profiles


def _print_run_on_all_results(
    results: list[ProfileRunResult],
    *,
    started_at: float,
) -> int:
    results.sort(key=lambda result: result.name)
    for result in results:
        _print_profile_result(result)

    max_code, category_counts, exit_code_counts, failed_profiles = (
        _summarize_run_on_all_results(results)
    )
    total = len(results)
    ok = category_counts.get("ok", 0)
    failed = total - ok
    total_elapsed = time.monotonic() - started_at
    print(
        f"[summary] profiles={total} ok={ok} failed={failed} elapsed={total_elapsed:.2f}s",
        flush=True,
    )
    for category, count in sorted(category_counts.items()):
        if category == "ok":
            continue
        print(f"[summary] {category}: {count}", flush=True)
    print(f"[summary] max_exit_code: {max_code}", flush=True)
    print(
        "[summary] exit_code_histogram: "
        + ", ".join(
            f"{exit_code}={count}"
            for exit_code, count in sorted(exit_code_counts.items())
        ),
        flush=True,
    )
    if failed_profiles:
        print(
            f"[summary] failed_profiles: {', '.join(failed_profiles)}",
            flush=True,
        )

    return max_code


def run_on_all(args: argparse.Namespace, command: str) -> int:
    """Run command on all config profiles in parallel."""
    started_at = time.monotonic()
    command_timeout = _command_timeout_arg(args)
    command_hard_timeout = _command_hard_timeout_arg(args)
    config_file = _run_all_config_file(args)
    profiles = _load_profiles_for_run_all(config_file)

    require_auth = not _has_cli_auth_override(args)
    validated_profiles: dict[str, dict[str, Any]] = {}
    for name, entry in profiles.items():
        validate_profile(entry, name, require_auth=require_auth)
        validated_profiles[name] = cast(dict[str, Any], entry)

    # Collect results in parallel, print sequentially
    results: list[ProfileRunResult] = []
    max_workers = _run_all_max_workers_arg(args, len(validated_profiles))
    context = ProfileRunContext(
        args=args,
        config_dir=config_file.parent,
        command=command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_name = {
            pool.submit(
                _run_profile_command,
                name,
                entry,
                context,
            ): name
            for name, entry in validated_profiles.items()
        }
        for future in as_completed(future_to_name):
            profile_name = future_to_name[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.debug(
                    "Unexpected worker failure for profile '%s'",
                    profile_name,
                    exc_info=True,
                )
                results.append(
                    _profile_error_result(
                        name=profile_name,
                        code=EXIT_CMD_ERROR,
                        category="internal_error",
                        error=str(exc),
                        started_at=started_at,
                    )
                )

    return _print_run_on_all_results(results, started_at=started_at)


# ── Main ────────────────────────────────────────────────────


def _run_all_main_action(args: argparse.Namespace) -> int:
    try:
        return run_on_all(args, args.command)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as exc:
        logger.debug("Unexpected run-all failure", exc_info=True)
        print(f"Run-all failed: {exc}", file=sys.stderr)
        return EXIT_CMD_ERROR


def _open_main_client(args: argparse.Namespace) -> MainConnectionResult:
    target = "unknown target"
    client: ClosableRemoteCommandClient | None = None
    exit_code: int | None = None

    try:
        apply_config(args)
        target = f"{args.user}@{args.host}:{args.port or 22}"
        client = connect_with_retry(args)
    except Exception as exc:
        classified = _classify_connection_error(
            exc,
            target_known=target != "unknown target",
        )
        if classified.category == "connect_timeout":
            print(f"[{target}] Connection timed out: {exc}", file=sys.stderr)
        elif classified.category == "config_error":
            print(f"Config error: {exc}", file=sys.stderr)
        elif classified.category == "network_error":
            print(f"[{target}] Network error: {exc}", file=sys.stderr)
            print("  Hint: check connectivity and firewall rules.", file=sys.stderr)
        elif classified.category == "auth_error":
            print(f"[{target}] Auth failed: {exc}", file=sys.stderr)
            print(
                "  Hint: verify password/key in target.json or set password_env.",
                file=sys.stderr,
            )
        else:
            print(f"[{target}] Connection failed: {exc}", file=sys.stderr)
        exit_code = classified.code

    return MainConnectionResult(client=client, exit_code=exit_code)


def _run_main_action(
    args: argparse.Namespace,
    client: RemoteCommandClient,
) -> int:
    try:
        if args.action == "check":
            port = args.port if args.port is not None else 22
            print(f"OK - {args.user}@{args.host}:{port}", flush=True)
            return EXIT_OK
        command_timeout = _command_timeout_arg(args)
        command_hard_timeout = _command_hard_timeout_arg(args)
        return run_command(
            client,
            args.command,
            command_timeout=command_timeout,
            command_hard_timeout=command_hard_timeout,
        )
    except TimeoutError as exc:
        print(f"Command timed out: {exc}", file=sys.stderr)
        return EXIT_CMD_ERROR
    except Exception as exc:
        print(f"Command failed: {exc}", file=sys.stderr)
        return EXIT_CMD_ERROR


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # --all: parallel execution across all profiles
    if getattr(args, "run_all", False):
        return _run_all_main_action(args)

    connection = _open_main_client(args)
    if connection.exit_code is not None:
        return connection.exit_code

    if connection.client is None:
        print("Connection failed: SSH client was not created.", file=sys.stderr)
        return EXIT_SSH_ERROR
    try:
        return _run_main_action(args, connection.client)
    finally:
        connection.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
