# 2026-09-25 维护锁路径统一远端部署（bwg）与 zz 处置决策

**性质**: 真实远端写入（bwg：月度维护重投影 + guardrails -Apply；内核 wrapper 重投影由并行会话同日完成，本文记录终态）
**授权**: 用户 2026-09-25 指示"按推荐/建议连续执行修复和优化，直至完成所有任务"；全程逐台串行（bwg 完成并复验后才评估 zz），未并行触发两台。
**脱敏**: 全程未输出或提交 target.json、密码、IP、token、随机路径。

## 1. 背景与目标

仓库内四个远端入口此前各自持锁，路径不统一（`/opt/cliproxyapi/auto-update.lock`、`/run/v2ray-agent-maint.lock`），跨入口互斥不成立。仓库侧已统一为 `/run/vps-ssh-launcher-maintenance.lock`（eeac7a1..9dcbed6 系列 + d45a455），本轮把远端投影补齐：

- bwg `/etc/v2ray-agent/auto_update_xray.sh`（内核周更，cron.d `vps-launcher-kernel-update`）
- bwg `/usr/local/sbin/monthly-maintenance.sh`（月度维护，cron.d `vps-launcher-monthly-maintenance`）
- bwg `/opt/cliproxyapi/auto-update.sh`（CPA 每日 updater，timer 04:00 UTC）

## 2. 执行与证据（bwg，UTC）

1. 内核 wrapper 重投影（11:11，含 vasma 菜单锚点预检 `verify_vasma_anchors`，与当日 f37eb14 同步部署）；投影 pin=`v26.3.27` + sha `8255dd93…`，与现网二进制完全一致 → 今晚 14:00 UTC cron 只会"已匹配跳过"或 latest 漂移 `UNVERIFIED` 跳过，无无人值守升级。
2. 月度维护重投影（11:22）：`system_maintenance_cron.ps1 -Profile bwg -Apply`；备份 `/var/backups/v2ray-agent-maint.W9iT5T`；wrapper 含 docker 容器快照复验段，`syntax-ok`。
3. guardrails -Apply（11:24）：六文件投影 + CPA 重启 + Nginx reload；备份 `/root/cpa-guardrails-backup-20260925T112430.033937952Z`；`GUARDRAILS_APPLIED`；目录 12 模型（bare luna/glm-5.3(+flash)/deepseek/ai.input.im 裸名/CIII 别名/slot3 别名全对位，退役 ID 未复活）；apply 后 `READY_STATUS=200`。

## 3. 终态复核

- `LOCK_FILE`/`exec 9>` 三文件全部 = `/run/vps-ssh-launcher-maintenance.lock`（第二条 SSH 命令逐文件 grep 确认）。
- 严格 doctor：`DOCTOR_CONTRACT_OK`，projection-drift 6×MATCH（apply 前 auto-update.sh 为 MISMATCH，apply 后清除）。
- `oauth_monitor=OK`、`cooldown_state=none`、`catalog_gpt6_luna=present`。
- 服务：xray active、8443 LISTEN、cli-proxy-api running。

## 4. zz 处置决策：不改锁路径

zz 实测（只读）：sing-box lane（`auto_update_singbox.sh` 在、`sing-box.service` running、无 xray），月度为 zz 原生 `auto_system_maint.sh`（带受控自动重启，7/10 起既有设计）。并行会话当日已把 zz wrapper 刷新为与原生脚本**共用 `/run/v2ray-agent-maint.lock`**（见 20260925-bwg-zz-cron-maintenance.md §3）。

决策：**zz 不迁移到统一锁路径**。zz 上仓库管理的入口只有内核 wrapper 一个，其唯一互斥伙伴是原生月度脚本；把 wrapper 改到新路径会破坏这层既有互斥（原生脚本不在本仓管辖内，"zz 原生路径不动"），而新路径在 zz 没有其他消费者。统一锁的目标是跨入口互斥，zz 当前用旧路径恰好达成该目标。

## 5. 回滚入口

- 内核 wrapper：`/var/backups/v2ray-agent-maint.5tgb1U`（11:11）与本轮各备份目录。
- 月度维护：`/var/backups/v2ray-agent-maint.W9iT5T`（11:22）。
- CPA 投影六文件：`/root/cpa-guardrails-backup-20260925T112430.033937952Z`。
- Git 回滚只撤销仓库文件；远端恢复以上述备份为准。

## 6. 受控实战验收（12:08 UTC，零变更路径）

三个重投影入口在新锁路径下的运行时行为验收（全部只读/零变更，第二命令复核）：

1. 内核 wrapper 正路径：`bash /etc/v2ray-agent/auto_update_xray.sh` →
   锚点校验+布局检测通过、`xray run -test` Configuration OK、
   `INFO: pinned Xray version and hash already match; skip reinstall`、
   `WRAPPER_EXIT=0`；版本 26.3.27 未动、xray active。未调用 vasma、未发起下载。
2. 内核 wrapper flock 负路径：后台持 `/run/vps-ssh-launcher-maintenance.lock` 4 秒
   并发运行 wrapper → `INFO: another maintenance/update job is already running; exit`、
   `BUSY_EXIT=0`；锁文件在统一路径就位。
3. CPA updater `--check`（提前退出模式，零变更零 provider 流量）：新锁获取成功、
   `CANDIDATE current=v7.3.17 target=v7.3.17`（无成熟新候选，明日 04:00 UTC 定时
   运行将走零流量 no-update readiness 路径）、`BACKUP_HEALTH status=ok`、
   `CHECK_EXIT=0`；容器运行 `v7.3.17@sha256:a1dffb9c…` 与 README 指针一致。
4. 月度 wrapper 锁冒烟：`flock -n <新锁> true` 获取成功；重量级 apt/容器复验路径
   已由同日 01:41 UTC 受控实跑验收（见 20260925-bwg-zz-cron-maintenance.md §2.3），
   本轮模板增量仅锁路径一行，下次调度实跑（10-01 14:00 UTC）为准。

## 7. 残留观察

- zz wrapper 为旧模板（无 vasma 菜单锚点预检、无 pin 强制）。因锚点校验需模板参数化才能在保留旧锁路径的前提下投影，收益/成本比不成立，本轮不做；若未来 zz 原生月度脚本迁移或入库，再一并处理。
- 每日 04:00 UTC CPA updater 明日起以新锁路径运行；次日巡检基线（crontab 1 行 TLS + bwg 两个 cron.d + zz 两个 cron.d）不变。

ACCEPTANCE_RESULT=PASS（bwg 三入口锁路径统一 + DOCTOR_CONTRACT_OK 6×MATCH + 服务复验；zz 维持共享旧锁的互斥终态）
