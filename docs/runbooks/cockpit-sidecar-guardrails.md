# Cockpit sidecar guardrails

本仓库把本机 Direct API 的持久修复、重投影和重启后验收集中到：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Audit
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

## 证据分层

- `Audit`：只读输出安装文件 hash、运行中的两个 sidecar、10909/14185 监听、持久 collection、活动 config/manifest。
- `Audit` 同时读取最近 180 分钟的脱敏 `request_logs`：将约 45 秒的本地闸门 429、115 秒以上的远端 admission/长等待 429 和 60 秒以上的成功慢请求分开计数。三个桶只是 timing bucket；只有和 CPA request-id、`cpa-admission` journal 同窗关联后，才能升级为确切归因。
- `Project`：要求传入与本机 Cockpit 版本匹配且 hash pinned 的候选 exe；已匹配时走 no-op。需要替换时先备份、staging/hash 复核，再用旧文件改名→新文件落位；失败会尝试恢复旧文件。两个本机 collection 也只在值发生变化时写入。
- `Verify`：确认安装 hash、两个运行进程的磁盘路径 hash、每个监听端口的拥有 PID、两个监听端口和全部持久 collection；`HOST_LOADED=INFERRED_FROM_LISTENER_OWNERS` 表示端口拥有者与 pinned 映像一致的运行态推断，不伪装成内核级 loaded-image hash 证明。活动 provider manifest 的 `0/120000` 属于官方生成器已知漂移，由 r3 入口钳制覆盖。

## 重投影

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 `
  -Mode Project `
  -CandidatePath "$env:TEMP\cockpit-tools-v1.3.65-build-20261002\sidecars\cockpit-cliproxy\cockpit-cliproxy-v135-gate-r3-20261002.exe"
```

脚本不会停止 Cockpit 或 sidecar。若输出 `SIDECAR_ALREADY_PROJECTED=1`，二进制无需再次投影；仍应按当前运行态执行 `Verify`。若确实发生了二进制或 collection 改动，输出 `RELOAD_REQUIRED=1` 后，使用 Cockpit 正式重载/启动路径，随后运行：

```powershell
pwsh -NoProfile -File .\scripts\cockpit_sidecar_guardrails.ps1 -Mode Verify
```

`Verify` 是状态验证（hash/端口/监听归属/持久 collection）。重投影了**新构建**
二进制后，再补一次行为级验收（零上游配额，桩上游 + scratch 端口）：

```bash
./.venv/Scripts/python.exe scripts/cockpit_gate_wait_cap_check.py
```

3 条请求占满账号槽后，第 4 条应在期望封顶（默认 45s，`--expect-cap-s` 调整）
附近以 429 结束并打印 `ACCEPTANCE_PASS`；`--control <旧exe>` 可附加 A/B 对照
（r2-vs-r3 首次对照实测 120.002s → 45.003s）。仅 `Verify` 通过不足以证明封顶
行为，两者判据不同。

真实 OAuth 请求不属于默认验收。受控实战使用已有的非 OAuth `glm-5.3` 路径；容量、429 和慢速分析按远端 CPA admission journal、本机 `request_logs` 和请求耗时分别归因。

## 配置形态检查（10909 静默停机）

上面的 `Audit`/`Verify` 证明的是**二进制与监听**已经就位。它们答不了另一个问题：
*这次配好的形态，下次重启还会不会把 10909 拉起来？* 后者由「默认实例绑定账号」决定
（`ensure_provider_gateway_for_bind_account`），而绑定账号会被 UI 编辑 key 的联动更新静默改掉。

```bash
./.venv/Scripts/python.exe scripts/cockpit_provider_health.py
./.venv/Scripts/python.exe scripts/cockpit_provider_health.py --json
```

只读，退出码：`0` 无发现 / `1` 有发现 / `2` 无法检查（缺 provider 注册表）。
它核四条不变量：

1. **key 与端点匹配**——`agt_codex_` 是本地 sidecar key，`agt_gw_` 属于远端 `fq.sciman.top`。
   把后者挂到 10909 条目上，就是 `PROVIDER_MODELS_HTTP_401` 的已记录成因。
2. **绑定与凭据一致**——账号 id 是 `md5(api_key)`，换 key 必换 id。绑定 id 必须能反查到
   仍存在于某条 provider 里的 key；反查不到即"条目与账号已错位"。
3. **sidecar config 与 provider 条目一致**——10909 接受的必须是本地 key，转发给上游的
   必须是远端 key；两个方向分开判，不要混用（混用会把正确的上游 key 判成非法）。
4. **桌面目录可路由**——桌面模型选择器是**投影**：它把 provider 目录合并成一份 slug 列表，
   所以会一直提供某些"只在某个网关里存在"的名字。选中的网关必须能解析每一个 slug，
   否则稳定得到 `400 model_not_found`，而用户读到的是"网关坏了"。

### 桌面指向哪个网关：两种模式都接受

`[model_providers.codex_local_access]` 的 `base_url` 决定模式，脚本会把它打成
`local_gateway` / `public_gateway` / `other` / `unknown` 四类之一：

| 模式 | 取值 | 得到 | 失去 |
|---|---|---|---|
| `local_gateway` | `http://127.0.0.1:10909/v1` | 本地并发闸门、模型别名重写层；桌面只持有本地 key | 侧车静默停机这一类故障 |
| `public_gateway` | `https://fq.sciman.top:8443/<hex>/v1` | 没有侧车静默停机 | 本地闸门与别名层；公网网关 key 落在桌面配置里 |

**两种都不是"错"**，脚本不会因为选了公网就报错——它只要求"选中的网关能服务目录里的名字"。
`other` 与 `unknown` 报 warn（无法判定服务集合），不改变退出码。

判据（公网模式）：

```bash
./.venv/Scripts/python.exe scripts/cockpit_provider_health.py \
  --routes scripts/remote/cpa_provider_routes.json \
  --desktop-catalog ~/.codex/cockpit-model-catalog.json
```

发现 `desktop-model-unroutable` 时，先确认这些名字是否真的需要：要么在 Cockpit UI 里从
provider 目录移除，要么把它们加回可路由清单（后者是路由变更，走 `bwg-cpa-route-change`
技能的闭环）。`codex-auto-review` 之类由 Cockpit 本地持有的名字在
`DESKTOP_LOCAL_ONLY_MODELS` 白名单里，不会被报。

**首选做法：让桌面目录成为所选网关的投影**（而不是逐个删名字）。桌面目录是
provider 目录 merge 出来的，所以只要**重建**它，陈旧名字会自然消失：

1. 在 Cockpit UI 的「模型供应商」里选中桌面正在用的那条（`public_gateway` 模式下是
   `fq.sciman.top`）。
2. 触发一次「获取上游模型」/重建目录，让它从该网关的 `/v1/models` 重新拉取。
3. 复跑 `cockpit_provider_health.py`，`desktop-model-unroutable` 必须消失。
4. ⚠️ **不要外部编辑** `codex_model_providers.json` / `cockpit-model-catalog.json`：
   它们是 app 合成领域，外部改会与运行中的 app 打拉锯战。

**可选加固：给桌面一把独立客户端 key。** `public_gateway` 模式下桌面会持有公网网关
key；如果希望它能独立轮换（不动其他消费方），在 CPA `config.yaml` 的 `api-keys` 里
**追加**一把新 key，再在 UI 里把桌面那条 provider 的 key 换成新的：

- ⚠️ **只能追加，不能改 `api-keys[0]`**：guardrails 多处读 `config["api-keys"][0]`
  （`cpa_bwg_guardrails.ps1:1308` 等），改第一位会连带影响探针与契约断言。
- ⚠️ `api-keys` **不在 `-Apply` 的投影范围**（manifest 只描述 provider 路由）⇒
  这是一次手工远端编辑，必须按 `docs/runbooks/cpa-manual-rollback.md` 备份，
  改完 `docker restart cli-proxy-api`，再复跑 strict doctor。
- 轮换流程见 `cpa-ban-throttle-incident-response.md` 的「双 key 窗口」——
  迁移期新旧 key 同时有效，才不会产生 401 风暴触发 fail2ban 自伤封禁。
- **不要只做一半**：新 key 加上但桌面没切，等于凭空多一把无人使用的有效凭据，
  比不做更差。

配套的人工判据是 `outputs/verify-sidecar-10909.sh`（运行时视角，结论行 `10909: OPEN|CLOSED`）。
**三者判据不同**：本脚本回答"配置形态是否会被拉起 + 目录能不能被服务"，那个脚本回答
"现在是否在跑"。改过 key 或改过桌面指向后都要跑。

### 已记录的复发路径

在「模型供应商」页编辑 **`fq.sciman.top`** 或 **`CPA (local 10909)`** 的 API Key，都会走
`CodexModelProviderManager.tsx:2544-2588` 的联动更新，其中 :2559 把 `api_provider_mode`
重算为 `isOpenAIOfficial ? "openai_builtin" : "custom"`。被绑定账号因此不再满足
`account_requires_provider_gateway`，10909 静默停止——**app 日志里没有任何错误**。
（桌面走 `public_gateway` 时这一步不再影响桌面可用性，但仍会停掉 10909 与其他消费方。）

不要删除这两个 provider 条目，理由与桌面指向哪个网关无关：

- 两把 key 都由这两条条目持有，删掉任一条就等于让对应凭据失去管理入口——
  绑定账号的 id 是 `md5(api_key)`，凭据从条目里消失后绑定会**错位**，
  而 `cockpit_provider_health.py` 的 `binding-strands` 会立刻报出来。
- `fq.sciman.top` 那条的 key 就是桌面在 `public_gateway` 模式下使用的那把；
  `CPA (local 10909)` 那条持有本地 sidecar key，切回 `local_gateway` 时要靠它。
- 删除本身也有引用保护（`handleDeleteProvider` 在 `providerReferenceCount > 0` 时拒绝）。

## 源码定案：10909 / 14185 的启动开关（从 MEMORY.md 外移，2026-10-04）

- **14185（API 服务）**：`local_access_gateway_should_run()` =
  `collection.enabled || internal_api_service_required()`
  （`codex_local_access_foundation.rs:119`）⇒ 启用就一直起。
- **10909（Provider Gateway）**：入口
  `commands/codex_instance.rs:410-467 ensure_provider_gateway_for_bind_account`；
  判定 `account_requires_provider_gateway()`
  （`codex_local_access_provider_gateway.rs:1307`）三条件之一：
  `is_grok_upstream_provider` / `is_chat_completions_api_key_account`
  （API key + `wire_api==chat_completions`）/ `account_uses_synced_model_shell_gateway`
  （API key + `api_provider_mode==Custom` + `api_sync_model_catalog_to_codex`）。
- **日志判据**：`[Codex Start] default provider gateway phase finished: elapsed_ms=1`
  且无后续 `[provider-gateway] sidecar 已启动: … bind=127.0.0.1:10909` = 本次没起 10909。
  **10909 的启动日志只写 `logs/codex-api.log.<date>`（tag `[provider-gateway]`），
  app.log 里搜不到**——用 app.log 判有无会得出错误的"从未启动"。
- **触发 10909 停止的真实路径**：编辑 provider API Key →
  `CodexModelProviderManager.tsx:2544-2588` 联动 `updateCodexApiKeyCredentials` →
  `apiProviderMode` 重算为 `isOpenAIOfficial ? "openai_builtin" : "custom"`（:2559）
  ⇒ 判定不再满足 ⇒ 10909 停。**账号 id = `md5(api_key)`**（`codex_account_provider.rs:450`）
  ⇒ 换 key 必换 id，旧绑定失效。签名：`Codex API Key 账号凭据已更新: old_id=… new_id=…`。
- ❌ `restart_local_access_sidecar`（`codex_local_access_commands.rs:1344`）**只重启 14185**
  ⇒ UI「重启 API 服务」帮不上 10909。能拉起 10909 的只有
  `activate_provider_gateway_after_switch_if_needed`（切号时）与
  `ensure_provider_gateway_for_dir`（实例启动时）。
- **没有「sidecar 自动重启配额耗尽」这回事**：
  `sidecar_usage_event_should_auto_restart`（`codex_local_access_sidecar_runtime.rs:63-112`）
  只在超时类失败触发，窗口 10min 最多 3 次；app.log 全量命中 0。**别再往这个方向归因。**
- **停用路径成功时静默**：`stop_provider_gateways_for_profile_locked` 只在失败时 warn
  ⇒ 判据翻转在日志平面零痕迹，只能靠配置形态检查发现。
- ⚠️ `codex_549794138e26af5d101f2307cdce7f41`（OAuth, plus）走 14185，**不会**拉起 10909。

### 「获取上游模型失败」两种形态的精确出处

- 标签 `PROVIDER_MODELS_HTTP_{}` 出自 **Cockpit 自身**
  （`codex_model_provider_commands.rs:1762`）；401 响应体出自 **Cockpit 自己的 sidecar**
  （`provider_gateway.go:41,67 requireAPIKey`）；UI 文案
  （`useCodexAccountsAccessController.tsx:2124-2150`）用的是**当前输入框的 `apiKeyInput`**。
- **`HTTP_401` = key 挂错条目**：10909 只认它自己 `api-keys` 里的本地 key。
  选择态默认 `provider.apiKeys[0]`（`CodexModelProviderManager.tsx:1249-1256`），
  是组件内存态、重启回默认。
- **`HTTP_503` 真因 = 10909 根本没在运行**（不是 key / config / 自动重启配额）：
  UI 请求被拒（`WinError 10061`）→ 渲染成 503。
- ❌ **不要删除 `CPA (local 10909)` 条目**：桌面 `config.toml` 可能指向它，
  且 `handleDeleteProvider` 有引用保护（`providerReferenceMap > 0` 即拒绝）。

## 本机闸门（Cockpit 本地并发等待）的源码定案

- `latency` 恰为「等待预算」的 429 = **Cockpit 本地闸门超时**，不是 admission。
  r2 补丁 `provider_gateway_concurrency.go::admitDirectProviderAccount`：
  本地并发 > `manifest.maxAccountConcurrency` → 等 `AccountConcurrencyWaitMs` → 超时
  → 429 + `Retry-After: 1`。判别：**同一时刻 nginx 429 = 0**。
- 两参数是 UI 设置（`codex_local_access.json`）。**两个 sidecar 生效路径不同**：
  API 服务 sidecar 直读真 collection（用户设置生效）；provider gateway sidecar
  **读不到**（`build_provider_gateway_collection_for_profile:1837` 从
  `new_empty_local_access_collection()` 起步）⇒ 恒为结构体默认，
  **改 settings 文件修不到它，必须二进制兜底**。
- 2026-10-02 落地：① 真 collection wait 120000→**45000**；
  ② **r3 二进制入口 `capAccountConcurrencyWaitMs` 封顶 45000ms**
  （SHA `72860fd9…`，r2 备份 `.v135-gate-r2-20261002.bak`）；
  `maxAccountConcurrency` 保持 3。**受控对照验收 `ACCEPTANCE_PASS`**。
- ⚠️ **重启只重新生成 API 服务 sidecar 的 manifest**，provider gateway 的仍是旧的
  ⇒ **不能靠"重启后看文件值"验收**。
- 45s 指纹从"约 4 次/天"升到"4 次/小时"是上游变慢所致，**不是 r3 引入的**；
  此时**不要**调大 `maxAccountConcurrency`。
