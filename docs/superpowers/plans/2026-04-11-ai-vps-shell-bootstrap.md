# vps-ssh-launcher Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan step by step. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `connect.cmd` the single entrypoint for a copied tool directory by auto-creating `target.json` when missing, bootstrapping Python dependencies, and supporting a short command-driven launch path.

**Architecture:** Keep `target.json` as the runtime config file and `target.example.json` as the checked-in sample template. `connect.cmd` delegates to `connect.ps1`, which creates a placeholder local config on first run, installs `paramiko` if needed, and then invokes the existing PowerShell/Python execution path.

**Tech Stack:** Windows batch, PowerShell 5+, Python 3, Paramiko

---

### Task 1: Make the bootstrap entrypoint self-contained

**Files:**
- Modify: `E:/CODE/ai-vps-shell/connect.cmd`
- Modify: `E:/CODE/ai-vps-shell/connect.ps1`

- [ ] **Step 1: Define the first-run behavior**

```powershell
# If target.json is missing, create it with placeholder values and exit.
```

- [ ] **Step 2: Define the dependency bootstrap behavior**

```powershell
# Detect python/py, install requirements.txt, then invoke connect.ps1.
```

- [ ] **Step 3: Wire connect.cmd to connect.ps1**

```bat
@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%connect.ps1" %*
exit /b %errorlevel%
```

- [ ] **Step 4: Verify the bootstrap flow**

Run: `E:\CODE\ai-vps-shell\connect.cmd`

Expected: If `target.json` is absent, a placeholder `target.json` is created and the command exits cleanly.

### Task 2: Tighten the direct launch path

**Files:**
- Modify: `E:/CODE/ai-vps-shell/connect.ps1`
- Modify: `E:/CODE/ai-vps-shell/ssh_tool.py`

- [ ] **Step 1: Allow command-only launch when config already exists**

```powershell
# connect.ps1 should pass through Config/Command/StrictHostKeyChecking without requiring extra wrapper files.
```

- [ ] **Step 2: Read target.json as the default config**

```python
# ssh_tool.py should load target.json from the script directory when --config is omitted.
```

- [ ] **Step 3: Validate missing-command failure is explicit**

```python
# run mode without command should fail fast with a clear error message.
```

- [ ] **Step 4: Verify the direct launch path**

Run: `E:\CODE\ai-vps-shell\connect.cmd -Command "uname -a"`

Expected: The wrapper forwards the command through to the SSH runner.

### Task 3: Update docs for the copy-and-run workflow

**Files:**
- Modify: `E:/CODE/ai-vps-shell/README.md`
- Modify: `E:/CODE/ai-vps-shell/tool-protocol.md`

- [ ] **Step 1: Document the new-machine flow**

```markdown
Copy the directory, fill target.json, run connect.cmd.
```

- [ ] **Step 2: Document first-run template generation**

```markdown
If target.json is missing, connect.cmd creates a placeholder target.json and exits.
```

- [ ] **Step 3: Document the short command entry**

```powershell
E:\CODE\ai-vps-shell\connect.cmd -Command "uname -a"
```

- [ ] **Step 4: Verify docs mention the local runtime config first**

Run: `Select-String -Path E:\CODE\ai-vps-shell\README.md -Pattern 'target\.example|target\.json\.template'`

Expected: No matches.
