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

- `scripts/run_gates.ps1` (full), after the fixes in this slice: **`EXIT=0`** —
  `build` OK; `test` → **234 passed, 1 skipped, 247 subtests passed** in 213.4 s;
  `hotspot:bandit` clean; `lint:ruff` clean; `lint:format` 21 files already
  formatted; `type:mypy` clean (21 source files).
- Before the fixes the same gate reported `231 passed / 2 failed / 1 skipped /
  243 subtests`. Both failures reproduced on a clean tree (`git stash` +
  single-test run) and were local-environment artifacts rather than regressions;
  they are now fixed (see "Repository fixes carried in this slice") rather than
  waived. Neither touched CPA routing.
- `git diff --check`: passed. `scripts/remote/cpa-acceptance.py` is not in the
  gate's mypy target list; its 11 remaining annotation errors are pre-existing
  and the one introduced here was annotated.

## Controlled acceptance (disposable fixture on the host)

- Fixture, built and torn down on BWG: `mktemp -d /tmp/cpa-acceptance.XXXXXX`, the running
  v8.0.4 binary extracted with `docker cp cli-proxy-api:/CLIProxyAPI/CLIProxyAPI`,
  a `FIXTURE_ONLY` marker, the deployed `auto-update.sh` and `cpa-health.py`, and the
  projected `cpa_provider_routes.json` (sha `406a4567…ae12`). No `config.yaml`, no `auth/`,
  no credentials; the synthetic upstream listens on loopback 18318.
- Executed under `unshare --mount --net --fork` with the fixture bind-mounted at
  `/opt/cliproxyapi`. Final line: `ACCEPTANCE_RESULT=PASS`.
  - overload → 1 upstream call, capacity marker present;
  - cooldown → 503 with **0** upstream calls (no amplification);
  - after 62 s → 200 `response.completed`, 1 upstream call, same process;
  - `cpa-health.py generation` → `HEALTH_OK`;
  - update scenarios `start_fail` exit 1 (rollback), `model_exposure` exit 1 (rollback),
    `transient` exit 10 (UNVERIFIED kept, no rollback), `success` exit 0.
- Manifest-relevant readouts: `assert_catalog_contract` passed; the fixture CPA registered
  12 IDs (the fixture declares no OAuth auth file and no image route) with
  **`gpt-5.6-sol` absent**; `bare_diag` consistent in every scenario; `sol_diag` =
  `200 gpt-6-sol stop`.
- Fixture directory removed by exact path; no fixture CPA process remains.

## Real-traffic acceptance (public 8443 → nginx → admission 8318 → CPA v8.0.4)

Non-OAuth only; the single subscription OAuth account was not consumed.

- `glm-5.3-flash` plain → 200, 3.6 s, `finish=length` (`max_tokens=64` truncation,
  not a failure).
- `glm-5.3-flash` stream → 200, **TTFB 2383 ms**, total 2882 ms, `finish=stop`
  (progressive framing holds: TTFB << total).
- `gpt-6-sol-input` → 502 `upstream_error`; `gpt-6-astra` → 502 `upstream_error`. Both are
  slot-1 (`ai.input.im`) routes whose upstream is currently unavailable. Pre-existing and
  unchanged by this transaction — the alias set is identical, only the retired bare name
  left — and the 2026-09-29 root-cause audit reached the same conclusion.
- `gpt-5.6-sol` → **400 `invalid_request_error` / `model_not_found`**, identical in shape to
  `glm-5.3-flashx` (a name never exposed). This is the direct positive acceptance of the
  change: the retired name now fails fast instead of being routed.

## Repository fixes carried in this slice

- `test_scripts.py::_bash_path` now resolves the mount prefix per launcher (WSL
  `/mnt/<drive>`, Git Bash `/<drive>`) instead of handing Git Bash the raw Win32 form. The
  Win32 form breaks any utility that rejects an embedded drive prefix — observed on this
  host as `[safe-delete][SAFE_DELETE_INVALID_PATH] embedded drive prefix is not allowed`,
  which made `test_cpa_prune_backups_keeps_newest_backup_dirs` remove nothing and fail.
- `test_scripts.py::test_v2ray_agent_script_updater_only_replaces_management_script` invoked
  a bare `"bash"`, which on some Windows hosts resolves to the WSL launcher and is blocked,
  leaving `stdout=None` and a `TypeError` instead of a readable failure. It now uses the
  existing `_resolve_bash()` / `_bash_command()` helpers like the other bash assertions, and
  concatenates `or ""` so a failed spawn still reports.
- `test_scripts.py`: the bash harnesses share `BASH_HARNESS_TIMEOUT_SECONDS = 90`. A Git Bash
  login shell alone costs ~3 s on Windows and the first `rm` through the host's safe-delete
  wrapper adds ~20 s, so the hardcoded 30 s sat on the edge and flaked under load. The
  assertions themselves are unchanged.
- `scripts/remote/cpa-acceptance.py`: the overload assertion pinned HTTP 503, which only held
  while `codex.stream-bootstrap-buffering` was on. With buffering off (production since
  2026-09-28) CPA has already committed the response headers when the upstream failure
  arrives, so it relays the upstream's 200 with the capacity marker inside the body and
  `cpa-admission` classifies it (observed in production as `status=200 capacity=true`). The
  expected status is now derived from the fixture config
  (`expected_overload_status()`), keeping the assertion exact for either shape; the
  invariants that matter — one upstream call, the marker present, zero amplification during
  cooldown, same-process recovery — are untouched. Pinned by a new unit test covering both
  contract shapes plus malformed/missing configs.

## Cockpit sidecar catalog (separate layer, local machine)

- `C:\Users\sciman\.antigravity_cockpit\codex_model_providers.json`: removed `gpt-5.6-sol`
  from the `fq.sciman.top` provider's `modelCatalog` (14 → 13). Backup
  `codex_model_providers.json.before-gpt56-sol-retire-20260930-010222.bak`.
  Byte-minimal: 7469 → 7448 bytes, LF preserved, 6 providers intact and every non-catalog
  field (including the plaintext API keys) untouched. No whole-entry dumps were printed or
  logged at any point.
- The other providers' catalogs still list `gpt-5.6-sol`; those are per-upstream offerings,
  not the desktop-facing aggregate, and the upstreams do serve that name.
- `gpt-6-sol-input` is exposed by the gateway but was deliberately **not** added to the
  desktop catalog: its slot-1 upstream currently returns 502, so adding it would offer a
  known-failing entry.
- Cockpit projects this file at startup, so a restart is needed before the desktop reflects
  it; Cockpit may also rewrite it on its own next write.

## Concurrency note

- A parallel session upgraded this host's CPA `v8.0.2 → v8.0.4` (container
  recreated 16:16:41Z) while this change was being prepared, and committed its own
  evidence plus this session's raw doctor/gates logs (`de62b6c`, `b1f3831`).
  This transaction ran against v8.0.4 and re-projected only
  `cpa_provider_routes.json` and the slot-1 `config.yaml` models; `config.yaml`
  was byte-identical across that upgrade, so the two changes do not interact.
  The guardrail transactions share `/run/vps-ssh-launcher-maintenance.lock`, so
  they cannot interleave.

## Final read-only doctor (post-acceptance closeout)

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg`
  → `DOCTOR_CONTRACT_OK`, `EXIT=0`. Raw output:
  `outputs/doctor-final-20260930.txt`.
- All nine projected files `drift … MATCH`; `MODEL_IDS_UNKNOWN=none`;
  container `v8.0.4@sha256:72205ea2…` `restart=0`; `oauth_monitor=OK`,
  `luna_state=available`, `admission-health=OK`, `POLICY_OK`,
  `semantic-policy=OK`, `nginx-syntax=OK`.
- Catalog at this point: 12 IDs. `config.yaml` SHA-256 is
  `bffa1db1…58c8`, **byte-identical to the post-apply projection**, so the
  difference from the 14-ID post-apply catalog is not config drift: `gpt-6-astra`
  and `gpt-6-sol-input` dropped out of `/v1/models` at runtime because their
  slot-1 (`ai.input.im`) upstream is unavailable (the 502s recorded under
  "Real-traffic acceptance"). The two names remain declared in the manifest and
  in `config.yaml`; `MODEL_IDS_UNKNOWN=none` still holds because a cooldown may
  only remove IDs, never add an unregistered one.

## Not covered

- The Cockpit sidecar projection `~/.codex/cockpit-model-catalog.json` (what the
  desktop actually renders) is regenerated by Cockpit at startup and was not
  edited — only its sidecar source was. A Cockpit restart is required.
- The OAuth lane (`gpt-6-luna` / `gpt-5.6-luna` / `gpt-6-sol`) was not consumed by
  any acceptance probe, and streaming was only measured on the non-OAuth gate
  target `glm-5.3-flash`.
- `gpt-6-astra`, `deepseek-v4.1-flash` and `gpt-6-sol-input` remain declared on slot 1
  per the user's list, and all three currently return 502 `upstream_error` from the
  `ai.input.im` upstream. That is an upstream availability question, not a routing or
  configuration defect: the names are registered, routed, and answered — the upstream
  rejects them. Removing them is the user's call (the 2026-09-29 audit flagged the same
  three; this round only retired the one the user's list omitted).
