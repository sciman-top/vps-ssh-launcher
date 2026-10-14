# 2026-10-14 BWG CPA v8.0.28 canary 升级证据

## 目标与范围

- 目标：仅将 `bwg` VPS 上的 CLIProxyAPI 从 v8.0.27 升级到官方 v8.0.28，并按风控纪律
  复核封号/限流/降智、吐字缓慢与缓存命中率三类关切。
- 官方 release：[router-for-me/CLIProxyAPI v8.0.28](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.28)，
  本次固定使用镜像
  `eceasy/cli-proxy-api:v8.0.28@sha256:5de1679350c368fb031d46900da30d97c1e9e9a3b150fc4c4181e7e65022a09e`。
- 升级前镜像为
  `eceasy/cli-proxy-api:v8.0.27@sha256:b7cf456bf6ef7f6489f18144e7cc166dbb8b2e5f46d6ee8881a321aaea563730`。
- digest 双信源逐字节一致：本地经 Xray 代理查询 Docker Hub registry
  （`Docker-Content-Digest` header）+ VPS 直连 `docker pull`（`outputs/fixture-v8028-namespaced.txt`
  的 step 2 按 digest 拉取成功）回报。
- 升级事务零配置改动；仅改动 Compose 的 CPA 镜像行。不触碰 `zz`、OAuth/API 凭据、Nginx、
  admission、路由清单、并发、重试或冷却策略。

## 上游变更与风控决策

v8.0.27 → v8.0.28 共 28 个提交、85 个文件（GitHub compare 全量 dump 存
`~/.zcode/tmp/cpa-v8028/diff-v8027-v8028.json`，只读消费，不入仓）。与本栈三类关切相关的
改动：

- **缓存命中**：`97ba1e89`（#6560，`fix/claude/tool-search-deferred-anchor`）在
  `tool_search` 发现首个 deferred tool 时保留 prompt cache 锚点，避免会话中途缓存失效导致
  重算与上下文回退。
- **封号/限流误伤**：`56eee6a4` 把 HTTP/2 closed connection 归类为 transient lifecycle
  drop（`sdk/cliproxy/auth/conductor_cooldown.go` + `connection_lifecycle_cooldown_test.go`），
  不再因传输层断连给凭据记冷却；`e235fb3d`/`e230a04b`/`81573f04` 修复并发 config 访问与
  reload 数据竞态，避免运行期配置错读造成的假失败。
- **降智与流式吐字**：`7795de19` 新增 `CodexSSEScanner` 修正事件数据切分；`b0db5448` 为
  OpenAI responses 流的 fallback item ID 加唯一前缀；`0d18d3b3` 由 registry 提供
  model-specific default max tokens；`57d005fe` 剥离越界 int64 数值边界、
  `a0fd8448` 保留多分支 anyOf/oneOf 联合、`02fc76b1` 恢复被包装的完整 `mcp__` MCP 工具名，
  降低 tool schema 被上游拒绝/截断的概率。
- registry 增删（`muse-wonder-1.0`、`web_search`、`claude-haiku-5-5`，移除 legacy gemini，
  新增 `gemini-4-argon(-high)`）**不影响本栈暴露的模型 ID**：post doctor
  `MODEL_IDS_UNKNOWN=none`。
- **无新增配置键**：`requests.streaming.keepalive-seconds`、`session-affinity`、
  `request-retry=0`、`transient-error-cooldown-seconds=60`、`disable-cooling=false` 全部保持
  既有值；`session-affinity-rebind-on` 延续周期决策不启用（缓存已近上限、单 OAuth 凭据）。

## 仓库切片

本周期 `scripts/remote/*.py` 零改动；夹具上传副本经远端 `UPLOAD_COMPILE=ok` 校验。

v8028 驱动脚本由 v8027 家族锚点替换生成，规范化（LF）SHA-256：

- `outputs/cpa-v8028-canary.sh` `85f8e38a5154e6bb2af437eb01a6a085f5e4f9d94533afa22bdcb2492e520c08`
- `outputs/cpa-v8028-fixture.sh` `b9b154d691fceb8d5c11f283063fd79c2787729b9a87ab628e8583d51a70f536`
- `outputs/cpa-v8028-probes.sh` `a2e162f58aa1767f42d91f8a379a24ba4ca9aedfb0ea75b441c6f061511e8217`
- `outputs/deploy-cpa-v8028-canary.ps1` `070692e3515d0b5a91b02f7df2b5bac7d902a0fc4cc0059d713d76de4df5a16d`
- `outputs/deploy-cpa-v8028-fixture.ps1` `2e5020398805f379ab163038e55c0ed2823704c49478d8c3b699900b916eb83d`
- `outputs/deploy-cpa-v8028-probes.ps1` `c311b321cdaa856494f58229cb9cd8f05636fbc3ea19548d1b85fd7ef84d3ee0`
- `outputs/cpa-v8028-stream-observe.py` `d7b9ef9f4f82021f5b354f0250b9aed33f3d24e0ffc7fc5d3d4c769c3c92d4e7`
- `outputs/deploy-cpa-v8028-stream-observe.ps1` `8f0ae9a16a0e058b1acf02f5d83ddf8a7c9f17a97868d72a54e2a81da57d8393`

锚点复核：canary 的 `EXPECTED_CUR`/`TARGET`/OK 日志行 digest 与 from-版本字面量均已跟随替换
（无 v8027 遗留的陈旧 digest 文本）；fixture 上传路径 `/tmp/cpa-acceptance-v8028.py`、
`/tmp/cpa-update-acceptance-v8028.py` 与包装脚本一致。

本周期唯一的 launcher 侧代码改动是 doctor 的 HEAD blob 哈希路径：

- `scripts/cpa_bwg_guardrails.ps1`：`Get-HeadBlobSha256` 由
  `Start-Process -RedirectStandardOutput <temp> -Wait` 改为直接 `[Diagnostics.Process]` +
  子进程 stdout **原始字节流**（`StandardError.ReadToEndAsync()` 并发排空后
  `StandardOutput.BaseStream.CopyTo`），不再经临时文件中转。
- 依据（`gh run view --job <id> --log`，CI 门禁 `ci.yml`）：
  - run `38150441987`（commit `e33423f0`，2026-10-11）：同一 HEAD 内 pin 文本读出正确的
    `5273f59e5b07cdaa13df1c66cb95bdb212905d94df9c906e3e813bee5b0218e0`，而
    `Get-HeadBlobSha256` 算出 `8a377cd12e231fb672f504202f5b6b451e467b7b182fc6d4252a34a2aa52a626`；
    windows 同一 commit 通过。
  - run `38203727221`（2026-10-12）同形；run `38370252302`（commit `6f3c7af`，2026-10-13）
    pin 为 `fbaeb631…`、算出 `d74ac1e8a093bd9728b07132a0cb161cc161ff624713d5f4b9309837940ca92c`。
    ⇒ 失败只在 ubuntu 的**临时文件中转**路径上复现，pin（走 `git show` 字符串捕获）始终正确。
- 边界：两个错值都无法从仓库内容复原——已穷举对应 blob 的**任意前缀**、按页边界
  （512/1K/4K/8K/16K/64K）的零填充与整段轮换、LF/CRLF/BOM/UTF-16/去尾换行等编码变换，
  以及 `6f3c7af` 全部 468 个文件的 blob，均无命中。可确证的是"临时文件捕获的字节 ≠ blob
  字节，且不是该 blob 的确定性变换"，与 `Start-Process -Wait` 的重定向刷盘竞态一致；
  具体损坏形态无法从留存日志复原（PowerShell 错误渲染截断长行，临时文件随 runner 销毁）。
- `tests/test_cpa_guardrails_script.py`：`ruff format` 修正夹具内联 here-string 的引号风格
  （main 自 6f3c7af 起 `lint:format` 红）。

## 前置证据

`outputs/doctor-20261014-pre-v8028.txt`（2026-10-14T01:31:36Z）返回 `DOCTOR_CONTRACT_OK`：

- v8.0.27@b7cf456b 容器 running、restart=0；全部投影 `MATCH`；`semantic-policy=OK`；
  `MODEL_IDS_UNKNOWN=none`；冷却态 `none`；`luna_state=available`；
  OAuth 9 天窗口 / 7d 零刷新失败；`gateway_tls_cert_days_left=88`；磁盘可用 30G。

## 隔离 fixture 验收

`outputs/fixture-v8028.txt`（首跑）与 `outputs/fixture-v8028-namespaced.txt`（mount+net
namespace 复跑）均以目标 v8.0.28 digest 提取二进制（sha 前缀 `1f836dc2d64a2731`），在
disposable namespace 中运行，未接触生产容器或真实 provider：

- overload：HTTP 200 + capacity marker + 1 次合成上游调用；cooldown：HTTP 503 + 0 次调用；
  62 秒后同进程恢复 HTTP 200、`completed=true`；`actual_health` 为 `HEALTH_OK`。
- updater 四场景全部契约符合：`start_fail` 与 `model_exposure` exit 1 + 恢复旧 Compose +
  rollback 日志；`transient` exit 10 + `UNVERIFIED` 日志；`success` exit 0；各场景
  pre-state 均 attempt=1 即 `HEALTH_OK`。
- 终态：`ACCEPTANCE_RESULT=PASS`、`ACCEPTANCE_EXIT=0`、`CLEANUP_OK`（泄漏扫描零命中）。

## 生产升级事务

收据见 `outputs/cpa-v8028-canary-run.log`：

- 01:54:16Z 经 `bwg` profile 开始，精确确认当前 v8.0.27 digest。
- 前置与后置 generation 均 `HEALTH_OK`；备份目录为
  `/opt/cliproxyapi/backups/20261014T015418.612501598Z-from-v8.0.27-canary-v8028`。
- 01:54:22Z 更新完成，退出码 0；未触发回滚，也未 prune 旧镜像。

## 升级后验证

生产读回（`outputs/post-v8028-probes.txt`）：容器 `v8.0.28@5de16793` running、restarts=0、
started=01:54:20Z，`repo_digests` 与目标一致。

quality-canary（`CPA_HEALTH_NO_OAUTH=1`，预算 7 个非 OAuth 请求）单轮全过：`gpt-6-astra`
42.971s、`gpt-6.1-sol-input` 2.040s、`glm-5.3-flash` 2.384s、`deepseek-v4.1-flash` 1.850s、
`gpt-5.6-terra` 6.999s、`glm-5.3` 2.155s、`muse-spark-1.3-contributor` 107.072s，全部
HTTP 200、`finish=stop`。astra 与 muse 偏慢属通道侧涨落（muse 上一周期亦为通道侧超时，
本轮成功返回），非升级回归。

cache-canary：首样本冷启动 `0/4114`，第二样本 `4096/4114`，命中率 `0.9956`，与既有基线
逐位一致；未改动 `support-prompt-cache-key` 或 session-affinity 配置。

stream observe（`outputs/cpa-v8028-stream-observe.txt`，`deepseek-v4.1-flash`）：
HTTP 200、首帧 0.61s、31 个 data frame、`gap_p50=0.02s`、`gap_max=0.25s`、`gap>5s=0`、
`done_frame_present=True`、首 data 前无注释帧（keepalive 未误插队）。

升级后即刻的 doctor（`outputs/doctor-20261014-post-v8028.txt`，仍走改写前的旧哈希路径）同样返回
`DOCTOR_CONTRACT_OK`、全部投影 `MATCH`，与"该竞态只在 ubuntu 复现"一致。

`outputs/doctor-20261014-post-v8028-verify.txt`（2026-10-14T02:15:39Z，**改写哈希路径后重跑**）
返回 `DOCTOR_CONTRACT_OK`、退出码 0：

- 容器 `v8.0.28@5de16793` running、restart=0；11 项投影全部 `MATCH`；`admission-integrity=OK`；
  `POLICY_OK`/`semantic-policy=OK`；`MODEL_IDS_UNKNOWN=none`；`model_substitution_warnings_7d=0`；
  冷却态 `none`；`luna_state=available`；OAuth 7d 零刷新失败；TLS 88 天；磁盘可用 29G；
  五个 maintenance heartbeat 全 success/exit 0。
- 24h 状态面：200×546、401×6、404×7、503×1、400×2；`admission-journal=OK`
  （`upstream_results=2726`）；lane reject 10 次全为 `chatgpt-oauth` 的 `cooldown`；
  capacity 事件 5 次（`gpt-6-luna:2`、`gpt-6.1-sol:3`），与前置同量级。
- 该次重跑同时是哈希改动的**真实主机证明**：新的字节路径对全部受管文件与 admission
  integrity pin 算出的 SHA-256 与远端部署逐字节一致（`cpa-admission.py` =
  `fbaeb6314c083c410059aa7c3cf84c5046234666a4fe8e00b7613ffcb552748c`，与 pin 相同）。

## 本地门禁

`outputs/gates-20261014-v8028.txt`：build、bandit（含 scripts）、ruff check、ruff format、
mypy 全部通过；pytest `571 passed, 1 skipped, 269 subtests passed`（唯一跳过项为仓库约定的
真实 SSH integration），门禁退出码 0。

首跑 `test` 门禁红：改写后的 `Get-HeadBlobSha256` 把 `Task.GetAwaiter().GetResult()` 的
`VoidTaskResult` 泄漏进输出流，pin 比对拿到数组；加 `[void]` 语义修正（改读
`BaseStream.CopyTo`）后单测与全门禁转绿。

推送后 CI run `38501393250`（commit `c4042d6`/`ea13655` 所在的 `main`）在 windows-latest 与
**ubuntu-latest 双双 success**——即本修复在原先必失败的平台上通过，是哈希路径改写的跨平台验收。

## 回滚入口

备份和 v8.0.27 digest 旧镜像已保留。发生本地 readiness/doctor 失败时，按
`docs/runbooks/cpa-manual-rollback.md` 从上述备份恢复 Compose，再使用旧 digest 执行
`docker compose up -d --pull never`，随后重新运行严格 doctor；本次未需要回滚。
本地代码切片回滚仅撤销 `scripts/cpa_bwg_guardrails.ps1` 与
`tests/test_cpa_guardrails_script.py` 的本次改动，不代替远端恢复。

## 分层结论

| 层级 | 结论 |
| --- | --- |
| `repo_verified` | 官方 release、compare 全量 diff（28 commits/85 files）、双信源 digest、fixture 两轮 PASS、全门禁 `571 passed` 通过；驱动脚本与上传副本均有哈希与编译核对。 |
| `filesystem_projected` | 生产受管文件零改动，post doctor 11 项投影全部 `MATCH`；Compose 镜像行已按 canary 原子替换。 |
| `host_loaded` | v8.0.28 目标 digest running、restart=0、generation/readiness 与 strict doctor（含哈希路径改写后重跑）通过。 |
| `controlled_live_replay` | 非 OAuth quality-canary 7/7 路 200/stop；GLM cache-canary 命中率 0.9956 与基线逐位一致；stream observe 首帧 0.61s、无 >5s gap。 |
| `natural_live_accepted` | **未宣称**。延续本轮纪律：未制造 OAuth/桌面自然会话；OAuth 依赖真实流量与 updater timer，muse 与 astra 的慢通道以自然流量/下次 doctor 观察为准。 |
