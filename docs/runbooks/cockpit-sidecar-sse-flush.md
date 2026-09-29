# Runbook: 修复 Cockpit sidecar 的 SSE 缓冲（token 吐出缓慢）

> **2026-09-29 决定**：用户选择**走上游路线**（把补丁与证据提给 Cockpit Tools，
> 不在本机改源/重编）。本 runbook 保留为**上游修复落地后的验收步骤**，
> 以及用户日后改变主意时的操作依据。上游材料：
> `outputs/cockpit-tools-upstream-report-2026-09-29.md`。

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

`bytes` 与 `net/http` 已在该文件被引用，无需改 import。

**可直接应用的补丁**：`outputs/cockpit-sidecar-sse-flush.patch`
（已用 `git apply --check -p1` 对 `v1.3.57-7-gdbe56a1e` 校验，exit 0）：

```bash
git apply -p1 outputs/cockpit-sidecar-sse-flush.patch
```

> 注意：不要把这段 diff 贴进 Markdown 后交给会「去掉行首空白」的格式化器 ——
> unified diff 的上下文行必须以**一个空格**开头，被剥掉后补丁即失效
> （2026-09-29 实际发生过一次，故另存了独立 `.patch` 文件）。

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

> **2026-09-29 实测修正**（按此执行，别用旧写法）：
> 1. `D:\CODE\external\cockpit-tools` **是写保护的**（`git apply` → `Permission denied`）。
>    必须**复制副本再构建**，不要试图就地打补丁。
> 2. 副本里没有 `sidecars/cockpit-cliproxy/` 前缀，所以补丁要用 **`-p3`**。
> 3. Go 模块代理：`GOPROXY=https://proxy.golang.org,direct` 配 **`https_proxy=http://127.0.0.1:10808`**
>    （**不是** 12803，那个到外网不通）。

```bash
# 1) 复制副本（排除 bin/ 里的预编译产物）
WORK=/c/Users/sciman/AppData/Local/Temp/cpa-sidecar-build
SRC=/d/CODE/external/cockpit-tools/sidecars/cockpit-cliproxy
rm -rf "$WORK/src"; mkdir -p "$WORK/src"
tar -C "$SRC" --exclude=bin -cf - . | tar -C "$WORK/src" -xf -

# 2) 打补丁（-p3，因为副本里没有前缀目录）
cd "$WORK/src"
git apply -p3 /d/CODE/vps-ssh-launcher/outputs/cockpit-sidecar-sse-flush.patch
grep -c 'flusher, ok := c.Writer.(http.Flusher)' provider_gateway.go   # 期望 5（原 4）

# 3) 构建
export https_proxy=http://127.0.0.1:10808 http_proxy=http://127.0.0.1:10808
export GOPROXY=https://proxy.golang.org,direct GOFLAGS=-mod=mod
"/c/Program Files/Go/bin/go.exe" build -trimpath -o "$WORK/cockpit-cliproxy-patched.exe" .
```

**先隔离验证，再替换**（用临时端口 + 线上同一份 config/manifest，不动线上）：

```bash
# 期望：headers_ms < 2s、socket_reads ≈ 25、delta_events 不变
PROBE_SIDECAR_PORT=17500 PROBE_SIDECAR_KEY=<key> \
  ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar
```

替换（**备份 → 停 Cockpit → 换文件 → 启动**）：

```powershell
$bin = "$env:LOCALAPPDATA\Cockpit Tools\cockpit-cliproxy.exe"
Copy-Item $bin "$bin.before-sse-flush-$(Get-Date -Format yyyyMMdd-HHmmss).bak"   # 备份
```

> ⚠️ **停止与启动 Cockpit 必须由用户手动做**（托盘 → 退出；再双击启动）。
> 2026-09-29 实测：从非交互工具会话 `Start-Process` 启动该 GUI 应用时，
> 前端**从不挂载**（`[Diagnostics] 前端启动超时: timeoutMs=15000, lastStage=none`，
> 缺少成功启动时的 `[Updater]` / `react_mounted` 日志），应用随后静默退出；
> 且 `Start-Process` 启动 GUI 程序会被安全策略拦（`reg.exe` 在 Program Blacklist）。
> **不要在工具会话里启动它。**

**版本风险**：`D:\CODE\external\cockpit-tools` 停在 `v1.3.57-7-gdbe56a1e`
（CHANGELOG 只到 1.3.57），而应用是 **v1.3.62**。好消息：**源码里没有 sidecar 版本握手**，
且 2026-09-29 的隔离验证证明旧源码能正确处理线上 manifest；
但若日后出现异常，先回滚再排查版本差异。

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

3. **可执行断言（一条命令判 PASS/FAIL）** —— 加 `PROBE_ASSERT=1` 即按阈值判定并
   用退出码表示结果（`ACCEPTANCE_RESULT=PASS|FAIL ...`）：

   ```bash
   PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> \
   ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
   ```

   阈值可用环境变量覆盖：
   `PROBE_MAX_HEADERS_MS`（默认 2000）、`PROBE_MIN_READS`（默认 20）、
   `PROBE_MIN_GAP_P50`（默认 1）、`PROBE_MIN_DELTA_EVENTS`（默认 1）。
   修复前该命令**必须**返回非 0（缺陷态），修复后返回 0。

4. **对照**：`outputs/sse_framing_probe.py public`（公网直连）应保持
   `headers_ms ≈ 2 s`、`socket_reads ≈ 21` —— 修复的目标就是让 sidecar 向它对齐。

5. **长会话**：由用户在实际 desktop 里确认「token 逐字流出、不再整批出现」。
   `natural_live_accepted` 只能由用户亲自确认，探针不能替代。

---

## 不改远端

远端网关本轮实测健康（CPA 直连首字节 665 ms、142 分块、gap_p50 6 ms；
nginx `proxy_buffering off`；`stream-bootstrap-buffering: false`）。
**不要为了这个症状去动远端配置** —— 缓冲发生在本地 sidecar，改远端不会有任何效果。
