# BWG GLM rate limits and user-image preflight, 2026-10-24

## Incident and scope

The user reported GLM Flash retry-limit 429, followed by a content-type error
after selecting GLM 5.3 in the same chat. The user subsequently authorized the
recommended repair, projection and bounded verification. All existing OAuth
accounts and API-key channels are retained.

Fresh remote evidence, times in Asia/Shanghai:

- 23:32:40 and 23:33:58: BigModel returned HTTP 429, code `1302`, account rate
  limit. Admission classified these as capacity failures; they were upstream
  responses, not Nginx throttles or local queue rejections.
- 23:13:38: an earlier upstream 429 carried `1308`, five-hour usage limit, with
  the message reset time `2026-10-25 02:57:50`. That text has no explicit timezone
  and is not a guaranteed recovery deadline for subsequent `1302` errors.
- 23:35:24: `glm-5.3` returned HTTP 400, code `1210`, accepting only `text`.
  The request contained 26 historical images. The actual outbound request had
  25 tool images replaced by CPA's existing omission marker, but retained one
  user `image_url` at `messages[7].content[2]`.
- Deployed configuration already declared `glm-5.3` as `input-modalities:
  [text]`. Both GLM models share the existing Zhipu Coding Plan channel.

The referenced upstream checkout was read only, revision
`355fd10e76b82e03ba6625acc583b900286e4bf2`. Its
`internal/runtime/executor/helps/openai_compat_tool_results.go` handles tool
images and deliberately preserves ordinary user images. No upstream source,
image, provider credential, model alias or account channel was changed.

## Repair and repository proof

Implementation commit: `e58b6a2`.

- Admission derives text-only models from the existing sibling route manifest.
- Responses and Chat Completions user images, including history, are rejected
  before acquiring a lane lease. The HTTP 422 code is
  `user_image_input_unsupported`, with `upstream_contacted=false`,
  `task_mode=user_image_input`, and `route_capability=text_only`.
- There is no `Retry-After`; callers receive the matching admission reason
  header. The message explains the text-only-chat or image-capable-model
  options. User images are neither discarded nor rewritten.
- Existing tool-result normalization and byte-exact allowed-body forwarding
  are preserved. No rate-runtime change was justified: upstream 1302/1308 body
  and Retry-After forwarding already worked and have dedicated regression tests.

Verification receipts:

- Full gate build/test stages: `757 passed, 1 skipped, 369 subtests passed`;
  the real SSH integration test remained opt-in and skipped.
- The gate then stopped because `bandit` was absent. After provisioning declared
  development tools in the project virtualenv, the exact remaining stages
  passed: both Bandit scans, Ruff check/format, and mypy on 65 source files.
  Missing `types-pexpect` was likewise installed before re-running mypy.
- GLM 1302/1308 fixtures were refined to the observed string codes and UTF-8
  Chinese messages, then reverified: `4 passed`. Explicit Retry-After is retained;
  absent deadlines remain absent, and each visit invokes the stub only once.
- Focused admission/guardrail tests: `239 passed, 44 subtests passed`.
- Independent read-only review found no actionable defect. Candidate remote
  compile/manifest/image-boundary dry-run passed without production changes.

## Projection and active-process acceptance

The reviewed transaction acquired the maintenance lock and exclusive admission
traffic lock, asserted all lanes idle and no active GLM cooldown, backed up code
and pin, atomically replaced them, and restarted only `cpa-admission`.
It included automatic restore on failure. CPA itself was not restarted.

- Backup: `/opt/cliproxyapi/backups/20261024T155907Z-admission-glm-preflight`.
- Old admission SHA-256:
  `0d5e8e43d98aa921810b4b762a5e8f1d56c18788063abef6919963dbde1d8113`.
- New admission SHA-256 and integrity pin:
  `4be8252cea2136b85dd85de736cc0ac4e32d537898d394d1b2fa894f36858265`.
- Receipt: `ADMISSION_INTEGRITY_OK`, `DEPLOY_COMPLETE=PASS`.
- Configuration, Compose, route/admission configuration and existing credential
  JSON hashes were identical across deployment; lane hold reasons were preserved.
- CPA image remained official v8.0.41, digest
  `sha256:ccbbbfc149fd0d8ab5aede6740be9f8498fb2329eeccfe2f6bb6ee6fd8c748d7`.

Actual loopback controlled requests to the running admission:

| Request | Result | Request ID |
| --- | --- | --- |
| Responses, suffixed model, user image + 25 historical tool images + current text | 422, 3 ms, no Retry-After | `5ae82506c8f44dbbbfb5eea36a95c37a` |
| Chat Completions, user image | 422, 3 ms, no Retry-After | `a932e6a296654b24a1163068d30e8888` |

Both returned the expected structured rejection. Journal correlation found
`protocol_reject` and no `upstream_result` for either ID:
`JOURNAL_NO_UPSTREAM=PASS`, `HOST_LOADED_PREFLIGHT=PASS`.

Post-deployment strict doctor exited 0 with `DOCTOR_CONTRACT_OK`,
`admission-integrity=OK`, `model-input-modalities=OK`, all projection drift
entries `MATCH`, and health/policy/admission/Compose/Nginx checks OK. The local
receipt is `bwg-glm-doctor.txt` in this chat's artifact directory. The historical
model-substitution observation remains a warning rather than a strict failure;
this transaction did not alter provider routing or claim to resolve that metric.

## Acceptance limits and rollback

Repository verification, projection and active-process image-preflight behavior
are proven separately. Limit handling was verified against a local synthetic
provider. No generation probe was sent to the real rate-limited GLM channel;
natural GLM recovery and successful continuation of the user's chat remain
unproven. This change does not grant image capability or replenish quota.

For runtime rollback, acquire the maintenance lock and exclusive traffic lock,
drain active requests, restore `cpa-admission.py` and `cpa-admission.sha256` from
the named backup to `/opt/cliproxyapi/cpa-admission.py` and
`/etc/vps-ssh-launcher/cpa-admission.sha256`, run the integrity check, restart
only `cpa-admission`, and repeat health/strict doctor. Git revert alone does not
restore production files. No credential reset or cooldown clearing is required.
