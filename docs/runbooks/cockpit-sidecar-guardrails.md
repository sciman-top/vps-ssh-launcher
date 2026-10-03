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

配套的人工判据是 `outputs/verify-sidecar-10909.sh`（运行时视角，结论行 `10909: OPEN|CLOSED`）。
**三者判据不同**：本脚本回答"配置形态是否会被拉起 + 目录能不能被服务"，那个脚本回答
"现在是否在跑"。改过 key 或改过桌面指向后都要跑。

### 已记录的复发路径

在「模型供应商」页编辑 **`fq.sciman.top`** 或 **`CPA (local 10909)`** 的 API Key，都会走
`CodexModelProviderManager.tsx:2544-2588` 的联动更新，其中 :2559 把 `api_provider_mode`
重算为 `isOpenAIOfficial ? "openai_builtin" : "custom"`。被绑定账号因此不再满足
`account_requires_provider_gateway`，10909 静默停止——**app 日志里没有任何错误**。

不要删除这两个 provider 条目：桌面 `~/.codex/config.toml` 指向 10909，删掉条目会让这把 key
失去管理入口；而且删除有引用保护（`handleDeleteProvider` 在 `providerReferenceCount > 0` 时拒绝）。
