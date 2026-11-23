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

**2026-11-23/24 已执行**：v8.1.2 五叠层代上产（镜像
`v8.1.2-gemini-raw-daily-cap-authiso-sigiso` id `ceb72412`、binary `19789412`、
SWAP_OK 16:07:30Z、8318 双探针 200），配方=golang 三方合并 + WSL 本地构建
（bwg 内存护栏拒跑的合规替代，bookworm glibc 门内建）+ SFTP 上传；台账见
[20261124-bwg-cpa-v812-five-overlay-swap.md](../change-evidence/20261124-bwg-cpa-v812-five-overlay-swap.md)。
"重登后 restart 容器"纪律随 #7961 正式修复可退役。

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
