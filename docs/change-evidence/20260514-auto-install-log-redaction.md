# 2026-05-14 auto install log redaction

- rule_id: R2/R6/R8
- risk_level: low
- scope: `auto_install.py`, `test_auto_install.py`
- change: redact automated prompt responses in local logs and only mirror installer output through `pexpect.logfile_read`, reducing the chance of future prompt values appearing in evidence or terminals.
- real_ssh_triggered: no
- rollback: revert this git change, or restore `auto_install.py` and `test_auto_install.py` from the previous commit.

## Commands

- `python -m pytest -q test_auto_install.py test_ssh_tool.py test_scripts.py`
- `python -m ruff format --check auto_install.py test_auto_install.py test_ssh_tool.py`
- `python -m ruff check auto_install.py test_auto_install.py test_ssh_tool.py`
- `python -m mypy auto_install.py test_auto_install.py`
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- `git diff --check`

## Key Output

- `pytest`: `81 passed, 1 skipped, 20 subtests passed`
- `unittest`: `Ran 82 tests ... OK (skipped=1)`
- `contract:powershell-policy`: `status=pass`, `violation_count=0`
- `pip check`: `No broken requirements found.`
- `pip_audit`: `No known vulnerabilities found`
- `ruff format`: `6 files already formatted`
- `ruff check`: `All checks passed!`
- `mypy`: `Success: no issues found in 6 source files`
- `pyright`: `0 errors, 0 warnings, 0 informations`
- `git diff --check`: exit code `0`; Git also reported the existing line-ending warning for `connect.ps1`.
