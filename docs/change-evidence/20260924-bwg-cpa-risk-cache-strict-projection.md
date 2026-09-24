# BWG CPA risk/cache strict projection — 2026-09-24

## Scope

- Scope: the explicitly authorized `bwg` profile only. No `zz` access or
  mutation occurred.
- The public Nginx plus random-path data plane was retained; no path/key
  rotation, account/IP rotation, SSH tunnel data plane, or provider retry was
  introduced.
- Credentials, host addresses, capability paths, tokens, request bodies, and
  full sensitive commands are intentionally omitted.
- Slot 3's HTTP upstream remains in place under the user-approved constraint;
  the apply receipt keeps its cleartext-transport warning explicit.

## Projection and recovery

- Source commit: `b915b31` (`修复 Windows SSH 信任库复用与 Bash 测试`).
- The strict host-key workflow now explicitly reuses the current Windows
  user's OpenSSH `known_hosts` when it is readable. It does not accept unknown
  or changed keys and only requires that store in strict mode.
- `bwg -Apply` completed with `GUARDRAILS_APPLIED` and `READY_STATUS=200`. It
  created one remote backup at a redacted timestamped
  `cpa-guardrails-backup-*` location. Git rollback alone cannot restore this
  remote projection.

## Fresh verification

- A strict doctor passed both before and after Apply with
  `DOCTOR_CONTRACT_OK`. It verified loopback-only CPA binding, one public
  gateway listener, random-route and 401/404/404 contracts, Nginx/fail2ban
  configuration, permissions, zero retry policy, no global account
  concurrency limiter, and remote syntax/config checks.
- The running CPA remained on the pinned v7.3.15 image with restart count 0
  after the post-Apply readiness window.
- One bounded GLM generation returned `HEALTH_OK`; no retry was made.
- The bounded DeepSeek cache canary returned `HEALTH_OK`; two ephemeral
  same-session samples each reported `hit_ratio=0.9579`. This is controlled
  canary evidence only, not a promise for OAuth, the HTTP bridge, or natural
  user traffic.
- Local full gate after source changes: `156 passed, 1 skipped, 181 subtests
  passed`; Bandit, Ruff, Ruff format, Mypy, and `git diff --check` passed.

## Boundaries

- The doctor observed existing upstream 429/502/503 and short-gap client retry
  patterns in retained logs. Those are classified for observability; this run
  does not add provider retries, change upstream routing, or claim account
  safety, quota health, quality, or natural acceptance.
- Cache usage-queue aggregation remains non-consuming by default. No raw usage
  records were fetched in this run.
