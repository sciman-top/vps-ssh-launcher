# 2026-09-20 BWG CPA 探针收紧与 OAuth 风控监护投影

## Scope

One `cpa_bwg_guardrails.ps1 -Apply` transaction (bwg only) projecting the
hardened remote assets from `be907dd` plus the follow-up regression tests in
`99dbbe8`. No provider routing, relay, `zz`, or auth-directory content was
changed.

Functional changes now live on the VPS:

- `cpa_policy.py` fails closed on provider base-url shape (https, exact host,
  exact path, no port/userinfo/query/fragment/params), requires exactly one
  plain `api-key-entries` credential per controlled provider, and rejects
  transport override keys (`headers`, `proxy-url`, `tls`, ...).
- `cpa-auto-update.sh` no longer auto-probes the third-party relay channel
  (`relay-soft` removed from both timer paths; daily cost is back to one Luna
  smoke); a post-update readiness failure now distinguishes upstream-unavailable
  (retain, exit 10) from local contract failure (rollback); error-dump chmod
  failures return nonzero and `DEFER` the run before any provider traffic
  (`SECURITY_BLOCK`).
- `cpa-health.py` guards all real-generation modes with a non-blocking
  `flock` on `/opt/cliproxyapi/health-probe.lock` (`PROBE_ALREADY_RUNNING`,
  exit 14) and classifies a per-route 403 (catalog already accepted the local
  key) as upstream-unavailable in every explicit mode.
- Doctor gains an `==oauth-monitor==` gate: active codex OAuth expiry/refresh
  metadata and a 7-day `invalid_grant`/refresh-failure signal scan; expired,
  unparseable, or refresh-signal states fail the doctor, days-left <= 3 fails,
  <= 7 warns. Rollback receipts re-verify the merged Nginx route contract and
  the public 401/404/200 probe set before reporting `ROLLBACK_VERIFIED`.

## Remote evidence

- Projected file sha256 (remote == repo): `cpa_policy.py 765010e7…`,
  `cpa-health.py f122b3d3…`, `auto-update.sh 21083f55…`.
- `config.yaml` sha256 unchanged by the transaction (`316bc390…`, identical to
  the pre-apply value), so no config rollback input is required.
- Strict doctor: `DOCTOR_CONTRACT_OK` with the new gate present:
  `oauth_codex_files=1`, `oauth_expired=false`, `oauth_days_left=8`,
  `oauth_refresh_age_hours=36.0`, `oauth_refresh_failures_7d=0`,
  `oauth_monitor=OK`.
- Luna data plane: `python3 /opt/cliproxyapi/cpa-health.py generation` →
  `HEALTH_OK` (exit 0), single request, no retries.
- Public route contract within doctor: valid-path unauth 401, bare/wrong 404.

## Controlled live acceptance (post-projection, same day)

- Probe-lock contention, live: `generation-all` started detached, second
  `generation` probe issued 2s later → `PROBE_ALREADY_RUNNING`, `second_rc=14`,
  no queueing and no second provider request from the blocked probe.
- Full matrix, live (`generation-all`): luna 200 / 2158ms, sol 200 / 1677ms,
  terra 200 / 5637ms, glm-5.3-flash 200 / 4231ms, deepseek-flash 200 / 876ms,
  all `finish=stop`, final `HEALTH_OK` (exit 0). Single-shot per model, no
  retries.
- Container state after the projection restart
  (`started=2026-09-20T00:31:40Z`): `restarts=0`; CPA container log
  error/warn count over the following 2h: 0.

## Rollback

- Repo: `git revert be907dd 99dbbe8` then re-run `-Apply` to re-project.
- VPS: the transaction's own backup under `/root/cpa-guardrails-backup-*` is
  restorable by re-running `-Apply` against the reverted repo; the auth
  directory was not touched.
- The OAuth renewal window remains the standing operational risk: 8 days left
  at verification time; the doctor now fails at <= 3 days or on refresh
  failure signals, so the timer/doctor surface gives the re-enroll trigger
  defined in `docs/runbooks/cpa-oauth-luna-slot.md`.
