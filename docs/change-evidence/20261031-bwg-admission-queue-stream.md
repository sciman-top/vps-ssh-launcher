# BWG admission：排队期 SSE 早头、心跳与流内终态（2026-10-31）

## 目标与根因

用户从 ChatGPT desktop 经 CPA client key 调 DeepSeek 时反复看到
`exceeded retry limit, last status: 429`、`idle timeout waiting for SSE`、
`stream disconnected before completion` 和 high-demand 文案。

24h journal 的只读聚合给出直接证据：

- 5313 个完成结果里有 51 个排队超过 25 秒；桌面客户端在两个约 30.3 秒和
  45.0 秒的簇附近放弃请求（499 / `downstream_gone`）。
- admission 在拿到 lane 槽位前不返回响应头或任何字节；CPA 的 15 秒 SSE
  心跳也只有请求进入 CPA 后才开始。因此长 FIFO 等待会被客户端当作静默断网。
- 2026-10-31 14:47:47Z 更新后的 admission 仍记录了一条
  `lane=chatgpt-oauth reason=queue_timeout retry_after=10 waited_ms=120000`：
  客户端白等 120 秒，之后才拿到可重试状态。

## 变更

- `scripts/remote/cpa-admission.py`（source sha256 `e991acd9…`）：
  - 对 `stream:true` 且属于共享车道的 generation 请求，在 `acquire()` 前提交
    `200 + text/event-stream`、`Cache-Control: no-store` 和 close-delimited
    framing；立即获得槽位的成功流不额外插入正文。
  - 排队期每完整 15 秒写一帧 `: keepalive`，并通过连接存活探针让
    `downstream_gone` 及时归还未入场的队列位置。
  - 排队拒绝、`queue_timeout`、上游非 SSE/失败和截断 SSE 统一以流内
    `response.failed` 结束；chat-completions 使用 error 帧后接 `[DONE]`。
  - 非流式请求和 pass-through 路由保持原 HTTP 状态语义。
  - 同一候选包含已审查的数字型 token/cache journal 遥测，字段只写入
    `usage_in/usage_cached/usage_out`，不记录正文。
- `scripts/remote/cpa-admission-integrity-pin.txt`：随候选
  sha256 `e991acd9…` 原子轮换。
- `outputs/deploy-admission-queue-stream-20261031.ps1`：仅在
  `04:00-06:00 +0800` 放行；维护锁、临时排空、代码+pin 原子替换、
  readiness/完整性复验和失败回滚在同一事务内完成。

仓内提交：`7f19bdd`（实现与测试）、`9e19f71`（runbook 契约）。

## 已验证

- `pytest tests/test_cpa_admission.py -q`：274 passed。
- 新端到端用例证明占用唯一 lane 槽位时，第二个流在入场前已收到
  `X-CPA-Admission-Reason: queue_stream` 和 50 ms 测试心跳；
  `queue_timeout` 以 `response.failed` 返回；坏 SSE 补失败终态。
- 完整 `scripts/run_gates.ps1` 首轮只失败于并行会话尚未提交完的
  route-manifest oracle；该组测试补齐后两条定向门禁均通过。
- `deploy-admission-queue-stream-20261031.ps1 -DryRun`：
  `ADMISSION_PREFLIGHT_OK lanes=4 holds=null`；
  `DRY_RUN_OK baseline=970a8ad2… candidate=e991acd9… low_peak=(skip)`。

## 投影状态与边界

源码、测试、dry-run 已验证；生产仍在运行 `970a8ad2…`，本轮修复尚未投影。
事务只允许在 `04:00-06:00 +0800` 执行，避免重复本轮事故中活跃会话被重启
切断。部署后须单独记录 `DEPLOY_OK`、restart readiness、doctor 与自然流量；
源码测试不能替代真实 Desktop 完整回合或长期稳定性验收。

回滚：事务备份
`/opt/cliproxyapi/backups/<UTC>-admission-queue-stream-20261031/` 中的
`cpa-admission.py` 与 `cpa-admission.sha256`；恢复后重启 `cpa-admission`
并复验 `/healthz`。Git 回滚不能替代远端代码与 pin 的成对恢复。
