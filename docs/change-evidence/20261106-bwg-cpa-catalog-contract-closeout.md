# 2026-11-06 BWG CPA catalog contract and controlled acceptance closeout

## Scope

This receipt closes the local catalog-contract failure found after the
WorkBuddy 6004 bounded-park repair. It covers only the reviewed CPA route
contract and the resulting host projection. It does not claim that an OAuth
provider will preserve quota, account standing, model quality, or network
availability over time.

No OAuth credential, API key, account, IP/UA identity, retry count, lane
concurrency, or cooldown state was rotated, deleted, cleared, or widened.
The existing plaintext HTTP slot 3 remains an explicitly accepted upstream
boundary and continues to be reported by the doctor.

## Root cause and repository repair

Fresh strict doctor before the repair reported:

```text
MODEL_IDS_UNKNOWN=gpt-6-chat-thinking
MODEL_CONTEXT_MISSING=gpt-6.1-sol-input
client-model-catalog-contract=FAIL
DOCTOR_CONTRACT_FAILED
```

The runtime catalog advertised `gpt-6-chat-thinking` as an OpenAI-owned
dynamic OAuth model that was not part of the reviewed public route manifest.
The manifest now keeps that name in both OAuth and Codex API-key exclusion
tombstones, so it cannot widen either lane without a reviewed catalog change.

`gpt-6.1-sol-input` is a declared optional compatibility-provider alias. The
doctor context checker previously treated every manifest provider entry as
required even though `cpa-health.py` already treats optional provider, OAuth,
and plugin entries as availability-filtered. The checker now reports optional
absence separately and only fails on missing required entries, unreadable
context, or context drift. A listed model with the wrong context remains a
hard failure.

Repository commit:

```text
a61e3ea fix(cpa): honor optional model context catalog entries
```

## Repository proof

The full repository gate completed after the repair:

```text
1118 passed, 18 skipped, 579 subtests passed
bandit: PASS
ruff check: PASS
ruff format --check: PASS
mypy: PASS
```

The targeted CPA checks also passed (`109 passed, 63 subtests`). The skipped
real SSH test was not used as production evidence; the production evidence
below came from the explicit guardrail transaction and fresh remote reads.

## Projection and host loading

The catalog change was projected with the existing backup-first guardrail
transaction:

```text
GUARDRAILS_APPLIED
READY_STATUS=200
BACKUP_DIR=/root/cpa-guardrails-backup-20261106T012731.133462817Z
```

The transaction acquired the normal maintenance/admission drain boundary and
reloaded the existing CPA stack. The one connection reset and one temporary
8318 refusal during restart converged to `HEALTH_OK` and `READY_STATUS=200`;
they were not treated as provider failures.

Fresh strict doctor after projection returned:

```text
MODEL_IDS_UNKNOWN=none
MODEL_CONTEXT_DRIFT=none
MODEL_CONTEXT_OPTIONAL_MISSING=none
MODEL_CONTEXT_MISSING=none
MODEL_CONTEXT_UNREADABLE=none
cooldown_state=none
lane-hold-workbuddy-oauth=OPEN
workbuddy-version=0.9.87
DOCTOR_CONTRACT_OK
```

Every projection entry reported `MATCH`, including `cpa_provider_routes.json`,
`cpa-health.py`, `cpa_policy.py`, the admission files, the systemd unit and
the Nginx/fail2ban contracts. The running CPA image remained `v8.0.55` with
restart count zero after the transaction.

## Controlled live acceptance

One low-cost, single-shot WorkBuddy request was sent through the loopback
admission listener after the doctor passed. The helper read the client key on
the host and never printed it. There was no retry or follow-up request:

```text
model=deepseek-v4.1-flash-sg
PRODUCTION_STATUS=200
PRODUCTION_SSE_EVENTS=4
TERMINAL=true
TEXT='PONG'
PRODUCTION_PONG_ACCEPTANCE=PASS
```

A post-probe strict doctor still returned `DOCTOR_CONTRACT_OK`, with the
WorkBuddy lane open and local cooldown state empty. This proves the current
projection and one real end-to-end SSE path. It does not turn the one probe
into natural Desktop traffic acceptance or a long-term provider-risk claim.

## Residual boundaries

- Provider-side 6004, 429, capacity, account review, and model-quality
  decisions remain external signals. The local policy continues to use
  `request-retry=0`, `max-retry-credentials=1`, `account_max_concurrent=1`,
  and `identity_switch_on_429=false`.
- The WorkBuddy 6004 bounded park remains capped at 300 seconds; the upstream
  credential/model window still owns the real reset time.
- Slot 3 remains cleartext HTTP by explicit user acceptance. Do not send
  sensitive prompts or credentials through that upstream.
- The receipt is `repo_verified -> filesystem_projected -> host_loaded ->
  controlled_live_accepted`; it is not `natural_live_accepted`.
