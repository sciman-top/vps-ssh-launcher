#!/usr/bin/env python3
"""Automate vasma installation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

INSTALL_SCRIPT = Path("/etc/v2ray-agent/install.sh")
INSTALL_DOMAIN = os.environ.get("VPS_DOMAIN", "fq.sciman.top")
SPAWN_TIMEOUT = 1800
EXPECT_TIMEOUT = 600
MAX_RESPONSES = 30
GENERIC_SELECT_PROMPT = r"请选择:"


def _generic_select_response(count: int) -> str:
    if count == 1:
        return "2"  # Main menu: custom install
    if count == 2:
        return "1"  # Core: Xray
    return ""


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


def main() -> int:
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

    generic_select_count = 0

    # Specific patterns (checked first)
    specific_prompts = {
        r"请选择\[多选\]": "7,12",
        r"Reality目标域名": "n",
        r"请输入目标域名": "",
        r"DNS API": "n",
        r"请选择.*使用默认": "",
        r"域名:": INSTALL_DOMAIN,
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

    # Specific patterns first, then the generic menu prompt, then EOF/TIMEOUT.
    patterns = list(specific_prompts.keys()) + [GENERIC_SELECT_PROMPT]
    responses = list(specific_prompts.values())

    sent_count = 0

    while sent_count < MAX_RESPONSES:
        try:
            i = child.expect(
                cast(Any, patterns + [pexpect.EOF, pexpect.TIMEOUT]),
                timeout=EXPECT_TIMEOUT,
            )
            if i < len(patterns) - 1:
                child.sendline(responses[i])
                sent_count += 1
                print(
                    f"\n>>> [#{sent_count}] Specific #{i}, Sent: '{responses[i]}' <<<\n"
                )
            elif i == len(patterns) - 1:
                generic_select_count += 1
                resp = _generic_select_response(generic_select_count)
                child.sendline(resp)
                sent_count += 1
                print(
                    f"\n>>> [#{sent_count}] 请选择 #{generic_select_count}, Sent: '{resp}' <<<\n"
                )
            elif i == len(patterns):
                print(f"\n=== EOF after {sent_count} responses ===")
                break
            else:
                print(f"\n=== Timeout after {sent_count} responses ===")
                break
        except Exception as e:
            print(f"\n=== Error: {e} ===")
            break

    if child.isalive():
        try:
            child.expect(pexpect.EOF, timeout=EXPECT_TIMEOUT)
        except pexpect.TIMEOUT:
            pass

    child.close()
    exit_code = cast(int | None, child.exitstatus)
    if exit_code is None:
        exit_code = 1
        if child.signalstatus is not None:
            print(
                f"\n=== Exited by signal: {child.signalstatus}, sent {sent_count} responses ==="
            )
        else:
            print(f"\n=== Exited with unknown status, sent {sent_count} responses ===")
    else:
        print(f"\n=== Exited: {exit_code}, sent {sent_count} responses ===")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
