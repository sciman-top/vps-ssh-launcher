# 修复 10909（Provider Gateway）不启动

> 诊断时间：2026-10-03 21:40–22:10 CST｜纯只读诊断，**未改任何配置**
> 验证脚本：`bash outputs/verify-sidecar-10909.sh`

## 症状

| 症状 | 表现 |
|---|---|
| Cockpit「获取上游模型」 | `PROVIDER_MODELS_HTTP_503`（每次重启 Cockpit 后都有） |
| ChatGPT desktop | `Connection failed: error sending request`，需手动切换/启动 API 网关 |
| 端口 | `10909 CLOSED` / `14185 OPEN` |

## 根因（已闭环到源码）

10909 的启动**完全由「默认实例绑定哪个账号」决定**，与 key、config、配额都无关。

### 因果链

```
16:52  默认实例绑定 = __provider_gateway__:codex_apikey_ec280ff6…   → 10909 正常运行
       19:19:32 10909 仍在正常服务（当日共 396 条成功请求）
─────────────────────────────────────────────────────────────
19:30:26  在「模型供应商」页编辑该 provider 的 API Key
          → CodexModelProviderManager.tsx:2544-2588 的「联动更新关联账号」
          → 调用 updateCodexApiKeyCredentials(...)
          → 日志: Codex API Key 账号凭据已更新: ec280ff6 → 3364f7f2
          → 账号 id 由 md5(api_key) 重算，因此变了（codex_account_provider.rs:450）
          → 同时 apiProviderMode 被重算（:2559）
19:30:40  再编辑一次（id 不变 3364f7f2 → 3364f7f2）
─────────────────────────────────────────────────────────────
19:35:21  Cockpit 重启
19:35:23  只起了 14185，10909 没了
20:14:18  用户切号 → 绑定变成裸 codex_apikey_3364f7f2（无 __provider_gateway__: 前缀）
          → 此后每次启动都不再拉起 10909
```

### 判定代码

`commands/codex_instance.rs:410-467 ensure_provider_gateway_for_bind_account`：

- 绑定 `__provider_gateway__:<id>` → **启动 10909**
- 绑定裸 `codex_apikey_*` → 仅当 `account_requires_provider_gateway(account)` 为真才启动，
  否则 `stop_provider_gateways_for_profile`（**主动停**）

`codex_local_access_provider_gateway.rs:1307 account_requires_provider_gateway` 的三条：

1. `is_grok_upstream_provider` — 有 `upstream_grok_account_id`
2. `is_chat_completions_api_key_account` — API key 且 `wire_api == "chat_completions"`
3. `account_uses_synced_model_shell_gateway` — API key + `api_provider_mode == "Custom"`
   + `api_sync_model_catalog_to_codex` + `wire_api == "responses"` + 模型列表非空

**19:30 的联动更新把 `api_provider_mode` 重算掉了**（`CodexModelProviderManager.tsx:2559`
`isOpenAIOfficial ? "openai_builtin" : "custom"`，preset 由 baseUrl 推断），
三条全部落空 ⇒ 10909 不再启动。

### 日志判据（重要）

- 10909 的日志**只在 `logs/codex-api.log.<date>`**，tag `[provider-gateway]`
- **app.log 里搜不到**（用 app.log 判会得出错误的"从未启动"）
- 一次启动没起 10909 的签名：
  `[Codex Start] default provider gateway phase finished: elapsed_ms=1`（1ms 空操作）

### 已排除的错误假设

- ❌ **不是** sidecar 自动重启配额耗尽：app.log 全量搜 `自动重启`/`已达到限制` 命中为 **0**，
  Cockpit 从未触发过自动重启（`codex_local_access_sidecar_runtime.rs:63-112`）
- ❌ **不是** key 无效：`IhCT`（42 字符）是 10909 的唯一有效 key，`fE_m` 是 fq 公网的 key
- ❌ **不是** CPA/VPS 故障：远端全绿，与本次无关

---

## 修复方案

### 方案 A（推荐）：恢复该账号的 provider 配置，让 10909 自动拉起

**入口 1 —— 实例模型路由的「获取列表」按钮**（已验证会透传/兜底为 custom）

`CodexModelRoutingFields.tsx:615-631` 的 `updateCodexApiKeyCredentials` 调用中，
`api_provider_mode` 取 `account.api_provider_mode ?? "custom"` —— **undefined 时兜底 custom**：

1. 打开 Cockpit → **实例**（Codex 默认实例）→ 模型路由 / 添加 API 渠道区域
2. 找到绑定账号 `api-key-3364f7f2` 那一行
3. 点「**获取列表**」按钮（`instances.form.modelRouting.fetchModels`）
4. 等待成功 → 账号的 `api_provider_mode` 被写入 `custom`（若原为 undefined）
5. 回到默认实例，**重新切号**到该账号（触发 `activate_provider_gateway_after_switch_if_needed`）
6. 运行 `bash outputs/verify-sidecar-10909.sh` → 期望 `10909: OPEN`

**入口 2 —— 直接改 provider 的 baseUrl 为非 OpenAI 官方**

`isOpenAIOfficial = (presetId === "openai_official")`，preset 由 `baseUrl` 推断。
把 provider 的 baseUrl 保持在**非 `https://api.openai.com/v1`**（例如你实际用的中转地址），
`apiProviderMode` 就会重算为 `custom`。

### 方案 B：绑定到需要网关的账号

1. Cockpit → Codex 账号页 → 选择/切换到一个**需要 provider gateway** 的账号
   （Grok 账号 / `wire_api=chat_completions` 的 key / Custom 模式 + 同步目录的 key）
2. 切号后 `activate_provider_gateway_after_switch_if_needed` 会自动拉起 10909
3. 运行验证脚本确认

> ⚠️ **注意**：`codex_549794138e26af5d101f2307cdce7f41`（`sciman.top@gmail.com`, `plus`）
> 是 OAuth 账号，走的是 **14185 API 服务**路径，**不会**拉起 10909。

### 桌面 ChatGPT desktop

**无需改动** `~/.codex/config.toml`。它已指向 10909：

```toml
[model_providers.codex_local_access]
name = "CPA (local 10909)"
base_url = "http://127.0.0.1:10909/v1"
```

10909 一旦拉起，desktop 自动恢复。这与 10/2「直连走本地闸门」的整改方向一致。

---

## 验收

```bash
bash outputs/verify-sidecar-10909.sh
```

期望：
- `10909: OPEN`
- 第 5 节出现**今天新的** `provider-gateway] sidecar 已启动 … 10909`
- 结论行显示 `✓ 10909 正在运行`

然后：
- Cockpit「获取上游模型」不再报 503
- ChatGPT desktop 可正常连接（无需手动切换）

---

## 回滚

本次修复**只改 UI 配置，不涉及文件写入**。若要回滚：
把账号的 provider 配置改回改前的值，或改绑其他账号。无残留。
