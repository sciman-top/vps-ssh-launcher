# 2026-04-27 bwg vasma force Xray update

## Scope

- Host/profile: `bwg` only.
- Entry point: `run.cmd`.
- Required update path: remote `vasma` / v2ray-agent menu.
- Excluded: `zz` was not connected, checked, or modified.
- Excluded: no manual Xray download-and-replace path was used for the successful run.

## Precheck

- `xray`: `active`.
- `nginx`: `active`.
- Xray config test: `config-ok`.
- Xray before: `26.3.27`.
- Xray sha before: `8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed`.
- No residual manual replacement process or temp directory was present.
- `vasma` path:

```text
/usr/bin/vasma -> /etc/v2ray-agent/install.sh
```

## Failed Attempt And Recovery

The first `vasma` attempt was wrapped in the same remote shell command that also inspected `/etc/v2ray-agent/xray/xray`.
This caused v2ray-agent's `handleXray stop` check to false-match the parent shell command via `pgrep -f "xray/xray"`.

Observed output:

```text
xray关闭失败
请手动执行【ps -ef|grep -v grep|grep xray|awk '{print $2}'|xargs kill -9】
```

Impact:

- Xray binary sha did not change.
- `xray` was left `inactive`.

Recovery:

```powershell
.\run.cmd -Profile bwg -Command "systemctl start xray"
```

Recovery evidence:

- `systemctl start xray`: exit `0`.
- `xray`: `active`.
- Xray config test: `Configuration OK`.

## Successful vasma Run

Command:

```powershell
.\run.cmd -Profile bwg -Command "printf '16\n1\n1\ny\n' | /usr/bin/vasma; echo vasma_exit=$?"
```

Menu path:

- `16.core管理`
- `1.Xray-core`
- `1.升级Xray-core`
- Same-version prompt: `y`

Evidence:

```text
当前版本:v26.3.27
最新版本:v26.3.27
Xray关闭成功
Xray-core版本:v26.3.27
Xray关闭成功
Xray启动成功
vasma_exit=0
```

Conclusion: v2ray-agent/vasma downloaded and reinstalled the same Xray-core version successfully.

## Final Verification

- `xray`: `active`.
- `nginx`: `active`.
- Xray after: `26.3.27`.
- Xray sha after: `8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed`.
- Backup file:

```text
/etc/v2ray-agent/xray/xray.backup-before-vasma-mHS
```

- Note: the backup name is odd because the remote timestamp was passed through `run.cmd` / Windows batch expansion.
- Xray config test: `config-ok`.
- Google/Gemini IPv4 routing markers remain present:
  - `gemini.google.com`.
  - `google_ipv4_out`.
  - `ForceIPv4`.
- Recent warning-or-higher Xray journal after successful run: no entries.
- Local TCP checks to `144.34.229.116`:
  - `29712`: `TcpTestSucceeded=True`.
  - `443`: `TcpTestSucceeded=True`.
  - `15374`: `TcpTestSucceeded=True`.
  - `22835`: `TcpTestSucceeded=True`.
  - `34546`: `TcpTestSucceeded=True`.
- Full repo gate with real `bwg` SSH integration:
  - `55 passed, 6 subtests passed`.
  - `pip check`: no broken requirements.
  - `pip-audit`: no known vulnerabilities.
  - `ruff`, `mypy`, `pyright`: all passed.

## Result

- Forced same-version Xray-core replacement through `vasma`: passed.
- No service regression found.
- No port regression found.
- No config regression found.
- No Google/Gemini IPv4 routing regression found.

## Rollback

Rollback was not needed.

If rollback is ever needed:

```bash
cp -f /etc/v2ray-agent/xray/xray.backup-before-vasma-mHS /etc/v2ray-agent/xray/xray
chmod 755 /etc/v2ray-agent/xray/xray
systemctl restart xray
/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf
```
