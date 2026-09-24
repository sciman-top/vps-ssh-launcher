# BWG CPA v7.3.16 risk and cache closeout

## Scope and authorization

- Scope was limited to the `bwg` profile. `zz` was not connected or changed.
- The user explicitly authorized a direct upgrade to `v7.3.16` and required the
  slot 3 plaintext HTTP upstream exception to remain in place.
- The local user-level OpenSSH `known_hosts` file already contained the host
  keys. Its read ACL was restored for the current Windows user; file contents
  were not changed.

## Repository changes

- `scripts/remote/cpa_policy.py` now enforces `logs-max-total-size-mb: 32`.
- Strict doctor and apply enforce Compose `umask 077` plus
  `exec ./CLIProxyAPI`, and keep retained error dumps private.
- The public Nginx limiter is projected as six concurrent requests per client,
  10 requests per second, `burst=10` without `nodelay`. Apply accepts the
  previously deployed `burst=20 nodelay` as an input state and rewrites it in
  the same transaction.
- Slot 3 remains the explicitly authorized exact plaintext HTTP exception;
  the guardrail continues to report it as `INSECURE_HTTP_PROVIDER`.
- The normal updater still requires the existing 72-hour maturity window.
  The direct release transaction was a one-time, digest-pinned exception.

## Remote transaction

- Fresh pre-change doctor: v7.3.15, `DOCTOR_CONTRACT_OK`.
- GitHub release metadata and Docker Hub tag metadata for `v7.3.16` matched.
- The image was pulled and pinned to
  `sha256:ca6af8b19642b3176eaa6b6ed197cf870a9f8026d1b58693d3ff2a4d7faaedf3`.
- Compose was backed up before mutation under the remote updater backup root.
  The direct transaction installed a rollback trap and verified local
  readiness after activation.
- The existing backup-first guardrail apply then projected the repository
  policy, updater, health script, provider routes, Nginx and fail2ban files.
- Apply readiness returned `200` after a transient restart-time connection
  reset. No partial write remained.
- Post-change strict doctor returned `DOCTOR_CONTRACT_OK`; the container image,
  Compose, policy, Nginx syntax, public route contract and error-dump modes all
  passed. `auto-update.sh --check` reports `current=v7.3.16 target=v7.3.16`.

## Risk and cache evidence boundary

- `request-retry=0`, `max-retry-credentials=1`, cooldown persistence remains
  disabled, and session affinity remains enabled for one hour.
- The 24-hour gateway sample still shows both slow upstream failures and local
  fast failures. This is operational evidence, not provider-quality or ban
  immunity proof.
- The strict doctor intentionally reports
  `cache_usage=UNAVAILABLE_NON_CONSUMING_DOCTOR`; no upstream generation was
  sent solely to manufacture a cache-hit claim.
- No natural-user acceptance or provider-account acceptance is claimed. A
  future controlled replay must be reported separately from readiness and
  doctor results.

## Rollback

- Image rollback: restore the direct-upgrade backup Compose file, run
  `docker compose up -d --pull never`, and verify readiness.
- Policy rollback: use the backup directory emitted by the same guardrail
  apply transaction, restore only the files changed by that transaction, then
  rerun strict doctor.
