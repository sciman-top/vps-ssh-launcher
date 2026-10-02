# 三症状全面复审 — 2026-10-02 21:40 CST（UTC 13:40）

只读审查，未改动本机或远端任何配置。数据窗口：远端 nginx 当前访问日志
`00:48→13:42 UTC`（≈13h）；本机 `codex-api.log.2026-10-02`（当日全量）。

---

## 0. 结论速览

| 项 | 判定 |
|---|---|
| 远端 admission 三层修复（半开队列 / 诚实 Retry-After / 上限） | ✅ 已部署、结构门禁全绿、行为可见 |
| 远端 nginx 连接预算 6→12→20 | ✅ 已投影，**近 10 小时 0 次连接层拒绝** |
| 本机重试放大（request-retry=1） | ✅ 运行态已被 r2 二进制钳制为 0（磁盘值仍是缺陷形状） |
| 本机并发闸门（maxAccountConcurrency=0） | ✅ 运行态钳制为 3 |
| 症状 A「at capacity」 | ⚪ **不由本栈产生**（两侧 12h 内该标记均为 0） |
| 症状 B「429」 | ⚠️ **大幅下降但未消除**：3 个成因，其中 2 个是上游容量，1 个是 admission 的 120s 长挂起 |
| 症状 C「慢」 | ❌ **仍然是主要问题**，且 ~99% 在上游；另发现 2 条**当前仍然坏死的路由** |
| **新发现（未修复）** | 🔴 slot1 `gpt-6.1-sol` 上游 502 拒绝（已独立复现）；slot3 `gpt-5.5` 100–220s 且间歇 502 |

---

## 1. 远端 bwg VPS（strict doctor @ 2026-10-02T13:39:40Z）

### 1.1 结构与契约：全绿

```
DOCTOR_CONTRACT_OK
drift=auto-update.sh / cpa-health.py / cpa_policy.py / cpa_provider_routes.json /
      cpa-admission.py / cpa-admission.json / cpa-admission.service /
      cpa-gateway.conf(x2)                    全部 MATCH   (9/9)
gateway-per-ip-concurrency=20   gateway-throttle-status=429   nginx-syntax=OK
admission-service=enabled-active  admission-health=OK  admission=OK
cooldown_state=none   luna_state=available   oauth_days_left=3
retained_overload_request_files=0   overload_markers=0
model_substitution_warnings_7d=0
CPA v8.0.9 @sha256:3ee53068…   container started 02:28:01Z   restart=0
部署 SHA == 仓库 HEAD（cpa-admission.py 2183d13c… / .json 24f19d55… / routes 485e967b…）
```

三条 lane 与设计一致：`chatgpt-oauth(2,4,120)` / `zhipu-coding-plan(3,4,120)` /
`deepseek-official(3,4,120)`，`retry_after_max_seconds=86400`。

### 1.2 429 分层（13h 窗口，共 34 条）

| 来源 | 条数 | 时间 | 性质 |
|---|---|---|---|
| `127.0.0.1` | **19** | 02:19–02:25 UTC | **本机上午压测产生的测试残留**，非生产 |
| `183.236.x.x`（操作者自有出口） | **15** | 01:00–02:59 UTC | 真实客户端 |

按小时：

```
02/Oct 00 tot=24  429=0  5xx=0  rej=0
02/Oct 01 tot=403 429=5  5xx=0  rej=4
02/Oct 02 tot=484 429=29 5xx=9  rej=7
02/Oct 03 tot=41  429=0  5xx=0  rej=0
02/Oct 05 tot=3   429=0  5xx=0  rej=0
02/Oct 07 tot=10  429=0  5xx=0  rej=0
02/Oct 12 tot=13  429=0  5xx=0  rej=0
02/Oct 13 tot=59  429=0  5xx=4  rej=0     ← 13:00–13:42 UTC（当前）
```

⇒ **自 03:00 UTC（11:00 CST）起，连接层/速率层 0 拒绝、429 = 0，持续约 10.7 小时。**
所有 nginx 层拒绝（`limit_conn=REJECTED` 5 条 + `limit_req=REJECTED` 6 条）都落在 01–02 UTC。

### 1.3 admission journal（近 12h，`lane_reject` 共 23 条）

| 小时(UTC) | reason | 条数 |
|---|---|---|
| 01 | `busy` / `downstream_gone` | 1 / 1 |
| 02 | **`busy`** / **`cooldown`** | 15 / 5 |
| 13 | `downstream_gone` | 1 |

- `cooldown` 5 条：`retry_after = 12432 / 12395 / 12395 / 11744 / 11705` 秒
  （**≈3.2–3.45 小时**），发生在 02:34–02:46 UTC。
- 容器日志（TZ=CST）在同一时刻给出根因：
  `usage_limit_reached … plan_type: plus … resets_at:1790920871`（10:33:13 CST = 02:33 UTC）。
- 13:32:36 UTC 的 `downstream_gone`：`model=gpt-6-luna waited_ms=20980 retry_after=1`
  ⇒ 客户端在 admission 队列里等了 21 秒后**自己断开**（对应 nginx 侧 5 条 499）。

### 1.4 症状 C 的远端量化

```
upstream_header_time (route=responses): n=738  min=0.001  p50=11.883  p90=41.697  p99=94.183  max=220.799
request_time        (route=responses): n=770  p50=21.406  p90=61.257  max=277.567
```

本地占比（用同一条 502 的两个计时器相减）：

```
13:30:23  request_time=4.709  upstream_time=4.532   → 本地 0.177s (3.8%)
13:31:35  request_time=4.184  upstream_time=4.008   → 本地 0.176s (4.2%)
```

⇒ 网关本地开销 0.15–0.25 s，**其余 96–99% 全在上游**。

### 1.5 13h 内全部 5xx（13 条，全部来自 `183.236.x.x`，全部 `route=responses`）

```
02:07:16  500  upstream=-   auth_status=502  bytes=177
02:09:07  500  upstream=-   auth_status=502  bytes=177
02:11:00  503  upstream=503 0.851s   bytes=128
02:11:02  502  upstream=502 0.867s   bytes=157
02:13:44  503  upstream=503 78.489s  bytes=128
02:13:47  502  upstream=502 ×3
02:28:01  503  upstream=503 0.005s   bytes=128
13:30:23  502  upstream=502 4.532s   bytes=103
13:30:37  503  upstream=503 0.015s   bytes=273
13:31:35  502  upstream=502 4.008s   bytes=103
13:31:36  503  upstream=503 0.015s   bytes=273
```

**13:30/13:31 那 4 条的根因（容器日志原文）**：

```
[21:30:23] 502 | 4.518s | upstream execution failed:
  provider=openai-compatible-ai.input.im model=gpt-6.1-sol auth=api_key=sk-…f1e7
  err={"error":{"message":"Upstream access forbidden, please contact administrator",
                "type":"upstream_error"}}
[21:30:37] 503 | 6ms      ← 紧随其后，auth 被标记不可用
```

### 1.6 🔴 新发现：slot 1 的 `gpt-6.1-sol` 是一条**坏死路由**（独立复现）

远端直接对上游发同型号请求（只读、极小 body）：

```
GET  https://ai.input.im/v1/models            → HTTP 200，13 个模型，
       其中 "gpt-6.1-sol" 在列（不在列的是 gpt-6.1-sol-input）
POST https://ai.input.im/v1/responses
     {"model":"gpt-6.1-sol","input":"hi","max_output_tokens":16}
                                              → HTTP 502
       {"error":{"message":"Upstream access forbidden, please contact administrator",
                 "type":"upstream_error"}}
```

即：**ai.input.im 在 `/v1/models` 里宣传 `gpt-6.1-sol`，但实际拒绝服务它。**
而路由清单 `scripts/remote/cpa_provider_routes.json` 的 slot 1 恰恰是：

```json
{ "name": "gpt-6.1-sol", "alias": "gpt-6.1-sol-input" }
```

并被投影进远端 `config.yaml` 的 `openai-compatibility[0]`（已核对部署物）。
本机 Cockpit 网关又把桌面别名 `gpt-5.6-terra` 重写成 `gpt-6.1-sol-input`
（`codex_provider_gateway_sidecars/36218dcc…/config.json`）⇒ **桌面选 `gpt-5.6-terra`
必然落到这条死路由**，表现为 502（随后 1 秒内 503）。

### 1.7 slot 3（`http://35.213.82.91:8003`）是延迟与错误源

桌面别名 `gpt-5.5` → `gpt-6.1-sol-91` → slot 3。今日实测：

```
502 ×3（latency 2108ms / 105270ms / 137149ms）
503 ×1（latency 80116ms）
200 多次，其中 5 次 ≥ 100s，最大 221821ms
```

桥本身可达（401 快速返回），但其上游调用经常 100 s 以上甚至失败。

---

## 2. 本机 Cockpit（`codex-api.log.2026-10-02`，Direct API 路径）

### 2.1 当日状态码

```
"status":200 → 234
"status":502 →  11
"status":429 →   6
"status":503 →   3
"status":404 →   1   "status":400 → 1
关键字计数：capacity=0  overloaded=0  usage_limit=0  rate_limit=0  auth_unavailable=0  retry=0
```

⇒ **本机侧从未出现「at capacity」字样**，也没有任何容量/限流关键字。

### 2.2 按模型的失败与延迟

| 模型 | 结果 |
|---|---|
| `gpt-6-luna` | 502 ×4（21:32:37–39，**与 Cockpit 21:32:41–53 重启重合 ⇒ 重启期 in-flight 被杀**） |
| `gpt-5.6-terra` | 502 ×4（21:30:23 / 21:31:35 真因=slot1 死路由；其余重启期）、503 ×2 |
| `gpt-6.1-sol` | 429 ×3 |
| `gpt-5.6-luna` | 429 ×3 |
| `gpt-5.5` | 502 ×3、503 ×1（全部指向 slot 3） |
| `glm-5.3` | 400 ×1 |

**429 的 latency 分布（关键）**：

```
0.17s / 0.87s                       ← 冷却期秒拒
120005 / 120020 / 120049 / 120006ms ← 恰好 120s = queue_timeout_seconds
```

⇒ 有 4 次是**在 admission 队列里被挂满 120 秒后才被 429 拒绝**，客户端白等两分钟。

**成功响应的端到端延迟**：

```
n=237  p50=32.6s  p90=82.9s  max=221.8s
```

### 2.3 本机补丁运行态（已核实为**生效**）

- 两个 sidecar 进程：PID 28012 / 17528，**CreationDate 2026-10-02 21:32:53/54**，
  ExecutablePath = `C:\Users\sciman\AppData\Local\Cockpit Tools\cockpit-cliproxy.exe`
  （44 315 136 B，mtime 09:42）⇒ **加载的正是 r2 补丁二进制**
  （同目录留有 `…v135-gate-r1-20261002.bak`，44314624 B，09:36，为被替换的 r1）。
- 磁盘 `config.json.request-retry=1`、`manifest.json.maxAccountConcurrency=0`
  **仍是生成器缺陷形状**，但 r2 在二进制入口强制 `RequestRetry=0` +
  `clampMaxAccountConcurrency`（默认 3），运行态生效。当日日志 `retry=0` 命中。

### 2.4 桌面当前实际配置

```
~/.codex/config.toml:  model_provider = "codex_local_access"
                       [model_providers] 只有一段：
                       base_url = "http://localhost:10909/v1"   ← Direct API（Provider Gateway）
                       wire_api = "responses"
                       experimental_bearer_token = agt_codex_…
                       model = "gpt-6.1-sol"  service_tier="default"  model_reasoning_effort="high"
~/.codex/auth.json:    auth_mode = "apikey"，仅 OPENAI_API_KEY=agt_codex_…
```

⇒ **桌面当前处于 Direct API 模式**，config.toml 中**不存在**任何 OAuth/chatgpt provider 段。

Direct OAuth（API 服务侧车 14185）现状：
`auth-dir = C:\Users\sciman\.cockpit_tools\codex_local_access_sidecar\auths` → **目录为空**，
`api-keys = []`。账号池实际在 `codex_accounts/`（3 个 OAuth `codex_*.json` + 10 个
`codex_apikey_*.json`）。
> 注：`.cockpit_tools` 与 `.antigravity_cockpit` 内容一致（同一目录的两种引用）。
> 记忆里「auths 为空」的结论**路径写错了**，但结论本身成立。

桌面可见受管模型（`backups/behavior/codex/…/provider-model/`）：
`gpt-6.1-sol, gpt-6-luna, gpt-5.6-luna, gpt-6-astra, gpt-5.6-sol, gpt-5.6-terra, gpt-5.5,
glm-5.3-flash, gpt-image-2.5, deepseek-flash, glm-5.3, gpt-6-astra-cii`
（`previous_model = gpt-6-luna`）

Cockpit 网关别名重写（`codex_provider_gateway_sidecars/36218dcc…/config.json`）：

```
gpt-5.6-sol   → deepseek-v4.1-flash      (slot1, 快)
gpt-5.6-terra → gpt-6.1-sol-input        (slot1 → 🔴 死路由)
gpt-5.5       → gpt-6.1-sol-91           (slot3 → 慢/502)
```

---

## 3. 逐症状裁定

### 症状 A「Selected model is at capacity. Please try a different model」

- 远端容器日志近 12h：`server_is_overloaded` **0** 次、`retained_overload_request_files=0`、
  `overload_markers=0`。
- 本机日志当日：`capacity` / `overloaded` **0** 次。
- ⇒ **这句话不由 bwg CPA / admission 产生。** 它是 Codex/ChatGPT 客户端对上游容量信号
  （503 或流内错误事件）的固定文案，客户端不自动重试。
- 当前桌面在 Direct API 模式下根本不会产生该文案；用户看到它时，请求走的是**另一条不经本栈的
  路径**（官方直连 / OAuth 模式），因此该症状属**上游账号容量**，本地无修复项。
- ⚠️ 但本栈确实存在**另一类硬失败**（502/503，见症状 C 与 §1.6），不要与 A 混为一谈。

### 症状 B「exceeded retry limit, last status: 429 Too Many Requests」

**已修复的部分（有前后对照）**：

| 放大器 | 状态 |
|---|---|
| nginx `limit_conn` 连接预算（旧 6 == lane 容量 6，抢先触顶） | ✅ 6→12→20，近 10.7h 连接层 0 拒绝 |
| 半开探针期秒回 429（`half_open_probe`） | ✅ `1ffcd45` 改为有界 FIFO 队列，02:00 UTC 后 0 次 |
| shed 恒返回 `Retry-After: 1` | ✅ `d35d24f` 改为诚实值 |
| 超长 Retry-After（曾见 210568s） | ✅ `d711c1d` 加上限（`retry_after_max_seconds=86400`） |
| 本机 `request-retry=1` 重试放大 | ✅ r2 补丁运行态强制 0 |
| CPA 各 provider `request-retry` | ✅ 部署 config.yaml 全为 `0` |

**仍然存在的 3 个成因**：

1. **上游单账号容量**（主因）：02:33 UTC `usage_limit_reached`（plus / 300min 窗口）⇒
   整条 `chatgpt-oauth` lane 冷却 **3.2–3.45 小时**（`retry_after` 实测 12432s）。
   **不是 bug，是设计上的诚实退避**；但一条 lane 全灭 = luna/sol 全部不可用。
2. **lane 队列满 `busy`**：02 小时 15 次（`waited_ms=0`、`retry_after=10` 秒拒）。
   根因是每请求太慢导致 in-flight 堆积（见 C）。
3. **`queue_timeout` 挂满 120s 后拒绝**（本机 4 次，latency 恰好 120.0s）。
   这是最像用户主观感受的一类：等两分钟 → 429。
   ⇒ 代码注释（`cpa-admission.py:496-510`）表明这是**刻意**设计——因为「观测到的客户端不会
   等 Retry-After，而是直接失败整个回合」，所以宁可挂住。**但代价是白等 120s。**
   这是一个**可讨论的取舍点**，不是回归。

### 症状 C「慢 / tps 低」

- 远端 TTFB p50 **11.88s** / p90 **41.70s**；端到端 request_time p50 **21.41s** / p90 **61.26s**。
- 本机 Direct API 端到端 p50 **32.6s** / p90 **82.9s** / max **221.8s**。
- 本地占比 0.15–0.25s（3.8–4.2%）⇒ **96%+ 在上游**。
- 上游瓶颈两类：
  - `chatgpt-oauth` lane：单一 ChatGPT Plus 账号，实测吞吐 3.3–4.6 req/min；
  - `slot3`（35.213.82.91:8003）：100–220 s 级。
- ⇒ **本地无可优化项**（这一点与 D 决策一致，且本轮数据再次印证）。

---

## 4. 仍然未修复 / 需要决策的项（按优先级）

| # | 问题 | 证据 | 可选动作 | 风险 |
|---|---|---|---|---|
| 1 | **slot1 `gpt-6.1-sol` 死路由**：上游宣传但 502 拒绝 | §1.6 独立复现 2 次 | 从 slot1 移除该 model（或移入 `optional_models`），或改桌面别名指向 | 改路由清单属**远端投影**，需 `-Apply`，有回滚保障 |
| 2 | **桌面暴露映射到死/慢上游的模型**：`gpt-5.6-terra`、`gpt-5.5` | §2.2 / §2.4 | 从桌面可见目录移除或改指向 | 本机配置，低风险 |
| 3 | **`queue_timeout` 120s 白等** | 本机 4 次 latency=120.0s | 缩短预算，或 busy 路径提前给诚实 Retry-After | 可能提高 shed 率（需权衡） |
| 4 | **单账号 lane 可被 `usage_limit_reached` 关停 3.4h** | §1.3 | 增加上游 OAuth 账号 / 分流 | 治本，需资源 |
| 5 | **slot3 100–220s** | §1.7 | 评估是否继续保留该明文例外 | 用户明确保留 |
| 6 | 本机磁盘 `request-retry=1` / `maxAccountConcurrency=0` | §2.3 | 运行态已补丁；每次 Cockpit 更新会再回退 | 等上游 PR #2677 合并 |

---

## 5. 可直接降低症状 C 的客户端侧杠杆（无需改远端）

1. `~/.codex/config.toml` 的 `model_reasoning_effort = "high"` → `"medium"`/`"low"`：
   对 sol/luna 的生成时长影响最直接（此前曾是 `"max"`）。
2. 需要速度时改选 `gpt-5.6-sol` 别名（→ `deepseek-v4.1-flash`，快 lane），
   而不是 `gpt-6.1-sol` / `gpt-6-luna`（OAuth lane，单账号瓶颈）。
3. `service_tier`：本机为 `"default"`；**注意**此前实测在 fq 路径上 `priority` 被上游回显为
   `default`（被剥离），故开 Fast 对 Direct API 无效，仅可能在官方直连生效。
4. 降低单会话请求频率——当前 in-flight 常态 6–16 而 lane 容量 6，请求越密越容易撞 `busy`。

---

# 6. 追加（21:55 CST）：除别名与 reasoning effort 外，仍应执行的修复与优化

数据源：本机 `~/.antigravity_cockpit/codex_local_access_logs.sqlite` 的 `request_logs`
（含 `error_category` / `latency_ms` / `requested_model` / `gateway_mode`，共 305 268 行），
远端 admission journal 24h 普查，nginx 24h 客户端分类。

## 6.1 8 天趋势（本机权威口径）

| 日期 | 总请求 | 成功 | 429 | 5xx | 其中 429 且 latency ≥115s |
|---|---|---|---|---|---|
| 09-26 | 1814 | 1304 | 37 | **458** | 0 |
| 09-27 | 307 | 253 | 31 | 19 | 4 |
| 09-28 | 1285 | 981 | 22 | **258** | 6 |
| 09-29 | 1032 | 932 | 42 | 48 | 0 |
| 09-30 | 108 | 73 | 0 | 30 | 0 |
| 10-01 | 2769 | 2556 | **75** | 83 | 9 |
| 10-02 | 307 | 275 | 14 | 15 | 4 |

- **5xx 已从 458/258 量级降到 15**（09-26、09-28 那两天是本栈修复前的状态）⇒ 修复有效。
- **429 在 10-01 达到峰值 75，10-02 为 14。**
- **`latency ≥115s` 那一列是纯本机产生的白等**，4 天出现（09-27/09-28/10-01/10-02），
  **每天 4–9 次**，是**唯一一类持续存在且完全可控**的 429。

## 6.2 今日 14 次 429 的完整归因（本机口径）

```
120005 / 120020 / 120049 / 120006 ms   ← 4 次 = 本机闸门 AccountConcurrencyWaitMs 等待超时
169 / 874 / 2867 / 386 / 392 / 402 / 409 / 462 ms  ← 10 次 = 快速拒绝
```

其中 4 次 120s 的：**同一时刻 nginx 访问日志里 429 = 0**（hour 03 UTC `429=0`）
⇒ 429 由本机生成，请求根本没发到 fq。

机制（r2 补丁 `provider_gateway_concurrency.go` → `admitDirectProviderAccount`）：

```
本地并发 > manifest.maxAccountConcurrency(=3, 由补丁钳制)
  → 进入 account 等待队列，deadline = startedAt + manifest.AccountConcurrencyWaitMs(=120000ms)
  → 超时 → selector.concurrencyExceededError(...) → HTTP 429 + Retry-After: 1
```

补丁自带单测 `TestDirectProviderConcurrencyWaitCancellationAndTimeout` 的 `timeout` 分支
即断言此路径返回 429，可自证。

**为什么是纯浪费**：客户端不会等 2 分钟（今日 journal 有
`reason=downstream_gone waited_ms=20980` 的主动断开）。等待 120s 后仍失败，只换来
更长的墙钟时间和更差的体验。

**触发条件**：桌面开了多智能体（`~/.codex/config.toml` 的 `[agents] enabled = true`、
`max_concurrent_threads_per_session = 2`），3 并发很容易被突破；本地 access sidecar 配置里
`codex.optimize-multi-agent-v2 = True` 也印证这一路径。

**修法（全部在本机补丁内，远端零改动）**：
1. 新增 `clampAccountConcurrencyWaitMs`（建议 20 000–30 000 ms）——**只减不增**；
2. 可选：`maxAccountConcurrency` 3→6，与 VPS lane 容量（2 inflight + 4 pending）对齐，
   把削峰交给 admission 的 FIFO 队列 + 诚实 Retry-After，而不是本地干等。

## 6.3 远端 admission 侧：**没有新的可修项**（本轮有数据支撑）

24h `lane_reject` 普查（全部集中在 `chatgpt-oauth`）：

```
reason=cooldown          17
reason=busy              17
reason=downstream_gone    2
reason=queue_timeout      0   ← 从不发生
```

`waited_ms`（成功入场）：`n=1070, p50=0, p90=27.6s, max=93.4s`
⇒ lane 平时**不饱和**，只有约 10% 的请求实质排队。

`upstream_result`：`capacity=true` 27 / `capacity=false` 995（2.6%）。

⇒ `max_pending` / `queue_timeout` 保持现状是有依据的，不再是「拍脑袋不改」。

## 6.4 诊断口径缺口（我们自己的代码，建议修）

nginx 24h 的 429 构成：**`127.0.0.1` 20 条（压测残留）+ 真实客户端 15 条**。
今早那次「429 归零」的错误读数，根因就是 doctor 只给 24h 总数、不给客户端分类与小时分布。

建议给 `cpa-health.py` 的 `gateway-statuses-current-log-24h` 增加：
(a) loopback vs external 客户端分类；(b) 按小时分桶；(c) `lane_reject` 的 reason 普查。
均为只读增强，不改门禁语义；但 doctor 输出被 guardrail 断言解析 ⇒ 必须**同一提交**
同步 `cpa-health.py` + `test_scripts.py` 断言 + runbook，并按 focused closeout 跑门禁。

## 6.5 可观测性缺口（零改动）

`usage_limit_reached` 会让整条 `chatgpt-oauth` lane **静默停摆 3.45 小时**（02:34–02:46 UTC，
`retry_after≈12400s`），期间没有任何主动提醒。建议把 doctor 的
`cooldown_state` / `oauth_days_left` 纳入**定期只读巡检**，提前告知而不是等撞上 429 再排查。

## 6.6 明确不建议动的项

| 项 | 理由 |
|---|---|
| CPA 把 provider auth 的 502 映射成 **500**（`auth_status=502` 那两条） | CPA 二进制行为，需上游修 |
| `save-cooldown-status: false` | 翻转会引入「陈旧冷却」风险，风险不对称 |
| `half_open_probe` 期间第 2 个 inflight 槽空转 | 刻意保守：探针即真实上游请求，放开等于把 1 路探针变 2 路真实流量 |
| `usage_limit_reached` 的 3.45h 冷却 | 诚实退避，行为正确 |

---

# 7. 执行记录（22:00–22:35 CST）

## 7.1 已完成

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 真 collection `accountConcurrencyWaitMs` 120000 → 45000 | ✅ 已落盘 | 备份 `codex_local_access.json.bak-waitcap-20261002`；脚本 `outputs/cap-local-gate-wait-20261002.py` |
| 2 | r3 二进制入口 `capAccountConcurrencyWaitMs` 封顶 45000 ms | ✅ 已落盘 | SHA `72860fd9…`；`go test . -count=1` PASS 31.022s（含 4 新单测）；`go vet` 干净；gofmt 8 文件全 CLEAN |
| 3 | doctor 429 归因新增客户端分层 + 小时分桶 | ✅ 已提交 + 远端实测 | `5421f31`；`DOCTOR_CONTRACT_OK`，`statuses_by_client_class` / `statuses_by_hour` 已产出 |
| 4 | 每日只读巡检自动化 | ⛔ **已按用户指令删除** | 曾创建 id `9ebec7b1-b6a8-4f7d-a1c0-51048cf7c5e0`；仓库内无其专属代码（prompt 自包含） |

## 7.2 过程中的一次关键纠正

22:00 曾判断「`accountConcurrencyWaitMs` 会被生成器正确写入 manifest，因此是纯配置项，
不需要二进制补丁」。**该结论是错的**，实现时读源码发现：

```
build_provider_gateway_collection_for_profile   (codex_local_access_provider_gateway.rs:1837)
  let mut collection = new_empty_local_access_collection()?;   // 并发 0 / 等待 120000（结构体默认）
  apply_provider_gateway_template_settings(&mut collection, &template)  // :1566，只复制 18 个无关字段
```

`max_account_concurrency` 与 `account_concurrency_wait_ms` **都不在那 19 个字段里**
⇒ provider gateway sidecar 恒收到默认值，用户在 UI 里的设置对它一概无效。
只有 API 服务 sidecar 直接读真 collection。

⇒ 只改 settings 文件**修不到产生 120 s 白等的那条路径**（那些 429 的 `apiKeyId` 正是
`provider_gateway_codex_apikey_…`）。因此补了 r3 的二进制封顶，并把 runbook 里的错误结论
改写成「两侧生效路径对照表 + 根因引用（含文件:行号）」。

## 7.3 r3 行为判据（为什么没有重跑受控回放）

入口对 `m` 的原地修改路径**已由 r2 在生产中证明有效**：若 `clampMaxAccountConcurrency`
未生效，`maxAccountConcurrency` 会停在 0 使闸门整体禁用，本机就不会出现
`account_concurrency_exceeded`；而实测确实出现了等满 120 s 的该错误 ⇒ 闸门带正并发值在跑，
即入口钳制生效。r3 复用同一条路径与同一个 `m`，因此 `go test` + 该生产证据共同构成判据。

## 7.4 投影 + 验收：**已完成**（22:30 用户重启 Cockpit）

**运行加载已确认。** 应用 22:30:21 重启，两个 sidecar 22:30:21/22 启动，
`ExecutablePath` 均为安装目录 exe、SHA `72860FD9…`（r3）：

```
PID=35944  --config ...\codex_local_access_sidecar\config.json
PID=12564  --config ...\codex_provider_gateway_sidecars\36218dcc…\config.json
```

一个必须记住的事实：**这次重启只重新生成了 API 服务 sidecar 的 manifest**
（已变 `accountConcurrencyWaitMs=45000`），**provider gateway 的 manifest 仍是 09:38 的
120000 / 0，生成器没有重写它**。⇒ 二进制兜底不是冗余，是唯一生效路径；也说明**不能靠
"重启后看文件值"验收**。

### 受控对照验收（`ACCEPTANCE_PASS`）

`outputs/gate-wait-cap-acceptance-20261002.py`：本地桩上游（接受连接、永不回包）+
线上 provider-gateway config/manifest 的 scratch 副本（端口改 19109、去掉 `proxy-url`、
抬高 stream 超时）。**两次运行输入完全相同**（manifest 里仍是 120000），**唯一变量是二进制**。
3 条并发请求占满 `maxAccountConcurrency=3`，再发第 4 条测其 429 延迟。零上游配额消耗。

| 二进制 | 第 4 条 | 错误体 |
|---|---|---|
| **r3（封顶 45000 ms）** | **429 @ 45.003 s** | 「等待 **45.0** 秒后仍没有可用槽位」 |
| r2（无封顶） | 429 @ 120.002 s | 「等待 **120.0** 秒后仍没有可用槽位」 |

⇒ 封顶**确实覆盖了陈旧的 manifest 值**，且只缩小等待、不改变拒绝语义。

### 受控实战回放（`LIVE_REPLAY_PASS`）

真实链路 10909 → fq.sciman.top → CPA → zhipu lane：`glm-5.3` →
**HTTP 200 / `status:"completed"` / 10.57 s**（零 OAuth 配额）。

### 重启后现场

```
request_logs since 22:30 : non-200 = 0
成功请求                  : n=10, p50 27.3s, max 159.4s   ← 慢仍在上游
今日 429 latency 桶       : lt5s:10, ge115s:4             ← 后者全是 10:51/11:02 的历史事件
```

## 7.5 最终复验快照（22:22 CST）

```
CPA v8.0.10 @sha256:0b007a6a…   container started 14:14:24Z   restart=0
DOCTOR_CONTRACT_OK              9/9 drift MATCH              cooldown_state=none
statuses_by_client_class = external/429: 15  loopback/429: 20   ← 新键在 v8.0.10 下同样生效
statuses_by_hour         = 00..14 九个桶，含 limit_rejected
admission_429_shape      = {local_nginx_429: 11, likely_admission_fast_203: 6,
                            numeric_upstream_429_other: 18}
```

external 429 仍为 15、loopback 仍为 20 —— 与 13:39Z 那次完全一致，说明这两个数字全部来自
上午的窗口，**下午到夜间没有新增**。

---

# 8. 上游 issue/PR 执行记录（22:50–23:15 CST）

## 8.1 已发布

| 目标 | 动作 | 链接 |
|---|---|---|
| #2677（PR） | 评论：CI 处于 `action_required`（等维护者批准 fork 分支的 workflow），附本机等价补丁的受控对照（45.003s vs 120.002s） | `#issuecomment-5955012637` |
| #2676（issue） | 评论：精确根因（`apply_provider_gateway_template_settings` @ `:1566` 只复制 18 个字段）+ `write_secret_string_atomic_if_changed` 导致 manifest 从不重写（"文件陈旧"是自证症状）+ 量化影响 | `#issuecomment-5955015256` |
| #2658（issue） | 评论 v1.3.65 已修（`writeProviderGatewayResponsesStream` 现在空行处 + 末尾 err 分支都 flush）+ **关闭（reason=completed）** | `#issuecomment-5955016095` |

## 8.2 新建 PR #2697（实现 #2685）

- 分支 `sciman-top:feat/provider-gateway-account-admission` → base `main`，
  **5 文件 632+/3−**，`MERGEABLE`。
- **移植零漂移的证据**：main 与 v1.3.65 的 `provider_gateway.go` / `manifest_policy.go`
  sha256 逐字节相同；`0b6514b` 是 main 的直接父提交（GitHub compare: ahead=1, behind=0）。
- 在独立 `git worktree`（干净 v1.3.65 基底）里**只**移植特性：
  `provider_gateway.go` + `manifest_policy.go`（剔除 clamp/cap 块）+ 3 个新文件；
  **不动 `main.go`**，并用 grep 断言无生成器 workaround 泄漏。
- 验证：gofmt 5 文件全 CLEAN、`go vet ./...` 干净、`go test . -count=1` **ok 27.513s**。
- 正文明确注明：**闸门在 #2677 落地前运行时是 no-op**（`MaxAccountConcurrency<=0` 即禁用）。
- 该 PR 同样落在 `action_required`，与 #2677 一样等维护者批准 CI。

## 8.3 边界（重要）

**本机 r2/r3 补丁 ≠ 上游 PR。** 本机额外含 4 处生成器 workaround
（`RequestRetry=0`、`StreamBootstrapBuffering=false`、`clampMaxAccountConcurrency`、
`capAccountConcurrencyWaitMs`）——它们是绕过生成器缺陷的临时手段，**不进上游**；
#2697 只包含"按账号限流 + 尊重上游 Retry-After"这一项真实特性。

## 8.4 顺带修正

本报告与 runbook、记忆里此前写的"19 个无关字段"实测为 **18** 个（main 上
`grep -c 'collection\.'`），已全部更正。
