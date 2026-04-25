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
import json
import logging
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import paramiko

# --- Tunables ---
CONNECT_TIMEOUT = 8  # TCP + SSH handshake + auth (seconds)
CMD_TIMEOUT = 60  # Remote command timeout (seconds)
KEEPALIVE_INTERVAL = 30  # SSH keep-alive (seconds), 0 = disabled
CONNECT_RETRIES = 2  # Extra retries for transient errors

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
SENSITIVE_COMMAND_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
)

_paramiko_module: Any | None = None


class RemoteCommandClient(Protocol):
    def exec_command(self, command: str) -> tuple[Any, Any, Any]: ...


class ClosableRemoteCommandClient(RemoteCommandClient, Protocol):
    def close(self) -> None: ...


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


def _coerce_port(value: Any, *, context: str) -> int:
    """Normalize a port value and fail fast on invalid input."""
    if isinstance(value, bool):
        raise ValueError(f"{context}: port must be an integer 1-65535, got {value!r}.")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdigit():
        port = int(value.strip())
    else:
        raise ValueError(f"{context}: port must be an integer 1-65535, got {value!r}.")
    if not 1 <= port <= 65535:
        raise ValueError(f"{context}: port must be an integer 1-65535, got {port!r}.")
    return port


def _cli_password_arg(args: Any) -> str | None:
    password = getattr(args, "password", None)
    return password if isinstance(password, str) and password else None


def _cli_key_arg(args: Any) -> str | None:
    key = getattr(args, "key", None)
    return key.strip() if isinstance(key, str) and key.strip() else None


def _has_cli_auth_override(args: Any) -> bool:
    return bool(
        getattr(args, "allow_agent", False)
        or _cli_password_arg(args) is not None
        or _cli_key_arg(args) is not None
    )


def _redact_command_for_log(command: str) -> str:
    lowered = command.lower()
    if any(marker in lowered for marker in SENSITIVE_COMMAND_MARKERS):
        return "<redacted command containing sensitive marker>"
    return command


def _drain_channel(channel: Any) -> tuple[str, str, int]:
    """Read stdout and stderr without deadlocking the SSH channel.

    Uses a two-tier timeout:
    - Idle timeout (CMD_TIMEOUT): resets on every data received.
    - Hard total timeout (3x CMD_TIMEOUT): absolute upper bound regardless of activity.
    """
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    idle_deadline: float | None = None
    hard_deadline: float | None = None
    if CMD_TIMEOUT > 0:
        now = time.monotonic()
        idle_deadline = now + CMD_TIMEOUT
        hard_deadline = now + CMD_TIMEOUT * 3

    while True:
        progressed = False

        while channel.recv_ready():
            out_chunks.append(channel.recv(32768).decode(errors="replace"))
            progressed = True
            if idle_deadline is not None:
                idle_deadline = time.monotonic() + CMD_TIMEOUT

        while channel.recv_stderr_ready():
            err_chunks.append(channel.recv_stderr(32768).decode(errors="replace"))
            progressed = True
            if idle_deadline is not None:
                idle_deadline = time.monotonic() + CMD_TIMEOUT

        if (
            channel.exit_status_ready()
            and not channel.recv_ready()
            and not channel.recv_stderr_ready()
        ):
            break

        now = time.monotonic()
        if hard_deadline is not None and now > hard_deadline:
            raise socket.timeout(
                f"Remote command exceeded hard timeout of {CMD_TIMEOUT * 3}s."
            )
        if idle_deadline is not None and now > idle_deadline:
            raise socket.timeout(f"Remote command timed out after {CMD_TIMEOUT}s idle.")

        if not progressed:
            time.sleep(0.05)

    return "".join(out_chunks), "".join(err_chunks), channel.recv_exit_status()


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
        "--all",
        action="store_true",
        dest="run_all",
        help="Run on all profiles in parallel",
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
        config_relative = config_dir / key_path
        legacy_cwd_relative = Path.cwd() / key_path
        if config_relative.exists() or not legacy_cwd_relative.exists():
            return str(config_relative)
        logger.debug(
            "Using legacy cwd-relative key path for compatibility: %s",
            legacy_cwd_relative,
        )
        return str(legacy_cwd_relative)
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

    print("Available VPS profiles:")
    names = list(profiles)
    for i, n in enumerate(names, 1):
        p = profiles[n]
        print(
            f"  {i}. {n} "
            f"({p.get('user', '?')}@{p.get('host', '?')}:{p.get('port', 22)})"
        )

    try:
        choice = input("Select VPS (number or name): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        raise ValueError("Cancelled.") from None

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


def apply_config(args: argparse.Namespace) -> None:
    """Merge config file into CLI args (CLI takes precedence)."""
    script_dir = Path(__file__).resolve().parent
    default_config = resolve_default_config_path(script_dir)
    config_path = args.config or (str(default_config) if default_config else None)
    if not config_path:
        return

    config_file = Path(config_path).expanduser()
    config = load_config(config_file)

    if "profiles" in config:
        profiles = config["profiles"]
        if not isinstance(profiles, dict) or not profiles:
            raise ValueError("'profiles' must be a non-empty object.")
        profiles = cast(dict[str, Any], profiles)
        default_name = config.get("default")
        if default_name is not None and not isinstance(default_name, str):
            raise ValueError("'default' must be a string when set.")
        name = select_profile(profiles, default_name, args.profile)
        entry = profiles[name]
    else:
        entry = config
        name = "(root)"

    cli_password = _cli_password_arg(args)
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
    if cli_key is not None:
        args.key = cli_key
    if args.password is None and cli_key is None:
        args.password = _resolve_password(entry)
    if args.key is None and cli_password is None:
        args.key = _resolve_key(entry, config_dir=config_file.parent)


# ── Connection ──────────────────────────────────────────────


def connect_client(args: Any) -> "paramiko.SSHClient":
    host = args.host.strip() if isinstance(args.host, str) else args.host
    user = args.user.strip() if isinstance(args.user, str) else args.user
    if not host or not user:
        raise ValueError("Missing host or user. Provide via CLI or target.json.")
    use_agent = getattr(args, "allow_agent", False)
    password = args.password if isinstance(args.password, str) else None
    key = _cli_key_arg(args)
    if not password and not key and not use_agent:
        raise ValueError(
            "No auth method. Use --password, --key, --allow-agent, "
            "or set credentials in target.json."
        )

    port = _coerce_port(
        args.port if args.port is not None else 22, context="Connection"
    )
    logger.debug("Connecting to %s@%s:%d", user, host, port)

    key_path: Path | None = None
    if key:
        key_path = Path(key).expanduser()
        if not key_path.exists():
            raise FileNotFoundError(f"SSH key file not found: {key_path}")

    paramiko_module = _load_paramiko()

    # socket.create_connection supports both IPv4 and IPv6
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)

    client = paramiko_module.SSHClient()
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        if getattr(args, "strict_host_key_checking", False):
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko_module.RejectPolicy())
        else:
            # Compatibility default; callers can opt into strict host key checking.
            client.set_missing_host_key_policy(paramiko_module.AutoAddPolicy())  # nosec

        connect_kwargs: dict[str, Any] = dict(
            hostname=host,
            port=port,
            username=user,
            sock=sock,
            timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
            allow_agent=use_agent,
            look_for_keys=False,
        )

        if key_path:
            connect_kwargs["key_filename"] = str(key_path)
        elif password:
            connect_kwargs["password"] = password

        client.connect(**connect_kwargs)
    except Exception:
        client.close()
        sock.close()
        raise

    # Keep-alive prevents idle disconnects on long-running commands
    if KEEPALIVE_INTERVAL > 0:
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(KEEPALIVE_INTERVAL)

    logger.debug("Connected to %s@%s:%d", user, host, port)
    return client


def connect_with_retry(args: Any) -> "paramiko.SSHClient":
    """Connect with optional retry for transient network errors."""
    for attempt in range(1 + CONNECT_RETRIES):
        try:
            return connect_client(args)
        except FileNotFoundError:
            raise
        except (TimeoutError, socket.timeout, OSError) as exc:
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


def exec_remote(
    client: RemoteCommandClient,
    command: str,
) -> tuple[int, str, str]:
    """Execute command, return (exit_code, stdout_text, stderr_text)."""
    logger.debug("Running: %s", _redact_command_for_log(command))
    # This tool intentionally executes the explicit command supplied by the user.
    stdin, stdout, _stderr = client.exec_command(command)  # nosec
    try:
        stdin.close()  # Prevent hangs on commands that read stdin
        stdout.channel.settimeout(CMD_TIMEOUT)

        # Drain both streams incrementally to avoid filling one buffer while
        # waiting on the other. This keeps stderr-heavy commands safe.
        out, err, code = _drain_channel(stdout.channel)
        return code, out, err
    finally:
        try:
            stdout.close()
        finally:
            _stderr.close()


def run_command(client: RemoteCommandClient, command: str) -> int:
    """Execute command, stream output. Returns exit code."""
    code, out, err = exec_remote(client, command)
    if out:
        print(out, end="", flush=True)
    if err:
        print(err, end="", file=sys.stderr, flush=True)
    logger.debug("Exit code: %d", code)
    return code


def run_on_all(args: argparse.Namespace, command: str) -> int:
    """Run command on all config profiles in parallel."""
    started_at = time.monotonic()
    script_dir = Path(__file__).resolve().parent
    config_path = (
        Path(args.config) if args.config else resolve_default_config_path(script_dir)
    )
    if not config_path:
        raise FileNotFoundError("Config file not found. Create target.json.")
    config_file = config_path.expanduser()
    config = load_config(config_file)
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("No profiles found in config.")
    profiles = cast(dict[str, Any], profiles)

    cli_password = _cli_password_arg(args)
    cli_key = _cli_key_arg(args)
    require_auth = not _has_cli_auth_override(args)
    for name, entry in profiles.items():
        validate_profile(entry, name, require_auth=require_auth)

    def _run_one(
        name: str, entry: dict[str, Any]
    ) -> tuple[str, int, str, str, str, float]:
        run_started = time.monotonic()
        client: ClosableRemoteCommandClient | None = None
        try:
            if cli_key is not None:
                profile_password = None
                profile_key = cli_key
            elif cli_password is not None:
                profile_password = cli_password
                profile_key = None
            else:
                profile_password = _resolve_password(entry)
                profile_key = _resolve_key(entry, config_dir=config_file.parent)

            ns = argparse.Namespace(
                host=cast(str, entry["host"]).strip(),
                port=_coerce_port(entry.get("port", 22), context=f"Profile '{name}'"),
                user=cast(str, entry["user"]).strip(),
                password=profile_password,
                key=profile_key,
                allow_agent=getattr(args, "allow_agent", False),
                strict_host_key_checking=getattr(
                    args, "strict_host_key_checking", False
                ),
            )
            client = connect_with_retry(ns)
        except (ValueError, FileNotFoundError) as exc:
            return (
                name,
                EXIT_CONFIG_ERROR,
                "",
                str(exc),
                "config_error",
                time.monotonic() - run_started,
            )
        except (TimeoutError, socket.timeout) as exc:
            return (
                name,
                EXIT_TIMEOUT,
                "",
                str(exc),
                "connect_timeout",
                time.monotonic() - run_started,
            )
        except OSError as exc:
            return (
                name,
                EXIT_NETWORK_ERROR,
                "",
                str(exc),
                "network_error",
                time.monotonic() - run_started,
            )
        except Exception as exc:
            if _is_paramiko_auth_error(exc):
                return (
                    name,
                    EXIT_SSH_ERROR,
                    "",
                    str(exc),
                    "auth_error",
                    time.monotonic() - run_started,
                )
            logger.debug("Unexpected error connecting to '%s'", name, exc_info=True)
            return (
                name,
                EXIT_SSH_ERROR,
                "",
                str(exc),
                "connect_error",
                time.monotonic() - run_started,
            )

        try:
            code, out, err = exec_remote(client, command)
            category = "ok" if code == EXIT_OK else "remote_nonzero"
            return name, code, out, err, category, time.monotonic() - run_started
        except socket.timeout as exc:
            return (
                name,
                EXIT_CMD_ERROR,
                "",
                str(exc),
                "command_timeout",
                time.monotonic() - run_started,
            )
        except Exception as exc:
            logger.debug("Unexpected error executing on '%s'", name, exc_info=True)
            return (
                name,
                EXIT_CMD_ERROR,
                "",
                str(exc),
                "command_error",
                time.monotonic() - run_started,
            )
        finally:
            if client is not None:
                client.close()

    # Collect results in parallel, print sequentially
    results: list[tuple[str, int, str, str, str, float]] = []
    max_workers = max(1, min(len(profiles), 32))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_run_one, n, e) for n, e in profiles.items()]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort()  # deterministic output order by profile name
    max_code = EXIT_OK
    category_counts: dict[str, int] = {}
    for name, code, out, err, category, elapsed in results:
        if out:
            for line in out.splitlines(keepends=True):
                print(f"[{name}] {line}", end="", flush=True)
        if err:
            for line in err.splitlines(keepends=True):
                print(f"[{name}] {line}", end="", file=sys.stderr, flush=True)
        if code != EXIT_OK:
            print(f"[{name}] exit code: {code}", flush=True)
        print(f"[{name}] elapsed: {elapsed:.2f}s", flush=True)
        category_counts[category] = category_counts.get(category, 0) + 1
        max_code = max(max_code, code)

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

    return max_code


# ── Main ────────────────────────────────────────────────────


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    target = "unknown target"

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        # --all: parallel execution across all profiles
        if getattr(args, "run_all", False):
            return run_on_all(args, args.command)

        apply_config(args)
        target = f"{args.user}@{args.host}:{args.port or 22}"
        client = connect_with_retry(args)
    except TimeoutError as exc:
        print(f"[{target}] Connection timed out: {exc}", file=sys.stderr)
        return EXIT_TIMEOUT
    except (ValueError, FileNotFoundError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except OSError as exc:
        if target == "unknown target":
            print(f"Config error: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        print(f"[{target}] Network error: {exc}", file=sys.stderr)
        print("  Hint: check connectivity and firewall rules.", file=sys.stderr)
        return EXIT_NETWORK_ERROR
    except Exception as exc:
        if _is_paramiko_auth_error(exc):
            print(f"[{target}] Auth failed: {exc}", file=sys.stderr)
            print(
                "  Hint: verify password/key in target.json or set password_env.",
                file=sys.stderr,
            )
            return EXIT_SSH_ERROR
        print(f"[{target}] Connection failed: {exc}", file=sys.stderr)
        return EXIT_SSH_ERROR

    try:
        if args.action == "check":
            port = args.port if args.port is not None else 22
            print(f"OK - {args.user}@{args.host}:{port}", flush=True)
            return EXIT_OK
        return run_command(client, args.command)
    except socket.timeout:
        print(f"Command timed out after {CMD_TIMEOUT}s.", file=sys.stderr)
        return EXIT_CMD_ERROR
    except Exception as exc:
        print(f"Command failed: {exc}", file=sys.stderr)
        return EXIT_CMD_ERROR
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
