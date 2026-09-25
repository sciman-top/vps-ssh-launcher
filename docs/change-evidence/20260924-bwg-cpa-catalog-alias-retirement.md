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

## 20260925 follow-up: restore the gpt-5.6-luna OAuth alias

- User instruction: the OAuth lane serves both `gpt-6-luna` and the
  compatibility alias `gpt-5.6-luna` again; `glm-5.3-flashx` stays retired.
  Target catalog is back to 12 IDs.
- Mechanism: the manifest `oauth_routes` now declares both Luna names, and
  `gpt-5.6-luna` was removed from `oauth_exclusions` (it stays in
  `codex_api_key_exclusions`, which policy requires so API-key lanes can never
  serve it). Because the previous apply had written an exact
  `gpt-5.6-luna` exclusion into `oauth-excluded-models.codex` and the apply
  flow previously only appended exclusions, the embedded config transaction
  now drops exact-name exclusions for aliases the manifest declares as OAuth
  routes; broader wildcard blockers still refuse.
- `cpa-health.py` needed no further change: allowed/matrix/expected sets are
  manifest-derived, so both aliases are optional matrix members automatically.
- Local and remote `cpa_provider_routes.json` SHA-256 matched:
  `2e8592e784453020281a5af7a0b517f4b131da14f0992200ac7108d5aa44b1f9`.
  The projected `cpa_policy.py` is the newer committed version from the
  parallel 9/25 hardening commits; it validated the dual-alias manifest.
- Backup-first apply completed with `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
  `HEALTH_OK`; rollback backup:
  `/root/cpa-guardrails-backup-20260925T053117.752095491Z`.
- Post-apply loopback catalog summary: exactly 12 IDs; `has_bare_luna=True`,
  `has_bare_gpt6_luna=True`, `has_retired_glm_5_3_flashx=False`, no `r1/`
  prefix, no retired CIII names.
- Fresh read-only strict doctor: `DOCTOR_CONTRACT_OK`,
  `catalog_gpt6_luna=present`, `luna_state=available`, `oauth_monitor=OK`,
  `model_substitution_warnings_7d=0`.
- Repository verification: full gates passed 158 tests, 1 skipped, 190
  subtests with Bandit, Ruff lint/format, and mypy clean; guardrails-focused
  tests re-passed after the catalog-summary label revert; `git diff --check`
  passed.
- This restores LIVE_ACCEPTED catalog visibility for both Luna names. No
  provider generation request targeted the alias in this transaction; natural
  use is the acceptance path.

## 20260925 acceptance rounds for the alias restore

### Controlled live matrix (production, PASS for this change's targets)

- `python3 /opt/cliproxyapi/cpa-health.py generation-all` against production,
  one request per route, zero retries (contract held; `MATRIX_EXIT=10` reflects
  the known upstream-side relay failures below).
- This change's targets: `gpt-6-luna` HTTP 200, `finish=stop`, 1462 ms;
  `gpt-5.6-luna` HTTP 200, `finish=stop`, 1039 ms. Both Luna names are
  LIVE_ACCEPTED for generation after the restore.
- Also 200/stop: `gpt-6-sol` (8551 ms), `gpt-6-astra` (17108 ms),
  `gpt-5.6-terra` (3306 ms), `glm-5.3` (1432 ms), `glm-5.3-flash` (1879 ms),
  `deepseek-flash` (744 ms), `deepseek-v4-pro` (1642 ms).
- Known upstream-side failures, unchanged from the 2026-09-24 record and not
  retried: `gpt-6-astra-cii` and `gpt-6-sol-cii` network timeout at 120 s
  (codex.ciii.club relay), `gpt-6-sol-91` 504 at 62 s (slot 3 relay upstream).

### Isolated fixture acceptance (harness drift found; catalog contract passes)

- Full-run fixture harness under `unshare --mount --net --fork` with the
  v7.3.16 binary extracted from the running container; fixture directory also
  carries the deployed `cpa_provider_routes.json`, which the manifest-derived
  `cpa-health.py` now requires (new dependency since the fixture harness was
  last run).
- Overload path passed end to end: upstream 503 → CPA 503 with exactly one
  upstream call → cooldown-blocked immediate retry → 62 s wait → 200 with
  `response.completed` in the same process.
- `assert_catalog_contract` and an instrumented catalog dump passed: the
  fixture CPA registered exactly the 12 expected IDs (both Luna names
  included, no extras) — the manifest/catalog portion of this change is
  fixture-verified.
- The run then failed at `actual_health`: a raw `/chat/completions` probe
  returned the synthetic upstream's responses-format SSE passed through
  verbatim, so `cpa-health.py generation` finds no `choices` and exits 10
  (UPSTREAM_UNAVAILABLE). Root cause: the fixture synthetic upstream speaks
  responses-API SSE on every POST; current v7.3.16 openai-compat behavior no
  longer translates that into chat JSON. This is pre-existing harness/binary
  drift (harness last green on an older minor), not a regression of this
  change; production chat paths were verified live in the matrix above.
- Follow-up slice (not part of this change): modernize the fixture synthetic
  upstream to answer `/chat/completions` with chat-completion payloads so the
  full fixture run is green on v7.3.16+.
- All fixture temp directories and diagnostic copies were removed by exact
  path after the runs; no fixture CPA process remains.

## 20260925 follow-up slice: fixture harness green on v7.3.16

- Fixed the synthetic upstream in `cpa-acceptance.py`: it now answers
  `/chat/completions` with a real chat completion payload (JSON, or standard
  chat chunks plus `data: [DONE]` when the request sets `stream`), while the
  `/responses` lane keeps the responses-format SSE contract. A new unit test
  (`test_cpa_acceptance_synthetic_upstream_matches_wire_contract`) pins all
  four wire cases: chat JSON echo, chat chunk stream, responses SSE, and the
  http503 overload branch.
- Fixed cross-scenario cooldown contamination in `cpa-update-acceptance.py`:
  each update scenario now clears persisted `auth/*.cds` at the scenario
  boundary. The fixture intentionally runs `save-cooldown-status=true`, the
  known #5639/#5770 class where a persisted cooldown outlives its expiry
  across restarts; the `transient` scenario's http503 window otherwise left a
  `.cds` on `glm-5.3-flash` that made the `success` scenario's pre-update
  generation gate unpassable (observed: 17 retries over the full 150 s window,
  catalog missing exactly that ID). Production avoids this class entirely via
  `save-cooldown-status=false`; clearing at scenario boundaries is
  decontamination, not a weakened assertion.
- Repository gates: `git diff --check` clean; 50 passed, 156 subtests; full
  gate suite passed (build, pytest, Bandit, Ruff lint/format, mypy).
- Remote fixture full run under `unshare --mount --net --fork` with the
  v7.3.16 binary and the deployed route manifest:
  overload → cooldown-blocked → 62 s → same-process recovery 200;
  `actual_health` exit 0 `HEALTH_OK`; update scenarios `start_fail` exit 1
  (rollback, old compose restored, rollback logged), `model_exposure` exit 1
  (rollback), `transient` exit 10 (UNVERIFIED kept, no rollback),
  `success` exit 0 — final line `ACCEPTANCE_RESULT=PASS`.
- All fixture directories and diagnostic copies were removed by exact path
  after the run; no fixture CPA process remains. The known `sol_diag` 400 in
  pre-state diagnostics is the probe intentionally using the unexposed
  upstream raw ID `gpt-5.6-sol`; it appears in every scenario including
  passing ones.

## 20260925 slot-1 image route addition (gpt-image-2.5)

- Requested change: expose bare client model `gpt-image-2.5` on slot 1
  (ai.input.im). Target catalog: 13 IDs.
- Read-only upstream preflight (local, key never printed): `https://ai.input.im/v1/models`
  returned 12 IDs including `gpt-image-2.5` (siblings `gpt-image-2`,
  `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst` were NOT projected; only the
  exact requested name).
- Manifest: slot 1 gains the model plus `optional_models` membership (readiness
  and relay-soft stay independent of it, same precedent as `gpt-6-sol`) and a
  new provider-level `image_models` declaration; `gpt-image-2.5` added to both
  `oauth_exclusions` and `codex_api_key_exclusions` so the gpt- prefixed route
  stays pinned to ai.input.im.
- `cpa_policy.py` now validates `image_models` as a list of declared provider
  aliases (fail-closed, mirroring `optional_models`).
- `cpa-health.py` derives image aliases from the manifest and skips them in
  generation/quality matrices (the exact-OK chat contract is meaningless for
  image models); a cataloged image model emits
  `ROUTE_PREPARED model=gpt-image-2.5 status=skipped kind=image`. Readiness,
  relay-soft, the scheduled gate, and the cache canary are unchanged.
- New tests: slot-1 manifest contract assertions and a matrix-skip behavior
  test (13-ID catalog → 12 probed targets, no chat call to the image alias).
- Repository gates: `git diff --check` clean; 55 passed, 159 subtests; full
  gate suite passed (build, pytest, Bandit, Ruff lint/format, mypy).
- Backup-first apply completed with `GUARDRAILS_APPLIED`, `READY_STATUS=200`,
  `HEALTH_OK`; rollback backup:
  `/root/cpa-guardrails-backup-20260925T123134.523032091Z`. Post-apply catalog
  summary: exactly 13 IDs with `has_ai_input_im_image_gpt_2_5=True`.
- Controlled single probe (loopback, one request, no retry, response body not
  echoed): HTTP 503, `error.type=server_error`, `error.code=internal_server_error`.
  The route is cataloged and routed, but the upstream rejected this
  chat-completions-shaped request; whether the model requires a dedicated
  images endpoint or different payload is an upstream behavior question left
  to the owner. No retry was performed (zero-retry contract).
- Post-apply strict doctor: `semantic-policy=OK`, `oauth_monitor=OK`,
  `cooldown_state=none`, `catalog_gpt6_luna=present`, `luna_state=available`,
  `nginx-syntax=OK`; `DOCTOR_CONTRACT_FAILED` was observed only in the
  projection-drift section, which anchors to the HEAD blobs and cannot pass
  until this change is committed (the gate landed mid-session from a parallel
  hardening commit); it was re-run green after the commit below. All other
  sections passed before the commit.

### 20260925 image-route diagnosis follow-up (read-only)

- The error dump of the 503 probe preserved the upstream's own explanation:
  `model gpt-image-2.5 is only supported on /v1/images/generations and
  /v1/images/edits` — the model exists upstream; chat completions is simply
  the wrong endpoint. CPA v7.3.16 registers `/v1/images/generations` and
  `/v1/images/edits` (server_routes.go) and the openai-compat executor relays
  them to `{base-url}/images/generations`, so the gateway can carry image
  requests for this route.
- One controlled probe via the gateway `/v1/images/generations` (loopback,
  single request, body not echoed): HTTP 403,
  `Image generation is not enabled for this group`. The blocker is the
  ai.input.im account-group permission for the slot-1 key, not the route, the
  model, or this deployment. Enabling image generation for that key's group
  upstream (or supplying a group-enabled key in slot 1) makes the route
  usable with no repository change; until then the bare name stays cataloged
  and requests fail fast (403/503 with a 60 s in-memory cooldown, harmless).
