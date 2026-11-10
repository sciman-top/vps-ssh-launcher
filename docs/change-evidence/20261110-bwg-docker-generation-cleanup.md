# bwg docker 代际裁决清理 + 月维 df 水位线（2026-11-10）

**触发**：用户授权按 2026-11-10 清理评审建议自主执行（"自主代理执行清理，授权按推荐连续自动修复"）。
**边界**：只清理"无人值守可判定安全"的子集；soak 期回滚材料（fork 构建产物、tagged 镜像、备份目录、用户数据）全部保留。

## 1. 裁决逻辑

保护集 = 运行镜像 + 全部保留备份记录的 image-id（复用 `cpa-auto-update.sh`
`prune_images` 的同一逻辑；fork 时代 updater 拒绝更新导致该 prune 不再触发，
故本次人工执行同一策略）：

- 运行镜像 `786669dd7438`（cli-proxy-api-fork:v8.0.61-gemini-raw）
- 备份引用（10/10 全量 PROT_OK 验证）：`fc714b4339f2` `40dda818164c`
  `060f352248b4` `afe1135e8d87` `19eae277664c` `66e51ccba478`
  `5d5bab318ac0` `c198886a86b5` `ca6af8b19642` + 运行镜像
- 删除候选与保护集本地断言 `overlap=0` 后才发送；远端循环内每镜像删前
  再复核"已获得新 tag 则 RMI_SKIP"双保险。

## 2. 执行动作（bwg，单会话串行）

| 动作 | 结果 |
|---|---|
| 死容器 rm ×4 | `mystifying_mayer`(Created, 镜像已亡) + `cpa-wb-failover-build{,-v2,-v3}-20261019`(Exited, 10-19 构建测试) |
| 未保护 `<none>` 代际 rmi ×41 | `RMI_SUMMARY ok=41 skip=0 fail=0` |
| `docker builder prune -f` | 释放 830.7MB（悬空 cache；in-use cache 保留加速 soak 期重建） |

**刻意保留（观察挂账，soak 过后逐代裁决，不挂自动清理）**：
tagged 回滚梯（v8.0.53/55/58/59/61 + fork）、备份引用的 4 个 `<none>`
（v7.3.17/v8.0.48/v8.0.52/v8.0.54 时代）、古董 tagged `v8.0.28/31/34/35` +
`bwg-local/cpa:v8.0.40-pr6784-sim-v2`、双 golang 基础镜像、`/root` 11-09
fork 事务产物（~750MB）。

## 3. 前后对比

| 指标 | 前 | 后 |
|---|---|---|
| `df -h /` | 29G used / 9.0G avail / 77% | **23G / 15G / 61%** |
| docker images | 58 个 / 9.606GB | 17 个 / 4.761GB |
| containers | 5（4 死） | 1（运行中） |
| build cache | 9.318GB | 8.487GB |
| CPA 备份目录 | 63 | **63（未动）** |

## 4. 验证

- `cli-proxy-api` Up（无中断）；`127.0.0.1:8318/healthz` = 200
- 保护集 `PROT_OK` 10/10（每个保留备份的回滚镜像仍在位）
- strict doctor `DOCTOR_CONTRACT_OK`（尾部 `credential_ban_signal=1` 为既有
  6008 观察项，脚本自标 "observation, not a gate"，与本轮无关）

## 5. 月维 df 水位线

`system_maintenance_cron.ps1` 维护脚本收尾处新增结构化行
`DISK_ROOT_AFTER=$(df -h / | tail -1)`，月度累积趋势可从
`/var/log/monthly-maintenance.log` grep，不新增任何 cron/自动化面。
双机串行 `-Apply`（各自内置 backup + 回滚 trap）：

- bwg：`APPLY_BACKUP_DIR=/var/backups/v2ray-agent-maint.bx56qG`，
  部署脚本 line 447 含该行；cron `0 14 1 * *` 不变
- zz：`APPLY_BACKUP_DIR=/var/backups/v2ray-agent-maint.EAsFWE`，
  `-PreserveAutoReboot` 精确校验通过，cron 保留
  `AUTO_REBOOT_ON_MAINT=1 AUTO_REBOOT_DELAY_MIN=15` 原文
- 测试：`test_maintenance_cron_family.py` + `test_herestring_escaping.py`
  22 passed / 17 subtests

## 6. 回滚入口

- 镜像：保护集在位即本地回滚能力未受损；被删 41 个为上游失签旧 digest，
  仅可按 digest 重拉（上游保留时），与任何保留备份无引用关系
- 月维 wrapper：两机各自 `APPLY_BACKUP_DIR` 上文所列，`backup_file` 语义
  按脚本 `rollback_apply` 恢复

## 7. 部署后受控演练与实战收官（-Apply 之后补做）

- 双机 `bash -n /usr/local/sbin/monthly-maintenance.sh` = `SYNTAX_OK`
- 双机部署行 verbatim 演练（stub log + eval 部署文件 447 行原文）：
  bwg `DISK_ROOT_AFTER=/dev/sda3 40G 23G 15G 61% /`、
  zz `DISK_ROOT_AFTER=/dev/vda1 20G 5.9G 14G 31% /`，格式与预期一致
- CPA 数据面实战：8317 直探单发 POST（access key 远端运行时读取、零回显）
  `/v1/chat/completions` model=hy4free max_tokens=1 → **200**
- 完整 apt 月维护实战不在本轮授权范围（比例门禁）；自然验收锚点 =
  12-01 定时首跑后 `grep DISK_ROOT_AFTER /var/log/monthly-maintenance.log`
