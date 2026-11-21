# 2026-11-21 BWG CPA rollback first-step telemetry evidence

## 1. Goal and scope

- Close the two remaining process gaps from the 2026-11-21 admission rollback incident:
  1. `restore_all` must report the first failed restore step instead of only a boolean `ROLLBACK_FAILED`.
  2. The local systemd fixture must exercise the deployed systemd 255 behavior where `systemctl show /path/to/unit.timer -p ...` returns an empty value and the basename retry returns the loaded property.
- No model routing, OAuth credential, retry, admission RPM, cooldown, account, or egress setting was changed.
- No OAuth generation probe was sent.

## 2. Repository change

- Commit: `5cf4bc9 fix(cpa): identify first rollback failure step` (pushed to `origin/main`).
- [scripts/remote/cpa-guardrail-apply.sh](../../scripts/remote/cpa-guardrail-apply.sh):
  - `restore_all` now records the first caller line through `mark_rollback_failed`.
  - Failure output becomes `ROLLBACK_FAILED first_step=restore_all:<line-or-unknown>` while retaining the existing best-effort full restore behavior.
- [tests/test_cpa_guardrails_script.py](../../tests/test_cpa_guardrails_script.py):
  - Adds a behavioral first-failure probe proving later failures do not overwrite the first step.
  - Adds an apply-fragment probe where path-form `systemctl show` is empty and the unit-name retry supplies `TimersCalendar`, `RandomizedDelayUSec`, and `TimeoutStartUSec`.

## 3. Verification

### Repository

- Targeted probe: `2 passed, 66 deselected`.
- Full guardrails test file: `68 passed, 117 subtests passed`.
- Full closeout (`scripts/run_gates.ps1 -RunParallel`):
  - `CATALOG_CHECK=PASS`
  - build, bandit, ruff check/format, and mypy passed.
  - pytest: `1821 passed, 30 skipped, 893 subtests passed`.
- Push/post-push fetch: local `HEAD` and `origin/main` both equal `5cf4bc968e2d56a0a30d21dfafb0795962f63fa6`.

### BWG projection and controlled acceptance

- Fresh read-only preflight: `DOCTOR_CONTRACT_OK`; 17/17 projected runtime files `MATCH`; all active OAuth lanes `OPEN`; `credential_ban_signals_7d=0`; OAuth refresh failures `0`; cooldown state none.
- `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` exited 0 with `GUARDRAILS_APPLIED`.
  - Backup: `/root/cpa-guardrails-backup-20261121T105912.693338477Z`.
  - All 17 projection hashes verified.
  - Two transient curl errors appeared only inside the bounded restart/readiness window; the transaction subsequently reported `HEALTH_OK` and `READY_STATUS=200`.
  - The wrapper streams the apply transaction payload and does not retain a steady-state apply-script hash; the successful HEAD-byte transaction is the host execution receipt for this slice.
- Post-apply strict doctor: `DOCTOR_CONTRACT_OK`; 17/17 `MATCH`; `admission-integrity=OK`; container `restart=0` after restart; all active OAuth lanes `OPEN`; ban and refresh-failure signals remained zero.
- Zero-write Linux behavior simulation on BWG:

```text
ROLLBACK_FIRST_STEP_SIM first=restore_all:10 second=restore_all:10 failed=1
```

This proves the first marker is sticky under later failures. A real restore failure was not induced on the live gateway.

- Non-OAuth controlled live request on `zhipu-coding-plan/glm-5.3-flash`:

```text
CACHE_CANARY sample=1 input_tokens=4114 cache_read_tokens=0 hit_ratio=0.0000
CACHE_CANARY sample=2 input_tokens=4114 cache_read_tokens=4096 hit_ratio=0.9956
HEALTH_OK
CACHE_CANARY_EXIT=0
```

## 4. Rollback

- Repository: revert commit `5cf4bc9`.
- Host transaction state: restore `/root/cpa-guardrails-backup-20261121T105912.693338477Z` through the guarded rollback path.
- The runtime projection hashes were unchanged by this slice; the admission generation pin remained `53bb40c5b78a3d2279ef9238f0164a2380af17124ef7a483a7b685a1db9f3fc9`.

## 5. Boundaries

- The Linux simulation and repository test cover the diagnostic helper; they do not claim that a natural rollback occurred.
- Cache canary proves the non-OAuth API-key path and cache behavior after the transaction, not OAuth model quality or account safety.
- Antigravity model substitution remains an observation item; no alias or prompt was changed.
