# 2026-07-10 bwg live maintenance evidence

## Scope

- Rule IDs: R1-R8, E4, E5.
- Risk: high for the real Xray wrapper and system-maintenance runs; medium for
  the persistent wrapper/cron refresh.
- Target: profile `bwg` only; public address and SSH port redacted.
- Explicit exclusion: profile `zz` was not connected, checked, or modified.
- Real SSH: yes, only against `bwg`.
- Goal: verify the Xray-only weekly `vasma` wrapper, Google/Gemini IPv4-only
  routing, and the monthly system upgrade/cleanup task.

## Local launcher defects found and fixed

The first checks returned exit code 0 without connecting because the
compatibility entry point did not call the packaged CLI when executed as a
script. After fixing that issue, the first real `-Apply` attempt stopped on
the first Bash line because a PowerShell CRLF here-string reached Bash as
`pipefail\r`; no remote write occurred on that attempt.

The shared launcher helper now normalizes CRLF arguments to LF, streams command
output without contaminating the numeric exit code, and `ssh_tool.py` invokes
`main()` when run directly. Focused regression tests cover direct execution,
line-ending normalization, and PowerShell parsing.

## Weekly Xray update

The controlled apply and read-only verification used:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray
```

Verified remote state:

```text
timezone: Etc/UTC
cron: 20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
xray wrapper: executable, bash -n OK
sing-box wrapper: absent
/usr/bin/vasma -> /etc/v2ray-agent/install.sh
```

The wrapper was then run as the only remote command. It exited 0 at
`2026-07-10 09:13:00 UTC`, detected that Xray `v26.3.27` already matched the
latest stable release visible to the wrapper, skipped reinstall, and completed
an Xray configuration test with `Configuration OK`.

Post-run checks confirmed Xray, nginx, SSH, fail2ban, and cron were active;
sing-box was inactive/disabled; Xray listened on the expected public and
loopback paths. Exact listener ports are intentionally redacted.

## IPv4-only routing

The Xray configuration still contains the Google/Gemini route to
`google_ipv4_out`, and the outbound uses `domainStrategy: ForceIPv4`.
`xray.service` retains:

```text
ExecStartPre=/bin/bash /etc/v2ray-agent/apply-google-ipv4-routing-config.sh
```

The host itself remained dual stack; exact public addresses are intentionally
redacted. The scoped Google/Gemini outbound was forced to IPv4 by Xray rather
than disabling host IPv6 globally.

## Monthly system maintenance

The persistent schedule is:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

With the VPS on `Etc/UTC`, this is 22:00 Asia/Shanghai on day 1. Logs prove
successful scheduled runs on June 1 and July 1. The July 1 run upgraded
packages, cleaned the system, verified Xray, and scheduled the required reboot
for 15 minutes later; the host subsequently returned healthy.

A real interactive run on July 10 used `AUTO_REBOOT_ON_MAINT=0` to avoid an
unrequested immediate reboot while preserving the persistent cron policy. It
upgraded `iproute2`, `curl`, `libcurl4t64`, `libcurl3t64-gnutls`, and
`docker-compose-plugin`, then ran autoremove, apt clean, journal vacuum, and
proxy health checks. It exited 0.

Final state:

```text
apt-get -s upgrade: 0 upgraded
reboot-required: no
xray/nginx/ssh/fail2ban/cron: active
xray config: Configuration OK
nginx config: successful
disk: 12G/40G (32%)
```

## Local verification

The fixed order was executed:

```text
build -> pytest -> unittest -> scripts/run_gates.ps1
```

The full gate included real SSH integration for `bwg` only:

```text
94 passed, 20 subtests passed
pip check: no broken requirements
pip-audit: no known vulnerabilities
Bandit/Vulture/Ruff: passed
mypy: success
pyright: 0 errors, 0 warnings
```

## Rollback

- Local: revert the changes to `ssh_tool.py`,
  `scripts/lib/project_environment.ps1`, and `test_scripts.py`.
- Xray wrapper/cron: restore root crontab from its prior history and restore the
  prior `/etc/v2ray-agent/auto_update_xray.sh`; the refreshed state is
  functionally equivalent to the prior intended Xray-only state.
- Package changes: consult `/var/log/apt/history.log` and downgrade only a
  specifically identified faulty package. Do not bulk downgrade.
- Connectivity recovery: use the provider console, restore the Xray config
  backup if a later manual change breaks it, then run the Xray config test
  before restarting the service.
