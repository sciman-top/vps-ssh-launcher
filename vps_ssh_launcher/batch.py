"""Read-only multi-profile admission, bounded dispatch and ordered reporting."""

from __future__ import annotations

import argparse
import logging
import shlex
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .config import (
    resolve_auth_for_entry,
    has_cli_auth_override,
    build_connect_namespace,
    load_config,
    resolve_entry_config_path,
    validate_profile,
)
from .connection import classify_connection_error, connect_with_retry
from .execution import (
    _execute_capture,
    command_timeout_arg,
    command_hard_timeout_arg,
    execute_remote_capture,
    write_stream,
)
from .contracts import (
    CaptureSubmitter,
    ClosableRemoteCommandClient,
    DEFAULT_RUN_ALL_MAX_WORKERS,
    RUN_ALL_OUTPUT_LIMIT,
    EXIT_OK,
    EXIT_CMD_ERROR,
    EXIT_SSH_ERROR,
)

logger = logging.getLogger("ssh_tool")

RUN_ALL_READ_ONLY_INVOCATIONS = frozenset(
    {
        ("uptime",),
        ("uname",),
        ("uname", "-a"),
        ("df",),
        ("df", "-h"),
        ("df", "-Pk"),
        ("free",),
        ("free", "-h"),
        ("free", "-m"),
        ("hostname",),
        ("id",),
        ("whoami",),
        ("pwd",),
        ("true",),
        ("test",),
        ("ss", "-ltn"),
        ("ss", "-ltnp"),
        ("ps", "aux"),
        ("docker", "ps"),
        ("docker", "images"),
        ("docker", "version"),
    }
)
RUN_ALL_SHELL_METACHARS = frozenset(";&|><$`(){}\n\r")
RUN_ALL_SYSTEMCTL_VERBS = frozenset(
    {"cat", "is-active", "is-enabled", "show", "status"}
)
RUN_ALL_BOOLEAN_LAUNCHER_OPTIONS = frozenset(
    {
        "--allow-agent",
        "--strict-host-key-checking",
        "--allow-unknown-host-key",
    }
)


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
    submit: CaptureSubmitter | None = None


def _safe_command_error(exc: BaseException) -> str:
    """Keep class-level diagnosis without echoing provider exception text.

    TLS, proxy and transport exceptions can embed request bytes or credentials.
    The full exception remains available only through debug tracebacks.
    """
    name = type(exc).__name__
    if not name.isidentifier() or len(name) > 80:
        name = "Exception"
    return f"Command or transport failed ({name}); see diagnostics."


def _validate_run_all_command(command: str) -> None:
    """Reject commands that are unsafe to fan out concurrently.

    This is deliberately a conservative admission check, not a shell parser.
    Callers can still run an intentional command on one host through the normal
    ``run`` path.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("--all requires a non-empty read-only command.")
    if any(char in RUN_ALL_SHELL_METACHARS for char in command):
        raise ValueError(
            "--all accepts one read-only command only; shell operators, "
            "substitutions, and redirections are blocked."
        )
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"--all command has invalid quoting: {exc}") from exc
    if not argv:
        raise ValueError("--all requires a non-empty read-only command.")

    # A copied launcher invocation must review the command the host will
    # actually execute, not whichever token happens to match at the end.
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        option = argv[index]
        if option in RUN_ALL_BOOLEAN_LAUNCHER_OPTIONS:
            index += 1
            continue
        raise ValueError(
            "--all accepts only approved read-only systemctl and diagnostic "
            "invocations; use single-host run for other commands."
        )
    if index < len(argv) and argv[index] == "run":
        raise ValueError(
            "--all accepts only a bare read-only diagnostic; pass launcher "
            "options to this invocation and omit 'run --command'."
        )
    if index >= len(argv):
        raise ValueError("--all requires a command after launcher options.")
    argv = argv[index:]

    if tuple(argv) in RUN_ALL_READ_ONLY_INVOCATIONS:
        return
    # Only these verbs with literal unit names are admitted. Options, executable
    # paths and shell expansion are deliberately outside this small policy.
    if (
        argv[0] == "systemctl"
        and len(argv) >= 3
        and argv[1] in RUN_ALL_SYSTEMCTL_VERBS
        and all(
            unit
            and not unit.startswith("-")
            and all(c.isascii() and (c.isalnum() or c in "_.@-") for c in unit)
            for unit in argv[2:]
        )
    ):
        return
    raise ValueError(
        "--all accepts only approved read-only systemctl and diagnostic "
        "invocations; use single-host run for other commands."
    )


def _run_all_config_file(args: argparse.Namespace) -> Path:
    config_path = resolve_entry_config_path(args.config)
    if not config_path:
        raise FileNotFoundError("Config file not found. Create target.json.")
    return config_path


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


def _run_profile_command(
    name: str,
    entry: dict[str, Any],
    context: ProfileRunContext,
) -> ProfileRunResult:
    run_started = time.monotonic()
    if context.submit is not None:
        try:
            code, out, err, stdout_truncated, stderr_truncated = _execute_capture(
                context.submit,
                context.command,
                command_timeout=context.command_timeout,
                command_hard_timeout=context.command_hard_timeout,
                capture_limit=RUN_ALL_OUTPUT_LIMIT,
            )
        except TimeoutError as exc:
            return _profile_error_result(
                name,
                EXIT_CMD_ERROR,
                "command_timeout",
                str(exc),
                run_started,
            )
        except Exception as exc:
            logger.debug("Unexpected local command error on '%s'", name, exc_info=True)
            return _profile_error_result(
                name,
                EXIT_CMD_ERROR,
                "command_error",
                _safe_command_error(exc),
                run_started,
            )
        return ProfileRunResult(
            name=name,
            code=code,
            stdout=out,
            stderr=err,
            category="ok" if code == EXIT_OK else "remote_nonzero",
            elapsed=time.monotonic() - run_started,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    client: ClosableRemoteCommandClient | None = None
    connection_error: tuple[int, str, str] | None = None
    try:
        ns = build_connect_namespace(
            entry,
            profile_name=name,
            base_args=context.args,
            config_dir=context.config_dir,
            strict_host_key_checking=getattr(
                context.args, "strict_host_key_checking", True
            ),
        )
        resolve_auth_for_entry(
            entry,
            context.args,
            config_dir=context.config_dir,
        )
        client = connect_with_retry(ns)
    except Exception as exc:
        classified = classify_connection_error(exc, target_known=True)
        if classified.category == "connect_error":
            logger.debug("Unexpected error connecting to '%s'", name, exc_info=True)
        connection_error = (
            classified.code,
            classified.category,
            (
                _safe_command_error(exc)
                if classified.category == "connect_error"
                else str(exc)
            ),
        )

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
            code, out, err, stdout_truncated, stderr_truncated = execute_remote_capture(
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
                error=_safe_command_error(exc),
                started_at=run_started,
            )
        return error_result
    finally:
        if client is not None:
            client.close()


def _print_prefixed_lines(
    name: str,
    text: str,
    *,
    stream: Any | None = None,
    trailing_newline: bool = True,
) -> None:
    if not text:
        return
    if stream is None:
        stream = sys.stdout
    lines = text.splitlines(keepends=True)
    output = "".join(f"[{name}] {line}" for line in lines)
    if trailing_newline and lines and not lines[-1].endswith(("\n", "\r")):
        output += "\n"
    # Results have already been captured with a cap; one write avoids flushing
    # the terminal thousands of times for a single completed host.
    write_stream(stream, output)


def _print_profile_result(result: ProfileRunResult) -> None:
    # Streams are prefixed by physical content lines. Passing the captured
    # separators back through Python's text stream would translate LF to CRLF
    # on Windows and produce stray blank, prefix-only lines. Helpers that parse
    # the summary would then see different line counts on different hosts.
    stdout = result.stdout.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    stderr = result.stderr.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    _print_prefixed_lines(result.name, stdout, trailing_newline=True)
    _print_prefixed_lines(
        result.name,
        stderr,
        stream=sys.stderr,
        trailing_newline=True,
    )
    if result.stdout_truncated:
        print(f"[{result.name}] stdout truncated", flush=True)
    if result.stderr_truncated:
        print(f"[{result.name}] stderr truncated", flush=True)
    if result.code != EXIT_OK:
        print(f"[{result.name}] exit code: {result.code}", flush=True)
    print(f"[{result.name}] elapsed: {result.elapsed:.2f}s", flush=True)


def _summarize_run_on_all_results(
    results: list[ProfileRunResult],
    *,
    expected_profiles: int | None = None,
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

    if expected_profiles is not None:
        missing = max(0, expected_profiles - len(results))
        if missing:
            category_counts["missing_result"] = missing
            max_code = max(max_code, EXIT_CMD_ERROR)
    return max_code, category_counts, exit_code_counts, failed_profiles


def _print_run_on_all_results(
    results: list[ProfileRunResult],
    *,
    started_at: float,
    expected_profiles: int | None = None,
) -> int:
    results.sort(key=lambda result: result.name)
    for result in results:
        _print_profile_result(result)

    max_code, category_counts, exit_code_counts, failed_profiles = (
        _summarize_run_on_all_results(
            results,
            expected_profiles=expected_profiles,
        )
    )
    total = len(results) + category_counts.get("missing_result", 0)
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
    """Run one admitted read-only command on all profiles in parallel."""
    started_at = time.monotonic()
    if getattr(args, "profile", None):
        raise ValueError(
            "--all targets every profile in the config; --profile cannot "
            "narrow it. Use the single-host path for a specific profile."
        )
    _validate_run_all_command(command)
    command_timeout = command_timeout_arg(args)
    command_hard_timeout = command_hard_timeout_arg(args)
    config_file = _run_all_config_file(args)
    profiles = _load_profiles_for_run_all(config_file)

    require_auth = not has_cli_auth_override(args)
    validated_profiles: dict[str, dict[str, Any]] = {}
    for name, entry in profiles.items():
        validate_profile(entry, name, require_auth=require_auth)
        validated_profiles[name] = cast(dict[str, Any], entry)

    # Collect results in parallel, print sequentially
    results: list[ProfileRunResult] = []
    max_workers = min(len(validated_profiles), DEFAULT_RUN_ALL_MAX_WORKERS)
    context = ProfileRunContext(
        args=args,
        config_dir=config_file.parent,
        command=command,
        command_timeout=command_timeout,
        command_hard_timeout=command_hard_timeout,
    )
    pool = ThreadPoolExecutor(max_workers=max_workers)
    future_to_name: dict[Future[ProfileRunResult], str] = {}
    try:
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
                        error=_safe_command_error(exc),
                        started_at=started_at,
                    )
                )
    except BaseException:
        # A read-only run can be interrupted by Ctrl-C or a caller's failure.
        # Workers own their connections and check the command deadline, so do
        # not wait for the context manager to join them an unbounded time.
        for pending in future_to_name:
            pending.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True, cancel_futures=False)

    return _print_run_on_all_results(
        results,
        started_at=started_at,
        expected_profiles=len(validated_profiles),
    )
