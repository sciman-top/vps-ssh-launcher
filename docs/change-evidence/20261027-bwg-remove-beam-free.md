# BWG remove `beam-free` while retaining OpenCode Zen DeepSeek (2026-10-27)

## Scope

The user corrected the requested scope during the transaction: remove only
`beam-free`; retain the OpenCode Zen provider, its `deepseek-v4.1-flash` route,
the WorkBuddy OAuth route, and the pooled dual-homed execution behavior. No
OAuth credentials, API-key values, provider endpoints, or WorkBuddy files were
deleted.

## Transaction

- Source of truth change: removed the single `beam-free` model entry from
  `scripts/remote/cpa_provider_routes.json`. OpenCode Zen slot 2 remains at
  `https://opencode.ai/zen/go/v1` with `deepseek-v4.1-flash` only.
- An initial incorrect-scope apply was interrupted before mutation. Fresh
  readback immediately afterward showed the remote configuration unchanged:
  OpenCode Zen still had both `beam-free` and `deepseek-v4.1-flash`.
- Corrected `-Apply` used the shared maintenance lock, created backup
  `/root/cpa-guardrails-backup-20261027T151204.603743712Z`, projected the route
  manifest and config, and returned `GUARDRAILS_APPLIED`.
- Projection hashes verified, including `/opt/cliproxyapi/cpa_provider_routes.json`.
  The transaction recreated CPA once; its final image remained the pinned
  v8.0.44 digest and restart count returned to zero.

## Post-apply proof

Fresh remote readback passed:

- `api-keys.openai-compatibility` contains the OpenCode provider with exactly
  `deepseek-v4.1-flash`; `beam-free` is absent.
- The projected manifest contains slot 2 with exactly
  `deepseek-v4.1-flash`; `beam-free` is absent.
- Client catalog has `deepseek-v4.1-flash` and no `beam-free`.
- Health endpoint returned `status=ok`; ChatGPT, Antigravity, WorkBuddy and
  Zhipu admission lanes were present.
- Strict doctor returned `DOCTOR_CONTRACT_OK`, `POLICY_OK`, and
  `MODEL_CONTEXT_DRIFT=none`. OAuth lanes were OPEN and the WorkBuddy
  `account_max_concurrent=1` and `identity_switch_on_429=false` protections
  remained in force.
- No generation probe was issued for the removed model. The retained
  DeepSeek route was verified structurally and remains eligible for the
  existing natural-use / pooled execution path.

## Rollback

If required, restore the listed backup's `config.yaml` and
`cpa_provider_routes.json` under the maintenance lock, re-run the reviewed
projection and strict doctor, and verify that the OpenCode provider has the
intended model set. Do not restore old authentication files or overwrite newer
OAuth state.
