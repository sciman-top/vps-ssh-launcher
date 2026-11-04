# Issue A — 全池 credits 耗尽时仍返回耗尽凭据

**目标仓库**：`mmqz/cpa-multi-plugins`
**类型**：缺陷 / 特性（混合 provider 请求无法落到下一个 provider）
**建议标题**：`[workbuddy] scheduler.pick returns a credit-exhausted credential, so mixed-provider requests never reach another provider`
**关联**：#35（200 + 空内容）、#71（priority reclaim）
**核对基线**：v0.8.93 / commit `c326248ef6fd3dbeee9e16c53adf279ffa5904cf`

---

**TL;DR** — With `scheduler_mode: credits`, when every WorkBuddy candidate is
credit-exhausted, `scheduler.pick` still returns one of them (`Handled: true`).
A request that also carries a non-WorkBuddy candidate for the same model
(e.g. an OpenCode / DeepSeek key) therefore never reaches that provider: it is
served by a credential with zero balance and comes back as an upstream
429/6004 or as a 200 with empty content (#35).

**Where**

`scheduler.go` already defers correctly when the candidate list is empty
(`:135-143`) and when the picker returns `""` (`:161-163`). The gap is the
picker's last resort:

```go
// active_auth.go:83-91
if next == "" {
    // All exhausted or disabled — keep current if still alive, else first candidate.
    if cur != "" {
        if _, ok := byID[cur]; ok {
            return cur
        }
    }
    next = candidates[0].ID
}
```

`isCreditsExhausted` (`billing.go:895-907`) has already computed the state at
`scheduler.go:148`; it is passed in as `Exhausted` and then discarded once no
healthy candidate exists.

**Repro** (should fail on v0.8.93)

```go
func TestSchedulerPick_AllCreditExhausted_DefersToHost(t *testing.T) {
	resetActiveAuth(t)
	defer setSchedulerMode(schedulerModeCredits)()
	accountCache.Store("wb-a", &accountCacheEntry{credits: &creditsSummary{TotalRemain: 0, TotalUsed: 500, TotalSize: 500}})
	accountCache.Store("wb-b", &accountCacheEntry{credits: &creditsSummary{TotalRemain: 0, TotalUsed: 500, TotalSize: 500}})
	defer func() { accountCache.Delete("wb-a"); accountCache.Delete("wb-b") }()
	raw, err := handleSchedulerPick(mustMarshal(t, pluginapi.SchedulerPickRequest{
		Provider: providerName,
		Model:    "deepseek-v4.1-flash",
		Candidates: []pluginapi.SchedulerAuthCandidate{
			{ID: "wb-a", Provider: providerName},
			{ID: "wb-b", Provider: providerName},
		},
	}))
	if err != nil {
		t.Fatal(err)
	}
	if resp := parsePickResponse(t, raw); resp.Handled {
		t.Fatalf("credit-exhausted pool must defer to the host, got %+v", resp)
	}
}
```

Actual: `Handled: true, AuthID: "wb-a"` (or `wb-b`). Expected: `Handled: false`.

The expected failure was confirmed by replicating `pickActiveAuth` verbatim in
isolation (all-exhausted input returns `wb-a`); I could not run the package test
itself here because `main.go` is cgo-only and this workstation has no C
toolchain — CI will be the first real run.

**Proposed change** — after `picked` is resolved in `handleSchedulerPick`, if the
picked candidate is exhausted, return `Handled: false` — the same envelope the
empty-pick path already uses. Keep the hard stop for the veto classes that must
not leak to another provider: channel-risk cooling (11128), account-restriction
quarantine (403 + 11140) and the #61 credit guardrails. Those are policy
decisions; credit exhaustion is capacity.

**Compatibility** — deferral changes what a single-provider deployment sees: the
host reports "no auth" instead of spending a doomed attempt. If you would rather
not change default behaviour, I can gate it behind
`scheduler_defer_exhausted_pool: true` (default false). Either shape is fine —
your call, and I will write the PR against `main` with the test above.

**Scope** — one function plus one test; no new config key if you prefer the
unconditional form.
