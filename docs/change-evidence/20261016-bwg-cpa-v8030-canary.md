# 2026-10-16 BWG CPA v8.0.30 canary 升级证据

## 目标与范围

- 用户 2026-10-16 授权：直接升级 `bwg` VPS 上的 CLIProxyAPI 到官方 v8.0.30，按风控纪律
  复核封号/限流/降智、吐字缓慢与缓存命中率，并连续执行修复与优化直至闭环。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.30](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.30)，
  镜像 `eceasy/cli-proxy-api:v8.0.30@sha256:a0b564ef82f67b157ebf488ee11d395e8c1ff6281c1fd17e7c34261f85d31f22`
  （Docker Hub tags API digest，与 `cpa-auto-update.sh` 同源；pull 按 digest 内容寻址校验）。
  镜像发布于 2026-10-15T14:01Z，不足 updater 72h 双源成熟期，自动 updater 会拒绝；
  按 10/12–10/15 同日 canary 先例走手动 digest 钉版事务。
- 升级前生产为 `v8.0.29@sha256:b98590c932e2a372e61d53e175a3abe4e8a22118667b2de3ad71514cbf1e3eb6`
  （2026-10-15 canary 部署，容器 10-15T10:59:54Z 起 restart=0）。
- 事务脚本 `outputs/deploy-cpa-v8030-canary-20261016.sh`（规范化 LF SHA-256
  `31724d046fe0d80efa4676051a616c8eb85345ac6a02b40e85a963f21ea2208e`，上传远端临时副本
  `sha256sum` 回执逐字节一致；远端 `bash -n` 通过后才执行）复刻 `cpa-auto-update.sh --apply`
  全部纪律：maintenance flock 互斥、compose 钉版 fail-closed 预检、备份、回滚 trap、
  exit 0/1/10/20/75 语义；两处已注释偏差（不写 timer 的 `cpa-update.status` 心跳、
  post-start bind-wait sleep 防误回滚）。与 v8.0.29 事务逐行 diff 仅版本/digest/日志名差异。
- 升级事务零配置改动：仅 compose 的 CPA 镜像行原子替换（replacements=1 断言）。

## 上游变更与风控决策（v8.0.29 → v8.0.30）

- **缓存命中**：release 无缓存/透传语义改动 → 升级后双样本实测与基线逐位一致（见下），
  且升级前写入的缓存条目跨容器重建仍命中。
- **封号/限流**：release 无 OAuth 限流语义改动；`Auth: protect active model and credential
  cooldowns from being cleared by stale in-flight execution successes` 修复冷却被陈旧成功
  误清，与本地冷却阶梯（60s 起步、policy 三向持久钉定）方向一致，零配置改动。
  全部验收探针 OAuth lane opt-out（`oauth_lane=excluded:CPA_HEALTH_NO_OAUTH`），订阅账号零探针消耗。
- **降智/吐字**：`strip unsupported discriminator keyword from JSON schemas for Gemini and
  Antigravity` 收窄 antigravity lane 工具 schema 假失败面；watcher 管理保存竞态修复；
  Claude 翻译侧 tool status→is_error 映射。`streaming keepalive 15s / send-keepalive-before-ft: true`
  升级后 grep 回执在位（嵌套键，事务未触碰 config.yaml）。
- **配置面**：`operator overrides for Claude header defaults / client request ID modes` 为新增
  可选键，本栈不添加（R5）；`PreapareConfigFileForUpload` 远端存储 seeding 为管理面特性，
  本栈管理 API 仅 loopback-keyed，不使用。
- **registry**：`gemini-4-argon` 入册。不扩本栈目录，antigravity 排除表 7 名不变
  （doctor `antigravity_lane=OK` 实证）；`session-affinity-rebind-on` 延续周期决策不启用
  （缓存近上限、单 OAuth 凭据）。
- **不适用**：Vertex `/publishers/` 前缀、xAI web_search 选项过滤——本栈无对应消费面。

## 前置证据（v8.0.29 基线，2026-10-15T16:0x–16:1xZ）

- 开工严格 doctor `DOCTOR_CONTRACT_OK`：v8.0.29@b98590c9 running restart=0；timer heartbeat
  `result=success exit_code=0 age_hours=5`；冷却 none；`luna_state=available`；TLS 87 天；
  12 项投影 `MATCH`；`admission-integrity=OK`；`oauth_monitor=OK`（days_left=7、
  refresh_failures_7d=0）。
- pre cache-canary 双样本 `hit_ratio=0.0000`（冷写）→ `0.9956`（4096/4114 命中），基线重锚定。
- pre quality-canary 7/7 可达通道 `200/stop`（OAuth 4 模型 suppressed）。
- **降智信号观察**：旧容器 5h 窗口累计 `model_substitution_warnings_7d=29`
  （WARN_SUBSTITUTION_ELEVATED，>5 阈值）；同期 admission journal 24h 容量事件 21 起
  （429×19，antigravity-oauth/gemini-3.8-flash-high 16 起、chatgpt-oauth/gpt-6.1-sol 5 起），
  reject 7 次均为既有 `cooldown/downstream_gone/model_cooldown/queue_timeout` 族——
  与上游容量窗透传口径一致，属 AI Pro 账号上游容量行为，配置侧无再动空间
  （sensitive-words 掩蔽、单模型钉定、admission lane 策略均在位）。

## 生产升级事务（2026-10-15T16:19Z）

- 16:19:23Z `PREFLIGHT pin=v8.0.29@b98590c9` 精确匹配 + `PRE_GENERATION OK`（HEALTH_OK）。
- 16:19:24Z 备份 `/opt/cliproxyapi/backups/20261015T161924.840885154Z-from-v8.0.29-canary-v8.0.30`
  （仅 compose.yml；v8.0.29 旧镜像本地保留作回滚集，事后 `ROLLBACK_IMAGE_PRESENT` 实证）。
- digest 钉定 pull → compose 镜像行替换 → `docker compose up -d --pull never`。
- 16:19:32Z `OK: updated v8.0.29 -> v8.0.30`，post generation `HEALTH_OK`；未触发回滚；
  容器 restarts=0；`TRANSACTION_EXIT=0`。远端临时事务文件已清理。

## 升级后验证

- 容器 `v8.0.30@a0b564ef` running、restarts=0，`repo_digests` 与目标 digest 精确一致。
- cache-canary 双样本 `hit_ratio=0.9956 / 0.9956`（升级前写入的缓存条目跨容器重建仍命中，
  前缀稳定设计生效），与 0.9956 基线逐位一致。
- quality-canary 7/7 可达通道 `200/stop`：`gpt-6-astra` 12.9s、`gpt-6.1-sol-input` 13.0s、
  `glm-5.3-flash` 3.3s、`deepseek-v4.1-flash` 2.0s、`gpt-5.6-terra` 8.7s、`glm-5.3` 3.3s、
  `muse-spark-1.3-contributor` 2.3s（astra/sol-input 秒级波动属通道 TTFT 时间窗族，
  两次探针均在健康区间，勿按升级回归归因）；OAuth 4 模型默认 opt-out 零消耗。
- 严格 doctor `DOCTOR_CONTRACT_OK`：12 项投影 `MATCH`；`admission-integrity=OK`；
  `oauth_monitor=OK`；`antigravity-lane=OK`（allowed=gemini-3.8-flash-high + 7 名排除不变、
  loopback 精确绑定）；冷却 none；`luna_state=available`；`gateway_tls_cert_days_left=87`。
- `model_substitution_warnings_7d=0`：容器重建后新日志窗口从零起算（该计数只读 retained
  container logs），**不构成上游替换停止的证明**；升级前 29 条为旧容器窗口累计，
  后续以 doctor 该计数继续监测，复发时先查 antigravity 容量 429 共现再归因。
- 风控参数回执：`keepalive-seconds: 15`、`send-keepalive-before-ft: true`、
  `sensitive-words: [gpt, openai]`（antigravity 掩蔽，apply 生成升级/再生成不丢）均在位；
  admission journal 24h `OK`，与升级前同量级。
- timer 下次运行（10-16T04:00Z）将看到 `current=v8.0.30` 走"无更新 readiness 验证"
  非消耗路径，与本次手动 canary 无冲突；`cpa-update.status` 心跳未被手动事务触碰。

## 本地门禁

- 切片 = outputs 事务脚本 + 本文档，无 launcher/runtime/SSH/config/schema 代码改动 →
  focused closeout：`git diff --check` + `bash -n`（本地全部新脚本 + 远端事务脚本执行前各一次，
  均通过）；不机械叠加完整 suite。
- 真实 SSH 远端写入属用户本次显式授权（"自主在 bwg VPS 上直接更新/升级 CPA"）。

## 回滚入口

备份 compose.yml 与 `v8.0.29@b98590c9` 旧镜像已保留（镜像 prune 保护 running+retained backups）。
本地 readiness/doctor 失败时按 `docs/runbooks/cpa-manual-rollback.md` 从备份恢复 Compose 并以旧
digest `docker compose up -d --pull never`，随后重跑严格 doctor；本次未需要回滚。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release notes、Docker Hub digest/publish 时间、事务脚本本地/远端 SHA-256 一致、fail-closed 预检通过。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 12 项投影全部 `MATCH`；compose 镜像行原子替换。 |
| `host_loaded` | v8.0.30 目标 digest running、restarts=0、generation/readiness 与严格 doctor 通过。 |
| `controlled_live_replay` | 非 OAuth quality-canary 7/7 路 200/stop；GLM cache-canary 0.9956 双样本与基线逐位一致（跨容器重启命中）。 |
| `natural_live_accepted` | **未宣称**。延续纪律：未制造 OAuth/桌面自然会话；替代信号为 OAuth 探针零消耗、refresh 失败 0、冷却 none、gateway 24h 状态面无新增异常。 |
