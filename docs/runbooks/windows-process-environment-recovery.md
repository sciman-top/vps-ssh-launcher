# Windows Process Environment Recovery

本 runbook 处理 Windows 父进程环境不完整导致的子进程异常，不处理 SSH 业务逻辑。

## 识别

优先怀疑进程环境的情形：

- `python -c "import asyncio"` 报 `WinError 10106`。
- `cmd`、`rg`、Python 或 PowerShell 子进程只在某个宿主内失败。
- 同一命令在新开的 PowerShell 7 中正常。

仓库入口会通过 `scripts/lib/project_environment.ps1` 补齐常见缺失项：`ComSpec`、`SystemRoot`、`WINDIR`、`APPDATA`、`LOCALAPPDATA`、`PROGRAMDATA`，并统一解析项目 Python。

## 最小诊断

在新开的 `pwsh` 中运行：

```powershell
python -c "import asyncio; print('asyncio ok')"
$env:ComSpec
$env:SystemRoot
$env:APPDATA
```

随后通过仓库入口重试：

```powershell
.\connect.cmd -Profile example
.\scripts\run_gates.ps1
```

如果只有原宿主失败，比较两个进程的环境变量；优先换到正常会话或补齐当前进程环境。不要先改业务代码，也不要未经用户明确确认重启、停止或拉起 Codex。

## 沙箱/ACL 改权限失败（区分：不是进程环境）

DSH 的 `workspace-write` 会在启动子进程前给工作区加写权限；若工作区根目录的 ACL 混用
显式 ACE 与继承 ACE，该步骤可能以 `SetNamedSecurityInfoW ... Win32 5 (拒绝访问)` 失败，
此时任何命令都不会启动，报错来自沙箱而不是仓库逻辑。判据与处置：

- 只读检查：`icacls <workspace>` 是否同时出现显式条目（特定 SID 或沙箱组）与 `(I)` 继承条目。
- 不要为了绕过而改仓库或重试同一命令；这不是进程环境缺失。
- 处置顺序：改用具备所需权限的审批模式（`danger-full-access` 或等价），或让目录管理员把
  工作区根的 ACL 收敛为单一来源后再用受限模式。
- 报错文本里的 `grantWrite(<path>)` 是工作区根路径，不是失败的文件；不要据此删除或移动目录。

## 系统级恢复

只有新的管理员 PowerShell 也能复现时，才考虑系统网络栈修复：

```powershell
netsh winsock reset
netsh int ip reset
ipconfig /flushdns
```

这些命令及后续重启需要显式授权。执行前记录失败命令、父进程、缺失变量，以及新 `pwsh` 是否复现；不要为普通本地改动另建审计文档。
