#!/usr/bin/env python3
"""Automate vasma installation."""

from __future__ import annotations

import os
import sys
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

INSTALL_SCRIPT = Path("/etc/v2ray-agent/install.sh")
INSTALL_DOMAIN = os.environ.get("VPS_DOMAIN", "fq.sciman.top")
SPAWN_TIMEOUT = 1800
EXPECT_TIMEOUT = int(os.environ.get("VPS_AUTO_INSTALL_EXPECT_TIMEOUT", "45"))
MAX_RESPONSES = 30
GENERIC_SELECT_PROMPT = r"请选择:"
EXECUTE_ENV = "VPS_AUTO_INSTALL_EXECUTE"


def _generic_select_response(count: int) -> str:
    if count == 1:
        return "2"  # Main menu: custom install
    if count == 2:
        return "1"  # Core: Xray
    return ""


@dataclass(frozen=True)
class PromptDriveResult:
    sent_count: int
    stop_reason: str


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
        default=EXPECT_TIMEOUT,
        help="Seconds to wait for a known prompt before aborting.",
    )
    return parser.parse_args(argv)


def _prompt_plan(install_domain: str) -> tuple[list[str], list[str]]:
    specific_prompts = _specific_prompts(install_domain)
    patterns = list(specific_prompts.keys()) + [GENERIC_SELECT_PROMPT]
    responses = list(specific_prompts.values())
    return patterns, responses


def _drive_prompts(
    child: Any,
    pexpect: Any,
    *,
    install_domain: str = INSTALL_DOMAIN,
    expect_timeout: int = EXPECT_TIMEOUT,
    max_responses: int = MAX_RESPONSES,
) -> PromptDriveResult:
    patterns, responses = _prompt_plan(install_domain)
    generic_select_count = 0
    sent_count = 0

    while sent_count < max_responses:
        try:
            i = child.expect(
                cast(Any, patterns + [pexpect.EOF, pexpect.TIMEOUT]),
                timeout=expect_timeout,
            )
        except Exception as exc:
            print(f"\n=== Error: {exc} ===")
            return PromptDriveResult(sent_count=sent_count, stop_reason="error")

        if i < len(patterns) - 1:
            child.sendline(responses[i])
            sent_count += 1
            print(f"\n>>> [#{sent_count}] Specific #{i}, Sent: '{responses[i]}' <<<\n")
            continue

        if i == len(patterns) - 1:
            generic_select_count += 1
            resp = _generic_select_response(generic_select_count)
            child.sendline(resp)
            sent_count += 1
            print(
                f"\n>>> [#{sent_count}] 请选择 #{generic_select_count}, Sent: '{resp}' <<<\n"
            )
            continue

        if i == len(patterns):
            print(f"\n=== EOF after {sent_count} responses ===")
            return PromptDriveResult(sent_count=sent_count, stop_reason="eof")

        print(f"\n=== Timeout after {sent_count} responses ===")
        return PromptDriveResult(sent_count=sent_count, stop_reason="timeout")

    print(f"\n=== Reached max responses ({max_responses}) ===")
    return PromptDriveResult(sent_count=sent_count, stop_reason="max_responses")


def _wait_for_child_exit(child: Any, pexpect: Any) -> None:
    if child.isalive():
        try:
            child.expect(pexpect.EOF, timeout=EXPECT_TIMEOUT)
        except pexpect.TIMEOUT:
            pass


def _terminate_child(child: Any) -> None:
    if not child.isalive():
        return
    try:
        child.close(force=True)
    except TypeError:
        child.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.execute and os.environ.get(EXECUTE_ENV) != "1":
        print(
            "ERROR: auto_install.py drives /etc/v2ray-agent/install.sh and can "
            "rewrite live VPS proxy config. Re-run with --execute only after a "
            "fresh backup and a plan to restore xray/nginx/subscription state.",
            file=sys.stderr,
        )
        return 2

    if args.expect_timeout <= 0 or args.expect_timeout >= 60:
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
    child.logfile = sys.stdout
    drive_result = _drive_prompts(
        child,
        pexpect,
        install_domain=INSTALL_DOMAIN,
        expect_timeout=args.expect_timeout,
        max_responses=MAX_RESPONSES,
    )
    if drive_result.stop_reason not in {"eof"}:
        _terminate_child(child)
    else:
        _wait_for_child_exit(child, pexpect)

    child.close()
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
