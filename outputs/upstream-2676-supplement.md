## Supplement: exact root cause, a self-evidencing symptom, and measured impact

#2677 fixes this, but the root cause is narrower than "the rebuild drops the
setting", a second field travels with it, and the failure leaves an observable
fingerprint worth recording.

### 1. The generator never copies the account-concurrency family at all

`build_provider_gateway_collection_for_profile`
(`src-tauri/src/modules/codex_local_access_provider_gateway.rs:1837`) starts from
an empty collection:

```rust
let mut collection = new_empty_local_access_collection()?;   // max_account_concurrency = 0
                                                            // account_concurrency_wait_ms = 120000
if let Some(template) = load_collection_from_disk()? {
    apply_provider_gateway_template_settings(&mut collection, &template);
}
```

`apply_provider_gateway_template_settings` (`:1566`) copies **18** unrelated
fields — `client_base_url_host`, `image_generation_mode`, `upstream_proxy_url`,
`routing_strategy`, `model_aliases`, `model_pricings`, `excluded_models`,
`session_affinity`, `session_affinity_ttl_ms`,
`session_affinity_default_enabled_migrated`, `max_retry_credentials`,
`max_retry_interval_ms`, `timeouts`, `active_timeout_preset_id`,
`timeout_presets`, `disable_cooling`, `restrict_free_accounts`, `debug_logs` —
but **neither `max_account_concurrency` nor `account_concurrency_wait_ms`**.

So nothing is "lost" on the way: the provider-gateway sidecar is structurally
incapable of ever observing the user's setting. Both fields stay on the struct
defaults — concurrency `0` (gate disabled) and wait `120000` ms.

### 2. The stale manifest file is itself a symptom

The manifest is written through `write_secret_string_atomic_if_changed`, which
**skips the write when the content is byte-identical**. Because neither field is
copied, the generated content is identical run over run, so the file is never
rewritten and keeps its old mtime.

Observed 2026-10-02 (Windows, v1.3.65) after an app restart at 22:30:21, with
both sidecars restarting (22:30:21 / 22:30:22):

```
codex_local_access_sidecar/manifest.json
    -> rewritten 22:30, accountConcurrencyWaitMs = 45000
codex_provider_gateway_sidecars/<hash>/manifest.json
    -> untouched, still 09:38, accountConcurrencyWaitMs = 120000,
       maxAccountConcurrency = 0
```

The API-service sidecar picked up the changed setting; the provider-gateway one
did not, and the unchanged mtime is the visible fingerprint of the missing copy.
A user cannot tell "not regenerated" from "regenerated identically", so the
stale file is not a separate bug.

### 3. Measured impact of the wait default

The wait budget is not cosmetic. With `maxAccountConcurrency = 3`, a request that
finds the gate saturated waits out the whole budget and is *then* rejected with
429 `account_concurrency_exceeded`:

| wait budget | observed rejection latency |
|---|---|
| 120000 ms (generator default) | 120.002 s |
| 45000 ms | 45.003 s |

Same scratch config + manifest and same local stub upstream in both runs, so the
only variable is the binary and the latency equals the configured budget exactly.

Over 8 days the client request log shows 4–9 such events per day
(09-27: 4, 09-28: 6, 10-01: 9, 10-02: 4) with client-visible latencies of
120005 / 120020 / 120049 / 120006 ms. A request that exhausts the entire budget
and still fails cannot have been rescued by a longer wait, so the default only
buys two minutes of dead client time.

### Suggested fix

#2677 does the right thing (both fields plus a regression test). Copying
`account_concurrency_wait_ms` matters as much as the concurrency limit itself.
