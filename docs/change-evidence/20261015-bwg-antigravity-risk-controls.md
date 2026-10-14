# BWG Antigravity OAuth 隔离与策略修复

## 范围与决策

- 2026-10-15（Asia/Shanghai），用户授权连续修复，并确认使用已登录的
  Antigravity OAuth 通道。保留凭据、单模型 `gemini-3.8-flash-high`、现有
  admission 2/4/120s、零 CPA 重试、冷却、禁止自动换项目/预览模型与 credits 策略。
- 官方 API 替代路线未实施：未提供独立 API 凭据、项目与计费预算。
- Google FAQ 明确提示第三方访问的条款与账号风险。限流和一次成功均不构成防封号保证。
- 用户选择由主代理串行复核，接受没有独立 reviewer 复核的边界。

## 修复与最低证明

- 代码提交 `41d690d`：复用隔离事务，新增 `-QuarantineAntigravity` /
  `-RestoreAntigravity`；provider 级排除表及运行标记独立，恢复遇到漂移拒绝。
- 修复旧隔离事务对混合 Codex / Antigravity OAuth 拓扑的拒绝，以及合法 Codex
  API-key 路由被目录验证误判为未知的缺口；凭据仅检查拓扑，不修改或回放。
- 语义策略覆盖 Antigravity 排除表，拒绝无标记停用、退役模型意外开放及非法类型。
  `-Apply` 遇任一 provider 隔离标记拒绝执行；doctor 区分正常与已隔离状态。
- `pwsh -NoProfile -File scripts/run_gates.ps1`：574 passed / 1 skipped，
  280 subtests passed；build、Bandit、Ruff、mypy 通过。真实 SSH integration 默认跳过。
- 最终受影响测试（policy / guardrails / health-doctor / updater-apply）：
  74 passed、93 subtests passed；最终 Ruff 和受影响 mypy 通过。

## 投影与运行态

- 预检：三个 lane inflight/pending 均为 0、无活动冷却与 half-open；
  `PREFLIGHT_ADMISSION_IDLE=PASS`。这是投影前快照，不是原子维护隔离证明。
- 命令：`pwsh -NoProfile -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`。
- `GUARDRAILS_APPLIED`、`READY_STATUS=200`；备份
  `/root/cpa-guardrails-backup-20261014T161123.664205959Z`。
- CPA 仍为 v8.0.28，镜像与 admission 代码/配置/pin 未变；生产 config.yaml
  SHA256 保持 `07809c244fdb811b8fae3b94b9ed525a5526c51be6faf085e6c8e7fe997ae81f`。
- 新 policy SHA256：`20a29c66784ce2e1b2cd04b790a3aa4fa3d6990257f6d79d595bff3c458dc587`。
- 投影后严格 doctor：`DOCTOR_CONTRACT_OK`、`POLICY_OK`、projection MATCH、
  admission health/integrity OK、MODEL_IDS_UNKNOWN=none。重启期间的短暂连接失败
  后已就绪；本次没有启用生产隔离或恢复，也没有清除活动保护状态。
- 在 BWG 用已部署 policy 和合成标记验证：无标记漂移拒绝、两个 provider 同时
  隔离及分别恢复通过，生产配置未变，临时标记已清理。
  `CONTROLLED_FIXTURE_RESULT=PASS`，`google_generation_requests=0`。

## 边界与回滚

- 已证明 repo / filesystem / host 及受控合成配置行为；未证明 Google 生成质量、
  上游持续容量或防封号。`high` 请求返回 `-n` 的既有观察仍不能单独判定降智。
- 隔离入口会重启 CPA；执行前确认请求已结束。隔离停止生成路由，但保留凭据和
  后台刷新，不能视为 Google 风控状态重置。生产隔离/恢复未实战执行。
- Git 回滚只撤销本次切片；远端按上面的备份与
  [CPA 回滚入口](../runbooks/cpa-manual-rollback.md)恢复对应文件、复验策略/目录/服务。
  隔离恢复使用对应 provider 的 Restore 开关，配置漂移时先归因，不强行覆盖。
- 用户授权 Git 代理改用可用监听端口：本仓 `.git/config` 的
  `http.https://github.com.proxy=http://127.0.0.1:10808`；HTTPS 探针 200、fetch 成功。
  系统登记仍为旧 10810，未修改系统/全局代理。该本仓设置原本不存在，回滚为
  `git config --local --unset http.https://github.com.proxy`。
