# 2026-10-23 BWG admission lock diagnostics and DeepSeek/GLM stream audit

## Scope

Authorized BWG-only maintenance. No `zz`, credential rotation, provider key
edit, retry-policy change, IP/UA change, OAuth account probe, or concurrency
increase occurred. The admission change was deployed during the authorized
maintenance window. DeepSeek/GLM root-cause work in this document is diagnostic
plus a disposable candidate check; neither CPA nor WorkBuddy was changed for
those incidents.

## Admission `maintenance_in_progress` diagnostic

Codex received `503 maintenance_in_progress` from the public Responses route.
The old admission guard used one refusal path for maintenance holding the
traffic lock and for an unexpectedly open or failed `flock`, so the historical
trigger could not be distinguished after the fact.

Repository repair:

- `scripts/remote/cpa-admission.py` now records redacted
  `request_guard_refused` outcomes:
  `stage=open reason=lock_unavailable errno=N`,
  `stage=flock reason=maintenance_busy errno=N` for `EAGAIN/EACCES`, and
  `stage=flock reason=lock_error errno=N` otherwise.
- Exception text and filesystem paths are not logged.
- `scripts/remote/cpa-admission-integrity-pin.txt` moved from
  `8a8e1baef23008809be84c29af355221ac36ef8026a60f36fc219c712928a557` to
  `f4f984f1076830a32c75994dc7d8b5bf388458a4ff69beb98fa2ac78e41aecb7`.
- Focused admission tests: 188 passed. Full local gates: 734 passed, 1 skipped,
  347 subtests passed; compileall, Bandit, Ruff, Ruff format, and mypy passed.
- Commit: `ee74fbf759dc906526c74146e0c252e482e4c0c8`.

Deployment:

- A first safe `-Apply` refused pin rotation with no files changed, as designed.
- A narrow reviewed transaction replaced only
  `/opt/cliproxyapi/cpa-admission.py` and
  `/etc/vps-ssh-launcher/cpa-admission.sha256`.
- Backup:
  `/root/cpa-admission-logging-backup-20261023T114626.902429665Z`.
- The transaction asserted the old code and pin, held the traffic lock,
  syntax-checked and integrity-checked the staged file, atomically installed
  it, restarted only `cpa-admission`, and retried health. One transient
  healthz connection failure occurred during startup; the transaction then
  printed `DEPLOY_OK`.
- Host module simulation exercised all three refusal classes and found no
  private exception text or temporary path in logs.
- Fresh strict doctor at `2026-10-23T12:40:00Z`: `DOCTOR_CONTRACT_OK`; all
  projection drift entries `MATCH`; deployed admission hash
  `f4f984f1076830a32c75994dc7d8b5bf388458a4ff69beb98fa2ac78e41aecb7`.

Evidence boundary: this is `host_loaded` plus an isolated host module
simulation. It does not prove the original 503 trigger, which old code did not
record, and it is not natural failure classification until a future real refusal
emits one of the new reasons. Rollback restores both files from the named
backup and restarts `cpa-admission`; Git rollback is not remote rollback.

## GLM `apply_patch` incident

At local `2026-10-23 20:25:11` / UTC `12:25:11`, GLM
`glm-5.3-flash` returned CPA's sanitized error
`Invalid apply_patch tool arguments received from upstream.` Admission recorded
the stream end as HTTP 200 with `successful=false`; Nginx recorded a full
HTTP 200 response of about 26.776 s and 37,900 bytes. CPA emitted 502 after
26.854 s. The later 20:25:38 `503 auth_unavailable` was credential cooldown
after CPA classified the validation failure. The half-open probe recovered the
GLM lane, and a request at 20:26:37 succeeded.

CLIProxyAPI v8.0.40 deliberately replaces invalid upstream apply-patch
arguments with this message and fails the stream; it does not expose the raw
arguments. Therefore this was not a network disconnect: the CPA/OpenAI
compatible bridge received or derived an invalid `apply_patch` argument shape.
The model catalog exposes `apply_patch_tool_type: null` for GLM, but the client
still supplied an `apply_patch` tool request, so catalog absence alone does not
prevent the bridge from validating the model's attempted call.

No production change was made. Removing `apply_patch` globally or even only for
GLM is a functional workaround that may remove Codex editing capability, not a
proven root-cause repair. The next useful evidence is either upstream/model
telemetry for the invalid argument shape or an explicitly accepted controlled
experiment that defines the intended GLM fallback behavior.

## DeepSeek stream termination incident

At local `2026-10-23 20:26:57` / UTC `12:26:57`, admission request
`5f1722151a8a4d05951886901acad2bf` used `workbuddy-oauth /
deepseek-v4.1-flash`. Admission recorded HTTP 200 `successful=false`; Nginx
recorded HTTP 200, upstream HTTP 200, 7.927 s, and 114,763 bytes. The client
saw `upstream stream closed before a terminal event (last event:
response.output_item.done)`. Whether the stream was strictly reasoning-only or
also contained visible payload cannot be determined from redacted
admission/Nginx telemetry.

This is not a rollback of the prior truncation guard. WorkBuddy 0.9.65 contains
`sawFinish`, `sawTerminator`, `truncatedStreamError`, `chunkCarriesFinish`, and
`stream_termination_test.go`. Current plugin hash:
`d52a0a3b9b4aa5fc146f60330c7e9295fe9339142cc64eb218999f77f48bb6b8`. The prior
0.9.64-local guard hash remains
`86441f5d931a6ddeecd3875efcf115f53d7cc29fb1827cbaefcd067a761f15aa`, with the
0.9.65 backup under
`/opt/cliproxyapi/backups/20261023T041434.262710010Z-plugin-0964trunc-to-0965/workbuddy.so`.

The remaining identified host-side defect is in CPA v8.0.40's OpenAI-compatible
stream conversion: it tracks `finish_reason`, but a reasoning-only compatible
stream ending at `[DONE]` without a chat finish can still translate without the
Responses `response.completed` terminal. Upstream
[PR #6784](https://github.com/router-for-me/CLIProxyAPI/pull/6784),
`fix(openai): terminate reasoning-only compatible streams`, addresses this.
At the current audit it remained OPEN and clean/mergeable against `dev`; the
latest GitHub release was still `v8.0.40`, whose production image is pinned by
`sha256:f7a424d78498dc413808414a3143c8b23cddf78eecdea69979cf88adf3bfa9ef`.

Disposable candidate check:

- Base tag/source commit: `v8.0.40` / `d946c4fba6fc9a5cfbca1f3d85184d62ad2fb683`.
- PR head commit: `d11a6c6990c5d49c8eef363ec3ba6109bcbe180d`.
- `git apply --check` succeeded for PR #6784 against the v8.0.40 tree.
- After applying it in a disposable tree:
  `go test ./internal/runtime/executor/helps -run OpenAICompat -count=1`
  passed, and
  `go test ./internal/runtime/executor -run OpenAICompat -count=1`
  passed.

No CPA deployment followed. Production doctor/updater policy requires the
official, mature release image; building and pinning a local patched image is a
contract change that was not separately authorized or represented by the
candidate. A reasoning-only stream must not be rewritten into a synthetic
`response.completed` merely to silence the client unless the upstream fix's
explicit no-visible-payload guard is deployed.

Isolated Docker differential simulation:

- Base official image:
  `eceasy/cli-proxy-api:v8.0.40@sha256:f7a424d78498dc413808414a3143c8b23cddf78eecdea69979cf88adf3bfa9ef`.
- Applied patch: upstream PR #6784 diff
  `sha256:44ab2b314eae0df6d634b74f385518bdd6319a9a7353c3c0531061c2a409345f`
  at head `d11a6c6990c5d49c8eef363ec3ba6109bcbe180d`.
- The simulation used only loopback port `18317` and a Docker-bridge reasoning
  fixture on port `18318`; it did not use production config, auth, plugins,
  admission, the public port, or the production container. Before/after checks
  asserted that the production container identity and start time did not
  change.
- Baseline official v8.0.40 reproduced the defect:
  `BASELINE_DEFECT_REPRO=PASS`, HTTP 200, last event
  `response.output_item.done`, sanitized error
  `upstream stream closed before a terminal event`, and no
  `response.completed`.
- PR #6784 applied as-is failed the same way:
  `CANDIDATE_TERMINAL_FIX=FAIL`.
- A minimal v8.0.40 adapter was then added only inside the simulation: it
  translated the PR synthetic finish and then also translated the original
  `[DONE]`. Candidate v2 still failed identically:
  `CANDIDATE_TERMINAL_FIX=FAIL`. Retained candidate image:
  `bwg-local/cpa:v8.0.40-pr6784-sim-v2`
  (`sha256:7ef169bdbb3b4da8f6885ff4edd193908ffa193a619636f3228b2447d0236363`).
- Obsolete simulation image tags were removed after the test; only candidate
  v2 was retained. No local image was deployed.
- After simulation, a fresh strict doctor at `2026-10-23T14:02:51Z` returned
  `DOCTOR_CONTRACT_OK`; production remained the official fixed-digest v8.0.40
  image, the admission hash remained
  `f4f984f1076830a32c75994dc7d8b5bf388458a4ff69beb98fa2ac78e41aecb7`, and all
  projection drift entries were `MATCH`.

Conclusion: PR #6784 is not sufficient for production v8.0.40, and the first
local adapter did not fix the terminal conversion either. Per systematic
debugging, no further speculative candidate is accepted. The controlled result
is `CANDIDATE_TERMINAL_FIX=FAIL`; it does not approve a production candidate.
The next valid evidence requires an upstream correction or a separately
reviewed and tested patch that demonstrates a terminal event on a
reasoning-only stream without synthesizing success when visible payload was
not delivered.

Post-audit baseline check: one zero-retry controlled `/v1/responses` request to
DeepSeek at UTC `12:49:08` (admission request
`3df38a885bfa46afa2c4bd3ddec31257`) returned HTTP 200 in 1.077 s, with visible
text deltas and `response.completed`/`completed`; no error or incomplete
terminal appeared. Admission correlated it as `successful=true`. This proves
only that the simple production path was healthy at that instant; it neither
reproduces the rare termination failure nor proves PR #6784 deployed.

## Current classification and next evidence

- Admission repair: `repo_verified -> filesystem_projected -> host_loaded`;
  future natural refusal classification remains open.
- DeepSeek: controlled baseline reproduces the CPA v8.0.40 terminal-stream
  defect. PR #6784 and the v8.0.40 `[DONE]` adapter attempt both failed the
  controlled candidate check. Production remains unchanged; there is no
  accepted production repair, and live acceptance remains open.
- GLM: text-only tool-result normalization and incompatible freeform
  `apply_patch` declaration filtering are projected and controlled replay
  accepted; natural live acceptance remains open.
- WorkBuddy 0.9.65 and CPA v8.0.40 remain on their reviewed deployed pins.
- Do not infer a generic VPS failure from these incidents: both matched CPA
  protocol/lane behavior, and strict gateway/admission contract checks passed.

Upstream watch item: do not accept PR #6784 as currently written. Watch for an
upstream correction or reviewed patch that passes the retained reasoning-only
differential simulation; then repeat CPA canary policy, image digest pinning,
plugin registration, admission drain, and one bounded DeepSeek Responses check
before claiming repair.

## Later same-day GLM protocol repair

Separate GLM root causes were confirmed and repaired; this section supersedes
the earlier "no GLM workaround" classification only.

1. `glm-5.3` HTTP 400, upstream code `1210` (`14b00c32f1924be7b052db2c2113e2d1`,
   `22:31:01+08:00`): the current user payload was text-only, but serialized
   history at `input[320].output[0].type=input_image` was translated to
   `messages[213].content[0].type=image_url`. The Zhipu coding-plan endpoint
   rejected the image. CPA v8.0.40's native model capability
   `input-modalities: [text]` preserves tool-result text and replaces the
   unsupported image with `[image omitted: unsupported by upstream]`.
2. `glm-5.3-flash` terminal 502 (`d8e1fa539785476db3079f7a0f797320`,
   `22:53:05+08:00`): upstream returned HTTP 200 but produced malformed
   `apply_patch` arguments. CPA's protocol guard refused them before delivery;
   Codex surfaced this as stream disconnected. CPA's documented payload filter
   removes the incompatible freeform declaration instead of weakening that
   guard.

Repository repair commit: `8649a5e0cf9516b412e24fe809c5cfd5914d2961`. Full
gates passed at `734 passed / 1 skipped / 353 subtests`; bandit, ruff
check/format, and mypy also passed. CPA v8.0.40 upstream focused tests for
`input-modalities` normalization passed. Rollback is `git revert` plus restore
the guardrail backup below.

Projection and host load:

- Apply transaction: `GUARDRAILS_APPLIED`; backup
  `/root/cpa-guardrails-backup-20261023T152150.677229115Z`.
- Config hash: before `8d254f0ac635cf05e3fca4414b0e724b83a1d251a916373f251378b18e6e1c1f`,
  after `263d5e4666ac80a4527ced50f43c217c0d4c9dd9d73b524ebc917dbcfbf6c803`.
- The apply recreated the CPA/admission data plane at `15:21:53Z`; the image
  remained official
  `v8.0.40@sha256:f7a424d78498dc413808414a3143c8b23cddf78eecdea69979cf88adf3bfa9ef`,
  admission hash remained
  `f4f984f1076830a32c75994dc7d8b5bf388458a4ff69beb98fa2ac78e41aecb7`.
- Fresh strict doctor at `15:24:49Z`: `DOCTOR_CONTRACT_OK`, deployed
  `glm-5.3` showed `input-modalities`, `model-input-modalities=OK`, and all
  projection drift entries were `MATCH`.

Zero-retry admission-plane controlled replay at `15:27:28Z`:

| Probe | Model | Result |
| --- | --- | --- |
| Tiny historical image-bearing tool result | `glm-5.3` | HTTP 200, `response.completed`, admission request `9313a0041ec24b70970bede22775a9da`, `successful=true`; no code `1210` dump |
| Freeform `apply_patch` declaration | `glm-5.3-flash` | HTTP 200, `response.completed`, admission request `be72ea7b4ccc4a0f8e921ceadc7eee61`, `successful=true`; no invalid-arguments 502 |

These receipts establish `controlled_live_replay` only. They do not prove
natural Codex Desktop acceptance, long-term upstream stability, or the
independent DeepSeek terminal-stream fix. The freeform filter is a capability
reduction for GLM: it cannot emit a direct freeform `apply_patch` call, but
CPA's fail-closed protocol guard is retained.
