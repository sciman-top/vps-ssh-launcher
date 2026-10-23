# BWG cron-family hardening + WorkBuddy 0.9.66 (2026-10-24)

Authorized continuation of the 2026-10-24 review: doc corrections, outputs
round-3 retirement, cron hardening, and the plugin upgrade the review's
differential evaluation recommended. zz monthly untouched (native, protected).

## 1. Documentation contract corrections

- README + `kernel-core-manual-rollback.md` +
  `singbox-core-update-migration.md`: the "zz must keep the old
  maintenance lock / template re-projection is forbidden" contract text was
  stale since the 2026-10-07 convergence. Replaced with the probe-verified
  facts: both hosts' wrappers and the updater hold the unified new lock;
  zz native monthly keeps `/run/v2ray-agent-maint.lock`; exclusion is by
  date isolation with collision dates 2027-01-01 / 2027-10-01 (accepted,
  recoverable). The REAL prohibition is now stated: never re-project zz
  monthly with `system_maintenance_cron.ps1` (it would silently drop the
  native controlled-reboot semantics).

## 2. outputs/ round-3 retirement

- 23 files removed (the retired claude-haiku lane's 2026-10-16 one-off
  probes/logs/doctor receipts + the 10909-era narrative leftover), per the
  2026-10-20 review scope and reference checks.
- Deliberately KEPT: `deploy-cpa-*` canary scripts (frozen evidence of the
  2026-10-21 drain-contract root cause, pinned by
  `test_cpa_updater_apply.py`), `verify-sidecar-10909.sh` (live runbook
  reference), all current-lane tooling.

## 3. Cron-family hardening (both hosts re-projected)

- **SIGHUP traps**: all four deployment ps1s' apply-side rollback traps and
  both embedded kernel wrappers' `recover_on_error` traps, plus the
  updater `.sh`'s `on_error` trap, now handle HUP (exit 129) so an SSH
  channel drop can no longer bypass the EXIT-trap status write / rollback.
  The behavioral signal test
  (`test_maintenance_cron_family.py`) extended to `("HUP", 129)`.
- **Xray geodata rollback**: the xray wrapper branch now backs up
  `geoip.dat`/`geosite.dat` next to the binary and `restore_xray` restores
  them before the service restart (vasma's Xray-core update refreshes
  geodata; a binary-only rollback left a geodata-caused failure alive).
- **First-deploy rollback verification**: the updater deployment's
  `rollback()` now asserts ABSENCE for `.missing` branches instead of
  `bash -n` on a deleted file (false `ROLLBACK_FAILED` eliminated), keeping
  `bash -n` for restore-branches.
- Re-projected: kernel wrappers (bwg xray, zz sing-box) and the updater
  (both hosts) with unchanged pins (vasma `1d0d39d1`, bwg xray
  `v26.10.21`/`a27e3f95`, zz sing-box `v1.14.3`/`768d31fa`). Controlled
  triggers after projection: wrapper skip paths rc=0 with fresh
  `success` heartbeats; zz updater `--check` `CHECK_NO_CHANGE` +
  `SERVICES_OK`. renewtls/monthly projection files unchanged (apply-side
  only edits), not re-projected.
- Full gates: 734 passed, 1 skipped (real-SSH opt-in), bandit/ruff/mypy
  clean.

## 4. WorkBuddy plugin 0.9.65-local → 0.9.66-local (bwg)

- **Evaluation finding (corrects the review's initial framing)**: the
  upstream repo's `v0.9+` tags are per-commit CI tags WITHOUT releases; the
  release line is `v0.8.80` (2026-10-23). The real gap was ONE workbuddy
  patch: `0.9.66` classifies `server closed idle connection` /
  `unexpected EOF` as connection-level billing errors that ride the
  4-attempt ladder instead of escaping one-shot - directly relevant to the
  observed `downstream_gone` / idle-cut symptom class. Local patch surface
  (selected-account/account-failover: scheduler/cooldown/auth/usage files)
  has zero overlap with `billing.go`; only `main.go` (version constant)
  co-changes, applied cleanly.
- Build (remote docker golang:1.26, `go vet` + `go test` + c-shared):
  source tarball pinned `0ac02407…` sha `09612bcc…`; artifact
  `workbuddy.so` sha `9bc1c97f13523b9d48578ea57a0ac7b63ca33f2af58e3206d28350535eaa67ed`.
- Canary install (`outputs/install-workbuddy-0966-20261024.sh`): traffic
  guard drain, lane-hold snapshot (empty both sides), container ldd check,
  atomic rename swap, `docker compose restart`, registration assertion
  `version=0.9.66`, readiness `HEALTH_OK`, backup
  `/opt/cliproxyapi/backups/20261023T171157.239576284Z-plugin-0965-to-0966`
  (prior .so `d52a0a3b…`). Live acceptance: single-shot HTTP 200 in
  1.005 s, `finish=stop`, no error.
- Rollback: restore the backup .so + `docker compose restart` (the install
  script's own rollback path), or re-run the 0965 flow with a rebuild.

## 5. BWG backup hygiene

- Deleted `/var/backups/vps-maint-system-20261015.Qikxd9` (384M, the
  oldest of three same-day sequential upgrade snapshots from 2026-10-15;
  superseded by the 10:59 and 11:03 runs). Kept `…E5Ntle` (372M, newest
  system snapshot) and `…iscsi…jUs7gR` (283M, newest of its kind).
  `/var/backups` 1.1G → 727M. Observation: the CPA updater's retention
  does not cover `/var/backups/vps-maint-*` system tars - watch the 11-01
  monthly run's accumulation before deciding on a policy.
