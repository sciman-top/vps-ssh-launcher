# 2026-06-24 Robustness Refactor

- Rule IDs: `R1`, `R2`, `R6`, `R8`, `E5`, `E6`
- Risk level: medium
- Triggered real SSH: no

## Scope

- Repackaged Python implementation into `vps_ssh_launcher/cli.py` while keeping
  `ssh_tool.py` as a compatibility alias.
- Added explicit `--command-hard-timeout` / `-CommandHardTimeout`.
- Removed legacy cwd fallback for relative `profiles[].key`.
- Added bounded `run_all` output truncation markers.
- Restricted dependency bootstrap in `connect.ps1` to isolated Python by
  default; explicit override is now `-AllowGlobalBootstrap`.
- Switched `auto_install.py` to redacted transcript capture instead of raw
  stdout passthrough.
- Moved shared PowerShell launcher/config invocation into
  `scripts/lib/project_environment.ps1`.

## Commands

- `python -m venv .venv`
- `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`
- `.\.venv\Scripts\python.exe -m pytest -q test_ssh_tool.py`
- `.\.venv\Scripts\python.exe -m pytest -q test_scripts.py`
- `.\.venv\Scripts\python.exe -m pytest -q test_auto_install.py`
- `.\.venv\Scripts\python.exe -m compileall -q ssh_tool.py vps_ssh_launcher auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
- `.\.venv\Scripts\python.exe -m pytest -q`
- `.\.venv\Scripts\python.exe -m unittest -q`
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`

## Verification Status

- Targeted Python tests: pass
- Targeted PowerShell/script tests: pass
- Targeted auto-install tests: pass
- Full repository gates: pass (`93 passed, 1 skipped, 22 subtests passed`; `mypy`/`pyright`/`ruff`/`bandit`/`vulture`/`pip-audit`/`pip check` all green)

## Breaking Changes

- Relative `profiles[].key` no longer falls back to the current working
  directory. It now resolves only relative to the config file directory.
- `connect.ps1` no longer installs dependencies into PATH/global Python unless
  `-AllowGlobalBootstrap` is passed explicitly.

## Rollback

- Revert this branch to the pre-refactor commit before `20260624-robustness-refactor`.
- If launcher behavior regression is found, restore the prior single-file
  `ssh_tool.py` implementation and previous `connect.ps1` dependency bootstrap.
