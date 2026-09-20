# 2026-09-21 BWG CPA 风控与缓存优化收口

## Scope

- 仅处理 `bwg`；未连接、修改或重启 `zz`。
- 通过现有 `cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 的 backup-first
  事务投影策略契约、健康探针、OAuth 监护和非消费型 doctor 行为。
- 未修改账号数量、OAuth 凭据内容、provider 出口、随机公网路径或入口限额。

## Repository and projection

- 本地提交：`a128b0b`（同步 BWG 裸名路由与 Astra 健康契约）。
- Full gate：`131 passed, 1 skipped, 164 subtests`；Bandit、Ruff、format、Mypy
  均通过。
- 远端文件 SHA-256 与本地源一致：
  `cpa-health.py=242eceef…`、`cpa_policy.py=0961e1a7…`、
  `cpa-auto-update.sh=21083f55…`。
- 投影事务期间第一次策略收紧因现有 Astra 路由不匹配而 fail-closed 并回滚；
  将 Astra 纳入当前已授权 provider 映射后重新投影成功，未留下半应用状态。

## Fresh host readback

- CPA image：`v7.3.7`，digest 固定，container `running`，`restart=0`。
- CPA 保持 loopback `127.0.0.1:8317`；公网仍为一个 Nginx `8443` 随机路径；
  401/404/404 路由合同通过；管理面为 keyed-loopback。
- `request-retry=0`、`max-retry-credentials=1`、`session-affinity-subagents=false`。
- OAuth：一个活动 Codex 文件，约 181 小时剩余，`oauth_refresh_failures_7d=0`，
  `oauth_monitor=OK`。
- doctor 默认报告 `cache_usage=UNAVAILABLE_NON_CONSUMING_DOCTOR`，不会消费
  `usage-queue`；显式消费仍需设置 `CPA_DOCTOR_CONSUME_USAGE_QUEUE=1`。
- readiness：`HEALTH_OK`。

## Controlled live acceptance

- `generation-all`：Luna、Sol、Terra、Astra、GLM 和 DeepSeek 各单次请求，均
  `status=200`、`finish=stop`，最终 `HEALTH_OK`；无重试。
- `cache-canary`：两次同一非敏感长前缀样本均为
  `input_tokens=3875`、`cache_read_tokens=3712`、`cache_miss_tokens=163`、
  `hit_ratio=0.9579`、`HEALTH_OK`。`cache_write_tokens` 未返回，因此不宣称
  写入成本或长期业务平均收益。
- 探针锁按设计阻止并发探针；一次生成探针在输出完整结果后因 HTTP keep-alive
  未退出而持有锁，已只终止该探针进程并确认 CPA container 仍 `running restart=0`。

## Evidence boundary and rollback

- 本次证明：`repo_verified -> filesystem_projected -> host_loaded -> controlled_live_accepted`。
- 本次未证明：长期 provider 质量、长期缓存平均命中率、账号不会封禁/限流/降智，
  也未执行质量评测或自然用户流量验收。
- VPS 回滚使用本次 Apply 事务生成的 `/root/cpa-guardrails-backup-*`；仓库回滚
  使用 `git revert a128b0b` 后重新执行同一 Apply 事务。Git 回滚不替代 VPS 备份恢复。
