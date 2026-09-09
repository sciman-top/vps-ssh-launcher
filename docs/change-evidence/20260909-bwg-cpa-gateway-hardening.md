# 2026-09-09 bwg CLIProxyAPI gateway hardening (updater fix, rate limits, fail2ban)

## Scope

- Target: profile `bwg` only. Profile `zz` was not connected, checked, or modified.
- Architecture decision confirmed by the user beforehand: the gateway keeps its
  public Nginx TLS entry with the random capability path prefix on 8443; no
  SSH-tunnel-only conversion was applied. All changes below sit on that public
  path or on the updater script.
- Real remote writes: yes — `/opt/cliproxyapi/auto-update.sh`,
  `/etc/nginx/conf.d/cpa-gateway.conf`, and two new fail2ban config files.
  The CPA container itself was not restarted and its `config.yaml` was not
  modified.

## Preflight and rollback

- Fresh read matched the 2026-09-09 risk-hardening receipt: `v7.2.154` image,
  `request-retry: 1`, `max-retry-credentials: 1`, `save-cooldown-status: true`,
  `force-model-prefix: true`, `routing.strategy: fill-first`,
  `allow-remote: false`, and `secret-key` set (60-char hash). The open panel
  question from the assessment is closed: management API is localhost-only with
  a secret configured.
- Backup before any write:
  `/root/cpa-hardening-backup-20260909T134333Z/` (auto-update.sh,
  cpa-gateway.conf, fail2ban jail.d).
- Rollback: restore those files (`nginx -t && systemctl reload nginx`; remove
  the two new fail2ban files and `fail2ban-client reload`; copy back
  auto-update.sh). No container or data rollback is involved.

## Changes

1. `auto-update.sh` health-check hardening:
   - New `allowlist_ok()` guard added to the update health decision
     (`models=200 AND luna smoke OK AND allowlist OK`; failure auto-reverts).
     It checks that no sentinel excluded model family
     (`gpt-5.3/5.4/5.5`, `gpt-5.6-sol/terra`, `gpt-6-`, `gpt-image-`,
     `codex-`) appears among bare (unprefixed) ids in `/v1/models`, closing
     the silent-regression gap for the undocumented per-auth
     `excluded-models` field. Prefixed `r1/*`/`r2/*` ids are deliberately
     ignored.
   - Fixed a latent updater bug found during this run: the client API key
     extraction assumed a quoted hex value, but `config.yaml` stores the
     `api-keys` entry unquoted, so the old `sed` produced an empty key. The
     health check would have returned 401 on the first real update and
     spuriously reverted (first exposure: the 2026-09-14 timer, since every
     prior run had exited at the "already latest"/soak step before the health
     check). The key is now extracted by stripping non-hex characters from
     the line after `api-keys:`, with a new fail-fast guard that logs
     `FAIL: cannot read client API key` instead of a spurious revert.
2. Nginx `cpa-gateway.conf` tightened (public abuse surface):
   - `limit_req` zone `cpa_rl`: `20r/s` -> `10r/s`, location burst `40` -> `20`
     (`nodelay` kept).
   - New `limit_conn` zone `cpa_cc` capped at 6 concurrent connections per IP
     (SSE-friendly; protects upstream quota from a leaked key).
   - `limit_req_status`/`limit_conn_status` set to `429`.
   - Dedicated `access_log /var/log/nginx/cpa_gateway.access.log` (covered by
     the existing `/etc/logrotate.d/nginx` wildcard rule).
3. fail2ban: new `cpa-gateway` jail (filter + jail files in
   `/etc/fail2ban/{filter.d,jail.d}/cpa-gateway.conf`) matching `401|403` on
   the dedicated log: `maxretry=20`, `findtime=600s`, `bantime=86400s`,
   `ignoreip = 127.0.0.1/8 ::1` so local probes and the SSH-tunneled panel can
   never trigger a ban.

## Verification

- `bash -n` on the patched updater; `diff` against backup reviewed.
- Allowlist guard tests: real service (authenticated HTTP 200 + no sentinel)
  OK; synthetic body containing `gpt-5.6-sol` detected; synthetic
  `r1/gpt-5.5-*` correctly ignored (no false positive on prefixed channels).
- Fixed key extraction verified: 48-char key, direct `:8317` probe 200.
- Manual updater run: `SKIP: v7.2.155 is 0d old (<3d soak)` — main path
  regression-free; the first real update leg (with the new guards) is expected
  on the 2026-09-14 timer.
- `nginx -t` passed; reload (not restart) applied. Burst test on the public
  path from the host: 25x200 then 429s under rapid fire, single request 200
  after cooldown — limit semantics confirmed.
- fail2ban: `fail2ban-client -t` OK, reload OK, two jails active
  (`cpa-gateway`, `sshd`). `fail2ban-regex` against the live log matched 28 of
  80 lines (the deliberate 401 burst), 200/429 lines not matched; loopback
  failures stay uncounted by design.
- Final round: container `Up`, nginx/xray/fail2ban/ssh/cron active, timer next
  2026-09-14 04:13 UTC; public probes: with key 200, without key 401, wrong
  path 404; `gpt-5.6-luna` exact-OK smoke passed.

## Redaction incident (disclosed)

- One diagnostic command in this session printed the gateway client API key,
  the GLM plan key, and the management secret-key hash into the local session
  log: the masking pattern assumed quoted values while the config stores them
  unquoted. Nothing left the machine, but the redaction-first standard was
  missed. User-side decision recommended: rotate the gateway client key
  (update `config.yaml` and all devices) and optionally the GLM plan key.

## Remaining / deferred

- `routing.session-affinity` for the `r1` two-key channel stays deferred until
  the r1 station-side issue is resolved (per the risk assessment).
- Per-credential `proxy-url` remains on-demand only, if IP-related risk
  signals ever appear.
- Client-side practice (stable `prompt_cache_key`/session ids, avoid parallel
  saturation of the 5h window) is a user-device matter, not enforceable here.
