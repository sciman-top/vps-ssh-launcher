# BWG CPA 流式首字节延迟：bootstrap 扣留根因定位与投影（2026-09-28）

## 范围与授权

- 代码基线：`e031e7e` → 本次提交 `8903e6d`（脚本投影）+ 本文档所在提交（runbook 与断言）。
- 目标：仅 `bwg`。`zz` 未访问、未读取、未修改。未轮换凭据、未轮换公网随机路径、
  未消费原始 Usage queue、未重启主机、未改动 nginx 限流参数。
- 延续本日既有 BWG-only 授权（执行 `-Apply`），并显式获准消费 OAuth/Usage/token
  用于受控实战验收（探针本身会消耗真实 OAuth 轮次）。
- SSH 使用严格 host-key checking。证据不含密码、私钥、token、订阅地址、随机公网
  路径、公共 IP、请求体或响应正文。

## 现象与分层判定

用户报告：ChatGPT desktop 经 Cockpit Tools 的 Direct OAuth / Direct API 调用
`fq.sciman.top:8443` 上的 `gpt-6-luna`「token 吐出缓慢」，CPA 路径尤其明显。

分层只读探针（同一请求体，逐层单发，消耗真实 OAuth 轮次）：

| 目标 | 首字节 (headers) | 总时长 | 分块数 | 分块间隔 p50 |
|---|---|---|---|---|
| CPA 直连 `127.0.0.1:8317` | **10272 ms** | 15232 ms | 70 | **0 ms** |
| admission `127.0.0.1:8318` | 9442 ms | 13019 ms | 33 | 57 ms |

`gap_p50 = 0 ms` 是关键指纹：分块不是持续流出，而是**同一毫秒内整批爆出**。
配合 `first_event = "event: response.created"`，说明 CPA 把整个 SSE 握手扣在缓冲区里，
直到上游产出第一个生成事件才一次性下发。

对照基线：同一链路上 `glm-5.3-flash`（非 Codex executor）首字节 **1435 ms** ——
证明 CPA 自身的转发开销只有百毫秒量级，10 秒不是链路或 Python 准入层造成的。

## 根因

`codex.stream-bootstrap-buffering: true`。CPA 在该开关打开时，把「尚未产生可观察
输出」的帧（`response.created` / `response.in_progress` / `codex.rate_limits` /
`codex.response.metadata` / `keepalive` / 空 `*.added`）全部扣住，直到上游产出第一个
真实内容增量才提交下游响应头。官方 `config.example.yaml` 对该开关的说明与实测一致：

> Trade-off: the response headers are delayed until the upstream starts generating,
> which on a slow reasoning turn now means several heartbeat intervals rather than one

即：**首字节延迟 = 上游首个生成 token 的延迟**。Luna 的推理阶段约 9–10 秒，于是每一轮
客户端都先经历约 10 秒「零字节」，再看到整批握手 + token。用户感知为「卡死 → 突然
吐字」，与 `token 吐出缓慢` 的描述完全一致。

源码核对（`v7.3.17` 与 `v8.0.2` 逐字节同构，与镜像版本无关）：
`internal/runtime/executor/codex_executor_stream.go` 的 `buffering` 分支、
`codex_executor_terminal.go` 的 `isCodexBootstrapBufferableEvent` /
`codexBootstrapMaxBufferedFrames=48` / `codexBootstrapMaxBufferedBytes=1<<20`。

### 该开关换来的分类在本机是冗余的

开关的收益是：把上游藏在 HTTP 200 流内的 `server_is_overloaded` 变成真正的 503，
以便（a）换凭据重试、（b）让熔断/冷却按状态码分类。本机两条收益都不成立：

1. **没有可切换的凭据**：`/opt/cliproxyapi/auth/` 下只有
   `codex-1c3cf6c3-*.json` 一个 Codex 账号，`max-retry-credentials: 1` 无对象可换。
2. **准入层自己就能分类**：`cpa-admission` 的 `is_capacity_response()` 只要
   `status ∈ {429,503}` **或** 前 256 KiB 响应体内出现 lane 的 capacity marker
   （含 `server_is_overloaded`）即判为容量失败。实测 6 小时内出现 **13 次
   `status=200 capacity=true`** —— 正是「bootstrap 预算耗尽 / 上限已到，过载在流内
   下发」的那条路径 —— 并且这 13 次都正常打开了 lane 熔断。

因此关闭该开关不削弱任何一层保护，只是把过载判定从「提前 10 秒的 503」改成
「流内错误 + 准入层按响应体 marker 熔断」，而后者本来就已经在承担大部分判定。

## 修复

1. `scripts/remote/cpa_policy.py` / `scripts/cpa_bwg_guardrails.ps1`：
   `codex.stream-bootstrap-buffering: false`、`codex.stream-bootstrap-timeout: "0"`。
   保留 `timeout` 键并显式写 `"0"`，使「重新打开」必须是一次刻意编辑而非顺手改动。
2. `cpa_safe` 日志格式**末尾**追加 `upstream_header_time=$upstream_header_time`。
   `upstream_time` 是整轮总时长，把「网关扣住响应头 10 秒」和「上游生成慢」混成同一个
   数；header-time 是唯一能分离二者的字段。追加在末尾以免移位既有解析锚点
   （`bytes=` / `limit_req=`），并新增 `pre_ttfb_log_format` 作为 `-Apply` 的迁移源。
3. `scripts/remote/cpa-admission.py`：新增 `requested_model()`，`requested_lane()` 改为
   复用它；日志行改用 `observed_model`。此前所有非 lane 流量都记成 `model=other`，
   恰好把「一批 pass-through 5xx 到底是谁在打」这个问题本身的信息抹掉了。

## 验收（投影后实测）

- 服务：`cpa-admission` `ActiveEnterTimestamp=2026-09-28 16:09:42 UTC`；远端
  `cpa-admission.py` / `cpa_policy.py` 的 sha256（LF 归一化）与 `HEAD` blob 逐字节相同。
- 远端 `config.yaml`：`stream-bootstrap-buffering: false`、`stream-bootstrap-timeout: '0'`。
- nginx 已 reload，`cpa_safe` 新字段生效（日志 19/19 行带 `upstream_header_time`）。

同请求体复测：

| 目标 | 修复前首字节 | 修复后首字节 | 总时长 |
|---|---|---|---|
| CPA 直连 `8317` | 10272 ms | **653 ms** | 15232 → 4103 ms |
| admission `8318` | 9442 ms | 36182 ms（单飞排队） | 13019 → 38128 ms |
| 公网 `8443` | — | 13118 ms（排队） | 16784 ms |

真实流量（reload 后 19 行）：`route=responses status=200` 的
**TTFB p50 = 1.68 s / p90 = 6.95 s / max = 13.11 s**，总时长 p50 = 13.57 s。

准入层：重启后 `lane_reject = 0`、`Traceback = 0`，全部 `upstream_result` 为
`capacity=false`。

## 第二轮：单飞排队（残余边界 1）的受控 A/B 与修复

第一轮把 `max_inflight=1` 记为「残余边界」，理由是「并发时首字节可达 36 s」。受控并发
回放把它推翻了：它不只是慢，而是**结构性拒绝**。

### 暴露过程

3 并发 `gpt-6-luna` 打 admission 8318（A 相，`max_inflight=1`）：

```
{"label":"c1","http_status":429,"elapsed_ms":120035}
{"label":"c2","http_status":429,"elapsed_ms":120002}
{"label":"c3","http_status":429,"elapsed_ms":120002}
journal: lane_reject lane=chatgpt-oauth model=gpt-6-luna reason=queue_timeout waited_ms=120000  ×3
```

三个全灭，等待时间精确等于预算。同时 CPA 侧同窗口的 turn 时长实测为
**39.4 s / 1 m 52 s / 3 m 36 s**，healthz 显示 `inflight=1` 被一个长 turn 占满。

### 根因

`max_inflight=1` + 上游 turn 39–216 s ⇒ 同一轮的第二个请求**必然**排满整轮，然后在
`queue_timeout_seconds=120` 处被拒。而客户端在 **~45 s** 就放弃（doctor：
`client_abort_request_time` 32 次 499 聚在 45.0–45.05 s）—— 客户端永远等不到那个 120 s。
所以这些 429 与上游过载无关，是单飞设计的必然产物。

**原有单飞理由已被数据推翻**：上游错误是 `server_is_overloaded`（全局容量信号，带
`x-retry-metadata: NO_MORE_RETRY`），不是 `rate_limit_exceeded` / `usage_limit_reached`
这类账号级信号；且 `inflight=1` 期间仍有 ~3% 的 capacity 命中（luna 6 h 内 503×11 +
`200 capacity=true`×7），说明**并发不是触发器**。

### 修复

`chatgpt-oauth` 的 `max_inflight` 由 1 提到 **2**，四处契约同步：
`cpa-admission.json`、`cpa-admission.py` 的 `ADMISSION_MAX_INFLIGHT_BY_LANE`、
`cpa_policy.py` 的 `EXPECTED_ADMISSION_MAX_INFLIGHT`、guardrails 的两个
`expected_max_inflight`。取 2 而非 3：目标是让 desktop 常见的主响应 + 标题/摘要对不再
互锁，同时不把单账号开放成扇出；GLM/DeepSeek 在各自独立额度下保持 3。

### 修复后同一回放（B 相，`max_inflight=2`）

```
{"label":"c1","http_status":200,"ttfb_ms":665,"total_ms":1895}
{"label":"c2","http_status":200,"ttfb_ms":2791,"total_ms":5908}
{"label":"c3","http_status":200,"ttfb_ms":6388,"total_ms":8326}
journal waited_ms: 0 / 1747 / 5757   （且与真实 desktop gpt-6-sol 流量同时成立）
```

**0/3 成功 → 3/3 成功**，最大首字节 6.4 s，无 429。真实 desktop 流量同期
`waited_ms=0`，未受影响。

### 配套的队列观测与回收（同一轮）

- `Lease.waited_ms`：`acquire()` 全程打点并写进 `lane_reject` / `upstream_result` /
  `downstream_disconnect` / `upstream_error` 四条日志。此前排队等待完全不可见，
  nginx 的 `upstream_time` 把排队与生成折成一个数。
- `acquire(alive)`：等待改为 1 s 分片，分片醒来发现下游已离开就以
  `reason=downstream_gone` 交回 pending 槽位，不再占满 120 s 预算。
  `Handler._downstream_alive()` 用 `select` + `MSG_PEEK`；**刻意不对称**：缓冲区非空
  一律视为存活（pipelined 请求与「body 后接 FIN」在 socket 层不可区分，消费该字节会
  破坏 keep-alive 的下一行请求行）。漏回收退化成原来的整段等待，误回收会拒掉活请求。

投影字节校验：远端 `cpa-admission.py` / `cpa-admission.json` / `cpa_policy.py`
的 sha256（LF 归一化）与 `HEAD` blob 逐字节相同。

### 尚未闭合

- 若 desktop 同时发 3 个以上 luna/sol 请求，第 3 个仍会排队。是否继续放宽需以
  更长窗口的 `capacity=true` 比例为准；本轮只做了 3 并发这一档的对照。
- 长 turn（216 s）本身仍会占满 2 个槽位中的 1 个，这是共享订阅账号的固有上限，
  本地无法消除。

## 第三轮：并发上限与中转可用性的最终判定

### 并发上限：不再是约束

`max_inflight=2` 之后再扫 N=2/3/4 无法得到干净读数 —— 不是方法问题，是**上游容量事件
成了主导项**。90 分钟窗口（15:20–16:50 UTC）OAuth lane 实测：

| 指标 | 值 |
|---|---|
| `upstream_result` 合计 | **223**（200×214 / 503×8 / 502×1）⇒ 成功率 **96%** |
| `lane_reject` 合计 | **38**（cooldown 33 / queue_timeout 5） |
| `lane_probe`（半开验证） | 10 |
| `capacity=true` | 24 |

一次上游 `server_is_overloaded` 会同时触发两层冷却：CPA 侧凭据冷却
（`transient-error-cooldown-seconds: 60`，返回 503 `auth_unavailable`）与 admission 的
lane 熔断。冷却期内所有并发请求都是快速 429，与 `max_inflight` 无关 —— 这就是扫描
读数全部落在 `reason=cooldown` 的原因。

**结论**：单飞排队（第一轮修）与并发上限（第二轮修）都已不是 binding constraint；
剩下的 14.6% 拒绝率来自上游容量，本地无法消除。`queue_timeout` 只剩 5 次/90 分钟。

补充观察（未改，留证据）：24 次 capacity 事件里 8 次带 `Retry-After`，而
`ADMISSION_COOLDOWN_FAILURE_THRESHOLD=2` 在 `retry_after is not None` 分支被**有意
旁路**（"honour it immediately rather than waiting for the streak to build"）。也就是说
阈值=2 的「单次抖动不开闸」保护，对带 `Retry-After` 的 503 不生效。这是设计取舍，
不是缺陷；要收紧需先有更长窗口的对照。

### 中转可用性：两家中转对 CPA 映射的模型全部失效（第三方，非本仓）

用各自 `/v1/models` 返回 200 的**有效** key 直连测试（不经过 CPA）：

| 中转 | `/v1/models` | `/v1/chat/completions` | `/v1/responses` |
|---|---|---|---|
| ai.input.im | 200（14 个模型，含全部目标名） | 502 `Upstream access forbidden` | 502 同上 |
| codex.ciii.club | 200（7 个模型，含全部目标名） | 400 `unknown provider for model …` | 502 `Upstream request failed` |

逐模型（ai.input.im）：`gpt-6-astra` / `gpt-5.6-sol` / `gpt-6-sol` / `gpt-5.6-terra`
全部 502；`deepseek-v4.1-flash` 503；`gpt-image-2.5` 400（该模型本就不在
Chat Completions 端点）。

**所以这不是 key 失效、不是 wire 协议、不是本仓路由写错** —— 两家中转自己的上游/权益
有问题，而它们的 `/v1/models` 仍在对外宣称这些名字。本仓映射到它们的所有别名
（`gpt-6-astra`、`gpt-5.6-sol`、`gpt-6-sol-input`、`deepseek-v4.1-flash`、
`gpt-image-2.5`、`gpt-6-astra-cii`、`gpt-6-sol-cii`）当前**全部不可用**，而它们仍在
Cockpit 的客户端 catalog 里，用户选中即失败。

**本地唯一可做的是停止对外宣称**（改 `cpa_provider_routes.json` 与 guardrails 的
catalog 断言，或从 Cockpit catalog 摘掉），但路由清单是本仓声明的唯一事实源、属用户
决策项，本轮不擅自改动。

### 附加确认：请求体编码不影响归因

`outputs/body_encoding_probe.py` 验证：plain JSON 与「声明 gzip 但明文发送」都能正确
归因模型（`model=gpt-6-sol-91`）；真 gzip 体无法解析（`model=other`），但 CPA 直接
400 `unsupported request content encoding: gzip` 拒绝，**不会**变成 503。故历史
229 次 `model=other` 的快速 503 与编码无关；结合 CPA 错误转储
（`UA: Go-http-client/2.0`、`model=gpt-6-sol`、`status=503`）可确认那批 503 就是
OAuth 凭据冷却的快速失败，新日志已能正确归因到 lane 与模型。

## 残余边界（未修，明确记录）

1. ~~**单飞排队是新的最大项**~~ —— **已在第二轮修复**：`max_inflight` 1→2，受控 3 并发
   回放由 0/3 成功变为 3/3 成功（详见上节）。6 小时内 `lane_reject` 分解为
   cooldown 17 / half_open_probe 6 / queue_timeout 6 / busy 4，其中 `queue_timeout=6`
   正是本缺陷的直接计数。
2. **上游容量仍在**（非本仓缺陷）：`responses 200` 的整轮时长 p50 15.4 s / p90 48.8 s；
   `server_is_overloaded` 约占 3%。`server_is_overloaded` 是 OpenAI 侧全局容量信号，
   本地无法消除，只能靠准入层快速失败 + 冷却。
3. **客户端模型目录漂移**（Cockpit 侧，非本仓）：`fq.sciman.top` 的 catalog 里有
   CPA 无法服务的 `gpt-6-sol-91`（400 `model_not_found`）；`ai.input.im` 与
   `codex.ciii.club` 两家**对 CPA 映射的所有模型全部失效**（第三轮已用有效 key
   直连证实是中转自身问题）。本地只能停止对外宣称，属用户决策项。
4. 存量测试失败：`test_cpa_prune_backups_keeps_newest_backup_dirs` 与
   `test_v2ray_agent_script_updater_only_replaces_management_script` 在干净树上同样失败
   （后者由沙箱 Program Blacklist 拦截 `wsl.exe` 引起），与本次变更无关。

## 回滚

`git revert` 本文档所在提交与 `8903e6d`，再执行
`pwsh -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 即恢复
`stream-bootstrap-buffering: true` / `"20s"` 与旧日志格式（`pre_ttfb_log_format`
已在迁移源列表中，回滚方向的 `-Apply` 同样可用）。准入层与 nginx 其余参数未改动。
