# 2026-05-04 zz scheduled maintenance setup

## Scope

- Target profile: `zz`
- Risk level: medium, remote cron and sing-box config mutation
- Execution mode: sequential host maintenance after `bwg` confirmation
- `bwg` status: not changed in this run
- Real SSH triggered: yes
- Secrets: no credentials or private key material recorded

## Goal

Configure and verify `zz` only:

- Weekly sing-box core auto-update through the VPS-local `vasma` wrapper path.
- Monthly system upgrade/optimization/cleanup on day 1 at 22:00 Asia/Shanghai.
- sing-box IPv4-only routing strategy and service health remain valid.

## Commands

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<read-only sing-box ipv4-only check>" --command-timeout 120
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<backup and add sing-box resolve ipv4_only rule>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<install monthly maintenance cron>" --command-timeout 120
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<read-only cron/service/config verification>" --command-timeout 120
```

## Key Output

Weekly sing-box kernel update cron:

```text
==selected-kernel==
sing-box
==cron==
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
==scripts==
--/etc/v2ray-agent/auto_update_xray.sh--
missing
--/etc/v2ray-agent/auto_update_singbox.sh--
syntax-ok
```

Initial sing-box IPv4-only check:

```text
sing-box=active
sing-box-enabled=enabled
sing-box version 1.13.11
config-ok
resolve-ipv4-only=missing
recent-ipv6-network-errors=found
```

Repair:

```text
backup=/root/vps-ssh-launcher-zz-ipv4-only-20260504T144200Z/config.json
inserted resolve ipv4_only at rule[1]
config-ok
sing-box=active
```

Monthly system maintenance cron:

```text
==timezone==
Time zone: Etc/UTC (UTC, +0000)
==monthly-cron==
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
==script-syntax==
syntax-ok
```

Final read-only verification:

```text
==cron-summary==
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
==wrapper-syntax==
xray-wrapper-absent-ok
singbox-wrapper-syntax-ok
maint-syntax-ok
==services==
sing-box=active
nginx=active
==sing-box-config-test==
config-ok
==ipv4-only-route==
resolve-ipv4-only=present
rule[1]={"action": "resolve", "strategy": "ipv4_only"}
==recent-ipv6-network-errors-after-restart==
none
```

## Rollback

Remote config rollback:

```bash
cp -a /root/vps-ssh-launcher-zz-ipv4-only-20260504T144200Z/config.json /etc/v2ray-agent/sing-box/conf/config.json
/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json
systemctl restart sing-box
```

Cron and wrapper rollback:

```bash
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v -E 'auto_update_singbox\.sh|auto_system_maint\.sh' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp"
rm -f /etc/v2ray-agent/auto_update_singbox.sh
systemctl restart sing-box nginx
```

Do not run rollback unless the operator confirms `zz` is abnormal.
