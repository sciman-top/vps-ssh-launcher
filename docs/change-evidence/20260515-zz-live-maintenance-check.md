# 2026-05-15 zz live maintenance check

## Scope

- Target profile: `zz`
- Risk level: high for kernel wrapper trigger and medium for sing-box route repair
- `bwg` status: not touched
- Real SSH triggered: yes
- Secrets: no credentials, private keys, or target config content recorded

## Goal

Verify and, if needed, repair `zz` only:

- Weekly sing-box auto-update uses the VPS-local `vasma` wrapper path.
- The opposite Xray weekly wrapper is absent on `zz`.
- sing-box IPv4-only route is present and service health remains valid.
- Monthly system upgrade/optimization/cleanup is scheduled for day 1 at 22:00 Asia/Shanghai.
- Manual sing-box wrapper trigger exits successfully and leaves sing-box healthy.

## Commands

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<baseline, sing-box, ipv4-only, cron verification>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<backup, insert resolve ipv4_only, sing-box check, restart>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<monthly dry-run verification>" --command-timeout 180
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "/bin/bash /etc/v2ray-agent/auto_update_singbox.sh" --command-timeout 600
```

```powershell
python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<post-wrapper health check>" --command-timeout 240
```

## Key Output

Weekly sing-box wrapper:

```text
==selected-kernel==
sing-box
==cron==
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
--/etc/v2ray-agent/auto_update_xray.sh--
missing
--/etc/v2ray-agent/auto_update_singbox.sh--
syntax-ok
```

Initial state:

```text
hostname=C202604071640752
sing-box=active
xray=inactive
nginx=active
sing-box version 1.13.11
config-ok
resolve-ipv4-only=missing
```

IPv4-only route repair:

```text
backup=/root/vps-ssh-launcher-zz-ipv4-only-20260514T162250Z/config.json
inserted resolve ipv4_only at rule[1]
config-ok
sing-box=active
post-config-ok
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

The dry-run verified the intended steps without upgrading or rebooting:

```text
[dry-run] apt-get update -qq -o DPkg::Lock::Timeout=600
[dry-run] apt-get upgrade -y -qq --with-new-pkgs ...
[dry-run] apt-get autoremove --purge -y -qq ...
[dry-run] apt-get clean -qq
[dry-run] journalctl --vacuum-size=50M --vacuum-time=7d
INFO: xray enabled_state=disabled, skip
OK: sing-box is active
```

Manual wrapper trigger and post-check:

```text
[2026-05-14 16:23:24] ========== vasma sing-box update start ==========
[2026-05-14 16:23:27] INFO: current sing-box version v1.13.11 equals vasma-visible latest; skip reinstall
[2026-05-14 16:23:27] ========== vasma sing-box update skipped ==========
```

```text
sing-box version 1.13.11
vasma-visible-stable=v1.13.11
sing-box=active
xray=inactive
nginx=active
config-ok
resolve-ipv4-only=present
rule[1]={"action": "resolve", "strategy": "ipv4_only"}
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
lock-free
0 loaded units listed.
```

Listeners after repair and wrapper trigger:

```text
udp *:443 sing-box
tcp 0.0.0.0:35622 nginx
tcp 0.0.0.0:31854 sshd
tcp *:22752 sing-box
```

## Result

`zz` had one configuration drift: the sing-box `resolve ipv4_only` route was
missing. It was repaired with a timestamped backup, validated by `sing-box check`,
and restarted successfully.

After the repair, `zz` is healthy. The weekly sing-box wrapper is installed and
uses the VPS-local `vasma` menu path. The manual wrapper trigger exited
successfully and skipped reinstall because the current sing-box version already
equals the latest stable version visible to `vasma`.

The monthly maintenance schedule is installed at the intended Beijing time and
the script dry-run succeeds. The previous real monthly run on 2026-05-01 was
skipped because another maintenance job held the shared lock at that moment.

## Rollback

Remote sing-box config rollback if the operator reports `zz` is abnormal:

```bash
cp -a /root/vps-ssh-launcher-zz-ipv4-only-20260514T162250Z/config.json /etc/v2ray-agent/sing-box/conf/config.json
/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json
systemctl restart sing-box
```

Cron and wrapper rollback should not be used unless explicitly required:

```bash
tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v -E 'auto_update_singbox\.sh|auto_system_maint\.sh' > "$tmp" || true
crontab "$tmp"
rm -f "$tmp" /etc/v2ray-agent/auto_update_singbox.sh
systemctl restart sing-box nginx
```
