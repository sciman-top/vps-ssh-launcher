# BWG + ZZ v2ray-agent v3.5.33 updater projection and apply

## Scope

- Targets: `bwg` then `zz`, one host at a time, each verified before moving on.
- Change: advance the reviewed v2ray-agent updater to the pinned official
  `install.sh` candidate `v3.5.33`, then re-project each kernel wrapper with
  the fresh vasma pin. Upstream delta is a SOCKS5 standalone-install feature
  plus sing-box service-registration robustness fixes; no proxy kernel,
  configuration, or hardening-lane change was intended or performed.
- Repository commit: `4f68880` (`v3.5.33` source pin, three-file sync:
  pin JSON, updater template anchors, contract test).

## Evidence

- Source commit: `876ed197c4a5fc386793f7eb17a9688e466c1ce5`.
- Candidate SHA-256:
  `ae56d372cfc9249235bffd0ee1887a53ab2dd8a6b811a7005d51532f6f5d07e5`.
- Pre-apply installed SHA-256: bwg `04d2bf9c…` (`v3.5.32`), zz `ba83e425…`
  (`v3.5.31`).
- Updater preflight per host: `RUNTIME_VERIFY_OK`, syntax OK, observed sha
  used as the `-Apply` transaction guard.
- bwg: `UPDATER_PROJECTED` (backup `/var/backups/v2ray-agent-script-update-deploy.LDxP3J`),
  then remote `--apply` → `APPLIED version=v3.5.33` (backup
  `/var/backups/v2ray-agent-script-update.2fXOI4`), `SERVICES_OK`. Kernel
  wrapper re-projected (backup `/var/backups/v2ray-agent-maint.BWYCbM`).
- zz: `UPDATER_PROJECTED` (backup `…script-update-deploy.OjphqL`), remote
  `--apply` → `APPLIED version=v3.5.33` (backup `…script-update.M1j6Ke`),
  `SERVICES_OK`. Kernel wrapper re-projected (backup
  `/var/backups/v2ray-agent-maint.CbB03p`).
- Post-apply read lane per host: `vasma_live_sha256=ae56d372…`,
  `pin_freshness vasma_pin=fresh binary_pin=fresh`, pinned kernel unchanged
  (bwg Xray `v26.10.29`, zz sing-box `v1.14.4`), kernel update log records
  `skip reinstall`.
- Anti-blocking hardening survived the vasma update untouched: bwg
  `camouflage_root=decoy-applied`, `empty_short_id=no` on both REALITY
  inbounds, `obfs_lane=absent` (bwg design); zz `camouflage_root=decoy-applied`,
  `obfs_lane=present listening=yes port=8444`, `empty_short_id=no`. Services
  `xray`/`sing-box`/`nginx`/`fail2ban` active.
- Final GFW-path TCP reachability from the operator host (IP-literal, no
  proxy): bwg 443/21470/10094 and zz 22752 all `open` after the update.

## Boundary and follow-up

- This receipt proves updater projection, vasma script replacement, wrapper
  re-projection, and low-level service/listener state. It is not client-side
  `live_accepted` evidence for any lane; per-lane live acceptance remains the
  hardening-lane receipts and the operator's normal client usage.
- vasma reinstalls/regenerations can revert the decoy page and the stripped
  empty shortId; the read lane now reports `camouflage_root=`/`obfs_lane=` so
  drift is visible before the next kernel cron. Re-run the hardening lanes per
  `docs/runbooks/proxy-camouflage-lanes.md` if a future vasma apply reverts
  them.
- Next scheduled convergence: kernel weekly crons (Friday 14:20/14:40 UTC) run
  against wrappers that now expect vasma `ae56d372…`; both hosts already
  satisfy that expectation.
