# Windows Process Environment Recovery

本 runbook 处理的不是仓库业务逻辑 bug，而是 Windows 父进程环境不完整导致的子进程异常。它对应 `.governed-ai/repo-profile.json` 中的 `windows_process_environment_policy.runtime_runbook`。

## 典型症状

如果在 Codex、CI 包装脚本、最小化 PowerShell 或其他宿主里看到下面现象，先按“进程环境异常”处理：

- `python -c "import asyncio"` 报 `WinError 10106`
- `node -e "console.log('node ok')"` 报 `ncrypto::CSPRNG`
- `rg`、`cmd`、`Start-Process`、`pyright`、`pip-audit` 表现异常
- 同一台机器上，仓库脚本在某个父进程里失败，但在新的 PowerShell 会话里恢复正常

## 已有自愈能力

本仓以下入口会先补齐常见缺失变量，再解析 Python：

- `connect.ps1`
- `scripts/run_gates.ps1`
- `scripts/google_ipv4_routing.ps1`
- `scripts/vasma_kernel_update_cron.ps1`

共用逻辑位于 `scripts/lib/project_environment.ps1`，会优先处理：

- `ComSpec`
- `SystemRoot`
- `WINDIR`
- `APPDATA`
- `LOCALAPPDATA`
- `PROGRAMDATA`
- `.venv\Scripts\python.exe` / `VPS_SSH_LAUNCHER_PYTHON`

## 先做什么

1. 在新的 `pwsh` 会话里重试最小探针：

```powershell
python -c "import asyncio; print('asyncio ok')"
node -e "console.log('node ok')"
```

2. 检查关键变量是否存在：

```powershell
$env:ComSpec
$env:SystemRoot
$env:WINDIR
$env:APPDATA
$env:LOCALAPPDATA
$env:PROGRAMDATA
```

3. 再跑仓库入口，而不是直接怪罪业务代码：

```powershell
.\connect.cmd -Profile example
.\scripts\run_gates.ps1
```

## 判定逻辑

### 只在 Codex / 包装进程失败

如果新开的 PowerShell 7 正常，而 Codex 或某个包装器内失败，优先判定为当前父进程环境不完整：

- 先比较当前进程和新会话中的环境变量差异
- 优先从新会话重跑命令，或在当前进程补齐环境后再跑
- 不要先改仓库逻辑
- 未经用户在当前任务中明确确认，不要重启、停止、杀掉或自动拉起 `Codex App` / `codex`

### 新的管理员 PowerShell 也失败

如果在新的管理员 PowerShell 中，同样的 Python / Node 探针也失败，再按系统级网络栈异常处理：

```powershell
netsh winsock reset
netsh int ip reset
ipconfig /flushdns
shutdown /r /t 0
```

只有这一步也需要时，才把问题升级为系统级修复；不要在仅 Codex 失败时直接重置 Winsock。

## 建议留痕

最少记录以下证据：

- 失败命令
- 失败时所在父进程（Codex、CI、PowerShell、其他 wrapper）
- 缺失或异常的关键环境变量
- 新开的 `pwsh` 是否能复现
- `python -c "import asyncio"` 与 `node -e "console.log('node ok')"` 的结果
- 如已运行仓库入口，再记录 `connect.cmd` 或 `scripts/run_gates.ps1` 的关键输出
