# BWG admission stream-failure label (2026-11-01)

## Scope

Projected the committed admission change `9d2f12c` / rebased `edb5233` to
BWG. The change only relabels the in-stream failure that admission synthesizes
after a `200 text/event-stream` header was already committed:

- `response.failed` / `response.incomplete` -> "CPA received an upstream stream
  error before completion (last event: ...)".
- a silent close after `response.in_progress` / `response.created` /
  `response.output_item.added` / `response.output_item.done` -> "CPA upstream
  stream closed before a terminal event (last event: ...)".
- both explicitly say this is an upstream provider failure, not a local
  cooldown or admission refusal.
- the generic fallback and the generic SSE repair path are unchanged.

No admission threshold, lane RPM, credential, provider route, concurrency,
retry, cooldown, CPA image, plugin binary, Nginx, or account state was changed.

## Evidence layers

- `repo_verified`: admission source hash
  `f286e7a9d3bdff2ba9969dbfd3a86d5fee00e574bd2d49a8f871df6dda156dff`
  matches `scripts/remote/cpa-admission-integrity-pin.txt`; the admission tests
  cover both a `response.failed` event and a silent close after
  `response.in_progress`.
- `test/simulation`: `tests/test_cpa_admission.py` 277 passed; Ruff check and
  Bandit clean.
- `host_loaded`: the reviewed admission deploy transaction ran with the
  active-window override requested by the user:
  `ACTIVE_WINDOW_OVERRIDE user_requested_at=2026-11-01T17:31:07+0800`,
  `ADMISSION_PREFLIGHT_OK lanes=4 holds=null`, `ADMISSION_DRAIN_OK`,
  `ADMISSION_INTEGRITY_OK`, `DEPLOY_OK
  admission=f286e7a9...`. Backup:
  `/opt/cliproxyapi/backups/20261101T093107.598775232Z-admission-queue-stream-20261031`.
- `host_loaded` verification: `/opt/cliproxyapi/cpa-admission.py` and
  `/etc/vps-ssh-launcher/cpa-admission.sha256` both hash `f286e7a9...`;
  `ADMISSION_INTEGRITY_OK`; strict doctor returned `DOCTOR_CONTRACT_OK`,
  `admission-integrity=OK`, `admission-health=OK`, all projection checks
  `MATCH`, workbuddy `posture=OPEN hold_reason=none`, `cooldown_state=none`.
- `controlled_live`: one loopback DeepSeek Responses SSE request through
  `127.0.0.1:8318` with the client key read inside the remote process (never
  printed) returned `LIVE_PROBE_HTTP=200`,
  `LIVE_PROBE_EVENT_COUNT=10`, `LIVE_PROBE_COMPLETED=true`,
  `LIVE_PROBE_TEXT_MATCH=true`, `LIVE_PROBE_RESULT=PASS`. The request id was
  retained only as a hash prefix `dbdcba2f51fc`.

## Boundary

This proves the projection, strict contract, and a normal-path streaming
acceptance. It does not prove that the upstream WorkBuddy provider will stop
closing streams early. The observed failures remain upstream/provider
failures; this change only makes their client-visible label honest. A real
natural `response.failed` / early-close message change will be visible the
next time that upstream behavior recurs, and it is not manufactured here.
Long-term provider completeness and China-network acceptance remain open.

## Rollback

Restore `/opt/cliproxyapi/cpa-admission.py` and
`/etc/vps-ssh-launcher/cpa-admission.sha256` from the backup directory above,
restart `cpa-admission`, and rerun strict doctor. A Git revert is not a
substitute for restoring the remote file.
