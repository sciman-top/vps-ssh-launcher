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
> 1. `D:\CODE\external\cockpit-tools` **是写保护的**（就地应用补丁报 `Permission denied`）。
> 2. **优先用官方 tag 构建「版本匹配」的 sidecar**：官方仓
>    `jlcodes99/cockpit-tools` 有 `v<应用版本>` tag（应用版本读
>    `~/.antigravity_cockpit/server.json` 的 `version`）。稀疏克隆只取 sidecar 子树，约 **30 MB**。
> 3. Go 模块代理：`GOPROXY=https://proxy.golang.org,direct` 配
>    **`https_proxy=http://127.0.0.1:10808`**（**不是** 12803，那个到外网不通）。
> 4. 用**结构式修补**（`cockpit-sidecar-flush-fix.py`）而不是行号补丁 ——
>    它按锚点定位函数体，版本变化后仍能命中，且幂等；会自动识别并保留 CRLF 行尾。

### 推荐路径：一键脚本

```bash
bash docs/runbooks/rebuild-cockpit-sidecar-patch.sh
```

它会：读已装版本 → 稀疏克隆官方仓对应 tag → 结构式打补丁 → 构建 → 打印替换步骤。
若上游已自带该修复，会直接报 `already_patched` 并退出（**升级即可，无需重打**）。

### 等价的手工步骤

```bash
VERSION=$(./.venv/Scripts/python.exe -c "import json,os;print(json.load(open(os.path.expanduser('~/.antigravity_cockpit/server.json')))['version'])")
W=/c/Users/sciman/AppData/Local/Temp/ct-$VERSION
git clone --depth 1 --branch "v$VERSION" --filter=blob:none --sparse \
  https://github.com/jlcodes99/cockpit-tools.git "$W"
(cd "$W" && git sparse-checkout set sidecars/cockpit-cliproxy)

./.venv/Scripts/python.exe docs/runbooks/cockpit-sidecar-flush-fix.py \
  "$W/sidecars/cockpit-cliproxy/provider_gateway.go"     # 期望 status=patched

cd "$W/sidecars/cockpit-cliproxy"
export https_proxy=http://127.0.0.1:10808 GOPROXY=https://proxy.golang.org,direct GOFLAGS=-mod=mod
"/c/Program Files/Go/bin/go.exe" build -trimpath -o /c/Users/sciman/AppData/Local/Temp/cockpit-cliproxy-patched.exe .
```

### 备用路径：从本地 checkout 的副本构建

外置仓写保护时用副本（注意副本里没有 `sidecars/cockpit-cliproxy/` 前缀，补丁用 `-p3`）：

```bash
WORK=/c/Users/sciman/AppData/Local/Temp/cpa-sidecar-build
SRC=/d/CODE/external/cockpit-tools/sidecars/cockpit-cliproxy
rm -rf "$WORK/src"; mkdir -p "$WORK/src"
tar -C "$SRC" --exclude=bin -cf - . | tar -C "$WORK/src" -xf -
cd "$WORK/src" && git apply -p3 /d/CODE/vps-ssh-launcher/outputs/cockpit-sidecar-sse-flush.patch
```

⚠️ 该路径产出的 sidecar 源码基线是 **v1.3.57-7**，而应用可能已是更新的版本；
**优先用上面的 tag 路径**。

### 替换前先隔离验证

用临时端口 + 线上同一份 config/manifest 跑新二进制，不动线上：

```bash
PROBE_ASSERT=1 PROBE_SIDECAR_PORT=17600 PROBE_SIDECAR_KEY=<key> \
  ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
```

### 替换

```powershell
$bin = "$env:LOCALAPPDATA\Cockpit Tools\cockpit-cliproxy.exe"
Copy-Item $bin "$bin.before-sse-flush-$(Get-Date -Format yyyyMMdd-HHmmss).bak"   # 备份
```

> ⚠️ **停止与启动 Cockpit 必须由用户手动做**（托盘 → 退出；再双击启动）。
> 2026-09-29 实测：从非交互工具会话启动该 GUI 应用时，
> 前端**从不挂载**（`[Diagnostics] 前端启动超时: timeoutMs=15000, lastStage=none`，
> 缺少成功启动时的 `[Updater] Tauri Updater + Process 插件已初始化` 与
> `前端已就绪: react_mounted` 日志），应用随后静默退出；
> 且从工具会话启动 GUI 程序会被安全策略拦（`reg.exe` 在 Program Blacklist）。
> **不要在工具会话里启动它。**

### 补丁资产存放位置

`%USERPROFILE%\.antigravity_cockpit\_codex_verify_backups\sse-flush-patch-20260929\`
—— 内含两个已构建的 sidecar（v1.3.57-7 基线与 **v1.3.62 版本匹配版**）、修补脚本、
重打脚本与 README。**不要把资产只放在 `%TEMP%`，它会被清理。**

---

## 验收（必须做）

1. **基线**（改动前，在运行中的实例上）：
   ```bash
   PROBE_SIDECAR_KEY=<从 manifest 读> PROBE_MODEL=gpt-6-luna \
   PROBE_MAX_TOKENS=96 ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar
   ```
   期望（缺陷态）：`headers_ms ≈ total_ms`、`socket_reads ≈ 8`、`read_gap_p50 = 0`。

2. **修复后**：同一命令应变为
   - `headers_ms` 明显早于 `total_ms`（`headers/total` 从缺陷态的 **0.93–0.98** 降到 **0.11–0.32**）
   - `socket_reads` 从 ~8 升到 **~25**
   - `delta_events` / `delta_chars` **不变**（内容未受影响）

   > 注意：不要用固定的 `headers_ms < 2s` 当唯一判据 ——
   > 公网直连自身首字节波动就有 **1275–2220 ms**，2s 卡在上游自身抖动之内
   > （2026-09-29 实测因此误判过一次）。

3. **可执行断言（一条命令判 PASS/FAIL）** —— 加 `PROBE_ASSERT=1` 即按阈值判定并
   用退出码表示结果（`ACCEPTANCE_RESULT=PASS|FAIL ...`）：

   ```bash
   PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> \
   ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
   ```

   阈值（可用环境变量覆盖）：
   | 变量 | 默认 | 说明 |
   |---|---|---|
   | `PROBE_MAX_HEADERS_RATIO` | **0.5** | **主判据**：`headers_ms / total_ms` 必须 ≤ 该值 |
   | `PROBE_MAX_HEADERS_MS` | **4000** | 绝对上限（放宽到高于公网自身波动） |
   | `PROBE_MIN_READS` | 20 | 逐事件投递的下限 |
   | `PROBE_MIN_GAP_P50` | **0** | 不设阈值：握手事件同毫秒到达，修复后也可能是 0 |
   | `PROBE_MIN_DELTA_EVENTS` | 1 | 内容未丢 |

   **回归验证**（2026-09-29，用 8 组实测数据跑 `evaluate()`）：
   4 个缺陷态样本（12683/13415、6985/12535、22507/23038、10310/11131，reads=8/34/8/8）
   全部 **FAIL**；4 个修复态样本（1692/7262、1690/6385、1306/12224、2032/6359，reads=25）
   全部 **PASS**。⇒ 判据干净分离，且不受上游首字节抖动影响。

4. **对照**：`outputs/sse_framing_probe.py public`（公网直连）应保持
   `headers_ms ≈ 2 s`、`socket_reads ≈ 21` —— 修复的目标就是让 sidecar 向它对齐。

5. **长会话**：由用户在实际 desktop 里确认「token 逐字流出、不再整批出现」。
   `natural_live_accepted` 只能由用户亲自确认，探针不能替代。

---

## 不改远端

远端网关本轮实测健康（CPA 直连首字节 665 ms、142 分块、gap_p50 6 ms；
nginx `proxy_buffering off`；`stream-bootstrap-buffering: false`）。
**不要为了这个症状去动远端配置** —— 缓冲发生在本地 sidecar，改远端不会有任何效果。
