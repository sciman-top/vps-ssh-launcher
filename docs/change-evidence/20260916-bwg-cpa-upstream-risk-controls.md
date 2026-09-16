# BWG CPA upstream risk-control projection

## Scope

- Target: `bwg` only. No connection or change was made to `zz`.
- Source commit: `57813f9` (`收紧 CPA 更新与上游风控`).
- Data-plane invariant retained: CPA loopback `127.0.0.1:8317`; one public
  Nginx TLS listener on `8443`; one random capability path; no SSH tunnel
  data plane.

## Changes

- The updater now accepts only mature patch releases in the current
  major/minor line. Mature minor and major candidates are report-only and
  require a separate canary.
- After an updated image reports an upstream transient failure, the updater
  checks local readiness once and returns `UNVERIFIED`; it does not issue a
  second generation request.
- `cpa-health.py quality-canary` is an explicit, one-request-per-exposed-route
  semantic check. It does not log response bodies and is never called by the
  timer.
- The stale-cooldown runbook now permits one controlled restart followed by a
  full cooldown window, not restart or generation retry loops.

## Update Policy Decision

- Decision source: the user confirmed this policy after comparing the earlier
  same-major cross-minor automation with the production risk review.
- Active policy: patch-only automatic updates. Mature minor and major releases
  are report-only and require a single-host explicit canary.
- This supersedes `d067157` for BWG. The reason is a single production gateway
  without staging and active upstream availability variance; 72-hour maturity,
  a digest, and rollback do not establish behavioral acceptance.
- Exception: a minor release that explicitly fixes a current confirmed defect
  or security issue may use an expedited canary, but still requires backup,
  strict doctor, and one low-frequency acceptance before becoming the baseline.

## Preflight And Projection

- Preflight strict doctor passed on 2026-09-16. The host was running
  `eceasy/cli-proxy-api:v7.3.4` with a digest-pinned image, loopback-only CPA,
  public Nginx contract, client authentication, Fail2Ban file monitoring, and
  remote management disabled.
- `-Apply` created `/root/cpa-guardrails-backup-20260916T144320Z`, returned
  `READY_STATUS=200`, and returned `GUARDRAILS_APPLIED`.
- Fresh doctor after projection returned `DOCTOR_CONTRACT_OK`. The projected
  normalized SHA-256 values matched the source commit:
  - `auto-update.sh`: `ed0e56dc17abd22a73dab93d650d8814a824796b1e5114122c96674ebfbc4856`
  - `cpa-health.py`: `ca850ab621f717435963919b2a7dfd0008ea07caf73e40a2fd326c1305cb6167`

## Rate And Quality Boundary

- The current access-log window showed upstream `502` and `503` responses
  while the Nginx limit markers had one connection rejection. This is not
  evidence for an ingress bottleneck or a safe shared-account quota.
- No generic global limiter was introduced: r1 and GLM may not share a quota,
  and a guessed threshold would not address the observed upstream failures.
- No real `quality-canary` request was sent after projection. The observed
  `429`/`502`/`503` signals require stopping extra provider probes; the new
  command is available for a separately authorized low-frequency acceptance.

## Follow-up Acceptance And Projection

- Source commit `c3dc2dc` corrected the explicit quality canary so one JSON
  Markdown fence is accepted and a relay `403` after a valid catalog is
  `UPSTREAM_UNAVAILABLE`, not a local configuration failure.
- The same commit added the container `StartedAt` field and a strict failure
  for an unexpected `cpa_total` global Nginx concurrency limiter.
- A read-only `nginx -T` preflight found only the per-IP `cpa_cc` limiter. The
  BWG-only Apply created `/root/cpa-guardrails-backup-20260916T151529Z` and
  returned `READY_STATUS=200` and `GUARDRAILS_APPLIED`.
- Fresh strict doctor returned `DOCTOR_CONTRACT_OK`, reported
  `global-account-concurrency=ABSENT`, and reported the container start time.
  The projected `cpa-health.py` SHA-256 was
  `902b00af7d0cf4a41a23782fe8e5d222a2ae9ca65e672ca42a467e76b91cb8e6`.

## Rollback

- Restore only the files in `/root/cpa-guardrails-backup-20260916T144320Z`,
  then restart CPA, reload Nginx, and rerun the strict doctor.
- Reverting the Git commit does not restore the VPS. Do not rotate the public
  path, credentials, or provider routing as part of this rollback.
