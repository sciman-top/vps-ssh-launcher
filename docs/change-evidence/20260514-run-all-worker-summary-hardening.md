# 2026-05-14 run-all worker summary hardening

- rule_id: R2/R6/R8
- risk_level: low
- scope: `ssh_tool.py`, `test_ssh_tool.py`
- change: keep `run --all` result aggregation alive when a worker raises an unexpected internal exception; keep status lines readable when remote output has no trailing newline.
- real_ssh_triggered: no
- rollback: revert this git change, or restore `ssh_tool.py` and `test_ssh_tool.py` from the previous commit.

## Commands

- `python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m pytest -q`
- `python -m unittest -q`
- `python -m ruff check ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m ruff format --check ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m mypy ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m pyright ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `python -m bandit -q -r ssh_tool.py auto_install.py`
- `python -m vulture ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py --min-confidence 80`
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- `git diff --check`

## Key Output

- `pytest`: `73 passed, 1 skipped, 15 subtests passed`
- `unittest`: `Ran 74 tests ... OK (skipped=1)`
- `contract:powershell-policy`: `status=pass`, `violation_count=0`
- `pip check`: `No broken requirements found.`
- `pip_audit`: `No known vulnerabilities found`
- `ruff`: `All checks passed!`
- `ruff format`: `6 files already formatted`
- `mypy`: `Success: no issues found in 6 source files`
- `pyright`: `0 errors, 0 warnings, 0 informations`

## Notes

- `git status` still warns about an unrelated untracked path under `port\`; this change did not create or modify that path.
