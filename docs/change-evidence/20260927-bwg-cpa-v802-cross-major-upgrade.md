# BWG CPA v8.0.2 cross-major upgrade acceptance

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change (user, 2026-09-27): upgrade CPA v7.3.17 -> v8.0.2 and
  review/optimize the configuration, with real-traffic acceptance at the end.
- This is a manual cross-major hop. The standing updater policy is
  `cross-major NEVER` (auto-update.sh never crosses major/minor on its own);
  this round is an explicit user-authorized policy bypass, compensated by a
  binary canary before the swap and a 72h doctor watch afterwards.
- Releases involved: v8.0.0 (v8 configuration API + OAuth routes, config
  layout v8, PR #6154), v8.0.1 (drop obsolete legacy keys during v8
  migration), v8.0.2 (preserve unknown legacy sections as comments during v8
  migration). Rides along the skipped v7.3.18-v7.3.20 fixes: invalid_grant
  credential handling with exponential backoff, Claude cloaking default,
  Codex service tier support, native Responses thinking logs.

## Config migration decision (source-verified + empirical)

- v8 keeps reading the legacy (v7) config spelling: `internal/config/parse.go`
  still normalizes `oauth-excluded-models`, `internal/config/config.go` keeps
  `OpenAICompatibility yaml:"openai-compatibility"` and the codex-api-key /
  claude-api-key legacy key lists; the v8.0.2 `config.example.yaml` compat
  notes state legacy-only fields remain readable until a successful
  `/v8/management` write.
- The migration/rewrite lives on the config WRITE path only
  (`internal/config/config_yaml.go`: prune/merge runs when persisting). Our
  management panel is not exposed through nginx and nothing calls
  `/v8/management`, so no writer exists.
- Empirical: `config.yaml` sha256 `4c0e6524…e26` identical before and after
  the upgrade; post-upgrade startup log has zero migration/legacy warnings.
- Config optimization outcome: zero changes. The live config already matches
  every `cpa_policy.py` assertion (routing, cooldown 60s, error-log caps,
  usage statistics, oauth exclusions, provider excluded-models) and already
  pins `redis-usage-queue-retention-seconds: 3600`; the v8 key set adds
  nothing the deployment needs (`commercial-mode` would kill the log signals
  the doctor depends on and stays off).

## Timeline

- Pre-flight (read-only): strict doctor `DOCTOR_CONTRACT_OK`; container
  `v7.3.17@sha256:a1dffb9c…` running; 32G free; backup chain intact; auth
  dir = 1 OAuth json (compose mounts `config.yaml`, `auth/`, `plugins/` into
  the container, so a recreate cannot lose credentials).
- Parallel-session round at 12:49Z (before this task) had re-projected the
  six deployed files and restarted container + admission; all six hashes
  verified == repo HEAD, so nothing needed absorbing.
- Canary: v8.0.2 image pulled by digest
  `sha256:b8306b3965755908e1dfcfe0fa114d8d3d4a3df2769ef5e5e3c62cfdf56ee315`,
  binary extracted into a fresh fixture dir and run under
  `unshare --mount --net --fork` with the production `cpa-acceptance.py` /
  `cpa-update-acceptance.py` / `cpa-health.py` / `auto-update.sh` unchanged:
  overload -> 503 `server_is_overloaded` with exactly 1 upstream call;
  cooldown -> 503 with 0 upstream calls; after 62 s -> 200
  `response.completed` in the same process; bare catalog exact-match (14 IDs)
  plus sol smoke 200/`gpt-5.6-sol`/stop; `cpa-health.py generation` ->
  `HEALTH_OK`; update scenarios `transient` exit 10 (UNVERIFIED kept) and
  `success` exit 0; final marker `ACCEPTANCE_RESULT=PASS`.
- Upgrade at 13:50Z: backup dir
  `backups/20260927T135014.266190377Z-manual-v8.0.2-from-v7.3.17/`
  (compose.yml pinning v7.3.17 -> stays in the prune-protected rollback set,
  config.yaml + pre sha), compose image pin swapped to
  `eceasy/cli-proxy-api:v8.0.2@sha256:b8306b39…`, `docker compose up -d
  --pull never`. Container recreated `v8.0.2@sha256:b8306b39…`
  `restart=0` (banner `Version: v8.0.2, Commit: 4a2c818`).
- Post-upgrade gates: `cpa-health.py readiness` -> `HEALTH_OK`;
  `cpa-health.py generation` -> `HEALTH_OK` (first generation through v8.0.2,
  sanctioned non-OAuth gate target); strict doctor -> `DOCTOR_CONTRACT_OK`
  (8x projection MATCH, cooldown none, luna available, oauth aliases both in
  catalog, model-substitution 0, auth/error-dump modes OK, all syntax OK).

## Real-traffic acceptance (public 8443 listener -> nginx -> admission 8318 -> CPA v8.0.2)

- OAuth lane streaming: `gpt-6-luna` chat completions, stream=true -> 200,
  TTFB 4.68s, total 6.46s, 60 SSE chunks, `finish_reason=stop`, responded
  model `gpt-6-luna` (progressive framing confirmed: TTFB << total; the
  admission SSE contract on the v8 upstream holds).
- API-key lane streaming: `glm-5.3-flash` stream -> 200, TTFB 0.88s, total
  2.51s, 117 chunks, `finish_reason=length` (max_tokens bound).
- Plain probes: `glm-5.3-flash` 200/1.55s, `deepseek-v4.1-flash` 200/1.69s
  (both responded-model correct, finish `length` under the 16-token cap).
- Concurrency: 3 parallel `glm-5.3-flash` requests -> all 200 (1.50-1.79s);
  admission never fail-closed; `cpa-admission` journal clean over the probe
  window.
- The only errors in the container log are the pre-existing ai.input.im
  upstream fault window (`gpt-6-sol` 502 "Upstream access forbidden",
  qq-codex-bot client, 8.163.x.x) — upstream-side, present before the
  upgrade, unchanged after.

## Residual watch

- 72h watch (until ~2026-09-30): doctor oauth/catalog segments after each
  timer run; the v8 config API + query-param OAuth routes are the one new
  variable on this deployment. OAuth refresh point ~2026-09-27/28 overlaps
  the watch window — attribute refresh failures to v8 only after the token
  itself is confirmed healthy.
- auto-update.sh now treats v8.0.2 as current: same-minor v8.0.x patches
  auto-apply after the 72h soak; minor/major remain visibility-only.
- Rollback entry: `cp -a` the backup compose.yml back and
  `docker compose up -d --pull never` (v7.3.17 image retained under digest
  pin); config.yaml is byte-identical so no config rollback is needed.
- Reference checkout `D:\CODE\external\vps-ssh-launcher-references` still
  pins v7.3.17 source; refresh to v8.0.2 needs explicit user authorization
  for the external root.
