# 2026-05-28 zz sing-box and system maintenance

## Scope

- Repo: `D:\CODE\vps-ssh-launcher`.
- Target profile: `zz` only.
- Explicit non-target: `bwg` was not checked, updated, or otherwise touched.
- Live config source: `%APPDATA%\vps-ssh-launcher\target.json`.
- Risk level: high for remote service maintenance, constrained to one VPS and verified after each disruptive step.

## Local Verification

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_gates.ps1
```

Result:

- exit code: `0`.
- pytest: `82 passed, 1 skipped, 22 subtests passed`.
- unittest: `83 tests OK (skipped=1)`.
- `pip check`: no broken requirements.
- `pip-audit`: no known vulnerabilities found.
- Bandit, Vulture, Ruff, format check, Mypy, Pyright: passed.

Real SSH checks scoped to `zz`:

```powershell
python .\ssh_tool.py --config "%APPDATA%\vps-ssh-launcher\target.json" --profile zz check
.\run.cmd -Profile zz -Command "printf launcher-wrapper-ok"
```

Result:

- Direct `ssh_tool.py check`: success.
- Wrapper chain `run.cmd -> connect.cmd -> connect.ps1 -> ssh_tool.py`: printed `launcher-wrapper-ok`.

Explicit integration test:

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION='1'
$env:VPS_SSH_LAUNCHER_INTEGRATION_CONFIG="$env:APPDATA\vps-ssh-launcher\target.json"
$env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE='zz'
$env:VPS_SSH_LAUNCHER_INTEGRATION_COMMAND='printf vps-ssh-launcher-integration'
$env:VPS_SSH_LAUNCHER_INTEGRATION_EXPECTED='vps-ssh-launcher-integration'
.\.venv\Scripts\python.exe -m pytest -q test_integration_real_ssh.py
```

Result: `3 passed`.

## Pre-Maintenance Remote State

Read-only baseline on `zz`:

- `sing-box`: `active`, `enabled`.
- `xray`: `inactive`, `disabled`.
- `nginx`: `active`, `enabled`.
- `cron`, `ssh`, `sshd`: active.
- `sing-box version`: `1.13.12`.
- `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json`: OK.
- sing-box route JSON contained `resolve + ipv4_only` once.
- root crontab contained:
  - `0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 /bin/bash /etc/v2ray-agent/auto_system_maint.sh`
  - `20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh`
- `/etc/v2ray-agent/auto_update_xray.sh`: missing, as expected for `zz`.
- `/etc/v2ray-agent/auto_update_singbox.sh`: present and `bash -n` OK.
- listeners:
  - UDP `*:443` by `sing-box`.
  - TCP `0.0.0.0:35622` by `nginx`.
  - TCP `31854` by `sshd`.

## sing-box vasma Maintenance

Configured and verified the host-specific weekly wrapper:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
```

Result:

- selected kernel: `sing-box`.
- cron: `20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh`.
- `/etc/v2ray-agent/auto_update_xray.sh`: missing.
- `/etc/v2ray-agent/auto_update_singbox.sh`: present, executable, `bash -n` OK.
- wrapper uses the remote `vasma` menu path:
  - `16.core管理 -> 2.sing-box -> 1.升级 sing-box`
  - `printf '16\n2\n1\ny\n' | /usr/bin/vasma`

Manual trigger used a single remote command, per project rule:

```powershell
python .\ssh_tool.py --config "%APPDATA%\vps-ssh-launcher\target.json" --profile zz run --command "/bin/bash /etc/v2ray-agent/auto_update_singbox.sh" --command-timeout 0
```

Post-trigger result:

- wrapper exit code: `0`.
- `sing-box version`: `1.13.12`.
- log showed current `v1.13.12` equals vasma-visible latest and reinstall was skipped.
- `sing-box`: `active`, `enabled`.
- `sing-box check`: OK.
- expected cron entries stayed present.
- key listeners stayed present on `443/udp`, `35622/tcp`, and `31854/tcp`.

## System Upgrade / Optimization / Cleanup

Inspected `/etc/v2ray-agent/auto_system_maint.sh` before execution:

- script sha256: `e06dd91484f85f138a61dd386b697741dc5ab97583023000788cc7efd4a96dd5`.
- `bash -n`: OK.
- steps: `apt update`, `apt upgrade --with-new-pkgs`, `apt autoremove --purge`, `apt clean`, `journalctl --vacuum-size=50M --vacuum-time=7d`, proxy service health checks.

Actual run:

```powershell
python .\ssh_tool.py --config "%APPDATA%\vps-ssh-launcher\target.json" --profile zz run --command "<AUTO_REBOOT_ON_MAINT=0 /bin/bash /etc/v2ray-agent/auto_system_maint.sh; tail log>" --command-timeout 0
```

`AUTO_REBOOT_ON_MAINT=0` was used for this interactive run to avoid an unrequested immediate remote reboot. The persistent monthly cron still keeps its original `AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15` policy.

Result:

- exit code: `0`.
- upgraded packages observed in command output:
  - `iproute2` -> `6.1.0-1ubuntu6.3`
  - `open-vm-tools` -> `2:13.0.0-2~ubuntu0.24.04.1`
  - `distro-info-data` -> `0.60ubuntu0.6`
  - `nginx` -> `1.31.1-1~noble`
  - `snapd` -> `2.75.2+ubuntu24.04`
- `apt autoremove --purge`, `apt clean`, and journal vacuum completed.
- script log reported `No reboot required`.
- disk after maintenance: `4.4G/20G (23%)` on `/`.

## Post-Maintenance Verification

Command shape:

```powershell
python .\ssh_tool.py --config "%APPDATA%\vps-ssh-launcher\target.json" --profile zz run --command "<post-maint health probe>" --command-timeout 180
```

Result:

- `apt list --upgradable`: no packages listed after `Listing...`.
- `systemctl --failed`: `0 loaded units listed`.
- `sing-box`: active/enabled.
- `nginx`: active/enabled.
- `ssh`/`sshd`: active.
- `docker` and `containerd`: active/enabled.
- `unattended-upgrades`: active/enabled.
- `sing-box check`: OK.
- `nginx -t`: successful.
- reboot required: `no`.
- auto-maint reboot flag: `no`.
- listeners stayed present on `443/udp`, `35622/tcp`, and `31854/tcp`.

Residual operational note:

- `needrestart -b` still lists these restart candidates: `dbus.service`, `docker.service`, `systemd-logind.service`, `unattended-upgrades.service`.
- They were not restarted in this run because restarting Docker or login/session infrastructure can interrupt unrelated workloads; all listed services remained active, and no reboot was required.

## Rollback / Recovery

- sing-box wrapper/cron: rerun `.\scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply` to restore the intended `zz` shape, or remove the sing-box cron line and wrapper manually if rollback requires disabling the weekly update.
- system packages: use apt history / package cache policy for package-specific rollback. Current verified package versions are recorded above.
- service recovery checks:

```bash
systemctl is-active sing-box nginx ssh docker containerd
/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json
nginx -t
ss -lntup | grep -E ':(443|31854|35622)\b'
```

