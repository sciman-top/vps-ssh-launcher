# 编辑「模型供应商」页的 API Key 会重算 `api_provider_mode`，静默停掉本地 Provider Gateway

## 环境

- Cockpit Tools v1.3.65（源码版本；现象在 v1.3.57 分支上同样可读，逻辑一致）
- 平台：Windows 11
- 上游 provider：自建 CLIProxyAPI 网关（公网 HTTPS 入口）
- Codex CLI / ChatGPT desktop 通过 `~/.codex/config.toml` 指向本地 Provider Gateway

## 摘要

在「模型供应商」页编辑某个 provider 的 **API Key** 时，`handleSaveApiKeyEdit` 会联动更新
关联账号，并在其中**重算** `api_provider_mode`：

```tsx
// src/components/codex/CodexModelProviderManager.tsx
const presetId = resolveCodexApiProviderPresetId(savedProvider.baseUrl);
const isOpenAIOfficial = presetId === "openai_official";
const wireApi = resolveProviderWireApi(savedProvider);
const apiProviderMode = isOpenAIOfficial ? "openai_builtin" : "custom";   // ← 这里
```

这个重算会让账号的 `api_provider_mode` 从 `Custom` 变成 `openai_builtin`（或反之），
从而使 `account_requires_provider_gateway(account)` 由真变假。该函数是本地
Provider Gateway sidecar 是否启动的唯一判据：

```rust
// src-tauri/src/modules/codex_local_access_provider_gateway.rs
pub fn account_requires_provider_gateway(account: &CodexAccount) -> bool {
    if codex_account::is_grok_upstream_provider(account) { return true; }
    if is_chat_completions_api_key_account(account) { return true; }
    account_uses_synced_model_shell_gateway(account)      // 依赖 api_provider_mode == Custom
}
```

`account_uses_synced_model_shell_gateway` 的第一道检查就是
`account.api_provider_mode != CodexApiProviderMode::Custom ⇒ false`。

结果：sidecar 不再启动，本地端口消失。**Cockpit 的 app 日志里没有任何错误**——
既没有"sidecar 启动失败"，也没有"网关停止"记录，只有
`[Codex Start] default provider gateway phase finished: elapsed_ms=1` 一行正常日志。

停用路径本身是静默的，可在源码确认：`stop_provider_gateways_for_profile_locked`
（`codex_local_access_provider_gateway.rs`）只在**失败**时写日志
（`等待端口释放失败` / `停止监听任务超时`），成功停止不写 info。
`stop_provider_gateway_runtime` 同理，仅 `log_codex_api_warn` 出现。
所以判据翻转这件事在整个日志平面没有留下痕迹。

## 影响

客户端（同一台机器）表现为两个看似无关的错误：

1. Cockpit 自己的「获取上游模型」→ `PROVIDER_MODELS_HTTP_503`
2. ChatGPT desktop / Codex CLI → `Connection failed: error sending request`

两者同源：都指向那个已经不存在的 loopback 端口。

用户视角是"每次重启 Cockpit 后都报错"，因为重启只重新走一遍启动阶段，而判据已经被永久改写。
唯一能自救的动作是**切换一次账号**（`activate_provider_gateway_after_switch_if_needed`），
所以现象表现为"需要手动切换或切换并启动 API 网关"——这正是容易被误判为使用者操作问题的原因。

## 最小复现

1. 建一个 API Key 类型的 provider，`baseUrl` 指向 `http://127.0.0.1:<port>/v1`，
   并让账号满足 `account_requires_provider_gateway`（`api_provider_mode=Custom` +
   `api_sync_model_catalog_to_codex` + catalog 非空 ⇒ 走 synced model shell gateway）。
2. 确认本地 Provider Gateway 已启动。
3. 在「模型供应商」页对该 provider 点「编辑」→ 只改 **API Key** 一个字段 → 保存。
4. 观察：sidecar 不再启动；app 日志无错误；客户端报 503 / connection failed。

## 期望行为

编辑一个 provider 的 API Key 不应该改变它的**接入模式**。`api_provider_mode` 表达的是
"该 provider 用官方内置通道还是自定义通道"，它与密钥内容无关。当前实现把它当成
`baseUrl` 的派生值重算，导致一次纯凭据更新携带了模式变更的副作用。

注意 `apiProviderId` 已经用同样的 `presetId` 决定取值（`presetId === CODEX_API_PROVIDER_CUSTOM_ID
? savedProvider.id : presetId`），说明这套字段确实与 `baseUrl` 相关——但**沿用**一个已经存在的
`api_provider_mode` 与**重算**它是两件事，前者不会破坏已有绑定。

## 建议修法

在联动更新时优先沿用账号已有的 `api_provider_mode`，只有当账号没有该字段（或为空）时才按
`baseUrl` 推断：

```tsx
for (const account of linkedAccounts) {
  const apiProviderMode =
    account.api_provider_mode ?? (isOpenAIOfficial ? "openai_builtin" : "custom");
  ...
}
```

如果"provider 改过 baseUrl 后必须重算模式"是刻意设计，那么退一步：**仅在 `baseUrl` 实际发生变化时
才重算**，并把该变化显式告知用户（因为它的后果是本地网关停摆，属于可感知的副作用，不该静默发生）。

## 附带：为什么这个坑很难自诊断

- 端口消失导致的 503 与"上游网关认证失败"在 UI 上文案相同；
- 触发点（编辑 key）与症状（下次重启后报错）之间隔着一次重启，时间上不衔接；
- app 日志不记录该停止决策——判据翻转发生在一次成功保存操作里，没有任何异常路径；
- 账号 id 是 `md5(api_key)` 派生的（`build_api_key_account_id`），换 key 会同时换 id，
  使旧绑定失效，进一步把因果链拉长成两步。

顺带一提：如果 app 日志能在 `stop_provider_gateways_for_profile` 被调用时记一行
（含被停端口与判据名），这类问题会从"需要读源码才能归因"降为"看日志即知"。

## 相关

- `docs`：无（本 issue 由本地反复踩坑归纳）
- 我记录的本机诊断脚本（只读，可复现判据）：
  `scripts/cockpit_provider_health.py` —— 核 key/端点匹配、绑定 md5 反查、sidecar
  config 一致性三条不变量，退出码 0/1/2。如果维护者需要，我可以整理成更小的
  复现用例提交。
