# BWG CPA provider route projection

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Source: local private `- 副本.env`; credentials are omitted from this record and were not printed.
- Slot 3 (`35.213.82.91:8003`, HTTP) was omitted from the first projection and added in a later apply after explicit authorization to use plaintext HTTP.
- Slot 3's API key is sent by CPA to that upstream over unencrypted HTTP. The exception is restricted in code and policy to this exact host and port; other providers remain HTTPS-only.
- A read-only slot 3 `/models` preflight returned HTTP 200 with 38 model IDs. The API key was sent over HTTP for this authorized catalog read; it was never printed or recorded.
- The current read-only slot 2 preflight returned HTTP 200 with seven IDs, including `gpt-6-astra` and `gpt-5.6-sol`; slot 3 returned HTTP 200 with 38 IDs, including `gpt-5.6-sol`. Neither catalog listed upstream `gpt-6-sol`.
- No provider generation requests were made. Catalog visibility is not inference acceptance.

## Current CPA client model IDs

| Environment slot | Provider base URL | CPA client model IDs |
| --- | --- | --- |
| 1 | `https://ai.input.im/v1` | `gpt-6-sol`, `gpt-6-astra` |
| 2 | `https://codex.ciii.club/v1` | `gpt-6-astra-cii` → upstream `gpt-6-astra`; `gpt-6-sol-cii` → upstream `gpt-5.6-sol` |
| 3 | `http://35.213.82.91:8003/v1` | `gpt-6-sol-91` → upstream `gpt-5.6-sol` |
| 4 | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-5.3`, `glm-5.3-flash`, `glm-5.3-flashx` |
| 5 | `https://api.deepseek.com` | `deepseek-flash`, `deepseek-v4-pro` |

`gpt-6-luna` remains on the ChatGPT Plus OAuth lane and is not a compatibility provider.
The provider-to-alias mapping and OAuth lane are maintained in
`scripts/remote/cpa_provider_routes.json`.
The slot 1 GPT-5.6 bare names and the previous GPT-5.6 `-91` aliases are no longer exposed.
The current Sol aliases use the cataloged upstream ID `gpt-5.6-sol`; OAuth and Codex API-key
exclusions are generated from the same manifest. The four retired CIII aliases stay excluded
so they cannot reappear through a fallback provider.
Although the slot 4 catalog preflight listed additional upstream IDs, only `glm-5.3`,
`glm-5.3-flash`, and `glm-5.3-flashx` are configured as CPA client model IDs; all other
BigModel IDs are omitted.

## Projection evidence

- Backup-first apply command: `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`.
- Remote rollback backup: `/root/cpa-guardrails-backup-20260923T125713.679042245Z`.
- Apply result: `GUARDRAILS_APPLIED`, `READY_STATUS=200`; all projected file hashes were emitted.
- A transient connection reset occurred while the CPA restarted; the apply completed, the model catalog returned, and the fresh checks below passed.
- Fresh strict doctor (`-Observe`): `POLICY_OK`, `semantic-policy=OK`, `DOCTOR_CONTRACT_OK`; CPA running; loopback binding, public Nginx random path, and expected HTTP route statuses passed.
- Fresh remote config readback for the first apply showed each then-projected provider base URL and aliases (slots 1, 2, 4, and 5) matching the route manifest.
- Fresh loopback catalog readback: HTTP 200, 12 model IDs. All four CIII aliases, four ai.input.im aliases, GLM, and DeepSeek aliases were present.
- Local and remote `cpa_provider_routes.json` SHA-256 matched: `5056dc15d19d6d12553e51ffeb509f22d42bcb9a40184b89165abf772edf0879`.
- The catalog and doctor establish host-loaded configuration and route discoverability. Provider generation, controlled live acceptance, and natural-use acceptance were not tested.

## OAuth lane mapping follow-up

- The route manifest now explicitly declares `gpt-6-luna` on `chatgpt-plus-oauth`; the compatibility routes remain `gpt-6-sol` and `gpt-6-astra` on `ai.input.im`, GLM on BigModel Coding Plan, and DeepSeek on its official API.
- Backup-first follow-up apply completed with `GUARDRAILS_APPLIED` and `READY_STATUS=200`.
- One transient connection reset occurred while CPA restarted; readiness returned to 200, and the fresh strict doctor passed.
- Follow-up rollback backup: `/root/cpa-guardrails-backup-20260923T132659.957910884Z`.
- Fresh strict doctor returned `POLICY_OK`, `semantic-policy=OK`, and `DOCTOR_CONTRACT_OK`.
- Fresh remote manifest readback matched the requested OAuth and provider mappings. Local and remote manifest SHA-256: `fb14b04b84588c3947cc10e5668682f9c2477e8346d175dc648352a88b1bf79f`.
- No provider generation requests were sent.

## Slot 3 HTTP bridge projection

- The route manifest includes environment slot 3 as `http://35.213.82.91:8003/v1`, guarded by the exact host, port, and `allow_insecure_http` policy. It maps four unique text model aliases: `gpt-5.4-mini`, `gpt-5.5-openai-compact`, `grok-4.5`, and `grok-chat-fast`.
- Duplicate aliases `codex-auto-review`, `gpt-5.5`, `gpt-5.6-sol`, and `gpt-5.6-terra` remain assigned to their existing providers. Image-model variants were not added to the bare-name route list.
- Backup-first apply completed with `GUARDRAILS_APPLIED`, `READY_STATUS=200`; rollback backup: `/root/cpa-guardrails-backup-20260923T134353.444780858Z`.
- A transient connection reset occurred during CPA restart; readiness recovered to 200 and the fresh strict doctor returned `POLICY_OK`, `semantic-policy=OK`, and `DOCTOR_CONTRACT_OK`.
- Remote config readback showed the slot 3 HTTP URL and four aliases. CPA `/v1/models` returned HTTP 200 with 16 IDs; all four slot 3 aliases were present.
- Local and remote route-manifest SHA-256 matched: `430cb17324a425203966d64bb2ecfc8b14e327d287d04d5cea4f7ba1909382c9`.
- These checks establish host-loaded configuration and catalog visibility. They do not establish successful generation through the HTTP bridge or the other providers.

## Repository verification

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`: passed before the slot 3 projection; 153 passed, 1 skipped, 180 subtests; Ruff, Bandit, and mypy passed.
- `git diff --check`: passed.

## Latest route revision: GPT-6 aliases and restricted GLM catalog

- Final client mapping: slot 1 bare `gpt-6-sol` / `gpt-6-astra`; slot 2 `gpt-6-astra-cii` → upstream `gpt-6-astra` and `gpt-6-sol-cii` → upstream `gpt-5.6-sol`; slot 3 `gpt-6-sol-91` → upstream `gpt-5.6-sol`; slot 4 only `glm-5.3`, `glm-5.3-flash`, `glm-5.3-flashx`; slot 5 `deepseek-flash`, `deepseek-v4-pro`; OAuth `gpt-6-luna`.
- Slot 2 and slot 3 catalog preflights returned HTTP 200. Slot 2 listed seven upstream IDs, including `gpt-6-astra` and `gpt-5.6-sol`; slot 3 listed 38, including `gpt-5.6-sol`. Neither exposed upstream `gpt-6-sol`, so the two Sol client aliases use the cataloged `gpt-5.6-sol` ID.
- Backup-first apply completed with `GUARDRAILS_APPLIED` and `READY_STATUS=200`; backup: `/root/cpa-guardrails-backup-20260923T143902.216406194Z`. A connection reset occurred during restart; local readiness recovered before completion.
- Fresh BWG doctor returned `POLICY_OK`, `semantic-policy=OK`, and `DOCTOR_CONTRACT_OK`. Config readback showed exactly the active slot aliases from the mapping above.
- Authenticated client `/v1/models` readback returned HTTP 200 with 12 IDs: `deepseek-flash`, `deepseek-v4-pro`, `glm-5.3`, `glm-5.3-flash`, `glm-5.3-flashx`, `gpt-5.6-luna`, `gpt-6-astra`, `gpt-6-astra-cii`, `gpt-6-luna`, `gpt-6-sol`, `gpt-6-sol-91`, `gpt-6-sol-cii`. The old CIII aliases and old GPT-5.6 Sol/Terra client aliases are absent.
- Local and remote `cpa_provider_routes.json` SHA-256 match: `6a2496769eec9ddb8ca33726c55a6d4ae4a9b5eaafe84b504c157e714dfd8909`.
- Final repository gate passed: 153 tests passed, 1 skipped, 180 subtests passed; Bandit, Ruff lint/format, and mypy passed. `git diff --check` passed.
- This proves repository validation, remote projection, and current catalog discoverability. No provider generation request was sent; inference acceptance and natural-use acceptance remain untested.

## 20260924 Terra bare-name addition

- Requested change: add bare client model `gpt-5.6-terra` in slot 3, backed by
  upstream `gpt-5.6-terra` at `http://35.213.82.91:8003/v1`.
- Local route manifest and remote readback SHA-256 both matched:
  `6d6a0e8c3614df3f3e71a4df7b29346fe5a155f7c840c637da948ffdb5f197a8`.
- Backup-first apply completed with `GUARDRAILS_APPLIED` and `READY_STATUS=200`;
  rollback backup: `/root/cpa-guardrails-backup-20260924T010751.443138373Z`.
- Fresh strict doctor returned `POLICY_OK`, `semantic-policy=OK`, and
  `DOCTOR_CONTRACT_OK`. Config readback contained `alias: gpt-5.6-terra` and
  authenticated client `/v1/models` returned 13 IDs including `gpt-5.6-terra`.
- One explicit `generation-all` matrix was run without retries. The new Terra
  route returned `status=200`, `finish=stop`, `latency_ms=13530`.
  Other matrix failures were reported separately as upstream/network conditions:
  ai.input.im Astra timed out, CIII aliases returned `502`, and GLM FlashX
  returned `429`; no failed route was retried.
- Repository verification after the change: focused `test_scripts.py` passed
  47 tests / 147 subtests; full gates passed 153 tests, 1 skipped, 181
  subtests, with Bandit, Ruff, and mypy clean; `git diff --check` passed.
- This establishes repository verification, filesystem projection, host-loaded
  Terra discoverability, and one controlled live success for Terra. It does not
  establish long-term provider availability or natural-use acceptance for the
  other routes.
