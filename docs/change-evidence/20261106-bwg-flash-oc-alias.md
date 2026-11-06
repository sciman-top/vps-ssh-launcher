# 2026-11-06 BWG CPA：`deepseek-v4.1-flash-oc` 单宿别名上线

## 触发与决策

同日 flash 6004 窗口事故（见
[bounded-park 台账](20261106-bwg-admission-6004-bounded-park.md)）暴露：cap=1
下裸名 `deepseek-v4.1-flash` 的 OpenCode priority:-1 腿不可达（24h
`provider=opencode` 请求 0）。用户评审后授权增设单宿公开别名作为确定性
调用方入口；`max-retry-credentials=1` 与裸名双宿声明均保持不变；`-sg` 名称
维持（上游模型身份，不改 realm 语义）；wb 车道序维持 INTL→CN 终态。

## 仓库变更（提交 `8aca65e`）

- `scripts/remote/cpa_provider_routes.json`：opencode provider 增第二条
  `{name: deepseek-v4.1-flash, alias: deepseek-v4.1-flash-oc,
  max-context-length: 1000000}`；`oauth_exclusions` 增 `deepseek-v4.1-flash-oc`
  墓碑（OAuth lane 永不抢注）。
- `tests/cpa_catalog_expectations.py`：`PROVIDER_MATRIX_TAIL` 按派生序插入
  `deepseek-v4.1-flash-oc`（bare 名维持显式豁免）。
- `tests/test_cpa_guardrails_script.py`：slot-2 models 精确断言夹具更新。
- `tests/script_validation_support.py`：探针响应 helper 增回显映射
  `flash-oc → deepseek-v4.1-flash`（aliased 通道上游按名回显，
  `expected_models[alias]={name}`，cpa-health.py:1135）。
- `docs/runbooks/cpa-gateway.md`：lane 清单补 sg（11-05 采纳时遗漏）+
  路由表记 `-oc` 增设。

## 门禁

- FULL `run_gates.ps1`：`1118 passed, 18 skipped, 579 subtests passed`
  （4:54）；bandit / ruff check / ruff format / mypy 全绿；`git diff --check`
  通过。首跑曾挂 9 项 health_doctor 夹具（假响应回显别名而非上游名），修复
  夹具后全绿，属清单"故意变化型夹具"范围。

## 生产事务

- `-Apply`：`GUARDRAILS_APPLIED`、`READY_STATUS=200`；备份
  `/root/cpa-guardrails-backup-20261106T014854.840347743Z`（keep_8 策略顺带
  清 1 个旧备份）。事务重启 CPA 容器与 `cpa-admission`（合并订阅/硬化车道
  均只读面，未触碰）。
- 严格 doctor：`DOCTOR_CONTRACT_OK`、exit=0、`MODEL_IDS_UNKNOWN=none`、
  `catalog_oauth_missing=none`、projection-drift 全 MATCH、
  `workbuddy posture=OPEN hold_reason=none`。

## 受控实战验收（8318 回环单发，不重试）

- `POST /v1/chat/completions model=deepseek-v4.1-flash-oc`：
  **`ACCEPTANCE_RESULT=PASS`（LIVE_ACCEPTED）**——`http=200`/0.89s、
  `finish_reason=stop`、content 正确、`model` 回显上游名
  `deepseek-v4.1-flash`（契约期望），usage 含
  `opencode.billing_source=go` 直接证明走 oc zen Go 通道。
- 单宿性：该请求不经 wb 车道任何凭据；wb 模型冷却（≤300s 有界 park）对
  该别名零影响（admission passthrough 路径）。

## 回滚

恢复上述备份目录的 manifest 投影面并按
[cpa-manual-rollback runbook](../runbooks/cpa-manual-rollback.md) 重启；
Git 侧 `git revert 8aca65e` 不能代替远端恢复。

## 边界

单发验收证明别名可用与路由归属，不证明 oc 上游配额持续性、质量或长期
可用性；`-oc` 与 wb 两 realm 的窗口互斥性由结构保证（不同提供方），未做
对照实测。目录矩阵新增 1 个探针目标（显式 matrix 模式才发请求，scheduled
门不变）。
