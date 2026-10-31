# BWG CPA `gpt-reserve` route projection (2026-10-31 +08:00)

## Scope and authorization

The user directed that CPA expose a route for `gpt-reserve`. The change is
limited to the existing ChatGPT OAuth lane and its admission budget. It does
not create a provider, rotate or refresh credentials, change retries,
concurrency, cooldowns, Nginx limits, ZZ, or any other VPS.

## Route decision

`gpt-reserve` is a Codex client catalog slug (“GPT-Reserve” / desktop metadata
also identifies it as GPT-5.6 Reserve, 272K). It is not a model ID advertised
by the current ChatGPT OAuth credential, and none of the configured API-key
channels advertise it:

- ChatGPT OAuth direct request before registration: `400 model_not_found` /
  `unknown provider for model gpt-reserve`.
- `ai.input.im`, `http-bridge-8003`, and Zhipu `/models` probes: no
  `gpt-reserve`.

CPA therefore registers the client-visible name with an OAuth model alias.
The registry template is the existing `gpt-5.6-luna` model; `gpt-reserve` is
sent upstream as its own model ID and response model fields are mapped back to
the alias. The bare retired `gpt-5.6` name remains excluded, and
`gpt-reserve` / `gpt-5.6` remain in `codex_api_key_exclusions` so API-key
channels cannot claim the subscription route.

## Repository changes

- `cpa_provider_routes.json`: declares `gpt-reserve` on `chatgpt-plus-oauth`,
  pins 272K context, and declares the `codex` OAuth alias.
- `cpa-admission.json`: adds `gpt-reserve` to the existing
  `chatgpt-oauth` lane (`max_inflight=2` unchanged).
- `cpa-guardrail-apply.sh`: project and validate `oauth.model-alias`; rename
  aliases must be declared OAuth route aliases, drop only an exact-name source
  exclusion, and refuse a wildcard-excluded source.
- `cpa-guardrail-doctor.sh`: verifies deployed aliases against the manifest.
- `cpa_policy.py`: pins the exact deployed alias contract.
- Runbooks and catalog expectations updated.

Source commits: `bfe47fd`, `1fb741f`, `ec37444`, `4f25649`, `f973cb3`.

## Projection

The first alias projection failed closed because the exact source exclusion
`gpt-5.6` was still present; `ROLLBACK_VERIFIED` was returned. The apply
transformation was corrected to remove only the exact source exclusion and
refuse wildcard conflicts.

Successful guarded applies used these backups:

- direct-route interim state:
  `/root/cpa-guardrails-backup-20261031T020859.525136904Z`
- failed closed alias transaction:
  `/root/cpa-guardrails-backup-20261031T025213.277517230Z`
- fork alias state:
  `/root/cpa-guardrails-backup-20261031T025936.241770431Z`
- final force-mapped state:
  `/root/cpa-guardrails-backup-20261031T030531.112038767Z`

Final apply returned `CODEX_OAUTH_ALIASES_READY gpt-5.6-luna->gpt-reserve`,
`HEALTH_OK`, `READY_STATUS=200`, and `GUARDRAILS_APPLIED`. Catalog readback was
`models=12` and `has_gpt_reserve=True`.

## Verification

Final fresh strict doctor:

- `MODEL_ALIAS_CONFIG=OK codex:gpt-5.6-luna->gpt-reserve`
- `MODEL_CONTEXT_CONFIG=OK provider_models=6 oauth_channels=3`
- `MODEL_IDS` includes `gpt-reserve`
- `MODEL_CONTEXT_CATALOG=gpt-reserve:272000`
- `MODEL_CONTEXT_DRIFT=none`, `MODEL_CONTEXT_MISSING=none`
- all active OAuth lanes `OPEN`, `admission-health=OK`,
  `cooldown_state=none`
- `DOCTOR_CONTRACT_OK`

Controlled live acceptance after the final projection was one minimal
streaming Responses request through admission (`max_output_tokens=32`,
`store=false`, no retry):

- HTTP 200 `text/event-stream`
- terminal `response.completed`
- response model `gpt-reserve`
- one `response.output_text.delta` of length 2

This is controlled route acceptance, not a claim about long-term model quality
or upstream capacity. Natural traffic remains the authority for those.

After projection, `cockpit_provider_health.py` no longer reports
`desktop-model-unroutable` for `gpt-reserve`. The remaining
`gateway-not-required` warning is pre-existing and only concerns the local
10909 sidecar path.

Focused repository gates for the changed contracts passed:
`124 passed, 59 subtests` for guardrails/policy/risk audit, plus
`62 passed, 59 subtests` after force-mapping. Shell syntax checks passed.

## Rollback

Do not use a Git revert as a substitute for host recovery. Under the existing
maintenance lock, restore only the affected `config.yaml`,
`cpa_provider_routes.json`, `cpa_policy.py`, and `cpa-admission.json` from the
relevant backup above, run the admission integrity check, and restart through
the guarded path. Then run fresh strict doctor and confirm `gpt-reserve` is no
longer in `/v1/models` if rolling back the route entirely. To keep the route
but remove only force-mapping, apply the source tree without that field.
