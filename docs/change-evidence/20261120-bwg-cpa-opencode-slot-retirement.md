# BWG CPA OpenCode 槽位整槽退役验收（2026-11-20）

**目标**：用户于 2026-11-20 退订 OpenCode Go 订阅，并指令关闭相关模型。当日早间
该槽刚随并行批次（`24ac9b0` 前置并存路由 + `d33029f`/`04fd115`）完成 GLM oc 开名
与 CF1010 加固并上线，退订后上游即对 slot-2 全部模型（含 `deepseek-v4.1-flash-oc`）
回 403 `permission_error`（订阅门）。按 2026-11-11 先例（`2366cd0` + `20540ac`），
死 oc 腿的认证类 403 有把配额窗故障放大成整车道持久锁的风险，整槽退役。

**改动范围**（提交 `e758324`，13 文件）：

1. `scripts/remote/cpa_provider_routes.json`：删除 slot 2（opencode provider 整块，
   含 `x-opencode-session`/`user-agent: cpa-bwg-gateway/1.0` 钉头与
   `request-scoped-errors: [{403, match 1010, stop}]` 载体）；`opencode.ai` 入
   `retired_hosts`（20540ac 教训：不入册则 apply 以孤儿 provider 回滚
   POLICY_FAILED）；`deepseek-v4.1-flash-oc` / `glm-5.3-oc` / `glm-5.3-flash-oc`
   三个别名按现行墓碑铁律保留于 `oauth_exclusions`（防复用/抢注，readiness
   fail-closed 拦复活）。
2. `scripts/remote/cpa-guardrail-doctor.sh`：`deepseek-route-order` 段 manifest/live
   opencode 期望翻转 `expected=removed`，终行 `WORKBUDDY_ONLY`。
3. 派生面同步：`PROVIDER_MATRIX_TAIL` 摘三别名、`CPA_TEST_PROVIDER_ALIASES` 清空、
   acceptance 夹具摘 opencode 块、slot-2 字面量断言改为"无 slot-2 路由 + retired_hosts
   含 opencode.ai + 三墓碑在册"、桌面上下文 override 清单摘三条（目录门双向强制）。
4. `04fd115` 引入的 cf1010/rse 机制测试改用合成载体保留覆盖（policy 规则本为泛化）；
   transform 测试改断言不再重建 opencode provider；`test_cpa_catalog_check.py` 两处
   探针名换仍在役别名（`glm-5.3` / `glm-5.3-flash`）。
5. 文档：`cpa-workbuddy-plugin.md`（deepseek 车道回 wb 单腿）、`cpa-gateway.md`
   （槽位世代记录收口）、`codex-model-catalog-change.md`（上下文契约表摘 oc 名）。

## 分层验证与证据

- **`repo_verified`**（提交 `e758324`）：`scripts/run_gates.ps1 -RunParallel` 全绿——
  `CATALOG_CHECK=PASS`、静态腿（bandit/ruff/mypy）PASS、pytest **1764 passed / 30
  skipped**（4 workers, 68.8s）、receipt `PASS (Full)`。
- **`filesystem_projected` + `host_loaded`**（bwg，`-Apply -Profile bwg`）：
  - `GUARDRAILS_APPLIED`、`HEALTH_OK`、`READY_STATUS=200`（容器与 cpa-admission 各
    重启一次，ready 等待窗内 curl 瞬断属预期）；
  - `BACKUP_DIR=/root/cpa-guardrails-backup-20261120T153224.749312037Z`；
  - 17 项 `PROJECTION_HASH_VERIFIED`（含 `/opt/cliproxyapi/cpa_provider_routes.json`）。
- **strict doctor**（apply 后即时）：`DOCTOR_CONTRACT_OK`、`admission-health=OK`、
  `semantic-policy=OK`、`deepseek-manifest-opencode-models=removed`、
  `deepseek-live-opencode-models=removed`、`deepseek-provider-order=WORKBUDDY_ONLY`、
  `MODEL_IDS_UNKNOWN=none`、`MODEL_CONTEXT_DRIFT=none`、`catalog_oauth_missing=none`、
  `cooldown_state=none`。live `/v1/models` 目录与 manifest 一致，三个 oc 别名不再
  对外暴露（readiness 目录门 fail-closed 兜底）。

## 回滚入口

- 远端：从 `/root/cpa-guardrails-backup-20261120T153224.749312037Z` 恢复受控文件并按
  runbook 重启 `cli-proxy-api` / `cpa-admission`；Git 回滚不能替代远端恢复。
- 续订重放路径：续订后从 git 历史（本提交的父提交 `b537882` 状态）恢复 slot-2 manifest
  块 + 摘 `opencode.ai` 出 `retired_hosts` + 恢复派生面，走开名 checklist 全流程；
  `providers.env` 的 `BASE_URL_2`/`API_KEY_2` 映射历次退役均保留未动，槽号不复用。

## 边界说明

- 仅操作 `bwg`，未触碰 `zz`；其余 OAuth 账号与 API key 渠道、admission lanes、
  WorkBuddy RPM 均原样。deepseek 车道回 wb 单腿：wb 全候选耗尽时按容量类阶梯窗
  自探自愈，无认证类持久锁。
- 客户端面（desktop cockpit 目录）待用户操作：供应商管理器移除三个 oc 模型并刷新
  opencode 供应商快照 → 切 lane 重建受管目录 → 校验 → 重启 desktop（见
  `codex-model-catalog-change.md` 删除模型节）。仓库侧 override 清单已同批摘除。
