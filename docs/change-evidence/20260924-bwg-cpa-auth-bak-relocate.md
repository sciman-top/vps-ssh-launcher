# BWG CPA stray auth backup relocation

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Change: relocate one stray credential-bearing backup file out of
  `/opt/cliproxyapi/` root into the existing `backups/` tree. No config,
  container, route, projected file, or CPA state was touched; no service
  restart.
- Found by the 2026-09-24 deep review (P3-1): a 9/8 login-state backup was
  sitting in the CPA root. The credential was re-enrolled 2026-09-18 via
  device flow, so the backup has no recovery value; root-only permissions
  bounded the exposure, but it belonged with the other backups.

## Change evidence

- File:
  `codex-1c3cf6c3-<acct>-plus.json.bak-20260908` (3898 B, mode 600 root,
  sha256 prefix `a66bf0b52f324b01`).
- Moved to:
  `/opt/cliproxyapi/backups/20260924T151807Z-manual-auth-bak-relocate/`
  (directory 700, file kept 600 root), following the existing timestamped
  `-manual-` backups/ naming convention.
- Active auth untouched: `auth/codex-1c3cf6c3-<acct>-plus.json` remains
  mode 600 with mtime `2026-09-18T12:38:59Z` (pre-dating this change).
- Root now contains only the four intentional `config.yaml.bak-20260921-*`
  key-rotation rollback artifacts, left in place (removal is a user decision;
  the rotated-out key they contain is already revoked server-side).

## Rollback

- `mv /opt/cliproxyapi/backups/20260924T151807Z-manual-auth-bak-relocate/codex-*bak-20260908 /opt/cliproxyapi/`
  restores the prior state exactly.

## Verification

- Post-move `stat`: file 600 root, size 3898 B unchanged; sha256 prefix
  unchanged.
- No gate re-run: the doctor asserts nothing about this file and no projected
  file was touched, so a full doctor is disproportionate for this slice
  (proportionate-gate rule). The first scheduled cache-baseline doctor run
  (daily 22:05 automation, created 2026-09-24) re-verifies the host state
  tonight as a natural follow-through.

## Not covered

- Bot-side 429/502 jittered backoff (review P2-1) is being delivered by
  in-flight `qq-codex-bot` work (`_provider_watchdog.py` already carries
  Retry-After capture, `gateway_probe_backoff_until`, and jitter helpers);
  this repository is not involved.
- 401 residual from the same review was attributed (doctor's by-design
  unauth contract probes from loopback, plus ceased switch-window stale-key
  attempts) and needs no action.
