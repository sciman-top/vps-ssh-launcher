# 2026-10-28 BWG admission TTFB observability and cache acceptance

## Scope

Authorized BWG-only follow-up after the local CPA risk review. The change adds
redacted admission timing fields and validates the existing non-OAuth cache and
SSE probes. No OAuth credential was refreshed, rotated, quarantined, deleted,
or probed. No concurrency, retry, cooldown, model-selection, or WorkBuddy
realm policy was changed.

## Repository change

- `scripts/remote/cpa-admission.py` now records `upstream_header_ms` and
  `upstream_first_byte_ms` in `upstream_result` lines. The first-byte value is
  measured from upstream payload forwarding and excludes local SSE heartbeat
  comments.
- `scripts/remote/cpa-admission-integrity-pin.txt` was rotated to the matching
  normalized source hash.
- `docs/runbooks/cpa-failure-triage.md` documents how to interpret the two
  fields.
- Local full gates passed: 803 tests passed, 1 skipped, 398 subtests; Bandit,
  Ruff, format, mypy and documentation checks passed.
- Commit: `4e916ec`.

## Remote evidence

- Initial strict doctor observed the old admission source and refused ordinary
  `-Apply` because the integrity pin rotation requires a reviewed deploy
  transaction.
- The reviewed transaction dry-run passed syntax and preflight. Its apply run
  refused `live_code_drift` before replacing files; the temporary Nginx
  maintenance window was restored successfully. No admission file replacement
  or admission restart was performed by that transaction.
- A later fresh doctor observed the new source hash, `admission-integrity=OK`,
  all projection entries `MATCH`, and `DOCTOR_CONTRACT_OK`. The mechanism that
  changed the remote source between those observations is not attributed by
  this receipt; the deployed state is verified, but the transition is not
  treated as proof of a particular updater path.
- Three active OAuth lanes were `OPEN`; WorkBuddy remained
  `scheduler_mode=credits`, `account_max_concurrent=1`,
  `identity_switch_on_429=false`, with background task/lifecycle/token
  keepalive automation disabled.
- The catalog later reported `MODEL_CONTEXT_MISSING=none` and
  `catalog_oauth_missing=none`; no route-manifest change was needed.

## Controlled acceptance

- `cpa-health.py cache-canary glm-5.3-flash` returned two samples:
  `input_tokens=4114`, then `cache_read_tokens=4096`, giving a second-sample
  hit ratio of `0.9956`.
- The existing GLM streaming probe returned `status=200`, first text at about
  `2.37s`, total about `2.40s`, 54 events, clean terminal `[DONE]`, and
  `finish_reason=stop`.
- These probes exercised a non-OAuth provider only. They do not establish
  OAuth account health, provider compliance, long-term cache retention, or
  natural traffic quality.

## Remaining boundaries

The 24-hour journal still showed upstream ChatGPT capacity events and one
Antigravity model-substitution warning. Those are observed upstream/runtime
signals; increasing concurrency, clearing cooldown, or adding retry would
increase risk and was intentionally not done. Codex OAuth expiry remained in
the short review window and requires an operator-managed refresh according to
the existing runbook.
