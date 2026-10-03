[Bug][Codex] Editing a provider API key in the Model Providers page recomputes `api_provider_mode` and silently stops the local Provider Gateway

## Summary

Saving a provider API key in the Model Providers page runs a linked-account update
(`handleSaveApiKeyEdit` in `src/components/codex/CodexModelProviderManager.tsx`) that
**recomputes** `api_provider_mode` from the provider's base URL:

```tsx
const presetId = resolveCodexApiProviderPresetId(savedProvider.baseUrl);
const isOpenAIOfficial = presetId === "openai_official";
const wireApi = resolveProviderWireApi(savedProvider);
const apiProviderMode = isOpenAIOfficial ? "openai_builtin" : "custom";   // recomputed, not preserved
```

That value is what `account_uses_synced_model_shell_gateway` checks first:

```rust
// src-tauri/src/modules/codex_local_access_provider_gateway.rs
fn account_uses_synced_model_shell_gateway(account: &CodexAccount) -> bool {
    if !account.is_api_key_auth() { return false; }
    if account.api_provider_mode != CodexApiProviderMode::Custom { return false; }   // <- flipped here
    ...
}

pub fn account_requires_provider_gateway(account: &CodexAccount) -> bool {
    if codex_account::is_grok_upstream_provider(account) { return true; }
    if is_chat_completions_api_key_account(account) { return true; }
    account_uses_synced_model_shell_gateway(account)
}
```

`account_requires_provider_gateway` is the sole predicate for whether the local
Provider Gateway sidecar is started. Once it goes false, the sidecar is not started
and the loopback port simply stops existing.

**Nothing is logged.** The stop path is silent on success: both
`stop_provider_gateways_for_profile_locked` and `stop_provider_gateway_runtime` only
emit `log_codex_api_warn` on failure (`等待端口释放失败` / `停止监听任务超时`); there is no
info line on a successful stop. The only line in the app log is the normal
`[Codex Start] default provider gateway phase finished: elapsed_ms=1`.

## Symptoms

Two apparently unrelated client errors on the same machine:

1. Cockpit's own "fetch upstream models" → `PROVIDER_MODELS_HTTP_503`
2. ChatGPT desktop / Codex CLI → `Connection failed: error sending request`

Both point at the same now-missing loopback port. The user-visible shape is
"it fails again after every Cockpit restart", because a restart re-runs the start
phase against a predicate that has already been permanently rewritten. The only
self-service recovery is **switching accounts once**
(`activate_provider_gateway_after_switch_if_needed`), which is why the symptom
presents as "I have to manually switch or switch-and-start the API gateway".

## Reproduction shape

1. Create an API-key provider whose `baseUrl` points at `http://127.0.0.1:<port>/v1`,
   with an account that satisfies `account_requires_provider_gateway`
   (`api_provider_mode=Custom` + `api_sync_model_catalog_to_codex` + non-empty catalog).
2. Confirm the local Provider Gateway sidecar is running.
3. In the Model Providers page, click edit on that provider, change **only** the API key,
   and save.
4. Observe: the sidecar is not started; the app log contains no error; clients report
   503 / connection failed.

Note that the account id is derived from the credential
(`build_api_key_account_id = codex_apikey_{md5(api_key)}`), so the same save also
changes the account id and invalidates any binding that referenced the old id. That
turns a one-step cause into a two-step chain, which is part of why this is hard to
attribute from logs alone.

## Expected behavior

Editing a provider's API key should not change its **access mode**.
`api_provider_mode` describes whether the provider uses the built-in official channel
or a custom channel; it is not a function of the credential's contents. Today a pure
credential update carries a mode change as a side effect.

`apiProviderId` is already derived from the same `presetId`
(`presetId === CODEX_API_PROVIDER_CUSTOM_ID ? savedProvider.id : presetId`), so these
fields are legitimately base-URL related. But **preserving** an existing
`api_provider_mode` and **recomputing** it are different operations, and only the
latter breaks existing bindings.

## Suggested fix

Prefer the account's existing `api_provider_mode`, falling back to the base-URL
inference only when it is absent:

```tsx
for (const account of linkedAccounts) {
  const apiProviderMode =
    account.api_provider_mode ?? (isOpenAIOfficial ? "openai_builtin" : "custom");
  ...
}
```

If recomputing on a base-URL change is intentional, then at minimum scope it to the
case where the base URL actually changed, and surface that the local gateway will stop
— it is a user-observable side effect and should not happen silently.

## Safety constraints

- Do not change which accounts require the gateway; only stop the mode from being
  rewritten by an unrelated key edit.
- No credential rotation, quota change, or model remapping as part of this fix.

## Related

- Optionally: logging a single info line when `stop_provider_gateways_for_profile`
  is entered (with the stopped port and the predicate name) would turn this class of
  problem from "requires reading the source to attribute" into "visible in the log".
- I maintain a read-only diagnostic script for this exact shape (`key` ↔ endpoint fit,
  bound-account md5 reverse lookup, sidecar config agreement; exit codes 0/1/2).
  Happy to reduce it to a minimal repro fixture if that would help.
