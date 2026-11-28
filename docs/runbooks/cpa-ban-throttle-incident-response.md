# CPA 封号 / 限流 / 降智应急响应（bwg）

本页是 OAuth（ChatGPT Plus）、第三方中转或官方 API 通道出现疑似风控信号时的
处置入口。设计哲学是 fail-closed：先停止可疑流量，再归因；任何"规避检测"类
操作（state 注入、UA 伪装调整、身份混淆、第二账号轮换）都已明确否决，不在
可选动作里。

### 保留现有账号与渠道的执行边界

2026-10-22 用户明确要求保留全部现有 OAuth 账号与 API key 渠道。优化不以迁移、
删除凭据、退役路由或预防性隔离作为完成条件；遇到真实账号风险信号仍按下文
停止相应流量并保留凭据。官方 API 迁移只是后续选项，不自动执行。

并发预算不等于 RPM/TPM/日额度预算，多个 key 也可能共享组织或项目额度。
没有供应商可核验额度时，不凭社区画像增加并发或设定“安全频率”。新增账号前
复核初始选取、失败回退和共享额度；现行 `request-retry=0` 与
`max-retry-credentials=1` 将单个请求限制为一份合格凭据，不开启额外请求级重试轮次。
WorkBuddy 在后续独立请求中仍由 `credits` 选择候选账号。WorkBuddy 的 selected 模式另行
限制自动回退，但无既有选择时仍允许初始选取，不能据此宣称固定到指定账号。

明文 HTTP 渠道继续保留；未取得并验证供应商 HTTPS 端点前不猜测替换 URL，
不放宽 TLS 校验，不发送凭据试探端口。该段传输仍暴露 key、提示词与回答，
应避免敏感数据。公网入口 HTTPS 不覆盖 CPA 到上游这一段。

质量核对先固定请求模型、reasoning、上下文、工具与输出预算，再检查实际路由、
协议完成和截断。HTTP 200、模型自述、served model 名称或零替换日志均不能
独立证明质量；容器重建后的日志计数只代表保留窗口。不挂周期 OAuth 生成探针。

## 信号识别（先 doctor，再日志）

一次 strict doctor 覆盖大部分归因面：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

按段读数：

| 段 | 关注信号 | 含义 |
|---|---|---|
| `==gateway-statuses==` | 24h 状态分布、`five_xx_local_vs_upstream`、`client_503_retry_pattern`、499 耗时簇 | 区分上游过载、本地冷却与客户端紧重试放大；维护窗口 503 见 `503/admission_maintenance_44B_upstream_lt_0_5s` 与 `client_503_maintenance_shape_total`，已从重试模式剔除 |
| `admission-health` 检查 | `oauth_lane` 的 `OPEN` / `WAIT_UPSTREAM_WINDOW` / `OPERATOR_REVIEW_REQUIRED` | 运行中熔断器状态：分别表示无当前阻断、整通道或单模型冷却、人工 hold；机制检查通过不等于通道开放，OPEN 不证明上游接受账号 |
| `==oauth-monitor==` | `ACTION_REQUIRED`（≤22h）、`FAIL_EXPIRED`、`FAIL_REFRESH_SIGNAL`（`invalid_grant`/`refresh_token_reused`） | OAuth 生命周期问题，转 [OAuth 失效恢复](cpa-oauth-failure-recovery.md) |
| `==cooldown-state==` | `active_cooldown` vs 滞留 | 正常退避必须等待；滞留口径见 [冷却恢复](cpa-stale-cooldown-recovery.md) |
| `==model-substitution==` | WARN 计数 + `model_substitution_distinct_pairs` / `model_substitution_pair` 拆分 | 上游静默换模型（降智的间接信号，仅观测）；判读见下节 |
| `==oauth-quarantine==` | `oauth_quarantine=none` / `active` / `UNAVAILABLE` / `UNMARKED_OAUTH_BLOCK` / `INVALID_MARKER` / `INCONSISTENT` | `active` 是已声明的可逆隔离；`none` 正常；`UNAVAILABLE` 表示 config 不可读；其余三种 doctor fail-closed |
| `==claude-lane==` | `claude_lane=OK` / `QUARANTINED` / `EXCLUSION_DRIFT` / `BLOCKED_DECLARED_ALIAS` / `UNDECLARED_ROUTE` / quarantine marker 错误 | 正常态须与 manifest 墓碑一致且不得阻断认领名；显式 Claude 隔离时必须同时有合法 marker、完整 `claude-*` 阻断与空目录 |

补充取证（只读）：

```bash
docker logs cli-proxy-api --since 24h | grep -ciE 'invalid_encrypted_content|thinking_signature_invalid|capacity|server_is_overloaded'
```

容器名是 `cli-proxy-api`；grep `cliproxy|cpa` 匹配为空是无效证据（docker logs
空跑）。看 `X-Codex-Routing-Hint` 头相关问题前，先确认它是 2026-09-25
（v7.3.17）之后唯一新增的 OAuth lane 变量。

### 静默换模型判读

`model_substitution_warnings_7d` 统计 `upstream served model` warn，受一层边界
约束：只覆盖容器**保留**的日志（容器重建后该计数会静默变成"自本次启动以来"）。
**per-request substitution warn（usage_helpers.go）已长期在产**：源码锚
`e9463ff5`（2026-09-18 进主线），**v8.0.13 即已包含并在产**——10-18 的 261 条
爬坡系 antigravity lane 落地后用量上升，不是版本行为变化，旧"v8.0.3x/32 起"
归因作废（2026-10-21 源码复核）。每个被替换的请求都记一条 warn（旧版每
（凭据，模型）对 10 分钟一条的节流已不存在），所以 7d 计数随用量放大属正常，
判扩散看 doctor 同段的 `model_substitution_distinct_pairs`、
`model_substitution_known_name_variant_pairs`、
`model_substitution_unknown_pairs` 与 `model_substitution_pair` 拆分（只含模型名，
凭据哈希不进 doctor 输出）。`model_substitution_known_name_variant_pairs` 只表示
替换对已登记，不证明请求与实际服务模型质量等价。2026-11-18 起，
`gemini-3.8-flash-n <== gemini-3.8-flash-high` 即使是唯一替换对，也报告
`WARN_SUBSTITUTION_REVIEWED_PAIR`；未知替换对仍报告 `WARN_SUBSTITUTION_*`。
这是复核信号，不单独判定质量变化。临时
取证（auth_index 归属或自定义时间窗）仍用手工一行：

```bash
docker logs --since 168h cli-proxy-api 2>&1 | grep 'upstream served model' \
  | sed -E 's/.*served model "([^"]+)" for requested model "([^"]+)".*/\1 <== \2/' \
  | sort | uniq -c
```

- 单一（凭据，模型）对：先查本仓是否已有该对的已记录观察。例：
  2026-10-15 保留窗口内 29 条全部是 `gemini-3.8-flash-n <== gemini-3.8-flash-high`
  （当时为 Antigravity 单凭据），不足以单独证明降智；2026-11-18 的 desktop 形状
  对照则观察到该替换扩散至凭据池，因此当前 doctor 将该对持续标为复核信号。
- 替换源属于 OAuth lane 时，**默认** `quality-canary` 看不见它（默认排除 OAuth
  lane），照 doctor 提示直接跑会得到无信息的绿灯。要么停在日志级归因，要么按
  下文 L3 的准入显式回选 `CPA_HEALTH_INCLUDE_OAUTH=1`。
- 只有非 OAuth lane 的替换才适合用默认 `quality-canary` / `quality-eval` 复核。

### 增补信号词汇（多 lane 风控评审：2026-10-18 初版、2026-10-19 增补）

**2026-10-22 归因修正（优先于下表历史观察）**：

- Codex #6742 的刷新提前量修复和 v8.0.39 请求级 401 处理属于针对性修复，
  不证明周期失效已经消失。复发先核对实际版本、请求级错误与刷新时间线；
  不承诺重新授权或 refresh-all 必然恢复，也不自动执行批量刷新。
- Antigravity 的 payload-sensitive 429 仅是历史相关性观察。快拒和配额页正常
  不足以确定过滤机制或排除限流；保留现有 sensitive-words 兼容设置，但不扩展
  混淆，也不把它当作账号保护或条款合规措施。按完整协议错误及可信等待窗口处置。
- **2026-11-25 复审（身份字面量的作用面）**：零宽 `sensitive-words` 自 2026-11-17
  起不再清除 429，上游零宽方案 [#6280](https://github.com/router-for-me/CLIProxyAPI/pull/6280)
  已于同日关闭未合并；生效机制是 `sensitive-words-replace` 对
  `systemInstruction` 的字面替换，也是维护者在
  [#7553](https://github.com/router-for-me/CLIProxyAPI/issues/7553) 给出的口径
  （触发句要逐客户端二分定位，可能有多段独立触发）。该机制**够不到 user 角色**：
  11-22 卡死线程的压缩交接摘要（user 消息）复述了
  `"You are Codex, an agent based on GPT-5..."`，11-25 用现行管线回放同一历史仍得
  裸 429，而身份句只出现在工具输出里的会话全部 200。处置分两层：Antigravity
  `systemInstruction` 由整词词表加有序 RE2 `sensitive-patterns-replace` 处理；
  admission 的 `neutralize_agent_identity_text()` 只改写**身份句本身**（Claude Code
  整句、`DeepSeek Harness` 短语、ZCode / CodeBuddy / WorkBuddy 与 Codex 身份句），
  裸产品名、`.zcode/skills` 这类路径与工具输出原样保留——整词键会把路径改坏。
  作用面：agy 车道的 developer 与 user 文本；WorkBuddy 与 ZCode 起步套餐车道的
  system、developer、user 文本。ChatGPT 车道不处理（Codex 的身份句在那里是官方
  客户端自身的形状），11-21 摘除的全量 user 改写不恢复。排查序：先看 journal 的
  `kind=antigravity_identity_user_text` 或 `kind=agent_identity_neutralized`（每请求
  恰一条），再按维护者方法二分，**不得用在役账号做触发二分**——先发清洗后的
  提示，全部通过就没有可二分的对象。清洗只针对内容过滤与客户端披露，不是封号
  的充分条件。已知触发面不止身份行：#7627 证实 Claude Code 第二段系统提示另有两
  条独立触发句，#7904 二分出 6 条触发行（含 Claude Code Desktop 的
  ", running within the Claude Agent SDK." 尾巴变体——将来接入按整行 pattern 处
  理，勿用单句规则）；扫描面含 user 消息（#7608 维护者确认，demote
  systemInstruction 进 user 消息绕不开过滤，该方案已关闭）；工具描述与 user 文本
  里的同一短语不单独触发（#7627 原文）。线级抓包（11-25，同镜像隔离容器加回环假
  上游）的边界：身份句在
  `systemInstruction` 与 agy 的 user 文本里已清除；工具定义、对话历史里的裸产品名
  与工具输出不清扫；`systemInstruction` 的整词替换有损，`.codex` 这类路径会被改写。
  台账：[定位与回放](../change-evidence/20261125-bwg-antigravity-handoff-identity-swap.md)、
  [多车道中性化](../change-evidence/20261125-bwg-agent-identity-neutralization.md)。
- **2026-11-27 复审（规则覆盖实测与 #8521）**：
  - 用仓内真实函数 `neutralize_agent_identity_sentences` 对 31 条合成文本实测。
    `openai/codex` 三个提示模板（`gpt_5_codex_prompt.md`、`gpt-5.2-codex_prompt.md`、
    `gpt-5.1-codex-max_prompt.md`）首句均为 `You are Codex, based on GPT-5.`，现行规则
    已覆盖。唯一缺陷是模型记号只吞到数字：`You are Codex, based on GPT-5.6-terra.`
    被改成 `You are a coding agent-terra.`；已修并随 reviewed deploy 上线（台账
    [identity-suffix](../change-evidence/20261127-bwg-admission-identity-suffix.md)）。
  - **OpenCode V2 是未覆盖的已证实触发**：上游
    [#8521](https://github.com/router-for-me/CLIProxyAPI/issues/8521)（2026-11-27 关闭）
    报告 OpenCode V2（`2.2+` / `2.3+`）每个请求都把 `tools.opencode.session_move` 注入
    `systemInstruction`，Google 对含该子串的请求返回裸 `429 RESOURCE_EXHAUSTED`，并把
    健康凭据送进 `model_cooldown`；原文明确 `sensitive-words` 的零宽方案**不生效**，
    上游的处理只是一条文档提交（`0e19e8e`，不在 v8.1.7 内），做法是自行在
    `sensitive-patterns-replace` 里加规则。**本仓未加该规则**：48h 内 admission
    journal、保留的错误转储、nginx 访问日志均无 OpenCode 痕迹，Antigravity 车道当前
    不可达该触发；而验证规则形状需要向 Google 发含该子串的请求，违反"不得用在役
    账号做触发二分"。**接入 OpenCode 到 agy 车道前必须先补这条规则并走受控验证**，
    这与本节"工具描述与 user 文本里的同一短语不单独触发"（#7627）不矛盾：#8521
    证实的是它出现在 `systemInstruction` 里时触发。
- **2026-11-25 第二轮复审（自然流量验收与 ChatGPT 车道结论）**：
  - 自然流量已验收。48h 内 Antigravity 车道 71 发里 68 发命中
    `antigravity_identity_user_text`，69 发完成；journal 里该车道只有一发 429，
    是 11-24 16:03Z 部署前的受控回放，不是自然流量。WorkBuddy 车道 175 发里
    162 发命中 `agent_identity_neutralized`，零上游错误。ZCode 起步套餐车道仍是
    零流量，未验收。
  - ChatGPT 车道**不建清洗规则**。该车道的 agent 流量全部走 `/v1/responses` 且
    来自官方 Codex 客户端；`/v1/chat/completions` 上的 108 发（48h）是约 53 token
    的非流式探活，不带系统提示。官方客户端的请求没有顶层 `instructions`（基础
    提示是 `input[0]` 的 developer message），经翻译的第三方客户端在结构上即可
    分辨，清洗身份句不改变这一点（出站头与请求形状的实测见
    [上游台账](cpa-observed-baselines-and-upstream-log.md) 六节）。上游也没有
    OpenAI 按提示词内容处置账号的报告：#2396 是新号首用即封，维护者称与 CPA
    无关。证据充分的只有 Antigravity 的内容过滤（#4696、#7553、#7904、#7627）。
  - **周期探活（观察，未处置）**：全天候都有上述小请求打到订阅号——中位间隔
    `gpt-6.1-sol` 约 25 分钟、`gpt-6.1-terra` 约 64 分钟、`deepseek-v4.1-flash`
    约 29 分钟，UA `codex_exec/0.142.3 (Windows 10.0.26200; x86_64)`，经同一出口。
    这是机器形状的订阅号流量，与「不挂周期 OAuth 生成探针」相悖。来源没有从
    服务端确认（nginx 与 CPA 都不记 UA 与 URI）；本机 `qq-astrbot-stack` 的
    provider 看门狗有同形的周期探测。是否改指向 `/v1/models` 由用户裁决。
  - 降智读数：48h 内 `gemini-3.8-flash-high` 的 77 发全部被
    `gemini-3.8-flash-n` 接管，是免费号的服务档策略，客户端无解。
- **2026-11-26 换到 v8.1.4**（用户指令直接升级）：五叠层原样重移植，身份句中性化
  与日量帽、签名隔离不变；7966fix 两部分去化（上游 `fe571afe` 及 v8.1.4 的配对
  契约）。`gpt-6.1-terra` 保留——上游移除的是 API key 渠道的登记项，Plus OAuth
  仍支持。事务、回滚与验收见
  [换代台账](../change-evidence/20261126-bwg-cpa-v814-five-overlay-swap.md)；
  换代顺序的教训（先 `-Apply`、后换芯）见
  [派生镜像 RFC](cpa-fork-image-derivation-rfc.md)。
- WorkBuddy 官方客户端与网关的结果差异只能缩小排查范围；两侧同拒仍可能
  是共享模型故障或网络问题，不能直接确认账号受限。11158 等社区分类也不是
  永久模型封锁的证明，不主动诱发错误做 A/B。
- 秒级连发也可能来自多个客户端、并发任务或其他重试层。先关联时间、调用方
  与会话配置，不能仅凭时间间隔认定旧会话配置未加载；不自动重启 Codex App。
- 社区 14003 出口聚合和请求频率数字不构成官方阈值；HTTP 200 空内容也不能
  单独确认免费额度耗尽，应同时排查协议、流截断和实际额度。

| 信号 | lane | 判读 |
|---|---|---|
| 上游码 `14003` / `6002` | workbuddy | 供应商错误码，尚无官方证据证明固定对应 IP / 账号层级（社区定量画像见下行 14003 行）；记录协议错误和窗口，禁止换 IP、换账号续打。`11140 / request illegal` 须人工核对账号状态，不按普通容量自动恢复 |
| `x-codex-safety-buffering-enabled` / `x-codex-safety-buffering-faster-model` 响应头 | codex | 上游替换路径（safety buffering）指示；比 turn-state 长度更可靠的降级观测信号，记录出现窗口，不按 5xx 处置。观测点=客户端流内 `codex.response.metadata` 事件（上游源码仅测试 fixture 引用该头，CPA 不落服务端日志；admission sidecar 虽在流上但动它须 reviewed deploy，本仓不建服务端收集器，勿重复提议） |
| HTTP 200 流内 served model ≠ requested model | codex | 模型身份差异观测（与 doctor `upstream served model` warn 同源）；先核对别名、模型族与上游命名，再结合固定样例质量证据评估，不能单独证明降智 |
| turn-state 长度 292/312 | codex | 实为 per-turn 粘性路由 token，单账号可在 292↔312 间翻转（路由抽签），单点读数不构成降智结论；Team 形态另有 332/356 |
| 数据面 `401 "Encountered invalidated oauth token for user"`（刷新面正常、doctor `oauth_days_left` 正常）（2026-10-19 增补） | codex | 上游 [#6742](https://github.com/router-for-me/CLIProxyAPI/issues/6742) 家族：社区在 v8.0.31 观察到的 ~7 天周期性凭据失效。**2026-10-19 更新：已修**——上游 commit `feb6398` 把 proactive refresh lead 提到 96 小时，随 v8.0.34 发布（生产已在 v8.0.34）。复发时处置不变=人工重新授权该凭据一次（或面板手动 refresh-all）即恢复；勿循环重试、勿删凭据、不按封号处置，与刷新失败（`invalid_grant`）分开归因。观察项=v8.0.34 上 7 天周期是否消失（零成本，journal 判读） |
| antigravity `429 RESOURCE_EXHAUSTED`：0.2–1.2s 快拒、配额页正常、词级二分可复现（含指纹句必 429 / 任一词替换即 200）（2026-10-19 增补） | antigravity | **假 429 = 上游对 system instruction 的身份词内容过滤**，不是配额。现行对冲 = `sensitive-words=["gpt","openai"]`（cpa_policy 钉定，只混淆 systemInstruction，user 角色消息不触发）；上游默认混淆 [#6280](https://github.com/router-for-me/CLIProxyAPI/pull/6280) 截至 v8.0.34 未合并，**勿删词表**。复发排查序=错误转储混淆命中计数→分辨带 "Resets in" 的真配额 429→WAF 波动窗（同形状批量 200 有先例）。判据源：20261015 台账 + [aiferry#184](https://github.com/Muqian-Sun/aiferry/pull/184)（10-17 第三独立仓复证机制仍活跃） |
| `11128 request illegal` 家族 / `11158` / `11134` / `11102`（2026-10-19 增补） | workbuddy | 11128 先取 displayMsg 全文分辨子条件（system-first 文案 / #31 WAF 签名句 / 会话头存在性校验[已由 conversation-headers 补丁修复] / 其它），细节在 [cpa-workbuddy-plugin.md](cpa-workbuddy-plugin.md)；`11158` = 免费号模型级墙（intl personal_free_user 被挡高端模型名单，`deepseek-v4.1-flash` 暂不在名单）——**出现即钉定模型进墙、lane 失效的结构信号**，非账号封禁；`11134/11102` = 模型级故障/下线（社区建议勿计入账号熔断）。账号级 vs 通道级判别法（三次实证）=同账号在官方桌面客户端单发 A/B：桌面 200 + 网关拒 = 通道/指纹侧，两侧同拒 = 账号侧 |
| `14003` 定量画像 / `14018` / WAF 403 升级形态（2026-10-19 增补） | workbuddy | 社区 [workbuddy2api-hub#313](https://github.com/ardeyouxipianyi/workbuddy2api-hub/issues/313) 12 号 10h 实测：14003 按**出口 IP 聚合**、计请求数不计 token（单分钟 4.08M token 零 429）、家宽阈值约 13–19 rpm、"墙高随 IP 变"（另一用户单 IP 150 号 10 天稳定）。IP 级限流换 IP 有效、账号级惩罚换 IP 无效——两类正交；处置仍按上行 `14003/6002` 行，不换 IP/换号续打。`14018`=credits、`6004`=频率/用量窗（官方口径频率限制；raw 正文 `usage exceeds frequency limit` 且自带 reset 时间戳，自恢复，非 credits 耗尽）；2026-10-21 收口：CPA 包装信封（wrapper `type=rate_limit_error`）曾使 14003 波误置 durable hold（wrapper 标签击败 header 层阶梯），已修复——分类按 wrapper message 内嵌上游 code（14003→阶梯自恢复），裸 rate_limit_error/6003/6004 仍持锁（[台账](../change-evidence/20261021-bwg-workbuddy-14003-wrapper-fix.md)）。同日晚（北京 19:22–19:49）`deepseek-v4.1-flash` 6004 波实测：v8.0.35 生产 wrapper 形状=HTTP 500+`type=server_error/code=internal_server_error`（message 尾段内嵌转义的上游 JSON），三 hold 条件（结构化 code/type）全不命中，由散文 `rate limit` marker 归 **lane 阶梯自恢复（60→900s+半开探针），无 hold**——与官方频率限制自恢复语义一致，属预期非回归；再现持锁或该形状转为无 reset 文案的真配额形态时再评估 reviewed deploy 调整 |
| HTTP 200 但流内零内容直接收尾（不报错、不进冷却，session 粘性仍绑死同一账号）（2026-10-21 增补） | workbuddy | 免费额度到量的降智式表现（上游 [#6526](https://github.com/router-for-me/CLIProxyAPI/issues/6526)）：成功形态外壳、无产出。先查面板 credits/额度与账号档位（与 `11158` 模型级墙同族观测），勿当健康绿灯、勿以重复探针确认；付费档出现同形态时同样先核额度再归因 |
| IDE "at capacity" 弹错与 `503 auth_unavailable` 的连发密集度变化（2026-10-21 增补，2026-11-01 修正） | codex | 对 5xx 不读 `Retry-After`（退避写死 200ms×2ⁿ±10%，默认 4 次来自 `DEFAULT_REQUEST_MAX_RETRIES`），本机 `~/.codex/config.toml` 两 provider（现役 `codex_local_access` + `fq_sciman_top`，同指 fq 网关）都钉 `request_max_retries=0`；现役 provider 的 `stream_max_retries=2`、同网关对照保持 0。2026-10-31 隔离回放（Codex 0.159.2）确认 0/2 在 HTTP 503 后经流重连发 3 次 POST；2026-11-01 用 Codex CLI 0.166.0 对同一 SSE 中途截断做 A/B，0 只发 1 次并直接显示用户原报错，2 自动 Reconnecting 一次后完成。该流重连只吸收已开始 SSE 的传输截断，仍不增加请求级重试；同网关只允许一个 provider 开预算，`cockpit_provider_health.py` 与 `codex-desktop-gateway-attach.md` 已按此设门。journal 再现秒级连发=有会话仍持旧默认预算（会话先于配置启动），处置=重启该会话，勿调服务端阶梯参数。上游 [#3229](https://github.com/router-for-me/CLIProxyAPI/pull/3229) watch 中，merged 后随 canary 评估吸收；[#5407](https://github.com/router-for-me/CLIProxyAPI/pull/5407)（上游 overload 文案保真）低优先仅观测 |
| GLM/sol 渠道信号（2026-10-19 增补） | zhipu-coding-plan / ai.input.im | 429 走 lane 冷却（设计内，勿开案）；`gpt-6.1-sol-input` 404 `optional_route_unavailable`、通道侧 "not priced"/forbidden = ai.input.im 已知偶发（历史 ~8h 自愈先例），勿当升级回归、勿发探针确认；GLM Coding Plan 条款风险为 2026-09-26 用户风险接受决定（复核触发在账） |
| Claude 降智观测（2026-10-19 增补） | claude | 静默降档三通道：①限额回退 Opus→Sonnet（响应 model 字段≠请求名）；②2026-10 新报告的**安全分类器 mid-task 静默降档**（[claude-code#103382](https://github.com/anthropics/claude-code/issues/103382) 族，运维/系统管理类会话误伤率高）；③近周限时 prompt cache TTL 降至 5min（[#102883](https://github.com/anthropics/claude-code/issues/102883)）。均仅观测（doctor substitution 计数 + 客户端 model 字段与 cache 行为），不建新门禁、不据此停流 |

> **2026-10-24 复核（单凭据 + 上游 overload）**：上游
> [#6851](https://github.com/router-for-me/CLIProxyAPI/issues/6851) /
> [#6852](https://github.com/router-for-me/CLIProxyAPI/pull/6852) 确认了
> "瞬时上游错误 → 该 provider 全部凭据 60s 冷却 → 后续零出网 `503 auth_unavailable`"
> 的机制；本部署 codex 只有 1 个 OAuth 凭据（`oauth_codex_files=1`），所以一次
> `server_is_overloaded` 就等于整条 `chatgpt-oauth` lane 在冷却窗口内全灭。
> 两个版本事实：**#6852 的日志修复截至 v8.0.40 仍未合并**（`warnLogAuthUnavailable`
> 仍只汇总 quota 冷却，快 503 只有 access log 行）；**#6856 的 `response.metadata`
> 缓冲修复已在 v8.0.40 源码**（`isCodexBootstrapBufferableEvent`），但本部署
> `stream-bootstrap-buffering=false` 使其不生效、且单凭据无转移对象。
> 处置仍按 L2：等可信窗口结束或切非 OAuth lane，**不改 `capacity_markers`、
> 不清冷却、不缩短 `transient-error-cooldown-seconds`、不加账号**。

> **2026-11-24 增补 / 2026-11-26 策略反转（Originator 分流与 cloaking 断言）**：上游
> [#4679](https://github.com/router-for-me/CLIProxyAPI/issues/4679) 的 A/B 证明
> OpenAI 在负载升高时优先自家客户端——同一请求 `Originator: codex_cli_rs`
> 4/4 成功，`codex-tui` 立即流内 `server_is_overloaded`，只改 User-Agent 无效。
> 2026-11-25 实测确认本栈开启 cloaking 时实际出站正是 `codex-tui`（并将官方桌面端
> 请求改写为矛盾的 macOS iTerm UA + Windows 桌面专属头），且 2026-11-24..26
> 持续 >48h 触发高频 `server_is_overloaded`。2026-11-26 经用户授权已反转为
> `upstream.codex.disable-codex-cloaking: true`（`cpa_policy.py` 钉定并投影，
> 拒绝旧路径显式 `false`），让官方桌面客户端透传真实且自洽的 `Originator`/UA；
> 过程与回滚见 [20261126 台账](../change-evidence/20261126-bwg-cpa-disable-codex-cloaking.md)
> 及 [上游台账](cpa-observed-baselines-and-upstream-log.md) 六节。

## 分级处置

### L1 线程级错误（thinking_signature_invalid / encrypted_content item_id mismatch）

另有不同的请求格式问题：当前 Codex Multi-Agent v2 将任务正文放入
`agent_message.content` 的 `encrypted_content` 字符串，前面带有包含 `Task name`、`Sender`
和 `Payload` 的文本头。Codex 源码的 `new_encrypted` 将调用方提供的字符串保存在该字段；
字段类型由 Responses 协议解释。Admission 只在字段、正文头以及 `author`/`recipient` 完全符合
该格式时，才将这段字符串映射成标准 Responses user message，并保留正文及头部。它不做密码学
解密、不删正文、不换模型，也不把推理签名当作任务。GPT 请求和普通请求原样转发；字段缺失、
额外块或未知结构仍在上游前返回 `422 / encrypted_agent_payload_unsupported`，不输出任务正文
到日志。映射后的任务正文会发送给原请求选中的 provider。模型回复仍需检查是否真正完成了委派任务。
本地 loopback 测试只证明转换后的请求形状到达模拟上游，不证明 provider 或端到端子代理成功；
部署后仍需先验收服务加载，再用一次有用的受限任务验收实际委派。

已定案为线程级问题（上游已知类），不是封号信号：

1. 确认是线程/协议兼容问题后，可按客户端指引重建会话；恢复与否需要新请求证据，不把新会话当作账号或配额恢复手段，不跨账号搬运对话。
2. 加密历史线程不要经过会改写 ID 的中间层（new_api 等）。

### L2 容量或配额窗口（先判 scope）

1. `429 / 503` 结合协议错误类型、模型标记和可信 Retry-After 判读；
   `401 / 403` 先核对鉴权、权限和执法文案，不统归单模型容量。
2. 尊重最长可信等待窗口，收敛客户端重试。缺失可信配额窗口时人工核查，
   不清冷却、不换账号或 IP 继续消耗同一权益。
3. 明确单模型容量只冷却该模型；账号、项目和组织配额按实际共享范围停流。
   其他独立 API 渠道仅承接用户允许且协议兼容的任务，禁止静默改变指定模型。
4. 并发预算是本地控制值，不是供应商安全承诺；保留现有账号和渠道配置。

### L3 明确账号风险或鉴权异常

1. `turn-state` 长度、capacity 持续、目录变化和命名差异都不能单独证明封号。
   先记录时间、lane、HTTP 状态、协议错误类型及脱敏文案。
2. 明确账号禁用、暂停、第三方客户端资格限制或鉴权失败时，让对应 lane
   进入持久人工复核状态；保留凭据和路由，不自动换号、刷新 OAuth 或反复探针。
3. 正常生成内容中提到“封号”等词不能触发停流；只解析协议错误信封。
4. 人工通过官方界面或支持渠道核对并解决原因后，按维护流程解除状态，
   一次有界请求确认。capacity 消失本身不构成账号恢复证据。
5. CPA admission 已拒绝不支持的 encrypted agent payload，且具有 lane 并发预算；
   加密载荷兼容性故障按 L1 处理，不推导为账号风控。
6. 当前授权要求保留全部凭据；不得执行 `-DeactivateOAuthLuna`。2026-10-19 用户
   重申：现有全部 OAuth 账号与 API key 渠道保留，任何处置不得删除凭据或退役渠道。

### L4 确认封号

1. 停止该通道流量（客户端 preset 已有跨族逃生与 Retry-After 尊重；公共
   Nginx 路径已有对应的 shared-account admission，不要另建一层全局限流来
   "补偿"）。
2. 记录脱敏证据（`docs/change-evidence/`），包含 doctor 输出摘要与时间线，
   不含凭据、完整请求或随机路径。
3. 是否重新接入（新账号或官方 API key）是用户决策；接入前先更新
   `cpa_provider_routes.json` 路由与排除清单，再 `-Apply`。

## antigravity TOS_VIOLATION 池级保护停（2026-11-17 起在役）

v8.0.72+ 内置的 TOS 处理（上游 #7515，提案先例 #7296）：

- **自动面**：首例 `403 TOS_VIOLATION` → 违规凭据自动停用（OAuth 刷新保留，
  管理 API 透出 `tos_appeal_url`）+ Antigravity 全池暂停
  `tos-pause-window: "2h"`（2026-11-17 用户裁决启用，`oauth.providers.antigravity`
  下）。暂停只覆盖 Antigravity 车道；**429/配额类永不触发或延长**（上游仅
  TOS 路径写入）。
  **机器判据（2026-11-18 补齐）**：该键已进 `cpa_policy.py` 的
  `EXPECTED_CONFIG_PATHS` 钉定，并由 `-Apply` 在既有钉定块里写回；缺失、缩短或
  清零都会让语义复验 fail-closed。此前只有文档声明而无判据，管理面重序列化或
  投影漏写都不会报错。上面"提前恢复"的翻 `"0"` 是**事件期受控例外**，翻回
  `"2h"` 有硬时序：**必须持 `"0"` 至本次武装的原 TTL（武装时刻 + 事件前的
  `tos-pause-window` 值）自然过期后再翻回**。翻 `"0"` 只抑制执法、不删除
  kv.db 里的暂停条目；同一事件内提前翻回 `"2h"` 会让未过期的旧 TTL 复活执法
  （2026-11-18 发现，2026-11-22 二次实证：翻回约 25 分钟后 8317 探针即刻
  `503 "antigravity pool paused after TOS_VIOLATION"`，重新翻 `"0"` 后复探
  200）。持 `"0"` 窗口内 doctor 报钉定漂移=事故姿势有意保持，翻回后自愈。
- **恢复**：窗口到期自动恢复（本机 kv.db 持久化 + TTL 兜底，重启容器不清）。
  **提前恢复手筋**：确认无新 TOS 信号后，把该键值**保 inode** 翻 `"0"`
  （CPA watcher 热加载即时生效，`docker logs` 应出现
  `config file changed, reloading`），**持 `"0"` 至原 TTL 过期**后翻回
  `"2h"`（长持 0 时把翻回挂成延时任务）。运行时读数以
  `/v0/management/config` 的 `antigravity.tos-pause-window` 为准
  （`X-Management-Key` 头，键文件 `/root/.cpa-management-key`）。
- **陷阱**：host 侧对该文件的 rename 类编辑（`os.replace`/tmp+mv）对单文件
  bind-mount 里的容器**不可见**（容器钉在旧 inode，2026-11-17 实证），需
  `docker compose up -d --force-recreate`（drain 锁 + 有界等待）才生效。
  常规配置编辑一律保 inode 写入。
- 申诉与凭据善后（官方表单 revoke OAuth → 重登 → appeal_url；社区实证
  2026-02 波有分批解封先例）是人工动作，与池恢复正交；"解封后再犯=永久"
  纪律不变。

### 双层锁释放顺序与事故指纹（2026-11-18 首例实弹固化；2026-11-22 第二例复验并修正第 4 步时序）

真实 `TOS_VIOLATION` 会同时武装两层：CPA 池停（上游请求拿
`503 "antigravity pool paused after TOS_VIOLATION"`）+ admission
`account_restricted` durable hold。**池停未清时任何探针只会拿到 503，且该
503 响应体里的 TOS_VIOLATION 字样会把 admission hold 再 latch 一轮**
（2026-11-18 实证）。释放顺序固定为，顺序错=浪费一轮并多落一次锁：

1. 保 inode 翻 `tos-pause-window` `"2h"`→`"0"`（上节手筋，等 watcher 热加载，
   `/v0/management/config` 读数复核）；
2. `systemctl stop cpa-admission` → 备份并置
   `antigravity-risk.json` 为
   `{"version": 1, "not_before": 0, "hold_reason": null}` → `start`；
3. 8318 回环**单发**探针定性（pool 已开时直达上游，一发分辨"仍禁用 vs 已恢复"）；
4. **持 `"0"` 至原 TTL 自然过期后**（武装时刻 + 事件前的
   `tos-pause-window` 值）再翻回 `"2h"`（钉定契约要求，见上节）。**禁止同一
   事件内提前翻回**——2026-11-22 实测提前翻回即刻复活池停（503）。处置须
   立即收尾时，把翻回挂成延时任务（如 15:35Z 翻回 13:33:15Z 武装的 2h TTL），
   翻回后单发探针复验 200。

事故期日志判别：`stale_write_ahead_cooldown retry_after≈90` = 重启写前窗
指纹（良性，自行到期，勿当上游信号）；`operator_review_required
retry_after=300` 循环 = 真 hold（人工复核锁，不自动恢复）。部署夜的重启
指纹会掩盖首信号：90s 窗到期后的 half_open 探针才是第一发真实上游测试，
其结果才入判决。

**重登不解服务禁用**（2026-11-18 实测：OAuth 重登刷新 auth json 至
`disabled:false` 后，回环探针同 403 原样返回）：禁用在账号侧，凭据侧
`disabled` 标记清零只代表本地路由面恢复；唯一出路 = `appeal_url` 申诉
（403 ErrorInfo metadata / 管理 API `tos_appeal_url` 透出），
"解封后再犯=永久"。单凭据命中≠扫荡波：先跑 doctor 的 credential_ban
观察块（sha8 标签 7d 逐凭据计数）普查，再定全池停还是单号 hold。

## antigravity 封号触发面与登录风控纪律（2026-11-18 增补）

社区 2026-02 起报告的 Antigravity 永久封禁潮（2026-11 第二波延续）把可核验
触发面收敛为下表。本节只登记**行为收敛与观测**纪律：一切规避检测类操作仍按
本页置顶哲学全部否决；混淆类手段（零宽字符等）已实证会被破且本身即条款红线
（#6280 路线判否，勿重开）。

### 触发面清单（社区口径，非官方公告）

| 触发面 | 证据 | 本栈姿态 |
|---|---|---|
| 第三方框架路由 Gemini token（OpenClaw 类，社区转述 2026-02-25 起官方按"恶意使用"限制访问） | [Reddit 封禁潮帖](https://www.reddit.com/r/google_antigravity/comments/1r1unas/google_is_permanently_banning_antigravity_users/)（转述） | CPA 网关本身在该打击面上——**结构性残余风险，不可本地消除**（同 OpenAI 行结论）；对冲=上节 TOS 池级暂停 + L3/L4 分级停流 + 凭据池备份（agy 池 8/8 已做 2026-11-18 复核，恢复演练挂月维），不新增对抗手段 |
| 配额规避与多账号切换 | 同上；Google ToS 明禁"规避安全保护" | 2026-10-22 边界不变：不新增账号、不轮换凭据、不预防性隔离；已封凭据走官方申诉（上节），在役健康账号不做预防性 re-login（CPA-only 裁决） |
| 网络环境/IP 不纯净、出口与账号多对多 | 社区封禁归因主项 | 单账号钉死既有 bwg 出口；不为防风控换 IP/换出口（主动换出口本身是被识别的滥用形态） |
| 风控信号出现后反复重试登录/数据面 | [登录 403 处置](http://littlemagic8.github.io/2026/11/15/how-to-solve-antigravity-login)：反复重试加深"异常"标签 | 与 L3 同义：登录页 403 / 数据面风控提示=立即停手；事件窗口不重试、不换号、不清冷却，lane hold 人工释放后才动 |
| 第三方"账号管理器"类工具批量操作凭据 | [Antigravity-Manager #646](https://github.com/lbjlaq/Antigravity-Manager/issues/646)（新号 429 风控与该类工具伴生） | 凭据操作只走本仓 guardrails/面板既有事务，不引入外部管理器 |
| 账号类型集中（jio/pixel/学生号等定向扫荡传闻） | 2026-11-16 社区传闻（按扩大处理） | 现有池保持多来源分散；传闻并入全池暂停纪律处置，不按类型预防性删号 |

2026-11-20 增补（上游 [CLIProxyAPI #7768](https://github.com/router-for-me/CLIProxyAPI/issues/7768)
运维报告：另一部署的 agy 凭据池 2026-11-16..20 请求级日志，6 次账号级 403）：
4/6 封禁前有同短消息 `RESOURCE_EXHAUSTED` 429 风暴，1 例无前置风暴（429 是
相关项不是必要条件）；其余相关因子=新凭据首用后分钟级即上自动化负载、短时
高突发（个案 ~177rpm）、多凭据共享机房/关联代理 IP、单客户端批量生成、
**非配额 429 后自动换凭据把上游指纹扩散全池**。对齐本栈：admission 的 429
分类+容量标记、per-lane RPM 帽、session 粘性与 TOS 池级暂停已覆盖该报告向上游
请求的熔断诉求；可执行增量仅一条——**新号入池前几小时保持低量**（补 carbon
档新号时适用）。

2026-11-24 增补（同 issue photobrand 跟评的 109 号池对照表，REPORTED）：被封
7 号在 11-10..20 被选中 4,836–7,269 次、单分钟峰值 10–49 RPM；存活 16 号为
0–3,101 次、峰值 0–18 RPM。**请求量只是相关项**：82 个被封号里 35 个
（42.7%）封前 24 小时零 429；#8021 报整池同批停用。打标离线完成后在下一次
请求才显现，本栈第二至四例"空闲后首请求即 403"同形，继续压低日量不改变这
一维。**每出口的账号数**是该表区分度最大的一列：86 号挤在 3 个代理端口
（28–29 号/端口）0/86 存活，1–2 号/端口 16/23 存活。对齐本栈：峰值 8 号共用
bwg 单出口，现 2 号；同出口不再回加账号。另有三条上游事实支持"封禁号移出
auth 目录而非留 `disabled`"的现行做法——后台刷新仍会对留存的号调
`loadCodeAssist`（[#7994](https://github.com/router-for-me/CLIProxyAPI/issues/7994)，
维护者 11-24 以 working as intended 关闭）、`fetchAvailableModels` 周期探测
TOS 号（#7869）、重启后 `disabled` 回落为 false（#8021，REPORTED）。

2026-11-27 复核（官方页直取并与 Wayback 归档逐句对比；社区面只覆盖官方开发者论坛与
GitHub，Reddit / X / linux.do 未覆盖）：

- **条款正文自 11-19 归档起逐句未变**，FAQ 把范围写得更具体：问题标题点名
  "Claude Code, OpenClaw, or OpenCode"；"Wrapping agy inside third-party coding agents or
  applications, or automating account rotation, is not supported under consumer plans"；
  官方给第三方 agent 的出路是 Gemini Enterprise 或 AI Studio key（[FAQ](https://antigravity.google/docs/faq)）。
- **Plans 页在 11-21 至 11-27 之间改写**（[plans](https://antigravity.google/docs/plans)）：
  Free 与 Google AI Plus 只有**周窗口**（"A meaningful quota, refreshed weekly"），可用模型是
  "Gemini 4 Hydrogen (and Gemini 3.8 Flash until November 30, 2026)"；Hydrogen 是
  Free / Plus / 试用号的默认模型（[models](https://antigravity.google/docs/models)）。同页
  写明 11-30 下线 Gemini 4 Argon、Gemini 3.8 Flash、GPT-OSS-120b、Claude 4.6（本栈这些
  名字已是排除表墓碑）。每日 150 的上限不是从官方数字换算来的。
- **思考档位与周额度**：Hydrogen 有 Low / Medium（默认）/ High / Extra（CLI 的 `xhigh`）
  四档，全套餐可用；论坛（非 staff 账号）回复称更高的 reasoning 档消耗更多周额度。本栈
  车道只发布 `gemini-4-hydrogen-xhigh`，每一发都按最高档扣周额度。v8.1.7 源码确认带后缀
  的模型以后缀为档位、客户端显式传的 `reasoning_effort` 会覆盖后缀——要省额度就在客户端
  降档，不必改目录。返回名不带 `-xhigh` 属正常（Google 报基础名），不是降档。
- **封号是持续流而非单次事件**：官方开发者论坛 11-26/27 新增十余个独立申诉帖，其中有
  "CLIProxyAPI + OpenCode" 的自述（[205848](https://discuss.ai.google.dev/t/205848)、
  [主申诉帖](https://discuss.ai.google.dev/t/199195)），付费 Pro / Ultra 也在其中。没有找到
  "低量即安全"或"免费号更易 / 更难被封"的判别证据。申诉流程只见于论坛回复（账号无 staff
  标记）：只走 TOS Appeal Form，一次性，称自动处理 48–72 小时，批准后下次登录自动恢复，
  须先撤销第三方 OAuth 授权；实例为 4 到 20 天。
- **同窗口的新失败形态**：Antigravity 2.35.2 起大量
  `403 UNSUPPORTED_LOCATION (1008) "Detected location: CN"`，账号国家与出口为 JP / US / SG
  仍中招，原因未证实。本栈 admission 对 Antigravity 的 JSON 403 一律落
  `authentication_required` 持久锁（不含 TOS 文本时不会升级为 `account_restricted`），
  遇到时读转储原文判别，不按 TOS 处置。
- **上游状态**：2026-11-26 05:39–05:45Z 维护者批量关闭至少 195 个 Antigravity issue
  （completed、零评论），**closed / completed 不等于已修**——
  [#8409](https://github.com/router-for-me/CLIProxyAPI/issues/8409)（数组包裹的 403 TOS 载荷
  不被识别）与 [#8452](https://github.com/router-for-me/CLIProxyAPI/issues/8452)（403 一律
  显示 `payment_required`）的函数体到 v8.1.7 / main 字节级未变。本栈的兜底：Antigravity 的
  JSON 403 即使不被 CPA 识别为 TOS，admission 也会落持久锁，车道照样停。#7658 的上限 /
  停止逻辑在 v8.1.5 起存在；#7768、#7676 仍 open；[#8522](https://github.com/router-for-me/CLIProxyAPI/pull/8522)
  （模板收录客户端触发词）被维护者拒绝，触发词清单继续由本仓自己维护。

### 执行器侧 thoughtSignature 凭据隔离（sig-iso fork 车道，2026-11-23 起）

#7846 的跨账号签名重放面有两层：reasoning replay 账本面已由 crossauth 车道
（sessionKey `auth:<hash8>:` 前缀）封口；**translator trailing-signature 缓存
（`cacheGeminiResponsesTextSignatures`，按 model+msgID 键、TTL 1h、无凭据维度）
与客户端会话历史回带**两个 rebind 面由本车道封口。机制（fork overlay
`sig-iso`，五叠层最上）：

- **收集**：上游 200 响应里的 `thoughtSignature` 按**首发凭据**入索引
  （进程内 `sha256(sig)[:16] → {authID, seenAt}`，TTL 1h、上限 2 万、带锁，
  首源优先不覆盖）。三个响应面都挂：非流式 body、claude 非流式 SSE 循环、
  流式逐事件（紧邻 `replayAccumulator.ObserveSSELine`）。
- **剥离**：发送前对 `request.contents[].parts[].thoughtSignature`（及
  countTokens 的顶层 `contents`）逐项查索引：**已知且异凭据→替换为
  `internalsignature.GeminiSkipThoughtSignatureValidator`**（"skip" 旁路哨兵，
  保留 part 结构）；**未知且非 functionCall part→同样替换**（覆盖重启后 KV/
  缓存恢复的旧签名）；**未知且 functionCall part→保留**（客户端回显的工具
  签名，剥离有 400 风险=协议固有残余面）；已知同凭据与哨兵值→不动。四个
  挂钩点=既有 `sanitizeAntigravityGeminiRequestSignaturesDoc` 调用点旁
  （execute ×2、stream、countTokens），**无条件运行**（上游 sanitize 自身被
  `antigravityUsesReasoningReplayCache` 门控，非 replay 模型原本无任何签名
  消毒）。debug 日志行 `isolated %d request thought signature(s)`。
- **边界与判读**：哨兵替换的降级=特性前形态，只损 thinking 连续性不致 400；
  重启清索引（进程态），重启后首批同账号请求也会出现哨兵替换=预期非故障；
  replay 账本恢复的签名不经过剥离点，但其键已按凭据隔离（crossauth），
  本车道不重复覆盖。不改 translator/缓存/selector，不做任何身份伪装。
- **裁决记录（同日）**：扩 sensitive-words/patterns 重写覆盖面=拒绝
  （2026-10-22 边界"不扩展混淆"不变）；`request-scoped-errors` 裸 429
  `action: stop` 规则=不加（`max-retry-credentials=1` + repeat-reject guard
  已实现同语义，叠加规则可能与 fork bare-429 语义互扰）；上游 dev `b8e3bc6`
  序列化对齐=只跟踪不采纳（未发布、无 A/B 证据）；#7701 prompt-guard 插件=
  拒绝（未合并第三方件，供应链面）；换出口/IP 分散=拒绝（置顶纪律）。
  官方申诉表单 `forms.gle/hGzM9MEUv2azZsrb9`；条款底线不变：第三方 OAuth
  接入本身即违约面，技术缓解只降检出概率，不构成合规。

### 上游签名缓存缺口与重登后的连接纪律（2026-11-23 增补，v8.1.1 源码+上游 issue 核证）

- **上游全局 signatureCache 无凭据维度**（[#7960](https://github.com/router-for-me/CLIProxyAPI/issues/7960)，
  open；`internal/cache/signature_cache.go` 按 model-group+text-hash 键、TTL 3h）：
  一个账号的 thoughtSignature 可经该缓存注入另一账号的上游请求。**本栈判定：
  sig-iso 车道已把该面封口**——注入发生在请求构建期，剥离点无条件运行，
  thinking-part 命中"未知"或"已知异凭据"分支均替换为哨兵（functionCall part
  保留=协议固有残余面，与上文一致）。下代 fork 换代时仍建议给上游缓存加
  auth 维度，把保障从"剥离顺序"改为"结构隔离"；在那之前不要因为该 issue
  单独换代。
- **同槽重登后 keep-alive 连接跨号复用**（[#7961](https://github.com/router-for-me/CLIProxyAPI/issues/7961)，
  open；transport scope=`id:<authID>`，不含 grant 代）：antigravity 凭据
  原地重登（同 auth 文件刷新 token）后，新账号的首批请求可能骑在旧账号的
  keep-alive TCP 连接上。v8.1.1 已有缓解（auth 更新回调会关闭该 scope 的
  **idle** transports，`antigravity_executor_auth.go`），残余=仍在活跃使用中
  的连接不受 close-idle 影响。**纪律：对 antigravity 凭据做手工重登后，
  顺手 `docker restart cli-proxy-api`**（与换代 swap 事务的习惯一致；重启
  同时清 sig-iso 进程内签名索引，属预期非故障）。

### 降智检测锚（served-model 观测）

- 服务侧已有：doctor `==model-substitution==`（`upstream served model` warn +
  `model_substitution_distinct_pairs` 拆分），判读见上文"静默换模型判读"；
  不新增周期探针，观测仍走 doctor 既有计数与事发取证。
- 外部锚：Gemini API response 的 `modelVersion` 字段是 served 模型的客观读数；
  free tier 有 forced fallback（请求名被 smaller variant 接管、社区报质量劣化），
  付费/Priority 档显著减少但非零（[模型文档](https://aistudio.google.com/docs/models)、
  [Tier-2 503 实例](https://discuss.ai.google.dev/t/persistent-503-on-gemini-3-7-flash-with-priority-tier-tier-2-paid-0-success-over-multiple-retries/179804)）。
- 已定性结论不变：agy 降智=Google tier serving 政策（碳/氩 served 裸名、flash-n
  全池降级、换凭据无效），**客户端无解**。
  2026-11-18 证据修正：11-17 的"全池降级"结论来自裸 user 小探针，对 desktop 形状
  不成立——同日单变量对照显示 desktop 原样重放是 WAF 指纹 429（T1/T2），修复后
  `flash-high`/`xhigh` 仍被替换为 `flash-n`/裸名。因此**不能再用"换更高档凭据"作为降智
  解（池内换号已实测无效，且 Antigravity FAQ 明确消费级套餐不支持自动化账号切换）**；
  在役杠杆只有两条：质量敏感流量改走其它车道（如 `claude-sonnet-5-5-high`），或迁移
  官方 API key / Gemini Enterprise（用户决策）。
  不凭主观感受开案：先固定请求模型与样例，再对照替换对计数与 modelVersion 读数。
- 限额口径（官方页）：组模型共享 5h 窗与周顶，Ultra（$100/mo）token 限额 ≈ 5×
  Pro（$20/mo）（[Plans 文档](https://antigravity.google/docs/plans)）；有 Pro 仍可能
  显示 "ineligible for higher rate limits"
  （[Dev Forum](https://discuss.ai.google.dev/t/ineligible-for-higher-antigravity-rate-limits-although-account-is-eligible-for-google-ai-pro/183071)），
  资格判定以官方页读数为准，勿按社区画像补判。

## claude-oauth lane 信号形态与急停（2026-10-17 lane 落地起）

claude 订阅 OAuth lane 在役后，本页分级对本 lane 适用。判读入口仍是 strict
doctor（`==claude-lane==` 段）+ error dumps；条款与资格边界见
[cpa-claude-account-risk.md](cpa-claude-account-risk.md)。

> **2026-10-20 状态**：该 lane 已随账号停用退役（当前路由不再供给、凭据数 0）。
> 本节与下文 `-QuarantineClaudeOAuth` / `-RestoreClaudeOAuth` 工具保留为复活评审
> 的处置依据；复活评审要求见 cpa-claude-account-risk.md 置顶状态行。

| 信号形态 | 判读 | 处置 |
|---|---|---|
| 数据面持续 4xx，dumps 文案含 "This credential is only authorized for use with Claude Code" | **凭据级执法**（订阅 OAuth 被限制用于第三方客户端，2026-02 起有执法先例），不是普通配额 | 立即急停 b（见下），官方界面核对账号状态并走官方申诉；事件窗口不重试、不换号、不清冷却 |
| 授权页/回调 `account_on_hold` | 账号受限（多为登录态指向了受限账号） | 按 attach runbook 核对登录邮箱后重走流程，不重试本次回调 |
| `Anthropic-Ratelimit-Unified-5h/7d-Status: rejected` | 真配额窗（5h/周窗，claude.ai/Desktop/Code 全产品共享池），不是风控事件 | 尊重完整 reset 窗口；缺少可信期限则人工复核停流；与官方桌面/网页重活错峰；不硬打 |
| `invalid_grant` / 刷新持续失败 | 先核 provider 归属：doctor 只将带 provider 标签的容器刷新失败归给对应通道；无归属日志与混合凭据 dumps 单列 unknown，元数据正常不证明账号可用 | 人工确认对应凭据后按官方支持流程恢复；[OAuth 失效恢复](cpa-oauth-failure-recovery.md) 的 device-login 命令仅适用 Codex |
| doctor `claude_lane=EXCLUSION_DRIFT` / `BLOCKED_DECLARED_ALIAS` | 目录契约漂移（排除表与 manifest 墓碑不一致，或认领名被墓碑阻断） | 恢复走受审查 apply 重投影；不手工改 config 续命 |
| `400 invalid_request_error` "Third-party apps now draw from your extra usage, not your plan limits." | Anthropic 的第三方流量分类器命中：订阅 OAuth 上的第三方调用改走额外用量计费，关掉 Extra Usage 的号直接 400（上游 [#6432](https://github.com/router-for-me/CLIProxyAPI/issues/6432)，两人复现） | 计费/断流后果而非封号；仍是"被识别为第三方"的直接证据，按账号级信号停流复核，不以改写 system 块绕过 |

急停优先用可逆的 provider 级隔离，完整步骤见
[cpa-claude-oauth-attach.md](cpa-claude-oauth-attach.md)：

```powershell
# 停止 Claude OAuth 模型路由；保留凭据，不重置额度或冷却（开关权属用户，
# 须携带用户当次指令原文，缺省 REFUSE）
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg `
  -QuarantineClaudeOAuth -QuarantineUserDirective "<用户当次指令原文>"
# 只有人工确认后才恢复
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth
```

隔离事务先备份并投影 marker-aware `cpa_policy.py`，再依靠 CPA v8.0.31 配置热加载。
事务校验目标凭据唯一性、策略基线、准入 lane 连续 3 次空闲和当前模型目录，再写入
`claude-oauth-quarantine.json` 与 `claude-*` provider 排除项；配置用保 inode 的受控
写入触发 CPA watcher，marker 用原子写入；
隔离后所有 Claude OAuth 别名必须消失。strict doctor 的预期状态是
`claude_lane=QUARANTINED`、`CLAUDE_OAUTH_CATALOG=blocked`。任一回读失败会恢复原配置和
marker，并验证目录回到原状；不会自动重启 CPA。若热加载失败，停止写操作并等待单独
维护窗口。

隔离只阻止后续经 CPA 的模型调用，不撤销官方 OAuth 授权/刷新会话，也不改变既有账号
风险状态；它不是解封、免限流或恢复模型质量的保证。单人单凭据、不挂定时生成探针
（doctor 目录读数除外）的纪律继续适用；事件信号出现后不重试连发、不换号、不改
指纹或出口来规避服务方限制。

## 密钥轮换前操作清单（防 fail2ban 自伤封禁）

> **背景**：`cpa-gateway` jail 为 `maxretry=20 / findtime=600`，首次
> `bantime=3600`，并按 `factor=2` 递增，最多 `604800` 秒。密钥轮换窗口内
> 客户端用旧 key 高速重试，可能在 10 分钟内累计 20 次 401，触发自伤封禁；
> 重复触发会快速延长封禁。这不是 provider 封号，但对使用方完全不可用。
>
> **回环豁免是不变量**：jail 固定带 `ignoreip = 127.0.0.1/8 ::1`，而 nginx 正是
> 经 `127.0.0.1:8317` 访问 CPA。若这行丢失，doctor 自己每次运行产生的未认证
> 401 就会累积到 `maxretry` 并封掉回环，**网关会整体失联**。因此 strict doctor
> 以 `fail2ban-ban-scope=loopback_exempt_incremental` 断言该行与递增封禁参数，
> 且 guardrails 在读取投影源时就对缺失的 `ignoreip` 直接 `throw`
> （fail-closed），不等到远端生效才发现。

**推荐流程：双 key 窗口（迁移期零 401）**

自伤封禁只在"旧 key 已被吊销、但仍有消费者在用"时才可能发生。只要迁移期间
**新旧 key 同时有效**，掉队消费者拿到的仍是 200，不产生任何 401：

1. 在 `config.yaml` 的 `api-keys` 里**追加**新 key（保留旧 key），执行 `-Apply`。
   两个 key 同时有效。
2. 逐个消费者切到新 key。此阶段无需停任何消费者，也不必担心 401。
3. 全部切换完成后，**移除**旧 key 并执行 `-Apply`。
4. 吊销后按下面的"掉队探测"确认没有遗留消费者。

**掉队探测（吊销后）**

```bash
# 最近 5 分钟 401 计数
docker logs cli-proxy-api --since 5m 2>&1 | grep -c '401' || true
```

- 若 401 计数在 5 分钟内超过 15，说明仍有消费者在用旧 key：立即把旧 key 加回
  `api-keys`（或让该消费者切换），不要靠等待封禁解除。
- **CPA v7.3.17 不提供客户端 key 归属**：`GET /v0/management/api-key-usage`
  虽然存在，但按 **provider + provider key** 分桶（实测为 ai.input.im /
  codex-ciii / deepseek / http-bridge-8003 / zhipu-plan 各一条），**不是**按
  调用方公共 key 分组；`/usage-queue` 的记录里虽有 `api_key` 字段，但该端点是
  破坏性读取（会弹出记录），不适合做常规观测。因此本页不提供"按消费者区分
  key 使用"的自动读数——这正是 2026-09-26 审查中"双 key 过渡"一项**仍未闭环**
  的原因。
- 若必须在不加回旧 key 的情况下探测掉队消费者，可**在受控窗口内**临时放宽
  jail（人工、有界、可回滚），而不是缩短 `bantime`：

  ```bash
  # 备份 -> 提高 maxretry -> 探测 -> 立即恢复
  cp -a /etc/fail2ban/jail.d/cpa-gateway.conf /root/cpa-jail-before-rotation.conf
  # 将 maxretry 临时改为 200，reload，探测窗口结束后必须改回 20
  fail2ban-client reload cpa-gateway
  # ...探测...
  cp -a /root/cpa-jail-before-rotation.conf /etc/fail2ban/jail.d/cpa-gateway.conf
  fail2ban-client reload cpa-gateway
  ```

  这只是把"自伤"的概率降到可接受，不是安全增强：窗口内对真实 brute-force 的
  容忍度也同时被放大，因此必须人工在场、窗口尽量短，并在结束后确认
  `maxretry=20` 已恢复。

**自伤封禁解封**（非自动化，单次人工）：
```bash
fail2ban-client set cpa-gateway unbanip <client-ip>
fail2ban-client get cpa-gateway banip   # 验证已解封
```

---

## L3 风控静默期：OAuth lane 硬门与质量探针

### 可逆隔离（服务端硬门）

`-QuarantineOAuthLuna` 把两个 Luna 别名从可路由目录中移除，使**任何**持有公共
key 的消费者（不限于本仓自动化）都无法再触达唯一 ChatGPT Plus 账号：

```powershell
# 隔离：停 OAuth lane 流量，保留凭据与刷新
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
# 恢复：必须独立显式执行，不会因 timer、探针或客户端重试自动解除
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
```

契约要点：

- 机制是 `oauth-excluded-models.codex` 加 `/opt/cliproxyapi/oauth-quarantine.json`
  状态标记，与 `-Apply` 计算排除清单用的是同一条路径，只是方向相反。
- 事务开始前严格确认**恰好一个**活跃 Codex OAuth 凭据；拓扑不符即 `REFUSE`，
  不发生任何写入。全程持有
  `/run/vps-ssh-launcher-maintenance.lock`，与 updater、`-Apply`、系统维护互斥。
- **不读、不复制、不删除、不回放**凭据 JSON；后台 token 刷新继续运行，slot 不会
  因静默期过期；**不**调用 `reset-quota`，**不**清理冷却状态。输出
  `OAUTH_CREDENTIAL_RETAINED=yes` 与 `QUOTA_STATE_RESET=no` 作为证据行。
- 隔离期间 strict doctor 报 `oauth_quarantine=active` 且视为契约成立（不误报
  OAuth 故障）；发现被阻断但无标记时报 `UNMARKED_OAUTH_BLOCK` 并 fail-closed。
- 隔离期间 `-Apply` 会拒绝执行（`REFUSE OAuth lane quarantine is active`），
  防止例行投影把排除清单改回原样、静默撤销风控决定。
- 隔离与恢复都是单事务：备份 config.yaml 与标记、`docker restart`、目录契约
  校验、`cpa_policy.py` 语义复验，任一步失败即 `restore_all` 回滚并报
  `ROLLBACK_VERIFIED` / `ROLLBACK_FAILED`。
- 这是**流量闸门**，不是凭据处置，也不是"重置风控"；销毁凭据仍只有
  `-DeactivateOAuthLuna`。

### 非 OAuth 质量探针

`generation-all` / `quality-canary` / `quality-eval` **默认已经排除** OAuth lane，
因此静默期可以直接跑：

```bash
# 默认即为非 OAuth 矩阵，逐行输出各路由 GENERATION 结果
python3 /opt/cliproxyapi/cpa-health.py generation-all

# 需要语义验证时同样默认安全
python3 /opt/cliproxyapi/cpa-health.py quality-canary

# 仅当已确认事件状态不是 L3、且本次确实需要复核 OAuth lane 时才回选
CPA_HEALTH_INCLUDE_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py generation-all

# CPA_HEALTH_NO_OAUTH=1 仍是优先的硬抑制，即使同时写了 INCLUDE
CPA_HEALTH_NO_OAUTH=1 python3 /opt/cliproxyapi/cpa-health.py generation-all
```

经 SSH 运行时可加 `python3 -u` 及时输出逐路结果，并用 launcher 的
`--command-timeout` 和 `--command-hard-timeout` 给评估留出有上限的窗口。
先跑 `quality-canary` 查看逐路 `GENERATION` 结果。探针遇到首个失败即退出，
此前通过的路由保留各自的验收结果，后续路由仍待验证。`quality-eval` 返回
`LOCAL_CONTRACT_FAILED` 时需要进一步定位具体模型和用例，单凭退出码无法判断
是接口响应形状、模型名还是语义断言失败。若单宿 API key 路由返回 `403`，
保留该路由并核对官方控制台的权限、订阅状态和脱敏请求记录。
OpenCode 的 `403` 需同时核对响应 `message`。2026-11-18 的 Python 健康探针
收到 `error code: 1010`，CPA 包装码为 `insufficient_quota`，不能仅凭该包装码
判定 Go 订阅额度耗尽。该次冷却自然结束后，同一单宿路由经 curl 单次请求返回
HTTP 200。探针显式设置 User-Agent 并投影后，非 OAuth 质量矩阵通过；若再次
出现 1010，停止重复探测并核对响应及冷却状态。

每次运行会输出一行 `PROBE_BUDGET`，给出 `models=`、`cases_per_model=`、
`planned_generation_requests=` 与 `oauth_lane=excluded:<原因>`，用来在执行前
确认这次运行的计划请求量。`readiness` 和 `generation`（单目标
`glm-5.3-flash`）路径本来就不发 OAuth 请求，不受该开关影响。

**注意**：探针开关只约束探针。静默期要挡住外部消费者必须用
`-QuarantineOAuthLuna`。

---

## 已知限制（2026-09-26 深度审查）

- **上游 `Retry-After` 已由 CPA 前的 lane admission 有界尊重**：CPA 自身的
  `transient-error-cooldown-seconds` 于 2026-10-31 由 60s 收紧到 30s（短过载窗
  曾被 60s 下限拖长成整段零出网 `503 auth_unavailable`；显式上游 `Retry-After`
  仍优先于此下限），doctor 也只把上游
  `Retry-After` 分类成 `absent` / `seconds` / `other`；但公共 Nginx 路径上的
  三条共享官方账号 lane 会先于 CPA 处理容量类 `429/503`。有效
  `Retry-After` 直接打开对应 lane 的冷却窗口，安全上限 86400 秒；无 header
  时使用 `60/120/240/480/900` 秒阶梯。熔断期间新请求由本地返回带剩余时间的
  `429`，不会送入 CPA。该策略只覆盖经过公共 Nginx 数据面且模型别名已登记到
  lane 的请求；本机直连 `8317` 或新增未审查别名不会被误认为已受保护。
- **fail2ban 递增封禁仍可能误伤良性 401 风暴**：首次封禁 1h，随后按 2 倍
  递增至最多 7 天。密钥轮换窗口内客户端用旧 key 高速重试仍可能触发自伤封禁
  （不是 provider 封号）。**预防**见上文"密钥轮换前操作清单"的双 key 窗口
  流程；解封走远端 `fail2ban-client`，不自动化。缩短首次 bantime 不会取消
  递增机制，也不应被当作规避刷 401 的手段。
- **lane admission 不是账号配额**：入口限流仍是 per-IP；CPA 前的
  `cpa-admission.service` 现为四条 lane（2026-10-21 更新；deepseek-official 已
  2026-10-11 退役，Claude OAuth 已 2026-10-20 随账号停用退役）：ChatGPT OAuth
  `max_inflight=2`、GLM Coding Plan 3、Antigravity OAuth 2、WorkBuddy OAuth 1
  （禁突发工程化）；
  全部 `max_pending=4`、队列 120s、冷却阶梯 `60/120/240/480/900` 与 capacity
  熔断。这是针对 desktop 同一轮主响应与
  标题/摘要并发请求的有界预算，不能
  提高 provider 额度，也不能证明长期使用模式不会触发风控。CPA 管理面没有
  任何 per-key/per-account 的 QPS/RPM/token 预算键，客户端 semaphore 仍是
  第一层自律。
- **OAuth lane 静默期已有硬门（2026-09-26 收口）**：`-QuarantineOAuthLuna` 把
  Luna 别名从可路由目录移除，隔离外部消费者；`generation-all` /
  `quality-canary` / `quality-eval` 也已改为默认排除 OAuth lane，只有
  `CPA_HEALTH_INCLUDE_OAUTH=1` 才回选。两者是互补的：探针开关管本仓自动化，
  `-QuarantineOAuthLuna` 才管外部消费者。隔离不销毁凭据、不重置配额/冷却。
- **本地限流 429 已带明确 `Retry-After`（2026-09-26 收口）**：被本地
  `limit_req`/`limit_conn` 拒绝的响应带 `Retry-After: 1`，由
  `map "$limit_req_status:$limit_conn_status"` 守卫，只在真正被本地拒绝时出现；
  `200`/`401`/`404` 与上游透传的 `429`/`5xx` 不受影响。strict doctor 以
  `safe-throttle-retry-after=OK` 与 `throttle-retry-after-map-count=1` 冻结。
  受控压测方法与实测读数见
  [cpa-gateway.md](cpa-gateway.md) 的"入口限流的实际作用"。注意客户端仍应按
  自身退避策略处理：`1` 是下界而非预测。

### 接受项与观察口径（不要当待修项）

审计工具 `scripts/cpa_admission_risk_audit.py` 当前本机姿态的基线是
`fail=0 warn=5`，不是 `warn=2`。不同警告分别表示已接受的风险、本机日志不可证，
或需要另行核对路由；它们记录的是**已经做过的取舍**，不是统一自动修复项：

- **`lane-cooldown-server-cap`：上游 `Retry-After` 可以超过 lane 的阶梯上限。**
  每条 lane 声明 `cooldown_cap_seconds=900`，但服务器退避路径用的是
  `retry_after_max_seconds=86400`。一条带 `Retry-After` 的 `usage_limit_reached` 因此
  能把**整条** `chatgpt-oauth` lane 钉到数小时（历史实测 11705–12432s，即 3.2–3.45h）。
  这是**故意**的：上游明确要求退避时继续压它才是封号风险。
  ⇒ 见到数小时的 429，先读远端 `curl -s http://127.0.0.1:8318/healthz` 的
  `cooldown_remaining`，确认是"在等账号恢复"而不是"客户端有问题"。
  **不要**为了缩短它去调小 `retry_after_max_seconds`。
- **`capacity-probe-window`：容量标记只在前 `probe_bytes`（256 KiB）字节里解析。**
  理论上流到一半才出现的 `server_is_overloaded` 不会被计入熔断。**实测未发生**：
  近 72h 本机口径里 `http_status=200 且 success=0`（流内失败）的行数为 **0**，
  即没有一次"HTTP 通了、流里才报容量"的样本。⇒ 维持 256 KiB，
  等出现真实样本再动（动它就是 admission 契约变更，须同一提交改
  `.py` + `.json` + 测试 + runbook）。
- **`plaintext-http-provider`：slot 6 Start Plan sidecar 的 Docker-bridge HTTP
  明文传输已由用户明确接受。** 防火墙只允许容器网桥访问 `172.17.0.1:18082`，
  不对公网暴露；但同宿主具备抓包/转发能力的观察面仍可读取 key、提示词和回答。
  不得向该通道发送敏感数据，也不得把改 TLS/unix socket、轮换凭据或更改出口
  当作消除此警告的自动修复。
- **`posture-data-stale` 与 `advertised-failing-route-stale-data`：**
  本机 Cockpit 日志已停更，这两条只是明确声明 live/历史分类不可证；必须改用
  BWG 的 admission journal 判读当前请求，不能把本机旧失败说成当前活故障。
- **`gpt-6.1-sol-input` 不在当前路由宣告面：** 它只保留为两个排除集中的退休墓碑。
  审计不再把它的历史失败报作活跃路由；其余仍在活跃路由中的告警也不能因这条退休
  而一并隐藏。

**先跑审计再归因**：

```bash
./.venv/Scripts/python.exe scripts/cpa_admission_risk_audit.py --hours 24
```

它把"配置是否安全自洽"（冷却阶梯、容量信号覆盖、lane 成员可路由性、路由/排除集合代数）
和"仍在宣告但已大面积失败的路由"分开报，判读见
[cpa-admission-risk-audit.md](cpa-admission-risk-audit.md)。

---

## 2026-10-04 全面审查：风险控制覆盖矩阵与残留项

针对"封号 / 限流 / 降智"三类风险，把**已有的控制点**（实现位置 + 机器判据）与
**尚未覆盖的部分**一次列清。审查可重复：跑
`scripts/cpa_admission_risk_audit.py --hours 24`、`scripts/cpa_failure_triage.py --hours 24`、
`scripts/cockpit_provider_health.py`，再按本表逐行对号。

### 覆盖矩阵

| 风险 | 控制点 | 实现位置 | 机器判据 | 状态 |
|---|---|---|---|---|
| 封号·并发 | 共享账号 lane 有界并发（2026-10-20 现值：chatgpt 2 / zhipu 3 / antigravity 2 / workbuddy 1；deepseek 已 10-11 退役、claude 已 10-20 随账号停用退役） | `cpa-admission.json` `lanes[].max_inflight` | 审计 `lane-*`；常量与 JSON 强等（`cpa-admission.py:62`） | ✅ |
| 封号·排队 | 有界 FIFO + `max_pending=4` + `queue_timeout=120` | `LaneState.acquire` | triage 的 `admission_queue_timeout` 分层 | ✅ |
| 封号·退避 | 阶梯 `60/120/240/480/900`；上游 `Retry-After` 立即生效、上限 86400 | `LaneState.release` | `lane-cooldown-ladder-*`、`lane-cooldown-server-cap` | ✅（server-cap 为接受项） |
| 封号·半开 | 冷却期只放**一个**需求驱动探针，探针失败保持熔断 | `acquire` 的 `early_probe` / `half_open` | `/healthz` 的 `half_open_probe` | ✅ |
| 封号·拒绝质量 | shed 返回诚实 `Retry-After`（不是恒 1） | `_shed_retry_after` | `test_cpa_admission.py` | ✅ |
| 封号·误伤 | 传输抖动 / 客户端断开**不**推进熔断 | `_proxy` 的 `except` 分支置 `capacity_error=False` | journal `transport_failure` / `downstream_disconnect` | ✅ |
| 封号·闸门覆盖 | 声明 `admission_lane` 的路由必须被该 lane **全覆盖** | 清单 + `cpa-admission.json` | 审计 **`lane-route-coverage-missing`（2026-10-04 新增）** | ✅ |
| 封号·上游身份 | fail2ban `cpa-gateway` + 回环豁免 | `cpa-fail2ban-jail.conf` | doctor `fail2ban-ban-scope=loopback_exempt_incremental` | ✅ |
| 限流·入口 | nginx `limit_conn cpa_cc`=20 / `limit_req`；被拒带 `Retry-After: 1` | `cpa-gateway.conf` | doctor `safe-throttle-retry-after` | ✅ |
| 限流·客户端 | Cockpit 本地闸门等待预算 45000ms（持久 collection → 生成 manifest） | `scripts/cockpit_sidecar_policy.json` | `cockpit_gate_wait_cap_check.py` | ✅ |
| 限流·轮换自伤 | 双 key 窗口（新旧并存 → 逐个切 → 移除旧 key） | 本页"密钥轮换前操作清单" | 401/5min 计数 | ✅（掉队探测仍未闭环） |
| 降智·静默换模型 | doctor `==model-substitution==`（仅观测） | `cpa-health.py` / doctor | WARN 计数 | ⚠️ 仅观测 |
| 降智·客户端侧 | 本机 sqlite `requested_model` vs `upstream_model` | 日志字段 | 逐对计数 | ✅（24h 100% 一致） |
| 降智·容量型降级 | 体内容量标记（`200` + `server_is_overloaded`） | `is_capacity_response` + `capacity_markers` | `lane-capacity-marker-missing`；`late-stream-failures` | ⚠️ 只解析前 `probe_bytes`(256 KiB) |
| 封号·跨模型污染 | 点名**单个模型**的容量信号只停该模型（`model_capacity_markers`），凭证级信号才停整条 lane | `capacity_scope` + `model_capacity_markers` | 审计 `lane-model-capacity-scope-missing` / `lane-model-capacity-marker-scope`；doctor `admission-health`；`/healthz` 的 `cooldown_scope`、`model_cooldowns` | ✅（2026-10-10 实测 + 受控 A/B） |
| 封号·容量识别覆盖 | 上游容量响应是否**有落在词表外的信号** | `scripts/cpa_error_dump_forensics.py` | 该工具退出码（`1` = 发现未识别信号） | ✅（2026-10-04 实测无盲区） |
| 封号·配额预算 | —— | —— | —— | ❌ **无**（CPA 管理面无 per-account QPS/RPM/token 预算；OAuth 后端不暴露余量） |

### 残留项（需要决策或显式授权）

1. **admission 不看响应头 —— 2026-10-04 实测证伪，不要改。** `is_capacity_response` 只用
   `status` + `probe` 体 + `Retry-After`；曾怀疑上游把头当唯一信号。对 5 份保留转储取证后：
   `x-ratelimit-*`、`x-retry-metadata`、`resets_at`/`resets_in_seconds` **全部 0 次出现**，
   容量响应一律是 `503` + `Retry-After`(22–59s) + `server_is_overloaded` —— 三者都已被现有
   逻辑覆盖。**不要**因此给 `capacity_markers` 加 `auth_unavailable`（它只是 CPA 对
   503 的包装文案，重复加只会让路由/鉴权类失败也开整条 lane）。复核方法见
   [cpa-error-dump-forensics.md](cpa-error-dump-forensics.md)。
2. **`Retry-After` 期间零探针。** 阶梯冷却走 `_next_probe_at`（10s 后可探），而服务器退避
   路径要求 `now >= _server_not_before` ⇒ 带 `Retry-After` 的冷却**全程不探测**。
   这是"尊重上游退避"的**故意**取舍，**不要**为了缩短它去改 `retry_after_max_seconds`；
   真要放宽必须单独论证"探针本身是否构成风控风险"。
3. **CPA 自身 429 与上游容量 429 在 journal 里同形**（`upstream_result ... status=429
   capacity=true`）。转储取证已确认容量响应是 CPA 自己的 `auth_unavailable` 包装
   （`code=internal_server_error`）随 **503** 到达；429 只占容量事件的一小部分
   （`status=429 capacity=true` = 9，对照 `503 capacity=true` = 136）。归因时跑
   `cpa_error_dump_forensics.py`，不要只看状态码。
4. ~~外部参考 checkout 落后一个大版本~~ ⇒ **2026-10-04 已更新到 v8.0.13 并逐条复核**。
   ⚠️ **2026-10-15 起"与部署一致"这个前提已失效**：bwg 已部署
   `eceasy/cli-proxy-api:v8.0.29`（升级记录见 `outputs/deploy-cpa-v8029-canary-20261015.sh`），
   而 `D:/CODE/external/vps-ssh-launcher-references/repos/CLIProxyAPI` 的**工作树仍停在
   v8.0.13（`d7914afd`）**。读源码要对部署版本读：
   `git -C <checkout> show v8.0.29:<path>`（对象库已有该 tag，只读，不 fetch/checkout）。
   **2026-10-20 更新**：上段"工作树停在 v8.0.13"已再次过时——2026-10-17 起该 checkout
   切为 **floating main**（实测 `355fd10e` = tag v8.0.31；`references.manifest.json` 登记为
   "weekly floating main"，由外置参考仓 Advance 流程推进，生产版本仍以 change-evidence
   为准）。读源码的新纪律：先 `git -C <checkout> describe --tags` 核对对象库覆盖到的最新
   tag，部署版本 ≤ 它才可 `git show <部署tag>:<path>`（例：生产 v8.0.34 > 对象库
   v8.0.31 ⇒ 不可直接 show，须用户授权 fetch/Advance 或上游 tarball 只读消费，勿臆断
   行号）；8.0.x 机制级结论可跨版本引用，常量值（UA 串/beta 日期/冷却秒数）以生产
   二进制实测为准。
   2026-10-15 复核：下列锚点的**文件与符号在 v8.0.29 仍存在**——`modelCooldownError`
   （`sdk/cliproxy/auth/selector.go`）、`conductor_cooldown.go` 里的
   `auth.Quota.Reason == "credential_quota"`、`catalog_credential_quota_regression_test.go`、
   `internal/config/config_v8.go`、以及 `config_types.go` 中注释仍写 Default is false 的
   `StreamBootstrapBuffering`；但**行号已漂移**（该字段 203 → 208），语义未逐条重跑。
   2026-10-04 的复核结论（按 v8.0.13）：
   - **`Retry-After` 仍只由 CPA 自己的 `modelCooldownError`（429 / `code=model_cooldown`）
     设置**（`sdk/cliproxy/auth/selector.go:360`），**从不透传上游**；上游文本只以脱敏摘要
     `last_upstream_error`（≤256 字符）进响应体。⇒ admission 尊重 `Retry-After` = 尊重
     **CPA 对凭据恢复时间的判断**，不是 provider 的；对单账号 lane 这是正确方向。
   - **2026-10-21 修正（源码复核，checkout `355fd10e`=v8.0.31）**：上条"只由
     `modelCooldownError` 设置"已过时——v8.0.31 另有
     `authUnavailableError.Headers()`（`sdk/cliproxy/auth/errors.go:322-328`，
     引入于 6724a958，注释明言 exposes only the locally computed recovery hint,
     never upstream headers）与 `SafeResponseHeaders`（`home_concurrency.go`）
     两条**本地计算** Retry-After 出口。"从不透传上游头"与 admission 语义
     （尊重 CPA 对凭据恢复时间的判断）均不变。
   - 凭据级配额冷却已移到 `sdk/cliproxy/auth/conductor_cooldown.go`
     （`auth.Quota.Reason == "credential_quota"`），并有 `catalog_credential_quota_regression_test.go` 钉住。
   - **v8 新增 YAML 布局迁移层** `internal/config/config_v8.go`：旧顶层键是**别名**
     （`codex.stream-bootstrap-buffering` → `upstream.codex.stream-bootstrap-buffering`，
     `codex` → `oauth.providers.codex`），canonical 路径"存在即优先"。
     `StreamBootstrapBuffering` **默认 `false`**（`internal/config/config_types.go:203`），
     所以部署里那个旧写法（顶层 `codex:`）**两条路径下都生效为 `false`** ⇒
     admission 依赖的"200 + 体内容量标记"行为成立。
   - ⚠️ **`v813` 是 v8.0.13 的简写，不是 v8.1.3**（`outputs/cpa-v813-direct.sh`、
     `docs/change-evidence/20261003-bwg-cpa-v813-direct-upgrade.md` 讲的都是 v8.0.13）。
     **不要再按 v8.1.3 归因。**

### `reference_only` 参考树现状（2026-10-15 实测）

`D:\CODE\external` **当前处于经用户授权的长期解锁态**：`reference_only` 由规则与
skills-manager 的 baseline（`references/external-readonly-baselines.json` 中的 HEAD/origin/
cleanliness 检查）执行，**不再由 ACL 执行**。实测（skills-manager 仓内，只读）：
`pwsh -NoProfile -File .\scripts\lock-external-repos.ps1 -Status` 报告 `locked=False`，
9 个顶层写探针全部通过（`cockpit-tools` 现为指向 `_shared/repos/cockpit-tools` 的
junction；探针与 `ExternalGitRepositoryDiscovery` 都跳过 reparse point）。

2026-10-16 该清单已按用户显式授权连续重钉三次：#101 把检出纳入共享仓根（98 条登记、
14 条退休）、#102 退休已移除的 `codex-meter`（97 条）、#103 收录 `cockpit-tools` 搬迁
与同日 Advance 的 14 次 fast-forward；现为 **97 条登记**，`verify-reference-governance.ps1`
恢复 exit 0。重钉仍须显式授权，不要自行执行。

⚠️ **结构性失配（不是损坏）**：baseline 钉的是每个仓的精确 HEAD，而
`invoke-external-reference-maintenance.ps1 -Mode Advance` 会 fast-forward 浮动仓。
2026-10-16 00:52–00:55 的一次 Advance 推进了 14 个仓（报告
`external-update-20261015-165543-234.json`：`mode=Advance total=105 success=104
protectedSkip=1 operationalFailure=0 exitCode=2`），verifier 随即报 14 条
`HEAD drifted from baseline`，重钉后回到 exit 0。⇒ **每次 Advance 之后都要显式重钉一次**，
否则 `reference_only` 一直是红的。对应自动化 `外置参考仓每周 Advance`
（`~/.codex/automations/advance/automation.toml`）当前状态为 `PAUSED`。
`-Mode Observe` 只刷新 inventory/state 与 fetch，不写 HEAD。

- **不要为了让护栏"看起来生效"去重装 deny ACE**。重新锁定必须**显式** `-Lock`；AI 会话
  不自行加/改 ACL，也不重钉 baseline。解锁态下的写操作只受规则约束。
- 历史做法（2026-10-04 前）：根上挂显式 DENY ACE
  `sciman-home\sciman:(OI)(CI)(DE,WD,AD,WEA,DC,WA)`（`inherited=False`）。DENY 只挡写/删、
  **不挡读**，所以锁着也能 grep 源码。
- 历史坑（仍有效）：Git Bash 会把裸 `/deny` 当成 Windows 路径（报 `无效参数"…\deny"`）而
  **静默跳过恢复**；`icacls /deny` 与 `Set-Acl` 的 Deny 规则在本机工具层会被**直接终止**
  （SIGTERM / 无输出）。⇒ 装卸 ACL 这一步必须预留人工执行。
- 需要更新参考仓时：先取得用户对精确根目录、动作和 stop 的当前授权，再按 skills-manager
  的 `-Status -Deep` / `-RefreshBaseline` 流程走；本仓不接管外置根。只读查阅随时可用，
  且优先用 `git show <tag>:<path>` 读部署版本，不必改动工作树。
5. `gpt-5.5` / `gpt-5.6-sol` 仍在桌面目录里可选但公网网关不可路由（见
   `cockpit_provider_health.py` 的 `desktop-model-unroutable`），处置在 Cockpit UI。

### 同日证据（本机 sqlite 24h，窗口末 2026-10-04 01:12，Direct API 链路）

- 643 请求 / 110 失败（17.1%），其中 **90 个是 `gpt-5.6-terra` 的过期爆发**（最后失败
  656 min 前，而该模型**最新一次请求成功**、在 79 min 前）⇒ 活失败 ≈ 20（3.1%）。
- 6 个 `quota_or_rate_limit`(429)：1×838ms 快速 429、**1×45047ms = 本机闸门**、
  **4×120458–124918ms = admission `queue_timeout`** ⇒ 24h 内**没有**上游容量型 429 到达客户端。
- 失败时延分布：`<1s` 54、`1–45s` 51、`45–46s` 1（闸门）、`≥119s` 4（队列）。
- `error_message` 含 capacity/overload = **0 行**（A 的载荷只存在于客户端渲染）。
- `requested_model == upstream_model` 100% ⇒ 24h 内未观察到降智/换模型。

⇒ **本地闸门与 admission 侧修复确实生效**（busy/cooldown ≈ 0；429 只剩闸门与队列预算），
残留全在上游（上游 5xx + 单账号吞吐）。

## 批量封号窗判读（2026-11 社区反馈）

社区在 2026-11 集中报告 OpenAI/GPT 再次出现批量封号。把社区叙事与本栈**实测**分开，
是本页对"要不要立刻动网关"的唯一判据：

### 与代码无关、但与账号风险直接相关的官方口径（2026-11）

| 事实 | 强度 | 对本栈的含义 |
|---|---|---|
| 设备与支付卡双上限覆盖 Go/Plus/Pro，**退登不释放名额**，买 credits/重置不解除 | [社区转述官方帮助页](https://www.nediya.com/insights/2026-11-chatgpt-device-limit-shared-pool-gift-card.html)（官方页 `help-lb.openai.com/.../20001559`；具体数字未公布） | 不在同一设备上叠加/轮换账号登录；本栈的 OAuth 凭据都在服务器侧，**不因此豁免** |
| 12 月起 Plus/Pro/Business 的用量在 ChatGPT 与 Codex 间共享同一上限 | **2026-11-27 一手复核**：官方帮助页 [20001625](https://help.openai.com/en/articles/20001625-understanding-chatgpt-and-codex-usage-limits) 原文 "Starting in December, Plus, Pro, and Business plans' usage allowance will be shared across ChatGPT and Codex…"。此前本行误引 20001559——那篇是卡/设备限制页，全文没有合并额度的语句 | 唯一 Plus 账号（`chatgpt-oauth` lane）的 Codex 用量会与网页额度互相挤占；限流会更早出现，**不应**用加号/换号对冲 |
| 停用原因含"账号或 API key 不当共享""规避安全或访问限制" | [OpenAI 帮助页转述](https://qcode.cc/en/codex-account-ban-guide)（2026-08-03 版） | 这条正是消费级 OAuth 经第三方向多台设备供数的条款风险本体；也是本仓长期登记为"不可本地消除"的残余风险 |
| 官方建议为自动化/多用户使用 API key（按 token 计费） | [同上](https://qcode.cc/en/codex-account-ban-guide) | 唯一结构性的规避手段是迁移官方 API/工作区档位；属用户决策，agent 不擅自迁移 |
| `safety_identifier` 用于滥用检测归因，`prompt_cache_key` 用于缓存路由，二者**不同生命周期** | [OpenAI 安全最佳实践](https://developers.openai.com/api/docs/guides/safety-best-practices.md)、[迁移说明](https://dev.to/ssukhpinder/openai-responses-api-user-migration-split-safety-from-prompt-caching-32io) | 不要为"防风控"把二者互填；本栈 Codex 执行器本就剥离 `safety_identifier`（`codex_executor_execute.go`），保持不动 |

**2026-11-27 一手复核补充**（官方页直取 + openai/codex issue；子代理的搜索配额当日耗尽，
Reddit / X / linux.do 等论坛未覆盖，下列"没有查到"都只限所查范围）：

- **合并额度的三个细节**（同一官方页）：按 token 计量（"measured in tokens"，模型越贵、
  任务越复杂扣得越多）；"Instant chat will not draw from your usage"；"A small group of users
  may see the usage changes before December"，所以不能假设 12 月 1 日才生效。合并余额在
  ChatGPT 的 Settings → Usage。
- **GPT-6.2 计量更正**：社区转述 OpenAI 2026-11-26 通知，GPT-6.2 Astra / Sol 发布时计量偏低，
  此后触顶约快 40% / 25%（[openai/codex#64248](https://github.com/openai/codex/issues/64248)，
  通知原文未见）。本栈 `gpt-6.2-sol` 在 ChatGPT 车道目录内。
- **VPS 路径的额度读数可能与住宅线路不一致**：4 个独立账号（都用官方 CLI / 桌面端）报告
  同一 token 经 VPS 线路读到周额度已用尽并被拒、经住宅线路读到剩余 88–93%；重新登录、
  兑换 reset 都无效（[#63977](https://github.com/openai/codex/issues/63977)、
  [#64178](https://github.com/openai/codex/issues/64178)、
  [#64123](https://github.com/openai/codex/issues/64123)）。报告者自注无法区分出口 IP 差别
  对待、区域路由与后端记账，也没有维护者回应。**处置含义**：`chatgpt-oauth` 出现数小时
  乃至数天的 `usage_limit_reached` 冷却时，先从住宅线路的 Settings → Usage 核对真实余额，
  再决定是等窗口还是按上游问题处理；不要重登（每次重登都是一次新的设备登录），也不要用
  reset 兑换去"修"。是否调整出口仍是用户裁决，本页置顶纪律不变。
- **并发即 429**：7 个并发线程触发账号级退避 63–578 秒，退避期间取消后重发会拿到新的、
  更长的 429（[#63861](https://github.com/openai/codex/issues/63861)，单例）。与现行
  `max_inflight=2`、不跨号重放一致，无需改动。
- **封号面**：openai/codex 与 CLIProxyAPI 两仓 2026-11-20 之后没有新的封号报告（关键词
  检索，范围有限）。CLIProxyAPI [#7783](https://github.com/router-for-me/CLIProxyAPI/issues/7783)
  的诉求指向"多用户共享导致封号"，维护者建议多用户各部署独立实例；单人自用、不共享 key
  与现行边界一致。官方 auth 文档列举的登录载体都是官方客户端，并写明自动化建议用 API
  key；所查 auth / pricing 两页内没有"Sign in with ChatGPT 仅限官方客户端"的禁令，也没有
  对第三方网关的许可（只查了这两页，不是全站结论）。
- **单凭据的持久性风险**（未分诊的 bug 报告）：[#8543](https://github.com/router-for-me/CLIProxyAPI/issues/8543)
  关闭时不等待进行中的 auth refresh 落盘，轮换后的 refresh token 丢失，重启后
  `invalid_grant`；[#8539](https://github.com/router-for-me/CLIProxyAPI/issues/8539) auth 文件
  原地改写，崩溃或满盘可能截断。本栈该车道只有一个凭据，丢了就要重新设备登录：换芯 /
  重启避开刷新窗口（doctor 的 `oauth_refresh_policy` / `oauth_hours_left` 可读下一次刷新），
  成功刷新后留一份 auth 文件备份，磁盘余量保持充足。

### 实测：本窗口内没有账号级信号

2026-11-08 只读复核（`cpa_bwg_guardrails.ps1 -Profile bwg` + admission journal 24h +
`docker logs` 168h）：

- 24h 内 OAuth 车道**零** 401/403：`admission_protocol_results` 中 antigravity 的
  `HTTP401` 为 3 次探测失败，计数与 `oauth_*_refresh_failures_7d=0` 一致；容器日志
  168h 内 `status=401|403` 计数为 0。
- 三条在役 OAuth 车道 `lane-hold` 全为 `OPEN`，`oauth_quarantine=none`，
  `workbuddy-version=0.9.88` 已注册。
- 唯一的 24h 落锁形态是 WorkBuddy `operator_review_required`（10 次，2026-11-07
  13:00–14:06Z 区间，已人工释放），与 OpenAI 封号无关。
- 因此：**"社区在封号"不能推出"本栈今天在封号"**。没有账号级信号时，任何
  "预防性"换号、清冷却、调并发都是自伤，正是本页禁止项。

### 2026-11 与 OpenAI 同时期的第二波：Antigravity/Gemini 与 WorkBuddy

同一周社区另报两条独立封号/断流线，性质与 OpenAI 不同，**不是同一套风控**，本页分开判读：

| 线路 | 社区口径 | 官方/结构证据 | 对本栈的含义 |
|---|---|---|---|
| Antigravity/Gemini | 2026-11-06 Linux.do 用户报两个 Google 账号在 magpie 中全部报错，归于"依 ToS 封停 Antigravity 第三方接入"；此前 8 月已有同题报道 | [80aj 2026-11-06](https://www.80aj.com/2026/11/06/google-antigravity-gemini-ban/)（转述论坛帖，非官方公告） | 命中即**账号侧**，本栈无法靠调参规避；`antigravity-oauth` lane 是共享 Google 订阅，属同一条款风险面 |
| WorkBuddy CN/INTL | 2026-10-20 起限制非官方客户端请求；反代类项目相继停维护；社区称有账号积分被清零/封禁 | [liurb 2026-10-20](https://blog.liurb.org/posts/2026/10/20/workbuddy-connect/)（转述停维护公告与社区反馈） | 这是**通道校验 + 账号处置**双轨：既拦非官方客户端，也处置账号；本栈 14 份凭据全部走第三方通道 |

**本栈 2026-11-08 实测对照（拒绝叙事，只看信号）**：

- Antigravity：2 份凭据 `disabled=false`、token 仍在刷新（最近 13:31/13:32 有成功调用），
  7d 容器日志 `antigravity.*(401|403|PERMISSION_DENIED|forbidden)` 计数 **0**。
- WorkBuddy：24h 内 `status=200` 1103 次、`429` 10 次、`500` 7 次；7d 内
  `lane_hold_latched` 仅 2 次（均为 `retry_window_review_required`，已释放），
  **无 401/403**。14 份凭据 token 剩余 308–8595 小时，说明刷新通道正常。
- 结论：**本窗口内两条线均无账号级封禁信号**。社区的大规模封号不能外推为本栈已中招；
  但两条线的条款风险是**真实且结构性**的，本栈不能通过换号、改并发、伪造客户端指纹消除。

**这对"该不该现在动网关"的影响**：

1. 不新增账号、不轮换凭据、不跨 realm 续打——这三条正是被官方识别的滥用形态。
2. WorkBuddy 的 `disabled=false` 不等于"官方认可"；它是本地状态，不是上游授权。
3. 若某凭据出现 `401`/`permission_error`/`request illegal`，按上表停流保留凭据，
   不做自动重登；重新 OAuth 登录本身可能就是被计数的异常行为。
4. 唯一结构性缓解仍是迁移官方 API/工作区档位（用户决策），agent 不擅自执行。

**2026-11-27 复核**（插件作者的 CHANGELOG / 源码 + 社区 issue；论坛未覆盖）：

- **11128 是内容指纹判定**。插件作者的一手消融：system 含 `DeepSeek Harness` 即
  `400/11128`，请求体超过 1KB 才稳定触发，零宽字符变体仍被拦；DSH 的 `@` 文件引用段在
  system 与 assistant 被拦、在 user 放行（`luode0320/cpa-workbuddy-plugin`
  CHANGELOG 0.21.20 / 0.21.23）。社区：同会话第 4、5 轮被拒、24 秒后第 6 轮通过
  （[dsh-connect-workbuddy#329](https://github.com/dingminhua/dsh-connect-workbuddy/issues/329)）；
  `kimi-k3.1-pro` 10/10 被拒，而同账号同 system 下 `glm-5.4`、`deepseek-v4.1-flash` 正常
  （[#322](https://github.com/dingminhua/dsh-connect-workbuddy/issues/322)）。**含义**：
  WorkBuddy 车道上的身份句清洗有实际作用——上游扫描的是 system 与 assistant，现行规则
  覆盖 system / developer / user，48h 内 1189 发全部 HTTP 200（协议级完成 1185、客户端
  断开 3、流内 `upstream_error` 1）。被拒不具确定性，不能靠重试
  硬闯；assistant 角色里的指纹段不在清洗范围，是已知残余面。`kimi-k3.1-pro` 不经此车道。
- **账号级失效的两种形态**：`11140`（账号异常被限制，换模型、等冷却、人工启用都不解除，
  应直接摘号并停止探活——0.21.22 的作者生产观测）与 `12153 refresh token revoked`
  （同一凭据在别处刷新，或同 IP 批量号被踢下线；按"摘号重登"处理，不是等冷却）。
  **纪律：一个账号只给一个消费者**——同一账号若还在官方客户端或第二个网关登录，会整链
  吊销。2026-11-20 之后没有找到"积分被清零"的确认案例。
- **插件默认值变化**：0.21.31 起保活线 400→50、硬禁用线 100→20（未显式配置即生效，所以
  2026-11-27 两个号在 20 分以下被自动停用是新默认的行为）；0.21.32 起失败写盘禁用默认
  关闭，失败只走内存阶梯冷却。
- **上游仓库更正**：插件在
  [`luode0320/cpa-workbuddy-plugin`](https://github.com/luode0320/cpa-workbuddy-plugin)
  （关闭了 issue 区），早期记录的 `cpa-multi-plugins` 地址已 404；
  `ardeyouxipianyi/workbuddy2api-hub` 也已 404，上文 14003 定量画像的出处无法复核，
  口径保留但降级为历史观察。
- **容量与成本**：每天北京时间 15:00–17:00 渠道接近不可用（`6021` / `queue.full`）；
  `glm-5.4` 的积分系数约为 `deepseek-v4.1-flash` 的 6 倍（社区转述的模型元数据，对应关系
  为推断），长上下文放 deepseek 更省；`reasoning_effort: off` 会被 `11133` 拒绝，要关思考
  就不发该字段。插件作者的生产数据里 300K–380K prompt 有 214 发、零失败（最大 372K），
  与本栈实测 338K 输入被完整计入一致——没有为防降智而缩小上下文的依据。
- **模型 ID 必须是小写精确值**：大写模型名曾触发上游 `11102` 并扇出全池（60 分钟 1196
  发）。本栈 `retry_on_4xx: 0`，同类误用的上限是单发。

### 若真命中账号级信号

按上表 `403 request illegal` / `11140` / 认证类 401 处置：**停流、保留凭据、
不清冷却、不换号、不加号**，按官方申诉入口（停用邮件内链接或
`openai.com/form/appeal`，附 User ID/Org ID 与使用说明）处理。gateway 侧只做
`-QuarantineClaudeOAuth` 类**用户指令驱动**的隔离，agent 不代做订阅迁移决策。

## ZCode 起步套餐（Start Plan）车道的官方口径（2026-11-27 复核）

本节只登记官方原文与边车实测；车道去留与清洗范围是用户裁决。

- **官方明文限于官方客户端**：[ZCode FAQ 第 11 条](https://zcode.z.ai/cn/docs/qa)
  "体验套餐（Start Plan）只能在官方 ZCode 客户端里使用。把体验套餐的额度转接到其他工具…
  体验套餐会被暂停：调用时报 Your account has been suspended for misusing the Start Plan
  （405 / 3012）…暂停只针对体验套餐，不影响你购买的 GLM Coding Plan"。`zcode-start-plan`
  车道正是该条描述的形态。体验套餐是新用户 5 天试用（[配置页](https://zcode.z.ai/cn/docs/configuration)），
  "工作日 23:00 起免费 + 整周末"只见第三方转述，官方页写"以登录后 Start Plan 页面的实时展示为准"。
- **付费 Coding Plan 的协议现行版生效于 2026-11-13**
  （[订阅及自动续费协议](https://docs.bigmodel.cn/cn/terms/subscription-agreement) §七）：
  "如通过非官方工具或未授权方式使用，平台有权对相关权益进行限制、暂停或封禁处理"；
  "批量调用"是点名的触发项；永久封禁保留给倒卖、中转、套利。使用须知
  （[usage-notes](https://docs.bigmodel.cn/cn/coding-plan/usage-notes)）的梯度是限制权益 →
  限流 / 冻结 → "3 次以上违规可能会被封禁账号"。官方工具名单含 ZCode、Claude Code、Codex、
  OpenCode、Cline 等，不含 CLIProxyAPI。相对 09-26 的风险接受日是否改过措辞无法核实。
- **边车上游**：`TriDefender/zcode-api`，4.9.1（2026-11-20）仍是最新 release。它对每个上游
  请求注入模拟 ZCode 桌面端的身份头，并构造"官方三块"系统提示词（README 与
  `src/config/template.ts`）。2026-11-20 的 [#85](https://github.com/TriDefender/zcode-api/issues/85)
  标题为"现在可以被官方检测出来了"，维护者指系统提示词不一致是检测面；
  [#83](https://github.com/TriDefender/zcode-api/issues/83) 是同账号 start-plan `405/3012`
  而 coding-plan 200，与官方"只暂停体验套餐"一致（issue 正文经摘要工具转述）。
- **边车实测**（只读）：单元 `zcode-start-plan.service` 自 2026-11-21 运行，配置只有
  `server / auth / provider: bigmodel / plan: start-plan / models / identity`。v4.9.1 的
  `claim.enabled` / `claim.auto` 默认都是 `true`：每 5 分钟用 OAuth JWT 轮询 `zcode.z.ai`
  的活动页并自动领取（含验证码），而本部署用的是 BigModel 起步套餐，轮询没有收益，
  却是凭据上最像"羊毛党自动化"的行为。**2026-11-27 已改**：`claim.auto: false` 追加到
  边车配置（`enabled` 保留，CLI `zcode-proxy claim` 仍可用），重启后 `[claim]` 行为 0。
  该单元不受本仓投影管理；回滚入口与事务见
  [起步套餐台账](../change-evidence/20261127-bwg-zcode-start-plan-quota-and-claim.md)。
- **与清洗规则的关系**：admission 在 `zcode-start-plan` 车道改写 ZCode 身份句
  （`_AGENT_IDENTITY_TEXT_ROLES`）。边车 v4.9.1 源码（`src/proxy/body-transformer.ts`
  `applyStartPlanOpenAISystem`、`src/proxy/system-prompt.ts` `buildStartPlanSystem`）
  证实两者**不冲突**：网关靠 `system` 字段里的官方身份块识别 ZCode，而边车是在
  **出站时**才前置这三块（块 2 是字节精确的官方稳定文本）；admission 在边车之前改写的
  只是客户端自己带来的 system 文本，碰不到官方块。官方 FAQ 的执法条件是"把额度转接到
  其他工具"，清洗一句身份句既不构成也不消除这一条件。**2026-11-27 用户裁决：车道保留，
  清洗规则保持现状**（不扩、不撤）。上一版本此处"方向性冲突"的说法已撤回：边车维护者
  在 [#85](https://github.com/TriDefender/zcode-api/issues/85) 的原话是"系统提示词肯定是不一致的"
  （针对任意第三方客户端，报告者用的是 zai 侧 + omp 客户端），不是指身份句改写。
  已知残余：边车把客户端系统提示的其余部分原样放进块 3，本仓改不了。
- **502 的真实原因（2026-11-27 实测，取代"边车自身挂起"的推断）**：上游
  `zcode.z.ai` 对额度耗尽回的是 **HTTP 200 + 业务错误体**
  `{"code":1005,"msg":"exceed quota limit"}`；边车无法把它翻译成流，产出
  `502 translation_failed`，把上游 JSON 原文引在自己的 message 里。所以该车道看到的 502
  = **起步套餐额度已用完**，不是风控、不是边车故障、也不是免费窗口的问题。已修：
  `exceed quota limit` 加入该车道 `capacity_markers`，admission 现在把它当容量信号入
  冷却阶梯；此前它被当成传输故障（`upstream_error`），不冷却，客户端可以立即重试。
  排查手筋：普通 `serve` 模式只留一行 `502` 表格行，不留原因——**先直接对边车发一发
  非流式请求读原始错误体**（边车端口 18082，用边车自己的 `proxyApiKey`），比临时切
  `serve debug` 更省，也不用再改单元。
- **边车运行手筋**：`auth status` 必须在服务的完整环境里跑——只设 `HOME` 不带
  `ZCODE_PROXY_CREDENTIAL_SECRET` 会报 "Ignoring corrupted or stale credentials / Not
  logged in"，那是解密种子缺失的假象，不是凭据坏了（2026-11-27 实犯）。正确写法：
  `set -a; . /etc/zcode-start-plan.env; set +a; zcode-proxy auth status`。
- **降智与用量**：官方 FAQ 否认自动换模（"在模型选择器里选哪个，就用哪个"），只有高峰
  （工作日 14:00–18:00 UTC+8）排队限速的表述。GLM-5.4 思考下限是 `low`，开启思考但缺省
  `reasoning_effort` 会落到 `max`（[latest-model](https://docs.bigmodel.cn/cn/coding-plan/latest-model)）。
  ZCode 按 `usage` 判断是否压缩，网关须原样回传，否则客户端会反复压缩。
- **信号处置**：`405 / 3012` = 体验套餐被暂停或临时风控，admission 现在把结构化
  3012（含 `zcode-proxy` / CPA 包装体内的内嵌 `3012`）或明确的
  `misusing the Start Plan` / 暂停文案写成 `account_restricted` 锁，停该车道、
  不重试、不突发；空的 405 或无账号证据的通用 `Method Not Allowed` 仍不落锁。
  `exceed quota limit` /
  `code 1005` = 套餐额度用尽，已入冷却阶梯；`1308` / `1310` = 5 小时 / 周额度窗，
  退避到重置点，不在两条 GLM 车道间同时重试放大；边车 502 先按上一条手筋读原始体，
  再判断是额度、风控还是边车故障。
- `zcode-start-plan` 已纳入 `DURABLE_OAUTH_LANES`，经 `cpa_admission_bootstrap.py`
  绑定 `--zcode-start-plan-state-file /var/lib/cpa-admission/zcode-start-plan-risk.json`
  落盘（0600 `nobody:nogroup`），并接入 `cpa-guardrail-doctor.sh` 的权限与 `lane-hold`
  门禁及 `cpa-auto-update.sh` 重启前后状态比对，3012 封控锁跨重启持久生效。

## 禁止

2026-10-19 用户明确授权现有账号池故障切换：WorkBuddy 的可识别
11128 请求拒绝会临时冷却失败凭据 5 分钟，由 credits 调度选择其他可用账号。
这是本地熔断窗口，不是上游承诺的恢复时间。明确封禁、认证失败、额度耗尽
仍按对应事件处理，不以账号切换绕过人工 hold 或上游配额。
CPA 的 max-retry-credentials=1 将每个请求限制为一份凭据，
request-retry=0 禁止额外请求级重试轮次。WorkBuddy 插件自身的 credits 调度仍可影响
之后的独立请求，但同一失败请求不重放到兄弟消费级凭据，也不进入已退役的
OpenCode 扇出。流已输出后不重放。
所有候选冷却或耗尽时插件明确拒绝，不能回落到已冷却凭据。

- 不做对抗性规避（state 注入、UA/cloaking 调整、identity-confuse、第二账号
  轮换）——全部已裁定不采纳。
- 不把 `-DeactivateOAuthLuna` 当作"重置风控"的常规手段：它不可逆地删除全部
  可刷新 OAuth 物料。
- 不在事件窗口调整 nginx 并发/限流参数来"吸收"风控信号：入口阈值不是
  provider 配额，历史上（2026-09-13）已因此误伤独立通道。
- 不把多账号/多 key 当作扩容或防风控手段：不因配额耗尽新增账号、轮换账号、
  换 IP/UA、跨 realm 续打；不共享消费级 OAuth 凭据给外部消费者。
- 保留明文 HTTP 渠道时，按“已接受传输风险”处理：不传敏感数据，不把 key 写入
  日志、证据或提交。

### 探针脱敏（2026-11-25 事故后补）

- **事故**：一次只读的配置脱敏探针没有遮住 `access.api-keys` 的列表项，网关
  接入 key（`agt_gw_` 前缀那一把）明文进入了当次会话输出。它是客户端接入
  key，不是上游 OAuth 凭据；轮换与否由用户裁决，流程见上文
  「密钥轮换前操作清单」。
- **成因**：YAML 允许块序列与父键同缩进（`api-keys:` 与 `- <key>` 同一列），
  脚本只在列表项缩进更深时才沿用机密上下文，本地假值夹具恰好写成了更深缩进，
  所以没复现。
- **纪律**：
  1. 机密键的上下文延续到其后所有 `- ` 列表项，遇到下一个键行才结束。
  2. 任何打印主机配置的脚本，先在本机用**同缩进与深缩进两种**假值夹具跑一遍
     （`python -I`），确认两种写法都被遮住再上主机。
  3. key 只留在主机进程内（管道进解释器，不经命令行参数、不落 `/tmp`、不回显）。
  4. 泄漏后如实报告并给出轮换路径；不要通过重跑探针来「确认没有泄漏」。

## 边界

本页只覆盖 CPA 网关侧的账号风险处置。VPS 系统层（IP 信誉、端口扫描、
fail2ban 误封）不在此页：fail2ban 解封走远端 `fail2ban-client`，不自动化。
Anthropic/Claude 的条款、地区与凭据边界见
[cpa-claude-account-risk.md](cpa-claude-account-risk.md)：claude-oauth lane
2026-10-17 曾在役，2026-10-20 已随账号停用退役（三名认领 + 16 墓碑的目录契约
保留为复活评审基线），复活后本页分级与 claude 信号形态表对其适用；
Antigravity OAuth lane 在役（`==antigravity-lane==` 门禁）。

### 2026-11-18 一手条款原文复核与出口实测

本节只记录本次直接读取的官方原文与只读实测；仍不构成对封号阈值的任何判断。

| 来源 | 原文要点（摘要） | 对本栈的含义 |
|---|---|---|
| [Antigravity Terms](https://antigravity.google/terms) | 通过第三方软件、工具或服务访问服务（例：OpenClaw 配 Antigravity OAuth）属违约，可致 Antigravity 与 Gemini CLI 账号暂停或终止 | `antigravity-oauth` lane 落在条款禁止面内；结构性残余风险 |
| [OpenAI Terms of Use](https://openai.com/policies/terms-of-use/) | 不得共享账号凭据或让他人使用账号；不得自动或程序化提取数据或 Output；不得规避速率限制或保护措施（站点对脚本返回 403，经只读文本代理读取） | `chatgpt-oauth` lane 同属条款风险面；API Key 另适用商业条款，本次未读 |
| [CodeBuddy 用户协议 §8](https://www.codebuddy.cn/agreement) | 不得通过非腾讯云授权的第三方软件、插件、外挂登录或使用本软件；账号需实名并绑定腾讯云账号 | `workbuddy-*` lane 同属条款风险面 |
| [Google 官方公告 gemini-cli#20632](https://github.com/google-gemini/gemini-cli/discussions/20632) | 封禁针对用第三方工具或代理访问 Antigravity 资源与额度；风控在后端统一层，故同时阻断 Gemini CLI 与 Gemini Code Assist；今后被标记账号会收到邮件与专门报错，走自助表单恢复 | 被封后按官方表单恢复，不换出口、不换号重试；**不得用日常主力 Google 账号接入 CPA**，专用账号才可作为可丢通道 |

2026-11-18 只读实测（不输出凭据，标签为 sha256[:8]）：

- 现役 auth 共 25 份，权限均为 600：antigravity 8、codex 1、workbuddy 国内 10（1 禁用）、
  workbuddy 国际 7（6 禁用）；**无一配置账号级 `proxy-url`**，故全部账号共用 BWG 单一出口。
  该事实按本页既有口径登记为接受项：不为防风控换出口；条件触发（多号同窗被封或持续
  IP 级限流）才评估拆分，且逐号小批、先预演、单独授权。
- 7 天窗内唯一 `PERMISSION_DENIED` 信号（标签 `5edbfc5b`）的 `auth_file` 取自日志值，
  不是现役 auth 文件名；现役 25 份均不匹配，较可能对应 `auth-removed-20261105` 中的
  已摘除凭据（推断，未读文件内容）。无现役账号被标记。
- OpenAI 状态页近两周无持续性容量事故，仅 2026-11-09 API 错误率升高等小事件；
  容量不足按短时高峰处理，不据此调整网关。
- 降智：本次未取得可引用的社区量化数据，判读仍以本页 served-model 观测锚为准。
