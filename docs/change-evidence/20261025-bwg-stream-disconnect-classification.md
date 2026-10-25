# BWG desktop errors and SSE disconnect classification (2026-10-25)

## Scope and sources

The user authorized continuous task-scoped repairs after reporting recurring
desktop stream errors and 502/503/403/429, then supplied an Nginx HTML 503 sample.
This transaction preserves existing credentials, routes, retry budgets, lane
concurrency and cooldown policy. Only `bwg` admission code and its integrity pin
are deployment targets. The separate CPA/plugin upgrade is recorded in
[its own receipt](20261025-bwg-cpa-v8042-plugin-0969.md).

- [Nginx auth_request documentation](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html):
  2xx allows access, 401/403 deny access, other subrequest statuses are errors.
- [CPA v8.0.42 release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.42)
  and [PR #6784](https://github.com/router-for-me/CLIProxyAPI/pull/6784): the
  compatible reasoning-only terminal conversion proposal is not an established
  fix for this deployment's native Codex OAuth errors. The PR remained open at
  review; its Responses-path limitations are discussed in its review.
- Similar reports in [discussion #2138](https://github.com/router-for-me/CLIProxyAPI/discussions/2138)
  and [issue #2358](https://github.com/router-for-me/CLIProxyAPI/issues/2358)
  establish that the symptom is shared, not the cause of a particular request.

## Fresh incident observations

All timestamps below are UTC; China time is UTC+8.

- At 04:35–04:36, strict doctor passed on v8.0.41 despite natural incomplete
  streams. The last-hour journal contained seven ChatGPT OAuth `HTTP200`
  `missing_terminal` results (Sol five, Luna two), plus three `busy` rejections.
- Rotated external POST access logs included four `status=500 upstream_status=-
  auth_status=502` requests at 04:22:04–04:22:05 around the prior container
  replacement. These failed during authentication, before business forwarding.
- The supplied HTML 503 matches the independently backed-up temporary ingress
  maintenance response at 04:41:05–04:42:24. The screenshot alone has no request
  timestamp/ID and cannot identify one log entry. Readback at 04:44:41 showed
  zero maintenance markers, v8.0.42 running, and 24 post-recreation completed
  protocol results across GPT/DeepSeek/GLM. Strict doctor at 04:45:22 passed.
  This short observation does not establish long-term reliability.

## Causal repair

The SSE forwarding loop checks downstream socket liveness both before waiting
for an upstream chunk and after receiving it. A positive socket-peek disconnect
previously retired the upstream reader without setting `client_lost`. When the
heartbeat had not set that flag first, the result could be reported as
`missing_terminal`; if the terminal event had already arrived, the result could
even be reported as successful. The two exit paths now set the flag before
retiring the reader. Genuine upstream EOF still requires a real terminal event.
Historical `missing_terminal` counts cannot be retroactively assigned a cause.

Added metadata is bounded: inspected byte/event counts and an allowlisted last
SSE event category. Unknown event names become `other`; no generated output,
prompt, account identifiers or tokens are added to logs. Existing reason fields
remain compatible. Doctor aggregates explicit protocol reasons and separates
authentication-subrequest failures without guessing provider failures from 5xx.

No success event is synthesized and no failed request is replayed. The change
corrects client-disconnect handling and attribution; it does not repair unknown
upstream truncation, provide capacity, or prove account eligibility.

## Deployment and verification

Transaction source: `outputs/deploy-admission-disconnect-20261025.sh`.
It checks the exact live code/pin, stages and parses the candidate, takes the
shared maintenance lock, waits up to 180 seconds for the exclusive request drain,
and backs up code/pin before replacing either. Only admission is restarted.
A busy drain leaves live files unchanged; failures after mutation restore both
files and restart admission. It does not pause Nginx or recreate the CPA container.

Local verification:

- New doctor classification regressions first failed on the old implementation
  (missing reason and auth-subrequest output), then passed after the repair.
  The updated fixture includes explicit `missing_terminal`, client disconnect,
  capacity, success, legacy entries and an unrecognized sensitive reason.
- Runtime focused suite: 35 passed, 170 deselected.
- Full gate build/test/Bandit/Ruff-check phases passed: 759 passed, 1 skipped,
  370 subtests passed. The skip is the opt-in real SSH integration test.
  The runner then exited 1 at Ruff format for two formatting differences in
  the added test. After formatting that file, the same full target set passed
  Ruff format (65 files) and mypy (65 source files), exit 0. No semantic source
  was changed to repair the format gate. The final doctor fixture rerun passed
  3 tests, 33 deselected. Completed gate phases were reused.
- Both shell files passed `bash -n`; `git diff --check` passed.
- Remote dry-run exited 0: live code/pin matched baseline, candidate hash and
  AST passed, all four lane holds were null. It did not replace live code.

Deployment completed with exit 0:

- Old pin: `4be8252cea2136b85dd85de736cc0ac4e32d537898d394d1b2fa894f36858265`.
- New LF source/pin: `621847118304eb7b5206ac994c01c72bd0d885a2d9718c4e40b8ca64cfd78623`.
- `STAGED_SYNTAX_OK`, `ADMISSION_IDLE_OK`, `ADMISSION_INTEGRITY_OK`, `DEPLOY_OK`.
- Code/pin backup:
  `/opt/cliproxyapi/backups/20261025T050650.608959179Z-admission-disconnect`.
- Ingress backup/window began at 05:06:32Z:
  `/opt/cliproxyapi/backups/20261025T050632.059373119Z-admission-disconnect-ingress-window`.
- `NO_PLUGIN_MUTATION`, `ADMISSION_MAINTENANCE_PASS`, `nginx -t` passed,
  and `INGRESS_RESTORED`. No CPA container/plugin restart was performed.

For rollback, pause ingress with the same reviewed wrapper, acquire the maintenance
and exclusive admission request locks, restore `cpa-admission.py` and
`cpa-admission.sha256` from the code/pin backup, run the integrity checker,
restart admission, verify health/hold state, and restore ingress. Git revert alone
does not roll back the host. Rollback was reviewed, not force-triggered in production.

Post-load readback and natural-flow observations are recorded after verification.

The first deployment attempt used a 60-second drain without pausing ingress and
exited 75 (`DEFER active_requests_did_not_drain; live_unchanged`). No live code or
pin was replaced. The next transaction reuses the existing reviewed maintenance
wrapper with the admission transaction as its host payload and a no-op plugin
payload. It allows at most 180 seconds to drain after ingress is paused; it
restores both Nginx locations on exit. This is deployment downtime, not an
acceptance probe or a provider recovery action.

## Coordinated final candidate

The other CPA upgrade chat stopped all further writes and handed final deployment
and Git closeout to this chat with explicit user authorization. Candidate commit
`07a2a3a` has source/pin `fdc76675756e2800b3a7e719c085166d9c9ab02928dab4578ec04486427d7492`.
Its full gate receipt is exit 0: 760 passed, 1 skipped, 370 subtests passed;
build, Bandit, Ruff and mypy passed. Admission suite: 206 passed; doctor: 36 passed.
These exact-source receipts are reused; only the deployment scripts changed here.

Before final projection, the prior deployed `621847...` readback at 05:58:03Z
showed 382 completed and 2 client_disconnect results, 0 missing_terminal;
384 results contained the new bounded metadata. This is existing gateway traffic,
which may include other chats' non-OAuth controlled requests, not exclusively
natural user traffic and not proof for the final candidate.

A read-only review found no deployment blocker. One logging limitation remains:
non-SSE `response.read1()` exceptions occur outside `_forward()`; its comments
about detecting downstream reset after a paused read overstate that coverage.
Such read errors can still be logged as upstream transport errors when the
client is also gone. They clear capacity state and do not advance the breaker.
The added regression covers the downstream body write after client reset; it
does not prove classification of simultaneous upstream read failure.

The final PowerShell wrapper corrects the reserved `$Host` variable, validates
candidate source against the repository pin, and invokes the actual ingress
wrapper. Apply accepts only `apply-paused`. Unused/dead helper and a duplicate
pin check introduced during concurrent work were removed. Remote dry-run passed
with live baseline `621847...`, candidate `fdc766...`, and four null lane holds.
Final deployment exited 0 at 06:08:40Z (14:08:40 China):

- `STAGED_SYNTAX_OK`, `ADMISSION_IDLE_OK`, `ADMISSION_INTEGRITY_OK`,
  `DEPLOY_OK`, `ADMISSION_WINDOW_PASS`, and `INGRESS_RESTORED`.
- Source/pin: `fdc76675756e2800b3a7e719c085166d9c9ab02928dab4578ec04486427d7492`.
- Code/pin rollback backup:
  `/opt/cliproxyapi/backups/20261025T060840.237435937Z-admission-disconnect`.
- Ingress backup:
  `/opt/cliproxyapi/backups/20261025T060832.495472582Z-admission-ingress-window`.
- Only admission restarted; Nginx was reloaded to enter/leave the window.

Fresh readback at 06:10:11Z exited 0: exact live source/pin match, admission
started 06:08:40Z, health `ok`, four null holds, zero temporary maintenance
markers. Since restart, existing traffic produced 30 `completed` results with
new metadata: DeepSeek 14, Gemini 4, GLM flash 6, GPT-6 Luna 3, Sol 3;
no missing-terminal result in this short interval. No synthetic OAuth request
was sent. This establishes activation and short observation, not long-term
stability or resolution of provider quota, capacity or account restrictions.
Strict doctor at 06:10:23Z exited 1 (`DOCTOR_CONTRACT_FAILED`). All admission,
integrity, Nginx, source projection and service checks passed. The distinct
failure is provider-config drift: live opencode models contain `beam-free`
but omit `longcat-2.5-preview-free`, while unchanged repository policy/routes
expect both. This also causes input-modality and context-configuration failures.
The upgrade chat confirms it neither received authorization nor removed that
model. Ownership/intent is being checked before any restoration; this doctor
receipt must not be presented as a full pass. CPA remains v8.0.42 with unchanged
container start at 04:42:21Z; the admission-only deployment did not recreate it.