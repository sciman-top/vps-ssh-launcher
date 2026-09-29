# 决策简报：CPA 流式慢速（GLM 结论的独立核验 + 待你拍板项）

2026-09-29 · 核验人：本会话（与 GLM 会话无共享上下文，全部独立复测）
基准：`main @ add058e`（工作树干净，除并行会话的 `.tmp_*.ps1`）

---

## 1. GLM 结论逐条独立核验

| # | GLM 断言 | 我的核验 | 判定 |
|---|---|---|---|
| 1 | 主因＝本机 `writeProviderGatewayResponsesStream` 从不 Flush | 本会话在收到 GLM 报告**之前**已独立定位到同一行（`provider_gateway.go:636-652`），并用 nginx 侧 `upstream_header_time` 0.531–2.154 s 对本地 `headers_ms` 6985–22507 ms 做交叉验证 | ✅ **确认**（两条独立路径互证） |
| 1b | `chat_completions` 绕行更糟 | 实测网关侧首字节 10.685 / 14.613 s（chat 格式不渲染握手事件，CPA 合法扣头到首 token）；隔离实例复测同样 `headers ≈ total` | ✅ **确认** |
| 2 | 7373 的 `stream-bootstrap-buffering` 被源码钉死 true | `codex_local_access_sidecar_config.rs:2419`；Sep 27 历史备份同样是 true；当前 7373 无监听、`auths/` 空、`enabled:false` | ✅ **确认** |
| 3 | 上游高峰：3h 容量事件 21 次、冷却拒绝 29 次 | 我 24h 窗口：`capacity=true` 106、`lane_reject` 151（cooldown 93 / half_open_probe 46）、OAuth 200×1104 / 503×52 / 502×25 | ✅ **量级一致**（窗口不同） |
| 4 | `model_reasoning_effort="max"` 显著放大 | 文件确认 `max` + 子代理 `xhigh` ✅；影响见 §2 | ⚠️ **事实确认，量级见下** |
| 5 | 10909 失效别名「可摘」 | 持久源＝`codex_model_providers.json` 的 `modelCatalog`；但桌面几乎不用这些别名（`gpt-5.5` 仅 3/2700 次） | ⚠️ **可摘但收益小** |
| 6 | `proxy-url=10808` 纯绕行「可摘」 | 绕行成立（xray 出口确为 `vless → fq.sciman.top:443`）；**但来源是 Windows 系统代理** | ❌ **修正，不建议摘**（见 §3） |
| 7 | 曾把 `agt_gw` key 打进会话日志 | `git grep agt_` 在 **HEAD 与工作树零命中**；fq 前缀与客户端 IP 也不在 HEAD | ✅ **未进仓**，泄漏面限于本机会话日志 |

---

## 2. 我补测的量：`reasoning_effort` —— 方向支持，**量级不可信**

公网干净路径，`gpt-6-luna`，四组观测：

| # | 设计 | effort 顺序 | 首个可见 token | reasoning_tokens | 判定 |
|---|---|---|---|---|---|
| A | 简单题（口算），单次扫描 | medium→high→xhigh→max | — | 0 / 10 / 14 / 15 | 单调但绝对值极小 |
| B | 难题，单次 | medium → max | 5067 → **8452** ms | 115 → **341** | max 差 |
| C | 难题（换题），单次 | max → medium | 9729 → **15325** ms | 278 → **617** | **medium 反而差（矛盾）** |
| D | 同题成对交错，仅第 1 轮有效 | max → medium | **47792 → 22801** ms | **749 → 266** | max 差 ≈2.8× |
| D′ | 第 2 / 3 轮 | — | — | — | 被上游过载污染（503 `auth_unavailable` / 429 lane cooldown），**无有效读数** |

**结论（按证据强度如实表述）**：
- `reasoning.effort` **确实被透传**——A 组出现单调梯度（0→10→14→15）。
- **方向支持** GLM 的判断：在推理密集的题上，`max` 倾向产生 **2–3 倍推理 token**、
  首个可见 token 明显更晚（D 组 +25 s）。
- **但量级不可信**：C 组方向相反，轮间方差（上游负载）与 effort 效应混杂，
  D 组只有 **1 对**有效样本。**不要按"能省 X 秒"来预期。**
- 一条 GLM 没提的硬约束：`reasoning.effort = "minimal"` 会被上游直接 **400** 拒绝
  （`The following tools cannot be used with reasoning.effort 'minimal': image_gen`），
  所以桌面可选下限是 `low`，"改 medium/high" 落在可行区间内。
- **一条机制层面的提示**：D 组 `max` 的首个可见 token 达 **47.8 s**，已越过此前 doctor
  记录的桌面 ~45 s 放弃阈值（499 聚集在 45.0–45.05 s）。`max` 可能正是"转半天然后失败"
  的一个成因——这比"慢几秒"更值得注意。

**建议**：值得试，但按"试一周体感"来评估，不要按秒数预期。要拿可信数字，需要在
干净窗口（非晚间高峰）做更大的配对样本——本轮拿不到。

---

## 3. 对 GLM 一处建议的修正：`proxy-url` 不能摘

GLM 建议摘掉 10909 打 fq 的 `proxy-url=127.0.0.1:10808`。绕行的判断没错，但**来源不是 Cockpit 开关**：

```
HKCU:\...\Internet Settings → ProxyEnable=1  ProxyServer=127.0.0.1:10808
D:\TOOL\v2rayN\binConfigs\config.json → inbound mixed 10808
                                        outbound vless → fq.sciman.top:443
```

即 sidecar 继承的是 **Windows 系统代理**（v2rayN 系统代理模式）。要摘就得关掉全局系统代理，
会连带影响所有走系统代理的应用，且实测代价只有 **~180 ms**（1457 vs 1275 ms，中位轮 18 s 的 1%）。
**结论：不值得动。** 若真要动，应在 Cockpit 侧找 per-provider 代理开关，而不是关系统代理。

---

## 4. 待你拍板（我一项都没擅自改）

| 项 | 我的建议 | 操作位置 |
|---|---|---|
| **A. 上游报告提交** | 建议尽快提。渠道需你确认后自行发出（对外发布动作我不代提）。材料已备齐：`outputs/cockpit-tools-upstream-report-2026-09-29.md` + `outputs/cockpit-sidecar-sse-flush.patch`（`git apply --check -p1` 三种行尾均 rc=0） | Cockpit Tools 上游仓库 issue/PR |
| **B. 临时绕过** | 仅当你现在就要"能用"：Cockpit UI 把 desktop provider 切到直连条目，绕过 10909（今晚实测直连健康）。代价＝失去 sidecar 的模型目录管理。**手改 `config.toml` 会被 takeover 覆盖，别改** | Cockpit UI |
| **C. 降 `model_reasoning_effort`** | **方向支持、量级未定**（见 §2）：难题上 `max` 倾向 2–3× 推理 token、首 token 更晚，本轮最好的一对是 47.8 s → 22.8 s。但样本薄、有一组反例，请按"试一周体感"评估而非按秒数预期。`minimal` 不可用，下限 `low` | 桌面 UI 的 reasoning 选择器优先（`~/.codex/config.toml` 注释明说 Codex App 会重写 model/reasoning 选择） |
| **D. 清理死模型目录** | 收益小（别名命中率 3/2700；`deepseek-v4.1-flash` 243/72h 全失败）、且第三方中转可能恢复。**我倾向不做**；要做请点名 | `codex_model_providers.json` → fq 条目 `modelCatalog`（改后需重启 Cockpit 生效） |
| **E. `agt_gw` key 是否轮换** | 未出本机、未进仓，风险有限；但既然落过一次盘，轮换是最干净的收口。由你定 | Cockpit / VPS 两侧同步 |

---

## 5. 我刻意没有做的事（附理由）

- **没有改动任何 Cockpit 配置或桌面配置**：两个"可摘"项经核验分别是"收益 1% 且要动全局系统代理"
  和"命中率 0.1%"，不足以支撑在你在用的环境上做写操作；C 项是你的质量权衡。
- **没有重启 Cockpit / 没有动 Direct OAuth 账号池**：账号池正由并行会话处理
  （`enabled:false` / `accountIds:[]`），避免撞车。
- **没有删 27 个 `.tmp_*.ps1`**：那是并行会话的活跃脚手架（含它可能复用的 `apply_fq_wireapi`）。
- **没有 push `add058e`**：它的提交信息是并行会话写的，你可能想先改；推上去再改要 force-push。

---

## 6. 「打补丁」这条路值不值得走（2026-09-29 补测）

### 6.1 打补丁的持久性：**重启不覆盖，更新会覆盖**
| 问题 | 结论 | 证据 |
|---|---|---|
| Cockpit **重启**会覆盖补丁吗？ | **不会** | `cockpit-cliproxy.exe` 的 mtime 是 **09-29 19:37**；而 `server.json` 记录 Cockpit 本次启动 **21:19:55**（version 1.3.62），期间 sidecar 也重启过多次 —— mtime 始终未变。重启只是启动它，不重写 |
| 新版 Cockpit **安装**会覆盖吗？ | **会** | ① 本机 09-29 19:37 的更新把该 exe 换成官方版（`cockpit-tools.exe` 19:50 同步更新）；② 安装目录里躺着 `cockpit-cliproxy.exe.localpatch-20260906-2337.bak`、`.before-5_6-capabilities-20260710-135135.bak` —— **07-10 与 09-06 两次本地补丁都已被后续更新覆盖** |
| 更新是静默的吗？ | **不是** | `remote_config_cache.json` → `updatePrompt: {mode: "popup"}`，弹窗提示后才更新 ⇒ 你有机会先备份再更新 |

**补充风险**：补丁是**源码级 diff**，锚定 `v1.3.57-7-gdbe56a1e`。新版源码一旦变动，
`git apply` 可能不再干净命中，需要重新核对函数体 —— 也就是每次更新后都要重做一遍。

### 6.2 ⚠️ 本节结论已被推翻（2026-09-29 22:2x 更正）

**原结论**：直连模式可用，应优先切 UI 直连、不必打补丁。
**更正**：**直连模式对 fq 不可用** —— 那条路只对 DeepSeek 账号开放。原结论作废，
`docs/runbooks/cockpit-direct-mode-switch.md` 已撤回，改为
`docs/runbooks/cockpit-fq-access-mode-options.md`。

推翻它的两条证据：

```
CodexApiKeyLaunchSection.tsx:107
  const canChooseAccessMode = isDeepSeekResponsesAccount(account);   // ← 门控在这里

codexDeepSeekAccess.ts:84-96
  isDeepSeekAccount = (api_provider_id === "deepseek")
                   || api_base_url.includes("api.deepseek.com")
```

fq 的 `baseUrl` 是 `https://fq.sciman.top:8443/<prefix>/v1`、provider id 也不是 `deepseek`
⇒ `canChooseAccessMode = false` ⇒ 启动区只渲染**只读**的「协议 / 接入」，
那三个可点的模式按钮（网关列出 / 直连官方 / CDP 注入）**根本不出现**。

另外「接入方式」在 provider 表单里**也不是可编辑控件**：`enableModePreference` 是由
wireApi 芯片自动派生的（`CodexModelProviderManager.tsx:1779-1806`），表单里只做只读展示。
所以我此前说的「启用策略 → 接入方式 → 直连官方 API」是**错的**。

（能力矩阵本身没写错 —— `openai_responses_native` 确实是 `defaultEnableMode: "direct"`；
错在我把「能力存在」当成了「UI 可切换」。）

### 6.3 更正后的推荐顺序

1. **打补丁**（`outputs/cockpit-sidecar-sse-flush.patch`）——**这是 fq 场景下唯一的本机真修复**。
   有效，但**每次 Cockpit 更新后要重打**（本机 07-10、09-06 两次先例都被覆盖）。
   重打检测用现成命令：`PROBE_ASSERT=1` 跑 sidecar 探针，**exit 0 = 补丁在，exit 1 = 被覆盖**。
   构建/替换步骤见 `docs/runbooks/cockpit-sidecar-sse-flush.md`。
   ⚠️ 它与你此前「不要修改源码/构建、确保官方原版」的约束相左，需要你明确授权。
2. **提 issue/PR** —— 零维护、能真正终结这个补丁。材料已备齐。
3. **手改 `~/.codex/config.toml` 指向 fq** —— **不推荐**：
   `codex_account_model_catalog.rs:2398` 无条件写 `model_provider = "codex_local_access"`，
   下一次投影就覆盖（GLM 会话独立得出同一结论）。

## 7. 本轮新增的可执行验收

上游修复落地后（或补丁被更新覆盖后），一条命令判定（`PROBE_ASSERT=1`，退出码即结果）：

```bash
PROBE_ASSERT=1 PROBE_SIDECAR_KEY=<key> \
  ./.venv/Scripts/python.exe outputs/sse_framing_probe.py sidecar; echo "exit=$?"
```

阈值可用 `PROBE_MAX_HEADERS_MS`（默认 2000）/ `PROBE_MIN_READS`（默认 20）/
`PROBE_MIN_GAP_P50`（默认 1）覆盖。**修复前必返回非 0**（已自检），修复后返回 0。
新增 `outputs/reasoning_effort_probe.py` 用于复现 §2 的量。
