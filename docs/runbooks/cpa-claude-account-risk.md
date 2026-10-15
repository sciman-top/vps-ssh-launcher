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
3. **确需 Claude 时走官方 API key 或授权云通道**（Claude Console API key、
   Amazon Bedrock、Google Cloud Agent Platform、Microsoft Foundry）。接入前先有明确
   消费者、成本归属与 lane 设计，再按
   [cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md) 走清单变更，不给
   订阅账号开路由。

## 官方依据（2026-10-15 复核）

| 结论 | 出处 |
| --- | --- |
| 产品只在白名单国家/地区可用；未支持包括"人身处未支持地区""实体注册或总部在未支持地区"及其控股主体 | [Supported Regions Policy](https://www.anthropic.com/legal/supported-regions)（生效 2026-05-12） |
| 禁止 resell / proxy，"包括通过消费者订阅路由请求，或未如实说明所用产品或客户端" | [Usage Policy](https://www.anthropic.com/legal/aup) |
| 禁止违反 Supported Regions Policy 向他人提供访问；禁止多账号规避检测与绕过封禁 | 同上 |
| 不得与他人共享账号登录信息、API key 或账号凭据，也不得让账号供他人使用 | [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) |
| 除 API key 场景外，不得通过自动化或非人类方式访问服务 | 同上 |
| 不允许第三方开发者提供 Claude.ai 登录，或代表其用户转发 Free/Pro/Max 凭据；不得收集、存储、中转 Claude.ai 凭据或 session token | [Claude Code 合规页](https://docs.claude.com/en/docs/claude-code/legal-and-compliance) |
| Pro/Max 的公布额度假设 "ordinary, individual usage" | 同上 |
| 封号的已知原因：反复违反 Usage Policy、从不支持地区创建账号、违反 ToS | [Safeguards 警告与申诉](https://support.claude.com/en/articles/8241253-safeguards-warnings-and-appeals) |

对本部署的直接含义：bwg CPA 是"一台 VPS + 一个共享账号 + 多个调用方"的形态。
对 ChatGPT OAuth 这是有明确 lane 与隔离的既有取舍；对 Claude 订阅账号则同时命中
"proxy"、"share credentials" 与 "未如实说明客户端" 三条。

## 接入前的分层清单

分两类：**硬约束**（违反即属条款违规，不是概率问题）与**降概率项**（工程经验，
官方未公布判据）。

### 硬约束

- **地区与身份**：账号创建地、付款主体、实际使用地应在支持地区内且一致。代理、
  VPS 或指纹手段不改变资格，只增加"账号创建于未支持地区"的封号概率。
- **凭据独占**：一个订阅账号只服务本人，只经官方客户端与官方登录流程；不把
  `ANTHROPIC_BASE_URL` 指向第三方中继来跑订阅凭据。
- **支付合规**：使用与账号地区一致的合法支付方式。
- **不做对抗性规避**：指纹清洗、UA/cloaking、identity-confuse、state 注入、
  第二账号轮换、多账号规避检测。与
  [cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md) 的
  "禁止"段同口径——这些动作本身即 Usage Policy 违规项。

### 降概率项（不构成合规或防封保证）

- **出口稳定**：单一、长期稳定的出口；不在会话中途更换国家/地区。
- **使用形态**：普通交互式个体使用；不做脚本化批量调用、不并发扇出，尊重限额与
  `Retry-After`。
- **IP 信誉**：机房/VPS 出口在账号创建与高频自动化场景信誉更差，属观察经验；官方
  未公布 IP 评分口径，不要当作判据。
- **客户端**：用官方 Claude Code / Desktop 安装包与官方登录。网关只在携带自有
  API key / Bedrock / Vertex 凭据时使用——
  [LLM gateway 文档](https://docs.claude.com/en/docs/claude-code/llm-gateway) 说明
  网关凭据生效时会替代订阅登录、订阅额度不适用。

## 可执行判据

仓库侧（只读，确认 Claude 没有被接成路由）：

```bash
rg -n "claude" scripts/remote/cpa_provider_routes.json
# 期望：仅出现在 antigravity_oauth_exclusions / codex_api_key_exclusions（墓碑），
# 不出现在 oauth_routes[].models 或 providers[].models。
```

远端侧（只读，确认目录里没有 Claude 名）：

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg
# 期望：MODEL_IDS_UNKNOWN=none、catalog_oauth_missing=none，目录不含 claude-*。
```

语义侧：任何新增的共享订阅 OAuth 路由都必须有已审查 lane，否则 `cpa_policy.py`
拒绝（`ungated shared credential = ban-shaped`）。这条对 Claude 同样成立——即使
将来由用户显式决策恢复某条 OAuth lane，它也首先是"降低触发概率"，不是"合规化"。

## 禁止

- 不为 Claude 订阅 OAuth 建公共 key、lane 或探针矩阵。
- 不用 HTTP 200、固定出口 IP、限流或一次成功推断账号安全。
- 不在事件窗口用换账号、清冷却、复用旧凭据等方式"恢复"服务。
- 不把凭据、device-login 日志或授权 URL 带入 Git、receipt 或聊天回显。

## 复核触发

以下任一发生时重读官方条款并更新本页：Anthropic 更新 Supported Regions Policy、
Usage Policy、Consumer Terms 或 Claude Code 合规页；本仓出现 Claude 接入需求；出现
疑似 Claude 侧风控信号。
