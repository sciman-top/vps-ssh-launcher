# BWG CPA slot 1 input.codes BASE_URL migration

## Scope

- Target: BWG CPA only. ZZ was not accessed or changed.
- User directive: `ai.input.im API网关的BASE_URL修改为https://input.codes/v1` / `在bwg VPS CPA中修改`
- Action:
  - Local `%APPDATA%\vps-ssh-launcher\providers.env` `BASE_URL_1` updated to `https://input.codes/v1`. Existing API key preserved.
  - Manifest `scripts/remote/cpa_provider_routes.json` slot 1 `host` updated from `ai.input.im` to `input.codes` (`path` remains `/v1`).
  - `ai.input.im` added to `retired_hosts` so legacy provider entry is cleanly dropped upon projection apply.
  - Health checks and acceptance updated in `scripts/remote/cpa-health.py` and `scripts/remote/cpa-acceptance.py`.
  - Guardrail and policy tests updated in `tests/test_cpa_guardrails_script.py` and `tests/test_cpa_policy_runtime.py`.

## Preflight evidence

- Local probe: `https://input.codes/v1/models` returned HTTP 200 with 5 models (`gpt-6-astra`, `gpt-6.1-sol`, `gpt-image-2`, `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`).
- BWG host outbound probe: `https://input.codes/v1/models` via existing slot 1 API key returned HTTP 200 with 5 models.
- Credential check: Existing slot 1 credential is fully valid on `input.codes`. No credential rotation needed.

## Controlled verification

- Local test suite:
  - `pytest tests/test_cpa_guardrails_script.py` -> 42 passed
  - `pytest tests/test_cpa_policy_runtime.py` -> 22 passed
  - `pytest tests/test_cpa_health_doctor.py` -> 24 passed
  - `pytest tests/test_cpa_admission_risk_audit.py` -> 63 passed
- Remote apply (`scripts/cpa_bwg_guardrails.ps1 -Apply`):
  - Backup: `/root/cpa-guardrails-backup-20261102T043706.906478477Z`
  - Result: `GUARDRAILS_APPLIED`, `READY_STATUS=200`, `models=12`
  - All 13 projection hashes matched committed HEAD hashes.
- Remote doctor:
  - `pwsh -NoProfile -File scripts/cpa_bwg_guardrails.ps1 -Observe` -> `DOCTOR_CONTRACT_OBSERVE_OK`
  - `pwsh -NoProfile -File scripts/cpa_bwg_guardrails.ps1` -> `DOCTOR_CONTRACT_OK`, exit 0
  - Drift check: all projected files reported `MATCH`, zero drift.
- Model route live check:
  - `python3 /opt/cliproxyapi/cpa-health.py relay-soft` -> `HEALTH_OK`, exit 0.
  - Slot 1 (`input.codes`) model request succeeded and answered with expected contract.
  - Admission layer probe (`http://127.0.0.1:8318/v1/chat/completions`, model `gpt-6-astra`) -> HTTP 200, output `pong`, model `gpt-6-astra`.
  - Nginx gateway frontend probe (`https://127.0.0.1:8443/<random-path>/v1/chat/completions`, model `gpt-6-astra`) -> HTTP 200, output `pong`, model `gpt-6-astra`.

## Acceptance Boundary

- `repo_verified`: PASS (all unit tests, syntax, and policy validations passed).
- `filesystem_projected`: PASS (remote configuration updated, backup created).
- `host_loaded`: PASS (doctor passed with 0 drift, all hashes match Git HEAD).
- `controlled_live_accepted`: PASS (end-to-end controlled single-shot generation succeeded across CPA, admission proxy, and Nginx gateway).
- `natural_live_accepted`: PENDING (deferred to user's daily client sessions, e.g. Cockpit/ChatGPT Desktop natural traffic).
