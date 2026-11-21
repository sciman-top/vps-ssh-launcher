# BWG Admission 镜像冷却半开探测（mirror probe，2026-11-22）

## 依据与行为

- 北京时间 01:13:39 的单发 Antigravity `429`（`Retry-After: 17504s`）把 `antigravity-oauth` 共享车道整体冻结至 06:05:24。根因是结构性的：strict 车道（`STRICT_OAUTH_LANES`）在 `acquire()` 的 early-probe 分支被 `not self._strict_backoff` 一票否决，`record_risk`/`release` 又把 `_next_probe_at` 钉在 `open_until`，因此上游带 `Retry-After` 的镜像窗口在任何车道上都不会被提前验证，strict 车道连 ladder 冷却也不探测。
- `3af4ba3`（额度组冷却）解决了该 429 的分类路径：可归组的 429 只停对应额度组，不再全道冻结。但 lane 级镜像窗口（503、无法归组的 429）仍是盲窗。本代（`28266b4`）补上这一缺口：车道可通过 lane 配置键 `mirror_probe_interval_seconds` 启用半开探测——镜像冷却期内每个间隔放行**一个**由真实需求携带的 reset probe，成功立即清窗，失败重新镜像上游窗口并重新 arm 节奏，上游请求速率始终有界；冷却拒绝信封改为指向下一个 recheck slot（`_shed_retry_after` 既有契约"广告车道实际重估时刻"的推广），使客户端重试骑上探测。hold（运营审查）永不探测：hold 门在探测分支之前返回；未配置该键的车道保持历史盲窗语义，重启后首个完整间隔内不探测。
- 与 `3af4ba3` 的合并为互补而非重叠：额度组门（`quota_group_cooldown`）位于 `acquire()` 中 lane 冷却块之前，探测分支只在该模型额度组未停时可达；`acquire_after_reset` 维持 strict 阻断，两个机制不会对上游双发。
- 信封契约冲突的裁决：`test_antigravity_provider_quota_window_survives_restart` 的 lane 侧参数原本断言拒绝信封携带完整窗口剩余；镜像探测启用后"车道下次重估时刻"变为 recheck slot，测试更新为断言信封 `600` 且内部 `cooldown_remaining` 仍保留完整窗口（重启持久意图不丢）。

## 模拟、投影与受控验收

- lane 配置键校验（类型/下限 30s）、探测成功清窗、失败重镜像+重 arm、禁用键保持盲窗、队列头骑探测槽、hold 不探测、重启 arm 节奏等 7 项新单测，连同既有 333 项通过；合并树 Full 门禁重跑通过（1,840 passed / 30 skipped / 896 subtests，catalog/bandit/ruff/format/mypy 全绿）。
- 部署事务 `outputs/deploy-admission-mirror-probe-20261122.ps1` 在 maintenance lock + request-drain lock 下执行：staged 源 `compile()` + 以合并模块现场装载 staged 配置（`STAGED_CONFIG_LOAD_OK`）、旧 pin/配置/routes/bootstrap 基线逐一核对、双锁排空、备份先行、四件（admission 源码、lane JSON、request guard、pin）同事务原子换、失败自动回滚并复验完整性。`-DryRun` 先行通过（`DRY_RUN_OK`），事务返回 `DEPLOY_OK`，pin `9569639e…` → `87ffa289…`，lane JSON `fb054ae6…` → `77b5f465…`（`antigravity-oauth` 增 `mirror_probe_interval_seconds: 600`，其余车道不变）。
- 事务内置 `MIRROR_PROBE_LIVE_OK`：重启后 `/healthz` 实证 `antigravity-oauth` `mirror_probe_interval=600`。strict doctor 全 MATCH（`cpa-admission.py`、`cpa-admission.json`、`cpa_request_guard.py` 等 11 项投影零漂移）、`admission-integrity=OK`、`runtime_binary_hash=MATCH`。
- 实弹验收：`gemini-3.8-flash-high` 与 `gemini-4-argon-high` 各一发 loopback `/v1/responses` 均 HTTP 200；额度组窗（`claude-gpt` 组，截止仍为 06:05:24）由 `3af4ba3` 机制维持，claude 系模型按组窗恢复。镜像探测的清窗路径由单测覆盖，本次窗内无可安全触发的 lane 级 503/未分类 429，未做实弹触发。

## 回滚

- 备份目录 `/opt/cliproxyapi/backups/20261121T183431.840524094Z-admission-mirror-probe-20261122`（含 admission 源码、lane JSON、request guard、pin）。事务失败自动恢复该组备份并重启校验。
- 降级为删去 lane JSON 中 `mirror_probe_interval_seconds` 并恢复部署事务备份的源码/pin/guard 组合即可；风险状态文件无需改动（mirror probe 不引入新的持久化字段，`hold_reason`/`not_before`/`quota_group_not_before` 语义不变）。
