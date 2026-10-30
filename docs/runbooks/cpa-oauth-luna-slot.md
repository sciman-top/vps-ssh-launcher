# CPA OAuth Luna Slot

## Scope

This runbook applies only to the BWG CPA deployment. It keeps the logical
Luna slot while removing every locally stored Codex OAuth token from the VPS.
The live names `gpt-6-luna`, the compatibility alias `gpt-5.6-luna`,
`gpt-6.1-sol`, and `gpt-6.1-terra` (adopted 2026-10-27)
are all declared on the ChatGPT Plus OAuth route in the route manifest and
served only by that OAuth credential. They are not
automatic fallbacks to ai.input.im or any other provider. The Codex OAuth exclusion list keeps `gpt-6-astra` and `gpt-6.1-sol-input`
pinned to ai.input.im; `gpt-6.1-sol` itself is served by this OAuth lane since
2026-09-30. `gpt-5.6-luna` was retired from the lane on 2026-09-24,
restored on 2026-09-25, retired again on 2026-10-01, and restored again on
2026-10-02; `gpt-6-sol` remains retired (tombstones stay in the exclusion
lists). Catalog additions, renames
and retirements follow
[cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md).

## Quarantine (reversible traffic stop)

When the goal is only to stop traffic reaching the single ChatGPT Plus account -
for example during a suspected risk-control window - use the reversible
quarantine instead of deactivation:

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -QuarantineOAuthLuna
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreOAuthLuna
```

It removes the OAuth lane aliases from the routed catalog through
`oauth-excluded-models.codex` plus an `oauth-quarantine.json` marker, so public
key holders can no longer reach the lane. The Codex OAuth JSON is never read,
copied, deleted or replayed; background token refresh keeps running so the slot
does not expire; quota and cooldown state are untouched. `-Apply` refuses to run
while a quarantine is active. Details and doctor readback in
[cpa-gateway.md](cpa-gateway.md).

## Deactivate

Run the versioned transaction from the repository root:

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

The transaction requires exactly one active `type=codex` OAuth JSON, stops CPA,
and removes Codex OAuth JSON from the active auth directory and CPA backup
locations. It does not copy the token to a new remote backup. A failure after
the stop leaves CPA stopped instead of silently restoring an OAuth token.

`OAUTH_REMOVAL_VERIFIED=yes` proves only that the VPS no longer contains a
local OAuth access or refresh token in the scanned CPA locations. It cannot
prove provider-side session revocation. Revoke the device/session through the
account's official security controls before or after this transaction if that
is required; do not place a newly issued token on the VPS until re-enrollment.

## Re-enroll

The retained slot is metadata only: provider `codex`, intended bare models
`gpt-6-luna` and `gpt-6.1-sol`, and the original
OAuth route. Re-enrollment creates a fresh
OAuth credential through the supported interactive device-login flow, then
restores the existing OAuth-only Luna route in a reviewed BWG-only change.
Perform one low-frequency OAuth generation check after re-enrollment; do not
retry 408, 429, 502, or 503 responses.

ChatGPT subscription OAuth through this public CPA gateway carries account and
terms-of-use risk. OpenAI recommends API keys for scripted workflows and says
not to expose Codex execution to untrusted or public environments. Keep the
random public path and API key private, use only personally controlled clients,
and stop on OAuth 401/403, 429, or repeated upstream failures. The existing
zero-retry and low-frequency probes reduce request amplification but cannot
guarantee immunity from throttling or account action.

The ai.input.im route serves `gpt-6-astra` and
`gpt-6.1-sol-input` (upstream ID `gpt-6.1-sol`); the
optional `gpt-image-2.5` image route was retired on 2026-10-04 (never used
in production; tombstone kept in the exclusion lists). It does not serve
Luna. `deepseek-v4.1-flash` moved from this slot to the new opencode.ai zen
slot on 2026-10-11 (re-added 2026-10-01 after a brief same-day retirement in
between) and later moved to the workbuddy plugin lane (2026-10-18); since
2026-10-25 the slot also exposes the bare name itself (the short-lived
`deepseek-v4.1-flash-open` alias was retired the same day), so the registry
pools the workbuddy plugin credential and the opencode credential under the
one public ID. CPA `max-retry-credentials=0` is required for the execution
layer to exhaust the WorkBuddy pool before trying the OpenCode credential;
the value `1` previously truncated that mixed-provider path after the first
6004. The bare
`gpt-5.6-sol` name was retired from this slot on 2026-09-30, and
`gpt-6-sol-input` was replaced by `gpt-6.1-sol-input` the same day. The
slot-2 channel is `https://opencode.ai/zen/go/v1` since 2026-10-11 (replacing
the retired codex.ciii.club CIII lane) and serves the bare
`deepseek-v4.1-flash` (dual-homed with the workbuddy plugin lane since
2026-10-25) over chat/completions; `muse-spark-1.3-contributor` is served through the
managed codex-family /responses group, and `claude-haiku-5-5` (added
2026-10-16) through the managed claude-family group, which speaks the native
Anthropic Messages surface (`/zen/go/v1/messages`). The claude group carries
slot 2's credential in a pinned `x-api-key` header, because CPA emits that
header shape only for api.anthropic.com while the endpoint reads nothing
else; the chat and responses surfaces return 400 ModelProtocolUnsupported
for it. The CIII aliases are retired tombstones.
Slot 3 keeps only `gpt-5.6-terra` since 2026-10-11 (`gpt-6.1-sol-91`
retired), and the DeepSeek official channel (`deepseek-flash`,
`api.deepseek.com`) was retired the same day with its shared admission lane.
The
ChatGPT Plus OAuth lane serves `gpt-6-luna`, `gpt-5.6-luna` (restored on
2026-10-02 after the 2026-10-01 retirement), and `gpt-6.1-sol`;
`gpt-6-sol` remains retired (2026-09-30).
A second OAuth route, `antigravity-oauth` (Google AI Pro) is **in service**
(user decision 2026-10-17, reverting the same-day quarantine note from 1e56b44
before it was ever executed remotely; two active credentials in one provider
lane, low-frequency manual use, the same risk-acceptance class as the ChatGPT
Plus lane). New sessions use the first eligible credential in registration order;
session affinity keeps that binding, and quota/auth/model-unavailable signals are
the allowed rebind triggers. This is failover capacity, not round-robin or a
guaranteed per-request account switch. Google
Antigravity Terms explicitly prohibit access via third-party
software/tools/services (including an Antigravity OAuth example) and warn of
suspension or termination; that risk is accepted and on record, is not
engineered around, and no UA/device/IP rotation is ever used to evade it.
The manifest declares only `gemini-3.8-flash-high`; the provider-level
exclusion snapshot covers every other upstream static model, verified by the
doctor `==antigravity-lane==` gate. The doctor gate verifies configuration and
catalog state, not account safety or eligibility. `claude-haiku-5-5` remains
an exclusion tombstone (2026-10-16); the claude-oauth subscription lane that
briefly served the alias (2026-10-17) retired on 2026-10-20, and the
tombstone still blocks Antigravity from claiming it. The Luna quarantine
and deactivation switches affect only the ChatGPT Plus route; Antigravity
quarantine is a separate transaction.
ai.input.im and opencode.ai are third-party channels and may return
408/429/5xx or provider quality/risk-control failures. The presence of any
of these names in `/v1/models` proves catalog registration only, not OAuth
account health or provider acceptance.
