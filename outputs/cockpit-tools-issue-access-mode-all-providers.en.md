# Feature request: expose the "instance access mode" selector to all API Key provider accounts (backend already supports it; frontend is DeepSeek-gated only)

> Target repo: jlcodes99/cockpit-tools · Baseline: v1.3.65 · Type: feature request with root-cause analysis
> Draft only — no secrets or hostnames included.

## Background

When a third-party Responses-protocol provider (self-hosted CLIProxyAPI gateway, API Key auth)
is bound as the Codex default instance, the Codex model list keeps showing "official shell"
aliases such as:

```
gpt-5.6-sol → gpt-6-astra-ciii
gpt-5.5     → deepseek-v4.1-flash
```

The v1.3.65 source shows these are deterministically produced by `allocate_provider_model_slots`:
`CODEX_PROVIDER_MODEL_SHELL_POOL = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]`
(`codex_local_access_foundation.rs:261`); every upstream model whose ID does not match an official
slug claims a free shell name in catalog order. When the upstream catalog changes (a self-hosted
gateway's catalog tracks upstream health), shells get re-assigned, which looks like "the mappings
keep changing and legacy names can never be removed". This is by design in gateway mode — but for
self-hosted gateways, showing real upstream IDs directly would be preferable.

## Request

Expose the access-mode selector (gateway / direct / CDP) to **all API Key provider accounts**,
or at least allow "direct" for accounts with `wire_api = responses`.

The backend is already ready:

```rust
// codex_account_provider.rs:951 update_account_instance_access
// 所有 API Key 供应商账号都支持实例接入方式：
// - gateway：走本地供应商网关（默认，行为不变）
// - direct：直连上游（要求上游说 Responses，Codex 端才能直连）
// - cdp：把模型清单注入官方客户端（传输仍按 wire_api 决定）
```

Validation already guards `direct` behind Responses support. Only the frontend blocks it:

- `CodexApiKeyLaunchSection.tsx:107`: `canChooseAccessMode = isDeepSeekResponsesAccount(account)`
- All three executors forward `deepSeekAccessMode` only when `isDeepSeekAccount(account)`:
  `useCodexAccountsAccessController.tsx:734`, `CodexInstancesPage.tsx:302`,
  `CodexModelProviderManager.tsx:3098`

## What direct mode gives these accounts (all anchors from v1.3.65)

1. `account_uses_raw_provider_model_ids = true` ⇒ identity model slots: Codex shows real upstream
   IDs; shell aliases (and pool names like `gpt-5.5`) disappear entirely.
2. `account_requires_provider_gateway = false` ⇒ account switch writes the raw account binding
   (`codex_account_commands.rs:1315`), the local instance gateway leaves the request path, and
   `restore_instance_gateways_on_startup` has no target for this account either
   (`codex_local_access_instance_gateways.rs:94`) ⇒ no more manual "switch & start" after every
   app/PC restart.

## Expected behavior

- Provider accounts (`wire_api = responses`) get the same access-mode picker in the launch area /
  provider form as DeepSeek accounts (at minimum gateway/direct).
- Choosing direct rewrites the binding through the existing switch flow; no manual JSON edits.

## Environment

- OS: Windows 11 · App: Cockpit Tools v1.3.65 (official installer)
- Provider: self-hosted CLIProxyAPI public gateway, native Responses wire API, API Key auth
