# bwg CPA 多账号 · 封号/限流/降智/吐字 复审与修复（2026-11-16）

**性质**：全面深度复审 + 仓库代码修复。**本轮远端零写入**：未改 config、未增删或轮换凭据、
未清冷却、未重启服务、未变更并发、未 `-Apply`。

**授权**：用户当次请求"全面深度审查…登录多个 OAuth 账号与多个 API key 渠道时怎样防范
封号、限流、降智的风险/风控，优化吐字缓慢与缓存命中率；如有必要就修复/优化本项目代码"。

**前置阅读**：[2026-11-08 交接书](20261108-bwg-cpa-handoff.md) 与
[2026-11-15 增量复审](20261115-bwg-cpa-risk-review.md)。本页只写**本轮新增证据、本轮
新增修复、以及相对前两页的更正**，不重复推导既有结论。

---

## 1. 采集口径与证据层级

| 项 | 值 | 层级 |
|---|---|---|
| 采集时间 | 2026-11-16 23:47–00:15 (+08) | — |
| 连接器 | `ssh_tool.py --profile bwg run`（只读命令） | `host_loaded` |
| 容器 | `cli-proxy-api-fork:v8.0.61-gemini-raw`，`Up ~1h`，StartedAt 2026-11-16T14:26:03Z | `host_loaded` |
| admission | 重启于 14:26:03Z，`NRestarts=0`，四车道 `hold_reason=null` | `host_loaded` |
| 上游参考源码 | `router-for-me/CLIProxyAPI` main@`c19787b2`，最新 tag `v8.0.75`（2026-11-16T09:12Z）；本地外置仓已 fetch，工作树仍停在 `v8.0.61-77-g1aaf56bc` | `repo_verified` |

**外推边界**：只覆盖这台主机与上述窗口。上游容量、账号政策与社区叙事都是外部事实，
本页不用它们证明本栈健康或中招。

**情报获取限制（如实记录）**：本会话 `web_search` 渠道不可用（`web provider "web-access"
is not registered`），只有 `web_fetch` 可用。因此本轮**没有**新增社区封号叙事；
外部情报沿用 11-08 §8.1 已登记的四条转述，不据此改变任何判定。

---

## 2. 账号面实测（本轮新证据）

`/opt/cliproxyapi/auth` 逐文件只读统计（**未打印任何 token**）：

| 类别 | 文件数 | 状态 |
|---|---:|---|
| Antigravity（Google AI Pro） | **4** | 全部 `disabled=false`，令牌到期 2026-11-17T00:4x (+08) |
| Codex / ChatGPT（Plus） | 1 | `disabled=false`，`expired=2026-11-24`，`last_refresh=2026-11-14` |
| WorkBuddy（CN） | 8 | `disabled=false` |
| WorkBuddy（INTL） | 7 | `disabled=false` |
| Claude / Anthropic | **0** | config `oauth.excluded-models.claude` 墓碑仍在；`claude-risk.json` 保留 `authentication_required` |

**更正 11-15 §2**："Antigravity 2 份凭据"已过期，**当前为 4 份**（`agi.phys`、`mamoceesay5`、
`sciman.phys`、`sciman.top`）。过期时间集中在 2026-11-17T00:30–00:47 (+08)，说明刷新链在跑。

### 2.1 长期日志窗（`docker logs` 全量保留期，约 5 天）账号级信号计数

| 信号 | 计数 | 判读 |
|---|---:|---|
| `TOS_VIOLATION` / `PERMISSION_DENIED` / `SERVICE_DISABLED` | 0 / 0 / 0 | 无 Google 吊销形态 |
| `account_deactivated` / `account_suspended` / `UNAUTHENTICATED` | 0 / 0 / 0 | — |
| `violation of terms` / `Account suspended` / `has been disabled/suspended` | 0 | — |
| `Access denied`（边缘/WAF 403 页） | 0 | — |
| `6004` 模型级限流 | 187 | 频率族，可恢复 |
| `6008` 额度耗尽 | 52 | 全部 INTL 凭据，UTC+8 日界自愈 |
| `quota` | 10 | — |

**结论**：全保留期零账号级信号。**不做**任何加号、换号、轮换、清冷却、提并发。

### 2.2 一个必须说清的噪声（不是风险）

`docker logs` 里的 `403` grep 命中全部是 WorkBuddy 插件**签到**日志：

```
workbuddy: 夜间补签 black_cat 失败 (…): http 403: growth system is only available for personal users
workbuddy: credit guardrail configured but … has no credits snapshot … fail-open until then (issue #70)
```

它们既不经过模型请求路径，也不产生账号级判定。**不要**把它当成 403 封号证据。

---

## 3. 限流面：6004 的"池化别名放大"（本轮定性）

### 3.1 现象

`workbuddy-oauth` 是一条 lane，`models` 同时含 `deepseek-v4.1-flash`（CN/INTL 共享裸名，
且在 `cpa_provider_routes.json` 里是 `pooled_aliases`：WorkBuddy 主、OpenCode 备）与
`deepseek-v4.1-flash-sg`（INTL 专属）。config 的 opencode 兼容渠道把裸名别名成
`deepseek-v4.1-flash-oc`。

**admission 按模型名判 lane**，所以一次来自**单份凭据**的 6004 会在
`capacity_scope()` 里落成 `"lane"` → 整条 lane 冷却。冷却上限 300 s 虽然由
`WORKBUDDY_6004_MAX_COOLDOWN_SECONDS` 压住，但**同一个模型名在网关侧是唯一的**：
它被冷却期间，OpenCode 这唯一一条备份路由也一起消失。11-15 §3.2 已把这条登记为
"本栈最主要的共享账号面扩散来源"，并把它列为**用户决策**（拆 lane 属契约变更）。

### 3.2 本轮修复（比拆 lane 更窄、风险更低）

新增 lane 级契约键 `pooled_models`（`cpa-admission.json`，当前仅
`workbuddy-oauth.deepseek-v4.1-flash`），请求路径据此把**通用路径上已经成立的容量信号**
收窄到**模型作用域**，并把时长钉在 `POOLED_LANE_MODEL_COOLDOWN_SECONDS = 300`：

- 信号仍然被尊重（模型仍被 park，客户端仍然 fail-fast 而不是反复打上游）；
- 兄弟名字 `deepseek-v4.1-flash-sg` 与整条 lane 不再被牵连；
- 静态限制（6003 / 429 / 403 / 11140 等**已声明**的 lane 级信号）**保持 lane 级**
  ——实现上是一次"去掉 pooled 标记后重新判定"的复测，只有复测仍判 capacity 的信号
  才被收窄，`test_workbuddy_json_frequency_family_persists_no_hold` 的行为即由此钉住。

**代价（必须如实记录）**：单份凭据被上游日窗口���限制时，admission 只在本地压 300 s，
之后同一模型的下一次请求仍可能落到**同一份**已耗尽凭据上，由插件
（`scheduler_mode: credits` + 每凭据 `account_max_concurrent: 1` + 6004 注册表）兜底。
这是既有设计的选择：宁可让插件按"每（凭据 × 模型）"精确 park，也不让网关按模型名
把唯一备份路由整段删掉——2026-11-06 实测过一次 22254 s 的整段删除。

**未做**：CN/INTL 拆两条 lane（11-15 §3.3 选项 A）。那会改裸名路由契约与跨 realm 兜底
语义，仍属用户决策；本轮的 `pooled_models` 不改变任何路由归属。

---

## 4. 封号面修复：长期限制不再"骑着容量阶梯"

### 4.1 缺陷（读码确认）

`oauth_risk_signal()` 对 `chatgpt-oauth` / `workbuddy-oauth` 有一条**有意**的宽容：
裸 401/403 不再单独锁 lane（2026-11-11 夜间那次锁来自 fallback 腿的 JSON 403）。
但下游的"账号已停用"判定只认 `error.message` 里的一小串固定措辞，而：

- 上游可能把措辞放在 `reason` / `description`，或把 ErrorInfo 平铺在 error 对象上；
- Antigravity 的 `RetryInfo` 在客户端不回显 `@type` 时被整段丢弃，strict 车道退化成
  "没有提示 → 用本地阶梯"，从而**比上游声明的 reset 早**恢复请求。

后果是**方向性错误**：账号被停用的最强证据被当成"慢一点"，继续按 60/120/240/480/900 s
打上游——这正是放大风控响应的形状。

### 4.2 修复

| 改动 | 位置 | 效果 |
|---|---|---|
| `STANDING_RESTRICTION_MARKERS` 逐车道统一 | `scripts/remote/cpa-admission.py` | 停用措辞在 ChatGPT / WorkBuddy / Antigravity 三处**同一张表**判定，不再某条车道落后 |
| `STANDING_RESTRICTION_REASONS`（`TOS_VIOLATION` / `POLICY_VIOLATION` / `SERVICE_DISABLED` / `ACCOUNT_DISABLED` / `ACCOUNT_SUSPENDED`） | 同上 | 平铺 `reason` 与嵌套 `ErrorInfo.reason` 同等对待 |
| `details` 单对象归一为列表；`@type` 用后缀匹配 | 同上 | 不再因形状差异丢证据 |
| 未带 `@type` 的 `retryDelay` / `metadata.quotaResetDelay` 仍解析 | 同上 | strict 车道真正尊重上游 reset |
| `GEMINI_CAPACITY_MARKERS = ("resource_exhausted",)` | `capacity_scope()` | Google 的 `RESOURCE_EXHAUSTED` 不再因为不含 `rate limit`/`quota exceeded` 字样而被当成非容量响应透传 |

**为什么需要最后一条**：现有 lane 的 `capacity_markers` 全是英文速率措辞，
Google 的配额拒绝是 `RESOURCE_EXHAUSTED`（ErrorInfo.reason / status 字）。这是一个
**只会在配额耗尽时才暴露、且暴露方向是"继续打"的 fail-open 缺口**，必须补齐。

---

## 5. 吐字与缓存面

### 5.1 本轮实测（`/healthz` 1h 窗口）

| model | 样本 | 缓存命中率 | `first_text_ms` p50 / p90 |
|---|---:|---:|---|
| `gpt-6-luna` | 107 | **96.0%** | 14.7 s / **30.8 s**（max 85.3 s） |
| `gpt-6.1-sol` | 24 | 91.4% | 5.4 s / 12.7 s |
| `deepseek-v4.1-flash` | 18 | 92.9% | 2.0 s / 2.1 s |
| `gemini-4-hydrogen-xhigh` | 0 | — | — |
| `claude-sonnet-5-5-high` | 0 | — | — |

**判读**：缓存命中率已经不是瓶颈（与 11-15 §4 一致）；吐字慢仍集中在长上下文的
ChatGPT 车道，且 **`gpt-6-luna` p90 30.8 s / max 85.3 s 是本轮唯一可操作的吐字项**。
admission 侧 `waited_ms` 未出现异常（`inflight=0`、四车道无 pending）。

### 5.2 上游 `v8.0.61 → v8.0.75` 与吐字的直接关系（本轮新证据，`repo_verified`）

`v8.0.61..v8.0.75` 共 **125 个提交**，其中与本议题直接相关的：

| 提交 | 内容 | 对"吐字/缓存/风控"的意义 |
|---|---|---|
| `2c5b01f4` | Claude 默认传输**关闭空闲连接复用**（`MaxIdleConnsPerHost=-1`），并回滚 post-`message_stop` 的 body drain | 修复"下一条请求被挂在一条已完成但未释放的流上"的首字延迟 |
| `098af7d5` | 跨等价模型拼写共享并迁移冷却 | 模型名大小写/别名差异不再让冷却互相踩 |
| `14e57717` | 插件执行器可上报**凭据级** 429 | WorkBuddy 这类插件通道的账号级限流终于能被正确归属 |
| `ed1c23f9` | Gemini/Gemini-Vertex 尊重 429 的 `Retry-After` 与 `RetryInfo` | Antigravity 车道与 §4.2 的本地修复同向 |
| `5a351d95` + `e96d4c48` | `TOS_VIOLATION` 直接把该凭据 `disabled`（OAuth 刷新保留），池级保护性暂停**默认关闭** | 封号处置落到"凭据"这一精确层，不再靠整池暂停 |
| `7e82d0d1` / `cf07c78a` / `f2efe2c4` | Gemini 零输出 502 / 异常终止**不再**冷却凭据，且 200 承诺前失败 | 减少"假失败"造成的账号冷却与空等 |
| `4da9f685` | 终端 429 携带**池**的真实恢复时间 | 客户端退避与真实 reset 对齐 |
| `69ed202a` | 冷却截止后自动恢复模型列表 | 客户端不再长期看不到已恢复的模型 |
| `84534584` | 文档：`X-Session-ID` 与全文压缩下的会话亲和 | 直接指导"压缩后缓存全丢"的客户端侧解法 |

**判定**：升级到 `v8.0.75` 对**吐字（首字延迟）与封号处置（凭据级停用）**都有实测依据，
但在本轮的只读窗口内**没有执行**（soak / 投影 / doctor 复核是一整条受控事务，且需要
换芯后的 `cpa_policy` 键表复核）。这是本轮最大的**待决项**，见 §8。

---

## 6. 本轮代码改动（全部在仓库，远端零写入）

| 文件 | 改动 | 门禁层 |
|---|---|---|
| `scripts/remote/cpa-admission.py` | `STANDING_RESTRICTION_*` 统一；Antigravity 信号容错；`GEMINI_CAPACITY_MARKERS`；`pooled_models` 收窄 + 300 s 界限；`_generic_capacity_scope()` 把"收窄前该信号是否已成立"抽成一处 | `tests/test_cpa_admission.py` |
| `scripts/remote/cpa-admission-integrity-pin.txt` | 随源文件刷新（`ExecStartPre` 校验的是这个值） | `tests/test_cpa_guardrails_script.py` |
| `scripts/remote/cpa-admission.json` | `workbuddy-oauth.pooled_models = ["deepseek-v4.1-flash"]` | 同上 + 策略测试 |
| `scripts/remote/cpa_policy.py` | 钉住 `requests.streaming.bootstrap-retries = 1` | `tests/test_cpa_policy_runtime.py` |
| `scripts/remote/cpa-guardrail-apply.sh` | 投影同一键 | 同上（`semantic_policy` 比对两侧派生） |
| `tests/script_validation_support.py` | fixture 增键 | 同上 |
| `tests/test_cpa_admission.py` | 新增 6 组断言（含大小写、单对象 `details`、未带 `@type` 的 `retryDelay`、`pooled_models` 越界拒绝）+ 1 处既有断言改为模型作用域契约 | 同上 |

**门禁实测**（2026-11-17 00:2x +08，本机 Windows）：受影响四件套 `test_cpa_guardrails_script` /
`test_cpa_admission` / `test_cpa_policy_runtime` / `test_cpa_updater_apply` = **435 passed,
4 skipped**；`ruff check` / `ruff format --check` 干净；`check_docs.py` = `DOCS_CHECK=PASS (42
documents)`；`cpa_catalog_check.py` = `CATALOG_CHECK=PASS`；全量 `pytest -q` = **1723 passed, 31 skipped,
815 subtests passed**（257 s）。

### 6.1 `bootstrap-retries = 1` 的理由与边界

上游 `sdk/config/config.go` 的默认值是 **0**；`sdk/api/handlers/handlers_stream.go` 只在
**首字节未发出前**、且错误是 401/403/402/408/429/5xx 时重放一次。对共享账号的含义是：

- 一次注定失败的首字前失败，**不会**变成客户端再发一次真实请求（那才是双倍上游消耗）；
- `routing.retry.request-retry` 仍是 0，字节一出手就绝不重放，不构成重试环；
- `v8.0.75` 里 `7e82d0d1`/`cf07c78a` 又把"零输出 502"从凭据冷却里摘出去，
  与本改动同向。

---

## 7. 降智面

沿用 11-15 §6.3 的判定：医生台 `model_substitution` 的稳定变体对
`gemini-3.8-flash-n <== gemini-3.8-flash-high` 与 `hy4-free <== hy4free` 是上游回显
基础名，**不是降级证据**。本轮 `/healthz` 的 `gemini-4-hydrogen-xhigh` /
`claude-sonnet-5-5-high` 样本为 0，说明新开的名字**尚未被消费**，因此也无法用
样本证明或否定其质量——不要在无样本时下"新模型更好/更差"的结论。

---

## 8. 待决项（按收益/风险排序，均需用户裁决）

| 优先级 | 项 | 收益 | 需要的动作 |
|---|---|---|---|
| **P0** | 升级 `v8.0.61 → v8.0.75` | 首字延迟（`2c5b01f4`）、凭据级 TOS 停用（`5a351d95`）、Gemini 配额尊重、插件凭据级 429 | 换芯事务：pin digest → soak 判据 → 备份 → 投影 → `DOCTOR_CONTRACT_OK` → 复跑 `cpa_policy` 键表 |
| **P1** | 把本轮 admission 改动带上机 | `pooled_models` 收窄 + Gemini 配额缺口 + 长期限制统一 | 该文件受**完整性 pin** 约束：必须走"等 drain + 源与 pin 同事务投影"，不能用普通 `-Apply` |
| **P2** | `gpt-6-luna` 的长上下文冷重启 | p90 30.8 s → 缩到 10 s 级 | 客户端侧：新任务开新会话；压缩时带 `X-Session-ID`（`84534584`），并先跑 `cpa_cache_reset_probe.py` 归因 |
| P3 | CN/INTL 拆两条 lane（11-15 §3.3 A） | 彻底消除跨 realm 兜底 | 契约变更 + 全套测试 + 桌面目录同步 |
| — | 账号面动作 | — | 本轮零信号，**不做** |

---

## 9. 回滚入口

本轮**无远端写入**（改的都是仓库文件），回滚 = 撤销本提交。
远端若后续执行 P1/P0，回滚入口分别是：

- admission：投影事务自带的备份目录（`/root/cpa-guardrails-backup-<ts>/`）内
  `cpa-admission.py` + `cpa-admission.json` + `cpa-admission-integrity-pin.txt`，
  还原后 `systemctl restart cpa-admission`；
- 换芯：见 [cpa-manual-rollback](cpa-manual-rollback.md)。

---

## 10. 更正台账（相对 11-08 / 11-15）

| 出处 | 旧表述 | 本轮证据 |
|---|---|---|
| 11-15 §2 | "Antigravity 2 份凭据" | **4 份**，全部在役（§2） |
| 11-15 §2 | "20 份凭据" | **20 份**仍成立：4 Antigravity + 1 Codex + 15 WorkBuddy |
| 11-08 §8.3 | "`bootstrap-retries` 保持 0" | 已改为 **1**，理由与边界见 §6.1：0 会让首字前失败变成**客户端**重发 |
| 11-15 §3.2 | 6004 扩散"只能拆 lane 才能收敛" | 本轮证明还能在 `capacity_scope` 层收窄到模型作用域（§3.2），不改路由契约 |
