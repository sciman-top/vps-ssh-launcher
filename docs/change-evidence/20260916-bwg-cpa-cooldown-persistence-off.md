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

## 受控验收（同日追加，两层）

- 生产单发矩阵（6 裸名，max_tokens 512）：`glm-5.3-flash`、`gpt-5.5`
  （别名，responded_model=glm-5.3-flash 精确命中）、`gpt-5.6-sol`、
  `gpt-5.6-terra`、`gpt-6-astra` 全部 200/stop；`gpt-5.6-luna` 502
  `server_is_overloaded`（上游容量族，非本地契约）。含 1 次失败共 6 次真实
  请求后 `auth/*.cds` 仍为空（持久化关闭的实战证明）；readiness `HEALTH_OK`。
- fixture 全场景（私有 mount+net ns；容器内实际 v7.3.4 二进制 + 部署版
  updater `563b0dcb…`/health `79296e48…`；验收脚本 b64 传输哈希断言
  `6e55dec9…`/`1c35bde3…`）：`ACCEPTANCE_EXIT=0`——overload 503/upstream=1 →
  冷却窗 503/upstream+0（零放大）→ 62s 同进程恢复 200/completed、upstream+1 →
  真实 cpa-health generation `HEALTH_OK` → start_fail 与 model_exposure
  exit 1+恢复旧 compose+rollback 日志 → transient exit 10 不回滚保留新版
  （UNVERIFIED，既有 defer 设计）→ success exit 0。关闭 9/15-16 挂起的
  "v7.3.4 fixture 重跑"观察项，并作为 9-21 计时器腿（v7.3 线首个真实
  prune+update 周期）的前置验收。
- 执行注记：首跑在 62s 等待段被 SSH 工具 60s 空闲超时截断，清点确认零残留后
  以 setsid 脱离会话 + 日志轮询重跑一次完整通过；fixture 目录与日志已删除
  （`NO_FIXTURE_LEFTOVERS`），隔离命名空间随进程销毁；生产全程未受影响。
  fixture 配置的 `save-cooldown-status: true` 为封闭环境刻意保留（见上节）。
