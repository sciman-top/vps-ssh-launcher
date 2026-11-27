# BWG CPA 升级 v8.1.7（五叠层无冲突合并）（2026-11-27）

**性质**：真实远端写入 + release 级证据留档（redaction-first）。时间均为 UTC
（换芯 `SWAP_OK=2026-11-27T04:55:01Z`，本地记 2026-11-27）。
**授权**：用户指令（2026-11-27）"https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.1.7，你自主在bwg VPS上调用/执行本项目的个人 VPS 自动化维护功能，直接更新/升级CPA/插件/zcode-start-plan等的最新版……注意防范多个OAuth账号的封号、限流、降智等的风险/风控，优化缓存命中率、TPS/TTFT/E2E、清洗系统提示词等"。

## 裁决

| 事项 | 结论 | 依据 |
|---|---|---|
| CPA v8.1.7 | 直接升级 | 上游 `47f18e4d`（v8.1.6 之上 3 提交）：#8516 修复 Unix 插件重载测试、`#8520` 修复 Gemini 合并工具响应后的用户消息排序（防 Vertex AI 400）、`#8519` 修复插件重载/重配/目录错误时调度器路由保持；无 OAuth/风控破坏性变更 |
| 五叠层 | 全部保留、零冲突合并 | 与 v8.1.6→v8.1.7 修改的 9 文件零交集，18 个叠层文件（+998/−23）形态不变 |
| WorkBuddy CN 插件 | 维持 0.21.32 | `luode0320/cpa-workbuddy-plugin` `registry.json` 与 releases 确认 `workbuddy-provider` 最新仍为 `0.21.32`；INTL 保持卸载/closed-dark |
| zcode-start-plan | 维持 `zcode-proxy 4.9.1` | `TriDefender/zcode-api` 最新 release 仍为 `v4.9.1`（11-20）；服务 `enabled-active`，仅监听 `172.17.0.1:18082`，无公网暴露 |
| Claude 车道 | 维持 `HELD` | `unconfigured-tombstone-inert`，未触碰 |
| 主机 `/run/reboot-required` | 留待人工窗口 | `bwg_full_maintenance.ps1 -Mode Observe` 报告主机存在 `/run/reboot-required`（阻塞无人值守 `docker`/`xray` 计划）；本次 CPA 换芯未重启宿主机 |

## 合并与构建（WSL `/root/build-v814/repo`）

- 备份分支：`backup-merge-v814-pre-v817`（指向 v8.1.6 合并树 `4ed8ca5`）。
- base = `69c166d`（纯净 v8.1.6 `0aaf6d1c`）；theirs = `upstream-v817`（纯净 v8.1.7 `47f18e4d82bd72f7d2b5ecf5ede93e4837a4625e`）；无冲突合并入 `main`，叠层 diff 保持 18 文件（+998/−23）。
- 全量 `go test ./...`：**109 包 ok**（较 v8.1.6 多 1 包，因 `#8516` 修复了 `TestUnixPluginReloadAfterShutdownUsesReplacedFile`），仅剩 1 项既有 CAQS 基线偏差（`TestAntigravityExecutorCAQSTwoTurnReplay`，sig-iso 哨兵 vs 上游契约，v8.1.2/v8.1.4/v8.1.6 同形）。
- binary：WSL go1.26.10、`CGO_ENABLED=1`、`-trimpath -buildvcs=false`，Commit 串 `47f18e4d82bd72f7d2b5ecf5ede93e4837a4625e+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`，BuiltAt `2026-11-27T04:50:29Z`；SHA `f0d6349a…`（80,945,320 字节，xz 后 19,223,228 字节）。
- 上传：Paramiko SFTP（复用 launcher `target.json` + 严格 `known_hosts`），50.9s，远端解压后双向 SHA 一致。

## 镜像与隔离预检（bwg）

- 基底镜像：`eceasy/cli-proxy-api:v8.1.7@sha256:fd49f56b…`（本地 RepoDigest 与 Docker Hub tag API 双验一致）。
- 派生镜像：`cli-proxy-api-fork:v8.1.7-gemini-raw-daily-cap-authiso-sigiso`，id `sha256:21ce0b61…`，`BOOKWORM_SMOKE_OK`，标签（`fork-binary` / `overlays` / `upstream.tag`）全验。
- 隔离预检×2（`outputs/preflight-v817-20261127.ps1`，`--network none`）：`SCRATCH_PREFLIGHT_OK version=v8.1.7 faults=0`、`CONFIG_PREFLIGHT_OK version=v8.1.7 plugin=0.21.32 errors=6`（6 条均为无网 DNS/只读 kv 预期噪音）→ **`PREFLIGHT_BOTH_OK`**。

## 换芯与护栏投影

- 换芯（`outputs/cpa-v817-swap-transaction-20261127.ps1`）：`DRY_RUN_OK` → `BACKUP_READY` → `DRAIN_OK` → `IMAGE_ID_READBACK_OK` → `START_LOG_CLEAN version=v8.1.7 plugin=workbuddy-provider:0.21.32 intl=closed_dark` → `RUNTIME_BINARY_OK` → `READINESS_OK` → `AUTH_TOKEN_REFRESHED_SAFE`（disabled 集不变）→ `LANE_STATE_DELTA=none` → **`SWAP_OK`**（`2026-11-27T04:55:01Z`）。备份 `/opt/cliproxyapi/backups/20261127T045451.468541274Z-swap-v816-to-v817`。
- 投影收尾（提交 `7eea7db`，更新 `scripts/remote/fork-binary.sha256` `b0d4b7f0`→`f0d6349a`）：`scripts/cpa_bwg_guardrails.ps1 -Apply` → `GUARDRAILS_APPLIED`、17 项 `PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、`READY_STATUS=200`，备份 `/root/cpa-guardrails-backup-20261127T045547.696541653Z`。

## 验收与风控/缓存审计

- strict doctor：**`DOCTOR_CONTRACT_OK`**，`runtime_binary_hash=MATCH`，`workbuddy-version=cn:0.21.32 intl:uninstalled`，`zcode-sidecar-service=enabled-active`，`zcode-sidecar-listener=OK`，`zcode-sidecar-public-socket=ABSENT`，`credential_ban_signals_7d=0`，三活跃 OAuth 车道 OPEN、`claude-oauth` HELD（`unconfigured-tombstone-inert`）。
- 换芯前 12h 自然流量与清洗审计：`agent_identity_neutralized=883`、`protocol_normalized=1198`；缓存 `turns=906`（`with_cache=865`，`115,328,512 / 120,650,724` = **95.59%**）；`holds_12h=0`。
- 受控探针（经 8318 admission，单发不重试，遵守外部 API 探测纪律）：

| 探针 | 车道 | 结果 |
|---|---|---|
| `gemini-4-hydrogen-xhigh` chat 流式 | `antigravity-oauth` | HTTP 200，`terminal=true`，`PONG` |
| `deepseek-v4.1-flash` chat 流式（插件 0.21.32） | `workbuddy-oauth` | HTTP 200，`terminal=true`，`PONG`，served 同名 |
| `gpt-6.1-sol` Responses（`effort=low`） | `chatgpt-oauth` | HTTP 200，`completed`，`SOL_OK` |
| `glm-5.4` Responses | `zhipu-coding-plan` | HTTP 429 `code=1308`（5h 窗口上限，13:00:20 本地重置）；admission 即时进入冷却（`hold=None cooldown=True`），零重试并 fail-closed 停止 |

## 回滚

镜像回 `cli-proxy-api-fork:v8.1.6-gemini-raw-daily-cap-authiso-sigiso`（`sha256:d2312be1…`），从 `/opt/cliproxyapi/backups/20261127T045451.468541274Z-swap-v816-to-v817/compose.yml` 恢复编排并 `docker compose up -d --force-recreate --pull never`；仓库 `scripts/remote/fork-binary.sha256` 改回 `b0d4b7f0…` 后重跑 `scripts/cpa_bwg_guardrails.ps1 -Apply`（投影备份 `/root/cpa-guardrails-backup-20261127T045547.696541653Z`）。WSL 备份分支 `backup-merge-v814-pre-v817` 保留。
