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
| [#7960](https://github.com/router-for-me/CLIProxyAPI/issues/7960) | 全局 signatureCache 无凭据维度→跨号签名注入 | sig-iso 车道已封口；下代 fork 给上游缓存加 auth 维度，见封号 runbook"上游签名缓存缺口"节 |
| [#7961](https://github.com/router-for-me/CLIProxyAPI/issues/7961) | transport scope 不含 grant 代→重登后跨号 keep-alive 复用 | 重登后 restart 容器的纪律，见封号 runbook 同节 |
| [#7946](https://github.com/router-for-me/CLIProxyAPI/issues/7946) | v8.1.1 大上下文流式内存尖峰 ~2.3 GB | 并发 320K+ 容器无解释重启时首查；512MB 限制勿上调，见衍生 RFC"构建车道内存护栏"节 |
| [#7934](https://github.com/router-for-me/CLIProxyAPI/issues/7934) | compaction 把 MAX_TOKENS 截断摘要密封为 completed+清零计数 | 320K 试用（200000/256000 阈值）期间"变笨"类投诉先核此项 |
| [#7952](https://github.com/router-for-me/CLIProxyAPI/issues/7952) | forked session 失败逐出父会话 affinity 绑定 | 小概率缓存损失，仅 watch |
| [#7924](https://github.com/router-for-me/CLIProxyAPI/issues/7924) | disableCooling=true 时 TOS_VIOLATION 重置 Unavailable | N/A：本栈 cooling 在役 |

**换代指引**：上游 main 在 v8.1.1 后已积累 ~20 commits（未发版）＝已闭缺陷修复波
（#7914/#7915/#7916/#7917/#7918/#7919/#7920：相邻 user turns、filter-block 双面、
budget clamp、schema keywords、grounding、count-tokens in-band）+ `b8e3bc66`
（请求对齐/最小化，WAF 相关需细读）+ `2f86992c`（分组模型按
thinking-level 路由）+ Copilot provider（与本车道无关）。**等 v8.1.2 tag 落地
再整批 sync**，不直追 dev；换代事务按既有 swap 流程，并核对该批改动与
daily-cap / sig-iso overlay 的冲突面。

**归因能力缺口（luna 缓存 0.27 watch 的边界）**：2026-11-23 起 24h 审计中
`chatgpt-oauth/gpt-6-luna` cache_hit=0.27（11-21 重登后"冷缓存 24-48h 回判"
未兑现）。按客户端归因暂不可行：CPA 侧无持久请求日志（`logs/` 目录空、usage
队列仅 1h 保留），admission journal 的 `upstream_result` 不携带任何客户端标识
（末位是 `_trace_context`）。可行路径＝维护窗短暂开启 CPA request-log，或
admission 给 `upstream_result` 加脱敏 client sha8 字段（两者均属 reviewed
变更，未裁决前不做）；小样本下客户端构成（单发首问+前缀不稳客户端）仍是首选
假说，非服务端回归证据。
