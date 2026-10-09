# CPA capacity / 429 / 慢速统一工作流

本 runbook 把故障归因、Cockpit sidecar 重投影、BWG CPA 投影、重载后验收和低风险受控回放串成一个项目入口：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Audit
```

默认只读。它依次输出：

1. Cockpit 当前生效的 provider 目标与目录关系；`local_gateway` 才检查本机
   sidecar 状态和监听拥有者，`public_gateway`（`fq.sciman.top`）下没有
   10909/14185 是预期状态。
2. 最近 4 小时本机 request log 的 `local_gate`、admission queue、fast reject、upstream capacity、dead route、慢速成功等分层归因，唯一实现是 `cpa_failure_triage.py`。
3. BWG 严格 doctor（包括 admission、Nginx、随机路径、槽位 3 和投影漂移）。

只跑统一归因（不读远端、不投影）：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 `
  -Mode Triage -Hours 4
```

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

`-DoctorOutput` 传入现有 doctor 文件时，workflow 会把同一份远端事实自动传给 canonical
`cpa_failure_triage.py`；不会再出现 doctor 和本机归因各跑一遍但没有关联的情况。

远端 `-Apply` 是显式高风险动作；它沿用 `cpa_bwg_guardrails.ps1` 的备份、源指纹、原子投影、服务复验和失败回滚契约。没有 `-ApplyRemote` 时不会写远端。

sidecar 投影后必须使用 Cockpit 正式重载/启动路径。禁止 `taskkill`、停止 API-bearing 进程或用旧 PID 代替重载证据。

## 验收

```powershell
# 重载后状态验收：按当前 provider 目标选择 sidecar 或 public gateway 契约，
# 然后检查远端 doctor
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode Verify

# 不消费唯一 ChatGPT OAuth 账号的单次受控实战
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode ControlledReplay

# 本地 loopback stub 模拟：验证 45 秒闸门封顶，不访问 CPA/OAuth
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 -Mode WaitCapSimulation
```

受控回放固定使用本地 10909 的 `glm-5.3` 非 OAuth lane，单次、无重试。它只证明健康链路没有回归，不证明 Luna/Sol 的 provider capacity、长期配额或自然 Desktop OAuth 稳定性。

`Verify` 会读取 `cockpit_provider_health.py --json` 的 `configTarget`：目标为
`fq.sciman.top` 时输出 `COCKPIT_SIDECAR_VERIFY=SKIPPED_PUBLIC_GATEWAY`；目标为
`127.0.0.1`、`localhost` 或 `::1` 时继续执行 10909/14185 sidecar 验收。
如果当前 `config.toml` 没有可判定的 `base_url`，工作流会输出
`COCKPIT_GATEWAY_MODE=unknown` / `COCKPIT_PROVIDER_VERIFY=UNRESOLVED`，但仍会继续
执行 BWG doctor 和本机 triage；最终以 `WORKFLOW_RESULT=FINDINGS` 返回，避免桌面目标
漂移把远端 admission 证据遮住。目标为其它主机时同样保持未决并拒绝把它当成已验收路径。

`WaitCapSimulation` 使用临时配置、临时端口和永不响应的 loopback stub，占满 scratch sidecar 槽位后观察第 4 个请求在 45 秒预算附近返回 429；它不接触远端 CPA，也不消费 OAuth。

## 额度主动重置后恢复

admission 的本地退避每 10 秒允许一次由真实请求触发的半开验证。上游明确返回
`Retry-After` 时，普通请求会等待该截止时间；上游之外的额度重置操作不会自动
通知 admission。因此即使上游已恢复，旧截止时间仍可能挡住整条 OAuth lane。

完成真实额度重置后，使用现有工作流发送一次有界恢复验证：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 `
  -Mode RecoverAfterReset -QuotaResetConfirmed -RecoveryModel gpt-6.1-sol
```

如果操作的是认证文件页“清除冷却”，使用对应的信号参数：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_recovery_workflow.ps1 `
  -Mode RecoverAfterReset -CooldownResetConfirmed -RecoveryModel gpt-6.1-sol
```

该模式经严格主机密钥校验连接 BWG，调用仅限 VPS loopback 的
`POST /admin/recover-after-reset`，发送固定、低 effort、1024 token 上限的
Responses 请求。它不重试，也不重启服务；正常的 Audit、Verify 和
ControlledReplay 保持原来的行为。

恢复验证必须匹配当前失败代次，且 lane 没有在途或排队请求。同一 lane 的
主动恢复验证间隔至少 300 秒。只有收到 `status=completed`、无错误并包含预期
`OK` 内容，且期间没有新容量错误，才解除旧退避。HTTP 200 中的错误、未完成
响应、鉴权或传输失败均不能解除退避。

`RECOVERY_RESULT=RECOVERED` 证明本次生成成功且 admission 已解除旧退避；
`NOT_REQUIRED` 表示当前已无退避；`NOT_RECOVERED` 或 `UNVERIFIED` 应保留现场，
检查 CPA 账号冷却与上游错误，不能循环执行。清除 CPA/Cockpit 的本地限额
统计本身不证明 OpenAI 额度恢复。

`/healthz` 额外显示 `failure_generation`、`server_retry_after_remaining` 和
`reset_probe_in`，用于识别上游截止时间与主动恢复预算。恢复管理路径在公网
Nginx 的 `/v1/` 数据面之外；带 `X-Forwarded-*` 的请求也会被拒绝。

CPA 没有与 Cockpit 管理页共享的直接 IPC；当前可用的成功事件源是 Cockpit 本机
app log 和 BWG CPA 容器管理审计日志。普通请求在上游截止时间到达后仍会自动进行
半开验证。

### Cockpit 成功重置事件桥

当前 Cockpit 会在本机 app log 写入精确的成功记录：
`rate-limit-reset-credits/consume, status=200 OK`；CPAMC 成功执行认证文件页的
“清除冷却”后，BWG CPA 容器日志会写入 `200 POST
"/v8/management/routing/cooldown/reset"`。仓库提供一个本机事件桥，同时监听
这两个脱敏事件，然后调用一次带匹配信号的 `RecoverAfterReset`。它不读取或改写 OAuth
凭据、不携带管理明文 key、不会直接清除远端冷却，也不会因失败循环重试。

首次安装并启动当前用户的隐藏任务：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_cpa_reset_event_bridge.ps1 -StartNow
```

桥接器状态和脱敏日志写在 `%LOCALAPPDATA%\vps-ssh-launcher\`。它使用当前
`bwg` profile 的严格 host-key SSH 只读查询 CPA 容器日志，每 30 秒轮询一次；
每条成功事件在调用前持久化一次，进程重启不会重复消费同一事件。首次启动只
建立本地与远端历史基线，不重放旧事件。若 SSH、CPA 或上游验证失败，状态记为
`DISPATCHED_FAIL` 并保持 fail-closed；下一次新的成功事件才会再次触发。移除任务：

管理页 `http://127.0.0.1:18317/management.html#/quota` 中的“重置额度”在
返回成功后会自动触发这条桥；“刷新额度”只读取用量，不触发恢复。配额页的
“重置额度”不会直接清除 CPA admission 或 credential 冷却；认证文件页的
“清除冷却”会先通过 `POST /v8/management/routing/cooldown/reset` 清理指定凭据的
本地路由冷却，然后由事件桥触发一次 `reason=cooldown_reset` 恢复验证。只有恢复
探针收到完整的 `OK` 响应才会清除 admission 旧退避，所以上游仍受限时不会被 UI
操作强行清零。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_cpa_reset_event_bridge.ps1 -Remove
```

## 归因纪律

- 45 秒和 120 秒桶是等待预算指纹；未与 CPA request-id 和 admission journal 同窗关联前，只能称为 suspected/timing bucket。
- 上游 `capacity=true`、`usage_limit_reached`、`server_is_overloaded` 应降低请求频率并等待恢复，不通过盲目提高并发或重试放大流量。
- 本机 `local_gate` 429、远端 admission 429、Nginx 429 和上游 429 不相加统计。
- `natural_live_accepted` 只能由用户正常 Desktop OAuth 会话观察确认；恢复验证仅在显式指定 `RecoverAfterReset -QuotaResetConfirmed` 时发送单次 OAuth 请求。

## 回滚

- sidecar：使用 `cockpit_sidecar_guardrails.ps1 -Mode Project` 生成的 `.before-project-*.bak`，按 runbook 手工恢复后再走正式重载与 Verify。
- BWG：使用 `cpa_bwg_guardrails.ps1` apply 产生的远端备份和 `docs/runbooks/cpa-manual-rollback.md`，逐台复验 8317/8318/8443、admission health 和随机路径。
