# 2026-04-27 vasma wrapper manual run

## Scope

- Manually triggered the remote `vasma` wrapper scripts.
- Profiles:
  - `bwg`: `/etc/v2ray-agent/auto_update_xray.sh`.
  - `zz`: `/etc/v2ray-agent/auto_update_singbox.sh`.
- Purpose: verify the cron wrapper itself runs correctly through `vasma`.

## Initial Incorrect Trigger Shape

The first manual trigger chained wrapper execution and verification in one remote shell command.
Those verification commands included paths such as:

```text
/etc/v2ray-agent/xray/xray
/etc/v2ray-agent/sing-box/sing-box
```

v2ray-agent/vasma uses broad `pgrep -f` checks while stopping the service, so the parent shell command was false-matched and both wrappers returned non-zero.

Recovery:

- `bwg`: Xray was already active by the time of follow-up verification.
- `zz`: ran `systemctl start sing-box`; service recovered and config check passed.

Code hardening:

- Added wrapper `trap` recovery in `scripts/vasma_kernel_update_cron.ps1`.
- On failure, the wrapper checks whether the target service is inactive and tries `systemctl start <service>` before exiting.
- Re-applied wrappers to both hosts.

## Correct Manual Trigger Shape

The valid manual trigger is the same shape as cron: run only the wrapper in that remote command, then verify in a separate command.

### bwg

Command:

```powershell
.\run.cmd -Profile bwg -Command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh"
```

Result:

- Exit code: `0`.
- Log tail:
  - `Xray关闭成功`.
  - `Xray启动成功`.
  - `vasma Xray-core update done`.
- Service verification:
  - `xray=active`.
  - `nginx=active`.
  - Xray config test: `config-ok`.

### zz

Command:

```powershell
.\run.cmd -Profile zz -Command "/bin/bash /etc/v2ray-agent/auto_update_singbox.sh"
```

Result:

- Exit code: `0`.
- Log tail:
  - `sing-box关闭成功`.
  - `sing-box启动成功`.
  - `vasma sing-box update done`.
- Service verification:
  - `sing-box=active`.
  - sing-box config check: `config-ok`.

## Port Verification

- `bwg`:
  - `29712`: `TcpTestSucceeded=True`.
  - `443`: `TcpTestSucceeded=True`.
  - `15374`: `TcpTestSucceeded=True`.
  - `22835`: `TcpTestSucceeded=True`.
  - `34546`: `TcpTestSucceeded=True`.
- `zz`:
  - `31854`: `TcpTestSucceeded=True`.
  - `22752`: `TcpTestSucceeded=True`.

## Conclusion

- The wrapper scripts run normally when triggered in the same shape as cron.
- Do not chain post-run verification commands containing `xray/xray` or `sing-box/sing-box` in the same remote shell command as `vasma`; run verification as a second SSH command.
