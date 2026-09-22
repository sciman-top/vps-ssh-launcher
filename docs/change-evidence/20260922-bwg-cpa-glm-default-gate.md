# 2026-09-22 BWG CPA GLM 默认健康门

- 范围：仅 `bwg`。未触碰 `zz`、OAuth 凭据内容、公网随机路径或公网监听策略。
- 目标：把无人值守更新器的默认 `generation` 代表路由从 ChatGPT Plus OAuth Luna
  移到官方 GLM Coding Plan 的 `glm-5.3-flash`，降低 OAuth 账号在定时维护中的暴露；
  Luna、DeepSeek、Sol/Terra/Astra 仍保留在显式矩阵和人工低频检查中。
- 依据：OAuth 路由出现 Cockpit 本地 `turn-state` 长度 312 启发式信号，并在官方
  Desktop 出现 `Selected model is at capacity`；这不是封号或降智的官方证明，因而只
  调整自动化流量，不做账号轮换、换 IP 或重试放大。

## Repository verification

- 提交：`aed4847`（运行逻辑）和 `f043e3e`（README、源码注释一致性）。
- `scripts/run_gates.ps1`：`152 passed, 1 skipped, 178 subtests passed`；Bandit、
  Ruff、format、mypy 均通过。
- 受影响 focused test：`46 passed, 144 subtests passed`。

## Projection and host readback

- 使用现有 backup-first `cpa_bwg_guardrails.ps1 -Profile bwg -Apply`，结果为
  `GUARDRAILS_APPLIED`，备份目录：
  `/root/cpa-guardrails-backup-20260922T113613.069616761Z`。
- 应用窗口内出现一次 `curl: (52) Empty reply from server`；事务随后得到
  `READY_STATUS=200`，并完成目录与语法收口。
- 投影后的 `/opt/cliproxyapi/cpa-health.py` SHA-256：
  `4ab3d1afd3d5d582baa5c3571fa7a2b491b1b04ed45499ab044ab4c3545930a4`，与本仓
  当前文件一致。
- post-apply strict doctor：`DOCTOR_CONTRACT_OK`；CPA 保持 loopback，Nginx 保持
  单一随机路径，`request-retry=0`、`max-retry-credentials=1`、60 秒瞬态冷却和
  非阻塞探针锁均保持不变。
- OAuth 观察：`oauth_expired=false`、`oauth_days_left=6`、
  `oauth_refresh_failures_7d=0`、`oauth_monitor=OK`。

## Controlled live acceptance

- 仅执行一次默认 `python3 /opt/cliproxyapi/cpa-health.py generation`；返回
  `HEALTH_OK`，无重试。该默认门现在只发送 GLM 请求，未发送 Luna 生成请求。
- 这证明 `repo_verified -> filesystem_projected -> host_loaded ->
  controlled_live_accepted`；不证明账号不会被封禁、限流、降智，也不证明长期
  provider 质量或自然用户流量验收。

## Rollback

- VPS 回滚使用上述备份目录恢复本次 Apply 涉及的文件，再重启 CPA 并运行 strict
  doctor；不要删除或重放 `auth/` 目录。
- 仓库回滚只撤销本切片的提交（`git revert f043e3e aed4847`），随后按同一
  backup-first Apply 流程重新投影。Git 回滚不能替代 VPS 文件恢复。
