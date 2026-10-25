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
## User-confirmed Longcat retirement

The user subsequently confirmed that Longcat was intentionally removed and
instructed us to preserve that state. The route manifest, catalog expectations
and slot-2 runbook were aligned by removing only that model declaration and its
optional-model entry. Slot 2, beam-free, credentials, transport headers and all
other model declarations remain unchanged. Remote preview exited 0:
`STAGED_POLICY_OK`, `ROUTE_PREVIEW_OK only_longcat_removed no_config_or_credential_change`.
The candidate manifest SHA256 is
`c095f9380ca3b7710ded5a444779b50eba099fd98b6f5c008b3aee6f4adeb796`.
The new policy was checked against the existing live config using a temporary
copy of the policy/manifest; no live config was modified during preview.
The full gate rerun exited 0: 760 passed, 1 skipped, 370 subtests passed;
build, Bandit, Ruff check/format and mypy all passed. The skipped test is the
opt-in real SSH integration. Final manifest projection exited 0 at 06:19:11Z:
`ROUTE_DEPLOY_OK`, source hash matched the preview candidate, and the unchanged
live config passed policy validation. No service was restarted or reloaded.
Rollback backup:
`/opt/cliproxyapi/backups/20261025T061911.144957Z-longcat-retirement/cpa_provider_routes.json`.
Restore only this manifest from backup to undo projection; repository retirement
would also need reversal for doctor alignment. Reintroducing Longcat into the
actual CPA config is outside this transaction and contradicts the user's choice.
Final strict doctor exited 0: `DOCTOR_CONTRACT_OK`, `semantic-policy=OK`,
`model-input-modalities=OK`, `MODEL_CONTEXT_MISSING=none`, admission integrity
and health OK. All projected artifacts, including the retired-model manifest
and the deployed admission source/pin, matched repository expectations. This
closes the earlier policy-drift failure; it does not establish provider quota,
account eligibility or long-term model-generation stability.
## Follow-up acceptance and post-header transport repair

The user requested continued optimization plus simulated/controlled acceptance.
Fresh readback at 06:31:24Z matched deployed admission `fdc766...` and manifest
`c095f9...`, health OK, four null holds and zero maintenance markers. Since
06:08:40Z existing gateway traffic produced 407 completed, 3 client_disconnect,
and 1 malformed_event results; no missing_terminal. The malformed event had
891433 inspected bytes and 693 events. Current metadata cannot distinguish an
upstream malformed JSON frame from the probe's bounded inspection limit; this
sample remains unclassified and must not be declared repaired.

Two sequential non-OAuth GLM Flash streaming requests each used one attempt,
zero retries, a 90-second deadline, and the maintained acceptance parser:

- VPS loopback via Nginx TLS -> admission: PASS, HTTP 200, valid SSE, expected
  `pong`, successful response.completed, 36 data events, total 1561.48 ms.
- Windows current configured provider/client key via public TLS: PASS, HTTP 200,
  valid SSE, expected `pong`, successful response.completed, 94 data events,
  total 3071.16 ms. The key/path remained internal; neither was printed.

These are short controlled samples, not Desktop tool-turn acceptance, throughput
benchmarks, or provider/account safety evidence. No synthetic OAuth request was
sent. The Windows sidecar ports 10909/14185 were not listening; the current
provider directly targets the public gateway, so that actual configured route
was used instead of starting or changing a sidecar.

A new local scripted-upstream SSE test exposed an actual gap: after headers and
a partial stream, an upstream chunk truncation logged IncompleteRead but left
the downstream keep-alive waiting for a terminator until its own read timeout.
The tightened regression failed before the fix (TimeoutError instead of timely
IncompleteRead). The transport exception handler now closes an already-started
response without synthesizing a success frame. The regression requires prompt
failure, lane release, and no capacity streak/cooldown advance.

The concurrent upgrade chat was stopped and handed back its incomplete non-SSE
regression. Its original test passed without the intended patch, so the parent
strengthened the fixture to force a real chunked read1 IncompleteRead before any
downstream body write. It then failed on the unchanged implementation with
upstream_error instead of downstream_disconnect. The non-SSE read handler now
checks downstream liveness and converts that case to DownstreamClientDisconnected.
This closes the earlier documented non-SSE read-error attribution limitation.
Focused verification after both repairs: 5 passed, 203 deselected, exit 0.
Candidate source/pin:
`d59e5a40af8785303e2c67ddc81dea40c93cae7e3ee82c80a6c97551f2b591e8`.
Final pre-deployment verification: full runner build/test/Bandit/Ruff phases
passed with 762 passed, 1 skipped, 370 subtests passed. Runner initially exited
1 at mypy for a test helper returning Any; an explicit bytes cast fixed only
that test annotation. All 65 mypy target files then passed, exit 0; both new
regressions reran with 2 passed, and Ruff check/format passed. Earlier successful
gate phases are reused; this is composed gate evidence, not a fabricated full
runner exit 0. Independent read-only review found no deployment blocker.
Remote dry-run exited 0, live baseline `fdc766...`, candidate `d59e5a40...`,
all four lane holds null. The wrapper now accepts an explicit validated
ExpectedLivePin (defaulting to this transaction's baseline) instead of hardcoding
the previous 621847 baseline.
Final admission deployment exited 0: `ADMISSION_IDLE_OK`,
`ADMISSION_INTEGRITY_OK`, `DEPLOY_OK`, `ADMISSION_WINDOW_PASS`,
`INGRESS_RESTORED`. Live code/pin is `d59e5a40...`.
Code/pin backup:
`/opt/cliproxyapi/backups/20261025T064653.911642283Z-admission-disconnect`.
Ingress backup:
`/opt/cliproxyapi/backups/20261025T064648.920210645Z-admission-ingress-window`.
Only admission restarted; CPA/plugin were not restarted.

The user reported repeated HTML 503 during this deployment. Fresh readback at
06:52:08Z found exactly 3 HTTP 503s in the last 20 minutes, at 06:46:54/55/56Z:
197-byte bodies, upstream_status=-, auth_status=-. They coincide with our ingress
maintenance window. These refusals are deployment-induced and must not be
attributed to a model/provider failure. At readback, temporary maintenance markers
were zero, health OK, four null holds, and no later 503 was logged. Further
maintenance windows were stopped; remaining acceptance was read-only or single
non-OAuth requests.

Post-deployment Windows current-provider public-TLS GLM Flash acceptance exited
0: PASS, HTTP 200, expected pong, response.completed, 54 data events, no capacity
marker, total 2462.12 ms. One attempt, zero retries. This validates the deployed
candidate on that controlled route; it does not erase the observed deployment
503s or prove all models and Desktop tool turns are permanently error-free.
Final strict doctor exited 0 (`DOCTOR_CONTRACT_OK`), including admission
integrity/health, semantic policy and no missing context-model declarations.
Fresh post-load observation at 06:54:02Z: admission started 06:46:54Z, 25 completed
protocol results (DeepSeek 15, GLM Flash 1 controlled sample, GPT-6 Luna 2, Sol 7),
no noncompleted result since restart, health OK and all four holds null. Access
logs after 06:47:00Z showed 25 HTTP 200 and loopback contract probes (2 each
401/404), with no 5xx. This is a short bounded observation, not a promise of
future availability. The earlier malformed-event sample remains an unresolved
historical classification, not evidence that every upstream stream is repaired.
## Malformed-event ambiguity follow-up (repository candidate, not deployed)

The user challenged the unresolved malformed_event sample. Reproduction proved
that a valid JSON event above the inspection limit, followed by valid completion,
was incorrectly labelled malformed_event while malformed_event=False and
complete=False. A second integration reproduction found a real success-judgment
bug: malformed JSON followed by valid completion was successful=true and cleared
a half-open lane failure streak from 2 to 0. Both new parameterized cases failed
on the old runtime (2 failed); the doctor inspection-limit fixture also failed
on its prior reason allowlist.

The candidate separates inspection_limit from true JSON/encoding parse failure,
requires no malformed_event before successful recovery, and logs three body-free
flags: sse_inspection_complete, sse_malformed_json, sse_terminal_seen. Tests assert
byte-for-byte forwarding remains unchanged, no false recovery, and distinct safe
metadata. Focused post-fix proof: 5 passed. Doctor treats old malformed-event
counts as ambiguous and does not retroactively reclassify the historical sample.

Candidate source/pin:
`dfe91018f0659009cb8c0d975a4376371396a411d53343a95533d34f49274096`.
Production remains the previously deployed `d59e5a40...` admission. No restart,
ingress change or model request was performed for this diagnostic follow-up.
The candidate is not host_loaded/live_accepted. It must not be described as
already deployed, and a candidate-HEAD strict doctor would correctly report a
source projection difference. Prior production acceptance applies to d59 only.
Final candidate full gate runner exited 0: 764 passed, 1 skipped, 370 subtests
passed; build, Bandit, Ruff check/format and mypy all passed. The skip is the
opt-in real SSH integration. Independent read-only review found no blocker.
This receipt proves repo_verified only; the candidate has deliberately not been
projected/restarted during the user's active calls after the observed maintenance
503 impact. A controlled idle deployment and exact-source live acceptance remain
required before calling this follow-up repair production-complete.
## Final candidate projection and remaining GLM availability failure

The user explicitly requested finishing projection and controlled acceptance.
Fresh dry-run exited 0 against the exact d59e5a40 baseline with four null holds.
The dfe91018 candidate was then deployed successfully: ADMISSION_IDLE_OK,
ADMISSION_INTEGRITY_OK, DEPLOY_OK, ADMISSION_WINDOW_PASS, INGRESS_RESTORED.
Ingress window began 07:17:26Z (15:17:26 China); admission started 07:18:05Z.
Only admission restarted, with existing requests drained; CPA/plugin/accounts
were unchanged. Code/pin backup:
`/opt/cliproxyapi/backups/20261025T071805.176568960Z-admission-disconnect`.
Ingress backup:
`/opt/cliproxyapi/backups/20261025T071726.110657541Z-admission-ingress-window`.

Strict doctor exited 0 (DOCTOR_CONTRACT_OK), including admission integrity,
health and semantic policy. It also reported MODEL_CONTEXT_MISSING=glm-5.3-flash;
this local contract pass is not model-generation availability evidence.

A single Windows-current-provider public-TLS GLM Flash SSE attempt failed:
HTTP 503, no SSE/text/completion, total 19870.61 ms, zero retries. It is not a
passing live acceptance receipt. Readback at 07:20:04Z confirmed exact dfe91018
code/pin, health OK, four null holds, zero maintenance markers, 14 result
lines carrying the new flags. Results: 11 completed, 2 capacity, 1 upstream_error,
1 inspection_limit. The bounded capture therefore contains 15 reason-bearing
results but only 14 rows with all new flags; the unmatched row's code provenance
is not established, and full flag coverage is not claimed. The inspection_limit sample explicitly had
sse_inspection_complete=false, sse_malformed_json=false, sse_terminal_seen=false;
it is no longer misreported as malformed JSON.

Two post-window 503s (07:18:22Z and 07:19:49Z) were GLM route unavailability.
Retained response-only evidence names openai-compatible-zhipu-plan/glm-5.3-flash,
code auth_unavailable, message marker no auth available. The same failure existed
at 15:13:51 and 15:15:51 China, before this deployment. No retained 1302/1308
quota code or explicit retry deadline explains it; the CPA container log bounded
scan exposed no provider error code. Therefore it remains CPA route credential
availability of unproven cause, not demonstrated provider quota exhaustion.
The response.failed SSE result is separately classified as upstream_error.

No cooldown was cleared, no account/key/channel changed, no CPA restart or
further generation retry was performed. The user was asked for current GLM
Coding Plan control-panel quota/rate-limit/account status without sharing a key.
All confirmed local repairs are projected; GLM Flash controlled live acceptance
remains open and must not be reported as all-model recovery.
## GLM recovery and final public acceptance

The user supplied an official-client screenshot showing 83% in the 5-hour quota
bucket, with a 15:31 reset label. This contradicts any unsupported assertion of
persistent overall quota exhaustion, but does not alone prove that the CPA
API-key route has the same eligibility/state as the official desktop session.

Read-only live check at 07:28:28Z (15:28:28 China): zhipu-plan remained enabled,
with one configured key and the exact authorized Coding Plan endpoint and both
GLM models. CPA /v1/models now naturally contained glm-5.3 and glm-5.3-flash.
Admission GLM state had no lane/model cooldown or hold, failure_streak=0;
CPA container still had its original 04:42:21Z start. No cooldown was cleared,
credential/config changed or container restarted to obtain this transition.

Based on the newly observed model availability, one fresh Windows-current-
provider public-TLS GLM Flash SSE acceptance was admitted (one attempt, zero
retries, 90-second deadline). It exited 0: PASS, HTTP 200, 35 data events,
expected pong text, successful response.completed, no error/capacity marker,
total 2294.99 ms (first event 1815.13 ms, first text 2170.39 ms,
completion 2288.14 ms). This closes the previously failed controlled live
acceptance for the deployed dfe91018 candidate; the earlier failed attempt is
retained above, not overwritten or counted as successful.

All confirmed local repairs are now repository-verified, projected, loaded and
have the defined controlled live acceptance. The historical GLM auth_unavailable
trigger remains unproven because the initial provider/credential-state cause is
not retained; current recovery is not a retroactive root-cause determination.
No claim is made that every model/session is permanently error-free. Existing
accounts, keys, route choices, retry/concurrency budgets and cooldown policy are
preserved. No further production mutation is required for this acceptance.

## WorkBuddy 6004 false hold and multi-model 403 incident

Later desktop calls showed HTTP 403 `workbuddy-oauth requires operator review`.
CPA container logs identified four upstream WorkBuddy failures for
`deepseek-v4.1-flash` with `code=6004` at 09:56:43, 09:57:45, 09:59:46 and
10:03:50Z (17:56–18:03 China). WorkBuddy's own wording says 6004 is a
model-level rate limit: only the named model is throttled and other models are
unaffected. The then-deployed admission `31df...` nevertheless mapped 6003 and
6004 to lane scope and could create `retry_window_review_required`, a durable
manual hold. Subsequent DeepSeek requests were therefore rejected locally as
403; a bounded 75-minute query counted 48 such rejections and no provider-side
403 account ban evidence.

The claim that every non-GLM upstream was unavailable is not supported by the
authoritative request bodies: GLM uses the separate Zhipu lane, while the same
window also admitted and completed Terra requests. Screenshots labelled later
turns as GPT, but every 403 journal line names `deepseek-v4.1-flash`; those UI
labels therefore did not prove that the outgoing payload had switched models.
After the repair, natural post-load traffic included a completed GPT-6.1 Sol
request as well as GLM requests.

Repository candidate `029f834` distinguishes the two signals: WorkBuddy 6004
uses the existing model-scoped cooldown path; 6003 remains lane-scoped because
its scope is not proven, and 11140 remains account-restricted. Full gates for
the exact source/pin `49ee2e0c...` exited 0 with 766 passed, 1 skipped and 372
subtests passed; build, Bandit, Ruff check/format and mypy passed. The skipped
test is opt-in real SSH integration. Two test readers were also corrected to
decode UTF-8 explicitly instead of relying on the Windows locale codec.

The first final deployment transaction at 11:34Z intentionally cleared the
false WorkBuddy hold, then correctly failed its postcondition because hold
state differed from the pre-state. Its rollback restored code/pin `31df...`
and the ingress window; readback showed health OK, all four holds null and no
service errors. The reported `ROLLBACK_FAILED` was the hold-comparison guard,
not evidence of a failed code restore. A follow-up commit made state cleanup
idempotent; `bash -n` passed and the second transaction retained the existing
cleared state.

The successful deployment at 11:41Z used exact baseline `31df...`, candidate
`49ee2e0c...`, drained requests, restarted only admission and restored ingress.
Receipts were `ADMISSION_IDLE_OK`, `ADMISSION_INTEGRITY_OK`,
`WORKBUDDY_FALSE_HOLD_ALREADY_CLEARED`, `DEPLOY_OK`,
`ADMISSION_WINDOW_PASS` and `INGRESS_RESTORED`. Code/pin backup:
`/opt/cliproxyapi/backups/20261025T114132.594983631Z-admission-disconnect`.
Ingress backup:
`/opt/cliproxyapi/backups/20261025T114109.778763016Z-admission-ingress-window`.
The reviewed old WorkBuddy state is retained at
`workbuddy-risk.json.pre-6004-model-scope` inside the code/pin backup.

Strict doctor exited 0 with `DOCTOR_CONTRACT_OK`, admission integrity/health
OK and semantic policy OK. Readback at 11:43:52Z matched live source/pin
`49ee2e0c...`, admission start 11:41:32Z, health OK, all four holds null and
WorkBuddy persisted state null. Natural post-load traffic had 20 completed
results, including GLM and GPT-6.1 Sol, with no lane rejections. No synthetic
OAuth request, account switch, credential change or cooldown clear was used.
This proves the false hold is removed and the 6004 classification is loaded;
it does not prove that WorkBuddy will never issue another model-level rate
limit or that every desktop model selector always updates its outgoing payload.

## Wrapped and cached WorkBuddy 6004 follow-up

Fresh retained error responses explain the remaining lane-wide 429 after the
direct-code fix. The plugin returns HTTP 500 `server_error` /
`internal_server_error`, with two message forms: its first rejection contains
an explicit model-only statement and `| raw: {"code":6004,...`, whose JSON tail
can be truncated; subsequent requests receive a bilingual cached model-only
message with no JSON object. The old JSON-only classifier missed both and
fell through to lane-scoped HTTP 500 capacity handling. This is a local
classification defect layered over a genuine upstream model limit, not
evidence that every provider account is banned.

The retained 20:40:24 local-time dump explicitly reports reset at
2026-10-26 12:34:44 UTC+8; the 20:47:24 dump quotes an upstream reset at
2026-10-26 12:16:03 UTC+8. These differing upstream observations are preserved,
not combined into a guaranteed recovery deadline. Neither response contains
a Retry-After header. No account switch or upstream quota reset was attempted.

The repair handles direct JSON, quoted JSON and both exact plugin message
forms. Mixed 6003/auth/account signals remain lane-scoped/protected; successful
generated text is not inspected as an error. The first follow-up deployment
at 12:47:05Z loaded `970eb4ef...`; a newly retained first-rejection dump then
exposed the truncated raw format. Final source/pin `e4d85aab...` includes that
format and was loaded at 12:51:39Z. Both transactions passed idle drain,
integrity/readiness checks and restored ingress; only admission was restarted.
Final code/pin rollback backup:
`/opt/cliproxyapi/backups/20261025T125139.732104233Z-admission-disconnect`.
Final ingress backup:
`/opt/cliproxyapi/backups/20261025T125136.970722882Z-admission-ingress-window`.

Full gates passed with 775 tests, 1 opt-in integration skip and 372 subtests;
the final affected admission suite passed 223 tests after adding the two live
message formats. Final compileall, Ruff and Bandit checks passed. Three retained
production dumps replayed against the final deployed classifier with
`LIVE_RETAINED_REPLAY_PASS 3 upstream_contacted=false`, all model-scoped with
no manual hold. Live source/pin matched, health was OK, and all four configured
lane holds were null. This establishes repository, projection, host-load and
retained-response replay acceptance. No post-load natural generation had
completed at the immediate readback; DeepSeek upstream recovery remains open.
Final strict doctor exited 0 with `DOCTOR_CONTRACT_OK`, integrity and syntax OK;
WorkBuddy reported `WAIT_UPSTREAM_WINDOW`, with no manual hold. Final guardrail
regressions passed 36 tests and 44 subtests.
Later post-load natural observation recorded six GPT-6.1 Sol and three GLM
Flash results with HTTP 200, `successful=true`, `capacity=false`. This disproves
a current global non-GLM outage; it does not establish DeepSeek recovery.

## Persisted-cache message shape follow-up (60703c77)

A CPA container restart (13:39Z, cleared in-memory unavailable marks) exposed a
fourth rendered shape of the cached WorkBuddy 6004: the B opening with the A
scope clause and the parsed reset time (`上游模型级频率限制（code 6004）… // Upstream
model-level rate limit (code 6004) — other models on the same credential are
unaffected, resets at …`), with no raw dump. Both deployed branches missed it
and the failure classified lane-scoped (proven locally against the deployed
source: `quoted=None`, `capacity_scope="lane"`, `risk_signal=(None, None)`).

The repair unifies the matcher on the provider-code phrase plus a model-only
scope clause, covering all four observed renderings. Full gates passed; four
fixture variants (brace-free, raw-truncated x2, persisted-reset) classify
model-scoped with no manual hold. Deployed at 13:48:24Z with baseline
`e4d85aab...` -> candidate `60703c77...`; receipts `ADMISSION_IDLE_OK`,
`ADMISSION_INTEGRITY_OK`, `DEPLOY_OK`, `ADMISSION_WINDOW_PASS`,
`INGRESS_RESTORED`; backup
`/opt/cliproxyapi/backups/20261025T134824.517145911Z-admission-disconnect`.

Credential state after the restart: the in-memory account selection reverted
to the CN default (`workbuddy-eb624ce3`, 6004-limited, reset 2026-10-26
12:34:44 UTC+8) and the operator re-select of `workbuddy-intl-ca64800b`
(single 6003 at 11:04Z, never 6004-limited, clean after restart) was pending.
`workbuddy-intl-0faaff85` remains 6004-limited (reset 12:16:03 UTC+8). In
selected mode the plugin escalates a limited selected account to
`oauth_account_review_required` within one or two requests, so per-model
parking is mostly exercised on the first cached-limit response; verification
of the live model park resumes after the re-select.
