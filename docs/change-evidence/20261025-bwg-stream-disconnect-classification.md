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

Deployment and post-load receipts follow after the transaction completes.

The first deployment attempt used a 60-second drain without pausing ingress and
exited 75 (`DEFER active_requests_did_not_drain; live_unchanged`). No live code or
pin was replaced. The next transaction reuses the existing reviewed maintenance
wrapper with the admission transaction as its host payload and a no-op plugin
payload. It allows at most 180 seconds to drain after ingress is paused; it
restores both Nginx locations on exit. This is deployment downtime, not an
acceptance probe or a provider recovery action.
