# Runbook: 修复 Cockpit sidecar 的 SSE 缓冲（token 吐出缓慢）

> 当前 v1.3.63 的 Direct API 已采用合并后的生成器 + sidecar 源补丁；版本、哈希、
> 重建和回滚入口见 [`cockpit-tools-persistent-fix-v1.3.63.md`](cockpit-tools-persistent-fix-v1.3.63.md)。
> 本文保留通用 SSE 根因与旧版本结构式修补说明。

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

### ⚠️ 2026-09-30 更正：改 `config.json` **不持久**，改 Rust 源码也不在最小改动面上

`prepare_sidecar_launch_config()`（`src-tauri/src/modules/codex_local_access_sidecar_config.rs:2046`）
在**每次 API 服务实例启动/配置变化**时重新生成
`~/.antigravity_cockpit/codex_local_access_sidecar/config.json`
（调用点 `codex_local_access_gateway_runtime.rs:40`）。所以手改该文件会被下一次启动覆盖。

**已落地的做法**：改 **sidecar 二进制的入口**，而不是配置文件、也不是 Tauri 主程序。
`cockpit-cliproxy.exe` 是 `CLIProxyAPI v7.2.155 + Cockpit 定制`的**单二进制**
（`sidecars/cockpit-cliproxy/go.mod` 的 `replace … => ./third_party/CLIProxyAPI`），
10909（Provider Gateway）与 14185（API 服务）跑的是同一个 exe、不同 `--config`。
在 `main.go` 的 `config.LoadConfig()` 之后加一行即可覆盖所有实例：

```go
cfg.Codex.StreamBootstrapBuffering = false
```

对 Provider Gateway 无影响 —— 它本来就配置为 `false`，且其请求走 `provider_gateway.go`
透传，根本不读这个键。

**产物**：`~/.antigravity_cockpit/_codex_verify_backups/sse-flush-bootstrap-off-20260930/`
（新 exe + `main.go.bootstrap-off.patch` + `sidecar-full-v1.3.63.patch`）。

**已记录的二进制哈希（每次升级后追加一行）**：

| Cockpit 版本 | 官方原版 | SSE flush 补丁 | **SSE flush + bootstrap-off** |
|---|---|---|---|
| v1.3.63 | `abdb8f0c8a3752d823dea917df41f195762c51aba2f5a7634f5ff8ae5e783990` | `d1decd980bab3260d07aa348edbbaba03d8a24f95b1f9af26292093c2de825bc` | `f06bb374b2d1af8b65d846c8f598331a91a720a71a564fc3c9ef51fcd858e0dc` |

**隔离验证**（不动线上、不刷新任何凭据）：

```bash
python outputs/sidecar_bin_verify.py <新exe> <config副本> <manifest副本> 17999
```

**替换（2026-09-30 实测的更优做法）**：直接 `cp` 会被运行中的 sidecar 锁住
（`Device or resource busy`）。**不必先退出 Cockpit** —— Windows 允许**重命名**正在运行的 exe：

```python
os.rename(target, target + ".replaced-<ts>.bak")   # 允许，不会失败
shutil.copy2(new_exe, target)                      # 写新文件，与旧映像不冲突
```

失败要回滚（`os.rename` 回来），否则 target 会缺失、sidecar 下次启动失败。

替换后**磁盘上已是新二进制，但运行中的进程仍用旧映像** ⇒ **需要一次 sidecar 重启才生效**。
触发方式：Cockpit 里切一次账号（日志 `[Codex Switch][Backend] restart specified app stage finished`），
或直接重启 Cockpit Tools。验证只看 sha256，不要看进程是否还在跑。

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
   - **`delta_span_ms` 铺满整条流**（主判据，见下）
   - `socket_reads` 从 ~8 升到 **~25 起**（长输出可达 90）
   - `headers_ms` 从 10–22 s 降到 **~1 s**
   - `delta_events` / `delta_chars` **不变**（内容未受影响）

   > ⚠️ **不要用「`headers_ms` < 2 秒」或「`headers/total`」当主判据** ——
   > 响应很短很快时它们会天然接近上限/1，**流式完全正常也会报 FAIL**
   > （2026-09-29 实测踩过：一次 `total` 只有 1.5 s 就误判了）。这两个数只作参考。

3. **决定性判据：比对已装二进制的 sha256**（推荐用这个判断补丁是否还在）

   ```bash
   sha256sum "$LOCALAPPDATA/Cockpit Tools/cockpit-cliproxy.exe"
   ```

   已记录的各版本产物哈希（**每次升级后追加一行**）：

   | Cockpit 版本 | 官方原版（未打补丁） | 补丁产物（应等于已装） |
   |---|---|---|
   | v1.3.62 | — | `a44e922b240d8aef…` |
   | v1.3.63 | `abdb8f0c8a3752d823dea917df41f195762c51aba2f5a7634f5ff8ae5e783990` | `d1decd980bab3260d07aa348edbbaba03d8a24f95b1f9af26292093c2de825bc` |

   相等 ⇒ 补丁在；变成别的值 ⇒ 被 Cockpit 更新覆盖，需重打。**这个判据零抖动。**

   > ⚠️ **两个不可用的替代判据（2026-09-30 实测，都试过）**：
   > - **别搜补丁字面量**：`streaming_not_supported` / `streaming not supported`
   >   在**官方原版**里就已存在（上游别处也用），正负对照全部命中，判别力为零。
   > - **别用文件大小**：官方 v1.3.63 为 44 MB、补丁产物 56 MB，但同版本官方构建
   >   本身就在 44–56 MB 间波动（v1.3.62 官方是 56 MB），大小不可靠。

### 升级后 60 秒核查（Cockpit 每次升级都会换掉 sidecar）

```bash
VER=$(python -c "import json,os;print(json.load(open(os.path.expanduser('~/.antigravity_cockpit/server.json')))['version'])")
echo "版本=$VER"
sha256sum "$LOCALAPPDATA/Cockpit Tools/cockpit-cliproxy.exe"   # 与上表比对
netstat -ano | grep LISTENING | grep :10909                    # sidecar 是否在听
ls -t "$LOCALAPPDATA/Temp"/cockpit-cliproxy-*.exe 2>/dev/null  # 是否有新构建产物
```

- 哈希在上表里 ⇒ 无需动作。
- 哈希是新值 ⇒ 先看 `%TEMP%\cockpit-cliproxy-repatched.exe` 是否存在且**哈希与已装一致**
  （说明某次重打已生效）；否则按第 1–3 步用**新版本源码**重打（**不要**回灌旧补丁二进制，那是降级）。
- 取 sidecar 的客户端 key 的位置：`~/.antigravity_cockpit/codex_provider_gateway_sidecars/<hash>/manifest.json`
  的 `apiKeys[0].key`（**不是** `codex_model_providers.json`，后者是桌面目录）。

4. **行为粗筛（`PROBE_ASSERT=1`）—— 只用于抓粗大故障，PASS 是必要不充分条件**

   ```bash
   PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> PROBE_MAX_TOKENS=512 \
   PROBE_PROMPT="Write the numbers from 1 to 30, one per line, nothing else." \
   ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
   ```

   判据只有三条：`headers_ms ≤ 8000`、`socket_reads ≥ 10`、`delta_events ≥ 1`。
   能抓住「整段被憋到流末尾」（headers 10–22 s、读取次数个位数）。

   > ⚠️ **别把任何单一阈值当决定性判据** —— 2026-09-29 实测两种写法都产生过假阴性/假阳性：
   > - `headers_ms < 2s`：上游首字节自身波动 **0.7–5.5 s**；
   > - `headers/total ≤ 0.5`：响应很短很快时天然接近 1（一次 `total` 仅 1.5 s 就误判）；
   > - `delta_span/total ≥ 0.3`：上游长时间推理后才快速吐字时跨度天然很小（一次 8.7 s 里
   >   delta 只占 1.19 s，流式其实完全正常）。
   >
   > 缺陷态与修复态的指标分布**有重叠**（缺陷态 headers 6985–22507 ms；修复态 755–5538 ms），
   > 所以**没有**可靠的单一阈值。请以上面的 **sha256 比对**为准。

5. **token 节奏（「吐字快不快」的直接度量）** —— 用长输出跑一次，看探针输出的
   `ms_per_delta`（每个 delta 的平均间隔）与 `first_delta_ms`（首个可见 token 的等待）：

   | 路径 | first_delta_ms | delta_span_ms | **ms_per_delta** |
   |---|---|---|---|
   | 经 sidecar 10909（已修复） | 34626 | 2351 | **30** |
   | 公网直连（对照） | 11061 | 2197 | **28** |

   ⇒ 修复后 **sidecar 的吐字节奏与直连基本一致（30 vs 28 ms/token，约 7% 开销）**。
   而 `first_delta_ms` 的差异（34.6 s vs 11.1 s）来自**上游**（推理/容量波动），不是 sidecar。

4. **对照**：`outputs/sse_framing_probe.py public`（公网直连）应保持
   `headers_ms ≈ 2 s`、`socket_reads ≈ 21` —— 修复的目标就是让 sidecar 向它对齐。

5. **长会话**：由用户在实际 desktop 里确认「token 逐字流出、不再整批出现」。
   `natural_live_accepted` 只能由用户亲自确认，探针不能替代。

---

## 不改远端

远端网关本轮实测健康（CPA 直连首字节 665 ms、142 分块、gap_p50 6 ms；
nginx `proxy_buffering off`；`stream-bootstrap-buffering: false`）。
**不要为了这个症状去动远端配置** —— 缓冲发生在本地 sidecar，改远端不会有任何效果。
