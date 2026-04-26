# 2026-04-26 Code Review Optimization Evidence

## Scope

- Rule IDs: R1/R2/R6/R8, E4/E5
- Risk level: low
- Current landing: `D:\CODE\vps-ssh-launcher`
- Target landing: preserve launcher/config/SSH behavior while improving gate reliability and the Google IPv4 routing helper safety.
- Real SSH triggered: yes, follow-up verification used `bwg` and `zz` profiles with read-only commands.

## Baseline

Commands:

```powershell
python --version
python -c "import asyncio, socket, ssl; print('python_runtime_ok')"
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
python -m unittest -q
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Key evidence:

- `python_runtime_ok`
- `compileall` passed.
- `pytest`: `45 passed, 1 skipped, 3 subtests passed`.
- `unittest`: `Ran 46 tests`, `OK (skipped=1)`.
- Full gate initially failed at `lint:format`: `.governed-ai\verify-powershell-policy.py` would be reformatted.

## Changes

- Formatted `.governed-ai/verify-powershell-policy.py` with `ruff format`.
- Hardened `scripts/google_ipv4_routing.ps1`:
  - initializes stripped Windows process environment values before config lookup;
  - resolves Python through `VPS_SSH_LAUNCHER_PYTHON -> .venv\Scripts\python.exe -> python -> py -3`;
  - validates `-RemoteApplyScript` as a safe absolute Linux path before embedding it in the remote apply command.
  - reports `xray-missing` instead of failing when a profile is not an Xray-based host.
- Hardened `scripts/run_gates.ps1`:
  - `-RunIntegration` now fails fast with a clear message when an integration config has multiple profiles, no `default`, and no `-IntegrationProfile`.
- Extended `test_scripts.py`:
  - parses every repository PowerShell script under `scripts/*.ps1`;
  - verifies the Google IPv4 routing helper is opt-in for apply and reuses project Python resolution.

## Final Verification

Commands:

```powershell
python -m pytest test_scripts.py -q
python -m ruff format --check .
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
python .governed-ai\verify-powershell-policy.py
git diff --check
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
```

Key evidence:

- `test_scripts.py`: `9 passed, 3 subtests passed`.
- Full gate: `46 passed, 1 skipped, 3 subtests passed`; `pip check` passed; `pip-audit` reported no known vulnerabilities; `bandit`, `vulture`, `ruff`, `mypy`, and `pyright` passed.
- PowerShell policy verifier: `status=pass`, `violation_count=0`.
- `git diff --check` passed; line-ending warnings for `.governed-ai/*.json/.py` were informational.

## Real VPS Verification

Commands:

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile bwg -IntegrationCommand 'printf vps-ssh-launcher-integration' -IntegrationExpected 'vps-ssh-launcher-integration'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile zz -IntegrationCommand 'printf vps-ssh-launcher-integration' -IntegrationExpected 'vps-ssh-launcher-integration' -SkipDependencyAudit
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/google_ipv4_routing.ps1 -Profile bwg
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/google_ipv4_routing.ps1 -Profile zz
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/google_ipv4_routing.ps1 -Profile bwg -Apply
python .\ssh_tool.py --config $config --profile bwg run --command "echo '==xray-active=='; systemctl is-active xray; echo '==xray-failed=='; systemctl is-failed xray || true; echo '==xray-recent-warnings=='; journalctl -u xray --since '10 minutes ago' --no-pager -p warning..alert | tail -80 || true"
```

Key evidence:

- First `-RunIntegration` attempt without `-IntegrationProfile` exposed an automation bug: pytest attempted interactive profile selection when the real config had multiple profiles and no default.
- After the fail-fast fix, `bwg` integration passed with `47 passed, 3 subtests passed`; full gates, dependency audit, lint and type checks passed.
- `zz` integration passed with `47 passed, 3 subtests passed`; dependency audit was skipped for the second profile to avoid repeating the same supply-chain check.
- `bwg` Google IPv4 read-only check: Xray `active`, drop-in present, `google_ipv4_out` and `ForceIPv4` present, `xray-config-test=config-ok`.
- `zz` Google IPv4 read-only check: Xray service `inactive`, Xray files absent, script reported `xray-missing` and completed successfully; IPv6 public egress check could not connect, while IPv4 egress returned successfully.
- `bwg -Apply`: remote script reported `google-ipv4-routing already current`, `Configuration OK`, Xray `active`, and the post-apply check still returned `xray-config-test=config-ok`.
- Post-apply observation on `bwg`: `systemctl is-active xray` returned `active`; `systemctl is-failed xray` returned `active`; `journalctl -u xray --since '10 minutes ago' -p warning..alert` returned `-- No entries --`.

## Rollback

- Revert this change set with git history for:
  - `.governed-ai/verify-powershell-policy.py`
  - `scripts/google_ipv4_routing.ps1`
  - `test_scripts.py`
  - `docs/change-evidence/20260426-code-review-optimization.md`
- Remote apply was explicitly executed only for `bwg` after user approval; it was idempotent and reported `google-ipv4-routing already current`.
- No real password, private key, token, or remote credential was recorded.

## Residual Risk

- Real SSH integration remains intentionally skipped unless `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1` or `scripts/run_gates.ps1 -RunIntegration` is used with valid local secrets.
- `target.json` remains local sensitive configuration and must not be staged.
