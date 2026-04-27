# 2026-04-27 vasma kernel cron code update

## Scope

- Repository change: add a managed local entrypoint for remote weekly proxy kernel update cron.
- Remote change: replace old direct GitHub release download updater scripts with thin wrappers that call `vasma`.
- Profiles updated:
  - `bwg`: Xray-core only.
  - `zz`: sing-box only.

## Code Changes

- Added `scripts/vasma_kernel_update_cron.ps1`.
- Updated `README.md` with the intended `vasma`-based update model.
- Updated `test_scripts.py` to assert:
  - Xray update uses `printf '16\n1\n1\ny\n' | /usr/bin/vasma`.
  - sing-box update uses `printf '16\n2\n1\n' | /usr/bin/vasma`.
  - no direct GitHub release download URL or `REPO="..."` updater pattern is present in the project script.
- Formatted `.governed-ai/verify-powershell-policy.py` because the full gate's `ruff format --check .` was already blocked by that file.

## Remote State After Apply

### bwg

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
```

Evidence:

```cron
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
```

`/etc/v2ray-agent/auto_update_xray.sh` now contains:

```text
Auto-update Xray core through v2ray-agent/vasma.
Menu path: 16.core管理 -> 1.Xray-core -> 1.升级Xray-core -> y when same version.
printf '16\n1\n1\ny\n' | /usr/bin/vasma
```

`auto_update_singbox.sh` is absent on `bwg`.

### zz

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
```

Evidence:

```cron
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
```

`/etc/v2ray-agent/auto_update_singbox.sh` now contains:

```text
Auto-update sing-box core through v2ray-agent/vasma.
Menu path: 16.core管理 -> 2.sing-box -> 1.升级 sing-box.
printf '16\n2\n1\n' | /usr/bin/vasma
```

`auto_update_xray.sh` is absent on `zz`.

## Verification

- `python -m pytest -q`: `55 passed, 1 skipped, 8 subtests passed`.
- `python -m pytest -q test_scripts.py test_auto_install.py`: `18 passed, 8 subtests passed`.
- `scripts/run_gates.ps1 -RunIntegration ... -IntegrationProfile bwg`: passed.
- `scripts/run_gates.ps1 -RunIntegration ... -IntegrationProfile zz`: passed.
- Both full gates reported:
  - `56 passed, 8 subtests passed`.
  - `pip check`: no broken requirements.
  - `pip-audit`: no known vulnerabilities.
  - `ruff check`: passed.
  - `ruff format --check`: passed.
  - `mypy`: passed.
  - `pyright`: passed.

## Rollback

Restore the previous remote updater scripts from git/evidence if needed, or rerun:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
```

The new project entrypoint is idempotent for the intended profile/kernel mapping.
