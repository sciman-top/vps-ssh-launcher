# 在另一台电脑上复现该补丁 —— 提示词（整段复制给那边的 AI）

> 上游 issue：https://github.com/jlcodes99/cockpit-tools/issues/2658
> 上游 PR（含精确 diff）：https://github.com/jlcodes99/cockpit-tools/pull/2659
> 下面这段提示词是**自包含**的：即使那台电脑访问不了 GitHub，也能照做。

## 最省事的用法（推荐）

那台电脑上只需要一条命令就能拿到现成脚本（本仓库是公开仓，匿名可读）：

```bash
curl -fsSL https://raw.githubusercontent.com/sciman-top/vps-ssh-launcher/main/docs/runbooks/portable/patch-cockpit-sidecar.sh \
  -o /tmp/patch-cockpit-sidecar.sh && bash /tmp/patch-cockpit-sidecar.sh
```

脚本会自己：定位 Cockpit → 读已装版本 → 稀疏克隆官方对应 tag → 结构式打补丁 → 构建 →
打印「替换 + 验收」步骤。**替换与启动仍需人工做**（它会提示）。

访问 GitHub 需要代理时加 `--proxy http://<你的代理>`；Cockpit 目录不在默认位置时加
`--cockpit-dir <目录>`。依赖：`git`、`go`、`python3`。

**已验证**：该脚本在本机对 `v1.3.62` 跑通，产出的二进制 sha256 与手工构建**逐位一致**
（`a44e922b240d8aef`）。

若那条 `curl` 走不通（网络/代理），再用下面的**提示词**让那边的 AI 手工完成。

---

## 提示词（从下一行开始整段复制）

我要修一个本地 Cockpit Tools 的 bug，请你在本机执行。**这不是配置问题，是 sidecar 二进制的缺陷，需要重新构建一个打过补丁的 sidecar 并替换。**

### 背景（已在上游定案）

Cockpit Tools 的 sidecar（`cockpit-cliproxy`）在处理 provider 的 `wireApi = "responses"` 流式透传时，**只往 `c.Writer` 写、从不调用 `Flush()`**。`gin` 的 `c.Status()` 只记录状态码不落盘，`c.Writer.Write` 落在 `net/http` 的 bufio（约 2–4 KiB），于是**攒满缓冲或 handler 返回才真正发给客户端**。

后果：客户端在**整个生成期间收不到响应头与数据**，直到流结束才一次性收到整段 SSE —— 看起来像「模型很慢 / token 吐不出来」，而上游其实一直是正常流式的。

同文件的 `writeProviderGatewayChatStream` 与 `writeProviderGatewayTranslatedChatStream` **都有 `Flush()`**，只有这条 `responses` 透传漏了。上游 issue #2658 / PR #2659 已确认此修复；官方 `v1.3.62` 与 `main` 的这个文件逐字节相同。

### 请你做的事

**第 0 步：先确认本机确实有这个 bug（不要跳过）**

1. 找到 Cockpit 的安装目录。Windows 通常是
   `%LOCALAPPDATA%\Cockpit Tools\`，里面应有 `cockpit-tools.exe` 与 `cockpit-cliproxy.exe`；
   macOS/Linux 请按运行中的 `cockpit-cliproxy` 进程的可执行文件路径定位。
2. 读 `~/.antigravity_cockpit/server.json` 的 `version` 字段 —— 这是本机 Cockpit 版本，记为 `<VER>`。
3. 读 `~/.antigravity_cockpit/codex_provider_gateway_sidecars/*/manifest.json`，
   取 `apiKeys[0].key`（记为 `<KEY>`）；目录名是哈希，取存在 `providerGateway` 字段的那个。
   注意：**这个 key 不要打印到日志或提交到任何地方**。
4. 向本机 sidecar 发一个流式请求并统计「响应头到达时间 / socket 读取次数」：
   `POST http://127.0.0.1:<sidecar端口，通常 10909>/v1/responses`，
   `Authorization: Bearer <KEY>`，body 形如
   `{"model":"<任一可用模型>","input":"List 8 colors, one per line.","stream":true,"max_output_tokens":96}`。
   **判据：如果 `headers_ms ≈ total_ms` 且 socket 读取次数只有个位数 ⇒ bug 存在。**
   （正常流式应是 `headers_ms` 明显小于 `total_ms`、读取次数 ~25。）

**第 1 步：取与本机版本匹配的 sidecar 源码**

```bash
VER=<VER>                     # 例如 1.3.62
W=$(mktemp -d)/ct-$VER
git clone --depth 1 --branch "v$VER" --filter=blob:none --sparse \
  https://github.com/jlcodes99/cockpit-tools.git "$W"
(cd "$W" && git sparse-checkout set sidecars/cockpit-cliproxy)
```

只取 sidecar 子树，约 30 MB。若本机访问 GitHub 需要代理，按本机情况配置
（`https_proxy` / `http_proxy`）；若 tag `v<VER>` 不存在，用 `main` 并说明。

**第 2 步：打补丁（结构式，别用行号）**

目标文件：`$W/sidecars/cockpit-cliproxy/provider_gateway.go`，函数
`writeProviderGatewayResponsesStream`（签名可能是 `(c *gin.Context, body io.Reader)`
或 `(c *gin.Context, body io.Reader, multiAgentV2Optimized bool)`，按实际来）。

**只做三处插入**（不要改动其它逻辑）：

1. 在函数开头的 `if body == nil { return }` 之后插入：
```go
	flusher, ok := c.Writer.(http.Flusher)
	if !ok {
		writeAPIError(c, http.StatusInternalServerError, "streaming not supported", "streaming_not_supported")
		return
	}
```
2. 在函数内那处 `if _, writeErr := c.Writer.Write(...); writeErr != nil { return }` 之后插入：
```go
			// SSE events are terminated by a blank line; flush there so the client sees
			// each event as it arrives instead of one bufio burst at the end.
			if len(bytes.TrimRight(line, "\r\n")) == 0 {
				flusher.Flush()
			}
```
3. 在函数内最后的 `if err != nil { return }` 里、`return` 之前插入 `flusher.Flush()`。

`bytes` 与 `net/http` 在该文件已导入，**不需要改 import**。
注意：Windows 上 git 工作树常是 CRLF，按 LF 写的匹配会失败 —— 先归一化再匹配，写完按原风格还原。
若该函数体内**已经出现** `c.Writer.(http.Flusher)`，说明本机版本已自带修复，**停止，不要重复打**。

**第 3 步：构建**

需要 Go（`go version`；缺失就先装）。然后：

```bash
cd "$W/sidecars/cockpit-cliproxy"
go build -trimpath -o /tmp/cockpit-cliproxy-patched .   # Windows 下加 .exe
```

**第 4 步：先隔离验证，再替换**

把本机 sidecar 的 `config.json` / `manifest.json` 复制到临时目录、把 `port` 改成一个没人用的端口
（例如 17600），用新构建的二进制跑起来，再用第 0 步同样的方法测一次。
**期望：`headers_ms` 明显小于 `total_ms`、socket 读取次数 ~25、响应体字节数与事件数不变。**
隔离实例测完请关掉。

**第 5 步：替换（这一步必须由我手动配合，不要自己启动应用）**

1. 先备份：把 Cockpit 目录下的 `cockpit-cliproxy`（Windows: `cockpit-cliproxy.exe`）
   复制成 `cockpit-cliproxy.exe.before-sse-flush-<时间戳>.bak`
2. **让我手动退出 Cockpit**（托盘图标 → 退出）—— 应用在运行时该文件被锁住，无法替换
3. 我退出后，把新构建的二进制覆盖过去
4. **让我手动双击启动 Cockpit**

⚠️ **不要从脚本或工具会话里启动这个应用**。实测：非交互会话启动时它的前端不会挂载
（日志 `[Diagnostics] 前端启动超时: timeoutMs=15000, lastStage=none`，缺少正常启动时的
`[Updater] Tauri Updater + Process 插件已初始化` 与 `前端已就绪: react_mounted`），随后静默退出。
而且从工具会话启动 GUI 程序可能被本机安全策略拦截。

**第 6 步：验收**

我启动后，用第 0 步的方法再测一次。**期望 `headers_ms / total_ms ≤ 0.5`、socket 读取次数 ≥ 20。**
注意：不要用固定的「`headers_ms` < 2 秒」当唯一判据 —— 上游直连自身首字节波动就有 1.2–2.2 秒。

### 请一并告诉我

- 本机 Cockpit 版本、sidecar 二进制替换前后的 sha256
- 修复前后各一次测量（`headers_ms` / `total_ms` / socket 读取次数 / 响应字节数 / delta 事件数）
- 备份文件的确切路径
- **一个重要提醒**：Cockpit 下次升级会覆盖这个二进制（更新是弹窗提示、非静默）。
  请告诉我升级后如何判断补丁还在不在（跑一次第 6 步的测量即可：达标=在，不达标=被覆盖需重打）。

## 提示词结束

---

## 附录：两处容易踩的坑（可以一起粘给那边的 AI）

1. **别用 `wireApi: "chat_completions"` 绕**：实测更差。chat 格式不渲染握手事件，
   上游代理必须等到第一个生成 token 才提交响应头（实测 `upstream_header_time` 10.7 / 14.6 秒）。
   `responses` 才是对的 wire API，缺的只是这个 `Flush`。
2. **别自动「回灌」备份的旧补丁二进制**：Cockpit 升级会连新版 sidecar 一起换掉，
   把旧补丁版灌回去等于**降级** sidecar。正确做法是升级后按上面第 1–3 步用**新版本源码**重新构建。

## 本仓库里的等价资产（如果那台电脑能拿到这个仓库）

| 文件 | 用途 |
|---|---|
| `docs/runbooks/rebuild-cockpit-sidecar-patch.sh` | 一键重打（读版本 → 稀疏克隆对应 tag → 打补丁 → 构建 → 打印替换步骤） |
| `docs/runbooks/cockpit-sidecar-flush-fix.py` | 结构式修补脚本（锚点定位、幂等、自动处理 CRLF） |
| `docs/runbooks/cockpit-sidecar-sse-flush.md` | 完整落地与验收 runbook |
| `outputs/sse_framing_probe.py` | 测量探针（带 `PROBE_ASSERT=1` 断言模式） |

这两个脚本目前按 Windows 路径写的，换到 macOS/Linux 需要把
`"/c/Program Files/Go/bin/go.exe"`、`$LOCALAPPDATA` 之类的路径改成对应平台的写法；
**提示词里的步骤本身是平台无关的**，让那边的 AI 按本机情况调整即可。
