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

## Rollback

Restore the projected CPA files from the remote backup above, restart
`cli-proxy-api`, and rerun the strict BWG doctor. Git rollback alone does not
restore the remote runtime state.
