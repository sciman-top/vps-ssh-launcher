# BWG CPA session affinity quota rebind projection (2026-10-29 +08:00)

## Scope and authorization

The user authorized continued repair and optimization of the BWG CPA risk
control stack. This transaction made one reviewed routing-policy change:
explicitly set `routing.session-affinity-rebind-on=quota` for CPA v8.0.46.
No OAuth credential, API key, model alias, realm pin, concurrency limit, retry
budget, cache key, plugin binary, or provider route was changed.

## Reason for the change

CLIProxyAPI v8.0.46 defaults an omitted `session-affinity-rebind-on` to `any`.
The `quota` mode preserves an established session binding across transient
5xx, empty-stream, and similar transient failures, and rebinds only for quota,
authentication, model-support, or permanent-unavailability signals. This
reduces transient account spread and preserves prompt-cache locality for the
existing multi-credential pool.

The setting is global to CPA routing. It is not WorkBuddy realm isolation and
does not create an account-level RPM/TPM budget.

## Repository change

Commit `b88a6ef` (`fix(cpa): preserve session affinity across transient failures`)
updated:

- `scripts/remote/cpa_policy.py`
- `scripts/remote/cpa-guardrail-apply.sh`
- `scripts/remote/cpa-guardrail-doctor.sh`
- the isolated acceptance fixture and affected policy/apply tests
- the current CPA gateway and failure-triage runbooks

The semantic policy now fails closed if the key is absent or differs from
`quota`. The doctor prints the deployed value without exposing credentials.

## Local proof

- Focused policy/guardrail tests: `85 passed, 107 subtests`.
- Full repository gate: `807 passed, 1 skipped, 398 subtests`.
- Bandit, Ruff check, Ruff format, mypy, shell syntax, and `git diff --check`
  passed. The one skipped test is the opt-in real SSH test in the full gate.
- The focused format check for remote projected Python files reports existing
  unrelated full-file formatting differences; the repository contract keeps
  remote runtime files byte-projected and does not formatter-rewrite them.
- The external CLIProxyAPI reference checkout is already at v8.0.46 commit
  `9717e325`, which contains the reviewed `quota` semantics.

## BWG transaction

The guarded `-Apply` transaction used the shared maintenance lock, admission
drain, backup and rollback checks. It completed with `GUARDRAILS_APPLIED` and
backup directory:

`/root/cpa-guardrails-backup-20261028T190016.455826877Z`

During the planned reload window one admission connection reset and port 8318
was briefly unavailable. The transaction then returned `HEALTH_OK`, verified
the catalog contract, and completed successfully. No credentials were read or
modified by the policy change.

## Post-deploy verification

- Strict doctor: `DOCTOR_CONTRACT_OK`.
- Deployed readback: `session-affinity-rebind-on: quota`.
- CPA image: v8.0.46, restart count 0 after the transaction.
- Admission integrity and all projected file hashes: `MATCH`.
- ChatGPT, Antigravity and WorkBuddy admission lanes: `OPEN`.
- `MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_DRIFT=none`,
  `MODEL_CONTEXT_MISSING=none`.
- `cpa-health.py readiness`: `HEALTH_OK`.
- SSH integration after reload: `3 passed`.
- No OAuth generation probe, cache canary, account rotation, IP rotation,
  cooldown clearing or retry/concurrency increase was performed.

After the reload, a read-only natural-traffic observation found 14 admission
requests since the transaction, all HTTP 200 with
`protocol_result=completed` and `capacity=false`. CPA logs contained 12
existing `session-affinity: cache hit` lines and no new rebind, upstream 401/403,
or rate-limit event. This confirms post-reload natural traffic and affinity
health; it does not prove the `quota` branch under an induced transient fault.
No fault was injected because manufacturing OAuth/provider failures would add
risk without improving the safety boundary.

## Rollback

Under the maintenance lock, restore the affected `config.yaml` from the backup
directory, restart CPA through the existing guarded path, and confirm
`session-affinity-rebind-on` returns to the previous value or is intentionally
absent. Then run strict doctor, readiness, admission health and projection
checks. Do not restore the whole old configuration over later unrelated edits.

## Remaining boundaries

This change does not prove provider compliance, OAuth account health, strict
WorkBuddy CN/INTL separation, long-term cache retention, or protection from
provider-side bans and quotas. Those remain governed by the existing incident
runbooks and official provider policies.
