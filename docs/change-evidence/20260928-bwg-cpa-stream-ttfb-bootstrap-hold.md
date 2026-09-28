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

## 残余边界（未修，明确记录）

1. **单飞排队是新的最大项**。OAuth lane `max_inflight=1`，实测并发时单发请求首字节
   可达 36 s（`upstream_header_time` 含排队）。这是同一共享订阅账号的既有保护，
   不是本次引入。6 小时内 `lane_reject` 分解为 cooldown 17 / half_open_probe 6 /
   queue_timeout 6 / busy 4 —— `queue_timeout=6` 表示有请求等满 120 s 仍未获准。
   建议下一步：把 `max_inflight` 提到 2 并观察 24 h 的 `capacity=true` 比例是否上升；
   本次不擅自改动该安全参数。
2. **上游容量仍在**（非本仓缺陷）：`responses 200` 的整轮时长 p50 15.4 s / p90 48.8 s；
   `server_is_overloaded` 约占 3%。`server_is_overloaded` 是 OpenAI 侧全局容量信号，
   本地无法消除，只能靠准入层快速失败 + 冷却。
3. **客户端模型目录漂移**（Cockpit 侧，非本仓）：`fq.sciman.top` 的 catalog 里有
   CPA 无法服务的 `gpt-6-sol-91`（400 `model_not_found`）；`ai.input.im` 中转对
   `gpt-5.6-sol` / `gpt-6-sol-input` / `gpt-6-astra` / `deepseek-v4.1-flash` 全部返回
   502 `Upstream access forbidden`，疑为上游 key 失效。另有 233 次/16 h 的
   `route=responses` 快速 503（带 CPA `Retry-After`，2 ms 级）来自客户端非 lane 模型，
   新日志字段落地后可直接由 journal 归因。
4. 存量测试失败：`test_cpa_prune_backups_keeps_newest_backup_dirs` 与
   `test_v2ray_agent_script_updater_only_replaces_management_script` 在干净树上同样失败
   （后者由沙箱 Program Blacklist 拦截 `wsl.exe` 引起），与本次变更无关。

## 回滚

`git revert` 本文档所在提交与 `8903e6d`，再执行
`pwsh -File scripts/cpa_bwg_guardrails.ps1 -Profile bwg -Apply` 即恢复
`stream-bootstrap-buffering: true` / `"20s"` 与旧日志格式（`pre_ttfb_log_format`
已在迁移源列表中，回滚方向的 `-Apply` 同样可用）。准入层与 nginx 其余参数未改动。
