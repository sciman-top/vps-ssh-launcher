# BWG CPA provider route projection

## Scope

- Target: BWG only. ZZ was not accessed or changed.
- Source: local private `- 副本.env`; credentials are omitted from this record and were not printed.
- The retired slot 3 (`35.213.82.91`, legacy HTTP) was not selected or transmitted.
- No provider generation requests were made. Catalog visibility is not inference acceptance.

## Bare-name mapping

| Environment slot | Provider base URL | CPA bare names |
| --- | --- | --- |
| 1 | `https://ai.input.im/v1` | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-sol`, `gpt-6-astra` |
| 2 | `https://codex.ciii.club/v1` | `codex-auto-review`, `gpt-5.5`, `gpt-5.6`, `gpt-reserve` |
| 4 | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-5.3-flash` |
| 5 | `https://api.deepseek.com` | `deepseek-flash` |

`gpt-6-luna` remains on the ChatGPT Plus OAuth lane and is not a compatibility provider.
The provider-to-alias mapping and OAuth lane are maintained in
`scripts/remote/cpa_provider_routes.json`.
The CIII aliases do not overlap the existing ai.input.im bare names. OAuth and Codex API-key
exclusions are generated from that same manifest.

## Projection evidence

- Backup-first apply command: `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`.
- Remote rollback backup: `/root/cpa-guardrails-backup-20260923T125713.679042245Z`.
- Apply result: `GUARDRAILS_APPLIED`, `READY_STATUS=200`; all projected file hashes were emitted.
- A transient connection reset occurred while the CPA restarted; the apply completed, the model catalog returned, and the fresh checks below passed.
- Fresh strict doctor (`-Observe`): `POLICY_OK`, `semantic-policy=OK`, `DOCTOR_CONTRACT_OK`; CPA running; loopback binding, public Nginx random path, and expected HTTP route statuses passed.
- Fresh remote config readback showed each provider base URL and its aliases matching the table above.
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

## Repository verification

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`: passed; 153 passed, 1 skipped, 180 subtests; Ruff, Bandit, and mypy passed.
- `git diff --check`: passed.
