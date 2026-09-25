# 2026-09-25 BWG/ZZ 调度修复与月度系统维护部署

**性质**: 真实远端写入（bwg 两次 -Apply + 一次受控实战验收；zz 一次 -Apply + DRY_RUN 验收）
**授权**: 用户 2026-09-25 明确指示"按推荐/建议连续执行修复和优化，直至完成所有任务"，并沿用既定逐台顺序（先 bwg 验收完毕，再 zz，全程未并行）。
**脱敏**: 全程未输出或提交 target.json、密码、IP、token。

## 1. 检测结论（修复前）

| 项目 | bwg | zz |
| --- | --- | --- |
| 每周内核自动更新 | wrapper 在、调度行丢失（9/24 14:07-14:12 UTC crontab 被改写） | 正常（9/18 实跑记录；wrapper 为 7/10 旧模板无 flock） |
| 每月 1 日 22:00 系统维护 | 完全不存在 | 正常（`auto_system_maint.sh`，7/10 部署，每月实跑：6/1、7/1、8/1、9/1，带 apt 锁等待/重试/服务自恢复/受控重启） |

bwg 9/24 调度行丢失归因：journal 仅见 `cron[823]: (root) RELOAD (crontabs/root)` 两次（14:07/14:12 UTC），bash history 无记录，本仓脚本逻辑不会产生该终态；同期 bwg 有两次重启（13:09/14:00 UTC），无法指认具体写入者。对策 = 恢复 + 巡检基线比对（见 §5）。

## 2. bwg 变更

1. 每周 xray 内核 cron 恢复：`vasma_kernel_update_cron.ps1 -Profile bwg -Kernel xray -Apply`
   - 证据：`APPLY_BACKUP_DIR=/var/backups/v2ray-agent-maint.Vt6G2X`；cron 恢复为 `20 14 * * 5 /bin/bash /etc/v2ray-agent/auto_update_xray.sh`；wrapper `syntax-ok`。
2. 月度维护部署：新脚本 `scripts/system_maintenance_cron.ps1 -Profile bwg -Apply`（默认时刻 `0 14 1 * *`，UTC 主机上即北京时间 22:00）
   - 证据：`APPLY_BACKUP_DIR=/var/backups/v2ray-agent-maint.mVIq9T`；cron `0 14 1 * * /bin/bash /usr/local/sbin/monthly-maintenance.sh`；wrapper `syntax-ok`；永不自动重启主机。
3. 受控实战验收（2026-09-25 01:41-01:42 UTC）：真实跑一轮
   - 日志收口 `monthly maintenance done`，五步全过、零 ERROR；apt 实际升级 docker-ce 29.8.1、netplan.io、curl 等积压包。
   - dockerd 重启带动 cli-proxy-api 容器正常重启（ExitCode=0、RestartCount=0），随后真实流量 200 恢复；复验 xray active、8443 LISTEN、`DOCTOR_CONTRACT_OK`。
   - 剩余 0 个可升级包（6 个 phased held-back 属 Ubuntu 正常态）。
4. bwg crontab 终态基线（3 行）：RenewTLS 行、`20 14 * * 5 ... auto_update_xray.sh`、`0 14 1 * * ... monthly-maintenance.sh`。

## 3. zz 变更

zz 两项需求修复前即满足，仅做一项一致性优化：

- sing-box wrapper 刷新到当前模板（补 flock 互斥 + 上游查询失败优雅跳过），使其与 `auto_system_maint.sh` 共用 `/run/v2ray-agent-maint.lock`：`vasma_kernel_update_cron.ps1 -Profile zz -Kernel sing-box -Apply`
  - 证据：`APPLY_BACKUP_DIR=/var/backups/v2ray-agent-maint.92XVsH`；cron 行保持 `20 14 * * 5 ... auto_update_singbox.sh` 不变；flock 就位（wrapper 行 8/24/25）；`bash -n` 通过；sing-box active。
- zz 月度维护保持原实现不动（其能力为 bwg 新脚本的超集；AUTO_REBOOT_ON_MAINT=1 为 7/10 起的既有显式设计）。
- DRY_RUN 验收（2026-09-25 01:47 UTC）：`DRY_RUN=1 auto_system_maint.sh` 退出码 0，全链路可执行。
- 事实提示：DRY_RUN 暴露 zz 当前存在 `/run/reboot-required`（待重启包 libc6，内核无需更换）；预计 2026-10-01 22:00 月度维护后 22:15（北京时间）自动重启，与 7/1、8/1 既有行为一致。
- zz crontab 终态基线（3 行）：RenewTLS 行、`0 14 1 * * AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15 ... auto_system_maint.sh`、`20 14 * * 5 ... auto_update_singbox.sh`。

## 4. 回滚入口

- bwg 内核 cron：远端备份 `/var/backups/v2ray-agent-maint.Vt6G2X`（含旧 wrapper 与旧 crontab）。
- bwg 月度维护：`/var/backups/v2ray-agent-maint.mVIq9T`；或删除 crontab 中 `monthly-maintenance.sh` 行与 `/usr/local/sbin/monthly-maintenance.sh`。
- zz wrapper：`/var/backups/v2ray-agent-maint.92XVsH`（7/10 旧模板）。
- Git 回滚只撤销仓库文件，远端恢复以上述备份为准。

## 5. 防复发

每日 22:05 BWG CPA 巡检自动化已追加只读 crontab 基线比对（bwg/zz 各 3 行）与维护日志 ERROR 检查；任何缺行/改动=ATTENTION。月度任务首次 cron 实跑时点：2026-10-01 14:00 UTC（bwg）、同日 zz（含可能的 22:15 自动重启）。

## 6. 仓库内变更

- 新增 `scripts/system_maintenance_cron.ps1`（默认只读探针；-Apply 带备份+回滚 trap；永不自动重启）。
- `test_scripts.py`：新增安全契约文本断言（禁自动重启、noninteractive+confold、共用锁、回滚 trap、cron 安装只删自身行）与渲染 wrapper `bash -n` 验证；入口点清单纳入新脚本。
- `README.md` 新增"月度系统维护"用法段；`AGENTS.md` 脚本清单行同步。

ACCEPTANCE_RESULT=PASS（bwg 实跑 done + DOCTOR_CONTRACT_OK；zz DRY_RUN=0 + wrapper 语法/锁位验证）
