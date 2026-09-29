# Runbook: 把 desktop 的 fq provider 切到「直连」以绕开 sidecar 的 SSE 缓冲

**目的**：绕开 `cockpit-cliproxy` 的 `writeProviderGatewayResponsesStream` 缺 Flush 缺陷
（根因见 `outputs/cpa-streaming-rootcause-audit-2026-09-29.md`），**无需打补丁、无需改源码**。
**决策依据**：`outputs/cpa-streaming-decision-brief-2026-09-29.md` §6。

---

## 为什么这条路可行

Cockpit 每条 provider 都有「接入方式」（源码 `enableModePreference`），
能力矩阵对 `wireApi: "responses"` 的 provider 给出：

```
adapterProfile:  "openai_responses_native"
defaultEnableMode: "direct"      ← 官方默认就是直连
requiresGateway: false
supportsDirect: true
capabilities: { responses: true, tools: true, reasoning: true, streamUsage: true,
                hotSwitch: false, requestLogs: false, failover: false }
```

**直连模式 = desktop 直接打 provider 自己的 `baseUrl`，不经过 10909 sidecar**，
那个缺 Flush 的函数自然碰不到。key 的下发方式已从源码确证：写入
`~/.codex/config.toml` 的 `experimental_bearer_token`
（`codex_account_model_catalog.rs:2418`），所以直连模式是完整可用的。

---

## 操作（UI，2 步）

1. 打开 Cockpit Tools → **Codex** → **模型服务商**（Model Provider Manager）
2. 找到 `fq.sciman.top` 条目 → **接入方式** → 选 **「直连官方 API」**
   （当前是「网关列出模型」；UI 文案：`启用策略` / `接入方式` / `直连官方 API` / `网关列出模型`）
3. 让 Cockpit 重新投影 desktop 配置（通常保存后自动；必要时重启 Cockpit 与 desktop）

---

## 预期变化（切之前请确认能接受）

`~/.codex/config.toml` 的 `[model_providers.codex_local_access]` 会从

```toml
name = "Codex API Service"
base_url = "http://localhost:10909/v1"        # 网关模式（有缓冲缺陷）
experimental_bearer_token = "agt_codex_…"      # sidecar 本地 key
```

变成指向 fq 的直连条目（`base_url = https://fq.sciman.top:8443/<prefix>/v1`，
`experimental_bearer_token = <fq 的客户端 key>`）。

**代价（三项能力关闭）**：
- `requestLogs` —— 失去 `codex_local_access_logs.sqlite` 的逐请求延迟/用量记录
  （本报告一直用它做归因，切了就没了）
- `failover` —— 失去多 key/多账号自动切换
- `hotSwitch` —— 切换模型需重启 desktop

**另需确认的一点**：当前 desktop 只有一个 provider 条目（`codex_local_access` =
「Codex API Service」），它把 **所有** provider（fq / GLM / DeepSeek / ai.input.im / cii）
聚合在 10909 后面。切 fq 为直连后，desktop 的 provider 会变成 fq 这一个 ——
**其他 provider 的模型可能不再可用**。若你日常也在用 GLM/DeepSeek 系模型，
这一点必须先确认（这是我建议先小范围试、而不是直接切的原因）。

---

## 验证（切换后必做）

```bash
# 1) 看 desktop 现在指向哪里
grep -A5 '\[model_providers' ~/.codex/config.toml
#    期望：base_url 指向 fq（https://fq.sciman.top:8443/...），不再是 localhost:10909

# 2) 确认 10909 不再被使用（可选）
netstat -ano | grep -E ":(10909)\b"      # 仍在监听是正常的，只要 desktop 不再指向它

# 3) 真实体感：在 desktop 里发一轮需要推理的问题，观察 token 是否逐字流出
```

**对照基线**（本机实测，同一模型 `gpt-6-luna`）：

| 路径 | headers_ms | socket 读次数 | gap_p50 |
|---|---|---|---|
| 经 sidecar 10909（缺陷态） | **6985–22507** | **8** | **0 ms** |
| 公网直连（直连模式将走这条） | 1275–2220 | 21–99 | 8–12 ms |

---

## 回滚

同一条目把 **接入方式** 改回 **「网关列出模型」**（或「自动」），保存并重启即可。
`~/.codex/config.toml` 会被 Cockpit 重新投影回 `http://localhost:10909/v1`。

---

## 与「打补丁」的对比

| | 直连模式 | 打补丁 |
|---|---|---|
| 是否改 Cockpit 源码/二进制 | **否** | 是 |
| 重启后是否失效 | **否** | 否 |
| Cockpit 更新后是否失效 | **否** | **是**（本机 07-10、09-06 两次补丁都已被 09-29 更新覆盖） |
| 代价 | 失去 requestLogs / failover / hotSwitch，且可能失去聚合的其他 provider 模型 | 无（但每次更新要重打） |
| 维护成本 | 0 | 每次 Cockpit 更新后重打 + 重核 diff |

⇒ **优先试直连模式。** 只有当"必须保留网关模式"（例如必须保留 requestLogs 或聚合多 provider）
时，才考虑打补丁。
