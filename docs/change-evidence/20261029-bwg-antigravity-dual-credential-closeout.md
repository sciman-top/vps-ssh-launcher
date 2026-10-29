# BWG antigravity dual-credential closeout (2026-10-29 +08:00)

## Scope and authorization

The user added a second antigravity Gemini OAuth credential to the BWG CPA
gateway and asked for an assessment, then authorized executing the
recommended follow-ups to completion. The read-only assessment concluded the
gateway had already absorbed the second credential by design; no routing,
admission, catalog or risk-control change was needed. This round executed
the two recommended items: credential backups and a doctor visibility fix.

## Assessment facts (read-only)

- The new credential hot-loaded without a restart (`auth file changed
  (CREATE) -> processing incrementally`); doctor reports
  `oauth_antigravity_files=2` with zero refresh failures in 7d.
- Upstream v8.0.47 `pickNext` fast path selects the first eligible
  credential in registration order and only rotates on retry/failover;
  combined with `session-affinity-rebind-on=quota` deployed earlier the
  same day, the second credential is a hot spare with zero served traffic
  (148 session-affinity records and three controlled loopback probes all
  bound to the original credential). This is expected behavior, not a
  load-balancing defect.
- The provider-level controls (single-model catalog exclusion,
  sensitive-words filter, `antigravity-oauth` admission lane with
  max_inflight=2) cover both credentials automatically.
- The retained `-n` substitution warnings all originate from the original
  credential and predate the second account; the single recorded pair is
  the known naming observation and stays observation-only.
- Production runs CPA v8.0.47; container recreation bounds the log
  retention window to the same day.

## Executed changes

### Credential backups (remote, on-box copy only)

Both current antigravity auth files were copied to
`/root/auth-backups/` with mode 0600 and sha256-verified byte equality,
extending the 2026-10-14 backup practice. Receipts use sha256 prefixes of
the file names only: `450dc450` (original) and `463ffe6f` (new), sizes
1221/1218 bytes, copy=VERIFIED for both.

### Doctor per-credential refresh visibility (repo 9f6d20d + projection)

Commit `9f6d20d` (`feat(cpa): per-credential oauth refresh visibility for
multi-file lanes`) changed `scripts/remote/cpa-guardrail-doctor.sh`:
when a monitored oauth provider holds more than one auth file, the
oauth-monitor block now emits per-credential `expiry_known`,
`refresh_age_hours` and coarse `status` lines, labeled by a sha256 prefix
of the auth file name so account emails never reach doctor output. The
`WARN_MULTIPLE_ACTIVE_FILES` observation stays non-blocking; single-file
and codex gate behavior is unchanged. A behavioral test feeds two
antigravity files (one valid, one expired) and asserts the six new lines,
the redaction of file names and token material, and the unchanged monitor
status.

## Local proof

- Focused doctor tests: `48 passed, 68 subtests` (includes the new
  multi-file visibility test).
- Full repository gate on the settled tree (after the parallel session's
  `f6d2a3d` ninth-review batch landed): `817 passed, 1 skipped, 403
  subtests`; bandit, ruff check, ruff format (68 files) and mypy clean.
  The one skip is the opt-in real SSH integration test.
- Ruff check and `git diff --check` clean on the changed files.

## BWG transaction

`GUARDRAILS_APPLIED` with the shared maintenance lock, backup and rollback
checks; transaction backup directory
`/root/cpa-guardrails-backup-20261029T162618.043253052Z`. The projection
also carried the already-committed ninth-review hardening of
`cpa-guardrail-apply.sh` (`f6d2a3d`); both batches were gated together.
The catalog contract verified `has_antigravity_gemini_3_8_flash_high=True`
and the full pinned model set; backup pruning kept 8.

## Post-deploy verification

- Strict doctor: `DOCTOR_CONTRACT_OK`, all 12 projection entries `MATCH`.
- The new per-credential lines are live in production for both files:
  `oauth_antigravity_credential_450dc450_status=METADATA_OK` and
  `oauth_antigravity_credential_463ffe6f_status=METADATA_OK` with
  `expiry_known=true`.
- `refresh_age_hours=unknown` for both files: these Google auth files
  carry no refresh-timestamp metadata key; expiry visibility is
  unaffected and refresh failures remain covered by the provider-labelled
  container signal scan (7d=0).
- `oauth_lane=antigravity-oauth posture=OPEN hold_reason=none`;
  `lane-hold-antigravity-oauth=OPEN`.
- No OAuth re-login, credential mutation, catalog change, admission
  parameter change or fault injection was performed.

## Rollback

Under the maintenance lock, restore `cpa-guardrail-doctor.sh` from the
transaction backup directory (or revert commit `9f6d20d` and re-run the
guarded `-Apply`), then re-run strict doctor and confirm the contract.
The on-box credential backups are additive copies and are removed by
deleting the two new files in `/root/auth-backups/`; the live auth files
were never modified.

## Remaining boundaries

Whether the spare credential actually serves on a quota/failover rebind
remains unverified by design: manufacturing OAuth failures was rejected
by the standing no-fault-injection ruling. The first natural rebind or
upstream event will exercise it. Provider compliance, quota and ban-risk
surfaces for a second account on the same egress IP remain governed by
the 2026-10-15/17 risk rulings and the existing incident runbooks.
