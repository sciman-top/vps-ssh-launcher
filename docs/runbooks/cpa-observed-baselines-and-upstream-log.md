# CPA 观测基线、上游 issue/PR 台账

从 `.workbuddy-ai/memory/MEMORY.md` 外移的参考性内容（2026-10-04）。MEMORY.md 只留结论与指针，
明细放这里，避免注入时被截断。

## 一、性能与延迟基线（2026-10-02 实测）

- **远端 TTFB**（`upstream_header_time`, route=responses, n=738）：p50 **11.88s** /
  p90 41.70s / p99 94.18s / max 220.80s；`request_time` p50 21.41s。
  本地占 0.15–0.25s（3.8–4.2%）。
- **本机 Direct API 端到端**（`codex-api.log.<date>` 的 `latencyMs`，n=237）：
  p50 **32.6s** / p90 82.9s / max 221.8s。
- **`queue_timeout` 会挂满 120s 再拒**（`cpa-admission.py:496-510` 注释表明**刻意**）
  ⇒ 客户端白等两分钟。
- **本机权威错误口径 = `~/.antigravity_cockpit/codex_local_access_logs.sqlite` 的
  `request_logs`**（字段 `error_category` / `latency_ms` / `requested_model` /
  `gateway_mode`，只读 `file:...?mode=ro`）。`error_category`：
  `quota_or_rate_limit`=429、`upstream_error`=5xx、`auth_failed`=401、
  `model_not_available`=404、`request_failed`=400。**比 `codex-api.log.<date>` 更全。**
- **只有容器日志 TZ = CST**（UTC +8 换算）；主机 `journalctl` 与 nginx 访问日志是
  UTC，二者可直接与 `time=[... +0000]` 对齐。**cooldown 可达 3.2–3.45 小时**
  （`retry_after` 实测 12432/12395/11744/11705 s，根因 `usage_limit_reached`）
  ⇒ **整条 `chatgpt-oauth` lane 全灭**（这是接受项，见
  [cpa-ban-throttle-incident-response.md](cpa-ban-throttle-incident-response.md)）。
- **本机 24h 失败率基线**（2026-10-03/04）：791 请求 / 138 失败（17.4%）；
  其中约 120 个是 `gpt-5.6-terra` 的 502/503（见下"未闭合"）。

## 二、上游 jlcodes99/cockpit-tools 的 issue/PR（作者 `sciman-top`）

- **2658**（issue，已关闭）= Provider Gateway `responses` 透传缺 Flush，**v1.3.65 已修**。
- **2676**（issue，open）= rebuild drops account concurrency
  （`apply_provider_gateway_template_settings:1566` 只复制 18 字段）⇒ **manifest 从不被重写**；
  **2677**（PR，open）= 补这两个字段，`MERGEABLE`；
  **2685**（issue，open）→ **2697**（PR，open，`feat/provider-gateway-account-admission`）。
- **⚠️ CI 状态修正（2026-10-03 22:3x 核实）**：2677/2697 都是
  `mergeStateStatus=UNSTABLE` + `MERGEABLE`，但 **`gh pr checks` = 0 条（workflow 从未触发）**。
  同期**其他** fork PR（如 `feat/pi-platform`）是 `action_required`，且
  `fix-codex-sidecar-stop-state` 曾 `success` ⇒ **维护者会批准，不是故意冷处理**。
  上游 `build-matrix.yml` 触发条件 = `pull_request: branches:[main]`（我的 base 确实 main，
  本该触发）；我的 fork 启用 Actions 但**只跑过 `main` 分支**（Sync Fork/CodeQL），
  PR 分支从未触发。⇒ **别断言"请批准 CI"**（没东西可批准）；已在 #2697 留评论说明状态
  并请求指引。**下次跟进先看 `gh pr checks <n>`，不要凭记忆下结论。**
- **2702**（issue，open，2026-10-03 提交）= **编辑 provider API Key 重算
  `api_provider_mode` ⇒ 10909 静默停止**。正文
  `outputs/cockpit-tools-issue-api-provider-mode-recompute.en.md`（英文，贴上游风格）；
  中文版 `.md` 为本机归因记录。建议修法：沿用账号已有 `api_provider_mode`，
  仅在缺失时按 baseUrl 推断。
- **#2697**：`admitDirectProviderAccount`（按账号占槽，等 `accountConcurrencyWaitMs` 后
  429 `account_concurrency_exceeded`）+ `recordDirectProviderBackoff`。
  `MaxAccountConcurrency<=0` 时闸门禁用 ⇒ **在 #2677 落地前运行时是 no-op**。
- **本机额外 4 处生成器 workaround**（`RequestRetry=0` / `StreamBootstrapBuffering=false` /
  `clampMaxAccountConcurrency` / `capAccountConcurrencyWaitMs`）——**不进上游 PR**。
- **上游"支持 N 并发"无证据**：CPA 有 `home_concurrency.go` 但远端容器日志命中 0。
  **本栈并发数字只有 2/3/6**（唯一的 4 是 `ADMISSION_MAX_PENDING`）。
- **Cockpit 补丁构建**：Go 1.26.3 在 `/c/Program Files/Go/bin`；
  `CGO_ENABLED=0 go build -trimpath -ldflags '-s -w' -o <out>.exe .`；
  验证 `gofmt -l`（**先 CRLF→LF**）、`go vet ./...`、`go test . -count=1`；
  导出 `git add -N <新文件>` 后 `git diff -- sidecars/cockpit-cliproxy`。

## 三、已闭合：客户端名字 ≠ 网关名字（2026-10-04）

本机 24h 有 **120 个 `gpt-5.6-terra` 502/503**（`gateway_mode=sidecar`、
`api_key_label=Provider Gateway: api-key-ec280ff6`），但远端 admission journal 同窗口
**没有任何 `gpt-5.6-terra` 的 5xx**。按时间+状态+计数对齐后闭合：

| 侧 | 窗口 | 记录 |
|---|---|---|
| 本机 10909（CST） | 14:14–14:19 | `gpt-5.6-terra` 502×3 + 503×3（同窗口其他流量只有 `gpt-6-luna` 200×14） |
| 远端 journal（UTC） | 06:14–06:19 | `lane=passthrough model=gpt-6.1-sol-input` 502×3 + 503×3，无 terra |

⇒ **10909 把客户端的 `gpt-5.6-terra` 转发成了 `gpt-6.1-sol-input`**（1:1 对齐）。
本机 terra 5xx 与 CPA 侧 sol-input 5xx 是同一批请求。

**映射不在磁盘上**：10909 的 `config.json`、`manifest.json`（`modelAliases` 只有
`gpt-5.6-sol`/`gpt-5.5`）、`~/.codex/cockpit-model-catalog.json` 都不含
`gpt-6.1-sol-input` ⇒ 它存在于**运行中的 sidecar 内存态**（与"生成器不重写 manifest、
磁盘值与运行值不一致"这条既有结论一致）。

**推论（重要）**：桌面**可选**的 `gpt-5.6-terra` 由槽位 1 `ai.input.im` 的
`gpt-6.1-sol-input` 承载。所以"保留 `gpt-6.1-sol-input` 不动"等价于保留
`gpt-5.6-terra` 的失败窗口。**改路由前必须先到网关 journal 确认真正服务的模型名。**

**方法论**：跨层对齐时按**时间 + 状态 + 计数**，不要按名字。名字可能被客户端侧
别名层改写；对不齐就标 open，不要归档到任何一层。

## 四、上游 CLIProxyAPI issue/PR 台账：codex 容量卸载与 `auth_unavailable`（2026-10-24 复核）

| 编号 | 状态（API 实测） | 结论 | 与 v8.0.40 的关系 |
|---|---|---|---|
| [#6668](https://github.com/router-for-me/CLIProxyAPI/issues/6668) | closed (issue) | 一次 503 把**所有** codex 凭据冷却 60s，随后 `auth_unavailable` 零出网 | 机制确认，未改变 |
| [#6851](https://github.com/router-for-me/CLIProxyAPI/issues/6851) | closed (issue) | 上述快 503 未记录：`warnLogAuthUnavailable` 只汇总 `blockReasonCooldown` | 机制确认 |
| [#6852](https://github.com/router-for-me/CLIProxyAPI/pull/6852) | closed, `merged=false` | 记录 transient 阻断的 `auth_unavailable`（日志修复） | **未进入 v8.0.40**；v8.0.40 仍写 "are in cooldown" |
| [#6856](https://github.com/router-for-me/CLIProxyAPI/issues/6856) | closed/Fixed (issue) | 10-18 起上游 `response.metadata` 帧使 `stream-bootstrap-buffering` 容量失败转移失效 | **修复已在 v8.0.40 源码**（`isCodexBootstrapBufferableEvent` 含 `response.metadata`） |
| [#6860](https://github.com/router-for-me/CLIProxyAPI/pull/6860) | closed, `merged=false` | #6856 的补充回归覆盖 | v8.0.40 已含等价 allowlist |
| [#3229](https://github.com/router-for-me/CLIProxyAPI/pull/3229) | open | 容量映射 429+短 Retry-After→请求内重试 | 未合并；会增加请求放大，与 no-auto-retry 冲突，仅 watch |
| [#5514](https://github.com/router-for-me/CLIProxyAPI/pull/5514) | open | 单次上游 404 冷却 12h→10min | 未合并，仅 watch |

**本部署约束**：codex OAuth 只有 1 个凭据（`oauth_codex_files=1`），任何"失败转移"类
修复都没有第二对象；`stream-bootstrap-buffering` 保持 `false`（理由见
`scripts/remote/cpa_policy.py` 的 `EXPECTED_CODEX`）。单凭据下的正确处置是等窗口或切
非 OAuth lane，不是加账号、清冷却或开重试。

## 五、上游 CLIProxyAPI post-v8.1.1 watch 台账（2026-11-23 实拉）

生产 fork upstream 锚 = `6eb8554f`（v8.1.1）。2026-11-21 起 `gh` 只读实拉的
open 缺陷 watch 清单（处置详见各 runbook，索引如下）：

| 编号 | 一句话 | 本栈处置 |
|---|---|---|
| [#7960](https://github.com/router-for-me/CLIProxyAPI/issues/7960) | 全局 signatureCache 无凭据维度→跨号签名注入 | **v8.1.2 已修（85225381）**：缓存按凭据域+unscoped 恢复 fail-closed；sig-iso 请求侧剥离仍独有（客户端回带面），见封号 runbook"上游签名缓存缺口"节 |
| [#7961](https://github.com/router-for-me/CLIProxyAPI/issues/7961) | transport scope 不含 grant 代→重登后跨号 keep-alive 复用 | **v8.1.2 已修（bffe6e7e）**：transport 按 auth 身份+refresh grant digest 分池；升级前维持重登后 restart 容器纪律 |
| [#7946](https://github.com/router-for-me/CLIProxyAPI/issues/7946) | v8.1.1 大上下文流式内存尖峰 ~2.3 GB | 并发 320K+ 容器无解释重启时首查；512MB 限制勿上调，见衍生 RFC"构建车道内存护栏"节 |
| [#7934](https://github.com/router-for-me/CLIProxyAPI/issues/7934) | compaction 把 MAX_TOKENS 截断摘要密封为 completed+清零计数 | **v8.1.2 已修（dc2455d9）**：升级前 320K 试用期间"变笨"类投诉先核此项 |
| [#7952](https://github.com/router-for-me/CLIProxyAPI/issues/7952) | forked session 失败逐出父会话 affinity 绑定 | **v8.1.2 已修（ee5225e1）** |
| [#7924](https://github.com/router-for-me/CLIProxyAPI/issues/7924) | disableCooling=true 时 TOS_VIOLATION 重置 Unavailable | **v8.1.2 已修（14cc6ad1）**：TOS 终态在 skip-cooldown 分支也保持 |
| [#7957](https://github.com/router-for-me/CLIProxyAPI/issues/7957) | 503 MODEL_CAPACITY_EXHAUSTED 忽略 RetryInfo/Retry-After | **v8.1.2 已修（ae3d80f7）**；admission 镜像层此前已兜住 |
| [#7964](https://github.com/router-for-me/CLIProxyAPI/issues/7964) | Claude→Gemini 翻译器丢 stop_sequences/output_config.format | 新 watch：本栈 desktop 流量不带 stop_sequences，低影响 |
| [#7963](https://github.com/router-for-me/CLIProxyAPI/issues/7963) | 无 init PID 1 时插件孙进程僵尸堆积 | 新 watch：2026-11-23 实测容器 defunct=0；若出现堆积再评估 compose `init: true` |
| [#7966](https://github.com/router-for-me/CLIProxyAPI/issues/7966) | **v8.1.2 tag 自带回归**：并行工具调用流索引重复+参数拆裂（`4df055de`）、中断消息配对错乱（`ae88b00c`）；本栈已实证 FAIL | **已触发回滚**（v8.1.1-sigiso 复役）；上游修复落地后重放换代配方（全量测试门） |

**换代指引（2026-11-23 晚更新：v8.1.2 已发布）**：上游 **v8.1.2 tag =
`85225381`（2026-11-23 14:49Z，51 commits）**整批收编了上表已修项与
#7914–#7920 修复波、`b8e3bc66`（请求对齐/最小化）、`2f86992c`
（thinking-level 原生路由+意图读取）、大批 Claude/Gemini 翻译质量修复
（空工具参数/提前终止/finish reason/grounding/compaction 校验）。
"整批 sync"判定成立，但**五叠层与 v8.1.2 改写面全部相交，v8.1.1 版
overlay tarball 不可复用**（叠层是整文件替换形态）：

| 叠层 | 替换文件 | v8.1.2 冲突面 |
|---|---|---|
| gemini-raw | `internal/translator/gemini/openai/responses/*` | v8.1.2 gemini 翻译修复波触及同域，需 diff 重生成 |
| agy-effort | `internal/thinking/apply.go`、`helps/model_capabilities.go`+2 测试 | apply.go +64/−6 直改；**先判超代**：上游原生已按 reasoning_effort/thinking_level 选档（`helps/antigravity_thinking.go`、`thinking/antigravity_intent.go`），叠层测试全过后才决定保留/收缩/退役 |
| guard | `config_types.go`、`antigravity_daily_cap.go(+test)`、`execute.go`、`stream.go` | execute +65/−106、stream +34/−62 重写；daily-cap 挂钩需重打 |
| crossauth | `antigravity_reasoning_replay.go` | **保留**：上游 replay KV 有意不按凭据分域（`antigravity_reasoning_replay_cache.go:564` 注释明言保 failover 连续性），#7846 仍为 open PR 未合并，本叠层仍是独有保护 |
| sig-iso | `antigravity_signature_auth_isolation.go(+test)` 新文件 + request/execute/stream/tokens 挂钩版 | request +205/−22、execute/stream/tokens 重写；v8.1.2 的缓存级隔离（85225381）与请求侧剥离互补，**保留并 rebase** |

换代走人工事务（updater 对 fork 镜像 `FORK_IMAGE_REBUILD_REQUIRED` 防呆
照旧），构建必须继承 `scripts/cpa_fork_rebuild.ps1` 内存护栏，overlay 逐层
重生成+`go vet`+`go test -race`+swap 事务（drain/备份/`IMAGE_ID_READBACK`/
`AUTH_BYTES_UNCHANGED`/8318 验收）全流程见 11-23 sig-iso 台账先例。

**2026-11-23/24 已执行（终态更新）**：v8.1.2 五叠层代首上产 12.5 分钟因
#7966 tag 回归回滚 v8.1.1-sigiso；随后 **fork 内修两处回归重放换代**——现役
`v8.1.2-gemini-raw-daily-cap-authiso-sigiso-7966fix`（id `d2804f23`、binary
`de6169b8`、SWAP_OK 17:23:27Z、8318 并行工具调用功能实弹三调用 index [0,1,2]
单 delta 完整参数=修复直证），配方与契约裁决见
[20261124-bwg-cpa-v812-7966fix-swap.md](../change-evidence/20261124-bwg-cpa-v812-7966fix-swap.md)。
"重登后 restart 容器"纪律随 #7961 正式修复**已退役**。硬门纪律=fork 构建
必须全量 `./internal/...`（已知的 CAQS 基线偏差与 cmd 负载 flake 见 7966fix
台账"硬门与基线偏差"节）。

**已闭合：`chatgpt-oauth/gpt-6-luna` 低表观缓存命中率（0.25–0.27）的会话级归因（2026-11-23 修正）**：

- **日志字段事实修正**：`cpa-admission.py` 的 `_trace_context` 已在每条 `upstream_result`
  末尾输出 `client_request_hash=<12-hex>`（来自 `X-Client-Request-Id`）与
  `instance_hash=<12-hex>`（来自 `X-Cockpit-Instance-Id`），并由
  `scripts/cpa_cache_reset_probe.py` 直接消费；无需开启 CPA `request-log` 或新增日志字段。
- **24h 实测归因（`cpa_cache_reset_probe.py --ssh-profile bwg --hours 24`）**：
  1. **原生多轮会话命中正常**：`gpt-6-luna` 作为主对话模型连续多轮运行时（如会话
     `6a21bb035db8` 7 轮 95.3%、`4c2c35e05e1b` 5 轮 96.5%、`9cb5bd1fd351` 4 轮 90.4%、
     `806c7701c05f` 4 轮 90.3%），缓存命中率达 **90%–97%**，与同凭据下的
     `gpt-6.1-sol`（0.94）、`gpt-6.1-terra`（0.94）、`gpt-6.2-sol`（0.95）完全一致。
  2. **跨模型单发（one-shot）委派稀释**：本机 `~/.codex/config.toml` 配置了
     `review_model = "gpt-6-luna"` 且多个只读/快速子代理（`quick_triage`、
     `documentation_review`、`luna_max`）绑定 `gpt-6-luna`。当主线程在
     `gemini-4-neon-xhigh`、`gemini-4-argon-high` 或 `gpt-6.1-sol` 上累积 140k–419k
     上下文后派生单发 review/子代理到 `gpt-6-luna` 时，请求复用父会话
     `client_request_hash`，但因 OpenAI Prompt Cache 按模型隔离，该单发大上下文请求在
     `gpt-6-luna` 上只能命中 ~6,016 tokens 的静态系统前缀（或 0），单次产生 200k–419k
     uncached tokens；同时同一秒并发派生的 2–3 个子代理在 `chatgpt-oauth`
     （`max_inflight=2`）上还会制造 `waited_ms` p90≈22.1s 的本地排队尖峰。
3. **结论**：`gpt-6-luna` 的 0.25–0.27 是**跨模型 one-shot 派生流量在聚合分母上的统计稀释**，
   而非服务端 session/prompt_cache_key 退化或账号降智。

## 六、post-v8.1.2 dev 增量与 2026-11-24 实测（2026-11-24 实拉）

生产 fork upstream 锚 = `85225381`（v8.1.2）+ `7966fix`。`main` 无新 tag；
`dev` 在 v8.1.2 之后 27 个提交（至 `b82d3d4e`）。只列与风控/翻译面相关项：

| 提交 | 一句话 | 本栈处置 |
|---|---|---|
| `fe571afe`（#7966） | 上游官方修复并行工具调用流：未标记的完整 functionCall 立即作单 delta 发射，并支持 partialArgs/willContinue 续接 | 仅在 dev。下次 rebase 以官方实现为准去化 `7966fix` 叠层，去化前跑全量 `./internal/...`（TestIssue6825 族） |
| `38067a29`（[#7967](https://github.com/router-for-me/CLIProxyAPI/issues/7967)） | `requestId` 改斜杠分隔 `reqType/<uuid>` 并保留 normalizer 已提供的值；原生 Gemini 形态请求统一工具输出键与单段 systemInstruction | 仅在 dev；翻译器仍写 `functionResponse.response.result`。对封号率无数据 |
| `20be744e` / `d1bdee85` / `9bce4825`（#7990–#7992） | 上游 HTTP 200 但空体、非 SSE 或非 JSON 时改判可重试 502，不再记为成功 | 新 watch：现役代仍把这类响应当零输出成功，降智类投诉先核此形；admission 的 `empty_text_completed` 计数可观测 |
| `7be2a678` / `b82d3d4e`（#8000/#8001） | Gemini thoughtSignature 清洗补 snake_case 与空签路径 | 与 crossauth/sig-iso 同域，rebase 时对照 |
| `0286e5d1` / `6c2308a1` / `06225fcf`（#7988/#7989/#7968） | 签名缓存模型组归一、空 scope 保留 Gemini 哨兵、首次刷新保留 grant 身份 | v8.1.2 缓存级隔离的补丁，rebase 时整批带入 |
| `e9dd8bad` / `3b20ee22` / `335b6987` / `14a95b19` / `e9a92d25`（#7997/#7995/#7970/#7985/#7969） | replay 快照失效、LCP 命名空间拆分、websocket nil auth、thinking-group 输出上限、model enum 快照 | 无直接风控动作 |

**[#7977](https://github.com/router-for-me/CLIProxyAPI/issues/7977) 分诊后的三条口径**
（维护者 2026-11-24 回复；报告项均未定论）：

- 503 容量错误的 `continue` workaround **判否**：维护者建议对
  "No capacity available" / "MODEL_CAPACITY_EXHAUSTED" 配 `action: continue`
  做请求内换号。本栈 agy 池只剩 2 个同为 flash-only 的号，容量 503 是后端面
  而非凭据面，换号只多一次上游接触；维持 `request-retry=0` +
  `max-retry-credentials=1` 的单次尝试语义，由 admission 镜像 Retry-After。
- Gemini 3.8 Flash 工具续轮"0-thinking 塌陷"（#7977 第 2 点、#8013）：维护者
  认为代码证据不足以认定是代理造成，未采纳合成续轮。仅 watch：flash-high
  工具循环若出现空 reasoning 加截断短答，先与官方客户端同题对照再归因。
  本栈 7 天 flash-high 103 发完成里 `truncated_text_completed` 与
  `empty_text_completed` 均为 0。
- 后台 `fetchAvailableModels` 探测洪水：维护者要求时间线与计数证据。本栈
  容器日志 info 级不记录该调用，`OVERLOADED_TOO_MANY_RETRIES_PER_REQUEST`
  自容器启动以来 0 次；空闲 12 分钟（journal 零上游结果）的 5 秒粒度套接字
  采样未见出站连接。**可量化的后台接触来自 token 刷新**：Google 返回的
  `refresh_interval_seconds` 为 1399/1657，每号每天约 53 次刷新，每次按
  #7994 配一次 `loadCodeAssist`（维护者以 working as intended 关闭）；版本号
  拉取每 3 小时一次。`per-credential-daily-cap` 只计 execute/stream，不含
  这些调用。在 fork 内跳过它需要重新构建与 swap，且没有证据表明它计入执法，
  暂不做。

### 2026-11-25 身份字面量作用面复审（system instruction 与 user 角色）

上游对 `systemInstruction` 提供三档机制，dev 自 v8.1.2 起无新增：

| 机制 | 语义 | 本栈 |
|---|---|---|
| `sensitive-words` | 子词间插 U+034F 零宽字符 | 自 11-17 起失效，仅为兼容保留 |
| `sensitive-words-replace` | 大小写不敏感子串的字面替换（最长优先） | 在役，4 个键 |
| `sensitive-patterns-replace` | 有序 RE2 规则，优先于词表，空替换即删除 | 在役，3 条：Claude Code 整句、`DeepSeek Harness` 短语、ZCode / CodeBuddy / WorkBuddy 身份句 |

三档都只作用于 `request.systemInstruction`。本栈 agy 车道 7 天 846 发里 831 发
是 Responses 协议，WAF 型裸 429 只有 11-22 的 fork 会话（哈希 `1b0aa645df9f`）。
离线对照（本地 rollout 对 journal 结果）：

| 线程 | 成功 / 快拒 429 | user 角色里的身份句 | 工具输出里的 `codex` |
|---|---|---|---|
| `0f43c4f7c4ff` | 22 / 0 | 0 | 2005 |
| `17fdba2e83ea` | 78 / 0 | 0 | 572 |
| `98a98d8d465c`（同期对照） | 44 / 0 | 0 | 468 |
| `1b0aa645df9f`（fork） | 0 / 3 | 5（压缩交接摘要） | 157 |

成功的会话同样带压缩交接摘要、同样在压缩后用过 agy，差别只在身份句所在的
角色。11-25 00:03Z 用当日 Codex 桌面端信封加重建的该会话历史，经 8318 现行
管线对 `gemini-3.8-flash-high` 单发回放，上游 610ms 返回裸
`RESOURCE_EXHAUSTED`；按约定只发一发。处置见
[封号 runbook](cpa-ban-throttle-incident-response.md) 的 2026-11-25 复审条。

同日后续（用户要求覆盖 ZCode、Claude Code、WorkBuddy、DeepSeek Harness）：
规则改走句子级，因为整词键会改写 ZCode 提示里的 `.zcode/skills` 路径。规则由
Go RE2 编译执行，部署前在 WSL 按上游顺序实测；部署后管理视图顶层
`antigravity.sensitive-patterns-replace` 为 3 条，零条无效正则告警（注意管理视图是
扁平布局，不在 `oauth.providers` 下）。逐客户端只发清洗后的提示，全部 `200`，
没有可二分的对象：Claude Code、DeepSeek Harness、ZCode、CodeBuddy + WorkBuddy
各一发（直连 8317），卡死历史经 8318 一发，WorkBuddy 车道一发。ZCode 起步套餐
车道在非免费时段，未做线上验证。过程、回滚与边界见
[多车道中性化台账](../change-evidence/20261125-bwg-agent-identity-neutralization.md)。

### 2026-11-24 `chatgpt-oauth` 流内过载波（单凭据实测）

- 02:00Z–12:00Z：76 发里 31 发 `capacity=true`（41%），全部是 HTTP 200 流内
  第 4 帧 `response.failed`，携带
  `{"type":"service_unavailable_error","code":"server_is_overloaded"}` 与
  `retry-after≈347s`；四个模型均匀分布，53 token 的 watchdog 探针同样被甩。
  此前 48 小时约 900 发仅 2 发。
- OpenAI 状态页同时段无事故，上游 issue 近两日无同形集中报告，原因在网关侧
  **无法定论**（上游负载突发，或该账号被降优先级）。#4679 的判别项见下节
  2026-11-25 更正：当时写的「出站身份为官方 `codex_cli_rs`，已核」不成立，
  实际出站是 `codex-tui`。
- 网关行为符合设计：`request-retry=0` 不重打，Retry-After 镜像为模型级窗口，
  同车道兄弟模型不连坐。判读升级条件：持续超过 24 小时，或官方客户端直连
  同账号不复现同形错误——届时按账号级软限流处置并停下复核。

### 2026-11-25 第二轮复审：出站身份地面真相、官方请求形状、v8.1.3

**出站身份**（线上保留的错误转储，入站 `HEADERS` 对比发往 chatgpt.com 的
`API REQUEST 1`；只读头名与三个非机密头的值）：

| 入站客户端 | 入站 `Originator` / `User-Agent` | 发往上游 |
|---|---|---|
| Codex Desktop 0.171.0-alpha.13.2（Windows 11） | `Codex Desktop` / `Codex Desktop/0.171.0-alpha.13.2 …` | `codex-tui` / `codex-tui/0.154.0 (Mac OS 26.5.2; arm64) iTerm.app/3.6.11 (codex-tui; 0.154.0)` |
| 小探针（`/v1/chat/completions`，约 506 字节） | 无 Originator / `codex_exec/0.142.3 (Windows 10.0.26200; x86_64) …` | 同上 |

- cloak 是无条件覆盖（v8.1.2 `codex_executor_request.go` 的
  `applyCodexCloakingHeaders`，在 `applyCodexHeadersWithSIWC` 末尾执行）：
  `User-Agent` 与 `Originator` 恒写常量，客户端真实值、`codex-header-defaults`
  与 auth `header:` 规则都被盖掉；`x-codex-window-id`（桌面专属）、
  `x-codex-beta-features: remote_compaction_v2`、`x-codex-turn-metadata`、
  `thread-id` 则原样转发。结果是一个自相矛盾的复合指纹（自称 macOS iTerm 上的
  0.154.0 终端客户端，却带 Windows 桌面窗口 id 与新版本特性标记），并与所有
  CPA 部署共用同一串 UA。
- [#4679](https://github.com/router-for-me/CLIProxyAPI/issues/4679) 的 A/B：
  `Originator: codex_cli_rs` 4/4 成功，`codex-tui` 立即流内
  `server_is_overloaded`，只改 UA 无效。本栈发出的正是后者；24h 容量事件 34 发
  （流内过载 14、502 19、503 1）。**cloak 开关对过载率的影响没有测过**——
  要改远端配置并做受控 A/B，属用户裁决。
- 取舍：本栈 ChatGPT 车道的 agent 流量全部来自官方客户端，关 cloak 即透传各自
  真实且自洽的官方指纹；代价是探针类请求露出真实 UA（无 Originator 时仍回落
  `codex-tui`）。维护者「保持开启」的建议面向第三方客户端。**2026-11-26 更新**：
  过载波持续超 48h 后经用户授权已通过 `upstream.codex.disable-codex-cloaking: true`
  投影落地（`cpa_policy.py` 同步钉定），详见
  [20261126 台账](../change-evidence/20261126-bwg-cpa-disable-codex-cloaking.md)。

**官方客户端请求形状**（同一转储的 `REQUEST BODY`）：顶层键为
`client_metadata / include / input / model / parallel_tool_calls / prompt_cache_key /
reasoning / store / stream / text / tool_choice / tools`，**没有顶层
`instructions`**；`input[0]` 是 developer 角色的基础提示（约 18K 字符），其后是
`<app-context>`（约 46K 字符）等；顶层 `tools` 18 个；`client_metadata` 含
`x-codex-installation-id` 与 `x-codex-window-id`。与源码一致
（openai/codex `codex-rs/core/src/client.rs` 的 `build_responses_request`，
2026-11-25 读 HEAD）。含义：经 CPA 翻译的第三方客户端请求（顶层 `instructions`
加顶层 `tools`）在结构上即可分辨，不依赖提示词内容。ChatGPT 车道上清洗身份句
对 OpenAI 侧的识别没有实质作用，不为此建规则。

**GPT-6.2 与顶层 `instructions` 的缓存**：OpenAI 向 OpenCode 确认 GPT-6.2 把
顶层 `instructions` 排除在提示词缓存之外并置于输入末尾，建议放进 `input` 的
首条 developer message（[anomalyco/opencode#64550](https://github.com/anomalyco/opencode/issues/64550)，
2026-11-18；GPT-6.1 正常缓存）。本栈官方桌面端不带顶层 `instructions`，不受
影响。`gpt-6.2-astra`（API key 渠道）的自然流量另有一个与摆放位置无关的现象：
48h 内 48 组「上下文增长不足 1500 token」的相邻请求，增长中位数 371、未命中
中位数 17,374；同一客户端在 `gpt-6.1-sol` 上是 190 / 381。未命中量约占输入的
16%。当日在该付费渠道做了一次有界实验（developer / instructions / system /
user 四种摆放各 2 发，约 13 万输入 token）：第二发都只命中 81–86%。**结论：
这是该渠道自身的缓存比例上限，网关侧没有杠杆，未改代码。**

**v8.1.3 已发布**（2026-11-25T04:26Z，v8.1.2 之后 45 个提交）：

| 条目 | 本栈处置 |
|---|---|
| `fe571afe`（#7966） | 官方修复并行工具调用流；下次 rebase 以官方实现去化 `7966fix`，去化前跑全量 `./internal/...` |
| `38067a29`（[#7967](https://github.com/router-for-me/CLIProxyAPI/issues/7967)） | Antigravity 出站线格式对齐：requestId 斜杠分隔、`functionResponse.response.output`、单段 systemInstruction。与官方二进制一致性的直接改进，rebase 时带入 |
| `06225fcf` / `0286e5d1` / `6c2308a1`、`7be2a678` / `b82d3d4e`、`e9dd8bad` | 签名缓存、签名清洗、replay 快照；与 crossauth / sig-iso 同域，rebase 时逐层对照 |
| `20be744e` / `d1bdee85` / `9bce4825` | 上游 HTTP 200 但空体时改判可重试 502 |
| `5b857ecc`（main，v8.1.3 之后） | 移除登记为 obsolete 的 GPT-6.1 Terra；[#8012](https://github.com/router-for-me/CLIProxyAPI/issues/8012) 报 Free 号对该模型 `model_not_found`。移除的是 OpenAI **API key** 渠道的登记项，v8.1.4 的 `codex_client_models.json` 仍列该模型（`visibility: list`，`available_in_plans` 含 `plus`）；本栈 Plus 号 48h 内 63 发 200 完成、零条 `model_not_found`，**保留**（2026-11-26 用户条件指令“不支持才去掉”，条件不成立） |

生产是 fork，换代要重放五个叠层并过全量测试门；新鲜 tag 先 soak（按惯例 72h，
最早 2026-11-28 04:26Z）。WorkBuddy CN 插件上游已有 0.21.31，在役 0.21.30。

**目录泄漏**：`glm-5.4` 经 WorkBuddy CN 插件的动态目录进入 `/v1/models`
（`owned_by=workbuddy-provider`），strict doctor 报
`client-model-catalog-contract=FAIL`。排除表此前只存在于主机，现纳入清单
`plugin_exclusions`（见 [WorkBuddy runbook](cpa-workbuddy-plugin.md) §2.1）。

### 2026-11-26：换到 v8.1.4，去化 7966fix，开放 glm-5.4

用户 2026-11-26 指令直接升级（不等 soak）。生产现为
`cli-proxy-api-fork:v8.1.4-gemini-raw-daily-cap-authiso-sigiso`（binary `424fb393…`，
upstream 锚 `38fe9295`），五叠层经三方合并重移植；WorkBuddy CN 插件 0.21.31；
`glm-5.4` 开放为 CN 的第二个 pin。事务、回滚与验收见
[换代台账](../change-evidence/20261126-bwg-cpa-v814-five-overlay-swap.md)。

- **v8.1.4**（2026-11-25T14:02:19Z，v8.1.3 之后 48 个提交）：v8.1.3 的全部条目
  （#7966 官方修复、#7967 线格式对齐、签名缓存三连、空成功体改判 502）加上
  Hub fallback 2.35.1、一批翻译器 null 守卫（对应 #8100–#8120 那批报告）与 Home
  派发到插件执行器。
- **7966fix 全部去化**。emit-once 由上游 `fe571afe` 取代；seen-condition 也去掉：
  上游在 v8.1.4 把 `InterruptedMessageBeforeRealOutput…` 测试的期望定为“工具响应组
  在前、后到的用户打断在后”，与自修正相反。差异只在 `willContinue` 标记的流式调用。
- **跨号隔离叠层多一处测试适配**：上游新增的 `TestIssue6332ReasoningReplayTwoTurns`
  （`sdk/api/handlers/openai`）按不带凭据前缀的键读 replay 缓存，读键改为作用域键。
- **事务顺序**：主机上 `glm-5.4` 未声明时，`cpa-health.py readiness` 的目录门
  fail-closed，换芯脚本的 `wait_readiness` 必失败并回滚。所以声明新模型的换代必须
  **先 `-Apply`、后换芯**。
- **上游抖动**：`TestAntigravityManagerExecuteAndStream_ToppedUpCreditsRecoverDuringQuotaCooldown`
  隔离重跑 6 次失败 1 次，纯净 v8.1.4、生产树、合并树三边同形；未报上游。
- zcode-start-plan 已是最新（`zcode-proxy` 4.9.1），未动。

### 2026-11-27：换到 v8.1.6，插件 0.21.32

用户 2026-11-27 指令直接升级（沿 11-26 先例不等 soak）。生产现为
`cli-proxy-api-fork:v8.1.6-gemini-raw-daily-cap-authiso-sigiso`（binary
`b0d4b7f0…`，upstream 锚 `0aaf6d1c`），五叠层经三方合并重移植；WorkBuddy CN
插件 0.21.32（失败即写盘禁用改默认关闭，`failover_disable_enabled` 显式开启）。
事务、回滚与验收见
[换代台账](../change-evidence/20261127-bwg-cpa-v816-five-overlay-swap.md)。

- **v8.1.5/v8.1.6**（2026-11-26，v8.1.4 之后共 72 提交）：antigravity 执行器
  修复波、签名缓存 TOCTOU（ff09fcb1）与 Home OAuth refresh 的 grant-scope 保持、
  replay clean-EOF 提交（7fa46e9d）、codex websocket 读限心跳续期、
  `requestId`/translator null 守卫大批；v8.1.6 新增 Home 本地插件执行器分发
  （#8515，生产未启用该面）。
- **crossauth 适配面扩大**：上游新增 `TestIssue6332Review7*` 两例（clean-EOF/
  partial-EOF replay 提交语义）同样按无前缀键读缓存，读键改作用域键（与 11-25
  `TestIssue6332ReasoningReplayTwoTurns` 适配同形）。上游对 replay KV 仍不按凭据
  分域，跨号隔离叠层继续保留。
- **三方合并冲突面收敛**：v8.1.6 与五叠层仅 1 文件冲突（replay：HEAD 的
  `antigravityReplayAuthScope` vs 上游 thought-signature 剪枝函数，互为新增），
  execute/stream/apply 三钩子自动合并且 delta 形状与上代逐行对齐
  （18/18、16/16、67/67）。
- **测试硬门基线更新**：108 包 ok，两失败均非换代引入——
  `TestUnixPluginReloadAfterShutdownUsesReplacedFile` 纯净 v8.1.6 同样失败
  （dlopen 句柄复用断言，环境抖动，三边同形）；CAQS 基线偏差同前。
- **插件 0.21.32**（11-25T16:45Z，上代换芯后 1 小时被漏）：0.21.31+`19d4c5f2`
  ——失败即写盘禁用链路默认关闭，改由 `failover_disable_enabled` 显式开启。
  插件注册断言在隔离预检里需生产 `plugins.configs` 段（scratch 只验 binary）。
- terra 已于 11-26 由并行会话退役（`540dec1`），探针集里用 `gpt-6.1-sol`
  替代；400 `model_not_found` 对退役名是正确行为。

### 2026-11-27（同日续）：换到 v8.1.7

用户同日指令直接升级至 v8.1.7。生产现为
`cli-proxy-api-fork:v8.1.7-gemini-raw-daily-cap-authiso-sigiso`（binary
`f0d6349a…`，upstream 锚 `47f18e4d`），五叠层零冲突合并；WorkBuddy CN 插件维持
`0.21.32`（上游最新）、`zcode-start-plan` 维持 `zcode-proxy 4.9.1`（上游最新）。
事务、回滚与验收见
[换代台账](../change-evidence/20261127-bwg-cpa-v817-five-overlay-swap.md)。

- **v8.1.7**（2026-11-27，v8.1.6 之上 3 提交）：`830b9cac`（#8516 修复 Unix 插件
  重载测试的 fresh-image 断言）、`7fa52365`（#8520 修复 Gemini OpenAI Chat 翻译中
  合并工具响应的用户消息排序，防 Vertex AI HTTP 400）、`47f18e4d`（#8519 插件重配、
  热重载与目录错误时保持调度器路由与原子快照）。
- **测试硬门基线更新**：WSL 全量 `go test ./...` 升至 **109 包 ok**，上代失败的
  `TestUnixPluginReloadAfterShutdownUsesReplacedFile` 已恢复通过，仅保留既有的
  `TestAntigravityExecutorCAQSTwoTurnReplay`（sig-iso 基线偏差）。
- **验收与 12h 基线**：strict doctor `DOCTOR_CONTRACT_OK`（含新增的
  `zcode-sidecar-*` 门禁）；12h 自然流量缓存命中率 **95.59%**（865/906 turns），
  `agent_identity_neutralized=883`、`holds_12h=0`、`credential_ban_signals_7d=0`；
  三大 OAuth 车道受控单发探针（`gemini-4-hydrogen-xhigh` / `deepseek-v4.1-flash` /
  `gpt-6.1-sol`）全 200，`zhipu-coding-plan`（`glm-5.4`）遇 5h 窗口 429 `code=1308`
  后 admission 即时冷却、零重试 fail-closed。

### 2026-11-27（同日三续）：全面深审的实测结论

- **astra 缓存未命中是冷启动型，不是固定渠道上限**：606 发（输入 ≥20K）里 554 发命中
  ≥80%，34 发全未命中（每发 37K–170K token），集中在 UTC 14–15 时。OpenAI 官方
  [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) 写明
  GPT-5.6 起 `prompt_cache_key` 只用于缓存记账、不再影响路由，且缓存不跨组织共享，所以
  网关注入 cache key 无效；保持 `support-prompt-cache-key: false`。
- **身份句规则覆盖实测**：见
  [封号 runbook 2026-11-27 段](cpa-ban-throttle-incident-response.md)与
  [identity-suffix 台账](../change-evidence/20261127-bwg-admission-identity-suffix.md)。
  上游 [#8521](https://github.com/router-for-me/CLIProxyAPI/issues/8521) 证实 OpenCode V2
  注入 `systemInstruction` 的 `tools.opencode.session_move` 是独立触发，上游只落了文档
  提交 `0e19e8e`（不在 v8.1.7 内）。
- **不适用于本部署的上游 open issue**：#8538（`disable-cooling` 下 429 凭据被立即重发；
  本仓 `disable-cooling: false`）、#8530（Claude OAuth 约 4 周登录上限；本仓 Claude 车道无
  凭据）。**留观**：#8539（auth 文件原地重写，崩溃或满盘可能留下截断凭据；Antigravity
  两凭据约每 25 分钟重写一次，`RefreshLead=30m`）。
- **WorkBuddy 凭据池（管理 API 实测，标签为文件名 sha256[:8]）**：`scheduler_mode:
  credits` 下流量落在积分最高的凭据；11-27 04:39Z 与 04:59Z 两个凭据因积分低于
  `low_credit_disable_below=20` 被插件自动禁用（其中一次伴随 `glm-5.4` 的上游
  `429 code=14018 额度已用尽`）。这是插件的低积分下线设计，admission 的 12 RPM 车道帽
  仍生效，不因集中而突破 14003 风险区间。
- **zcode-start-plan 502 根因未证实**：边车 `auth status` 报 `Logged in: bigmodel`
  （凭据文件在、可解密），`/health` 正常；`/quota` 的 coding 平面回
  `1001 Header中未收到Authorization`，但该边车是 `plan: start-plan`，不能据此判定 LLM
  请求必然失败。边车以普通 `serve` 运行，每请求只留一行表格日志；`zcode-proxy` 支持
  `serve debug`（逐请求诊断）。该单元不受仓库投影管理，且 journald 只留约 21 小时，
  所以**未常驻开启 debug**：下次该车道再出 502 或开始有稳定流量时，临时以 `serve debug`
  跑一轮定位。重登入口（需本人在浏览器完成授权）：
  `zcode-proxy auth login zai_cn`（带 `/etc/zcode-start-plan.env` 的加密种子）。
- **`chatgpt-oauth` 后台 `gpt-6-luna` 过载连坐 `gpt-6.1-sol` 根治（同日四续）**：
  Codex Desktop 硬编码后台标题/建议请求走 `gpt-6-luna`，当上游在 HTTP 200 SSE 流内返回
  `response.failed`（`code: "server_is_overloaded"`，内嵌 `headers.retry-after: 317`）
  时，CPA 核心（#7600）仅冷却 `gpt-6-luna` 模型分片；但 `cpa-admission` 此前既未把
  `server_is_overloaded` 列入 `chatgpt-oauth.model_capacity_markers`，也未解析流内 JSON
  的 `headers.retry-after`，导致排队的第 2 笔 `gpt-6-luna` 打穿到 CPA 核心拿到顶层 503
  + `Retry-After: 317` 后将**整条 `chatgpt-oauth` 车道**锁死 317 秒，连坐拒绝用户的
  `gpt-6.1-sol` 主请求。现已将 `server_is_overloaded` 纳入 `chatgpt-oauth` 模型级容量标记，
  并由 `codex_overload_retry_after()` 提取流内/响应体内嵌 `headers.retry-after(-ms)` 直写
  `model_retry_after`，首笔流内过载即单独冷却 `gpt-6-luna`，保障 `gpt-6.1-sol` /
  `gpt-6.2-sol` 正常放行。详见
  [模型级过载隔离台账](../change-evidence/20261127-bwg-cpa-codex-model-scoped-overload.md)。
- **`chatgpt-oauth` 冷却拒绝的重试期限客户端可读（同日五续）**：模型级隔离上线后，主模型
  自身被上游过载时仍秒级硬失败——admission 写进已提交 200 流的拒绝只带裸 `error.retry_after`，
  而 Codex 只解析 `error.headers["retry-after"]`（openai/codex
  `codex-rs/codex-api/src/sse/responses_error.rs`），读不到期限就按本地短退避在窗口内连打两次
  后报错（18:30Z 与 11:21:48–51Z 的五笔拒绝均如此）。现已让 `cooldown` / `model_cooldown`
  且 ≤600 秒的拒绝在 `/v1/responses` 带上该头；队列/限速/传输类与配额类长窗口保持快速失败。
  同批修了三个存量缺陷：模型级冷却文案写死 "Antigravity"、流式请求遇上游拒连抛
  `UnboundLocalError`、客户端小写 `content-length` 与 admission 的 `Content-Length` 并存。
  上游过载的定性（openai/codex #56346、#63577，本栈 29 小时实测 42%）与判否项
  （缩短 CPA 冷却并在窗内复检：冷却到期后重试成功率仅约 25–30%，判否；admission 的
  `mirror_probe_interval_seconds` 会被 CPA 本地 `503 auth_unavailable` 挡住）见
  [拒绝重试期限台账](../change-evidence/20261127-bwg-admission-refusal-retry-advice.md)。

### 2026-11-27（同日六续）：第二轮全面深审

只读审查（strict doctor、48h journal、管理 API、保留转储、脱敏配置、上游源码、官方页与
社区 issue），**网关代码与配置未发现需要修改的缺陷**，本轮只改文档。

- **线上基线**（12:42Z）：`DOCTOR_CONTRACT_OK`，`credential_ban_signals_7d=0`，三条 OAuth
  车道 OPEN，投影 17 项 MATCH，admission pin `2bc34f73`。容器 12:19Z 的重启是并行会话
  admission 换代后的 `-Apply` 收口（同刻有 guardrails 备份目录），非异常。上游 CLIProxyAPI
  仍为 v8.1.7（main 无新提交）。
- **流量画像（48h，1926 发 upstream_result）**：WorkBuddy 车道 1189 发，占 OAuth 流量
  92%，单小时峰值 297 发、单分钟峰值 10 发，p50 输入 131K token（最大 338K），缓存命中
  0.983；来源是本机 ZCode 桌面端经 `fq.sciman.top` 的编码会话（provider 配置的模型即
  `glm-5.3-flash-wb` / `glm-5.4-wb`）。ChatGPT 车道 79 发，容量失败 35 发（约 45%，仍是
  上游过载）。Antigravity 3 发、zhipu 4 发、zcode-start-plan 1 发。
- **跨车道归一化不伤缓存（假设已证伪）**：`strip_antigravity_model_switch_items` /
  `normalize_antigravity_developer_context` / `collapse_antigravity_retry_user_turns` 自
  `10575d2`（2026-11-23）起对所有车道生效，48h 内在 WorkBuddy 车道剥掉了 78 个中置
  `<skills_instructions>` part 并折叠 628 条重试 user 轮。曾怀疑它破坏前缀缓存：实测发生
  折叠的 314 发命中率中位 0.997，未折叠的 715 发为 0.997，无差异。`glm-5.4-wb` 的低命中
  请求（119 发里 47 发 <50%）集中在 04:30–04:50Z 与 07:00Z 的十分钟桶内，且都不带折叠，
  是新会话冷启动与模型切换，不是归一化造成。
- **错误转储的结构**（5 份保留样本，只读角色与长度）：官方 Codex Desktop 的请求无顶层
  `instructions`，`input[0]` 为 developer 基础提示，`tools` 8–17 个；一份 73 项的样本是
  71 条连续 user 消息（每条约 8.5K 字符）而无 assistant 轮，是失败重试在客户端累积的
  形状，与此前"重复重试撑大上下文"的判断一致。
- **cloak 关闭后的出站身份**（v8.1.7 源码）：`disable-codex-cloaking: true` 时
  `applyCodexCloakingHeaders` 直接返回，`User-Agent` 取客户端值（无则
  `codex-header-defaults`，再无则常量 `codex-tui/0.154.0…`）；`Originator` 取客户端值，
  客户端不带时**仍回落 `codex-tui`**。即官方客户端透传自洽指纹，不带 Originator 的调用
  （探针、脚本）仍以 `codex-tui` 出站——这是 #4679 里被甩的那个值。本栈该车道 agent
  流量全来自官方客户端，不受影响；结论：不要用脚本直接打 ChatGPT 车道。
- **周期探活的现状**：`/v1/chat/completions` 路由在 ChatGPT 车道 72h 内 13 发（约 53
  token，单一客户端实例），全部在 2026-11-26 17:01Z 之前；terra 退役后不再出现。
  WorkBuddy 插件的 active ping 为每账号 4 小时最多一发（实测 6 轮、每轮 9 发），plus 每 4
  小时 credits sweep。Antigravity 两凭据每 25 分钟被上游重写一次是 `RefreshLead=30m` 设计。
- **上游未修项**：#8409 / #8452 的函数到 v8.1.7 字节级未变，是 2026-11-26 批量关闭的
  195 个 issue 之一；admission 对 Antigravity JSON 403 一律落持久锁，构成兜底。
- **官方口径更正与新证据**：见
  [封号 runbook](cpa-ban-throttle-incident-response.md) 的 OpenAI 补充、WorkBuddy 复核、
  Antigravity 复核与 ZCode 起步套餐各节。要点：合并额度文章号是 20001625（此前误引
  20001559）；WorkBuddy 11128 是内容指纹判定；Antigravity 免费档只有周窗口；ZCode
  Start Plan 官方明文限于官方客户端。
- **研究覆盖的缺口**：子代理的搜索与网页阅读配额当日耗尽（2026-12-16 重置），Reddit、
  X、linux.do、V2EX 等论坛全部未覆盖；证据仅来自官方页直取与 GitHub。

### 2026-11-27（同日七续）：起步套餐车道的根因与修复

用户裁决该车道保留并授权修复。事务与证据见
[起步套餐台账](../change-evidence/20261127-bwg-zcode-start-plan-quota-and-claim.md)。

- **该车道此前从未验收，502 的原因不是边车故障**：上游 `zcode.z.ai` 对额度耗尽回
  HTTP 200 + `{"code":1005,"msg":"exceed quota limit"}`，边车把它包成
  `502 translation_failed` 并把原文引在自己的 message 里。六续里"边车 502 多为自身挂起"
  （引自 zcode-api 旧 issue 标题）对本部署不成立，已在该 issue 的当前版本上实测排除。
- **撤回六续的一个推断**：admission 在该车道改写 ZCode 身份句与边车的官方系统块不冲突——
  边车在出站时才前置官方三块，admission 改的是边车之前的客户端文本。清洗规则保持现状。
- **两处修复**：`exceed quota limit` 加入 `zcode-start-plan` 的 `capacity_markers`
  （此前额度耗尽被当成传输故障，不冷却）；边车 `claim.auto: false`（上游默认开启的
  活动 polling 对 BigModel 起步套餐没有收益）。`cpa-admission.py` 字节未变，pin 不轮换。
- **排查手筋**：边车普通 `serve` 只留一行 502 表格行。先直连边车发一发非流式请求读
  原始错误体，不必切 `serve debug`；`auth status` 必须带服务的完整环境（含
  `ZCODE_PROXY_CREDENTIAL_SECRET`），否则会报凭据损坏的假象。
