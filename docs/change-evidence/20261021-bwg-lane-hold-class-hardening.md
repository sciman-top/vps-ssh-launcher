# BWG lane-hold 类根治：updater 差分检测 + canary 契约 + monthly 检测（2026-10-21）

## 背景与根因

- 同日两起 chatgpt-oauth `unverified_request` durable hold（10-20 14003-ladder 事务重启
  admission、10-21 v8.0.38 canary 重建容器）同一机制：请求跨越服务/容器重启，admission
  write-ahead 锁存按设计 fail-closed（台账 20261021-bwg-chatgpt-unverified-hold-release.md）。
- 取证发现：timer updater 自带 traffic-guard drain（`requests.lock` 排它 flock；
  admission 每请求共享锁、维护期新请求 503 `maintenance_in_progress`），但 `outputs/`
  全部 7 个历史 canary（v8029→v8038）都只有 healthz 空闲快照、没有 drain。admission
  2026-10-19 上线后该缺口才可致锁——漏抄是家族性的，不是单个会话的失误。

## 变更（32c1638，full gates 727 passed + 345 subtests 绿）

- updater（`scripts/remote/cpa-auto-update.sh`）：drain 后采 lane-hold 基线
  （`PRE_HOLDS`），容器重建且生成验收通过后差分（`comm`）；仅**本次重启新现**的 hold →
  exit 10 `UNVERIFIED` 保留镜像不回滚（镜像已验证；自动清锁被明确否决，处置归 cpa-gateway.md
  人工恢复）；既有 hold 归 doctor `==lane-hold==` 门禁；claude 墓碑
  （`authentication_required`）刻意排除；`LANE_HOLD_STATE_DIR` env 可覆盖
  （同 `TRAFFIC_GUARD_URL` fixture 纪律，doctor 断言生产字面量）。
- 门禁（`tests/test_cpa_updater_apply.py`）：updater 源码契约、嵌入式 python 功能三分支
  （clean/新现/损坏文件 exit 2）、outputs canary 机械门禁——7 个已执行历史 canary 冻结
  豁免（不改写已执行证据），下一个 authored canary 起强制含 `requests.lock` 排它 flock。
- monthly apt 维护（`scripts/system_maintenance_cron.ps1`）：docker 恢复复核后新增
  `lane-hold-state-after-maintenance` 检测日志（仅检测；daemon 弹跳无 drain 可言，执法归
  doctor `==lane-hold==`，绝不自动释放）；配套测试
  （`tests/test_maintenance_cron_family.py`）。
- runbook（`docs/runbooks/cpa-gateway.md`）：hold 差分语义 + 手动 canary 契约两节。

## 部署与验证

- `cpa_bwg_guardrails.ps1 -Apply`：首次被 fail-closed 拒绝（要求干净投影源），按序先提交
  32c1638 后 `GUARDRAILS_APPLIED`（自带备份与保留 8 清理）。
- `system_maintenance_cron.ps1 -Profile bwg -Apply`：wrapper 重投影，cron
  `0 14 1 * *`（北京 1 日 22:00）不变。
- 远端：`bash -n` 双脚本 OK；auto-update.sh 7 处新标识落位（23706B@`23482b17`）；monthly
  wrapper 检测块 2 处落位；嵌入式检查实弹 `LIVE_STATE=[]` + 幂等 OK（真实现网四 lane 全
  null；验证命令自身的引号噪音与本部署无关）。
- strict doctor：projection-drift 12×MATCH、`lane-hold-chatgpt-oauth=OPEN`、
  `lane-hold=OK`、`DOCTOR_CONTRACT_OK`。

## 边界与回滚

- updater 新现 hold 语义=保留镜像报 UNVERIFIED，不自动清锁、不回滚；回滚入口不变
  （compose 备份 + `--pull never`）。
- 投影回滚：guardrails -Apply 自带备份目录（保留 8）；monthly wrapper 安装自带
  backup + rollback 路径。
- 2026-11-01 22:00 北京 monthly 首跑 = admission 上线后首次无人值守 daemon 弹跳窗口：
  已有检测日志 + doctor 门禁兜底；日志若现 `WARN: lane-hold-state-after-maintenance=…`
  按 cpa-gateway.md 人工恢复流程处置。
