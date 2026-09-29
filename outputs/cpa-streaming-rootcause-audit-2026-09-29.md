# CPA 流式「慢 / token 吐出缓慢」根因审查 — 2026-09-29

链路：ChatGPT desktop → Cockpit Tools 本地 sidecar（`cockpit-cliproxy.exe`）
→ 本地 xray `127.0.0.1:10808` → 公网 `fq.sciman.top:8443`（nginx）
→ admission `8318` → CPA 容器 `8317`（`eceasy/cli-proxy-api:v8.0.2`）→ OpenAI Codex OAuth。

**结论摘要**：远端网关是健康的，本轮**不需要远端投影**。用户感知的「慢」由
**本地 Cockpit sidecar 的两个独立缺陷**造成，其中一个（Direct API 路径）
会把整个 SSE 响应攒到流末尾才交给客户端。

---

## 1. 分层实测（同一模型 `gpt-6-luna`、同一 lane、同一时段）

### 1.1 远端分层（在 VPS 上跑 `outputs/stream_hop_probe_20260929.py`）

| 目标 | headers | 总时长 | 分块数 | gap_p50 | gap_max |
|---|---|---|---|---|---|
| CPA 直连 `127.0.0.1:8317` | **665 ms** | 17665 ms | **142** | 6 ms | 10489 ms |

- `665 ms` 与第 3 轮修复后的 `653 ms` 一致 ⇒ 远端 bootstrap 扣留修复**仍然有效**。
- `gap_p50 = 6 ms` ⇒ 远端**逐事件平滑转发**，没有批处理。
- 唯一一处 `gap_max = 10.5 s` 是上游推理期静默（CPA 直连同样存在 ⇒ 不是代理扣留）。

### 1.2 本地客户端链路（`outputs/stream_local_chain_probe.py` / `sse_framing_probe.py`）

| 路径 | headers_ms | 总时长 | 字节 | socket 读次数 | gap_p50 |
|---|---|---|---|---|---|
| sidecar `127.0.0.1:10909`（Direct API） | **6985 → 22507** | 11444–23038 | 10863 | **8** | **0 ms** |
| 公网 `8443` 直连（无代理） | **1275 / 2220** | 13672 | **10863** | **21** | 12 ms |
| 公网 `8443` 经 xray `10808` | **1457** | 14749 | 36944 | 91 | 14 ms |

**字节数、delta 事件数、事件种类完全一致**（10863 / 15 deltas / 同样 8 种事件），
只有**投递方式**不同：sidecar 把整个响应攒到流末尾一次性吐出。

### 1.3 网关侧对照（`/var/log/nginx/cpa_gateway.access.log`，同批探针请求）

```
route=responses status=200 request_time=10.940 upstream_time=10.939 bytes=10863 upstream_header_time=0.561
route=responses status=200 request_time=16.338 upstream_time=16.336 bytes=46903 upstream_header_time=0.531
route=responses status=200 request_time=13.319 upstream_time=13.137 bytes=11013 upstream_header_time=1.691
route=responses status=200 request_time=22.849 upstream_time=22.846 bytes=10863 upstream_header_time=1.976
```

**网关在 0.53–2.2 s 内就发出了响应头**，而本地 sidecar 直到 10–22 s 才交给客户端。
⇒ 缓冲 100% 发生在本地 sidecar 内部，与远端、admission、xray、TLS 全部无关。

---

## 2. 根因 A（Direct API 路径，用户描述「尤其慢」的那条）

`cockpit-cliproxy` 的 provider-gateway 透传函数**逐行写但从不 Flush**：

`sidecars/cockpit-cliproxy/provider_gateway.go:636-652`
```go
func (s *relayServer) writeProviderGatewayResponsesStream(c *gin.Context, body io.Reader) {
	reader := bufio.NewReaderSize(body, 64*1024)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 {
			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
				return
			}
		}
		if err != nil { return }
	}
}
```

调用链：`provider_gateway.go:113` / `:124` → `handleProviderGatewayRequest`
→ `provider_gateway.go:401-402` `c.Status(http.StatusOK)` + 上面的函数。

`c.Status()` 在 gin 里只**记录**状态码，不落盘；`c.Writer.Write` 进 Go `net/http`
的 bufio（约 2–4 KiB），**只有攒满缓冲或 handler 返回才会真正发出**。

对照证据 —— **同一个文件里 chat 路径是有 Flush 的**：

| 函数 | 行号 | Flush |
|---|---|---|
| `writeProviderGatewayChatStream` | 501-593 | ✅ 546 / 552 / 574 |
| `writeProviderGatewayTranslatedChatStream` | 595-635 | ✅ 625 / 630 |
| **`writeProviderGatewayResponsesStream`** | **636-652** | **❌ 无** |

而 `fq.sciman.top` 这个 provider 的 `wireApi` 正是 `responses`
（`codex_model_providers.json` / provider-gateway `manifest.json`），
所以**只有这条路径**吃到了这个缺陷 —— 与用户「尤其是调用 CPA 的 API key 时」完全吻合。

**为什么表现为「token 吐出缓慢」**：短回复（< 缓冲）整个生成期间客户端一个字节都收不到，
最后一刻全部出现；长回复按缓冲大小分批爆出。实测 `headers_ms ≈ total_ms` 就是它的签名。

### 2.1 绕行方案 `wireApi: chat_completions` 已被证伪

`chat` 路径有 Flush，但**网关侧首字节反而更差**：

```
route=chat status=200 request_time=11.716 upstream_time=11.549 bytes=4818 upstream_header_time=10.685
route=chat status=200 request_time=16.320 upstream_time=16.319 bytes=4818 upstream_header_time=14.613
```

原因与官方文档一致：Chat Completions 格式「不渲染握手事件」，CPA 必须等到**第一个生成 token**
才提交下游响应头（`config.example.yaml` 对该行为的原文说明）。
隔离实例复测同样 `headers_ms ≈ total_ms`。

⇒ **`responses` 才是正确的 wire API，必须修 sidecar 的 Flush，不能靠换协议绕。**

---

## 3. 根因 B（Direct OAuth 路径）

`codex_local_access_sidecar/config.json` 里：

```json
"codex": { "optimize-multi-agent-v2": true, "stream-bootstrap-buffering": true }
```

而远端 CPA 上这个开关**早已改成 `false`**（第 3 轮修复），本地 sidecar 没有同步。
`stream-bootstrap-buffering: true` 会把 SSE 握手扣到「上游产出第一个生成 token」才提交响应头
⇒ 每轮首字节 = 上游推理耗时（≈ +10 s）。

**来源已核实为 Cockpit 自身生成，不是手改**：`src-tauri/src/modules/codex_local_access_sidecar_config.rs:2419`
```rust
config.insert("codex".to_string(), json!({
    "optimize-multi-agent-v2": true,
    "stream-bootstrap-buffering": api_service,   // 本地访问 sidecar 传入 true
}));
```
（`codex_local_access_gateway_runtime.rs` 传 `api_service = true`；
`codex_local_access_provider_gateway.rs` 传 `false`。）
Sep 27 的历史备份里同样是 `true` ⇒ 每次 Cockpit 启动都会重新生成，改配置文件会被覆盖。

**当前状态**：`codex_local_access.json` 为 `enabled: false`、`accountIds: []`、
两个 sidecar 的 `auths/` 目录均为空 ⇒ **Direct OAuth 当前未启用**，
只有 Direct API（10909）在跑。

---

## 4. 上游侧（本地无法消除，仅记录）

### 4.1 单一 OAuth 凭据 + 双重冷却

- `auth-dir` 下**只有一个** Codex 凭据：`codex-1c3cf6c3-…-plus.json`；`max-retry-credentials: 1`。
- `transient-error-cooldown-seconds: 60`（管理接口回读生效值 = 60）。
- 上游 `server_is_overloaded`（`x-retry-metadata: NO_MORE_RETRY`）**每次要 10–32 s 才浮现**
  （CPA `conductor_execution.go:1965` 实测 9.7 / 10.3 / 10.7 / 12.1 / 20.3 / 20.7 / 21.3 / 31.7 s）。
- 一次过载同时触发**两层冷却**：CPA 凭据冷却 60 s + admission lane 熔断
  （schedule `[60,120,240,480,900]`，阈值 2，`early_probe_interval_seconds=10`）。

24 h 实测：
| 指标 | 值 |
|---|---|
| `upstream_result` 总计 | 2043 |
| OAuth lane 结果 | 200×**1104** / 503×52 / 502×25（成功率 93.5 %） |
| `lane_reject` | **151**（cooldown 93 / half_open_probe 46 / queue_timeout 8 / downstream_gone 2 / busy 2） |
| `capacity=true` | **106**（其中 200 流内 29 / 502 25 / 503 52） |
| `waited_ms` | 0 ms×1544，>1 s×60，>15 s×13，>40 s×6 |
| journal traceback | 0（4 处均早于 09-28 16:36:56 重启） |

⇒ 约每 14 分钟一次上游容量事件，每次造成 10–32 s 停顿 + 最长 60 s 的 lane 拒绝。
**这是上游全局容量，不是本地配置错误**；把 60 s 冷却调短只会把「快速失败」换成「再次停顿」。

### 4.2 桌面真实流量（`codex_local_access_logs.sqlite`，`request_logs` 30 万行）

- 近 72 h `gpt-6-luna`：1558 次，成功 1419，**p50 18.3 s / p90 61.5 s / p99 184 s / max 350.7 s**。
- `gpt-6-sol` p50 20.4 s / p90 49.9 s；`gpt-5.6-terra` p50 11.6 s / p90 29.7 s。
- 今日 366 请求，**最大并发 3**（1→324 样本、2→55、3→36）⇒ 确实会碰到 `max_inflight=2` 的排队。
- `deepseek-v4.1-flash`：243 次仅 14 成功（5.8 %），227 次 503 `upstream_error`（平均 576 ms）。

### 4.3 宣称但不可用的路由

`oauth-excluded-models.codex` 把 `gpt-5.6-sol`、`gpt-6-astra`、`gpt-5.4*`、`gpt-4*`、
`codex-*`、`gpt-6-sol-cii`、`gpt-6-sol-91` 等排除在 OAuth 之外，而唯一宣称
`gpt-5.6-sol` / `gpt-6-astra` / `deepseek-v4.1-flash` 的 `ai.input.im` 上游已失效
（`Upstream access forbidden`），`codex.ciii.club` 同样全 502。
⇒ 这些名字**必然失败**，桌面选中即得到快速 503/502。72 h 内 `gpt-5.6-sol` 0/14 成功。

---

## 5. 远端配置复核（**不需要改动**）

| 项 | 状态 |
|---|---|
| `codex.stream-bootstrap-buffering` | `false` ✅（管理接口回读一致） |
| `codex.stream-bootstrap-timeout` | `"0"` ✅ |
| nginx `proxy_buffering off` / `proxy_http_version 1.1` / `Connection ""` | ✅ |
| `proxy_read_timeout` / `proxy_send_timeout` | 300 s ✅ |
| nginx `auth_request` 到 CPA `/v1/models` | 1–3 ms ✅ 非瓶颈 |
| admission lane `max_inflight` | chatgpt-oauth **2**、zhipu 3、deepseek 3 ✅ |
| `request-retry` / `max-retry-credentials` / `transient-error-cooldown-seconds` | 生效值 0 / 1 / 60 ✅ 与文件一致 |

> 排查中曾怀疑 v8.0.2 把 `retry:`/`cooldown:` 移入 `routing:` 导致顶层键失效 ——
> **已用管理接口 `/v0/management/config` 回读生效值证伪**：顶层键在 v8.0.2 仍然生效。

---

## 6. 修复建议（按收益排序）

1. **【根因 A，必做】给 `writeProviderGatewayResponsesStream` 加逐事件 Flush。**
   最小改动与验收步骤见 `docs/runbooks/cockpit-sidecar-sse-flush.md`。
   需要 `go build` 重编 `cockpit-cliproxy.exe`（本机有 Go 1.26.3）。
   验证方法：`outputs/sse_framing_probe.py` 的 `socket_reads` 应从 8 升到 ~100，
   `headers_ms` 应从 ≈`total_ms` 降到 < 2 s。
2. **【根因 B】让本地访问 sidecar 也用 `stream-bootstrap-buffering: false`。**
   Cockpit 每次启动重新生成，需改 Rust 生成器（同 patch 文件）或在 Cockpit 侧提供开关。
3. **【运维】停止对外宣称不可用的路由**（`gpt-5.6-sol` / `gpt-6-astra` / `deepseek-v4.1-flash`）。
   属路由清单变更（用户决策项），本轮未动。
4. **【上游】增加第二个 Codex OAuth 凭据**是唯一能真正消除 10–32 s 停顿 + 冷却窗口的办法。
5. **【参考卫生】`references.manifest.json` 里 CLIProxyAPI 仍锚定 v7.3.17，
   线上镜像是 v8.0.2** —— 参考已滞后两个 minor，建议重新固定版本。

---

## 7. 验收分级

| 层级 | 判定 | 依据 |
|---|---|---|
| repo_verified | **PASS** | 源码逐行定位（provider_gateway.go:636-652 / 501-593）+ 本报告全部读数 |
| filesystem_projected | **N/A** | 本轮无远端改动（远端已合规，无需投影） |
| host_loaded | **PASS** | 远端 CPA `v8.0.2`、`stream-bootstrap-buffering=false`、healthz lane 值 |
| controlled_live_replay | **PASS** | 分层三跳 + 隔离实例 A/B + nginx 侧时序对照，全部消费真实 OAuth turn |
| natural_live_accepted | **NOT CLAIMED** | 需用户实际 desktop 长会话确认 |

---

## 8. 复现用脚本

- `outputs/stream_local_chain_probe.py` — 本地分层（sidecar / 公网直连 / 经 xray）
- `outputs/sse_framing_probe.py` — 原始 SSE 帧定性（读次数、gap、delta 计数、首尾帧）
- `outputs/stream_hop_probe_20260929.py` — 远端分层（CPA / admission / 公网，需在 VPS 上跑）

全部只读、不打印凭据。
