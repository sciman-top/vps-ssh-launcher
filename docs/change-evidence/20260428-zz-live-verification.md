# 2026-04-28 zz live verification

## Scope

- Rule IDs: R2, R4, R6, R8, E4, E5.
- Risk level: high for remote service/kernel/maintenance actions; executed sequentially on a single VPS.
- Target profile: `zz` only.
- Explicit exclusion: `bwg` was not targeted in this run.
- Credentials: no `target.json` content, passwords, private keys, or remote secrets are recorded here.

## Commands and Evidence

### Baseline SSH and service state

Command:

```powershell
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz check
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "<service baseline>" --command-timeout 120
```

Key output:

```text
OK - root@38.244.39.84:31854
hostname: C202604071640752
sing-box: active
xray: inactive
systemctl --failed: 0 loaded units listed
```

### vasma sing-box wrapper and manual kernel update

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "/bin/bash /etc/v2ray-agent/auto_update_singbox.sh" --command-timeout 900
```

Key output:

```text
/usr/bin/vasma -> /etc/v2ray-agent/install.sh
selected kernel: sing-box
cron: 0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_singbox.sh
/etc/v2ray-agent/auto_update_singbox.sh exists, executable
bash -n: ok
```

Post-run verification command:

```powershell
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "<sing-box post-update verification>" --command-timeout 180
```

Key output:

```text
systemctl is-active sing-box: active
sing-box version 1.13.11
sing-box check: config-ok
listeners: udp/*:443, tcp/*:22752, sshd/*:31854
systemctl --failed: 0 loaded units listed
```

### System upgrade, optimization, and cleanup

Command:

```powershell
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "<heartbeat wrapped AUTO_REBOOT_ON_MAINT=1 /etc/v2ray-agent/auto_system_maint.sh>" --command-timeout 1200
```

Key output:

```text
maint-running pid=115243 2026-04-27 16:09:59
maint-exit=0
Vacuuming done, freed 0B of archived journals from /run/log/journal.
Vacuuming done, freed 0B of archived journals from /var/log/journal.
```

Post-run verification command:

```powershell
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "<maintenance post-verification>" --command-timeout 300
```

Key output:

```text
dry_run=0
Step 1: apt update
Step 2: apt upgrade
Step 3: apt autoremove --purge
Step 4: apt clean
Step 5: journal vacuum
INFO: xray enabled_state=disabled, skip
OK: sing-box is active
No reboot required
apt-get check: exit 0
sing-box: active
sing-box check: config-ok
systemctl --failed: 0 loaded units listed
reboot: no-reboot-required
disk: /dev/vda1 20G 4.1G 16G 22% /
```

### Google IPv4 routing script

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile zz
```

Key output:

```text
xray-version: xray-missing
service: inactive
google-ipv4-dropin: missing
xray-config-test: xray-missing
public-egress: 38.244.39.84
```

N/A:

- `gate_na`: `-Apply` was not executed for `zz` because this script mutates the Xray routing path, while `zz` is the sing-box target and Xray is absent/inactive.
- Alternative verification: read-only detection above confirmed the Xray path is not applicable to `zz`.
- Evidence link: this file.
- Expires at: when the script supports a sing-box equivalent apply path or `zz` changes back to Xray.

### auto_install.py remote guard

Command:

```powershell
.\.venv\Scripts\python.exe .\ssh_tool.py --profile zz run --command "<copy auto_install.py to /tmp and run guard checks>" --command-timeout 180
```

Key output:

```text
/etc/v2ray-agent/install.sh exists
/usr/bin/vasma -> /etc/v2ray-agent/install.sh
guard-default: default_exit=2
guard-invalid-timeout: invalid_timeout_exit=2
help_exit=0
```

N/A:

- `gate_na`: `auto_install.py --execute` was not run because it drives `/etc/v2ray-agent/install.sh` and can rewrite live VPS proxy config.
- Alternative verification: remote guard/default/help/invalid-timeout checks proved the safety behavior without mutating the service.
- Evidence link: this file.
- Expires at: next planned reinstall or when a fresh backup and restore plan is explicitly approved.

### Full local gate with real zz SSH integration

Command:

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 `
  -RunIntegration `
  -IntegrationConfig $config `
  -IntegrationProfile zz `
  -IntegrationCommand 'printf vps-ssh-launcher-zz-real-ssh-20260428' `
  -IntegrationExpected 'vps-ssh-launcher-zz-real-ssh-20260428'
```

Key output:

```text
build: exit 0
pytest: 68 passed, 15 subtests passed
unittest: Ran 68 tests ... OK
powershell-policy: status pass, violation_count 0
pip check: No broken requirements found.
pip-audit: No known vulnerabilities found
ruff: All checks passed!
ruff format: 6 files already formatted
mypy: Success: no issues found in 6 source files
pyright: 0 errors, 0 warnings, 0 informations
```

Additional explicit integration proof:

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION='1'
$env:VPS_SSH_LAUNCHER_INTEGRATION_PROFILE='zz'
.\.venv\Scripts\python.exe -m unittest -v test_integration_real_ssh.RealSSHIntegrationTests.test_real_ssh_command_round_trip
```

Key output:

```text
test_real_ssh_command_round_trip ... ok
Ran 1 test in 3.530s
OK
```

## Rollback

- Code rollback: use git history for repository changes.
- sing-box cron/wrapper rollback: remove or edit `/etc/v2ray-agent/auto_update_singbox.sh` and its crontab entry, then rerun service/config verification.
- sing-box service rollback: use `vasma` core management or restore `/etc/v2ray-agent/sing-box/conf/config.json` from a known-good backup, then run `sing-box check` and `systemctl restart sing-box`.
- System maintenance rollback: no reboot was performed and no dpkg/apt error remains; if package-level rollback becomes necessary, use apt history/dpkg logs on the VPS with a package-specific restore plan.

