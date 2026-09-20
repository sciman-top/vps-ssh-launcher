# 2026-09-20 BWG CPA OAuth 与观测收口

## Scope

- 仅处理 `bwg`；未连接或修改 `zz`，未推送 Git。
- 保留 CPA `127.0.0.1:8317`、公网 Nginx `0.0.0.0:8443` 和既有随机路径。
- 未修改 OAuth 凭据内容、账号数量、客户端密钥、provider 路由、出口、UA
  或 TLS/header 覆写。
- 本次投影对应 OAuth 刷新门禁、错误转储统计、静默模型替换能力判定和
  Nginx 499 `request_time` 观测；未执行新的 provider generation 或质量探针。

## Source and verification

- OAuth 调度说明与门禁修正：`34f0f3b`。
- 错误转储路径统计与模型替换版本能力修正：`3dead96`。
- 499 客户端中断时长统计：`308a658`（写入本证据前的实现基线，且当时与
  `origin/main` 一致）。
- Full gate：`131 passed, 1 skipped, 163 subtests`；Bandit、Ruff、
  Ruff format 和 Mypy 均通过。受影响 focused suite：
  `44 passed, 129 subtests`。
- 两次 backup-first `-Profile bwg -Apply` 均在短暂重启窗口后恢复
  `READY_STATUS=200` 并报告 `GUARDRAILS_APPLIED`。最新备份为
  `/root/cpa-guardrails-backup-20260920T120338.713176706Z`。

## Host readback

- Fresh strict doctor：`DOCTOR_CONTRACT_OK`；容器 `running`、restart=0，
  五模型目录完整，公网随机路径的 401/404/404 合同保持不变。
- 风控配置保持 `request-retry=0`、`max-retry-credentials=1`、
  `save-cooldown-status=false`、`session-affinity-subagents=false`。
- OAuth：`oauth_hours_left=192.6`、`oauth_refresh_failures_7d=0`、
  `oauth_monitor=OK`。门禁现按精确小时判级：`>72h` 为 OK，
  `22h < remaining <= 72h` 为非阻断 WARN，`<=22h` 为 FAIL；
  `invalid_grant`、`refresh_token_reused` 或 OAuth 401 信号立即 FAIL。
  该阈值匹配到期前 24 小时的刷新提前量，并保留 2 小时调度宽限。
- 错误转储修复后按真实路径逐文件统计：10 个扫描文件中仅 2 个保留的
  overload request 文件、4 个 marker 和 2 个 OAuth upstream 事件。修复前
  输出的 10 个同时间事件来自循环复用残留路径，不是真实十次事件。
- 当前运行版 `v7.3.7` 不具备上游静默模型替换告警能力，因此 doctor
  正确报告 `model_substitution_warnings_7d=unavailable` 和
  `model_substitution=UNAVAILABLE_VERSION`，不再误报为 OK。
- Nginx 499 样本：count=198、min=6.364s、p50=45.044s、max=45.051s。
  紧密的约 45 秒聚类证明存在固定客户端 total timeout，但现有脱敏服务端
  日志不能把请求归因到某个客户端。

## ZCode boundary

- 只读检查 ZCode 的 `config.json`、`setting.json` 和
  `provider_config.json`，未发现受支持的 timeout、concurrency、parallel
  或 retry 配置键；未读取凭据文件，未修改文件，也未停止或重启 ZCode。
- ZCode 当日日志中的两个明确 `AbortError` 属于 15 秒 usage quota /
  marketing API 请求，不是 CPA generation 数据面。当前证据无法将 BWG
  的约 45 秒 499 样本归因给 ZCode，因此没有可安全执行的客户端修复；
  不猜测隐藏配置，也不修改安装包。

## Evidence boundary and rollback

- `repo_verified=yes`、`filesystem_projected=yes`、`host_loaded=yes`，且 strict
  doctor 已通过。因本切片未新增 generation / quality probe，不建立新的
  `live_accepted` 或模型质量结论。
- VPS 回滚使用最新 `/root/cpa-guardrails-backup-*` 恢复本次投影文件并重新
  执行 strict doctor；仓库回滚使用对应提交的 `git revert` 后再投影。Git
  回滚不能替代 VPS 备份恢复。
