补充 2026-10-01 复核 —— 两条新证据，都是为了说明**修复面是唯一的、可控的**。

### 1. 同类问题不存在：另一个角色（API 服务）本来就是 flush 的

`cockpit-cliproxy.exe` 是 `CLIProxyAPI + 定制`的**单二进制**，10909（Provider Gateway）与 14185（API 服务）跑的是**同一个 exe**、只是 `--config` 不同。所以有必要排除「另一个角色也漏了 Flush」：

| 路径 | 实现位置 | Flush |
|---|---|---|
| Provider Gateway | `sidecars/cockpit-cliproxy/provider_gateway.go` | ❌（本 PR 修的） |
| API 服务 | 内置 CPA 的 `sdk/api/handlers/stream_forwarder.go` | ✅ **6 处** |

后者的 `ForwardStream()` 在 keep-alive、每个 chunk、终止事件处都有 `flusher.Flush()`；且 `internal/api/middleware/response_writer.go` 的包装器不吞 `Flush`（嵌入 `gin.ResponseWriter`，未覆盖该方法）。

⇒ **漏 Flush 只存在于 `writeProviderGatewayResponsesStream` 一处**，本 PR 的改动面即是全部。

### 2. 与「正确基线」的同形态对照（强化上一条对照评论）

同一模型（`gpt-6-luna`）、同一 prompt、同一时段：

| 路径 | headers | total | socket 读取 | read_gap_p50 | first_delta | ms_per_delta |
|---|---|---|---|---|---|---|
| 经 gateway（10909，本补丁） | 1861 ms | 14318 ms | **89** | **2 ms** | 11347 ms | 37 |
| 直连上游（8443，对照） | 1256 ms | 13723 ms | 44 | 20 ms | **11389 ms** | 29 |

响应体**字节数（27863）与 delta 事件数（79）完全一致**，`first_delta` 只差 42 ms ⇒ 打完补丁后 gateway 与直连**逐项等价**，不再引入任何额外等待。

上游访问日志侧一致：`upstream_header_time` 0.63–1.30 s，而 `upstream_time` 13.7 s —— 缓冲过去完全发生在本地 gateway。

### 3. 版本复测

v1.3.63 正式版（2026-09-30 06:11 发布）的 sidecar 仍未修；本 PR 的 diff 在 v1.3.63 源码上 `git apply` 仍干净命中（`mergeable: MERGEABLE`）。
