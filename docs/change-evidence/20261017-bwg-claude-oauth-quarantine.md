# 2026-10-17 BWG Claude OAuth 路由隔离

## 决策与官方依据

- 决策：停止 BWG CPA 后续 Claude subscription OAuth 模型路由，保留 OAuth 文件与额度/冷却状态；不重启 CPA，不发生成请求。
- [Anthropic Consumer Terms](https://www.anthropic.com/legal/consumer-terms) 当前页面标注生效日为
  2025-10-08，规定除 Anthropic API key 或明确许可外，不得以自动或非人工方式访问服务。
  未找到官方明确允许 CPA 代持订阅 OAuth 的依据，因此按高风险处理；此记录不作法律结论。
- [Claude Help Center：Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
  （2026-10-14 更新）描述消费者订阅可用于官方 Claude Code 和受支持 IDE；该说明不等于授权
  第三方 CPA 中转订阅 OAuth。
- [Usage Policy](https://www.anthropic.com/legal/aup) 页面在本次读取时标注新版本 2026-11-12 生效，晚于本次操作日期；未将未来版本作为现行依据。

## 版本与实现

- 本仓实现提交：`b584b4c`（增加 Claude OAuth 可逆隔离控制），推送至 `origin/codex/project-rules-987`。
- 事务使用的 CLIProxyAPI 上游参考：[`router-for-me/CLIProxyAPI`](https://github.com/router-for-me/CLIProxyAPI)
  `v8.0.31`，revision `355fd10e76b82e03ba6625acc583b900286e4bf2`，MIT。源码只读核对了
  `config.yaml` 文件监视行为；采用保留 inode 的配置写入并以模型目录回读确认，不以源码行为
  推断账号安全。
- 初次只读 doctor 报告 `claude_lane=INVALID_QUARANTINE_MARKER` 与 `cpa_policy.py` 投影差异。
  远端策略哈希与本仓提交前父版本一致；该版本的 `load_quarantine_aliases` 不接受 `claude`
  provider，故 lane 错误来自未投影的策略版本，不是已存在的 Claude marker。其他 doctor 检查
  通过，`semantic-policy=OK`；事务随后将策略更新到本仓提交哈希
  `464fb2c3c10577d8885d9f78b6a53b2c01d349117c0689ae8953c545e46e0739`。
- 事后复核发现 idle 快照离配置写入较远。`9f40d98` 将三次快照移至策略投影、目录预检和备份之后，紧邻配置写入，并更新 runbook。此优化缩小竞态窗口，但不构成入口锁；操作期间应暂停自动或并行派发。

## BWG 执行与读回

- 范围仅 BWG；通过 `-Profile bwg` 与 `-StrictHostKeyChecking` 执行。未访问 ZZ。
- 写入前：CPA `v8.0.31`、容器 `running`、重启计数 `0`；严格 doctor 仅报告预期的
  Claude lane/策略投影差异。事务确认所选 Claude OAuth 凭据恰好 1 份、lane 三次 idle、
  当前模型目录符合 active 状态。
- 事务备份目录：`/root/cpa-oauth-quarantine-backup-20261017T073010.059026692Z`。
  备份并校验 `config.yaml`、`cpa_policy.py`；无既有 Claude marker。历史保留策略 `keep_8`，
  本次 `removed=0`。
- 隔离通过热加载配置，将 `claude-*` 加入 `oauth.excluded-models.claude` 并写入 Claude
  quarantine marker。事务回读 `/v1/models` 为 HTTP 200，全部 Claude OAuth 别名消失。
  输出：`OAUTH_CREDENTIAL_RETAINED=yes`、`QUOTA_STATE_RESET=no`。
- 隔离后 strict doctor：`semantic-policy=OK`、`claude_lane=QUARANTINED allowed=none`、
  `CLAUDE_OAUTH_CATALOG=blocked`、`cpa_policy.py` projection `MATCH`、`DOCTOR_CONTRACT_OK`。
  CPA 重启计数仍为 `0`，容器启动时间未变。
- 恢复须人工显式调用：
  `pwsh -NoProfile -File .\scripts\cpa_bwg_guardrails.ps1 -Profile bwg -RestoreClaudeOAuth`。
  （已于同日晚按用户指令恢复，见下节。）事务备份保留供检查。

## 仓库验证与验收边界

- 完整 pytest：`625 passed, 1 skipped`（真实 SSH 集成按默认安全开关跳过），
  `304 subtests passed`；Bandit、Ruff check 通过。格式检查发现的测试文件排版已修正并复查；
  完整 mypy 无问题。
- 后续竞态窗口调整：2 项目标测试通过，Bash `-n`、修改文件 Ruff check/format、mypy 与 `git diff --check` 通过。
- 本次已验证 `repo_verified` 与 `host_loaded`（CPA 目录回读及 strict doctor）；没有执行生成
  请求，因此不声明 `controlled_live_replay` 或 `live_accepted`。路由隔离不撤销官方 OAuth
  授权/刷新会话，也不能证明账号未被标记、免限流或恢复模型质量。
- 未记录账号标识、OAuth 文件名、令牌、密钥、公网地址或授权 URL。

## 2026-10-17 晚恢复（用户指令重开）

- 用户明确指令重开 Claude OAuth 模型路由（同日"开→关→重开"第二次翻转，决策权在
  用户）；ToS 风险接受挂账与纪律边界不变（cpa-claude-account-risk.md）。
- 恢复前只读探针与本账"隔离后"读数一致：`claude_lane=QUARANTINED allowed=none`、
  `CLAUDE_OAUTH_CATALOG=blocked`、`DOCTOR_CONTRACT_OK`（quarantine-aware 门禁在
  远端实证工作）。
- 执行 `-RestoreClaudeOAuth`：exit 0；`QUARANTINE_RELEASED` / `RESTORED_OAUTH_ALIASES=`
  恰三名（opus/sonnet/haiku-5-5）；`OAUTH_CREDENTIAL_RETAINED=yes`；未重启 CPA。
- 恢复后只读 strict doctor：`claude_lane=OK allowed=恰三名 excluded=恰16墓碑`
  （marker 已移除，无 `claude-*`）、`semantic-policy=OK`、`admission-health=OK`
  （四 lane）、`MODEL_IDS` 恰 14 名（三 claude 名回目录）、drift 12×MATCH、
  `DOCTOR_CONTRACT_OK`。
- 本恢复不构成账号安全证明；单凭据、无定时生成探针、限额窗排队（admission 阶梯）
  纪律继续适用。
