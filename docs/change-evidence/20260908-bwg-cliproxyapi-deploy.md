# 2026-09-08 bwg CLIProxyAPI deployment evidence

## Scope

- Target: profile `bwg` only; public address and SSH port redacted. Profile
  `zz` was not connected, checked, or modified.
- Real remote writes: yes — a new, self-contained deployment under
  `/opt/cliproxyapi/`. No existing service, port, cron, or firewall rule was
  modified.
- Goal: deploy the personal API gateway decided on 2026-09-07 (CLIProxyAPI
  over sub2API) with bare-name/prefix isolation:
  1x ChatGPT Plus OAuth + 2 Codex relay stations (3 keys, prefixed `r1`/`r2`)
  + GLM Coding Plan + DeepSeek official, behind one client API key.

## Deployed state

- Layout: `/opt/cliproxyapi/{compose.yml, config.yaml, auth/, plugins/}`;
  `config.yaml` mode 600, `auth/` mode 700.
- Image pinned to `eceasy/cli-proxy-api:v7.2.154` (latest versioned tag at
  deploy time); compose `mem_limit: 512m`; port published as
  `127.0.0.1:8317:8317` only — no public exposure (`ss` verified).
- Risk-control config: `force-model-prefix: true` (unprefixed requests never
  hit prefixed relay credentials), `routing.strategy: fill-first`,
  `request-retry: 3`, management API fully disabled (`allow-remote: false`,
  empty `secret-key`).
- Client API key generated server-side with `openssl rand -hex 24` and written
  into config via in-place sed; the value never appeared in session logs.
- Five upstream placeholders remain in `config.yaml` (`REPLACE_WITH_*` for
  GLM plan key, DeepSeek key, station1 key+endpoint, station2 keys A/B
  +endpoint); relay station 2 has two credentials sharing prefix `r2`.
- Service verified: container up, `127.0.0.1:8317` listening, logs show
  `5 clients (3 Codex keys + 2 OpenAI-compat)`; `/v1/models` returned 21
  models (`r1/*` and `r2/*` auto-discovered from relay endpoints,
  `glm-5.3-flash`, `deepseek-chat`, `deepseek-reasoner`). OAuth `gpt-*`
  models register after the user completes the Codex login.
- Pre-existing host services confirmed untouched: nginx (443, high port),
  xray (two high ports), napcat/astrbot (loopback only), fail2ban, sshd on a
  non-standard high port.

## User-in-the-loop steps outstanding

1. ~~Fill the five upstream keys and two relay base URLs~~ — 4 keys filled
   2026-09-08 (GLM plan, relay station `r1` x2 keys, relay station `r2` x1
   key) via server-side scripted replace; secrets never echoed to session
   logs. DeepSeek key not provided yet; its entry remains a placeholder.
2. ~~Complete ChatGPT Codex OAuth~~ — done 2026-09-08: callback delivered to
   the waiting login process server-side (HTTP 302); credentials saved to
   `auth/codex-*-plus.json` (name redacted), hot-loaded by the running
   service ("auth file changed (CREATE)").
3. Channel smoke tests 2026-09-08:
   - `gpt-5.6-luna` (OAuth): OK.
   - `glm-5.3-flash` (GLM plan): OK (reasoning model; needs max_tokens headroom).
   - `r1/gpt-5.6-luna` (ai.input.im, 2 keys round-robin): OK.
   - `r2/*` (codex.ciii.club): station-side failure. Isolated by direct
     probe from the VPS: `/v1/models` works with the key, but
     `/v1/responses` returns "Upstream access forbidden, please contact
     administrator" — station upstream authorization problem, not a CPA or
     config issue. Awaiting station admin fix.
   - `deepseek-*`: not testable until the key is filled.
4. The login container was removed afterwards; only `cli-proxy-api` remains,
   and port 1455 is closed again.

## Model allowlist restriction (same day)

- Requirement: the ChatGPT Plus OAuth channel may serve only
  `gpt-5.6-luna`; the GLM plan channel only `glm-5.3-flash`.
- OAuth: `excluded-models` (wildcard globs covering codex-*, gpt-5.3/5.4/5.5,
  gpt-5.6-sol/terra, gpt-6-*, gpt-image-*) added as a top-level field of the
  OAuth auth JSON. This per-auth-file field is not in the official docs but
  was verified empirically: hot reload accepted it, and `/v1/models`
  unprefixed catalog reduced to `deepseek-chat`, `deepseek-reasoner`,
  `glm-5.3-flash`, `gpt-5.6-luna`. Calling excluded `gpt-5.6-sol` now
  returns HTTP 400; `gpt-5.6-luna` still answers. Pre-edit auth file backed
  up outside the watched auth dir (`auth` dir would otherwise re-ingest it).
- GLM: restriction already inherent — openai-compatibility providers only
  register declared `models:` entries, and only `glm-5.3-flash` is declared.
- Rollback: remove the `excluded-models` field from the auth JSON (or restore
  the kept backup) and let the watcher reload.

## Nginx TLS reverse proxy for multi-device access (same day)

- New file `/etc/nginx/conf.d/cpa-gateway.conf` (self-owned; v2ray-agent's
  managed `alone.conf`/`subscribe.conf` untouched): server block on public
  `8443 ssl`, server_name `fq.sciman.top`, single regex location
  `^/<random-8-byte-hex>/v1/(.*)$` proxying to `127.0.0.1:8317/v1/…`;
  everything else returns 404. Hardening: `limit_req` 20r/s burst 40,
  `proxy_buffering off` + 300s timeouts for SSE, `client_max_body_size 32m`.
  The random path prefix is a capability URL; value redacted here, delivered
  to the user in session.
- Certificates: the conf deliberately references
  `/etc/v2ray-agent/tls/zz.sciman.top.{crt,key}` — the wildcard `*.sciman.top`
  cert that the acme.sh renewal hook actually maintains (valid to 2026-10-13,
  auto-redeploys on renewal). Verified with a non-`-k` curl: TLS chain
  validates, gateway answers 401 without key.
- Pre-existing issue discovered, and FIXED with user authorization (same
  day): the acme.sh renewal hook had been re-registered (between April and
  July 2026, cause: the hostname `zz.sciman.top` belongs to the user's other
  VPS, and the hook was pointed at `zz.*` file names during that box's
  setup) to deploy to `/etc/v2ray-agent/tls/zz.sciman.top.{crt,key}`, while
  `subscribe.conf` and the xray configs consume `fq.sciman.top.{crt,key}`.
  The `fq.*` copies expired 2026-07-15, so xray and the subscription page
  served an expired certificate since July. Also `Le_ReloadCmd` was empty.
  Fix applied: expired files backed up to `/root/tls-backup-20260908/`;
  `acme.sh --install-cert` re-registered to deploy to `fq.*` with
  `--reloadcmd "systemctl reload nginx && systemctl restart xray"`;
  xray restarted and both its TLS ports verified serving the new cert
  (notAfter 2026-10-13); the gateway conf now also references `fq.*`;
  stale `zz.*` files removed. Next renewal 2026-09-12 will deploy and
  reload automatically.
- Rollback: delete `/etc/nginx/conf.d/cpa-gateway.conf` and
  `systemctl reload nginx`. TLS hook rollback: restore files from
  `/root/tls-backup-20260908/` and re-run `acme.sh --install-cert` with the
  previous paths.

## Weekly auto-update with soak/health-check/rollback (same day)

- Upstream has no stable/pre-release channel (all GitHub releases marked
  stable, near-daily cadence), so "stable" is implemented as a policy:
  versions must be at least `SOAK_DAYS=3` days old before adoption.
- New `/opt/cliproxyapi/auto-update.sh` (mode 700) + systemd
  `cliproxyapi-update.service/.timer` (Mondays 04:00 UTC + 30min jitter,
  `Persistent=true`). Flow: read pinned tag -> fetch latest versioned tag ->
  skip if same or younger than soak -> backup `config.yaml` + `auth/` to
  `backups/<ts>-from-<old>/` (keep 4) -> rewrite tag in `compose.yml`,
  pull+up -> health check = `/v1/models` 200 AND a real `gpt-5.6-luna`
  smoke request (3 tries) -> on failure auto-revert tag and `up -d`.
  Append-only log at `/opt/cliproxyapi/auto-update.log`.
- Verified: `bash -n` clean; timer enabled (next 2026-09-14 04:17 UTC);
  manual run logged `OK: already latest (v7.2.154)`.
- Manual ops: read `auto-update.log`; force update by lowering SOAK_DAYS;
  disable via `systemctl disable --now cliproxyapi-update.timer`.
- Note: old versioned images accumulate for rollback (each update keeps the
  previous tag); prune manually if disk fills.

## Cross-network acceptance run (same day, from a local Windows machine over
the public internet)

Via `https://fq.sciman.top:8443/<prefix>/v1` with the gateway key:

1. `GET /v1/models` — 200, 0.43 s.
2. `gpt-5.6-luna` (ChatGPT OAuth) — replied `OK`.
3. `r1/gpt-5.6-luna` (ai.input.im relay) — replied `OK`.
4. `glm-5.3-flash` (GLM plan) — replied `OK`.
5. SSE streaming on `gpt-5.6-luna` — chunks flowing through nginx
   (`proxy_buffering off` confirmed in practice).
6. Restricted model `gpt-5.6-sol` — HTTP 400 (allowlist enforced on the
   public path).

Not exercised in practice (known gaps): the auto-update flow's actual update
leg (engages on the first release older than the 3-day soak; revert leg is
scripted but untriggered), the 2026-09-12 acme renewal cycle (the
deploy+reload leg was exercised manually during the hook fix), interactive
management-panel login and a second device using the public URL (user-side),
`deepseek-*` (key not provided) and `r2/*` (station-side outage).

## Rollback

- `cd /opt/cliproxyapi && docker compose down` and `rm -rf /opt/cliproxyapi`
  removes only this deployment. No other host state must be reverted.
