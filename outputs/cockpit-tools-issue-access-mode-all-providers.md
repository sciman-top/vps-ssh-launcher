# Issue 草稿：请把「接入方式」选择器开放给所有 API Key 供应商账号（后端已支持，仅前端 DeepSeek 门控）

> **已发布**：https://github.com/jlcodes99/cockpit-tools/issues/2723 （2026-10-05 02:2x，以本文件为正文）
> 目标仓库：jlcodes99/cockpit-tools · 基准版本：v1.3.65 · 性质：功能请求（含根因分析）
> 本文不含任何密钥/主机名等敏感信息。

## 背景

用第三方 Responses 协议供应商（自建 CLIProxyAPI/CPA 网关的 API Key 账号）绑定为 Codex
默认实例后，Codex 模型列表里持续出现「官方壳位名」别名，例如：

```
gpt-5.6-sol → gpt-6-astra-ciii
gpt-5.5     → deepseek-v4.1-flash
```

排查 v1.3.65 源码后确认这是 `allocate_provider_model_slots` 的确定性分配：
`CODEX_PROVIDER_MODEL_SHELL_POOL = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]`
（`codex_local_access_foundation.rs:261`），凡不匹配官方 slug 的上游模型按目录顺序领取壳名。
上游目录变化（自建网关的目录是上游健康快照）会导致壳位重排，表现为「映射总是变来变去、
旧名清不掉」。这是网关模式的设计行为，不是数据污染——但对自建网关用户来说，
更希望直接看到上游真实模型 ID。

## 请求

把「接入方式」（网关列出 / 直连上游 / CDP 注入）选择器开放给**所有 API Key 供应商账号**，
至少对 `wire_api = responses` 的账号开放「直连上游」。

理由：后端已经就绪——

```rust
// codex_account_provider.rs:951 update_account_instance_access
// 所有 API Key 供应商账号都支持实例接入方式：
// - gateway：走本地供应商网关（默认，行为不变）
// - direct：直连上游（要求上游说 Responses，Codex 端才能直连）
// - cdp：把模型清单注入官方客户端（传输仍按 wire_api 决定）
```

校验已包含 `direct` 需 Responses 的防护。阻塞只在前端：

- `CodexApiKeyLaunchSection.tsx:107`：`canChooseAccessMode = isDeepSeekResponsesAccount(account)`
- 三个执行器仅在 `isDeepSeekAccount(account)` 时传递 `deepSeekAccessMode`：
  `useCodexAccountsAccessController.tsx:734`、`CodexInstancesPage.tsx:302`、
  `CodexModelProviderManager.tsx:3098`

## direct 对这类账号的实际收益（效应链均有源码锚点）

1. `account_uses_raw_provider_model_ids = true` ⇒ 模型槽位全 identity，
   Codex 直接展示上游真实 ID，不再产生壳位别名（`gpt-5.5` 等池名彻底消失）。
2. `account_requires_provider_gateway = false` ⇒ 切号写入裸账号 ID 绑定
   （`codex_account_commands.rs:1315`），本地实例网关不再挂入请求链路；
   `restore_instance_gateways_on_startup` 的恢复目标也被同一谓词门控
   （`codex_local_access_instance_gateways.rs:94`）⇒ 重启后无网关恢复步骤，
   不再需要每次手动「切换并启动」。

## 期望行为

- 供应商账号（`wire_api = responses`）的启动区/provider 表单出现与 DeepSeek 一致的
  「接入方式」三选一（或至少 网关/直连 两项）。
- 选直连后由既有切换流程自动改写绑定，不需要用户手改任何 JSON。

## 实际验证（v1.3.65，生产环境）

我们已通过状态级手段（直接更新账号记录字段）在一台生产机上启用 direct，端到端验证全部通过：

- 切号不再拉起实例网关（切号耗时从 ~900ms 降至 ~200ms，无网关分支日志），全部本地网关端口无监听；
- 模型目录以 identity 槽位写入受管目录并同步 `model_catalog_json` 指针，Codex 模型列表
  与上游 `/v1/models` 逐名一致，无壳位别名；跨应用/系统重启稳定；
- 直连生成请求 200 正常返回。

即：后端链路在生产环境完整可用，缺的只是前端入口。长期依赖状态级手段不可维护
（app 更新后无法通过 UI 调整），希望官方把入口放开。

## 环境信息

- OS: Windows 11 · App: Cockpit Tools v1.3.65（官方安装版）
- 供应商类型：自建 CLIProxyAPI 公网网关，Responses 原生协议，API Key 鉴权
