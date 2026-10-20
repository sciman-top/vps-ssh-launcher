# CPA 封号 / 限流 / 降智应急响应（bwg）

本页是 OAuth（ChatGPT Plus）、第三方中转或官方 API 通道出现疑似风控信号时的
处置入口。设计哲学是 fail-closed：先停止可疑流量，再归因；任何"规避检测"类
操作（state 注入、UA 伪装调整、身份混淆、第二账号轮换）都已明确否决，不在
可选动作里。

## 信号识别（先 doctor，再日志）

一次 strict doctor 覆盖大部分归因面：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
```

按段读数：

| 段 | 关注信号 | 含义 |
|---|---|---|
| `==gateway-statuses==` | 24h 状态分布、`five_xx_local_vs_upstream`、`client_503_retry_pattern`、499 耗时簇 | 区分上游过载、本地冷却与客户端紧重试放大 |
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
**v8.0.3x 起（usage_helpers.go）每个被替换的请求都记一条 warn**（旧版每
（凭据，模型）对 10 分钟一条的节流已不存在），所以 7d 计数随用量放大属正常，
判扩散看 doctor 同段的 `model_substitution_distinct_pairs` 与
`model_substitution_pair` 拆分（只含模型名，凭据哈希不进 doctor 输出）。临时
取证（auth_index 归属或自定义时间窗）仍用手工一行：

```bash
docker logs --since 168h cli-proxy-api 2>&1 | grep 'upstream served model' \
  | sed -E 's/.*served model "([^"]+)" for requested model "([^"]+)".*/\1 <== \2/' \
  | sort | uniq -c
```

- 单一（凭据，模型）对：先查本仓是否已有该对的已记录观察，不重复开案。例：
  2026-10-15 保留窗口内 29 条全部是 `gemini-3.8-flash-n <== gemini-3.8-flash-high`
  （antigravity 单凭据），即已记录的上游命名观察，不单独构成降智证据。
- 替换源属于 OAuth lane 时，**默认** `quality-canary` 看不见它（默认排除 OAuth
  lane），照 doctor 提示直接跑会得到无信息的绿灯。要么停在日志级归因，要么按
  下文 L3 的准入显式回选 `CPA_HEALTH_INCLUDE_OAUTH=1`。
- 只有非 OAuth lane 的替换才适合用默认 `quality-canary` / `quality-eval` 复核。

### 增补信号词汇（多 lane 风控评审：2026-10-18 初版、2026-10-19 增补）

| 信号 | lane | 判读 |
|---|---|---|
| 上游码 `14003` / `6002` | workbuddy | 供应商错误码，尚无官方证据证明固定对应 IP / 账号层级（社区定量画像见下行 14003 行）；记录协议错误和窗口，禁止换 IP、换账号续打。`11140 / request illegal` 须人工核对账号状态，不按普通容量自动恢复 |
| `x-codex-safety-buffering-enabled` / `x-codex-safety-buffering-faster-model` 响应头 | codex | 上游替换路径（safety buffering）指示；比 turn-state 长度更可靠的降级观测信号，记录出现窗口，不按 5xx 处置 |
| HTTP 200 流内 served model ≠ requested model | codex | 模型身份差异观测（与 doctor `upstream served model` warn 同源）；先核对别名、模型族与上游命名，再结合固定样例质量证据评估，不能单独证明降智 |
| turn-state 长度 292/312 | codex | 实为 per-turn 粘性路由 token，单账号可在 292↔312 间翻转（路由抽签），单点读数不构成降智结论；Team 形态另有 332/356 |
| 数据面 `401 "Encountered invalidated oauth token for user"`（刷新面正常、doctor `oauth_days_left` 正常）（2026-10-19 增补） | codex | 上游 [#6742](https://github.com/router-for-me/CLIProxyAPI/issues/6742) 家族：社区在 v8.0.31 观察到的 ~7 天周期性凭据失效。**2026-10-19 更新：已修**——上游 commit `feb6398` 把 proactive refresh lead 提到 96 小时，随 v8.0.34 发布（生产已在 v8.0.34）。复发时处置不变=人工重新授权该凭据一次（或面板手动 refresh-all）即恢复；勿循环重试、勿删凭据、不按封号处置，与刷新失败（`invalid_grant`）分开归因。观察项=v8.0.34 上 7 天周期是否消失（零成本，journal 判读） |
| antigravity `429 RESOURCE_EXHAUSTED`：0.2–1.2s 快拒、配额页正常、词级二分可复现（含指纹句必 429 / 任一词替换即 200）（2026-10-19 增补） | antigravity | **假 429 = 上游对 system instruction 的身份词内容过滤**，不是配额。现行对冲 = `sensitive-words=["gpt","openai"]`（cpa_policy 钉定，只混淆 systemInstruction，user 角色消息不触发）；上游默认混淆 [#6280](https://github.com/router-for-me/CLIProxyAPI/pull/6280) 截至 v8.0.34 未合并，**勿删词表**。复发排查序=错误转储混淆命中计数→分辨带 "Resets in" 的真配额 429→WAF 波动窗（同形状批量 200 有先例）。判据源：20261015 台账 + [aiferry#184](https://github.com/Muqian-Sun/aiferry/pull/184)（10-17 第三独立仓复证机制仍活跃） |
| `11128 request illegal` 家族 / `11158` / `11134` / `11102`（2026-10-19 增补） | workbuddy | 11128 先取 displayMsg 全文分辨子条件（system-first 文案 / #31 WAF 签名句 / 会话头存在性校验[已由 conversation-headers 补丁修复] / 其它），细节在 [cpa-workbuddy-plugin.md](cpa-workbuddy-plugin.md)；`11158` = 免费号模型级墙（intl personal_free_user 被挡高端模型名单，`deepseek-v4.1-flash` 暂不在名单）——**出现即钉定模型进墙、lane 失效的结构信号**，非账号封禁；`11134/11102` = 模型级故障/下线（社区建议勿计入账号熔断）。账号级 vs 通道级判别法（三次实证）=同账号在官方桌面客户端单发 A/B：桌面 200 + 网关拒 = 通道/指纹侧，两侧同拒 = 账号侧 |
| `14003` 定量画像 / `14018` / WAF 403 升级形态（2026-10-19 增补） | workbuddy | 社区 [workbuddy2api-hub#313](https://github.com/ardeyouxipianyi/workbuddy2api-hub/issues/313) 12 号 10h 实测：14003 按**出口 IP 聚合**、计请求数不计 token（单分钟 4.08M token 零 429）、家宽阈值约 13–19 rpm、"墙高随 IP 变"（另一用户单 IP 150 号 10 天稳定）。IP 级限流换 IP 有效、账号级惩罚换 IP 无效——两类正交；处置仍按上行 `14003/6002` 行，不换 IP/换号续打。`14018`=credits、`6004`=quota |
| GLM/sol 渠道信号（2026-10-19 增补） | zhipu-coding-plan / ai.input.im | 429 走 lane 冷却（设计内，勿开案）；`gpt-6.1-sol-input` 404 `optional_route_unavailable`、通道侧 "not priced"/forbidden = ai.input.im 已知偶发（历史 ~8h 自愈先例），勿当升级回归、勿发探针确认；GLM Coding Plan 条款风险为 2026-09-26 用户风险接受决定（复核触发在账） |
| Claude 降智观测（2026-10-19 增补） | claude | 静默降档三通道：①限额回退 Opus→Sonnet（响应 model 字段≠请求名）；②2026-10 新报告的**安全分类器 mid-task 静默降档**（[claude-code#103382](https://github.com/anthropics/claude-code/issues/103382) 族，运维/系统管理类会话误伤率高）；③近周限时 prompt cache TTL 降至 5min（[#102883](https://github.com/anthropics/claude-code/issues/102883)）。均仅观测（doctor substitution 计数 + 客户端 model 字段与 cache 行为），不建新门禁、不据此停流 |

## 分级处置

### L1 线程级错误（thinking_signature_invalid / encrypted_content item_id mismatch）

另有不同的请求格式问题：Codex `agent_message.content` 携带非空
`encrypted_content` 时，跨协议的 Claude/GLM/Gemini 路由不能据此恢复任务正文。
Admission 对此返回 `422 / encrypted_agent_payload_unsupported`，在获取 lane
lease 和发送上游请求前停止；不设置 Retry-After、不推进熔断器，也不重写密文。
这不等于模型整体不可用：普通消息、明文委派和 reasoning signature 不受此检查影响，
GPT 原生请求保留原路径。遇到该错误应检查宿主委派格式支持，禁止重发或轮换模型规避。
不能把“把密文字段改成 text”称为解密，也不能只删除加密块后以任务头代替真实正文。
本地测试通过或文件投影并不证明运行中 admission 已加载，部署仍须维护窗口和加载核验。

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

## claude-oauth lane 信号形态与急停（2026-10-17 lane 落地起）

claude 订阅 OAuth lane 在役后，本页分级对本 lane 适用。判读入口仍是 strict
doctor（`==claude-lane==` 段）+ error dumps；条款与资格边界见
[cpa-claude-account-risk.md](cpa-claude-account-risk.md)。

| 信号形态 | 判读 | 处置 |
|---|---|---|
| 数据面持续 4xx，dumps 文案含 "This credential is only authorized for use with Claude Code" | **凭据级执法**（订阅 OAuth 被限制用于第三方客户端，2026-02 起有执法先例），不是普通配额 | 立即急停 b（见下），官方界面核对账号状态并走官方申诉；事件窗口不重试、不换号、不清冷却 |
| 授权页/回调 `account_on_hold` | 账号受限（多为登录态指向了受限账号） | 按 attach runbook 核对登录邮箱后重走流程，不重试本次回调 |
| `Anthropic-Ratelimit-Unified-5h/7d-Status: rejected` | 真配额窗（5h/周窗，claude.ai/Desktop/Code 全产品共享池），不是风控事件 | 尊重完整 reset 窗口；缺少可信期限则人工复核停流；与官方桌面/网页重活错峰；不硬打 |
| `invalid_grant` / 刷新持续失败 | 先核 provider 归属：doctor 只将带 provider 标签的容器刷新失败归给对应通道；无归属日志与混合凭据 dumps 单列 unknown，元数据正常不证明账号可用 | 人工确认对应凭据后按官方支持流程恢复；[OAuth 失效恢复](cpa-oauth-failure-recovery.md) 的 device-login 命令仅适用 Codex |
| doctor `claude_lane=EXCLUSION_DRIFT` / `BLOCKED_DECLARED_ALIAS` | 目录契约漂移（排除表与 manifest 墓碑不一致，或认领名被墓碑阻断） | 恢复走受审查 apply 重投影；不手工改 config 续命 |

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

每次运行会输出一行 `PROBE_BUDGET`，给出 `models=`、`cases_per_model=`、
`planned_generation_requests=` 与 `oauth_lane=excluded:<原因>`，用来在执行前
确认这次运行的计划请求量。`readiness` 和 `generation`（单目标
`glm-5.3-flash`）路径本来就不发 OAuth 请求，不受该开关影响。

**注意**：探针开关只约束探针。静默期要挡住外部消费者必须用
`-QuarantineOAuthLuna`。

---

## 已知限制（2026-09-26 深度审查）

- **上游 `Retry-After` 已由 CPA 前的 lane admission 有界尊重**：CPA 自身的
  `transient-error-cooldown-seconds` 仍固定 60s，doctor 也只把上游
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
  `cpa-admission.service` 现为五条 lane（2026-10-19 更新；deepseek-official 已
  2026-10-11 退役）：ChatGPT OAuth `max_inflight=2`、GLM Coding Plan 3、
  Antigravity OAuth 2、Claude OAuth 2、WorkBuddy OAuth 1（禁突发工程化）；
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

### 2026-10-04 追加：两条**接受项**（不要当待修项）

审计工具 `scripts/cpa_admission_risk_audit.py` 会把下面两条报成 warn；它们记录的是
**已经做过的取舍**，不是缺陷：

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
| 封号·并发 | 共享账号 lane 有界并发（2026-10-19 现值：chatgpt 2 / zhipu 3 / antigravity 2 / claude 2 / workbuddy 1；deepseek 已 10-11 退役） | `cpa-admission.json` `lanes[].max_inflight` | 审计 `lane-*`；常量与 JSON 强等（`cpa-admission.py:62`） | ✅ |
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

## 禁止

2026-10-19 用户明确授权现有账号池故障切换：WorkBuddy 的可识别
11128 请求拒绝会临时冷却失败凭据 5 分钟，由 credits 调度选择其他可用账号。
这是本地熔断窗口，不是上游承诺的恢复时间。明确封禁、认证失败、额度耗尽
仍按对应事件处理，不以账号切换绕过人工 hold 或上游配额。
CPA 的 max-retry-credentials=0 取消固定账号数量上限，request-retry=0 禁止
额外重试轮次；同一请求排除已尝试凭据，成功即停止。流已输出后不重放。
所有候选冷却或耗尽时插件明确拒绝，不能回落到已冷却凭据。

- 不做对抗性规避（state 注入、UA/cloaking 调整、identity-confuse、第二账号
  轮换）——全部已裁定不采纳。
- 不把 `-DeactivateOAuthLuna` 当作"重置风控"的常规手段：它不可逆地删除全部
  可刷新 OAuth 物料。
- 不在事件窗口调整 nginx 并发/限流参数来"吸收"风控信号：入口阈值不是
  provider 配额，历史上（2026-09-13）已因此误伤独立通道。

## 边界

本页只覆盖 CPA 网关侧的账号风险处置。VPS 系统层（IP 信誉、端口扫描、
fail2ban 误封）不在此页：fail2ban 解封走远端 `fail2ban-client`，不自动化。
Anthropic/Claude 的条款、地区与凭据边界见
[cpa-claude-account-risk.md](cpa-claude-account-risk.md)：claude-oauth lane 自
2026-10-17 起在役（三名认领 + 16 墓碑排除），本页分级与 claude 信号形态表对其
适用；Antigravity OAuth lane 同样在役（`==antigravity-lane==` 门禁）。
