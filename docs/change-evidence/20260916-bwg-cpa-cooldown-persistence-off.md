# BWG CPA 冷却持久化关闭（save-cooldown-status → false）

- Scope: 仅 `bwg` profile；未连接或修改 `zz`。未使用 SSH tunnel。
- Authorization: 用户 2026-09-16 指令"按你的推荐/建议连续执行修复和优化，直至完成所有任务"（对应当日诊断结论中的 L1 主修复提案）。
- 动机：OAuth luna 频繁 502/503 容量族错误（`service_unavailable_error/server_is_overloaded`，上游全局事件，非账号风控）叠加两台放大器——客户端秒级重试风暴（40ms 本地冷却拒绝）与 `save-cooldown-status: true` 持久化；上游滞留 bug [#5639](https://github.com/router-for-me/CLIProxyAPI/issues/5639)/[#5770](https://github.com/router-for-me/CLIProxyAPI/issues/5770) 经 releases 核实截至 v7.3.4（最新）未修。冷却转纯内存态后，持久化滞留类故障整类消失，内存冷却最长 30min（429 退避封顶）且任一成功请求自愈。

## 事务与回滚

- 备份：`/root/cpa-cds-persist-off-20260916T130833Z/`（config.yaml + auth 内现存 `.cds`）。
- 变更：断言 `^save-cooldown-status: true$` 恰 1 处后 sed 翻转为 `false`；
  config sha256 `bbcfbfcd…` → `1627a8c2…`；按 runbook 顺序
  `docker stop` → `rm auth/*.cds` → `docker start`。
- 回滚 = 拷回备份 config.yaml 与 `.cds` 并 `docker restart cli-proxy-api`，
  同时 revert 本切片提交（含 guardrails 断言回翻）。

## 验证

- 重启后 readiness 首试 `HEALTH_OK`（内存冷却清除、目录收敛）。
- generation 返回 `UPSTREAM_UNAVAILABLE`（exit 10，上游仍过载，非本地契约失败；
  更新器按既有设计 defer，不回滚）。
- 关键证明：generation 冒烟失败后 5s 复查 `auth/*.cds` 为空（`NO_CDS_OK`）
  ——旧行为下该次失败会立即落盘 `.cds`，现持久化确证关闭。
- 容器 `running`，`RestartCount=0`，`StartedAt=2026-09-16T13:08:34Z`。
- strict doctor `DOCTOR_CONTRACT_OK`；`==cooldown-state==` 输出
  `cds_files=0 / cooldown_state=none`（预期降级），`catalog_luna=absent` 为
  冒烟失败后的内存冷却+投影陈旧形态：不影响客户端路由（selection 按时间判断），
  仅使健康检查保守 defer，重启或首次成功请求即自愈。
- 全量门禁：129 passed / 1 skipped / 97 subtests，Bandit/Ruff/format/Mypy 全绿。

## 契约同步

- `scripts/cpa_bwg_guardrails.ps1`：-Apply 配置规范化断言翻转为
  `save-cooldown-status must remain false`（含理由注释）。该翻转被并行会话
  提交 7d365ad（新增 doctor `==cooldown-state==` 诊断段）一并收入，内容经本切片复核。
- `docs/runbooks/cpa-stale-cooldown-recovery.md`：机制段改写（持久化关闭为当前
  默认，`.cds` 流程仅适用历史/回退状态）；识别段补充——持久化关闭后
  `stale_cooldown_suspected` 不可达，滞留判据改为一次重启并等满一个瞬态窗口后
  仍缺席（此时转上游/本地故障）；最小诊断与恢复步骤同步。
- `README.md`：冷却陈旧段与 doctor cooldown-state 段同步新状态。
- `scripts/remote/cpa-acceptance.py` fixture 保持 `save-cooldown-status: true`
  （封闭测试环境，冷却时序测试依赖不变，刻意不镜像生产该键）。
- `request-retry: 0`、`transient-error-cooldown-seconds: 60` 维持不变。

## 并发与边界

- 本切片前约 13:02 UTC 容器曾被并行活动重启（并行会话 13:04 +08 提交 7d365ad）；
  本事务自带备份、断言与验证，独立成立，二者内容兼容。
- 恢复期间通道中断约 5 秒（stop→start），已按 runbook"人工个案"口径执行单次。
- 上游容量事件期间 luna 实际可用性仍取决于 OpenAI；裸名 sol/terra/astra（中转）、
  glm、5.5 为既有备用路由。
