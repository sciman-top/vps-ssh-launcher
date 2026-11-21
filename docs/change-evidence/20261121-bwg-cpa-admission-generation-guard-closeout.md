# 2026-11-21 BWG CPA admission 代际一致性修复投影与维护巡检收口

**性质**：真实远端写入（guardrails `-Apply`）+ 代码修复收口记录。落实
[20261121-bwg-cpa-admission-generation-rollback-incident.md](20261121-bwg-cpa-admission-generation-rollback-incident.md) §5 第 1 项建议；同轮完成版本巡检、strict doctor 与非 OAuth 受控验收。zz、aliyun 未触碰。

## 1. 版本巡检结论（无升级项）

- CPA：生产 `v8.0.80-gemini-raw-agy-effort`；上游 latest 即 v8.0.80（2026-11-20 发布），main 在其后无新提交。dev 分支有未发版提交（Antigravity refresh 持久化、SSE 重组装硬化等），未发版不部署。updater 2026-11-21T04:00 UTC 成功：`no newer mature release; current=v8.0.80 readiness verified`。
- WorkBuddy CN 插件：latest 即 `0.21.29`，已部署；INTL 保持 `closed_dark_20261117`。
- zcode-start-plan sidecar（`TriDefender/zcode-api`）：latest 即 `v4.9.1`；远端二进制 SHA `1473c759…` 与官方 release 资产逐字节一致。

## 2. 代码修复（commit 86a818d，已推 origin/main）

1. `scripts/remote/cpa-guardrail-apply.sh` `restore_all`：恢复后校验 admission.py SHA == pin == request guard 内嵌 `SOURCE_SHA256`，失配则 `ROLLBACK_FAILED admission_generation_set_mismatch`（此前回滚只报布尔，可能恢复出混合代际并复发崩溃循环）。
2. `scripts/remote/cpa-admission-integrity-check.sh`：boot 前置检查新增 guard↔pin 耦合校验（guard 存在时内嵌 `SOURCE_SHA256` 必须等于 pin，否则 `ADMISSION_INTEGRITY_REFUSE guard_generation_mismatch`）。
3. `tests/test_cpa_guardrails_script.py`：新增 `guard_generation_mismatch` 子测试。

Full 门禁：`1817 passed, 30 skipped, 893 subtests`；bandit/ruff/mypy 全绿（receipt 见提交信息 `FULL_GATES=PASS`）。

## 3. 部署与验收（UTC 09:44–09:46）

- `cpa_bwg_guardrails.ps1 -Profile bwg -Apply`：`GUARDRAILS_APPLIED` 退出码 0；`READY_STATUS=200`、`HEALTH_OK`；17/17 `PROJECTION_HASH_VERIFIED`；备份 `/root/cpa-guardrails-backup-20261121T094411.809903326Z`（apply 内旧备份自动 prune 1，policy=keep_8）。pin 未轮换（`53bb40c5…`）：本切片只改 integrity-check 与 rollback 段，不改 admission.py 本体，故无需原子旋转。
- strict doctor 只读复验：`DOCTOR_CONTRACT_OK` 退出码 0；`admission-integrity=OK`（新 guard 校验生效）；projection-drift 17 项全 MATCH；container `restart=0`；updater timer/schedule/heartbeat OK。
- OAuth 车道：chatgpt / antigravity / workbuddy / workbuddy-intl 全 OPEN；claude 车道 RETIRED（tombstone inert）。7 个 Antigravity 凭据 `refresh_age_hours` 0.0–0.1h、`METADATA_OK`；`credential_ban_signals_7d=0`；codex OAuth `oauth_expired=false`（约 9 天余量，lead24h 刷新策略内）。
- 非 OAuth 受控验收（zhipu/glm API-key lane，无 OAuth 生成探针、无凭据轮换、无冷却清理）：`cpa-health.py cache-canary glm-5.3-flash chat` 两样本 `hit_ratio=0.0000 → 0.9956`，`HEALTH_OK`。

## 4. 性能与风险画像（维持既有决策）

- 缓存命中率健康：workbuddy 0.98 / zhipu 0.96 / antigravity 0.92–0.94 / chatgpt 0.73–0.91（24h 自然流量审计）；canary 0.9956。
- TPS 属模型原生节奏：antigravity neon p50 176 tok/s、workbuddy p50 286 tok/s、chatgpt 37–52 tok/s（长思考回合所致），无回归信号。
- workbuddy admit 峰值 19 rpm 触及社区上沿 → **不收紧**（用户既定决策：12 RPM 会拒合法突发，保持 18，仅监控）。
- model_substitution `WARN_SUBSTITUTION_ELEVATED` 仍在（335 次/7d，集中于 2 个凭据，覆盖 99% 车道请求）：Antigravity `-xhigh`/`-high` 被基础名服务，属 agy-effort 补丁的已知观察项，本轮不动。

## 5. 边界与未做项

- 事故记录 §5 第 2、3 项（回滚失败输出首个失败步骤名、本地 stub systemctl 路径形式夹具）仍未修，属独立切片。
- dev 分支未发版提交不部署；OAuth 凭据、订阅、密钥未触碰；无 OAuth 生成探针。

## 6. Git

- 投影锚定 HEAD=86a818d（已推 origin/main，push 后 fetch/status 干净同步）。
- 本记录为 docs-only 切片：`git diff --check` + `check_docs.py`。
