#!/usr/bin/env python3
"""Automate vasma installation."""

from __future__ import annotations

import argparse
import io
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

INSTALL_SCRIPT = Path("/etc/v2ray-agent/install.sh")
INSTALL_DOMAIN = os.environ.get("VPS_DOMAIN", "fq.sciman.top")
SPAWN_TIMEOUT = 1800
DEFAULT_EXPECT_TIMEOUT = 45
EXPECT_TIMEOUT_ENV = "VPS_AUTO_INSTALL_EXPECT_TIMEOUT"
MIN_EXPECT_TIMEOUT = 1
MAX_EXPECT_TIMEOUT_EXCLUSIVE = 60
MAX_RESPONSES = 30
MAX_GENERIC_SELECT_REPEAT = 3
GENERIC_SELECT_PROMPT = r"请选择:"
CUSTOM_INSTALL_PROMPT_COUNT = 1
XRAY_CORE_PROMPT_COUNT = 2
CUSTOM_INSTALL_MENU_OPTION = "2"
XRAY_CORE_MENU_OPTION = "1"
EXECUTE_ENV = "VPS_AUTO_INSTALL_EXECUTE"


def _generic_select_response(count: int) -> str:
    if count == CUSTOM_INSTALL_PROMPT_COUNT:
        return CUSTOM_INSTALL_MENU_OPTION  # Main menu: custom install
    if count == XRAY_CORE_PROMPT_COUNT:
        return XRAY_CORE_MENU_OPTION  # Core: Xray
    return ""


@dataclass(frozen=True)
class PromptDriveResult:
    sent_count: int
    stop_reason: str
    transcript: str = ""


def _load_pexpect() -> Any:
    try:
        import pexpect
    except ImportError as exc:
        print(
            "ERROR: pexpect is required for auto_install.py. "
            "Install it with `pip install pexpect` on the target Linux host.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return pexpect


def _specific_prompts(install_domain: str) -> dict[str, str]:
    return {
        r"请选择\[多选\]": "7,12",
        r"读取到上次安装的配置，是否使用": "n",
        r"读取到上次安装设置的Reality域名，是否使用": "y",
        r"Reality目标域名": "n",
        r"请输入目标域名": "",
        r"DNS API": "n",
        r"请选择.*使用默认": "",
        r"域名:": install_domain,
        r"UUID:": "",
        r"用户名:": "",
        r"路径:": "",
        r"伪装站点.*重新安装": "n",
        r"是否重新安装": "n",
        r"是否使用.*上次": "n",
        r"是否使用.*端口": "y",
        r"是否使用.*path": "y",
        r"端口:": "",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drive /etc/v2ray-agent/install.sh. This mutates the remote VPS and "
            "is disabled unless --execute or VPS_AUTO_INSTALL_EXECUTE=1 is set."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the remote v2ray-agent installer.",
    )
    parser.add_argument(
        "--expect-timeout",
        type=int,
        default=None,
        help="Seconds to wait for a known prompt before aborting.",
    )
    return parser.parse_args(argv)


def _resolve_expect_timeout(cli_value: int | None) -> int:
    if cli_value is not None:
        return cli_value

    raw_value = os.environ.get(EXPECT_TIMEOUT_ENV)
    if raw_value is None:
        return DEFAULT_EXPECT_TIMEOUT

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{EXPECT_TIMEOUT_ENV} must be an integer, got {raw_value!r}."
        ) from exc


def _prompt_plan(install_domain: str) -> tuple[list[str], list[str]]:
    specific_prompts = _specific_prompts(install_domain)
    patterns = [*specific_prompts.keys(), GENERIC_SELECT_PROMPT]
    responses = list(specific_prompts.values())
    return patterns, responses


def _response_for_log(response: str) -> str:
    if not response:
        return "<empty>"
    return "<redacted>"


def _drive_prompts(
    child: Any,
    pexpect: Any,
    *,
    install_domain: str = INSTALL_DOMAIN,
    expect_timeout: int = DEFAULT_EXPECT_TIMEOUT,
    max_responses: int = MAX_RESPONSES,
) -> PromptDriveResult:
    patterns, responses = _prompt_plan(install_domain)
    generic_select_count = 0
    sent_count = 0
    transcript_buffer = io.StringIO()
    child.logfile_read = transcript_buffer

    while sent_count < max_responses:
        try:
            i = child.expect(
                cast(Any, [*patterns, pexpect.EOF, pexpect.TIMEOUT]),
                timeout=expect_timeout,
            )
        except Exception as exc:
            print(f"\n=== Error: {exc} ===")
            return PromptDriveResult(
                sent_count=sent_count,
                stop_reason="error",
                transcript=transcript_buffer.getvalue(),
            )

        if i < len(patterns) - 1:
            child.sendline(responses[i])
            sent_count += 1
            print(
                f"\n>>> [#{sent_count}] Specific #{i}, "
                f"Sent: {_response_for_log(responses[i])} <<<\n"
            )
            continue

        if i == len(patterns) - 1:
            generic_select_count += 1
            if generic_select_count > MAX_GENERIC_SELECT_REPEAT:
                print(
                    f"\n=== Repeated generic select prompt after {sent_count} responses ==="
                )
                return PromptDriveResult(
                    sent_count=sent_count,
                    stop_reason="repeated_prompt",
                    transcript=transcript_buffer.getvalue(),
                )
            resp = _generic_select_response(generic_select_count)
            child.sendline(resp)
            sent_count += 1
            print(
                f"\n>>> [#{sent_count}] 请选择 #{generic_select_count}, "
                f"Sent: {_response_for_log(resp)} <<<\n"
            )
            continue

        if i == len(patterns):
            print(f"\n=== EOF after {sent_count} responses ===")
            return PromptDriveResult(
                sent_count=sent_count,
                stop_reason="eof",
                transcript=transcript_buffer.getvalue(),
            )

        print(f"\n=== Timeout after {sent_count} responses ===")
        return PromptDriveResult(
            sent_count=sent_count,
            stop_reason="timeout",
            transcript=transcript_buffer.getvalue(),
        )

    print(f"\n=== Reached max responses ({max_responses}) ===")
    return PromptDriveResult(
        sent_count=sent_count,
        stop_reason="max_responses",
        transcript=transcript_buffer.getvalue(),
    )


def _render_transcript_summary(transcript: str) -> str:
    if not transcript.strip():
        return "<empty transcript>"
    return "<redacted transcript captured>"


def _wait_for_child_exit(child: Any, pexpect: Any, *, expect_timeout: int) -> None:
    if child.isalive():
        with suppress(pexpect.TIMEOUT):
            child.expect(pexpect.EOF, timeout=expect_timeout)


def _terminate_child(child: Any) -> None:
    if not child.isalive():
        return
    try:
        child.close(force=True)
    except TypeError:
        child.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        expect_timeout = _resolve_expect_timeout(args.expect_timeout)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.execute and os.environ.get(EXECUTE_ENV) != "1":
        print(
            "ERROR: auto_install.py drives /etc/v2ray-agent/install.sh and can "
            "rewrite live VPS proxy config. Re-run with --execute only after a "
            "fresh backup and a plan to restore xray/nginx/subscription state.",
            file=sys.stderr,
        )
        return 2

    if not MIN_EXPECT_TIMEOUT <= expect_timeout < MAX_EXPECT_TIMEOUT_EXCLUSIVE:
        print(
            "ERROR: --expect-timeout must be between 1 and 59 seconds so unknown "
            "installer prompts abort before ssh_tool.py's remote command idle timeout.",
            file=sys.stderr,
        )
        return 2

    pexpect = _load_pexpect()

    if not INSTALL_SCRIPT.exists():
        print("ERROR: install.sh not found")
        return 1

    child = pexpect.spawn(
        "/bin/bash",
        [str(INSTALL_SCRIPT)],
        timeout=SPAWN_TIMEOUT,
        encoding="utf-8",
        env=cast(
            Any,
            {
                "LANG": "en_US.UTF-8",
                "TERM": "xterm",
                "HOME": "/root",
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
        ),
    )
    drive_result = _drive_prompts(
        child,
        pexpect,
        install_domain=INSTALL_DOMAIN,
        expect_timeout=expect_timeout,
        max_responses=MAX_RESPONSES,
    )
    if drive_result.stop_reason not in {"eof"}:
        _terminate_child(child)
    else:
        _wait_for_child_exit(child, pexpect, expect_timeout=expect_timeout)

    child.close()
    print(
        f"\n=== Transcript: {_render_transcript_summary(drive_result.transcript)} ==="
    )
    exit_code = cast(int | None, child.exitstatus)
    if drive_result.stop_reason != "eof":
        print(
            f"\n=== Aborted installer after stop={drive_result.stop_reason}; "
            "child process was terminated to avoid partial unattended changes. ==="
        )
        return 2
    if exit_code is None:
        exit_code = 1
        if child.signalstatus is not None:
            print(
                f"\n=== Exited by signal: {child.signalstatus}, "
                f"sent {drive_result.sent_count} responses, "
                f"stop={drive_result.stop_reason} ==="
            )
        else:
            print(
                f"\n=== Exited with unknown status, "
                f"sent {drive_result.sent_count} responses, "
                f"stop={drive_result.stop_reason} ==="
            )
    else:
        print(
            f"\n=== Exited: {exit_code}, sent {drive_result.sent_count} responses, "
            f"stop={drive_result.stop_reason} ==="
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
