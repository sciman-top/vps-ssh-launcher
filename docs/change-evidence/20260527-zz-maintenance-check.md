# zz scheduled maintenance check evidence

- Date: 2026-05-27 Asia/Shanghai
- Target: `zz` profile, host `38.244.39.84`, SSH port `31854`
- Scope: verify weekly sing-box update wrapper, monthly system maintenance, sing-box service health, and repair the missing IPv4-only route setting.
- Risk: medium for the sing-box config repair because it required a persistent JSON change and `systemctl restart sing-box`. No `bwg` command was run in this phase.
- Rollback: restore `/etc/v2ray-agent/sing-box/conf/config.json` from `/root/vps-ssh-launcher-zz-ipv4-only-20260527T133845Z/config.json`, then run `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json` and restart sing-box.

## Entrypoints

```powershell
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile zz check
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile zz run --command <remote-checks>
```

## Results

- SSH connectivity: `OK - root@38.244.39.84:31854`.
- Host timezone: `Etc/UTC`; system clock synchronized and NTP active.
- Cron daemon: `active`.
- Weekly kernel cron: `0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh`.
- Monthly maintenance cron: `0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh`.
- `0 14 1 * *` on this UTC host maps to Beijing time `22:00` on the 1st day of each month.
- `/usr/bin/vasma -> /etc/v2ray-agent/install.sh` exists and is executable.
- `/etc/v2ray-agent/auto_update_singbox.sh` exists, is executable, uses the `vasma` sing-box menu path, and passed `bash -n`.
- `/etc/v2ray-agent/auto_update_xray.sh` is absent on `zz`, matching the host mapping.
- sing-box service: `active`.
- Xray service: `inactive`, expected for this host mapping.
- nginx service: `active`; listener observed on `0.0.0.0:35622`.
- sing-box version: `1.13.12`.
- sing-box latest stable probe: `v1.13.12`; wrapper logic can see the current stable release.
- Weekly log shows `2026-05-22 14:00:06 INFO: current sing-box version v1.13.12 equals vasma-visible latest; skip reinstall`.
- sing-box config check: `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json` returned `sing-box-config-ok`.
- Monthly maintenance script `/etc/v2ray-agent/auto_system_maint.sh` exists, is executable, passed `bash -n`, and includes lock, apt update/upgrade/autoremove/clean, journal vacuum, service health checks, and reboot policy.
- Monthly dry-run at `2026-05-27 12:27:06 UTC` exited `0`, skipped disabled Xray, checked sing-box active, reported no reboot required, and completed.

## Finding

The IPv4-only route setting is not present in the current sing-box configuration.

Config search result:

```text
/etc/v2ray-agent/sing-box/conf/config.json route.rules = [{"action":"sniff","timeout":"1s"}]
/etc/v2ray-agent/sing-box/conf/config/sniff.json route.rules = [{"action":"sniff","timeout":"1s"}]
deep search for action=resolve, strategy=ipv4_only, domain_strategy: no matches
```

Conclusion: weekly sing-box update and monthly maintenance are functioning, but the `ipv4_only` route setting is missing and should not be reported as healthy until repaired and rechecked with `sing-box check` plus service health verification.

## Repair

Remote repair entrypoint:

```powershell
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile zz run --command <backup-edit-check-restart> --command-timeout 180
```

Repair evidence:

```text
backup=/root/vps-ssh-launcher-zz-ipv4-only-20260527T133845Z/config.json
before-route=[{"action":"sniff","timeout":"1s"}]
after-route-candidate=[{"action":"sniff","timeout":"1s"},{"action":"resolve","strategy":"ipv4_only"}]
config-check=ok
sing-box-active=active
xray-active=inactive
nginx-active=active
route-final=[{"action":"sniff","timeout":"1s"},{"action":"resolve","strategy":"ipv4_only"}]
```

Post-restart listeners remained present:

```text
udp *:443 sing-box
tcp 0.0.0.0:35622 nginx
tcp *:22752 sing-box
```

Final conclusion: `zz` weekly sing-box update, monthly maintenance, service health, and `ipv4_only` route are now healthy.

## Collision Avoidance Optimization

The monthly task remains at Beijing time `22:00` on the 1st day of each month:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

The weekly sing-box wrapper was moved from `0 14 * * 5` to `20 14 * * 5` on the UTC host. This keeps the weekly job on Friday evening Beijing time, but avoids competing for `/run/v2ray-agent-maint.lock` if the 1st day of a month is also Friday.

Remote verification after the change:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
current=v1.13.12 latest=v1.13.12
sing-box=active
sing-box-config-ok
xray=inactive
nginx=active
route-final=[{"action":"sniff","timeout":"1s"},{"action":"resolve","strategy":"ipv4_only"}]
```
