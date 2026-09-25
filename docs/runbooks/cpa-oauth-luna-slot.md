# CPA OAuth Luna Slot

## Scope

This runbook applies only to the BWG CPA deployment. It keeps the logical
Luna slot while removing every locally stored Codex OAuth token from the VPS.
The live bare names `gpt-6-luna` and the compatibility alias `gpt-5.6-luna`
are both declared on the ChatGPT Plus OAuth route in the route manifest and
served only by that OAuth credential. They are not
automatic fallbacks to ai.input.im or any other provider. The Codex OAuth
exclusion list keeps `gpt-6-sol` and `gpt-6-astra` pinned to ai.input.im.

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
`gpt-6-luna` and the compatibility alias `gpt-5.6-luna`, and the original
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

The ai.input.im route serves `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-sol`, and
`gpt-6-astra`; it does not serve Luna. GPT-6 Sol is preconfigured but may not
yet be available upstream. ai.input.im is a third-party channel and may return
408/429/5xx or provider quality/risk-control failures. The presence of either
Luna name in `/v1/models` proves catalog registration only, not OAuth account
health or provider acceptance.
