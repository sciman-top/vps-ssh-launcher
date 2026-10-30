# vps-ssh-launcher

<p align="center">
  <strong>⚡ Windows-First High-Performance SSH Launcher · Declarative VPS Maintenance Control Plane · Production-Grade AI Gateway Guardrails</strong>
</p>

<p align="center">
  <a href="README.md">中文文档</a> | <a href="#-why-vps-ssh-launcher">Features</a> | <a href="#-quick-start">Quick Start</a> | <a href="#-core-architecture">Architecture</a> | <a href="#-declarative-vps-maintenance-control-plane-vps-maint">Control Plane</a> | <a href="#-production-grade-ai-gateway-guardrails-cpa">AI Gateway Guardrails</a> | <a href="#-runbook-navigation">Runbooks</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-0078D6?logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PowerShell-7%2B-5391FE?logo=powershell" alt="PowerShell">
  <img src="https://img.shields.io/badge/SSH%20Core-Paramiko%205.x-green" alt="Paramiko">
  <img src="https://img.shields.io/badge/Code%20Style-Ruff-black?logo=ruff" alt="Ruff">
  <img src="https://img.shields.io/badge/Type%20Check-Mypy-blue" alt="Mypy">
  <img src="https://img.shields.io/badge/Security-Bandit-red" alt="Bandit">
</p>

---

## 📖 Introduction

`vps-ssh-launcher` is a Windows-first, cross-platform **Python/PowerShell SSH launcher, declarative VPS maintenance control plane, and production-grade AI gateway (CLIProxyAPI) guardrail stack**.

It is engineered to solve common pain points when managing Linux VPS fleets from Windows: escaping chaos across CMD/PowerShell, fragile credential boundaries, collateral damage during bulk concurrent executions, lack of declarative rollback guarantees, and the notorious "429 Rate Limit mystery" and account ban risks when running high-concurrency AI API reverse/proxy gateways.

> [!TIP]
> **Three Distinct Product Boundaries**:
> 1. **Universal SSH Launcher**: Eliminates Windows quote/escaping hell via file-level clean transport (`-CommandFile`), SOCKS5 tunneling, idle/hard timeouts, and safe allowlist-only concurrent diagnostics (`-RunAll`).
> 2. **Declarative VPS Maintenance Control Plane (`vps-maint`)**: Follows a strict `State -> Inventory -> Plan -> Apply -> Receipt` closed loop in SQLite, with exact binary/image digest pinning, host fact fingerprint drift detection, and automated atomic rollback.
> 3. **AI Gateway Guardrails & Scheduling (CPA)**: Built for production CLIProxyAPI deployments, featuring 4-tier 429 smart failure triage, 4 physically isolated admission lanes with model-level circuit breaking, zero-downtime reversible account quarantine, and host-wide mutual exclusion lock protection.

---

## ✨ Why vps-ssh-launcher?

Targeting everyday Linux VPS operations and AI gateway setups, `vps-ssh-launcher` provides out-of-the-box engineering solutions:

| Ops Pain Point | Traditional Challenges | vps-ssh-launcher Solution |
| --- | --- | --- |
| **Windows Command "Escaping Hell"** | CMD / PowerShell quotes, pipes (`\|`), `&&`, and newlines frequently break during SSH forwarding | **Native `-CommandFile` Support**: Write complex shell scripts to local UTF-8 files; built-in Base64 / chunked transport streams them cleanly to the remote host without escaping issues |
| **Credential Leaks & Accidental Damage** | Config files accidentally committed to Git; bulk concurrent operations taking down multiple servers at once | **Physical Credential Isolation + Strict Readonly Allowlist**: Real credentials live exclusively in local `%APPDATA%`; operations default to readonly probes; `-RunAll` concurrent mode permits only allowlisted diagnostic commands, while high-risk changes require explicit, serialized confirmation per host |
| **Non-Deterministic Upgrades & Lack of Rollbacks** | Relying on one-click scripts blindly; unable to rollback when environments change or configs conflict | **Declarative Maintenance Control Plane (`vps-maint`)**: SQLite-backed state tracking, mandatory version / container image SHA-256 pinning, pre-apply host fact fingerprint verification, and fully automated atomic rollback on failure |
| **AI Gateway & Multi-Account "429 Mystery"** | Client receives 429; impossible to tell if it's local throttling, Nginx rate limits, queue timeouts, or upstream capacity bans | **4-Tier 429 Smart Failure Triage**: Pinpoints root cause by analyzing request wait budgets and access logs; 4 physically isolated Admission Lanes prevent single-model overload from cascading |
| **High-Value OAuth Account Ban & Contagion** | Testing new models or traffic spikes leading to the ban of your sole ChatGPT/Claude/Gemini account | **Zero-Downtime Reversible Account Quarantine**: Safely isolates high-risk accounts from routing catalogs with zero downtime while preserving credentials, preventing aggressive retries from triggering collateral bans |
| **Multi-Task Crontab Conflicts & Overwrites** | Automated scripts clashing, or third-party tools wiping crontab during cert renewals | **System-Wide Distributed Lock + `/etc/cron.d/`**: Unified `/run/vps-ssh-launcher-maintenance.lock` (flock); all scheduled tasks live in isolated `/etc/cron.d/` units, completely immune to crontab overwrites |

---

## 🏛️ Core Architecture

```text
+---------------------------------------------------------------------------------+
|                         Local Host (Windows / Linux)                            |
|                                                                                 |
|  +-----------------------+  +-------------------------+  +-------------------+  |
|  |  Universal Launcher   |  |   Declarative Control   |  |   CPA Gateway     |  |
|  |                       |  |   Plane (vps-maint)     |  |   Guardrails      |  |
|  |  • run.cmd            |  |  • Inventory (Readonly) |  |  • 429 Triage     |  |
|  |  • connect.ps1        |  |  • Plan (Fingerprint)   |  |  • Risk Audit     |  |
|  |  • -CommandFile       |  |  • Apply (Pin/Rollback) |  |  • Lane Admission |  |
|  |  • -RunAll (Safe)     |  |  • Receipts (SQLite)    |  |  • Quarantine     |  |
|  +-----------+-----------+  +------------+------------+  +---------+---------+  |
|              |                           |                         |            |
|              +---------------------------+-------------------------+            |
|                                          |                                      |
|                       Local Auth: %APPDATA%/target.json                         |
|                       Paramiko 5.x / SOCKS5 / SSH Agent                         |
+------------------------------------------+--------------------------------------+
                                           |  Encrypted SSH (Base64/Chunked)
                                           v
+---------------------------------------------------------------------------------+
|                              Remote Linux VPS                                   |
|                                                                                 |
|    [ Unified Maintenance Lock: /run/vps-ssh-launcher-maintenance.lock (flock) ] |
|                                                                                 |
|  +---------------------------+  +---------------------+  +-------------------+  |
|  | Core Services             |  | AI Gateway (CPA)    |  | Scheduled Tasks   |  |
|  | • Xray / sing-box         |  | • Nginx (TLS 8443)  |  | (/etc/cron.d/)    |  |
|  | • Docker Compose Stacks   |  | • Admission (8318)  |  | • Kernel Updates  |  |
|  | • v2ray-agent (Pinned)    |  | • CPA Daemon (8317) |  | • Monthly Apt     |  |
|  +---------------------------+  +---------------------+  +-------------------+  |
+---------------------------------------------------------------------------------+
```

### Module Boundaries & Implementation Matrix

The SSH core is strictly separated by responsibility. Existing commands, exit codes, and the `ssh_tool.py` entrypoint remain backward-compatible.
New code imports from corresponding capability modules directly; public re-exports in `cli.py` preserve legacy Python callers.
Package initialization loads only shared contracts, deferring CLI loading until `main()` is called.
Launcher unit tests align with modules: `test_config.py` / `test_connection.py` / `test_execution.py` / `test_batch.py` map to the four capability modules; `test_ssh_tool.py` retains CLI parsing, `main()`, and compatibility surfaces. Shared fakes and patch helpers reside in `tests/launcher_fakes.py`.

| Target File | Responsibility | Verification on Changes |
| --- | --- | --- |
| `vps_ssh_launcher/contracts.py` | Defaults, exit codes, remote client protocols | Verify all consumers and compatibility |
| `vps_ssh_launcher/config.py` | Local config, profile selection, auth resolution | Config errors, CLI overrides, relative key paths |
| `vps_ssh_launcher/connection.py` | SSH lifecycle, host keys, retry logic | Strict trust, failure cleanup, transient retry only |
| `vps_ssh_launcher/socks5.py` | SOCKS5 CONNECT handshake and failure cleanup | IPv4/IPv6/domain, truncated replies, SSH stream alignment |
| `vps_ssh_launcher/execution.py` | Command execution, stdout/stderr drain, timeout caps | Dual streams, UTF-8 chunking, idle/total timeout |
| `vps_ssh_launcher/batch.py` | Readonly admission, concurrency cap, per-host aggregation | Write rejection, output limits, failure classification |
| `vps_ssh_launcher/cli.py` | Argument parsing, action dispatch, terminal exit codes | CLI compatibility, entrypoint error mapping |
| `vps_ssh_launcher/maintenance_cli.py` | `vps-maint` subcommand dispatch, apply high-risk gate | CLI / apply flows in `tests/test_maintenance.py` |
| `vps_ssh_launcher/maintenance/workflow.py` | Validate one host's plan, run adapters, persist each transition | Connect failure, result markers, adapter interruption, client close |
| `vps_ssh_launcher/maintenance/` | Policy, inventory, planner, state, adapters, receipts | Plan / execution / rollback in `tests/test_maintenance.py` |
| `vps_ssh_launcher/maintenance/resources.py` | Unified resource registry: pin validation, plan build | Resource config -> plan -> execution workflow |
| `vps_ssh_launcher/maintenance/fingerprint.py` | Canonical JSON and inventory identity fingerprints, without SSH | Volatile telemetry stays out; listener changes require a new plan |
| `vps_ssh_launcher/maintenance/planner.py` / `models.py` / `state.py` | Plan building and transitions, status contract, persistence validation | Profile scope, unknown status rejection, historical plan compatibility |
| `vps_ssh_launcher/maintenance/xray.py` / `docker.py` | Resource-specific pin schema, upgrade logic, rollback | Resource plan, execution, failure recovery |
| `vps_ssh_launcher/maintenance/pins.py` / `adapters.py` | Shared pin validation / registry dispatch & verdict | Unknown resource rejection, success/rollback markers |
| `scripts/cpa_bwg_guardrails.ps1` | CPA admission, template loading, hash projection | PowerShell parsing, source drift, parameter stability |
| `scripts/remote/cpa-guardrail-*.sh` | Doctor, apply, path rotation, OAuth deactivation/quarantine | Bash syntax, template loading, fixture tests |
| `scripts/` and `scripts/remote/` | Local ops entrypoints and byte-deployed remote runtimes | Domain tests in `tests/`, projection manifest |

Add Python subpackages under `vps_ssh_launcher/` with an `__init__.py`; package discovery
is restricted to that prefix. Inspect the built wheel when adding modules. Keep CLI
argument/output handling in the entrypoint and remote execution in the maintenance workflow;
plan status transitions live in `planner.updated_plan()`.

---

## ⚡ Quick Start

```powershell
.\run.cmd
```

On first run, a configuration template will be automatically created at `%APPDATA%\vps-ssh-launcher\target.json`. Fill in your server details and run again:

```powershell
# Connectivity probe
.\run.cmd -Profile example

# Execute remote single-line command
.\run.cmd -Profile example -Command "uname -a"
```

> [!NOTE]
> `run.cmd` is a short alias for `connect.cmd`. The end-to-end invocation chain is:
> ```text
> run.cmd -> connect.cmd -> connect.ps1 -> ssh_tool.py -> vps_ssh_launcher/cli.py
> ```

### Eliminating Windows Escaping Hell: `-CommandFile`

Never paste complex shell commands directly into CMD arguments. Write your remote shell script into a UTF-8 file and pass its path.
The file content can freely include pipes, `&&`, nested quotes, and multiline scripts. Built-in Base64 / chunked transport streams it losslessly to the remote host:

```powershell
@'
printf '%s\n' "quoted" | grep quoted && echo 'done'
'@ | Set-Content -LiteralPath .\remote-command.sh -Encoding utf8NoBOM

.\run.cmd -Profile example -CommandFile .\remote-command.sh
```

When using `connect.ps1` directly, pass `-CommandFile <path>`. `-CommandFile` and `-Command` are mutually exclusive. Simple single-line commands can still use `-Command`.

### Timeouts & Long Command Protection

- `--command-timeout <seconds>` (default `60`): Idle timeout. Enforced during command submission; once output begins, acts as "idle timeout without output" and renews automatically upon receiving data; `0` disables idle timeout.
- `--command-hard-timeout <seconds>` (default `0`): Absolute maximum duration calculated from command submission; `0` disables hard timeout.
- Silent long-running commands (e.g. slow model generation, heavy compilations) must explicitly increase or set timeouts to `0`, otherwise they will be terminated locally after 60 seconds.

---

## ⚙️ Configuration & Security Model

### Search Order & Physical Isolation

1. `%APPDATA%\vps-ssh-launcher\target.json` (**Primary source of truth**, immune to accidental Git commits)
2. Repository root `target.json` (Retained for legacy compatibility only)

### Configuration Example (`target.json`)

```json
{
  "profiles": {
    "example": {
      "host": "YOUR_VPS_IP",
      "port": 22,
      "user": "root",
      "password_env": "VPS_EXAMPLE_PASSWORD",
      "socks5": "127.0.0.1:10808"
    }
  },
  "default": "example"
}
```

### Credentials & Security Standards

> [!IMPORTANT]
> Never commit or log real `target.json`, plaintext passwords, private key files, tokens, subscription URLs, or sensitive command output in code, documentation, or Pull Requests.

- **Multiple Authentication Methods**: Supports environment variable `password_env`, local `password`, private key path `key`, runtime `-Key`, and `-AllowAgent` (SSH Agent). Runtime parameters override profile configurations. Relative key paths resolve against the config file directory.
- **Safe Pipeline Input**: Supports `--password-stdin` to read a password line from stdin, completely preventing plaintext passwords from appearing in process listings.
- **SOCKS5 Tunnel Integration**: Supports `socks5` field (e.g. `"127.0.0.1:10808"` or `{"host": "::1", "port": 1080}`). When configured, SSH connects through the SOCKS5 proxy while host key verification and `known_hosts` remain strictly bound to the real destination address.
- **Host Key Verification**: Uses TOFU (Trust On First Use) by default, persisting accepted keys in the user profile directory. Subsequent key changes fail-closed. `-StrictHostKeyChecking` rejects any unknown host keys immediately.

---

## 📋 CLI Parameters & Exit Codes

### Common CLI Parameters

| Parameter | Behavior |
|---|---|
| `-Config <path>` | Specify configuration file path |
| `-Profile <name>` | Select target profile |
| `-Command <text>` | Execute remote command; checks connectivity when omitted |
| `-CommandFile <path>` | Submit remote shell script from local UTF-8 file, eliminating escaping conflicts |
| `-CommandTimeout <seconds>` | Idle timeout in seconds (default `60`, `0` disables) |
| `-CommandHardTimeout <seconds>` | Absolute maximum duration in seconds (default `0`, disabled) |
| `-Key <path>` | Use specified private key |
| `--password-stdin` | Read SSH password from stdin to avoid exposing it in process listings |
| `-AllowAgent` | Use SSH Agent for authentication |
| `-StrictHostKeyChecking` | Reject unknown host keys; default TOFU mode records key on first connection and fails closed on changes |
| `-RunAll` | Concurrently execute all profiles; implementation accepts only allowlisted diagnostic commands and rejects writes |
| `-AllowGlobalBootstrap` | Explicitly allow dependency installation into non-isolated Python |
| `-Verbose` | Output verbose debug logging |

### Standardized Exit Codes

| Exit Code | Meaning | Remediation |
|:---:|---|---|
| `0` | Success | Command completed normally |
| `1` | SSH Authentication Error | Check password, private key, SSH Agent, or remote permissions |
| `2` | Configuration Parsing Error | Check `target.json` syntax, missing profile, or field types |
| `3` | Connection Timeout | Check network connectivity, firewall rules, or SOCKS5 proxy status |
| `4` | Network Error | Check DNS resolution, port listening, or routing reachability |
| `5` | Local Driver / Remote Timeout / Internal Error | Check idle timeout budget or local execution pipe |
| `0-255` | Remote Command Native Exit Code | Once SSH connects, `run` passes remote exit codes directly |

After successful SSH connection, `run` returns remote exit codes `0-255` verbatim. `-RunAll` prints results and failure classifications per profile, returning the maximum exit code.

Single-host `run` streams stdout/stderr incrementally. `-RunAll` buffers up to 64K characters per stream in memory to prevent output interleaving.

### Safe Concurrent Diagnostics: `-RunAll` Allowlist

`-RunAll` restricts execution to fixed diagnostic commands: `uptime`, `uname`/`uname -a`, `df`/`df -h`/`df -Pk`,
`free`/`free -h`/`free -m`, `hostname`, `id`, `whoami`, `pwd`, `true`, `test`,
`ss -ltn`/`ss -ltnp`, `ps aux`, `docker ps`/`docker images`/`docker version`, and
`systemctl cat|is-active|is-enabled|show|status <literal-unit...>`.
All other commands must be executed via single-host `run` to prevent multi-purpose write arguments from bypassing batch boundaries.

---

## 🐍 Environment & Runtime Resolution

### Interpreter Resolution Order

1. `VPS_SSH_LAUNCHER_PYTHON`
2. `.venv\Scripts\python.exe`
3. PATH `python`

### PowerShell 7+ Requirement

`connect.cmd` requires PowerShell 7; `VPS_SSH_LAUNCHER_POWERSHELL` can specify an approved launcher, avoiding silent fallback to legacy Windows PowerShell 5.1.

### Paramiko 5.x Modern SSH Core

If Paramiko is missing or `<5`, `connect.ps1` automatically installs or upgrades it inside an isolated environment. Verified versions are cached in `paramiko-probe.cache`.
Runtime dependencies are pinned to validated Paramiko 5.x, deprecating insecure RSA/SHA-1 signatures and outdated KEX/GSSAPI paths.

### Development & Quality Gates

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run focused tests for fast feedback:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ci_meta.py tests/test_connect_entrypoints.py
```

Unified local quality gate:

```powershell
.\scripts\run_gates.ps1
```

The default `Full` profile executes an installed-package import check, pytest, Bandit, Ruff lint/format, and Mypy. CI runs the static scope once and keeps pytest on both Windows and Ubuntu; use `-FullScope Runtime` or `-FullScope Static` when selecting one scope explicitly.

For source-only changes, `Focused` accepts a Python file or directory. It runs related tests when they can be identified and still performs Ruff, format, and Mypy checks when no matching test exists.

---

## 🎯 Declarative VPS Maintenance Control Plane (`vps-maint`)

Addressing the pitfalls of blind, unaudited, non-rollbackable one-click scripts, `vps-maint` establishes a declarative closed loop:

```text
State (TOML) ──> Readonly Inventory ──> Deterministic Plan ──> Controlled Apply ──> Redacted Receipt
```

### 1. Core Workflow & Commands

```powershell
# 1. Initialize local maintenance policy from template
Copy-Item .\maintenance.example.toml "$env:APPDATA\vps-ssh-launcher\maintenance.toml"

# 2. Collect readonly inventory facts
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
vps-maint inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --output .\inventory.json

# 3. Generate deterministic upgrade plan against target state
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" plan --inventory-file .\inventory.json

# 4. Review historical plans and execution state
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" history
```

> [!NOTE]
> Real inventory collection requires `--run-integration` and `$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"`. State is persisted in `%APPDATA%\vps-ssh-launcher\maintenance.db` (SQLite), and execution receipts are written to `maintenance-receipts\` with strict credential redaction.

### 2. Host Fact Fingerprint Drift Prevention

`vps-maint apply` defaults to dry-run. Executing remote changes requires `--yes --remote-write --run-integration` under a single profile.
Prior to execution, inventory is freshly re-collected and verified against the planned **Fingerprint**:
- **Fingerprint Scope**: Covers identity facts (kernel, architecture, OS, binary SHA-256, container image digest, container lists, listening ports, service backends).
- **Telemetry Exemption**: Variable metrics (disk percentage, memory usage) are excluded from fingerprinting to avoid spurious plan invalidations.
- **Anti-Drift Guard**: Any modification in core identity facts aborts execution immediately, requiring a new plan.

### 3. Exact Pinning & Automated Rollback

- **Xray Core**: Strictly requires explicit version and binary SHA-256 pins (`Xray-linux-64.zip`); executes backup, config validation, service reload, and automated atomic rollback on failure.
- **Docker Containers**: Strictly requires absolute Compose file paths, service allowlists, and image digest pins; executes Compose config checks, health checks, digest readbacks, and rollback on failure.

Example upgrade command:

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" plan --live-inventory --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg --output .\upgrade-plan.json
vps-maint --config "$env:APPDATA\vps-ssh-launcher\maintenance.toml" apply --plan-id <plan-id> --yes --remote-write --run-integration --target-config "$env:APPDATA\vps-ssh-launcher\target.json" --profile bwg
```

### 4. PowerShell Scheduled Maintenance Task

Install a daily silent observation task (`20:00`) that generates plans and local logs without executing remote changes:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -WhatIf
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1
```

To remove the scheduled task:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_vps_maintenance_task.ps1 -Remove
```

---

## 🛡️ Production-Grade AI Gateway Guardrails (CPA)

CLIProxyAPI (CPA) on BWG exposes OpenAI-compatible APIs behind Nginx TLS 8443 with high-entropy randomized capability paths:
- **Public Ingress**: Nginx (8443) terminates TLS and validates random capability paths; rate limits return standard 429 with `Retry-After`.
- **Admission Layer**: `cpa-admission.service` (listening on `127.0.0.1:8318`), mediating all upstream AI requests.
- **Core Container**: CPA container binds strictly to local `127.0.0.1:8317`, preventing external bypass.

`scripts/cpa_bwg_guardrails.ps1` provides unified management (defaulting to readonly diagnostic probes). See [CPA Gateway Runbook](docs/runbooks/cpa-gateway.md) for architectural details.

### 1. 4-Tier 429 Smart Failure Triage

In distributed AI gateways, client-side `429 Too Many Requests` can stem from four distinct layers:
1. **Local Client Throttling**: Desktop sidecar concurrency exceeded or backoff window unhonored.
2. **Nginx Ingress Rate Limits**: Request rate exceeding ingress limits.
3. **Admission Lane Queue Limits**: Per-model concurrency cap (`max_inflight`) reached or queue timeout expired (120s).
4. **Upstream Provider Outage / Quota**: Upstream returns `Selected model is at capacity`, 429, 503, or account quota exhausted.

`cpa_failure_triage.py` combines local access log fingerprints with remote doctor diagnostics to differentiate local queue rejections from true upstream capacity exhaustion:

```powershell
# Readonly diagnosis for the past 4 hours of 429 / latency roots
.\.venv\Scripts\python.exe scripts/cpa_failure_triage.py --hours 4

# Generate comprehensive 24-hour JSON triage report with remote doctor output
.\.venv\Scripts\python.exe scripts/cpa_failure_triage.py --hours 24 --doctor outputs/doctor-latest.txt --json
```

See [Failure Triage Runbook](docs/runbooks/cpa-failure-triage.md) for triage tables.

### 2. Admission Risk & Contract Audit

Verify whether shared-account admission configs are consistent and whether published models remain operational:

```powershell
# Run 24-hour contract and route posture audit
.\.venv\Scripts\python.exe scripts/cpa_admission_risk_audit.py --hours 24

# Quick check verifying manifest config contracts only
.\.venv\Scripts\python.exe scripts/cpa_admission_risk_audit.py --skip-posture
```

Unified recovery workflow:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Audit
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Triage -Hours 4
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Verify
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode ControlledReplay
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode WaitCapSimulation
```

### 3. Four Physically Isolated Admission Lanes

| Admission Lane | Supported Models | Concurrency Control | Circuit Breaker & Cooldown |
|---|---|:---:|---|
| **`chatgpt-oauth`** | `gpt-6-luna`, `gpt-5.6-luna`, `gpt-6.1-sol` | `max_inflight=2`<br>`max_pending=4` (120s) | Distinguishes model-level vs account-level capacity; single model overload does not trip other models on the same account; 60–900s exponential backoff |
| **`zhipu-coding-plan`** | `glm-5.3`, `glm-5.3-flash` | `max_inflight=3`<br>`max_pending=4` (120s) | Follows Zhipu protocol; text-only models fast-fail image preflights (422); 86400s max cooldown with half-open probes |
| **`antigravity-oauth`** | `gemini-3.8-flash-high` | `max_inflight=2`<br>`max_pending=4` (120s) | Strict cooldown aligned to `RetryInfo.retryDelay`; `401/403` triggers traffic shutdown |
| **`workbuddy-oauth`** | `deepseek-v4.1-flash` | `max_inflight=1`<br>`max_pending=4` (120s) | Strict single-flight throttling; tombstone markers block legacy channels |

> [!IMPORTANT]
> **SSE Protocol Integrity & Disconnect Protection**: A recovery is only acknowledged when a compliant terminal event is received without errors. Truncated deltas or abrupt EOFs do not count as recovery, preventing false 200s from resetting circuit breakers.

### 4. Zero-Downtime Reversible Account Quarantine vs Deactivation

```powershell
# 1. Strict readonly health check (Doctor)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg

# 2. Reproject configurations from manifest (Apply)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply

# 3. Reversible Account Quarantine: Removes alias from routing, sets marker, retains credentials
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineAntigravity
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineClaudeOAuth -QuarantineUserDirective "User authorized quarantine"

# 4. Safe Restoration after troubleshooting
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreAntigravity
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth

# 5. Emergency Action: Permanent credential deactivation (Irreversible)
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

> [!TIP]
> During quarantine, `-Apply` is blocked automatically to prevent inadvertent configuration overwrites. Claude quarantine uses configuration hot-reload without restarting CPA; Luna and Antigravity quarantine update routing exclusions while preserving credentials.

### 5. Client Routing & Luna Dual-Mode Switching

- **Traffic Routing Priority**: DeepSeek Official API (cost-effective / non-sensitive) > GLM Coding Plan (compliant coding) > Luna ChatGPT Plus OAuth (high-interactivity) > ai.input.im (fallback).
- **Luna Switching Modes**: Cockpit client supports seamless switching between **Direct OAuth** and **Direct BWG API**.
- **Local Sidecar Guardrails & Health Checks**:
  ```powershell
  # Local desktop Sidecar contract audit and verification
  pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Audit
  pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify

  # Desktop provider config and endpoint check
  .\.venv\Scripts\python.exe scripts/cockpit_provider_health.py
  ```

---

## ⏰ Scheduled Tasks & Automated Operations

To prevent cron collisions and accidental crontab overwrites:
1. **System-Wide Distributed Mutex**: All scheduled tasks acquire `/run/vps-ssh-launcher-maintenance.lock` (`flock -n`), preventing concurrency conflicts.
2. **Decoupled from Root Crontab**: Tasks are installed into `/etc/cron.d/` units, completely immune to third-party scripts wiping crontab tables.

### 1. Xray / sing-box Kernel Updates (`vasma_kernel_update_cron.ps1`)

Automates weekly kernel updates with pre-execution verification of official binary SHA-256 and script fingerprints, automatically rolling back on failure:

```powershell
# 1. Readonly inspection
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray

# 2. Deploy verified update task
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray `
  -Version 26.3.27 -InstalledSha256 <binary-sha256> -VasmaSha256 <script-sha256> -Apply

# bwg is a dual-lane host (xray + sing-box coexist, vasma mixed mode); the
# projected cron file carries one line per wrapper at 14:20 / 14:30 UTC.
pwsh -NoProfile -File .\scripts\vasma_kernel_update_cron.ps1 -Profile bwg -Kernel sing-box `
  -Version 1.14.4 -InstalledSha256 <binary-sha256> -VasmaSha256 <script-sha256> `
  -Schedule '30 14 * * 5' -Apply
```

### 2. v2ray-agent Script Pinned Commit Updates (`v2ray_agent_script_update_cron.ps1`)

Pulls script updates strictly pegged to audited Git commits and file hashes defined in `scripts/remote/v2ray-agent-source-pin.json`:

```powershell
# 1. Read current remote script hash
pwsh -NoProfile -File .\scripts\v2ray_agent_script_update_cron.ps1 -Profile bwg

# 2. Deploy weekly cron update task (Fridays 14:40 UTC)
pwsh -NoProfile -File .\scripts\v2ray_agent_script_update_cron.ps1 -Profile bwg -InstallSha256 <fresh-sha256> -Apply
```

### 3. Monthly System Security Maintenance (`system_maintenance_cron.ps1`)

Runs security updates and housekeeping on the 1st of every month (`update` + `upgrade --with-new-pkgs` + `autoremove --purge` + 30-day journal vacuum):
- **Never automatically reboots**: Logs reboot flags to `/var/log/monthly-maintenance.log` for administrator scheduling.
- **Post-upgrade service health checks**: Verifies Xray, Nginx, fail2ban, etc.

```powershell
# 1. Readonly check
pwsh -NoProfile -File .\scripts\system_maintenance_cron.ps1 -Profile bwg

# 2. Deploy monthly maintenance task
pwsh -NoProfile -File .\scripts\system_maintenance_cron.ps1 -Profile bwg -Apply
```

### 4. BWG Full Maintenance Pipeline (`bwg_full_maintenance.ps1`)

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"

# Readonly observe mode
pwsh -NoProfile -File .\scripts\bwg_full_maintenance.ps1 -Mode Observe -RunIntegration

# Immediate maintenance run
pwsh -NoProfile -File .\scripts\bwg_full_maintenance.ps1 -Mode RunNow -RunIntegration
```

### 5. High-Risk Installer Boundary (`auto_install.py`)

`auto_install.py` drives remote `/etc/v2ray-agent/install.sh`. It is blocked by default and requires explicit authorization:

```bash
python -m pip install '.[installer]'
python ./auto_install.py --execute --install-script-sha256 <sha256>
```

---

## 🩺 Troubleshooting, Forensics & Evidence Boundaries

### Windows Process Environment Recovery

When encountering `WinError 10106` (Winsock catalog corruption), Python startup failures, or missing environment variables in minimal or sandbox hosts, follow [Windows Process Environment Recovery](docs/runbooks/windows-process-environment-recovery.md).

### Redaction-First Evidence Logging

- **Zero Leakage**: All receipts and logs are filtered through a strict sensitive information sanitization engine. Real IPs, passwords, private keys, tokens, and subscription URLs are never recorded.
- **Tiered Evidence Management**: Routine tests and readonly probes stay in commit messages; only actual remote changes, incidents, or releases record receipts in `docs/change-evidence/`.

---

## 📚 Runbook Navigation

23 comprehensive operational runbooks covering AI gateways, client guardrails, core infrastructure, and recovery:

| Category | Runbook | Scenario & Solution |
|---|---|---|
| **AI Gateway (CPA)** | [cpa-gateway.md](docs/runbooks/cpa-gateway.md) | CPA deployment topology, Nginx TLS + random capability path, strict Doctor gates |
| | [cpa-failure-triage.md](docs/runbooks/cpa-failure-triage.md) | 4-tier 429 triage, wait-budget analysis, timeout vs upstream circuit breaker identification |
| | [cpa-admission-risk-audit.md](docs/runbooks/cpa-admission-risk-audit.md) | Shared account admission config consistency, route probes, risk defense |
| | [cpa-recovery-workflow.md](docs/runbooks/cpa-recovery-workflow.md) | End-to-end recovery workflow (Audit -> Triage -> Verify -> ControlledReplay) |
| | [cpa-ban-throttle-incident-response.md](docs/runbooks/cpa-ban-throttle-incident-response.md) | Emergency response and traffic shutdown on bans or massive 429 cascades |
| | [cpa-stale-cooldown-recovery.md](docs/runbooks/cpa-stale-cooldown-recovery.md) | In-memory cooldown state management and stale cooldown cleanup |
| | [cpa-oauth-failure-recovery.md](docs/runbooks/cpa-oauth-failure-recovery.md) | OAuth credential expiration, refresh failures, and capacity overload recovery |
| | [cpa-oauth-luna-slot.md](docs/runbooks/cpa-oauth-luna-slot.md) | Luna dedicated slot model mapping and multi-model dispatch |
| | [cpa-catalog-change-checklist.md](docs/runbooks/cpa-catalog-change-checklist.md) | Model catalog addition, renaming, or deprecation checklist |
| | [codex-model-catalog-change.md](docs/runbooks/codex-model-catalog-change.md) | Codex instance catalog, model visibility, and context pin changes |
| | [cpa-claude-account-risk.md](docs/runbooks/cpa-claude-account-risk.md) | Anthropic ToS constraints, subscription OAuth risk boundaries, official API alternatives |
| | [cpa-claude-oauth-attach.md](docs/runbooks/cpa-claude-oauth-attach.md) | Claude OAuth hot-reload quarantine and transaction rollback |
| | [cpa-workbuddy-plugin.md](docs/runbooks/cpa-workbuddy-plugin.md) | WorkBuddy integration standards and DeepSeek single-flight concurrency constraints |
| | [cpa-error-dump-forensics.md](docs/runbooks/cpa-error-dump-forensics.md) | Gateway error snapshot dump and network forensics |
| | [cpa-manual-rollback.md](docs/runbooks/cpa-manual-rollback.md) | Manual rollback procedures for CPA deployments, manifests, and projected files |
| | [cpa-observed-baselines-and-upstream-log.md](docs/runbooks/cpa-observed-baselines-and-upstream-log.md) | Upstream request baseline observation and latency behavior reference |
| **Client & Sidecar** | [cockpit-sidecar-guardrails.md](docs/runbooks/cockpit-sidecar-guardrails.md) | Desktop Cockpit Sidecar concurrency caps, 45s wait limits, contract acceptance |
| | [cockpit-tools-persistent-fix-v1.3.65.md](docs/runbooks/cockpit-tools-persistent-fix-v1.3.65.md) | Persistent client toolchain fix and version evolution |
| | [cockpit-fq-access-mode-options.md](docs/runbooks/cockpit-fq-access-mode-options.md) | Technical comparison between direct access and gateway access modes |
| **VPS Core Infrastructure** | [v2ray-agent-script-update.md](docs/runbooks/v2ray-agent-script-update.md) | v2ray-agent script update, Git commit verification, rollback guide |
| | [kernel-core-manual-rollback.md](docs/runbooks/kernel-core-manual-rollback.md) | Manual emergency rollback guide for Xray / sing-box kernels |
| | [singbox-core-update-migration.md](docs/runbooks/singbox-core-update-migration.md) | sing-box 1.12+ architectural migration and lossless config upgrade |
| **System & Diagnostics** | [windows-process-environment-recovery.md](docs/runbooks/windows-process-environment-recovery.md) | Winsock corruption recovery and environment variable restoration |

---

## 🤝 Contributing & License

Contributions via Pull Requests and GitHub Issues are warmly welcomed! Please run local quality gates before submitting changes:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_gates.ps1 -Profile Full
```

Licensed under the MIT License.
