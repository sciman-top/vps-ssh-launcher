# Claude / Anthropic 接入边界（bwg CPA）

本页维护 **CPA 部署决策与验证边界**。通用的注册/登录资格、Windows、浏览器、
指纹、Claude Code / Desktop 和社区症状说明统一维护在相邻检出的
[claude-antiban RUNBOOK](../../../claude-antiban/RUNBOOK.md)。
该链接需要本地相邻项目，公开仓库或单独检出时可能不可用；
本页保留执行 CPA 决策必需的约束与官方链接，不依赖相邻项目执行运行检查。

网关运维见 [cpa-gateway.md](cpa-gateway.md)，异常分级见
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)，
凭据生命周期见 [cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)。

## 本部署决策

> **2026-10-20 当前状态**：Claude subscription OAuth 已在账号 `account_on_hold` 事件后退役；当前路由不再供给 Claude OAuth，凭据数为 0。下文 2026-10-17 的开放决定和验收结果仅为历史记录，不能作为当前可用性依据。恢复须另行取得用户授权，并按账号恢复信号及现行 runbook 执行。

> **2026-10-17 历史运行决定**：用户明确要求必须重开 Claude subscription OAuth；BWG CPA
> 路由按此决定保持开放。当天 strict doctor 已读到 `claude_lane=OK`、
> `semantic-policy=OK`，目录含 `claude-haiku-5-5`、`claude-opus-5-5`、
> `claude-sonnet-5-5`。这证明检查时的路由和目录状态，不证明 Anthropic 已授权此
> CPA 用途、账号不会被限流/停用，或模型质量已验收。接入按
> [cpa-claude-oauth-attach.md](cpa-claude-oauth-attach.md) 执行；单人单凭据、
> 无公共 key、无转售、无账号探针矩阵。本次运行决定不等于 Anthropic 授权；Usage
> Policy 的版本和生效时间按下文记录，不把尚未生效的条款表述成当前有效规则。
> 决策历史：87fb574 开（带风控预埋）→ 1e56b44 关 → 本版按用户要求重开。
> **2026-10-19 用户决策**：现有全部 OAuth 账号与 API key 渠道保留（含本 lane 与
> Antigravity/WorkBuddy/ChatGPT lane 及 GLM、ai.input.im 等渠道），以风险接受姿态
> 进入 2026-11-12 新版 Usage Policy 生效日；当日复核 Anthropic AUP 页确认新版
> 生效标注与措辞同 2026-10-17 记录一致（原文见下）。执法画像未见恶化（2026-10
> 一手渠道零新增订阅 OAuth 封号案例，历史案例均为多账号/共享/虚拟卡形态）。
> Antigravity OAuth lane 同日经用户决定**维持在役**（单人单凭据、低频手动使用，
> 同 ChatGPT lane 风险接受类；2026-10-17 凌晨 1e56b44 的隔离注记从未在远端
> 执行，本文档为准）。Google Antigravity Terms 的第三方访问禁止条款与暂停/
> 终止警告作为**已接受风险挂账**，不做任何工程规避，条款锚点见下。

1. **BWG CPA 承载 Claude Pro/Max 订阅 OAuth lane（按用户决定保持开放）。** 订阅凭据
   经 CPA 中转供本人客户端使用；不代持他人凭据、不转售、不配置公共 key。
   `claude-header-defaults.stabilize-device-profile: true` 让真实 Claude Code 客户端
   的设备画像随凭据保持稳定。本部署没有额外自定义 UA/OS/timezone，但这不等于
   CPA 完全透明转发：v8.0.31 默认 `auto` cloaking 会处理未被识别为原生 Claude Code
   的 OAuth 请求。2026-10-18 用户决定保留现有客户端兼容性和该上游默认行为；
   这既不是服务方授权，也不是防封证明。不得另加身份、设备或地区规避。
   API-key 通道是不同认证路径，不能据此推断订阅 OAuth 获准或账号安全。
2. **按凭据路径区分 Claude 模型**：
   `claude-opus-4-6-thinking`、`claude-sonnet-4-6` 仍是
   `antigravity_oauth_exclusions` / `codex_api_key_exclusions` 中的墓碑。
   曾有 `claude_api_key_routes`（opencode.ai zen 第三方 API key 服务
   `claude-haiku-5-5`）于 2026-10-17 随 Claude 订阅 OAuth lane 落地而退役——
   同名 haiku 改由订阅凭据直接供给，第三方渠道不再经手 Claude 模型。该路由
   曾钉固定网关 `User-Agent`（2026-10-16 Cloudflare 1010 可用性处置），退役后
   该处置一并失效；它从来不是指纹保护或防封手段。第三方渠道不构成地区资格或
   授权证明的结论对任何未来同类路由继续适用。
3. 新的程序化 Claude 接入需求须先核实地区/主体资格及官方 API 或授权云通道的条件，
   明确消费者、凭据用途和成本，再按
   [目录变更清单](cpa-catalog-change-checklist.md) 审查 lane。
   API 和云通道不自动替代实际使用资格。
4. CPA 代码、部署配置、准入与远端运维继续在本仓维护，
   不复制到 claude-antiban；后者只维护通用说明与本地诊断工具。

当前 `-QuarantineClaudeOAuth` / `-RestoreClaudeOAuth` 是停止/恢复 CPA 路由的可逆
控制，保留登录凭据。当前运行决定是保持路由开放；这些命令只改变本地 CPA 路由状态，
不能撤销官方 OAuth 授权，也不能把第三方中转变成 Anthropic 认可的接入方式或证明账号
安全。既有单人单凭据、低频手动使用、排队与退避、不自动重试的控制用于减少本地并发和
重复请求；它们不规避服务方政策，也不保证免封、免限流或维持模型质量。出现账号限制、
凭据级拒绝或持续异常时，按事故 runbook 急停并走官方支持渠道，不换号、伪装身份或清除
冷却后续试。

lane 开关权属用户（2026-10-17 固化，同日曾发生 agent 会话仅凭条款评估单方面隔离）：
`-QuarantineClaudeOAuth` 必须携带 `-QuarantineUserDirective`（用户当次指令原文，进
事务输出留痕）才会执行；agent 会话不得仅凭条款或政策风险评估预防性关闭本 lane，仅在
出现上述凭据级执法/账号受限信号时急停并事后报备。`-RestoreClaudeOAuth` 不设指令
门槛（恢复是安全方向）。luna/antigravity 开关暂不加门槛：luna 是已定案的事件急停
工具，antigravity 仅有文档层误动先例，按观察→强制升级路径处理。

## 必需的官方约束

- [Supported Regions Policy](https://www.anthropic.com/legal/supported-regions)
  在线页面在 2026-10-17 复核时显示 **Effective November 12, 2026**，尚未生效，
  不把该版新增措辞当成当前规则。资格仍须结合已生效条款和实际主体核实；
  出口 IP 或代理不改变资格。
- [Usage Policy](https://www.anthropic.com/legal/aup) 在 2026-10-17 在线复核时显示
  **Effective November 12, 2026**，该版尚未生效。其[上一版本](https://www.anthropic.com/legal/archive/22742366-2ef0-4c7a-a833-6523f10d3944)
  标注 **Effective September 15, 2025**；截至本次复核日期，这是官网提供的已生效
  版本。该旧版已禁止通过其他账号规避封禁，以及违反 Supported Regions Policy
  提供 Claude 账号/API 访问，并保留节流、暂停或终止访问的措施。新版本增加了对
  未经授权转售、代理或以其他方式提供 Claude 访问的明确限制，并举例包括经
  consumer subscription 路由请求的服务；归档旧版没有这组明确措辞。
  2026-10-19 二次在线复核一致，新版措辞原文："Resell, proxy, or otherwise
  provide access to Claude through unauthorized means, including services
  that route requests through consumer subscriptions"。旧版措辞差异
  不代表本 CPA 用途获准，仍须按主体、凭据与实际用途判断授权。
  [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) 要求遵守
  Supported Regions Policy，并保留暂停或终止访问的权利。该政策文本构成本部署的
  明确风险提示；本 runbook 和技术配置无法证明此 CPA 用途已获 Anthropic 授权。网络
  稳定、固定出口、HTTP 200、目录可见或限流控制均不构成资格、许可或防封证明。
- [Subscriptions and gateways](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)
  明确区分凭据来源：仅设置 ANTHROPIC_BASE_URL 不替换官方 Code 已保存的订阅认证；
  网关凭据或 apiKeyHelper 生效后使用该凭据，订阅额度不适用。
  官方 Code 原有请求经网关转发，与 CPA 代持订阅令牌供其他客户端使用须分别判断；
  文档说明 gateway 的技术配置及 OAuth capability 转发，但不能只凭技术兼容或
  BASE_URL 非空判定此 CPA 用途已获服务方许可。
- [Claude Code 合规说明](https://code.claude.com/docs/en/legal-and-compliance)
  对向终端用户提供 Claude Code 的开发者限制代付、转售或代中转 Claude 使用，
  并要求终端用户使用自己的适用凭据；不要把这条规则泛化为任何第三方 API
  服务都必然禁止，也不要把它反向解释为某个第三方路由已获授权。
- [Google Antigravity Terms](https://antigravity.google/terms) 明确规定，通过第三方软件、
  工具或服务访问 Antigravity（条款以 Antigravity OAuth 为例）违反协议，可能导致
  Antigravity 和/或 Gemini 服务暂停或终止。此条款已于 2026-10-17 在线复核；本仓
  不通过 UA、设备画像、出口 IP 或客户端轮换绕开该边界。
- [Google Antigravity FAQ](https://antigravity.google/docs/faq/) 单列第三方软件与 headless/
  自动化访问问题；FAQ/Terms 不为 CPA 转发授权提供例外。
- [Claude Code 权限模式](https://code.claude.com/docs/en/permission-modes)
  说明 Auto mode 使用分类器审查动作；若服务端不复核，Claude Code 会回退到自己的
  分类器请求，而网关丢弃复核请求或结果是官方列出的常见原因。该文档没有定义本仓
  请求过滤器中 `safeguards` 与服务端复核协议的逐字段关系。
- 第三方网关不被 Anthropic 认可、维护或审计；不把“工具能调用”解释成条款允许。
  具体用途仍须按当前官方文件、渠道授权和服务方条款判断。

Claude 地区/网关政策于 2026-10-17 在线复核；Antigravity Terms/FAQ 于
2026-10-17 在线复核。出现政策变化或疑似风控信号时，重新核对对应原文；
社区个案不代替官方条款，不推导指纹、IP、客户端或频率的封号阈值。
通用社区样本及证据限制见相邻项目 RUNBOOK。

## CPA 代理与源码核对

CPA 不以环境代理作为受支持配置入口；模型请求与管理查询的选择规则见
[CPA 出站代理与管理查询](cpa-gateway.md#cpa-出站代理与管理查询)。
本机浏览器、CLI 或 Desktop 的代理设置不自动配置容器内 CPA。

外置源码路径与 revision 以 `D:/CODE/external/_shared/references.manifest.json`
中 `logicalName=CLIProxyAPI` 条目的 `consumerPaths` 为准。2026-10-16 清单登记的
检出位于 vps-ssh-launcher reference checkout 下；旧文档误写成 `external` 的直接
子目录，不能据此定位源码。跨机器时重新读 manifest，不硬编码本机路径。
先核对 manifest、检出 HEAD 与当前运行版本；参考仓可能落后于运行镜像。
仅当本地已有对应对象时，用 `git -C <checkout> show <tag>:<path>` 读取该版本；
对象缺失应报告版本证据缺口，不能用旧版源码替代运行版证明。
不 fetch/checkout，不假设工作树就是运行版本，也不因有新上游版本自动更新远端。

## 可执行判据

仓库侧（只读，按凭据类型确认当前路由清单）：

```bash
rg -n "claude" scripts/remote/cpa_provider_routes.json
# claude 订阅 OAuth lane 落地前，policy 要求 claude-* 全量排除（fail-close）；
# 落地后 oauth_routes 声明 claude-oauth，未认领名仍留排除墓碑。
# 旧 claude-haiku-5-5 API-key 通道已退役；当前同名模型由 Claude OAuth lane 供给。
```

远端侧（只读 strict doctor；使用本机私有连接配置）：

```powershell
$env:VPS_SSH_LAUNCHER_RUN_INTEGRATION = "1"
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg `
  -Config "$env:APPDATA\vps-ssh-launcher\target.json"
# 真实 BWG 的结果必须与当前授权的 manifest / 投影源逐项比对；
# 显式指定私有配置，避免因默认配置路径不匹配而误生成模板。
```

`cpa_policy.py` 拒绝未审查的共享 OAuth 路由，并逐路径校验 Claude OAuth 排除表和
客户端画像默认值；静态 Antigravity 模型快照要求每个 ID 都被明确路由或排除。
这些检查不扫描或删除 auth 目录凭据，也不证明第三方 OAuth 使用获得许可。运行态须
另以 BWG strict doctor 确认 Claude 排除表状态（落地前 `claude-*`、落地后显式
墓碑名单）、Antigravity quarantine marker、模型目录和服务状态；目录消失或
doctor 通过都不是账号安全证明。

## 事故与验收边界

- **Claude Code Auto mode 在当前订阅 OAuth 路由上未验收。** 旧 zen API-key
  路由曾删除 `safeguards`、`context_management` 与消息级 `output_config`；该通道及其
  payload 钉死规则现已退役，不再把旧生成探针的结果套到当前订阅 lane。
  需要权限确认的工作保持 Manual（配置值 `default`）和逐项用户确认；不得把普通
  消息生成成功表述为 Auto mode 服务端复核或回退分类器闭环验收。
- Claude 订阅 OAuth lane 为单人单凭据，不建公共 key、不做真实账号探针矩阵；
  Antigravity OAuth lane 在役，同样不发生成请求或额度探针。
- 不用 HTTP 200、固定出口、一次成功或网关健康状态推断账号安全。
- 不在事件窗口用换号、清冷却、复用旧凭据等方式恢复服务。
- 凭据、device-login 日志、授权 URL、session token 不入 Git、receipt 或聊天。
- 文档及仓库检查仅为 repo_verified；远端投影、host_loaded 和 live_accepted
  分别需要独立证据。受控隔离不代表账号解封、限流豁免或自然使用验收。

## Claude 持久化停流与上游退避头缺口

`claude-oauth` 与 Antigravity 共用严格退避实现，使用独立的
`/var/lib/cpa-admission/claude-risk.json`（version 1，0600，nobody；父目录 0700）。
服务以 `--claude-state-file` 显式加载；缺失、损坏或两 provider 指向同一路径时拒绝
启动。状态先写后发，崩溃留下 `unverified_request`；写失败、401/403 或错误
协议中的明确凭据限制进入人工复核，另一在途成功不能清除，重启也不清除。
普通 403 仅表示认证/权限待查，不自动宣称封号。

Claude HTTP 429 的等待期从 `Retry-After`（秒/HTTP-date）、`Retry-After-Ms` 与
`Anthropic-Ratelimit-Unified-*-Status: rejected` 对应的 UNIX reset 时间取最长值，
不截短超过一天的期限。529/503 按过载等待完整阶梯，无提前半开；到期只由一个真实
来流请求验证，不主动发生成探针。没有可信期限的 HTTP 429 或协议级
`rate_limit_error` 进入 `retry_window_review_required`，后续本地 403 且不带
自动重试提示。有效响应头仍会转发，Claude 错误等待期统一为秒级 `Retry-After`。

**已知上游边界**：CPA v8.0.31 的 Claude 错误对象可能丢失原始上游退避头
（[PR #6700](https://github.com/router-for-me/CLIProxyAPI/pull/6700)）。admission
只能处理收到的头，不能重建丢失的官方期限；上述人工停流是保守保护，不等于头已
修复。后续只有稳定版本包含修复、经模拟 429/529 的端到端头传递验证后，才重审
该缺口；不因 PR 存在直接安装候选版本，也不删除“未知窗口需复核”的保护。

恢复需先停止该账号调用方，核对官方限制与实际 reset；备份状态，按
[cpa-gateway.md](cpa-gateway.md#oauth-持久化风险状态的人工恢复) 恢复。
`-RestoreClaudeOAuth` 仅恢复目录排除表，不能清除风险锁；不执行通用额度重置
探针绕过本 lane 的复核要求。

调用方仍须独立限制重试；`request-retry=0` 不控制 Cockpit、SDK、任务恢复器。
建议订阅账号只运行一个活跃重任务，与官方 Code/Desktop/网页错峰。固定出口、画像
一致性和这些本地并发上限均非官方安全阈值，也不提供防封保证。

## 请求模型与监控边界（2026-10-18）

- 三个 Claude OAuth 目录模型均在最终 Claude payload 上过滤 `fallbacks`，包括客户端
  自带项和上游默认 cloaking 为 Opus 添加的旧型号项。请求失败应原样暴露，不能靠
  改用旧型号维持成功率；其余默认兼容处理保留。依据是 CLIProxyAPI v8.0.31
  `355fd10e76b82e03ba6625acc583b900286e4bf2`（MIT）的
  `claude_executor_cloaking.go` 与 execute/stream 中后置的 `ApplyPayloadConfigWithTrackedPaths`。
- 此过滤只能禁止该请求字段触发的 fallback，不能证明上游实际权重、模型质量或
  服务端内部调度。模型名差异应结合完整错误类型、thinking/effort、上下文和固定样本
  判断；不凭一次输出或 HTTP 200 判定降智，也不安排定时生成探针消耗 OAuth 额度。
- Claude 的 JSON/SSE 错误即使 HTTP 200，也按有效 `Retry-After`、毫秒等待值和
  `rejected` 窗口 reset 的最长值退避；未知截止时间保留人工检查停流，不自动试号。
- doctor 的 Claude/Antigravity 监控读取凭据元数据与有 provider 标签的容器刷新
  失败计数；无归属日志和混合凭据 dumps 单列 unknown。日志扫描仅覆盖保留尾部
  20000 行，不能保证完整 7 天。access token 过期提示需要刷新，不直接等同于
  会话失效或封号；元数据正常也不证明刷新或生成已被服务方接受。历史字段
  `oauth_monitor` 专指 Codex，另读各 provider 的 monitor 与 coverage 字段。
