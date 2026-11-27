# BWG CPA admission 换代：Codex 身份句吞掉带后缀模型名（2026-11-27）

**性质**：真实远端写入（reviewed admission deploy 事务）+ release 级证据留档（redaction-first）。时间均为 UTC。
**授权**：用户指令（2026-11-27）"授权按你的推荐/建议连续自动修复和优化，直至完成所有未执行但又应当执行的任务"。
**提交**：`c8c3f49`（规则 + 测试 + pin）、`06a4b2b`（事务脚本）。

## 裁决

| 事项 | 结论 | 依据 |
|---|---|---|
| Codex 身份句规则的可选模型记号 | 扩为完整模型 id（吞掉小写字母/数字/连字符后缀） | 实测：`You are Codex, based on GPT-5.6-terra.` 被改写成 `You are a coding agent-terra.`，破折号残片悬空 |
| 官方现行句式 | 不变、已覆盖 | `openai/codex` 三个提示模板首句均为 `You are Codex, based on GPT-5.`；实测清洗后为 `You are a coding agent.` |
| 其他客户端身份句（OpenCode / Gemini CLI / Hermes / omp / Kimi / Cursor） | 不预加规则 | 本机提示源未发现这些身份句；无证据表明这些客户端接入了受清洗车道（反过度设计） |

## 取证

- 用仓内真实函数 `neutralize_agent_identity_sentences` 对 31 条合成文本实测：路径
  `~/.zcode/skills`、`~/.codex/config.toml`、工具名、裸产品名全部保持原字节。
- 模型名格式取自本机真实配置（`gpt-6.1-sol`、`gpt-5.6-terra`、`gpt-5.3-codex`）与
  `openai/codex` 的分模型模板文件名（`gpt-5.1-codex-max_prompt.md`）。
- 规则可达性偏低：官方三个模板均写死 `GPT-5`，带后缀句式目前不出现在官方提示里；
  修复属"有失败依据的收尾小修"，不是在修一个已发生的事故。

## 执行事务

- 脚本：`outputs/deploy-admission-identity-suffix-20261127.ps1`，由
  `outputs/deploy-admission-early-probe-fastfail-20261126.ps1` 派生；差异 = 换 pin 锚点、
  config 只校验不替换、暂存断言改为在主机上以 `nobody` 真实执行被改规则。
- DryRun：`STAGED_RULE_OK identity_suffix_consumed official_shape_intact` →
  `ADMISSION_INTEGRITY_OK` → `DRY_RUN_OK baseline=261a95eb… candidate=3a9df1d3…`。
  **DryRun 首跑被本地预检拦下**：派生时手抄的 config 哈希少一位（63 字符），预锚校验
  fail-closed；改为计算取值后通过。
- Apply：`PREFLIGHT_OK` → `REQUEST_GUARD_EXCLUSIVE_OK` → `ADMISSION_IDLE_OK` →
  `BACKUP_READY` → `ADMISSION_PIN_ROTATED old=261a95eb… new=3a9df1d3…` →
  `ADMISSION_INTEGRITY_OK` ×3 → `DEPLOY_OK`。
- 备份：`/opt/cliproxyapi/backups/20261127T054124.389472489Z-admission-identity-suffix-20261127`。
- 未跑批量 `-Apply`：事务已原子换齐 admission / guard / pin，推送后 strict doctor
  17 项全 MATCH。

## 验收

| 层级 | 证据 |
|---|---|
| `repo_verified` | `scripts/run_gates.ps1 -RunParallel`：`CATALOG_CHECK=PASS`、bandit、ruff、format（178 files）、mypy（165 files, 0 issues）、pytest 1898 passed / 30 skipped / 903 subtests |
| `filesystem_projected` | live `cpa-admission.py` = 主机 pin = `3a9df1d3…`；`drift=cpa-admission.py / cpa_request_guard.py / cpa-admission.sha256` 均 MATCH |
| `host_loaded` | 推送（HEAD `06a4b2b`）后 strict doctor `DOCTOR_CONTRACT_OK`；admission active；healthz `ok`，5 lanes |
| `live_accepted` | **本代未取得自然流量验收**：`3a9df1d3…` 在役 05:41:24Z–05:58:01Z，期间零自然流量，随后被后继代 `b99cfd2f…`（`96636da`，叠加 Claude Code Desktop / Agent SDK 身份句变体）接替。后继代保留本修复（后缀分支在其 `AGENT_IDENTITY_SENTENCES` 内），其后自然流量有 2 次清洗命中且均 200（06:00:50Z `glm-5.3-flash-wb`、06:00:52Z `gemini-4-hydrogen-xhigh`），但这两次走的不是后缀分支。**补充模拟验收**：对线上字节 `b99cfd2f…` 以 `nobody` 加载并执行 `neutralize_agent_identity_sentences`，8 例全过（带后缀模型名、官方句式、无损路径/工具名/裸模型名），零网络、零写入。后缀分支的真实流量命中仍待自然出现 |

## 回滚

用备份目录还原 `cpa-admission.py`、`cpa_request_guard.py`、`cpa-admission.sha256` 后
`systemctl restart cpa-admission`，并复跑 `cpa-admission-integrity-check`；仓内回滚
`c8c3f49`（规则 + pin）。事务脚本自身在任一步失败时会自动执行同一组动作并输出
`ROLLBACK_VERIFIED`。

## 后续观察

- 带后缀句式目前不出现在官方提示里，本修复无即时可见的行为变化；其价值在于模型名
  演进时规则不产生病句。
- 其他客户端若接入 Antigravity / WorkBuddy / 起步套餐车道，须先确认其身份句有规则覆盖。
