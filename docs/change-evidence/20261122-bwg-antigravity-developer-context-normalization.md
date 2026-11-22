# BWG Antigravity 错位 `<app-context>` 提升与中置重复 developer 声明归一化（2026-11-22）

## 触发问题与根因

在历史旧图省略（`79d81b0`）上线后，原旧会话（`warm_fork` / 压缩后会话）于 20:04 使用 `gemini-4-hydrogen-xhigh` 自然复测仍返回裸 `429 RESOURCE_EXHAUSTED`（同分钟 BWG 上其他大上下文会话连续 `200 OK`，`usage_in` 达 `308,560` tokens，且同一账号组在 4 个 Antigravity 模型上仅该旧会话 0/15 全 429）。

对 `error-v1-responses-2026-11-22T200440-0170537e.log` 脱敏拆解并对照 `CLIProxyAPI` `gemini_openai-responses_request.go` 转换逻辑后，确认三处此前漏网的 `developer` 载荷结构：

1. **错位的 session-start `<app-context>` bundle 掉入 `contents`**：`warm_fork`/压缩把 5 条早期用户消息放在 `[00]`（base prompt）与 `[06]`（52.7 KB，含 `<app-context>`、Memory、34.2 KB `<skills_instructions>`、`<permissions>`、`<collaboration_mode>`、`<plugins_instructions>`、`<tools>`）之间；CPA 判定 `hasEncounteredConversation = true` 后将 `[06]` 整块包进 `<system-reminder>` 写入 `contents` user turn，而非 `systemInstruction`。
2. **多 part 的中置 `<skills_instructions>` 漏网**：`[29]`（23.6 KB：`<skills_instructions>` + `<collaboration_mode>`）与 `[62]`（37.0 KB：`<skills_instructions>` + `<collaboration_mode>` + `<plugins_instructions>`）因末尾不是 `</skills_instructions>`，绕过了 `strip_antigravity_model_switch_items()` 的整条匹配。
3. **中置重复状态声明堆积**：11 条独立 `<collaboration_mode>` 及重复的 `<plugins_instructions>` / `<codex_apps_client_time_context>` 与前缀重复并被折叠为多段 `contents` user turn。

## 仓库改动

- `a8f8591` — `fix(cpa): promote displaced app-context bundle and dedupe mid-session developer notices on antigravity`
- `scripts/remote/cpa-admission.py`
  - 新增 `normalize_antigravity_developer_context()`，并在 `strip_antigravity_model_switch_items()` 之后接入（仅作用于 `antigravity-oauth` `/responses`）。
  - 将落入首个非 `developer`/`system` 项之后的 `<app-context>` 多 part developer bundle 提升回前缀区（零信息损失，由 CPA 正常路由至 `systemInstruction`）。
  - 按 part 剥离中置 `developer` 项中的 `<skills_instructions>...</skills_instructions>`。
  - 对中置 `<collaboration_mode>`、`<plugins_instructions>`、`<codex_apps_client_time_context>` 按 tag 去重：与前缀完全一致的副本直接丢弃，仅当内容相对前缀发生变化（如切换 Plan 模式或日期更新）时保留最新一条。
  - 日志记录 `protocol_normalized model=... kind=antigravity_developer_context promoted_bundles=... stripped_skills_parts=... deduped_parts=...`，不输出正文。
- `tests/test_cpa_admission.py`
  - 新增 `warm_fork` 提升 + 多 part 剥离 + 去重回归测试、正常会话无改动测试、模式切换保留最新变更测试以及非 Antigravity 车道/路径豁免测试。
- `scripts/remote/cpa-admission-integrity-pin.txt`
  - 更新为 `28542fc75880c1d5216ac7eaa8bc578cb4ef6e1f7f646d93f25772da9da21069`。

## 分层验证

| 层级 | 证据 | 结果 |
| --- | --- | --- |
| `repo_verified` | `ruff check` + `pytest tests/test_cpa_admission.py -q` | `362 passed, 3 skipped` |
| `repo_verified` | Full 门禁 `scripts/run_gates.ps1 -RunParallel` | `1869 passed, 30 skipped, 896 subtests passed`；catalog/build/bandit/ruff/mypy 全绿 |
| 离线回放 | BWG 20:04 真实失败样本 `error-v1-responses-2026-11-22T200440-0170537e.log` | `promoted_bundles=1, stripped_skills_parts=2, deduped_parts=15`；`420,389 -> 342,456` bytes（`-77,933` bytes），`75 -> 61` items，中段仅余 1 条 412 bytes 最新时间声明 |
| `filesystem_projected` | `scripts/cpa_bwg_guardrails.ps1 -Apply` | 17 项 `PROJECTION_HASH_VERIFIED`、`HEALTH_OK`、`READY_STATUS=200`、`GUARDRAILS_APPLIED` |
| `host_loaded` | `scripts/cpa_bwg_guardrails.ps1` strict doctor | live hash `28542fc7...1069`、`admission-integrity=OK`、`admission-health=OK`、projection drift 全部 `MATCH`、`DOCTOR_CONTRACT_OK` |
| `live_accepted` | 用户在原旧会话自然复测 | 待自然复测；未发送 OAuth 合成生成探针 |

## 回滚

远端备份路径：

```text
/root/cpa-guardrails-backup-20261122T122658.315434152Z
```

回滚时从该备份恢复 `cpa-admission.py` 与 `cpa-admission.sha256`（或回退至上一提交后重跑 `scripts/cpa_bwg_guardrails.ps1 -Apply`），重启 `cpa-admission` 并复跑 strict doctor。

## 边界

- 未改动 `request-retry=0`、未清冷却、未轮换 OAuth 凭据、未发送 OAuth 合成生成探针、未触碰 `zz`、未修改外置参考仓。
- `host_loaded` 证明新归一化逻辑已在 BWG 加载并生效，不能代替用户在原旧会话中的 `live_accepted` 自然复测，也不能证明长期 OAuth 账号风控安全。
