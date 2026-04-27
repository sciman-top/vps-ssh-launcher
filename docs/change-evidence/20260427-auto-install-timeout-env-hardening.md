# 2026-04-27 Auto Install Timeout Env Hardening Evidence

## Scope

- Rule IDs: R1, R2, R3, R6, R8, E4
- Risk level: low
- Current landing: `D:\CODE\vps-ssh-launcher`
- Target landing: keep existing launcher, SSH, `target.json`, and installer behavior while improving configuration error handling in `auto_install.py`
- Real SSH triggered: no
- Sensitive data recorded: no

## Baseline

Commands:

```powershell
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
python -m unittest -q
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Results:

- `compileall`: passed
- `pytest`: `56 passed, 1 skipped, 8 subtests passed`
- `unittest`: `Ran 57 tests`, `OK (skipped=1)`
- Full gate: passed, including `pip check`, `pip-audit`, `bandit`, `vulture`, `ruff`, `mypy`, and `pyright`

## Issue Found

- `auto_install.py` parsed `VPS_AUTO_INSTALL_EXPECT_TIMEOUT` at module import time. A malformed environment value such as `not-an-int` could crash import before the CLI reached its normal safety checks and user-facing error path.

## Changes

- Replaced import-time integer parsing with `_resolve_expect_timeout()` inside `main()`.
- Kept CLI `--expect-timeout` precedence over the environment variable.
- Reused the resolved timeout for both prompt matching and EOF wait.
- Added a regression test for malformed `VPS_AUTO_INSTALL_EXPECT_TIMEOUT`.

## Verification

Targeted verification:

```powershell
python -m pytest -q test_auto_install.py test_ssh_tool.py
python -m ruff check auto_install.py test_auto_install.py
python -m mypy auto_install.py test_auto_install.py
```

Results:

- Targeted pytest: `45 passed`
- Ruff: passed
- Mypy: passed

Full verification after the change:

```powershell
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
python -m unittest -q
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Results:

- `compileall`: passed
- `pytest`: `57 passed, 1 skipped, 8 subtests passed`
- `unittest`: `Ran 58 tests`, `OK (skipped=1)`
- Full gate: passed, including `pip check`, `pip-audit`, `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, and `pyright`

## N/A

- Real SSH integration: `gate_na`
- reason: this change affects only local `auto_install.py` CLI/env parsing and does not alter SSH transport, profile selection, or remote command execution.
- alternative_verification: unit tests plus full local gate.
- evidence_link: this file.
- expires_at: next change to SSH connection, `run_on_all`, profile schema, or remote command execution behavior.

## Rollback

- Code rollback: revert `auto_install.py`, `test_auto_install.py`, and this evidence file with git.
- Runtime rollback: no `target.json`, SSH profile, remote host configuration, password, key, or production service was modified.
