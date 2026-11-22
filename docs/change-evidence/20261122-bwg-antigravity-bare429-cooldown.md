# BWG Antigravity bare-429 stop→stop-and-cooldown（affinity 死锁解除）

**日期**: 2026-11-22
**判定**: ACCEPTANCE_RESULT=PASS（审计+探针+门禁全绿；规则实弹指纹留观察项）
**回滚**: pin/config/apply-script 三处的 `stop-and-cooldown` 逐处还原为 `stop`（git revert ebdc298 + config 同步 + 重投影），或临时还原 `transient-error-cooldown-seconds` 不受影响。

## 事故指纹（14:45–15:06Z 北京）

Codex 桌面端多模型连环 `stream disconnected: Antigravity model X is cooling down; retry after 57s`。admission healthz 全空（`cooldown_active=false`/`model_cooldowns={}`/risk 52B）+ journal 三连 `lane_admit → upstream 429 capacity=true retry_after=absent（~700ms）→ lane_reject reason=model_cooldown retry_after=59/57`。admission 无辜，拒绝源在上游 CPA core。

## 根因（四层互锁）

1. **上游真烧尽**：`b7177976`（argon/hydrogen/sonnet/flash 桶）与 `gantasalabojes`（flash 桶）429 `RESOURCE_EXHAUSTED`（裸体无 details）。02:11Z–06:45Z 无任何 CPA 流量经过这两账号=烧灼在 CPA 之外；同账号 argon 桶可健康=按模型桶烧；烧灼窗口 ~30-60min 自愈。
2. **裸 429 `stop` 规则**（11-21 硬化）：不冷却烧尽账号、不 rebind。11-21 前提"裸形=WAF 标记、真配额必带 details"被今日字段证据推翻：同 payload（同 client_request_hash）06:53 在健康账号 200，烧尽账号同体 429。
3. **session-affinity-ttl 6h** 逐模型钉死会话在烧尽账号。
4. **互锁**：admission 60s 模型阶梯挡快速重试 → 放行时核心 30s 瞬态块已过期 → cache hit → 再 429；`max-retry-credentials=1`（11-21 反扇出裁决）禁同请求换号 → 死循环至 TTL。

## 修复（ebdc298，另一会话 15:18 提交并部署；本会话收尾）

- 裸 429 规则 `action: stop → stop-and-cooldown`（`scripts/remote/cpa_policy.py` + `scripts/remote/cpa-guardrail-apply.sh`）：请求仍在第 1 个凭据上停止（反扇出/防 payload 重放不变），但 (auth, model) 进入冷却且 `session-affinity-rebind-on=quota` 可把会话移到健康账号（上游语义锚：`ErrorCodeForceCooldown` → `shouldSkipCredentialCooldown=false` → 429 → rebind）。
- 语义代价（已记录）：若真出现 message-less WAF 标记形，被标记 payload 会以每请求一账号的速度缓慢扩散（每号 30s 冷却自愈）——字段未观测过该形，留 watch。
- 本会话补齐（ba3b5d8）：同步 cpa_policy.py 与测试的注释语义（ebdc298 改 action 未改注释）。
- bwg 端 15:16/15:26 三次容器重启使 config 生效并清空 affinity 缓存（顺带解除存量死锁会话）。
- 投影欠账由本会话经 `cpa_bwg_guardrails.ps1 -Apply` 清偿（cpa_policy.py 远端旧版曾致 `POLICY_FAILED rule drift` + `projection-drift MISMATCH`）。

## 验收

- `cpa_bwg_guardrails.ps1 -Observe`：修复前后对比 `DOCTOR_CONTRACT_OBSERVE_FAILED` → **`DOCTOR_CONTRACT_OBSERVE_OK`**（`semantic-policy=OK`、`projection-drift MATCH`）。
- 池健康探针（直探 8317、全新 X-Session-Id、绕 admission）：argon ×4 全 200（覆盖 4 个 G4 账号，含曾烧尽的 b7177976）+ flash ×1 200。
- 门禁：`run_gates.ps1 -RunParallel` **1855 passed / 30 skipped（平台条件）/ 896 subtests**（覆盖 ebdc298+ba3b5d8）。
- 规则实弹指纹（下次自然烧灼时核对）：上游 429 后同会话重试应现 `cache miss, new binding`（rebind）而非 `cache hit`；轮转在冷却期内跳过烧尽账号。
- ChatGPT desktop E2E：容器重启清缓存+账号自愈后交用户复测（本证据归档时待用户确认）。

## Watch items

- 方案 B（`transient-error-cooldown-seconds` 30→90）仅在 A 落地后仍见"轮转落回烧尽账号"的显著残余失败率时再议（10-31 有 codex 车道 pin 理由，全局旋钮不动）。
- b7177976 烧灼源在 CPA 之外（外部消费或试用分带），待排查。
- 生产 fork 的 rebind-on-429 与上游 HEAD（f5c5681a）存在代差嫌疑，下次 fork 升级核对。
