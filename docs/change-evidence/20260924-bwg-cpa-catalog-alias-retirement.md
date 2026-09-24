# BWG CPA catalog alias retirement

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change: retire two bare catalog names. The client catalog keeps
  exactly 11 IDs afterwards:
  `gpt-6-luna` (ChatGPT Plus OAuth), slot 1 `gpt-6-sol` / `gpt-6-astra`,
  slot 2 `gpt-6-astra-cii` (upstream `gpt-6-astra`) / `gpt-6-sol-cii`
  (upstream `gpt-5.6-sol`), slot 3 `gpt-6-sol-91` (upstream `gpt-5.6-sol`) /
  `gpt-5.6-terra`, slot 4 `glm-5.3` / `glm-5.3-flash`,
  slot 5 `deepseek-flash` / `deepseek-v4-pro`.
- `glm-5.3-flashx`: removed from slot 4's projected `models`; BigModel keeps
  only `glm-5.3` and `glm-5.3-flash`.
- `gpt-5.6-luna`: added to the manifest `oauth_exclusions` (flows into
  `oauth-excluded-models.codex`) and `codex_api_key_exclusions`. CPA merges
  per-account and provider-level exclusions (v7.3.15
  `internal/watcher/synthesizer/helpers.go`, per-account ∪ global), so after
  restart the OAuth lane stops registering the legacy alias while `gpt-6-luna`
  stays available. `gpt-5.6-luna` is now a retired name everywhere, not a
  served alias.
- Health/observability follow-through: `cpa-health.py` allowed/matrix/expected
  model sets are manifest-derived only (no hardcoded `gpt-5.6-luna`), the
  explicit matrix probes `gpt-6-luna` when cataloged, and the scheduled gate
  target stays `glm-5.3-flash` (2026-09-22 decision unchanged). The doctor
  cooldown section reports `catalog_gpt6_luna` only. The apply catalog summary
  prints the two retired names as `has_retired_*` probes.

## Projection evidence

- Pre-apply read-only strict doctor: `DOCTOR_CONTRACT_OK`,
  `catalog_gpt6_luna=present`, `luna_state=available`, `cds_files=0`,
  `cooldown_state=none`, `oauth_monitor=OK`, CPA v7.3.15.
- Backup-first apply:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`.
- Rollback backup: `/root/cpa-guardrails-backup-20260924T144139.650211411Z`.
- Apply result: `GUARDRAILS_APPLIED`, `READY_STATUS=200`, `HEALTH_OK`.
  A transient connection reset occurred during the CPA restart readiness
  window; the loop recovered to 200.
- Post-apply loopback catalog summary: exactly 11 IDs; all eleven expected
  routes present; `has_retired_bare_luna=False`;
  `has_retired_glm_5_3_flashx=False`; no `r1/` prefix; no retired CIII names.
- Post-apply read-only strict doctor: `DOCTOR_CONTRACT_OK`,
  `catalog_gpt6_luna=present`, `luna_state=available`, `oauth_monitor=OK`,
  `oauth_days_left=3` (pre-existing refresh cadence, next refresh point on the
  usual ~3-day rolling window; unchanged by this transaction),
  `model_substitution_warnings_7d=0`, `error-dump-permissions=OK`.
- Local and remote `cpa_provider_routes.json` SHA-256 matched:
  `1169784e9d6756d698d7768d3355ab4d9641a023b616b0ff8b1deed04312ee42`.
  Projected `cpa-health.py` SHA-256 matched:
  `70ab9fa753d7dee393a30d2f788c8dacf912484a05cbff5d6ad353fadaafb756`.
  `cpa_policy.py` was unchanged (`8c04fa5f22ffed44d4a368b74f56a27fa6d4a3a67dd01df26ae41039266e4d80`).

## Behavioral consequences

- Any client still calling `gpt-5.6-luna` or `glm-5.3-flashx` now receives the
  standard unknown-model failure instead of a routed response. The public
  gateway contract and all other routes are unchanged.
- Readiness/health fail closed: a catalog containing either retired name
  returns exit 20, so reappearance of the aliases through any lane is a
  visible health failure, not a silent route.

## Repository verification

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`:
  156 passed, 1 skipped (real SSH integration opt-in), 182 subtests passed;
  Bandit, Ruff lint/format, and mypy clean.
- `git diff --check`: passed.

## Not covered

- No provider generation request was sent during this transaction
  (`HEALTH_OK` readiness plus the apply-time scheduled smoke only). Long-term
  provider availability and natural-use acceptance are not established by
  this record. Cockpit/new_api clients that cached the old names need their
  local model lists updated separately.
