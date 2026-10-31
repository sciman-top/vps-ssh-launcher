# BWG CPA WorkBuddy per-lane RPM projection (2026-11-01)

## Scope

This change closes a live projection drift found during the CPA risk review.
The repository contract already pinned the `workbuddy-oauth` admission lane to
`requests_per_minute=12`, but BWG was still running the previous value `18`.
The repair changed only the admission projection. OAuth credentials, API keys,
provider routes, model aliases, egress, retry policy, and account state were
not rotated, deleted, or cleared.

## Evidence layers

- `repo_verified`: `scripts/remote/cpa-admission.json` contains
  `workbuddy-oauth.requests_per_minute=12`; the repository hash is
  `bd785579df15eb7887f7570b979cdf852a10bf2bd26a3dfaa31a2f2399915e54`.
- `host_loaded` before repair: BWG `/opt/cliproxyapi/cpa-admission.json` had
  `workbuddy-oauth:18`, which caused `admission-health=FAIL` and one
  `projection-drift-cpa-admission.json` failure. Other projected CPA files
  matched.
- `host_loaded` after repair: `scripts/cpa_bwg_guardrails.ps1 -Profile bwg
  -Apply` returned `GUARDRAILS_APPLIED`, created backup directory
  `/root/cpa-guardrails-backup-20261031T162825.720769794Z`, and reported the
  target admission config hash.
- `host_loaded` verification: strict doctor returned `DOCTOR_CONTRACT_OK`,
  `admission-health=OK`, `admission-integrity=OK`, all projection drift checks
  `MATCH`, OAuth lanes `OPEN`, and `cooldown_state=none`.
- `controlled_live`: one-hour natural traffic after projection reported
  `workbuddy-oauth/deepseek-v4.1-flash` peak `10 rpm`, `completed=288`,
  `capacity=0`, `client_disconnect=1`, `cache_hit_ratio=1.00`.

## Latency and cache reading

The same one-hour sample reported first-byte p50 of about 5.8 seconds for
WorkBuddy and 4.6 seconds for Antigravity. These values track upstream header
generation time; the local path already uses
`stream-bootstrap-buffering=false`, `stream-bootstrap-timeout=0`, and the
Nginx streaming contract. No safe evidence supports increasing concurrency,
adding retries, or enabling provider prompt-cache keys for OAuth lanes.

## Rollback

Restore the backed-up `cpa-admission.json` from the directory above through the
existing guardrail rollback procedure, then restart and rerun strict doctor.
Do not use a Git revert as a substitute for restoring the remote file.

## Boundary

This receipt proves the local projection and the observed one-hour window. It
does not prove provider quota, long-term OAuth account safety, or China-network
natural acceptance. The existing plaintext `http-bridge-8003` transport warning
remains unchanged and is not used as evidence for OAuth safety.
