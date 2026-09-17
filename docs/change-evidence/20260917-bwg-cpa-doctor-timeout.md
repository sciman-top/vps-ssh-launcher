# BWG doctor 容器状态行 + ssh_tool 超时口径文档化

- Scope: 仓库代码与文档；doctor 经 SSH 只读实测，远端无写入。授权：用户
  2026-09-17"按你的推荐/建议连续执行修复和优化，直至完成所有任务"。

## 发现与处置

1. **`--command-timeout` 早已存在，缺口是运营认知**（P1.1 修正口径）：
   `run` 子命令有 `--command-timeout`（默认 `CMD_TIMEOUT=60`，按"无输出空闲"
   计时、收到输出自动续期、`0` 关闭）与 `--command-hard-timeout`（绝对上限），
   且 test_ssh_tool 已覆盖自定义/禁用/负值/`--all` 透传。今日两次操作踩坑
   （fixture 62s 等待段、astra 慢生成）均因未传该参数，属文档缺口而非代码
   缺口。处置：README 启动链一节补充用法与"静默长命令必须显式调大或置 0"
   的告警；实测验证——静默 75s 命令在 `--command-timeout 150` 下完整存活
   （旧行为 60s 即断）。setsid 脱离仍适用于流式/守护场景。
2. **doctor 新增 `==container==` 段**（P1.2）：输出
   `StartedAt=… RestartCount=… Status=…`，插入 `==inventory==` 与
   `==cooldown-state==` 之间。动机：本轮诊断"裸目录 8↔10 波动"时，容器
   StartedAt 是区分"投影漂移 vs 内存冷却"的关键判据（并行会话风控评审亦
   点名此候选）。测试同步：timer→auth-modes 段锚点测试加入
   `==container==`/`StartedAt=`/`RestartCount=`。
3. **`has_r2=` 死标志清理**（P2.4）：r2 通道 9/15 已彻底移除，该信息位永远
   False；从 guardrails 摘要删除（无测试钉定）。

## 验证

- 全量门禁：132 passed / 1 skipped / 117 subtests + bandit/ruff/format/mypy
  全绿（含新增锚点断言）。
- 实测：`--command-timeout 150` 静默 75s 命令完整存活；doctor 线上运行输出
  `==container== StartedAt=2026-09-17T13:28:17Z RestartCount=0
  Status=running`，总体 `DOCTOR_CONTRACT_OK`。

## 边界

- 远端零写入（doctor 只读）；`--command-timeout` 默认 60 保持不变，不影响
  既有调用方；`--command-hard-timeout` 默认关闭。
- 遗留 user-side：relay key 轮换（等新 key）、provider 侧 OAuth 会话吊销
  （官方账号安全设置）；fixture 重跑按计划留待下次二进制验收。
