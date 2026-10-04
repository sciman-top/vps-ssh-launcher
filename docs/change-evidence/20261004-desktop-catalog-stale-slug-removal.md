# 桌面模型目录清理：移除不可路由的 gpt-5.5 / gpt-5.6-sol

**时间**：2026-10-04 09:12 CST  
**背景**：全面深度审查发现，`cockpit_provider_health.py` 的第四条不变量检查  
（"桌面目录可路由"）报告 `gpt-5.5` 和 `gpt-5.6-sol` 在桌面模型选择器中可选，  
但 `fq.sciman.top` 公网网关的路由清单（`cpa_provider_routes.json`）不路由这两个名字  
（同时列在 `oauth_exclusions` 和 `codex_api_key_exclusions` 中）。用户决策：从目录移除。

## 改动

- `~/.codex/cockpit-model-catalog.json`：移除 `gpt-5.5`、`gpt-5.6-sol` 两个 model 对象  
  （11 → 9 个 slug）
- `~/.antigravity_cockpit/codex_model_providers.json`：从 4 个 provider 条目的  
  `modelCatalog` 字段中移除同名 slug，共 7 处（35.213.82.91 ×2、ai.input.im ×1、  
  codex.ciii.club ×2、CPA local 10909 ×2）

## 备份

- `cockpit-model-catalog.json.before-rm-gpt55-sol-20261004.bak`
- `codex_model_providers.json.before-rm-gpt55-sol-20261004.bak`

## 验收

`cockpit_provider_health.py` 退出码 0，输出：  
`✓ 未发现配置层面的已知故障形态`（桌面目标模式 = `public_gateway`）

## 边界

- 这两个文件由 Cockpit app 管理；Cockpit 重启后 `cockpit-model-catalog.json`  
  可能被重新投影。重新投影后如需持久化，须在 UI 里删除对应 provider catalog 条目。
- 不触及 CPA 侧路由（`config.yaml`、`cpa_provider_routes.json`），无需 `-Apply`。
- 无远端写入，无 OAuth 凭据操作。

## 2026-10-04 09:31 CST 复验与重投影收口

首次记录的文件状态在 Cockpit 运行期间仍可被旧投影恢复。本轮按用户明确要求，
在不停止 `cockpit-tools` / `cockpit-cliproxy` 的情况下先备份，再原子更新两个
Cockpit 管理文件：

- `~/.codex/cockpit-model-catalog.json`：`11 → 9` 个模型；两个旧 slug 均不存在。
- `~/.antigravity_cockpit/codex_model_providers.json`：4 条 provider 的 `modelCatalog`
  共移除 7 个旧 slug；`fq.sciman.top` 原始目录本来就没有这两个名称。
- 本轮备份：
  `cockpit-model-catalog.json.before-user-rm-gpt55-sol-20261004-093152.bak`、
  `codex_model_providers.json.before-user-rm-gpt55-sol-20261004-093152.bak`。

等待 3 秒后再次读取，两个文件均未被运行中的 Cockpit 重投影恢复；随后
`cockpit_provider_health.py` 公网模式退出码为 `0`，55 个 provider-health 单测、
全仓 `run_gates.ps1`（404 passed, 1 skipped, 312 subtests passed）均通过。

## 2026-10-04 15:08 CST 二轮：重投影造出 slug/显示名错位壳（用户报 Sol 91 选中即 400）

13:20:45 Cockpit 从内部状态重投影两个文件，产生两类回退：

- 全局 `cockpit-model-catalog.json` 变 14 slug：退役名 `gpt-5.6-sol`、`gpt-5.5`
  回归，且各挂**错误显示名**成壳（`slug=gpt-5.6-sol` 挂 display
  `gpt-6.1-sol-91`；`slug=gpt-5.5` 挂 display `glm-5.3`）——UI 选显示名、
  请求带退役 slug，CPA 墓碑 fail-closed 报
  `unknown provider for model gpt-5.6-sol`；壳 payload 为旧模型 GPT-5 时代
  文案，不可改名沿用。
- `CPA (local 10909)` 条目恢复到 13 slug（下午清理被回滚）；`fq.sciman.top`
  条目反而重同步为当前 13 ID 全集（无图像名）。

### 处置（文件级，备份 `*.before-fix-shells-20261004-1508.bak`）

- `slug=gpt-5.6-sol` 壳 → 改正为 `gpt-6.1-sol-91`，payload 采用同上游模型
  `gpt-6.1-sol` 条目（槽位 3 与 OAuth 同为上游 gpt-6.1-sol）。
- `slug=gpt-5.5` 壳（display glm-5.3）→ 删除（无同源 payload；gpt-5.5 已按
  用户决策退役）。正式 glm-5.3 条目待 UI 重建目录后恢复，glm-5.3-flash 不受影响。
- local-10909 条目再清 `gpt-6-astra` / `gpt-6-astra-cii` / `gpt-image-2.5`
 （provider-health 判决 + 退役决策）。

### 验收与边界

- 两文件写后 3 秒未被回读恢复；`cockpit_provider_health.py
  --check-all-providers` 退出码 0「未发现配置层面的已知故障形态」。
- 公网网关实查（只读 /v1/models，key 取自 config.toml 未回显）：当时 9 ID
  在册（上游可用性正常涨落），`gpt-6.1-sol-91` 在册可路由、`gpt-5.6-sol`
  墓碑不在。
- **持久性边界（与 09:12 轮同）**：文件级修复抗不住 app 下一次从内部状态
  重投影（13:20 已实证一次回滚）。持久解 = 在 Cockpit UI 里重建/重拉模型
  目录并删除 gpt-5.5 / gpt-5.6-sol 旧条目（唯一写入 app 内部状态的路径）。

## 2026-10-04 19:40 CST 三轮：重启后回归定案 + 只读强制根治

重启电脑 → app 重启 → 19:33 从内部状态重投影，壳条目回归且配对再次漂移
（`gpt-5.5`→冒充 Sol 91、`gpt-5.6-sol`→冒充 sol-ciii、`gpt-5.6-luna`→冒充
deepseek-v4.1-flash 共 3 壳），证实文件级打地鼠不可持续。内部污染源定位到
`codex_local_access_stats.json` 的 `models` 数组 = 历史使用过的全部 modelId
累计清单（含大量退役名），app 投影时以内部清单 × 最新显示名做错位配对。

### 根治手段

- 从 `before-fix-shells` 备份重建 13 条正确目录（同 15:08 变换）。
- `attrib +R` 只读强制：实测 `os.replace` 到只读目标被 PermissionError 拦截；
  app 19:33:36 的重投影未能改变内容（13 slug 零壳复核通过）。
- local-10909 条目再次清理；provider-health 剩余两条 local_gateway 告警
  （gpt-6.1-sol "不可路由"）与用户实测矛盾（5.6-luna/sol 经 sidecar 均 200），
  判定为该 oracle 对 local_gateway 集合偏保守，不追逐。

### 同步发现

- app 同时把桌面 provider 绑定从公网直连回滚到 `localhost:10909` sidecar
 （config.toml 19:33 重投影），10/4 早前定案的 public_gateway 终态被回归；
  如需恢复，在 UI 里重选 fq.sciman.top 为账号 provider（外部改 config.toml
  会与运行中 app 拉锯）。
- 回滚只读强制：`attrib -r <catalog>`；备份
  `cockpit-model-catalog.json.before-readonly-rebuild-20261004-1730.bak`。

## 2026-10-04 19:55 CST 四轮：10909 本地闸门持久禁用（用户决策"彻底禁用"）

按 provider-health 既有判据（绑定账号不带 `__provider_gateway__:` 前缀 ⇒
10909 不会启动），把内部状态与 config.toml 一次对齐到公网直连终态：

- `~/.antigravity_cockpit/codex_instances.json`：
  `defaultSettings.bindAccountId` 去掉 `__provider_gateway__:` 前缀
 （`codex_apikey_ec280ff6…`，归属 fq.sciman.top cmk_1791017330289_1）。
- `~/.codex/config.toml`：`model_provider = "fq_sciman_top"`（公网直连，
  Responses 原生 + agt_gw key，即 10/4 早前定案终态）。
- 备份：两文件 `*.before-direct-20261004-1950.bak`；写后 3 秒未被运行中
  app 回写。
- 复验：绑定/key 归属/网关前缀三项全对，退出码 0。工具"桌面目标模式=
  local_gateway"一行系读取已废弃的 `codex_local_access` 段（当前无消费者），
  下次 app 重启按无前缀绑定重投影后自愈对齐。
- 在跑的 10909 sidecar 进程不动（当前会话零中断）；重启后不再拉起。
- 目录只读强制保持：直连模式"目录必须与网关可路由集合一致"，13 条目录
  即路由真源全集。
- 回滚：恢复备份，或在 UI 里把账号 provider 重选为网关模式。
