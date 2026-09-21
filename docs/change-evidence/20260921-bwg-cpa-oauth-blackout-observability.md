# 2026-09-21 BWG CPA OAuth 与冷却黑洞观测修复

## Scope

- 仅处理 `bwg`；未连接或修改 `zz`，未轮换账号、OAuth、client/provider key
  或公网随机路径。
- 投影 OAuth 刷新信号去污染、`auth_unavailable` 有界样本观测、48 小时错误
  转储保留、无候选更新零 generation 和 provider/model 缓存分桶。

## Repository verification

- 实现提交：`074e087`；README 契约同步：`ac4e764`。
- Focused：`44 passed, 136 subtests`。
- Full gate：`131 passed, 1 skipped, 170 subtests`；Bandit、Ruff、format、
  Mypy 均通过。

## Projection and host readback

- Backup-first apply：`GUARDRAILS_APPLIED`；备份目录
  `/root/cpa-guardrails-backup-20260921T054249.846733382Z`。
- Apply 的短暂容器重建窗口出现一次预期 connection reset，随后
  `READY_STATUS=200`。
- 远端与本地版本源 SHA-256 一致：
  `auto-update.sh=cc1bf733...`、`cpa-health.py=242eceef...`、
  `cpa_policy.py=0961e1a7...`。
- Fresh strict doctor：`DOCTOR_CONTRACT_OK`；OAuth 剩余约 174.9 小时，
  `oauth_refresh_failures_7d=0`、`oauth_monitor=OK`。先前由请求正文关键词触发的
  `60/FAIL_REFRESH_SIGNAL` 假阳性已消失。
- 保留错误转储样本显示 `auth_unavailable_retained_sample_count=10`：
  `codex/gpt-5.6-luna=6`、`openai-compatible-ai.input.im/gpt-5.6-terra=4`；
  覆盖明确为 CPA 最新有界转储，不是完整 24 小时或 7 天计数。
- 手动执行实际无候选 updater 路径返回 `HEALTH_OK`、
  `REFRESH_SIGNALS_24H=0 non_consuming=true`、exit 0；未发送 generation。

## Controlled live acceptance

- 投影后仅执行一次 `generation-all`；共享探针锁防止并发，每条暴露路由只发送
  一个固定 `OK` 请求且不重试。
- 六条路由均返回 `status=200`、`finish=stop`：Luna 13.244s、Sol 40.661s、
  Terra 58.082s、Astra 47.118s、GLM 2.066s、DeepSeek 0.657s；矩阵最终
  `HEALTH_OK`。
- 探针后的 fresh strict doctor 再次 `DOCTOR_CONTRACT_OK`：OAuth
  `oauth_refresh_failures_7d=0` / `oauth_monitor=OK`，容器 `running`、
  `restart=0`，没有新增持久或内存冷却证据。
- 本次改动不触及模型内容转换，`generation-all` 已覆盖投影后的真实路由、鉴权、
  provider 返回和完整结束语义；未追加 `quality-canary`，避免无独立失败模式的六次
  额外账号请求。

## Cache observation boundary

- 第一次消费型收集已触达 management endpoint，但收集器错误地让 Python heredoc
  占用 stdin，弹出的原队列样本未能解析且不可恢复；未为补样发送 provider 请求。
- 修正收集器后仅观察到失败收集之后的 7 条自然流量：Luna 5 条无 cache token，
  DeepSeek 1 条短样本无命中，Sol 1 条为 `183680/184820`，约 99.38%。
- 该样本只证明按 provider/model lane 聚合可工作，不证明长期缓存平均收益，也不
  支持据此改变 `support-prompt-cache-key=false`。

## Rollback and boundary

- 远端回滚使用上述 `/root/cpa-guardrails-backup-*` 恢复并重新运行 strict doctor；
  Git 回滚不能代替远端恢复。
- 本次证明 `repo_verified -> filesystem_projected -> host_loaded ->
  controlled_live_accepted`，以及实际无候选 updater 的非消费行为；未建立长期
  provider 质量、账号免风控或自然用户业务验收结论。
