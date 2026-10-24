# BWG CPA OpenCode provider: session header and LongCat model (2026-10-24)

## Scope

The user authorized repairing and optimizing the `opencode` entry in the CPA
`openai-compatibility` provider list. Only that provider block was edited. No
OAuth credential and no API key was created, deleted, rotated, disabled,
migrated, or exported. Other compatibility providers (`ai.input.im`,
`http-bridge-8003`, `zhipu-plan`) and all OAuth lanes were left untouched.

## Root cause of `400`

The configured base URL was already correct for the Go plan:
`https://opencode.ai/zen/go/v1`. CPA simply did not send the
`x-opencode-session` header that OpenCode Go requires for routing, so every
generation failed with:

```text
400 MissingSessionID: Request is missing x-opencode-session and cannot be routed efficiently
```

## Applied change

```yaml
        - name: "opencode"
          base-url: "https://opencode.ai/zen/go/v1"
          keys:
            - api-key: "<redacted>"
          disabled: false
          headers:
            x-opencode-session: "cpa-bwg-gateway-01"
          models:
            - name: "beam-free"
            - name: "longcat-2.5-preview-free"
```

`longcat-2.5-preview-free` is not returned by the provider's `/models`
endpoint, so it can only be declared manually. It is nonetheless a valid
Go-plan model.

## Verification

- `GET /v1/models` on the CPA gateway now lists `longcat-2.5-preview-free`
  and `beam-free` alongside the pre-existing models.
- `POST /v1/chat/completions` for `beam-free` through CPA returned `200`,
  which proves the session header reaches the upstream.
- `POST /v1/chat/completions` for `glm-5.3-flash` returned `200`; the
  managed `zhipu-plan` provider is intact.
- `longcat-2.5-preview-free` alternates between `200` and upstream
  `429 Endpoint is unavailable` on both direct and CPA paths. This is
  OpenCode-side capacity/rate limiting of the free preview model, not a CPA
  configuration defect; `longcat-2.0` is the paid fallback.
- `accepted`: one bounded generation per model above. No retry loop, no
  concurrency increase, no credential or IP change.

## Rollback

Both snapshots are mode `0600` on the host:

```text
/opt/cliproxyapi/config.manual-snapshot-20261024T135445Z-pre-opencode-session.yaml
/opt/cliproxyapi/config.manual-snapshot-20261024T135445Z-post-opencode-session.yaml
```

Rollback copies the `pre` snapshot over `config.yaml`, then restarts
`cli-proxy-api`. Git rollback does not replace this host-side restore.

## Durable registration

`opencode.ai` was retired on 2026-10-18 (commit `c894a02`), while slot 2 and
the `providers.env` `BASE_URL_2`/`API_KEY_2` mapping were intentionally kept so
the channel could be re-activated without renumbering. The operator re-added the
credential on 2026-10-24, so slot 2 is now re-declared:

```json
{
  "slot": 2,
  "name": "opencode",
  "host": "opencode.ai",
  "path": "/zen/go/v1",
  "headers": {"x-opencode-session": "cpa-bwg-gateway-01"},
  "models": [
    {"name": "beam-free", "alias": "beam-free", "max-context-length": 1048576},
    {"name": "longcat-2.5-preview-free", "alias": "longcat-2.5-preview-free", "max-context-length": 1000000}
  ],
  "optional_models": ["longcat-2.5-preview-free"]
}
```

`opencode.ai` is removed from `retired_hosts`. `longcat-2.5-preview-free` is
declared optional because OpenCode's `/models` endpoint omits it while the
generation path still serves it. Context windows come from the models.dev
catalog (`beam-free` 1,048,576; LongCat 2.5 Preview 1,000,000).

Verification: `_route_manifest_issues` is empty, `cpa_policy.py` returns
`POLICY_OK` against the shaped live config, and the full gate run passes
(739 tests / 369 subtests; bandit, ruff check/format, mypy).

## Error classification

At 22:24:35 local the upstream answered `403 error code: 1010` for both
OpenCode models. CPA classified that 403 as `quota` and put the single
credential into a 20-minute cooldown, which removed `beam-free` from
`/v1/models` and returned `503 auth_unavailable` / `429 model_cooldown` until
the cooldown expired. Direct calls with the same key and session header still
returned `200`, so this was an upstream anti-abuse/WAF response, not a
credential or routing defect.

The provider now pins a scoped-error rule so that specific WAF signature fails
fast instead of parking the whole channel:

```yaml
          request-scoped-errors:
            - status: 403
              match:
                - "1010"
              action: "stop"
```

The rule is a live override. `-Apply` deep-copies the existing entry, so it
survives re-projection, and `cpa_policy.py` permits the field.

## Residual work

The full `-Apply` / doctor / push closeout is **not** done:

- The deployed admission pin is still `ab7748bd…` while HEAD's
  `cpa-admission.py` is `0d5e8e43…`, so `-Apply` refuses with
  `admission_integrity_pin_mismatch` until the separate admission deploy
  rotates the pin.
- `-Apply` also requires a clean tree, and it restarts CPA plus the admission
  service; that window belongs to the pending admission change.

Once that deploy lands, run `-Apply`, then the doctor, then push.

## Live re-check queue

Re-ran one sequential generation per model (no concurrency) after the cooldown
window ended; a concurrent pair of requests is the shape that triggered the
1010 response, so acceptance must not re-create that burst.

## Final state

- `cpa_policy.py` on the live config: `POLICY_OK`.
- `cpa-health.py readiness`: `HEALTH_OK`.
- `GET /v1/models`: 12 ids, including `beam-free` and
  `longcat-2.5-preview-free`.
- `beam-free` through the full production path
  (`127.0.0.1:8318` → CPA → OpenCode): HTTP 200, `finish_reason=length`,
  `billing_source=go`, `cost=0`.
- Streamed acceptance on the same path: `model=beam-free`,
  `finish_reason=stop` → `LIVE_ACCEPTED`.
- `longcat-2.5-preview-free` single shot: upstream HTTP 429
  `Endpoint is unavailable` — capacity flakiness of the free preview model,
  and specifically **not** `400 MissingSessionID`, which proves the session
  header is on the wire. Per the runbook contract the 429 was not retried.
- Projected files on the host match the repo except `cpa-admission.py`, which
  is the pending unrelated admission change (`ab7748…` deployed vs `0d5e8e43…`
  at HEAD). That single file is what still blocks `-Apply` / doctor / push.

## Projection simulation

Replaying `build_provider`/`model_entry` from
`scripts/remote/cpa-guardrail-apply.sh` against the checked-in manifest,
`providers.env` slot 2, and the live entry (which `-Apply` deep-copies) yields
a provider dict identical to the live one, including key order. `-Apply` is
therefore a semantic no-op for this provider; the only remaining `-Apply`
effect would be the unrelated admission projection.

## Addendum (2026-10-24 ~23:20 +08): durability chain completed

The "deliberately not done" durability work above landed as commits
`4ff9a4b` / `e24e691` / `ba33a98` (slot 2 reactivation, 1010 scoped-error
rule, reviewed gateway session id) plus the absorbed admission/triage
generation (`833aa74`, `d7b83d0`). This addendum records the remote
transaction chain that made it live; both steps were executed by a second
session working the same tree (verify-absorb mode, no rework).

1. **Admission rotation** — plain `-Apply` correctly REFUSED with
   `admission_integrity_pin_mismatch` (rotation is not a bulk-projection
   concern). Ran the reviewed transaction as
   `outputs/deploy-admission-protocol-observability-20261024b.ps1`: identical
   to the committed `...20261024.ps1` except the baseline pin is sourced from
   `833aa74^` (the pre-rotation generation, matching the live host) because
   HEAD already carries the new pin. First attempt REFUSED via the idle gate
   (`active traffic ['chatgpt-oauth','zhipu-coding-plan']`) — fail-closed as
   designed; retry on a quiet window deployed cleanly:
   `OK deployed pin=0d5e8e43…` with backup
   `/opt/cliproxyapi/backups/20261024T150951.154020490Z-admission-protocol-observability`,
   `HEALTH_OK lanes=4 retired_readers=0`.
2. **`-Apply`** — `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
   `BACKUP_DIR=/root/cpa-guardrails-backup-20261024T151237.143062914Z`.
   This also seeded the client-facing context catalog for the two new models,
   which before apply still showed CPA synthesized defaults
   (`context=500000 max=872000`) instead of the manifest pins.
3. **Doctor** — `DOCTOR_CONTRACT_OK`: `admission-integrity=OK`,
   `semantic-policy=OK`, `MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_DRIFT=none`,
   projection drift all MATCH.
4. **Independent live acceptance re-run** — one-shot curl via
   `127.0.0.1:8318` (admission chain): `HTTP 200`, `model=beam-free`,
   `finish_reason=stop`, content `OK`, 75 total tokens. Consistent with the
   streamed acceptance above. Note: python-urllib probes against this lane
   hit the upstream Cloudflare `1010` fingerprint block (runbook
   classification); the projected `request-scoped-errors` stop rule kept
   those fast-fails cooldown-free.

Watch item: the manifest pins (models.dev: `beam-free`=1048576,
`longcat-2.5-preview-free`=1000000) exceed the context the Go plan currently
advertises for these models (~500K). If large-context requests start failing
upstream, lower both pins to 500000 (manifest + live config, one re-apply).
