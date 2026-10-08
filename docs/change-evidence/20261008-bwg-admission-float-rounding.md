# BWG CPA admission 浮点冷却时间修复部署

## 变更范围

- 目标：BWG `/opt/cliproxyapi/cpa-admission.py`。
- 代码版本：`10b190e`（`_ceil_remaining_seconds` 吸收单调时钟的 ULP 级误差；`snapshot()` 在锁内读取时钟）。
- 远端动作：在共享维护锁内备份并原子替换单个 admission 脚本，重启
  `cpa-admission.service`。未触碰 ZZ；未改 OAuth 凭据、Nginx 配置或维护计划。
- 修复原因：Windows 单调时钟值 `6419.828` 加 7200 秒后，二进制浮点差可能
  让 `ceil()` 返回 7201；单调时钟读取在锁内与状态快照保持同一时点。

## 部署前证明

- `scripts/run_gates.ps1 -Profile Full`：498 passed、1 skipped（真实 SSH 集成测试
  按默认策略跳过）、214 subtests passed；Bandit、Ruff、格式检查和 mypy 通过。
- `scripts/bwg_full_maintenance.ps1 -Mode Observe -RunIntegration`：9 个步骤全部通过，
  包含 CPA doctor 前后检查、控制面清单和 BWG 维护只读探针。
- 部署前 BWG 远端脚本 SHA-256：
  `c64114425612a992b38d6d1acff20fda1a7048091d0deb3ac6cef566b7ad3693`，与变更前
  `HEAD` 一致。strict doctor 返回 `DOCTOR_CONTRACT_OK`。
- 本次暂存脚本由共享 `Invoke-LauncherRemoteCommand` 传输；逐项校验旧/新 SHA，
  healthz 指向 admission 实际绑定的 `127.0.0.1:8318`，并在替换前要求维护锁可得、
  服务健康且所有 lane 的 `inflight`、`pending` 与 `retired_readers` 为零。

## 执行与结果

- 事务时间：2026-10-08 03:06:51 UTC（远端备份目录时间戳）。
- 执行入口：`outputs/deploy-admission-20261008.ps1`，Profile=`bwg`；执行时脚本
  SHA-256=`4d37a65935f7b4e01f1be41ee49bb17e6f16823936dfb2803de0801202be6055`。
- 暂存文件 `python3 -m py_compile` 通过；事务锁获取成功；lane 空闲检查通过；
  原子替换后服务健康复起，远端暂存目录已清理。
- 远端文件 SHA-256：
  `e7a5e108916e8fd4923a2423207187a80dc0bd25fb72436fc2c95a5d7e3e1ae3`，与本地源码一致。
- 部署后 strict doctor 退出码 0，返回 `DOCTOR_CONTRACT_OK`；随后 SSH 只读 SHA
  复核仍与本地源码一致。
- 未发送模型生成请求；结果证明代码投影与 admission 服务加载，不代表自然用户流量
  或长期课堂/业务接受。

## 回滚

- 变更前备份：`/root/cpa-admission-floatfix-backup-20261008T030651Z/`。
- 手动回滚入口：
  `bash /root/cpa-admission-floatfix-backup-20261008T030651Z/rollback.sh`。
- 回滚脚本先取共享维护锁，恢复原文件及属主/权限，重启 admission 并检查 healthz；
  回滚后应重跑 strict doctor。若仓库仍保留 `10b190e`，doctor 会如实报告源码投影
  不匹配，需按仓库真源选择重新部署或回滚代码。

## ZZ 只读核验

- `ssh_tool.py --profile zz check`、`google_ipv4_routing.ps1 -Profile zz`、
  `vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box`、
  `system_maintenance_cron.ps1 -Profile zz` 均退出码 0。
- ZZ 未执行 Apply、升级、重启或计划任务写入。
