# 2026-11-03 CPA 深度审查修复投影（bwg 单机）

## 范围

- 代码基线：`4fd4188`（包含 `3a08bba`、`9f542e7`、`4fd4188`）。
- 唯一生产写入：bwg CPA guardrail `-Apply`；未操作 zz。
- 投影前保持现有 OAuth、API key、DeepSeek `oc -> INTL -> CN` 路由和凭据不变。
- `-Apply` 前四个 admission lane 的 `inflight/pending` 均为零。

## 变更与判据

- 修复 SSH 输出 EOF/退出状态 drain、多端口 host key、私有目标配置 ACL 和
  私有 known_hosts 权限。
- 加固维护备份、回滚、deferred/busy 边界、证据脱敏和 Windows 管道参数。
- 投影 `cpa-auto-update.sh`、`cpa_policy.py`、doctor/guardrail 的已审查版本；
  不改变现有路由语义。
- 本地全量门禁：`1047 passed, 18 skipped, 512 subtests passed`；Bandit、Ruff、
  Mypy 全部通过。
- 真实 SSH integration：`3 passed`。
- 投影事务：`GUARDRAILS_APPLIED`。
- 严格 doctor：`DOCTOR_CONTRACT_OK`；CPU/容器、admission、nginx、证书和
  `projection-drift` 文件哈希均为 `MATCH`。
- 受控 CPA health：`readiness` 与 `generation` 均返回 `HEALTH_OK`；随后四个
  admission lane 恢复为 `0/0`。
- 当前网关访问日志只读采样：`2517` 个 `200`，未观察到 maintenance-shaped
  `503`。该采样不能替代长期自然流量验收。

## 备份与回滚

- 远端备份：`/root/cpa-guardrails-backup-20261102T161140.912048408Z`。
- 回滚入口：使用备份中的原文件恢复，并按
  `docs/runbooks/cpa-manual-rollback.md` 复验 CPA、admission、nginx 和 fail2ban；
  不接受仅以 Git revert 代替远端恢复。

## 边界与残留

- 未修改或轮换任何账号、OAuth token、API key、订阅地址或 IP/UA。
- 17 个依赖 `flock` 的 nginx 本地行为测试在当前 Git Bash 环境跳过；生产内的
  nginx/admission 容器和配置检查已在同一轮 doctor 中通过。
- 历史观察仍显示已知的 Gemini 名称变体 substitution；没有新增未知 pair。
- 本轮完成到 `host_loaded` + 受控 CPA health；自然桌面回合和长期稳定性仍需
  后续流量观察，不能由本次事务自动外推。
