# 2026-10-16 BWG CPA v8.0.31 canary 升级证据

## 目标与范围

- 用户 2026-10-16 授权：直接升级 `bwg` VPS 上的 CLIProxyAPI 到官方 v8.0.31，防范/控制
  封号、限流、降智风险，关注吐字缓慢与缓存命中率，连续执行修复和优化直至闭环。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.31](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.31)，
  镜像 `eceasy/cli-proxy-api:v8.0.31@sha256:db1812ea5fc703ae228ef53e75e03f502211a2c9194cc2e8152000d4b974da74`
  （远端 `docker pull` 后 `RepoDigests` 实测；`docker manifest inspect` 交叉核对
  amd64 `sha256:368116af...`）。发布于 2026-10-16，不足 updater 72h 双源成熟期；
  按 10/12–10/16 同日 canary 先例走手动 digest 钉版事务。
- 升级前生产为 `v8.0.30@sha256:a0b564ef82f67b157ebf488ee11d395e8c1ff6281c1fd17e7c34261f85d31f22`
  （2026-10-16 16:19Z canary 部署，容器 11:36:28Z 起 restart=0）。
- 事务脚本 `outputs/deploy-cpa-v8031-canary-20261016.sh`（规范化 LF SHA-256
  `d517b7535442f8716af90be260d0b56837f064007d2aab5dc4f827690555cc8f`，上传远端
  `sha256sum -c` 回执一致后执行）复刻 `cpa-auto-update.sh --apply` 全部纪律：
  maintenance flock 互斥、compose 钉版 fail-closed 预检、备份、回滚 trap、
  exit 0/1/10/20/75 语义；与 v8.0.30 事务逐行 diff 仅版本/digest/日志名差异。
- 升级事务零配置改动：仅 compose 的 CPA 镜像行原子替换（replacements=1 断言）。

## 上游变更与风控决策（v8.0.30 → v8.0.31）

- **claude 路由**：`pin validated request body per-stream in OpenAI Responses translation`
  —— 流式翻译层把已验证请求体按流固定，消除流式会话中请求体可变性问题。与 10/16 刚
  LIVE 的 `claude_api_key_routes`（opencode-zen-claude / claude-haiku-5-5）直接相关，
  是本次升级主要受益面；升级后以探针同形状实测该路由正常服务（见下）。
- **管理 API**：v8 配置端点新增 ETag/If-Match 乐观并发。向后兼容新增（不发送 If-Match
  的既有客户端不受影响）；本栈配置写入走投影/policy 面不依赖管理端点语义，零配置改动。
  观察项：若后续 cockpit/管理面写配置遇 409 再按乐观并发语义排查。
- **插件宿主**：动态库加载内容寻址影子副本。本栈不使用动态库插件，无消费面。
- **缓存命中**：release 无缓存/透传语义改动 → 升级后实测与 0.9956 基线逐位一致（见下）。
- **封号/限流**：无 OAuth 限流语义改动。全部验收探针 OAuth lane opt-out
  （`CPA_HEALTH_NO_OAUTH=1`），订阅账号零探针消耗；升级期间唯一 OAuth 请求为外部
  客户端自然流量（22:10:47 gpt-6.1-sol 200），非探针。

## 前置证据（v8.0.30 基线）

- 14:06:18Z 事务预检 `pin=v8.0.30@a0b564ef` 精确匹配；pre generation `HEALTH_OK`。
- 开工 `git status` 干净、`main..origin/main` 为空（双机协议 fetch/review 无新远端差异）。
- 磁盘 avail 28.9 GB（阈值 2 GiB）；v8.0.31 镜像预拉后按 digest 复用。

## 生产升级事务（2026-10-16T14:06Z）

- 14:06:18Z `PREFLIGHT pin=v8.0.30@a0b564ef` → 14:06:19 `PRE_GENERATION OK`。
- 14:06:19Z 备份 `/opt/cliproxyapi/backups/20261016T140619.731015389Z-from-v8.0.30-canary-v8.0.31`
  （仅 compose.yml；v8.0.30 旧镜像本地保留作回滚集）。
- digest 钉定 pull → compose 镜像行替换 → `docker compose up -d --pull never`。
- 14:06:27Z `OK: updated v8.0.30 -> v8.0.31`，post generation `HEALTH_OK`；`CANARY_EXIT=0`；
  容器 restarts=0；未触发回滚。

## 升级后验证

- 容器 `v8.0.31, Commit: 355fd10, BuiltAt: 2026-10-16T04:05:41Z` running、restarts=0，
  `repo_digests` 与目标 digest 精确一致；8 clients 加载完整
  （2 auth + 1 Claude API keys + 1 Codex keys + 4 OpenAI-compat）。
- cache-canary 双样本：`hit_ratio=0.0000`（上游缓存条目 10h 间隔自然过期后冷写）→
  `0.9956`（4096/4114 命中），与 0.9956 基线逐位一致；同轮次内写读即中证明前缀稳定
  设计生效。冷写首样本与 v8.0.30 升级时双命中样本的差异 = GLM 上游缓存 TTL，非回归。
- **zen claude 路由实测**（v8.0.31 per-stream pin 修复面，探针同形状
  safeguards/context_management/output_config）：`stop_reason=end_turn,
  model=claude-haiku-5-5`，零错误。**zen codex 路由实测**（responses 形状 +
  desktop tools）：`status=completed, model=muse-spark-1.3-contributor`，零错误。
- **流式节奏（吐字专项）**：`glm-5.3-flash` TTFT 4.82s、31 数据帧、最大帧间隔 0.62s；
  `deepseek-v4.1-flash` TTFT 1.63s、9 帧、0.04s；两路零断流，keepalive 注释帧未需触发
  （上游 TTFB < 15s，`keepalive-seconds: 15` 兜底在位）。TTFT 属既有通道时间窗波动族。
- 严格 doctor `DOCTOR_CONTRACT_OK`：投影全部 `MATCH`；`admission-integrity=OK`；
  `oauth_monitor=OK`（days_left=6、refresh_failures_7d=0，刷新窗口由 timer 自愈不干预）；
  `antigravity-lane=OK`；`model_substitution_warnings_7d=0`（容器重建后新窗口起算，
  勿当上游停止证明）；TLS 86 天；冷却 cds_files=0；admission journal 24h `OK`
  （upstream_results=3472、reject/capacity 事件 0）。
- 风控参数回执（yaml 全路径，事务未触碰 config.yaml）：`requests.streaming.keepalive-seconds=15`、
  `send-keepalive-before-ft=true`、`oauth.providers.antigravity.sensitive-words=[gpt, openai]`
  均在位。

## ai.input.im 通道既有故障与 quality-canary 中止归因（非升级回归）

- 22:07:09Z+8 首轮 quality-canary 在 `gpt-6-astra` 处 502
  `Upstream access forbidden`（ai.input.im 站点层错误体，CPA 透传；provider=
  openai-compatible-ai.input.im）。
- CPA 本地冷却将 astra 从 `/v1/models` 摘除 → 探针 catalog gate fail-closed
  （静态 required 集 ⊄ 实际 ids，`MISSING_FROM_CATALOG: [gpt-6-astra]` 实测）→
  后续全部 generation/quality 单发 exit 10 且请求未发出（容器日志零 chat/completions
  记录实证）。`readiness` 对 catalog 不完整按设计返回 0（假绿是既有口径：
  catalog 降级不阻断 readiness）。cache-canary 不经该 gate，故正常完成。
- 非回归证据链：doctor 24h gateway-statuses 显示升级前（UTC 03/04/10 点，v8.0.30
  时期）已有同族 500/502；v8.0.30 canary 验收（10-15T16:1xZ）时 astra 尚 200——
  ai.input.im forbidden 故障起点约 10-16 上午，早于本次升级；与 10/11 forbidden
  ~8h 自愈、10/13 "not priced" 同族，通道侧站点层行为。
- 处置：零配置动作，等待自愈；astra 回 catalog 后 quality-canary 全矩阵自然恢复。
  期间单发 generation 对 astra 必 10 属探针正确行为，勿按升级回归排查。
- 观察项（不扩本次 scope）：探针 catalog gate 失败时无诊断输出（单发模式亦无
  per-model report 行），本次归因耗时较长；后续可为 cpa-health.py 增补 gate 失败
  差集诊断行（触及 runtime 面，须另立任务走 full closeout）。

## 本地门禁

- 切片 = outputs 事务脚本 + 本文档，无 launcher/runtime/SSH/config/schema 代码改动 →
  focused closeout：`git diff --check` + `bash -n`（本地事务脚本，通过）；不机械叠加
  完整 suite。
- 真实 SSH 远端写入属用户本次显式授权（"自主在 bwg VPS 上直接更新/升级 CPA"）。

## 回滚入口

备份 compose.yml 与 `v8.0.30@a0b564ef` 旧镜像已保留。readiness/doctor 失败时按
`docs/runbooks/cpa-manual-rollback.md` 从备份恢复 Compose 并以旧 digest
`docker compose up -d --pull never`，随后重跑严格 doctor；本次未需要回滚。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release notes（3 commits、无 schema 破坏）、manifest/RepoDigests 双源 digest、事务脚本本地/远端 SHA-256 一致、fail-closed 预检通过。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 投影全部 `MATCH`；compose 镜像行原子替换。 |
| `host_loaded` | v8.0.31 目标 digest running、restarts=0、8 clients 加载完整、generation/readiness 与严格 doctor 通过。 |
| `controlled_live_replay` | 非 OAuth 探针全过：GLM cache-canary 0.9956 与基线逐位一致；zen claude 路由 end_turn（v8.0.31 修复面实测服务正常）；zen codex 路由 completed；流式双路零断流。ai.input.im 通道既有故障致 quality 全矩阵探针 fail-closed 中止（通道侧，见归因节）。 |
| `natural_live_accepted` | **未宣称**。延续纪律：未制造 OAuth/桌面自然会话；替代信号为 OAuth 探针零消耗、升级窗口 OAuth 自然流量 200、refresh 失败 0、冷却 none、gateway 24h 状态面无新增异常。 |
