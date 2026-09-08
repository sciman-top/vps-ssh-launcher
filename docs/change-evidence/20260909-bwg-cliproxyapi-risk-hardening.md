# 2026-09-09 bwg CLIProxyAPI risk hardening and update check

## Scope

- Target: profile `bwg` only. Profile `zz` was not connected, checked, or
  modified.
- The worktree was clean at the start. No credentials, OAuth files, gateway
  path, upstream URLs, model keys, or Nginx/Xray configuration were changed.
- The binary update leg was checked but intentionally remained a no-op because
  `v7.2.154` was already the current versioned image.

## Preflight and rollback

- Fresh read showed `eceasy/cli-proxy-api:v7.2.154` running with the host port
  restricted to `127.0.0.1:8317`.
- Before the write, a host-side backup was created at
  `/root/cliproxyapi-before-risk-hardening-20260908T163435Z/`. It contains the
  pre-change `config.yaml`, `compose.yml`, `auto-update.sh`, and `auth/`.
- Rollback is limited to restoring those files from the backup and recreating
  the `cli-proxy-api` Compose service. No other host service requires rollback.

## Applied changes

- `remote-management.allow-remote`: `true` -> `false`.
- Global `request-retry`: `3` -> `1` additional retry round.
- Added `max-retry-credentials: 1` to cap new credentials tried in a retry
  round.
- Added `save-cooldown-status: true` so auth cooldown state can survive a
  container restart.
- Hardened the existing auth file permissions to `0600`; the config remains
  `0600` and the auth directory remains `0700`.
- The candidate YAML was parsed successfully before atomic replacement. The
  single CPA container was then recreated to guarantee that the new config was
  loaded; it returned to `/v1/models` readiness without a restart loop.

## Verification

- `bash -n /opt/cliproxyapi/auto-update.sh`: passed.
- Manual updater run: `OK: already latest (v7.2.154)`.
- Local `/v1/models`: HTTP 200, 22 models.
- Existing TLS reverse-proxy `/v1/models`: HTTP 200, 22 models.
- `gpt-5.6-luna`: HTTP 200, exact-OK smoke passed.
- `glm-5.3-flash`: HTTP 200, exact-OK smoke passed.
- `r1/gpt-5.6-luna`: one controlled smoke request did not return a captured
  final result; a recent CPA `408` signal was observed. It remains
  unaccepted and was not retried.
- `r2/*` was not probed because the prior receipt identified a station-side
  403; DeepSeek was not probed because its key was still incomplete.
- After recreation the container was `running`, restart count `0`, and the
  host services `nginx`, `xray`, `fail2ban`, `ssh`, and `cron` were active.
- Recent aggregated logs showed no `429`, `500`, `502`, `503`, `504`,
  `rate.limit`, or `quota` signal in the post-change observation window.
- A two-request controlled prompt-cache probe used a synthetic stable
  `prompt_cache_key`; the runtime did not expose usable `cached_tokens`
  telemetry, so this receipt makes no claim of measured upstream cache-hit
  improvement. The existing `fill-first`, prefix isolation, and default
  `identity-confuse=false` behavior were preserved for continuity.

## Freshness and remaining risk

- The official release page showed `v7.2.154` as the latest release at the
  time of this run. The next scheduled updater remains protected by its
  three-day soak policy and health/revert flow.
- A successful HTTP response proves the gateway path and the tested channels,
  not immunity from provider bans, quotas, quality degradation, or future
  upstream policy changes. `r1` timeout and the known `r2` station failure
  remain separate follow-up items.
