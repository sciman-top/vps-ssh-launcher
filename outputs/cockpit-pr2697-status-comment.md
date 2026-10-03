Thanks for maintaining this — a short status note so this is easy to pick up.

**CI has not run on this branch.** `gh pr checks` reports no checks at all on
`feat/provider-gateway-account-admission`, and the same is true for the sibling
`fix/provider-gateway-template-concurrency` (#2677). I mention it only in case
something on my side is the cause — my fork has Actions enabled, but its run
history only ever contains `main` (Sync Fork / CodeQL), never a PR branch. If
there is anything I should change to let the checks trigger (branch naming,
re-pushing, re-opening against a different base), say the word and I will do it.

**What I verified locally**, on a clean v1.3.65 base (`0b6514b`):

```
gofmt -l            -> clean for all five files
go vet ./...        -> clean
go test . -count=1  -> ok  ...cockpit-cliproxy  27.5s
```

**Relationship to #2677**, since the two are easy to confuse:

- #2677 makes `maxAccountConcurrency` / `accountConcurrencyWaitMs` survive
  Provider Gateway collection regeneration. Without it nothing reaches the
  manifest, so **this PR is inert at runtime by design** — `MaxAccountConcurrency
  <= 0` leaves the gate fully disabled and existing deployments are unaffected.
- They are otherwise independent and can merge in either order; #2677 alone is
  useful, and this one only becomes observable once #2677 lands.

Go sidecar only — no Rust or app-layer changes. Happy to rebase or split it
further if that makes review easier.
