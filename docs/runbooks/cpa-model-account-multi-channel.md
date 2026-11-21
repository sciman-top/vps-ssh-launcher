# 多 OAuth 账号 + 多 API key 渠道：封号 / 限流 / 降智 / 吐字 的判读与操作

适用问题：**把若干订阅账号（ChatGPT/Codex、Antigravity、WorkBuddy CN/INTL、Claude）与若干
API key 渠道同时挂在一台 CPA 上，怎样不把账号打死、不把自己限死、不让模型偷偷降级、
不让用户等字等到放弃。**

本页是**判读与操作手册**（什么信号说明什么、该动哪一层），不替代证据台账：
实际值以 [20261116 复审](../change-evidence/20261116-bwg-cpa-multi-account-risk-review.md)
与现场只读探针为准。工具与判据边界见
[cpa-admission-risk-audit](cpa-admission-risk-audit.md)、
[cpa-failure-triage](cpa-failure-triage.md)、
[cpa-context-speed-budget](cpa-context-speed-budget.md)。

---

## 0. 一句话结论

**网关能做的只有三件事**：把请求**摊开**（每条共享账号 lane 有并发/速率上限）、把失败
**归到正确的层**（模型 / 凭据 / lane / 客户端）、把最强信号**立刻停手**（长期限制不骑阶梯）。
网关**做不到**的是"让上游不封你"，也**不该做**的是改写 prompt 前缀去追缓存命中率。

四类风险的杠杆分配：

| 风险 | 真正的主导变量 | 网关杠杆 | 网关无责的部分 |
|---|---|---|---|
| 封号 | 账号画像、IP 信誉、第三方反向代理行为、上游政策 | 并发上限、每请求凭据数上限、长期限制立刻停手 | 政策判定、申诉、支付与设备关联 |
| 限流 | 每（凭据 × 模型）窗口、池化别名扩散 | 冷却作用域、退避时长、探测节奏 | 上游真实容量 |
| 降智 | 上游响应模型、客户端请求形态 | 只做**观测**（`model_substitution`），不改写 | 模型选择本身 |
| 吐字慢 | **上游思考时长、首包与稳定前缀** | 排队时延、连接复用、bootstrap 重试、子任务亲和 | prefill 物理成本 |

---

## 1. 账号面：什么算"账号级信号"

只有下面这些才是账号可能出事的证据；**其余全部不是**。

| 是 | 不是（常见误判） |
|---|---|
| `TOS_VIOLATION` / `POLICY_VIOLATION` / `SERVICE_DISABLED`（Google `ErrorInfo.reason`） | 边缘/WAF 的 HTML/纯文本 403 | 
| `account has been suspended/disabled` 等停用措辞（任意 `message`/`reason` 字段） | WorkBuddy 插件**签到**日志里的 `403 growth system is only available for personal users` |
| `invalid_grant`（刷新令牌失效） | `429`、`6004`、`6008`、`14003`（频率/额度族，可恢复） |
| `code 11140`（WorkBuddy 账号异常行为） | fallback 腿返回的裸 401/403 JSON |
| 同一账号在**官方客户端**也不可用 | 网关侧 429、客户端超时、SSE 半截 |

**纪律**：出现"是"列的信号才进入处置（停流 / 隔离 / 申诉）；出现"不是"列时，
**不得**做预防性换号、清冷却或提并发——那是在用更大的风险换一个不存在的收益。

**长期限制的处置层级**（由内到外）：

1. **凭据级**：上游自己把该凭据 `disabled`（`v8.0.75` 起 `TOS_VIOLATION` 会这么做，OAuth
   刷新保留，可直接申诉）；插件（WorkBuddy）把 11140 隔离并注明"插件无法解除"。
2. **lane 级**：admission 锁 `account_restricted`，等人工复核。
3. **池级**：上游 `oauth.providers.antigravity.tos-pause-window` 默认 `0`，但本栈已按
   2026-11-17 裁决显式钉定为 `"2h"`（并由 `cpa_policy.py` 门禁校验），用于在池内出现首条
   `403 TOS_VIOLATION` 时立即暂停同池其余凭据 2 小时，防止自动化扫池连坐。如人工核验确认为
   单号孤立事件、需要提前恢复，按 [cpa-ban-throttle-incident-response](cpa-ban-throttle-incident-response.md)
   的保 inode 释放流程在事故控制下临时翻回 `"0"`，不得借日常投影静默归零。

---

## 2. 限流面：先问"这个冷却作用在谁身上"

三层作用域，判读顺序固定：

| 作用域 | 表现 | 谁负责 | 什么时候该用 |
|---|---|---|---|
| 模型 | `/healthz` → `model_cooldowns` 非空、`cooldown_scope=model` | admission | 上游明说"单个模型"（`selected model is at capacity`、6004、池化别名） |
| 凭据 | 该账号在这条 lane 上整体不可用，其他 lane 正常 | CPA 凭据池 | 账号级 429 / 配额 |
| lane | `cooldown_scope=lane`，整条 lane 429 且带 `Retry-After` | admission | 账号级过载、`server_is_overloaded`、`RESOURCE_EXHAUSTED` |

模型级窗口可单独存在：被点名的模型可能按其上游 `Retry-After` 保留冷却，而其他模型
仍可服务。读 `/healthz.model_cooldowns`，不能因兄弟模型成功就判定该窗口不存在；
也不能为了尽快恢复而换号、换 IP 或缩短真实等待。模型冷却默认受
`retry_after_max_seconds` 约束；Antigravity 尚无模型级容量标记，其 429/503 可停整条
`strict_backoff` lane 并保留完整正等待。这不是“最多 24 小时”的保证，也不是缩短
真等待的理由。

**池化别名例外（2026-11-16；当前无池化）**：一个模型名同时由两个 provider 承载时，
**按名字冻结整条 lane 会顺手删掉另一个合法承载路由**。admission 因此把这类信号收窄到
模型作用域并封顶 300 s（`pooled_models` + `POOLED_LANE_MODEL_COOLDOWN_SECONDS`）；
真正的"每（凭据 × 模型）"park 仍由插件注册表持有。2026-11-20 OpenCode 槽退役后，
`deepseek-v4.1-flash` 只由 WorkBuddy 提供，`pooled_aliases` 为空；不能用上述例外
自动恢复或轮换其他 OAuth 账号。

**不要做的事**：

- 不要为了"少 429"把 `retry_after_max_seconds` 调小 —— 那是让本地在上游配额耗尽后继续压上游。
- 不要在 6004 上镜像上游的整段 reset（实测一次 22254 s）—— 那是按模型名删备份路由。
- 不要提高 `max-retry-credentials` 去"吸收"限流 —— 每多一个凭据就是多一个账号被同一请求连坐。

**请求级止损与会话重绑边界（`oauth.request-scoped-errors` & `session-affinity-rebind-on`）**：

- `routing.session-affinity-rebind-on` 三个取值语义：`any`（上游默认）在任何失败含瞬态 5xx
  时都重绑凭据；`quota`（本栈钉定）仅在 429/403 等配额/鉴权/永久不可用信号时重绑、瞬态 5xx
  留在原凭据保缓存；`never` 永不换号。
- 当前生产若仍是 `max-retry-credentials: 3` + `session-affinity-rebind-on: quota`，一旦 Antigravity 收到
  Google WAF/内容过滤返回的**裸 `429 RESOURCE_EXHAUSTED`（无 `details`）**，CPA 默认会当成配额
  冷却首号并把同一标红 payload 重放到最多 3 个账号。本栈通过 `oauth.request-scoped-errors.antigravity`
  （正则 `(?s)"status"\s*:\s*"RESOURCE_EXHAUSTED"\s*\}\s*\}\s*$`，`action: "stop"`）在首号立即止损、
  不冷却不换号，同时保留 `cpa-admission` 的 60 s 车道退避；带 `details`（`ErrorInfo`/`RetryInfo`）
  的真实配额 429 不受影响，仍正常换号。
- `oauth.request-scoped-errors.codex` 同步匹配单账号 Codex 车道偶发的裸
  `{"detail":"Rate limit exceeded"}` 429（`action: "stop"`），避免误触发 CPA 指数配额冷却锁死整号，
  同时不影响携带 `usage_limit_reached` / `resets_in_seconds` 的真实配额模型级冷却。

---

## 3. 降智面：只观测，不改写

医生台的 `model_substitution` 记录"请求的名字"与"上游回显的名字"。已知且已审查的等价对：
`gemini-3.8-flash(-n) <== gemini-3.8-flash-high`、`gemini-4-hydrogen <== gemini-4-hydrogen-xhigh`、
`gemini-4-carbon <== gemini-4-carbon-high`、`gemini-4-argon(-trial) <== gemini-4-argon-high`、
`gemini-4-neon <== gemini-4-neon-xhigh`、`hy4-flash-free <== hy4flash-free`
（上游把 `-high`/`-xhigh` 拆入 `thinkingLevel` 后只回显基础名或 `-n`/`-trial` 后缀，**不是降级**；历史行 `hy4-free <== hy4free`
属于 2026-11-17 更名前退役的上游）。

出现**未知**变体对时：先看该名字的 `first_text_ms` 与输出长度分布是否同步劣化，
再去上游 release / issue 找对应说明。**网关不做别名改写**：改写会让缓存键变化、
让 A/B 结论失效，且把"观测"变成"猜测"。

---

## 4. 吐字面：三个可动项，一个禁区

| 可动项 | 条件 | 手段 |
|---|---|---|
| 排队时延 | `waited_ms` p90 > 50 ms 且 `pending` 常年非零 | 调整 inflight/queue 预算（属契约变更，需与测试同提交） |
| 首字前失败重放 | 首字节前失败被当成"死轮" | `requests.streaming.bootstrap-retries = 1`（本栈已钉住） |
| 连接复用与压缩 | 大请求体（1 MB+）上传占满首字预算、或每轮新建跨海 TLS 握手 | Codex `upstream-request-compression`；`api-keys.openai-compatibility` 的 `connection-pool: {}`；Antigravity 每凭据隔离 `oauth.providers.antigravity.connection-pool`（`enabled: true`、`idle-conn-timeout: "30s"`、`max-idle-conns-per-host: 2`，按 `auth.ID` 严格隔离、不跨号共享连接）；`requests.tls-handshake-timeout-seconds: 15` |
| 子任务缓存与账号扩散 | 多子任务共享父会话前缀、却分落到不同凭据 | 目标为 `session-affinity=true`、TTL `6h`、`session-affinity-subagents=true`；前两项已在 bwg 加载，子任务项仍待独立投影与自然流量 A/B。保持模型不变，新工作流另开父会话 |

**禁区**：任何在网关层改写 prompt 前缀的动作（截断旧消息、重排 system/tools、注入摘要）。
命中率已在 90–99%，改写把 90% 打到 0，同时把单账号每分钟 token 消耗抬一个数量级——
**那才是最容易触发上游限流的自伤动作**。

客户端侧（收益最大、风险最低）：新任务开新会话；压缩时带 `X-Session-ID`（上游 `84534584`
说明 Merkle LCP 匹配要求至少 2 轮相同尾部）；不要在同一会话里换模型。

---

## 5. 渠道组合的选型原则

| 渠道类型 | 典型 | 风险特征 | 建议 |
|---|---|---|---|
| 消费级 OAuth（订阅） | ChatGPT Plus、Google AI Pro、WorkBuddy、Claude 订阅 | 账号是**唯一**资源，封号不可逆；并发与频率是主要触发面 | 单 lane 小并发（1–3）、每请求凭据数 ≤ 3、长期限制立刻停手 |
| Coding Plan | 智谱 GLM Coding Plan | 协议限制（`developer` 角色 400/1214）、并发按套餐 | 保持协议兼容层（payload.override），不越套餐并发 |
| 官方/中转 API key | input.im、自有官方 API 项目、自建 bridge | 无消费级订阅封号风险，仍有账单、组织/项目配额和 relay 条款风险 | 作为经授权的**备份路由**与重负载通道；不要与订阅模型共用裸名。第三方 relay 的信任与数据边界仍需单独评审 |

**裸名归属纪律**：一个裸名只能有一个"主"承载者；确有池化时必须在清单
`pooled_aliases` 登记，由审计器的 `alias-multi-slot-pooled` 保留可见性。未登记的裸名
碰撞会让客户端**无法预知**自己落到哪个上游——这既是排障噩梦，也是封号归因的盲区。

---

## 6. 一页速查（排障顺序）

1. `scripts/cpa_status.py` —— 每个模型为什么不可用（车道/模型冷却、配额、凭据失效、手动 hold）。
2. `scripts/cpa_failure_triage.py --hours 4` —— 这次 429 出自哪一层。
3. `scripts/cpa_admission_risk_audit.py --hours 24` —— 这份配置自洽吗、宣告的名字还活着吗。
4. `scripts/cpa_cache_reset_probe.py --ssh-profile bwg --hours 48` —— 对照冷请求前后的客户端实例、
   请求模型、准入车道和缓存读取。上游凭据重绑与缓存驱逐需另行取证。首次轮次不能当作
   “可避免的缓存丢失”；`warm_to_cold` 也只是现场 census，不是 cache reset 的因果证明。
5. `scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --hours 24` —— 分 lane 的压力画像。

四者不可互相替代：**绿灯不代表上游健康，归因全绿不代表配置自洽**。
