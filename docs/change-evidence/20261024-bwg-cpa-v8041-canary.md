# BWG CPA v8.0.41 canary (2026-10-24)

## Scope and authorization

Authorized single-host update on `bwg`: CPA official release `v8.0.41`
(2026-10-23T18:27Z, commit `8289ec4`) pinned to amd64 digest
`sha256:ccbbbfc149fd0d8ab5aede6740be9f8498fb2329eeccfe2f6bb6ee6fd8c748d7`
(convention verified against the in-service `v8.0.40` pin, which matches the
Hub-reported amd64 digest). Release contents: cluster latency telemetry for the
home feature, codex commentary→thinking mapping in the Claude translator,
Claude tool-argument fix, Gemini signature/token-limit/schema fixes, RESP
bounds validation, and stream usage merge token semantics. No OAuth flow
changes. WorkBuddy `0.9.66-local` (`9bc1c97f…`, installed earlier the same
day) rides along pinned; its upstream source `0ac02407` remained HEAD at
execution time, so no plugin rebuild was needed. No OAuth generation probes,
credential edits, realm switching, retry/IP/UA or concurrency changes.

## Transaction

`outputs/deploy-cpa-v8041-canary-20261024.{ps1,sh}` replays the reviewed
v8.0.40 canary contract: compose/plugin pin verification, admission idle
preflight, free-space gate, non-OAuth pre-generation smoke, compose backup,
exclusive drain of `/run/cpa-admission/requests.lock`, atomic image-pin
rewrite, recreate, plugin registration assertion, generation gate, and
lane-hold delta across the restart.

Executed `2026-10-24T03:59:05Z`, exit 0: `OK: updated v8.0.40 -> v8.0.41
digest=sha256:ccbbbfc1… plugin=0.9.66@9bc1c97f…`; backup
`/opt/cliproxyapi/backups/20261024T035901.022271268Z-from-v8.0.40-canary-v8.0.41`.
Earlier attempts surfaced `REFUSE active traffic` (chatgpt lane continuously
inflight) and one `DEFER pre_generation_failed` — both no-mutation refusals;
the pre-generation failure is explained by the catalog incident below.

Rollback: restore the backup `compose.yml` and `docker compose up -d --pull
never`; plugin-independent.

## Catalog drift incident (transient, pre-existing; not caused by the upgrade)

During preflight retries the strict doctor failed
`client-model-catalog-contract` with 29 `MODEL_IDS_UNKNOWN` ids (glm-4.x/5.x
menagerie, deepseek-v4, kimi-k2/k3, minimax, hunyuan, hy3/hy4, step, mimo,
`auto`, `default`). Timeline from CPA logs (all UTC):

- 03:01:46 a management-plane client (172.19.0.1, the operator's panel) PUT
  `/v8/management/config/plugins/configs/workbuddy` (`login_region`
  intl→cn); the config reload's auth-store churn re-delivers a bare plugin
  reconfigure, and the plugin rebuilds `nextPinned` on every configure —
  `models_cn`/`models_intl` pins are NOT sticky (unlike `login_*`), so the
  in-memory pin was wiped.
- 03:02:09 the next `/v1/models` request triggered cn-realm discovery
  (32-model snapshot) and CPA-side promotion of 14 unapproved ids into the
  client catalog.
- 03:49:57 / 03:54:23 the operator restarted CPA; the plugin re-registered
  with the full on-disk config (pins intact on disk throughout), and the
  catalog returned to the 10 approved ids.

Containment held throughout: admission's workbuddy lane admits only
`deepseek-v4.1-flash`, so no unapproved model was routable even while
advertised. Follow-ups in `cpa-workbuddy-plugin.md` §3; a sticky-pin patch or
upstream issue is left as a user decision (low severity: transient exposure,
zero routability, one-command recovery).

## Post-update verification

- Strict doctor (read-only): `DOCTOR_CONTRACT_OK`, exit 0;
  `MODEL_IDS_UNKNOWN=none`; `semantic-policy=OK`; all 12 projection-drift
  entries `MATCH`; `catalog_oauth_missing=none`; lane-hold chatgpt/workbuddy/
  antigravity `OPEN`, claude tombstone inert.
- Cache canary `glm-5.3-flash`: sample 1 miss, sample 2 read 4096/4114 →
  hit ratio 0.9956 — identical to the standing cross-version baseline.
- Non-OAuth stream probe: HTTP 200, first text 2.447 s, total 2.471 s,
  89 events, exact expected text, `finish_reason=stop` + `[DONE]` —
  within the 10-23 baseline (2.453–2.465 s).
- Window since the 03:59:05Z recreate: admission 16/16 upstream results
  successful (chatgpt-oauth 14, passthrough 2), CPA log errors/fatals/panics 0,
  plugin registered `version=0.9.66`. WorkBuddy cn (`eb624ce3…`) and intl
  (`0faaff85…`, `ca64800b…`) auth files present.

## Upstream watch

- PR #6784 (reasoning-only compatible streams missing `response.completed`)
  remains open and is not part of v8.0.41; the DeepSeek terminal-stream defect
  classification from 2026-10-23 stands.
- v8.0.40 known GLM `apply_patch` sanitized-argument guard behaviour is
  unchanged by v8.0.41 release notes.

## Root-cause correction (same day, post-push; supersedes the incident section's mechanism)

The "Catalog drift incident" section above attributed the pin wipe to a bare
reconfigure clearing non-sticky plugin pins. Source-level verification
falsified that reading. Corrected chain, with anchors:

- `PUT /v8/management/config/plugins/configs/<id>` REPLACES the whole plugin
  section: `internal/api/handlers/management/plugins.go` `PutPluginConfig`
  assigns `h.cfg.Plugins.Configs[id] = updated`; `PATCH` is the shallow-merge
  variant.
- The panel's 03:01:46Z PUT body carried `login_region` (the plugin logged
  `login_region=cn applied` at that second) but evidently not the
  `models_cn`/`models_intl` keys: had they been present, the re-registered
  plugin would have kept pinning and the 03:02:09Z discovery would not have
  run. The section was therefore replaced without pins — a partial-form PUT
  against replace semantics.
- v8.0.41 has no bare/default reconfigure path: `defaultRuntimeItemConfig`
  has no callers, and every `callRegister` site passes the full
  `item.ConfigYAML` built from the on-disk section. The plugin's own
  "auth-store churn reconfigure" sticky comment does not apply to this host
  version.
- `internal/watcher/diff/config_diff.go` `BuildConfigChangeDetails` is a
  field allowlist that does not cover the plugins section at all — the
  11:01:46Z reload diff listed only ice-servers/payload rules while the
  workbuddy section was silently rewritten. This is the observability gap
  that made the drop invisible in logs.
- The plugin and host behaved as designed at every layer; admission's lane
  model list (`deepseek-v4.1-flash` only) contained routability throughout.
  The operator restored the pins on disk (present from the 03:52–03:59Z
  window onward, together with `login_region: intl`) and restarted CPA
  (03:49:57 / 03:54:23 / 04:03:05Z); the catalog is the 10 approved ids and
  discovery events ceased.

Consequences: the sticky-pin plugin patch is withdrawn — it would fight
legitimate un-pin edits, since a pinless section is a legitimate expression.
Recovery is to restore the two keys (panel full-form save, a PATCH merge, or
a config edit, all effective via reload/restart). Prevention is a post-save
pin check (grep or the existing doctor `MODEL_IDS_UNKNOWN` gate; no new gate
added). The runbook section was corrected in place; a user-gated upstream
issue draft about the replace semantics + differ blind spot lives in
`outputs/issue-draft-cpa-put-plugin-config-20261024.md`.
