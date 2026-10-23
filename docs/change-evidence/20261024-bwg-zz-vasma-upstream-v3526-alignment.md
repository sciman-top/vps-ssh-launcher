# BWG+ZZ vasma convergence on official upstream v3.5.26 (2026-10-24 UTC 10-23 late)

User directive after `20261023-zz-vasma-v3526-upgrade.md`: the fork repo
`sciman-top/v2ray-agent` was deleted intentionally; both hosts must converge
on the official source `https://github.com/mack-a/v2ray-agent`.

## BWG finding: user-driven v3.5.26 vs stale pins (weekly fight)

- Read-only probe found bwg live install.sh back at upstream v3.5.26
  (`1d0d39d1…`, mtime `2026-10-23 15:21:21Z`) only 41 minutes after the
  Friday updater had restored the v3.5.25 pin (`14:40:03Z APPLIED`).
- Attribution: the user's own interactive session (last: pts/0 from the same
  ISP IP, 15:21-15:32; bash history ends `vasma`, `16`, `vasma`). Intent now
  confirmed by the directive itself.
- Consequence if unaddressed: kernel wrapper pin (`fca0ad30`) would refuse
  every Friday 14:20 (exit 13) and the updater would revert the user's
  v3.5.26 every Friday 14:40 — a permanent weekly fight.
- Pre-adoption validation on the live v3.5.26: `RenewTLS` dry run rc=0
  (80 days, cert valid); all xray wrapper anchors present.

## Pin adoption (both hosts)

- Repo single source updated: `scripts/remote/v2ray-agent-source-pin.json`
  and the `SOURCE_REF`/`EXPECTED_CANDIDATE_SHA` anchors in
  `scripts/remote/v2ray-agent-script-update.sh` → `d8926406…` / `1d0d39d1…`
  (v3.5.26, upstream HEAD confirmed unchanged via fetch).
- bwg: updater re-projected (`UPDATER_PROJECTED`), kernel wrapper
  re-projected with `EXPECTED_VASMA_SHA256=1d0d39d1…` (xray pins
  v26.10.21/`a27e3f95…` untouched).
- zz: official install.sh `1d0d39d1…` deployed from the git blob (LF bytes;
  pre-state guard on the fork build `3d4ee60a…`, remote sha re-verify, CR
  assertion, backup `/var/backups/vps-launcher-vasma/install.sh.fork-v3526`);
  sing-box wrapper pin re-projected to `1d0d39d1…` (sing-box pins
  v1.14.3/`768d31fa…` untouched); updater deployed for the first time
  (cron `40 14 * * 5`, same lane shape as bwg).

## Updater service-verification fix (required for zz, behavior-identical on bwg)

- First zz deployment failed (`RUNTIME_VERIFY_FAILED service=xray`): both
  the deployment ps1's `verify_runtime` and the updater's `verify_services`
  hard-required `xray nginx fail2ban`.
- A naive presence-aware fix (skip absent units) still failed bwg: each host
  keeps a DORMANT unit for the other core (bwg `sing-box.service` exists but
  inactive+disabled; zz `xray.service` exists with no process). Unit
  presence is not the right predicate on this fleet.
- Final semantics in both layers: proxy core = at least one of
  xray/sing-box present-and-active (`VERIFY_FAILED service=proxy-core`
  otherwise); nginx/fail2ban required only when their unit exists. bwg
  behavior unchanged (xray core, both aux present); zz passes via
  sing-box+nginx (fail2ban present and active).
- `test_kernel_update_cron.py` contract assertions updated to pin the new
  shape (`for service in xray sing-box; do`, `proxy-core` marker,
  `|| continue` presence skip).

## Verification

- Both hosts: updater `--check` → `CHECK_NO_CHANGE` + `SERVICES_OK` against
  `mack-a@d892640`; deployed updater sha identical (`2b6e3664…`); live
  install.sh == wrapper pin == updater candidate everywhere.
- zz RenewTLS dry run rc=0 (80 days, cert valid); sing-box/nginx active
  throughout; zz sing-box process uptime continuous.
- Full gates: 734 passed, 1 skipped (real-SSH opt-in), bandit/ruff/mypy
  clean.
- Rollback: hosts keep `/var/backups/` chains; repo pins revert by restoring
  the JSON/.sh anchors and re-projecting.

## Fork checkout disposition

`sciman-v2ray-agent/` local checkout is now historical (origin repo deleted
by the user; local branch ends at `873ed90` with the v3.5.26 merge and the
eol=lf pin, unpushed and unpushable). The deploy lane no longer reads it;
both hosts consume mack-a@d892640 via the updater.
