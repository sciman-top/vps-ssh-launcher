# 2026-04-27 cron schedule verification

## Scope

- Entry point required by operator: `run.cmd`.
- Profiles checked and fixed: `bwg`, `zz`.
- `bwg` expected proxy kernel: Xray only.
- `zz` expected proxy kernel: sing-box only.

## Finding

- Weekly proxy kernel update tasks were present and pointed at the expected host-specific updater:
  - `bwg`: `/etc/v2ray-agent/auto_update_xray.sh`
  - `zz`: `/etc/v2ray-agent/auto_update_singbox.sh`
- Both update scripts update the corresponding kernel binary from the upstream release repository and restart only the corresponding service.
  - `bwg`: `REPO="XTLS/Xray-core"`, `XRAY_DIR="/etc/v2ray-agent/xray"`, `systemctl stop/start xray`.
  - `zz`: `REPO="SagerNet/sing-box"`, `SB_DIR="/etc/v2ray-agent/sing-box"`, `systemctl stop/start sing-box`.
- No reinstall script invocation was found in either weekly updater.
- Monthly system maintenance was present but scheduled at `20 14 1 * *`, which is 22:20 Asia/Shanghai because both hosts use `Etc/UTC`.

## Fix

- Updated both hosts' root crontab monthly maintenance line to:

```cron
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

- This is 22:00 Asia/Shanghai on the 1st day of each month.
- Created a root crontab backup before replacing the line on each host:
  - `/root/crontab.backup-vps-ssh-launcher-mHS`
  - Note: the name is odd because `run.cmd` passes through a Windows batch layer, where `%Y%m%d%H%M%S` was expanded before reaching the remote shell. The backup content was still created on each host before crontab replacement.

## Verification

### bwg

Command shape:

```powershell
.\run.cmd -Profile bwg -Command "<remote cron and script verification>"
```

Evidence:

- Host: `sciman`.
- Timezone: `Etc/UTC`.
- `cron`: `active`, `enabled`.
- Root crontab after fix:

```cron
30 1 * * * /bin/bash /etc/v2ray-agent/install.sh RenewTLS >> /etc/v2ray-agent/crontab_tls.log 2>&1
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

- `bash -n /etc/v2ray-agent/auto_update_xray.sh`: passed.
- `bash -n /etc/v2ray-agent/auto_system_maint.sh`: passed.
- Monthly maintenance dry-run:
  - `DRY_RUN=1 AUTO_REBOOT_ON_MAINT=0 /bin/bash /etc/v2ray-agent/auto_system_maint.sh`
  - exit: `0`.
- Dry-run log confirmed:
  - `OK: xray is active`.
  - `sing-box enabled_state=disabled, skip`.
- Weekly Xray log showed Friday 14:00 UTC checks and no reinstall action:
  - `2026-04-24 14:00:01 Current: v26.3.27`
  - `2026-04-24 14:00:03 Latest: v26.3.27`
  - `Already up-to-date, skipping`.

### zz

Command shape:

```powershell
.\run.cmd -Profile zz -Command "<remote cron and script verification>"
```

Evidence:

- Host: `C202604071640752`.
- Timezone: `Etc/UTC`.
- `cron`: `active`, `enabled`.
- Root crontab after fix:

```cron
30 1 * * * /bin/bash /etc/v2ray-agent/install.sh RenewTLS >> /etc/v2ray-agent/crontab_tls.log 2>&1
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

- `bash -n /etc/v2ray-agent/auto_update_singbox.sh`: passed.
- `bash -n /etc/v2ray-agent/auto_system_maint.sh`: passed.
- Monthly maintenance dry-run:
  - `DRY_RUN=1 AUTO_REBOOT_ON_MAINT=0 /bin/bash /etc/v2ray-agent/auto_system_maint.sh`
  - exit: `0`.
- Dry-run log confirmed:
  - `xray enabled_state=disabled, skip`.
  - `OK: sing-box is active`.
- Weekly sing-box log showed Friday 14:00 UTC checks and kernel update behavior:
  - `2026-04-24 14:00:01 Current: 1.13.8`
  - `2026-04-24 14:00:04 Latest: v1.13.11`
  - `2026-04-24 14:00:10 Updated: 1.13.8 -> 1.13.11`.

## Current Status

- Weekly proxy kernel update: OK on both hosts.
- Monthly system upgrade/optimization/cleanup at 22:00 Asia/Shanghai on the 1st day: fixed and verified on both hosts.
- `bwg` does not require sing-box.
- `zz` does not require Xray.

## Rollback

Restore the per-host crontab backup if needed:

```bash
crontab /root/crontab.backup-vps-ssh-launcher-mHS
crontab -l
```
