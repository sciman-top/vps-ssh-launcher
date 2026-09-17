# BWG doctor 容器行去重 + auth/logs 边界核查 + fixture success 场景失败记录

- Scope: 仓库代码（doctor 去重）；远端只读+清理；fixture 在隔离 ns 内运行。
  授权：用户 2026-09-17 三项指令（①fixture 重跑关 P2.3 ②doctor 容器行去重
  ③auth/logs 边界核查与处置）。

## ② doctor 容器状态行去重

- 并行会话在 `==cpa-doctor==` 早段守卫（含 status/restart/started/image 与
  mark_fail container）与本人后加的 `==container==` 段（StartedAt/RestartCount/
  Status）输出重复。去重：移除后加的冗余段，保留信息更全的早段守卫。
- 测试同步：移除三个旧锚点，新增两条断言——早段守卫行必须存在
  （`restart={{.RestartCount}} started={{.State.StartedAt}}`）、
  `echo "==container=="` 全文恰出现一次（防再次重复）。

## ③ auth/logs 边界核查与处置

- 实况：`error-v1-*.log` 恰 10 个文件（2-3KB/个）= `error-logs-max-files: 10`
  封顶生效；`logs-max-total-size-mb: 32` 在位；总量 1.4MB。**error 日志有界，
  无限增长不成立。**
- 发现并处置：`request-log-parts-request-body-3369352565/body-*.tmp`——
  2026-09-15 遗弃的请求正文残片（1.17MB，含用户提示词内容，隐私残留）。
  已删除（rm，无备份——删除即目的）。error 日志按上限保留。
- 结论：auth/logs 有界（≤32MB/10 文件）+ 无 further 动作；残片来源疑为
  9/15 一次中断的流式请求日志写入，若再现按同口径清除即可。

## ① fixture 重跑：success 场景失败（如实记录，未通过）

- 运行形态：真实 v7.3.4 二进制 + 并行会话刚部署的收紧版 updater
  （`ed0e56dc…`，commit 57813f9）与现 HEAD health（`7b14f531…`）+ 现版本
  验收脚本（`368c7f77…`/`541dcbb4…`，均为 9651fb6/57813f9 首次入库）。
- 结果：overload/cooldown/62s 恢复/真实 health/start_fail/model_exposure
  回滚/transient defer 全部通过；**success 场景失败**——更新器 pre-update
  health 返回 `UPSTREAM_UNAVAILABLE`（fixture 诊断：CANDIDATE v0.0.1→v0.0.2
  `DEFER: pre-update health unavailable; image unchanged`）→ 走 DEFER 而非
  升级，`cpa-update-acceptance.py:117` 断言失败。
- 根因判定：transient 场景的持续 503 使 fixture 内 sol 进入冷却；紧接的
  success 场景未等冷却窗结束即做 pre-update health → 冒烟失败 → DEFER。
  即收紧版 updater/验收脚本的场景节奏存在冷却竞速回归。
- 附带缺陷：进程以 AssertionError 退出但包装行打印 `ACCEPTANCE_EXIT=0`
  （启动口令的 `$?` 绑定问题）——退出码行不可信，以 traceback 为准。
- 影响：**生产回滚安全不受影响**（start_fail/model_exposure 的回滚路径、
  transient 的 defer 路径全部按设计工作）；最坏情形是 9-21 计时器腿在
  sol 冷却/坏窗口时 benign defer（不升级、不回滚）。success 路径的节奏
  修复属于并行会话刚入库的新代码（其作者一小时前仍在迭代），为避免竞态
  冲突本切片不代改，仅记录移交。
- 清理：`NO_FIXTURE_LEFTOVERS`，临时目录与日志已删；生产全程健康
  （readiness OK、容器 Up、`NO_CDS_OK`、部署 sha updater `ed0e56dc…`/
  health `7b14f531…` 与 HEAD 一致）。
