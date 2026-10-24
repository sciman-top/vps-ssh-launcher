# BWG CPA model context windows (2026-10-24)

## Scope

The user authorized applying the recommended context-window settings to the
models exposed by BWG CPA. No OAuth credential or API key was created,
deleted, rotated, disabled, or exported. No generation probe was sent.

## Applied values

| Public model | CPA channel | `max-context-length` |
| --- | --- | ---: |
| `gpt-6-astra` | `ai.input.im` compatibility provider | 272000 |
| `gpt-6.1-sol-input` | `ai.input.im` compatibility provider | 500000 |
| `gpt-5.6-terra` | `http-bridge-8003` compatibility provider | 272000 |
| `glm-5.3` | `zhipu-plan` compatibility provider | 1000000 |
| `glm-5.3-flash` | `zhipu-plan` compatibility provider | 1000000 |
| `gpt-6-luna` | ChatGPT OAuth (`codex`) | 272000 |
| `gpt-5.6-luna` | ChatGPT OAuth (`codex`) | 272000 |
| `gpt-6.1-sol` | ChatGPT OAuth (`codex`) | 500000 |
| `gemini-3.8-flash-high` | Antigravity OAuth | 1048576 |
| `deepseek-v4.1-flash` | WorkBuddy plugin | 1000000 |

`gpt-6.1-sol` and `gpt-6.1-sol-input` are distinct public IDs. The first is the
ChatGPT OAuth model; the second is the `ai.input.im` compatibility-provider
alias for the same upstream model name. Both are intentionally retained.

The values use the supported source precision:

- Current Codex client metadata and CPA's bundled client template use
  `500000` for GPT-6.1 Sol's context window. CPA applies a
  `max-context-length` override to both `context_window` and
  `max_context_window`, so `512000` or `524288` would advertise a larger
  window than that source supports.
- The ChatGPT OAuth/Codex catalog uses `272000` for GPT-6 Astra, GPT-5.6
  Terra, GPT-6 Luna, and GPT-5.6 Luna. The public API model pages advertise
  larger API-key windows, so their values are not used as ChatGPT sign-in
  overrides.
- DeepSeek's official model page lists DeepSeek-V4.1-Flash with a `1M`
  context window: https://api-docs.deepseek.com/quick_start/pricing
- The Zhipu and WorkBuddy limits are decimal `1000000`; the Gemini OAuth
  window is binary `1048576`.

## Implementation

Commit `d50b896` pins the values in
`scripts/remote/cpa_provider_routes.json` and extends the apply and doctor
workflow. Compatibility-provider values are model-entry fields; ChatGPT,
Antigravity, and WorkBuddy values are managed `oauth.settings` entries.
`-Apply` replaces only those three managed channel lists and preserves other
OAuth settings that may already exist.

Commit `f988113` fixes the doctor checker to feed the client catalog through
Python's standard input while obtaining the checker program with `python3 -c`.
The first strict-doctor attempt exposed the original heredoc/stdin collision;
the corrected checker was applied and passed.

## Transaction and verification

- Full gate before deployment: `739 passed, 1 skipped` and
  `369 subtests passed`; Bandit, Ruff, formatting, and mypy passed.
- First apply: `READY_STATUS=200`, `GUARDRAILS_APPLIED`, backup
  `/root/cpa-guardrails-backup-20261024T081122.427404348Z`.
- Second apply after the doctor fix: `READY_STATUS=200`,
  `GUARDRAILS_APPLIED`, backup
  `/root/cpa-guardrails-backup-20261024T081723.122503900Z`.
- Final deployment hash:
  `93af893eca6cf1943b6c7b6150ed23db635714d401fa9b8bb2e1fd310751329d`
  for `/opt/cliproxyapi/config.yaml`.
- Final strict doctor: `DOCTOR_CONTRACT_OK`, all projected hashes `MATCH`,
  `MODEL_IDS_UNKNOWN=none`, `MODEL_CONTEXT_CONFIG=OK`, 
  `MODEL_CONTEXT_DRIFT=none`, `MODEL_CONTEXT_MISSING=none`, and
  `catalog_oauth_missing=none`.

The authenticated client catalog read back:

```text
MODEL_IDS=deepseek-v4.1-flash,gemini-3.8-flash-high,glm-5.3,glm-5.3-flash,gpt-5.6-luna,gpt-5.6-terra,gpt-6-astra,gpt-6-luna,gpt-6.1-sol,gpt-6.1-sol-input
MODEL_CONTEXT_CATALOG=deepseek-v4.1-flash:1000000,gemini-3.8-flash-high:1048576,glm-5.3:1000000,glm-5.3-flash:1000000,gpt-5.6-luna:272000,gpt-5.6-terra:272000,gpt-6-astra:272000,gpt-6-luna:272000,gpt-6.1-sol:500000,gpt-6.1-sol-input:500000
MODEL_CONTEXT_DRIFT=none
MODEL_CONTEXT_MISSING=none
MODEL_CONTEXT_UNREADABLE=none
```

This proves configuration and client-facing catalog metadata only. It does not
prove that a request containing one million tokens succeeds at the upstream
provider. Dynamic OAuth availability can still remove a model from the live
catalog when the upstream account does not currently advertise it.

## Rollback

The pre-change BWG configuration is
`/root/cpa-guardrails-backup-20261024T081122.427404348Z/config.yaml`. For a
remote rollback, compare the current config with that backup, restore only the
intended file, then restart `cli-proxy-api` and re-read the catalog. A Git
rollback of `d50b896` and `f988113` must be re-projected with
`cpa_bwg_guardrails.ps1 -Apply`; Git rollback alone does not restore BWG state.
