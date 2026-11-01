# BWG admission rate-guard queue projection (2026-11-01)

## Scope

Projected the committed admission change `209d986` to BWG. The change makes a
normal request that reaches the local rolling `requests_per_minute` window wait
in the lane's existing bounded FIFO instead of being rejected after a short
4–6 second window. Recovery probes still bypass the window; only a full queue
or exhausted queue budget is refused. OAuth/API-key credentials, provider
routes, model aliases, CPA image, plugin binary, Nginx, account state, and
egress were not changed.

## Evidence layers

- `repo_verified`: admission source hash
  `b22bb66e270c43d6964f3908f880442a5011d8f9942390a5058156ed751449e3`
  matches `scripts/remote/cpa-admission-integrity-pin.txt`; admission config
  hash `7f53e6c7fc33423060f0027f7d2121d626faae0e75b1356932d086a375d3e89f`
  contains lane RPM `24/30/18/18`.
- `test/simulation`: full gates passed with `966 passed, 8 skipped, 459
  subtests`; Bandit, Ruff, Ruff format, and mypy passed. The admission tests
  include the real Handler/AdmissionProxy/Server stack against a fake upstream:
  the rate guard queues a normal admission until the rolling window frees,
  while a zero queue budget still returns the attributable 429 refusal.
- `host_loaded`: deploy dry-run verified live baseline
  `e991acd9335ca29b369fc33267ec9b21d4af7e9a38f689978005165d833790dc`
  and candidate `b22bb66…`; the reviewed transaction reported
  `ADMISSION_PREFLIGHT_OK lanes=4 holds=null`, `ADMISSION_DRAIN_OK`,
  `ADMISSION_INTEGRITY_OK`, and `DEPLOY_OK`. The active-window override was
  explicitly authorized by the user; the backup is
  `/opt/cliproxyapi/backups/20261101T050730.720709264Z-admission-queue-stream-20261031`.
- `host_loaded` verification: fresh strict doctor returned
  `DOCTOR_CONTRACT_OK`, `admission-integrity=OK`, all projection checks
  `MATCH`, `admission-health=OK`, and three active OAuth lanes `OPEN`.
- `controlled_live`: one loopback DeepSeek SSE request through
  `127.0.0.1:8318` returned HTTP 200, 21 events,
  `response.completed=true`, text matched `pong`, 756 ms, and
  `LIVE_PROBE_RESULT=PASS`. Client key material remained inside the remote
  process and was not printed; only a request-id hash prefix was retained.

## Rollback

Restore `cpa-admission.py` and `cpa-admission.sha256` from the backup directory
above, restart `cpa-admission`, and rerun strict doctor. Git rollback is not a
substitute for restoring the remote files. The admission config
`cpa-admission.json` was not changed by this transaction.

## Boundary

This receipt proves the repository/source/pin contract, the bounded drain
transaction, service loading, strict projection, and one successful data-plane
request. It does not prove that a future natural burst will never wait, that a
provider quota is protected, or that the queue implementation was exercised
against a real production RPM saturation event. The queue behavior is covered
by the local end-to-end simulation; long-term account health and provider
compliance remain external observations.
