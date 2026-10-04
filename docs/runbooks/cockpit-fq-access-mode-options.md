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

---

## 二次复核（2026-10-05，对 v1.3.65 官方源码）：direct 是后端就绪的正解，阻塞只在前端门控

> 源码基准：`github.com/jlcodes99/cockpit-tools` tag `v1.3.65`（在装 app 即 v1.3.65 = 上游最新 release，
> 本地 tarball 解包于 `%TEMP%/cockpit-tools-upstream-20261005/`；`D:\CODE\external\cockpit-tools`
> 旧 checkout 停在 v1.3.57 且整个 `D:\CODE\external` 树带继承 DENY ACE 写保护，fetch 需先解 ACL，未动）。

### 1. 「UI 切直连对 fq 不可用」仍然成立，但原因只剩前端一处门控

后端 `update_account_instance_access`（`codex_account_provider.rs:951`）注释明说
**「所有 API Key 供应商账号都支持实例接入方式」**，校验只要求
`direct` 必须 Responses 协议（fq 满足）。前端 v1.3.65 仍把入口锁在 DeepSeek：

- `CodexApiKeyLaunchSection.tsx:107`：`canChooseAccessMode = isDeepSeekResponsesAccount(account)`
- 三处执行器只在 `isDeepSeekAccount(account)` 时才传 `deepSeekAccessMode`：
  `useCodexAccountsAccessController.tsx:734`、`CodexInstancesPage.tsx:302`、`CodexModelProviderManager.tsx:3098`
- provider 表单的 `enableModePreference` 是纯前端注册表元数据，**Rust 侧零消费**，与账号接入方式无关。

### 2. 「僵尸旧名/错误映射」的真正根因 = 官方壳位池自动分配（非文件污染）

实例网关（供应商网关 sidecar）manifest 里的 `modelAliases`
`gpt-5.6-sol→gpt-6-astra-ciii`、`gpt-5.5→deepseek-v4.1-flash` 是 app **确定性分配**的产物：

- 壳池常量 `CODEX_PROVIDER_MODEL_SHELL_POOL = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]`
  （`codex_local_access_foundation.rs:261`）；`gpt-6.1-sol/gpt-6-astra/gpt-6-sol/gpt-6-luna` 为 identity-only。
- 分配规则（`allocate_provider_model_slots`，`codex_local_access_provider_gateway.rs:706`）：
  匹配官方 slug 的上游名保持原名；其余上游模型按目录顺序**依次领取空余壳名**——
  fq 现目录 12 名里 `gpt-6-astra-ciii` 领 `gpt-5.6-sol`、`deepseek-v4.1-flash` 领 `gpt-5.5`，
  与实测 manifest 逐字节吻合。
- 因此：**网关模式下 gpt-5.5 / gpt-5.6-sol 无法彻底清除**——它们是池内官方壳名，
  每次目录刷新/manifest 再生成都会重灌；CPA 目录是上游健康实时快照，目录一变映射就重排（「总是变来变去」）。
  选中这些壳名请求会被 sidecar 改写成真名再发上游，**CPA 永远看不到旧名**，CPA 侧无需任何目录配置。

### 3. direct 的完整效应链（全部源码锚点）

```
api_instance_access_mode = "direct"
→ account_uses_provider_direct_access = true（codex_account_provider.rs:895）
→ account_uses_raw_provider_model_ids = true（:901）
→ account_uses_synced_model_shell_gateway = false（codex_local_access_provider_gateway.rs:1181）
→ account_requires_provider_gateway = false（:1216）
→ 槽位全 identity（provider_model_slots_for_account raw 分支），无壳位、无别名、modelCapabilities 干净
→ restore/启动目标收集被同一谓词门控（codex_local_access_instance_gateways.rs:88-105）：
  先把绑定剥成裸账号 ID、再 load_account 按谓词判定 ⇒ 绑定字符串（有无 __provider_gateway__: 前缀）
  只是事后标记，**不是门控**（10/4 实测：仅去掉前缀、谓词仍真 ⇒ 网关照样拉起）。
  谓词为假 ⇒ 切号写裸绑定（codex_account_commands.rs:1315）+ 重启恢复无目标，
  「每次重启都要手动切换并启动」随之消失（该循环本身是设计内行为：
  实例网关随 Codex 实例进程存活，实例不在即释放，:393 起，注释原文「等下次通过 Cockpit 启动该实例时再重建」）
```

注意：`codex_instances.json` 里网关绑定本身是持久的（重启丢的是网关进程，不是绑定）；
端口是动态的（当前 9778，v1.3.64 前架构的 10909 已不适用）。
受管目录引用已由 app 自身退役（10/4 catalog→.bak + config.toml 指针移除），无需再清。
另：「名字变来变去」有**两个独立来源**——① 壳位重排（本目录 §2）；
② 上游目录自身涨落/改名（10/5 01:3x fq 实测：槽位2 已由 `gpt-6-astra-ciii` 改名
`gpt-6.1-sol-ciii`，目录 12→11）。只修别名层不解决 ②，②也无需修（上游健康快照语义）。

### 4. 修复路径（按优先序）

1. **上游开放前端门控**（推荐）：给 jlcodes99/cockpit-tools 提 issue/PR，
   把接入方式选择器开放给所有 Responses 型 API Key 供应商（后端零改动）。
   issue 草稿：`outputs/cockpit-tools-issue-access-mode-all-providers.md`（+ `.en.md`）。
2. **本地状态手术**（备选，高风险须用户当次授权）：改账号记录里的
   `api_instance_access_mode: "gateway"→"direct"`。账号记录为 AES-256-GCM 信封
   （`account_store.sqlite` 表 `account_records.account_json`，key_id=
   `local-secure-account-storage-v1`，key 在 `secure-account-storage.key`；
   `codex_accounts/*.json` 为同构镜像），须关 app 操作，重启后任意一次「切换」生效。
   回滚 = 同法改回 `gateway`。注意 `~/.cockpit_tools` 是指向 `~/.antigravity_cockpit` 的 junction。

   **✅ 已于 2026-10-05 ~01:04 依此路径实施并验证**（证据）：
   - `app.log.2026-10-04` 行 10508-10528：01:05:46 `switch_codex_account` **无**
     「API Key 账号启用本地供应商网关」行，耗时 200ms（此前三次切号 856-1075ms 且都带网关行）；
   - 9778/10909/14185 全部未监听，仅剩 `cockpit-tools.exe` 主进程；
   - `codex_instances.json` 绑定=裸 `codex_apikey_ec280ff6…`（无前缀）；
   - `~/.codex/config.toml` 的 `codex_local_access` 与 `fq_sciman_top` 两个 provider
     base_url 均为公网 fq（`codex_local_access` 只是运行时 provider 名，**不能**再据其判网关）；
   - `~/.codex/cockpit-model-catalog.json` 仅剩 .bak（01:04）；
   - fq `/v1/models` 直连实测 200、11 名、零退役名（gpt-5.5 / gpt-5.6-sol 不存在）。
3. UI 内能做的只有 cosmetics：启动预览「刷新配置」把账号目录对齐 fq 现目录
   （10/5 实测上游已是 11 名：gpt-6.1-sol 缺席、槽位2 已改名 gpt-6.1-sol-ciii；
   direct 模式下 Codex 列表来自账号目录的 identity 槽位，刷新后才与上游对齐）。

### 5. 对 9/29 结论的修订汇总

| 9/29 结论 | v1.3.65 复核 |
|---|---|
| 「直连模式对 fq 不可用」 | UI 层仍不可用；**后端已就绪**，阻塞=前端 DeepSeek 门控一处 |
| 「本机唯一真修复是打 sidecar 补丁」 | 直连语义下 sidecar 退出链路，SSE/45s 钳制补丁随之不需要；结构修复=开通 direct 入口 |
| sidecar 补丁持久性结论 | 仍有效（仅网关模式相关） |

### 6. 终局收口（2026-10-05 01:44）：直连需要两个账号字段同时就位

`direct` 只解决了「网关退出链路」；**Codex 端模型目录**由另一个账号字段
`api_sync_model_catalog_to_codex`（UI：API Key 账号表单的「同步供应商模型到 Codex」复选框）
独立门控：为假时每次切号/启动走 `cleanup_managed_model_catalog_for_dir` 主动清目录
（`codex_account_model_catalog.rs:2550` sync_or_cleanup 的 else 分支）——这就是
「重启后要重新拉取模型列表」「拉取/切换都看不到 fq 目录」的直接机制。01:04 手术同时改了
两个账号字段（direct=true + sync=false），后者留下目录投影缺口；01:44 用户在 UI 勾回
sync（账号文件 encrypted_at 01:44:28 实证）后恢复。

- 00:37 vs 01:05 切号行为翻转之谜定案：网关分支守卫是**纯账号谓词**
  （`codex_account_runtime_switch.rs:709` activate_provider_gateway_after_switch_if_needed，
  谓词假时静默走 stop_provider_gateways，无日志行）⇒ 行为翻转 ⇒ 账号记录必在两刻间被改过
  （即 01:04 手术），不存在"账号没变、运行态翻转"的替代解释。
- direct + sync=true 组合的目录内容=identity 槽位真名（sync_api_key_model_catalog_to_dir
  调 provider_model_slots_for_account raw 分支），无壳位复活路径。
- 终态验收清单：config.toml 有 model_catalog_json 指针；受管目录文件存在且只含真名；
  9778/10909/14185 不监听；**重启 app/电脑一次后列表仍在**（原始诉求的最终验收）。
- 教训补记：本故障最终是**两个独立账号字段各缺一半**——只看网关（七轮文件战争）或只看
  目录（本轮）都会漏另一半；「解密读取账号字段」是唯一能同时看清两个开关的手段，
  文件级考古（catalog/stats/attrib/前缀）与 UI 观察都不充分。

### 7. 存储裁定与四件套验收（2026-10-05 01:5x）

**活跃账号存储=逐账号 AES 文件**（`codex_accounts/<id>.json`，mtime 跟随每次切号/保存：
00:19→00:37→01:05→01:44:28）；`account_store.sqlite`（mtime 00:25，01:05 切号与 01:44 保存
均未写它）是**陈旧镜像，不是权威记录**——对 sqlite 解密得到的"无 direct 字段/sync 本来就
true/updated_at 9/29"与三重事实矛盾：① 9/29 早于该账号创建时间（10/4 21:23）；② 其
api_provider_id 指向 9 月中的 provider 条目；③ 行为级证据：01:05 切号无网关行（纯谓词守卫
runtime_switch.rs:709）+ 01:44 sync=true 后切号**未拉起网关**——若 direct 缺失（谓词真），
sync=true 必然触发 shell-gateway 分支（拉网关+目录含壳名），两者均未发生。

**四件套全绿（01:5x 实测）**：config.toml:23 `model_catalog_json = "cockpit-model-catalog.json"` ✓；
受管目录文件重生（01:44，13 项=12 真名+codex-auto-review，零 gpt-5.5/gpt-5.6-sol）✓；
9778/10909/14185/7421 全不监听 ✓；direct 通路受控生成 200 'ok' ✓。剩余验收：自然重启后列表仍在。

**澄清两条**：① sidecar manifest 是 app 每次拉网关前**重新生成的输出物**
（prepare_sidecar_launch_config 重写 config+manifest），盘上旧 manifest（36218dcc，10/4 23:14）
不会被将来某次网关启动"读到"，无定时炸弹；② gpt-5.6-terra/luna 为 identity 槽位
（display==slug，上游真实服务），正当保留。壳位唯一回归条件=谓词翻真（回到网关模式），
复查判据=端口监听+目录出现 display≠slug 的名，触发场景=Cockpit 更新/架构变动，而非上游改名
（上游改名只影响网关模式下的壳位重排）。
