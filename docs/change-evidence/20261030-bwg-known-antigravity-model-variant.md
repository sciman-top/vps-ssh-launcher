# BWG known Antigravity model-name variant classification (2026-10-30)

## Scope and authorization

The user authorized continued repair and optimization after the CPA/BWG risk
review. This change improves substitution telemetry only. It does not change
provider routes, OAuth credentials, admission budgets, retry counts, cooldowns,
WorkBuddy settings, or model aliases.

## Finding

BWG retained 210 `upstream served model` warnings in the seven-day container
log window. All 210 warnings were the previously reviewed pair:

```text
gemini-3.8-flash-n <== gemini-3.8-flash-high
```

The pair is an Antigravity modelVersion naming observation already documented
in the incident runbook. The old doctor classified any count above five as
`WARN_SUBSTITUTION_ELEVATED`, which made normal request volume look like a new
degradation event.

## Repository change

Commit `9d840e5` (`feat(cpa): classify known antigravity model name variant`)
adds two redaction-safe counters and an observation status:

- `model_substitution_known_name_variant_pairs`
- `model_substitution_unknown_pairs`
- `model_substitution=KNOWN_NAME_VARIANT` when every observed pair is the
  reviewed pair

Unknown pairs continue to produce `WARN_SUBSTITUTION_OBSERVED` or
`WARN_SUBSTITUTION_ELEVATED`. Raw warning counts and pair lines remain
unchanged. `cpa_failure_triage.py` now carries the two counters into its
rendered summary.

## Local proof

- Focused tests: `65 passed, 64 subtests passed`.
- The substitution section also ran in a synthetic Bash harness: six known
  pairs classified as `KNOWN_NAME_VARIANT`, and six unknown pairs retained
  `WARN_SUBSTITUTION_ELEVATED`.
- The full gate on the source change before adding the harness was
  `818 passed, 1 skipped, 403 subtests passed`.
- Bandit, Ruff, ruff format, mypy and Bash syntax checks passed.
- The one skipped test is the explicit real-SSH integration test.

After the harness-only test commit, the CPA admission subset passed
`438 passed, 176 subtests passed`; the two Claude timing cases also passed
when rerun alone (`9 passed`). A later repository-wide gate was blocked by an
unrelated in-progress maintenance diff: one Xray inventory test still feeds
the new numeric-version guard an `observed="old"` fixture, and two Claude
cases fell below their fixed 3500-second assertion after the long suite had
elapsed. No CPA source failure was reproduced in the focused reruns.

## BWG projection and readback

Guarded apply completed under the maintenance lock:

```text
GUARDRAILS_APPLIED
BACKUP_DIR=/root/cpa-guardrails-backup-20261030T150419.891759251Z
READY_STATUS=200
```

The 8318 admission listener reset briefly during the guarded drain/recreate
window; the transaction then returned `HEALTH_OK` and `READY_STATUS=200`.

Fresh strict doctor at `2026-10-30T15:05:27Z` returned:

```text
model_substitution_warnings_7d=210
model_substitution_distinct_pairs=1
model_substitution_known_name_variant_pairs=1
model_substitution_unknown_pairs=0
model_substitution=KNOWN_NAME_VARIANT
oauth_lane=chatgpt-oauth posture=OPEN hold_reason=none
oauth_lane=antigravity-oauth posture=OPEN hold_reason=none
oauth_lane=workbuddy-oauth posture=OPEN hold_reason=none
cooldown_state=none
DOCTOR_CONTRACT_OK
```

No OAuth login, refresh, rotation, deletion, fault injection, or generation
probe was performed for this change. The classification is observation-only;
it does not establish provider compliance, account eligibility, or absence of
real model degradation.

## Rollback

Under the maintenance lock, restore the doctor source from the transaction
backup and rerun strict doctor, or revert commit `9d840e5` and run the guarded
apply again. The backup directory above is the remote rollback receipt.
