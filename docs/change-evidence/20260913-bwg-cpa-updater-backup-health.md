# BWG CPA updater backup-health follow-up — 2026-09-13

## Scope

- Scope: the explicitly authorized `bwg` profile only.
- No `zz` access or mutation, no SSH tunnel data plane, no path rotation, no
  key rotation, and no deletion of historical backups.
- Credentials, capability paths, IP addresses, tokens, and full sensitive
  commands are omitted.

## Source and projection

- Source commit: `156fdaa85e645cfa2d72689c47d736c9175cf5d1`
  (`增加 BWG updater 备份空间保护`).
- The change adds a non-destructive updater preflight: reject a backup root
  that is a symlink or non-directory, require mode `700`, require at least
  2 GiB free on the filesystem, and log backup count/size without pruning.
- `bwg -Apply` now atomically projects the version-managed updater source and
  includes it in the existing remote backup/rollback transaction.
- Remote apply backup: `/root/cpa-guardrails-backup-20260913T135401Z/`.
- Remote updater SHA-256 after projection:
  `cb6afeb8984d42aa3329b247c90b04ded345d668aa9926288a1d9ad2ba5413c5`.

## Verification

- Local full gate after the change: `112 passed, 1 skipped, 57 subtests
  passed`; Bandit, Ruff, Ruff format, Mypy, and `git diff --check` passed.
- Apply returned `READY_STATUS=200` and `GUARDRAILS_APPLIED`. A transient
  connection reset occurred during the restart window; the same apply then
  completed successfully and readiness recovered.
- Fresh strict doctor at `2026-09-13T13:54:30Z` returned
  `DOCTOR_CONTRACT_OK`; CPA was `running` with restart count `0`, the public
  gateway and 401/404/404 route contract remained intact, and the Nginx and
  fail2ban contracts remained valid.
- The projected updater passed remote `bash -n` and a real read-only
  `--check` at `2026-09-13T13:54:46Z` returned:

  ```text
  BACKUP_HEALTH status=ok backups=3 size_kib=5352 free_kib=34091936 minimum_free_kib=2097152
  ```

## Recovery and boundaries

- No backup was deleted. The remote recovery boundary remains the apply backup
  above; Git rollback alone cannot restore host state.
- This run proves the healthy backup-check path in the real host and the
  source/remote hash match. The low-space and unsafe-root branches are
  fail-closed code paths covered by source assertions, not production fault
  injection.
- The updater still does not automatically decide which backup is
  `last-known-good`; a retention/pruning policy remains intentionally absent.
- These checks do not prove provider-side quota health, immunity from bans or
  rate limits, or natural long-term acceptance.
