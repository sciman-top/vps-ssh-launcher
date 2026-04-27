# 2026-04-27 Code Review Optimization Evidence

## Scope

- Rule IDs: R1, R2, R3, R6, R8, E4, E5
- Risk level: medium
- Current landing: local launcher/config/test/gate code in `D:\CODE\vps-ssh-launcher`
- Target landing: preserve CLI, `target.json`, SSH behavior, and user-facing wrappers while improving correctness and gate reliability
- Real SSH triggered: yes, read-only integration round trips on `bwg` and `zz`
- Sensitive data recorded: no
- Pre-existing unrelated local changes left untouched: `.governed-ai/repo-profile.json`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`

## Baseline

Command:

```powershell
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
python -m unittest -q
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Result:

- `compileall`: passed
- `pytest`: `46 passed, 1 skipped, 3 subtests passed`
- `unittest`: passed
- Full gate: failed at `lint:format`
- Failure: `.governed-ai\verify-powershell-policy.py` needed `ruff format`
- Follow-up type/static checks after the baseline stop: `mypy`, `pyright`, `vulture`, and `ruff check` passed

## Issues Found

1. `-AllowAgent` was treated as an auth override during validation, but `apply_config` and `RunAll` still read profile `password_env` / `password` / `key` afterward. This could fail even when the operator explicitly selected SSH Agent authentication.
2. Paramiko can report `-1` when a remote command does not provide an exit status. `RunAll` could print a failed profile but still return process exit `0` because `max(0, -1) == 0`.
3. Multi-profile config without `default` entered an implicit prompt even in non-interactive contexts.
4. `VPS_SSH_LAUNCHER_PYTHON` pointing to a missing interpreter was silently ignored by PowerShell entrypoints, allowing accidental fallback to a different Python.
5. Full gate was blocked by tracked governance helper formatting drift.

## Changes

- Centralized Python auth resolution in `ssh_tool.py` so CLI auth choices consistently override profile defaults.
- Added invalid remote exit status handling before returning from `exec_remote`.
- Added non-interactive profile-selection failure with a clear `--profile` / `default` remediation.
- Added fail-fast checks for invalid `VPS_SSH_LAUNCHER_PYTHON` in `connect.ps1`, `scripts/run_gates.ps1`, and `scripts/google_ipv4_routing.ps1`.
- Fixed non-Windows fallback config path construction in `connect.ps1`.
- Formatted `.governed-ai/verify-powershell-policy.py`.
- Added regression tests for agent auth override, invalid remote exit status, non-interactive profile selection, and PowerShell invalid-Python diagnostics.
- Documented `-AllowAgent` / `-Key` runtime auth precedence in `README.md`.

## Verification

Targeted tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_ssh_tool.py -k "noninteractive or invalid_exit_status or allow_agent or cli_key or run_on_all_uses_cli_key"
.\.venv\Scripts\python.exe -m pytest -q test_scripts.py
```

Results:

- `test_ssh_tool.py` targeted set: `6 passed, 31 deselected`
- `test_scripts.py`: `10 passed, 6 subtests passed`

Full gate:

```powershell
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
python -m unittest -q
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Results:

- `pytest`: `51 passed, 1 skipped, 6 subtests passed`
- `unittest`: `52 tests`, `OK (skipped=1)`
- `pip check`: `No broken requirements found.`
- `pip-audit`: `No known vulnerabilities found`
- `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, `pyright`: passed
- Total command chain time: `34.08s`

Real SSH read-only integration:

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile bwg -IntegrationCommand 'printf vps-ssh-launcher-integration' -IntegrationExpected 'vps-ssh-launcher-integration'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile zz -IntegrationCommand 'printf vps-ssh-launcher-integration' -IntegrationExpected 'vps-ssh-launcher-integration' -SkipDependencyAudit
```

Results:

- `bwg`: `52 passed, 6 subtests passed`; full gate including dependency audit passed; `EXIT_CODE=0`; `38.84s`.
- `zz`: first attempt reached `52 passed, 6 subtests passed` but stopped at `lint:format` because `.governed-ai\verify-powershell-policy.py` had not been durably formatted.
- `.governed-ai\verify-powershell-policy.py` was reformatted with `ruff format`.
- `zz` rerun: `52 passed, 6 subtests passed`; dependency audit skipped because the same dependency tree had already passed on `bwg`; remaining gates passed; `EXIT_CODE=0`; `12.44s`.
- Remote command was limited to `printf vps-ssh-launcher-integration`; no remote host configuration was changed.

Additional verification:

```powershell
python .governed-ai\verify-powershell-policy.py
git diff --check
```

Results:

- PowerShell policy verifier: `status=pass`, `violation_count=0`
- `git diff --check`: passed; line-ending warnings only

Invalid interpreter fail-fast probe:

```powershell
$env:VPS_SSH_LAUNCHER_PYTHON = Join-Path (Get-Location) 'missing-python.exe'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Result:

- Expected failure observed with message containing `VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist`

Startup smoke:

```powershell
Measure-Command { .\.venv\Scripts\python.exe .\ssh_tool.py --version > $null }
Measure-Command { .\.venv\Scripts\python.exe .\ssh_tool.py --help > $null }
```

Results:

- `ssh_tool.py --version`: about `140.0ms`
- `ssh_tool.py --help`: about `138.3ms`

## N/A

- Dedicated benchmark: `gate_na`
- reason: this repo does not currently contain a benchmark script or benchmark framework.
- alternative_verification: full gate timing plus `--version` / `--help` startup smoke.
- evidence_link: this file.
- expires_at: next change that adds performance-sensitive startup, config parsing, or SSH execution behavior.

## Rollback

- Code rollback: use git to revert this change set for `ssh_tool.py`, tests, PowerShell scripts, README, and this evidence file.
- Runtime rollback: no `target.json`, SSH profile, remote host configuration, password, key, or production service was modified.
- If auth override behavior needs to be temporarily reverted, restore the previous per-call profile auth resolution in `apply_config` and `run_on_all`, then rerun the full gate.
