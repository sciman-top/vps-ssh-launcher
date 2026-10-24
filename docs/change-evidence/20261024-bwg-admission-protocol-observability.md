# BWG admission protocol-result observability (2026-10-24)

## Scope and authorization

Authorized continuous repair for the recurring CPA client-key failures. This
change only adds a bounded classification field to the admission journal. It
does not change credentials, provider routes, concurrency, retry policy,
cooldown windows, IP/UA identity, or SSE payload contents.

## Change

`scripts/remote/cpa-admission.py` now emits `protocol_result=` on each
`upstream_result` line. The value is one of:

- `completed`
- `missing_terminal`
- `malformed_event`
- `upstream_error`
- `capacity`
- `client_disconnect`
- `http_error` / `protocol_error`

The classifier never includes provider response text, request content,
credentials, or exception details. It distinguishes a 200 response that lacks
the requested protocol terminator from an upstream error or a capacity event.
Malformed JSON SSE payloads are recorded as `malformed_event`.

## Verification

- Focused admission/guardrail tests: `224 passed, 33 subtests passed`.
- Full local gates: `737 passed, 1 skipped, 358 subtests passed`; Bandit,
  Ruff check/format, and mypy passed. The skipped test is the repository's
  opt-in real SSH integration test.
- Remote transaction: backup-first, idle-lane preflight, staged SHA check,
  root-owned integrity check, atomic replacement, admission restart, and
  health verification all passed.
- Remote result: `ADMISSION_INTEGRITY_OK`, `HEALTH_OK lanes=4`, deployed
  admission SHA-256
  `ab7748bd9828575501d23a36546f77296dd759bea597cd4b024ff97f2e486245`.
- Remote backup:
  `/opt/cliproxyapi/backups/20261024T051407.144961091Z-admission-protocol-observability`.

The subsequent strict doctor readback confirmed the running file hash and
service health, but reported repository projection drift until this change was
committed because the doctor intentionally anchors expected hashes to `HEAD`.

## Acceptance boundary

This proves `repo_verified`, `filesystem_projected` after commit, and
`host_loaded` for the journal classifier. It does not prove that the provider
or any model is stable, and it does not synthesize a terminal event or turn an
incomplete stream into success. Natural Desktop acceptance remains a separate
observation.

## Rollback

Restore `cpa-admission.py` and `cpa-admission.sha256` from the named remote
backup and restart `cpa-admission`. Git revert removes the classifier and its
tests; Git rollback alone does not restore the already-loaded remote process.
