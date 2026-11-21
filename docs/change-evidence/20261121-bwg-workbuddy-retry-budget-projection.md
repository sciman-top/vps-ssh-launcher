# BWG WorkBuddy executor retry budget projection

## Scope

This change addresses request replay inside WorkBuddy CN plugin `0.21.29`. The
CPA host scheduler's `routing.retry.max-retry-credentials: 1` does not bound the
plugin executor's own retry budget. The plugin's `retry_on_4xx` field is now
projected as the integer `0`, and strict doctor rejects a missing, non-integer,
or nonzero value. INTL remains disabled, Claude OAuth remains retired, current
accounts and API-key routes remain present, and WorkBuddy admission RPM remains
18.

The plugin source reviewed for this policy was
`luode0320/cpa-workbuddy-plugin` commit
`1bea2d71816f0494b0ad3734716c2baa20932138`, whose `workbuddy/VERSION` is
`0.21.29`. The latest source tree reviewed on 2026-11-21 was main commit
`6999438588f5c8125dade9844699d8bf75f94859`; it still reports WorkBuddy
`0.21.29` and the same runtime default. The default is 10 retries, allowing up
to 11 attempts (initial attempt plus 10 retries). The top-level README example
at that revision sets the CN plugin to 3, while the runtime default and
plugin-specific README example are 10. The README value is an explicit example,
not the absent-field default. The updater fixture deliberately starts at 10 and
proves that the guardrail forces it to 0.

In the streaming executor, this budget governs same-request account rotation
for account-level 4xx, 429, and 5xx except channel-wide queue-full errors. It
also governs stream-read retries before answer content is emitted when the
request body is replayable and another account is available. Setting the
budget to 0 disables those executor retries. Some upstream comments still say
5xx is cooldown-only, but the executed path calls `shouldRotateOnStatus`,
which includes HTTP 500–599. A 429 still enters the normal cross-request
cooldown path as well.

## Repository change

Commit `aa409af6ae9eb0e6d3e995d1d337ab7f8d3ca21f` adds the plugin setting to the
BWG guardrail projection, pins it in strict doctor, updates the WorkBuddy and
failure-triage runbooks, and extends the natural-traffic audit with separate
local-rejection counts and queue-inclusive first-visible-text percentiles.

## Verification

- The isolated WorkBuddy simulation used synthetic credentials and a loopback
  TLS mock upstream in a network namespace without a default route. The
  `0.21.29` plugin passed 429, 503, and pre-output reset cases with both its
  default and zero retry budget. Default-budget cases tried two credentials and
  returned client HTTP 200 after failover. Zero-budget cases made one upstream
  attempt and returned the original client HTTP 500.
- The pre-projection strict doctor isolated the WorkBuddy configuration drift
  to missing `retry_on_4xx`.
- Full gate passed on the committed tree. The test lane reused its matching
  parallel receipt for 1,852 tests. The focused run passed 143 tests and 200
  subtests, with one Windows symlink-permission skip.
- `scripts/check_docs.py` passed for 42 documents, and `git diff --check`
  passed.
- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -AuditProjection` passed before
  the write.
- `scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` completed with
  `GUARDRAILS_APPLIED`, `READY_STATUS=200`, and `HEALTH_OK`. The transaction
  backed up the previous state to
  `/root/cpa-guardrails-backup-20261121T130909.132850243Z`.
- The post-projection strict doctor returned `DOCTOR_CONTRACT_OK`. It reported
  WorkBuddy CN `0.21.29`, `workbuddy-retry_on_4xx=0`, INTL disabled with all
  seven INTL auth files disabled, admission healthy, the expected runtime
  binary hash, and all 17 code projection hashes matching.
- A post-projection non-OAuth GLM cache canary returned `HEALTH_OK`. Its cold
  sample reported 0 cached tokens and its warm sample reported 4,096 of 4,114
  input tokens cached, for a `0.9956` hit ratio.
- The post-projection 24-hour natural-traffic audit covered 10,979 upstream
  results. Its window includes traffic from before and after deployment, so it
  is not a before/after performance comparison. WorkBuddy had 8,292 requests,
  11 capacity outcomes, seven client disconnects, and 32 local rejects. Queue
  wait was p50 3,872 ms and p90 8,940 ms. Queue-inclusive first visible text
  was p50 6,884 ms and p90 13,437 ms across 433 reported samples. Total request
  duration was p50 8.16 s and p90 14.80 s. Overall reported cache hit ratio and
  the long-prefix candidate ratio were both about 98.2%.

## Residual observations and rollback

The natural-traffic audit flagged WorkBuddy's 60-second admission peak of 19 as
at the upper edge of the community-reported 13–19 RPM observation. The
configured RPM remains 18 per the existing user decision. This task did not
increase account concurrency or change account selection to reduce queue wait.
The 24-hour queue and cache figures are observational aggregates, and the
queue-inclusive first-text metric was reported for 433 of 8,292 WorkBuddy
requests.

Strict doctor continues to report 338 model-substitution warnings over its
bounded seven-day container-log window. The previously reviewed Antigravity
substitution pairs remain a model-quality signal. The count does not establish
output quality or account status.

The guardrail apply output included brief connection-reset and admission
readiness probe errors during restart. The transaction subsequently reported
`HEALTH_OK` and `READY_STATUS=200`, and the post-projection strict doctor passed.
No failure-injection request was sent to a real OAuth account. The HTTP
Docker-bridge transport for the existing `zcode-start-plan` API-key slot remains
an explicitly accepted, separately documented risk and was not changed here.

The transaction's automatic rollback uses `restore_all` on failed
postconditions. Its captured host state is in the backup directory above.
Manual recovery must follow the existing guarded CPA maintenance procedure and
verify strict doctor after restoration.
