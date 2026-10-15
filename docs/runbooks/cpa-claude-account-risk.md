# Claude / Anthropic 接入风险与控制边界（bwg CPA）

本页回答一个问题：**Claude（Anthropic）账号能不能接进 bwg CPA，接入前有哪些硬约束。**
它不重复 [cpa-gateway.md](cpa-gateway.md) 的网关运维细节，也不替代
[cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md) 的分级
处置口径；凭据生命周期见 [cpa-oauth-failure-recovery.md](cpa-oauth-failure-recovery.md)。

## 结论（本部署口径）

1. **bwg CPA 不承载 Claude 订阅 OAuth lane。** CLIProxyAPI 自身提供 `--claude-login`
   与 Claude 多账号负载均衡（`cmd/server/main.go:138`、README），但"工具能开"不等于
   "本仓采纳"：把 Free/Pro/Max 订阅凭据经网关转发给其他调用方，正是 Anthropic 条款
   点名的禁止行为（见下节）。本仓不为其建 lane、不建 admission 门、不发公共 key。
2. **Claude 模型名在本仓只以墓碑存在**：`claude-opus-4-6-thinking` 与
   `claude-sonnet-4-6` 是 Antigravity 目录里被退役并钉进排除表的 ID
   （`antigravity_oauth_exclusions` 与 `codex_api_key_exclusions`），不得复活为可路由名。
3. **符合地区与主体资格、确需程序化接入时走官方 API key 或授权云通道**（Claude Console API key、
   Amazon Bedrock、Google Cloud Agent Platform、Microsoft Foundry）。接入前先有明确
   消费者、成本归属与 lane 设计，再按
   [cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md) 走清单变更，不给
   订阅账号开路由。API 和云通道同样受地区政策约束，不能替代实际使用资格。

## 官方依据（2026-10-15 复核）

| 结论 | 出处 |
| --- | --- |
| 产品只在白名单国家/地区可用；未支持包括"人身处未支持地区""实体注册或总部在未支持地区"及其控股主体 | [Supported Regions Policy](https://www.anthropic.com/legal/supported-regions)（生效 2026-05-12） |
| 禁止 resell / proxy，"包括通过消费者订阅路由请求，或未如实说明所用产品或客户端" | [Usage Policy](https://www.anthropic.com/legal/aup) |
| 禁止违反 Supported Regions Policy 向他人提供访问；禁止多账号规避检测与绕过封禁 | 同上 |
| 不得与他人共享账号登录信息、API key 或账号凭据，也不得让账号供他人使用 | [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) |
| 除 API key 或官方另有明确许可的场景外，不得通过自动化或非人类方式访问服务 | 同上 |
| 不允许第三方开发者提供 Claude.ai 登录，或代表其用户转发 Free/Pro/Max 凭据；不得收集、存储、中转 Claude.ai 凭据或 session token | [Claude Code 合规页](https://code.claude.com/docs/en/legal-and-compliance) |
| Pro/Max 的公布额度假设 "ordinary, individual usage" | 同上 |
| 封号的已知原因：反复违反 Usage Policy、从不支持地区创建账号、违反 ToS | [Safeguards 警告与申诉](https://support.claude.com/en/articles/8241253-safeguards-warnings-and-appeals) |

对本部署的直接含义：不要让 CPA 代持 Claude 订阅凭据并提供给其他调用方。
这与官方 Claude Code 自行登录、经网关转发原有请求是不同架构：
[Subscriptions and gateways](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)
明确说明，只设置 `ANTHROPIC_BASE_URL`、没有网关凭据时，本机已保存的订阅登录仍可
生效；配置网关凭据或 `apiKeyHelper` 后，请求改用该凭据，订阅额度不适用。
该文档不构成对 CPA 代持令牌、身份伪装或共享订阅的认可；不能仅凭使用网关就断言
共享了凭据或伪装了客户端。

## 上游症状样本（2026-10-15 只读核对，不作机制依据）

以下分别包含 Claude 停号报告、提问和 Antigravity/Google 案例，只作排障线索，不能
混合统计或推断 Claude 封号率。所引材料不足以确认指纹、遥测、地区或频率的因果关系；
issue 的 closed/completed 状态也不证明根因已确认、已修复或当前版本不受影响。

- [#2211](https://github.com/router-for-me/CLIProxyAPI/issues/2211) `claude code subscription banned`
  （closed/completed）：贴出 Anthropic 自动复核禁用原文，多人回应同类；客户端含 opencode。
- [#5343](https://github.com/router-for-me/CLIProxyAPI/issues/5343) `Anthropic account bans`
  （closed/completed）：只有提问，没有复现条件或结论，不能当"必然封号"证据。
- [#2983](https://github.com/router-for-me/CLIProxyAPI/issues/2983)（closed/completed）maintainer 口径：
  热门客户端（claude code、opencode）里用"必封"，冷门或自写客户端一般无碍；并称检测疑似
  基于报文关键词而非指纹。该说法只对应 Antigravity/Google，不能外推到 Anthropic。
- [#1803](https://github.com/router-for-me/CLIProxyAPI/issues/1803)、
  [#1814](https://github.com/router-for-me/CLIProxyAPI/issues/1814)：报告引用的 Antigravity
  停用通知提及"使用第三方软件登录"；其产品、凭据与执行链不同，不能外推到 Anthropic。

对本部署的含义：这些报告不能裁定 Claude 停号究竟由设备指纹、客户端身份、报文、
地区或其他因素触发。Google/Antigravity 的内容过滤复现也不能证明 Anthropic 的封号机制。
不采用指纹清洗或 UA 伪装，是因为缺少可靠收益证据且可能引入身份歪曲与凭据风险；
地区资格与订阅凭据用途仍按 Anthropic 官方条款判断，不从社区个案反推安全参数。

读取上游源码的口径（只读）：登记检出
`D:/CODE/external/vps-ssh-launcher-references/repos/CLIProxyAPI` 的**工作树可能落后于运行
镜像**（2026-10-15 实测：工作树 v8.0.13，bwg 已部署 v8.0.29）。用 `git -C <checkout> show
<tag>:<path>` 从既有对象库直接读部署版本，不 fetch/checkout，也不假设工作树等于运行版本；
行号会随版本漂移，按符号名检索。

## 接入前的分层清单

分两类：**资格与认证约束**及**减少配置错误的措施**。后者不构成防封保证，
官方未公布可据此计算封号概率的指纹或 IP 评分阈值。

### 硬约束

- **地区与身份**：按 Supported Regions Policy 核对实际使用地，以及组织的注册地、
  总部和控制关系。人在未支持地区时，代理、VPS 或指纹手段不改变资格；API 与云
  通道也不能绕过此限制。不额外推导所有地区字段必须相同的规则。
- **凭据保护**：个人订阅只服务本人，使用官方客户端与官方登录流程；不把订阅
  凭据交给 CPA 代持或供其他客户端使用。普通网络代理和上述官方 Code 网关路径
  应分别核对，不能仅凭 `ANTHROPIC_BASE_URL` 非空判断违规。
- **支付合规**：使用真实、准确且本人有权使用的账户与付款资料，并满足当前官方
  支付要求；没有官方依据时，不把付款地与出口 IP 同国写成硬规则。
- **不做对抗性规避**：指纹清洗、UA/cloaking、identity-confuse、state 注入、
  第二账号轮换、多账号规避检测。与
  [cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md) 的
  "禁止"段同口径。按规避限制或歪曲客户端身份的具体行为判断，不将普通清理
  浏览器数据、隐私设置或正常协议头维护一概判定为违规。

### 减少配置错误（不构成合规或防封保证）

- **网络稳定**：避免不必要的出口切换导致连接中断；保留 TLS 校验，分别核对浏览器、
  Desktop 和 CLI 的代理继承。网络连通性和出口所在地都不能证明账号资格或安全。
  CPA 自身不以环境代理作为支持的配置入口；管理查询与模型请求的选择规则见
  [CPA 出站代理与管理查询](cpa-gateway.md#cpa-出站代理与管理查询)。
- **使用形态**：遵循对应产品的认证与用量限制，避免无必要的重复发送和并发；尊重
  `Retry-After`。官方 Code、Agent SDK 与 API 的允许用途分别核对，不将自动化本身
  一概判为违规，也不把订阅额度转给其他客户端。
- **IP 信誉**：官方未公布机房或住宅 IP 的封号率、安全评分或保证；不根据社区
  经验要求购买“纯净 IP”，不以固定 IP 或检测网站评分作为准入判据。
- **客户端**：用官方 Claude Code / Desktop 安装包与官方登录，排查旧端点、凭据
  变量和 `apiKeyHelper` 的来源与优先级，不打印令牌或批量删除配置。网关认证行为
  按上文区分；本部署继续不采纳 CPA 代持 Claude 订阅 OAuth 的路径。

## 可执行判据

仓库侧（只读，确认 Claude 没有被接成路由）：

```bash
rg -n "claude" scripts/remote/cpa_provider_routes.json
# 期望：仅出现在 antigravity_oauth_exclusions / codex_api_key_exclusions（墓碑），
# 不出现在 oauth_routes[].models 或 providers[].models。
```

远端侧（经 SSH 连接真实 BWG，须有本任务明确授权；只读确认当时目录）：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
# 期望：MODEL_IDS_UNKNOWN=none、catalog_oauth_missing=none，目录不含 claude-*。
```

语义侧：`cpa_policy.py` 拒绝清单中未审查的共享 OAuth 路由；当前允许名称只有
ChatGPT 与 Antigravity。此检查不等于扫描或阻断 CPA auth 目录中的全部凭据。
没有 Claude 路由、`MODEL_IDS_UNKNOWN=none` 或 doctor 通过，都不能单独证明没有
Claude 凭据，更不能证明未来误导入的 Claude OAuth 会被自动阻断。全量排除是否启用
须另查运行配置；任何新增阻断策略都需独立授权、回滚与验证。

## 禁止

- 不为 Claude 订阅 OAuth 建公共 key、lane 或探针矩阵。
- 不用 HTTP 200、固定出口 IP、限流或一次成功推断账号安全。
- 不在事件窗口用换账号、清冷却、复用旧凭据等方式"恢复"服务。
- 不把凭据、device-login 日志或授权 URL 带入 Git、receipt 或聊天回显。

## 复核触发

以下任一发生时重读官方条款并更新本页：Anthropic 更新 Supported Regions Policy、
Usage Policy、Consumer Terms 或 Claude Code 合规页；本仓出现 Claude 接入需求；出现
疑似 Claude 侧风控信号。
