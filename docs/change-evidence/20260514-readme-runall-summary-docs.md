# 2026-05-14 README run-all summary docs

- rule_id: R2/R6/R8
- risk_level: low
- scope: `README.md`
- change: document `-RunAll` summary output, failure categories, exit-code aggregation, and the local redaction boundary for `auto_install.py` prompt responses.
- real_ssh_triggered: no
- rollback: revert this git change, or restore `README.md` from the previous commit.

## Commands

- `git diff --check`
- `python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m pytest -q`
- `python -m unittest -q`
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`

## Key Output

- `git diff --check`: exit code `0`
- `compileall`: exit code `0`
- `pytest`: `81 passed, 1 skipped, 20 subtests passed`
- `unittest`: `Ran 82 tests ... OK (skipped=1)`
- `run_gates.ps1`: `contract:powershell-policy` status `pass`, `pip check` no broken requirements, `pip_audit` no known vulnerabilities, `ruff` all checks passed, `mypy` success, `pyright` 0 errors.
