"""Command-line parsing and dispatch; stable facade for legacy Python callers.

Exit codes: 0 success, 1 SSH/auth, 2 configuration, 3 connection timeout,
4 network, 5 command failure. Single-host run returns remote codes 0-255.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from .batch import (
    ProfileRunContext as ProfileRunContext,
    ProfileRunResult as ProfileRunResult,
    RUN_ALL_READ_ONLY_INVOCATIONS as RUN_ALL_READ_ONLY_INVOCATIONS,
    RUN_ALL_SHELL_METACHARS as RUN_ALL_SHELL_METACHARS,
    run_on_all as run_on_all,
)
from .config import (
    SOURCE_ROOT as SOURCE_ROOT,
    APP_CONFIG_DIR as APP_CONFIG_DIR,
    APP_CONFIG_FILE as APP_CONFIG_FILE,
    apply_config as apply_config,
    load_config as load_config,
    resolve_default_config_path as resolve_default_config_path,
    select_profile as select_profile,
    validate_profile as validate_profile,
)
from .connection import (
    APP_KNOWN_HOSTS_FILE as APP_KNOWN_HOSTS_FILE,
    OPENSSH_KNOWN_HOSTS_FILE as OPENSSH_KNOWN_HOSTS_FILE,
    RETRYABLE_SOCKET_ERROR_CODES as RETRYABLE_SOCKET_ERROR_CODES,
    ConnectionErrorClassification as ConnectionErrorClassification,
    _classify_connection_error,
    connect_client as connect_client,
    connect_with_retry as connect_with_retry,
)
from .execution import (
    _command_timeout_arg,
    _command_hard_timeout_arg,
    exec_remote as exec_remote,
    run_command as run_command,
)
from .contracts import (
    CONNECT_TIMEOUT as CONNECT_TIMEOUT,
    CONNECT_RETRIES as CONNECT_RETRIES,
    KEEPALIVE_INTERVAL as KEEPALIVE_INTERVAL,
    MIN_PORT as MIN_PORT,
    MAX_PORT as MAX_PORT,
    MAX_REMOTE_EXIT_CODE as MAX_REMOTE_EXIT_CODE,
    DEFAULT_RUN_ALL_MAX_WORKERS as DEFAULT_RUN_ALL_MAX_WORKERS,
    RUN_ALL_OUTPUT_LIMIT as RUN_ALL_OUTPUT_LIMIT,
    CHANNEL_POLL_INTERVAL as CHANNEL_POLL_INTERVAL,
    CHANNEL_READ_BURST as CHANNEL_READ_BURST,
    CMD_TIMEOUT as CMD_TIMEOUT,
    EXIT_OK as EXIT_OK,
    EXIT_CONFIG_ERROR as EXIT_CONFIG_ERROR,
    EXIT_CMD_ERROR as EXIT_CMD_ERROR,
    EXIT_SSH_ERROR as EXIT_SSH_ERROR,
    EXIT_NETWORK_ERROR as EXIT_NETWORK_ERROR,
    EXIT_TIMEOUT as EXIT_TIMEOUT,
    __version__ as __version__,
    ClosableRemoteCommandClient as ClosableRemoteCommandClient,
    RemoteCommandClient as RemoteCommandClient,
)

logger = logging.getLogger("ssh_tool")


@dataclass(frozen=True)
class MainConnectionResult:
    client: ClosableRemoteCommandClient | None
    exit_code: int | None


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
    password_group = parser.add_mutually_exclusive_group()
    password_group.add_argument(
        "--password",
        help="SSH password (visible in process listings; prefer --password-stdin).",
    )
    password_group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the SSH password from one line on stdin.",
    )
    parser.add_argument("--key", help="SSH private key path")
    parser.add_argument(
        "--allow-agent",
        action="store_true",
        help="Use SSH agent for authentication",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    host_key_group = parser.add_mutually_exclusive_group()
    host_key_group.add_argument(
        "--strict-host-key-checking",
        dest="strict_host_key_checking",
        action="store_true",
        default=True,
        help="Reject unknown host keys (default)",
    )
    host_key_group.add_argument(
        "--allow-unknown-host-key",
        dest="strict_host_key_checking",
        action="store_false",
        help="Compatibility-only TOFU mode; accepts and persists an unknown host key",
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

    sub.add_parser("check", help="Test connectivity")

    return parser


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


def _harden_stream_errors() -> None:
    """Never crash on characters the target stream encoding cannot represent.

    Piped stdout on legacy Windows code pages raises UnicodeEncodeError in the
    middle of streaming remote output; degrade to escapes instead.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "password_stdin", False):
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            print(
                "Config error: --password-stdin received an empty password.",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        args.password = password

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    _harden_stream_errors()

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
