# BWG CPA 风控与传输层加固投影验收（2026-11-20）

**目标**：基于 `bwg` 48h 自然流量/缓存重置实测与上游 `CLIProxyAPI` `v8.0.79`（`6faa8d20`）源码审查，落地 3 项确定性护栏加固并完成远端投影与受控验收，不触碰 `zz`，保留全部既有 OAuth 账号与 API key 渠道。

## 改动范围（提交 `dc87018`）

1. **`requests.tls-handshake-timeout-seconds: 15`**：在 `scripts/remote/cpa_policy.py` 与 `scripts/remote/cpa-guardrail-apply.sh` 中钉定 uTLS 握手超时（上游默认 `0`），防止跨海 TLS ClientHello 卡死占满 OAuth `max_inflight` 槽位长达 300s；握手成功后自动解除，不影响推理与长流。
2. **Antigravity Claude 模型 user 层指纹词替换**：在 `scripts/remote/cpa_provider_routes.json` 中将 `claude-opus-5-5-high` 与 `claude-sonnet-5-5-high` 补入 `antigravity_fingerprint_swap.models`，使其与同端点 Gemini 模型具备一致的 `requests.payload.replace` 保护。
3. **`api-keys.openai-compatibility` 连接池纳管**：在 `scripts/remote/cpa-guardrail-apply.sh` 中自动保留/补全 `connection-pool: {}`，并在 `scripts/remote/cpa_policy.py` 中强校验，修复 `opencode` 槽位重投影后退回 `connection-pool=None` 的漂移。
4. **维持 WorkBuddy RPM = 18**：复核 `docs/change-evidence/20261101-bwg-workbuddy-rpm-18-apply.md` 历史回退记录后保持 `18`，不重新收紧至 `12`。

## 验证层级与证据

- **`repo_verified`**（提交 `dc87018`）：
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` 全绿：`CATALOG_CHECK=PASS`、`bandit`、`ruff check`、`ruff format --check`、`mypy`（164 source files）、`pytest` `1760 passed, 31 skipped, 833 subtests passed`。
- **`filesystem_projected` + `host_loaded`**：
  - 投影命令：`pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply`
  - 结果：`GUARDRAILS_APPLIED`、`HEALTH_OK`、`READY_STATUS=200`、17 项投影文件全部 `PROJECTION_HASH_VERIFIED`。
  - 远端备份目录：`/root/cpa-guardrails-backup-20261119T161307.023106915Z`
  - 投影后 `config.yaml` SHA256：`2f16d48476266284c4aa89aa290bd038e8b33989f86533151b6aa1470251fe04`
  - strict doctor（`pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg`）：`DOCTOR_CONTRACT_OK`、`POLICY_OK`、`semantic-policy=OK`、`admission-health=OK`、`admission-integrity=OK`、17 项 `projection-drift` 全部 `MATCH`、`credential_ban_signals_7d=0`、`nginx-syntax=OK`。
  - 远端配置读回：`tls_handshake_timeout_seconds=15`；`ai.input.im` / `opencode` / `zhipu-plan` 的 `connection-pool` 均为 `{}`；`requests.payload.replace` 覆盖全部 6 个 Antigravity 模型（`claude-opus-5-5-high`、`claude-sonnet-5-5-high`、`gemini-3.8-flash-high`、`gemini-4-argon-high`、`gemini-4-hydrogen-xhigh`、`gemini-4-neon-xhigh`）。
- **`controlled_live`**：
  - 经 `127.0.0.1:8318`（admission → CPA `v8.0.79-gemini-raw` → 上游）对非 OAuth 车道 `zhipu-coding-plan/glm-5.3-flash` 发送单次受控请求：`ONE_SHOT_8318 status=200 finish=length usage_in=17 usage_out=16`。

## 回滚入口

- 远端配置与投影文件回滚：从 `/root/cpa-guardrails-backup-20261119T161307.023106915Z` 恢复对应文件，重启受影响服务后重跑 strict doctor；Git 回滚不能替代远端恢复。

## 边界说明

- 本次受控单发仅覆盖非 OAuth 车道（`zhipu-coding-plan`），未发起额外 OAuth 生成探针；长周期账号安全与各客户端自然流量表现仍需在后续自然流量与 doctor 窗口中持续观察。
