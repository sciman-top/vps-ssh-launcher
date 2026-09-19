# 2026-09-19 BWG CPA compose umask hardening

## Scope

- Target: `bwg` only. The `zz` profile was not connected or modified.
- No client key, provider key, OAuth file, subscription URL, or public random
  path was printed, rotated, or committed.
- Authorized by the owner 2026-09-19 late evening as the root-cause fix for the
  error-dump permission drift observed in the same evening's audit.

## Root cause

- CPA hardcodes `0644` on error-request dumps (`os.OpenFile(..., O_CREATE,
  0644)` at reference `internal/logging/request_logger_writer.go:137`); there
  is no config option for the file mode.
- The container's default umask `022` therefore re-created dumps at `0644`
  between external chmod repairs, and the strict doctor's
  `error-dump-permissions` check flaked during active upstream error windows
  (observed twice on 2026-09-19 around 13:2xZ and 13:4xZ).
- Exposure was bounded throughout: `auth/logs` is `0700`, so non-root host
  users cannot traverse to the `0644` files. This fix completes the
  defense-in-depth invariant; it is not an incident response.

## Change

- `/opt/cliproxyapi/compose.yml` gained
  `entrypoint: ["/bin/sh", "-c", "umask 077 && exec ./CLIProxyAPI"]`.
  `CMD`'s relative-path semantics are preserved (same image WORKDIR), and the
  umask survives the `exec` because it is a process attribute.
- Applied as a backup-first single-file projection (`compose.yml` is validated
  but not rewritten by the guardrails `-Apply` transaction). No repository
  source change: compose is remote-owned.
- Container recreated `2026-09-19T14:03:45Z`; authenticated loopback readiness
  probe returned `200`.
- Host backup: `/root/cpa-compose-backup-20260919T1403Z`.

## Verification

- `docker exec ... umask` prints `0022` — this is EXPECTED and is not a fix
  signal: exec sessions are new processes with the runtime default umask and do
  not inherit PID 1's umask.
- Direct proof: `error-v1-chat-completions-2026-09-19T220521-d334ce1d.log` was
  created at `14:05:21Z` by the recreated main process (a deterministic
  deepseek forced-`tool_choice` upstream 400 trigger, the same failure class as
  the real client's recurring 400s) with mode `0600`. Pre-fix dumps were `0644`.
- Fresh acceptance around the change: full `quality-eval` matrix `HEALTH_OK`
  (20/20) and a deepseek-only quality eval `rc=0`; `generation-all` per-route
  lines healthy for luna/glm/deepseek. Sol/Terra had a transient ai.input.im
  window (13:35-13:40Z, sol 120s network timeout, terra 502) that had already
  recovered; `UPSTREAM_UNAVAILABLE` exit 10 is the designed signal for that.
- Post-recreate strict doctor: `DOCTOR_CONTRACT_OK` including
  `error-dump-permissions=OK`, `auth-permissions=OK`, `semantic-policy=OK`.
- Side benefit: future OAuth re-enrollment auth JSONs are now written `0600`
  natively; the runbook's manual chmod step becomes redundant.

## Risk boundary

- `umask 077` affects only files the CPA process creates inside its mounts
  (error dumps, temp request/response bodies, plugin state, auth JSONs). All
  consumers are host root; nothing requires group/other read.
- If a future apply flow regenerates `compose.yml` without this entrypoint,
  dumps regress to `0644` until the next repair; the doctor check catches it.

## Rollback

```bash
cp -a /root/cpa-compose-backup-20260919T1403Z/compose.yml /opt/cliproxyapi/compose.yml
docker compose -f /opt/cliproxyapi/compose.yml up -d
```
