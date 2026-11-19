# BWG CPA gemini-4-neon-xhigh 模型注册、投影与实战受控验收（2026-11-19）

## 背景与根因定位

1. **为什么刚发布就在 CPA 出现**：
   - CPA（CLIProxyAPI）配置中集成了 Google 内部 Antigravity 渠道（`https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`），该上游接口在 Google 发布 `gemini-4-neon` 后自动动态返回了新模型，使得 CPA 容器直接在 `/v1/models` 暴露了 `gemini-4-neon-xhigh`。
2. **为什么调用会报 429 Too Many Requests**：
   - CPA 准入与路由层（`cpa-admission.json`、`cpa_provider_routes.json`）尚未静态注册 `gemini-4-neon-xhigh`，导致请求被回退至 `lane=passthrough`。
   - 更关键的是，该模型未包含在 `antigravity_fingerprint_swap`（指纹清洗/关键字替换映射）中。Agentx 客户端请求携带的提示词包含特定指纹，直穿 Google Antigravity 上游时直接触发了 Google WAF 内容审计拦截，上游在数百毫秒内伪装返回 `RESOURCE_EXHAUSTED` / 429 错误。
   - 客户端触发 6 次重试上限后报错：`exceeded retry limit, last status: 429 Too Many Requests`。
3. **伴生定时器报警根因**：
   - 上游动态暴露 `gemini-4-neon-xhigh` 后，本地 `cpa-health.py readiness` 在白名单比较时检测到未配置的非预期模型（`ids - allowed`），触发 `LOCAL_CONTRACT_FAILED`（exit 20），导致 `cliproxyapi-update.service` 自动定时巡检记录失败（`result=failed exit_code=1`）。

## 变更内容

- **模型路由与准入契约**：
  - [cpa_provider_routes.json](../../scripts/remote/cpa_provider_routes.json)：
    - 将 `gemini-4-neon-xhigh` 纳入 `antigravity_fingerprint_swap`；
    - 在 `oauth_routes` 的 `antigravity-oauth` 下注册 `name` 与 `alias`；
    - 在 `oauth_settings.antigravity` 中钉住 `1048576` 上下文；
    - 纳入 `antigravity_upstream_static_models` 与 `agentx_api_key_exclusions`。
  - [cpa-admission.json](../../scripts/remote/cpa-admission.json)：
    - 在 `antigravity-oauth` 车道模型列表中加入 `gemini-4-neon-xhigh`。
  - [agentx-model-context-overrides.json](../../config/agentx-model-context-overrides.json)：
    - 声明 `gemini-4-neon-xhigh` 拥有 `1048576`（1M）上下文窗口。
- **门禁与测试同步**：
  - [cpa_catalog_expectations.py](../../tests/cpa_catalog_expectations.py)：同步加入 `ANTIGRAVITY_OAUTH_ROUTE_ALIASES`。
  - [cpa-guardrail-apply.sh](../../scripts/remote/cpa-guardrail-apply.sh) 与 [cpa-guardrail-doctor.sh](../../scripts/remote/cpa-guardrail-doctor.sh)：在 `expected["antigravity-oauth"]` 补充该模型。
  - [cpa_cache_reset_probe.py](../../scripts/cpa_cache_reset_probe.py) 与 [test_cpa_cache_reset_probe.py](../../tests/test_cpa_cache_reset_probe.py)：归一化正则支持 `gemini-4-neon-xhigh`。
  - 文档更新：[agentx-model-catalog-change.md](../runbooks/agentx-model-catalog-change.md)、[cpa-gateway.md](../runbooks/cpa-gateway.md)。

## 验证与验收凭据

1. **本地门禁验证**：
   - `python scripts/cpa_catalog_check.py` -> `CATALOG_CHECK=PASS`。
   - `pwsh -File scripts/run_gates.ps1 -RunParallel` -> `1751 passed`，静态检查（ruff check/format/mypy/bandit）全绿。
2. **远端投影与配置部署**：
   - `pwsh -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -Apply`：
     - 远端备份路径：`/root/cpa-guardrails-backup-20261119T041642.892026101Z`。
     - 17 项配置文件与守护单元哈希全匹配部署。
3. **定时器心跳刷新**：
   - 触发 `systemctl start cliproxyapi-update.service`：
   - 心跳文件 `/var/lib/vps-ssh-launcher/maintenance-status/cpa-update.status` 成功刷新为 `result=success exit_code=0`。
4. **严格 Doctor 门禁**：
   - `pwsh -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg` -> `DOCTOR_CONTRACT_OK`：
     - `MODEL_IDS_UNKNOWN=none`
     - `MODEL_CONTEXT_DRIFT=none`
     - 17 项投影文件漂移为零（全部 `MATCH`）
     - 车道准入检查 `antigravity_lane=OK` 包含 `gemini-4-neon-xhigh`。
5. **单发流式实战受控验收**：
   - 前置门禁：`AG hold=None gen=0 inflight=0 pending=0`。
   - 准入与流式回执：
     - `HTTP_STATUS=200`
     - `GEMINI_4_NEON_XHIGH_SSE_EVENTS=2 TERMINAL=true TEXT='PONG'`
     - `GEMINI_4_NEON_XHIGH_SERVED_MODEL=gemini-4-neon`
     - 后置门禁：`AG hold=None gen=0 inflight=0 pending=0`。
   - 准入 Journal 审计：
     - `INFO lane_admit lane=antigravity-oauth model=gemini-4-neon-xhigh`
     - `INFO upstream_result lane=antigravity-oauth model=gemini-4-neon-xhigh status=200 capacity=false successful=true protocol_result=completed upstream_header_ms=2838 first_text_ms=2838`。

## 回滚

- 远端：在维护锁下从 `/root/cpa-guardrails-backup-20261119T041642.892026101Z` 恢复配置文件并重启 `cpa-admission.service`。
- 本地：`git revert` 提交并重新运行门禁与投影。

ACCEPTANCE_RESULT=PASS
