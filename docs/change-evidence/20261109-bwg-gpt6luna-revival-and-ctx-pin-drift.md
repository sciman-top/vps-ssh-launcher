# 2026-11-09 bwg gpt-6-luna 复活实证与客户端上下文钉值漂移

## 背景

会话盘点 CPA 模型路由/映射表：`config.yaml` 注册面 14 个名字，但 `/v1/models`
只有 13 个（`gpt-6-luna` 缺席），据此提出"清理 settings.codex 失效条目"建议并获用户
授权执行。执行中前提被证伪，转为恢复 + 实证记录。

## 远端执行与回滚（bwg）

1. 预检：`gpt-6-luna` 在远端 `config.yaml` 恰好 1 处（oauth.settings.codex）；
   基线 sha256=`269579af7b654439…`，inode=524545，healthz ok。
2. 备份：`/root/cpa-config-backup-gpt6luna-20261109T105145Z.yaml`（sha 与基线一致）。
3. 同 inode 原地删除条目（size 6252→6196）：热加载干净
   （`oauth-settings[codex]: updated (4 -> 3 entries)`，21 clients 重建）。
4. **前提证伪**：目录 13→14，`gpt-6-luna`（owned_by=openai）重新出现。`oauth.settings`
   在上游源码语义是"advertised context window 覆写"（metadata-only，非过滤面，
   `config_types.go:594`）；reload 重建 clients 后 codex OAuth 上游目录把 luna 带回。
   此前 13 个是上游目录当时未供给 luna，并非条目把它藏住。
5. 探针：8318 回环单发 `gpt-6-luna` → HTTP 200，TTFB 6.77s，回包 "OK"——luna 上游已复活。
6. 恢复条目（同 inode，锚 `    codex:` 首行前插）：热加载 `3 -> 4 entries`；
   sha256 回到基线 `269579af…` **逐字节一致**。
7. 终态验证：healthz ok；目录 14 个（含 luna）；在役 doctor
   （`scripts/cpa_bwg_guardrails.ps1` 流式）= `DOCTOR_CONTRACT_OK`；
   `MODEL_CONTEXT_DRIFT=none`。

### 结论与勿再提

- `settings.codex` 的 `gpt-6-luna` 条目**不是 stale**，是与其余三个 codex OAuth
  模型一致的上下文 pin（272000）；删除会留下"可见但无 pin"偏差态，已恢复。
- `/root/cpa-guardrail-doctor.orphan-20261108.sh` 是 11-08 孤儿残缺副本
  （`projection-drift-__CPA_PROJECTION_HASH_PAIRS__` 为未展开占位符），其
  FAIL/DRIFT 输出全部作废，勿引用；在役 doctor 只认 wrapper 流式执行。
- 观察：doctor 报 3 个 workbuddy-intl 凭据各 1 次 6008（168h 窗，观察项非门禁）；
  gemini substitution 集中在 2 个凭据（同前口径）。

## 客户端面：500K 钉值漂移根因与决策

- `scripts/verify-codex-model-context.ps1` = `CONTEXT_VERIFY=FAIL`：
  受管目录 `cockpit-model-catalog.json` 中 `deepseek-v4.1-flash` 与
  `gemini-3.8-flash-high` 为 500000/450000，manifest 期望 1M 档。
- 根因：受管目录文件**滞后**。cockpit provider 权威窗口表
  （`~/.cockpit_tools/codex_model_providers.json` [3] `modelContextWindows`）
  已是 deepseek=1000000、gemini=1048576；目录停在 500K 时代
  （备份名 `before-context-500k-20261024`）。`experimental_mode=true`（config.toml:243），
  外部直修目录属无效补丁（runbook 定案）。
- 三源核对（官方文档实测）：DeepSeek flash=1M ctx/384K out、GLM-5.3/-flash=1M/128K、
  Gemini 3.8 Flash=input 1,048,576/65,536 → 500K 非最优，判漂移。
- **gemini 钉值裁定 = 1048576/943718**：与官方 input 逐字节一致、provider 表已一致、
  与 runbook compact 表（1048576→943718）自洽；manifest 同步 bump（本次唯一仓内
  行为变更）。deepseek 维持 1000000/900000（三源本已一致）。

## 遗留用户手势（唯一剩余步骤）

cockpit 运行中，勿手改其文件。收敛路径：
1. cockpit「当前 Codex 配置」弹层保存一次（或切 lane 触发受管目录重建）；
2. `pwsh -NoProfile -File scripts/verify-codex-model-context.ps1` →
   预期 `CONTEXT_VERIFY=PASS`（deepseek 1M/900K、gemini 1048576/943718、
   sol 500K/450K、terra 272000/244800）；
3. desktop 托盘全退重启加载新目录。

## ACCEPTANCE_RESULT=PASS

远端面零净变更（逐字节一致）+ luna 复活实证 + doctor OK；仓内仅 manifest
gemini 一条 bump 与本证据文件。
