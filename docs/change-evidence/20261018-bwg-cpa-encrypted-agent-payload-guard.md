# 2026-10-18 BWG CPA admission encrypted-agent payload guard

## Scope and authorization

- The user authorized one bounded maintenance of BWG `cpa-admission` after
  CI passed: refresh idle checks, back up and verify the admission source and
  integrity pin, project the change, restart this service once, and verify the
  loaded guard without a model-generation request. Rollback was authorized on
  failure.
- Repository change: `3b3a85a` adds a fail-closed response for opaque encrypted
  `agent_message` bodies on cross-provider Claude, GLM and Gemini Responses
  routes. `1723c9a` fixes a Windows Python 3.11 test-fixture compatibility
  issue. The latter does not change production code.
- Both Linux/Python 3.13 and Windows/Python 3.11 CI jobs passed:
  <https://github.com/sciman-top/vps-ssh-launcher/actions/runs/39126567169>.
- The screenshot supplied during the maintenance shows Claude Haiku returning
  a response, while saying it could not read the encrypted task and had not
  inspected or edited files. This confirms Claude was callable for that
  response; it does not prove that the delegated review task was understood or
  completed. It is consistent with the gateway payload-shape boundary addressed
  here.

## Deployment and rollback

- Scope was BWG only over the configured strict-host-key SSH route. No ZZ host,
  CPA container, Codex process, credential, provider selection, route, cooldown
  configuration or cooldown state file was changed.
- Initial fresh read showed `cpa-admission` active, health `ok`, all four lanes
  idle, no retired readers, source and pin both at the old digest, and the
  expected CPA v8.0.32 container running.
- The first projection attempt stopped before restart because a local
  maintenance-script field-name check falsely reported the unchanged CPA
  container as changed. Its trap restored the old source and pin. Backup
  `/var/backups/vps-ssh-launcher/cpa-admission-20261018T122423Z` was verified
  against the restored files; admission retained its original PID and the CPA
  container remained running. The check was corrected before the successful
  attempt.
- Successful backup:
  `/var/backups/vps-ssh-launcher/cpa-admission-20261018T122625Z`.
  It contains the old source, old pin and redacted preflight metadata. The old
  source and pin both hash to
  `e2735c1d8b0e9ab3e1bfe1f7647d32bcd1a0ba604bd3286fb0f9661cf9cb3549`.
- Projected source and pin both hash to
  `76eeb1c6deefdc8032c907587b7a5452a999d0e9f5fa38c5f307edc150a52a59`.
  The remote integrity checker returned `ADMISSION_INTEGRITY_OK` before and
  after restart. The source compiled before projection.
- `systemctl restart cpa-admission` was issued once. PID changed from `1071742`
  to `1102605`; the service is active. CPA container ID prefix remained
  `5f2f77093d86`, with the same v8.0.32 digest and start time.
- Rollback entry, if separately needed: restore the two files from the
  successful backup, run the integrity checker, then restart only
  `cpa-admission` so it loads the restored source. The backup directory is
  retained; neither backup was pruned.

## Loaded guard and state verification

- Post-restart health was `ok`; four lanes had `inflight=0`, `pending=0`, and
  there were no retired readers.
- One synthetic request was sent to the loopback admission listener with a
  dummy encrypted envelope. It returned HTTP `422`, code
  `encrypted_agent_payload_unsupported`, and `upstream_contacted=false`. This
  confirms the loaded guard rejected the envelope locally without a generation
  request or provider contact.
- The current lane snapshot has no active lane or model cooldown, no hold, and
  zero failure streak. Before restart, a bounded journal summary from the
  running service's full lifetime found no upstream capacity/rate-limit
  responses; persisted Claude and Antigravity lane state had zero remaining
  cooldown and no hold at restart. Their state files were not modified. These
  checks support that no active cooldown was cleared by the restart.
- No Claude, GLM, Gemini or GPT model-generation request was issued as part of
  this maintenance. A usable native cross-provider delegation task still needs
  to be separately accepted end to end; model callability alone does not prove
  task-payload compatibility.

## Verification boundary

- `repo_verified`: local focused tests and the full two-platform CI matrix
  passed for the repository commits.
- `filesystem_projected`: remote source and pin match the reviewed digest, with
  recoverable backups retained.
- `host_loaded`: service PID changed, health is ready, and the synthetic
  no-upstream rejection confirmed the new handler is active.
- `live_accepted`: not claimed for cross-provider subagent execution; no real
  model task was sent.
- `Removed: N/A`.
