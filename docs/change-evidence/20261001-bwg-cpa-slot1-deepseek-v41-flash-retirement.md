# BWG CPA slot-1 bare-name retirement: `deepseek-v4.1-flash`

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change (user, 2026-10-01): remove the slot 1 (`ai.input.im`) route
  `deepseek-v4.1-flash`. Catalog 11 → 10 bare names.
- The name is not OpenAI-namespace, so per the `deepseek-v4-pro` /
  `glm-5.3-flashx` precedent it gets **no exclusion-list tombstone**
  (`cpa_policy.py` only requires `gpt_routes ⊆ oauth_exclusions` and
  `(gpt_routes ∪ oauth_aliases) ⊆ codex_api_key_exclusions`); the
  fail-closed readiness/catalog contract blocks resurrection. DeepSeek
  traffic stays on the slot-5 `deepseek-flash` bare name.

## Repository change (commit `20d411d`, 7 files, +13/-17)

- `scripts/remote/cpa_provider_routes.json`: slot-1 models 4 → 3
  (`gpt-6-astra`, `gpt-6.1-sol-input`, `gpt-image-2.5`).
- `cpa_catalog_expectations.py`: `PROVIDER_MATRIX_TAIL` 8 → 7 (derived
  fixtures — `HEALTH_FIXTURE_CATALOG_IDS`, doctor `allowed_ids`,
  quality-eval models — shrink automatically).
- `scripts/cpa_bwg_guardrails.ps1`: catalog-summary marker renamed to
  `has_retired_slot1_deepseek_v41_flash` (informational only).
- `scripts/remote/cpa-acceptance.py`: fixture slot-1 mirror synced.
- `test_scripts.py`: relay-soft smoke 3 → 2 channel probes (`ok_ds41`
  removed), wrong-content case retargeted to the new first probe
  `gpt-6-astra`, manifest shape pin synced.
- `docs/runbooks/cpa-gateway.md`, `cpa-oauth-luna-slot.md`: slot-1 lists
  synced with retirement date.
- Admission lanes, OAuth lane and both exclusion lists: unchanged (the name
  was in none of them).

Gates: `run_gates.ps1` all green (234 passed, 1 skipped, 249 subtests;
bandit/ruff/mypy clean); `git diff --check` clean. Parallel-session commits
(`9e5289f`/`7cc6f06`/`0ddfd03`, cockpit docs + outputs) were untouched; the
apply clean-tree check is scoped to the projection source set, so their
untracked `outputs/` scratch files did not block `-Apply`.

## Projection evidence

- Sequence: full gates → commit `20d411d` → `-Apply` → doctor.
- `-Apply` (2026-09-30T16:17Z): `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
  zero `ROLLBACK`, backup
  `/root/cpa-guardrails-backup-20260930T161722.024697400Z`. Catalog summary:
  `models=10`, `has_retired_slot1_deepseek_v41_flash=False`,
  `has_deepseek=True` (slot-5 `deepseek-flash` retained).
- Post-apply strict doctor: `DOCTOR_CONTRACT_OK` (exit 0),
  `MODEL_IDS=deepseek-flash,glm-5.3,glm-5.3-flash,gpt-6-astra,gpt-6-astra-cii,gpt-6-luna,gpt-6.1-sol,gpt-6.1-sol-91,gpt-6.1-sol-input,gpt-image-2.5`
  (exactly 10), `MODEL_IDS_UNKNOWN=none`,
  `catalog_oauth_aliases=gpt-6-luna,gpt-6.1-sol`, `luna_state=available`,
  `gateway-443-fallback-lane=1`, projection drift all `MATCH` (HEAD
  `20d411d`).

## Live acceptance (loopback 8318, admission full chain, single shot, no retry)

- `gpt-6-astra`: 200, responded model `gpt-6-astra`, finish=stop, content OK,
  3.17 s — LIVE_ACCEPTED (slot-1 channel healthy after restart).
- `deepseek-v4.1-flash` (retired): 400 `model_not_found`
  ("unknown provider for model deepseek-v4.1-flash") in 0.01 s — clean
  fast-fail, no upstream call.

## Rollback

- Repo: `git revert 20d411d` then re-run `-Apply`.
- Remote: `/root/cpa-guardrails-backup-20260930T161722.024697400Z`
  (pre-apply `config.yaml` + route manifest).

## Notes

- `relay-soft` (manual observability mode) now probes 2 channel routes
  (`gpt-6-astra`, `gpt-6.1-sol-input`); the scheduled daily gate stays on
  `glm-5.3-flash` — unchanged.
- Clients with `deepseek-v4.1-flash` in cached/manual model lists get an
  instant 400; switch them to `deepseek-flash`.
