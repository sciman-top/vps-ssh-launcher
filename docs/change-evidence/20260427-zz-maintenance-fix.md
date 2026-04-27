# 2026-04-27 zz maintenance fix

## Scope

- Profile: `zz` only.
- Excluded: `bwg` was not connected, checked, or modified.
- Excluded as non-issue: `xray` is not required on `zz`.
- Risk level: medium remote package maintenance and systemd service reload fix.

## Findings

- `sing-box` was `active` and `enabled`.
- `sing-box` binary is managed by v2ray-agent at `/etc/v2ray-agent/sing-box/sing-box`; absence from the default shell `PATH` is not a service defect.
- `sing-box.service` had a malformed reload command:

```ini
ExecReload=/bin/kill -HUP 
```

- Packages pending upgrade:
  - `linux-firmware`: `20240318.git3b128b60-0ubuntu2.26` -> `20240318.git3b128b60-0ubuntu2.27`
  - `ubuntu-pro-client`: `37.1ubuntu0~24.04` -> `37.2ubuntu~24.04`
  - `ubuntu-pro-client-l10n`: `37.1ubuntu0~24.04` -> `37.2ubuntu~24.04`

## Changes

- Upgraded the three pending packages.
- Added systemd drop-in:

```ini
[Service]
ExecReload=
ExecReload=/bin/kill -HUP $MAINPID
```

- Ran `systemctl daemon-reload`.
- Ran `systemctl reload sing-box` to verify the reload path.
- No `xray` install, repair, or service change.
- No `auto_install.py --execute`.

## Commands

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
.\.venv\Scripts\python.exe .\ssh_tool.py --config $config --profile zz run --command '<zz read-only health check>'
.\.venv\Scripts\python.exe .\ssh_tool.py --config $config --profile zz run --command '<apt upgrade and sing-box ExecReload drop-in>'
.\.venv\Scripts\python.exe .\ssh_tool.py --config $config --profile zz run --command '<reload and post-fix verification>'
Test-NetConnection -ComputerName 38.244.39.84 -Port 31854
Test-NetConnection -ComputerName 38.244.39.84 -Port 22752
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile zz -IntegrationCommand 'printf vps-ssh-launcher-zz-after-fix-20260427' -IntegrationExpected 'vps-ssh-launcher-zz-after-fix-20260427'
```

## Evidence

- `apt-get update`: success.
- `apt-get install -y linux-firmware ubuntu-pro-client ubuntu-pro-client-l10n`: success.
- Post-upgrade versions:
  - `linux-firmware=20240318.git3b128b60-0ubuntu2.27`
  - `ubuntu-pro-client=37.2ubuntu~24.04`
  - `ubuntu-pro-client-l10n=37.2ubuntu~24.04`
- `apt list --upgradable`: no package entries after upgrade.
- `reboot-required`: `reboot-not-required`.
- `systemctl reload sing-box`: success.
- Services after fix:
  - `ssh=active`
  - `sshd=active`
  - `systemd-networkd=active`
  - `sing-box=active`
  - `cron=active`
  - `fail2ban=active`
- `systemctl --failed --no-legend`: no failed units.
- `sing-box version`: `1.13.11`.
- `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json`: exit `0`.
- Listeners:
  - TCP `*:22752` by `sing-box`.
  - UDP `*:443` by `sing-box`.
  - TCP `0.0.0.0:31854` by `sshd`.
- TCP checks from local host to `38.244.39.84`:
  - `31854`: `TcpTestSucceeded=True`.
  - `22752`: `TcpTestSucceeded=True`.
- Gate result with `zz` real SSH integration:
  - `55 passed, 6 subtests passed`.
  - `pip check`: no broken requirements.
  - `pip-audit`: no known vulnerabilities.
  - `ruff`, `mypy`, `pyright`: all passed.
- Recent `sing-box` log included one external invalid REALITY handshake, treated as internet scan/noise rather than a service defect.

## Rollback

- Package rollback, if needed:

```bash
apt-get install linux-firmware=20240318.git3b128b60-0ubuntu2.26 ubuntu-pro-client=37.1ubuntu0~24.04 ubuntu-pro-client-l10n=37.1ubuntu0~24.04
update-initramfs -u
```

- Reload drop-in rollback:

```bash
rm -f /etc/systemd/system/sing-box.service.d/10-reload-mainpid.conf
systemctl daemon-reload
systemctl restart sing-box
```

- Recheck after rollback:

```bash
systemctl --failed --no-legend
systemctl is-active sing-box ssh sshd systemd-networkd
/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json
```
