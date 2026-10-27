# Codex desktop 模型增删/上下文变更 Runbook

适用范围：让 ChatGPT desktop（Codex desktop）picker 里**增删模型**或**改上下文钉值**。
本文件覆盖本地链路；服务端 CPA 目录（`/v1/models` 内容）变更先走
[cpa-catalog-change-checklist.md](cpa-catalog-change-checklist.md)。
2026-10-26 实测定案：cockpit GUI 的"从上游获取+保存"**不能可靠刷新**
desktop 读到的模型集（保存只写 `~/.cockpit_tools/codex_model_providers.json`
的旧快照），本地注入是已验证的确定性路径。

## 四层链路（自上而下）

| 层 | 真源 | 动作 |
| --- | --- | --- |
| ① 服务端目录 | `scripts/remote/cpa_provider_routes.json` | 按 catalog checklist：manifest → full gates → commit → `-Apply` → strict doctor |
| ② 本地上下文钉值 | `config/codex-model-context-overrides.json` | 加/改条目（三字段：`context_window`/`max_context_window`/`auto_compact_token_limit`；compact=90% 上下文，272000→244800、1000000→900000、1048576→943718） |
| ③ 本地基底目录 | `~/.codex/fq-model-context-catalog.json` | 模型必须先在基底里存在，否则 sync 脚本 fail-closed（`found 0`） |
| ④ desktop 加载 | config.toml `model_catalog_json` 指针 | **托盘全退重启**（无应用内刷新；会话内 picker 不重读） |

## 增加模型（desktop 要看到新名）

1. ①服务端（若是新模型）：按 checklist 完成 `-Apply`+doctor。
2. ②overrides 加条目（上下文值与 manifest `max-context-length` 一致）。
3. ③基底检查：`CODEX_HOME=~/.codex codex debug models | grep <slug>`。
   缺失则**克隆注入**：备份 fq 文件 → 复制同通道现有条目（如
   `deepseek-v4.1-flash`）→ 改 `slug`/`display_name`/`description` + 三个
   上下文字段 → 写回。条目本就是合成物，克隆=生成器等价物。
4. 跑 `pwsh -NoProfile -File scripts/sync-codex-model-context.ps1`：
   应输出 `SYNC_RESULT=...` 且 `MODEL_CONTEXTS` 含新 slug；fail-closed
   报 `found 0` = 基底缺模型，回到第 3 步。
5. 告知用户托盘全退重启 desktop。

## 删除模型

1. ①服务端：按 checklist 退役（排除清单墓碑规则见 checklist）。
2. ②overrides 删条目、③基底删条目（先备份 fq 文件）——**两处都删**，
   否则 sync 脚本对"overrides 有、基底无"fail-closed。
3. 重跑 sync 脚本确认 `MODEL_CONTEXTS` 不再含该 slug。
4. 重启 desktop，picker 条目消失。

## 只改上下文钉值

模型已在基底里时：改 ②overrides → 重跑 sync 脚本（自动覆写三字段）→
重启 desktop。服务端目录的 context 值（manifest）同值同步另走 ①checklist。

## 回滚与坑

- 回滚：拷回 fq 文件旁的 `*.bak`（sync 脚本与手工注入都会留备份），重启。
- 指针必须保持 `model_catalog_json = "fq-model-context-catalog.json"`；
  cockpit 若翻回自己的目录文件会丢钉值，重跑 sync 脚本即指回。
- sync 脚本要求 `codex` CLI 可用（`codex debug models`）。
- 克隆条目沿用源模型的能力标注（推理档位等）；上游不支持时表现为调用
  报错而非显示异常，改对应字段即可。
- cockpit provider 列表旧快照（含墓碑名）不影响 desktop，属卫生项。
- 自管目录是静态快照：上游模型清单变化不会自动跟随，新模型一律走
  "增加模型"第 3 步克隆注入后重跑 sync 脚本。
- （2026-10-27 线索，本机无该 checkout 未核证）cockpit 源码称检测到用户
  自定义 `model_catalog_json` 时跳过受管目录重建、清理只触自管目录文件
  （`D:\CODE\external\cockpit-tools` 的
  `src-tauri/src/modules/codex_account_model_catalog.rs` :2788/:2940）；
  在检出该仓的机器核实成立后，可把上面"cockpit 翻回目录"风险降级为
  卫生项。
