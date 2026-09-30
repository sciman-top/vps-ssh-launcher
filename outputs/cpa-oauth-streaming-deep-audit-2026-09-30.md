# ChatGPT desktop → Cockpit Tools → fq(CPA) → ChatGPT OAuth
# 「慢 / token 吐出缓慢」全面深度审查 — 2026-09-30

**症状**：在 ChatGPT desktop 中经 Cockpit Tools 的 **Direct OAuth** 或 **Direct API**
调用 bwg VPS 上 CPA（CLIProxyAPI，`fq.sciman.top:8443`）的 ChatGPT OAuth 模型
（`gpt-6-luna` 等）时速度慢、token 吐出缓慢，**尤其走 CPA 的 API key 时**。

**方式**：本地已装二进制逐字节核对 + Cockpit v1.3.63 源码精读 + 本机分层实测 +
远端 nginx/CPA 日志 fresh read + 受控 live replay（本轮消费真实 OAuth turn，已授权）。

---

## 0. 结论先行

| 问题面 | 判定 |
|---|---|
| **Direct API 路径（用户当前实际在用）** | ✅ **已彻底修复**（09-29 定位的根因 A 补丁在位，实测与公网直连等价） |
| **Direct OAuth 路径（API 服务）** | ❌ **此前未修复** —— 根因 B（`stream-bootstrap-buffering=true`）一直留在配置里；**本轮已修** |
| **首 token 延迟（1.4 s ~ 13 s 波动）** | ⚠️ **上游 ChatGPT OAuth 后端**，本地无法消除，只能记录 |

**一句话**：09-29 定位的两个本地根因中，**只有 A（sidecar SSE flush）被真正修掉**；
B 从未修，只是因为它所在的那条路径当时处于停用状态而被误认为"不是问题"。
本轮把 B 也修掉了。剩下的"慢"是上游容量波动，不是本机链路。

---

## 1. 链路与两种接入方式（关键区分）

```
ChatGPT desktop
  └─ ~/.codex/config.toml  model_provider = "codex_local_access"
       ├─ [Direct API]   Provider Gateway  → 127.0.0.1:10909
       └─ [Direct OAuth] API 服务(localAccess) → 127.0.0.1:14185
              ↓（同一个二进制 cockpit-cliproxy.exe，配置不同）
       本地 xray 127.0.0.1:10808（继承 Windows 系统代理）
              ↓
       fq.sciman.top:8443 → nginx → admission 8318 → CPA 8317 → ChatGPT OAuth
```

**关键事实（本轮新确认）**：`cockpit-cliproxy.exe` **不是**一个薄转发器，而是
`CLIProxyAPI v7.2.155 + Cockpit 定制`的**单二进制**（`sidecars/cockpit-cliproxy/go.mod`:
`replace github.com/router-for-me/CLIProxyAPI/v7 => ./third_party/CLIProxyAPI`）。
两个端口跑的是**同一个可执行文件**，只是 `--config` 不同：

| 端口 | 角色 | 配置来源 | `codex.stream-bootstrap-buffering` |
|---|---|---|---|
| 10909 | Provider Gateway（Direct API） | `codex_provider_gateway_sidecars/<hash>/config.json` | **false** ✅ |
| 14185 | API 服务（Direct OAuth） | `codex_local_access_sidecar/config.json` | **true** ❌ |

**当前运行态（实测）**：10909 在听（pid 29156）；**14185 未监听**，
`codex_local_access.json` 为 `enabled:false`、`auths/` 为空 ⇒ **Direct OAuth 当前不可用**。
桌面流量 `api_key_label = "Provider Gateway: api-key-ec280ff6"` ⇒ **用户在用 Direct API**。

---

## 2. 分层实测（同一模型 `gpt-6-luna`，本轮真实消费 OAuth turn）

### 2.1 本地链路：sidecar 与公网直连**等价**

| 路径 | headers_ms | total_ms | socket_reads | read_gap_p50 | first_delta_ms | ms_per_delta |
|---|---|---|---|---|---|---|
| sidecar 10909（Direct API） | 1861 | 14318 | **89** | **2 ms** | 11347 | **37** |
| 公网 8443 直连（对照） | 1256 | 13723 | 44 | 20 ms | **11389** | **29** |

- **字节数、delta 事件数、事件种类完全一致**（27863 B / 79 deltas / 同样 8 种事件）。
- `first_delta` 两条路径只差 42 ms ⇒ **sidecar 没有引入任何额外等待**。
- `socket_reads=89`、`read_gap_p50=2ms` ⇒ **逐事件投递**（缺陷态是 `≈8` 次、`gap=0`）。
- `ms_per_delta` 30–37 ms/token，与直连的 29 基本一致 ⇒ **吐字节奏正常**。

### 2.2 网关侧（`/var/log/nginx/cpa_gateway.access.log`，同批探针）

```
183.236.101.21 route=responses status=200 request_time=13.763 upstream_time=13.762 bytes=27863 upstream_header_time=1.295
183.236.101.21 route=responses status=200 request_time=13.354 upstream_time=13.182 bytes=28174 upstream_header_time=0.722
183.236.101.21 route=responses status=200 request_time=2.160  upstream_time=2.158  bytes=26004 upstream_header_time=0.660
183.236.101.21 route=responses status=200 request_time=4.756  upstream_time=4.755  bytes=9142  upstream_header_time=0.633
```

**CPA 在 0.63–1.30 s 内就提交了响应头**，响应头之后的 12 s 全部是上游生成时间。
CPA 容器日志一致：`200 | 13.759s | POST /v1/responses`（同一条请求）。

### 2.3 非 OAuth 对照：GLM 快 5 倍

| 模型 | lane | headers_ms | first_delta_ms | total_ms | ms_per_delta |
|---|---|---|---|---|---|
| `glm-5.3-flash` | zhipu（openai-compatibility） | 877 | **1857** | 2375 | **7** |
| `gpt-6-luna` | ChatGPT OAuth | 937 | **11793** | 13075 | 30 |

⇒ 慢是 **OAuth lane 特有**，不是 CPA 链路、不是 nginx、不是本地代理。

### 2.4 首 token 分布：上游波动，不是固定开销

同一 prompt 连发 6 次（间隔 3 s）：

```
first_text_ms = 1352, 2293, 2640, 1522, 1579, 1391   (median 1579)
```

同一晚更早的 4 次（22:44–22:47）却是 `11347 / 11793 / 11306 / 11389`。

**⇒ 上游 ChatGPT OAuth 后端存在时段性排队**：空闲窗口 ≈1.4–2.6 s，
高峰窗口 ≈11.3–11.8 s（且高峰窗口内**极其一致**，说明是排队而非随机推理）。

### 2.5 桌面真实流量（`codex_local_access_logs.sqlite`）

| 窗口 | 模型 | n | 成功率 | avg latency | max |
|---|---|---|---|---|---|
| 24 h | `gpt-6-sol` | 127 | 124/127 | **19.9 s** | 150.8 s |
| 24 h | `gpt-6-luna` | 57 | 57/57 | 9.4 s | 60.0 s |
| 72 h | `gpt-6-luna` | 1317 | 1211/1317 | **25.6 s** | 267.6 s |
| 72 h | `gpt-5.6-terra` | 471 | 467/471 | 16.0 s | 114.8 s |
| 72 h | `deepseek-v4.1-flash` | 273 | **14/273** | 1.1 s | — |

> `deepseek-v4.1-flash` 273 次仅 14 次成功（5 %）⇒ 死路由，但用户几乎不用它。
> Provider Gateway 路径的 `input_tokens`/`output_tokens` 全部记录为 0 ⇒
> **Cockpit 不采集该路径的 token 用量**（不影响功能，影响可观测性）。

---

## 3. 根因清单（逐条判定）

### 根因 A — sidecar `writeProviderGatewayResponsesStream` 无 Flush（Direct API 路径）

- **位置**：`sidecars/cockpit-cliproxy/provider_gateway.go`（v1.3.63 中约 636–652 行）。
- **判定**：✅ **已真正修复**。
  - 已装二进制 sha256 = `d1decd980bab3260d07aa348edbbaba03d8a24f95b1f9af26292093c2de825bc`
    **==** `%TEMP%\cockpit-cliproxy-repatched.exe`（09-30 21:38 重打产物）。
  - 运行时实测 `socket_reads=89 / gap_p50=2 ms`（缺陷态为 ≈8 / 0）。
  - 与公网直连逐项等价（§2.1）。

### 根因 B — API 服务 sidecar 的 `codex.stream-bootstrap-buffering=true`（Direct OAuth 路径）

- **生成端**：`src-tauri/src/modules/codex_local_access_sidecar_config.rs:2419`
  ```rust
  "stream-bootstrap-buffering": api_service,   // prepare_sidecar_launch_config() 传 true
  ```
  只有 `prepare_sidecar_launch_config()`（API 服务专用，`codex_local_access_gateway_runtime.rs:40`）
  传 `true`；`prepare_sidecar_launch_config_in_dir()`（async 包装，Provider Gateway 用）硬编码 `false`。
- **语义**（CLIProxyAPI `internal/config/config_types.go:186-203` 原文）：
  > holds back the frames that arrive before generation starts … **until the first generated event
  > arrives**. … Trade-off: **the response headers are delayed until the upstream starts generating**,
  > which on a slow reasoning turn now means several heartbeat intervals rather than one.
  - 收益：上游把 `server_is_overloaded` 藏在 HTTP 200 流里时，延迟提交响应头以便**换凭据重试**。
  - 代价：**每轮首字节被推迟整个推理期**。
- **判定**：❌ **此前从未修复**（配置里一直是 `true`，09-29 的审计也确认"改文件会被覆盖，转上游建议"）。
  - 本机 `max-retry-credentials: 1` ⇒ **只有一个凭据，没有可换对象** ⇒ 收益为 0，代价全额支付。
  - 该路径**当前不可用**（`enabled:false`、`auths/` 空、14185 未监听）⇒ 症状暂时不显现，
    但**一旦启用就会立刻重现**"首字节迟迟不来 / token 吐出缓慢"。
- **本轮处置**：✅ **已修**（见 §4）。

### 根因 C — 内置 CPA 的 SSE 转发（Direct OAuth 路径是否有第二个缺陷）

- **判定**：✅ **无缺陷**。`third_party/CLIProxyAPI/sdk/api/handlers/stream_forwarder.go`
  的 `ForwardStream()` 有 **6 处 `flusher.Flush()`**（:115/122/127/154/165 及 keep-alive），
  `internal/api/middleware/response_writer.go` 的包装器不吞 `Flush`（嵌入 `gin.ResponseWriter`）。
  ⇒ Direct OAuth 路径**只差根因 B 一项**。

---

## 4. 本轮修复（Direct OAuth 路径的根因 B）

**为什么不改配置文件**：`prepare_sidecar_launch_config()` 在**每次 API 服务实例启动/配置变化**时
重新生成 `codex_local_access_sidecar/config.json`（`codex_local_access_gateway_runtime.rs:40`
→ `sidecar_config.rs:2059`）。改文件**不持久**。

**为什么改 Go 而不是 Rust**：Rust 侧要重编 100 MB 的 Tauri 主程序（`cockpit-tools.exe`）；
Go 侧是同一个 sidecar 二进制的入口，改动 1 行 + 注释，与既有 SSE flush 补丁**同一次构建**完成。

**改动**（`sidecars/cockpit-cliproxy/main.go`，`config.LoadConfig` 之后）：

```go
cfg.Codex.StreamBootstrapBuffering = false
```

（完整注释与理由见 `main.go.bootstrap-off.patch`；该赋值位于**每个 sidecar 实例加载配置的唯一入口**，
对 Provider Gateway 无影响 —— 它本来就配置为 `false`，且其请求走 `provider_gateway.go` 透传，
根本不读这个键。）

**产物**（`~/.antigravity_cockpit/_codex_verify_backups/sse-flush-bootstrap-off-20260930/`）：

| 文件 | 说明 |
|---|---|
| `cockpit-cliproxy-bootstrap-off.exe` | 新二进制，sha256 `f06bb374b2d1af8b65d846c8f598331a91a720a71a564fc3c9ef51fcd858e0dc` |
| `main.go.bootstrap-off.patch` | 本轮改动（1 处赋值 + 说明注释） |
| `sidecar-full-v1.3.63.patch` | 该二进制的**全部**本地改动（SSE flush + bootstrap-off） |

**隔离验证**（`outputs/sidecar_bin_verify.py`，scratch 端口 17999，副本配置，
`disable-auth-auto-refresh=true` 保证不刷新任何凭据）：

```json
{"bound": true, "alive_after_bind": true, "models_http": 401,
 "log_tail": "...{\"host\":\"127.0.0.1\",\"port\":17999,\"type\":\"ready\"}"}
```

⇒ 二进制可正常启动、监听、应答 HTTP（401 是未带 key 的预期值）。

**安装状态**：✅ **已就位**。直接 `cp` 会被运行中的 sidecar 锁住（`Device or resource busy`），
改用 Windows 允许的「**重命名正在运行的 exe**」：旧映像 →
`cockpit-cliproxy.exe.replaced-20260930-232908.bak`，新二进制写入原路径并核验
sha256 = `f06bb374…`。
**磁盘已生效，但运行中的进程仍用旧映像 ⇒ 需要一次 sidecar 重启才真正加载**
（Cockpit 里切一次账号，或重启 Cockpit Tools）。

---

## 5. 排除的假设（本轮实测证伪，别再试）

| 假设 | 结论 | 证据 |
|---|---|---|
| `reasoning.effort` 是主因 | ❌ 证伪 | 四档实测 `low 1493 / medium 2804 / high 2653 / xhigh 1867` ms，无单调关系；`reasoning_tokens` 仅 0–19 |
| `service_tier=priority/ultrafast` 可提速 | ❌ 证伪 | CPA 确实把它转成 `X-Codex-Routing-Hint: model=…;tier=…`（`codex_executor_request.go:391`），但**上游回显恒为 `default`**，无任何提速 |
| 本地 sidecar 仍在缓冲 | ❌ 证伪 | §2.1 与公网直连逐项等价 |
| CPA 侧在扣留 | ❌ 证伪 | nginx `upstream_header_time` 0.63–1.30 s；CPA 配置 `stream-bootstrap-buffering: false`、`stream-bootstrap-timeout: '0'` |
| 换 `wireApi: chat_completions` 可绕 | ❌ 早已证伪（09-29） | chat 格式不渲染握手事件，首字节更差（10.7–14.6 s） |
| 内置 CPA 的 SSE 转发也有 flush 缺陷 | ❌ 证伪 | `stream_forwarder.go` 有 6 处 `Flush()` |

---

## 6. 残余不可控因素（记录，非本机可修）

1. **上游 ChatGPT OAuth 后端时段性排队**：空闲 1.4–2.6 s，高峰 11.3–11.8 s 首 token。
2. **单一 Codex 凭据**：`auth-dir` 下只有 `codex-1c3cf6c3-…-plus.json`，
   `max-retry-credentials: 1` ⇒ 无法做凭据级 failover。**增加第二个 Codex OAuth 凭据**
   是唯一能真正摊薄高峰停顿的办法。
3. **死路由**：`deepseek-v4.1-flash` 72 h 内 273 次仅 14 次成功；`gpt-5.6-sol`/`gpt-6-astra`
   的上游（ai.input.im）长期 502。属路由清单决策项，本轮未动。

---

## 7. 验收分级

| 层级 | 判定 | 依据 |
|---|---|---|
| repo_verified | **PASS** | Cockpit v1.3.63 源码逐行定位（`sidecar_config.rs:2419`、`gateway_runtime.rs:40`、`config_types.go:186`、`stream_forwarder.go:59`） |
| filesystem_projected | **N/A** | 本轮无远端改动（远端配置本就合规） |
| host_loaded | **PASS（部分）** | Direct API 侧：补丁哈希 + 运行态实测；Direct OAuth 侧：新二进制**隔离启动**通过，**尚未替换安装** |
| controlled_live_replay | **PASS** | 分层三跳 + 双路径对照 + 模型对照 + effort/service_tier A/B + 6 次 TTFT 分布，全部消费真实 OAuth turn |
| natural_live_accepted | **NOT CLAIMED** | 需你实际 desktop 长会话确认 |

### 收尾状态

1. ✅ **二进制已替换就位**（`f06bb374…`；旧映像存为
   `cockpit-cliproxy.exe.replaced-20260930-232908.bak`）。
2. ⏳ **待生效**：磁盘上已是新二进制，但运行中的进程仍用旧映像 ⇒
   在 Cockpit 里**切一次账号**（触发 `[Codex Switch][Backend] restart specified app`），
   或直接**重启 Cockpit Tools**。
3. 核查（新哈希 ⇒ 两处补丁都在）：
   ```bash
   sha256sum "$LOCALAPPDATA/Cockpit Tools/cockpit-cliproxy.exe"
   ```

> ⚠️ **每次 Cockpit 升级都会换掉这个 exe**（历史先例：07-10、09-06、09-29 三次补丁均被更新覆盖）。
> 升级后按 `docs/runbooks/cockpit-sidecar-sse-flush.md` 的「升级后 60 秒核查」重打。

---

## 8. 复现用脚本

| 脚本 | 用途 |
|---|---|
| `outputs/sse_framing_probe.py` | 原始 SSE 帧定性（headers / socket_reads / gap / delta 节奏），`sidecar` 与 `public` 两种模式 |
| `outputs/reasoning_effort_probe.py` | reasoning effort A/B |
| `outputs/service_tier_probe.py` | **本轮新增**，service_tier A/B（含上游回显核对） |
| `outputs/sidecar_bin_verify.py` | **本轮新增**，重建后二进制的隔离启动验证 |

全部只读、不打印凭据。
