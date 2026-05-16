# 2026-05-14 bwg live maintenance check

## Scope

- Target profile: `bwg`
- Risk level: high for kernel wrapper trigger, executed on one host only
- `zz` status: not touched
- Real SSH triggered: yes
- Secrets: no credentials, private keys, or target config content recorded

## Goal

Verify `bwg` only:

- Weekly Xray-core auto-update uses the VPS-local `vasma` wrapper path.
- The opposite sing-box weekly wrapper is absent on `bwg`.
- Google/Gemini IPv4 routing markers remain valid.
- Monthly system upgrade/optimization/cleanup is scheduled for day 1 at 22:00 Asia/Shanghai.
- Manual Xray wrapper trigger exits successfully and leaves Xray healthy.

## Commands

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray
```

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\google_ipv4_routing.ps1 -Profile bwg
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<monthly cron and dry-run verification>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh" --command-timeout 300
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg run --command "<post-wrapper health check>" --command-timeout 180
```

## Key Output

Weekly Xray wrapper:

```text
==selected-kernel==
xray
==cron==
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
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
==google-ipv4-routing==
google_ipv4_out
ForceIPv4
==xray-config-test==
config-ok
```

Monthly system maintenance:

```text
==timezone==
Time zone: Etc/UTC (UTC, +0000)
==monthly-cron==
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
==maint-syntax==
syntax-ok
==monthly-dry-run==
dry-run-exit=0
==lock-probe==
lock-free
```

The latest scheduled monthly run on 2026-05-01 14:00 UTC logged:

```text
INFO: another maintenance job is already running; exit
```

The dry-run on 2026-05-14 verified the intended steps without upgrading or
rebooting:

```text
[dry-run] apt-get update -qq -o DPkg::Lock::Timeout=600
[dry-run] apt-get upgrade -y -qq --with-new-pkgs ...
[dry-run] apt-get autoremove --purge -y -qq ...
[dry-run] apt-get clean -qq
[dry-run] journalctl --vacuum-size=50M --vacuum-time=7d
OK: xray is active
INFO: sing-box enabled_state=disabled, skip
```

Manual wrapper trigger and post-check:

```text
[2026-05-14 15:33:12] ========== vasma Xray-core update start ==========
[2026-05-14 15:33:12] WARN: vasma-visible stable Xray version is empty; skip update to avoid empty download URL
Configuration OK.
[2026-05-14 15:33:13] ========== vasma Xray-core update skipped ==========
```

```text
Xray 26.3.27
xray=active
nginx=active
sing-box=inactive
config-ok
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
lock-free
```

Release comparison:

```text
current Xray: 26.3.27
per_page=5 stable query: empty
per_page=30 stable query: v26.3.27
```

## Result

`bwg` is healthy after the real wrapper trigger. The scheduled weekly Xray
wrapper is installed and uses the `vasma` menu path, but it safely skipped this
run because the current `vasma` script still cannot see a stable Xray release in
its `per_page=5` query window. A wider read-only query shows the current
installed Xray version already equals the latest stable version visible in the
larger window.

The monthly maintenance schedule is installed at the intended Beijing time and
the script dry-run succeeds. The previous real monthly run on 2026-05-01 was
skipped because another maintenance job held the shared lock at that moment.

## Rollback

No rollback was applied. If rollback is needed, remove only the affected `bwg`
cron entries and wrapper after operator confirmation:

```bash
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v -E 'auto_update_xray\.sh|auto_system_maint\.sh' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp" /etc/v2ray-agent/auto_update_xray.sh
systemctl restart xray nginx
```
