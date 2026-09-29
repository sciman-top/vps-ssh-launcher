# BWG CPA v8.0.4 upgrade acceptance

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change (user, 2026-09-30): upgrade CPA v8.0.2 -> v8.0.4 and
  review/optimize the configuration.
- This is a **same-minor patch hop** (v8.0.2 -> v8.0.4, skipping v8.0.3), not a
  cross-major hop. The standing updater policy is `cross-major NEVER` with
  same-minor patches auto-applied only after a 72h soak; v8.0.4 was published
  2026-09-29T00:12:47Z and had soaked ~16h, so this round is an explicit
  user-authorized **soak-window bypass** (not a boundary crossing).
- Releases involved: v8.0.3 (warn on unrecognized sections during v8 migration;
  pluginhost disabled-state fix; SSE trailing-newline fix; Gemini thought
  signatures), v8.0.4 (preserve unknown nested fields as comments during v8
  migration; Codex empty-argument normalization; Claude MCP alias remap;
  request IDs switched to UUIDv7).

## Config migration decision (source-verified + empirical)

- v8.0.4's own `config.example.yaml` states: legacy configuration files and
  `/v0/management` remain supported; `GET` requests and `config-version: 8`
  alone do **not** migrate a legacy file — only a successful configuration
  write through `/v8/management` does. Both v8.0.3 and v8.0.4 changelog entries
  touch the migration path only.
- The management panel is not exposed through Nginx and nothing calls
  `/v8/management`, so no writer exists on this deployment.
- Empirical: `config.yaml` sha256 `a127fda1…b142` **identical before and after**
  the upgrade; the post-upgrade startup log contains **zero**
  migration/legacy/unrecognized/deprecated lines (grep count = 0), and zero
  `[error]`/`[fatal]`/panic lines.
- Config optimization outcome: **zero changes**. The live config already
  satisfies every `cpa_policy.py` assertion (`POLICY_OK`), and the v8 key set
  adds nothing this deployment needs:
  - `server.trusted-proxies` — would only change which IP CPA records; the
    doctor's traffic analysis reads the Nginx access log, so there is no gain,
    and it would introduce forwarded-header trust semantics. Not adopted.
  - `server.commercial-mode` — would disable the high-overhead request logging
    the doctor's log segments depend on. Must stay `false`.
  - `server.discovery` — mDNS cannot cross the default Docker NAT bridge.
  - `plugins` / `observability.pprof` — unused; off is correct.
  - `multimedia.disable-image-generation` — image routes are deliberately
    catalog-exposed; left at default.
- Optional hardening candidate (presented, **not applied**):
  `remote-management.allow-remote: true -> false`. The doctor accepts either
  `DISABLED` or `LOOPBACK_KEYED`, the management API is only ever called from
  `127.0.0.1`, and Nginx carries no management route — so this would be pure
  defense-in-depth. Deferred pending explicit user approval because it is a
  behavior change requiring a container restart.

## Timeline

- Pre-flight (read-only, 2026-09-29T16:15Z): strict doctor
  `DOCTOR_CONTRACT_OK` + `POLICY_OK`; container `v8.0.2@sha256:b8306b39…`
  running `restart=0`; 32G free; 10 backup dirs intact; config sha
  `a127fda1…b142`; `cpa-health.py readiness` -> `HEALTH_OK`.
- Target digest resolved from Docker Hub: v8.0.4 =
  `sha256:72205ea2dff7e3e3ef23b03de4e17b169ff7449c02b12f2924a3d4d3eee68b7d`.
- Backup at 16:15Z:
  `backups/20260929T161546.983025771Z-manual-v8.0.4-from-v8.0.2/`
  (compose.yml `c87c6b27…`, config.yaml `a127fda1…`, `PRE_SHA256`).
- Image pulled by digest and verified; compose image pin swapped to
  `eceasy/cli-proxy-api:v8.0.4@sha256:72205ea2…` via an atomic
  read/verify-count/`os.replace` edit; `docker compose config --quiet` OK;
  `docker compose up -d --pull never` recreated the container.
- Container recreated at 16:16:41Z on `v8.0.4@sha256:72205ea2…`, `restart=0`,
  banner `Version: v8.0.4, Commit: d33f63f, BuiltAt: 2026-09-29T00:13:14Z`.
  Startup: `API server started successfully on: 0.0.0.0:8317`, 6 clients
  (1 auth entry + 5 OpenAI-compat) — same shape as pre-upgrade.
- Post-upgrade gates: `cpa-health.py readiness` -> `HEALTH_OK`;
  `cpa-health.py generation` -> `HEALTH_OK` (non-OAuth gate target
  `glm-5.3-flash`); strict doctor -> `DOCTOR_CONTRACT_OK` + `POLICY_OK`, all
  9 projected files `MATCH`, `drift` section clean, `management-remote=
  LOOPBACK_KEYED`, `cpa-port-binding=exact-loopback-only`,
  `nginx-no-management-route=OK`.

## Real-traffic acceptance (public 8443 listener -> nginx -> admission 8318 -> CPA v8.0.4)

- Plain probe: `glm-5.3-flash` -> 200, 2161ms, responded model `glm-5.3-flash`,
  `finish=stop`, content contract OK.
- Streaming probe: `glm-5.3-flash` stream=true -> 200, **TTFB 726ms**, total
  1468ms, 27 SSE chunks, `finish=stop` (progressive framing confirmed:
  TTFB << total — the admission SSE contract holds on the v8.0.4 upstream).
- Non-OAuth lane only; the single subscription OAuth account was not consumed
  by the acceptance probes or by the scheduled gate.

## Residual watch

- 72h soak that the standing policy would have required is **not** satisfied at
  install time; the practical compensation is the post-upgrade strict doctor
  plus this public-path acceptance, and continued observation on the next
  `cliproxyapi-update.timer` runs (they will report `current=v8.0.4
  target=v8.0.4` until a newer mature release appears).
- The v8.0.3 "warn on unrecognized sections during v8 migration" feature is the
  one new variable on this deployment; zero such warnings were emitted, but the
  next few startup/doctor cycles should be checked for it.
- Rollback entry: restore `backups/20260929T161546.983025771Z-manual-v8.0.4-from-v8.0.2/compose.yml`
  over `/opt/cliproxyapi/compose.yml` and run
  `docker compose up -d --pull never` (the v8.0.2 image remains locally
  available under its digest pin). `config.yaml` is byte-identical, so no
  config rollback is needed.

## Observations carried forward

- The recreate window cost two client-visible 503s (16:16:41Z, ~1.4s each)
  from a client that retries every ~1s. Expected for a container recreate, but
  worth noting for future maintenance windows.
- The startup log shows the management control-panel asset being refreshed from
  GitHub (`management asset updated successfully`) because
  `disable-control-panel` is at its default. The panel route is loopback-only
  and Nginx does not proxy it, so there is no exposure; listed only as a
  candidate if the deployment later wants to remove unused surface.
