# CPA OAuth Luna Slot

## Scope

This runbook applies only to the BWG CPA deployment. It keeps the logical
Luna slot while removing every locally stored Codex OAuth token from the VPS.
The live bare name `gpt-5.6-luna` is then explicitly served by the existing
unprefixed r1 relay declaration; it is not an automatic fallback.

## Deactivate

Run the versioned transaction from the repository root:

```powershell
pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -DeactivateOAuthLuna
```

The transaction requires exactly one active `type=codex` OAuth JSON, verifies
the r1 topology, adds the bare Luna mapping, stops CPA, and removes Codex OAuth
JSON from the active auth directory and CPA backup locations. It does not copy
the token to a new remote backup. A failure after the stop leaves CPA stopped
instead of silently restoring an OAuth token.

`OAUTH_REMOVAL_VERIFIED=yes` proves only that the VPS no longer contains a
local OAuth access or refresh token in the scanned CPA locations. It cannot
prove provider-side session revocation. Revoke the device/session through the
account's official security controls before or after this transaction if that
is required; do not place a newly issued token on the VPS until re-enrollment.

## Re-enroll

The retained slot is metadata only: provider `codex`, intended bare model
`gpt-5.6-luna`, and the original OAuth route. Re-enrollment creates a fresh
OAuth credential through the supported interactive device-login flow, then
reverses the explicit r1 bare Luna mapping in a reviewed BWG-only change.
Perform one low-frequency OAuth generation check after re-enrollment; do not
retry 408, 429, 502, or 503 responses.

The r1 route remains independently observable through `r1/gpt-5.6-luna`; its
availability is not proof of OAuth account health or provider acceptance.
