## 问题

`writeProviderGatewayResponsesStream` 把每一行 SSE 都写进 `c.Writer`，但**从不调用 `Flush()`**。

`gin` 的 `c.Status()` 只记录状态码、不落盘；`c.Writer.Write` 落在 `net/http` 的 bufio（约 2–4 KiB），于是**攒满缓冲或 handler 返回才真正发给客户端**。结果是客户端在**整个生成期间收不到响应头与数据**，直到流结束才一次性收到整段 SSE —— 表现成「模型很慢 / token 吐不出来」，而上游其实一直是正常流式的。

同一个文件里两个同类函数**都有 `Flush()`**，只有这条 `responses` 透传漏了：

| 函数 | Flush |
|---|---|
| `writeProviderGatewayChatStream` | ✅ |
| `writeProviderGatewayTranslatedChatStream` | ✅ |
| `writeProviderGatewayResponsesStream` | **❌ 无** |

## 实测（同一本地 gateway、同一上游、同一份请求体）

| | headers | socket 读取次数 | 响应体字节 | delta 事件 |
|---|---|---|---|---|
| 修复前 | 10310–22507 ms | 8 | 10863 | 15 |
| 修复后 | **725–824 ms** | **25** | 10863 | 15 |

上游侧访问日志的 `upstream_header_time` 只有 **0.53–2.2 s**，说明缓冲完全发生在本地 gateway。修复前后**响应字节数与 SSE 事件数完全一致**（10863 B / 15 个 `response.output_text.delta`），只有投递方式不同。

旁证（与 bufio 攒满才落盘的机制一致，响应越大越早看到响应头）：3145 B → headers 11752 ms（== total）；10863 B → 10310–22507 ms；46993 B → 6985 ms。

## 改动说明

按**空行（SSE 事件边界）**flush，与 `writeProviderGatewayChatStream` 的做法一致；`bytes` 与 `net/http` 在本文件已导入，无需改 import。`itemIDRewriter` 的跨行状态在事件边界处已完整，因此在空行处 flush 是安全的。

## 备注

`wireApi: "chat_completions"` **不能**作为绕过手段 —— 实测更差：chat 格式不渲染握手事件，上游代理必须等到第一个生成 token 才提交响应头（同批请求 `upstream_header_time` 为 10.7 / 14.6 s）。`responses` 才是对的 wire API，缺的只是这个 Flush。

完整现象、三层对照与复现方式见 #2658。
