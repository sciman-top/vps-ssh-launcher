# Codex desktop 模型增删/上下文变更 Runbook

适用范围：让 ChatGPT desktop（Codex desktop）picker 里**增删模型**或**改上下文钉值**。
服务端 CPA 目录（`/v1/models` 内容）变更先走
[cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md)。

**2026-10-27 架构定案**：模型清单随 lane 由 cockpit 托管；上下文钉值由 cockpit
的每模型上下文窗口数据承载（显式值优先于内置模板，每次重建目录都应用）。
静态自管目录（`fq-model-context-catalog.json`）与
`sync-codex-model-context.ps1` 已退役——静态指针会让 cockpit 跳过受管目录
重建（v1.3.75 `codex_account_model_catalog.rs` 用户指针 gate），清单冻结在
快照上不再跟随 lane，这正是 2026-10-25~27 "切 lane 后列表不变"的根因。

## 四层链路（自上而下）

| 层 | 真源 | 动作 |
| --- | --- | --- |
| ① 服务端目录 | CPA `/v1/models` | 按 catalog checklist：manifest → full gates → commit → `-Apply` → strict doctor |
| ② cockpit 供应商快照 | `~/.cockpit_tools/codex_model_providers.json` | 供应商管理器"从上游获取+保存"刷新 `modelCatalog` |
| ③ cockpit 上下文钉值 | 供应商记录 `modelContextWindows`（账户侧同名字段 `api_model_context_windows`） | 改 `config/codex-model-context-overrides.json` 后同值写入 cockpit |
| ④ desktop 加载 | cockpit 受管 `cockpit-model-catalog.json`（指针由 cockpit 写） | OAuth lane 无目录文件 → codex 内置账号清单，上下文=官方内置值 |

上下文三字段（`context_window`/`max_context_window`/`auto_compact_token_limit`）
由 cockpit 按窗口值统一生成，compact=90%（272000→244800、1000000→900000、
1048576→943718），与 manifest 语义一致。

## 增加模型（desktop 要看到新名）

1. ①服务端（若是新模型）：按 checklist 完成 `-Apply`+doctor。
2. ②cockpit 供应商管理器对该供应商"从上游获取+保存"，`modelCatalog` 出现新 slug。
3. ③若需钉上下文：`config/codex-model-context-overrides.json` 加条目，并在
   cockpit 同一编辑界面填该模型的上下文窗口值。
4. cockpit 里切到该供应商 lane（受管目录重建）→ 跑校验（见下）→ 托盘全退重启 desktop。

## 删除模型

1. ①服务端按 checklist 退役（排除清单墓碑规则见 checklist）。
2. ②cockpit 供应商管理器里删除该模型并保存；③若 manifest 有条目则同批删除。
3. 切换 lane 重建受管目录 → 校验 → 重启 desktop。

## 只改上下文钉值（2026-10-27 深夜 as-built v2）

cockpit 1.3.75 **没有**每模型上下文窗口的可用 UI，且**账户保存路径不携带
窗口**（GUI 代理执行实证：chip 种子+保存+切换并启动后目录仍为回落值）。
双层方案：

- **持久缺省（免维护）**：`~/.codex/config.toml` 的
  `model_context_window = 1000000`——cockpit 每次重建把它当非官方槽位的
  缺省窗口，重建后 glm-5.3/glm-5.3-flash/deepseek-v4.1-flash 自动 =
  1M/900K（精确达标），gemini/beam-free/sol-input ≈1M，官方壳位保持
  官方值。这行不删，漂移下限就是 1M 而非 128K。
- **精确对齐（按需）**：
  `pwsh -NoProfile -File scripts/verify-codex-model-context.ps1 -Apply`
  —— 按 manifest 把 12 个模型修到精确三字段（sol/sol-input 500K、
  gemini/beam 1048576、272K 族 max=272000），随后自动复验。

流程：改 manifest → `-Apply` → `CONTEXT_VERIFY=PASS` → 托盘全退重启
desktop。**cockpit 重建事件（保存账户表单/切换并启动/切 lane）后钉值会
漂到缺省基线——校验 FAIL 时重跑 `-Apply`**（重建后 glm/deepseek 仍精确，
漂移只剩 8 个模型的精确度差异）。供应商记录 `modelContextWindows` 已预置
12 值，未来 cockpit 版本若渲染窗口表或修复账户保存丢字段的问题，即可
全自动。

## 校验（只读）

```powershell
pwsh -NoProfile -File scripts/verify-codex-model-context.ps1
```

对当前 lane 生效目录核对 manifest：`LANE_CATALOG=cockpit-managed` 时逐模型
核对三字段（lane 不提供的模型记 `CONTEXT_SKIPPED`，跨 lane 正常；值不符 FAIL）；
`LANE_CATALOG=builtin`（无指针，OAuth 内置 lane）时钉值不适用、整报告 SKIP；
config.toml 指向 `fq-model-context-catalog.json` 或其它外域路径直接 FAIL。
`CONTEXT_VERIFY=PASS` 后重启 desktop。

## 回滚与坑

- 2026-10-27 本机变更前备份：`~/.codex/config.toml.before-fq-pointer-retire-20261027-201236.bak`、
  `~/.cockpit_tools/codex_model_providers.json.before-context-pins-20261027-201236.bak`。
- 旧静态目录方案从 git 历史恢复：`git log -- scripts/sync-codex-model-context.ps1`；
  `~/.codex/fq-model-context-catalog.json` 文件本体未删除（不再被引用）。
- cockpit 运行中不要手改 `codex_model_providers.json`（UI 保存会整文件覆写，
  且保存会把窗口表剪裁到 modelCatalog 内的模型）；`account_store.sqlite`
  的凭据账户列是加密信封，勿手改；`cockpit-model-catalog.json` 直修后不要
  动 `cockpit-model-catalog.meta.json`（版本戳不匹配会触发启动期无窗口重建）。
- OAuth lane 不经 cockpit 目录：清单与上下文都是 codex 内置官方值，manifest
  钉值只约束提供了这些模型的 lane（校验脚本按 lane 跳过缺失模型）。
- sync 脚本时代的"克隆注入/基底 fail-closed"流程随脚本一并退役，勿再使用。
