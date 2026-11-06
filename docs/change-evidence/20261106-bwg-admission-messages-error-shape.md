# BWG admission Messages 流拒绝协议形状修复部署（2026-11-06）

## 目标与边界

修复 DSH（DeepSeek Harness，内核 pi-ai）会话在正常运行中间歇报
`DeepSeek Messages SSE event type mismatch (MALFORMED_RESPONSE)` 的问题。
代码修复为提交 `1bd226c`（并行 DSH 会话产出，本会话独立复核）：
`cpa-admission.py` 的 `_send_stream_failure` 在 `/v1/messages` 路由改发
协议定义的流内失败信封 `event: error` + `{"type":"error","error":{...}}`
（携带原 code/message/retry_after），`/v1/responses` 与 OpenAI 兼容路由
保持原状。本次部署只换 `cpa-admission.py` + 完整性 pin 两件，不改
config、路由、凭据、并发、cooldown 与缓存键。

## 根因

- DSH 侧判定条件（app.asar 内 pi-ai 源码，逐帧校验）：data JSON 顶层无
  字符串 `type`，或 `event:` 行名与 `type` 不一致，即抛
  `MALFORMED_RESPONSE`。
- 服务侧：wb 车道 6004/容量信号 → admission 模型级冷却镜像拒绝时，
  `_send_stream_failure` 对所有非 `/v1/responses` 路由发 OpenAI 兼容的
  裸 `data: {"error":{...}}` + `[DONE]`（HTTP 200 SSE）。该帧顶层无
  `type`，pi-ai 判为协议违规而非可重试容量信号，`Retry-After` 永远不可
  见，一个拒绝即整轮硬失败。
- 8318 环回 1:1 探针曾确定性复现（bare flash 3/3 返回
  `code=model_cooldown` 裸帧；flash-sg 同窗 3/3 干净流），与
  "只有 `-oc`（opencode 腿，不经 wb 6004 镜像）正常"的用户观测闭环。
- 时间线备注：修复 23:35 由并行会话提交（`1bd226c`），但其提交说明
  "Observed end to end against the deployed gateway" 在本次部署（23:48）
  之前不成立；本部署使该表述首次为真。后续提交说明的证据层级
  （repo_verified / live_accepted）须如实分层。

## 部署方式

- 事务模板 `outputs/deploy-admission-messages-error-shape-20261106.ps1`
  （共享 `Invoke-LauncherRemoteCommand` 传输）：maintenance 锁 →
  requests.lock 独占排水（最长 240s）→ healthz inflight/pending=0 断言 →
  备份 → 原子换装 + pin 轮换 → ExecStartPre 完整性检查 → 仅重启
  admission → 就绪循环 → 换装后契约断言
  （`WORKBUDDY_6004_MAX_COOLDOWN_SECONDS == 300` 不回退）→ 在线回归探针
  （Messages 拒绝必须 typed 信封；健康流亦放行）→ 失败自动回滚。
- DryRun 首跑按设计报 `DRY_RUN_BUSY`（有在途流，未动任何状态）；apply
  拿到安静窗口一次通过。
- 部署 pin `62a39f9562266d78…`（= 提交 `1bd226c` blob，三方一致）。
  备份：`/opt/cliproxyapi/backups/20261106T154800.853167800Z-admission-messages-error-shape-20261106`。

## 验证

- `repo_verified`：`pytest tests/test_cpa_admission.py` 295 passed（部署
  前后各跑一次，部署后针对 HEAD 字节）；全量 gates 由 `1bd226c` 提交说明
  记录 1129 passed / ruff / mypy / bandit clean。
- `host_loaded`：事务内 `ADMISSION_INTEGRITY_OK`×2、
  `MESSAGES_ERROR_SHAPE_CONTRACT_OK`、`systemctl is-active` 通过；
  healthz `status=ok` 且四车道契约齐。
- `live_accepted`：部署后 8318 环回逐帧校验探针 bare flash ×2 与
  flash-sg ×2 全部 6 帧标准 anthropic 事件序列、`message_stop` 收尾、
  0 协议违规（bare flash 冷却恰已解除，wb 车道健康）；事务内探针
  `PROBE_HEALTHY_STREAM_OK`。

## 回滚

- 代码回滚：`git revert` 提交后按同模板机制重投影（事务基线断言会指向
  新 pin，需同步更新模板 `ExpectedLivePin`）。
- 直接回滚：恢复备份目录内 `cpa-admission.py` 与
  `cpa-admission.sha256` 并 `systemctl restart cpa-admission`（事务的
  rollback 路径已验证该流程）。

## 后续观察

- wb 车道今晚另有 `status=500` 瞬时簇（22:49–23:00 UTC，16ms 即拒）与
  6004 模型级窗口（镜像 300s 封顶），属上游供需压力，与本协议缺陷无关；
  若复发，DSH 现在会收到干净的 typed 错误（可按 retry_after 退避）而非
  协议违规硬失败。
- `-oc` 别名腿与本次缺陷无关（不经 wb 6004 镜像），保持现状。
