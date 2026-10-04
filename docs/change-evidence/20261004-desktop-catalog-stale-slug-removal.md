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
