# 2026-09-15 BWG CPA 审查修复收口（日志轮转/死凭据/nginx 缓冲/契约锚点）

全面审查（仓库 + 真机只读探针 + v7.2.158 上游源码核对）后，按用户授权连续执行
修复与优化。本次只处理 `bwg`，未连接或修改 `zz`；全程未输出 key、随机路径或
任何凭据材料。

## 前置：并行切片收口

执行期间发现工作树存在另一会话的已完成切片（health 五路由生成冒烟、guardrails
health 投影、google/vasma apply 事务化，含真机 15:37Z `-Apply` 验收证据）。按
9-14 先例复核其 diff、全量门禁通过后作为独立提交 `73658fb` 收口；本切片在其
干净的基线上实施。

## 变更（远端，均在备份/回滚边界内）

- `compose.yml`：为 `cli-proxy-api` 增加 `logging: json-file` 轮转
  （`max-size=32m`、`max-file=3`）。上游镜像与官方 compose 均无有界轮转，
  此前容器 stdout 日志无上限。
- `config.yaml`：删除两个 `excluded-models: ['*']` 死凭据条目
  （r2/codex.ciii.club 与重复的 r1 条目；此前注册 0 模型的 kill-switch 残留）。
  语义断言保证差异仅为这两个条目；删除后目录契约不变（17 = 6 裸名 + 11 `r1/*`，
  无 `r2/*`），减少两份 key 落盘。
- `/etc/nginx/conf.d/cpa-gateway.conf`：新增 `client_body_buffer_size 128k;`，
  消除 LLM 大请求体在默认 8k/16k 缓冲下反复落盘的 warn 噪声。

## 变更（仓库）

- guardrails doctor 新增两个严格契约检查：`container-log-rotation`（docker
  inspect 的 LogConfig 含 `max-size:32m`）与 `client-body-buffer`（Nginx conf
  含 `client_body_buffer_size 128k;`）；apply 的 required/回滚锚点同步加入
  buffer 指令，apply 内嵌 python 补充 safe_dump 不保留注释的边界注释。
- `scripts/remote/cpa-auto-update.sh`：删除未被读取的 `RETENTION_KEEP_IMAGES=2`
  死变量（保留策略实际为"当前+回滚镜像"硬编码），测试锚点同步。
- README：传输契约段落补 `client_body_buffer_size 128k` 与容器日志轮转说明；
  新增 `excluded-models` 字段边界（仅命名凭据条目生效，`openai-compatibility`
  无此字段、静默忽略——v7.2.158 源码核实）。
- runbook `cpa-stale-cooldown-recovery.md`：补充源码级机制（非配额瞬态冷却
  投影无过期时间戳、事件驱动重投影，v7.3.4 未修复）与"启动后一个
  transient-error-cooldown 窗口内的目录缺席不属本页处理范围"。

## 过程与一次设计内回滚

- 第一次远端批次在目录断言处失败并按 `restore_all` 完整回滚
  （`ROLLBACK_VERIFIED`）：容器重建窗口内 luna 因一次真实的
  `server_is_overloaded` 503 进入标准 60 秒瞬态冷却（`.cds` 记录
  `next_retry_after=15:54:01Z` 自愈），与断言时点重叠。批次随后改为
  最长 96 秒的目录收敛等待后重跑成功；该行为已写入 runbook 注意事项。
- 第二次批次输出 `REVIEW_CLOSEOUT_APPLIED`：`CATALOG_OK total=17 bare=6
  r1=11 r2=0`、`log_config=map[max-file:3 max-size:32m]`、路由探针
  401/404、readiness `HEALTH_OK`。

## 验证

- 远端最终 strict doctor：`DOCTOR_CONTRACT_OK`，含两个新检查
  `container-log-rotation=OK`、`client-body-buffer=OK`，无任何 FAIL。
- 投影后远端 `auto-update.sh` sha256
  `84b278571e7fe52bd20c830355b592d4ea67b36abe822520be09e13a9dfd19c4`
  与本地源一致（前值 `f4682077…`）；`bash -n` 通过；真机 `--check`
  exit 0（`CANDIDATE current=v7.2.158 target=v7.2.158`、备份健康 ok）。
- 文件 sha256（投影后）：config `e8d8e9fb…`、compose `b9c34246…`、
  nginx `a2853703…`。
- 本地 full gate：`126 passed, 1 skipped, 79 subtests passed`；Bandit、
  Ruff check/format、mypy 通过；`git diff --check` 通过。
- 24h access log 窗口：`200=847、503=620、502=47`（全部 upstream 侧同值，
  limit 标记未触发）。r1 恢复后成功占比回升，但中转站仍有间歇 503——
  维持"24-48h 稳定窗口后复查"的观察项，不作为任何账号状态结论。

## 备份与回滚

- `/root/cpa-review-closeout-20260915T155828Z/`（config.yaml、compose.yml、
  cpa-gateway.conf；回滚 = 拷回 + `docker compose up -d --pull never` +
  `nginx -t` 后 reload）。
- `/root/cpa-updater-deadvar-20260915T160715Z/auto-update.sh`（前值
  `f4682077…`）。
- 更早一次失败批次的完整备份：`/root/cpa-review-closeout-20260915T155339Z/`。

## 残余风险与边界

- CPA `error-*.log` 仍会保留完整下游请求头（含客户端 key）与正文（上游
  行为，无配置开关）；现有边界：10 文件上限 + `logs-max-total-size-mb: 32`、
  仅 loopback 可触发、root-only。知情保留，未新建治理面。
- 容器仍以 root 运行（上游镜像无 USER），记录为上游继承面，未本地分叉。
- `auto-update.log` 无轮转（周增约 8KiB，量级无害，接受）。
- fixture 受控验收维持既有计划：下次二进制/更新受控验收时按当前脚本重建。
- 9-21 timer 腿将是新 updater（`84b27857…`）与新五路由生成冒烟的首个真机
  组合验证点。
