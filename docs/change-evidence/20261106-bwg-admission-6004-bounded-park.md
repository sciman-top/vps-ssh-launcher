# 2026-11-06 BWG admission：WorkBuddy 6004 模型冷却封顶

## 触发

ChatGPT desktop 用 CPA key 调 `deepseek-v4.1-flash` 报
`stream disconnected before completion: Shared upstream lane workbuddy-oauth is
cooling down or busy; retry after 22210s`；同一 workbuddy 账号在本地 WorkBuddy
客户端正常；同时不会自动切到 OpenCode。

## 根因（只读归因）

1. `workbuddy_6004_retry_after()` 从 6004 响应体解析 `将于 … UTC+8 重置`，返回剩余
   秒数并写进 `_model_server_not_before[model]`。该字段在 `acquire()` 中**先于**任何
   探针/排队判定，因此整段窗口是硬拒绝。
   实测 2026-11-05 18:10:32Z `upstream_result ... status=500 capacity=true
   protocol_result=capacity` → 18:10:35Z `lane_reject reason=model_cooldown
   retry_after=22254`，后续 18:11:13/16/19Z 三次均为 0ms 拒绝。
2. 冷却键是**模型名**而非 provider。`deepseek-v4.1-flash` 是 `pooled_aliases`
   （WorkBuddy `priority: 0` 主、OpenCode `priority: -1` 备），lane 一钉，备份
   provider 同样不可达。
3. 规模：24h 内 6004 共 1761 次，覆盖 11 个 workbuddy 凭据中的 10 个；已存在
   `2026-11-06 12:10:34` / `22:00:53` UTC+8 的长窗口 ⇒ 下一次可能钉 10–20h。

## 仓库变更

- `scripts/remote/cpa-admission.py`：新增 `WORKBUDDY_6004_MAX_COOLDOWN_SECONDS = 300`，
  `workbuddy_6004_retry_after()` 的体解析 reset 改为 `min(remaining, 300)`；
  默认（无可解析 reset）仍为 60s。插件仍持有真正的每凭据 park。
- `scripts/remote/cpa-admission-integrity-pin.txt`：`1894719c…` → `45c5fab6…`。
- `tests/test_cpa_admission.py`：新增 22254s 长窗口 ⇒ 300 的定向断言。
- `outputs/deploy-admission-workbuddy-6004-cap-20261106.ps1`：受控事务（排空、备份、
  原子替换、完整性 pin 同步、失败回滚、离线守卫含 300s 断言）。相对 11-05 版本：
  更新配置代际哈希为 `18a2eda5…`；独占锁改为带截止时间的轮询并输出心跳（`flock`
  不排队，稳态共享锁流会饿死阻塞请求，首次尝试即因此被 120s 空闲超时打断，未发生任何
  变更）；新增 `PREFLIGHT_OK` / `BACKUP_READY` 进度标记。
- runbook：`cpa-admission-risk-audit.md` §4.5 与 `cpa-workbuddy-plugin.md` §4 记录封顶语义。

## 生产事务

- dry-run：`ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK`、
  `DRY_RUN_OK baseline=1894719c… candidate=45c5fab6… idle=true no_swap=true`。
- 第一次 apply：通过 preflight/排空/备份，在离线守卫处失败并
  `ROLLBACK_VERIFIED`（守卫文案缺少 6004 判别短语，非产品缺陷）；已修正守卫后重跑。
- 最终 apply：`PREFLIGHT_OK`、`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、
  `WORKBUDDY_6004_ADMISSION_GUARD_OK`、`ADMISSION_REGRESSION_GUARD_OK`、
  `DEPLOY_OK pin=45c5fab6…`。
- 备份：`/opt/cliproxyapi/backups/20261105T183644.804708766Z-admission-workbuddy-6004-cap-20261106`。
- 事务只重启 `cpa-admission`；CPA 容器、provider 路由、Nginx、凭据均未改动。

## 投影后验证

- 远端 `cpa-admission.py` = `45c5fab6d5fab9d05f134e368be66db998bd6fb2719c4402a322ed3c9f9923ea`，
  pin 同步；`cpa-admission` active；`/healthz` `status=ok`，四 lane 齐全。
- 严格 doctor：见下方收口复核（提交前报 `cpa-admission.py` 与
  `cpa-admission.sha256` 两处 MISMATCH，属预期，HEAD blob 锚定所致）。

## 收口复核（2026-11-06 02:52 CST，提交 `19218a0` 之后）

- 严格 doctor（HEAD blob 锚定）：`==projection-drift==` 全列 MATCH（含
  `cpa-admission.py` 与 `cpa-admission.sha256`）；`DOCTOR_CONTRACT_OK`、
  exit=0；`oauth_lane=workbuddy-oauth posture=WAIT_UPSTREAM_WINDOW
  hold_reason=none`（无人工 hold，仅等上游窗口）。
- 受控实战验收（8318 回环三探针）：`ACCEPTANCE_RESULT=PASS`。
  - flash 第 1 发：admission 放行，上游真实返回 `500`/6004 信封（仅该模型
    受限、建议切换其他模型）——窗口真实存在的直接证据；
  - flash 第 2 发（立即重试）：`429 model_cooldown retry_after=300`
    （修复前同位置为全窗镜像 ~19900s）；journal 同秒互证
    `upstream_result … capacity=true` → `lane_reject … retry_after=300`；
  - flash-sg：`200`（1.7s），wb 车道与模型轴独立健康。
- 监控锚：08:21 CST 该凭据窗口重开后 flash 自然恢复；今日 12:10/22:00 CST
  尚有其他凭据长窗到期，修复后均只表现为 ≤300s 有界 park。
- 08:32 CST 复核（窗口重开后）：flash 单发 `status=200`、`sse_events=10`、
  `terminal=completed`、`elapsed_ms=1483`、`X-CPA-Admission-Reason=queue_stream`；
  admission 同请求记录 `capacity=false retry_after=absent successful=true`。
  与上一条预测一致，且 03:00–08:30 窗口内 6004=0、`model_cooldown`=0。
- 未决（策略，待用户裁决）：`max-retry-credentials=1` 保持不变；本窗 24h
  `provider=opencode` 请求为 0，wb→oc 自动兜底不可达属该收紧的既知代价。

## 回滚

在同一维护锁与请求排空边界内恢复上述备份的 `cpa-admission.py` 与
`cpa-admission.sha256`，重启 `cpa-admission`，随后跑严格 doctor。不要用 Git revert
代替远端恢复。

## 未解决（需用户裁决）

`request-retry: 0` + `max-retry-credentials: 1` 仍使单请求只触达一个凭据，因此
OpenCode 备份不会被自动选中（24h 内 `provider=opencode` 请求数为 0）。这两个值同时被
`cpa_policy.py` 的期望表与 `cpa-guardrail-apply.sh` 的投影段钉定，改任一值都是策略变更：
放开即恢复 wb→oc 回退，但也重新允许单请求触达第二个 OAuth 凭据（2026-11-06 收紧的正是
这一点）。
