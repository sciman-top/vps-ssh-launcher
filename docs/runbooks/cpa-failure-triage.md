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
不读该头（退避固定 200ms×2ⁿ±10%，默认 4 次），本机两个 provider 应钉
`request_max_retries=0`/`stream_max_retries=2`。普通 HTTP 失败不会被客户端重放，
只有已开始且被截断的 SSE 流保留有限恢复机会。秒级连发来自仍持旧默认重试预算
的会话，处置是重启该会话，不是改服务端参数或重启 admission。

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
| 缓存命中率 | `cache-canary` 受控采样（无真实流量聚合通道） | 见下 |

缓存遥测：CPA 的 usage 统计只存在于内存与管理 API（无 MANAGEMENT_PASSWORD，
fail-closed 不注入），全量请求日志仅错误时落盘（5 文件轮换）——**真实流量的
token/cache 聚合不可行**，替代通道是按 lane 的受控采样：

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
| `admission_fast_reject` | 冷却或队列满的秒拒。看 journal `reason=`：`cooldown` 要等账号恢复（账号侧被外部重置的例外见 §4.1），`busy` 是瞬时削峰 |
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

## 5. 慢速归因：本机还是上游

**永远用两个计时器相减**，不要凭感觉：

- 远端 nginx：同一条日志行里 `request_time`（总）vs `upstream_time`（上游）。本地占
  3–4%（实测 0.15–0.25s）⇒ 慢在上游。
  - `upstream_header_time` 是 Nginx 收到 admission 响应头的时间，不是客户端
    首个可见文字时间；`upstream_response_time` 对 SSE 是整个 turn。
- admission journal 的 `upstream_header_ms` 是收到上游响应头的耗时，
  `upstream_first_byte_ms` 是收到首个上游 payload 的耗时（不计本地 SSE
    heartbeat）。首个 payload 可能是元数据，也不能据此推出首字或输出 tokens/s。
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
   （`transient-error-cooldown-seconds: 60`，或上游 `Retry-After`），冷却期内后续请求
   **零出网**直接回 `503 auth_unavailable`。本部署 codex 只有 1 个 OAuth 凭据
   （doctor `oauth_codex_files=1`），没有可转移对象；`stream-bootstrap-buffering`
   故意保持 `false`（见 `scripts/remote/cpa_policy.py` 的 `EXPECTED_CODEX` 注释）。
   判读：保留转储全部 `recognised`（503 + `server_is_overloaded`）时，**不要**改
   `capacity_markers`、**不要**清冷却、**不要**缩短 `transient-error-cooldown-seconds`；
   等窗口结束或切非 OAuth lane 即可。

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
