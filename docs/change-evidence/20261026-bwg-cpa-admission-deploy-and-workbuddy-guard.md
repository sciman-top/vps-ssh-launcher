# BWG admission 投影与 WorkBuddy 调度保护（2026-10-26）

## 目标与边界

保留 WorkBuddy 优先、`credits` 粘性账号选择与失败回退、OpenCode 裸名兜底，
补上 doctor 对 `scheduler_mode` 的漂移检查，并将已审查的 admission 源与完整性 pin
投影至 BWG。未改 CPA config、WorkBuddy 插件或 OAuth/API key 凭据；未发生成请求。

## 事务

- 新增 doctor 检查 `scheduler_mode=credits`；缺失、`off` 或其他值报告漂移。
- `tests/test_cpa_guardrails_script.py` 回归用例覆盖有效值、缺失和非法值。
- admission dry-run：`STAGED_SYNTAX_OK`；旧代码与 pin 均为
  `60703c774cf79feafa37e9026f959b209649d78bfc140f81d86cbf7d281561ee`；候选与仓库
  完整性 pin 均为 `d5625df7377f14d2de4f533691219f688d98c7a10b28f3e2a9beb08e30d16025`；
  四条 lane 的 `hold_reason` 均为空。`DRY_RUN_OK`，未改远端。
- 受控部署脚本先对两个 Nginx 请求入口启用临时维护响应（`Retry-After: 30`），
  等待 admission 排空，断言所有 lane `inflight=0/pending=0`，备份 admission 源与 pin，
  原子替换、重启 admission，核对 health 与 holds，再恢复入口。
- 部署回执：`ADMISSION_IDLE_OK`、`ADMISSION_INTEGRITY_OK`、`DEPLOY_OK`、
  `ADMISSION_WINDOW_PASS`、`INGRESS_RESTORED`。备份目录：
  `/opt/cliproxyapi/backups/20261026T122226.067402560Z-admission-disconnect`。

## 验收

- 离线受控测试：`tests/test_cpa_admission.py`，232 passed（89.20s）；
  WorkBuddy doctor 回归：37 passed、50 subtests passed。
- 远端读回：`cpa-admission.py` 与 `/etc/vps-ssh-launcher/cpa-admission.sha256`
  SHA-256 均为候选值；`ADMISSION_INTEGRITY_OK`；`cpa-admission.service=active`；
  CPA 容器仍 running、重启数 0。
- 最新只读 healthz：`status=ok`，四 lane 均无 hold。读回时 WorkBuddy `inflight=1`、
  `pending=0`（自然请求在途）；ChatGPT 有自然请求在途，故不声称所有 lane 空闲。
- strict doctor 的 WorkBuddy 配置门禁通过：`scheduler_mode=credits`、
  `account_max_concurrent=1`、CN/INTL 模型目录 pin 存在、后台任务策略通过。
- strict doctor 整体仍报告远端 `auto-update.sh` 与仓库源投影哈希不一致；这是此前存在的
  独立投影漂移，本事务未投影 updater，亦未运行会重启 CPA 的全量 `-Apply`。
- 不做 WorkBuddy OAuth 生成探针。线上完整模型回答、限流率改善与自然流量长期验收
  均未由本事务证明；以用户正常请求产生的流量观察这些行为。

## 回滚

在 BWG 维护锁下，将上述备份中的 `cpa-admission.py` 与
`cpa-admission.sha256` 原样恢复，运行
`/usr/local/libexec/cpa-admission-integrity-check`，重启 `cpa-admission`，等待
`/healthz` 200 并确认四条 lane 的 hold 与回滚前一致。不要用 Git 回滚替代远端恢复。
