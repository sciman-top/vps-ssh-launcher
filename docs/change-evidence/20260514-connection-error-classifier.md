# 2026-05-14 connection error classifier

- rule_id: R2/R6/R8
- risk_level: low
- scope: `ssh_tool.py`, `test_ssh_tool.py`
- change: share connection error classification across single-target and `run --all` paths to reduce duplicate branching and prevent future category/exit-code drift.
- real_ssh_triggered: no
- rollback: revert this git change, or restore `ssh_tool.py` and `test_ssh_tool.py` from the previous commit.

## Commands

- `python -m pytest -q`
- `python -m ruff format --check ssh_tool.py test_ssh_tool.py`
- `python -m ruff check ssh_tool.py test_ssh_tool.py`
- `python -m mypy ssh_tool.py test_ssh_tool.py`
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- `git diff --check`

## Key Output

- `pytest`: `81 passed, 1 skipped, 20 subtests passed`
- `unittest`: `Ran 82 tests ... OK (skipped=1)`
- `contract:powershell-policy`: `status=pass`, `violation_count=0`
- `pip check`: `No broken requirements found.`
- `pip_audit`: `No known vulnerabilities found`
- `ruff check`: `All checks passed!`
- `ruff format`: `6 files already formatted`
- `mypy`: `Success: no issues found in 6 source files`
- `pyright`: `0 errors, 0 warnings, 0 informations`
- `git diff --check`: exit code `0`; Git also reported the existing line-ending warning for `connect.ps1`.
