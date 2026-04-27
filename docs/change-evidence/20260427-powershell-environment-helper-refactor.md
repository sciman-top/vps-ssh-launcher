# 2026-04-27 PowerShell Environment Helper Refactor Evidence

## Scope

- Rule IDs: R1, R2, R3, R6, R8, E4
- Risk level: low
- Current landing: PowerShell launcher and maintenance helper scripts in `D:\CODE\vps-ssh-launcher`
- Target landing: keep existing CLI parameters and SSH behavior while removing duplicated Windows environment and Python resolution logic
- Real SSH triggered: no
- Sensitive data recorded: no

## Issue Found

- `connect.ps1`, `scripts/run_gates.ps1`, `scripts/google_ipv4_routing.ps1`, and `scripts/vasma_kernel_update_cron.ps1` each carried separate copies of Windows process environment recovery and project Python resolution. This made future host hardening fixes easy to apply to one entrypoint but miss another.

## Changes

- Added `scripts/lib/project_environment.ps1` with shared:
  - `Initialize-WindowsProcessEnvironment`
  - `Resolve-ProjectPython`
- Updated the four PowerShell entrypoints to dot-source the shared helper.
- Preserved existing behavior differences:
  - user-facing launcher/helper scripts still allow `py -3` fallback;
  - `run_gates.ps1` keeps isolated-Python semantics for environment-wide gates and does not add `py -3` fallback.
- Extended `test_scripts.py` to parse scripts recursively and verify entrypoints reuse the shared helper instead of reintroducing inline copies.
- Ran `ruff format` on `test_scripts.py` and the already-modified `.governed-ai/verify-powershell-policy.py` because the gate required formatting.

## Verification

Targeted checks:

```powershell
python -m pytest -q test_scripts.py
python -m ruff check test_scripts.py
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -SkipDependencyAudit
```

Results:

- `test_scripts.py`: `14 passed, 15 subtests passed`
- Ruff: passed
- `run_gates.ps1 -SkipDependencyAudit`: passed, including build/test/contract/pip-check/bandit/vulture/ruff/mypy/pyright

Full gate:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Results:

- `pytest`: `59 passed, 1 skipped, 15 subtests passed`
- `unittest`: `Ran 60 tests`, `OK (skipped=1)`
- `pip check`: `No broken requirements found.`
- `pip-audit`: `No known vulnerabilities found`
- `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, `pyright`: passed

Invalid interpreter fail-fast probes:

```powershell
$env:VPS_SSH_LAUNCHER_PYTHON = Join-Path (Get-Location) 'missing-python.exe'
pwsh -NoProfile -ExecutionPolicy Bypass -File .\connect.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\google_ipv4_routing.ps1 -Config .\target.example.json -Profile example
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Config .\target.example.json -Profile example -Kernel xray
```

Results:

- `connect.ps1`: `exit=1`, message contains `VPS_SSH_LAUNCHER_PYTHON is set but the file does not exist`
- `google_ipv4_routing.ps1`: `exit=1`, same shared-helper message
- `vasma_kernel_update_cron.ps1`: `exit=1`, same shared-helper message

## N/A

- Real SSH integration: `gate_na`
- reason: this is a local launcher/script refactor. It does not change SSH transport, profile schema, auth resolution, remote command text, or remote host state.
- alternative_verification: recursive PowerShell parse tests, shared-helper tests, full local gate, and invalid-interpreter probes.
- evidence_link: this file.
- expires_at: next change to SSH transport, profile schema, auth resolution, or remote maintenance apply behavior.

## Rollback

- Code rollback: revert `scripts/lib/project_environment.ps1`, the four PowerShell entrypoints, `test_scripts.py`, and this evidence file with git.
- Runtime rollback: no `target.json`, SSH profile, remote host configuration, password, key, or production service was modified.
