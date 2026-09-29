## 现象

用 **Provider Gateway**（provider 的 `wireApi` 为 `responses`）调上游时，客户端在**整个生成期间收不到任何响应头与数据**，直到流结束才一次性收到整段 SSE。表现为「模型很慢 / token 吐不出来」，但上游其实一直是正常流式的。

同一上游、同一模型、同一份请求体，三种路径对比（各跑多次，取代表性样本）：

| 路径 | headers_ms | 总时长 | socket 读取次数 | 分块间隔 p50 | 响应体字节 | delta 事件 |
|---|---|---|---|---|---|---|
| 上游直连（绕过 Cockpit） | 1275–2220 | 13672 | **21–99** | 8–12 ms | 10863 | 15 |
| 经 Cockpit 本地 gateway | **10310–22507** | 11131–23038 | **8** | **0 ms** | 10863 | 15 |

响应体**字节数与 SSE 事件数完全一致**，只有投递方式不同：gateway 把整段响应攒到流末尾才吐出来。

上游侧访问日志（nginx `upstream_header_time`）显示**上游在 0.53–2.2 s 就已发出响应头**，所以缓冲完全发生在本地 gateway。

## 根因

`sidecars/cockpit-cliproxy/provider_gateway.go`（对照 `v1.3.57-7-gdbe56a1e`）的 `writeProviderGatewayResponsesStream`（约 636–652 行）**逐行 `Write` 但从不 `Flush()`**：

```go
func (s *relayServer) writeProviderGatewayResponsesStream(c *gin.Context, body io.Reader) {
	if body == nil {
		return
	}
	reader := bufio.NewReaderSize(body, 64*1024)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 {
			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}
```

`gin` 的 `c.Status()` 只记录状态码、不落盘；`c.Writer.Write` 落在 `net/http` 的 bufio（约 2–4 KiB），**攒满缓冲或 handler 返回才真正发给客户端**。

同一个文件里两个同类函数**都有 Flush**，只有这条 `responses` 透传没有：

| 函数 | 行 | Flush |
|---|---|---|
| `writeProviderGatewayChatStream` | 501–593 | ✅ 546 / 552 / 574 |
| `writeProviderGatewayTranslatedChatStream` | 595–635 | ✅ 625 / 630 |
| `writeProviderGatewayResponsesStream` | **636–652** | **❌ 无** |

调用链：`provider_gateway.go:113` / `:124` → `handleProviderGatewayRequest` → `:401-402`（`c.Status(http.StatusOK)` + 上述函数）。

**旁证（与 bufio 攒满才落盘的机制一致，响应越大越早看到响应头）**：

| 响应字节 | headers_ms | 总时长 | 读取次数 |
|---|---|---|---|
| 3145 | 11752（== total） | 11752 | 2 |
| 10863 | 10310–22507 | 11131–23038 | 8 |
| 46993 | 6985 | 12535 | 34 |

## 建议的修复

```diff
--- a/sidecars/cockpit-cliproxy/provider_gateway.go
+++ b/sidecars/cockpit-cliproxy/provider_gateway.go
@@ -637,6 +637,11 @@
 	if body == nil {
 		return
 	}
+	flusher, ok := c.Writer.(http.Flusher)
+	if !ok {
+		writeAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")
+		return
+	}
 	reader := bufio.NewReaderSize(body, 64*1024)
 	for {
 		line, err := reader.ReadBytes('\n')
@@ -644,8 +649,14 @@
 			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
 				return
 			}
+			// SSE events are terminated by a blank line; flush there so the client
+			// sees each event as it arrives instead of one bufio burst at the end.
+			if len(bytes.TrimRight(line, "\r\n")) == 0 {
+				flusher.Flush()
+			}
 		}
 		if err != nil {
+			flusher.Flush()
 			return
 		}
 	}
```

`bytes` 与 `net/http` 在该文件已导入，无需改 import。按空行（SSE 事件边界）flush，与 `writeProviderGatewayChatStream` 的做法一致。

## 修复后实测

把上面的补丁编译进 sidecar，在**同一条本地 gateway（10909）**上连续 3 次：

| headers_ms | 总时长 | headers/total | socket 读取次数 | delta 事件 |
|---|---|---|---|---|
| 755 | 3861 | 0.20 | 25 | 15 |
| 725 | 5867 | 0.12 | 25 | 15 |
| 824 | 9023 | 0.09 | 25 | 15 |

=> **首字节 12.7 s → 0.73–0.82 s**，投递从 8 次批量读变成 25 次逐事件读，响应内容与事件数不变。

## 为什么没有用 `wireApi: chat_completions` 绕过

实测**更差**：chat 路径虽然有 Flush，但 chat 格式不渲染握手事件，上游代理必须等到第一个生成 token 才提交响应头 —— 同一批请求的 `upstream_header_time` 是 **10.685 / 14.613 s**。所以 `responses` 才是对的 wire API，缺的只是这个 Flush。

## 附带的一个疑问（非本 bug）

`src-tauri/src/modules/codex_local_access_sidecar_config.rs:2419`：

```rust
config.insert("codex".to_string(), json!({
    "optimize-multi-agent-v2": true,
    "stream-bootstrap-buffering": api_service,
}));
```

`codex_local_access_gateway_runtime.rs` 传 `api_service = true`，于是**本地访问（Direct OAuth）sidecar 固定带 `stream-bootstrap-buffering: true`**，每轮响应头被推迟到上游产出第一个生成 token（实测约 +10 s）。而该开关的收益是把流内的过载错误变成真 503 以便换凭据重试 —— 单凭据池下没有可换的对象。手改 `codex_local_access_sidecar/config.json` 会被每次启动覆盖。

想确认这是有意的吗？如果是，有没有官方支持的关闭方式？

## 环境

- Cockpit Tools **v1.3.62**（Windows 11）
- sidecar 源码对照 **v1.3.57-7-gdbe56a1e**
- 上游：CLIProxyAPI 兼容网关，走 `/v1/responses`，provider `wireApi = "responses"`

复现/测量用的是标准库写的 SSE 探针（统计 headers 到达时间、socket 读取次数、分块间隔与 `response.output_text.delta` 计数），不含任何凭据；需要的话我可以一并贴出来。
