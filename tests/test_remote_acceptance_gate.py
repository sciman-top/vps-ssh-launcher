"""The acceptance gates must not be assert-defined.

`cpa-acceptance.py` prints ACCEPTANCE_RESULT=PASS only after its scenario
asserts, and `scripts/vps_maintenance.ps1` treats that marker as the verdict.
Under `python3 -O` / PYTHONOPTIMIZE every assert disappears, so the old code
printed PASS without running a scenario. Both gates now refuse explicitly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, *flags: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import runpy,sys; sys.argv=['x']; "
        f"runpy.run_path(r'{REPO_ROOT / 'scripts' / 'remote' / script}', "
        "run_name='__main__')"
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, *flags, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )


def test_the_acceptance_gate_refuses_under_optimised_python() -> None:
    completed = _run("cpa-acceptance.py", "-O")
    assert completed.returncode != 0
    assert "ACCEPTANCE_RESULT=PASS" not in completed.stdout
    assert "asserts_disabled" in completed.stdout


def test_the_gate_never_prints_pass_outside_its_fixture() -> None:
    # Without the fixture namespace the run must fail before any PASS marker;
    # this environment has no /proc/self/ns/net at all.
    completed = _run("cpa-acceptance.py")
    assert completed.returncode != 0
    assert "ACCEPTANCE_RESULT=PASS" not in completed.stdout
