# bwg CPA v8.0.5 手动 canary 升级收据（2026-10-01）

## 授权与背景

- 用户当次明确指令：自主升级 CPA 至 v8.0.5 并防范封号/限流/降智风险（豁免 72h soak；先例 2026-09-25 v7.3.17）。
- 升级前生产：`eceasy/cli-proxy-api:v8.0.4@sha256:72205ea2…`（9/30 16:17Z timer 收编，restarts=0）。
- updater `--check` 证实 v8.0.5 未满 72h 成熟度（`CANDIDATE current=v8.0.4 target=v8.0.4`），故走与 9/25 同款的手动 canary，逐条镜像 `cpa-auto-update.sh` apply 语义。

## 变更面审查（风控视角）

v8.0.4→v8.0.5（upstream commit `e5b5a1c`，25 commits）：

- **封号指纹面：零改动**——无任何 UA/请求头/cloaking/刷新节奏变更。
- auth 侧为正收益：`fix(auth): track auto-refresh job epochs and bound credential acquisition`（81756a5，抑制刷新风暴）+ auth 持久化失败告警（02548d6）。
- codex 客户端兼容修复×2（agent_message compat 转换 f33f04d、tool 参数整数归一 71a5f1f），对 desktop 正向。
- SSE 失败事件处理修复（f169711）；新增模型注册 gpt-6.1-sol（c8a2bfe，本网关已在册，无目录影响）；其余为 Claude/Kimi/管理面/文档，与本部署无关。

## 执行（canary 脚本 sha256=aab83b07…，outputs/cpa-v805-canary.sh）

时序（UTC）：预检（config sha `52c295f2`、auth 2 项、镜像 digest）→ pre readiness+generation 双 HEALTH_OK → 备份 `backups/20260930T174505.495197128Z-canary-from-v8.0.4`（700）→ pull `v8.0.5@sha256:a3ffe52b…` → compose image 行单点替换（REFUSE≠1 保护）→ `docker compose config --quiet && up -d --pull never` → post generation HEALTH_OK → **CANARY_OK @ 17:45:16Z**。

回滚路径：恢复备份 compose + `up -d --pull never`（v8.0.4 镜像本地保留未 prune）；脚本对 exit 1 自动回滚、对上游侧 503 类（generation exit 10 + readiness OK）按 updater 语义保新。

## 验收

- 容器：v8.0.5@sha256:a3ffe52b started 17:45:12Z，restarts=0；启动日志零 error；config sha 不变；auth 2 项未动（无重登录）。
- 公网 luna 单发（消费 1 turn）：200，headers 1.7s，59 delta，18ms/delta（与升级前同形态）。
- 严格 doctor：**DOCTOR_CONTRACT_OK**——投影 9×MATCH、`oauth_monitor=OK`（days_left=5、7d 零刷新失败）、目录=精确 10 名（含 optional gpt-image-2.5）、`cooldown_state=none`、近 1h 177×200 零 429/5xx（24h 的 429×39/502×13 均为升级前/bot 上游诚实透传）。
- fixture（v8.0.5 二进制 sha `21611184…`，七件套全部 ==HEAD）：**ACCEPTANCE_RESULT=PASS**——流式三阶段+恢复、目录契约（9 裸名+墓碑 fail-closed+`sol_diag=200 gpt-6.1-sol stop`）、updater 四场景（start_fail/model_exposure→exit1+回滚、transient→exit10 保新、success→exit0）；清点零残留，生产未受扰动。

## 观察项

- 今晚 22:05+08 巡检、明日 04:00 UTC timer（将报 current=v8.0.5 + REFRESH_SIGNALS）。
- （补正 2026-10-01："22:05+08 每日巡检"已不存在——bwg 调度已全迁 `/etc/cron.d/` 仅 4 项；观察以每日 04:00 UTC update timer 与周五内核周更为准。）
- OAuth 刷新点 ~10/5（days_left=5，doctor 自动监护；失败才需 device-login）。
- 本轮消耗：luna 3 发（pre/post 健康门 + 公网探针）；fixture 零消耗。
