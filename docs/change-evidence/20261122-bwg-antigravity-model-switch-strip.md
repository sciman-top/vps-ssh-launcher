# BWG Antigravity 同会话切模型 `<model_switch>` 裸 429 根因修复（2026-11-22）

**目标**：根治 Codex Desktop (`0.171.0-alpha.13`) 在同一会话中切换 Antigravity 模型后，所有后续请求（含切回原模型）均在 ~550ms 返回 139B 裸 `429 RESOURCE_EXHAUSTED` 的故障。`zz` 未触碰；未向 OAuth 车道发送合成生成探针；未改动任何用户 OAuth 凭据或 `cli-proxy-api` 配置。

## 根因实证

1. **排除额度/TPM 耗尽**：
   - 8 个 Antigravity Pro 账号配额面板均显示充裕（GEMINI 5h 97%–100%、周额 91%–100%，ARGON 51%–100%，Claude 5h 全部可用）。
   - 同会话**不切模型**实测：`gemini-3.8-flash-high` 隔 8 秒连发两条 37K 全量 uncached 均 200，`gemini-4-hydrogen-xhigh` 连发两条均 200，`gemini-4-neon-xhigh` 同会话连发 14+ 条均 200。
2. **锁定真实触发源**：
   - 检查 5 份 429 error dump 请求体发现：Codex Desktop 每次在同会话切换模型时，都会在 `assistant` 之后往 `input[]` 永久追加一条 ~19.8 KB 的中置 `role: "developer"` 消息（`<model_switch>...</model_switch>`，重复整份 Codex 系统提示）。每多切一次模型，请求体精确叠加 +19.8 KB（`166 KB -> 186 KB -> 206 KB`）。
   - `CLIProxyAPI` 将中置 `developer` 消息合并进 `contents[]` 的 `role: "user"` 对话正文，触发 Google `daily-cloudcode-pa.googleapis.com` 入口跨模型、跨账号返回 139B 裸 `429 RESOURCE_EXHAUSTED`。

## 仓库改动

- **`c90edd1`** — `fix(cpa): strip mid-session <model_switch> developer notices on antigravity`
  - `scripts/remote/cpa-admission.py`：新增 `strip_antigravity_model_switch_items()` 并在 `_proxy_unlocked()` 中接入（位于 `lane_name` 解析之后、`body_length` / `body_spool` / `Content-Length` 重算之前），仅对 `lane == "antigravity-oauth"` 且 `/responses` 路径移除出现在 `assistant`/`function_call`/`reasoning` 之后的纯文本 `<model_switch>...</model_switch>` `developer` 项，保留首轮系统提示、对话历史和 `<collaboration_mode>`，并记录 `protocol_normalized model=... kind=antigravity_model_switch stripped=N`。
  - `scripts/remote/cpa-admission-integrity-pin.txt`：同步更新为 `3b2cad8c72800353f814aa52ee1f277f4651ec46ea3b737b962b15c8e86c5a2f`。
  - `tests/test_cpa_admission.py`：补充 3 组单元测试与本地代理集成测试（覆盖多条剥离、非 Antigravity 车道/非 `/responses` 路径/首轮项/非匹配项透传，以及上游 `Content-Length` 精确重算）。

## 远端投影与回滚备份（仅 `bwg`）

- 事务脚本：`outputs/deploy-admission-model-switch-20261122.ps1`
- 预演（`-DryRun`）：`STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK baseline=5162aa6bdc4ea7b200e8716f722f31a125cf53734d195ab20e6f3697474bfe34 candidate=3b2cad8c72800353f814aa52ee1f277f4651ec46ea3b737b962b15c8e86c5a2f config=77b5f465e0e9139405e190f868c38229e4f9f378f28d4aac58a075d9c1d19ff7 no_swap=true`
- 正式原子轮换：`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`ADMISSION_PIN_ROTATED`、`ADMISSION_INTEGRITY_OK`、`ADMISSION_MODEL_SWITCH_STRIP_LIVE_OK`、`DEPLOY_OK`
- 远端备份与回滚入口：`/opt/cliproxyapi/backups/20261122T082606.088604431Z-admission-model-switch-20261122`（回滚只需还原备份目录下 4 个文件并 `systemctl restart cpa-admission` + `cpa-admission-integrity-check`）

## 分层验证证据

| 层级 | 执行内容 | 结果 |
| --- | --- | --- |
| `repo_verified` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` | `CATALOG_CHECK=PASS`、`bandit` / `bandit-scripts`、`ruff check` / `ruff format --check`（178 files）、`mypy`（165 files, 0 issues）、`pytest` `1858 passed, 30 skipped, 896 subtests passed in 81.81s` |
| `filesystem_projected` + `host_loaded` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg` + `cpa-health.py status` | `DOCTOR_CONTRACT_OK`、`admission-integrity=OK`、`admission-health=OK`、`POLICY_OK`、`semantic-policy=OK`、17/17 `projection-drift MATCH`、`HEALTH_OK` |
| `live_accepted`（待用户自然复测） | Codex Desktop 同会话切换 Antigravity 模型 | 待用户在桌面端实际切模型复测（按风控原则未发 OAuth 合成探针） |
