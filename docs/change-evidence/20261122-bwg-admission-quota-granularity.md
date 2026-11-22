# 2026-11-22 BWG admission quota granularity (parallel-session merge + evidence-based envelope taxonomy)

## Incident and diagnosis

- 2026-11-21 17:13:39Z: the first-ever `claude-opus-5-5-high` request (~160K input)
  hit an upstream 429 with `Retry-After: 17504s`; the admission mirror latched the
  whole `antigravity-oauth` lane for the advertised ~4.9h. journalctl shows
  never-used `gemini-4-argon-high` requests refused at 17:13:51–17:16:35 with
  `reason=cooldown retry_after=1749x` while sibling traffic was still being served
  seconds later (28266b4 commit message). This is the "first use cooled, everyone
  punished" event the user reported.
- 2026-11-22 00:12Z: a generic `RESOURCE_EXHAUSTED` flash 429 armed a short
  lane window (`not_before` expiring 00:15:38Z, no `quota_group_not_before` on
  disk) — the pass-through shape behind the desktop screenshots
  ("Antigravity upstream returned HTTP 429. CPA did not receive a retry time.").
- Upstream quota topology (official docs + field data): per-account family
  buckets "Gemini models" / "Claude and GPT models" (5h + weekly, shared within
  family), the GEMINI 4 ARGON weekly bucket rolled out separately (first-party
  same-account panel divergence 2026-11-21 09:42:16: GEMINI 83% vs ARGON 32%),
  `MODEL_CAPACITY_EXHAUSTED` is a per-model capacity verdict (CLIProxyAPI
  upstream #7884, observed on gemini-4-hydrogen/neon and claude-opus-5-5, with a
  503 UNAVAILABLE twin whose message names the model), and detail-free 429
  storms can be content-fingerprint shaped (#7768).

## Two-session collision and reconciliation

A parallel session implemented the user's literal ask and rotated the live
generation five times (00:37–01:44Z, final `ab671756`), then cherry-picked its
work onto this session's `d1725c9` and pushed (`921e16a` → `a4f8ad2` →
`bc9388c`) before stopping. Its design: per-model durable quota keys
(`model:<name>` in `quota_group_not_before`), family split
claude/gpt/glm/deepseek with the legacy `claude-gpt` deadline migrated to
`family:claude`/`family:gpt` consult keys, a model-first default for every
Antigravity 429, a lane→model rewrite in release() for model-bearing refusals,
an observed-model fallback on lane-owned retries, and a schedule[0] fallback
for hint-less model windows.

This session's merge (`bd1f5cd`, base = the parallel session's live generation,
pulled and sha-verified `ab671756`) keeps that structure and grafts the
evidence-based pieces it lacked:

1. **argon family key** — `gemini-4-argon*` maps to its own family (independent
   weekly bucket; an argon verdict never consults the gemini `family:` key).
2. **`antigravity_envelope_scope()`** — the verified Cloud Code envelope owns
   the verdict: the bucket verdict (QUOTA_EXHAUSTED + "Individual quota
   reached") arms the durable per-model key even when the envelope names a
   same-family sibling; a cross-family reference defers to the default;
   MODEL_CAPACITY_EXHAUSTED / CAPACITY_EXHAUSTED arm the durable key when the
   envelope carries a reset (RetryInfo / quotaResetDelay) and defer otherwise;
   RATE_LIMIT_EXCEEDED / USER_RATE_LIMIT_EXCEEDED return a new `"account"`
   scope that arms the shared lane breaker and is exempt from the lane→model
   narrowing in release().
3. **Bounded model windows** — a Retry-After-armed model window on a
   mirror-probe lane is re-verified at the lane's cadence
   (`_model_server_not_before` capped at the interval, probe slot at the same
   bound) instead of blind-parking the model for the advertised magnitude;
   `_model_retry_after` advertises the recheck slot while the window is open;
   a hint shorter than the cadence keeps its own deadline. Strict lanes arm
   the model ladder from the first refusal.
4. `model_cooldown` rejections now state that sibling models remain available.

Test reconciliation: restored the lane mirror-probe suite (7 tests) and the
unsafe-interval config test dropped by the parallel cherry-pick resolution;
added envelope-taxonomy, argon-key isolation across restart, account-level
rate-limiter, and huge-hint-bound coverage. Full gates `-RunParallel`:
1855 passed, 30 skipped, 896 subtests.

## Deployment

`outputs/deploy-admission-quota-granularity-20261122.ps1` (four-file swap under
maintenance + request-drain locks, staged compile + merged-module config load,
baseline drift refusals, backup-first rollback): `-DryRun` → `DRY_RUN_OK`, then
`DEPLOY_OK pin=ab671756… → 5162aa6b…`, `config=77b5f465…` (lane JSON unchanged —
the generation reuses `mirror_probe_interval_seconds: 600`),
`QUOTA_GRANULARITY_LIVE_OK interval=600 lanes_open=true`. Backup:
`/opt/cliproxyapi/backups/20261122T021003.449052250Z-admission-quota-granularity-20261122`.

## Controlled acceptance (2026-11-22 ~02:10Z)

- Durable-key drill (backup `antigravity-risk.json.bak-drill-granularity`):
  injected `quota_group_not_before: {"model:gemini-4-argon-high": now+700}` and
  restarted → argon probe refused locally `429 quota_group_cooldown
  retry_after=696` (exact window, zero upstream contact) while
  `gemini-3.8-flash-high` and `claude-opus-5-5-high` both served 200 — the
  no-collective-cooldown contract, live.
- The drill cleanup race (stop-persist overwrote the restored file) itself
  re-proved the key's restart durability; final cleanup was done with the
  service stopped, leaving healthz clean (no hold, no windows).
- Per-model single-shot liveness post-cleanup: all six lane models 200
  (flash/hydrogen/neon/argon/sonnet/opus).

## Rollback

- Remote: redeploy from the backup directory above or rerun the mirror-probe
  transaction's baseline (`ab671756` pin, repo commit `bc9388c`).
- Repo: `git revert bd1f5cd`; the projected pin then matches `bc9388c`'s tree.
