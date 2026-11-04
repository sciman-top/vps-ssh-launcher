# BWG CPA `gpt-reserve` retirement (2026-11-04 +08:00)

## Scope and authorization

The user directed that the bwg CPA stop exposing `gpt-reserve`
("在bwg VPS CPA上不再开放gpt-reserve模型"). The change is limited to the
ChatGPT Plus OAuth lane and its admission budget. It does not create a
provider, rotate or refresh credentials, change retries, concurrency,
cooldowns, Nginx limits, ZZ, or any other VPS.

## Route decision

`gpt-reserve` was a Codex client catalog slug (GPT-5.6 Reserve, 272K) that CPA
served by renaming the upstream `gpt-5.6-luna` model through
`oauth.model-alias` (2026-10-31). Retirement removes the rename as well as the
route, so `gpt-5.6-luna` is served under its own name again and no client-only
slug is synthesized.

The bare name stays in `codex_api_key_exclusions` as a tombstone: an API-key
channel must never be able to claim a name the subscription lane no longer
serves. It was never in `oauth_exclusions`, and remains absent from it.

`oauth_model_aliases` is now an empty mapping. That is a deliberate signal, not
an omission — the projection treats an empty manifest as a full retirement and
clears `oauth.model-alias` wholesale, so the previous rename cannot survive as
a stale orphan.

## Repository changes

- `cpa_provider_routes.json`: drop the `gpt-reserve` OAuth route entry and its
  `oauth.settings.codex` context pin; empty `oauth_model_aliases`. The
  `codex_api_key_exclusions` tombstone is retained.
- `cpa-admission.json`: remove `gpt-reserve` from the `chatgpt-oauth` lane
  (`max_inflight=2` unchanged). A retired bare name left in the lane would let
  its capacity failure open a lane-wide cooldown for the models that remain.
- `cpa-guardrail-apply.sh`: accept an empty `oauth_model_aliases` (previously a
  hard `REFUSE ... must be a non-empty mapping`) and clear `oauth.model-alias`
  instead of preserving the deployed rename. Lane oracle and the
  `CODEX_OAUTH_ROUTES_READY` echo updated; the model-presence probe is renamed
  to `has_retired_gpt_reserve`.
- `cpa-guardrail-doctor.sh`: drop `gpt-reserve` from the independent lane
  oracle. The alias check already tolerates an empty manifest
  (`if expected_aliases:`), so no doctor change was needed for the alias block.
- `cpa_policy.py`: remove the `("oauth","model-alias","codex")` expectation and
  reword the `model-level-cooling` note (the retired name no longer shares the
  account).
- Tests and runbooks updated: `cpa_catalog_expectations.py`,
  `test_cpa_guardrails_script.py`, `cpa-gateway.md`,
  `codex-desktop-gateway-attach.md`.

Source commit: `3cf1d5e`.

## Gate receipt

`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`:

- `1093 passed, 18 skipped, 574 subtests passed` — build, Bandit, Ruff and mypy
  phases passed.
- One failure: `tests/test_cdn_fallback_lane.py::CdnFallbackLaneTests::
  test_payload_is_valid_bash`. Neither that test nor `cdn_fallback_lane.ps1` is
  touched by this change, and the case passes in isolation (11 passed) and in
  combination with the files that precede it (50 passed). Under the full
  collection the MSYS `bash -n` subprocess fails after roughly 250 modules are
  imported, so this is a pre-existing Windows process-resource limit, not a
  regression from this change. It is recorded here rather than hidden.
- A separate environment issue was found and worked around during this
  session: pytest's session-finish cleanup of its own `garbage-*` temp
  directories trips the host bulk-delete guard (92–110 files > 50), which
  forces exit 1 even when every test passes. Running with an explicit
  `--basetemp` avoids that cleanup path; the failing CDN case above is
  independent of it.

## Projection

`-Apply` completed with:

- `BACKUP_DIR=/root/cpa-guardrails-backup-20261104T112613.309719727Z`
- `HEALTH_OK`, `READY_STATUS=200`, `GUARDRAILS_APPLIED`
- `models=11` (was 12), `has_retired_gpt_reserve=False`

Final strict doctor: `DOCTOR_CONTRACT_OK`, `admission-health=OK`,
`MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_DRIFT=none`,
`MODEL_CONTEXT_MISSING=none`, `catalog_oauth_missing=none`, and every
`projection-drift` line `MATCH`. `MODEL_ALIAS_CONFIG` no longer appears, which
is correct for an empty alias manifest.

## Live acceptance

Single attempt each, no retry, through the admission chain on
`127.0.0.1:8318`:

- **Negative (the user's requirement)**: `POST /v1/responses` with
  `model=gpt-reserve` returns HTTP 400
  `{"code":"model_not_found","message":"unknown provider for model gpt-reserve"}`.
  The name is refused before any upstream call. `ACCEPTANCE_RESULT=PASS`.
- **Positive**: `model=gpt-5.6-luna` returned HTTP 200, terminal
  `response.completed`, response model `gpt-5.6-luna`, 4 characters of output.
  A later repeat attempt returned an empty stream because the upstream answered
  `server_is_overloaded` (retry-after 320s) — the same single-account capacity
  condition documented separately for this deployment, not a routing defect.
  No retry was issued for it.

This is controlled route acceptance, not a claim about long-term model quality
or upstream capacity.

## Rollback

Do not use a Git revert as a substitute for host recovery. Under the existing
maintenance lock, restore `config.yaml`, `cpa_provider_routes.json`,
`cpa_policy.py`, and `cpa-admission.json` from the backup above, run the
admission integrity check, and restart through the guarded path. Reintroducing
`gpt-reserve` also requires restoring the non-empty `oauth_model_aliases` entry
and its `oauth.settings.codex` context pin in the repository manifest, otherwise
the next `-Apply` will clear the alias again.
