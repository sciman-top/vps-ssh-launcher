# 定时器取证与 no-update 分支实跑（2026-11-09 补强）

**触发**：用户第二次追问"是否需要投影 / 受控实战验收"。
**方法**：这次不去重复既有验收，而是验证"自动清理"这句话的**因果链是否真的闭合**——
定时器有没有触发、触发后有没有走到清理代码。

## 1. 定时器是真的在跑

```
systemctl is-enabled cliproxyapi-update.timer → enabled
systemctl is-active  cliproxyapi-update.timer → active
Next: 2026-11-10 04:18:52 UTC   Last: 2026-11-09 04:10:11 UTC (1h ago)
ExecStart=/opt/cliproxyapi/auto-update.sh
OnCalendar=*-*-* 04:00:00  Persistent=true
service Result=success
```

## 2. 但连续三天都没走到清理代码

`journalctl -u cliproxyapi-update.service` 的真实记录：

```
2026-11-07T04:14:41Z CANDIDATE current=v8.0.58 target=v8.0.58
2026-11-07T04:14:41Z OK: no newer mature release; current=v8.0.58 readiness verified
2026-11-08T04:03:02Z CANDIDATE current=v8.0.59 target=v8.0.59
2026-11-08T04:03:02Z OK: no newer mature release; current=v8.0.59 readiness verified
2026-11-09T04:10:12Z CANDIDATE current=v8.0.61 target=v8.0.61
2026-11-09T04:10:12Z OK: no newer mature release; current=v8.0.61 readiness verified
```

**三天全部走 `exit 0` 早退**。这直接证实了上一轮的诊断不是推测：
旧代码下，清理在"无成熟候选"的日子里**一次都没跑过**，而这样的日子是常态。

## 3. 修好后的分支实跑

仅仅"把函数抽出来调用"只能证明函数对，不能证明调度器会到达它们。所以这次
按真实顺序跑完整序列（`prune_error_dumps` → `prune_backups` →
`prune_manual_backup_families`），fixture 为 9 个 40 天前的
`*-admission-*` 目录：

```
fixture_before=9 fixture_after=4
PRUNE scope=backups kept=0 removed=0 policy=keep_4
PRUNE scope=backup_family pattern=*-admission-* kept=4 removed=5 min_age_h=168
NO_UPDATE_BRANCH_CLEANS=yes
```

判据：`NO_UPDATE_BRANCH_CLEANS=yes`。即"没有新版本"这一天，清理照常发生。

## 4. 为什么定时器日志里可能还看不到 PRUNE 行

从 `journalctl` 可见，服务输出走 `auto-update.sh[pid]` 前缀，说明
`log()` 写 stdout；而脚本同时把细节写 `$LOG`（`/opt/cliproxyapi/auto-update.log`）。
本次修复后，**下一次 04:00 UTC 触发（2026-11-10）**应出现
`PRUNE scope=backups` 与 `PRUNE scope=backup_family` 行。

这是可验证的预测，也是留给下一轮的唯一观察点。若届时没有这两行，说明
systemd 环境缺少某个前提（例如 `BACKUP_ROOT` 未导出），需再查一次。

## 5. 当前状态汇总

| 项 | 证据 |
|---|---|
| 定时器 | enabled / active / Persistent，最后一次 success |
| 清理顺序 | 活体断言 `before_no_update_exit=True`（两项目录清理） |
| 分支实跑 | `NO_UPDATE_BRANCH_CLEANS=yes` |
| `--check` 只读 | `backups_before=117 backups_after=117` |
| doctor | `update_backups=117 kib=1163108 advisory=OK`，`DOCTOR_CONTRACT_OK` |
| 门禁 | 1192 passed / 18 skipped，bandit / ruff / format / mypy 全绿 |

## 6. 边界

- 本轮只读 + fixture 演练，未改生产。
- 不改变 `prune_images` 的时机与保护逻辑。
- 7 个 `image_gone` 备份的 `UNRESTORABLE` 标记不受影响。
