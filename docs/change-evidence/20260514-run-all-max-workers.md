# 2026-05-14 run-all max workers

- rule_id: R2/R6/R8/E4
- risk_level: low
- scope: `ssh_tool.py`, `connect.ps1`, `README.md`, `test_ssh_tool.py`, `test_scripts.py`
- change: add a bounded `--max-workers` / `-MaxWorkers` control for `run --all` so large profile sets can avoid excessive simultaneous SSH fanout; preserve the previous default cap of 32.
- real_ssh_triggered: no
- rollback: revert this git change, or restore the touched files from the previous commit.

## Commands

- `python -m pytest -q`
- `python -m ruff check ssh_tool.py test_ssh_tool.py test_scripts.py`
- `python -m ruff format --check ssh_tool.py test_ssh_tool.py test_scripts.py`
- `python -m mypy ssh_tool.py test_ssh_tool.py test_scripts.py`
- `python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
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
- `compileall`: exit code `0`
- `git diff --check`: exit code `0`; Git also reported the existing line-ending warning for `connect.ps1`.
