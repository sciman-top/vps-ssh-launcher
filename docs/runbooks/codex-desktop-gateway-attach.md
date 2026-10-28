# Codex desktop 网关车道按机 attach Runbook

适用：新机部署、重装、或目录链漂移后，把本机 ChatGPT desktop（Cockpit 托管）
收敛到项目标准形态。机制背景与目录变更流程见
[codex-model-catalog-change.md](codex-model-catalog-change.md)；钉值真源是
`config/codex-model-context-overrides.json`。参考机 2026-10-28 实测收敛。

## 标准形态（目标态）

- **车道**：fq.sciman.top 公网网关 API-key attach（desktop 经 CPA，OAuth 账号
  单一消费方；desktop 直接 OAuth 登录同一账号=第二消费者 P0，仅分时段临时用）。
- **config.toml 钉值**（顶层）：`model_context_window = 1000000`、
  `model_auto_compact_token_limit = 900000`、`request_max_retries = 0`、
  `stream_max_retries = 0`（codex 5xx 不读 Retry-After，重试只放大故障）。
- **实验开关**：cockpit「当前 Codex 配置」勾选 experimental_mode = true（写
  `[features.context_management]`，完全重启 desktop 生效）；「上下文管理」
  实例缺省 1M/900K；「模型管理」开启。
- **实例清单**（模型管理列表，值=manifest；CPA 暂不提供的模型保留行，
  回归后自动跟随）：gpt-6.1-sol 500K/450K；gpt-6.1-sol-input、glm-5.3、
  glm-5.3-flash、deepseek-v4.1-flash、gemini-3.8-flash-high 1M/900K（按需）；
  gpt-6-astra、gpt-6-luna、gpt-5.6-luna、gpt-reserve、gpt-6.1-terra、
  gpt-5.6-terra 272K/244,800。默认模型按操作者偏好（参考机为 gpt-6-luna）。
- **指针**：`model_catalog_json` = cockpit 托管名。cockpit 1.3.74（generator
  7）写 `cockpit-model-catalog.json`；1.3.75（generator 8）改名
  `cockpit-native-speed-models.json`（2026-10-28 第二机实测）。两代托管名均
  合规，verifier 双认；旧静态名仍是 FAIL。

## 坑位（2026-10-28 参考机实证，cockpit 1.3.74）

1. **网关 attach 翻指针**：`[Codex Switch]` 到 `codex_apikey_*` 会把
   `model_catalog_json` 钉回退役静态快照 `fq-model-context-catalog.json`
   （sync 脚本已死，该文件永不再更新=目录冻结）。每次 attach 后必查指针；
   翻回则改回托管名并再切道一次。普通 launch 的 sanitize 不改写指针。
2. **重建数据源=cockpit 供应商快照**：attach 前先在供应商管理器对该网关
   "从上游获取+保存"刷新 `modelCatalog`，否则已退役模型（如 beam-free）
   会借尸还魂。
3. **实例清单外的模型按官方内置模板套值**（如 gpt-5.6-terra max=872000 →
   verify MISMATCH）：需钉值的模型必须都在实例清单里。
4. **meta 版本戳**：`cockpit-model-catalog.meta.json` 与文件 hash 不一致会
   触发启动期无窗口重建；勿手改 meta，切道重建即一致。
5. cockpit 运行中勿手改 `codex_model_providers.json`；`account_store.sqlite`
   凭据列是加密信封勿碰；`-Apply` 直修已退役（git 16e547f 可找回），实例 UI
   是唯一编辑器。
6. 旧会话跨车道续发报 rustponsesapi / invalid_id（rs_A:rs_B）= Codex Desktop
   缺陷（openai/codex #57633、#52052），新会话即解，勿按配置故障排查。

## 验收（只读）

```powershell
pwsh -NoProfile -File scripts/verify-codex-model-context.ps1
```

- 期望 `LANE_CATALOG=cockpit-managed` + `CONTEXT_VERIFY=PASS`；CPA 目录波动
  期间 gpt-6.1-sol / gpt-reserve 可 `CONTEXT_SKIPPED`（#5639/#5770，自愈）。
- 另核：指针=托管名；meta hash 一致；config 四钉值在位；desktop picker 可见
  gpt-6.1-terra（272K）。
- runbook 勘误或新坑位按两机闭环（fetch→review→integrate→work→focused
  verify→commit→…→push）回写本仓；本机桌面配置本身不入库。
