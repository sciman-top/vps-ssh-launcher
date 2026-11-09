# bwg CPA model-scoped cooldown and audit differential — 2026-11-09

**Nature**: repository fix plus a controlled remote admission-source and
integrity-pin projection.

**Authorization boundary**: retain every existing OAuth account and API-key
channel. This transaction did not add, remove, rotate, relogin, or reassign a
credential, and did not change `config.yaml`, routing, concurrency, cooldown
policy, account selection, or `claude_lane`.

## 1. Repository receipt

- Commit: `adf8b7b2d4e5abb36714cc8d6b8f27a35a555260`
- Commit subject: `fix(cpa): preserve model-scoped cooldowns and audit text turns`
- Candidate admission pin:
  `e0ad23c9c900d67b8d94abcb1fe8b3ec38a1d0dd5f2e9bb147ce2dc099084870`

The change closes four observable gaps:

1. A WorkBuddy HTTP 429/503 header can no longer pre-arm a lane-wide cooldown
   before the bounded body identifies a model-only `6004`. Final response
   classification remains the authority for model-versus-lane scope.
2. Natural traffic telemetry distinguishes `tool_call`, `tool_calls`,
   `function_call`, `tool_use`, and `reasoning_only` instead of collapsing
   agentic tool turns into "no answer".
3. Doctor first-text metrics use three populations: measured visible-text
   turns, structurally textless turns, and unclassified absence. The no-text
   population is no longer reported as a fabricated latency sample.
4. Risk audit describes `max-retry-credentials=3` and `request-retry=0`
   accurately and surfaces model-scoped live cooldowns as a wait signal, not a
   ban signal.

## 2. Preflight

The read-first transaction was run before mutation:

```text
ADMISSION_IDLE_OK
ADMISSION_INTEGRITY_OK
DRY_RUN_OK baseline=285b4a638be90aac0b8f413b7859ca772cfc40a7d1670215dcfc332a5b99f03b candidate=e0ad23c9c900d67b8d94abcb1fe8b3ec38a1d0dd5f2e9bb147ce2dc099084870 idle=true no_swap=true
```

An earlier dry-run correctly refused while traffic was active:

```text
DRY_RUN_BUSY live_traffic_active=true no_swap=true
```

## 3. Projection

Tool:
`outputs/deploy-admission-riskmix-20261108.ps1`

The transaction acquired the maintenance and request locks, proved all lanes
idle, backed up the live source and lane state, rotated source and pin together,
restarted only `cpa-admission`, and ran the post-swap contracts:

```text
REQUEST_GUARD_EXCLUSIVE_OK
ADMISSION_IDLE_OK
ADMISSION_INTEGRITY_OK
ADMISSION_INTEGRITY_OK
TELEMETRY_ACCOUNTING_OK series=0
TELEMETRY_CONTRACT_OK series=0
HOLD_LATCH_ATTRIBUTABLE_OK
ADMISSION_REGRESSION_GUARD_OK
DEPLOY_OK pin=e0ad23c9c900d67b8d94abcb1fe8b3ec38a1d0dd5f2e9bb147ce2dc099084870 backup=/opt/cliproxyapi/backups/20261109T123510.278172678Z-admission-riskmix-20261108
```

`series=0` is expected immediately after restart: the telemetry window has no
post-restart samples yet. Natural traffic below populated it.

## 4. Post-projection host contract

Fresh strict doctor at `2026-11-09T12:36:26Z` returned
`DOCTOR_CONTRACT_OK` with exit code 0:

- `admission-integrity=OK`
- all 13 source/config/service projections `MATCH`, including
  `cpa-admission.py` and `cpa-admission.sha256`
- admission service enabled and active on `127.0.0.1:8318`
- CPA container running with `restart=0`
- `chatgpt-oauth`, `antigravity-oauth`, and `workbuddy-oauth` all `OPEN`
  with `hold_reason=none`
- `claude-lane=RETIRED`, with the unconfigured durable tombstone inert
- WorkBuddy remained at `0.9.89`, with CN/INTL order and both pinned model
  sets unchanged
- policy, auth permissions, Nginx syntax, model catalog, and model context
  contracts all passed

The configuration hash remained
`269579af7b65443900a6a9788e5f1cf285439b6b97830142e055ad366a0cfe87`
before and after the transaction. The host retained 19 auth files, all mode
`0600`.

## 5. Controlled and natural acceptance

Focused repository verification:

```text
441 passed, 84 subtests passed in 134.37s
exit=0
```

This includes an isolated HTTP upstream that returns `429 + Retry-After` with a
model-only `6004` body. The test proves that admission arms only the requested
model cooldown, keeps `cooldown_scope=model`, and does not set
`cooldown_active` for the lane.

Post-projection natural traffic window (`0.2h`, 87 admission records):

| Lane/model | Natural completions | Queue p90 | Header p50 | Cache hit | Text-shape observation |
|---|---:|---:|---:|---:|---|
| `antigravity-oauth/gemini-3.8-flash-high` | 4 | 30ms | 7.19s | 99% | 3 tool-call turns |
| `chatgpt-oauth/gpt-6-luna` | 21 | 3ms | 0.81s | 93% | 18 tool-call turns |
| `passthrough/deepseek-v4.1-flash-oc` | 62 | 0ms | 1.91s | provider did not report | non-SSE passthrough |

No empty or truncated text completions were reported. The audit exited 0 and
emitted no credentials, request IDs, prompts, or response bodies.

## 6. Risk interpretation and limits

- Existing channels remain present. Three retained WorkBuddy INTL credentials
  show historical `6008 Credits exhausted`; there are no retained
  `invalid_grant`, `TOS_VIOLATION`, `PERMISSION_DENIED`, `11140`, or
  `insufficient_quota` signals in the observed container-log window.
- Antigravity remains the highest policy-risk OAuth lane because its official
  terms prohibit third-party software access. A technical admission fix does
  not change that provider-policy boundary.
- Claude OAuth remains retired. The user may preserve resources, but this
  transaction neither re-enables nor relies on that lane.
- The observed Gemini `gemini-3.8-flash-high -> gemini-3.8-flash-n` pair remains
  a single known name variant with 100% lane coverage. It is not evidence by
  itself of a pool-wide quality downgrade.
- Cache ratios were already high before this change. Do not rewrite prompt
  prefixes or raise OAuth concurrency to chase a latency gain; neither changes
  single-request prefill and both can reduce cache locality or increase
  account pressure.
- This receipt proves repository behavior, host projection, and short-window
  natural traffic. It does not prove future provider compliance, hidden quota
  state, long-term account safety, or absence of future enforcement.

## 7. Rollback

The transaction auto-rolls back on any post-swap failure. Manual rollback within
the maintenance lock is:

```bash
BK=/opt/cliproxyapi/backups/20261109T123510.278172678Z-admission-riskmix-20261108
cp -a "$BK/cpa-admission.py" /opt/cliproxyapi/cpa-admission.py
cp -a "$BK/cpa-admission.sha256" /etc/vps-ssh-launcher/cpa-admission.sha256
systemctl restart cpa-admission
runuser -u nobody -- /usr/local/libexec/cpa-admission-integrity-check
```

Do not overlay the whole backup directory: `lane-state/` is an audit snapshot,
not a request to restore or clear live cooldowns.
