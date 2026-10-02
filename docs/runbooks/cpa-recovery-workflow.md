# CPA capacity / 429 / 慢速统一工作流

本 runbook 把故障归因、Cockpit sidecar 重投影、BWG CPA 投影、重载后验收和低风险受控回放串成一个项目入口：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Audit
```

默认只读。它依次输出：

1. 本机 sidecar 状态与监听拥有者。
2. 最近 4 小时本机 request log 的 `local_gate`、admission queue、fast reject、upstream capacity、dead route、慢速成功等分层归因。
3. BWG 严格 doctor（包括 admission、Nginx、随机路径、槽位 3 和投影漂移）。

## 重投影

### 只投影本机 sidecar

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 `
  -Mode Project `
  -SidecarCandidatePath "$env:TEMP\cockpit-tools-v1.3.65-build-20261002\sidecars\cockpit-cliproxy\cockpit-cliproxy-v135-gate-r3-20261002.exe" `
  -SkipRemote
```

### 投影 BWG CPA

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 `
  -Mode Project -ApplyRemote -SkipRemote:$false `
  -DoctorOutput ".\outputs\cpa-recovery-doctor-$(Get-Date -Format yyyyMMdd-HHmmss).txt"
```

远端 `-Apply` 是显式高风险动作；它沿用 `cpa_bwg_guardrails.ps1` 的备份、源指纹、原子投影、服务复验和失败回滚契约。没有 `-ApplyRemote` 时不会写远端。

sidecar 投影后必须使用 Cockpit 正式重载/启动路径。禁止 `taskkill`、停止 API-bearing 进程或用旧 PID 代替重载证据。

## 验收

```powershell
# 重载后状态验收：磁盘 hash、监听拥有者、持久参数、远端 doctor
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Verify

# 不消费唯一 ChatGPT OAuth 账号的单次受控实战
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode ControlledReplay

# 本地 loopback stub 模拟：验证 45 秒闸门封顶，不访问 CPA/OAuth
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode WaitCapSimulation
```

受控回放固定使用本地 10909 的 `glm-5.3` 非 OAuth lane，单次、无重试。它只证明健康链路没有回归，不证明 Luna/Sol 的 provider capacity、长期配额或自然 Desktop OAuth 稳定性。

`WaitCapSimulation` 使用临时配置、临时端口和永不响应的 loopback stub，占满 scratch sidecar 槽位后观察第 4 个请求在 45 秒预算附近返回 429；它不接触远端 CPA，也不消费 OAuth。

## 归因纪律

- 45 秒和 120 秒桶是等待预算指纹；未与 CPA request-id 和 admission journal 同窗关联前，只能称为 suspected/timing bucket。
- 上游 `capacity=true`、`usage_limit_reached`、`server_is_overloaded` 应降低请求频率并等待恢复，不通过盲目提高并发或重试放大流量。
- 本机 `local_gate` 429、远端 admission 429、Nginx 429 和上游 429 不相加统计。
- `natural_live_accepted` 只能由用户正常 Desktop OAuth 会话观察确认；本工作流不自动发送 OAuth 探针。

## 回滚

- sidecar：使用 `cockpit_sidecar_guardrails.ps1 -Mode Project` 生成的 `.before-project-*.bak`，按 runbook 手工恢复后再走正式重载与 Verify。
- BWG：使用 `cpa_bwg_guardrails.ps1` apply 产生的远端备份和 `docs/runbooks/cpa-manual-rollback.md`，逐台复验 8317/8318/8443、admission health 和随机路径。
