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

## 8. 第二轮（2026-11-08 晚，用户授权连续修复）

### 8.1 P0：`/v1/files` 探活把错误转储环挤爆（已修复）

**这是本轮最重要的发现**：`error-logs-max-files=5` 的五个槽位**全部**被
`/v1/files` 404 占据，轮转间隔 **12–25 秒**。实测：
- `/v1/files` 探活 6h 内 **570 次**（约 95 次/小时）；
- 探活方是**我们自己的 DSH 客户端**（UA `deepseek-harness/0.2.2-rc.1`，
  带 `Anthropic-Beta: files-api-2025-04-14`），每次文件引用触发一次上传尝试；
- CPA 从未实现该端点，404 在**任何 provider 调用之前**生成 —— 所以它不含任何
  上游证据，却占掉了唯一 5 个可用的错误转储槽。

后果：**真正的封号/限流错误转储存活不到 90 秒**。本轮早先引用的"48h 内无账号级
信号"因此有一个盲区：居于 90 秒之外的封号证据本就不可能被看到。

上游核对：`shouldLogRequest()`（request_logging.go:522）只排除 `/v0/management` 等
管理端点，**没有任何按路径排除 error dump 的配置**，所以只能在前置层解决。

修复：admission 对 `/v1/files`、`/v1/files/*`、`/v1/files/list` 返回确定性的
`404 files_api_unsupported`，**不转发、不产生 dump**，并附
`X-CPA-Admission-Reason` 头。判定故意收窄到该三个形状；`/v1/files-not-really`
这类邻近路径仍照常转发（有测试钉住）。

### 8.2 P1：按凭据区分封号信号（已加）

doctor 新增 `==credential-ban-signals==`：从容器日志的 `auth_file=` 归因到具体
凭据，统计 `invalid_grant` / `TOS_VIOLATION` / `PERMISSION_DENIED` / `11140` /
`insufficient_quota` / `6008` 六类信号，输出每个凭据的命中数、去重凭据数、按类别
的计数。**仅观测、不入 gate**（封号是外部事件，doctor 失败关闭会挡住无关投影）。

首跑结果（168h）：3 条信号，全部是 `workbuddy-intl` 三个凭据的 **6008 额度耗尽**，
`invalid_grant`/`TOS_VIOLATION`/`11140` 均为 **0** —— 与"无封号"判断一致，且现在
是**可归因**的。

### 8.3 P2：模型替换按凭据归因，并给出覆盖率（已加）

doctor 新增 `==model-substitution-by-credential==` 与
`model_substitution_coverage_pct_of_lane_requests`。

数据：886 次替换 / 886 次 antigravity 请求 = **覆盖率 100%**，全部集中在
`sciman.phys@gmail.com`（871）与 `sciman.top@gmail.com`（8）。

**但这不是降智。** 决定性 A/B（同一 prompt，`gemini-3.8-flash-high`）：

| `reasoning.effort` | reasoning_tokens（3 次） |
|---|---|
| `medium` | 117 / 198 / 249 |
| `low` | 0 / 0 / 0 |

即 thinking 档位**确实生效**，`-n` 只是上游 `modelVersion` 回显基础名，与
2026-10-22 的判读一致。真正的缺口是**旧 doctor 把这个结论写成了
"KNOWN_NAME_VARIANT = OK"，无法区分「命名回显」与「整池被降级」**。
现在用覆盖率做区分：良性回显 = ~100% 且 pair 不变；真实降级 = pair 改变或出现
未知 pair。

### 8.4 P3：`first_text_ms` 的分母陷阱（已修）

24h 实测 9739 条 `upstream_result`，只有 **908 条（9%）** 带得到 `first_text_ms`
样本 —— 非 SSE 与纯推理轮在构造上就没有「第一个字」。把两种总体混算 p90，是用
一堆永远不会有首个字的轮次回答「首字多慢」。

已拆为 `first_text_ms_text_turns` / `first_text_ms_no_text_turns` 并输出
`first_text_ms_measurable_share_pct`。拆分后**暴露了 10 倍的车道差距**：

| 车道 | p50 | p90 |
|---|---|---|
| `workbuddy-oauth/deepseek-v4.1-flash` | 3.3 s | **4.2 s** |
| `workbuddy-oauth/deepseek-v4.1-flash-sg` | 3.8 s | 5.5 s |
| `chatgpt-oauth/gpt-6.1-sol` | 3.3 s | 10.1 s |
| `chatgpt-oauth/gpt-6.1-terra` | 8.0 s | 16.4 s |
| `antigravity-oauth/gemini-3.8-flash-high` | 9.7 s | 25.3 s |
| `zhipu-coding-plan/glm-5.3-flash` | 12.2 s | 33.7 s |
| `chatgpt-oauth/gpt-6-luna` | 10.0 s | **43.0 s** |

"吐字慢"不是全局现象：WorkBuddy 4 秒级，ChatGPT/GLM 30–43 秒级。真实杠杆是
**该车道的上下文长度**，不是网关。

### 8.5 本轮否决

| 候选 | 判定 | 依据 |
|---|---|---|
| 给 `/v1/files` 加路由让它「可用」 | 否 | 我们不是 Anthropic Files API；实现它等于扩大伪造面。静默 404 才是正解 |
| 加大 `error-logs-max-files` | 否 | 探活 95 次/小时会同样挤爆更大环；治标且扩大明文请求体的留存面 |
| 关闭 DSH 的文件引用能力 | 待用户决定 | 探活来自客户端行为，网关无权也无力关闭；静默 404 已使代价归零 |
| 因 100% 替换率判定降智并换号 | **否** | A/B 证明 thinking 档位生效；`-n` 是命名回显 |

## 9. 第三轮：孤儿副本治理与客户端根因（2026-11-09）

### 9.1 根因定位：`/v1/files` 来自 DSH 内嵌的 Anthropic SDK

不是本仓库的行为，也不是本仓库能关闭的。证据：DSH 的 `app.asar` 内含
`@anthropic-ai/sdk`，其 `client.beta.files.upload()` 生成 **`POST /v1/files?beta=true`**，
并自动附带 beta 头（`files-api-2025-04-14` 等）。DSH 把文件引用实现为「先上传、再引用」，
网关没有该端点，于是每次文件引用都产生一次 404。

因此：**静默 404 是网关侧的正确终局**，不需要（也不应该）实现 Files API ——
我们不是 Anthropic，实现它等于扩大伪造面。

已验证真实 SDK 请求形状全部静默，且零 dump：

| 请求 | 结果 | dump |
|---|---|---|
| `/v1/files?beta=true` | 404 `files_api_unsupported` | 0 |
| `/v1/files/list?beta=true` | 404 `files_api_unsupported` | 0 |
| `/v1/files/file_abc?beta=true` | 404 `files_api_unsupported` | 0 |

（`urlsplit().path` 已剥离 query，因此 `?beta=true` 不影响匹配。）

> **勘误（2026-11-09，调用点实测）**：本节「内嵌 SDK」归因不成立。DSH 全部
> `@deepseek-ai/dsh-*` 模块对 `@anthropic-ai/sdk` **零 import**；实际发送方是
> `@deepseek-ai/dsh-llm-deepseek` 自研 `DeepSeekFilesClient.upload()`（调用形态
> `client.upload(`，包源 `packages/llm/llm-deepseek`），其代码路径不产生
> `?beta=true`；§8.1 观测的 UA `deepseek-harness/0.2.2-rc.1` 只出自 dsh-llm 的
> `attributionHeaders()`。上表三种请求形状的本地静默 404 行为与修复不受影响
> （无 query 的 `/v1/files` 命中同一 `_silent_probe_route`）。网关侧结论不变。
> 归因与 wire 细节以 runbook `/v1/files` 小节为准。

### 9.2 孤儿投影副本自检（已加）

`2026-10-08` 的模块化（`db7f52de`）把 doctor 从「落盘脚本」改为「内联流式注入」，
但 `/opt/cliproxyapi/cpa-guardrail-doctor.sh` 留在了主机上。**没有任何代码读取它**，
所以 `==projection-drift==` 也不覆盖它——它带着一整套过期的防护流水线在主机上躺了一个月，
无人察觉。

doctor 新增 `projection-copy-orphan=` 与 `projection-copy-coverage=`：对 5 个已改为
流式的模板检查主机上是否残留同名副本。**WARN 而非 FAIL**：副本是惰性的，
用硬门禁会为一具不执行的旧文件挡住每一次投影。

本轮同时清理了那具孤儿：备份
`/opt/cliproxyapi/backups/20261108T163309.148031035Z-stale-doctor-copy-20261108`
（含 `pre-sha256.txt`），另存 `/root/cpa-guardrail-doctor.orphan-20261108.sh`。
清理前已用 `git grep` 证实全仓无任何运行时引用指向该远端路径。

### 9.3 过程偏差（诚实留痕）

清理孤儿时，我**先删除、后证明**：当时的 grep 只查了 `/etc/systemd/system/`、
`/etc/cron.d/`、`/opt/cliproxyapi/*.sh`，**没有查仓库源码与 wrapper**。
删除后才用 `git grep` 确认安全。虽然结论正确（全仓仅 `outputs/` 下一个 2026-11-05 的
临时探针提到该路径，非运行时代码），且有两份可恢复副本，但**顺序错误**：
破坏性远端操作必须先证明无引用，再移除。

## 10. 第四轮：降智观测盲区（2026-11-09）

### 10.1 fork 镜像标签让整个降智观测静默失效

并行会话把运行镜像换成了 fork 构建：`cli-proxy-api-fork:v8.0.61-gemini-raw`。
doctor 解析版本的正则是 `s#.*:(vX.Y.Z)(@sha256:...)?$#` —— **把版本锚定在标签末尾**。
fork 标签带后缀，因此解析结果为空，`==model-substitution==` 整段被跳过，
输出 `model_substitution=UNAVAILABLE_VERSION`。

**后果**：静默降智观测在 fork 实际服役期间完全失效，而 doctor 仍然报
`DOCTOR_CONTRACT_OK` —— 一个观测段悄悄消失，契约却是绿的。

**修复**：改为匹配标签中任意位置的 `vX.Y.Z`（`s#.*:(v[0-9]+\.[0-9]+\.[0-9]+).*$#`），
三种形状均验证通过：

| 镜像标签 | 解析 |
|---|---|
| `eceasy/cli-proxy-api:v8.0.59` | `v8.0.59` |
| `cli-proxy-api-fork:v8.0.61-gemini-raw` | `v8.0.61` |
| `eceasy/cli-proxy-api:v8.0.61@sha256:abc` | `v8.0.61` |

并新增 `cpa_image_tag_unparsed=`：解析失败时**明确报出**标签末段，而不是静默跳过。
该行只打印最后一个路径分量，不输出可能含凭据的完整 registry 路径。

线上复验：`model_substitution=KNOWN_NAME_VARIANT` 恢复，
`coverage_pct_of_lane_requests=100`。（警告数仅 2 是容器刚重建、日志覆盖重置，
不是替换消失。）

### 10.2 教训

「doctor 绿」不等于「所有观测段都跑了」。一个段落可以因为前置解析失败而**整体
跳过**，且不产生任何 FAIL。今后新增或修改依赖解析段时，解析失败必须有独立的
可见信号（本节即 `cpa_image_tag_unparsed`），不能只留一个 UNAVAILABLE 状态值。

## 11. 第五轮：向外部提交 issue / discussion（2026-11-09，用户授权代理提交）

用户当次授权：「包括代理提交issue等」。以下三条均已实际提交并复验为 OPEN。

### 11.1 CLIProxyAPI #7362（文档 / 运维建议，非缺陷）

<https://github.com/router-for-me/CLIProxyAPI/issues/7362>

内容：CPA 前置线程化 sidecar 的两个运维陷阱 ——
(a) `socketserver.ThreadingHTTPServer` 默认 `request_queue_size=5`，实测把 8 并发压到
57 req/s（单线程 447 req/s），p99 从 22ms 涨到 1060ms；设 128 后恢复到 550 req/s；
(b) 长驻 CPython 代理的 glibc arena 膨胀（14 个 64MiB 对齐区、462MB、RSS==HWM），
`MALLOC_ARENA_MAX=2` 后 682→82MB 且 `waited_ms` p90 不变。
两者均为**操作侧**问题，明确不表述为 CPA 缺陷，并说明 docs/ 目前是 SDK 导向、
若不适合托管运维内容也完全可接受。

该草稿原为 2026-11-08 的 `outputs/upstream-drafts-20261108/`，当时判定「本轮不提」；
本轮用户授权后代为提交。提交前已复核：上游仍无运维章节（`docs/` 仅 11 个 sdk-*/
management-* 文件），且仓库内无同类 issue。基线从 `91e0dee8` 更新为 `681ce41b`。

### 11.2 CLIProxyAPI #7363（error dump 保留策略）

<https://github.com/router-for-me/CLIProxyAPI/issues/7363>

内容：`error-logs-max-files` 的环会被高频、无诊断价值的 404 挤爆。附本栈实测
（570 次/6h、5 槽位、12–25 秒轮转、真实失败 dump 存活 <90 秒），并指出
`shouldLogRequest()` 只排除 management 端点、没有按路径排除的入口。
给出三个可选方案（路径排除配置 / 未路由 404 降级为仅访问日志 / 仅文档说明），
并明说对方案 (1) 只是轻微偏好、不强烈主张。

### 11.3 deepseek-harness Discussion #12696（Q&A）

<https://github.com/deepseek-ai/deepseek-harness/discussions/12696>

该仓 **issues 已关闭**（`has_issues: false`），但开启 Discussions，故走 Q&A 分类。

内容：附完整请求特征（UA、`Anthropic-Beta: files-api-2025-04-14`、undici multipart
boundary、158KB、`Sec-Fetch-Mode: cors`），提问是否为本意功能、能否关闭或强制内联、
若是回退分支则触发条件是什么。

**如实声明了未能定位调用点**：`@anthropic-ai/sdk` 内含 `client.beta.files.upload()`
但全包搜索 `files.upload(` 的命中全是 SDK 文档/helper，**没有任何命中落在
`@deepseek-ai/dsh-*` 模块内**；且 DSH 自身附件链走 `image_url`/内联 base64，
不依赖 Files API。故以提问而非断言提交，并明确「若答复是忽略即可，我也接受」。

### 11.4 提交纪律

- 三条均声明**非缺陷**或以提问形式提出，未断言对方有错；
- 未包含任何凭据、内部地址、账号标识或私有拓扑；
- 附的是可复现的测量数据与命令，不是结论性指控；
- 对「不需要改」的答复均预留了接受空间。

## 12. 第六轮：并行会话处理 #12696 的深度审查（2026-11-09）

并行会话在 `f93cb2e` 更正了我 §9.1 的归因，并发布了 #12696 的回复。本次逐条独立
复验，结论：**核心归因修正是对的，但附带的两组数字不成立**，已当场撤回。

### 12.1 确认成立的修正

| 断言 | 复验方法 | 结果 |
|---|---|---|
| 发送方不是 `@anthropic-ai/sdk` | 全包搜 `DeepSeekFilesClient` | **存在（3 处）**，自研 transport |
| 路径无 query | 读 `upload()` 源码 | `this.request("/files", …)`，`messagesApiRoot` 只补 `/v1` |
| beta 头来自 dsh-llm | 搜 `MESSAGES_FILES_BETA` | `= "files-api-2025-04-14"` |
| UA 来自 `attributionHeaders()` | 读函数体 | `{"user-agent": userAgent(identity)}` |
| 没有 Files 开关 | 搜 config schema | 只有 `filesApiTimeoutMs`/`fileExpiresAfterSeconds` 等预算项 |
| `cpa_safe` 日志不记 URI | 读 `log_format` | **确认**：只记 method/route/status/bytes 等 |

其中「`cpa_safe` 不记 URI」直接推翻了我 §11 原先写的判据
「nginx 计数 0 → 宿主机直连」。**我把一个恒为 0 的 grep 当成了证据**，这是方法错误。

### 12.2 不成立、已撤回的数字

**(a) `app.asar payloadDigest 0881fb…`** —— 该值在归档中不存在。实测文件 SHA-256 为
`5fa8a6b21bceb8f3e99e30efa2270efd0cc773a11e42aa0488f804a64b34e637`。已在回复中撤回。

**(b) `1908 x 404 in 24h`** —— 无法复验。可核实的只有 admission 全程应答的 **31 次**。
并行会话说「log.1 有 1849 条 `route=other status=404`…与本机贴图窗口吻合」，实测不成立：

- 1849 条中 **175 条是扫描器**：`GET` + `upstream_status=-` + `bytes=146`，来自
  40.124.x / 147.185.x / 45.156.x 等分散公网 IP，到不了 admission；
- 其余 POST 条目的**小时分布**是 00 时 222、01 时 217、10 时 225……而探活窗口
  12–20 时只有 37/12/17/51/2/1/2 条，**两者形状不符**；
- **决定性反证**：真实探活时刻 `16:54:33` 与 `16:27:03`，两份 access log **各有 0 条**。
  那两次探活根本没经过 nginx，与这批扫描器流量无关。

### 12.3 补上的关键答案

issue 里悬着的「404 后是否回退内联」，答案在 `RequestFiles.resolve()`：它把**任何**
`ensureUploaded` 失败都包成 `FileResolutionFailure`（无状态码过滤），请求循环捕获该
类型后置 `inline = true` 并用 `inlineImages(...)` 重新序列化。因此 **404 必定回退内联、
本轮仍成功**，代价就是每轮一次无效上传。这已在更正中回复给上游。

### 12.4 结论

并行会话的**技术定位可信**（调用点、传输形状、无关闭开关都经我独立复验），
但其**量化证据链有一处编造式误配**（把扫描器 404 当成本机探活，并据此编出 1908）。
已发布更正评论撤回 digest 与 1908，并把正确判据写回 runbook。

### 12.5 三次勘误（2026-11-09，复验 §12.2 的"决定性反证"时发现时区错位）

§12.2(b) 的「决定性反证」**本身不成立**：`16:54:33` / `16:27:03` 是**北京时间**
（桌面侧工件），而 access log 是 UTC。16:54:33 CST = **08:54:33Z**，log.1（精确
覆盖 11-08 单日）在该秒有精确探活签名命中：`POST route=other status=404
upstream_status=404 bytes=0`（用户出口 IP）。且其余 POST 条目（**1655 条，全部
同一出口 IP**）的小时分布与同档 5505 条 `route=other status=200`（chat）逐小时
**同形**（同峰 00/01/04/07/10/11、同谷 12–20 UTC，即 08–19 京）——探活**确实
经过 nginx**，与 chat 同一客户端。16:27:03（京）在 11-08 的 log.1 两时区读法下
均未定位（可能属 11-07，对应轮转档未检出），不影响主结论。

两点定性修正：§12.4 的「编造式误配」措辞过重——原句是**采样 3 条 POST 探活签名
后把 1849 整体描述为同类且断言窗口吻合**，属**过度概括**（未跑直方图），非编造；
「据此编出 1908」同样不成立——1908 源自 §8.1 记录的旧容器日志（已随容器更替
不可复验），不是本次新造。**仍然成立的更正**：GET 扫描器混入、URI 不落盘、
570/6h、95/h、1908/24h 数字撤回、31 次 admission 计数、files-v3.json 恒空旁证、
digest 应为 5fa8a6b2。已在上游发布第三次更正评论。
