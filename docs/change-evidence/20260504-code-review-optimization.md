# 2026-05-04 code review optimization evidence

## Scope

- Rule IDs: R1, R2, R6, R8, E4, E5, E6
- Risk level: medium-low
- Current landing point: `D:\CODE\vps-ssh-launcher`
- Target home: launcher robustness and maintainability hardening
- Real SSH triggered: no

## Changes

- Refactored `ssh_tool.py` `run_on_all` internals from positional tuples to structured result/context dataclasses.
- Split connection setup, config profile selection, channel draining, batch result printing, and CLI action handling into focused helpers.
- Hardened `scripts/run_gates.ps1 -RunIntegration` so it resolves the effective integration config before applying the non-interactive profile guard.
- Aligned `test_integration_real_ssh.py` with the launcher default config resolution path.
- Simplified `auto_install.py` prompt-driving constants and child EOF wait handling without changing execution guard behavior.
- Documented the default real-integration config resolution in `README.md`.

## Verification

- Baseline before edits:
  - `python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py`
  - `python -m pytest -q` -> `69 passed, 1 skipped, 15 subtests passed`
  - `python -m unittest -q` -> `70 tests OK, skipped=1`
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1` -> pass
- Targeted after edits:
  - `python -m pytest -q test_ssh_tool.py -k "connect_client or run_on_all or main"` -> `19 passed, 25 deselected`
  - `python -m pytest -q test_auto_install.py test_integration_real_ssh.py test_scripts.py` -> `28 passed, 1 skipped, 15 subtests passed`
  - `python -m ruff check ssh_tool.py auto_install.py test_integration_real_ssh.py test_scripts.py` -> pass
  - `python -m mypy ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py` -> pass
  - `python -m pyright --level warning ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py` -> pass
  - `python -m ruff check --select B,SIM,PERF,RET,PIE,C4,UP,PLR ssh_tool.py auto_install.py` -> pass
  - `python -m ruff check --select C90 --config lint.mccabe.max-complexity=10 ssh_tool.py auto_install.py` -> pass
  - `git diff --check` -> pass
- Interim full-gate failure and fix:
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1` initially failed at `hotspot:bandit` due to two newly introduced `assert` runtime guards.
  - Replaced those guards with explicit runtime branches, then reran targeted `python -m bandit -q -r ssh_tool.py auto_install.py` -> pass.
- Final hard-gate closeout after the assert fix:
  - `python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py` -> pass
  - `python -m pytest -q` -> `72 passed, 1 skipped, 15 subtests passed`
  - `python -m unittest -q` -> `73 tests OK, skipped=1`
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1` -> pass, including `pip check`, `pip-audit`, `bandit`, `vulture`, `ruff`, `mypy`, and `pyright`

## Rollback

- Use Git to revert the touched files from this slice:
  - `README.md`
  - `auto_install.py`
  - `scripts/run_gates.ps1`
  - `ssh_tool.py`
  - `test_integration_real_ssh.py`
  - `test_scripts.py`
  - `docs/change-evidence/20260504-code-review-optimization.md`
- Existing pre-task governance/rule-file modifications were preserved and not reverted.
