# BWG CPA 503 归因与 Retry-After 观测 — 2026-09-25

## 范围

- 仅处理 `bwg`；未连接或修改 `zz`。
- 保留公网 Nginx 8443、随机路径、现有路由、重试策略、冷却时长和
  slot-3 明文 HTTP 上游例外。
- 不轮换凭据、不消费 usage queue、不为制造缓存或质量证据发送模型生成。

## 修复

- 503/5xx 归因先使用 Nginx `upstream_status` 判断上游或本地，再按
  `request_time` 分桶。此前仅按耗时会把快速的上游 503 误报为本地冷却。
- 安全 access log 增加 `Retry-After` 类别字段，只保留
  `absent`、`seconds`、`other`，不保存原始 header 值。
- doctor 增加 Retry-After map、日志字段和新归因分桶的 fail-closed 检查；
  保留旧日志格式的兼容替换路径。

## 仓库证据

- Commit: `2615775` (`修正 BWG 503 归因并增加 Retry-After 观测`)
- Full gate: `156 passed, 1 skipped, 187 subtests passed`；Bandit、Ruff、
  Ruff format、Mypy 全部通过；`git diff --check` 通过。
- 脚本内 20 段 Python heredoc 均通过 `ast.parse`。

## 远端事务

- 第一次 Apply 因现有 `cpa_safe` 日志格式未被兼容列表覆盖而 fail-closed，
  返回 `ROLLBACK_VERIFIED`；未留下半写状态。
- 修正兼容列表后第二次备份优先 Apply 成功：
  `BACKUP_DIR=/root/cpa-guardrails-backup-20260924T173650.754325542Z`、
  `READY_STATUS=200`、`GUARDRAILS_APPLIED`。
- Post-Apply strict doctor 返回 `DOCTOR_CONTRACT_OK`，并确认：
  `safe-retry-after=OK`、`retry-after-map-count=1`、Nginx syntax OK、
  公网 401/404/404 合同 OK、容器 restart=0、镜像 digest 仍为
  `sha256:ca6af8b19642b3176eaa6b6ed197cf870a9f8026d1b58693d3ff2a4d7faaedf3`。

## 观测结果与边界

- Post-Apply 24h 当前 access-log 样本：`retry_after_classes` 已出现
  `absent=7`，旧行保留为 `legacy_unknown=2795`，`unparsed_legacy_lines=0`。
- 503 全部按 `upstream_status=503` 进入上游分桶：
  `fast_upstream_lt_0_5s=138`、`mid_upstream_0_5_to_3s=82`；
  本次样本没有本地 503 分桶。两组高频 503 的重试间隔仍约为 1 秒和 2 秒，
  但该客户端重试位于 CPA 外部，当前切片只增加归因观测，没有擅自修改客户端。
- 这证明了日志归因和配置投影，不证明 provider 账户不会封禁、限流或降智；
  `cache_usage=UNAVAILABLE_NON_CONSUMING_DOCTOR` 仍是有意保持的非消费边界。

## 回滚

- 使用本次 Apply 输出的 `cpa-guardrails-backup-*` 目录，仅恢复本次事务涉及的
  Nginx/CPA 文件，再重跑同一 strict doctor。
- Git 回滚只恢复仓库源文件，不能替代远端备份恢复。

## 分层验收

- `repo_verified`: PASS
- `filesystem_projected`: PASS（Apply 备份、哈希和回滚证据完整）
- `host_loaded`: PASS（`DOCTOR_CONTRACT_OK`）
- `simulated_log_classification`: PASS（真实 doctor parser 对上游 503、本地 503、
  三类 Retry-After 和旧格式样本断言通过）
- `controlled_live_replay`: PASS（Apply 后实际公网随机路径合同探针：401/404/404，
  readiness=200；新日志已产生 `retry_after=absent`）
- `provider_generation_replay`: NOT EXECUTED（本切片为观测和归因修复；未发送生成）
- `natural_live_accepted`: NOT CLAIMED
