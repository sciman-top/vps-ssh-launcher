# 2026-10-15 BWG CPA v8.0.29 canary 升级证据

## 目标与范围

- 用户 2026-10-15 授权：直接升级 `bwg` VPS 上的 CLIProxyAPI 到官方 v8.0.29，按风控纪律
  复核封号/限流/降智、吐字缓慢与缓存命中率。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.29](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.29)，
  镜像 `eceasy/cli-proxy-api:v8.0.29@sha256:b98590c932e2a372e61d53e175a3abe4e8a22118667b2de3ad71514cbf1e3eb6`
  （Docker Hub tags API digest，与 `cpa-auto-update.sh` 同源；pull 按 digest 内容寻址校验）。
- 升级前生产为 `v8.0.28@sha256:5de1679350c368fb031d46900da30d97c1e9e9a3b150fc4c4181e7e65022a09e`
  （2026-10-14 canary 部署，timer 当日验证 success）。
- v8.0.29 镜像发布于 2026-10-14T16:22Z，低于 updater 72h 双源成熟期，自动 updater 会拒绝；
  按 10/12–10/14 同日 canary 先例走手动 digest 钉版事务。事务脚本
  `outputs/deploy-cpa-v8029-canary-20261015.sh`（规范化 LF SHA-256
  `18e9fd013c9d9211ecaa3b7526c289819073d6578045f0ebf3a5cee1ca4c97ef`，远端上传副本逐字节一致）
  复刻 `cpa-auto-update.sh --apply` 全部纪律：maintenance flock 互斥、compose 钉版 fail-closed
  预检、备份、回滚 trap、exit 0/1/10/20/75 语义；两处已注释偏差（不写 timer 的
  `cpa-update.status` 心跳、post-start 增加 bind-wait sleep 防误回滚）。
- 升级事务零配置改动：仅 compose 的 CPA 镜像行原子替换。不触碰 OAuth/API 凭据、Nginx、
  admission、路由清单、并发、重试或冷却策略。

## 上游变更与风控决策（v8.0.28 → v8.0.29）

- **缓存命中**：antigravity/Gemini responses 的 cached-token 用量统计修复（计量面，非缓存行为
  本身）；GLM lane 缓存路径无改动 → 升级后双样本实测命中率与基线逐位一致（见下）。
- **封号/限流**：release 无 OAuth/限流语义改动；`model registration fencings + epoch-checked
  publications` 属目录完整性强化（阻止 superseded 模型注册覆盖）。全部验收探针 OAuth lane
  opt-out（`PROBE_BUDGET ... oauth_lane=excluded:default_opt_out`），订阅账号零探针消耗。
- **降智/吐字**：`MALFORMED_FUNCTION_CALL` 转可重试错误（Claude 翻译）、Claude 静默 TLS 握手
  超时+重试、多分支 tool union 摊平（antigravity Claude 请求）、跨轮唯一 tool-use ID——均收窄
  假失败/截断面；升级后 `model_substitution_warnings_7d=0`（升级前 7d=3）。
- **配置键迁移**：`tls-handshake-timeout-seconds` 移至 root request 配置。本栈 config.yaml 未使用
  该键（升级前 grep 零命中），零影响；新握手超时/重试逻辑随默认值生效。刻意不新增该键
  （无故障证据，R5）。
- **registry**：`gemini-3.8` 变体入册（antigravity lane 唯一模型 `gemini-3.8-flash-high` 成为正式
  registry 条目）、`muse-wonder-full-1.0` 入册。不扩本栈目录，antigravity 排除表 7 名不变。
  `session-affinity-rebind-on` 延续周期决策不启用（缓存近上限、单 OAuth 凭据）。

## 前置证据（v8.0.28 基线，2026-10-14T16:0xZ）

- 开工严格 doctor `DOCTOR_CONTRACT_OK`：v8.0.28@5de16793 running；timer heartbeat
  `result=success exit_code=0 age_hours=12`；冷却 none；`luna_state=available`；TLS 88 天。
- pre cache-canary 双样本 `hit_ratio=0.0000`（冷写）→ `0.9956`（4096/4114 命中），基线锚定。
- 事务 `PREFLIGHT pin` 精确匹配 + `PRE_GENERATION OK`。

## 生产升级事务（2026-10-14T17:04Z）

- 17:04:51Z 备份 `/opt/cliproxyapi/backups/20261014T170451.921037141Z-from-v8.0.28-canary-v8.0.29`
  （仅 compose.yml；v8.0.28 旧镜像本地保留作回滚集）。
- digest 钉定 pull → compose 镜像行替换（replacements=1）→ `docker compose up -d --pull never`。
- 17:04:59Z `OK: updated v8.0.28 -> v8.0.29`，post generation `HEALTH_OK`（exit 0 verified 路径）；
  未触发回滚；容器 restarts=0。远端临时事务文件已清理。

## 升级后验证

- 容器 `v8.0.29@b98590c9` running、restart=0，`repo_digests` 与目标 digest 精确一致。
- cache-canary 双样本 `hit_ratio=0.9956 / 0.9956`（升级前写入的缓存条目跨容器重启仍命中，
  前缀稳定设计生效），与 0.9956 基线逐位一致。
- generation-all 7/7 可达通道 `200/stop`：`gpt-6-astra` 4.5s、`gpt-6.1-sol-input` 4.3s、
  `glm-5.3-flash` 1.7s、`deepseek-v4.1-flash` 1.1s、`gpt-5.6-terra` 3.9s、`glm-5.3` 2.7s、
  `muse-spark-1.3-contributor` 2.2s（可达通道较上一周期 6/7 增加 muse-spark，属通道侧恢复）；
  OAuth 4 模型（luna×2/sol/antigravity）默认 opt-out 零消耗。
- 严格 doctor 两次 `DOCTOR_CONTRACT_OK`：12 项投影 `MATCH`；`admission-integrity=OK`；
  `oauth-monitor=OK`（codex `days_left=8`、7d 刷新失败 0、`oauth_refresh_failures_7d=0`）；
  `antigravity-lane=OK`（allowed=gemini-3.8-flash-high + 7 名排除不变、loopback 精确绑定）；
  `model_substitution_warnings_7d=0`；冷却态 none；`REFRESH_SIGNALS_24H=0`；
  `gateway_tls_cert_days_left=88`；无 pending 文件。
- 风控参数复核：`keepalive-seconds: 15` / `send-keepalive-before-ft: true` 未动（事务只写镜像行）；
  admission journal 24h `OK upstream_results=2407`，lane reject 4 次全为 chatgpt-oauth 既有
  `downstream_gone/model_cooldown`，与升级前同量级。

## 本地门禁

- 切片 = outputs 事务脚本 + 本文档，无 launcher/runtime/SSH/config/schema 代码改动 →
  focused closeout：`git diff --check` + `bash -n`（本地与远端各一次，均通过）；不机械叠加完整 suite。
- 真实 SSH 远端写入属用户本次显式授权（同日 canary 先例）。

## 回滚入口

备份 compose.yml 与 `v8.0.28@5de16793` 旧镜像已保留（镜像 prune 保护 running+retained backups）。
本地 readiness/doctor 失败时按 `docs/runbooks/cpa-manual-rollback.md` 从备份恢复 Compose 并以旧
digest `docker compose up -d --pull never`，随后重跑严格 doctor；本次未需要回滚。timer 下次运行
将看到 `current=v8.0.29` 走"无更新 readiness 验证"非消耗路径，与本次手动 canary 无冲突。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release notes、Docker Hub digest、事务脚本本地/远端 SHA-256 一致、fail-closed 预检通过。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 12 项投影全部 `MATCH`；compose 镜像行原子替换。 |
| `host_loaded` | v8.0.29 目标 digest running、restart=0、generation/readiness 与两次 strict doctor 通过。 |
| `controlled_live_replay` | 非 OAuth generation-all 7/7 路 200/stop；GLM cache-canary 0.9956 双样本与基线逐位一致。 |
| `natural_live_accepted` | **未宣称**。延续纪律：未制造 OAuth/桌面自然会话；替代信号为 substitution 告警 3→0、refresh 失败 0、gateway 24h 状态面无新增异常。 |
