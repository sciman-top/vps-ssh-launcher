# BWG CPA v8.0.40 and WorkBuddy plugin update (2026-10-23)

## Scope and rationale

Authorized single-host update on `bwg`. CPA official release `v8.0.40`
(`2026-10-23T00:10:37Z`) was pinned to amd64 digest
`sha256:f7a424d78498dc413808414a3143c8b23cddf78eecdea69979cf88adf3bfa9ef`.
Its release notes include bounded decoded-body delivery to auth plugins, Codex
credential-selection correction, mixed Gemini/function-tool invocation, and
plugin-host race fixes. No change promises cache or token-throughput gains.
WorkBuddy v0.8.79 / 0.9.65 at source commit
`8ccf96b9a91cf695ad33995dff2685aa9d08ec28` retains the existing selected-account
and failover patches, includes the v0.9.64 truncation witness, and fixes billing
transport/DNS error diagnosis. Isolated build source tarball SHA-256:
`dea8d55f1325935337feb67d941cfbd1a573b2cf08c5680fe4d5a3ae91f35567`; built
artifact SHA-256: `d52a0a3b9b4aa5fc146f60330c7e9295fe9339142cc64eb218999f77f48bb6b8`.

## CPA transaction and verification

- Deployment: `outputs/deploy-cpa-v8040-canary-20261023.ps1` and `.sh`. The
  transaction verified the old digest-pinned image and WorkBuddy plugin hash,
  readiness, admission traffic-guard, exclusive request drain, pre-existing
  OAuth hold baseline, free space and a non-OAuth pre-generation smoke.
- Result: `v8.0.40@sha256:f7a424d7…` running, WorkBuddy `0.9.64` registered,
  readiness `HEALTH_OK`, exit 0 at `2026-10-23T04:05:57Z`; no new lane hold.
  Backup compose: `/opt/cliproxyapi/backups/20261023T040546.539882873Z-from-v8.0.39-canary-v8.0.40`.
- Post-update cache canary `glm-5.3-flash`: request 1 miss; request 2 read
  4096/4114 tokens, hit ratio 0.9956, `HEALTH_OK`.
- One non-OAuth streaming check: HTTP 200, 84 events, first text 2.465s, total
  2.490s, exact expected text and terminal markers. This is a controlled sample,
  not a sustained throughput or OAuth-lane acceptance test.

## WorkBuddy build and transaction

Build source is pinned by commit and source archive hash; build ran in the
existing 1-CPU/768-MiB disposable Docker isolation with no production mounts.
`go vet`, `go test -count=1 ./...`, and `CGO_ENABLED=1 go build -buildmode=c-shared`
passed. Existing CN/INTL configuration and auth files are untouched. The
0.9.65 installation script is prepared to atomically replace the plugin only
after verifying its hash, backup the current `.so`, drain active requests,
assert registration/readiness, and restore the prior plugin on local-contract
failure.

Repeated build preparations refused the already-existing source tree (exit 1);
the completed artifact was reused, and the install script pins its observed
SHA-256 directly. Installation completed at `2026-10-23T04:14:37Z`, exit 0:
container dependency check `LDD_OK`, plugin registration `version=0.9.65`,
`HEALTH_OK`, no new OAuth holds. Old plugin backup:
`/opt/cliproxyapi/backups/20261023T041434.262710010Z-plugin-0964trunc-to-0965`.
No credential, selected realm, account or background-task settings changed.
Final strict doctor returned `DOCTOR_CONTRACT_OK`, exit 0; all projected hashes
matched. Active OAuth lanes had no durable holds; chatgpt remained in its
existing upstream cooldown window. Final cache samples both read 4096/4114
tokens (0.9956); stream sample first text 2.453s, total 2.502s, 66 events, PASS.
OAuth post-swap generation was deliberately not exercised; provider acceptance
and long-run quality/throughput remain outside these controlled checks.

Focused updater tests: `python -X utf8 -m pytest tests/test_cpa_updater_apply.py
-q` -> 27 passed, 35 subtests passed. The first run without UTF-8 mode hit an
existing Windows GBK decode failure reading a UTF-8 doctor fixture; no production
or test behavior was changed to hide it.

## Risk posture and rollback

No OAuth probes, credential edits, account rotation, realm switching, retry, IP,
user-agent or concurrency changes were made. Existing strict admission remains:
Codex inflight 2, Antigravity 2, WorkBuddy 1; WorkBuddy background check-in,
tasks, lifecycle and token keepalive remain disabled. These controls reduce
retry amplification; they cannot guarantee against provider enforcement or
account limitation. Public terms restrict these third-party OAuth/plugin uses;
official API/enterprise access is the compliant alternative.

CPA rollback restores the backup `compose.yml` and runs `docker compose up -d
--pull never`; plugin rollback restores the transaction's `.so` backup and
restarts CPA. Verify plugin registration and readiness after either rollback.
