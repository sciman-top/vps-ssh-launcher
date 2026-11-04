# cockpit-tools #2676 评论草稿（补证据并请求关闭）

**目标**：`jlcodes99/cockpit-tools` Issue #2676
`[Bug][Codex] Provider Gateway rebuild drops account concurrency settings`
**动作**：只追加评论，**不编辑 issue 正文**（保留原始报告的记录价值）
**核对基线**：`origin/main`，2026-11-04

---

Thanks — re-checking against `main` today, the generator already copies both
fields, so this looks fixed after 1.3.66:

```rust
// src-tauri/src/modules/codex_local_access_provider_gateway.rs
:1479    collection.max_account_concurrency = template.max_account_concurrency;
:1481    collection.account_concurrency_wait_ms = template.account_concurrency_wait_ms;
```

Field evidence from our side, in case it helps confirm:

- two independent regenerations on 1.3.66 both produced
  `maxAccountConcurrency=3` / `accountConcurrencyWaitMs=45000` in the
  provider-gateway manifest (on 1.3.65 the same fields were `0` / `120000`,
  i.e. the struct defaults, which is what this issue reported);
- the sidecar now honours that budget: a controlled replay against a stub
  upstream returned `429 at 45.002s` for the 4th request with
  `maxAccountConcurrency=3`, i.e. the wait was applied instead of the old
  120 s default.

PR #2677 was closed unmerged, so I cannot point at a merge commit — but the
behaviour is present in `main`, and our local binary patch has been retired
since 1.3.66 (the sidecar guardrail now refuses to patch and reports
`SIDECAR_PATCH_RETIRED=1`).

If you agree the code above covers it, feel free to close. I am deliberately not
editing the issue body so the original report stays intact.
