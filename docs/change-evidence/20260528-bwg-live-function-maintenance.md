# bwg live function and maintenance evidence

- Date: 2026-05-28 Asia/Shanghai (`2026-05-28` UTC on the VPS)
- Target: `bwg` profile, host `144.34.229.116`, SSH port `29712`
- Scope: local project gates, launcher wrappers, live SSH check/run/integration, `RunAll` with a single-profile temporary config, Xray weekly `vasma` wrapper, Google IPv4 read-only check, and real system maintenance.
- Excluded: `zz` was not checked or modified in this run because the task explicitly said not to touch the `zz` VPS.
- Risk: medium. The run wrote/regenerated `/etc/v2ray-agent/auto_update_xray.sh`, manually triggered that wrapper, and ran `/etc/v2ray-agent/auto_system_maint.sh` on `bwg`.
- Reboot policy: real maintenance was run with `AUTO_REBOOT_ON_MAINT=0`; no reboot was required.
- Rollback: restore local files from git if needed. On `bwg`, remove or regenerate `/etc/v2ray-agent/auto_update_xray.sh` from a prior commit, restore crontab from root history/backup if needed, and use apt package history for package-level rollback.

## Local gates

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

Result:

```text
pytest: 82 passed, 1 skipped, 22 subtests passed
unittest: OK (skipped=1)
powershell-policy: pass, violation_count=0
pip check: No broken requirements found
pip-audit: No known vulnerabilities found
bandit: pass
vulture: pass
ruff check: All checks passed
ruff format --check: 6 files already formatted
mypy: Success
pyright: 0 errors, 0 warnings
```

## Live launcher checks

```text
python .\ssh_tool.py --config %APPDATA%\vps-ssh-launcher\target.json --profile bwg check
.\run.cmd -Profile bwg -Command "printf launcher-wrapper-ok"
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -RunIntegration -IntegrationProfile bwg -IntegrationCommand "printf vps-ssh-launcher-bwg-integration" -IntegrationExpected "vps-ssh-launcher-bwg-integration"
```

Result:

```text
check: OK - root@144.34.229.116:29712
run.cmd: launcher-wrapper-ok
integration gates: 83 passed, 22 subtests passed
```

`RunAll` was tested without touching `zz` by writing a temporary config containing only the `bwg` profile and deleting it immediately after the run.

```text
[bwg] runall-bwg-ok
[summary] profiles=1 ok=1 failed=0
[summary] max_exit_code: 0
[summary] exit_code_histogram: 0=1
```

`connect.cmd -Profile bwg` also returned:

```text
OK - root@144.34.229.116:29712
```

## Xray and vasma wrapper

Initial read-only state:

```text
/usr/bin/vasma -> /etc/v2ray-agent/install.sh
xray: active, enabled
Xray 26.3.27
xray-config-test-exit=0
sing-box: inactive
/etc/v2ray-agent/auto_update_singbox.sh: absent
cron:
0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
```

Applied the repo wrapper generator for `bwg`/Xray only:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
```

Result:

```text
selected-kernel: xray
/etc/v2ray-agent/auto_update_xray.sh exists and passed bash -n
/etc/v2ray-agent/auto_update_singbox.sh missing
cron: 20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh
```

Manual wrapper trigger:

```text
python .\ssh_tool.py --config %APPDATA%\vps-ssh-launcher\target.json --profile bwg run --command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh" --command-timeout 1800
```

Result:

```text
wrapper exit: 0
current Xray version v26.3.27 equals vasma-visible latest; skip reinstall
xray service: active
xray config test: Configuration OK
listeners include :443, :15374, :34546 and 127.0.0.1:45987
```

## System maintenance

Dry-run check:

```text
DRY_RUN=1 AUTO_REBOOT_ON_MAINT=0 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
dry-run-exit=0
```

Real run:

```text
AUTO_REBOOT_ON_MAINT=0 /bin/bash /etc/v2ray-agent/auto_system_maint.sh
```

Packages upgraded:

```text
libgcrypt20: 1.10.3-2ubuntu0.1
distro-info-data: 0.60ubuntu0.6
iproute2: 6.1.0-1ubuntu6.3
nginx: 1.31.1-1~noble
snapd: 2.75.2+ubuntu24.04
```

Maintenance also ran `apt autoremove --purge`, `apt clean`, and `journalctl --vacuum-size=50M --vacuum-time=7d`.

Post-maintenance verification:

```text
xray=active
nginx=active
ssh=active
fail2ban=active
systemd-resolved=active
systemd-networkd=active
xray-config-test-exit=0
nginx -t: successful
/var/run/reboot-required: no
apt-get -s upgrade: 0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded
```

The maintenance log recorded:

```text
Step 1: apt update
Step 2: apt upgrade
Step 3: apt autoremove --purge
Step 4: apt clean
Step 5: journal vacuum
Step 6: proxy service health checks
OK: xray is active
INFO: sing-box enabled_state=disabled, skip
Step 7: reboot policy
No reboot required
Disk: 3.5G/40G (10%)
System maintenance done
```

## Other function checks

Google IPv4 read-only check:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg
```

Result:

```text
xray service: active
drop-in: ExecStartPre=/bin/bash /etc/v2ray-agent/apply-google-ipv4-routing-config.sh
google_ipv4_out and ForceIPv4 present
xray-config-test: config-ok
public IPv4: 144.34.229.116
public IPv6: 2607:8700:5500:50c7::2
```

`auto_install.py` safety default:

```text
python .\auto_install.py
auto_install_default_exit=2
```

The default path refused to drive `/etc/v2ray-agent/install.sh` without `--execute`, as expected.
