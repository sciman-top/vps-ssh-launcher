# 2026-09-19 BWG CPA per-model observability and error-dump permissions

## Scope

- Target: `bwg` only. The `zz` profile was not connected or modified.
- No client key, provider key, OAuth file, subscription URL, or public random
  path was printed, rotated, or committed.
- The five-model topology was retained; Sol/Terra were not disabled.

## Local change

- `cpa-health.py generation-all` now emits one redacted line per exposed model:
  `model`, HTTP-like `status`, `latency_ms`, `finish`, and `error_class`.
- Explicit `generation-all` continues after a single route failure so the
  complete matrix is visible, while updater `generation`, `quality-canary`, and
  existing programmatic calls retain stop-on-first-failure behavior.
- No retry was added. Each explicit matrix route still receives at most one
  generation request.
- `auth/logs` is now guarded as mode `0700` and `error-*.log` as `0600` by the
  updater, strict doctor, and backup-first apply path. Dump contents are never
  read for permission repair or printed.
- README and focused regression tests document and protect these contracts.

## Repository verification

- Full gate: `128 passed, 1 skipped, 143 subtests passed`.
- Bandit, Ruff, Ruff format, mypy, Python compilation, updater Bash syntax,
  embedded doctor Bash syntax, and `git diff --check` passed.

## BWG projection and host proof

- Apply used the existing backup-first guardrail transaction.
- Host backup:
  `/root/cpa-guardrails-backup-20260919T131311.205636689Z`
- Fresh post-apply strict doctor: `DOCTOR_CONTRACT_OK`.
- Container: `running`, `restart=0`, image remains pinned to
  `v7.3.7@sha256:4ce7b1b5...`.
- `error-dump-permissions=OK`; CPA is loopback-only on `127.0.0.1:8317`,
  public Nginx remains the only `8443` listener, and the 401/404/404 route
  contract remains intact.
- Post-projection non-secret hashes:

  ```text
  /opt/cliproxyapi/config.yaml   316bc3909875170ac379f55deaf068554ba2bec7de1742fbc4cc752d3c34d5b2
  /opt/cliproxyapi/auto-update.sh d203e972576224fd758217163403643fa7b3ead4a1001dd0f3a30aeeb706b61e
  /opt/cliproxyapi/cpa-health.py 79c16ea31a596ae85deeffaaeabcf658f0fd3d4c07cc4dcece1671be32d87c6f
  ```

## Fresh low-frequency acceptance

- Explicit `generation-all` completed all five single-shot routes:

  ```text
  gpt-5.6-luna     200 finish=stop latency_ms=938
  gpt-5.6-sol      200 finish=stop latency_ms=2251
  gpt-5.6-terra    200 finish=stop latency_ms=9335
  glm-5.3-flash    200 finish=stop latency_ms=3040
  deepseek-flash   200 finish=stop latency_ms=1026
  ```

  Result: `HEALTH_OK`, five of five routes returned valid JSON with the
  expected model and `finish=stop`. This is a current low-frequency acceptance
  window, not a guarantee of long-term provider stability.
- Explicit DeepSeek cache-canary returned two samples with
  `input_tokens=3875`, `cache_read_tokens=3712`, `cache_miss_tokens=163`, and
  `hit_ratio=0.9579`; cache-write metadata was unavailable. This is isolated
  provider telemetry, not a claim about OAuth, Sol/Terra, GLM, or long-term
  aggregate hit rate.
- Updater `--check` found no newer release satisfying the existing dual-source,
  same-patch-line, 72-hour maturity rule; the pinned image was retained.

## Current risk boundary

- The current 24-hour access-log snapshot showed `200=1064`, `503=433`,
  `502=26`, and `429=5`. The five `429` entries had no upstream status and
  matched the local Nginx rejection marker; the `502/503` entries are upstream
  failures. This is an observation window, not provider ban/quota proof.
- The recent error-dump scan found no retained overload request files or
  `server_is_overloaded` markers. Provider-side account limits, bans, and
  model quality remain outside the evidence this gateway can prove.
- `request-retry=0`, `max-retry-credentials=1`, `save-cooldown-status=false`,
  `session-affinity-subagents=false`, and the existing Nginx ingress limits
  remain unchanged. `support-prompt-cache-key` remains disabled; no threshold
  was invented from one cache sample.

## Rollback

Restore the projected CPA and guardrail files from the host backup above,
restart `cli-proxy-api`, reload Nginx/fail2ban as needed, and rerun the strict
BWG doctor. Git rollback alone does not restore remote runtime state.
