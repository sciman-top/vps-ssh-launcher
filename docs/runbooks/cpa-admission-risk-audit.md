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

三者不可互相替代：审计通过**不代表**上游健康（上游容量是外部事实），归因全绿**不代表**
配置自洽。

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
| `capacity-probe-window` | warn | `probe_bytes >= 262144` | 容量标记只在前 `probe_bytes` 字节里解析；窗口太小 ⇒ **流到一半才降级的上游永远不会开冷却**，客户端继续重试打一个已经过载的账号 |

## 3. 姿态半边（连接"宣告"与"实测"）

窗口锚定在**本机日志里最新一行**，不是 `now`（避免机器休眠/时钟漂移把窗口算空）。

| 发现码 | 严重度 | 判据 |
|---|---|---|
| `posture-no-traffic` | info | 窗口内 0 行 ⇒ **明确声明本次连接是空转**，绿灯不构成任何证明 |
| `advertised-failing-route` | fail | 某宣告名失败率 ≥ 50% 且失败数 ≥ 5 |
| `advertised-degraded-route` | warn / info | 失败率 20–50% → warn；< 20% → info |
| `failing-unadvertised-model` | warn | 失败数 ≥ 5 但没有任何路由宣告它 ⇒ 客户端在点一个网关解析不出的名字 |

**为什么用失败率而不是绝对数**：OAuth lane 只有**一个**订阅账号，账号忙的时候 503 是
预期行为，不是路由缺陷。用绝对数会把正常的上游过载误报成"路由坏了"；失败率把
"这条路由基本不可用"和"上游偶尔忙"分开。

**归因边界**：`advertised-failing-route` 证明的是"**这个名字**在失败"，不是"**这条上游**
坏了"。名字 → 槽位的映射来自清单，清单不知道上游是不是拒绝服务。要落到具体上游，
回到 `cpa_failure_triage.py` 的 `dead_route` 层和远端 admission journal
（`upstream_result ... model=<名> status=<码>`）。

## 4. 判读与处置

1. 先看 `posture-no-traffic`：有它就别拿绿灯下结论。
2. `advertised-failing-route` ⇒ 走 `bwg-cpa-route-change` 技能的闭环（改清单 → 门禁 →
   提交 → `-Apply` → 复跑 doctor）。**退役一个裸名不会同步 Cockpit 的
   `codex_model_providers.json`**——那是另一层目录，桌面仍可能选到已失效的名字，
   必须同时在 Cockpit UI 里清掉；用
   `scripts/cockpit_provider_health.py` 的 `desktop-model-unroutable` 检查确认
   桌面目录与所选网关的可路由集合一致。
3. `lane-cooldown-server-cap` 是**接受项**，不是待修项：它描述的是"尊重上游退避"与
   "可用性"之间的取舍。出现长时间 429 时先读远端
   `curl -s http://127.0.0.1:8318/healthz` 的 `cooldown_remaining`，再决定是否等待。
4. `capacity-probe-window` 想收紧时，改的是 `cpa-admission.json` 的 `probe_bytes`，
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
