# bwg scheduled maintenance check evidence

- Date: 2026-05-27 Asia/Shanghai (`2026-05-26` UTC on the VPS)
- Target: `bwg` profile, host `144.34.229.116`, SSH port `29712`
- Scope: verify weekly Xray-core update wrapper, monthly system maintenance, Xray service health, IPv4-only Google routing, and harden the Xray wrapper stable-release probe.
- Risk: low to medium. The wrapper regeneration changed `/etc/v2ray-agent/auto_update_xray.sh` and the local generator, but did not trigger Xray-core update or restart Xray.
- Rollback: restore `scripts/vasma_kernel_update_cron.ps1` from git history and re-run the previous wrapper if needed. Remote rollback can restore the older `/etc/v2ray-agent/auto_update_xray.sh` content from host backup if available, or regenerate from the prior commit.

## Entrypoints

```powershell
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile bwg check
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\google_ipv4_routing.ps1 -Profile bwg
python .\ssh_tool.py --config $env:APPDATA\vps-ssh-launcher\target.json --profile bwg run --command <remote-readonly-checks>
```

## Results

- SSH connectivity: `OK - root@144.34.229.116:29712`.
- Host timezone: `Etc/UTC`; system clock synchronized and NTP active.
- Cron daemon: `active`.
- Weekly kernel cron: `0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh`.
- Monthly maintenance cron: `0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh`.
- `0 14 1 * *` on this UTC host maps to Beijing time `22:00` on the 1st day of each month.
- `/usr/bin/vasma -> /etc/v2ray-agent/install.sh` exists and is executable.
- `/etc/v2ray-agent/auto_update_xray.sh` exists, is executable, uses the `vasma` Xray menu path, and passed `bash -n`.
- `/etc/v2ray-agent/auto_update_singbox.sh` is absent on `bwg`, matching the host mapping.
- Xray service: `active`.
- Xray version: `Xray 26.3.27`.
- Xray config: `xray run -test -confdir /etc/v2ray-agent/xray/conf` returned `config-ok`.
- Google IPv4 routing drop-in exists: `/etc/systemd/system/xray.service.d/20-google-ipv4-routing.conf`.
- Google IPv4 outbound exists with `domainStrategy: ForceIPv4` and routing tag `google_ipv4_out`.
- Public host egress check showed both host IPv4 `144.34.229.116` and host IPv6 `2607:8700:5500:50c7::2`; the IPv4-only setting being checked here is the Xray Google routing override, not disabling host IPv6 globally.
- Monthly maintenance script `/etc/v2ray-agent/auto_system_maint.sh` exists, is executable, passed `bash -n`, and includes lock, apt update/upgrade/autoremove/clean, journal vacuum, service health checks, and reboot policy.
- Monthly dry-run at `2026-05-26 16:53:45 UTC` exited `0`, checked Xray active, skipped disabled sing-box, reported no reboot required, and completed.

## Finding

The weekly wrapper currently runs, but its latest-stable-version probe returns empty because the GitHub releases API first page contains five prereleases:

```text
v26.5.9 true false
v26.5.3 true false
v26.4.25 true false
v26.4.17 true false
v26.4.15 true false
```

The host is not stale today because the GitHub latest stable release is `v26.3.27`, matching the installed Xray version. However, the wrapper has a latent risk: if the installed Xray is older while the first API page contains only prereleases, it can skip instead of updating to the current stable release.

Recommended follow-up after user confirmation: update the wrapper/repo generator to use a stable-release source that does not miss the latest stable release, then re-run the same `bwg` checks before touching `zz`.

## Repair

Changed the local wrapper generator `scripts/vasma_kernel_update_cron.ps1` so both Xray and sing-box wrappers use GitHub `releases/latest` instead of listing recent releases and filtering prereleases locally.

Remote apply command for `bwg` only:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
```

Remote verification:

```text
/etc/v2ray-agent/auto_update_xray.sh uses https://api.github.com/repos/XTLS/Xray-core/releases/latest
current=v26.3.27
latest=v26.3.27
xray=active
xray-config-ok
nginx=active
cron:
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
```

No Xray update was manually triggered.

## Collision Avoidance Optimization

The monthly task remains at Beijing time `22:00` on the 1st day of each month:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

The weekly Xray wrapper was moved from `0 14 * * 5` to `20 14 * * 5` on the UTC host. This keeps the weekly job on Friday evening Beijing time, but avoids competing for `/run/v2ray-agent-maint.lock` if the 1st day of a month is also Friday.

Remote verification after the change:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
current=v26.3.27 latest=v26.3.27
xray=active
xray-config-ok
nginx=active
```

## Local Gates

```text
python -m compileall -q ssh_tool.py auto_install.py test_ssh_tool.py test_auto_install.py test_scripts.py test_integration_real_ssh.py
python -m pytest -q
82 passed, 1 skipped, 22 subtests passed
python -m unittest -q
Ran 83 tests; OK (skipped=1)
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_gates.ps1
pip check: No broken requirements found
pip-audit: No known vulnerabilities found
ruff: All checks passed
mypy: Success
pyright: 0 errors, 0 warnings
```
