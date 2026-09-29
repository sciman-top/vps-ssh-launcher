# Runbook: 修复 Cockpit sidecar 的 SSE 缓冲（token 吐出缓慢）

**适用症状**：ChatGPT desktop 经 Cockpit Tools 走 `fq.sciman.top:8443`（CPA）时，
响应头迟迟不来、token 像整批爆出而不是逐字流出。
**根因报告**：`outputs/cpa-streaming-rootcause-audit-2026-09-29.md`。

**边界**：`D:\CODE\external\cockpit-tools` 是外置源码 checkout，按 `AGENTS.md` 的
`reference_only` 约定，**改源/构建前需要用户对精确根目录、动作与 stop 的明确授权**。
本 runbook 只描述动作，不代为执行。

---

## 修复 1（必做）：`writeProviderGatewayResponsesStream` 缺少 Flush

文件：`sidecars/cockpit-cliproxy/provider_gateway.go`（约 636–652 行）

### 现状

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

`c.Status()` 在 gin 里只记录状态码；`c.Writer.Write` 落在 Go `net/http` 的
bufio（约 2–4 KiB）里，**攒满或 handler 返回才真正发给客户端**。
同文件的 `writeProviderGatewayChatStream`（501–593）与
`writeProviderGatewayTranslatedChatStream`（595–635）都有 `flusher.Flush()`，
只有这个 `responses` 透传函数没有。

### 改为

```go
func (s *relayServer) writeProviderGatewayResponsesStream(c *gin.Context, body io.Reader) {
	if body == nil {
		return
	}
	flusher, ok := c.Writer.(http.Flusher)
	if !ok {
		writeAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")
		return
	}
	reader := bufio.NewReaderSize(body, 64*1024)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 {
			if _, writeErr := c.Writer.Write(normalizeResponsesReasoningContentSSELine(line)); writeErr != nil {
				return
			}
			// SSE 事件以空行结束；在此 flush，客户端才能逐事件收到，
			// 而不是被 net/http 的 bufio 攒成整批。
			if len(bytes.TrimRight(line, "\r\n")) == 0 {
				flusher.Flush()
			}
		}
		if err != nil {
			flusher.Flush()
			return
		}
	}
}
```

`bytes` 与 `net/http` 已在该文件被引用；若 `net/http` 未显式导入需补上。

---

## 修复 2：本地访问 sidecar 的 `stream-bootstrap-buffering`

文件：`src-tauri/src/modules/codex_local_access_sidecar_config.rs`（约 2415–2421 行）

```rust
    config.insert(
        "codex".to_string(),
        json!({
            "optimize-multi-agent-v2": true,
            "stream-bootstrap-buffering": api_service,   // ← 本地访问 sidecar 传入 true
        }),
    );
```

改为固定 `false`：

```rust
            "stream-bootstrap-buffering": false,
```

**同步更新测试** `src-tauri/src/modules/codex_local_access_tests_takeover.rs:226`
（当前断言 `json!(api_service)`）。

理由：该开关的收益是「把藏在 HTTP 200 流内的上游过载变成真 503 以便换凭据重试」。
本机只有一个 Codex 凭据（`max-retry-credentials: 1`），**无对象可换**；
代价却是每轮首字节被推迟到第一个生成 token（≈ +10 s）。
远端 CPA 上这个开关早已是 `false`，本地应与之一致。

---

## 构建与安装

```powershell
# 1) 备份当前二进制
$bin = "$env:LOCALAPPDATA\Cockpit Tools\cockpit-cliproxy.exe"
Copy-Item $bin "$bin.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"

# 2) 在 sidecar 模块目录构建
Push-Location D:\CODE\external\cockpit-tools\sidecars\cockpit-cliproxy
go build -trimpath -ldflags "-s -w" -o .\bin\cockpit-cliproxy-patched.exe .
Pop-Location

# 3) 停掉 Cockpit（会同时停 10909 / 7373 两个 sidecar），替换二进制，再启动
#    注意：Cockpit 自身升级会覆盖这个文件，升级后需重做
```

**版本风险**：`D:\CODE\external\cockpit-tools` 停在 `v1.3.57-7-gdbe56a1e`，
而本机安装的 `cockpit-cliproxy.exe`（44 MB，2026-09-29 19:37）比仓库 `bin/`
里的（20 MB，2026-08-17）更新。**直接重编可能带来版本回退**；
更稳的做法是把这两个改动提给 Cockpit Tools 上游，等官方版本。

---

## 验收（必须做）

1. **基线**（改动前，在运行中的实例上）：
   ```bash
   PROBE_SIDECAR_KEY=<从 manifest 读> PROBE_MODEL=gpt-6-luna \
   PROBE_MAX_TOKENS=96 ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar
   ```
   期望（缺陷态）：`headers_ms ≈ total_ms`、`socket_reads ≈ 8`、`read_gap_p50 = 0`。

2. **修复后**：同一命令应变为
   - `headers_ms` **< 2 s**（对齐网关侧 `upstream_header_time` 的 0.5–2.2 s）
   - `socket_reads` 升到 **~50–150**（逐事件）
   - `read_gap_p50 > 0`
   - `delta_events` / `delta_chars` **不变**（内容未受影响）

3. **对照**：`outputs/sse_framing_probe.py public`（公网直连）应保持
   `headers_ms ≈ 2 s`、`socket_reads ≈ 21` —— 修复的目标就是让 sidecar 向它对齐。

4. **长会话**：由用户在实际 desktop 里确认「token 逐字流出、不再整批出现」。
   `natural_live_accepted` 只能由用户亲自确认，探针不能替代。

---

## 不改远端

远端网关本轮实测健康（CPA 直连首字节 665 ms、142 分块、gap_p50 6 ms；
nginx `proxy_buffering off`；`stream-bootstrap-buffering: false`）。
**不要为了这个症状去动远端配置** —— 缓冲发生在本地 sidecar，改远端不会有任何效果。
