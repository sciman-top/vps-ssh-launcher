# Runbook: fq provider 的接入方式选项（含一次错误结论的更正）

**结论先行**：Cockpit UI 里那三个接入模式（**网关列出 / 直连官方 / CDP 注入**）
**只对 DeepSeek 账号开放**。`fq.sciman.top` **拿不到**「直连官方」，
所以「切直连绕开 sidecar 缓冲」这条路**对 fq 不成立**。
对 fq 而言，本机唯一真修复是**打补丁**（或等上游）。

---

## 更正记录（2026-09-29 22:1x）

我此前在 `docs/runbooks/cockpit-direct-mode-switch.md` 里写「在 provider 的
接入方式里选『直连官方 API』」——**那是错的，已撤回该文件**。两条错误：
1. 「接入方式」在 provider 表单里**不是可编辑控件**：`enableModePreference` 是由
   **wireApi 芯片自动派生**的（选「Responses 原生」→ `direct`，选「Chat Completions 协议」→ `gateway`，
   见 `CodexModelProviderManager.tsx:1779-1806`），表单里只做只读展示。
2. 真正可点的三选一在**启动区**，但被 DeepSeek 判定门控：

```
CodexApiKeyLaunchSection.tsx:107
  const canChooseAccessMode = isDeepSeekResponsesAccount(account);

codexDeepSeekAccess.ts:84-96
  isDeepSeekAccount = (api_provider_id === "deepseek")
                   || api_base_url.includes("api.deepseek.com")
codexDeepSeekAccess.ts:127-135
  isDeepSeekResponsesAccount = isDeepSeekAccount && wire !== "chat_completions"
```

fq 的 `baseUrl = https://fq.sciman.top:8443/<prefix>/v1`、`provider_id` 也不是 `deepseek`
⇒ `canChooseAccessMode = false` ⇒ UI 只显示只读的「协议 / 接入」，**没有按钮**。
（UI 文案本身也印证这条路的价值：`直连官方` 的说明是
「不走网关，速度更快。不能在 Codex 内切换模型，也没有 OAuth 能力与生图转发。」）

**这条更正把推荐顺序反过来了**：既然直连模式对 fq 不可用，
「打补丁 + 提 issue/PR」在 fq 场景下**就是当前最优的可行组合**。

---

## fq 的实际可选路径

| 路径 | 可行性 | 说明 |
|---|---|---|
| UI 切「直连官方」 | **不可用** | 仅 DeepSeek 账号开放（见上） |
| UI 切「CDP 注入」 | **不可用** | 同上，且需注入官方客户端、不支持绑定 OAuth |
| 手改 `~/.codex/config.toml` 指向 fq | **不推荐** | `codex_account_model_catalog.rs:2398` 无条件写 `model_provider = "codex_local_access"`，下一次投影就覆盖；且 GLM 会话独立得出同一结论 |
| **打补丁**（给 `writeProviderGatewayResponsesStream` 加 Flush） | **唯一本机真修复** | 见下 |
| 等 Cockpit 上游修复 | 可行但被动 | 材料已备齐 |

---

## 打补丁的持久性（已实测）

| 问题 | 结论 | 证据 |
|---|---|---|
| 重启会覆盖吗 | **不会** | `cockpit-cliproxy.exe` mtime `09-29 19:37`，而 Cockpit 本次启动 `21:19:55`（`server.json`，v1.3.62），期间 sidecar 多次重启 —— mtime 未变 |
| 更新会覆盖吗 | **会** | ① 09-29 19:37 的更新把它换成官方版；② 安装目录有 `*.localpatch-20260906-2337.bak`、`.before-5_6-capabilities-20260710-135135.bak` ⇒ **07-10、09-06 两次补丁都已被更新覆盖** |
| 更新是静默的吗 | **不是** | `remote_config_cache.json` → `updatePrompt: {mode:"popup"}` |

**补丁是否还在**——一条命令判定（`exit 0` = 在，`exit 1` = 被更新覆盖）：

```bash
PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> \
  ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
```

**补丁内容**：`outputs/cockpit-sidecar-sse-flush.patch`
（`git apply --check -p1` 对 `v1.3.57-7-gdbe56a1e` 通过；CRLF/LF/`--ignore-whitespace` 三种情况均 rc=0）。
构建与替换步骤见 `docs/runbooks/cockpit-sidecar-sse-flush.md`。

> 注意：该 patch 锚定具体版本；Cockpit 更新后源码若变动，需重新核对函数体再应用。

---

## 附：本次排查顺带修好的推送问题（与本故障无关，但会挡 closeout）

`git push` 一直失败（`CONNECT tunnel failed, response 502`）的真因：
环境变量 `https_proxy` / `http_proxy` 指向 **`127.0.0.1:12803`**，该代理到 GitHub 不通
（`curl -x 12803 https://github.com` → code 000 超时）；而 v2rayN 的 xray **10808** 通
（code 200 / 4.7s）。且 git-lfs 的 pre-push 锁校验会返回 `Bad Gateway`。

可用组合：

```bash
git config lfs.https://github.com/sciman-top/vps-ssh-launcher.git/info/lfs.locksverify false
https_proxy=http://127.0.0.1:10808 http_proxy=http://127.0.0.1:10808 git push origin main
```
