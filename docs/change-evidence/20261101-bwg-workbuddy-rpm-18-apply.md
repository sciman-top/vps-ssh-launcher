# BWG CPA WorkBuddy RPM 18 apply (2026-11-01)

## Scope

Raise the `workbuddy-oauth` admission lane `requests_per_minute` from 12 back
to 18 and project it to BWG out-of-window (same-day, user-authorized) after
the 12 cap — live since ~2026-10-31 16:28Z projection — rejected real
multi-session bursts (8x `lane_reject reason=rate_limited`, retry_after 2-6s,
2-3 client hashes, minute-rate ~17 rpm) while the same 24h showed zero
upstream capacity/6004 signals (all 200 completed, peak 455 req/h).

## Evidence layers

- `repo_verified`: `192b565` (json + doctor expected_rpm + runbook) and
  `ff695e2` (apply.sh embedded `admission_config_contract` expected_rpm).
  Repo json hash: `workbuddy-oauth=18`, all four lanes 24/30/18/18.
- `host_loaded` (failed first attempt, rolled back clean): first `-Apply`
  run at ~04:41Z hit the transaction's fail-closed contract —
  `ROLLBACK admission_config_contract` — because the apply-side embedded
  expected_rpm still pinned 12 while json/doctor moved to 18. Remote file and
  runtime verified restored (12/12); backup
  `/root/cpa-guardrails-backup-20261101T044130.064961241Z`.
- `host_loaded` (successful apply): `-Apply` returned `GUARDRAILS_APPLIED`
  with all `PROJECTION_HASH_VERIFIED` lines and `ROLLBACK_VERIFIED`, backup
  directory `/root/cpa-guardrails-backup-20261101T044609.539945453Z`.
  Post-apply: `/opt/cliproxyapi/cpa-admission.json` workbuddy-oauth
  `requests_per_minute=18`; `/healthz` lanes rpm `24/30/18/18`,
  `cooldown_scope=none`.
- `controlled_live`:
  - Strict doctor (default read-only run) returned `DOCTOR_CONTRACT_OK`
    (syntax/health/policy/admission/compose/nginx all OK) against the new
    18 contract.
  - Single-shot live SSE probe on BWG loopback `127.0.0.1:8318`
    (`/v1/responses`, `deepseek-v4.1-flash`, `stream:true`, no retries,
    client key read in-remote-process from `config.yaml access.api-keys[0]`,
    never echoed): `PROBE_STATUS=200`, 20 events,
    `response.completed=true`, output text match `pong`,
    `LIVE_PROBE_RESULT=PASS`. Two earlier probe attempts FAILED with
    `upstream_error ... Upstream status=401` — probe harness defect
    (wrong key source: the `api-keys:` top-level block is the
    openai-compatibility provider table, not the client key list); production
    traffic unaffected (journal shows user sessions completing 200 throughout).

## Rollback

Restore `/opt/cliproxyapi/cpa-admission.json` from
`/root/cpa-guardrails-backup-20261101T044609.539945453Z` via the guardrail
rollback procedure (restore file, restart `cpa-admission`, rerun strict
doctor). A Git revert is not a substitute for restoring the remote file.

## Boundary

This receipt proves the projection, contract convergence, and one controlled
live probe. It does not prove long-term upstream quota behavior under 18 rpm
or the residual burst headroom above 18 (observed natural burst band top was
19 rpm); judge `rate_limited` recurrence over the next 24h before considering
any further adjustment.
