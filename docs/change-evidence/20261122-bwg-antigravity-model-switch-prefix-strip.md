# BWG Antigravity 前缀区 `<model_switch>` 剥离盲区修复（2026-11-22）

**目标**：根治 `c90edd1` 部署后仍残留的 Antigravity 多模型轮流裸 429 冷却（`Antigravity model X is cooling down; retry after 57s`）。`zz` 未触碰；未向 OAuth 车道发送合成生成探针；未改动任何用户 OAuth 凭据或 `cli-proxy-api` 配置。

## 根因实证（对 c90edd1 盲区的补全）

1. **故障指纹**：`2026-11-22 08:34–08:45Z` 同一会话（`X-Codex-Window-Id` = 交接的 Codex 线程）内 `neon`/`hydrogen`/`argon` 各吃一次 ~600ms 139B 裸 `429 RESOURCE_EXHAUSTED`，跨 `ai.sciman.top`、`b7177976`、`jalumuruvenkatarao` 三个账号；strict 车道首次容量信号即按 `cooldown_schedule_seconds[0]=60` 落模型级冷却，客户端重试连撞 `model_cooldown`（`retry after 57-59s`），体感为"各模型轮流 cooling down"。
2. **c90edd1 的位置守卫盲区**：该会话早期回合全部被用户中断（`<turn_aborted>`），没有产生 `assistant`/`function_call`/`reasoning` 输出，客户端每次切模型注入的 ~18–22 KB `<model_switch>` developer 项因此**全部堆积在对话前缀区**；而 `strip_antigravity_model_switch_items()` 只剥 `assistant`/`function_call`/`reasoning` **之后**的项。5 份新 error dump 结构证实：前缀区残留 1–5 条 `<model_switch>`（如 `164543` 样本：旧代仅剥 1 条、剩 5 条前缀项 → 仍 429）。
3. **对照组**：正常测试会话（`672b2d91a9e2`）的注入项都在 assistant 输出之后，`stripped=1..6` 恰等于切换次数，全部 200；该会话保留的 6 条 `<collaboration_mode>` 未触发拒单 → `<collaboration_mode>` 无罪，触发面唯一钉死在 `<model_switch>`。

## 仓库改动

- **`da65e31`** — `fix(cpa): strip prefix-region <model_switch> notices on antigravity`
  - `scripts/remote/cpa-admission.py`：`strip_antigravity_model_switch_items()` 去掉位置守卫，按标签在任意位置匹配剥离独立 `<model_switch>...</model_switch>` developer 项；首轮系统提示与 `<collaboration_mode>` 不带该标签自然透传，user 引用、未闭合、多 part developer 项仍按 `_model_switch_developer_text()` 纯文本条件排除。
  - `scripts/remote/cpa-admission-integrity-pin.txt`：更新为 `f70b874251a6f3f6eb44b83bca46818984095fac272e6b84963344b935356d29`。
  - `tests/test_cpa_admission.py`：新增 `test_strip_antigravity_model_switch_items_removes_prefix_notices_too`（前缀区剥离 + user 引用/未闭合/多 part 透传），并更名首轮行为测试。

## 远端投影与回滚备份（仅 `bwg`）

- 事务脚本：`outputs/deploy-admission-model-switch-prefix-20261122.ps1`
- 预演（`-DryRun`）：`STAGED_CONFIG_LOAD_OK`、`ADMISSION_INTEGRITY_OK`、`DRY_RUN_OK baseline=3b2cad8c... candidate=f70b8742... config=77b5f465... no_swap=true`
- 正式原子轮换：`REQUEST_GUARD_EXCLUSIVE_OK`、`ADMISSION_IDLE_OK`、`BACKUP_READY`、`ADMISSION_PIN_ROTATED old=3b2cad8c... new=f70b8742...`、`ADMISSION_MODEL_SWITCH_PREFIX_STRIP_LIVE_OK`、`DEPLOY_OK`
- 远端备份与回滚入口：`/opt/cliproxyapi/backups/20261122T090543.754815574Z-admission-model-switch-prefix-20261122`（还原 4 文件 + `systemctl restart cpa-admission` + `cpa-admission-integrity-check`）

## 分层验证证据

| 层级 | 执行内容 | 结果 |
| --- | --- | --- |
| `repo_verified` | `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/run_gates.ps1 -RunParallel` | `1858 passed, 30 skipped, 896 subtests passed`；`bandit`/`ruff check`/`ruff format`/`mypy` 全绿 |
| `filesystem_projected` + `host_loaded` | `scripts/cpa_bwg_guardrails.ps1 -Profile bwg` + `cpa-health.py status` | `DOCTOR_CONTRACT_OK`、`admission=OK`、`health=OK`、`POLICY_OK`、`HEALTH_OK` |
| 受控离线回放 | BWG 上加载 live `cpa-admission.py` 回放 5 份 429 error dump | 5/5 `tagged=N->0`：`163739` 剥 1（16→15 项，363842→345608B）、`163802` 剥 2、`164050` 剥 4、`164213` 剥 4、`164543` 剥 5（467515→372530B） |
| `live_accepted`（待用户自然复测） | 在先前"切模型即 429"的长会话内继续切模型 | 待用户桌面端复测（按风控原则未发 OAuth 合成探针） |

## 关联观察（不在本切片处理）

- `gemini-3.8-flash-high` "正在思考"卡顿：2026-11-22 06:00Z 起仅 `antigravity-knaha367` 账号 TTFB 47–65s（含一次 15s 零输出被客户端掐断），其余账号 flash 全部 2.4–2.8s。账号级上游降级，网关侧无法根治；是否将 `knaha367` 移出 flash 路由面属 OAuth 池操作，留待用户裁决。
