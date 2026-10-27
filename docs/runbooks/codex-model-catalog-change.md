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

## 只改上下文钉值（2026-10-27 深夜终版：实验性模型管理=唯一权威机制）

**权威编辑器=cockpit「当前 Codex 配置」弹层**：开启
「experimental_mode = true（实验性上下文管理）」（写官方
`[features.context_management] experimental_mode`，schema 原文 "Enables
experimental context management."）+「模型管理」实例自管目录后，每模型
「上下文与压缩」列即唯一编辑入口；account 同步后
`enforce_experimental_model_policy_for_dir` 自动重套——**切号/重建自愈，
不需要任何外部补丁**。实例缺省窗口由「上下文管理」下拉给出
（落 `config.toml` `model_context_window`，现为 1M/900K，勿删）。

- 改值：该弹层模型列表（「管理」按钮加/删行）直接填；保存后
  `CONTEXT_VERIFY=PASS` 即生效（desktop 重启加载）。
- `verify-codex-model-context.ps1` 为**只读漂移检测**（manifest 对齐
  2026-10-27 用户配置后的 live 值：sol-input/gemini=1M/900K）；
  原 `-Apply` 修复模式已退役（git 16e547f 加入、同日退役，可从历史找回
  ——实验系统会按 UI 配置改写目录，外部直修会被覆盖，属无效补丁）。
- beam-free 当前不在实例清单（2026-10-27 用户要求移除）；manifest 不再保留其钉值，模型
  回归清单时自动生效。
- 勘误：sol 500K/450K 为用户在实例目录手动输入，非任何补丁种子。

### gpt-6.1-terra 上下文定案（2026-10-27）

**272000 输入窗口 / 244,800 压缩阈值（90%）= 当前最优配置，保持不动。**
依据三方一致：CPA 网关 OAuth 通道 clamp `max-context-length=272000`
（采纳日 manifest）；codex 官方内置目录 ctx=272000/max=872000；采纳决策
（20261027-bwg-cpa-gpt-6.1-terra-adoption.md）=上游未公布新名字的窗口，
取已验证的 terra 家族（gpt-5.6-terra, 272000），sol 家族 500K 的
client-template 证据 terra 不具备；"若日后证实更大，走本流程上调"。
社区侧本轮无可达来源（内置搜索配额至 2026-11-16）。

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
