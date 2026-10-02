## 已在 v1.3.65 修复，建议关闭

在 v1.3.65（tag `0b6514b`）的 `sidecars/cockpit-cliproxy/provider_gateway.go` 里，
`writeProviderGatewayResponsesStream` 现在已经按**完整 SSE 事件**flush，不再是
"只 Write 不 Flush"：

```go
line = normalizeResponsesReasoningContentSSELine(line)
line = restoreProviderGatewayMultiAgentV2SSELine(line, multiAgentV2Optimized)
line = itemIDRewriter.RewriteSSEFrame(line)
if _, writeErr := c.Writer.Write(line); writeErr != nil {
    return
}
// Deliver each complete SSE event while the upstream stream is still open.
if len(bytes.TrimRight(line, "\r\n")) == 0 {
    c.Writer.Flush()
}
```

函数末尾的收尾分支也补了 flush：

```go
if err != nil {
    c.Writer.Flush()
    return
}
```

即本 issue 描述的根因（该 `responses` 透传路径缺少 flush，导致整段 SSE 被
`net/http` 的 bufio 攒到 handler 返回才落盘）在 v1.3.65 已不复存在，其余两条同类
函数本来就有 flush，现在三条一致。

本机在 v1.3.65 上未再复现"首字节等于总时长"的整段缓冲现象，故建议关闭。
如维护者希望补一个回归测试，可用 issue 里的原始判据（同请求体下 socket 读取次数
应远大于 8、分块间隔不应为 0 ms）。
