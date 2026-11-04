# CPA 网关故障归因与调优 runbook

适用症状：客户端报 `429 Too Many Requests`、`exceeded retry limit, last status: 429`、
`Selected model is at capacity`、或"慢 / 吐字慢"。覆盖 `capacity`、`429`、慢速三类。

配套工具：`scripts/cpa_failure_triage.py`（只读，本机口径归因）。所有实现、预算、
dead-route 清单和测试只维护在这一处。
远端结构事实：`scripts/cpa_bwg_guardrails.ps1`（默认严格 doctor）。

## 1. 为什么不能只看状态码

### Desktop 断流与维护窗口的额外判据

- Nginx HTML `503 Service Temporarily Unavailable` 只说明网关生成了错误页。
  若 `upstream_status=-`，先核对同一时间的维护入口标记和升级恢复记录；
  不把 HTML 文案解释为模型容量错误。2026-10-25 的联合升级曾临时在入口
  `return 503`，恢复入口后该窗口结束。不要把一次升级成功视为断流问题已修复。
- `status=500 upstream_status=- auth_status=502` 是认证子请求失败，模型业务
  请求尚未转发。[Nginx `auth_request`](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)
  只接受 2xx、401 和 403；其它返回码按错误
  处理。doctor 的 `auth_subrequest_failures` 将这类请求单独计数。
- `admission_protocol_reasons` / `..._by_model` 按明确的 `protocol_result`
  聚合；旧日志无该字段时保持 `unknown`。`successful=false` 本身不证明容量故障。
  修复前 admission 的 socket-peek 断连路径可能误记 `missing_terminal`，旧计数
  不能自动追认成上游截断。新日志附加的 `sse_bytes`、`sse_events`、
  `sse_last_event` 仅保留计数和固定类别，不保留响应内容。
- 客户端已断开与上游真实 EOF 分开处理；不能为补齐终态而合成
  `response.completed`，也不能按 HTTP 200 恢复失败 lane。原项目的 compatible
  reasoning-only 转换缺终态与 native Codex OAuth 缺终态需要分别取证，
  PR 标题或相似社区报错不足以证明修复适用。

**四层都能答 429**，而且每层的修法互斥。历史上多次因为只看状态码而归错层
（最典型的一次：把 loopback 压测残留当成生产故障，得出"429 已归零"的错误结论）。

| 层 | 判别字段 | 含义 |
|---|---|---|
| **本机闸门** | 本机 `latency_ms` ≈ 本机闸门等待预算（工具自动取自 collection 的 `accountConcurrencyWaitMs`；2026-10-08 实测 120000ms，direct fq 模式下无 sidecar 钳制生效），**且 nginx 访问日志里没有对应记录** | Cockpit 侧车按账号并发上限拒绝，请求从未离开本机 |
| **nginx 连接层** | `upstream_status=-` + `limit_conn=REJECTED` + `upstream_time=-` | 每 IP 连接预算满 |
| **nginx 速率层** | `upstream_status=-` + `limit_req=REJECTED` + `request_time=0.000` | 速率限制 |
| **admission lane** | `upstream_status=429` + `bytes≈203` + `upstream_time≈0.001` + `limit_conn=PASSED` | lane 队列满（`busy`）或冷却；具体原因以 journal `reason=` 为准 |
| **维护窗口** | `status=503` + `bytes=44` + `upstream_time≈0.002` + journal 只有 `http request_id`、**没有** `upstream_result`/`lane_reject`（doctor 归为 `503/admission_maintenance_44B_upstream_lt_0_5s`，见 `client_503_maintenance_shape_total`） | admission `request_guard` 拒绝 `{"error":{"type":"maintenance_in_progress"}}`；发生在 `-Apply`/canary 升级重启窗口，不是容量也不是客户端故障 |
| **上游容量** | `upstream_status=200` + 体内 `server_is_overloaded` / `usage_limit_reached`，或 5xx | 账号容量 / OpenAI 负载卸载 |

**关键推论**：本机闸门拒绝的请求**不会出现在 nginx 访问日志里**。所以
"远端 `external/429` 很少、但本机 45s 指纹很多"是**自洽**的形状，不是矛盾——
不要把两者相加，也不要用远端计数否定本机计数。

**维护窗口的秒级 503 连发不要当成"客户端重试风暴"**（2026-10-24 实证）：一次
v8.0.40 canary 升级（备份目录 `20261023T040546Z-from-v8.0.39-canary-v8.0.40`）期间，
操作者本机出口 IP 在 04:05:51–04:05:57Z 对同一个请求重试 6 次，全部拿到 44 字节的
`maintenance_in_progress`，04:06:08Z 恢复 200。判别：同 IP、body 44 字节、`upstream_time`
约 2ms、journal 只有 `http request_id` 而无 `upstream_result`，且时间落在升级窗口。
自 2026-10-23 11:46Z 的 admission 提交 `ee74fbf` 起，同类拒绝会记录
`request_guard_refused stage=flock reason=maintenance_busy`，可直接确认而不必靠字节数反推。
strict doctor 现在把该形态单独归类为 `503/admission_maintenance_44B_upstream_lt_0_5s`，
输出 `client_503_maintenance_shape_total`，并从 `client_503_retry_pattern` 剔除；
journal 段用 `admission_request_guard_refusal_reasons=flock/maintenance_busy:N`
给出权威原因，二者对不上时才需要回到原始 journal 逐条核对。

时间基准：主机 `journalctl` 与 nginx 访问日志都是 **UTC**（`timedatectl` =
`Etc/UTC`），可直接与 `time=[... +0000]` 对齐；只有**容器**日志是 CST（+8）。
上述 6 条拒绝在 journal 里只有 `http request_id=… route=responses
client_request_hash=e6633baa4f73`、没有 `request_guard_refused`，是因为该窗口
（04:05:51Z）早于 `ee74fbf` 的部署（11:46Z），不是判据失效。

**不要**为这段 503 加服务端 `Retry-After` 来"压掉秒级连发"：Codex 客户端对 5xx
不读该头（退避固定 200ms×2ⁿ±10%，默认 4 次），本机两个 provider 都钉
`request_max_retries=0`；现役 `codex_local_access` 设
`stream_max_retries=2`，同网关 `fq_sciman_top` 保持 0。请求级失败不重放；已开始
SSE 的传输截断允许一次有界重连，不用于容量恢复。2026-10-31 隔离回放
（Codex 0.159.2）确认 0/2 在 HTTP 503 后经流重连发 3 次 POST；2026-11-01
Codex CLI 0.166.0 的 SSE 截断 A/B 确认 0 直接失败，2 自动重连一次后完成。
秒级连发来自仍持旧默认重试预算的会话，处置是重启该会话，不是改服务端参数或
重启 admission。

### 车道人工锁与 403 分类（2026-10-31）

- `operator_review_required`（HTTP 403）是 **admission 持久锁**，发生在 CPA
  之前；此时插件调度与混合回退（OpenCode）完全不参与。判"为什么没切到
  opencode"时先看 `healthz` 的 `hold_reason`，不要翻 CPA 容器日志。
- 2026-10-31 起，chatgpt/workbuddy 车道的**非 JSON 403**（HTML/纯文本边缘页，
  如 Cloudflare `Access denied`）按瞬时链路信号处理：只走冷却阶梯，不再落
  持久锁；JSON `permission_error`/认证 403 与 `11140` 仍落锁（`account_restricted`
  或 `authentication_required`）。`claude-oauth` 语义不变。
- 异常 `Retry-After`（> 2^31 秒）：除 claude 外钳制到阶梯末档 900 秒并记录
  `implausible_retry_after_clamped`，不再升级成人工锁。
- 任何持久锁现在都记
  `lane_hold_latched lane=… reason=… previous=… source=…`；先按这一行定位来源
  （`response_signal` / `capacity_window` / `record_risk` / `state_persist_failed`）
  再决定是否释放。
- 释放流程：入口 503 → 四车道 `inflight=0` → 备份 state 文件 → 断言原 reason
  后置 `hold_reason=null` → 重启 admission → 按 sha 恢复入口。脚本见
  `outputs/release-workbuddy-hold-20261031.ps1`。
- 维护窗代价：入口 `return 503` 会让**所有会话同时**看到 503，窗口按秒计；
  执行前确认没有长推理会话，做完按 sha 校验入口配置已恢复，避免把门留在线上
  （2026-10-31 曾因备份缺失遗留 10 分钟）。

## 2. 本机口径是权威（先看这里）

> **时效警告（2026-10-05 复审）**：本机两库自 fq 账号切换 direct 接入模式
> （2026-10-05 01:0x 北京）起不再进新行；且 `request_logs` 的
> `input_tokens`/`cached_tokens` 列自 2026-09-27 16:15 UTC 起全零。
> direct 模式下桌面流量直连公网 API、不经本地网关，本机库只覆盖历史窗口。
> 当前归因**先走 §2.1 的网关侧权威源**，本机库仅用于回看 2026-10-04 17:04 UTC
> 之前的历史；`cpa_failure_triage.py` / `cpa_admission_risk_audit.py` 在此之后的
> "本机视图"输出按过期数据处理（实例：2026-10-05 审计报的
> deepseek-v4.1-flash FAIL 即过期数据假阳性，网关侧当日已 200 恢复）。

`%USERPROFILE%\.antigravity_cockpit\codex_local_access_logs.sqlite`，表 `request_logs`。
每个请求一行，字段：

| 字段 | 用途 |
|---|---|
| `timestamp` | epoch ms |
| `http_status` | 客户端实际看到的状态码 |
| `error_category` | `quota_or_rate_limit`=429、`upstream_error`=5xx、`auth_failed`=401、`model_not_available`=404、`request_failed`=400 |
| `latency_ms` | **客户端可见总时长——归因的核心指纹** |
| `requested_model` | 客户端请求的模型（死路由判定用） |

**只读打开**（工具内部已用 `mode=ro`）：

```
sqlite3 "file:$USERPROFILE/.antigravity_cockpit/codex_local_access_logs.sqlite?mode=ro"
```

### 2.1 direct 模式后的权威数据源与缓存采样

| 数据 | 权威源 | 说明 |
|---|---|---|
| 状态码/时延/逐小时分布 | doctor `==gateway-statuses-current-log-24h==`（nginx access log） | `request_time` vs `upstream_time` 差值=本地开销上界（2026-10-05 实测 2.91%，n=1539；2026-10-08 含轮转日志复测 p50 3.21%，n=800）。**轮转缺口**：该段只聚合当前 access log，logrotate 刚轮转后窗口近乎为空（2026-10-08 实测 26 行 vs journal 同窗 1498 条）；需要完整 24h 时对 `.1` 与 `.NN.gz` 手工聚合（按 `method=POST`+`status=200` 过滤后取 `request_time`/`upstream_time` 即可） |
| lane 冷却/熔断/自愈 | doctor `==admission-journal-24h==` + `journalctl -u cpa-admission` | `lane_reject`/`lane_probe`/`upstream_result capacity=` |
| 凭据/目录/冷却态 | guardrails 默认严格 doctor | `cooldown-state`、`oauth-monitor`、`client-model-catalog` |
| 缓存命中率 | `cpa_natural_traffic_audit.py` 的 `tokens` 行（真实流量聚合，2026-10-31 起）或 `cache-canary` 受控采样 | 见下 |

缓存遥测：CPA 的 usage 统计只存在于内存与管理 API（无 MANAGEMENT_PASSWORD，
fail-closed 不注入），全量请求日志仅错误时落盘（5 文件轮换）。**2026-10-31
起 admission 在 `upstream_result` journal 行追加 `usage_in/usage_cached/usage_out`
，并在可识别 SSE 文本事件时追加 `first_text_ms`（只取数字型白名单字段，
绝不记录正文）**，因此真实流量的缓存命中率与首字延迟都可直接聚合：

```bash
./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --hours 24
# 关注每 lane 的 tokens 行：cache_hit_ratio = cached / in；reports=N 是分母口径
```

尚需注意：并非所有 provider 都回传 usage（WorkBuddy/部分中转可能不回），
此时 `reports` 会小于 `n`、`cache_hit_ratio=na`，仍是"未报告"而不是"零命中"。
健康探针只计入 `requests`/`probes`，其时延、协议、吞吐、token/cache/cost
指标不进入 `natural` 聚合。自然流量中，至少 10 个 usage 报告且累计输入不少于
1,000,000 tokens 时，`cache_hit_ratio < 0.50` 会输出需要复核的 WARN；这是
本地调查阈值，不是 provider 配额或账号安全阈值。
需要受控对照时仍可按 lane 采样：

```bash
# 远端（经 ssh_tool.py / guardrails 会话），每次 2 样本、同前缀、消耗少量对应 lane 配额
python3 /opt/cliproxyapi/cpa-health.py cache-canary              # glm-5.3-flash（默认；白名单仅 glm-5.3/glm-5.3-flash）
python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3      # GLM lane
python3 /opt/cliproxyapi/cpa-health.py cache-canary glm-5.3-flash
```

模型白名单钉死在非 OAuth lane（`_CACHE_CANARY_MODELS`），OAuth 名与未知名本地
拒绝（exit 20 `CACHE_CANARY_MODEL_UNSUPPORTED`），保住"默认不消费 Plus 账号"纪律。
基线：DeepSeek `hit_ratio=0.9579`（2026-10-05 v8.0.15 首测；2026-10-08 v8.0.16 复测同值，稳定）。GLM lane 首测 `hit_ratio=0.9956`（2026-10-08，v8.0.16，`glm-5.3-flash`；首样本冷 miss 属预期）。
CPA 侧：`session-affinity: true`（`session-affinity-ttl: 1h`、
`session-affinity-rebind-on: quota`、`session-affinity-subagents: false`）由本仓投影钉死
（`scripts/remote/cpa_policy.py`、`scripts/remote/cpa-guardrail-apply.sh`）。CPA v8.0.45+
缺省 `session-affinity-rebind-on` 为 `any`，本仓显式改为 `quota`：瞬态失败保留当前凭据，
只有配额、认证、模型不支持或永久不可用才重绑定，减少账号扩散并保留缓存局部性。
会话粘性路由本身即缓存复用主机制；`support-prompt-cache-key` 各 provider 保持 `false`
（`cpa_policy.py` 的同名守卫在非 false/absent 时报错拒绝）：非 OAuth lane 实测已 ≥0.95，
OAuth lane 按纪律禁止 canary 消费，收益不可量化，保持不动。
2026-10-15 只读复核：两个键在部署版本 v8.0.29 的 `internal/config/config_types.go` 中仍是同名 YAML 拼写；上文原先标注的 v8.0.16 是当时的核对版本，不是运行版本，键名漂移时按符号检索而非行号。
降智（turn-state 312/292）遥测随 direct 模式失效，暂无替代通道；复发时以
CPAMC 台页与上游行为复核，不要引用本机库的 `turn_state_*` 列。

### 2.2 自然流量分 lane 审计（2026-10-31，2026-11-01 校正）

`scripts/cpa_natural_traffic_audit.py` 只读 admission journal，按 lane/model 汇总
总请求量、自然请求量、完成峰值、准入峰值、completed/capacity/client_disconnect、
header/first-byte/first-text p50/p90、stream/total duration、SSE bytes/s、输出 tokens/s、
token/cache 遥测、tiny SSE completed 与非 SSE completed。它不输出 request ID、
提示词、响应正文、客户端 key 或凭据。

```bash
./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --hours 24
./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --ssh-profile bwg --hours 24 --json
journalctl -u cpa-admission -o short-iso-precise --no-pager > /tmp/cpa.log
./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --file /tmp/cpa.log
# 可选：只对显式提供的受控缓存样本 dump 分类“有正文 / 空正文 /
# reasoning-only”，不记录正文
./.venv/Scripts/python.exe scripts/cpa_natural_traffic_audit.py --hours 24 `
  --origin chatgpt-oauth/gpt-6-luna:C:\captures\one-response.dump
```

口径：

- `peak_completed_60s` 数 `upstream_result` 的完成时间。长流可能在同一分钟集中
  完成，所以它**不是**准入 RPM，也不能拿来判断是否贴着上游请求阈值。
- `first_output_ttft` / `first_text` / `queue` / `decode` 是独立观察量。
  `first_output_ttft` 从上方的 `upstream_first_byte_ms` 得到；`first_text`
  是第一个非空可见文字事件的时间；`queue` 是准入等待；`decode`
  是首个上游 payload 之后到终态的窗口。`slow_5s` 分别数三阶段超过 5 秒的
  请求，三类可以重叠，不应相加成失败数。
- `admit_peak_60s` 优先使用 `lane_admit`，缺该行时做有界推导：SSE lane 的下游
  头在排队前提交，使用 `header time + waited_ms`；非 SSE 的下游头在上游响应后
  提交，使用 `header time - upstream_header_ms`。`admission_paired` 给出可配对
  request 数；没有配对时显示 `na`，不拿完成峰值替代。
- `empty_text_completed` / `truncated_text_completed` 读取 admission 的
  `sse_text_finish` 白名单字段，只在终态 Responses 对象明确含有非空
  `output_text` 时记 `stop`；含有空文本 part 时记 `empty_text`；只含
  reasoning/tool item 时记 `reasoning_only`。这是**结构性判断**，不是对正文
  的模糊猜测；`na/pending` 只是未完成分类，**不能**算作空正文或降智。
  旧日志没有该字段时只显示 0，不能解释为没有空正文或截断。完整缓存样本可用
  `--origin lane/model:path` 对一个显式 dump 分类；工具只输出
  `text_output_completed` / `reasoning_only_completed` 等计数，不打印或持久化
  正文、prompt、token、工具参数和凭据。
- `usage_cost_usd` 仅接受 provider 返回的数值型 `cost` / `total_cost` /
  `cost_usd` 字段；`0.00000000` 表示**报告零费用**，不表示免费容量、无限额或
  可安全换号。缺少该字段显示 `na`，不伪造金额。
- `--origin lane/model:path` 只对显式提供的响应样本做**结构性分类**：
  `output`、`empty`、`reasoning_only` 或 `truncated`；它不会扫描请求正文，
  不会输出文本、工具参数或凭据，也不会从自然流量文件推断正文质量。
- `output_tokens_per_s` 用上游 usage 的输出 token 除以“准入→首个上游 payload→
  终态”的窗口，是流窗口估计，不是 provider 吞吐基准。
- `sse_bytes_per_s` 是传输字节速率；`tokens uncached` 是
  `usage_in - usage_cached` 的观测值。未报告 usage 的请求不进分子或分母。
- 至少 10 个 usage 报告且输入达到 1M token、缓存命中率仍低于 0.20 时，工具会报
  `cache-cold` 提示。它要求先核对会话粘性、prompt 前缀和工具/系统提示是否逐轮
  漂移，不把单个窗口直接归因为 provider 缓存失效。

2026-11-01 近 1–8h 自然流量复核：WorkBuddy `admit_peak_60s=12`、
`peak_completed_60s=13`，说明完成峰值会高估突发；同期
`chatgpt-oauth/gpt-6-luna` `admit_peak_60s=13`、header p50 1.20s、total p50
8.12s、cache hit 0.98；WorkBuddy header p50 2.05–2.28s、total p50
8.35–11.49s、output p50 252–306 tokens/s、cache hit 0.97–0.99。该窗口跨越
WorkBuddy 12→18 RPM 的受控调整，24h 还包含更早的历史窗口；当前值必须同时读
`/healthz`、doctor 和规定投影，不能把历史 `rate_limited` 计数当当前阈值证据。

2026-11-01 部署后复核：`gpt-5.6-luna` 的 19 个请求是分散在约 2 小时内的独立
大输入（约 1.7 万–24 万 input token/次），多数是单次冷上下文，只有 2 次各命中
6,016 token；这不是“同一会话前缀每轮漂移”的可证结论，不能据此强制改
`prompt_cache_key` 或提高会话粘性。新的 `cache-cold` 告警只用于把这类高 token、
低命中的窗口挑出来，下一步应结合客户端实际会话前缀判断。

同日 1 小时真实流量复核（`cpa_natural_traffic_audit.py --ssh-profile bwg`，
n=707）：ChatGPT Luna 排队 p90=4ms、首输出 p90=1.77s、缓存命中 0.931；
WorkBuddy 排队 p90=14.9s、首输出 p90=6.74s、缓存命中 **0.999**；
GLM-Flash 排队 p90=0ms、首输出 p90=9.22s、缓存命中 0.972。WorkBuddy 的慢
主要是单凭据 `inflight=1` 下的**有界排队**，缓存不是瓶颈。该窗口未报告
`usage_cost_usd`，不能据此推算付费成本。此观测不证明上游额度或长期账号安全。
13:17–13:24Z 的 WorkBuddy/DeepSeek 最新 65 个 completed 样本中，63 个只含
reasoning/tool 结构、无可见 `output_text`，2 个为 `stop`。这只说明正文缺失
需要查，不证明降智；自然流量无法区分客户端渲染、推理预算或上游策略，先以
固定模型、reasoning、上下文和输出预算的受控样本复核，不据此清冷却或换号。

`admit_peak_60s` 贴着 WorkBuddy 社区 14003 画像的 13–19 rpm 时，先减少并行
标题/摘要；`peak_completed_60s` 单独偏高只说明长流完成时间聚集，不应据此清冷却、
换号、换 IP 或继续收紧 admission。

## 3. 用法

```bash
# 本机归因（默认近 24h，读 collection 自动取闸门预算）
./.venv/Scripts/python.exe scripts/cpa_failure_triage.py --hours 4

# 叠加远端事实（doctor 输出文件）
./.venv/Scripts/python.exe scripts/cpa_failure_triage.py --hours 24 \
    --doctor outputs/doctor-<date>.txt

# 机器可读
./.venv/Scripts/python.exe scripts/cpa_failure_triage.py --hours 24 --json
```

指纹参数：`gate_wait_ms` 自动取自 collection 的 `accountConcurrencyWaitMs`（所以调过预算
之后工具仍然准确）；admission 队列预算用 `--queue-timeout-ms` 覆盖；`--slow-ms` 调慢速阈值。

工具只输出**判读所需的**远端事实（契约、冷却、OAuth 余量、external/loopback 分层、
按小时分布、overload 计数），不会把整块 doctor JSON 倒出来。

## 4. 判读与处置

### GLM 的上游限流与图片能力错误

- BigModel HTTP 429 `1302` 表示上游账户速率限制；`1308` 表示用量窗口
  上限。`glm-5.3` 与 `glm-5.3-flash` 共用 `zhipu-coding-plan` lane，切换
  两者不能解除账户限制。保留错误原文及上游 `Retry-After`，停止连点重试；
  `1308` 消息里的无时区重置时间不自动换算成秒数，也不保证 `1302` 同时恢复。
  admission 原样透传上游错误；仅本地拒绝带 `X-CPA-Admission-Reason`。
- 路由清单声明 `input-modalities: [text]` 的模型不接受用户图片，包括旧会话
  中的图片。admission 在获取 lane 槽位前返回 HTTP 422
  `user_image_input_unsupported`、`upstream_contacted=false`，不带
  `Retry-After`。应新建纯文本聊天，或由用户明确选择支持图片的模型；不静默
  删除图片、不替换模型，也不重试同一不兼容请求。
- CPA 的既有 text-only 工具结果规范化仍处理历史工具图片；它不负责删除
  普通用户图片。当前 `glm-5.3` 声明为文本输入，`glm-5.3-flash` 没有该限制；
  以 `scripts/remote/cpa_provider_routes.json` 为能力真源，不按名称猜测。

| 层 | 处置 |
|---|---|
| `local_gate` | **不要调大 `maxAccountConcurrency`**。共享 lane 通常同时饱和，调大只会把"45s 快速失败"变成"120s 排队后失败"。根因是上游 turn 太慢占满槽位 |
| `admission_queue_timeout` | 上游变慢导致队列排不空。**不要在降级窗口调 admission 参数**——降级期的数据不代表容量 |
| `admission_fast_reject` | 冷却或队列满的秒拒。看 journal `reason=`：`cooldown` 要等账号恢复（账号侧被外部重置的例外见 §4.1），`busy` 是瞬时削峰，`rate_limited` 是命中 per-lane `requests_per_minute` 请求速率护栏（等 `Retry-After` 即可；若集中在真实单会话说明设定偏紧，应上调 conf 值而非清冷却/换号） |
| `upstream_capacity` | 上游自己的问题（overloaded / 账号额度）。本机无解；降低请求频率或换 lane |
| `dead_route` | 已知死路由（工具内 `DEAD_ROUTES`）。**该表当前为空**：历史 5xx 一律归 `upstream_capacity`，否则已恢复的路由会被永久误报为本地路由缺陷（2026-10-04 清空）。有命中才说明真的有一条被证实的死路由 |
| `slow_success` | 看第 5 节区分本机 / 上游 |
| `unclassified` | **先查这个**，不要相信其他计数 |

### 4.1 `credential_quota` 冷却：账号侧外部重置后不会自愈（2026-10-05 实证）

上游一次 `429 usage_limit_reached`（带 `resets_at` / `limit_window_minutes`）会让 CPA 把
**整张凭据**（sol/luna 共用同一 OAuth 账号）缓存为 `credential_quota` 冷却，**TTL 直接
锚定 `resets_at`**。TTL 内对这批模型的请求一律**本地快败**（实测 7ms，容器日志
`auth unavailable: ... in cooldown, reason=credential_quota, remaining=...`），
**不再出网试探**。

- **这个默认值是对的**：`resets_at` 是上游自己给的权威到期时间；提前试探 = 对已被限额
  的账号持续打必败请求（正是 9/21 定案的紧重试反模式）。与 `save-cooldown-status: false`
  （冷却不落盘、重启即清）配套，重启本身就是设计内的泄压阀。
- **缺口**：账号侧状态**外部**变更（Cockpit 消费 `rate-limit-reset-credits`、上游提前
  重置）不会给 CPA 任何信号，冷却照样挂到 `resets_at`。2026-10-05 实例：19:47:21 上游
  429（5h 窗口，resets 20:59:33）→ 19:47:49 账号侧重置已生效 → 19:47:50 仍 7ms 本地快败。

**恢复杠杆（按优先级）**：

1. **等 `resets_at`**（零操作）。
2. **CPAMC 认证文件页「清除冷却」**（免重启、单凭据、零中断，2026-10-05 bundle 级确证）：
   调用 `POST /v8/management/routing/cooldown/reset`（body `{"auth_index": ...}`），
   UI 自述"清除此凭证的本地路由冷却状态…立即再次参与请求，但不会恢复上游额度"。
   CPAMC（127.0.0.1:18317，管生产 CPA）持有管理明文 key，所以能用——脚本直调 401
   只因明文不在服务器（见杠杆 4）。**勿与配额管理页的「重置额度」混淆**：后者走
   `wham/rate-limit-reset-credits/consume` 消耗上游主动重置信用（与 Cockpit 消费同一
   机制），只清上游限额、不清本地冷却。局限：只清凭据冷却；若目录粘性隐藏同时在
   （`catalog_oauth_missing` 非空），用杠杆 3 一并清。
3. **`docker restart cli-proxy-api`**：兜底，一次清掉内存冷却 + 目录粘性隐藏，中断数秒，
   已验证主路径。2026-10-05 实证：20:10:21 重启 → doctor `cooldown_state=none`、目录 13 ID
   满编 → `gpt-6.1-sol` 单发 200/1.86s（LIVE_ACCEPTED，证明账号侧确已放行）。
   **不需要重启 admission**：`cpa-admission` 是独立 systemd 服务，重启 CPA 不会清它的
   lane 冷却（同样锚定 `resets_at`，journal 里会残留几分钟 `lane_reject reason=cooldown`
   秒拒），但它有 **half-open 恢复探针**（冷却期每个 interval 放行一个真实上游探针），
   CPA 健康后拿到 200 即自动复元——实证：重启后 33 分钟公网全链 sol 200/1.50s，
   早于 `resets_at`。
4. **脚本直调管理端点**：本部署 401（config `secret-key` 为 bcrypt 卢摘要、容器无
   `MANAGEMENT_PASSWORD`、server 模式无 `localPassword`）。明文 key 只存在于 CPAMC
   配置中——要走脚本化路径先解决明文分发（注入 env 或读取 CPAMC 配置），否则直接用
   杠杆 2/3，不要在这里耗时间。

**恢复后判据**：doctor `==cooldown-state==` 段 `cooldown_state=none` +
`catalog_oauth_missing=none`，再补一发单发生成探针——200 = 账号侧真放行；仍 429
`usage_limit_reached` = 重置未覆盖该窗口，回到杠杆 1 等真到期。**探针必须走公网全链**
（nginx → admission → CPA）：直探 `127.0.0.1:8317` 绕过 admission，admission 自身冷却
未消时会出现"探针绿、桌面仍 429"的假绿。公网探针形态见 cpa_bwg_guardrails.ps1 的
public-route 契约段（`--resolve` 到 127.0.0.1，prefix/key 全程留在远端变量里）。

### 4.2 上游模型级配额 6004：先查当日用量曲线，再决定等或分（2026-10-31 / 11-04 实证）

workbuddy 上游对单模型的 `code 6004` 限流是**滚动窗口配额**，不是随机抖动。
窗口确切长度无法从外部得知（形态为数小时滚动窗），但单次事件内可按两层
重置形态分诊：

- **固定 age-out 时刻**：同一凭据的多条 6004 全部指向同一未来时刻
  （实测 65 条齐指 `23:04:33`）= 该凭据该模型的窗口配额已烧穿，重置点 =
  窗口内最早请求 + 窗口长度。期间该凭据对该模型零出力，等重置即可；
  勿清冷却、勿加压重试。CPA 冷却镜像会反复回放同一重置时刻（实测 315 次），
  计数时勿把镜像行当独立上游拒绝。
- **短重置（60–90s，常成对出现）** = 突发层限流；请求经凭据间 failover
  通常仍 200。持续成片出现说明瞬时速率贴着上游阈值。
- **行数 ≠ 拒绝次数（2026-11-04 实证，归因第一步）**：同一批 6004 行先按
  `duration` 分族——**50–90ms 是插件内存 park 表的本地回放**（未打上游），
  **0.7–1.6s 才是真实上游拒绝**。实测 45 分钟 270 行里真实仅 6 次。
  回放行数还被扇出放大：`max-retry-credentials: 0` 时一次客户端请求会依次
  试完全部凭据，**一次请求 = 每凭据一行 6004**（实证请求 `[7dcb591c]`
  22:22:55 起 10 秒内试完 11 个凭据全 6004，再落 OpenCode 17.2s 返回 200）。
  判"是否全池被限"看**每凭据的真实拒绝次数**，不看日志行数。
  无 reset 时间戳的 6004（intl 那批）插件按 ~60s 重试，账号一恢复就被下一
  请求打回，**活跃流量下不会自愈**；只有带明确 age-out 时刻的才会真正到期。

**复发时的第一步是拉当日分时用量与 6004 时间线对照**：
`docker logs --since 30h cli-proxy-api 2>&1 | grep gin_logger | grep -oE "^\[2026-10-31 [0-9]{2}" | sort | uniq -c`。
重度 agent 会话（峰值 ≥14 req/min、日 >2000 请求）烧穿全部凭据配额属
**供需缺口**，不是网关缺陷：处置 = 需求分域（重会话切其它 lane/模型）、
加凭据、削峰（per-lane `requests_per_minute` 只压突发层，不解决总量）；
改冷却秒数或加大重试只会烧穿更快。同族历史：9/21、10-19、10-20、10-30、
10-31——凡"同族反复出现"，先按本节把供需对上，再谈单次归因。

**6004 ≠ 积分耗尽（2026-11-04 补）**：凭据 `note` 台账显示 11 个账号里 10 个
余量充足（余 92–2850）仍全部 6004；唯一耗尽的账号回的是裸 429 而非 6004。
故"没消耗 token 的账号也 6004"是正常形态，**不要用积分余额反推限流原因**。
当日形态：11 凭据 45 分钟仅承接约 25 次请求（0.55 req/min）仍全灭，且各凭据
`loginPlatform` 完全一致（intl 全 `ide`、cn 全 `CLI`）⇒ 该量级下"加凭据 /
削峰"都无效，应先按 **单账号单发探针**（只打一个凭据、不扇出、不重试）验证
限制键在账号窗口还是在登录面/出口，再决定保留还是停用该 lane。

**限制键已验证（2026-11-04 22:23–23:20 UTC+8 窗实证，悬案收口）**：单账号隔离
探针做不了（管理端点 401、客户端无法关 failover），改用分布证据 + 重置探针：
8h 窗 837 行 6004 中真实上游拒绝仅 363 次（其余 474 行为 park 表 <300ms 回放），
**7 个 intl 凭据各 47–51 次真实拒绝几乎相等，且 age-out 同秒同步（23:21:12/13/14）
⇒ intl 限制键是共享桶（键含出口 IP 或登录面，外部无法进一步分辨），不是账号
独立窗口**；3 个 CN 凭据各 5–8 次、reset 各隔 ~1h（次日 11:47/12:44/13:49）
⇒ CN 为账号独立窗口。intl 重置后 23:25 单发 8318 探针 200/pong ⇒ lane 自愈、
保留该 lane。共享桶下加凭据不增容量，"intl 集体 6004"与"多数账号没消耗 token"
完全自洽——躺枪是结构性的，不是账号各自烧穿。

**2026-11-05 BWG 运行行为**：生产 WorkBuddy 插件在当前 `0.9.85` 基线上叠加
INTL 共享窗口补丁。一个真实 INTL 6004 会同时建立 `realm=intl + model` reset：

- 同一请求中，后续 INTL 候选只产生约 100–200ms 的本地共享窗口拒绝，不再分别
  访问上游；真实上游 6004 仍约 0.7–2s。
- CN 和 Global 不进入该共享键；CN 的三个独立窗口仍可能各产生一次真实拒绝，
  这是保留其独立配额语义的预期行为。
- 全部 WorkBuddy 候选仍失败时，宿主可继续尝试 OpenCode；该补丁不增加上游
  额度、不解除共享限流，也不证明账号长期安全。

## 5. 慢速归因：本机还是上游

**永远用两个计时器相减**，不要凭感觉：

- 远端 nginx：同一条日志行里 `request_time`（总）vs `upstream_time`（上游）。本地占
  3–4%（实测 0.15–0.25s）⇒ 慢在上游。
  - `upstream_header_time` 是 Nginx 收到 admission 响应头的时间，不是客户端
    首个可见文字时间；`upstream_response_time` 对 SSE 是整个 turn。
- admission journal 的 `upstream_header_ms` 是收到上游响应头的耗时，
  `upstream_first_byte_ms` 是收到首个上游 payload 的耗时（不计本地 SSE
  heartbeat），`first_text_ms` 是收到首个非空可见文字事件的耗时。首个 payload
  可能是元数据，因此 `upstream_first_byte_ms` 不能单独推出首字或输出 tokens/s。
    doctor 保留整体分位数，并以 `latency_group` 按 lane、模型、完成结果拆分，
    避免把容量失败的等待与成功请求混在一起归因。只输出本机 admission 清单中
    的模型和已知结果，其他名称合并为 `other`/`unknown`。两项上游指标都很快
    但客户端仍成块收到时，再检查 Nginx/sidecar buffering 与客户端读取。
- 本机：`request_logs.latency_ms` 是端到端总时长；成功请求 p50 30s+ 属上游生成时间。

若本机占比只有几个百分点，**不要在网关上找慢的原因**。

## 6. capacity 与"上游支持几并发"

- 上游容量信号：`server_is_overloaded`（常带 `x-retry-metadata: NO_MORE_RETRY`）、
  `usage_limit_reached`（带 `resets_at`）、`service_unavailable_error`。
- CPA 另有 **Home 并发协议**（`credential_concurrency_exceeded` /
  `credential_model_concurrency_exceeded`），上游会下发
  `{accounted, credential_id, model}` 元组。**该协议存在 ≠ 我们撞过它**。
  2026-10-02 全量核查：容器日志保留期内这些错误串命中数为 **0**，`config.yaml` 里也没有
  任何并发配置项 ⇒ **没有证据支持"上游按 N 并发限制我们"**。看到的 5xx 是**负载卸载**，
  与"每凭据并发上限"是两件事。
- 要真定上游并发上限，只能在**上游健康时**做 2/4/6/8 受控梯度；降级窗口测没有意义。
- 本栈的并发数字只有 `max_inflight=2`、`maxAccountConcurrency=3`、lane 总容量 `2+4=6`；
  唯一的 "4" 是 `ADMISSION_MAX_PENDING=4`（队列深度），不是上游能力。

### 症状 A（`Selected model is at capacity`）的精确指纹与确认

**它不是一个错误码，是客户端对上游 overload 载荷的固定文案。** 2026-10-04 取到原文：

```
provider=codex model=gpt-6.1-sol auth_file=…-plus.json
err={"error":{"type":"service_unavailable_error","code":"server_is_overloaded",
     "headers":{"x-retry-metadata":"NO_MORE_RETRY"},
     "message":"Our servers are currently overloaded. Please try again later."}}
```

一条命令确认（**唯一能看到这个载荷的地方是容器日志**，本机 sqlite 与 nginx 都看不到）：

```bash
docker logs cli-proxy-api --since 30m 2>&1 | grep -iE 'server_is_overloaded|usage_limit_reached'
```

判读要点：

1. **本机日志里搜不到 `capacity` 字面量是正常的**（实测 24h = 0 行）。本机能看到的相关行是
   `http_status=503/502 + error_category=upstream_error`（CPA 中继/冷却），
   或干脆是一条 200 —— 因为 `stream-bootstrap-buffering=false` 时 CPA 会把上游过载
   **回显成 200 + 体内标记**。**不要因为本机没有 `capacity` 字样就断言"本栈无责"**，
   要用上面的容器日志确认。
2. **admission 确实在学**（72h 实测）：`chatgpt-oauth` lane 上
   `status=200 capacity=true` 47 次、`status=502 capacity=true` 12 次、`503 capacity=true` 58 次
   ⇒ 过载即使被 CPA 渲染成 200/502，只要体内标记在探测窗口内，熔断就算得出来。
   502 不在 `capacity_statuses`（只有 429/503）里，靠的是 `capacity_markers` 命中。
3. **客户端对这条文案不自动重试**（对 429 才重试）⇒ 用户感觉像"卡住/失败"而不是"可重试"。
   这是为什么"本地闸门的 429"换成"上游的 at capacity"后**体感更差**，
   尽管两者描述的是同一个事实（单账号饱和）。
4. **正确处置**：把桌面模型切到**另一条非 OAuth lane**（如 `glm-5.3-flash`；
   切 lane 只分流负载，不是规避账号风控的手段），
   见 [封号/限流/降智应急响应](cpa-ban-throttle-incident-response.md) 的 L2 分流顺序。
   **不要**为了让它变回 429 去调 `ADMISSION_COOLDOWN_FAILURE_THRESHOLD`——
   threshold=1 已被实测否决（一次抖动会把整条 lane 锁满 60s，而上游其实已经在服务）。
5. **`503 auth_unavailable` 是同一过载的续发形态，不是第二个故障**（2026-10-24 实测）：
   一次上游 `server_is_overloaded` 会让 CPA 把该 provider 的**唯一**凭据瞬时冷却
   （`transient-error-cooldown-seconds: 30`，2026-10-31 由 60s 收紧；或上游 `Retry-After`，后者优先），冷却期内后续请求
   **零出网**直接回 `503 auth_unavailable`。本部署 codex 只有 1 个 OAuth 凭据
   （doctor `oauth_codex_files=1`），没有可转移对象；`stream-bootstrap-buffering`
   故意保持 `false`（见 `scripts/remote/cpa_policy.py` 的 `EXPECTED_CODEX` 注释）。
   判读：保留转储全部 `recognised`（503 + `server_is_overloaded`）时，**不要**改
   `capacity_markers`、**不要**清冷却；`transient-error-cooldown-seconds` 已是审查过的
   30s 地板（不要再在降级窗临时调大或清零）；
   等窗口结束或切非 OAuth lane 即可。
6. **2026-11-04 实证增补（高峰窗三变体 + 排回归方法）**：
   ①文案族扩展：上游 502 带 `"Our servers are experiencing high demand"`
   （区别于 10-04 的 `server_is_overloaded`/"currently overloaded"）；luna/sol/terra
   同窗命中同一文案 = **模型侧全局容量而非账号配额，加凭据不绕过**。
   容器侧确认命令的 grep 要同时覆盖 `high demand|overloaded`。
   ②200+体内标记形态的体感更差：admission 队列放行后上游才吐 capacity 信封
   （实测 waited_ms=20803 + 159KB SSE 被嗅探分类），"横幅 + 长等待"是同一事件的
   两个侧面。
   ③**怀疑升级/变更引入时先定位 onset**（若错误模式早于变更时间点即排除回归；
   2026-11-03 v8.0.53 swap 即由此排除——错误模式先于 swap 存在）：

   ```bash
   journalctl --no-pager -S "24h ago" | grep upstream_result | grep "lane=chatgpt" \
     | grep -E "result=capacity|status=50[23]" | awk '{print substr($0,1,9)}' | sort | uniq -c
   ```

   ④实时判读当前停了哪个模型：8318 `/healthz` 的 `model_cooldowns` +
   `half_open_probe`；`cooldown_active=false` 但个别模型仍有剩余秒数 = **车道半恢复**
   （2026-11-04 实测仅 terra 停 599s），切未停模型即可、勿清冷却。
   capacity 与 completed 长时段交替（整夜 190 completed 夹 capacity 小时簇）=
   波动窗而非故障，勿开工单勿调参。

## 7. 变更与投影闭环（runbook 重投影）

改**路由清单 / guardrail / admission 参数**时的固定顺序：

```
改清单 → 跑门禁 → 提交 → -Apply → 复跑 doctor
```

原因与坑：

1. `doctor` 的 `projection-drift` 比对的是 **`git show HEAD:<path>` 的 blob（LF）**，
   **不是工作区**。只改不提交 = 必然报 `MISMATCH` + `DOCTOR_CONTRACT_FAILED`。
2. `-Apply` 的 `Assert-ProjectionSourcesUnchanged` 要求**投影源工作区干净**，而
   `scripts/cpa_bwg_guardrails.ps1` **本身就在投影源集合内** ⇒ 改了 guardrail 就
   **必须先提交**才能 `-Apply`。
3. `-Apply` 重启 admission 会让 `127.0.0.1:8318/healthz` 短暂 reset，命中 readiness
   轮询即 `ROLLBACK` —— **重跑即可**。成功判据是 stdout 出现 **`GUARDRAILS_APPLIED`**，
   不是退出码。
4. 比对目录用**已部署**的清单（`/opt/cliproxyapi/cpa_provider_routes.json`），
   不要用工作区（并行会话常有未提交改动）。
5. **nginx 契约点 3 处**必须同步：doctor 断言/输出、`-Apply` 的 `required` 清单、
   `ensure_nginx_directive` 锚点；再加
   `tests/test_cpa_guardrails_script.py` 断言 + runbook + 变更证据。
6. **admission 参数是带契约的硬编码常量**：`cpa-admission.py` 里的
   `ADMISSION_MAX_INFLIGHT_BY_LANE` / `ADMISSION_MAX_PENDING` /
   `ADMISSION_QUEUE_TIMEOUT_SECONDS` 与 `cpa-admission.json` 不一致就会 `raise`。
   改值必须**同一提交**同步 `.py` + `.json` + 测试 + runbook。

## 8. 本机 Cockpit 侧变更

- **生成器不复制账号并发族**：`apply_provider_gateway_template_settings`
  （`codex_local_access_provider_gateway.rs`，main 上 `:1566`）只复制 **18** 个无关字段，
  `max_account_concurrency` 与 `account_concurrency_wait_ms` 都不在其中
  ⇒ provider gateway 侧车永远只看到结构体默认值（0 / 120000ms），**UI 设置对它无效**。
- 因此**只有 sidecar 二进制入口的钳制能生效**（`clampMaxAccountConcurrency` /
  `capAccountConcurrencyWaitMs`）；改 `codex_local_access.json` 只影响 API 服务侧车。
- 落盘走 `write_secret_string_atomic_if_changed`（**内容不变就跳过写入**）⇒ 生成内容
  没变时 manifest 的 mtime 不会更新。**"文件陈旧"是这个缺陷的自证症状，不是独立问题。**
- **重载 sidecar 只有两条路径**：Cockpit 应用重启、或 Codex 切号。**禁止 `taskkill`**。
  重载前磁盘值与运行值不一致属预期。
- 验收判据：本机 429 的 `latency_ms` 指纹应从旧预算迁到新预算
  （实测 120.002s → 45.003s 受控对照，`ACCEPTANCE_PASS`）。

## 9. 上游贡献边界

- **特性进上游，workaround 不进**。入口钳制（`RequestRetry=0` /
  `StreamBootstrapBuffering=false` / 并发族钳制）是为绕过生成器缺陷的临时手段，
  上游该修的是生成器本身，**不要把这些提 PR**。
- 现状：`#2676`（生成器不复制字段，已补评论）、`#2677`（修该字段的 PR）、
  `#2697`（按账号限流 + 尊重 Retry-After 的实现 PR）。
- **`conclusion=action_required` 不是 CI 失败**，而是 fork PR 等维护者批准 workflow；
  正确动作是留评论请批准，不是改代码。

## 10. 纪律清单

**Do**

- 先跑 `scripts/cpa_failure_triage.py`，看 `unclassified` 是否为零。
- 账号侧消费 `rate-limit-reset-credits` 后，CPA 冷却**不会自愈**——按 §4.1 清内存态，不要干等也不要反复探针。
- 报数时永远给"时间窗 + 客户端平面（external/loopback）+ 按小时分布"。
- 远端与本机计数不一致时，先想"这一层会不会根本不写那条日志"。
- 变更前先想回滚；`-Apply` 前先备份、先提交。

**Don't**

- 不要只看状态码归层。
- 不要用降级窗口的数据调参。
- 不要在"上游慢"时调大并发——只会把快速失败换成排队失败。
- 不要把本机闸门 429 与远端 nginx 429 相加。
- 不要在没有对照的情况下宣称"已修复"——至少要有前后指纹对照或受控 A/B。


### SSE 检查上限与坏 JSON 的区别

- `protocol_result=inspection_limit` 表示有事件超出探针的有界检查上限，不能证明上游 JSON 损坏；保留不完整检查状态，不据此恢复半开 lane。
- `protocol_result=malformed_event` 表示已检查的事件发生 JSON/编码解析失败；后续合法完成事件不能将该请求转为成功或清除容量失败状态。
- `sse_inspection_complete`、`sse_malformed_json`、`sse_terminal_seen` 分别记录检查是否完整、是否确有解析失败、是否见到本协议的成功终止事件，不记录正文。
- 旧版本 `malformed_event` 同时包含检查超限，历史汇总无法追溯拆分。探针只检查转发内容；检查超限不等于网关主动截断响应。
