# BWG CPA 升级 v8.1.8（五叠层零冲突合并）+ WorkBuddy CN 插件 0.21.33（2026-11-28）

**性质**：真实远端写入 + release 级证据留档（redaction-first）。时间均为 UTC
（换芯 `SWAP_OK=2026-11-28T00:22:40Z`，本地记 2026-11-28）。
**授权**：用户指令（2026-11-28）"https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.1.8，你自主在bwg VPS上调用/执行本项目的个人 VPS 自动化维护功能，直接更新/升级CPA/插件/zcode-start-plan等的最新版……注意防范多个OAuth账号的封号、限流、降智等的风险/风控，优化缓存命中率、TPS/TTFT/E2E、清洗（系统/开发者/工具/User等）提示词等"。

## 裁决

| 事项 | 结论 | 依据 |
|---|---|---|
| CPA v8.1.8 | 直接升级 | 上游 `dbe641f28`（v8.1.7 之上 17 提交，75 文件 +9262/−291）：auth conductor 生命周期/冷却/刷新重构（#8523/#8537/#8543/#8556/#8553 测试）、auth 文件原子写、codex bootstrap 窗内计费额度错误 failover（GPT 车道正向）、Gemini functionResponse 分组合并、Gemini schema 清洗器去空枚举值（#8571）、watcher 原子改名续watch、antigravity 归一化降分配；无 OAuth/风控破坏性变更 |
| 五叠层 | 全部保留、零冲突合并 | 与 v8.1.8 改动文件交集仅 `internal/config/config_types.go`（上游 CodexConfig 注释 vs 叠层 AntigravityConfig 增段，异 hunk 自动合并）；合并后叠层 diff 保持 18 文件（+998/−23） |
| review7/issue6332 适配 | 本代无需新适配 | 上游 v8.1.8 未改 `issue6332_review7_test.go`/`issue6332_test.go`（合并保留 4ed8ca5 的 scoped-key 适配）；新增 `internal/cache/issue6332_delivery_test.go`、`handlers_stream_delivery_test.go`、conductor_issue*_test 均在叠层树下直过 |
| WorkBuddy CN 插件 | 0.21.32 → 0.21.33 | `luode0320/cpa-workbuddy-plugin` release `workbuddy-provider-v0.21.33`（2026-11-27T16:07Z）：插件自限内存上限+错峰启动抑制容器 OOM、面板去账号搜索；无 auth/调度/目录变更；zip `a5f67c66` checksums 验签，so `b8f27c15`；INTL 保持卸载/closed-dark |
| zcode-start-plan | 维持 `zcode-proxy 4.9.1` | `TriDefender/zcode-api` 最新 release 仍为 `v4.9.1`（11-20）；服务 `zcode-start-plan.service` active，仅监听 `172.17.0.1:18082`，无公网暴露，本次未动 |
| Claude 车道 | 维持 `HELD` | 未触碰 |
| 主机 `/run/reboot-required` | 留待人工窗口 | 与 v8.1.7 代相同；本次 CPA 换芯未重启宿主机 |

## 合并与构建（WSL `/root/build-v814/repo`）

- 备份链：`upstream-v817`（纯净 v8.1.7 导入）之上新建 `upstream-v818` = `a64556d`
  （纯净 v8.1.8 `dbe641f28`，导入树哈希与上游 tag 树哈希 `a89e5792` **逐字节一致**）。
  导入管线坑位两处：tarball 必须 `git -c core.autocrlf=false archive`（否则整树 CRLF
  假差异）；`git add -A` 漏 13 个被 .gitignore 命中的上游已跟踪文件（改 `-A -f`），
  另有 `assets/paidaxing.gif` 执行位需 `update-index --chmod=+x` 补回。
- 合并 `3110f63`（零冲突）；全量 `go test ./...`：**110 包 ok**，唯一失败 =
  `TestAntigravityExecutorCAQSTwoTurnReplay`（sig-iso 哨兵 vs 上游契约，v8.1.2/四代同形已知基线偏差）。
- binary：WSL go1.26.10、`CGO_ENABLED=1`、`-trimpath -buildvcs=false`，Commit 串
  `dbe641f286a489ecb39e3c041c554371e6add864+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`，
  SHA `17dc3d9c…`（81,060,104 字节，xz 后 18,921,792 字节，SFTP 48s 上传双端验一致）。

## 镜像与隔离预检（bwg）

- 基底镜像：`eceasy/cli-proxy-api:v8.1.8@sha256:29717a24…`（本地 RepoDigest 与 Docker Hub tag API 双验一致）。
- 派生镜像：`cli-proxy-api-fork:v8.1.8-gemini-raw-daily-cap-authiso-sigiso`，id
  `sha256:6b845e4e…`，`BOOKWORM_SMOKE_OK`，标签（`fork-binary` / `overlays` / `upstream.tag`）全验。
- 隔离预检×2（`outputs/cpa-v818-build/preflight-v818-20261128.ps1`，`--network none`）：
  `SCRATCH_PREFLIGHT_OK version=v8.1.8 plugin=0.21.33 faults=0`、
  `CONFIG_PREFLIGHT_OK version=v8.1.8 plugin=0.21.33 errors=6`（6 条均为无网 DNS/只读
  kv 预期噪音，与 v8.1.7 同形）→ **`PREFLIGHT_BOTH_OK`**。

## 换芯与投影

- 换芯（`outputs/cpa-v818-build/cpa-v818-swap-transaction-20261128.ps1`）：`DRY_RUN_OK`
  → `BACKUP_READY` → `DRAIN_OK` → `PLUGIN_INSTALLED` → `IMAGE_ID_READBACK_OK` →
  `START_LOG_CLEAN version=v8.1.8 plugin=workbuddy-provider:0.21.33 intl=closed_dark` →
  `RUNTIME_BINARY_OK` → `READINESS_OK` → `AUTH_TOKEN_REFRESHED_SAFE`（disabled 集不变）
  → `LANE_STATE_DELTA=none` → **`SWAP_OK`**（`2026-11-28T00:22:40Z`）。
  备份 `/opt/cliproxyapi/backups/20261128T002235.909103731Z-swap-v817-to-v818`。
- 仓库 repin：提交 `c7ff7c2`（`scripts/remote/fork-binary.sha256` `f0d6349a`→`17dc3d9c`）。
- **guardrails `-Apply` + strict doctor 延后**：收口时本仓工作树存在另一并行会话的
  admission 3012 持久锁活体 WIP（admission/bootstrap/pin/guardrail 投影源 10 文件未提
  交）；按活体 WIP 勿收编纪律不在其窗内投影（会把半成品 admission 一起带上产），待其
  提交后随其收口链一并 `-Apply`+doctor。换芯事务已直写主机 `/opt/cliproxyapi/fork-binary.sha256`
  = `17dc3d9c`，生产运行面不受延后影响。

## 验收与风控/缓存审计

- 五发探针（经 8318 admission，单发不重试；`outputs/cpa-v818-build/accept-v818-postswap-20261128.ps1`）：

| 探针 | 车道 | 结果 |
|---|---|---|
| `gemini-4-hydrogen-xhigh` chat 流式 | `antigravity-oauth` | HTTP 200，`terminal=true`，`PONG`（served=gemini-4-hydrogen 名变体，与上代同形） |
| `deepseek-v4.1-flash` chat 流式（插件 0.21.33） | `workbuddy-oauth` | HTTP 200，`terminal=true`，`PONG`，served 同名 |
| `glm-5.4` Responses（developer 项） | `zhipu-coding-plan` | HTTP 200，`completed`，`GLM54_OK` |
| `gpt-6.1-sol` Responses（`effort=low`） | `chatgpt-oauth` | HTTP 200，`completed`，`SOL_OK` |
| `glm-5.3-flash` Responses | `zhipu-coding-plan` | HTTP 200，`completed` |

- 四车道换芯后全 `LANE_IDLE`，零 hold/冷却；探针窗 journal 全部 `status=200 capacity=false`；
  换芯后 20 分钟容器日志零 panic/fatal/error。
- `cpa-health generation=HEALTH_OK`；**cache-canary hit_ratio=0.9956**（与 v8.1.6/v8.1.7
  代同值，缓存行为零回退）；换芯前 12h 自然流量清洗审计 `agent_identity_neutralized=92`、
  `protocol_normalized=94`、`lane_holds=0`。

## doctor 姿态回执（2026-11-28T00:35Z，strict 只读）

- **换代面全绿**：`runtime_binary_hash=MATCH`（repo pin `17dc3d9c` ↔ 容器内 binary）、
  container/image/listeners/nginx guardrails/syntax 全 OK、七类封号信号 7d=0、
  `zcode-sidecar-service=enabled-active`（credential-age 6d，observation_only）。
- **FAIL 仅 `admission-integrity` / `admission-health`**：并行会话提交 `3a17341`
  （admission 3012/zcode-suspension 加固，10 文件，本地未推送）领先主机一个
  admission 代际（主机仍 2bc34f73，服务 ActiveEnter=2026-11-27T14:07:06Z，当日无
  新 guardrails 备份）——即"本地 HEAD 领先主机"的文档化预期姿势。该代际的部署
  （-Apply/主机级模拟/受控实战/推送）属其收口链，本台账不代执行。

## 回滚

镜像回 `cli-proxy-api-fork:v8.1.7-gemini-raw-daily-cap-authiso-sigiso`
（`sha256:21ce0b61…`），从
`/opt/cliproxyapi/backups/20261128T002235.909103731Z-swap-v817-to-v818/compose.yml`
恢复编排并 `docker compose up -d --force-recreate --pull never`；同备份内的
`workbuddy-provider.so`（`4d4225cd`）回拷至 `/opt/cliproxyapi/plugins/`；仓库
`scripts/remote/fork-binary.sha256` 改回 `f0d6349a…` 后重跑
`scripts/cpa_bwg_guardrails.ps1 -Apply`。WSL 备份分支：`upstream-v818` 上一提交即
v8.1.7 合并态（`936fbc5`）。
