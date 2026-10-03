# CPA admission 全面深度审查（bwg）— 2026-10-03

审查对象：ChatGPT desktop → Cockpit Tools → bwg VPS CPA（CLIProxyAPI）链路，
重点为 `cpa-admission.service` 的共享账号闸门与封号/限流/降智风险控制。

方法：本机权威口径（`codex_local_access_logs.sqlite`，只读）+ 远端只读探针
（admission journal / `/healthz` / nginx `cpa_gateway.access.log` / 容器日志 / 已部署清单）
+ 源码与既有 runbook 对照。**全程只读，未改远端、未改 Cockpit 配置。**

---

## 1. 三个症状的判定

| 症状 | 判定 | 依据（本窗口实测） |
|---|---|---|
| **A** `Selected model is at capacity` | **不是本地缺陷，也不存在"修好"这一说**。它是客户端对上游过载的固定文案。24h 内本机日志里 `capacity` 字面量 **0 行**；真正对应的是 OAuth lane 上游过载：6h 内 `gpt-6-luna` 503 **6 次** | 本机 sqlite `error_message like '%capacity%'` = 0；远端 journal `capacity=true` 8 次（全在 `chatgpt-oauth`） |
| **B** `exceeded retry limit, last status: 429` | **作为系统性故障已修复**。24h 内 admission 拒绝 **4 次**、nginx 连接/速率层拒绝 **0 次**。残留 429 全部是 admission 队列在 120s 预算上超时（上游变慢导致队列排不空），不是本地 bug | 远端：`lane_reject=4`（3×queue_timeout）；nginx `limit_conn/limit_req` 的 `REJECTED` **= 0**；4 条 429 全是 `bytes=203` + `upstream_time≈120.0` |
| **C** 慢 / 吐字慢 | **未变，也不该期待变**。本地只占 3–4%，全在上游生成时间 | 远端 `request_time` 与 `upstream_time` 差 0.15–0.25s；本机成功请求 p50 约 20–32s，max 180s |

**结论**：B 已实质修复；A、C 的剩余部分根因是**上游单 ChatGPT OAuth 账号的容量与吞吐**，
属外部事实，本地无可优化项（与 2026-10-02 的 D 决策一致）。

---

## 2. 实测证据

### 2.1 远端运行态（2026-10-03 15:50Z）

- 容器 `cli-proxy-api` = `eceasy/cli-proxy-api:v8.0.13`，Up ~1h；`cpa-admission` active，
  `MainPID=344362`、`NRestarts=0`。
- `/healthz`：三条 lane `inflight` 分别 2 / 0 / 0，`cooldown_remaining=0`，
  `failure_streak=0`，`half_open_probe=false` ⇒ 无冷却、无半开。
- 已部署源码 SHA-256 前 16 位：`cpa-admission.py=2183d13caac7ad75`（= 本地 LF 归一化）、
  `cpa_provider_routes.json=54110cf3faae6eb5`（= HEAD blob）⇒ **投影已落地、无漂移**。
- 无 `oauth-quarantine.json` 标记；`credential_concurrency_exceeded` **0**；
  `invalid_grant`/`refresh_token_reused` **0**；磁盘 19%、内存 590/2048 MiB。

### 2.2 远端 24h 计数

- admission journal：`lane_reject=4`、`lane_probe=6`、`upstream_error=3`。
- 上游结果按模型（`lane=passthrough` 全部）：

  | 模型 | 状态 | 次数 |
  |---|---|---|
  | `gpt-6.1-sol-input` | 502 | 70 |
  | `gpt-6.1-sol-input` | 503 | 69 |
  | `deepseek-v4.1-flash` | 502 | 1 |
  | `gpt-5.6-terra` | 200 | 37（**0 个 5xx**） |
  | `gpt-6-luna` / `gpt-6.1-sol`（oauth lane） | 200 | 318 / 240 |

- nginx `cpa_gateway.access.log`（580 行）：200×412、404×65、503×32、401×30、502×26、
  400×9、429×4、500×1、504×1；**`limit_conn=REJECTED` 与 `limit_req=REJECTED` 均为 0**。
- 4 条 429 的形状：`upstream_status=429`、`bytes=203`、`upstream_time≈120.0`、
  `request_time≈120.2`、`retry_after=seconds` ⇒ **admission 队列超时**，不是 nginx。

### 2.3 本机权威口径

| 窗口 | 总数 | 失败 | 失败率 |
|---|---|---|---|
| 24h | 791 | 138 | 17.4% |
| 6h | 207 | 9 | 4.3% |
| 2h | 195 | 9 | 4.6% |

24h 分层（`cpa_failure_triage.py`）：`local_gate=1`、`admission_queue_timeout=1`、
`admission_fast_reject=1`、`upstream_capacity=13`、`dead_route=120`、`slow_success=67`、
`healthy=584`。

按模型 24h：`gpt-6-luna` 200×322；`gpt-6.1-sol` 200×231；**`gpt-5.6-terra` 502×60 + 503×60
（27×200，失败率 82%）**；其余零散。失败集中在 00、01、02、14 时四个窗口，23 时全部 200。

---

## 3. admission 风控审查：发现的问题

### F1（高）仍在对外宣告、但已大面积失效的路由

- CPA 侧 24h 上游 5xx 共 **140**，其中 **139（99%）是槽位 1 `ai.input.im` 的
  `gpt-6.1-sol-input`**（502×70 + 503×69）。
- 已部署 `config.yaml` 仍在槽位 1 宣告 `- name: gpt-6.1-sol` / `alias: gpt-6.1-sol-input`。
- 2026-10-03 的 `d983c37` 只把它写进 `optional_models`（**只影响 health 前置门**），
  **没有从可路由目录移除**；而且它是 `lane=passthrough`，admission 也不管它。
- 本机侧同窗口 `gpt-5.6-terra` 失败 120/156（77%），且 `183.236.101.21` 呈现
  「502（4.4s）→ 1 秒后 503（0.01s，CPA transient 冷却）→ 约 65s 后再来一轮」的
  **客户端重试循环**。
- **风险**：这不是封号风险，是可用性缺陷 + 误导归因（客户端与运维都会以为"上游过载"）。

### F2（中）冷却上限的契约不一致

- 每条 lane 声明 `cooldown_cap_seconds=900`，但服务器退避路径用的是
  `retry_after_max_seconds=86400`（`cpa-admission.py` 的 `release()`）。
- 一条带 `Retry-After` 的 `usage_limit_reached` 能把**整条 `chatgpt-oauth` lane**
  钉到数小时（历史实测 `retry_after` 11705–12432s ⇒ 3.2–3.45h）。
- **行为上这是对的**（尊重上游退避正是防封号要做的），但"900s 是这条 lane 的上限"的
  读法是错的，会把长时间 429 误判为客户端问题。已内置为审计的 `warn`。

### F3（中）容量检测窗口盲区

- 容量标记只在响应体的前 `probe_bytes`（256 KiB）里解析。
- 流到一半才出现的 `server_is_overloaded` / `usage_limit_reached` **不会被计入**
  ⇒ 熔断不学习，客户端继续重试一个已经过载的账号——**这正是防封号要避免的方向**。
- 已内置为审计的 `capacity-probe-window` warn。

### F4（中）归因表陈旧导致处置方向错

- `scripts/cpa_failure_triage.py` 的 `DEAD_ROUTES` 把 `gpt-5.6-terra` 描述为
  "槽位1 `ai.input.im` 的 `gpt-6.1-sol-input` 死路由"。
- 但清单里 `gpt-5.6-terra` 归**槽位 3**（`http://35.213.82.91:8003`），且 CPA 侧 24h
  该名 **37× 200、0 个 5xx** ⇒ 它现在不是死路由，是间歇不可用。
- 该条目会把槽位 3 的窗口性故障误报成"死路由"，把处置引向"等上游修/换模型"而不是
  "看窗口/换路由"。

### F5（状态漂移）桌面 provider 已不再指向 10909

- `~/.codex/config.toml` 的 `[model_providers.codex_local_access]` 在 10-03 22:05 由
  `http://localhost:10909/v1` 改为公网 `https://fq.sciman.top:8443/fc3003d5715fbdf6/v1`
  （备份名 `config.toml.before-fq-public-20261003-220557.bak`），并新增
  `[model_providers.fq_sciman_top]`。
- 与既有不变量冲突：桌面本应经 10909 走本地并发闸门与模型别名重写层。
  `scripts/cockpit_provider_health.py` 现在会打
  `⚠️ 桌面未指向 10909 —— 别名重写层被绕过`（退出码仍 0）。
- 副作用：本地 45s 闸门指纹从"约 4 次/小时"降到 24h **1 次**（闸门被绕过）；
  公网 capability path + 网关 key 长期留在桌面配置里，扩大暴露面。
- **未在仓库留任何变更证据**（`docs/change-evidence/` 无对应记录）。

### F6（未闭合）Cockpit 侧 terra 5xx 与 CPA 侧对不上

- 本机 120 个 `gpt-5.6-terra` 502/503（`gateway_mode=sidecar`、
  `api_key_label=Provider Gateway: api-key-ec280ff6`）在远端 admission journal 里
  **没有对应的 terra 5xx**（该窗口只有 `gpt-6.1-sol-input` 的 5xx）。
- 两种可能：① 10909 本地产生（未到达 CPA）；② 上游调用没经过 admission。
  需要读 10909 自己的 `[provider-gateway]` 上游日志（`logs/codex-api.log.<date>`）
  才能闭合。**在闭合前，`dead_route` 这一层计数不可当作已归因。**

---

## 4. 本次已内置为项目功能

**新增只读审计工具 `scripts/cpa_admission_risk_audit.py`**（+ 29 个单元测试、
runbook、README 入口、`pyproject.toml` testpaths 与 `run_gates.ps1` 的
`$testFiles`/`$supportFiles` 登记）。

它把本次审查用到的方法固化成可重复的门禁，回答一个此前没有工具回答的问题：
**这份 admission 配置是否安全自洽，以及它仍在宣告的名字里有没有已明显失效的。**

- **契约半边**（离线、确定性）：冷却阶梯与服务器退避上限的关系、每条 lane 是否认得
  `429/503` 与 `server_is_overloaded`/`usage_limit_reached`、lane 成员是否都能被路由解析
  （陈旧成员会开整条 lane 冷却）、alias 是否被多槽位重复宣告、以及
  `gpt 路由 ⊆ oauth_exclusions` / `(gpt 路由 ∪ oauth alias) ⊆ codex_api_key_exclusions`。
- **姿态半边**：把清单里的"已宣告名字"与本机权威口径连接，用**失败率**（而非绝对数）
  把"路由基本不可用"和"上游偶尔忙"分开——OAuth lane 单账号忙时回 503 是预期行为，
  不能报成路由缺陷。
- 首跑结果（24h）：`FAIL` —— `gpt-5.6-terra` 120/156（77%）宣告中失败；
  `WARN` —— 三条 lane 的上游 Retry-After 可钉 86400s 而阶梯上限只有 900s。

门禁证据：`ruff check` / `ruff format --check` / `mypy` / `bandit` 全通过；
`test_cpa_admission_risk_audit.py` **29 passed**；`test_cpa_admission.py` 合并 **68 passed**；
`git diff --check` 通过。

---

## 5. 待用户决策

1. **`gpt-6.1-sol-input` 怎么办**：退役（从槽位 1 可路由目录移除）还是改指到可用上游？
   注意 Cockpit 的 `codex_model_providers.json` 是**另一层目录**，CPA 侧退役不会同步它。
2. **桌面 provider 是否恢复指向 10909**：恢复（回到本地闸门 + 别名重写层）还是正式接受
   直连公网（并同步更新文档、`cockpit_provider_health.py` 的判据与不变量）？
3. 是否收紧 `probe_bytes`（容量窗口）与是否重新定义 `cooldown_cap_seconds` 的语义。

## 6. 边界

- 本轮**只读**：未改远端、未改 Cockpit、未改 `~/.codex`。
- `natural_live_accepted` **未宣称**：B 的改善是窗口内实测，长期稳定性仍需真实使用窗口观察。
- 审计通过 ≠ 上游健康；上游容量是外部事实。

---

## 7. 后续执行（2026-10-04 00:00–00:50）

用户裁决：**`gpt-6.1-sol-input` 保留不动**；并要求就"是否正式接受直连公网"给结论后连续执行修复。

### 7.1 F4 已由并行会话修复（本次未重复劳动）

`scripts/cpa_failure_triage.py` 的 `DEAD_ROUTES` 已清空，并加了明确注释（"历史 5xx 归
`upstream_capacity`，否则已恢复的路由会被永久误报为本地路由缺陷"）；
`test_cpa_failure_triage.py::test_historical_route_failure_stays_upstream_capacity`
把这条钉住（terra 的 500/502/503 必须判 `upstream_capacity`）。**本次未改动这两个文件。**

### 7.2 F5 结论：直连公网不是"最优"，但也不是错——已把它变成可检断言

**推荐**：接受 `public_gateway`，理由是侧车静默停机的根因在 Cockpit（上游 #2702 未修）、
本地无法根治，而丢掉的本地闸门在实测里代价很小（24h 内 admission `queue_timeout` 1 次）。
但"最优"必须补上三件事，本次已做：

1. `cockpit_provider_health.py` 不再把"未指向 10909"渲染成警告，而是分类成
   `local_gateway` / `public_gateway` / `other` / `unknown`，前两者为接受态。
2. **新增第四条不变量：桌面目录可路由。** 桌面模型选择器是 provider 目录的投影，
   选中的网关必须能解析每一个 slug，否则稳定 `400 model_not_found`。
   `public_gateway` 比路由清单，`local_gateway` 比 10909 `manifest.json` 的
   `providerGateway.upstreamModels ∪ modelAliases ∪ modelIds`。
3. **现场读数立即命中真问题**：`desktop-model-unroutable: gpt-5.5, gpt-5.6-sol`
   （退出码 1）。这两个 slug 在 `~/.codex/cockpit-model-catalog.json` 里可选，
   但在公网网关的路由清单里不存在（同时列在 `oauth_exclusions` 与
   `codex_api_key_exclusions` 中）⇒ 一旦选中就必然失败。
   处置需要在 Cockpit UI 里决定"从目录移除"还是"加回可路由清单"（后者是路由变更）。

定案记录：`docs/change-evidence/20261004-desktop-provider-target-decision.md`。

### 7.3 F2 / F3 定为接受项（不是待修项）

写入 `docs/runbooks/cpa-ban-throttle-incident-response.md`：

- **F2**：上游 `Retry-After` 超过 lane 阶梯上限是**故意**的（尊重上游退避 = 防封号）。
  见到数小时 429 先读 `/healthz` 的 `cooldown_remaining`；**不要**调小
  `retry_after_max_seconds`。
- **F3**：容量标记 256 KiB 窗口是**有界盲区**。**否定证据**：近 72h 本机口径里
  `http_status=200 且 success=0`（流内失败）的行数 **0** ⇒ 没有真实样本支持放宽窗口，
  维持现值，等出现样本再动（动它就是 admission 契约变更）。

### 7.4 新增旁证（并行会话 23:56 的 doctor）

`DOCTOR_CONTRACT_OK`、9 个投影文件全 `MATCH`、`MODEL_IDS_UNKNOWN=none`、
`admission-health=OK`、`POLICY_OK`、`luna_state=available`；
`oauth_monitor=WARN_RENEWAL_WINDOW`（OAuth 凭据进入续期窗口，属正常周期）。
**运行时目录 `MODEL_IDS` 不含 `gpt-6.1-sol-input`** ⇒ 与"上游拒绝服务时运行时目录收缩、
`config.yaml` 未变"的既有判据一致，进一步支持"该 alias 上游不可用"的结论。

### 7.5 本次边界

- **未做任何远端写入**：`gpt-6.1-sol-input` 保留、`probe_bytes` 不动、
  `retry_after_max_seconds` 不动、无 `-Apply`。
- F6（Cockpit 侧 terra 5xx 与 CPA 侧对不上）**仍未闭合**，下一步是读 10909 的
  `logs/codex-api.log.<date>`（tag `[provider-gateway]`）的上游调用记录。
- `natural_live_accepted` 仍未宣称。
