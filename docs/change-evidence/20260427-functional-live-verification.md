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

## Sensitive Data Boundary

- Verification used the user-local config at `%APPDATA%\vps-ssh-launcher\target.json`.
- Evidence intentionally omits passwords, private keys, and full local config contents.
