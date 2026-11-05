# WorkBuddy 0.9.86: a recorded 6004 reset only reaches executor/catalog

## Impact

`scheduler.pick` can keep selecting a credential whose `(credential, model)` 6004 window is still active. The executor then fast-fails locally, so the upstream is not called, but the scheduler has already consumed a selection and pushed the healthy WorkBuddy siblings behind the dead pair.

With `scheduler_priority_reclaim: true`, the sticky/priority pick returns that pair repeatedly until another request establishes an ordinary cooling entry.

## Source path (v0.8.94, commit 6dccdfb)

- `model_ratelimit.go`: one 6004 creates a precise `(uid, model)` reset.
- `main.go`: both executors reject that pair after they are selected.
- `models.go`: `handleModelForAuth` calls `filterCoolingModels`, but not the 6004 registry.
- `scheduler.go`: the candidate filter calls `modelIsCooling`, but not `modelRateLimitError`.

The 6004 registry uses the credential UID; the ordinary cooldown registry uses the host auth ID. The catalog URL accepts a sanitized UID while candidate IDs are filename-derived, so any fix must use one canonical join key.

In production, a 6004 entry for UID `0faaff85-…` was observed as a
`workbuddy-intl-0faaff85-….json` executor with a millisecond-scale local
rejection. That supports the reported key mismatch, but the exact candidate
ID-to-UID mapping remains a maintainer/host-contract question.

## Reproduction

Hermetic Go expression against `v0.8.94`, no network or credential:

1. Record a real 6004 envelope for account `A` and model `M`.
2. Make `M` still present for `A` and a healthy account `B`.
3. Call `handleSchedulerPick` with `[A, B]` and `M`.

Expected: `B`.

Observed: `A`; the executor would reject it before the upstream call.

Likewise, `handleModelForAuth` keeps advertising that known-limited `M` for `A`.

The source gap can be checked directly:

```text
plugins/workbuddy/model_ratelimit_test.go: modelRateLimitError fast-fails, but no scheduler/catalog integration test
plugins/workbuddy/models.go:1482: filterCoolingModels only
plugins/workbuddy/scheduler.go:116: modelIsCooling only
```

This is distinct from v0.8.94's fixed CN `11-128` classification (#75) and exhausted-pool delegation (#74). In this case the credential is not zero-balance and does not need a whole-account stop: only the recorded model pair is unavailable until its declared reset.

## A local 0.9.86 candidate is an illustrative fix, not yet merge-ready

Apply the attached patch. On BWG it passed `gofmt` check, `go vet ./...`, `go test -count=1 ./...`, and Linux amd64 C-shared build. Both of these new tests are included:

- scheduler skips `A`, selects `B` for `M`;
- catalog excludes the pair for `A`, but leaves another credential's independent window eligible.

No real request to WorkBuddy was made. The candidate directly joins on the
candidate ID, so it is not a verified fix for the filename/UID mismatch above.

Attached patch is 5 files, +84 lines, on release base `v0.8.94` /
`6dccdfb9f0cef599324c420a24057ea605970789`.

## Open API question

I have not asserted a stable mapping beyond the current local deployment.
The safer requested design is to resolve the stored UID exactly once, then use
that same canonical key in both scheduler and catalog filtering. I can implement
that follow-up after maintainers confirm the intended scheduler-to-storage join,
or submit the issue together with a failing integration test before changing
selection behavior.
