# Security Waivers

This file records explicit security tradeoffs that are preserved for backward
compatibility. Each waiver must have an owner, expiry, evidence, and recovery
plan.

- Last reviewed: `2026-06-13`
- Review basis: `ssh_tool.py`, `README.md`, `scripts/run_gates.ps1`, `python -m bandit -q -r ssh_tool.py auto_install.py`
- Next review trigger: before current `Expires at` date, or immediately when default host-key policy, remote command model, or CLI authentication semantics change

## B507: Default Non-Strict SSH Host Key Policy

- Owner: repository maintainer
- Status: accepted compatibility waiver
- Expires at: 2026-07-31
- Evidence link: `bandit -q -r ssh_tool.py auto_install.py`
- Affected code: `ssh_tool.connect_client`
- Reason: existing usage defaults to first-run VPS convenience. Changing the
  default to strict host key checking would reject unknown hosts and break the
  current copy-and-run workflow.
- Compensating control: users can opt into strict verification with
  `--strict-host-key-checking` or `connect.cmd -StrictHostKeyChecking`.
- Recovery plan: make strict host key checking the default only after adding a
  documented known-host bootstrap flow and a compatibility window.

## B601: Explicit Remote Command Execution

- Owner: repository maintainer
- Status: accepted design waiver
- Expires at: 2026-07-31
- Evidence link: `bandit -q -r ssh_tool.py auto_install.py`
- Affected code: `ssh_tool.exec_remote`
- Reason: executing the exact command supplied by the operator is the core
  feature of this CLI. The command is not interpreted as data from an external
  source by this tool.
- Compensating control: configuration and tests keep command execution explicit;
  no shell command is constructed from profile fields.
- Recovery plan: if this grows into a multi-user service, replace raw command
  input with an allowlisted operation model before exposing it beyond a trusted
  local operator.
