# CPA admission 风险与契约审计

适用问题：**共享官方账号 admission 的配置本身，是否在封号 / 限流 / 降智方向上是安全、
自洽、可解释的？** 以及：**它仍在对外宣告的模型名里，有没有已经明显失效的？**

工具：`scripts/cpa_admission_risk_audit.py`（只读，离线可跑）。

    ./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --hours 24
    ./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --skip-posture
    ./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --hours 24 --json
    ./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --strict

退出码：`0` 无失败项；`1` 存在失败项（`--strict` 时警告也算失败）；`2` 输入不可读。

## 1. 与相邻工具的职责边界（先读这段，别重复劳动）

| 工具 | 回答的问题 | 输入 |
|---|---|---|
| `scripts/cpa_failure_triage.py` | **这次请求**为什么失败（本机闸门 / admission 队列 / 上游容量 / 死路由） | Cockpit sqlite + 可选 doctor 输出 |
| `scripts/cpa_admission_risk_audit.py` | **这份配置**安不安全、**宣告的名字**有没有失效的 | `cpa-admission.json` + `cpa_provider_routes.json` + Cockpit sqlite |
| `scripts/cpa-health.py`（远端） | 上游现在能不能生成（真实探针） | 远端运行时 |
| `scripts/cpa_policy.py`（远端） | 已部署配置的语义是否合规 | 远端 config + 清单 |
| `scripts/cockpit_provider_health.py` | 本机 Cockpit 侧车/provider 接线对不对 | `~/.codex` + `~/.antigravity_cockpit` |
| `scripts/cpa_error_dump_forensics.py` | 上游的容量响应里**有没有闸门认不出的信号** | 保留的错误转储（本机或经 ssh 在主机上算） |

三者不可互相替代：审计通过**不代表**上游健康（上游容量是外部事实），归因全绿**不代表**
配置自洽。

远端运行态用严格 doctor 读取，避免把 Nginx 的状态形状当成 admission 内因：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

输出中的 `==admission-journal-24h==` 是只读聚合：`admission_lane_reject_reasons` 说明
本地队列/冷却/忙拒绝，`admission_capacity_statuses` 与 `admission_capacity_models` 说明
已到达 CPA 的上游容量响应；不输出请求 ID、提示词或凭据。它与 Nginx 的
`admission_429_shape`、本机 `cpa_failure_triage.py` 联看，才能把一次 429 定到正确层。

## 2. 契约半边（离线，逐条判据）

| 发现码 | 严重度 | 判据 | 为什么重要 |
|---|---|---|---|
| `admission-no-lanes` | fail | `lanes` 非空 | 空 lane 表 = 闸门形同不存在 |
| `alias-multi-slot` | fail | 同一 alias 只被一条路由宣告 | 客户端无法预知自己会落到哪个上游 |
| `lane-cooldown-ladder-mismatch` | fail | `cooldown_schedule_seconds[-1] == cooldown_cap_seconds` | 阶梯与声明上限不一致 ⇒ 实际退避不可预测 |
| `lane-cooldown-ladder-missing` | fail | 阶梯存在 | 没有阶梯就没有退避 |
| `lane-cooldown-server-cap` | warn | `retry_after_max_seconds > cooldown_cap_seconds` | **上游 `Retry-After` 能把整条 lane 钉到远超阶梯上限**（本栈 = 86400s vs 900s）。尊重上游退避是防封号的正确选择，但必须知道它意味着"整条 lane 可能数小时全灭"，而不是当作客户端 bug |
| `lane-capacity-status-missing` | fail | `capacity_statuses ⊇ {429, 503}` | 少一个就漏掉一类容量失败 |
| `lane-capacity-marker-missing` | fail | `capacity_markers ⊇ {server_is_overloaded, usage_limit_reached}` | `stream-bootstrap-buffering=false` 后 CPA 会回 **200 + 体内容量标记**，只看状态码会把过载当成功 |
| `lane-model-not-advertised` | fail | lane 的每个 model 都被某条路由宣告 | 陈旧 lane 成员一旦失败会开**整条 lane** 的冷却（跨模型污染） |
| `lane-model-in-multiple-lanes` | fail | 一个 model 只属一条 lane | 同上，且冷却语义互相干扰 |
| `oauth-exclusion-violation` | fail | `gpt 路由 alias ⊆ oauth_exclusions` | 防 API-key 路由被订阅账号承载 |
| `codex-key-exclusion-violation` | fail | `(gpt 路由 ∪ oauth alias) ⊆ codex_api_key_exclusions` | 防订阅模型被共享 API key 承载 |
| `lane-route-coverage-missing` | fail | 每条声明 `admission_lane` 的路由其**全部** alias 都在该 lane 的 `models` 里；且该 lane 确实存在 | 清单声明"走共享账号"，lane 才是限流的实现。两者之间没有别的联接：在路由上新增一个没进 lane 的名字 = 这个名字**无并发上限、无容量熔断**地打共享账号，正是自伤封号的形状 |
| `capacity-probe-window` | warn | `probe_bytes >= 262144` | 容量标记只在前 `probe_bytes` 字节里解析；窗口太小 ⇒ **流到一半才降级的上游永远不会开冷却**，客户端继续重试打一个已经过载的账号 |

## 3. 姿态半边（连接"宣告"与"实测"）

窗口锚定在**本机日志里最新一行**，不是 `now`（避免机器休眠/时钟漂移把窗口算空）。

| 发现码 | 严重度 | 判据 |
|---|---|---|
| `posture-no-traffic` | info | 窗口内 0 行 ⇒ **明确声明本次连接是空转**，绿灯不构成任何证明 |
| `advertised-failing-route` | fail | 失败率 ≥ 50% 且失败数 ≥ 5，**且该模型窗口内最新一行也是失败**（消息带 `last failure N min`） |
| `advertised-failing-route-stale` | warn | 失败率 ≥ 50% 但该模型窗口内**最新一行成功** ⇒ 历史故障，不是活故障 |
| `advertised-degraded-route` | warn / info | 失败率 20–50% → warn；< 20% → info（同样带 `last failure N min`） |
| `failing-unadvertised-model` | warn | 失败数 ≥ 5 但没有任何路由宣告它 ⇒ 客户端在点一个网关解析不出的名字 |
| `attribution-boundary` | info | 有路由命中时追加：**名字→槽位的映射只是清单投影，不是"哪条上游服务的"证明** |
| `late-stream-failures` | info | 窗口内 `success=0` 且 HTTP 2xx 的行数 ⇒ **这是决定要不要放宽容量探测窗口的唯一证据** |

**失败口径 = `success=0`，不是状态码。** `success` 是 app 自己的判决且从不为空；
实测全库 37,150 行没有 `http_status`，其中 **36,206 行是成功的** ⇒ 把"没有状态"当失败
会让任何回看够长的窗口虚高。只有表结构里没有 `success` 列时才退回状态码口径。
`requested_model` 解析不出来的行归入 `other` 桶，**不参与**逐模型判定（它不是模型名，
否则会变成一条永久警告，把真发现埋掉）。

**为什么用失败率而不是绝对数**：OAuth lane 只有**一个**订阅账号，账号忙的时候 503 是
预期行为，不是路由缺陷。用绝对数会把正常的上游过载误报成"路由坏了"；失败率把
"这条路由基本不可用"和"上游偶尔忙"分开。

**归因边界**：`advertised-failing-route` 证明的是"**这个名字**在失败"，不是"**这条上游**
坏了"。名字 → 槽位的映射来自清单，清单不知道上游是不是拒绝服务，也不知道客户端侧
别名层有没有把名字改写掉。**实测反例（2026-10-04）**：客户端记 `gpt-5.6-terra` 502/503，
网关 journal 同一秒记的是 `gpt-6.1-sol-input`——逐条 1:1 对齐后确认是同一批请求，
清单却把 terra 归到槽位 3。所以有命中时工具会追加一条 INFO `attribution-boundary`：
**改路由前先到网关 journal 确认真正服务的模型**
（`upstream_result ... model=<名> status=<码>`）。跨层对齐按**时间 + 状态 + 计数**，
不要按名字。

**窗口长度的坑（工具已内置判据）**：姿态半边锚定在本机日志最新一行，所以 `--hours 720`
会把**不同时期**混在一起。实测例子：`deepseek-v4.1-flash` 在 720h 窗口报"失败 259/273 = 95%"，
但同一名字在**网关侧 72h 是 98% 成功**（130×200）——那条 FAIL 描述的是 30 天里更早的一段。

⇒ 每条逐模型发现现在都带 **`last failure N min before the newest row`**：
**先看这个数**。实测对照（同一份 24h 窗口）：

| 名字 | 失败率 | 最后一次失败 | 判读 |
|---|---|---|---|
| `gpt-5.6-terra` | 90/126（71%） | **647 分钟前** | 全部落在切换前的时段 ⇒ **不是活故障** |
| `gpt-6.1-sol` | 6/225（3%） | **7 分钟前** | 就是当下的上游过载 |

所以：**定位用 24h 以内**。从 2026-10-04 起 FAIL 的判据已收紧为「**该模型窗口内最新一行
也是失败**」：只被历史故障命中、之后已服务成功过的名字改报 `advertised-failing-route-stale`
（warn），**不再让 `--hours 24` 这条标准判据长期以退出码 1 收场**（实测：`gpt-5.6-terra`
24h 失败 71%，但最新一次请求成功、最后一次失败在 656 分钟前 —— 那是切换前的时段）。
反过来，若某名字在「最近一次成功之后**又**失败」，仍按活故障报 FAIL。

## 4. 判读与处置

1. 先看 `posture-no-traffic`：有它就别拿绿灯下结论。
2. `lane-route-coverage-missing` ⇒ **最高优先级**。这是"某个名字绕过共享账号闸门"的唯一
   机器判据：要么在 `cpa-admission.json` 对应 lane 的 `models` 里补上该 alias，要么把它
   从路由里撤掉。它属于契约变更——同一提交改 `.json`（若动了 `cpa-admission.py` 还要
   同改 `.py`）+ 本页；**改了这两个文件必须 `-Apply`**，否则 doctor 的 `projection-drift`
   会用 HEAD blob 比对已部署文件并报漂移。
3. `advertised-failing-route` ⇒ 走 `bwg-cpa-route-change` 技能的闭环（改清单 → 门禁 →
   提交 → `-Apply` → 复跑 doctor）。**退役一个裸名不会同步 Cockpit 的
   `codex_model_providers.json`**——那是另一层目录，桌面仍可能选到已失效的名字，
   必须同时在 Cockpit UI 里清掉；用
   `scripts/cockpit_provider_health.py` 的 `desktop-model-unroutable` 检查确认
   桌面目录与所选网关的可路由集合一致。
4. `advertised-failing-route-stale` ⇒ 先别改路由。它的最新一次请求是成功的，说明故障
   窗口已过；确认 `last failure N min` 与"最近一次成功"的先后，再决定是否需要动作。
5. `lane-cooldown-server-cap` 是**接受项**，不是待修项：它描述的是"尊重上游退避"与
   "可用性"之间的取舍。出现长时间 429 时先读远端
   `curl -s http://127.0.0.1:8318/healthz` 的 `cooldown_remaining`，再决定是否等待。
6. `capacity-probe-window` 想收紧时，改的是 `cpa-admission.json` 的 `probe_bytes`，
   属于**契约变更**：同一提交同步 `.py` + `.json` + 测试 + 本页。

## 5. Do / Don't

**Do**

- 把它当**配置体检**用：改完路由清单 / admission 参数后跑一次，和 doctor 一起作为证据。
- 报数时永远给"时间窗 + 本机口径 + 失败率"。

**Don't**

- 不要用它的绿灯否定上游问题——上游容量是外部事实。
- 不要因为它报了 `lane-cooldown-server-cap` 就去把 `retry_after_max_seconds` 调小：
  那会让本地在账号配额耗尽时继续压上游，正是防封号要避免的方向。
- 不要只退役 CPA 侧裸名就以为客户端不会再选到它。
