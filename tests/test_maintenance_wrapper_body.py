"""The deployed monthly-maintenance wrapper must be self-contained.

The wrapper body is a here-doc that is written to /usr/local/sbin and then run by
cron, far away from this repository. A function defined only in the OUTER
PowerShell here-string therefore still passes bash -n and every static grep, and
fails only on the host at 14:00 on the first of the month -- which is exactly
what happened to prune_verified_orphan_containers().
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from script_validation_support import ScriptValidationMixin

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "system_maintenance_cron.ps1"


def _unescape_powershell(line: str) -> str:
    """Undo PowerShell here-string escaping exactly as the host will see it."""
    out: list[str] = []
    index = 0
    while index < len(line):
        if line[index] == "`":
            index += 1
            if index < len(line):
                out.append(line[index])
            index += 1
            continue
        out.append(line[index])
        index += 1
    return "".join(out)


def _wrapper_body() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if "cat >" in line and "maintenance_script" in line and "<<'EOF'" in line
    )
    end = next(
        index for index in range(start + 1, len(lines)) if lines[index].strip() == "EOF"
    )
    return "\n".join(_unescape_powershell(line) for line in lines[start + 1 : end])


def _defined_names(text: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", text, re.MULTILINE))


def test_the_deployed_wrapper_defines_every_repository_function_it_calls() -> None:
    body = _wrapper_body()
    inside = _defined_names(body)
    outside = _defined_names(SCRIPT.read_text(encoding="utf-8"))
    called_from_outside = sorted(
        name
        for name in outside - inside
        if re.search(r"(^|\s)" + re.escape(name) + r"(\s|$)", body, re.MULTILINE)
    )
    assert called_from_outside == [], (
        "the wrapper body calls functions that are defined only outside the "
        f"here-doc: {called_from_outside}"
    )
    assert "prune_verified_orphan_containers" in inside


def test_the_deployed_wrapper_defines_each_function_before_calling_it() -> None:
    # The wrapper runs under 'set -Eeuo pipefail', so calling a function that
    # is defined further down is "command not found" (exit 127) -- not a
    # forward reference bash quietly resolves. A definition placed after the
    # call therefore fails on the host at 14:00 on the first of the month
    # while bash -n, which only parses, stays green.
    body = _wrapper_body()
    lines = body.splitlines()
    definition_line = {
        match.group(1): index
        for index, line in enumerate(lines)
        if (match := re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", line))
    }
    assert definition_line, "the wrapper body defines no functions at all"
    offenders = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        for match in re.finditer(r"(?:^|[\s(])([a-z_][a-z0-9_]*)(?=[\s;)]|$)", line):
            name = match.group(1)
            start = definition_line.get(name)
            if start is not None and start > index:
                offenders.append(f"{name} (line {index + 1} -> defined {start + 1})")
    assert offenders == [], (
        "the wrapper calls functions before defining them; bash answers with "
        f"'command not found': {offenders}"
    )


def test_the_deployed_wrapper_is_syntactically_valid_bash(tmp_path: Path) -> None:
    bash = ScriptValidationMixin._resolve_bash()
    if bash is None:
        pytest.skip("bash is not available")
    script = tmp_path / "wrapper.sh"
    script.write_bytes(_wrapper_body().encode("utf-8"))
    completed = subprocess.run(
        ScriptValidationMixin._bash_command(
            bash, "-n", ScriptValidationMixin._bash_path(bash, script)
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
