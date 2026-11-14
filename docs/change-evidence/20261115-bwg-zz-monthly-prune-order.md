# 2026-11-15 双机月度 wrapper 函数定义顺序修复与部署

## 触发

bwg `monthly-maintenance.status` 在 2026-11-14T14:01Z 为 `result=failed exit_code=1`，
CPA strict doctor 报 `heartbeat-monthly-maintenance=FAIL`。

## 根因（repo_verified + live_accepted）

`scripts/system_maintenance_cron.ps1` 的内嵌 here-doc 在 step 列表中调用
`prune_verified_orphan_containers`，而该函数定义在 157 行之后、最终 `exit` 之后。
wrapper 以 `set -Eeuo pipefail` 运行，bash 在调用点解析不到函数即
`command not found`（退出 127），于是 step 失败、`fail=1`、成功标记被抑制。
`bash -n` 只做语法解析，不解析函数顺序，所以本地门禁全绿。

## 变更

- 把函数定义移到调用点之前（现在 301 定义 / 345 调用）。
- 新增 `test_the_deployed_wrapper_defines_each_function_before_calling_it`：
  扫描 here-doc 提取体，任一函数在被定义前被引用即失败。对旧文本会报
  `prune_verified_orphan_containers (line 302 -> defined 459)`。

## 部署（逐台，先 bwg 后 zz）

| 主机 | 备份目录 | 部署后 sha256 | 顺序 | 自动重启策略 |
| --- | --- | --- | --- | --- |
| bwg | `/var/backups/v2ray-agent-maint.i7tYCJ` | `cc528b66…` | def 301 / call 345 | `AUTO_REBOOT_ON_MAINT` 默认 0，未改 |
| zz | `/var/backups/v2ray-agent-maint.Xg7dZh` | `cc528b66…` | def 301 / call 345 | cron 行 `AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15` 原样保留 |

两台 `bash -n` 通过，cron 行未变。

## 验收（live_accepted）

bwg 实跑一次 wrapper：

```
[2026-11-14 14:01:54] ERROR: verified orphan container hygiene     <- 修复前
[2026-11-14 17:48:09] INFO:  verified orphan container hygiene     <- 修复后
[2026-11-14 17:48:09] INFO:  disk-hygiene scope=orphan_container removed=0
```

`monthly-maintenance.status` 变为 `result=success exit_code=0`，
doctor `heartbeat=monthly-maintenance result=success exit_code=0 age_hours=0`。

## 未完成与边界

`-AuditProjection` 仍为 `CPA_PROJECTION_AUDIT=PASS evidence=filesystem_projection`。
CPA 的 `-Apply` 主动拒绝：仓库 pin `104c0ef5…` ≠ 主机 pin `d2256369…`，
报 `REFUSE admission_pin_transition_pending`。这是设计意图（admission 世代轮换
必须走复核过的 deploy 交易，不能由批量重投影推进）。当前在树的最新交易脚本
`outputs/deploy-admission-delatch-20261114.ps1` 也因 `live_guard_generation_drift`
拒绝，说明主机 baseline 与仓库之间还有一代差距，需单独排期，不在本次范围。

## 回滚

`cp -a /var/backups/v2ray-agent-maint.<id>/ …` 按备份目录逐文件恢复；
或 `git checkout 5892454^ -- scripts/system_maintenance_cron.ps1` 后重跑
`system_maintenance_cron.ps1 -Profile <bwg|zz> -Apply [-PreserveAutoReboot]`。
