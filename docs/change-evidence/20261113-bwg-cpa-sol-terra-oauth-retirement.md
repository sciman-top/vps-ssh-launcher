# 2026-11-13 bwg CPA：gpt-6.1-sol / gpt-6.1-terra OAuth 车道退役

## 背景与根因

- 上游于 2026-11-12 20:50 UTC 起对 `gpt-6.1-sol` / `gpt-6.1-terra` 一律回
  400 `model_not_found`（"The 'gpt-6.1-sol' model is not supported when
  using Codex with a ChatGPT account."，真实上游往返 300-700ms）；
  2026-11-12 20:31 UTC 同名仍 200。定性：上游将两名从 ChatGPT-Codex
  计划摘除，与用量无关（近 7 天该 lane 200 数 0,0,0,0,0,5,0）。
- 核心对连续 model_not_found 合成本地 503+Retry-After（4ms 响应），
  admission 镜像为 lane 级冷却（两段 ~12h 窗，客户端侧报
  "retry after 25902s"）。等待到期无效：探针仍打两名死名 → 再 400 →
  再进窗。
- 账号本身健康：2026-11-13 直探 8317 `gpt-6-luna` 200/finish=stop。

## 变更内容（提交 d2f2eb7 / 2f782e1 / 19a329d）

1. **d2f2eb7**：两名从 `cpa-admission.json` lane models、
   `cpa_provider_routes.json`（oauth_routes codex + oauth_settings.codex）
   摘除；`oauth_exclusions` 加墓碑（`codex_api_key_exclusions` 已有）；
   guardrail doctor/apply 的 expected 字典与 `CODEX_OAUTH_ROUTES_READY`
   文案对齐；`cpa_catalog_expectations.py` 预言机与 6 个测试文件 fixture
   对齐；两本 runbook 活状态段更新。Full gates 1343 passed。
2. **2f782e1**：apply 的 `admission_unit_contract` 检查对齐 broker 启动链
   ——998d3a6 把 py/json 绑定改入
   `VPS_SSH_LAUNCHER_ADMISSION_TARGET/CONFIG` 环境变量导出，旧字面量 grep
   失配导致每次 `-Apply` 在契约门回滚（首次部署尝试即撞，两段
   PROJECTION_HASH_VERIFIED 后 ROLLBACK admission_unit_contract）。
3. **19a329d**：doctor 心跳判定不再对 `deferred` 无条件 FAIL——updater
   exit 30/76/77 是审阅后的让位决定，合法性白名单已钉死组合；保留时效
   STALE 检查。修复前自 2026-11-13 04:12 UTC 首个插件 canary 让位起每次
   doctor 必红。

## 部署与验收（-Apply 事务 2026-11-13 16:01 UTC）

- `GUARDRAILS_APPLIED`、`READY_STATUS=200`、
  `BACKUP_DIR=/root/cpa-guardrails-backup-20261113T160111.606180930Z`。
- doctor 严格模式（心跳修复后）：`DOCTOR_CONTRACT_OK`（exit 0）、
  drift 16 项全 MATCH、`MODEL_IDS_UNKNOWN=none`、
  `catalog_oauth_missing=none`、`admission-health=OK`。
- 8318 回环单发（一发不重试）：
  - `gpt-6-luna` → HTTP 200 / finish=stop / 1.80s / 回显模型正确
    → **LIVE_ACCEPTED**。
  - `gpt-6.1-sol` → HTTP 400 / 0.005s / `unknown provider for model`
    ——死名本地秒拒，零上游往返、零 lane 影响（lane_reject=0）。
- healthz：`lanes=chatgpt-oauth:2`，`cooldown_active=false`。

## 过程事件（如实记录）

1. 旧冷却态跨重启存活：-Apply 不清 lane state，chatgpt-risk.json 持久化
   `not_before` 使 lane 解停后仍被旧窗钉 16383s。处置：stop → 删
   chatgpt-risk.json → start。
2. **发现并行 request-guard 重构的崩溃循环缺陷**：新
   `cpa-admission.py:2508`（LaneState.__init__）对 state 文件无条件
   `read_text`，文件缺失即启动崩溃循环（旧版 fail-open）。删除后每
   2.5s 重启崩一次；写回良性 v1 空状态（`{"hold_reason": null,
   "not_before": 0, "version": 1}`）即恢复。**挂账**：state 文件缺失
   容错属 cpa-admission.py reviewed-deploy 面，由 request-guard 车道
   修复，本事务不改其代码。
3. 最终 Full 门禁 2 个挂点（test_cpa_status_view.py 脱敏测试）为并行
   会话同工作树实时改名（→ test_cpa_status.py）的churn，与本次变更集
   无关；本切片证据链 = d2f2eb7 内容 Full 1343 绿 + 2f782e1/19a329d
   各自定向复跑绿（含双文件双序 79 passed）。

## 回滚入口

- 仓库：revert 三个提交；远端：按
  `/root/cpa-guardrails-backup-20261113T160111.606180930Z` 反向恢复
  （apply 事务自带 restore_all 机制已两次演练验证）。
- workbuddy-oauth 6008 积分池 14 凭据全灭属账号面事件，与本变更无关，
  待面板购 add-on/补号。

**更正（2026-11-14，用户质疑"不可能 14 个全灭"触发实测复核）**：上条
"账号面事件/购 add-on"结论**错误，作废**。同日 16:49 UTC 单发探针
200/finish=stop（usage_in=9），由 CN 凭据 `workbuddy-297fa82d-*` 服务
（auth WRITE 事件同时刻自证）；14 凭据全部注册在役（apply 后管理面逐凭据
查询全 200）。6008="Credits exhausted"（本仓上游件 #7313）是 UTC+8 日窗
内的日额度烧尽签名——`daily_credits_cap=300` 免费池按日烧穿是常态形态，
11-11 credits 普查已证 CN 6/7 有余量、CN 侧纯 6004 日窗非额度问题；
16:00 UTC（北京零点）日界翻过+apply 重启后 credits 快照 fail-open
（issue #70）叠加，车道零账号动作自愈。官方 Error-Code 文档（workbuddy.cn）
6xxx 族口径=请求频率受限可恢复，6008 官方未列出。正确处置=等日界/窗口
复位，无需购 add-on/补号。复位单因（逐凭据 6004 reset 时刻 vs UTC+8 日界
vs fail-open）未拆分，不影响操作结论。
