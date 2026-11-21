# 2026-11-21 BWG CPA Start Plan (`zcode-start-plan`) Integration Evidence

## 1. Goal & Scope

- Enable BWG CPA to invoke GLM's free **Start Plan** (`glm-5.3-flash` during weeknights after 23:00 CST and weekends/holidays) using the user's existing BigModel primary account that already holds a paid Coding Plan.
- Keep paid Coding Plan (`zhipu-plan` / `glm-5.3-flash`) and Start Plan (`zcode-start-plan` / `glm-5.3-flash-start`) isolated on separate slots, aliases, and admission lanes.

## 2. Architecture & Changes

- **Host sidecar (`zcode-start-plan.service`)**:
  - Binary: `/opt/zcode-start-plan/zcode-proxy` (`TriDefender/zcode-api` v4.9.1 with built-in Aliyun Captcha V3 solver).
  - Bound strictly to Docker bridge gateway `172.17.0.1:18082` (`plan: start-plan`, `provider: bigmodel`).
  - Credentials encrypted at `/opt/zcode-start-plan/auth/.zcode-proxy/credentials.json` via `ZCODE_PROXY_CREDENTIAL_SECRET` in `/etc/zcode-start-plan.env`.
- **CPA & Admission Integration (`main` commits `91983f3`, `26f37ac`)**:
  - Added slot 6 (`zcode-start-plan`, `http://172.17.0.1:18082/v1`, `allow_insecure_http: true`) exposing `glm-5.3-flash-start` (`optional_models` so daytime non-free hours do not fail `cpa-health.py`).
  - Added dedicated admission lane `zcode-start-plan` (`max_inflight: 1`, `requests_per_minute: 20`).
  - Atomically rotated `/opt/cliproxyapi/cpa-admission.py` and `/etc/vps-ssh-launcher/cpa-admission.sha256` (`53bb40c5b78a3d2279ef9238f0164a2380af17124ef7a483a7b685a1db9f3fc9`), then applied guardrails via `scripts/cpa_bwg_guardrails.ps1 -Apply`.
- **Firewall Baseline (`bwg` only)**:
  - Updated `scripts/lib/proxy_firewall_contract.ps1`, `scripts/firewall_baseline.ps1`, and `scripts/proxy_hardening_drift.ps1` to allow `iifname "br-*" ip daddr 172.17.0.1 tcp dport 18082 accept` so the `cliproxyapi` container on `cliproxyapi_default` can reach the host sidecar without exposing port `18082` to public interfaces.
  - Applied on BWG via `scripts/firewall_baseline.ps1 -Profile bwg -Apply` (backup: `/var/backups/firewall-baseline.GOHGof`).

## 3. Verification Receipts

- `cpa_bwg_guardrails.ps1 -Apply`: `GUARDRAILS_APPLIED` (`BACKUP_DIR=/root/cpa-guardrails-backup-20261121T080520.729565978Z`).
- `cpa_bwg_guardrails.ps1` (read-only doctor): `DOCTOR_CONTRACT_OK`, `MODEL_IDS` includes `glm-5.3-flash-start`, `MODEL_CONTEXT_DRIFT=none`.
- `firewall_baseline.ps1 -Profile bwg -Apply` & read-only check: `FIREWALL_BASELINE_APPLIED`, `firewall_baseline=applied`.
- Controlled live request to `http://127.0.0.1:8318/v1/responses` (`model: "glm-5.3-flash-start"`, `stream: true`):
  - `RESPONSES_STATUS: 200`, `SSE_LINES: 106`, `status: "completed"`, output text `START_PLAN_CPA_OK`.
  - `cpa-admission` journal: `lane=zcode-start-plan model=glm-5.3-flash-start status=200 successful=true protocol_result=completed usage_in=288 usage_out=42`.

## 4. Operational Notes & Rollback

- Upstream ZCode JWT in `/opt/zcode-start-plan/auth/.zcode-proxy/credentials.json` will eventually expire and require re-authentication when upstream rejects tokens.
- Rollback:
  - Firewall: restore `/var/backups/firewall-baseline.GOHGof` or revert `proxy_firewall_contract.ps1` and re-run `firewall_baseline.ps1 -Profile bwg -Apply`.
  - CPA/Admission: restore `/root/cpa-guardrails-backup-20261121T080520.729565978Z` and `/var/backups/cpa-admission-pre-start-plan/`.
  - Sidecar: `systemctl disable --now zcode-start-plan.service`.
