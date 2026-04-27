# 2026-04-27 Gate Scope Hardening Follow-up

## Scope

- Rule IDs: R2, R6, R8, E4, E5
- Risk level: low
- Landing: local gate/test hardening only
- Target home: `scripts/run_gates.ps1`, `test_scripts.py`, `test_ssh_tool.py`, `.gitignore`
- Real SSH triggered: no

## Baseline

Command:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Result:

- build: passed
- pytest: `59 passed, 1 skipped, 15 subtests passed`
- unittest: passed
- pip check: passed
- pip-audit: no known vulnerabilities
- bandit/vulture/ruff check: passed
- stopped at `lint:format`
- root cause: `ruff format --check .` scanned non-source local/managed files (`.claude/` and `.governed-ai/verify-powershell-policy.py`), making the repo gate sensitive to local tool state and managed-file formatting drift.

## Changes

- Added `.claude/` to `.gitignore` so local tool hooks/settings stay out of repo status and normal gate discovery.
- Centralized `scripts/run_gates.ps1` Python source inputs in `$pythonGateFiles`.
- Changed `ruff`, `mypy`, `pyright`, and `vulture` gates from broad `.` scanning or duplicated lists to the explicit repo Python source/test file list.
- Added `contract:powershell-policy` to execute `.governed-ai/verify-powershell-policy.py` as a contract check without treating the managed file as normal Python source for formatting/type gates.
- Synchronized `.governed-ai/repo-profile.json` so the versioned repo metadata uses the same targeted `ruff` scope and records the PowerShell policy contract.
- Added tests that lock the new gate scope and the PowerShell policy contract entry.
- Added a repo-profile regression test so metadata cannot silently drift back to broad `ruff check .` / `ruff format --check .`.
- Added tests for SSH host-key policy behavior: default compatibility path uses `AutoAddPolicy`; `strict_host_key_checking=True` loads system host keys and uses `RejectPolicy`.

## Verification

Targeted:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_scripts.py
.\.venv\Scripts\python.exe .governed-ai\verify-powershell-policy.py
.\.venv\Scripts\python.exe -m pytest -q test_ssh_tool.py -k "host_key or connect_client"
.\.venv\Scripts\python.exe -m pyright test_ssh_tool.py
.\.venv\Scripts\python.exe -m json.tool .governed-ai\repo-profile.json
.\.venv\Scripts\python.exe -m pytest -q test_scripts.py -k "repo_profile or run_gates"
rg -n "ruff check \.|ruff format --check \." .governed-ai .github scripts test_scripts.py pyproject.toml README.md AGENTS.md CLAUDE.md GEMINI.md
```

Results:

- `test_scripts.py`: `15 passed, 19 subtests passed`
- PowerShell policy verifier: `status=pass`, `violation_count=0`
- host-key/connect-client targeted tests: `7 passed, 32 deselected`
- `pyright test_ssh_tool.py`: `0 errors, 0 warnings, 0 informations`
- `repo-profile.json`: valid JSON
- repo-profile/run-gates targeted tests: `3 passed, 12 deselected`
- broad `ruff check .` / `ruff format --check .` search: remaining hits only in `test_scripts.py` `assertNotIn` guards

Full gate:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Results:

- build: passed
- pytest: `62 passed, 1 skipped, 15 subtests passed`
- unittest: `63 tests`, `OK (skipped=1)`
- `contract:powershell-policy`: `status=pass`, `violation_count=0`
- `pip check`: `No broken requirements found.`
- `pip-audit`: `No known vulnerabilities found`
- `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, `pyright`: passed

Additional:

```powershell
git diff --check
Measure-Command { .\.venv\Scripts\python.exe .\ssh_tool.py --version > $null }
Measure-Command { .\.venv\Scripts\python.exe .\ssh_tool.py --help > $null }
```

Results:

- `git diff --check`: passed; line-ending warnings only
- startup smoke: `--version` about `149.3ms`; `--help` about `130.0ms`

Real SSH read-only integration:

```powershell
$config = Join-Path $env:APPDATA 'vps-ssh-launcher\target.json'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1 -RunIntegration -IntegrationConfig $config -IntegrationProfile bwg -IntegrationCommand 'printf vps-ssh-launcher-bwg-real-ssh-20260427' -IntegrationExpected 'vps-ssh-launcher-bwg-real-ssh-20260427'
```

Results:

- `bwg` only: passed.
- `pytest`: `63 passed, 15 subtests passed`; the real SSH integration test ran instead of being skipped.
- Full gate passed, including `contract:powershell-policy`, `pip check`, `pip-audit`, `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, and `pyright`.
- Remote command was limited to `printf vps-ssh-launcher-bwg-real-ssh-20260427`.
- `zz` was not targeted.
- `target.json` content was not opened or recorded; only the user config path was passed to the existing gate.

Bwg live function verification:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg -Apply
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "/bin/bash /etc/v2ray-agent/auto_update_xray.sh"
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "<post-Xray-wrapper service/config verification>"
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "<auto_system_maint.sh heartbeat wrapper>"
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "<post-maintenance service/package/config verification>"
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\google_ipv4_routing.ps1 -Profile bwg
```

Results:

- `google_ipv4_routing.ps1 -Apply`: passed; remote reported `google-ipv4-routing already current`.
- Xray state after Google IPv4 apply: `active`, `config-ok`; routing contained `google_ipv4_out` and `ForceIPv4`; public egress returned IPv4 `144.34.229.116` and IPv6 `2607:8700:5500:50c7::2`.
- `vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply`: passed; cron was `0 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh`.
- `bwg` kernel mapping: `/etc/v2ray-agent/auto_update_xray.sh` existed and passed `bash -n`; `/etc/v2ray-agent/auto_update_singbox.sh` was absent.
- Manual Xray wrapper run: `/bin/bash /etc/v2ray-agent/auto_update_xray.sh` returned exit `0`.
- Post-wrapper verification: `xray` was `active`, Xray version was `26.3.27`, config test returned `config-ok`, and `systemctl --failed` listed `0 loaded units`.
- First direct system-maintenance run exceeded the SSH helper's 60s idle timeout while remote `apt-get update` continued. Follow-up status confirmed no Xray outage, no failed units, and the apt process completed.
- System maintenance was rerun through a heartbeat wrapper to avoid SSH idle timeout. It returned `maint-exit=0`.
- Maintenance log showed complete real run through `Step 1` to `Step 7`, including `apt update`, `apt upgrade`, `apt autoremove --purge`, `apt clean`, `journal vacuum`, proxy service health checks, reboot policy, and `System maintenance done`.
- Apt history showed `liblcms2-2` upgraded from `2.14-2build1` to `2.14-2ubuntu0.1`.
- Post-maintenance verification: no maintenance/apt/dpkg processes remained; `dpkg --audit` was clean; `apt-get check` passed; `xray` was `active`; Xray config test returned `config-ok`; `systemctl --failed` listed `0 loaded units`; no reboot was required; `/` was `3.7G/40G (10%)` and `/boot` was `103M/974M (12%)`.
- Final `google_ipv4_routing.ps1 -Profile bwg` read-only check after Xray update and system maintenance still returned `active`, `config-ok`, `google_ipv4_out`, and `ForceIPv4`.
- `zz` was not targeted.

Auto-install guard verification:

```powershell
.\.venv\Scripts\python.exe .\auto_install.py
.\.venv\Scripts\python.exe .\auto_install.py --execute --expect-timeout 60
.\.venv\Scripts\python.exe .\auto_install.py --help
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "<copy auto_install.py to /tmp and run guard checks>"
```

Results:

- Local default run: expected guard failure, exit `2`; no installer started.
- Local `--execute --expect-timeout 60`: expected guard failure, exit `2`.
- Local `--help`: passed.
- `bwg` remote had `/etc/v2ray-agent/install.sh` and `/usr/bin/vasma -> /etc/v2ray-agent/install.sh`.
- Remote copied guard test default run: expected exit `2`.
- Remote copied guard test invalid timeout: expected exit `2`.
- Remote copied guard test `--help`: exit `0`.
- No residual `auto_install.py`, `/etc/v2ray-agent/install.sh`, or `/usr/bin/vasma` process was left by the guard tests.
- `auto_install.py --execute` was intentionally not run because it is an installer/reinstall path that can rewrite Xray, nginx, subscriptions, and port configuration; the verified behavior here is that its guard prevents accidental mutation.

## Follow-up Code Fix: Long Silent Command Timeout

Issue:

- The first direct `auto_system_maint.sh` run exceeded `ssh_tool.py`'s fixed 60s remote command idle timeout while the remote `apt-get update` was still running. The local SSH channel closed before the remote script reached its own health-check tail, and the remote log recorded `exit=141` at the generic command wrapper.

Changes:

- Added `ssh_tool.py run --command-timeout <seconds>`.
- Default remains `60` seconds for backward compatibility.
- `--command-timeout 0` disables command timeout.
- `connect.ps1` now exposes `-CommandTimeout` and passes it through to `ssh_tool.py`.
- Added regression tests for custom timeout, disabled timeout, invalid timeout, `RunAll` timeout propagation, and PowerShell argument passthrough.
- Documented `-CommandTimeout` in `README.md`.

Verification:

```powershell
.\.venv\Scripts\python.exe -m pytest -q test_ssh_tool.py -k "timeout or run_on_all_passes_command_timeout"
.\.venv\Scripts\python.exe -m pytest -q test_scripts.py -k "command_timeout or powershell_scripts_parse"
.\.venv\Scripts\python.exe -m mypy ssh_tool.py test_ssh_tool.py test_scripts.py
.\.venv\Scripts\python.exe -m pyright ssh_tool.py test_ssh_tool.py test_scripts.py
.\.venv\Scripts\python.exe .\ssh_tool.py --profile bwg run --command "sleep 75; printf command-timeout-ok" --command-timeout 120
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Results:

- Targeted timeout tests: `5 passed, 38 deselected`.
- Targeted PowerShell tests: `2 passed, 14 deselected, 5 subtests passed`.
- `mypy`: passed.
- `pyright`: passed.
- Real `bwg` long silent command: passed; `sleep 75` completed and printed `command-timeout-ok`.
- Full gate: `67 passed, 1 skipped, 15 subtests passed`; `unittest` ran `68 tests`, `OK (skipped=1)`; `contract:powershell-policy`, `pip check`, `pip-audit`, `bandit`, `vulture`, `ruff check`, `ruff format --check`, `mypy`, and `pyright` passed.

## Follow-up Fix: Repo Profile Contract Drift

Issue:

- The final full gate found `ScriptValidationTests.test_repo_profile_matches_targeted_python_gate_scope` failing because `.governed-ai/repo-profile.json` did not list the PowerShell policy verifier in `contract_commands`, while `scripts/run_gates.ps1` already enforced `contract:powershell-policy`.

Change:

- Added `python .governed-ai/verify-powershell-policy.py` to `.governed-ai/repo-profile.json` as required contract command `powershell-policy`.

Verification:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_gates.ps1
```

Results:

- Full gate passed.
- `pytest`: `67 passed, 1 skipped, 15 subtests passed`.
- `unittest`: `Ran 68 tests`, `OK (skipped=1)`.
- `contract:powershell-policy`: `status=pass`, `violation_count=0`.
- `pip check`: `No broken requirements found.`
- `pip-audit`: `No known vulnerabilities found`.
- `ruff check`: `All checks passed!`
- `ruff format --check`: `6 files already formatted`.
- `mypy`: `Success: no issues found in 6 source files`.
- `pyright`: `0 errors, 0 warnings, 0 informations`.

## N/A

- Dedicated benchmark: `gate_na`
- reason: this repo does not currently include a benchmark suite or benchmark script.
- alternative_verification: full gate timing plus `ssh_tool.py --version` / `--help` startup smoke.
- evidence_link: this file.
- expires_at: next change that adds performance-sensitive startup, config parsing, SSH execution, or benchmark tooling.

## Rollback

- Code rollback: revert this change set for `.gitignore`, `.governed-ai/repo-profile.json`, `scripts/run_gates.ps1`, `connect.ps1`, `ssh_tool.py`, `README.md`, `test_scripts.py`, `test_ssh_tool.py`, and this evidence file.
- Runtime rollback: Google IPv4 apply was idempotent; Xray wrapper/cron is the intended `bwg` state and can be reverted with `crontab -e` plus removing `/etc/v2ray-agent/auto_update_xray.sh` if needed. System maintenance changed normal apt/journal state and upgraded `liblcms2-2`; package rollback would require apt pin/downgrade if ever needed. No `target.json`, SSH profile, credential, or `zz` host was modified.
