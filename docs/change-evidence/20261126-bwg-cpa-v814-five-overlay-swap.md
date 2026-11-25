# 2026-11-26 bwg：CPA 升级 v8.1.4（五叠层重移植，去化 7966fix）+ WorkBuddy 插件 0.21.31 + 开放 glm-5.4

**性质**：真实远端写入 + release 级证据留档（redaction-first）。
**授权**：用户指令（2026-11-26）“开放 glm-5.4 路由/映射；ChatGPT Plus OAuth 不支持
gpt-6.1-terra 了？如果不支持，就去掉；……直接更新/升级 CPA/插件/zcode-start-plan
等的最新版”。时间均为 UTC。

## 裁决

| 事项 | 结论 | 依据 |
|---|---|---|
| `gpt-6.1-terra` | **保留**。Plus OAuth 仍然支持 | v8.1.4 的 `codex_client_models.json` 仍列该模型（`visibility: list`，`available_in_plans` 含 `plus`）；本号 48h 内 63 发 HTTP 200 完成；容器日志零条 `model_not_found`。上游 `5b857ecc` 移除的是 OpenAI **API key** 渠道的登记项，[#8012](https://github.com/router-for-me/CLIProxyAPI/issues/8012) 只影响 Free 号 |
| `glm-5.4` | 开放为 WorkBuddy CN 的第二个 pin | 用户指令；与 `deepseek-v4.1-flash` 共用 `workbuddy-oauth` 车道 |
| zcode-start-plan | **无需升级**，已是最新 | bwg 上 `zcode-proxy 4.9.1`，二进制 sha256 与 `TriDefender/zcode-api` v4.9.1 发布件 `zcode-proxy-linux-x64` 的 digest 一致（`1473c759…`），v4.9.1 即最新 release |
| WorkBuddy CN 插件 | 0.21.30 → 0.21.31 | 最新 release；0.21.31 只把保活线 400→50、硬禁用线 100→20 |

## 重移植（三方合并）

- base = 纯净 v8.1.2（`85225381`）；ours = 生产源码树（WSL `/root/build/src`，
  对应 binary `de6169b8`）；theirs = 纯净 v8.1.4（`38fe9295`，v8.1.2 之后 93 个提交）。
- 生产树相对 v8.1.2 的真实差异是 19 个文件（其中 3 个带 CRLF，合并前归一化），
  与上游 v8.1.2..v8.1.4 改动面重叠 10 个。
- 自动合并干净：执行器钩子（日量帽、签名隔离）、reasoning replay（跨号隔离）、
  `thinking/apply.go`（agy-effort）、gemini-raw 翻译器。钩子位置逐个复核：日量帽在
  TOS 暂停检查之后，签名隔离紧挨上游的签名消毒调用，响应面签名采集在 replay
  累加器之前。
- 冲突只在 7966fix 的两个文件（13 + 3 处），整体采用上游 `fe571afe`。差异仅在
  `willContinue` 标记的流式调用：自修是缓冲到完成再单发，上游是分片发但保持原索引；
  未标记的完整调用两边都是单 delta（生产真实形状）。
- 全量测试第一轮 3 个失败，逐个定性：

| 失败 | 定性 | 处置 |
|---|---|---|
| `TestAntigravityExecutorCAQSTwoTurnReplay` | 既有基线偏差（sig-iso 哨兵与上游「特性前签名」期望的契约张力；v8.1.2 生产树同样失败，纯净树通过） | 不动，留独立 reviewed 决策 |
| `…InterruptedMessageBeforeRealOutputPreservesPairingAndOrder` | 上游在 v8.1.4 把这个测试的期望反过来（“工具响应组在前，后到的用户打断在后”），与自修的 seen-condition（按时间序）正相反 | 去化 seen-condition（`antigravity_gemini_request.go` 取上游原样） |
| `TestIssue6332ReasoningReplayTwoTurns` | 上游新增测试，按不带凭据前缀的键读 replay 缓存；跨号隔离叠层把会话键改成了带前缀 | 测试读键改为作用域键，与上一代对 `pluginhost` 同类测试的适配一致 |

- 终态：合并树全量 108 包通过，只余 CAQS。另观察到上游抖动
  `TestAntigravityManagerExecuteAndStream_ToppedUpCreditsRecoverDuringQuotaCooldown`：
  隔离重跑 6 次各失败 1 次，**合并树、纯净 v8.1.4、生产树三边同形**，非本次引入。
  纯净 v8.1.4 全量 109 包通过。
- 留在 v8.1.4 之上的叠层：17 个文件（+990/−21），即 gemini-raw、agy-effort、
  daily-cap、crossauth-replay、sig-iso 五个；7966fix 的两部分都已不在树里。
- `go vet` 对 `internal/pluginhost/host_test.go:1684` 报 copylocks，纯净 v8.1.4 同样
  报，且不在 `go test` 的 vet 子集内。

## 构建与预检

- binary `424fb393baa7b6ed56dad146ea6cab8fe429372956690f3c0191e2a7de5b9026`（80,584,744
  字节）：WSL Ubuntu-24.04、go1.26.10、`CGO_ENABLED=1`、`-trimpath -buildvcs=false`，
  与上一代同一工具链；Commit 串
  `38fe929502f19f7cce03c69c19aacddf30f1ef5e+gemini-raw+agy-effort+daily-cap+crossauth-replay+sig-iso`，
  BuiltAt 2026-11-25T16:02:35Z；所需 glibc 符号最高 2.34。
- 上传：xz 后 18,838,048 字节（`87d8066f…`），流水线 SFTP 48 秒；主机解压后 sha 与
  构建值一致。
- 插件 0.21.31：zip sha `6b681ba8…` 与 GitHub 资产 digest 及 `checksums.txt` 均一致；
  `workbuddy-provider.so` sha `c8d69b79323dfb76e1e55863ea2b9c0a0474bd90bcb4a5f9d87a37edaad2d36b`。
  插件是 `-buildmode=c-shared` 的 C ABI 库，不要求与宿主同工具链。
- 镜像：基底 `eceasy/cli-proxy-api:v8.1.4@sha256:32fb7dfc272bf63daee4dcaaa679095aad431ceed31416f98ea55f3d1d7db77a`；
  `cli-proxy-api-fork:v8.1.4-gemini-raw-daily-cap-authiso-sigiso`，id
  `sha256:8f13c1388a511d14be1f960e80a4c6f071126437b26a0e1a69cd49b0398b3919`；bookworm
  基底内 glibc 门与执行冒烟通过。
- 隔离预检一（scratch 容器、无网络、最小配置）：新二进制启动，插件 0.21.31 注册，
  零 fault。
- 隔离预检二（生产配置的副本，改端口 8392、空凭据目录、独立数据目录）：v8.1.4 下
  配置完整加载，零 fault、零 error；无凭据所以只列出 API key 渠道的 4 个模型。
  **过程失误**：脚本在一个无匹配的 `grep` 上因 `set -e` 提前退出，scratch 容器与
  含密钥的配置副本（0700 目录）在主机上多留了约 3 分钟，下一步即清除
  （`container_present=0 copy_present=no`）。

## 执行顺序与事务

1. 换芯 dry-run 先失败于 `PREFLIGHT_READINESS_FAILED`：`cpa-health.py readiness` 的
   目录门对未声明的 `glm-5.4` fail-closed（2026-11-25 起即如此，与换芯无关）。
   顺序改为 **先 `-Apply`、后换芯**；换芯脚本的配置哈希由写死改为事务内快照。
2. `-Apply`：`GUARDRAILS_APPLIED`，17 项 `PROJECTION_HASH_VERIFIED`，`HEALTH_OK`，
   `READY_STATUS=200`，备份 `/root/cpa-guardrails-backup-20261125T172148.260268819Z`；
   config.yaml `b3d94c29…` → `a0cefa3a7…`。
3. 换芯（DryRun → Apply）：`PREFLIGHT_OK`（17:23:42Z）→
   `BACKUP_READY /opt/cliproxyapi/backups/20261125T172340.989537456Z-swap-v812-7966fix-to-v814`
   → `DRAIN_OK`（17:24:18Z）→ `PLUGIN_INSTALLED` → `IMAGE_ID_READBACK_OK` →
   `START_LOG_CLEAN version=v8.1.4 plugin=workbuddy-provider:0.21.31 intl=closed_dark` →
   `RUNTIME_BINARY_OK` → `READINESS_OK` → `AUTH_TOKEN_REFRESHED_SAFE` →
   `LANE_STATE_DELTA=none` → `SWAP_OK`（17:24:22Z）。
4. strict doctor：`DOCTOR_CONTRACT_OK`，17 项投影 MATCH，`MODEL_IDS_UNKNOWN=none`，
   `workbuddy-version=cn:0.21.31`，六条车道 OPEN。

## 验收

- 运行时：镜像 `cli-proxy-api-fork:v8.1.4-gemini-raw-daily-cap-authiso-sigiso`，容器
  17:24:19Z 启动，重启数 0，容器内二进制 sha `424fb393…`。
- 单发探针（`outputs/accept-v814-postswap-20261126.ps1`，经 127.0.0.1:8318 过
  admission，17:33Z；每发前确认该车道无 hold、无冷却、空闲，不重试）：

| 探针 | 结果 |
|---|---|
| `gemini-3.8-flash-high`，chat 流式 | 200，terminal，`PONG`；served `gemini-3.8-flash-n`（已知的免费号替换） |
| `gemini-3.8-flash-high`，流式 + 三个并行工具调用 | 200；`index [0,1,2]`，各 1 个 delta，参数 `{"tz": "UTC"}` / `{"tz": "America/Santiago"}` / `{"tz": "Asia/Tokyo"}` 均为完整 JSON，id 唯一 |
| `deepseek-v4.1-flash`（插件 0.21.31） | 200，terminal，`PONG`，served 同名 |
| `glm-5.4`，Responses 形状，带 developer 项 | 200，`completed`，`GLM54_OK`，in 33 / out 6，无 11128 |
| `gpt-6.1-terra`，Responses 形状 | 200，`completed`，`OK`，回显 `gpt-6.1-terra` |
| `glm-5.3-flash`，Responses 形状 | 200，`completed`，`OK` |

收尾时四条车道均空闲，无 hold、无冷却。

- 验收脚本首次运行在第一道闸门中止（17:31:52Z，ChatGPT 车道有在途请求，探针
  发数为 0）：原闸门对任何车道非空闲都中止。已改为只看即将探测的车道，在途最多等
  90 秒，hold 或冷却仍立即中止。
- 自然流量（换后一小时窗口，含换前约 25 分钟）：WorkBuddy `deepseek-v4.1-flash`
  397 发全部完成、零上游错误、缓存 0.98；`gpt-6.1-terra` 12 发自然流量里 10 发
  完成、2 发上游过载、缓存 0.97。审计的一条 WARN（Antigravity 车道
  `tiny_sse_non_tool_completed=1`）是本次的 PONG 探针：该车道窗口内只有 17:33:06Z
  与 17:33:10Z 两发，均为探针。
- 分层：`repo_verified` = Full 门禁（1891 passed，30 skipped，905 subtests）与 Go
  全量测试三边对照；`filesystem_projected` = `-Apply` 的 17 项
  `PROJECTION_HASH_VERIFIED` 与 `SWAP_OK`；`host_loaded` = strict doctor
  `DOCTOR_CONTRACT_OK`；`live_accepted` = 上表六发。

## 换后观察

- 换芯后 17:24:22Z 起自然流量即打到 `gpt-6.1-terra`，v8.1.4 下路由正常。17:25:21Z
  一发上游返回 502，正文是
  `service_unavailable_error / server_is_overloaded`（`retry-after: 344`），与换芯前
  当天的 terra 502 同形，属上游容量。它把唯一的 Codex 凭据打进约 5 分钟的瞬态冷却，
  期间 terra 不在 `/v1/models`（doctor 归入 `MODEL_CONTEXT_OPTIONAL_MISSING`）。
- Antigravity 侧两个 Claude 模型不在 `/v1/models`：换芯前 14:00Z 的 doctor 即如此，
  非本次引入，未归因。

## 回滚

`outputs/cpa-v814-rollback-transaction-20261126.ps1 -NewImageId <v8.1.2-7966fix 镜像 id>`
（先 `-DryRun`）：镜像回 `v8.1.2-gemini-raw-daily-cap-authiso-sigiso-7966fix`
（`sha256:d2804f23…`，binary `de6169b8…`），同一事务里把插件从
`/root/cpa-v814-build/rollback/workbuddy-provider-0.21.30.so` 还原为 0.21.30；随后把
`scripts/remote/fork-binary.sha256` 改回 `de6169b8…` 并 `-Apply`。配置与投影回到
`/root/cpa-guardrails-backup-20261125T172148.260268819Z`。
