# BWG CPA 配额重置后的 admission 恢复

日期：2026-10-05。范围：BWG CPA admission 运行时及恢复入口。
本文只记录脱敏判据、源码哈希和服务状态，不包含凭据、完整 capability
路径、用户请求正文或原始错误正文。

## 事故与根因

用户已实际重置上游配额，但 admission 仍保留旧的 Retry-After 截止时间，
使 Sol/Luna 请求在本地快速返回 429。旧截止时间为北京时间
2026-10-05 20:59:33，不能据此认定上游仍未恢复。

2026-10-05 12:15:45.570621 UTC（北京时间 20:15:45），在 admission
空闲且持有验证锁时，通过 VPS 本机 CPA 进行一次 Sol 验证，零重试：
HTTP 200，3.205 秒，完整生成预期输出，无 Retry-After，结果
`RESET_VERIFICATION=RECOVERED`。

12:24:53 UTC 的只读复验中，旧 admission 仍有 2081 秒冷却、
`failure_streak=2`，无 inflight/pending/probe；验证成功后没有新的
OAuth 容量失败事件。该组合证明上游已经恢复，而本地保护状态仍过期滞留。

## 修复契约

- `scripts/remote/cpa-admission.py` 增加仅回环可访问的
  `POST /admin/recover-after-reset`，拒绝转发头和非允许的模型/参数。
- 恢复必须明确声明配额已重置，并携带当前 `failure_generation`；
  lane 忙、generation 不匹配或恢复预算不足时拒绝。
- 每个运行中的 lane 最多每 300 秒允许一次独占恢复验证，单次上游请求、
  零重试。验证期间保留原冷却截止时间。
- 只有 JSON 完整完成、无错误且包含实际预期输出，才能清除同一
  failure generation 的冷却。仅 HTTP 200、空输出、未完成响应、
  认证错误和传输失败均不能解除保护；新的容量失败优先。
- 普通请求仍遵守上游 Retry-After。该入口依赖显式确认，
  不自动感知外部配额重置，也不提供定时 OAuth 探测。
- 健康状态增加 `cooldown_active`、`failure_generation`、
  `server_retry_after_remaining` 和 `reset_probe_in`。

本地改动包括 admission、`scripts/cpa_recovery_workflow.ps1`、
`test_cpa_admission.py` 及 `docs/runbooks/cpa-recovery-workflow.md`。

## 本地验证

最终统一门禁：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1
```

退出码 0：compileall 通过；438 passed、1 skipped、338 subtests passed；
两项 Bandit、Ruff check/format 和 mypy 均通过，mypy 检查 35 个文件。
默认跳过的是真实 SSH integration，远端验证另行显式 opt-in。
恢复相关 focused suite 为 8 passed、37 deselected。

测试覆盖 generation 竞争、并发排他、单次验证预算、失败保留保护、
未完成/错误嵌在 200 响应中、管理入口访问约束、固定验证请求及普通 429。

## 远端部署与来源

本次采用通过门禁的工作区 admission 字节进行单文件投影。
部署前观察到的本地 HEAD 为
`00f375e4d286c3b5418cea6962340046ba5c865c`。
修复尚未提交；本次部署不是 guardrails 的 clean HEAD `-Apply`。

| 对象 | SHA-256 |
| --- | --- |
| HEAD admission / 原远端文件 | `2183d13caac7ad756cda116702387605c16e75244930dc58848e14772ea58f81` |
| 已验证工作区 admission（LF）/ 部署后文件 | `0323c2b62e793e5fa78a2adc954d1ba111f67fa4e706ad398ed10cd176e77115` |

2026-10-05 12:29:57–58 UTC（北京时间 20:29:57–58）部署完成：

- 上传暂存文件，核对 baseline、HEAD、暂存和源文件稳定性；
  在维护锁内执行备份、原子替换和运行状态检查。
- 部署前要求所有 lane 空闲、OAuth 原 failure streak 未改变、
  自上游恢复验证以来无新增容量失败；回滚分支已准备。
- 备份目录：
  `/root/cpa-admission-reset-backup-20261005T122957Z-0ae891b8`，
  保存原文件、元数据、前后健康状态和 `rollback.sh`。
- 仅重启 `cpa-admission.service` 以加载代码并清除已证实滞留的内存冷却。
  `COOLDOWN_BEFORE=1776`，`COOLDOWN_AFTER=0`；
  新 PID 为 382651，启动时间为 12:29:57 UTC，状态 active。
- `PORTS_8317_8318_8443=PASS`，`ADMIN_GET_REJECTED=PASS`，
  `DEPLOY_RESULT=PASS`，`PROJECTION_SSH_EXIT=0`；未触发自动回滚。

CPA 配置、routes、updater、admission 配置和 unit、Nginx 配置的
哈希保持不变。Nginx/fail2ban PID 和 Docker 容器 ID、启动时间、PID
保持不变。公网 8443、原 capability 路径和认证方式保留；
本地 Cockpit、Desktop 和 API-bearing 进程未停止或重启。

## 公网完整生成验收

使用 Desktop 当前 provider 的实际配置，从用户电脑发往公网 HTTPS
8443 Responses 路由；私有读取并核对认证信息，未输出 URL capability
后缀或凭据。每个模型请求预算 1、重试 0，检查实际生成内容，
无原始响应正文输出。

| 模型 / 方式 | 请求 UTC（2026-10-05） | 北京时间 | HTTP | 耗时 | 结果 |
| --- | --- | --- | --- | --- | --- |
| `gpt-6.1-sol` / JSON | 12:31:16.025728 | 20:31:16 | 200 | 2.116 秒 | 完整完成、预期输出正确、无 Retry-After |
| `gpt-6-luna` / SSE | 12:32:04.887992 | 20:32:04 | 200 | 1.916 秒 | `response.completed`、预期输出正确、无 error event / Retry-After |

判据为 `CONTROLLED_PUBLIC_SOL_REPLAY=PASS` 和
`CONTROLLED_PUBLIC_LUNA_STREAM_REPLAY=PASS`。
SSE 检查包含完成响应及实际内容，排除了 failed/incomplete/error 事件。

## 部署后复验与 doctor 差异

12:33:05.277591 UTC 执行正式 `RecoverAfterReset` 工作流：
`COOLDOWN_BEFORE=0`，`RECOVERY_RESULT=NOT_REQUIRED`，
没有额外生成请求。此次没有活跃冷却可供管理入口解除；
实际冷却清除来自本次 admission 重启，恢复入口的清除语义由本地测试验证。

12:33:08.073915 UTC 的 fresh acceptance：

- 所有 lane 的 cooldown、failure streak、inflight、pending、probe 均为零，
  `retired_readers=0`，健康状态 `ok`。
- 部署后 journal 中 Sol/Luna 各有一次成功上游结果，容量失败均为零；
  `RESET_PROBE_EVENTS_AFTER_DEPLOY=0`。
- 运行文件哈希匹配已验证部署字节，`ACCEPTANCE_RESULT=PASS`。

12:33:28 UTC 的严格 doctor 退出码为 1，报告
`DOCTOR_CONTRACT_FAILED`。唯一显式失败项：

```text
drift=cpa-admission.py MISMATCH
projection-drift-cpa-admission.py=FAIL
POST_DEPLOY_STRICT_DOCTOR_EXIT=1
```

doctor 以已提交 HEAD 的旧 admission 哈希为真源，而实际运行的是尚未提交的
修复哈希，因此此差异符合本次投影方式。其余投影项 MATCH，服务、端口、
路由、认证、配置及语法契约通过。heartbeat 的 MISSING 为观察项。
未降低 doctor 检查标准，未将本次严格 doctor 记为 PASS。

## 后续配额重置操作

确认上游配额已经重置后，在仓库目录执行：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = '1'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_recovery_workflow.ps1 `
  -Mode RecoverAfterReset -QuotaResetConfirmed -RecoveryModel gpt-6.1-sol
```

没有冷却时返回 `NOT_REQUIRED`，不发送生成验证。存在冷却且符合预算与
空闲条件时，只执行一次验证；只有完整成功才返回 `RECOVERED`。
失败保留保护。具体参数、拒绝情况见恢复 runbook。

## 回滚

必要时通过现有严格 SSH 入口在 BWG 执行：

```sh
bash /root/cpa-admission-reset-backup-20261005T122957Z-0ae891b8/rollback.sh
```

脚本核对当前新文件与备份旧文件哈希，持有同一维护锁，原子恢复文件及
属主/权限并重启 admission。之后应复验健康状态、8317/8318/8443 和 doctor。
该脚本恢复原运行文件，不能恢复重启前的内存冷却倒计时。

## 证据层级与结论范围

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 工作区修复通过统一门禁；尚未提交 |
| `filesystem_projected` | BWG admission 文件匹配已验证工作区 SHA-256；与旧 HEAD 存在已记录差异 |
| `host_loaded` | 新 admission PID / schema、健康状态与端口复验通过 |
| `controlled_live_replay` | 实际公网 Sol 完整生成及 Luna 流式生成均通过 |
| `natural_live_accepted` | 留待用户正常 Desktop 会话观察确认 |

旧滞留冷却已清除，无需等到北京时间 2026-10-05 20:59:33。
当前受控生成已恢复；该证据不保证长期吞吐，也不排除将来的真实上游 429。
当前仓库已有提交 `d74ddcd`（admission 配额重置恢复入口与 Cockpit/CPAMC
事件桥）；本轮未再创建分支或追加提交。

## 管理页语义与自动桥接收口

对当前 `127.0.0.1:18317/management.html#/quota` 做了只读 bundle 与访问链核对：

- 配额页“重置额度”调用上游
  `wham/rate-limit-reset-credits/consume`，只消费上游主动重置权益；它不清理
  CLIProxyAPI 的 `credential_quota` 本地冷却。
- 认证文件页“清除冷却”调用
  `POST /v8/management/routing/cooldown/reset`，body 只带 `auth_index`；它清理
  单凭据本地路由冷却，但不会恢复上游额度。
- `18317` 是当前用户到 BWG CPA 管理面的 SSH 转发。CPA 容器访问日志会记录该
  管理 endpoint 的成功状态与路径，不包含管理 key 或请求正文；当前 24 小时日志
  只观察到一次 401 失败尝试，没有新的成功清除事件。

仓库新增的 `cpa_reset_event_bridge.ps1` 同时监听 Cockpit 的成功配额消费行和
BWG CPA 的成功管理审计行。配额事件使用 `reason=quota_reset`，管理清除事件使用
`reason=cooldown_reset`，两类事件都只触发一次 `RecoverAfterReset`：它不携带
管理明文 key、不直接清除 CPA credential cooldown；认证文件页的 UI 操作负责
清除该冷却，恢复工作流负责在 admission 层以零重试验证并清除旧 Retry-After。
首次启动建立本地与远端历史基线，未重放 19:47 的旧配额事件，也未产生新的远端
恢复请求。

2026-10-05 13:22 UTC（北京时间 21:22）注册并启动当前用户计划任务
`VPS-Cpa-Reset-Bridge`：隐藏、`Interactive`、`Limited`、`IgnoreNew`，登录时启动。
任务进程保持运行；Cockpit Tools PID、18317 SSH 隧道和 CPA 运行态未停止或重启。
状态文件显示 `last_result=BASELINE_INITIALIZED`、远端基线为空、没有
`DISPATCHING`/`MANAGEMENT_RESET_DISPATCHED` 行。

2026-10-05 13:26 UTC 的 strict doctor 新鲜读数：CPA 容器 `running`、restart=0，
`admission-health=OK`，`cooldown_state=none`，OAuth 目录缺失项为 `none`；严格总结果
仍为 `DOCTOR_CONTRACT_FAILED`，唯一已知阻断是未提交工作区 admission SHA 与 HEAD
旧真源哈希的 `projection-drift`。这与此前已加载的修复字节一致，不能标成 doctor
PASS，待 Git 正式收口后再复验。

## Git 收口后的当前复验

提交 `d74ddcd` 已把恢复入口、工作流参数和事件桥纳入 HEAD。2026-10-05
13:43:27 UTC（北京时间 21:43:27）重新执行默认 strict doctor，退出码为 0，
返回 `DOCTOR_CONTRACT_OK`。当前 BWG 运行文件
`/opt/cliproxyapi/cpa-admission.py` 的 SHA-256 为
`adc0e105742bea4296484abb5b3d697320d91e3ee07f74aaf0c8b8e0243642e6`，与
HEAD blob 和 `projection-drift` 的 `MATCH` 一致；`cpa-admission.service` 为
enabled/active，CPA 容器 running、restart=0，admission health、8317/8318/8443
和公网随机路径契约均通过。

同次读数为 `cooldown_state=none`、`catalog_oauth_missing=none`、
`luna_state=available`。24 小时历史 journal 仍汇总 10 次 `cooldown` 拒绝，
因此这里只确认当前没有滞留冷却，不把历史计数解释成上游配额已恢复或长期无
429。事件桥计划任务仍为 `Running`，本机状态仍为
`last_result=BASELINE_INITIALIZED`，没有新的恢复派发。

## 当前证据层级

| 层级 | 当前判定 |
| --- | --- |
| `repo_verified` | 提交 `d74ddcd` 已包含恢复入口、工作流和事件桥；PowerShell AST、`git diff --check` 通过；此前 Python 完整门禁 438 passed/1 skipped 可复用 |
| `filesystem_projected` | 本机状态文件、脱敏日志和计划任务已投影；首次基线行为已读回 |
| `host_loaded` | 当前 strict doctor=`DOCTOR_CONTRACT_OK`；BWG admission SHA 与 HEAD MATCH，服务 active，CPA 与 admission 健康读数通过 |
| `controlled_live_replay` | 既有公网 Sol/Luna 完整生成验收保持通过；本轮没有为验证而消费新的上游额度或重放恢复请求 |
| `natural_live_accepted` | 仍需用户在正常 Desktop 会话中观察；自动桥接安装不等于自然会话验收 |
