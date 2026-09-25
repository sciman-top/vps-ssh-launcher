# bwg 维护加固投影与受控实战验收（2026-09-25）

## 范围

把本日审查修复切片（`64d5d64`..`5a9b177`，上游 `e51726b`/`5450824` 为并行
会话产物）投影到 bwg 并做受控验收。全程脱敏：无凭据、无订阅地址、无随机
路径、无公网 IP（出回性质仅描述）。

## 只读基线（strict doctor，exit 0）

- `DOCTOR_CONTRACT_OK`；`==projection-drift==` 六文件全 `MATCH`（本日提交未
  触及六个已部署投影文件，远端无需 CPA 重投影）。
- 目录 `MODEL_IDS=` 12 项（`gpt-image-2.5` 为 optional 未开放缺席，与清单
  派生契约一致）。
- `oauth_monitor=WARN_RENEWAL_WINDOW`、`oauth_days_left=2`（刷新点 ~2026-09-27，
  未到 ACTION_REQUIRED）。
- timer `last_trigger_age_hours=9`；24h 状态面 200×479，502×26 均为
  `slow_upstream_ge_3s`（bot 上游侧，既有口径），503×13 中 12 个
  `fast_upstream_lt_0_5s` 本地快败。

## 月度维护 -Apply（唯一持久远端增量：logrotate）

- 部署前只读：wrapper 标记齐全 `syntax-ok`、`==logrotate== missing`。
- `-Apply` 成功：备份 `/var/backups/v2ray-agent-maint.vIZ55y` 四件套
  （maintenance-wrapper、cron-file、crontab、`logrotate-file.missing`——
  正确记录部署前不存在）；`ROLLBACK` 路径未触发。
- 部署后复验（第二条 SSH）：`/etc/logrotate.d/vps-launcher-monthly-maintenance`
  在位 644、`logrotate-config-ok`；`/etc/cron.d/vps-launcher-monthly-maintenance`
  原样（`0 14 1 * *`，10-01 22:00 北京首跑不变）；crontab 仍仅 TLS 行；
  wrapper 3297 字节 755 与仓库一致。

## google_ipv4_routing 新 apply 载荷受控实战（桩）

新载荷（共享锁 + 备份快照 + 超时）以 `-RemoteApplyScript /tmp/fake-reapply.sh`
桩脚本做零路由变异实战：

- `BACKUP_DIR=/var/backups/google-ipv4-routing-20260925T134548.542136719Z`
  生成，快照含 drop-in、两个 routing conf 与桩脚本本体。
- `APPLY_DRYRUN_STUB_EXECUTED`（桩按预期执行）。
- 后置只读段全绿：xray 26.3.27、服务 active、drop-in 在位、两 conf
  `marker-present`、`config-ok`、v4/v6 出口均通。
- 锁忙负路径：探针持锁期间二次 flock 被拒（`SECOND_FLOCK_REFUSED_AS_EXPECTED`）。
- 清理：桩脚本与验收备份目录已删除，`LOCK_ACQUIRED_OK` 确认共享锁恢复可用。
  该验证无持久远端增量。

## 计划任务 S4U 更新（含一次缺陷发现与修复）

- 首次非提权 `-Replace`：S4U 注册 `拒绝访问`，且旧实现的
  Unregister→Register 顺序在 Register 失败时**丢掉了既有任务**——立即按原
  配置（Interactive/PT20M）手工恢复。
- 脚本修复（本切片提交）：S4U 提权预检（非提权在卸载前即失败）+ 注册失败
  自动恢复旧任务定义；非提权路径实测预检生效、任务原样保留。
- UAC 提权重注册成功：`logon=S4U`、`limit=PT2H`、下次运行 2026-09-26 20:00。
- 受控触发一次：`LastTaskResult=0`，`maintenance-runs/20260925T135134Z-bwg.log`
  与 `-plan.json` 正常落盘（S4U 会话内真实 SSH 只读 inventory + dry-run）。

## 真实 SSH 集成

`test_integration_real_ssh.py`（固定无副作用 round-trip，profile=bwg）
3/3 通过。

## 残余与回滚

- 持久远端增量仅月度维护 logrotate 一项；回滚 = 恢复备份目录内四件套
  （`restore_apply_state` 语义）。
- guardrails 应急契约（`-DeactivateOAuthLuna` 清单派生）为调用时载荷，无需
  预投影；其实战触发仍以真实凭据处置场景为准（本切片以六场景行为钉测试
  覆盖，不主动销毁凭据做验收）。
- 下次月度维护（10-01 22:00 北京）首跑时日志滚动将首次生效。
