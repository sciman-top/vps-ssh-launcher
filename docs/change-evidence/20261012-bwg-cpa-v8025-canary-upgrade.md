# 2026-10-12 BWG CPA v8.0.25 canary 升级证据

## 目标与范围

- 目标：仅将 `bwg` VPS 上的 CLIProxyAPI 从 v8.0.24 升级到官方 v8.0.25。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.25](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.25)，本次固定使用镜像
  `eceasy/cli-proxy-api:v8.0.25@sha256:7f2735c1ab92b087ca43d1342dc139a6b7ece939731b0de2a9a3eee6eb45aab4`。
- 升级前镜像为
  `eceasy/cli-proxy-api:v8.0.24@sha256:302807246399682928f6dcf5a6f52032024ebffaf0e9c8bdeb05a7e235028d6f`。
- 仅改动 Compose 的 CPA 镜像行；不触碰 `zz`、OAuth/API 凭据、Nginx、admission、路由清单、并发、重试或冷却策略。

## 上游变更与风控决策

v8.0.23 → v8.0.25 中与当前风险最相关的官方修复已复核：

- `5dbd92a0e0df`：客户端取消流时保留上游错误携带的冷却信息，减少取消后重复冲击上游。
- `bbcd5734f87d`：将 `credential_quota` 按配额冷却处理，避免错误隐藏模型或继续消耗账号。
- `7a687f424c6b`：增加 `session-affinity-rebind-on`。本仓没有开启 `quota` 全局重绑定：当前缓存已接近上限，且 OAuth 只有一份凭据，盲改会扩大回归面。

现有 `session-affinity=true`、1h TTL、`stream-bootstrap-buffering=false`、`request-retry=0`、OAuth `max_inflight=2` 和 admission 队列上限均保持原值。没有证据表明增加并发、重试或缓存开关会改善当前结果，因此没有做额外配置写入。

## 仓库切片

- 夹具验收复用 v8.0.24 闭环（d35aade）已落地的 `scripts/remote/cpa-acceptance.py`
  夹具修复：client key 迁到 v8 `access.api-keys` 布局，是替换而非并存，未保留旧
  顶层列表（根因与修复记载见 v8.0.24 证据文档）。本次上传副本规范化 SHA-256：
  `4e9093ee13778a55216b3e210561b0b27566355d75a97b7cd6144d6620bc9a1d`，与仓库
  HEAD 逐字节一致。
- `outputs/cpa-v8025-canary.sh` 实现 BWG-only、共享维护锁、前置 generation、磁盘/备份门、digest-pinned pull、原子 Compose 替换、后置 readiness 和失败回滚。规范化 SHA-256：`184b66a22aa1c803aa9b9a98f3240a8fc4412898c3ad42a8746ef8438d483e78`。
- `outputs/deploy-cpa-v8025-canary.ps1` 只通过已配置的 `bwg` profile 注入并执行 canary；规范化 SHA-256：`105f25ede5b974fe5bdbcadbcb84191a1c9309743e84edddeb7a6a5d10bd6e81`。

## 前置证据

`outputs/doctor-20261012-pre-v8025.txt`（2026-10-12T12:36:36Z）返回 `DOCTOR_CONTRACT_OK`：

- v8.0.24 容器 running、restart=0，CPA/admission 为 loopback，Nginx 8443 为唯一公网入口。
- admission integrity、随机路径、401/404、Nginx 限流与 Retry-After、`POLICY_OK`、`semantic-policy=OK`、全部投影 `MATCH`。
- OAuth 单文件仍在续期窗口（约 59h），7d 刷新失败为 0；24h admission capacity 事件 49 次、lane reject 9 次，均为 `chatgpt-oauth`。没有用 OAuth 探针制造绿色结果。
- `request-retry=0`、`model_substitution_warnings_7d=0`、冷却态为 `none`，磁盘可用约 30GB。

## 隔离 fixture 验收

`outputs/fixture-v8025.txt` 使用目标 v8.0.25 digest 提取二进制，在 disposable mount/net namespace 中运行，未接触生产容器或真实 provider：

- binary SHA 前缀：`90945a254681478e`；两个验收脚本上传并 `py_compile` 通过。
- overload：HTTP 200 + capacity marker + 1 次合成上游调用；cooldown：HTTP 503 + 0 次调用；62 秒后同进程恢复 HTTP 200、`completed=true`。
- `actual_health` 为 `HEALTH_OK`；`start_fail` 与 `model_exposure` 均 exit 1 并恢复旧 Compose；`transient` exit 10 并记录 `UNVERIFIED`；`success` exit 0。
- 终态：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`。

## 生产升级事务

收据见 `outputs/cpa-v8025-canary-run.log`：

- 12:38:02Z 通过 `bwg` profile 开始，精确确认当前 v8.0.24 digest。
- 前置与后置 generation 均 `HEALTH_OK`；备份目录为
  `/opt/cliproxyapi/backups/20261012T123809.346711453Z-from-v8.0.24-canary-v8025`，权限 700。
- 12:38:12Z `OK: updated v8.0.24 -> v8.0.25`，退出码 0；未触发回滚，也未 prune 旧镜像。

## 升级后验证

`outputs/doctor-20261012-post-v8025.txt`（2026-10-12T12:39:05Z）返回 `DOCTOR_CONTRACT_OK`：

- 容器为 `v8.0.25@sha256:7f2735c1…`、running、restart=0；image digest 可读。
- loopback 8317/8318、公网 8443、随机路径 401/404、admission integrity、Nginx 443 lane、权限、语法和全部投影均通过。
- `POLICY_OK`、`semantic-policy=OK`、`MODEL_IDS_UNKNOWN=none`、`request-retry=0`、`model_substitution_warnings_7d=0`；OAuth 仍仅记录续期窗口和既有容量事实。

显式设置 `CPA_HEALTH_NO_OAUTH=1` 的低频复验结果（收据：`outputs/post-v8025-probes.txt`）：

- quality-canary 预算为 7 个非 OAuth 请求；`gpt-6-astra`、`gpt-6.1-sol-input`、`glm-5.3-flash`、`deepseek-v4.1-flash`、`gpt-5.6-terra`、`glm-5.3`、`muse-spark-1.3-contributor` 全部 HTTP 200、`finish=stop`，无重试。
- cache-canary（`glm-5.3-flash`）两样本：首样本冷启动 `0/4114`，第二样本 `4096/4114`，命中率 `0.9956`，与既有基线一致；因此没有改动 `support-prompt-cache-key` 或 session-affinity 配置。
- healthy lane 仍保持单发语义；57.1s 的 astra 单样本和其余 1.7–9.8s 的样本均归为上游生成时延观察，当前本地已是关闭 bootstrap buffering、无重试的低放大路径，没有证据支持增加并发或重试。

生产读回进一步确认：当前 Compose 为目标 digest，旧 v8.0.24 digest 仍可 `docker image inspect`，备份 Compose 含旧 digest，Compose 校验通过，维护锁为 `FREE`，升级后 10 分钟容器日志中 `panic/fatal error` 计数为 0。

## 本地门禁

`outputs/gates-20261012-v8025.txt`：

- build、bandit（含 `scripts`）、ruff check、ruff format、mypy 全部通过。
- pytest：`558 passed, 1 skipped, 269 subtests passed`；唯一跳过项是仓库约定的真实 SSH integration（未设置 `VPS_SSH_LAUNCHER_RUN_INTEGRATION=1`）。
- 门禁退出码为 0。

## 回滚入口

备份和旧 digest 已保留。发生本地 readiness/doctor 失败时，按
`docs/runbooks/cpa-manual-rollback.md` 从上述备份恢复 Compose，再使用旧 digest 执行
`docker compose up -d --pull never`，随后重新运行严格 doctor；本次未需要回滚。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release URL、双端 digest、fixture PASS、全门禁通过；canary 源与 fixture 修复均有本地收据。 |
| `filesystem_projected` | 生产受管文件未改动，post doctor 全部投影 `MATCH`；Compose 镜像行已按 canary 原子替换。 |
| `host_loaded` | v8.0.25 目标 digest running、restart=0、generation/readiness 与 strict doctor 通过。 |
| `controlled_live_replay` | 非 OAuth quality-canary 7 路 200/stop，GLM cache-canary 第二样本命中率 0.9956；fixture 为合成隔离回放。 |
| `natural_live_accepted` | **未宣称**。本次没有重新制造 OAuth/桌面自然会话；OAuth 续期窗口和账号级 capacity 事实继续观察，需用户日常真实会话产生新 receipt 才能升级此层。 |
