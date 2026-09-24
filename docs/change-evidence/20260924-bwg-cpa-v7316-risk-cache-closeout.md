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

## Controlled live replay (2026-09-25, follow-up)

This section reports the controlled replay that the previous section
explicitly deferred.

- Loopback single-shot probes through the public route
  (`POST /v1/chat/completions`, `max_tokens` 32, temperature 0), one per
  executor path:
  - `glm-5.3-flash` (zhipu-plan lane): `200`, served model preserved,
    13.69 s (reasoning consumed the 32-token budget, so content was empty;
    usage passthrough intact). This is also the scheduled gate target.
  - `deepseek-flash` (openai-compatible lane): `200`, `ok`, 0.72 s.
  - `gpt-6-sol` (ai.input.im lane): `502` "Upstream access forbidden, please
    contact administrator" in 7.8 s — the same upstream-rejection signature
    previously recorded for slot 2; `502/502` was passed through faithfully,
    so this is upstream-side.
  - `gpt-6-astra` (ai.input.im lane): read timeout at 90 s — consistent with
    the lane's documented slow-window character (85.7 s observed 2026-09-19)
    and the 24 h slow-upstream 502 profile; no local fault signature.
- `luna` was not probed (risk-control quiet period). Codex-lane evidence stays
  indirect: OAuth healthy (`oauth_days_left=3`, zero refresh failures), no
  post-upgrade `auth_unavailable` dump on the codex lane, failback traffic
  pattern unchanged.
- Slot 2/3 aliases (`-cii`/`-91`) were not re-probed: their upstream failure
  is pre-existing and covered by the standing keep-and-wait decision.
- Post-upgrade hourly error surface (15:21Z→16:16Z): no new local-plane
  failure mode; `429` fell from 14 in the first partial hour to 0 in the
  next; 502s remain slow-upstream passthrough.
- Post-upgrade strict doctor (2026-09-24T16:28Z): `DOCTOR_CONTRACT_OK`,
  `compose-umask=OK`, `logs-max-total-size-mb=32` enforced, public route
  contract OK, `oauth_monitor=OK`.
- Verdict: `ACCEPTANCE_RESULT=PASS` for the v7.3.16 runtime upgrade. The
  zhipu and openai-compatible executor paths are proven on the new version;
  the codex lane is indirectly verified; ai.input.im (slot 1) joins slot 2/3
  on the upstream-side wait-for-recovery list.
