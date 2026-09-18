# 2026-09-19 BWG CPA risk-control closeout

- Scope: `bwg` only. `zz`, OAuth credentials, client keys, and the public random
  path were not changed. No push was performed.
- Source revisions: `ebf2cf5` (auth-safe updater rollback, explicit subagent
  affinity policy, cache/quality tooling) and `91dfaf3` (cache canary token
  budget correction).
- Remote writes: backup-first `cpa_bwg_guardrails.ps1 -Profile bwg -Apply`
  completed twice. The latest backup is
  `/root/cpa-guardrails-backup-20260918T185908.694229837Z`.
- Projected result: `session-affinity-subagents: false` is present in
  `/opt/cliproxyapi/config.yaml`; updater, health checker, and policy checker
  were replaced atomically. The updater now rolls back only its compose image
  declaration and never replays the live `auth/` directory.
- Latest strict doctor: `DOCTOR_CONTRACT_OK`; CPA stayed loopback-only on
  `127.0.0.1:8317`, public Nginx stayed on `0.0.0.0:8443` with one random path,
  no public IPv6 listener, and the 401/404/404 route contract held. The running
  image remained `v7.3.7` with its prior digest.
- Latest projected file SHA-256 values: config
  `cb75f8d6902a7eafbea3cd3a5e8bd587a55323cd5a764875700c8c8292730919`, updater
  `55222286e6235d9714ba61e24b799dd358f8e95def987877a0197a78bf7039f8`, health
  `ad1b212533106b020489f20c136a4b483ab339da236e95f988575b839cdaf7fb`.
- Controlled cache evidence: one `cache-canary` run against `deepseek-flash`
  reported two samples with `input_tokens=3875`, `cache_read_tokens=3712`,
  `cache_miss_tokens=163`, and `hit_ratio=0.9579`; cache-write metadata was not
  returned. This establishes telemetry and a controlled sample only, not a
  provider-wide or long-term cache claim.
- Quality eval boundary: a fresh non-sensitive `quality-eval` was run through
  the tracked SSH session after confirming no prior evaluator remained. It
  returned `UPSTREAM_UNAVAILABLE` (exit 10). No retry was sent; the following
  strict doctor remained `DOCTOR_CONTRACT_OK`. Quality acceptance and model
  identity therefore remain `UNVERIFIED`, not failed local configuration.
- Residual risk: Sol/Terra remain exposed because route removal requires a
  separate product decision. Their relay remains an explicitly documented
  non-sensitive-only HTTP path; this deployment did not alter that boundary.
