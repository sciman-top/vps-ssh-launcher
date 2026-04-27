# 2026-04-27 bwg maintenance fix

## Scope

- Profile: `bwg` only.
- Excluded: `zz` was not connected, checked, or modified.
- Excluded as non-issue: `sing-box` is not required on `bwg`.
- Risk level: medium remote package maintenance.

## Change

- Upgraded `linux-firmware` on `bwg`:
  - from `20240318.git3b128b60-0ubuntu2.26`
  - to `20240318.git3b128b60-0ubuntu2.27`
- No `google_ipv4_routing.ps1 -Apply`.
- No `auto_install.py --execute`.
- No firewall enablement or SSH/networking policy change.

## Commands

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
.\.venv\Scripts\python.exe .\ssh_tool.py --config $config --profile bwg run --command '<apt update and linux-firmware upgrade>'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg
.\.venv\Scripts\python.exe .\ssh_tool.py --config $config --profile bwg run --command '<post-upgrade service/config/package verification>'
Test-NetConnection -ComputerName 144.34.229.116 -Port 29712
Test-NetConnection -ComputerName 144.34.229.116 -Port 443
Test-NetConnection -ComputerName 144.34.229.116 -Port 15374
Test-NetConnection -ComputerName 144.34.229.116 -Port 22835
Test-NetConnection -ComputerName 144.34.229.116 -Port 34546
```

## Evidence

- `apt-get update`: success.
- `apt-get install -y linux-firmware`: success.
- `apt list --upgradable`: no package entries after upgrade.
- `reboot-required`: `reboot-not-required`.
- `systemctl --failed --no-legend`: no failed units.
- Services after upgrade:
  - `ssh=active`
  - `networking=active`
  - `systemd-networkd=active`
  - `xray=active`
  - `nginx=active`
  - `cron=active`
  - `fail2ban=active`
- Xray config test: `config-ok`.
- nginx config test: successful.
- Google/Gemini IPv4 routing:
  - drop-in present.
  - apply and reapply scripts present.
  - `google_ipv4_out` present.
  - `ForceIPv4` present.
  - public IPv4 egress: `144.34.229.116`.
  - public IPv6 egress: `2607:8700:5500:50c7::2`.
- TCP checks from local host to `144.34.229.116`:
  - `29712`: `TcpTestSucceeded=True`.
  - `443`: `TcpTestSucceeded=True`.
  - `15374`: `TcpTestSucceeded=True`.
  - `22835`: `TcpTestSucceeded=True`.
  - `34546`: `TcpTestSucceeded=True`.
- Recent warning-or-higher journal after upgrade: no entries.

## Rollback

- Package rollback, if needed:

```bash
apt-get install linux-firmware=20240318.git3b128b60-0ubuntu2.26
update-initramfs -u
```

- Recheck after rollback:

```bash
systemctl --failed --no-legend
systemctl is-active xray nginx ssh networking
/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf
nginx -t
```
