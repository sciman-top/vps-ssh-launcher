# BWG CPA 单请求凭据上限收紧（2026-11-06）

## 目标

降低同一个客户端请求在 WorkBuddy consumer OAuth 账号之间扇出造成的
6004 放大、账号暴露和上游请求放大风险。保留现有 WorkBuddy CN/INTL、
ChatGPT/GPT、Antigravity/Gemini、API key 和明文 HTTP slot 3；本次不轮换、
删除、隔离或重新登录任何凭据。

## 触发证据与策略

- 旧策略：`routing.retry.request-retry=0`、
  `routing.retry.max-retry-credentials=0`。后者允许当前混合 provider round
  遍历所有合格凭据。
- 触发样本：脱敏 request `4e0cc917` 的真实 WorkBuddy `deepseek-v4.1-flash`
  6004 事件有 2 条、对应 2 个不同 UID（记录中以 `<uid-A>/<uid-B>` 代称）。
- 新策略：保持 `request-retry=0`，将 `max-retry-credentials` 改为 `1`。
  单个客户端请求最多触达一个上游凭据；后续账号选择只能发生在新的独立请求。
- 代价：请求遇到 6004/容量错误时不再在同一请求内继续遍历其他 WorkBuddy
  OAuth 账号或 OpenCode；调用方下一次明确请求或用户选定的 API-key 路由承担
  可用性降级。没有清冷却、增加重试或制造探针流量。

## 远端事务

- 入口：`scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply`，使用维护锁和
  backup-first `cpa-guardrail-apply.sh`。
- 结果：`GUARDRAILS_APPLIED`、`READY_STATUS=200`、`EXIT_CODE=0`。
- 备份/回滚入口：
  `/root/cpa-guardrails-backup-20261105T181410.584088845Z`，按
  [CPA 手动回滚 runbook](../runbooks/cpa-manual-rollback.md) 选择本次备份，
  恢复受影响的投影文件后重启并重新运行 strict doctor。
- 事务没有写入 auth 目录；备份目录没有 `auth`。`compose.yml` 前后 SHA-256
  均为 `3c9758a19b4018a44ac16497f1cfa9f84cf20a16b56511ac6086f0b515565e17`，
  Nginx gateway 配置前后 SHA-256 均为
  `45a2c7c0bd6e7a35e81aa4c12e3ee0f87245b3504caa87a4c64f8b503cfef416`。

## 分层验证

### `repo_verified`

- 更新 policy、apply、acceptance fixture、审计文案及回归测试。
- `461 passed, 135 subtests passed`（受影响 CPA 测试集合）。
- `git diff --check` 通过。
- 审计文案明确说明 pooled alias 仍可见，但单请求 cap 为 1，不再暗示当前
  请求会自动 failover。

### `filesystem_projected`

- apply 输出全部受管文件 `PROJECTION_HASH_VERIFIED`；strict doctor 的
  projection drift 全部 `MATCH`。
- CPA 镜像仍为 `v8.0.55`，WorkBuddy 插件仍为 `0.9.87`；本次没有更换插件
  或镜像。

### `host_loaded`

- strict doctor：`DOCTOR_CONTRACT_OK`、`EXIT_CODE=0`。
- 生效配置：`request-retry=0`、`max-retry-credentials=1`、
  `session-affinity=true`、`session-affinity-rebind-on=quota`、
  `session-affinity-subagents=false`。
- 8317/8318 仍为 loopback-only，Nginx 8443 为唯一公网入口，readiness 为
  `200`，WorkBuddy `scheduler_mode=credits`、`account_max_concurrent=1`、
  `identity_switch_on_429=false`，三条 OAuth lane 均 `OPEN`。
- Claude 仍为 `RETIRED no_oauth_route`；slot 3 明文 HTTP 仍按用户指定的
  上游边界保留并仅报告警告。

### `live_accepted`（有界观察）

- `cpa_natural_traffic_audit.py --ssh-profile bwg --hours 24 --json` 返回
  `EXIT_CODE=0`，聚合 `4246` 条 admission 结果。WorkBuddy
  `deepseek-v4.1-flash` 为 `2355` 请求、`2346` 完成、`1` capacity、`3`
  upstream error，缓存命中约 `98.7%`；first-text p50/p90 约
  `4.47s/11.00s`，慢点主要归因于 upstream/queue，decode p50/p90 为 `0/0ms`。
- ChatGPT `gpt-6-luna` 缓存命中约 `97.4%`；`gpt-5.6-luna` 约 `4.5%`，审计
  仅给出 prompt-prefix/session-affinity 检查提示，未启用跨账号或跨模型缓存
  key。低命中模型的 first-text p50/p90 约 `9.07s/13.11s`，属于上游/模型
  形态问题，不能用本地缓存开关安全掩盖。
- 从 apply 备份时刻 `2026-11-05T18:14:10Z` 起，用现有 UID coordinate
  只读归因逻辑扫描容器日志，返回 `FAILURE_REQUESTS=0`。该结果只证明这个
  窗口没有再次出现同请求多 UID 的真实 6004，不证明未来请求、账号状态、
  provider quota 或长期封禁风险。

## 未覆盖边界

自然流量和 strict doctor 不能证明供应商长期不封号、额度独立性、模型质量、
中国网络稳定性或明文 HTTP 传输安全。没有执行 OAuth 登录、账号删除、凭据轮换、
冷却清除、跨 realm 探测或额外重试；Claude 也没有被接入。后续如需改变单请求
可用性/回退边界，应先重新取得对应生产证据，再单独评审 `max-retry-credentials`
与 provider lane 的契约。
