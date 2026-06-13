# Governed Runtime Batch Validation

本文件汇总 `vps-ssh-launcher` 当前可引用的批量验证基线，避免把“代码已经变更”和“现场已经验证”混成一个口径。

## 当前基线

- 本地完整门禁入口：`scripts/run_gates.ps1`
- 真实 SSH 集成：默认跳过，需显式开启
- 远端脚本默认姿态：
  - `scripts/google_ipv4_routing.ps1`：只读，`-Apply` 才会执行远端修复脚本
  - `scripts/vasma_kernel_update_cron.ps1`：只读，`-Apply` 才会写远端 wrapper / cron
  - `auto_install.py`：默认阻断，必须显式 `--execute`

## 最新已归档验证

| 证据文件 | 范围 | 结论 |
|----------|------|------|
| `docs/change-evidence/20260528-bwg-live-function-maintenance.md` | 本地完整门禁、`bwg` 的 live `check/run/integration`、单 profile `RunAll`、Xray 周更 wrapper、Google IPv4 只读复查、系统维护 | 通过 |
| `docs/change-evidence/20260528-zz-singbox-system-maint.md` | 本地完整门禁、`zz` 的 live `check/run/integration`、sing-box 周更 wrapper、系统维护 | 通过 |

从两份最新现场证据可以提炼出当前可复用的仓库状态：

- `scripts/run_gates.ps1` 在最新现场验证中均通过：
  - `pytest`: `82 passed, 1 skipped, 22 subtests passed`
  - `unittest`: 通过
  - `contract:powershell-policy`: 通过
  - `pip check` / `pip-audit` / Bandit / Vulture / Ruff / Mypy / Pyright：通过
- `bwg` 与 `zz` 的 launcher 主链路都已验证：
  - `ssh_tool.py ... check`
  - `run.cmd -> connect.cmd -> connect.ps1 -> ssh_tool.py`
  - 显式 real SSH integration
- 两台 VPS 的代理内核周更 wrapper 均已按各自主机角色约束：
  - `bwg` 只保留 Xray wrapper / cron
  - `zz` 只保留 sing-box wrapper / cron

## 批量验证边界

本仓的“batch validation pass”不等于“允许默认执行一切远端变更”。

以下能力必须继续保持显式 opt-in：

- 真实 SSH 集成
- `google_ipv4_routing.ps1 -Apply`
- `vasma_kernel_update_cron.ps1 -Apply`
- `/etc/v2ray-agent/auto_update_xray.sh` 或 `/etc/v2ray-agent/auto_update_singbox.sh` 的手动触发
- `python .\auto_install.py --execute`

高风险远端动作仍然必须逐台执行；完成第一台后，要先复验服务、配置和端口，并等待用户确认，再动第二台。

## 建议刷新步骤

如果要刷新本文件引用的“最新状态”，最少应补下面这些证据：

1. 本地完整门禁：

```powershell
.\scripts\run_gates.ps1
```

2. 需要 real SSH 时，显式开启集成：

```powershell
.\scripts\run_gates.ps1 `
  -RunIntegration `
  -IntegrationProfile "<profile>" `
  -IntegrationCommand "printf vps-ssh-launcher-integration" `
  -IntegrationExpected "vps-ssh-launcher-integration"
```

3. 需要远端运维时，先跑只读复查，再决定是否 `-Apply`：

```powershell
.\scripts\google_ipv4_routing.ps1 -Profile "<profile>"
.\scripts\vasma_kernel_update_cron.ps1 -Profile "<profile>" -Kernel "<xray|sing-box>"
```

4. 把本次执行命令、关键输出、风险边界和回滚路径写入新的 `docs/change-evidence/*.md`。
