# 2026-04-27 functional live verification

- rules: R1/R2/R6/R8, E4/E5
- risk: medium; live SSH checks were read-only command/connectivity checks, no remote apply or destructive maintenance command was run.
- landing: current repo verification -> hard gate and live launcher behavior -> evidence file plus formatting gate fix.
- rollback: revert this evidence file and `.governed-ai/verify-powershell-policy.py` formatting change from git history if needed.

## Commands and Evidence

- `.\scripts\run_gates.ps1`
  - initial result: failed at `lint:format`
  - key output: `.governed-ai\verify-powershell-policy.py` would be reformatted
  - earlier gates passed before failure: build, pytest, unittest, pip check, pip-audit, bandit, vulture, ruff check
- `.\.venv\Scripts\python.exe -m ruff format .governed-ai\verify-powershell-policy.py`
  - result: `1 file reformatted`
- `.\scripts\run_gates.ps1`
  - result: pass
  - key output: `51 passed, 1 skipped, 6 subtests passed`; `No broken requirements found`; `No known vulnerabilities found`; `All checks passed`; `Success: no issues found`; `0 errors, 0 warnings, 0 informations`
- `.\run.cmd -Profile bwg -Command "printf vps-ssh-launcher-live-bwg"`
  - result: pass, exit 0
- `.\run.cmd -Profile zz -Command "printf vps-ssh-launcher-live-zz"`
  - result: pass, exit 0
- `.\connect.cmd -Profile bwg`
  - result: pass, exit 0
- `.\connect.cmd -Profile zz`
  - result: pass, exit 0
- `.\connect.cmd -Command "printf vps-ssh-launcher-runall" -RunAll`
  - result: pass, `profiles=2 ok=2 failed=0`
- `.\run.cmd -Profile bwg -Command "exit 7"`
  - result: pass, returned remote exit code 7
- `.\.venv\Scripts\python.exe .\ssh_tool.py --config .\does-not-exist.json check`
  - result: expected config failure, exit 2
- `.\.venv\Scripts\python.exe .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile __missing__ check`
  - result: expected unknown-profile failure, exit 2
- `.\connect.cmd -Config <temp>\target.json`
  - result: pass, created template config and exited 0; temp directory removed after verification
- `.\.venv\Scripts\python.exe .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" check`
  - result: expected noninteractive cancellation path, exit 2, because local config has multiple profiles and no default
- `python .\auto_install.py --help`
  - result: expected local guard failure, exit 1, `install.sh not found`; full auto install was not run because it is a remote installer path.
- `python .\ssh_tool.py --help`
  - result: pass, CLI help rendered.
- `pytest -q test_integration_real_ssh.py` with `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1`, profile `bwg`
  - result: pass, `1 passed`
- `pytest -q test_integration_real_ssh.py` with `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1`, profile `zz`
  - result: pass, `1 passed`
- `.\scripts\run_gates.ps1 -RunIntegration -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json"`
  - result: expected block, requires `-IntegrationProfile` when config has multiple profiles and no default.
- `.\scripts\run_gates.ps1 -RunIntegration -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" -IntegrationProfile bwg -IntegrationCommand "printf vps-ssh-launcher-gated-integration" -IntegrationExpected "vps-ssh-launcher-gated-integration"`
  - result: pass
  - key output: `52 passed, 6 subtests passed`; `OK`; dependency/security/lint/type gates passed.
- `.\scripts\google_ipv4_routing.ps1 -Profile bwg`
  - result: pass, read-only remote check; Xray active, Google IPv4 routing files present, Xray config test ok.
- `.\scripts\google_ipv4_routing.ps1 -Profile zz`
  - result: pass, read-only remote check; Xray not present/inactive on this profile, script handled the non-Xray host without failing.

## Not Run

- `scripts/google_ipv4_routing.ps1 -Apply` was not run. It is an explicit remote mutation path and was not needed for read-only effectiveness verification.
- Full `auto_install.py` remote install was not run. It launches `/etc/v2ray-agent/install.sh` and can change remote service state.

## High-Risk Path Follow-Up

- `.\scripts\google_ipv4_routing.ps1 -Profile bwg -Apply`
  - result: pass
  - key output: `google-ipv4-routing already current`, `Configuration OK`, `active`, read-only follow-up showed Google/Gemini routing and `ForceIPv4` still present.
- `.\scripts\google_ipv4_routing.ps1 -Profile zz -Apply`
  - result: expected remote guard failure
  - key output: `missing executable apply script: /etc/v2ray-agent/reapply-google-ipv4-routing.sh`
  - note: stopped touching `zz` after user explicitly requested not to operate on it.
- `auto_install.py` remote execution attempt on `bwg`
  - pre-snapshot: `/root/vps-ssh-launcher-backup-20260427122344/v2ray-agent.tgz`
  - dependency fix: installed `python3-pexpect` on `bwg`
  - result: unsafe for unattended execution in current form; first synchronous run hit command idle timeout while the remote installer continued, and background run reached an uncovered prompt: `读取到上次安装的配置，是否使用 ？[y/n]:`
  - impact: installer rewrote Xray config files around `2026-04-27T12:29:52Z`, changing active listener ports and breaking the expected Reality/XHTTP/VLESS entrypoints.
  - recovery: saved bad state to `/root/vps-ssh-launcher-bad-after-autoinstall-20260427123708.tgz`, restored `/etc/v2ray-agent` from `/root/vps-ssh-launcher-backup-20260427122344/v2ray-agent.tgz`, restarted `xray`.
  - verification after recovery: `systemctl is-active xray` returned `active`; `/etc/v2ray-agent/xray/xray run -test -confdir /etc/v2ray-agent/xray/conf` returned `Configuration OK`; listeners included `*:443`, `*:15374`, `*:22835`; local `Test-NetConnection` to `144.34.229.116` on ports `443`, `15374`, and `22835` returned `TcpTestSucceeded=True`; Google IPv4 routing read-only check returned `config-ok`.
- `bwg` Reality/XHTTP follow-up after user reported `Reality_XHTTP` still failed
  - root cause: restored Xray config expected Reality target `fq.sciman.top:34546`, but `/etc/nginx/conf.d/subscribe.conf` was missing and nginx was not listening on `34546`; XHTTP self-test failed with `connection reset by peer`.
  - fix: backed up nginx config to `/root/nginx-conf-before-restore-subscribe-20260427124419.tgz`, recreated `/etc/nginx/conf.d/subscribe.conf` for `fq.sciman.top:34546` using existing `/etc/v2ray-agent/tls/fq.sciman.top.*`, and reloaded nginx.
  - subscription cleanup: backed up `/etc/v2ray-agent/subscribe_local` to `/root/subscribe-local-before-clean-20260427124531.tgz`, removed stale `872ac823-VLESS_Reality_XHTTP` port `17947` entries, and kept current port `443` entries.
  - verification: `xray` and `nginx` both `active`; `xray run -test` returned `Configuration OK`; listeners included `*:443`, `*:15374`, `*:22835`, and `*:34546`; local `Test-NetConnection` to `144.34.229.116` on ports `443`, `15374`, `22835`, and `34546` returned `TcpTestSucceeded=True`; temporary Xray client self-test over `VLESS Reality XHTTP` returned `HTTP/2 200` from `https://example.com`.
- repo hardening after incident
  - root cause controls added: `auto_install.py` now refuses to run unless `--execute` or `VPS_AUTO_INSTALL_EXECUTE=1` is supplied; `--expect-timeout` must be less than `60` seconds so unknown prompts abort before `ssh_tool.py` idle timeout; non-EOF prompt driving failures terminate the child installer process.
  - prompt coverage added for `读取到上次安装的配置，是否使用` and `读取到上次安装设置的Reality域名，是否使用`.
  - tests: `pytest -q test_auto_install.py` covered the explicit-execute guard, unsafe timeout rejection, and newly covered prompt.
- bwg-only high-risk guard re-verification after hardening
  - scope: `bwg` only; `zz` was not touched.
  - pre-check: `xray` and `nginx` were `active`; listeners included `*:443`, `*:15374`, `*:22835`, and `*:34546`; no stale `17947` subscription entry; no residual `auto_install.py` or `/etc/v2ray-agent/install.sh` process.
  - remote guard test: copied the current `auto_install.py` to `/tmp/vps_auto_install_guard_test.py` on `bwg`; running it without `--execute` returned `2` and did not start the installer; running it with `--execute --expect-timeout 60` returned `2` and did not start the installer.
  - idempotent apply test: `.\scripts\google_ipv4_routing.ps1 -Profile bwg -Apply` returned `google-ipv4-routing already current`, `Configuration OK`, and `active`.
  - post-check: `xray` and `nginx` remained `active`; `xray run -test` returned `Configuration OK`; listeners still included `*:443`, `*:15374`, `*:22835`, and `*:34546`; local `Test-NetConnection` to all four ports returned `TcpTestSucceeded=True`; temporary Xray client self-test over `VLESS Reality XHTTP` returned `HTTP/2 200`; no stale `17947` subscription entry; no residual installer process.
- zz detection-only run after user request
  - scope: detection only; no repair, no `auto_install.py --execute`, and no `google_ipv4_routing.ps1 -Apply`.
  - service state: SSH was reachable; `sing-box` was `active`; `xray` and `nginx` were `inactive`; no failed systemd units; no residual `auto_install.py` or `/etc/v2ray-agent/install.sh` process.
  - config state: `/etc/v2ray-agent/sing-box/sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json` returned exit `0`; active listeners included UDP `*:443` for Hysteria2 and TCP `*:22752` for VLESS Reality.
  - detected issue: `VLESSReality` uses Reality handshake target `zz.sciman.top:35622`, but both local and domain TCP checks to `35622` were closed; `sing-box` journal contained `REALITY: failed to dial dest ... :35622: connect: connection refused`.
  - subscription state: stale local subscription entries were detected, including old/impossible VLESS Reality port `1578422752` and duplicate entries; current VLESS Reality listener is `22752`.
  - guard test: copied current `auto_install.py` to `/tmp/vps_auto_install_guard_test.py`; default run returned `2`, `--execute --expect-timeout 60` returned `2`, and no installer process was started.

## Sensitive Data Boundary

- Verification used the user-local config at `%APPDATA%\vps-ssh-launcher\target.json`.
- Evidence intentionally omits passwords, private keys, and full local config contents.

## 2026-04-27T21:03+08:00 Current Recheck

- scope: `bwg` only for live SSH/apply/integration checks; `zz` was not contacted or modified in this recheck.
- `python .\auto_install.py`
  - result: expected guard failure, exit `2`; no installer was started.
  - key output: requires `--execute` because the script can rewrite live VPS proxy config.
- `python .\auto_install.py --execute --expect-timeout 60`
  - result: expected guard failure, exit `2`; no installer was started.
  - key output: `--expect-timeout` must be between `1` and `59`.
- `python .\auto_install.py --help`
  - result: pass, exit `0`; CLI usage rendered.
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1`
  - result: pass.
  - key output: `54 passed, 1 skipped, 6 subtests passed`; dependency/security/lint/type gates passed.
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg -Apply`
  - result: pass and idempotent.
  - key output: `google-ipv4-routing already current`; `active`; `config-ok`; routing still includes `google_ipv4_out` and `ForceIPv4`; public egress check returned IPv4 `144.34.229.116` and IPv6 `2607:8700:5500:50c7::2`.
- `.\run.cmd -Profile bwg -Command "printf vps-ssh-launcher-bwg-live-check"`
  - result: pass, expected marker printed.
- `python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg check`
  - result: pass, `OK - root@144.34.229.116:29712`.
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -RunIntegration -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" -IntegrationProfile bwg -IntegrationCommand "printf vps-ssh-launcher-bwg-integration-20260427" -IntegrationExpected "vps-ssh-launcher-bwg-integration-20260427"`
  - result: pass.
  - key output: `55 passed, 6 subtests passed`; dependency/security/lint/type gates passed.

## 2026-04-27T21:24+08:00 zz Detection Recheck

- scope: `zz` live detection only; no repair, no `google_ipv4_routing.ps1 -Apply`, and no `auto_install.py --execute`.
- `python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz check`
  - result: pass, `OK - root@38.244.39.84:31854`.
- `.\run.cmd -Profile zz -Command "printf vps-ssh-launcher-zz-live-check"`
  - result: pass, expected marker printed.
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile zz`
  - result: pass as read-only detection.
  - key output: `xray-missing`; `inactive`; Google IPv4 drop-in missing as expected for non-Xray host; public IPv4 egress returned `38.244.39.84`; IPv6 egress curl failed to connect.
- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -RunIntegration -IntegrationConfig "$env:APPDATA\vps-ssh-launcher\target.json" -IntegrationProfile zz -IntegrationCommand "printf vps-ssh-launcher-zz-integration-20260427" -IntegrationExpected "vps-ssh-launcher-zz-integration-20260427"`
  - result: pass.
  - key output: `55 passed, 6 subtests passed`; dependency/security/lint/type gates passed.
- `python .\ssh_tool.py --config "$env:APPDATA\vps-ssh-launcher\target.json" --profile zz run --command "<read-only service and listener check>"`
  - result: pass.
  - key output: `sing-box` was `active`; `xray` and `nginx` were `inactive`; no failed systemd units; sing-box config check returned exit `0`; listeners included UDP `*:443` and TCP `*:22752`.
- local port probes
  - result: TCP `38.244.39.84:22752` succeeded; TCP `38.244.39.84:35622` failed.
- sing-box recent journal
  - result: current Reality target problem reproduced.
  - key output: `REALITY: failed to dial dest: dial tcp 38.244.39.84:35622: connect: connection refused`.

## 2026-04-27T21:28+08:00 bwg Networking Failed-Unit Repair

- scope: `bwg` only; `zz` was not contacted or modified during this repair.
- pre-check
  - `xray` was `active`; `nginx` was `active`; `sing-box` was `inactive`.
  - Xray config test returned `config-ok`; `nginx -t` returned successful.
  - ports `144.34.229.116:443`, `15374`, `22835`, and `34546` were reachable.
  - `scripts\google_ipv4_routing.ps1 -Profile bwg` returned `config-ok`; routing still included `google_ipv4_out` and `ForceIPv4`.
  - detected issue: `systemctl --failed` showed `networking.service loaded failed failed Raise network interfaces`.
- root cause
  - `/etc/network/interfaces` contained `auto eth1` and `iface eth1 inet dhcp`.
  - `/sys/class/net/eth1` did not exist.
  - `ifquery --list --allow auto` listed `eth1`, and `ifup --no-act -a` would try `dhcpcd eth1`.
- repair
  - remote backup created: `/etc/network/interfaces.bak-vps-ssh-launcher-20260427-132756`.
  - removed only the stale `eth1` `auto`/`iface` lines from `/etc/network/interfaces`.
  - post-edit `ifquery --list --allow auto` listed only `lo` and `eth0`.
  - post-edit `ifup --no-act -a` no longer referenced `eth1`.
  - ran `systemctl reset-failed networking` and `systemctl start networking`.
- post-check
  - `networking` is `active`; `systemctl --failed --no-legend` returned no failed units.
  - `xray` and `nginx` remained `active`; Xray config test returned `config-ok`; `nginx -t` returned successful.
  - listeners remained on `*:443`, `*:15374`, `*:22835`, and `0.0.0.0:34546`.
  - ports `144.34.229.116:443`, `15374`, `22835`, and `34546` remained reachable.
  - `scripts\google_ipv4_routing.ps1 -Profile bwg` returned `config-ok`; public egress returned IPv4 `144.34.229.116` and IPv6 `2607:8700:5500:50c7::2`.
  - `run.cmd -Profile bwg -Command "printf bwg-after-networking-fix"` returned the expected marker.
  - `scripts\run_gates.ps1 -RunIntegration ... -IntegrationProfile bwg` passed with `55 passed, 6 subtests passed`; dependency/security/lint/type gates passed.

## 2026-04-27T21:32+08:00 zz Read-Only Issue Sweep

- scope: `zz` read-only inspection; no repair, no restart, no config edit.
- healthy checks
  - host uptime was about 14 days; disk `/` was 22% used; available memory was about 574 MiB plus 1.0 GiB swap.
  - `sing-box` was `active`; `xray`, `nginx`, and `networking` were `inactive`; `systemctl --failed --no-legend` returned no failed units.
  - sing-box config check returned exit `0`; sing-box version was `1.13.11`.
  - active listeners included UDP `*:443` for Hysteria2 and TCP `*:22752` for VLESS Reality; SSH listened on `31854`.
  - TLS certificate for `zz.sciman.top` was valid from `2026-04-16` to `2026-07-15`; daily TLS cron logs reported the certificate valid.
  - no residual `auto_install.py`, `install.sh`, `auto_update_singbox`, or `auto_system_maint` process was present.
- issue: VLESS Reality handshake target is closed
  - sing-box config has `VLESSReality` listening on `22752`, with Reality handshake target `zz.sciman.top:35622`.
  - DNS for `zz.sciman.top` resolves to `38.244.39.84`.
  - local and external TCP probes to `38.244.39.84:35622` failed, while `38.244.39.84:22752` succeeded.
  - recent sing-box journal repeated `REALITY: failed to dial dest: dial tcp 38.244.39.84:35622: connect: connection refused`.
- issue: IPv4-only host still attempts IPv6 direct egress
  - host has no global IPv6 address and no IPv6 default route.
  - recent sing-box journal repeatedly logged `network is unreachable` for IPv6-only attempts such as `ipv6.msftconnecttest.com` and `ipv6.msftncsi.com`.
  - current sing-box config summary did not show a DNS/outbound strategy forcing IPv4.
- issue: subscription content appears stale
  - subscription port summary still included entries other than the active listener ports `443` and `22752`, including `15784` and `19518`.
  - this needs cleanup only after confirming the intended public subscription set.
- low-priority observation
  - kernel log showed one `bash` segfault on `2026-04-27T12:34:37Z`; no OOM evidence and no repeated segfaults were found in the 7-day kernel log slice.

## 2026-04-27T21:36+08:00 zz Repair

- scope: `zz` only.
- backups
  - sing-box and nginx config backup: `/root/vps-ssh-launcher-zz-fix-20260427-133648`.
  - subscription backup: `/root/subscribe-local-before-zz-clean-20260427-133750.tgz`.
- Reality handshake target repair
  - root cause: `VLESSReality` listened on TCP `22752`, but its Reality handshake target was `zz.sciman.top:35622`; nginx was installed and enabled but inactive, and no process listened on `35622`.
  - repair: added `/etc/nginx/conf.d/reality-handshake.conf` with TLS for `zz.sciman.top` on TCP `35622`, using the existing `/etc/v2ray-agent/tls/zz.sciman.top.*` certificate/key.
  - validation: `nginx -t` succeeded; `nginx` is `active`; TCP `38.244.39.84:35622` is reachable; TCP `38.244.39.84:22752` remains reachable.
- IPv4-only sing-box strategy repair
  - root cause: host has no global IPv6/default IPv6 route, but sing-box config had no IPv4-only resolution strategy and recent logs showed IPv6 direct egress attempts failing with `network is unreachable`.
  - rejected approach: outbound `domain_strategy: ipv4_only` failed `sing-box check` on sing-box `1.13.11` because legacy domain strategy options are deprecated and require an environment escape hatch.
  - repair: added route rule `{ "action": "resolve", "strategy": "ipv4_only" }` after the existing sniff rule in `/etc/v2ray-agent/sing-box/conf/config.json`.
  - validation: `sing-box check -c /etc/v2ray-agent/sing-box/conf/config.json` succeeded; `sing-box` restarted and is `active`.
- subscription cleanup
  - root cause: `subscribe_local` still contained stale old identity files and stale ports including `15784`, `19518`, and impossible VLESS Reality port `1578422752`.
  - repair: removed stale `61ea22fd` subscription files, removed the stale `1578422752` ClashMeta node, de-duplicated the current Hysteria2 node, and retained current active nodes only.
  - validation: stale port grep returned no matches; current subscription summary only includes Hysteria2 `443` and VLESS Reality `22752`.
- final verification
  - `systemctl --failed --no-legend` returned no failed units.
  - listeners include UDP `*:443`, TCP `*:22752`, and TCP `0.0.0.0:35622`.
  - recent sing-box journal no longer showed `connect: connection refused` or `network is unreachable`; it showed only `REALITY: processed invalid connection` for invalid client/probe traffic.
  - `scripts\run_gates.ps1 -RunIntegration ... -IntegrationProfile zz` passed with `55 passed, 6 subtests passed`; dependency/security/lint/type gates passed.
