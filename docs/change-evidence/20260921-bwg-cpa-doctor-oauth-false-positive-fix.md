# 2026-09-21 BWG CPA doctor OAuth 假阳性修复与非消费收敛

## Scope

- 仅处理 `bwg`；未连接、修改或重启 `zz`。
- 修复 strict doctor 的 OAuth 刷新监控确定性假阳性（交叉审查实证），收敛
  错误转储保留窗口与无候选更新日的账号暴露；不改变账号数量、凭据、
  provider 出口、随机公网路径、冷却时长（60s）、`request-retry=0` 或
  72h 双源成熟升级策略。

## Defect and root cause

- 错误转储（`auth/logs/error-*.log`）含明文请求正文。审查/会话文本中
  出现 `invalid_grant`、`refresh_token_reused`、`oauth … 401` 等关键词的
  请求失败后，6 个转储累计产生 36 次假命中，doctor 报
  `oauth_refresh_failures_7d=36`、`FAIL_REFRESH_SIGNAL`、
  `DOCTOR_CONTRACT_FAILED`。
- 容器真实日志 168h 内刷新错误为 0；OAuth 剩余约 175h；readiness 正常。
- 同源污染还影响 overload 段（正文引用的历史时间戳与假 marker）。
- 附带发现并修复容器扫描正则的 raw-string 转义错误（`[^\\n]`、`\\b`）。

## Repository and projection

- 源提交：`074e087`（修复 CPA doctor OAuth 假阳性并收敛非必要账号暴露）。
- Full gate：`131 passed, 1 skipped, 170 subtests`；Bandit、Ruff、format、
  Mypy 通过。
- `-Apply` 事务：备份 `/root/cpa-guardrails-backup-20260921T053735.854228777Z`，
  `READY_STATUS=200`，`GUARDRAILS_APPLIED`；`auto-update.sh` 投影 SHA
  `cc1bf733…`，其余文件哈希不变。

## Fresh host readback

- Fresh strict doctor：`DOCTOR_CONTRACT_OK`；`oauth_refresh_failures_7d=0`、
  `oauth_monitor=OK`、`oauth_refresh_coverage=…incomplete_bounded_sample`。
- 6 个原污染转储的 `time_as_logged` 恢复为 REQUEST INFO 真实时间
  （13:10:54–13:11:02 +08）；overload 假 marker 18→12（正文命中已排除）。
- 新观测：`auth_unavailable_retained_sample_count=10`
  （codex/gpt-5.6-luna=6，openai-compatible-ai.input.im/gpt-5.6-terra=4），
  `coverage=incomplete_bounded_error_dumps`。
- 转储保留：updater prune 7d→48h（CPA 自身另受 `error-logs-max-files: 10`
  约束）。

## Controlled live acceptance

- 新无候选更新分支实弹（`systemctl start cliproxyapi-update.service`）：
  `Result=success`、`REFRESH_SIGNALS_24H=0 non_consuming=true`、
  `OK: no newer mature release; current=v7.3.7 readiness verified`；
  零 generation、零 UNVERIFIED。
- 消费型 usage 队列一次性瞬时样本（通道分桶，`pops_records`，
  `retention<=1h`）：`openai-compatible-ai.input.im/gpt-5.6-sol` 8 请求
  `hit_ratio=0.9745`；codex/luna 5 请求无 cache 字段回报；deepseek 1 请求
  36 input（低于缓存阈值）。首次取样因客户端脚本解析错误作废。
- 并发估算（仅观察）：24h 总在途 1.65h ⇒ 平均在途 ≈0.07；完成时刻聚簇
  不证明同时在途，真实峰值不可由完成日志精确得出；现 nginx per-IP 并发 6
  为唯一硬顶。

## Evidence boundary and rollback

- 证明：`repo_verified -> filesystem_projected -> host_loaded ->
  controlled_live_accepted`（doctor 复绿 + updater 分支实弹）。
- 未证明：长期缓存平均命中率、账号免风控/限流/降智；v7.3.8+ 换模观测仍
  待镜像按成熟期升级后激活。
- 回滚：恢复备份目录内文件后重启 CPA；仓库侧 `git revert 074e087` 后重新
  执行同一 Apply 事务。Git 回滚不替代 VPS 备份恢复。
