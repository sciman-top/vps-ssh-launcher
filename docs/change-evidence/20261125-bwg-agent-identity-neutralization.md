# BWG 编码代理身份句中性化（多车道，2026-11-25）

> 时间均为 UTC，本机日期 2026-11-25。生产 fork 代 `v8.1.2-7966fix` 不变。
> 本记录取代 [同日上一份](20261125-bwg-antigravity-handoff-identity-swap.md) 里
> “user 文本套用 systemInstruction 同一张整词替换表”的做法。

## 指令与范围

用户 2026-11-24 指令：把 WorkBuddy、Claude/Anthropic、ZCode/glm、DeepSeek Harness 等
客户端的身份句也纳入清洗，加键并逐客户端验证。现有 OAuth 账号与 API key 渠道全部
保留；本次没有退役任何模型、账号或渠道。

## 摸底

- Antigravity 是唯一有实证内容过滤的上游（#7553）。WorkBuddy 上游 72 小时 106 发
  全过，并不拒绝带 Codex 身份句的提示；ZCode 起步套餐车道 72 小时零流量。
- 上游对 `systemInstruction` 的字面替换是整词的；ZCode 的系统提示里有几十个
  `.zcode/skills` 路径，整词键会把路径改坏。所以新增规则一律只匹配身份句或
  产品短语。
- Claude/Anthropic 没有在役上游（订阅 OAuth 车道已退役，Antigravity 侧的
  `claude-*` 零注册）；本次覆盖的是 Claude Code 这个客户端的身份句。

## 仓库改动

- `d690242` — `fix(cpa): neutralize coding-agent identity sentences across subscription lanes`
  - `scripts/remote/cpa-admission.py`：`neutralize_agent_identity_text()` 取代上一代的
    `sanitize_antigravity_identity_user_text()`。改写的是身份句本身：Claude Code 整句、
    `DeepSeek Harness` 短语、ZCode / CodeBuddy / WorkBuddy 身份句、Codex 身份句。
    作用面按车道：

    | 车道 | 角色 |
    |---|---|
    | `antigravity-oauth` | developer、user |
    | `workbuddy-oauth`、`workbuddy-intl-oauth` | system、developer、user |
    | `zcode-start-plan` | system、developer、user |

    兼容 Responses `input`、chat-completions `messages` 与 Anthropic `system` /
    `messages` 三种形状。ChatGPT 车道不处理：Codex 的身份句在那里是官方客户端
    自身的形状。工具输出、assistant 轮次与裸产品名一律保留。
  - `scripts/remote/cpa-guardrail-apply.sh`、`cpa_policy.py`：Antigravity
    `systemInstruction` 的有序 RE2 `sensitive-patterns-replace` 三条规则，policy
    钉死顺序与替换文本。
  - 测试：纯函数、agy 车道、WorkBuddy 与起步套餐车道、policy 钉值。
- `03064f4` — `fix(cpa): log one identity-neutralization record per request, named for its lane`
  - 见下文“部署后发现的缺陷”。

## 上游正则的离线验证

规则最终由 Go RE2 编译执行，与 Python 正则不等价。部署前在 WSL 用
`/usr/local/go/bin/go` 按上游顺序（模式规则先、词表后）实测：

| 输入 | 输出 |
|---|---|
| `You are Claude Code, Anthropic's official CLI for Claude. You help …` | `You are a coding assistant. You help …` |
| 同上，弯撇号 | 同上 |
| `You are DeepSeek Harness … model deepseek-v4.1-flash` | `You are Assistant Framework … model deepseek-v4.1-flash` |
| `You are ZCode … C:\Users\u\.zcode\skills\a\SKILL.md` | 身份句改写，路径原样 |
| `You are Codex, a coding agent based on GPT-5.` | `You are agentx, a coding agent based on agentx-5.`（既有词表） |

部署后运行态核对：管理视图顶层 `antigravity.sensitive-patterns-replace` 为 3 条，
容器启动以来零条 `rule dropped: invalid regex`。

## 受控验证（只发预期 200 的正向单发）

用户要求逐客户端二分验证。二分需要一个会失败的基线，而在役账号上制造失败就是
制造一次 WAF 命中，所以顺序是先发清洗后的提示；只要全部通过，就没有可二分的对象。
本次没有出现失败，因此没有做二分，全程零 429。

| 组 | 路径 | 探针 | 结果 |
|---|---|---|---|
| agy 系统指令 | 直连 8317（考察 CPA 规则） | Claude Code | `200` / completed |
| | | DeepSeek Harness | `200` / completed |
| | | ZCode（含 `.zcode` 路径） | `200` / completed |
| | | CodeBuddy + WorkBuddy | `200` / completed |
| agy 历史 | 经 8318 | 11-22 卡死 fork 会话 88 项历史 | `200` / completed，`usage_in=67233` |
| WorkBuddy | 经 8318 | 系统提示含全部客户端身份句 | `200` |

journal 侧对应：历史回放依次命中 `developer_context`、`retry_history
duplicate_user_turns=13`、`antigravity_identity_user_text developer_parts=1
handoff_parts=1`；WorkBuddy 那发命中 `agent_identity_neutralized system_parts=1
user_parts=1`。卡死历史在 16:03Z（上一代、整词替换之外无处理）曾返回裸 429，
这次经“只改身份句、保留裸产品名与路径”的处理后通过。

对照来自 #7553：未清洗的 Claude Code 身份句 0.85 秒即 429。本次没有发未清洗的
对照，所以“规则生效”依据的是运行态核对、RE2 实测与上述通过结果，不是同请求的
开关 A/B。

## 线级抓包（2026-11-25 04:30Z 前后）

用户追问“系统提示是否彻底清理干净”。探针只能证明上游返回 200，看不到发出去了
什么，所以用同一个 fork 镜像做了隔离抓包：独立的临时容器（host 网络，API 只绑
`127.0.0.1:8390`）、最小配置、一个假凭据（假 token、假项目号、`base_url` 指向
`127.0.0.1:8391`，只从线上凭据复制非机密的模型名单），宿主回环上的监听器记录
二进制实际发出的请求体。零上游接触，跑完即清理；生产容器的启动时间与重启次数
不变。脚本：`outputs/cpa-wire-capture-20261125.py`。

| 样本 | systemInstruction | tools | contents |
|---|---|---|---|
| R1：11-22 卡死会话历史，先过线上 admission 管线 | 身份句 0、厂商词 0；51,854 字符，开头 `<app-context>` / `# agentx desktop context` | 无（重建样本不含工具） | user 3 项：身份句 0、厂商词 31；model 12 项：身份句 1、厂商词 43 |
| R2：含全部身份句、路径和一个工具，不经 admission | 身份句 0、厂商词 0；`You are agentx, a coding agent based on agentx-5. You are a coding assistant. You are Assistant Framework…` | 厂商词 4，原样 | user 1 项：身份句 1（CPA 不处理 user 角色） |
| R3：ZCode 形状 `/v1/messages`，不经 admission | 身份句 0；`You are a coding assistant, an interactive agent…`；裸 `ZCode` 11 处 | 裸 `ZCode` 1 处，原样 | user 1 项：裸 `ZCode` 1 处 |

出站信封与官方客户端一致：`user-agent: antigravity/hub/2.34.0 (aidev_client; …)`、
`requestId` 为 `agent/<uuid>/<时间戳>`，带 `requestType`、`labels`、`sessionId`。

**结论**：

- 身份句在 `systemInstruction` 里已全部清除（五个客户端）；agy 车道 user 角色里
  的身份句也已清除（R1 的 user 部分从 5 处降到 0）。
- **工具定义完全不清扫**。描述里的 Codex、OpenAI、GPT-5 与 `.codex` 路径原样发出，
  这是上游实现决定的（清洗只作用于 `systemInstruction`）。真实 Codex 桌面端请求的
  工具定义里带大量厂商词（11-18 测得 157 处），同日对照显示工具定义不触发 429；
  改工具名会破坏调用，所以有意不动。
- **对话历史里的裸产品名不清扫**。R1 的 user 文本仍有 31 处厂商词，model 轮次与
  工具输出有 43 处，其中一处身份句在工具输出里（一条 grep 结果）。实证上这些
  不触发。
- **`systemInstruction` 的整词替换是有损的**。`~/.codex/config.toml` 发出去时成了
  `~/.agentx/config.toml`，`# Codex desktop context` 成了 `# agentx desktop context`。
  这是 11-18 起的既有行为，前提是 `systemInstruction` 里的裸厂商词被证实会触发
  429；代价是 Gemini 经 agy 回答涉及这些路径的问题时会拿到错的名字。

**未做线级验证的**：真实 Codex 工具定义（带工具的桌面端转储在抓包前已轮转掉，
重建样本不含工具）；WorkBuddy 与 ZCode 起步套餐车道（走插件与 sidecar，隔离容器
里没有对应凭据）。

抓包过程中踩过的三处，复用时注意：假上游对 `fetchAvailableModels` 必须回
`{"models": {<模型名>: {"model": …}}}`，回空目录会让凭据不支持任何模型；临时
容器里的配置要写容器内路径（挂载点 `/w/auth`），不是宿主路径；非流式请求走
`generateContent`，路径匹配不能区分大小写。

## 部署后发现的缺陷

`d690242` 的调用点残留了旧代码块尾部的日志语句：每个被改写的请求记两条
`protocol_normalized`，WorkBuddy 与起步套餐的请求还会被错标成
`antigravity_identity_user_text`。请求体不受影响，但 journal 是这条归一化的归因面。
本地对同一请求实测：该版 2 条，修复后 1 条。`03064f4` 删除残留并加代理级测试
（每请求恰一条、按车道命名）。线上复核：agy 请求 1 条
`antigravity_identity_user_text`，WorkBuddy 请求 1 条 `agent_identity_neutralized`。

成因：重建工作树时替换调用点的结束标记选成了 `.join(` 的右括号。教训两条：
替换代码块用足够长的结束标记并核对替换后的上下文；日志类断言要有代理级用例。

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | Full 门禁 `-Profile Full -RunParallel` | `d690242`：`1887 passed, 30 skipped, 903 subtests`；`03064f4`：`1889 passed, 30 skipped, 903 subtests` |
| 部署预演 | `outputs/deploy-admission-identity-neutralize-20261125.ps1 -DryRun`、`…-logfix-20261125.ps1 -DryRun` | 均 `STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK` |
| `filesystem_projected` | 两次 admission 换代 | `ADMISSION_PIN_ROTATED 5170375a → 4badc426 → 3dd64909`，`ADMISSION_IDENTITY_NEUTRALIZE_FUNCTIONAL_OK`，`DEPLOY_OK` |
| `filesystem_projected` | `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` | 17 项 `PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、`READY_STATUS=200`、`GUARDRAILS_APPLIED` |
| `host_loaded` | strict doctor、`-AuditProjection` | `DOCTOR_CONTRACT_OK`、`admission-integrity=OK`、`POLICY_OK`、`CPA_PROJECTION_AUDIT=PASS` |
| `live_accepted` | 上表 6 发 + 日志修复后 2 发 | 全部 `200` |

## 回滚

- admission：`/opt/cliproxyapi/backups/20261124T225723.495852707Z-admission-identity-logfix-20261125`
  （回到 `4badc426`）与
  `/opt/cliproxyapi/backups/20261124T223207.265905079Z-admission-identity-neutralize-20261125`
  （回到 `5170375a`）。在同一维护事务内恢复备份里的 `cpa-admission.py`、
  `cpa_request_guard.py`、`cpa-admission.sha256`，重启 `cpa-admission`，再跑
  `cpa-admission-integrity-check` 与 strict doctor。
- 配置与 policy：`/root/cpa-guardrails-backup-20261124T224129.232097759Z`。

## 过程事故

2026-11-24 22:07:47Z，本工作树里这一轮未提交的四个文件被另一会话（Codex）写回
HEAD，改动丢失后按仓外底稿重建。此后改完先做本地提交再跑门禁。由此带出一个假绿：
工作树干净时 `-Profile Auto` 判定“无改动、无需门禁”并返回 PASS，门禁其实没跑；
提交后再验收必须显式 `-Profile Full`。

## 边界

- **起步套餐车道没有做线上验证**。探针时是工作日白天，不在免费窗；该车道只有
  单测与部署时的实弹断言覆盖，待免费窗或自然流量验收。
- Claude Code、DeepSeek Harness、WorkBuddy 客户端只用各自的身份句验证过，没有用
  它们的完整系统提示：这些客户端不经过本网关，完整提示拿不到。句子级触发因此
  不可穷举。
- Codex 客户端走 agy 的路径只由卡死历史回放覆盖；WorkBuddy 车道上的 Codex 身份句
  在改动前就被上游接受，清洗属于不披露客户端的预防措施，不是修复已知故障。
- 未纳入的社区已知触发：omp 的 `Read xd://<tool> for full docs + JSON schema before
  first use.`、OpenCode 的 `tools.opencode.session_move`、pi 的 harness 句、Hermes
  的四段未公开片段（#7553、#7562）。OpenCode 那条已有社区解法可循：strannick-ru
  用词表把提示里的工具名提及替换成另一个真实工具名（429→200），并自行标注代价
  "可能影响真正需要 session_move 的任务"；照搬前需单变量探针。

## 依据补充（2026-11-25 晚，回答"这两条判断有没有依据"）

社区与官方文档给两条遗留判断补齐了依据，并暴露一个我们规则的文档化缺口：

- **"工具定义不触发"有直接社区实证**：#7627 原文——同一短语留在 user 文本和工具
  描述里不会单独触发 429；#7560 受控测试——只删全部工具定义仍 429；#7608 维护者
  确认扫描面是 systemInstruction **和 user 消息**（未提工具）。
- **"改工具名会破坏调用"是结构性推断，非实测**：Gemini 官方文档把 `name` 定为
  回呼与分发键（"Unique function name"，官方示例按 `fc_step.name` 分发）；CPA
  翻译层对名字原样透传（源码+线级抓包）；历史 function_call 项带原名且 admission
  有意不碰工具项。社区实践一致：strannick-ru 改的是提示里的提及（不是定义），
  Hirokimeji（AstrBot）明说"保留真实工具名、只改句子其余部分"，omp 修复只改句子。
  没有任何已确认的修复改过工具定义名或历史项。
- **路径损坏无社区先例**：检索全部相关 issue（20 条），无人报告整词替换损坏
  路径；社区实践普遍接受子串级有损替换（#7627 的规则就是裸短语替换，strannick-ru
  是整短语替换）。我们的线级抓包观察（`~/.codex` → `~/.agentx`）是新的，没有
  现成最佳实践可循。
- **我们的 Claude Code 规则存在文档化缺口（休眠）**：#7904 二分出 6 条触发行——
  两条身份行（CLI 变体 + `You are a Claude agent, built on Anthropic's Claude
  Agent SDK.`）、安全测试政策行、多条 harness 样板行；跟评指出 Claude Code
  Desktop 的身份行以 ", running within the Claude Agent SDK." 结尾，只匹配到
  `Claude\.` 的规则会在后面留下仍触发的残段。#7627 另证第二段系统提示还有两条
  独立触发句（"You are an interactive agent that helps users with software
  engineering tasks." 整句触发；"Dual-use security tools … require clear
  authorization context." 截到句中即触发）。我们的规则只覆盖 CLI 身份行；因
  Claude 车道现无在役上游（agy 侧 `claude-*` 零注册），缺口休眠。将来 Claude
  Code 接入时按 #7904/#7627 的整行 pattern（`[^\n]*` 到行尾）扩展，不能沿用单句
  规则。
- **user 消息在扫描面内已获维护者确认**：#7608 的 A/B 表与 luispater 的回复直接
  证明把 systemInstruction 降级进 user 消息绕不开过滤器（该方案已关闭）——反证
  我们 admission 层的 user 文本身份句清洗是必要的。
- ChatGPT 车道与 zhipu 付费车道有意不处理。
- 清洗只针对内容过滤与客户端披露这两件事，不是封号的充分条件。
- 自然流量验收尚未兑现：日志修复换代后只有 1 发真实的 chatgpt 请求和 2 发复核探针
  经过新版 admission。
