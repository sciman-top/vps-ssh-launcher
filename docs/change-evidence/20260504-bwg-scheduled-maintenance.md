# 2026-05-04 bwg scheduled maintenance setup

## Scope

- Target profile: `bwg`
- Risk level: medium, remote cron mutation
- Execution mode: sequential host maintenance
- `zz` status: not touched in this run
- Real SSH triggered: yes
- Secrets: no credentials or private key material recorded

## Goal

Configure and verify `bwg` only:

- Weekly Xray core auto-update through the VPS-local `vasma` wrapper path.
- Monthly system upgrade/optimization/cleanup on day 1 at 22:00 Asia/Shanghai.
- Google/Gemini IPv4 routing and service health remain valid.

## Commands

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
```

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<install monthly maintenance cron>" --command-timeout 120
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<read-only cron/service/config verification>" --command-timeout 120
```

## Key Output

Weekly Xray kernel update cron:

```text
==selected-kernel==
xray
==cron==
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
==scripts==
--/etc/v2ray-agent/auto_update_xray.sh--
syntax-ok
--/etc/v2ray-agent/auto_update_singbox.sh--
missing
```

Google/Gemini IPv4 routing:

```text
==service==
active
==google-ipv4-dropin==
[Service]
ExecStartPre=/bin/bash /etc/v2ray-agent/apply-google-ipv4-routing-config.sh
==xray-config-test==
config-ok
==google-ipv4-routing==
google_ipv4_out
ForceIPv4
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
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
==wrapper-syntax==
xray-wrapper-syntax-ok
singbox-wrapper-absent-ok
maint-syntax-ok
==services==
xray=active
nginx=active
==xray-config-test==
config-ok
==google-force-ipv4==
google_ipv4_out
ForceIPv4
```

## Rollback

Remove only the affected cron entries and wrappers on `bwg` if rollback is needed:

```bash
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v -E 'auto_update_xray\.sh|auto_system_maint\.sh' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp"
rm -f /etc/v2ray-agent/auto_update_xray.sh
systemctl restart xray nginx
```

Do not run rollback unless the operator confirms `bwg` is abnormal.
