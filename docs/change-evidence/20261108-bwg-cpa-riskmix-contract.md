# bwg CPA 风控/指标契约修复与封号潮复审（2026-11-08 晚）

**性质**: 仓库修复 + 真实远端写入（admission 源码与 pin 同时轮换）· 脱敏留档
**用户授权**: "全面深度审查…怎样防范封号、限流、降智等的风险/风控？优化吐字缓慢、
缓存命中率等。如有必要，就修复/优化本项目代码。"
**前序**: [20261108-bwg-cpa-handoff.md](20261108-bwg-cpa-handoff.md)（同日交接书，
§9 起为本轮增量）

## 1. 复审结论（一句话）

本栈**没有**账号级封号信号；这一轮真正的缺陷不在"防封策略"，而在**我们自己的
可观测数字是错的**——缓存命中率被算成约一半，导致"命中率只有 45–49%"这个错误
结论被写进 runbook、被当作"还要继续优化缓存"的依据，并且掩盖了"命中率已接近
上限、缓存侧无杠杆"这一真实结论。

## 2. 外部情报（全部为转述，非官方公告；不得外推为本栈已中招）

| 平台 | 本窗口证据 | 对本栈的含义 |
|---|---|---|
| Antigravity/Gemini | **唯一确证波次**：2026-11-05/06 集体 403 PERMISSION_DENIED +
  reason=TOS_VIOLATION，封的是 Cloud Code 服务（同账号 token 刷新仍正常）。官方 FAQ
  明文禁止第三方软件访问 Antigravity | 风险等级最高：有执法波次 + 官方书面口径 + 再犯永久停 |
| WorkBuddy CN/INTL | 2026-10-21 起密集，INTL 先行、CN 11-01 跟上；11140
  "account has been restricted"，无官方文件 | 付费不等于豁免；账号按消耗品建模 |
| OpenAI/Codex | 有封号报告但无"波次"定论；CPA #7319 报区域性批量封 | 归因不可控
  （cyber_policy 与应用层行为无关） |
| Anthropic/Claude | **无近期波次证据**（status.claude.com 近两周无封号公告）；但官方
  文档已写死"禁止代用户走订阅额度中转" | 风险是"何时执法"；lane 现为 RETIRED/tombstone，维持现状 |

本栈新鲜探针（2026-11-08 09:20–19:00 UTC）：48h 内 3 次 antigravity 401 均为瞬时
单发（前后紧邻 200），**无** `invalid_grant` / `account suspended` / `TOS_VIOLATION`；
7d `lane_hold_latched` 仅 2 次（workbuddy，`retry_window_review_required`，已释放）。
结论：**不换号、不清冷却、不动 claude lane**（该开关权属用户）。

## 3. 本轮修复（三处本仓缺陷 + 一处口径）

### 3.1 缓存命中率分母错误（P0，影响所有决策）

- 事实：`ModelTelemetry.snapshot()` 对每个 provider 都用
  `cached / (cached + usage_in)`。而 OpenAI/Antigravity/GLM/Codex 的 `usage_in`
  **本身就是总输入**，`cached` 只是其子集。正确式是 `cached / usage_in`。
- 决定性实验（生产侧，1 万 token 前缀，同 session 连发两次）：

  | 模型 | call1 cached | call2 cached | 语义判定 |
  |---|---|---|---|
  | `gpt-6-luna` | 0 / 9012 | **8832 / 9012** | 总输入（含缓存） |
  | `gemini-3.8-flash-high` | 0 / 9008 | **6960 / 9008** | 总输入 |
  | `glm-5.3-flash` | 0 / 9018 | **8960 / 9018** | 总输入 |
  | `deepseek-v4.1-flash` | **8832 / 9010** | 8832 / 9010 | 非总输入（cached > in） |

- 48h 逐行重算（旧式 vs 修正式）：

  | 模型 | 请求 | 旧式 | 修正 |
  |---|---|---|---|
  | `gpt-6-luna` | 3021 | 49.2% | **96.8%** |
  | `gemini-3.8-flash-high` | 1771 | 49.2% | **96.8%** |
  | `gpt-6.1-terra` | 943 | 48.7% | **95.0%** |
  | `gpt-6.1-sol` | 523 | 47.5% | **90.6%** |
  | `glm-5.3-flash` | 719 | 49.3% | **97.1%** |
  | `deepseek-v4.1-flash` | 2400 | 97.5% | **99.1%** |
  | `deepseek-v4.1-flash-sg` | 629 | 86.7% | **99.0%** |

- 修复：`snapshot()` 采用与 `cpa_natural_traffic_audit.py` 相同的判别式
  （`cached > usage_in` 时为可加形态，否则 `input - cached`）。两条路径从此同源。
- 影响：**"命中率 45–49%，还需继续优化缓存"这个结论作废**。真实是 90–99%，
  缓存侧已无杠杆；任何"为提升命中率改写 prompt 前缀"的提案收益上界 <10%，
  而风险是把 90% 打到 0%——永久否决，不再逐次评估。
- 边界：本轮只改计算式，不改任何 provider 请求字节，不改变命中率本身。

### 3.2 请求路径的持久 hold 没有归因日志（P0，影响故障定位）

- 事实：`LaneState.release(hold_reason=...)` 直接赋值 `_manual_hold`，**不经过**
  `_latch_hold()`，因此不产生 `lane_hold_latched` 行。凡由请求路径（而非
  `record_risk()`）锁存的 hold 都是"静默"的。
- 复现（本地单元级）：release 后 `hold == authentication_required`，caplog **零条**
  warning。
- 后果：lane 开始回 `operator_review_required` 时，无法从 journal 判断是谁锁的、
  为什么锁；只能回头翻可能已被轮换掉的 response dump。
- 修复：release 路径改走 `_latch_hold(hold_reason, "response_signal")`，与其他
  锁存点同源。

### 3.3 Antigravity TOS 撤销被分类成"token 过期"（P1，影响处置动作）

- 事实：2026-11-06/07 波次的信封是 `403 PERMISSION_DENIED` +
  `details[].reason=TOS_VIOLATION`，**没有** "suspended" 之类散文。分类器只匹配
  散文，于是落到兜底的 `authentication_required`。
- 后果：`authentication_required` 的处置是"重新登录"，而 TOS 撤销的处置是
  "停掉并申诉"——两者完全相反。误判会导致在已被执法的账号上反复重登。
- 修复：识别 `error.status` 的 `PERMISSION_DENIED` / `UNAUTHENTICATED`，并把
  `TOS_VIOLATION` / `POLICY_VIOLATION` 加入 ErrorInfo reason 白名单 →
  `account_restricted`。普通 401（无 TOS reason）仍为 `authentication_required`，
  语义不被稀释。

### 3.4 口径修正（非缺陷，但误导）

- `first_text_ms` 缺失时留字面 `None`：24h 内 4123 条 `None` + 3757 条 `na`，
  同一状态两种拼写；看 `na=` 计数的人会低估不可用样本。现统一为 `na`。
- `completed_response_text_state()` 把"只含 tool item"的终态判为 `reasoning_only`。
  Codex 类 agent 每一步都以工具调用收尾，于是某 24h 窗口 **1444 条** chatgpt-oauth
  结果全部显示 `reasoning_only`，把正常 agent 循环读成"没有正文"。现新增
  `tool_call` 类，与 `reasoning_only` / `empty_text` 分开。

## 4. 未采纳/否决项（附理由，避免重复评估）

| 候选 | 判定 | 依据 |
|---|---|---|
| 提高 OAuth lane `max_inflight` | 否 | 并发不改变单请求 prefill 时间，只增同账号瞬时压力 |
| 为"提升缓存命中率"改写 prompt 前缀 | **永久否决** | 命中率已 90–99%，收益上界 <10%，风险是把 90% 打到 0% |
| 升级 v8.0.60 以"修缓存" | 否 | `git log v8.0.59..v8.0.60` 只有 8 个提交，**无任何 cache 改动**；且此前被当作升级理由的 `27762819`/`374056f2` 经 `git tag --contains` 证实**已在 v8.0.59 内** |
| 新增/轮换账号、清冷却、跨 realm 续打 | 否 | 无账号级信号；换号只会扩大暴露面 |
| claude lane 预防性隔离 | 否 | 开关权属用户；本轮无官方波次证据 |
| `reserve_credits` | 否（沿用前判定） | 纯消耗池启用即削容量 |
| `error-logs-max-files` 5 → 更大 | 否 | `/v1/files` 404 探活每 8–15s 一份 dump，24h 内 1908 次 404；加大保留只会让唯一的 5 个槽更快被探活噪声挤掉真错误 |

## 5. 部署事务

- 脚本：`outputs/deploy-admission-riskmix-20261108.ps1`（dry-run 优先，排空事务）
- 事务契约：维护锁 → 排空 `/run/cpa-admission/requests.lock` → 校验 live pin 基线
  → 备份（含 `/var/lib/cpa-admission/*.json` lane state）→ 同时轮换 source + pin
  → 只重启 admission → 复验契约 → 失败自动回滚并复验。
- 新增 post-swap 契约（本轮专属，防"部署成功但跑的是旧逻辑"）：
  1. `cache_hit_ratio` 必须**逐模型等于** `cached/(cached+uncached)`
     （旧式对总输入形态恒 ≤0.5，因此这条能真正区分两套口径）；
  2. `HOLD_LATCH_ATTRIBUTABLE_OK`：源码中 `_latch_hold(hold_reason, ...)`
     至少出现 2 处（record_risk 与 release 各一）；
  3. 既有 `unsupported_content_encoding` 回归守卫保留。
## 6. 部署回执（live_accepted）

| 项 | 值 |
|---|---|
| 事务脚本 | `outputs/deploy-admission-riskmix-20261108.ps1` |
| Dry-run | `DRY_RUN_OK baseline=f48f9d1c… candidate=24959619… idle=true no_swap=true` |
| Apply | `REQUEST_GUARD_EXCLUSIVE_OK` → `ADMISSION_IDLE_OK` → `ADMISSION_INTEGRITY_OK` → 换源+换 pin → `ADMISSION_INTEGRITY_OK` → `TELEMETRY_ACCOUNTING_OK` → `TELEMETRY_CONTRACT_OK` → `HOLD_LATCH_ATTRIBUTABLE_OK` → `ADMISSION_REGRESSION_GUARD_OK` → **`DEPLOY_OK`** |
| 新 pin | `24959619e01dc7b7b36ee51317ce0caa017ad3be999b6a86e7a879ae32e5c19f` |
| 备份 | `/opt/cliproxyapi/backups/20261108T122425.156487909Z-admission-riskmix-20261108`（含 `lane-state/`） |
| 服务 | `active`，`NRestarts=0` |
| 重启时刻 | 2026-11-08T12:24:32Z（PID 219693 → 234919） |
| 门禁 | 本地 full gate 全绿（1171+ passed；bandit / ruff check / ruff format / mypy 干净） |

### 6.1 线上行为变化（决定性证据）

部署后主动生成同前缀双调用（1 万 token 前缀，同 session 连发两次），再读
`/healthz.telemetry.models`：

| 模型 | cached | uncached | ratio | 旧式应为 |
|---|---|---|---|---|
| `deepseek-v4.1-flash` | 17664 | 356 | **0.9802** | 0.9802（形态本就正确） |
| `gemini-3.8-flash-high` | 621944 | 16663 | **0.9739** | 0.487 — 已改变 |
| `glm-5.3-flash` | 8960 | 9076 | 0.4968 | 0.4968（本窗口仅 1 冷 1 热） |
| `gpt-6-luna` | 8832 | 9192 | 0.49 | 0.49（本窗口仅 1 冷 1 热） |

`gemini-3.8-flash-high` 的 0.9739 是这条修复的**活体证明**：旧式对该形态的数学
上界是 0.5，任何 >0.5 的值只能来自新口径。两个约 0.49 的模型窗口内各只有一冷
一热（冷调用 cached=0），算术正确。

`first_text_ms` 拼写：重启后新 PID 的 17 条 `upstream_result` 中 `None` 为 **0**、
`na` 为 15（另 2 条为真实数值）；重启前 12:00–12:24 窗口有 73 条 `None`。
唯一一条落在 12:24:30 之后但带 `None` 的记录属于**旧 PID 219693** 的在途请求，
不是新代码行为。

### 6.2 回滚

```bash
# 维护锁内
BK=/opt/cliproxyapi/backups/20261108T122425.156487909Z-admission-riskmix-20261108
cp -a "$BK/cpa-admission.py" /opt/cliproxyapi/cpa-admission.py
cp -a "$BK/cpa-admission.sha256" /etc/vps-ssh-launcher/cpa-admission.sha256
systemctl restart cpa-admission
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
```

事务脚本在任一契约失败时会自动执行同一组回滚并复验（`ROLLBACK_VERIFIED`）。
**不要把整份备份目录覆盖回去**：`lane-state/` 只用于审计，回滚不缩短任何 `not_before`。

## 7. 遗留与边界

- **`gemini-3.8-flash-high → gemini-3.8-flash-n` 静默替换**（7d 525 次）仍是 Google 侧
  响应行为，CPA 只告警不改写，网关层无杠杆。它是"降智"观感的主要来源，但**不是**
  本栈可修项；继续用 doctor `model_substitution` 观测。
- **v8.0.60 soak** 到期 2026-11-10T20:13:52Z，timer 2026-11-11T04:22Z 自动换芯。
  换芯后必须复跑 strict doctor：`cpa_policy` 的 `EXPECTED_CONFIG_PATHS` 若与 v8.0.60
  配置键不匹配会以 `POLICY_FAILED` 报出。
- **`/v1/files` 404 探活**（24h 1908 次，来自 `172.19.0.1`）不触上游、不耗额度，
  但它每 8–15s 生成一份 error dump，把 `error-logs-max-files=5` 的槽位挤满，
  **真正需要排查的错误转储会被它挤掉**。要消噪应关掉该客户端，不是加大保留数。
- 本台账**不**证明任何 provider 的合规状态，也不改变"账号是消耗品"的预算模型。
