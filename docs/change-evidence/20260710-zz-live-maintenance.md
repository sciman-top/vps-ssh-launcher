# 2026-07-10 zz live maintenance evidence

## Scope

- Rule IDs: R1-R8, E4, E5.
- Risk: high for the real sing-box wrapper and system-maintenance runs; medium
  for the persistent wrapper/cron and sing-box route repair.
- Target: profile `zz` only; public address and SSH port redacted.
- Explicit exclusion: profile `bwg` was not connected, checked, or modified
  during this phase.
- Real SSH: yes, only against `zz`.
- Goal: verify the sing-box-only weekly `vasma` wrapper, `ipv4_only` route,
  and monthly system upgrade/cleanup task.

## Baseline finding

The weekly and monthly schedules were present, sing-box `1.13.14` was active,
Xray was disabled, and the configuration passed `sing-box check`. However,
structured JSON inspection found no `ipv4_only` value or resolve rule.

The sing-box update log and config timestamp identified the root cause: the
June 26 scheduled `vasma` update successfully installed a newer sing-box and
restarted it, but the generated configuration replaced the previously repaired
`resolve + ipv4_only` rule. The old wrapper checked only configuration
validity, so it could not detect this semantic drift.

## Persistent repair

Before changing the remote wrapper, the current wrapper, root crontab, and
sing-box configuration were backed up to:

```text
/root/vps-ssh-launcher-zz-before-ipv4-wrapper-20260710T092553Z
```

The repository sing-box wrapper generator now:

- checks for exactly the required `resolve + ipv4_only` semantic rule;
- copies the current configuration to a timestamped backup when it is absent;
- generates the candidate with `jq`;
- validates the candidate with the current sing-box binary;
- replaces the configuration and restarts sing-box only when repair is needed;
- verifies service activity and the final configuration after the operation.

The controlled apply retained only the intended host mapping:

```text
20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
sing-box wrapper: executable, bash -n OK
Xray wrapper: absent
```

## Weekly sing-box update

The wrapper was run as the only remote command. At
`2026-07-10 09:26:15 UTC`, it detected that current sing-box `v1.13.14`
matched the latest stable release, skipped reinstall, restored the missing
`ipv4_only` rule, restarted sing-box, and exited 0.

The automatic repair backup is:

```text
/etc/v2ray-agent/sing-box/conf/config.json.pre-ipv4-only.20260710T092615Z
```

Final route state:

```json
[
  {"action": "sniff", "timeout": "1s"},
  {"action": "resolve", "strategy": "ipv4_only"}
]
```

The semantic count query returned `ipv4-only-count=1`. sing-box, nginx, SSH,
fail2ban, and cron were active; Xray remained inactive/disabled. Expected
sing-box and nginx listeners were present, with exact ports intentionally
redacted. IPv4 egress was available and IPv6 egress was unavailable; the
public address is intentionally redacted.

## Monthly system maintenance

The persistent schedule is:

```text
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

With the VPS on `Etc/UTC`, this is 22:00 Asia/Shanghai on day 1. Logs prove
successful scheduled runs on June 1 and July 1. The July 1 run upgraded and
cleaned the system, verified sing-box, scheduled the required reboot for 15
minutes later, and the host subsequently returned healthy.

A real interactive run on July 10 used `AUTO_REBOOT_ON_MAINT=0` to avoid an
unrequested immediate reboot while preserving the persistent cron policy. It
upgraded `python3-problem-report`, `python3-apport`,
`apport-core-dump-handler`, `apport`, and `iproute2`, then ran
autoremove, apt clean, journal vacuum, and proxy health checks. It exited 0.

Final state:

```text
apt-get -s upgrade: 0 upgraded
reboot-required: no
sing-box/nginx/ssh/fail2ban/cron: active
Xray: inactive and disabled
sing-box config: valid, ipv4-only-count=1
nginx config: successful
disk: 4.4G/20G (23%)
```

## Local verification

The fixed order was executed:

```text
build -> pytest -> unittest -> scripts/run_gates.ps1
```

The full gate included real SSH integration for `zz` only:

```text
94 passed, 20 subtests passed
pip check: no broken requirements
pip-audit: no known vulnerabilities
Bandit/Vulture/Ruff: passed
mypy: success
pyright: 0 errors, 0 warnings
```

## Rollback

- Restore the remote wrapper, root crontab, and configuration from
  `/root/vps-ssh-launcher-zz-before-ipv4-wrapper-20260710T092553Z`.
- Alternatively restore only
  `config.json.pre-ipv4-only.20260710T092615Z`, validate it, and restart
  sing-box.
- Local rollback: revert the sing-box wrapper changes in
  `scripts/vasma_kernel_update_cron.ps1`, its tests, and the README update.
- Package rollback: consult `/var/log/apt/history.log` and downgrade only a
  specifically identified faulty package. Do not bulk downgrade.
- Connectivity recovery: use the provider console, validate the restored
  sing-box configuration, then restart sing-box and verify the expected
  listeners before changing any other host.
