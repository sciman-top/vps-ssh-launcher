# 2026-09-24 BWG CPA usage telemetry and route observability hardening

## Scope

- 仅处理 BWG 的 CPA 运维真源和远端投影；未触碰 ZZ、OAuth 文件内容、随机公网路径值或 provider 路由映射。
- 目标是降低 usage queue 原始记录暴露风险，并让 499/502/503 能按脱敏 route class 归因。
- 不新增 provider 重试；继续保持 `request-retry=0`、`max-retry-credentials=1`、60 秒 transient cooldown 和 loopback CPA。

## Repository verification

- `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1`
- 结果：`153 passed, 1 skipped, 180 subtests passed`；Bandit、Ruff、format、mypy 均通过；仅已有 `B507 nosec` warning。
- `git diff --check` 通过。
- 本地消费开关调整后，CPA guardrail focused test 再次通过；strict doctor 默认仍不消费队列。

## Changes

1. `cpa-health.py` 的 usage queue 观察现在同时要求：
   - 本地 `-ConsumeUsageQueue`
   - 本地 `-AcknowledgeUsageQueueConsumption`
   - 只允许用于默认 strict doctor，两个开关必须同时提供。
2. 原始 queue 响应在单个 Python 进程中抓取、限长、归约；不进入 shell 变量、命令行或 doctor 输出。
3. loopback management 请求禁用环境代理，确保 management key 和原始响应不经 ambient proxy。
4. Nginx 安全日志增加脱敏 `route=$cpa_route_class`，只分类为 `models`、`chat`、`responses`、`other`，不记录随机公网路径。
5. doctor 兼容旧日志并将旧格式计为 `legacy_unknown`。
6. README 明确当前运行版本以 fresh host readback 为准，并补充 usage queue 的 destructive 语义。

## Host projection and acceptance

- 预投影 strict doctor：`DOCTOR_CONTRACT_OK`。
- 备份优先 Apply 成功；远端备份目录：`/root/cpa-guardrails-backup-20260923T155912.562292930Z`。
- Apply 后 readiness：`200`；容器 `running`、`restart=0`。
- Apply 后 strict doctor：`DOCTOR_CONTRACT_OK`。
- Apply 后关键结果：`safe-route-class=OK`、Nginx merged route contract 通过、随机路径语义 `401/404/404`、管理面仍为 `LOOPBACK_KEYED`、OAuth refresh failures 为 `0`。
- 新日志已经出现 `route_classes.models` / `route_classes.other`；历史行标为 `legacy_unknown`。
- 重启后的 DeepSeek cache canary 两次均为 `input_tokens=3875`、`cache_read_tokens=3712`、`cache_miss_tokens=163`、`hit_ratio=0.9579`、`HEALTH_OK`。

## Rollback

- 仅回滚本次远端备份中的 `cpa-gateway.conf`、projected health/policy/route files 和 config transaction；使用现有 guardrail restore path。
- Git 回滚不能替代远端恢复；远端回滚前仍需 readiness、Nginx route contract 和 strict doctor 复验。

## Boundary

- 本 receipt 证明 `repo_verified -> filesystem_projected -> host_loaded -> controlled_live_accepted` 的本次切片。
- 不证明自然用户验收、长期 provider 质量、长期缓存平均值、账号不会被限流或封禁。
