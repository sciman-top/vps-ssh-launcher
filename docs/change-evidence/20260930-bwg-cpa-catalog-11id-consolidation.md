# BWG CPA bare-name catalog consolidation to 11 IDs + 443-fallback lane-aware contract

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change (user, 2026-09-30): the client-facing bare-name catalog must be
  exactly
  - ChatGPT Plus OAuth: `gpt-6-luna` / `gpt-6.1-sol`
  - slot 1 (`ai.input.im`): `gpt-6-astra` / `gpt-image-2.5` /
    `deepseek-v4.1-flash` / `gpt-6.1-sol-input`
  - slot 2 (`codex.ciii.club`): `gpt-6-astra-cii`
  - slot 3 (`35.213.82.91:8003`): `gpt-6.1-sol-91`
  - slot 4 (BigModel): `glm-5.3-flash` / `glm-5.3`
  - slot 5 (DeepSeek): `deepseek-flash`
- Deltas vs the then-current 15-ID catalog: OAuth `gpt-5.6-luna` and `gpt-6-sol`
  retired; slot 1 `gpt-6-sol-input` (upstream `gpt-6-sol`) replaced by
  `gpt-6.1-sol-input` (upstream `gpt-6.1-sol`); slot 2 `gpt-6-sol-cii` retired;
  slot 3 `gpt-5.6-terra` retired and `gpt-6.1-sol-91` (upstream `gpt-6.1-sol`)
  added; slot 5 `deepseek-v4-pro` retired. Retired names stay as tombstones in
  the exclusion lists (readiness fail-closed blocks resurrection).
- Upstream availability was probed read-only from the VPS before editing
  (slot keys read from the owner-only `config.yaml`, never printed):
  `ai.input.im /v1/models` total=13 with `gpt-6.1-sol` present;
  `http-bridge-8003` total=42 with `gpt-6.1-sol` present;
  `codex.ciii.club` total=7 without `gpt-6.1-sol` (consistent with keeping
  only the astra alias there).

## Blocking finding: parallel 443-fallback lane (minimal absorb)

The pre-apply read-only doctor baseline (clean tree, HEAD `9ed4026`) failed
`DOCTOR_CONTRACT_FAILED` on four `==nginx-merged-contract==` counts
(random-route / shared-admission-proxy / throttle-retry-after-map /
shared-account-admission-proxy). Root cause: an undocumented remote-only
change deployed earlier on 2026-09-30 (~10:24-10:36+08) added
`/etc/nginx/conf.d/00-cpa-443-http.conf` (private `cpa_*443` map/zone mirrors)
and a gateway mirror location + `/_cpa_auth` inside `subscribe.conf` (port
33387 TLS). The repo had zero record of it and the tree was clean; the apply
rollback chain (`assert_merged_nginx_route_contract`, counts == 1) would have
self-reverted any `-Apply`.

Absorbed minimally instead of re-doing or reverting the lane: the doctor and
apply contracts now derive the expected mirror-shape counts as
`1 + lane_count`, with `log_format cpa_safe443` as the lane presence signal,
and the doctor reports `gateway-443-fallback-lane=`. With the lane absent the
expectations stay exactly 1, so the fail-closed property is unchanged. Deep
absorption of the lane (fail2ban scope, runbook contract, decision record)
remains with whoever owns that change; `cpa-gateway.conf` itself was not
touched (drift MATCH before and after).

## Repository change

Commit `d42ce17` (11 files, +151/-252):

- `scripts/remote/cpa_provider_routes.json` (source of truth): OAuth lane 4 →
  2 aliases; slot 1 sol entry renamed to upstream `gpt-6.1-sol` / alias
  `gpt-6.1-sol-input`; slot 2 down to the astra alias; slot 3 to
  `gpt-6.1-sol`/`gpt-6.1-sol-91`; slot 5 to `deepseek-flash`.
  `oauth_exclusions` += `gpt-5.6-luna`, `gpt-6-sol` (upstream still lists
  both — without tombstones CPA would re-register them),
  `gpt-6.1-sol-input`, `gpt-6.1-sol-91`. `codex_api_key_exclusions` +=
  `gpt-6.1-sol-input`, `gpt-6.1-sol-91` (uniform both-list convention).
- `cpa_catalog_expectations.py`: `OAUTH_ROUTE_ALIASES` 4 → 2,
  `ADMISSION_LANE_MODELS["deepseek-official"]` 2 → 1, `PROVIDER_MATRIX_TAIL`
  re-ordered around the new aliases.
- `scripts/remote/cpa-admission.json`: `chatgpt-oauth` and
  `deepseek-official` lane model lists synced.
- `scripts/cpa_bwg_guardrails.ps1`: both admission-projection `expected`
  dicts, `CODEX_OAUTH_ROUTES_READY` text, catalog-summary markers (new names
  present / retired names absent), lane-aware merged-nginx contracts (doctor +
  apply), and the luna-state/OAuth-deactivate comments refreshed.
- `scripts/remote/cpa-acceptance.py`: fixture manifest mirror synced
  (including the codex-api-key OAuth stand-in); `assert_catalog_contract`
  gained a retirement regression (`readiness_for(required | {"gpt-6-sol"}) ==
  20`).
- `scripts/remote/cpa-update-acceptance.py`: `sol_diag` probe targets
  `gpt-6.1-sol-input`.
- `scripts/remote/cpa-health.py`: stale comment example only (all sets are
  manifest-derived).
- `test_scripts.py`: six identical synthetic catalog literals collapsed into
  `HEALTH_FIXTURE_CATALOG_IDS` (derived from the oracle), doctor
  `allowed_ids` and quality-eval models derived, matrix 404 example moved to
  `gpt-6-luna`, 502 example to `gpt-6.1-sol-91`, relay-soft echoes to
  `gpt-6.1-sol`, luna-state partial fixture to `gpt-6.1-sol`, suppression-line
  counts derived from `len(OAUTH_ROUTE_ALIASES)`, quarantine negative filter
  moved to `gpt-6.1-sol`, manifest shape pins synced, doctor output pin made
  lane-aware.
- `test_cpa_admission.py`: lane-routing examples moved to `deepseek-flash`
  and `gpt-6.1-sol-91`.
- `docs/runbooks/cpa-gateway.md`, `docs/runbooks/cpa-oauth-luna-slot.md`:
  catalog descriptions synced with retirement dates.

Gates: `run_gates.ps1` all green (234 passed, 1 skipped, 248 subtests;
bandit/ruff/mypy clean); `git diff --check` clean.

## Projection evidence

- Sequence followed the checklist: full gates → commit `d42ce17` (clean tree)
  → `-Apply` → doctor.
- `-Apply` (2026-09-30T14:38Z): `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
  `HEALTH_OK`, zero `ROLLBACK` lines, all `PROJECTION_HASH_VERIFIED`.
  Backup: `/root/cpa-guardrails-backup-20260930T143830.728017833Z`.
  Catalog summary: `models=11`; all five retired markers `False`
  (`has_bare_luna` [gpt-5.6-luna], `has_retired_slot1_gpt6_sol_input`,
  `has_retired_ciii_gpt6_sol`, `has_retired_slot3_gpt56_terra`,
  `has_retired_deepseek_v4_pro`); all new/kept markers `True`.
- Post-apply strict doctor: `DOCTOR_CONTRACT_OK` (exit 0).
  `MODEL_IDS=deepseek-flash,deepseek-v4.1-flash,glm-5.3,glm-5.3-flash,gpt-6-astra,gpt-6-astra-cii,gpt-6-luna,gpt-6.1-sol,gpt-6.1-sol-91,gpt-6.1-sol-input,gpt-image-2.5`
  (exactly the 11 requested), `MODEL_IDS_UNKNOWN=none`,
  `catalog_oauth_aliases=gpt-6-luna,gpt-6.1-sol`, `catalog_oauth_missing=none`,
  `luna_state=available`, `oauth_monitor=OK`, `admission-health=OK`,
  projection drift 9x `MATCH` (HEAD `d42ce17` anchored),
  `gateway-443-fallback-lane=1` with mirror counts 2/2/2/2 all OK,
  `nginx-syntax=OK`.

## Live acceptance (loopback 8318, admission full chain, single shot, no retry)

- `gpt-6.1-sol-input`: 200, responded model `gpt-6.1-sol` (upstream echo
  correct), finish=stop, content OK, 15.43 s — LIVE_ACCEPTED.
- `gpt-6.1-sol-91`: 200, responded model `gpt-6.1-sol`, finish=stop, content
  OK, 8.00 s — LIVE_ACCEPTED.
- `gpt-6-luna`: 200, `gpt-6-luna`, stop, OK, 2.11 s — LIVE_ACCEPTED.
- `gpt-6.1-sol`: 200, `gpt-6.1-sol`, stop, OK, 2.06 s — LIVE_ACCEPTED.
- `gpt-6-sol-input` (retired): 400 `model_not_found`
  ("unknown provider for model gpt-6-sol-input") in 0.00 s — clean fast-fail,
  no upstream call.

## Rollback

- Repo: `git revert d42ce17` then re-run `-Apply` (projection is
  declarative; the doctor drift gate anchors the HEAD blob).
- Remote: restore from `/root/cpa-guardrails-backup-20260930T143830.728017833Z`
  (pre-apply `config.yaml` / route manifest / admission config; the
  transaction's own `restore_all` covers partial failures, and none fired).

## Residual / notes

- The 443-fallback lane is contract-tolerated but not yet deeply absorbed:
  no decision record, no fail2ban/runbook contract for the 33387 entry path.
  Owner unknown (remote-only change, no commit). Until absorbed, treat
  `gateway-443-fallback-lane=` in doctor output as the presence signal.
- ChatGPT desktop pickers show cached/manual lists: clients that want the new
  bare names (`gpt-6.1-sol-input`, `gpt-6.1-sol-91`) must add/re-pick them
  manually; retired names fail fast (400) if still selected.
- Desktop catalog claim "15 items" in the 2026-09-30 21:53+08 cockpit runbook
  commit predates this consolidation and is now stale (11 IDs).
- OAuth `oauth_days_left=5` at apply time; restart was routine (same shape as
  the monthly maintenance restart) and `oauth_refresh_failures_7d=0` after.
