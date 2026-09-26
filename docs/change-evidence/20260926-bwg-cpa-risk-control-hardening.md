# BWG CPA 风控门禁投影验收（2026-09-26）

## Scope

- Target: BWG only. ZZ 未访问、未改动。
- 请求变更：把 CPA 账号/限流/降智风控审查发现的 P1 契约缺口落地并投影——
  429 节流状态回归契约强制、doctor 限流/目录 fail-closed、config 权限门禁、
  `usage-statistics-enabled` 纳入 policy 强制、OAuth 静默期硬开关。
- 本仓提交：`7dfdf88`（本地 guardrails + 远端 `cpa-health.py` / `cpa_policy.py`
  + `test_scripts.py` + runbook/README）。

## Timeline

- **S0 提交。** `7dfdf88`；核对 `cpa-health.py` / `cpa_policy.py` 的 HEAD blob 与
  工作区 SHA 逐字节一致（`projection-drift` 比对 HEAD，未提交会造成投影后自相矛盾
  的 drift 失败，故必须先提交）。
- **S1 只读 strict doctor。** 新增契约项在远端全部生效：
  `gateway-per-ip-rate-limit=OK`、`gateway-throttle-status=429`、
  `config-permissions=owner-only`（远端 `config.yaml` 为 `600 root root`）、
  `MODEL_IDS_UNKNOWN=none`（目录 fail-closed 未误报）。`projection-drift` 在
  `cpa-health.py`（want `4cf728c3…` got `a0b23c82…`）与 `cpa_policy.py`
  （want `3a64df9b…` got `f379caa4…`）报 MISMATCH，其余四项 MATCH；
  `DOCTOR_CONTRACT_FAILED`（exit 1）——正是"待投影"信号。
- **S2 `-Apply` 投影。** 六个文件全部 `PROJECTION_HASH_VERIFIED`：fail2ban
  filter/jail、`auto-update.sh`、`cpa-health.py`、`cpa_provider_routes.json`、
  `cpa_policy.py`。备份 `BACKUP_DIR=/root/cpa-guardrails-backup-20260926T002854Z`
  已建立；`READY_STATUS=200`；`GUARDRAILS_APPLIED`（exit 0）。重载 nginx 时出现
  一次瞬时 `curl (56) Recv failure`，随后 `HEALTH_OK`，且 `/etc/nginx/conf.d/`
  SHA 未变（新 `ensure_nginx_directive` 对已含 429 的配置幂等）。
- **S3 doctor 复验。** `DOCTOR_CONTRACT_OK`（exit 0）；`projection-drift` 六项全
  MATCH；新契约项仍全 OK；容器 `v7.3.17@sha256:a1dffb9c…` `restart=0`，
  `luna_state=available`，`oauth_monitor=OK`，`cooldown_state=none`。

## Verification boundary

- 远端 `cpa-health.py` / `cpa_policy.py` 与 HEAD blob SHA 相同，部署字节即受测字节。
- doctor 对部署版 `cpa_policy.py` + 线上 `config.yaml` 跑出 `POLICY_OK` 与
  `semantic-policy=OK`（`usage-statistics-enabled: true` 已满足新强制项）。
- `CPA_HEALTH_NO_OAUTH` 为附加环境变量守卫，默认关闭、`readiness` 路径无副作用；
  其抑制逻辑由单测 `test_cpa_health_no_oauth_suppresses_oauth_routes` 覆盖，
  且部署文件与测试字节一致。
- **模拟验收（unshare fixture）本轮不适用**：该 harness 不 stage
  `cpa_policy.py`，且 `cpa-health.py` 新守卫默认关闭 → 行为与 2026-09-25 已验收的
  v7.3.17 基线一致，重跑不会新增覆盖。
- 未生成 OAuth lane 自动化流量（保持静默纪律）。

## Hygiene

- apply 前备份保留在 `BACKUP_DIR`，未删除。
- 本轮无事故、无 rollback 触发。

## Residual watch

- OAuth 刷新点 `oauth_days_left=9`（`oauth_hours_left=239.1`），阈值不变。
- 已知限制（上游 `Retry-After` 不参与冷却、无聚合/账号级闸门、fail2ban 24h 自 ban
  风险、降智检测为 reactive）已在
  `docs/runbooks/cpa-ban-throttle-incident-response.md` 记录，本轮未解决。
