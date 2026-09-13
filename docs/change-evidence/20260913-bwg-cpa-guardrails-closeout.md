# BWG CPA guardrails closeout — 2026-09-13

## Scope and authorization

- Scope: the explicitly authorized `bwg` profile only.
- No `zz` access or mutation, no SSH tunnel data plane, and no random public
  path rotation.
- Credentials, capability paths, IP addresses, tokens, and full sensitive
  commands are intentionally omitted.

## Source and remote projection

- Source commit: `cbbe808` (`完善 BWG CPA 风控与 fail2ban 契约`).
- Changed source paths: `scripts/cpa_bwg_guardrails.ps1`,
  `scripts/remote/cpa-fail2ban-filter.conf`,
  `scripts/remote/cpa-fail2ban-jail.conf`, `test_scripts.py`, and the related
  README contract.
- The final successful `-Apply` created the remote backup directory
  `/root/cpa-guardrails-backup-20260913T133410Z/` before mutation.
- The projected remote file hashes from the fresh readback were:

  ```text
  config.yaml:       8cbeb2aea0369b06bf2fa96211fe250ab6142282dffc9bc385651db7d1886d5d
  compose.yml:       dcbc00a2a0cdfa0e3cab0c3004ccf6a9866f26bbaa0e7c39dcc87d3d45208268
  auto-update.sh:    e418982686aef62ea369f2f9ddf94e641071fb90d7e396d6574d5752c79e3adf
  cpa-gateway.conf:  c8c84f083beaa541f1c818e5b9b1c477bc8ae248e010891b53f91c884a50a20b
  fail2ban filter:   8e9b785fc6faac280be2cb39153564c24f88307e3de57a0dca7db307699b16ce
  fail2ban jail:     7d737264f354e34686bd655a1c505a616e52f2315c25515385fe445d661ea302
  ```

## Verification

- `-Apply` returned `CONFIG_POLICY_READY request_retry=0`,
  `READY_STATUS=200`, and `GUARDRAILS_APPLIED`.
- Fresh strict doctor at `2026-09-13T13:38:08Z` returned `DOCTOR_CONTRACT_OK`:
  CPA was running with restart count `0`; CPA remained loopback-only on
  `8317`; Nginx remained the sole public gateway on `8443`; the random-path
  route contract was 401/404/404; remote management was disabled;
  `identity-confuse` was absent; and the Nginx/fail2ban safety contracts were
  present.
- The security access log was read back with
  `limit_req=PASSED limit_conn=PASSED`. Current 24-hour status observations
  were 200=147, 401=15, 404=32, 400=15, and 429=3. About 1001 legacy-format
  lines remain unparsed; this is a telemetry-quality warning, not evidence of
  provider account action.
- Repository closeout passed `111 passed, 1 skipped, 56 subtests passed`,
  Bandit, Ruff, Ruff format, Mypy, and `git diff --check`.

## Recovery and open boundaries

- The apply backup is the remote recovery boundary; Git rollback alone cannot
  restore host state. The apply rollback path now restores only the files in
  this transaction, validates Compose/Nginx/fail2ban/CPA readiness, and emits
  `ROLLBACK_VERIFIED` or `ROLLBACK_FAILED`. No failure-triggered rollback was
  needed during this successful apply, so that path is contract-tested but not
  live-fault-injected here.
- The updater currently retains three backups totaling about 5.3 MB, with
  about 33 GB free on `/`. No historical backup was deleted because a
  last-known-good and retention policy was not defined; deletion would weaken
  the recovery boundary without that decision.
- Existing CPA client keys were not rotated. Rotation requires coordinated
  update of every client and is outside a safe unilateral maintenance action.
- These checks establish `repo_verified`, remote projection, host-loaded state,
  and a controlled route/readback probe. They do not establish provider-side
  quota health, immunity from account bans/rate limits, natural long-term
  acceptance, or any claim of “降智” prevention.
