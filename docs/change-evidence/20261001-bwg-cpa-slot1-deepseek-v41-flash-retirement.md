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

## Same-day reversal: re-addition (commit `260779d`), upstream churn, and LIVE_ACCEPTED

- Requested change (user, 2026-10-01, later the same day): re-add
  `deepseek-v4.1-flash` to slot 1; catalog 10 → 11. Repo change commit
  `260779d` (7 files, +19/-12): manifest slot-1 back to 4 entries (same
  position as before the retirement), `PROVIDER_MATRIX_TAIL` 7 → 8,
  guardrails marker back to `has_ai_input_im_bare_deepseek_v41_flash`,
  acceptance fixture mirror, relay-soft smoke back to 3 channel probes,
  manifest shape pin, both runbooks. No exclusion-list tombstone; admission
  lanes/OAuth unchanged. Gates all green (242 passed, 1 skipped, 260
  subtests — includes the parallel session's new admission tests).
- `-Apply` (2026-10-01T10:05Z): `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
  zero `ROLLBACK`, backup
  `/root/cpa-guardrails-backup-20261001T100512.257023604Z`,
  `models=11`. Post-apply doctor `DOCTOR_CONTRACT_OK`, drift all `MATCH`
  (HEAD `260779d`).
- **First probe round failed upstream-side; root cause = user's own group
  switch on the ai.input.im relay.** Single-shot via loopback 8318:
  `deepseek-v4.1-flash` → upstream-relayed 404 `model_not_found` ("not
  supported by any configured account in this group") in 0.23 s;
  `gpt-6-astra` → 502 `upstream_error` in 0.34 s. Read-only upstream
  diagnosis: `ai.input.im /v1/models` had collapsed to 4 IDs
  (`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-astra`) — the
  relay's group switching dropped the DeepSeek and 6.x-Sol families. CPA
  side was correct: `models=11`, error bodies relayed verbatim (CPA log
  `conductor_execution.go` lines attribute provider/model precisely).
- User then switched the group back; the upstream catalog recovered
  (13 IDs incl. `deepseek-v4.1-flash`, `gpt-6.1-sol`, `gpt-6-astra`,
  `gpt-image-2.5`). Fresh single-shots: `gpt-6-astra` 200/stop/OK 5.38 s
  (channel healthy); `deepseek-v4.1-flash` → 0.01 s local 503
  `auth_unavailable` — CPA v8.0.8 had seeded a **sticky per-model
  unavailability** from the earlier upstream 404 (CPA logs prove no further
  upstream attempts; the state far outlives the 60 s transient cooldown;
  model hidden from `/v1/models` availability view). Upstream direct probe
  (bypassing CPA) confirmed the generation plane itself: 200/stop/OK
  1.27 s.
- Recovery: `docker restart cli-proxy-api` (the documented way to wipe
  memory-only state; same routine restart `-Apply` performs). After
  restart: `DS41_RELISTED=True`, and the gateway full-chain single-shot
  `deepseek-v4.1-flash` → **200, `deepseek-v4.1-flash` echo, finish=stop,
  content OK, 1.25 s — LIVE_ACCEPTED**.
- Post-restart strict doctor: `DOCTOR_CONTRACT_OK`, `MODEL_IDS_UNKNOWN=none`.
  Transient at write time: OAuth `gpt-6.1-sol` temporarily hidden
  (`luna_state=available_partial`) because a client request 0 s after
  restart hit a ChatGPT `server_is_overloaded` episode (NO_MORE_RETRY),
  seeding the same sticky per-model state. OAuth aliases are optional by
  design; the state is memory-only and relists on cooldown expiry. No
  further restarts/probes on the OAuth lane per the risk-control discipline.
- New operational fact recorded: **CPA v8.0.8 turns a single upstream 404
  (`model_not_found`) or 502 (`server_is_overloaded`) into a sticky
  per-model catalog hide + local fast-fail** — not the 60 s transient
  cooldown. Recovery = fix the upstream cause, then one container restart.
  Explains "disappearing catalog IDs" observations on v8.0.8.
- Rollback: `git revert 260779d` + re-`-Apply`; remote backup
  `/root/cpa-guardrails-backup-20261001T100512.257023604Z`.
