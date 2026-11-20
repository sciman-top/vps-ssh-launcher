# BWG CPA `gpt-5.6-luna` 退役验收（2026-11-20）

**目标**：用户指令退役 ChatGPT Plus OAuth lane 的兼容别名 `gpt-5.6-luna`。该别名随
2026-10-02 恢复在役至今；退役后 lane 现役四别名 `gpt-6-luna` / `gpt-6.1-sol` /
`gpt-6.1-terra` / `gpt-6.2-sol`。

**改动范围**（提交 `e517e79`，17 文件；同窗并行会话未提交的
`cpa_natural_traffic_audit` 工作留在工作树、未入本提交）：

1. `scripts/remote/cpa_provider_routes.json`：`oauth_routes` chatgpt-plus-oauth 摘
   模型条目；`oauth_settings.codex` 摘 272000 上下文钉；`oauth_exclusions` 加
   `gpt-5.6-luna` 墓碑（`codex_api_key_exclusions` 侧原有墓碑不动）——双清单拦复活。
2. `scripts/remote/cpa-admission.json`：`chatgpt-oauth` lane models 摘名。
3. 预言机：`cpa-guardrail-apply.sh` / `cpa-guardrail-doctor.sh` 内嵌 lanes 表（被
   `cpa_catalog_check.py` 对账）；apply `CODEX_OAUTH_ROUTES_READY` 文案翻
   `gpt-5.6-luna=retired`；client-model-catalog 探针改 `has_retired_bare_luna`
   （retired=True 预期）；`tests/cpa_catalog_expectations.py`
   `OAUTH_ROUTE_ALIASES`/`ADMISSION_LANE_MODELS` 同步。
4. 四个 CHOICE_SITES（`cpa_recovery_workflow.ps1` / `cpa_reset_event_bridge.ps1` /
   `hg03_contract.py` / `cpa_stream_acceptance.py`）摘名（目录门 #4 禁消费脚本宣传
   退役名；#5 要求列全在役 CHOICE_LANE 别名，均满足）。
5. `config/codex-model-context-overrides.json` 摘条目（目录门双向强制）。
6. `tests/test_cpa_admission.py` thinking-suffix 用例摘行（lane 成员语义依赖）；
   failure-triage / natural-traffic-audit / cpa-status 夹具中的同名仅作合成日志/文档
   数据保留（lane 取自日志文本，不查 manifest）。
7. 文档：`cpa-gateway.md`（lane 现役清单、路由映射、期望别名集、隔离标记语义、探针
   示例）、`cpa-oauth-luna-slot.md`（live 名与 re-enroll 意图模型）、
   `cpa-stale-cooldown-recovery.md`（available_partial 示例）、
   `codex-desktop-gateway-attach.md`（picker 清单）、`codex-model-catalog-change.md`
   （272K 契约行）、`README.md`（lane 表）。

## 分层验证与证据

- **`repo_verified`**：`scripts/run_gates.ps1 -RunParallel` 全绿——
  `CATALOG_CHECK=PASS`（chatgpt-oauth 派生四别名）、静态腿 PASS、pytest
  **1762 passed / 30 skipped**、receipt `PASS (Full)`。过程记录：一次 gates 运行出现
  6→1 个瞬态失败（`test_cpa_natural_traffic_audit` 等），与同窗并行会话在树中的
  audit 工具半成品写入时间吻合，两次连续全绿复跑未复现。
- **`filesystem_projected` + `host_loaded`**（bwg，`-Apply -Profile bwg`）：
  - 首次 apply 预写拒绝 `REFUSE cpa_apply_requires_idle_lanes`（exit 75，网关在途
    流量，零落盘）；等待约 90s 流量窗后重试成功。
  - `GUARDRAILS_APPLIED`、`HEALTH_OK`、`READY_STATUS=200`、
    `BACKUP_DIR=/root/cpa-guardrails-backup-20261120T161315.977602001Z`、
    投影哈希逐项核验。
- **strict doctor**：`DOCTOR_CONTRACT_OK`、`admission-health=OK`、`semantic-policy=OK`、
  `MODEL_IDS_UNKNOWN=none`、`MODEL_CONTEXT_DRIFT=none`、`catalog_oauth_missing=none`、
  `cooldown_state=none`；lanes 表与 manifest 对账 MATCH。
- **第一手目录读回**（8317 `/v1/models` 带鉴权）：14 模型，`gpt-5.6-luna` **不在册**，
  lane 四别名与 zhipu/workbuddy 面完好，三个 oc 墓碑名不在册（前次退役保持）。

## 回滚入口

- 远端：从 `/root/cpa-guardrails-backup-20261120T161315.977602001Z` 恢复受控文件并
  按 runbook 重启 `cli-proxy-api` / `cpa-admission`。
- 恢复在役：从 git 历史（本提交父级）摘除双排除清单墓碑 + 恢复路由/settings/lane
  四面 + override 清单，走 `cpa-catalog-change-checklist.md` 全流程（用户指令前置）。

## 边界说明

- 仅操作 `bwg`；其余 lane、OAuth 账号、API key 渠道、admission bounds 均原样。
- 桌面侧：cockpit 供应商快照刷新后 `gpt-5.6-luna` 从受管目录消失（仓库 override 已
  同批摘除）；desktop picker 见 `codex-model-catalog-change.md` 四层链路。
- 客户端面历史注记：`cockpit-fq-access-mode-options.md` 中外部 cockpit 工具的
  `CODEX_PROVIDER_MODEL_SHELL_POOL` 常量仍含该名（identity 槽位），属外部工具自身
  维护面，不在本仓退役范围。
