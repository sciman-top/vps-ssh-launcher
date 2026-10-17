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

> **2026-10-17 当前决定（当日二次翻转）**：用户订阅 Pro 后决定重开接入——BWG CPA
> 可登录、承载 **Claude Pro/Max** 订阅 OAuth（**Free 不在内：Claude Code 授权
> 要求订阅，免费账号被官方授权页直接拒绝，2026-10-17 实证**）。接入按
> [cpa-claude-oauth-attach.md](cpa-claude-oauth-attach.md) 执行：lane 落地前
> `oauth.excluded-models.claude: ["claude-*"]` 保持 fail-close；单人单凭据、
> 无公共 key、无账号探针矩阵。决策历史：87fb574 开（带风控预埋）→ 1e56b44 关
> → 本版重开；下方条款锚点全程有效。
> Antigravity OAuth lane 同日经用户决定**维持在役**（单人单凭据、低频手动使用，
> 同 ChatGPT lane 风险接受类；2026-10-17 凌晨 1e56b44 的隔离注记从未在远端
> 执行，本文档为准）。Google Antigravity Terms 的第三方访问禁止条款与暂停/
> 终止警告作为**已接受风险挂账**，不做任何工程规避，条款锚点见下。

1. **BWG CPA 承载 Claude Pro/Max 订阅 OAuth lane（fail-close 前置）。** 订阅凭据
   经 CPA 中转供本人客户端使用属用户风险接受决定（同 ChatGPT/Antigravity 模式）；
   不代持他人凭据、不转售。lane 落地前 `oauth.excluded-models.claude: ["claude-*"]`
   维持目录压制；`claude-header-defaults.stabilize-device-profile: true` 让真实
   Claude Code 客户端画像按凭据持久透传（KV 7 天 TTL），刻意不设 UA/OS/timezone
   覆盖（防泄漏、不伪装）。API-key 通道是不同认证路径，不能据此推断订阅 OAuth
   获准或账号安全。
2. **按凭据路径区分 Claude 模型**：
   `claude-opus-4-6-thinking`、`claude-sonnet-4-6` 仍是
   `antigravity_oauth_exclusions` / `codex_api_key_exclusions` 中的墓碑。
   当前清单另有 `claude_api_key_routes`，只声明
   `claude-haiku-5-5`，使用第三方 API key；它不是
   Anthropic Free/Pro/Max 订阅 OAuth。该 API-key 路由不构成地区资格或授权证明，
   也不能据此推断消费者账号安全。官方地区政策覆盖通过云平台和授权经销商提供的
   Claude 模型；本仓未证明该第三方渠道在相关地区/用途下属于授权渠道。
   当前路由钉有固定网关 `User-Agent`；这是 2026-10-16 记录的 Cloudflare 1010
   下游 UA 拒绝与共享凭据冷却问题的可用性处置，会替换该路由发往上游的下游 UA。
   它不是浏览器/设备指纹保护、账号防封手段或注册限制的绕过依据。若适用条款或
   服务方未授权此用法，应停止该路由，不得轮换客户端指纹继续试探。
3. 新的程序化 Claude 接入需求须先核实地区/主体资格及官方 API 或授权云通道的条件，
   明确消费者、凭据用途和成本，再按
   [目录变更清单](cpa-catalog-change-checklist.md) 审查 lane。
   API 和云通道不自动替代实际使用资格。
4. CPA 代码、部署配置、准入与远端运维继续在本仓维护，
   不复制到 claude-antiban；后者只维护通用说明与本地诊断工具。

## 必需的官方约束

- [Supported Regions Policy](https://www.anthropic.com/legal/supported-regions)
  按实际所在地、实体注册地/总部和控制关系判断资格；出口 IP 或代理不改变资格。
- [Usage Policy](https://www.anthropic.com/legal/aup) 禁止违反地区政策访问、绕过
  注册限制或通过换号规避封禁；[Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
  要求遵守 Supported Regions Policy，并保留暂停或终止访问的权利。网络稳定、
  固定出口、HTTP 200 或限流均不构成资格或防封证明。
- [Subscriptions and gateways](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)
  明确区分凭据来源：仅设置 ANTHROPIC_BASE_URL 不替换官方 Code 已保存的订阅认证；
  网关凭据或 apiKeyHelper 生效后使用该凭据，订阅额度不适用。
  官方 Code 原有请求经网关转发，与 CPA 代持订阅令牌供其他客户端使用须分别判断；
  不能只凭 BASE_URL 非空判定共享或认可。
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

Claude 地区/网关政策的上次逐项核对为 2026-10-16；Antigravity Terms/FAQ 于
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
# 现有 claude-haiku-5-5 API-key 通道是独立认证路径，不构成订阅 OAuth 准许。
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

- **Claude Code Auto mode 在当前 CPA Claude 路由上未验收。** `cpa_policy.py` 为兼容
  第三方上游，从 `claude-haiku-5-5` 请求中删除 `safeguards`、
  `context_management` 与消息级 `output_config`；代码注释将 `safeguards` 说明为
  权限模式分类器上下文。`cpa-health.py` 只验证过滤后消息生成可用，不覆盖 Auto mode
  的服务端复核或客户端回退分类器闭环。现有证据既不能证明删除 `safeguards` 会关闭
  官方分类器，也不能证明此 CPA 路由保留了相同的权限审查语义。不得把该生成探针、
  HTTP 成功或旧的客户端请求验收表述为 Auto mode 安全验收；资格与渠道授权另行成立时，
  需要权限提示的工作应使用 Manual（配置值 `default`），并保持用户逐项确认。
- Claude 订阅 OAuth lane 为单人单凭据，不建公共 key、不做真实账号探针矩阵；
  Antigravity OAuth lane 在役，同样不发生成请求或额度探针。
- 不用 HTTP 200、固定出口、一次成功或网关健康状态推断账号安全。
- 不在事件窗口用换号、清冷却、复用旧凭据等方式恢复服务。
- 凭据、device-login 日志、授权 URL、session token 不入 Git、receipt 或聊天。
- 文档及仓库检查仅为 repo_verified；远端投影、host_loaded 和 live_accepted
  分别需要独立证据。受控隔离不代表账号解封、限流豁免或自然使用验收。
