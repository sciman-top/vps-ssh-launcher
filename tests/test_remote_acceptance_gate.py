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
from typing import Any
from pathlib import Path

import pytest


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


NL = chr(10)


def _rehearsal_module() -> dict[str, Any]:
    """Load only the updater-rehearsal helper, without running main()."""

    import runpy

    return runpy.run_path(str(REPO_ROOT / "scripts" / "remote" / "cpa-acceptance.py"))


def test_update_rehearsal_runs_when_not_deferred(tmp_path: Path) -> None:
    """The default path still runs the real rehearsal."""

    module = _rehearsal_module()
    stub = "def run_cases():" + NL + "    print('REHEARSAL_RAN')" + NL
    (tmp_path / "cpa-update-acceptance.py").write_text(stub, encoding="utf-8")
    previous = os.environ.pop("CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL", None)
    try:
        module["run_update_rehearsal"](tmp_path)
    finally:
        if previous is not None:
            os.environ["CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL"] = previous


def test_deferral_requires_an_explicit_fork_image(tmp_path: Path) -> None:
    """Deferring is opt-in and refuses to hide a runnable check."""

    module = _rehearsal_module()
    previous = os.environ.get("CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL")
    os.environ["CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL"] = "1"
    try:
        # No compose in the fixture root: there is no evidence to defer on.
        with pytest.raises(SystemExit):
            module["run_update_rehearsal"](tmp_path)
        # An official image is exactly the case the rehearsal exists for.
        (tmp_path / "compose.yml").write_text(
            "services:"
            + NL
            + "  cli-proxy-api:"
            + NL
            + "    image: eceasy/cli-proxy-api:v8.0.75"
            + NL,
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            module["run_update_rehearsal"](tmp_path)
        # A derived fork image legitimately cannot rehearse the updater.
        (tmp_path / "compose.yml").write_text(
            "services:"
            + NL
            + "  cli-proxy-api:"
            + NL
            + "    image: cli-proxy-api-fork:v8.0.75-gemini-raw"
            + NL,
            encoding="utf-8",
        )
        module["run_update_rehearsal"](tmp_path)
    finally:
        if previous is None:
            os.environ.pop("CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL", None)
        else:
            os.environ["CPA_ACCEPTANCE_DEFER_UPDATE_REHEARSAL"] = previous


def test_the_gate_never_prints_pass_outside_its_fixture() -> None:
    # Without the fixture namespace the run must fail before any PASS marker;
    # this environment has no /proc/self/ns/net at all.
    completed = _run("cpa-acceptance.py")
    assert completed.returncode != 0
    assert "ACCEPTANCE_RESULT=PASS" not in completed.stdout


def test_acceptance_fixture_catalog_stays_aligned_with_health_contract() -> None:
    """cpa-acceptance.py's synthetic fixture must satisfy cpa-health's model contract."""
    import ast

    acceptance_path = REPO_ROOT / "scripts" / "remote" / "cpa-acceptance.py"
    module = _rehearsal_module()
    module["assert_catalog_contract"].__globals__["ROOT"] = (
        REPO_ROOT / "scripts" / "remote"
    )
    module["assert_catalog_contract"]()

    tree = ast.parse(acceptance_path.read_text(encoding="utf-8"))
    fixture_config = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "config"
                    and isinstance(node.value, ast.Dict)
                    and any(
                        isinstance(k, ast.Constant)
                        and k.value == "openai-compatibility"
                        for k in node.value.keys
                    )
                ):
                    # Replace str(ROOT / "auth") with a literal string for literal_eval
                    source_segment = ast.get_source_segment(
                        acceptance_path.read_text(encoding="utf-8"), node.value
                    )
                    assert source_segment is not None
                    fixture_config = ast.literal_eval(
                        source_segment.replace(
                            'str(ROOT / "auth")', '"/opt/cliproxyapi/auth"'
                        )
                    )
                    break
    assert isinstance(fixture_config, dict)
    module["assert_fixture_model_coverage"](fixture_config)
