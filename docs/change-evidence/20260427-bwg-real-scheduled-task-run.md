# 2026-04-27 bwg real scheduled task run

## Scope

- Host/profile: `bwg` only.
- Entry point: `run.cmd`.
- Excluded: `zz` was not connected, checked, or modified.
- Tasks actually run:
  - weekly Xray kernel update task.
  - monthly system upgrade/optimization/cleanup task.

## Precheck

Command shape:

```powershell
.\run.cmd -Profile bwg -Command "<precheck>"
```

Evidence:

- Host: `sciman`.
- `reboot-required`: `reboot-not-required`.
- Maintenance lock: free.
- `xray`: `active`.
- `nginx`: `active`.
- Xray config test: `config-ok`.
- Xray version before task: `26.3.27`.
- Script syntax:
  - `bash -n /etc/v2ray-agent/auto_update_xray.sh`: passed.
  - `bash -n /etc/v2ray-agent/auto_system_maint.sh`: passed.

## Real Run 1: Weekly Xray Kernel Update

Command:

```powershell
.\run.cmd -Profile bwg -Command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh"
```

Evidence:

- Exit code: `0`.
- Xray before: `26.3.27`.
- Xray after: `26.3.27`.
- Service after run: `xray=active`.
- Config after run: `config-ok`.
- Log:
  - `Current: v26.3.27`.
  - `Latest: v26.3.27`.
  - `Already up-to-date, skipping`.

Conclusion: the weekly task runs correctly. No update was needed because the installed Xray core was already current.

## Real Run 2: Monthly System Maintenance

Command:

```powershell
.\run.cmd -Profile bwg -Command "AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh"
```

Evidence:

- Pre-run `reboot-required`: `reboot-not-required`.
- Exit code: `0`.
- The task executed the real path, not dry-run:
  - `apt update`.
  - `apt upgrade`.
  - `apt autoremove --purge`.
  - `apt clean`.
  - `journalctl --vacuum-size=50M --vacuum-time=7d`.
  - proxy service health checks.
- Log:
  - `host=sciman dry_run=0`.
  - `Step 1: apt update`.
  - `Step 2: apt upgrade`.
  - `Step 3: apt autoremove --purge`.
  - `Step 4: apt clean`.
  - `Step 5: journal vacuum`.
  - `Step 6: proxy service health checks`.
  - `OK: xray is active`.
  - `sing-box enabled_state=disabled, skip`.
  - `No reboot required`.
  - `System maintenance done`.
- Post-run `reboot-required`: `reboot-not-required`.
- No reboot/shutdown systemd job was scheduled.
- `xray`: `active`.
- `nginx`: `active`.
- Xray config test: `config-ok`.

Conclusion: the monthly task runs correctly and did not schedule a reboot because no reboot was required.

## Final Verification

- Local TCP checks to `144.34.229.116`:
  - `29712`: `TcpTestSucceeded=True`.
  - `443`: `TcpTestSucceeded=True`.
  - `15374`: `TcpTestSucceeded=True`.
  - `22835`: `TcpTestSucceeded=True`.
  - `34546`: `TcpTestSucceeded=True`.
- Remote final status:
  - `xray=active`.
  - `nginx=active`.
  - Xray config test: `config-ok`.
  - `apt list --upgradable`: no package entries.
  - `reboot-required`: `reboot-not-required`.
- Full repo gate with real `bwg` SSH integration:
  - `55 passed, 6 subtests passed`.
  - `pip check`: no broken requirements.
  - `pip-audit`: no known vulnerabilities.
  - `ruff`, `mypy`, `pyright`: all passed.

## Rollback

- No rollback needed.
- No remote configuration was changed during this real-run verification.
- If the monthly task ever schedules a reboot unexpectedly, cancel before execution with:

```bash
shutdown -c
```
