# 2026-09-19 BWG CPA malformed-2xx guard and controlled acceptance

## Scope

- Target: `bwg` only; `zz` was not connected or modified.
- Goal: classify an upstream HTTP 2xx response that is not valid JSON as an
  upstream availability/protocol failure, without retrying or wrapping the
  body into a false success.
- No credentials, client keys, provider response bodies, or random paths were
  recorded.

## Local change

- `scripts/remote/cpa-health.py` now raises an internal
  `UpstreamProtocolError` when the loopback CPA data plane returns malformed
  JSON.
- Readiness and generation paths classify that condition as
  `UPSTREAM_UNAVAILABLE` (exit 10); the observability-only relay path remains
  `RELAY_DEGRADED` (exit 11).
- Cache canary and quality evaluation use the same upstream-unavailable
  boundary. No retry behavior was added.
- Focused regression coverage was added for readiness, generation-all, and
  relay-soft classification.

## Verification

- Full local gate: `127 passed, 1 skipped, 142 subtests passed`; Bandit,
  Ruff, format, and mypy passed.
- Backup-first projection command:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`
- Remote backup:
  `/root/cpa-guardrails-backup-20260919T095203.147222595Z`
- Post-apply readiness: `READY_STATUS=200`; catalog summary contained five
  approved bare model IDs.
- Post-apply strict doctor: `DOCTOR_CONTRACT_OK`.
- Projected `cpa-health.py` SHA-256:
  `069895fd78d45775e1bafd358271d70f2f7863cab89dbca9199a022186b0d9a3`.
- One post-projection `generation-all` run returned
  `UPSTREAM_UNAVAILABLE`, exit 10. No retry was sent.

## Acceptance boundary

- The local health/update control is repaired: malformed upstream 2xx is no
  longer treated as a local contract failure or a successful generation.
- Sol/Terra provider-side behavior remains unaccepted. Earlier fresh probes
  observed Sol HTTP 200 with a non-JSON body and Terra HTTP 503; this change
  intentionally does not invent a response parser without a confirmed
  upstream protocol contract.
- Sol/Terra remain a non-sensitive, low-frequency observation path only until
  a future fresh probe returns valid JSON with the expected model and
  `finish_reason=stop`.

## Follow-up directory refresh

- A fresh direct `ai.input.im /v1/models` read returned HTTP 200 and included
  both `gpt-5.6-sol` and `gpt-5.6-terra`.
- The loaded BWG config also declared both models and the provider was not
  disabled. Before refresh, the local CPA catalog intermittently contained Sol
  but omitted Terra.
- A single controlled `docker restart cli-proxy-api` was used to clear only
  in-memory registration/cooldown state. The first post-restart catalog read
  contained both Sol and Terra.
- A later local catalog read became incomplete again; the controlled generation
  run saw Sol HTTP 200 with a non-JSON body and Terra disappear from the local
  catalog. This is consistent with CLIProxyAPI's runtime availability registry
  hiding models during transient upstream failure; no `.cds` files were
  present. The registered read-only reference is the CLIProxyAPI checkout at
  `5b2785617d1e7de84a9f4dee599d275a4ccd8999` (MIT).
- The existing `transient-error-cooldown-seconds: 60` and `request-retry: 0`
  were retained. Disabling cooldown merely to keep a model listed would
  amplify repeated upstream 503s and was not adopted.

## Fresh fluctuation check

- A fresh authenticated local catalog read returned HTTP 200 with exactly the
  five approved bare IDs, including both Sol and Terra. `readiness` returned
  `HEALTH_OK`; the container was running with restart count zero.
- One single-shot request for each recovered channel then returned HTTP 200
  with `Content-Type: application/json`, but both response bodies were
  non-JSON. No retry was sent. This confirms that catalog recovery is not
  provider protocol acceptance: the ai.input.im route can reappear while its
  current responses remain unusable.
- `generation-all` consequently returned `UPSTREAM_UNAVAILABLE` (exit 10),
  which is the intended fail-closed result. No provider disable, cooldown
  bypass, repeated restart, or client-key change was performed.

## Rollback

Restore the projected CPA files from the remote backup above, restart
`cli-proxy-api`, and rerun the strict BWG doctor. Git rollback alone does not
restore the remote runtime state.
