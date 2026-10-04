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
