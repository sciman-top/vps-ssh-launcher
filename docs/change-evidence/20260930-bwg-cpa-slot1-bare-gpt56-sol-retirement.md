# BWG CPA slot-1 bare-name retirement: `gpt-5.6-sol`

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Requested change (user, 2026-09-30): the client-facing bare-name catalog must be
  exactly
  - ChatGPT Plus OAuth: `gpt-6-luna` / `gpt-5.6-luna` / `gpt-6-sol`
  - slot 1 (`ai.input.im`): `gpt-6-astra` / `gpt-image-2.5` /
    `deepseek-v4.1-flash` / `gpt-6-sol-input`
  - slot 2 (`codex.ciii.club`): `gpt-6-astra-cii` / `gpt-6-sol-cii`
  - slot 3 (`35.213.82.91:8003`): `gpt-5.6-terra`
  - slot 4 (BigModel): `glm-5.3-flash` / `glm-5.3`
  - slot 5 (DeepSeek): `deepseek-flash` / `deepseek-v4-pro`
- Line-by-line comparison against the checked-in route manifest found **exactly
  one delta**: slot 1 additionally declared the bare name `gpt-5.6-sol` (same
  string as its upstream ID). Confirmed with the user before touching the host,
  then retired.
- The upstream `gpt-5.6-sol` entry is **not** removed: slot 2's `gpt-6-sol-cii`
  alias still maps to it, so `oauth_exclusions` and `codex_api_key_exclusions`
  keep the name pinned (a superset there is allowed by `cpa_policy.py`; only
  `gpt_routes ⊆ oauth_exclusions` and
  `(gpt_routes ∪ oauth_aliases) ⊆ codex_api_key_exclusions` are required).
- OAuth lane, slots 2/3/4/5, the admission lanes (`cpa-admission.json`) and the
  two exclusion lists are unchanged. `cpa_policy.py`, `cpa-health.py` and the
  doctor derive every allowed/matrix/expected model set from the manifest, so
  only the manifest, its mirrors, and the catalog-summary probe needed edits.

## Repository change

- `scripts/remote/cpa_provider_routes.json`: slot-1 `models` 5 → 4 entries
  (`gpt-6-astra`, `deepseek-v4.1-flash`, `gpt-6-sol-input`, `gpt-image-2.5`).
  `optional_models` / `image_models` stay `["gpt-image-2.5"]`.
- `test_scripts.py`: slot-1 alias assertion, the doctor `allowed_ids` fixture,
  and every synthetic catalog/required/matrix list that mirrored the old slot-1
  set. The slot-2 upstream-name mapping (`gpt-6-sol-cii` → `gpt-5.6-sol`) is
  deliberately kept.
- `scripts/remote/cpa-acceptance.py`: the fixture's `ai.input.im` model list was
  mirrored. Required, not cosmetic: the fixture CPA's catalog must not contain an
  ID the manifest no longer allows, or `cpa-health.py generation` returns exit
  20 and the harness's pre-update gate can never pass.
- `scripts/remote/cpa-update-acceptance.py`: the `sol_diag` probe now targets
  `gpt-6-sol-input` instead of the retired bare name (diagnostic output only, not
  asserted).
- `scripts/cpa_bwg_guardrails.ps1`: the apply-time catalog summary line
  `has_ai_input_im_bare_gpt56_sol` became
  `has_retired_ai_input_im_bare_gpt56_sol` (informational print, not part of the
  fail-closed contract).
- `docs/runbooks/cpa-gateway.md`, `docs/runbooks/cpa-oauth-luna-slot.md`: slot-1
  bare-name lists synced.
- Commit: `a8c0f6e`.

## Projection evidence

- Pre-apply read-only strict doctor (2026-09-29T16:22Z): container
  `v8.0.4@sha256:72205ea2…` `restart=0`; catalog 15 IDs
  (`gpt-5.6-sol`, `gpt-6-astra` and `deepseek-v4.1-flash` were all back in
  `/v1/models` after the v8.0.4 recreate); `MODEL_IDS_UNKNOWN=none`;
  `oauth_monitor=OK`, `luna_state=available`, `admission-health=OK`,
  `POLICY_OK`, `semantic-policy=OK`. The only failure was
  `drift=cpa_provider_routes.json MISMATCH want=406a4567… got=dd1c1a2b…` — the
  expected "repo moved ahead of the last projection" shape before `-Apply`.
- Apply:
  `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`
  → `GUARDRAILS_APPLIED`, `READY_STATUS=200`, `HEALTH_OK`, `EXIT=0`.
- Rollback backup: `/root/cpa-guardrails-backup-20260929T162429.264762539Z`
  (config.yaml, compose.yml, auto-update.sh, cpa_provider_routes.json,
  cpa-health.py, cpa_policy.py, admission script/config/unit, nginx conf,
  fail2ban filter/jail). The transaction auto-rolls back on any failed assertion.
- Post-apply loopback catalog summary: `models=14`; all fourteen requested
  aliases present; `has_retired_ai_input_im_bare_gpt56_sol=False`;
  `has_r1=False`; `has_slot3_gpt6_sol_91=False`;
  `has_previous_gpt56_sol_terra_aliases=False`; `has_ciii_retired_models=False`;
  `has_retired_glm_5_3_flashx=False`.
- Post-apply read-only strict doctor: `DOCTOR_CONTRACT_OK`, `EXIT=0`, all 9
  projected files `drift … MATCH`, `MODEL_IDS_UNKNOWN=none`, catalog 14 IDs,
  `oauth_monitor=OK`, `luna_state=available`, `admission-health=OK`,
  `POLICY_OK`, `semantic-policy=OK`, container `restart=0`.
- `cpa_provider_routes.json`: local LF-normalized and remote SHA-256 both
  `406a4567fe0007c8fe5cb2f98076bff83aac092bf2219d4dd63d24f8826aae12`
  (3071 bytes, was `dd1c1a2b…3c82` / 3130 bytes).
- `config.yaml`: `a127fda1…b142` → `bffa1db1fa4fbb5a278036860d68b46fe1fe0b15b64d4f772add2ac0f60658c8`
  (2950 → 2905 bytes; only the slot-1 `gpt-5.6-sol` model entry left).
- All other projected files byte-identical.

## Behavioral consequences

- A client calling `gpt-5.6-sol` now gets the standard unknown-model failure
  instead of a routed response. Every other route is unchanged.
- Readiness/health fail closed: a catalog containing the retired name returns
  exit 20, so a reappearance through any lane is a visible health failure rather
  than a silent route.

## Repository verification

- `scripts/run_gates.ps1` (full): `build` OK; `test` →
  **231 passed, 2 failed, 1 skipped, 243 subtests passed** in 176.9s.
  Both failures reproduce on a clean tree (`git stash` + single-test run) and are
  local-environment artifacts, not regressions:
  `test_cpa_prune_backups_keeps_newest_backup_dirs` (Git Bash prune fixture) and
  `test_v2ray_agent_script_updater_only_replaces_management_script`
  (bare `bash` resolves to WSL `bash.exe`, which this host's sandbox blocks, so
  `subprocess` returns `stdout=None`). Neither touches CPA routing.
  The gate run stops at the `test` step, so the remaining gates were run
  directly with the same interpreter: `bandit -q -r` clean, `ruff check` clean,
  `ruff format --check` 21 files already formatted, `mypy` clean (21 files).
- `git diff --check`: passed.

## Concurrency note

- A parallel session upgraded this host's CPA `v8.0.2 → v8.0.4` (container
  recreated 16:16:41Z) while this change was being prepared, and committed its own
  evidence plus this session's raw doctor/gates logs (`de62b6c`, `b1f3831`).
  This transaction ran against v8.0.4 and re-projected only
  `cpa_provider_routes.json` and the slot-1 `config.yaml` models; `config.yaml`
  was byte-identical across that upgrade, so the two changes do not interact.
  The guardrail transactions share `/run/vps-ssh-launcher-maintenance.lock`, so
  they cannot interleave.

## Not covered

- The Cockpit sidecar catalog (`codex_model_providers.json`, `fq` provider
  `modelCatalog`) is a separate layer and was **not** touched. Per the
  2026-09-29 session note the user restored `gpt-5.6-sol` there, so the desktop
  can still offer it; selecting it will now fail against CPA. Retiring it there
  is a separate decision.
- No public-path generation probe was issued by this transaction. The apply
  transaction itself performs the authenticated public probe, readiness and
  health checks, and the post-apply doctor re-reads the public route contract.
- `gpt-6-astra` and `deepseek-v4.1-flash` remain declared on slot 1 per the
  user's list even though the 2026-09-29 root-cause audit measured their
  `ai.input.im` upstream as unavailable; they are back in `/v1/models` after the
  v8.0.4 recreate, and their real generation behaviour was not re-measured here.
