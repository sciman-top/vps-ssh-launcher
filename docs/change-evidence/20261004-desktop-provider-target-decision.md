# 桌面 Codex provider 指向变更的定案（2026-10-04）

## 范围与结论

- 范围：本机 `~/.codex/config.toml` 的桌面 provider 指向，以及仓库内对应的判据。
  未操作 bwg/zz 远端，未改 Cockpit 数据，未轮换凭据。
- 事实：2026-10-03 22:05（本地）该文件的 `[model_providers.codex_local_access]`
  `base_url` 由 `http://localhost:10909/v1` 改为
  `https://fq.sciman.top:8443/<16位hex>/v1`，并新增
  `[model_providers.fq_sciman_top]`（同 URL）。同期 `~/.codex/auth.json` 的
  `OPENAI_API_KEY` 由本地 sidecar key（`agt_codex_`，42 字符）变为公网网关 key
  （`agt_gw_`，39 字符），`config.toml` 出现两处同一 `experimental_bearer_token`。
  现场备份：`~/.codex/config.toml.before-fq-public-20261003-220557.bak`。
- 该变更**不在本仓任何提交或证据文件里**，属未记录的配置漂移；本轮把它定案。

## 为什么需要定案

既有不变量写的是"桌面整条链压在 10909 上"，`cockpit_provider_health.py` 因此把
"未指向 10909"直接渲染成一条 `⚠️ 别名重写层被绕过` 警告。警告是**陈述旧不变量**，
不是**检查新状态**，两边都失去意义：既不阻止真正的故障，也会长期噪声化。

## 决策：两种模式都接受，改为检查真正会坏的东西

| 模式 | 得到 | 失去 |
|---|---|---|
| `local_gateway`（10909） | 本地并发闸门、模型别名重写层；桌面只持有本地 key | 侧车静默停机（`account_requires_provider_gateway` 翻转）这一类故障，已造成多次 `Connection failed` / `PROVIDER_MODELS_HTTP_503` |
| `public_gateway`（公网 capability path） | 没有侧车静默停机 | 本地闸门与别名层；公网网关 key 落在桌面配置里 |

选择 `public_gateway` 是**有依据的取舍**：侧车静默停机的根因在 Cockpit
（编辑 provider key 重算 `api_provider_mode`，上游 issue #2702 仍未修），本地无法根治；
而本地闸门丢失的代价在实测里很小（24h 内 admission `queue_timeout` 仅 1 次，
本机 45s 闸门指纹由"约 4 次/小时"降到 1 次）。公网 key 原本就已以明文存在于
`~/.antigravity_cockpit/codex_model_providers.json` 与侧车 config，多一份副本不新增暴露类别。

## 仓库现在断言什么

`scripts/cockpit_provider_health.py` 把该警告替换为**结构化判据**：

- `classify_desktop_target()` 把 `base_url` 打成 `local_gateway` / `public_gateway` /
  `other` / `unknown`；前两者是接受态，`other`/`unknown` 只报 warn、不影响退出码。
- 新增第四条不变量 **桌面目录可路由**：桌面模型选择器是 provider 目录的**投影**，
  必须能被选中的网关解析。`local_gateway` 比 `manifest.json` 的
  `providerGateway.upstreamModels` ∪ `modelAliases` ∪ `modelIds`；
  `public_gateway` 比 `scripts/remote/cpa_provider_routes.json` 的 alias 集合。
  不匹配报 `desktop-model-unroutable`（error）。`DESKTOP_LOCAL_ONLY_MODELS`
  白名单收纳由 Cockpit 本地持有的名字（`codex-auto-review`）。

## 验证

- 现场读数（2026-10-04 00:1x，本机）：
  `桌面目标模式 = public_gateway`，
  `desktop-model-unroutable: gpt-5.5, gpt-5.6-sol` ⇒ **退出码 1**。
  这两个 slug 在 `~/.codex/cockpit-model-catalog.json` 里可选，但在公网网关的路由清单里
  不存在（`gpt-5.6-sol` / `gpt-5.5` 同时在 `oauth_exclusions` 与
  `codex_api_key_exclusions` 中）⇒ 选中即稳定 `400 model_not_found`。
- `test_cockpit_provider_health.py` **46 passed**（新增 12 个用例：目标分类、
  清单/目录/sidecar 读取、可路由性边界、白名单、`main` 端到端）。
- `ruff check` / `ruff format --check` / `mypy` 通过；`git diff --check` 通过。

## 未覆盖 / 边界

- **`gpt-5.5` 与 `gpt-5.6-sol` 的处置未做**：需要用户在 Cockpit UI 里决定"从目录移除"
  还是"加回可路由清单"（后者是路由变更，须走 `bwg-cpa-route-change` 闭环）。本文件只
  证明它们当前不可路由。
- `local_gateway` 模式下的可路由集合取自 10909 的 `manifest.json`；该文件受已知生成器
  缺陷影响可能落后于运行值（见 `cpa-failure-triage.md` 第 8 节），所以该分支的判据强度
  低于 `public_gateway` 分支。
- 未宣称 `natural_live_accepted`：桌面稳定性需在真实使用窗口继续观察。

## 2026-10-04 用户决策与增量定案（第二会话独立验证）

- **`gpt-6.1-sol-input` 保留不动**（用户决策）：slot1 上游恢复 + 容器重启即回归
  服务；窗口期失败如实透传，不做目录变更、不做远端写入。
- **桌面正式接受 `public_gateway` 直连**（用户按本会话推荐授权）：推荐依据 =
  一次性移除 10909 懒加载停机与本地 Xray 上游链两个本地单点（9/24 Xray 出口死亡
  曾整链拖垮），保护语义由远端 admission 等效承担（队列 + Retry-After 比本地 45s
  快败更温和），代价仅为高峰失败延迟 45s → 120s 排队；公网暴露面已有随机路径 +
  fail2ban + `limit_conn` 防线。CPA 服务端别名在 `config.yaml`，直连不受影响。
- **F6 闭合（本会话独立判别）**：远端 nginx `cpa_gateway.access.log` 在
  03/Oct/2026 06:00–06:05Z 的 `gpt-5.6-terra` 请求为 **0 条**，而 10909 sidecar
  日志同窗口有多条 terra `502`（慢败 ~4.5s）/`503`（快败 ~0.38s）——该批 5xx 为
  **sidecar 上游连接层本地合成**（未到达 CPA），非上游故障；10909 manifest 的
  `providerGateway.baseUrl` 实为公网 capability path（与桌面直连同源）。直连切换
  后该失败路径退出默认链路，不做进一步深挖。
- **`gpt-5.5` / `gpt-5.6-sol` 处置推荐**：优先在 Cockpit UI 重建桌面目录
  （modelCatalog 从公网网关 `/models` 重拉，两个退役名自然消失）。不推荐在 CPA
  侧加回：上游已无同名模型，任何 alias 重写都是误导性映射。10909 `manifest.json`
  的 `modelAliases`（`gpt-5.5`→`gpt-image-2.5`、`gpt-5.6-sol`→`deepseek-flash`）
  是 2026-09-25「旧模型名刻意保留」定案的实锤，仅 local 模式生效，保留不动。
  若 UI 重建后仍残留（目录 merge 语义合回），再启用
  `DESKTOP_LOCAL_ONLY_MODELS` 豁免并注明"已尝试修复的已知残留"。
- 边界：`cockpit-model-catalog.json` / `modelCatalog` /
  `codex_model_providers.json` 属 Cockpit app 合成领域，本会话未做外部编辑。

## 回滚

- 代码回滚：回滚本轮提交（`scripts/cockpit_provider_health.py`、
  `test_cockpit_provider_health.py`、`docs/runbooks/cockpit-sidecar-guardrails.md`）。
- 桌面指向回滚：把 `~/.codex/config.toml` 的 `codex_local_access.base_url` 改回
  `http://localhost:10909/v1`，`auth.json` 的 `OPENAI_API_KEY` 换回 `agt_codex_` 那把，
  然后按 `cockpit-sidecar-guardrails.md` 重载 sidecar（应用重启或切号，**禁止 taskkill**），
  并用 `outputs/verify-sidecar-10909.sh` 确认 `10909: OPEN`。
