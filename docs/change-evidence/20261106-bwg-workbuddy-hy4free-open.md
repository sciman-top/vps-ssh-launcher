# 2026-11-06 BWG CPA：WorkBuddy hy4free 免费模型上线与实战验收

## 触发与决策

WorkBuddy（CN 与 INTL 双端，客户端版本 5.9.4+）现已开放 `hy4free`（混元4 / Hy4，官方产品配置中定价为 `x0.00` credits，附带 `badge:Free now` 标识，免费使用）。
用户指令要求在 bwg VPS CPA 网关上开放该模型。

经查核：
- WorkBuddy 官方产品配置中配对关系为：`displayName="Hy4"`, `freeId="hy4free"`, `paidId="hy4std"`。
- CN 端（`copilot.tencent.com`）与 INTL 端（`codebuddy.ai`）均存在该模型，因此在 CPA 网关上作为跨 CN 和 INTL realm 共享池化模型开放。
- 上游模型 ID 为 `hy4free`，上下文窗口为 1,000,000 tokens（maxInputTokens 960k-1000k, maxOutputTokens 64k）。

## 变更面与落点

1. **路由真源（`scripts/remote/cpa_provider_routes.json`）**：
   - `oauth_settings.workbuddy` 增补 `{"name": "hy4free", "max-context-length": 1000000}`。
   - `plugin_routes` 中 CN 与 INTL 均纳入 `hy4free`。
   - `pooled_aliases` 增补 `hy4free`（双 realm 共享消费合法声明）。
2. **准入网关（`scripts/remote/cpa-admission.json`）**：
   - `workbuddy-oauth` 车道 models 增加 `hy4free`。
3. **门禁与预言机同步**：
   - `scripts/remote/cpa-guardrail-apply.sh` / `scripts/remote/cpa-guardrail-doctor.sh`：`workbuddy-oauth` 期望列表追加 `hy4free`。
   - `tests/cpa_catalog_expectations.py`：`ADMISSION_LANE_MODELS` 与 `WORKBUDDY_PLUGIN_MODELS`（cn/intl）同步追加 `hy4free`。
   - `tests/test_cpa_health_doctor.py`：目录门禁测试夹具同步追加 `hy4free`。
   - `tests/test_cpa_guardrails_script.py`：WorkBuddy 配置测试夹具同步追加 `hy4free`。
4. **远程配置变更脚本（`scripts/cpa_workbuddy_model_pins.ps1` 与单测）**：
   - 新增安全配置工具，持有维护锁与请求排空锁，通过原子正则匹配更新 `/opt/cliproxyapi/config.yaml` 的 `models_cn` 与 `models_intl`。
5. **文档记录**：
   - 更新 `docs/runbooks/cpa-gateway.md` 与 `docs/runbooks/cpa-workbuddy-plugin.md`。

## 生产事务与验证

1. **远程模型 Pin 写入**：
   - 执行 `scripts/cpa_workbuddy_model_pins.ps1 -Apply`：
     - 备份：`/root/cpa-workbuddy-model-pins-backup-20261106T053637.653661Z/config.yaml`
     - 更新：`models_cn: deepseek-v4.1-flash,hy4free`；`models_intl: deepseek-v4.1-flash,deepseek-v4.1-flash-sg,hy4free`
     - 回执：`WORKBUDDY_MODEL_PINS=APPLIED`、`SHA256_AFTER=c6852339…`。
2. **CPA 投影与重启（`scripts/cpa_bwg_guardrails.ps1 -Apply`）**：
   - 排空自然流量并获得锁：
     - 备份：`/root/cpa-guardrails-backup-20261106T055752.694845252Z`
     - 投影校验：`PROJECTION_HASH_VERIFIED` 全部匹配
     - 容器与准入服务健康重启：`READY_STATUS=200`、`GUARDRAILS_APPLIED`、`models=14`。
3. **严格模式 Doctor 复验（`scripts/cpa_bwg_guardrails.ps1`）**：
   - `client-model-catalog`：包含 `hy4free`，`MODEL_IDS_UNKNOWN=none`。
   - `client-model-context`：`hy4free:1000000`，`MODEL_CONTEXT_DRIFT=none`。
   - `workbuddy-config`：`workbuddy-models_cn=PINNED`、`workbuddy-models_intl=PINNED`、`workbuddy-config=OK`。
   - `lane-hold`：`lane-hold-workbuddy-oauth=OPEN`。
   - `DOCTOR_CONTRACT_OK`（exit=0）。
4. **受控实战单发验收（`outputs/run-workbuddy-hy4free-pong-20261106.ps1`）**：
   - 请求：`http://127.0.0.1:8318/v1/chat/completions`，`model="hy4free"`，流式请求返回 `PONG`。
   - 结果：`PRODUCTION_STATUS=200`，`PRODUCTION_SSE_EVENTS=113`，`TERMINAL=true`，`TEXT='PONG'`。
   - 回执：`PRODUCTION_HY4FREE_ACCEPTANCE=PASS`。
   - journal 证据：`lane=workbuddy-oauth model=hy4free status=200 capacity=false retry_after=absent waited_ms=3 successful=true protocol_result=completed route=chat`。
