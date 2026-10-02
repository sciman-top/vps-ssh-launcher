Two notes on this PR.

**1. CI is waiting for approval, not failing.** Both workflows triggered by this
PR are sitting in `action_required`, which is GitHub's state for "a maintainer
must approve the workflow run for this fork branch":

```
🚀 Build Matrix | event=pull_request | conclusion=action_required
CodeQL          | event=pull_request | conclusion=action_required
```

The code itself is not blocked on anything — the branch applies cleanly
(`mergeable: MERGEABLE`) and touches two files. Could a maintainer approve the
run so the checks can actually execute?

**2. The change is verified end to end locally.** I applied the equivalent
two-line change at the sidecar entry of v1.3.65 (at the entry rather than in the
generator, because the generator cannot be changed without rebuilding the app)
and ran a controlled A/B: a local stub upstream that accepts and never replies,
plus a scratch copy of the live provider-gateway config and manifest —
**identical inputs for both binaries**, so the only variable is the build. Three
concurrent requests saturate `maxAccountConcurrency = 3`, then a fourth is
measured:

| build | 4th request |
|---|---|
| with the copied wait (45000 ms) | 429 @ **45.003 s** |
| without it (manifest still says 120000) | 429 @ **120.002 s** |

Both rejections carry the same code, `account_concurrency_exceeded`; only the
wait differs, and it tracks the copied value exactly. That confirms the copied
setting really does reach the sidecar's behaviour, i.e. this PR alone is enough
to make the setting effective — no second code path is needed.

For context on why `account_concurrency_wait_ms` deserves to be in this PR and
not a follow-up: with the generator default of 120000 ms, a request that finds
the gate saturated waits out the entire budget and is then rejected, so the
client loses two minutes before seeing a 429. Measured over 8 days that happened
4–9 times per day (09-27: 4, 09-28: 6, 10-01: 9, 10-02: 4), with client-visible
latencies of 120005 / 120020 / 120049 / 120006 ms.
