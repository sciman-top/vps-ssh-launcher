# BWG CPA 升级 v8.1.6（五叠层重移植）+ WorkBuddy 插件 0.21.32（2026-11-27）

**性质**：真实远端写入 + release 级证据留档（redaction-first）。时间均为 UTC
（换芯 SWAP_OK=2026-11-26T16:59:35Z，本地记 2026-11-27）。
**授权**：用户指令（2026-11-27）"直接更新/升级 CPA/插件/zcode-start-plan 等的最新版……
授权按你的推荐/建议自动执行修复和优化，直至完成所有任务"；WSL
`/root/cpa-v816-audit.Q3bc5X` 并行会话遗留目录由本会话接手收口。

## 裁决

| 事项 | 结论 | 依据 |
|---|---|---|
| v8.1.5 / v8.1.6 | 直接升级，不等 soak（沿 11-26 先例） | 用户直接指令；v8.1.5+v8.1.6 共 72 commits，无 OAuth/风控破坏性变更；v8.1.6 新增 Home 本地插件执行器分发 |
| 五叠层 | 全部保留并 rebase | 签名缓存 TOCTOU 修复（ff09fcb1）与 sig-iso 互补；replay clean-EOF 提交（7fa46e9d）与 crossauth 同域需适配 |
| terra 探针 | 换 `gpt-6.1-sol` | `gpt-6.1-terra` 已由并行会话 11-26 退役（`540dec1`，routes 摘名），非本次回归 |
| zcode-start-plan | 不动 | v4.9.1 仍为最新 release（11-20），服务运行中 |
| WorkBuddy CN 插件 | 0.21.31 → 0.21.32 | 11-25T16:45Z 发布（上代换芯后被漏）；变更=失败即写盘禁用改默认关闭（`19d4c5f2`，需 `failover_disable_enabled` 显式开启），与 admission 管冷却姿势一致 |

## 重移植（三方合并，/root/build-v814/repo）

- base = 纯净 v8.1.4（`38fe9295`）；ours = `merge-v814` 分支（v8.1.4+五叠层）；
  theirs = 纯净 v8.1.6（`0aaf6d1c`，72 commits/187 文件 +27871）。
- 冲突仅 1 处：`antigravity_reasoning_replay.go`——HEAD 的
  `antigravityReplayAuthScope`（crossauth 作用域键）与上游
  `deleteAntigravityPartThoughtSignatureFields*`（thought-signature 剪枝）互为
  新增函数，双方保留。git 合并吃掉一处收尾大括号（`return ""` 后），手动补回并
  amend。
- 钩子 delta 形状核验：execute 18/18、stream 16/16、apply 67/67（vs 上代），
  replay 40/41（上游函数排版差异，内容等价）。
- **并行会话遗留目录收口**：`/root/cpa-v816-audit.Q3bc5X`（v8.1.6 archive
  import + 18 文件 staged 叠层移植）与本树逐文件对照 16/18 SAME；两个 DIFF 甄别
  ——caqs 测试本树=上游原样（audit 侧是其自加的每凭据签名变体，其 go-test FAIL
  或源于此）、replay 仅函数排版。**吸收 audit 的独有资产**：
  `issue6332_review7_test.go` 作用域键适配补丁（上游 v8.1.6 新测试按无前缀键读
  replay，crossauth 树必须适配，与我树既有 review 适配同形）。patch 归档
  `/root/cpa-v816-merge/audit-archive/`，临时目录已清（auths 仅 0 字节 .gitkeep，
  无凭据遗留）。
- 全量 `go test ./...`：**108 包 ok，2 失败均非本次引入**——
  `TestUnixPluginReloadAfterShutdownUsesReplacedFile`（dlopen 句柄复用断言，
  纯净 v8.1.6 同样失败=上游环境抖动，三边同形）；
  `TestAntigravityExecutorCAQSTwoTurnReplay`（已知 CAQS 基线偏差，sig-iso 哨兵
  vs 上游契约张力，v8.1.2/v8.1.4 生产树同形，留 reviewed 决策）。
- 留在 v8.1.6 之上的叠层：17 文件（+988/−21 + review7 适配），即 gemini-raw、
  agy-effort、daily-cap、crossauth-replay、sig-iso 五个。
- binary：WSL go1.26.10、CGO_ENABLED=1、`-trimpath -buildvcs=false`，Commit 串
  `0aaf6d1c…+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`，BuiltAt
  2026-11-26T16:41:00Z；sha `b0d4b7f0…`（80,916,616 字节，xz 后 18,882,816）。
- 上传：paramiko SFTP（复用 launcher target.json + Windows OpenSSH known_hosts
  严格校验），binary 48s + 插件 7s，远端解压后 sha 双向一致。

## 构建与预检（bwg）

- 插件 0.21.32：zip sha `a1a6b84e…`（checksums.txt + GitHub API digest 双验），
  `workbuddy-provider.so` sha `4d4225cd…`。
- 镜像：基底 `eceasy/cli-proxy-api:v8.1.6@sha256:e64b3e98…`（本地 RepoDigest 对
  Docker Hub tag API 双验，防镜像替换）；
  `cli-proxy-api-fork:v8.1.6-gemini-raw-daily-cap-authiso-sigiso`，id
  `sha256:d2312be1…`，标签（fork-binary/overlays/upstream-tag）全验。
- 隔离预检×2（`outputs/preflight-v816-20261127.ps1`，--network none）：
  scratch 最小配置启动零 fault；生产配置副本（端口 8392、空凭据目录）完整加载
  且插件 0.21.32 注册成功。6 条 error 全为 no-network 预期噪音（DNS/只读 kv）。
  **配方注**：v8.1.6 插件注册需要生产 `plugins.configs` 段，scratch 只验 binary
  启动，插件注册断言移到预检 2；入口无 `--config` 旗标（CMD 为默认路径挂载）。

## 执行事务

- 本代无新目录模型声明，无需先 `-Apply`（readiness 目录门绿）。
- 换芯（`outputs/cpa-v816-swap-transaction-20261127.ps1`）：DryRun
  `PREFLIGHT_OK` → `DRY_RUN_OK` → Apply `DRAIN_OK` → `PLUGIN_INSTALLED` →
  `IMAGE_ID_READBACK_OK` → `START_LOG_CLEAN version=v8.1.6
  plugin=workbuddy-provider:0.21.32 intl=closed_dark` → `RUNTIME_BINARY_OK` →
  `READINESS_OK` → `AUTH_TOKEN_REFRESHED_SAFE`（disabled 集不变）→
  `LANE_STATE_DELTA=none` → **`SWAP_OK`**（16:59:35Z）。备份
  `/opt/cliproxyapi/backups/20261126T165924.946984411Z-swap-v814-to-v816`。
  **配方注**：INTL 插件已不在主机（closed_dark 摘除），INTL 断言从 sha 比对改为
  文件不存在断言。
- 投影收尾（换芯后，提交 `263d237`）：`fork-binary.sha256` 424fb393→b0d4b7f0、
  version-check `EXPECTED_CN` 0.21.31→0.21.32（+测试 4 处）。`-Apply`：
  `GUARDRAILS_APPLIED`、17 项 `PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、
  `READY_STATUS=200`，备份 `/root/cpa-guardrails-backup-20261126T170558.843236669Z`。

## 验收

- strict doctor：**`DOCTOR_CONTRACT_OK`**，`MODEL_IDS_UNKNOWN=none`，
  `workbuddy-version=cn:0.21.32 intl:uninstalled`，
  `runtime-binary-hash` PASS；五车道 hold 面
  （chatgpt/workbuddy/antigravity=OPEN，claude=HELD unconfigured-tombstone-inert）。
- 受控探针（`outputs/accept-v816-postswap-20261127.ps1`，经 8318 过 admission，
  每发前车道空闲校验，不重试）：

| 探针 | 结果 |
|---|---|
| `gemini-3.8-flash-high` chat 流式 | 200，terminal，`PONG` |
| `deepseek-v4.1-flash`（插件 0.21.32） | 200，terminal，`PONG`，served 同名 |
| `glm-5.4` Responses 带 developer 项 | 200，`completed`，`GLM54_OK` |
| `gpt-6.1-sol` Responses（替代已退役 terra 项） | 200，`completed`，`SOL_OK` |
| `glm-5.3-flash` Responses | 200，`completed`，`OK` |

  （`gpt-6.1-terra` 400 `model_not_found` = 退役名的正确行为；探针脚本沿上代
  清单的该项已作废。）终态四车道空闲。
- cache-canary `glm-5.3-flash`：sample2 `4114 in / 4096 cached (0.9956)`、
  `HEALTH_OK`。
- 自然流量（换后 45 分钟）：glm-5.3-flash-wb 136 发全 200（缓存持续命中）；
  chatgpt sol 3×200 + 1×502 `server_is_overloaded`（上游容量波，
  `retry_after` 镜像，单发未放大）；封号信号（TOS/VALIDATION/RESTRICTED/quota）
  **0**；`chatgpt-risk.json` 无 hold；清洗 `agent_identity_neutralized` 135 发
  命中；`disable-codex-cloaking: true` 保持。
- 分层：`repo_verified` = 全量 go test 108 包 + guardrails 测试 70 passed；
  `filesystem_projected` = `SWAP_OK` + 17 项 `PROJECTION_HASH_VERIFIED`；
  `host_loaded` = strict doctor `DOCTOR_CONTRACT_OK`；`live_accepted` = 上表
  五发 + cache-canary。

## 回滚

镜像+插件同事务回滚：镜像回
`cli-proxy-api-fork:v8.1.4-gemini-raw-daily-cap-authiso-sigiso`
（`sha256:8f13c138…`），插件从
`/opt/cliproxyapi/backups/20261126T165924.946984411Z-swap-v814-to-v816/workbuddy-provider.so`
还原 0.21.31；随后 `scripts/remote/fork-binary.sha256` 改回 `424fb393…`、
version-check 改回 0.21.31 并重跑 `-Apply`。配置与投影回到
`/root/cpa-guardrails-backup-20261126T170558.843236669Z`。
WSL 合并树（`/root/build-v814/repo`，`merge-v814` 分支顶部
`adapt issue6332 review7 tests…`）与备份分支
`backup-merge-v814-pre-v816` 保留。

## 后续观察

- v8.1.6 新增 Home 本地插件执行器分发（#8515）：生产未启用 Home 执行器面，
  预期无行为变化；若 journal 出现 `executor dispatch` 新日志再评估。
- 上游插件 reload 测试抖动（dlopen 句柄复用）与 CAQS 基线偏差继续留观。
